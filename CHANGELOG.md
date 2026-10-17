# Changelog

Each release is a git tag, `vX.Y.Z`. Issue numbers refer to this repo unless they name another.

## 0.2.0

Breaking: tool names and results changed. The galaxy site plugin in smslavin/graccess-mcp needs its matching release.

- **Connects to `OPCUA_ENDPOINT` by itself** (#1). The first tool call connects, with `OPCUA_USERNAME`/`OPCUA_PASSWORD` if set, and asyncua reconnects after a drop; a call that finds the session just dropped retries once. `connect_server` and `disconnect_server` are gone: they let any chat session repoint or disconnect the one connection every user shares, and carried a password through the model. `discover_plant` lost its `endpoint` argument for the same reason.
- **Failures are tool errors** (#4). An unreachable server, an unknown node or path, a discovery that can't resolve its namespace, and a read where no node could be read all raise, so the client sees `isError` instead of a cached, audited "success". `read_nodes` still returns partial results, with an error entry per failed node.
- **Searches and tree walks are bounded** (#2). `search_nodes` stops after 100 matches, 2000 visited nodes or 30 s; `browse_tree` after 500 nodes or 30 s. Each says which limit it hit in `stopped_by`. Both skip namespace 0. Every tool gives up after 45 s, before the aggregator's 60 s timeout. Searches list children in batches of 20 and read values per folder in one request.
- **One tool set, shared verbs with mqtt-mcp, structured output** (#6). `browse_nodes` takes a `node_id` or a `path` (replaces `browse_by_path`); `read_nodes` replaces `read_node` and `read_multiple`; `search_nodes` takes `root_node_id` (replaces `search_in_modelview`). Every tool returns structured content with one node shape. `browse_tree` gives each node its browse path from the start node instead of the `binding_node` string with its hard-coded `OPCUA:WaterSim` alias.
- **Tests and pinned dependencies** (#5). `requirements.txt` pins exact versions; `tests/` runs every tool against `simulator.py`, including a simulator restart; CI runs them on Python 3.11 and 3.13.
- asyncua's per-request INFO logging is off, so the service log shows this server's own lines and warnings.
- `discover_plant`'s `endpoint` field is the URL; it was the repr of a parsed URL.

## 0.1.0

- **Its own repo.** opcua-mcp, with the Water Treatment Plant simulator, moved out of smslavin/mcp-servers, with its history, so it can be versioned and tagged. The code is unchanged from mcp-servers c37ae66.
- Already there before the move: tools declared read-only, the simulator's fault injection HTTP port (`FAULT_HTTP_PORT`), and `opcua_discover.py` for galaxy onboarding.
