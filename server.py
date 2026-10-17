"""OPC-UA MCP server: browse, read and search one OPC-UA server's address space.

The server talks to the endpoint in OPCUA_ENDPOINT, with OPCUA_USERNAME and
OPCUA_PASSWORD when the server needs them. It connects on the first tool call,
so a freshly started service works without a setup call, and asyncua
reconnects it after a drop. No tool can point it anywhere else: every chat
session and dashboard shares this one connection.

Every tool returns structured content. Every failure is raised as a ToolError,
so the client sees isError and doesn't cache or audit a failure as a result.
"""

import asyncio
import functools
import logging
import os
from datetime import datetime
from typing import Any

from asyncua import Client, ua
from asyncua.common.node import Node
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from typing_extensions import NotRequired, TypedDict

import opcua_discover

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("opcua-mcp")
# asyncua logs every request at INFO; keep its warnings only.
logging.getLogger("asyncua").setLevel(logging.WARNING)

ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://127.0.0.1:4841/avevawaterSimulator")
USERNAME = os.environ.get("OPCUA_USERNAME", "")
PASSWORD = os.environ.get("OPCUA_PASSWORD", "")

# mcp 2.0's MCPServer constructor dropped the port= shortcut FastMCP had;
# port is now passed to run() below instead.
mcp = MCPServer("opcua-mcp")

# Every tool here only observes the plant, so all are declared read-only.
# Clients (the chat UI, AGGREGATOR_READ_ONLY) rely on this: a tool without
# annotations is treated as a write and gated.
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

# Time limits. A tool gives up before the aggregator's 60 s read timeout, so
# the caller gets this server's message, not a timeout from the layer above.
# The walks stop earlier still and return what they found.
REQUEST_TIMEOUT_S = 10   # one OPC-UA request
RECONNECT_WAIT_S = 10    # how long a call waits while asyncua reconnects
RETRY_WAIT_S = 2         # before retrying a call that found the session dropped
TOOL_TIMEOUT_S = 45      # a whole tool call
WALK_TIME_S = 30         # browse_tree and search_nodes

MAX_BROWSE_CHILDREN = 500
MAX_TREE_NODES = 500
MAX_SEARCH_RESULTS = 100
MAX_SEARCH_VISITS = 2000  # nodes whose children a search may list
MAX_READ_NODES = 200
WALK_BATCH = 20           # children lists requested at once during a search
READ_BATCH = 500          # nodes per Read request; servers cap this


# ------------------------------------------------------------------
# Connection
# ------------------------------------------------------------------

class _Connection:
    """The server's one OPC-UA session.

    Connects on first use. After that asyncua's supervisor reconnects a
    dropped session by itself; a call made during the outage waits up to
    RECONNECT_WAIT_S and then fails. A failed first connect leaves nothing
    behind, so the next call simply tries again.
    """

    def __init__(self, url: str, username: str = "", password: str = ""):
        self.url = url
        self._username = username
        self._password = password
        self._client: Client | None = None
        self._lock = asyncio.Lock()

    async def client(self) -> Client:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                client = Client(
                    url=self.url,
                    timeout=REQUEST_TIMEOUT_S,
                    auto_reconnect=True,
                    reconnect_request_timeout=RECONNECT_WAIT_S,
                )
                if self._username:
                    client.set_user(self._username)
                    client.set_password(self._password)
                try:
                    await client.connect()
                except Exception as e:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    raise ToolError(f"Cannot connect to the OPC-UA server at {self.url}: {_reason(e)}") from e
                logger.info("Connected to %s", self.url)
                self._client = client
        return self._client

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass


_conn = _Connection(ENDPOINT, USERNAME, PASSWORD)


def _reason(e: BaseException) -> str:
    return str(e) or type(e).__name__


