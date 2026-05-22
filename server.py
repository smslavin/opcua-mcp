"""OPC-UA MCP server — browse and read OPC-UA nodes via FastMCP tools."""

import asyncio
import logging
import os
from asyncua import Client, ua
from asyncua.common.node import Node
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("opcua-mcp")

mcp = FastMCP("opcua-mcp", port=int(os.environ.get("FASTMCP_PORT", 8002)))

MAX_BROWSE_LINES = 500
MAX_SEARCH_RESULTS = 100

_client: Client | None = None
_server_url: str = ""
_reconnect_lock = asyncio.Lock()


async def _watch_client(client: Client, url: str) -> None:
    """Background task: clear _client when the connection drops."""
    global _client
    while True:
        await asyncio.sleep(5)
        if _client is not client:
            return  # replaced by a newer connect call
        try:
            await client.nodes.server_state.read_value()
        except Exception:
            if _client is client:
                logger.warning("OPC-UA connection to %s lost — will auto-reconnect on next call", url)
                _client = None
            try:
                await client.disconnect()
            except Exception:
                pass
            return


async def _get_client() -> Client:
    """Return the active client, auto-reconnecting if the connection was lost."""
    global _client
    if _client is not None:
        return _client
    if not _server_url:
        raise RuntimeError("Not connected. Call connect_server first.")
    async with _reconnect_lock:
        if _client is not None:
            return _client
        logger.info("Auto-reconnecting to %s …", _server_url)
        client = Client(url=_server_url)
        await client.connect()
        _client = client
        asyncio.ensure_future(_watch_client(client, _server_url))
        logger.info("Reconnected to %s", _server_url)
        return _client


def _nodeid_str(node: Node) -> str:
    return node.nodeid.to_string()


async def _node_display_name(node: Node) -> str:
    return (await node.read_display_name()).Text or ""


async def _node_class(node: Node) -> ua.NodeClass:
    return await node.read_node_class()


async def _search_walk(start: Node, query: str, max_depth: int) -> list[str]:
    """Walk a subtree and return formatted lines for nodes whose display name matches query."""
    results: list[str] = []
    seen: set[str] = set()
    q = query.lower()

    async def walk(node: Node, depth: int) -> None:
        if depth > max_depth or len(results) >= MAX_SEARCH_RESULTS:
            return
        try:
            children = await node.get_children()
        except Exception:
            return
        for child in children:
            if len(results) >= MAX_SEARCH_RESULTS:
                return
            child_id = _nodeid_str(child)
            if child_id in seen:
                continue
            seen.add(child_id)
            try:
                name = await _node_display_name(child)
                child_class = await _node_class(child)
                if q in name.lower():
                    if child_class == ua.NodeClass.Variable:
                        try:
                            value = await child.read_value()
                            results.append(f"[{child_class.name}] {name} = {value!r}  ({child_id})")
                        except Exception:
                            results.append(f"[{child_class.name}] {name}  ({child_id})")
                    else:
                        results.append(f"[{child_class.name}] {name}  ({child_id})")
                await walk(child, depth + 1)
            except Exception:
                continue

    await walk(start, 0)
    return results


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

@mcp.tool()
async def connect_server(url: str, username: str = "", password: str = "") -> str:
    """Connect to an OPC-UA server.

    Args:
        url:      OPC-UA endpoint, e.g. "opc.tcp://localhost:4840/simulator"
        username: Optional username for user/password auth.
        password: Optional password. Note: credentials appear as plain tool
                  arguments — use only in trusted/demo environments.
    """
    global _client, _server_url
    if _client is not None:
        try:
            await _client.disconnect()
        except Exception:
            pass

    client = Client(url=url)
    if username:
        client.set_user(username)
        client.set_password(password)

    await client.connect()
    _client = client
    _server_url = url
    asyncio.ensure_future(_watch_client(client, url))
    logger.info("Connected to %s", url)

    server_node = client.nodes.server
    name = (await server_node.read_display_name()).Text
    ns_array = await client.get_namespace_array()
    return (
        f"Connected to {url}\n"
        f"Server name: {name}\n"
        f"Namespaces: {', '.join(ns_array)}"
    )


@mcp.tool()
async def list_namespaces() -> str:
    """List all namespace URIs registered on the connected OPC-UA server.

    Returns each namespace's index and URI. Use the index as the 'ns=' prefix
    when constructing node IDs, or as the namespace_filter value in browse_nodes.
    """
    client = await _get_client()
    ns_array = await client.get_namespace_array()
    lines = [f"  {i}: {uri}" for i, uri in enumerate(ns_array)]
    return "Namespaces:\n" + "\n".join(lines)


