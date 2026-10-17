"""The tools as an MCP client sees them, against simulator.py.

The simulator's plant: Objects/Plant/WTP/{Pump,Tank,Dosing,UV}/<instance>/<attribute>,
in namespace urn:avevawaterSimulator, with string node IDs like
"ns=2;s=Plant.WTP.Pump.RawWater_01.Flow".
"""

import pytest
from mcp.client import Client

import server

FLOW = "ns=2;s=Plant.WTP.Pump.RawWater_01.Flow"
PUMP_PATH = "0:Plant/0:WTP/0:Pump/0:RawWater_01"  # simulator.py names its nodes in namespace 0

TOOLS = {"list_namespaces", "browse_nodes", "browse_tree", "read_nodes",
         "get_node_info", "search_nodes", "discover_plant"}


async def call(name, args=None):
    async with Client(server.mcp) as c:
        return await c.call_tool(name, args or {})


async def ok(name, args=None) -> dict:
    result = await call(name, args)
    assert not result.is_error, _text(result)
    assert result.structured_content is not None
    return result.structured_content


async def error(name, args=None) -> str:
    result = await call(name, args)
    assert result.is_error, result.structured_content
    return _text(result)


def _text(result) -> str:
    return "\n".join(b.text for b in result.content if hasattr(b, "text"))