def _tool(fn):
    """Register fn as a read-only tool, under the time limit, raising every failure as a ToolError.

    mcp 2.x shows the client a ToolError's message; any other exception
    reaches it as a bare "Error executing tool".
    """
    @functools.wraps(fn)
    async def guarded(*args, **kwargs):
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_S):
                try:
                    return await fn(*args, **kwargs)
                except ConnectionError:
                    # The session dropped and asyncua's supervisor hasn't
                    # noticed yet; it reconnects within a second or two.
                    # Every tool only reads, so one retry is safe.
                    await asyncio.sleep(RETRY_WAIT_S)
                    return await fn(*args, **kwargs)
        except ToolError:
            raise
        except TimeoutError:
            raise ToolError(f"The OPC-UA server at {_conn.url} did not answer in time.") from None
        except ua.UaStatusCodeError as e:
            raise ToolError(f"The OPC-UA server refused the request: {_reason(e)}") from e
        except Exception as e:
            logger.exception("%s failed", fn.__name__)
            raise ToolError(f"{fn.__name__} failed: {_reason(e)}") from e

    return mcp.tool(annotations=READ_ONLY)(guarded)


# ------------------------------------------------------------------
# Nodes
# ------------------------------------------------------------------

class NodeInfo(TypedDict):
    """One node, as every tool reports it. Fields that don't apply are left out."""
    node_id: str
    name: str                                # display name
    browse_name: str                         # "2:Flow": namespace index and name
    node_class: str                          # Object, Variable, ...
    path: NotRequired[str]                   # browse names from the walk's start, "/"-separated
    depth: NotRequired[int]                  # browse_tree only; the start's children are 0
    child_count: NotRequired[int]            # browse_nodes, for non-variables
    value: NotRequired[Any]                  # variables with a good read
    data_type: NotRequired[str]
    status: NotRequired[str]                 # variables: the read's status code name
    source_timestamp: NotRequired[str | None]
    description: NotRequired[str]            # get_node_info only


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


def _from_ref(ref: ua.ReferenceDescription, parent_path: str | None = None) -> NodeInfo:
    info: NodeInfo = {
        "node_id": ref.NodeId.to_string(),
        "name": ref.DisplayName.Text or "",
        "browse_name": ref.BrowseName.to_string(),
        "node_class": ref.NodeClass.name,
    }
    if parent_path is not None:
        info["path"] = f"{parent_path}/{info['browse_name']}" if parent_path else info["browse_name"]
    return info


def _put_value(info: NodeInfo, dv: ua.DataValue) -> None:
    status = dv.StatusCode
    info["status"] = status.name
    if status.is_good():
        info["value"] = _jsonable(dv.Value.Value if dv.Value is not None else None)
        info["source_timestamp"] = _jsonable(dv.SourceTimestamp)


async def _read(client: Client, node_ids: list, attr: ua.AttributeIds) -> list[ua.DataValue]:
    """One attribute of many nodes, in as few Read requests as the server allows."""
    out: list[ua.DataValue] = []
    for i in range(0, len(node_ids), READ_BATCH):
        out += await client.uaclient.read_attributes(node_ids[i:i + READ_BATCH], attr)
    return out


async def _add_values(client: Client, infos: list[NodeInfo], refs: list, *, data_types: bool = False) -> None:
    """Fill in value, status and (optionally) data type for the variables among infos."""
    var = [(i, r.NodeId) for i, r in zip(infos, refs) if r.NodeClass == ua.NodeClass.Variable]
    if not var:
        return
    ids = [nid for _, nid in var]
    for (info, _), dv in zip(var, await _read(client, ids, ua.AttributeIds.Value)):
        _put_value(info, dv)
    if data_types:
        dts = await _read(client, ids, ua.AttributeIds.DataType)
        names = await _type_names(client, [dv.Value.Value for dv in dts if dv.StatusCode.is_good()])
        for (info, _), dv in zip(var, dts):
            if dv.StatusCode.is_good():
                info["data_type"] = names.get(dv.Value.Value.to_string(), dv.Value.Value.to_string())