@mcp.tool()
async def browse_nodes(node_id: str = "", namespace_filter: str = "") -> str:
    """Browse children of an OPC-UA node.

    Pass no arguments to start from the Objects root. Pass a node_id returned
    by a previous browse call to drill into a specific folder or object.
    Non-variable nodes include a child count so you can estimate subtree size
    before committing to a deeper browse.

    Args:
        node_id:          OPC-UA node ID string, e.g. "ns=2;i=1001". Leave blank for Objects root.
        namespace_filter: Optional namespace URI (e.g. "urn:myServer") to show only children
                          in that namespace. Leave blank to show all namespaces.
    """
    client = await _get_client()
    node = client.get_node(node_id) if node_id else client.nodes.objects

    ns_index: int | None = None
    if namespace_filter:
        try:
            ns_index = await client.get_namespace_index(namespace_filter)
        except Exception as e:
            return f"Namespace not found: {namespace_filter!r} — {e}"

    display = await _node_display_name(node)
    children = await node.get_children()

    if not children:
        return f"{display} ({_nodeid_str(node)}) has no children."

    if ns_index is not None:
        children = [c for c in children if c.nodeid.NamespaceIndex == ns_index]
        if not children:
            return f"{display} ({_nodeid_str(node)}) has no children in namespace {namespace_filter!r}."

    lines = [f"Children of '{display}' ({_nodeid_str(node)}):"]
    for child in children:
        child_class  = await _node_class(child)
        child_name   = await _node_display_name(child)
        child_id     = _nodeid_str(child)
        class_label  = child_class.name

        if child_class == ua.NodeClass.Variable:
            try:
                value = await child.read_value()
                lines.append(f"  [{class_label}] {child_name} = {value!r}  ({child_id})")
            except Exception:
                lines.append(f"  [{class_label}] {child_name}  ({child_id})")
        else:
            try:
                gc_count = len(await child.get_children())
            except Exception:
                gc_count = 0
            lines.append(f"  [{class_label}] {child_name}  ({child_id})  [{gc_count} children]")

    return "\n".join(lines)


@mcp.tool()
async def browse_by_path(path: str) -> str:
    """Navigate to an OPC-UA node by browse path without knowing its node ID.

    Path format: slash-separated BrowseName segments, each optionally prefixed with
    their namespace index. Examples:
      "0:Objects/2:PLC1/2:Tanks"
      "Objects/Plant/WTP"   (namespace index 0 assumed when omitted)

    Returns the node's children in the same format as browse_nodes.

    Args:
        path: Slash-separated browse path starting from the server Root node.
    """
    client = await _get_client()
    segments = [s.strip() for s in path.split("/") if s.strip()]
    try:
        node = await client.nodes.root.get_child(segments)
    except Exception as e:
        return f"Path not found: {path!r} — {e}"

    display = await _node_display_name(node)
    children = await node.get_children()

    if not children:
        return f"'{display}' ({_nodeid_str(node)}) has no children."

    lines = [f"Children of '{display}' ({_nodeid_str(node)}):"]
    for child in children:
        child_class = await _node_class(child)
        child_name  = await _node_display_name(child)
        child_id    = _nodeid_str(child)
        class_label = child_class.name

        if child_class == ua.NodeClass.Variable:
            try:
                value = await child.read_value()
                lines.append(f"  [{class_label}] {child_name} = {value!r}  ({child_id})")
            except Exception:
                lines.append(f"  [{class_label}] {child_name}  ({child_id})")
        else:
            try:
                gc_count = len(await child.get_children())
            except Exception:
                gc_count = 0
            lines.append(f"  [{class_label}] {child_name}  ({child_id})  [{gc_count} children]")

    return "\n".join(lines)


