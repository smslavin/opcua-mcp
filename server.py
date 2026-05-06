"""OPC-UA MCP server — browse and read OPC-UA nodes via FastMCP tools."""

import os
from asyncua import Client, ua
from asyncua.common.node import Node
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

mcp = FastMCP("opcua-mcp", port=int(os.environ.get("FASTMCP_PORT", 8002)))

_client: Client | None = None
_server_url: str = ""


def _require_client() -> Client:
    if _client is None:
        raise RuntimeError("Not connected. Call connect_server first.")
    return _client


def _nodeid_str(node: Node) -> str:
    return node.nodeid.to_string()


async def _node_display_name(node: Node) -> str:
    return (await node.read_display_name()).Text or ""


async def _node_class(node: Node) -> ua.NodeClass:
    return await node.read_node_class()


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

@mcp.tool()
async def connect_server(url: str, username: str = "", password: str = "") -> str:
    """Connect to an OPC-UA server.

    Args:
        url:      OPC-UA endpoint, e.g. "opc.tcp://localhost:4840/simulator"
        username: Optional username for user/password auth.
        password: Optional password.
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

    server_node = client.nodes.server
    name = (await server_node.read_display_name()).Text
    ns_array = await client.get_namespace_array()
    return (
        f"Connected to {url}\n"
        f"Server name: {name}\n"
        f"Namespaces: {', '.join(ns_array)}"
    )


@mcp.tool()
async def browse_nodes(node_id: str = "") -> str:
    """Browse children of an OPC-UA node.

    Pass no arguments to start from the Objects root. Pass a node_id returned
    by a previous browse call to drill into a specific folder or object.

    Args:
        node_id: OPC-UA node ID string, e.g. "ns=2;i=1001". Leave blank for Objects root.
    """
    client = _require_client()
    node = client.get_node(node_id) if node_id else client.nodes.objects

    display = await _node_display_name(node)
    children = await node.get_children()

    if not children:
        return f"{display} ({_nodeid_str(node)}) has no children."

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
            lines.append(f"  [{class_label}] {child_name}  ({child_id})")

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
    client = _require_client()
    max_depth = min(int(max_depth), 8)
    lines: list[str] = []

    start_node = client.get_node(node_id) if node_id else client.nodes.objects
    start_name = await _node_display_name(start_node)

    async def recurse(node: Node, depth: int, path: str) -> None:
        if depth > max_depth:
            return
        try:
            children = await node.get_children()
        except Exception:
            return
        for child in children:
            if child.nodeid.NamespaceIndex == 0:
                continue  # skip OPC-UA built-in nodes
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
    return f"Tree from '{start_name}' (max_depth={max_depth}):\n" + "\n".join(lines)


@mcp.tool()
async def read_node(node_id: str) -> str:
    """Read the current value of an OPC-UA variable node.

    Args:
        node_id: OPC-UA node ID string returned by browse_nodes, e.g. "ns=2;i=1005".
    """
    client = _require_client()
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
async def get_node_info(node_id: str) -> str:
    """Get metadata about an OPC-UA node — display name, class, data type, and description.

    Use this to understand what a node represents before reading or mapping it
    to a System Platform attribute.

    Args:
        node_id: OPC-UA node ID string, e.g. "ns=2;i=1005".
    """
    client = _require_client()
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
async def disconnect_server() -> str:
    """Disconnect from the current OPC-UA server."""
    global _client, _server_url
    if _client is None:
        return "Not connected."
    try:
        await _client.disconnect()
    except Exception:
        pass
    _client = None
    url = _server_url
    _server_url = ""
    return f"Disconnected from {url}."


if __name__ == "__main__":
    mcp.run("sse")