async def _type_names(client: Client, type_ids: list[ua.NodeId]) -> dict[str, str]:
    """Data type NodeId -> its name; the standard types without a server round trip."""
    names: dict[str, str] = {}
    custom = []
    for t in {t.to_string(): t for t in type_ids}.values():
        if t.NamespaceIndex == 0 and t.Identifier in ua.ObjectIdNames:
            names[t.to_string()] = ua.ObjectIdNames[t.Identifier]
        else:
            custom.append(t)
    if custom:
        for t, dv in zip(custom, await _read(client, custom, ua.AttributeIds.DisplayName)):
            if dv.StatusCode.is_good():
                names[t.to_string()] = dv.Value.Value.Text
    return names


async def _children(node: Node) -> list[ua.ReferenceDescription]:
    return await node.get_children_descriptions()


async def _resolve(client: Client, node_id: str = "", path: str = "") -> Node:
    """The node a tool starts from: node_id, or a browse path under Objects, or Objects itself."""
    if node_id and path:
        raise ToolError("Pass node_id or path, not both.")
    if path:
        segments = [s.strip() for s in path.split("/") if s.strip()]
        if segments and segments[0] in ("Objects", "0:Objects"):
            segments = segments[1:]
        if not segments:
            return client.nodes.objects
        try:
            return await client.nodes.objects.get_child(segments)
        except Exception as e:
            raise ToolError(f"No node at path {path!r} under Objects: {_reason(e)}") from e
    if not node_id:
        return client.nodes.objects
    try:
        node = client.get_node(node_id)
    except Exception as e:
        raise ToolError(f"Not a valid node ID: {node_id!r} ({_reason(e)})") from e
    return node


async def _describe(client: Client, node: Node, *, details: bool = False) -> NodeInfo:
    """The node itself, read in one request. Raises a ToolError when it doesn't exist."""
    attrs = [ua.AttributeIds.DisplayName, ua.AttributeIds.BrowseName, ua.AttributeIds.NodeClass]
    if details:
        attrs.append(ua.AttributeIds.Description)
    dvs = await node.read_attributes(attrs)
    if not dvs[2].StatusCode.is_good():
        raise ToolError(f"No node {node.nodeid.to_string()} on the server ({dvs[2].StatusCode.name}).")
    node_class = ua.NodeClass(dvs[2].Value.Value)
    info: NodeInfo = {
        "node_id": node.nodeid.to_string(),
        "name": dvs[0].Value.Value.Text or "",
        "browse_name": dvs[1].Value.Value.to_string(),
        "node_class": node_class.name,
    }
    if details and dvs[3].StatusCode.is_good() and dvs[3].Value.Value and dvs[3].Value.Value.Text:
        info["description"] = dvs[3].Value.Value.Text
    if details and node_class == ua.NodeClass.Variable:
        dv, dt = await node.read_attributes([ua.AttributeIds.Value, ua.AttributeIds.DataType])
        _put_value(info, dv)
        if dt.StatusCode.is_good():
            info["data_type"] = (await _type_names(client, [dt.Value.Value])).get(
                dt.Value.Value.to_string(), dt.Value.Value.to_string())
    return info


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

class Namespace(TypedDict):
    index: int
    uri: str


class Namespaces(TypedDict):
    endpoint: str
    namespaces: list[Namespace]


@_tool
async def list_namespaces() -> Namespaces:
    """List the namespace URIs registered on the OPC-UA server, with their indexes.

    The index is the 'ns=' prefix in node IDs and the number in browse names
    such as "2:Plant".
    """
    client = await _conn.client()
    ns_array = await client.get_namespace_array()
    return {"endpoint": _conn.url, "namespaces": [{"index": i, "uri": u} for i, u in enumerate(ns_array)]}


class Browse(TypedDict):
    node: NodeInfo
    children: list[NodeInfo]       # the first 500
    total_children: int