@mcp.tool()
async def browse_tree(node_id: str = "", max_depth: int = 4) -> str:
    """Recursively browse the OPC-UA node tree from a starting node.

    Returns the complete subtree up to max_depth levels deep. Variable nodes
    include their current value, data type, and a binding_node that can be used
    directly in System Platform InputSource bindings via the OI Gateway OPC-UA channel.

    Binding node format: BrowseName1.BrowseName2...LeafName  (dot-separated, no leading dot)
    Full InputSource string: OPCUA:WaterSim.<binding_node>
    (where WaterSim is the OPC-UA namespace alias configured in SMC)

    Args:
        node_id:   Starting node ID (blank = Objects root).
        max_depth: How many levels to descend (default 4, max 8).
    """
    client = await _get_client()
    max_depth = min(int(max_depth), 8)
    lines: list[str] = []
    truncated = False

    start_node = client.get_node(node_id) if node_id else client.nodes.objects
    start_name = await _node_display_name(start_node)

    async def recurse(node: Node, depth: int, path: str) -> None:
        nonlocal truncated
        if depth > max_depth or truncated:
            return
        try:
            children = await node.get_children()
        except Exception:
            return
        for child in children:
            if truncated:
                return
            if child.nodeid.NamespaceIndex == 0:
                continue  # skip OPC-UA built-in nodes
            if len(lines) >= MAX_BROWSE_LINES:
                truncated = True
                return
            child_name  = await _node_display_name(child)
            child_class = await _node_class(child)
            child_path  = f"{path}/{child_name}" if path else child_name
            indent      = "  " * depth

            child_id = _nodeid_str(child)
            if child_class == ua.NodeClass.Variable:
                try:
                    value   = await child.read_value()
                    dt_id   = await child.read_data_type()
                    dt_name = await _node_display_name(client.get_node(dt_id))
                    dot_path = child_path.replace("/", ".")
                    lines.append(
                        f"{indent}[{dt_name}] {child_name} = {value!r}"
                        f"  node_id={child_id}  binding_node={dot_path}"
                    )
                except Exception:
                    dot_path = child_path.replace("/", ".")
                    lines.append(
                        f"{indent}[Variable] {child_name}"
                        f"  node_id={child_id}  binding_node={dot_path}"
                    )
            else:
                lines.append(f"{indent}[{child_class.name}] {child_name}  ({child_id})")
                await recurse(child, depth + 1, child_path)

    await recurse(start_node, 0, "")

    if not lines:
        return f"No children found under '{start_name}'."
    result = f"Tree from '{start_name}' (max_depth={max_depth}):\n" + "\n".join(lines)
    if truncated:
        result += f"\n\n[Truncated: showing first {MAX_BROWSE_LINES} nodes. Use browse_nodes with a specific node_id to explore sub-trees.]"
    return result


@mcp.tool()
async def read_node(node_id: str) -> str:
    """Read the current value of an OPC-UA variable node.

    Args:
        node_id: OPC-UA node ID string returned by browse_nodes, e.g. "ns=2;i=1005".
    """
    client = await _get_client()
    node = client.get_node(node_id)

    name  = await _node_display_name(node)
    dv    = await node.read_data_value()
    value = dv.Value.Value
    status = dv.StatusCode.name if dv.StatusCode else "Unknown"
    ts = dv.SourceTimestamp

    return (
        f"{name} ({node_id})\n"
        f"  Value:     {value!r}\n"
        f"  Status:    {status}\n"
        f"  Timestamp: {ts}"
    )


@mcp.tool()
async def read_multiple(node_ids: list[str]) -> str:
    """Read current values for multiple OPC-UA variable nodes in a single call.

    Returns one line per node: display name, node_id, value, status, and source timestamp.
    Prefer this over calling read_node N times for fleet-level status checks.

    Args:
        node_ids: List of OPC-UA node ID strings, e.g. ["ns=2;i=1001", "ns=2;i=1002"].
    """
    client = await _get_client()
    if not node_ids:
        return "No node IDs provided."
    lines = []
    for node_id in node_ids:
        node = client.get_node(node_id)
        try:
            name   = await _node_display_name(node)
            dv     = await node.read_data_value()
            value  = dv.Value.Value
            status = dv.StatusCode.name if dv.StatusCode else "Unknown"
            ts     = dv.SourceTimestamp
            lines.append(f"{name} ({node_id}): {value!r}  [{status}]  {ts}")
        except Exception as e:
            lines.append(f"{node_id}: ERROR — {e}")
    return "\n".join(lines)


@mcp.tool()
async def get_node_info(node_id: str) -> str:
    """Get metadata about an OPC-UA node — display name, class, data type, and description.

    Use this to understand what a node represents before reading or mapping it
    to a System Platform attribute.

    Args:
        node_id: OPC-UA node ID string, e.g. "ns=2;i=1005".
    """
    client = await _get_client()
    node = client.get_node(node_id)

    name       = await _node_display_name(node)
    node_class = await _node_class(node)
    browse_name = await node.read_browse_name()

    lines = [
        f"Node: {name}",
        f"  NodeId:     {node_id}",
        f"  BrowseName: {browse_name.Name}",
        f"  NodeClass:  {node_class.name}",
    ]

    if node_class == ua.NodeClass.Variable:
        try:
            dt_id  = await node.read_data_type()
            dt_node = client.get_node(dt_id)
            dt_name = await _node_display_name(dt_node)
            lines.append(f"  DataType:   {dt_name}")
        except Exception:
            pass
        try:
            value = await node.read_value()
            lines.append(f"  Value:      {value!r}")
        except Exception:
            pass

    try:
        desc = await node.read_description()
        if desc and desc.Text:
            lines.append(f"  Description: {desc.Text}")
    except Exception:
        pass

    return "\n".join(lines)


