# opcua-mcp

A natural language interface for an OPC-UA server, built on the Model Context Protocol (MCP). Enables AI assistants to browse, search, and read values from the OPC-UA server it is configured for — including the discovery workflows needed to map live process data to engineering models.

> **Note:** This server is under active development. Browsing and value reading are read-only. `opcua_discover.py` (see below) produces a discovery JSON consumed by the graccess-mcp `onboard_from_discovery` tool to automate full Galaxy onboarding.

---

## The Problem

OPC-UA servers in industrial environments expose hundreds or thousands of nodes organized in deep folder hierarchies. Understanding the available data — its structure, data types, and current values — typically requires a dedicated OPC-UA client (UA Expert, Prosys, etc.), knowledge of the server's namespace, and manual node browsing.

For engineers commissioning systems, performing brownfield assessments, or building integrations, this creates friction. The data is there; navigating to it efficiently is the problem.

---

## The Solution

opcua-mcp connects to the OPC-UA server named in `OPCUA_ENDPOINT` and exposes its node hierarchy as AI-callable tools via the [Model Context Protocol](https://modelcontextprotocol.io/). An AI assistant can walk the node tree, search by name, inspect metadata, and read live values conversationally — without needing a dedicated OPC-UA client.

Combined with a SCADA MCP server, opcua-mcp enables a full brownfield onboarding workflow: browse an OPC-UA source, discover available signals, map them to engineering model attributes, and bind them to live data — driven entirely by natural language.

**Example interactions:**

```
"Show me what's on the OPC-UA server."
"What namespaces does this server expose?"
"Browse the Pump folder and list all the variable nodes."
"Find all nodes with 'Flow' in their name."
"Search for pressure sensors under the WTP subtree."
"What is the current flow rate on RawWater_01?"
"Read the current values for all pump flow nodes in one call."
"Navigate to Objects/Plant/WTP/Pump without knowing the node IDs."
"Show me the data type and description for the UV_01 Intensity node."
"Map the OPC-UA nodes to System Platform UDAs and bind them."
```

---

## Key Capabilities

| Capability | Description |
|---|---|
| **Server connection** | Connects to `OPCUA_ENDPOINT` on the first call, anonymously or with `OPCUA_USERNAME`/`OPCUA_PASSWORD`, and reconnects after a drop |
| **Namespace listing** | List all registered namespaces with their indices — know your server before browsing |
| **Node browsing** | List a node's children by node ID or by browse path (e.g. `"Plant/WTP"`), from the Objects root by default; optionally filter by namespace; non-variable nodes show a child count to guide deeper browsing |
| **Tree discovery** | Recursively browse a full subtree in one call, with the browse path, data type and value of each node |
| **Value reading** | Read current value, status code, and timestamp for one or many variable nodes in one call |
| **Name search** | Case-insensitive partial-name search across the full address space or a scoped subtree, within a visit budget and time limit |
| **Node inspection** | Retrieve metadata — display name, browse name, node class, data type, description |
| **Plant discovery** | Auto-walk the plant hierarchy and emit a Galaxy-ready onboarding JSON; namespace auto-detected if not configured |

---

## Architecture

```
AI Assistant (Claude Desktop, Claude Code, Recon chat UI, etc.)
        │
        ▼
opcua-mcp (MCP SDK / SSE)
        │
        └── OPC-UA Server (asyncua async client, on-demand connections)
                └── Any OPC-UA 1.03/1.04 compliant server
```

### Design Decisions

**One configured server, one shared connection.** The server connects to `OPCUA_ENDPOINT` on the first tool call and asyncua's supervisor reconnects it after a drop. No tool can connect it elsewhere or disconnect it: every chat session and dashboard behind the aggregator shares this connection, and credentials come from the environment, never through the model.

**Structured results, failures as errors.** Every tool returns structured content (with an output schema), using one node shape throughout: `node_id`, `name`, `browse_name`, `node_class`, and for variables `value`, `status`, `source_timestamp` and sometimes `data_type`. A failure — unreachable server, unknown node, bad path — is a tool error, so clients don't cache or audit it as a result.

**Bounded walks.** `browse_tree` stops at 500 nodes and `search_nodes` at 100 matches or 2000 visited nodes; both stop after 30 s, and say which limit they hit. Every tool gives up after 45 s, before the aggregator's 60 s timeout.

**Node IDs as opaque handles.** Every result carries node IDs in standard OPC-UA string format (e.g. `ns=2;s=Plant.WTP`). The model passes these IDs back to `browse_nodes`, `read_nodes`, `get_node_info` and `search_nodes` without needing to understand the format.

**Inline values on browse.** Variable nodes include their current value in `browse_nodes` output, read for the whole folder in one request. This gives the model a snapshot of a folder's contents in a single call.

**Child counts on browse.** Non-variable (folder/object) nodes include `child_count` in browse output. This lets the model estimate subtree size before committing to a recursive browse, avoiding unnecessarily large calls.

**Namespace auto-detection.** `discover_plant` and `opcua_discover.py` auto-detect the first non-standard namespace (index > 1) when `OPCUA_NAMESPACE_URI` is not configured. This makes the discovery workflow portable to any OPC-UA server without requiring env var changes.

---

## Included Simulator

`simulator.py` runs a self-contained OPC-UA server exposing a synthetic Water Treatment Plant (WTP) data model — 10 equipment instances across 4 subsystems, 34 variable nodes total. Its folders and variables have browse names in namespace 0 (`0:Plant`), and string node IDs in namespace 2 (`ns=2;s=Plant.WTP.Pump.RawWater_01.Flow`). Values update on a configurable interval using random-walk (floats) and oscillating boolean generators.

```
Objects/Plant/WTP/
  Pump/   RawWater_01, RawWater_02, HighService_01, HighService_02
            Flow, Pressure, Running, Power
  Tank/   Clarifier_01, FinishedWater_01
            Level, pH, Turbidity
  Dosing/ Chlorine_01, Fluoride_01
            FlowRate, Running, TankLevel
  UV/     UV_01, UV_02
            Intensity, Running, LampHours
```

The simulator mirrors the topic structure of the MQTT brownfield simulator (`mqtt-mcp`), making it suitable for testing cross-protocol brownfield workflows where the same physical signals are available over both transports.

---

## Technology Stack

| Component | Technology |
|---|---|
| MCP Server | Python, [MCP SDK](https://github.com/modelcontextprotocol/python-sdk) (`MCPServer`, mcp>=2.0) |
| OPC-UA Client/Server | [asyncua](https://github.com/FreeOpcUa/opcua-asyncio) |
| Protocol | OPC-UA 1.03 / 1.04 |
| Runtime | Python 3.13 |

---

## MCP Tools

| Tool | Description |
|---|---|
| `list_namespaces` | List all registered namespace URIs with their indices — use before browsing an unfamiliar server |
| `browse_nodes` | List the children of a node, named by `node_id` or by `path` (browse names under Objects, e.g. `"Plant/WTP"` or `"2:Plant/2:WTP"`); Objects root by default; optional `namespace_filter` URI; non-variable nodes show child counts |
| `browse_tree` | Recursively browse a subtree (default depth 4, max 8), skipping namespace 0; each node has its depth and its browse path from the start node, which `browse_nodes` accepts; capped at 500 nodes and 30 s |
| `read_nodes` | Read current value, status code, and source timestamp for one or more node IDs (up to 200) in one request; a node that can't be read gets an error entry |
| `get_node_info` | Get metadata for any node — class, data type, browse name, description, and a variable's value |
| `search_nodes` | Case-insensitive partial name search below `root_node_id` (default Objects root), skipping namespace 0; stops at 100 matches, 2000 visited nodes or 30 s |
| `discover_plant` | Browse the full plant hierarchy and return a discovery ready to pass to graccess-mcp `onboard_from_discovery`; `namespace_uri` and `wtp_path` are optional overrides — both auto-detected if not provided |

Before 0.2.0, `browse_by_path` was `browse_nodes(path=...)`, `read_node` and `read_multiple` were `read_nodes`, and `search_in_modelview` was `search_nodes(root_node_id=...)`. `connect_server` and `disconnect_server` are gone: set `OPCUA_ENDPOINT` instead. The names follow one verb set with mqtt-mcp: `browse_*`, `read_*`, `search_*`/`scan_*`.

---

## Configuration

Copy `.env.example` to `.env` and adjust as needed.

| Environment Variable | Default | Description |
|---|---|---|
| `FASTMCP_PORT` | `8002` | Port for the MCP SSE server |
| `OPCUA_PORT` | `4841` | Port for the simulator OPC-UA server (4840 is reserved on Windows) |
| `PUBLISH_INTERVAL` | `2` | Seconds between simulator value updates |
| `OPCUA_ENDPOINT` | `opc.tcp://127.0.0.1:4841/avevawaterSimulator` | The OPC-UA server the MCP server and `opcua_discover.py` talk to |
| `OPCUA_USERNAME` / `OPCUA_PASSWORD` | *(blank — anonymous)* | Credentials for `OPCUA_ENDPOINT`, if it needs them |
| `OPCUA_NAMESPACE_URI` | *(blank — auto-detected)* | Namespace URI for discovery. When blank, the first non-standard namespace (index > 1) is used automatically. Set explicitly when a server has multiple custom namespaces. |
| `OPCUA_WTP_PATH` | `Plant.WTP` | Root node string ID for discovery. Falls back to Objects root if the path cannot be resolved on the connected server. |
| `OPCUA_DIO_PREFIX` | `OPCDataSim.Normal.OPCUA.WaterSimGroup` | OPCDataSim binding prefix for graccess-mcp; included in `dio_binding` fields in discovery JSON. Leave blank for generic servers. |

---

## Setup

### Prerequisites

- Python 3.13
- An accessible OPC-UA server, or use the included simulator

### Installation

1. Create and activate a virtual environment:
    ```powershell
    python -m venv .venv
    .venv\Scripts\activate   # Windows
    pip install -r requirements.txt
    ```

`requirements.txt` pins exact versions; raise them on purpose, with the tests passing.

### Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

The tests start `simulator.py` on free ports and call every tool through an MCP client, including a simulator restart to check reconnection. They need no other service.

### Running

**MCP server (Windows PowerShell):**
```powershell
.\start_opcua_mcp.ps1
```

**Simulator (separate window):**
```powershell
.\start_simulator.ps1
```

**Direct:**
```bash
python server.py    # MCP server on port 8002
python simulator.py # OPC-UA simulator on port 4841
```

### Running as Windows Services (recommended for lab / demo VMs)

Installs OpcuaMCP and OpcuaSimulator as auto-start Windows services via [NSSM](https://nssm.cc/download):

1. Install NSSM and add it to PATH
2. Edit the Configuration block at the top of `install_services.ps1` (OPC-UA port, publish interval)
3. Run as Administrator:
   ```powershell
   .\install_services.ps1
   ```

To remove: `.\uninstall_services.ps1` (run as Administrator).  
Logs are written to `logs\` with 10 MB rotation and automatic restart on crash.  
The simulator is started before the MCP server so data is ready on first tool call.

**Discovery (brownfield onboarding):**
```powershell
python opcua_discover.py                               # auto-detect namespace, outputs opcua_discovery.json
python opcua_discover.py --namespace-uri urn:myServer  # explicit namespace
python opcua_discover.py --wtp-path Plant.WTP          # explicit plant root
python opcua_discover.py --out my.json                 # custom output path
```

### Connecting to Claude Desktop or Claude Code

```json
{
  "mcpServers": {
    "opcua": {
      "type": "sse",
      "url": "http://localhost:8002/sse"
    }
  }
}
```

---

## Brownfield Discovery Script

`opcua_discover.py` browses the OPC-UA server's plant tree and emits a discovery JSON consumed by the graccess-mcp `onboard_from_discovery` tool to automate full Galaxy onboarding.

**What it does:**
1. Connects to the configured OPC-UA endpoint
2. Auto-detects the namespace (or uses `OPCUA_NAMESPACE_URI` / `--namespace-uri` if set)
3. Walks the plant node hierarchy (type folders → instance folders → variable nodes) starting from `OPCUA_WTP_PATH` or Objects root
4. Identifies the primary attribute per type (first float = PV) and marks remaining as UDAs
5. Derives OPCDataSim tag names using the convention `RawWater_01 + Flow → RawWater01_Flow`
6. Outputs a JSON with `types`, `instances`, `attributes`, and `bindings`

**Output JSON structure:**
```json
{
  "namespace_uri": "urn:avevawaterSimulator",
  "namespace_index": 2,
  "types": {
    "Pump": {
      "instances": ["RawWater_01", "RawWater_02", "..."],
      "attributes": [{"name": "Flow", "uda_type": "float", "is_primary": true}, "..."]
    }
  },
  "bindings": [
    {
      "tagname": "RawWater_01",
      "galaxy_attr": "PV",
      "dio_binding": "OPCDataSim.Normal.OPCUA.WaterSimGroup.RawWater01_Flow"
    }
  ]
}
```

Pass the JSON contents to `onboard_from_discovery` in graccess-mcp to create all templates, UDAs, instances, and IO bindings in one operation.

The `discover_plant` MCP tool runs the same logic inline on the server's connection and returns the result directly to the AI — no file export or copy-paste needed.

---

## Limitations and Known Constraints

- **Read-only.** The server cannot write values to OPC-UA nodes. All operations are observational.
- **Single server.** One OPC-UA server per opcua-mcp instance, set by `OPCUA_ENDPOINT`. Run a second instance on another port for a second server.
- **No certificate authentication.** Anonymous and username/password auth are supported; X.509 certificate-based security is not yet implemented.
- **No subscriptions.** Values are read on demand rather than via OPC-UA subscriptions. For high-frequency monitoring, a subscription-based model would be more efficient.

---

## Roadmap Considerations

- **OPC-UA subscriptions** — push-based value updates for high-frequency signals
- **Write support** — `write_node` tool with confirmation gates for setting values
- **Certificate authentication** — X.509 security for production OPC-UA servers

---

## License

MIT