@_tool
async def browse_nodes(node_id: str = "", path: str = "", namespace_filter: str = "") -> Browse:
    """List the children of one OPC-UA node, with current values for variables.

    Name the node by node_id (from an earlier result, e.g. "ns=2;s=Plant.WTP")
    or by path: browse names under Objects, "/"-separated, each optionally
    prefixed with its namespace index, e.g. "2:Plant/2:WTP". Give neither to
    start at the Objects root. Non-variable children carry child_count, so you
    can judge a subtree's size before browsing deeper. Lists the first 500
    children; total_children says how many there are.

    Args:
        node_id:          Node ID to browse. Leave blank to use path or the Objects root.
        path:             Browse path under Objects. Leave blank to use node_id.
        namespace_filter: Namespace URI (e.g. "urn:avevawaterSimulator"); shows only children in it.
    """
    client = await _conn.client()
    node = await _resolve(client, node_id, path)
    info = await _describe(client, node)
    refs = await _children(node)
    if namespace_filter:
        try:
            ns = await client.get_namespace_index(namespace_filter)
        except Exception as e:
            raise ToolError(f"No namespace {namespace_filter!r} on the server.") from e
        refs = [r for r in refs if r.NodeId.NamespaceIndex == ns]
    total = len(refs)
    refs = refs[:MAX_BROWSE_CHILDREN]
    children = [_from_ref(r) for r in refs]
    await _add_values(client, children, refs)
    containers = [(c, r) for c, r in zip(children, refs) if r.NodeClass != ua.NodeClass.Variable]
    for i in range(0, len(containers), WALK_BATCH):
        batch = containers[i:i + WALK_BATCH]
        counts = await asyncio.gather(
            *(_children(client.get_node(r.NodeId)) for _, r in batch), return_exceptions=True)
        for (c, _), grand in zip(batch, counts):
            if not isinstance(grand, BaseException):
                c["child_count"] = len(grand)
    return {"node": info, "children": children, "total_children": total}


class Tree(TypedDict):
    root: NodeInfo
    max_depth: int
    nodes: list[NodeInfo]
    complete: bool
    stopped_by: NotRequired[str]   # "node limit" or "time limit" when complete is false


@_tool
async def browse_tree(node_id: str = "", path: str = "", max_depth: int = 4) -> Tree:
    """Walk the OPC-UA node tree below one node, with values and data types for variables.

    Nodes come in depth-first order with their depth and their path (browse
    names from the start node, "/"-separated). From the Objects root that path
    can be passed straight to browse_nodes. Standard OPC-UA nodes (namespace
    0, such as the Server object) are skipped. Stops after 500 nodes or 30 s
    and says so in stopped_by; browse a sub-tree for the rest.

    Args:
        node_id:   Start node ID. Leave blank to use path or the Objects root.
        path:      Start browse path under Objects, as in browse_nodes.
        max_depth: Levels to descend below the start (default 4, max 8).
    """
    client = await _conn.client()
    start = await _resolve(client, node_id, path)
    root = await _describe(client, start)
    max_depth = max(0, min(int(max_depth), 8))
    nodes: list[NodeInfo] = []
    stopped: str | None = None
    deadline = asyncio.get_running_loop().time() + WALK_TIME_S

    async def walk(node: Node, depth: int, node_path: str) -> None:
        nonlocal stopped
        if stopped or depth > max_depth:
            return
        if asyncio.get_running_loop().time() > deadline:
            stopped = "time limit"
            return
        refs = [r for r in await _children(node) if r.NodeId.NamespaceIndex != 0]
        infos = [_from_ref(r, node_path) for r in refs]
        await _add_values(client, infos, refs, data_types=True)
        for info, ref in zip(infos, refs):
            if len(nodes) >= MAX_TREE_NODES:
                stopped = "node limit"
                return
            info["depth"] = depth
            nodes.append(info)
            if ref.NodeClass != ua.NodeClass.Variable:
                await walk(client.get_node(ref.NodeId), depth + 1, info["path"])
            if stopped:
                return

    await walk(start, 0, "")
    result: Tree = {"root": root, "max_depth": max_depth, "nodes": nodes, "complete": stopped is None}
    if stopped:
        result["stopped_by"] = stopped
    return result