@mcp.tool()
async def search_nodes(query: str, start_node_id: str = "", max_depth: int = 6) -> str:
    """Search OPC-UA address space by display name — case-insensitive partial match.

    Walks the tree from start_node_id (default: Objects root) and returns all nodes
    whose display name contains *query*. Useful for finding a node without knowing
    the full tree structure first.

    Args:
        query:         Substring to search for in display names (case-insensitive).
        start_node_id: Starting node ID (blank = Objects root).
        max_depth:     Maximum depth to walk (default 6, max 10).
    """
    client = await _get_client()
    max_depth = min(int(max_depth), 10)
    start = client.get_node(start_node_id) if start_node_id else client.nodes.objects
    results = await _search_walk(start, query, max_depth)

    if not results:
        return f"No nodes found matching '{query}'."
    header = f"Nodes matching '{query}' ({len(results)} found"
    if len(results) >= MAX_SEARCH_RESULTS:
        header += f", showing first {MAX_SEARCH_RESULTS}"
    header += "):"
    return header + "\n" + "\n".join(results)


@mcp.tool()
async def search_in_modelview(query: str, root_node_id: str, max_depth: int = 6) -> str:
    """Search for nodes by display name within a specific subtree.

    Same as search_nodes but scoped to a known subtree root. Use this when you
    know the relevant part of the address space to avoid scanning the whole tree.

    Args:
        query:        Substring to search for in display names (case-insensitive).
        root_node_id: Node ID of the subtree root to search within, e.g. "ns=2;i=1001".
        max_depth:    Maximum depth to walk from root (default 6, max 10).
    """
    client = await _get_client()
    max_depth = min(int(max_depth), 10)
    root = client.get_node(root_node_id)
    try:
        root_name = await _node_display_name(root)
    except Exception:
        return f"Could not resolve root node: {root_node_id}"
    results = await _search_walk(root, query, max_depth)

    if not results:
        return f"No nodes found matching '{query}' under '{root_name}'."
    header = f"Nodes matching '{query}' under '{root_name}' ({len(results)} found"
    if len(results) >= MAX_SEARCH_RESULTS:
        header += f", showing first {MAX_SEARCH_RESULTS}"
    header += "):"
    return header + "\n" + "\n".join(results)


@mcp.tool()
async def discover_plant(endpoint: str = "", namespace_uri: str = "", wtp_path: str = "") -> str:
    """Browse the OPC-UA plant hierarchy and return a discovery JSON for Galaxy onboarding.

    Runs the same discovery logic as opcua_discover.py: walks the plant node tree,
    identifies equipment types and instances, marks the primary attribute (PV) per type,
    and generates IO binding paths for every variable node.

    The returned JSON string can be passed directly to the graccess-mcp
    onboard_from_discovery tool to create templates, UDAs, instances, and IO
    bindings in one operation — no copy-paste required.

    Args:
        endpoint:      OPC-UA endpoint URL. Defaults to OPCUA_ENDPOINT env var.
        namespace_uri: Namespace URI to discover within. Auto-detected (first custom namespace)
                       when blank. Override when the server has multiple custom namespaces.
        wtp_path:      String node ID of the plant root node, e.g. "Plant.WTP". Uses
                       OPCUA_WTP_PATH env var when blank; falls back to Objects root if
                       the path cannot be resolved.
    """
    import json
    from opcua_discover import discover
    url = endpoint or os.environ.get("OPCUA_ENDPOINT", "opc.tcp://127.0.0.1:4841/avevawaterSimulator")
    try:
        existing = await _get_client() if (not endpoint or endpoint == _server_url) else None
        result = await discover(
            url,
            client=existing,
            namespace_uri=namespace_uri or None,
            wtp_path=wtp_path or None,
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error("discover_plant failed: %s", e)
        return f"Discovery failed: {e}"


@mcp.tool()
async def disconnect_server() -> str:
    """Disconnect from the current OPC-UA server."""
    global _client, _server_url
    current = _client
    if current is None:
        return "Not connected."
    _client = None
    try:
        await current.disconnect()
    except Exception:
        pass
    url = _server_url
    _server_url = ""
    logger.info("Disconnected from %s", url)
    return f"Disconnected from {url}."


if __name__ == "__main__":
    mcp.run("sse")