async def test_tool_surface():
    async with Client(server.mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == TOOLS
    for t in tools.values():
        assert t.annotations.read_only_hint is True, t.name
        assert t.output_schema and "result" not in t.output_schema.get("properties", {}), t.name
        assert t.description, t.name


async def test_connects_on_first_call(conn, simulator):
    # No connect tool: a fresh service talks to its endpoint straight away (#1).
    out = await ok("list_namespaces")
    assert out["endpoint"] == simulator.url
    assert {"index": 2, "uri": "urn:avevawaterSimulator"} in out["namespaces"]


async def test_unreachable_server_is_an_error_and_retried(monkeypatch, simulator):
    c = server._Connection("opc.tcp://127.0.0.1:1/nothing")
    monkeypatch.setattr(server, "_conn", c)
    assert "Cannot connect to the OPC-UA server at opc.tcp://127.0.0.1:1/nothing" in await error("list_namespaces")
    assert c._client is None  # nothing half-connected left behind; the next call tries again


async def test_browse_root(conn):
    out = await ok("browse_nodes")
    assert out["node"]["name"] == "Objects"
    plant = next(c for c in out["children"] if c["name"] == "Plant")
    assert plant == {"node_id": "ns=2;s=Plant", "name": "Plant", "browse_name": "0:Plant",
                     "node_class": "Object", "child_count": 1}


async def test_browse_by_path_and_namespace_filter(conn):
    out = await ok("browse_nodes", {"path": PUMP_PATH, "namespace_filter": "urn:avevawaterSimulator"})
    assert out["node"]["node_id"] == "ns=2;s=Plant.WTP.Pump.RawWater_01"
    by_name = {c["name"]: c for c in out["children"]}
    assert set(by_name) == {"Flow", "Pressure", "Running", "Power"}
    assert isinstance(by_name["Flow"]["value"], float)
    assert isinstance(by_name["Running"]["value"], bool)
    assert by_name["Flow"]["status"] == "Good"
    assert out["total_children"] == 4
    # A leading Objects segment is accepted too.
    again = await ok("browse_nodes", {"path": "0:Objects/" + PUMP_PATH})
    assert again["node"] == out["node"]


async def test_browse_children_limit(conn, monkeypatch):
    monkeypatch.setattr(server, "MAX_BROWSE_CHILDREN", 2)
    out = await ok("browse_nodes", {"path": PUMP_PATH})
    assert (len(out["children"]), out["total_children"]) == (2, 4)


async def test_browse_errors(conn):
    assert "not both" in await error("browse_nodes", {"node_id": FLOW, "path": PUMP_PATH})
    assert "No node at path '0:Plant/0:Nope'" in await error("browse_nodes", {"path": "0:Plant/0:Nope"})
    assert "No node ns=2;s=Nope" in await error("browse_nodes", {"node_id": "ns=2;s=Nope"})
    assert "Not a valid node ID" in await error("browse_nodes", {"node_id": "not an id"})
    assert "No namespace 'urn:nope'" in await error("browse_nodes", {"namespace_filter": "urn:nope"})


async def test_browse_tree(conn):
    out = await ok("browse_tree", {"max_depth": 8})
    assert out["complete"] is True
    assert not any(n["node_id"].startswith("i=") for n in out["nodes"])  # namespace 0 skipped
    flow = next(n for n in out["nodes"] if n["node_id"] == FLOW)
    assert flow["path"] == PUMP_PATH + "/0:Flow"
    assert flow["depth"] == 4
    assert flow["data_type"] == "Float"
    assert isinstance(flow["value"], float)
    # The tree's paths work in browse_nodes.
    pump = next(n for n in out["nodes"] if n["path"] == PUMP_PATH)
    assert (await ok("browse_nodes", {"path": pump["path"]}))["node"]["node_id"] == pump["node_id"]


async def test_browse_tree_from_a_path_and_depth(conn):
    out = await ok("browse_tree", {"path": "Plant/WTP", "max_depth": 0})  # no index: namespace 0
    assert out["root"]["name"] == "WTP"
    assert {n["name"] for n in out["nodes"]} == {"Pump", "Tank", "Dosing", "UV"}
    assert {n["path"] for n in out["nodes"]} == {"0:Pump", "0:Tank", "0:Dosing", "0:UV"}


async def test_browse_tree_node_limit(conn, monkeypatch):
    monkeypatch.setattr(server, "MAX_TREE_NODES", 5)
    out = await ok("browse_tree")
    assert len(out["nodes"]) == 5
    assert (out["complete"], out["stopped_by"]) == (False, "node limit")


async def test_read_nodes(conn):
    out = await ok("read_nodes", {"node_ids": [FLOW, "ns=2;s=Nope", "garbage"]})
    flow, missing, garbage = out["readings"]
    assert flow["name"] == "Flow" and flow["status"] == "Good" and isinstance(flow["value"], float)
    assert flow["source_timestamp"]
    assert "No such node" in missing["error"]
    assert "Not a valid node ID" in garbage["error"]


async def test_read_nodes_all_failed_is_an_error(conn):
    # A failure must not come back as a successful result (#4).
    text = await error("read_nodes", {"node_ids": ["ns=2;s=Nope"]})
    assert "None of the nodes could be read" in text and "ns=2;s=Nope" in text
    assert "at least one" in await error("read_nodes", {"node_ids": []})


async def test_get_node_info(conn):
    out = await ok("get_node_info", {"node_id": FLOW})
    assert out["name"] == "Flow" and out["browse_name"] == "0:Flow"
    assert out["node_class"] == "Variable" and out["data_type"] == "Float"
    assert isinstance(out["value"], float)
    folder = await ok("get_node_info", {"node_id": "ns=2;s=Plant.WTP"})
    assert folder["node_class"] == "Object" and "value" not in folder


async def test_search(conn):
    out = await ok("search_nodes", {"query": "flow"})
    assert out["complete"] is True
    ids = {m["node_id"] for m in out["matches"]}
    assert FLOW in ids
    assert "ns=2;s=Plant.WTP.Dosing.Chlorine_01.FlowRate" in ids
    flow = next(m for m in out["matches"] if m["node_id"] == FLOW)
    assert flow["path"] == PUMP_PATH + "/0:Flow" and isinstance(flow["value"], float)


async def test_search_in_a_subtree(conn):
    out = await ok("search_nodes", {"query": "Running", "root_node_id": "ns=2;s=Plant.WTP.UV"})
    assert out["root"]["name"] == "UV"
    assert {m["node_id"] for m in out["matches"]} == {
        "ns=2;s=Plant.WTP.UV.UV_01.Running", "ns=2;s=Plant.WTP.UV.UV_02.Running"}
    assert {m["path"] for m in out["matches"]} == {"0:UV_01/0:Running", "0:UV_02/0:Running"}


async def test_search_skips_namespace_0_and_no_match_is_not_an_error(conn):
    out = await ok("search_nodes", {"query": "ServerStatus"})
    assert out == {**out, "matches": [], "complete": True}


async def test_search_visit_limit(conn, monkeypatch):
    # A search that matches nothing stops at the visit budget instead of walking everything (#2).
    monkeypatch.setattr(server, "MAX_SEARCH_VISITS", 3)
    out = await ok("search_nodes", {"query": "zzz"})
    assert out["visited"] == 3
    assert (out["complete"], out["stopped_by"]) == (False, "visit limit")


async def test_search_result_limit(conn, monkeypatch):
    monkeypatch.setattr(server, "MAX_SEARCH_RESULTS", 2)
    out = await ok("search_nodes", {"query": "_0"})
    assert len(out["matches"]) == 2
    assert out["stopped_by"] == "result limit"


async def test_search_time_limit(conn, monkeypatch):
    monkeypatch.setattr(server, "WALK_TIME_S", -1)
    out = await ok("search_nodes", {"query": "zzz"})
    assert (out["visited"], out["stopped_by"]) == (0, "time limit")


async def test_discover_plant(conn, simulator):
    out = await ok("discover_plant")
    assert out["endpoint"] == simulator.url
    assert out["namespace_uri"] == "urn:avevawaterSimulator"
    assert out["format"] == "opcua-inventory/1"
    assert out["root"] == {"node_id": "ns=2;s=Plant.WTP", "name": "WTP"}
    pump = next(t for t in out["types"] if t["name"] == "Pump")
    assert [i["name"] for i in pump["instances"]] == ["RawWater_01", "RawWater_02", "HighService_01", "HighService_02"]
    flow = pump["instances"][0]["variables"][0]
    assert {k: flow[k] for k in ("name", "node_id", "path", "data_type")} == {
        "name": "Flow", "node_id": FLOW, "path": "WTP/Pump/RawWater_01/Flow", "data_type": "Float"}
    assert isinstance(flow["value"], float)
    running = next(v for v in pump["instances"][0]["variables"] if v["name"] == "Running")
    assert running["data_type"] == "Boolean" and isinstance(running["value"], bool)
    assert sum(len(i["variables"]) for t in out["types"] for i in t["instances"]) == 34


async def test_discovery_says_nothing_about_system_platform(conn):
    # PV, UDA types and IO binding names are graccess-mcp's decision (opcua-mcp#3).
    import json
    text = json.dumps(await ok("discover_plant"))
    for word in ("uda_type", "is_primary", "galaxy_attr", "dio_binding", "bindings", "OPCDataSim"):
        assert word not in text, word


async def test_discover_plant_bad_namespace_is_an_error(conn):
    assert "Cannot resolve namespace 'urn:nope'" in await error("discover_plant", {"namespace_uri": "urn:nope"})


@pytest.fixture
def own_simulator():
    from conftest import Simulator
    sim = Simulator()
    sim.start()
    yield sim
    sim.stop()


async def test_reconnects_after_the_server_restarts(own_simulator, monkeypatch):
    c = server._Connection(own_simulator.url)
    monkeypatch.setattr(server, "_conn", c)
    try:
        await ok("read_nodes", {"node_ids": [FLOW]})
        own_simulator.stop()
        own_simulator.start()
        # asyncua's supervisor reconnects; the first call after may wait for it.
        out = await ok("read_nodes", {"node_ids": [FLOW]})
        assert out["readings"][0]["status"] == "Good"
    finally:
        await c.close()


async def test_tool_time_limit(conn, monkeypatch):
    # A tool gives up before the aggregator's 60 s timeout, with its own message.
    monkeypatch.setattr(server, "TOOL_TIMEOUT_S", 0)
    assert "did not answer in time" in await error("browse_tree")