class Reading(TypedDict):
    node_id: str
    name: NotRequired[str]
    value: NotRequired[Any]
    status: NotRequired[str]
    source_timestamp: NotRequired[str | None]
    error: NotRequired[str]


class Readings(TypedDict):
    readings: list[Reading]


@_tool
async def read_nodes(node_ids: list[str]) -> Readings:
    """Read the current value, status and source timestamp of one or more variable nodes.

    One request for the lot, so read a fleet's values in one call rather than
    one call per node. A node that can't be read gets an error entry; the
    call fails only when none of them can be read.

    Args:
        node_ids: Node IDs from browse_nodes, browse_tree or search_nodes, e.g. ["ns=2;s=Plant.WTP.Pump.RawWater_01.Flow"].
    """
    if not node_ids:
        raise ToolError("Pass at least one node ID.")
    if len(node_ids) > MAX_READ_NODES:
        raise ToolError(f"Read at most {MAX_READ_NODES} nodes per call ({len(node_ids)} given).")
    client = await _conn.client()
    readings: list[Reading] = [{"node_id": nid} for nid in node_ids]
    parsed = []
    for r in readings:
        try:
            parsed.append((r, ua.NodeId.from_string(r["node_id"])))
        except Exception as e:
            r["error"] = f"Not a valid node ID: {_reason(e)}"
    if parsed:
        ids = [nid for _, nid in parsed]
        names = await _read(client, ids, ua.AttributeIds.DisplayName)
        values = await _read(client, ids, ua.AttributeIds.Value)
        for (r, _), name, dv in zip(parsed, names, values):
            if not name.StatusCode.is_good():
                r["error"] = f"No such node ({name.StatusCode.name})."
                continue
            r["name"] = name.Value.Value.Text or ""
            _put_value(r, dv)
            if not dv.StatusCode.is_good():
                r["error"] = f"The value can't be read ({dv.StatusCode.name})."
    if all("error" in r for r in readings):
        raise ToolError("None of the nodes could be read: " +
                        "; ".join(f"{r['node_id']}: {r['error']}" for r in readings))
    return {"readings": readings}


@_tool
async def get_node_info(node_id: str) -> NodeInfo:
    """Describe one OPC-UA node: name, browse name, class, description, and for a variable its data type and value.

    Use it to see what a node represents before reading or mapping it.

    Args:
        node_id: Node ID, e.g. "ns=2;s=Plant.WTP.Pump.RawWater_01.Flow".
    """
    client = await _conn.client()
    return await _describe(client, await _resolve(client, node_id), details=True)


class Search(TypedDict):
    query: str
    root: NodeInfo
    matches: list[NodeInfo]
    visited: int                   # nodes whose children were listed
    complete: bool
    stopped_by: NotRequired[str]   # "result limit", "visit limit" or "time limit"


@_tool
async def search_nodes(query: str, root_node_id: str = "", max_depth: int = 6) -> Search:
    """Find nodes whose display name contains query (case-insensitive) below a root.

    Searches the whole server from the Objects root by default; pass the
    node_id of a known sub-tree to search only there, which is much faster on
    a large server. Standard OPC-UA nodes (namespace 0) are skipped. Matching
    variables come with their current value. The search stops after 100
    matches, 2000 visited nodes or 30 s, and says which in stopped_by.

    Args:
        query:        Text to look for in display names.
        root_node_id: Node ID of the sub-tree to search. Leave blank for the Objects root.
        max_depth:    Levels to search below the root (default 6, max 10).
    """
    if not query.strip():
        raise ToolError("Pass some text to search for.")
    client = await _conn.client()
    start = await _resolve(client, root_node_id)
    root = await _describe(client, start)
    max_depth = max(0, min(int(max_depth), 10))
    q = query.lower()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + WALK_TIME_S

    matches: list[tuple[NodeInfo, ua.ReferenceDescription]] = []
    seen = {start.nodeid.to_string()}
    visited = 0
    stopped: str | None = None
    level: list[tuple[Node, str]] = [(start, "")]

    # Breadth-first, listing up to WALK_BATCH nodes' children at once.
    for _ in range(max_depth + 1):
        below: list[tuple[Node, str]] = []
        for i in range(0, len(level), WALK_BATCH):
            if visited >= MAX_SEARCH_VISITS:
                stopped = "visit limit"
            elif loop.time() > deadline:
                stopped = "time limit"
            if stopped:
                break
            batch = level[i:i + min(WALK_BATCH, MAX_SEARCH_VISITS - visited)]
            listed = await asyncio.gather(*(_children(n) for n, _ in batch), return_exceptions=True)
            visited += len(batch)
            for (_, parent_path), refs in zip(batch, listed):
                if isinstance(refs, BaseException):
                    continue
                for ref in refs:
                    nid = ref.NodeId.to_string()
                    if ref.NodeId.NamespaceIndex == 0 or nid in seen:
                        continue
                    seen.add(nid)
                    info = _from_ref(ref, parent_path)
                    if q in info["name"].lower():
                        matches.append((info, ref))
                        if len(matches) >= MAX_SEARCH_RESULTS:
                            stopped = "result limit"
                            break
                    below.append((client.get_node(ref.NodeId), info["path"]))
                if stopped:
                    break
            if stopped:
                break
        if stopped or not below:
            break
        level = below

    infos = [i for i, _ in matches]
    await _add_values(client, infos, [r for _, r in matches])
    result: Search = {"query": query, "root": root, "matches": infos,
                      "visited": visited, "complete": stopped is None}
    if stopped:
        result["stopped_by"] = stopped
    return result


class DiscoveredVariable(TypedDict):
    name: str
    node_id: str
    path: str                 # browse names from the plant root, "/"-separated
    data_type: str | None     # "Float", "Boolean", ...; None if unreadable
    value: Any


class DiscoveredInstance(TypedDict):
    name: str
    node_id: str
    variables: list[DiscoveredVariable]


class DiscoveredType(TypedDict):
    name: str
    node_id: str
    instances: list[DiscoveredInstance]


class DiscoveredRoot(TypedDict):
    node_id: str
    name: str


class Discovery(TypedDict):
    format: str               # "opcua-inventory/1"
    endpoint: str
    namespace_uri: str
    namespace_index: int
    root: DiscoveredRoot
    types: list[DiscoveredType]


@_tool
async def discover_plant(namespace_uri: str = "", wtp_path: str = "") -> Discovery:
    """Walk the plant hierarchy and return its equipment inventory, for Galaxy onboarding.

    Runs the same discovery as opcua_discover.py: the equipment types under
    the plant root, their instances, and each instance's variables with
    node ID, browse path, data type and current value. It says nothing about
    System Platform: pass it to graccess-mcp's onboard_from_discovery, which
    decides the templates, UDAs, PV and IO bindings.

    Args:
        namespace_uri: Namespace to discover in. Blank: the first custom namespace.
        wtp_path:      String node ID of the plant root, e.g. "Plant.WTP". Blank:
                       OPCUA_WTP_PATH, or the Objects root if that doesn't resolve.
    """
    client = await _conn.client()
    try:
        return await opcua_discover.discover(
            _conn.url, client=client, namespace_uri=namespace_uri or None, wtp_path=wtp_path or None)
    except ValueError as e:
        raise ToolError(str(e)) from e


if __name__ == "__main__":
    mcp.run("sse", port=int(os.environ.get("FASTMCP_PORT", 8002)))
