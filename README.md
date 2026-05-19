# opcua-mcp

A natural language interface for OPC-UA servers, built on the Model Context Protocol (MCP). Enables AI assistants to connect to, browse, and read values from OPC-UA servers — including the discovery workflows needed to map live process data to engineering models.

> **Note:** This server is under active development. Browsing and value reading are read-only. `opcua_discover.py` (see below) produces a discovery JSON consumed by the graccess-mcp `onboard_from_discovery` tool to automate full Galaxy onboarding.

---

## The Problem

OPC-UA servers in industrial environments expose hundreds or thousands of nodes organized in deep folder hierarchies. Understanding the available data — its structure, data types, and current values — typically requires a dedicated OPC-UA client (UA Expert, Prosys, etc.), knowledge of the server's namespace, and manual node browsing.

For engineers commissioning systems, performing brownfield assessments, or building integrations, this creates friction. The data is there; navigating to it efficiently is the problem.

---

## The Solution

opcua-mcp connects to any OPC-UA server and exposes its node hierarchy as AI-callable tools via the [Model Context Protocol](https://modelcontextprotocol.io/). An AI assistant can connect to the server, walk the node tree, inspect metadata, and read live values conversationally — without needing a dedicated OPC-UA client.

Combined with a SCADA MCP server (e.g. graccess-mcp for AVEVA System Platform), opcua-mcp enables a full brownfield onboarding workflow: browse an OPC-UA source, discover available signals, map them to engineering model attributes, and bind them to live data — driven entirely by natural language.

**Example interactions:**

```
"Connect to the OPC-UA simulator and show me what's available."
"Browse the Pump folder and list all the variable nodes."
"What is the current flow rate on RawWater_01?"
"Show me the data type and description for the UV_01 Intensity node."
"Map the OPC-UA nodes to System Platform UDAs and bind them."
```

---

## Key Capabilities

| Capability | Description |
|---|---|
| **Server connection** | Connect to any OPC-UA endpoint with optional username/password authentication |
| **Node browsing** | Walk the folder hierarchy from the Objects root or any specific node |
| **Tree discovery** | Recursively browse a full subtree in one call, with binding paths for each variable node |
| **Value reading** | Read current value, status code, and timestamp for any variable node |
| **Node inspection** | Retrieve metadata — display name, browse name, node class, data type, description |

---

## Architecture

```
AI Assistant (Claude Desktop, Claude Code, Recon chat UI, etc.)
        │
        ▼
opcua-mcp (FastMCP / SSE)
        │
        └── OPC-UA Server (asyncua async client, on-demand connections)
                └── Any OPC-UA 1.03/1.04 compliant server
```

### Design Decisions

**On-demand async client.** The OPC-UA client connects when `connect_server` is called and persists across subsequent tool calls in the session. asyncua's async client integrates cleanly with FastMCP's async tool execution model without requiring a background thread.

**Node IDs as opaque handles.** `browse_nodes` returns node IDs in standard OPC-UA string format (e.g. `ns=2;i=1001`). The model passes these IDs back to `read_node` and `get_node_info` without needing to understand the format — they're treated as opaque handles.

**Inline values on browse.** Variable nodes include their current value in `browse_nodes` output. This allows the model to get a useful snapshot of a folder's contents in a single call, reducing the round-trips needed to assess an unfamiliar server.

---

## Included Simulator

`simulator.py` runs a self-contained OPC-UA server exposing a synthetic Water Treatment Plant (WTP) data model — 10 equipment instances across 4 subsystems, 33 variable nodes total. Values update on a configurable interval using random-walk (floats) and oscillating boolean generators.

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
| MCP Server | Python, [FastMCP](https://github.com/jlowin/fastmcp) |
| OPC-UA Client/Server | [asyncua](https://github.com/FreeOpcUa/opcua-asyncio) |
| Protocol | OPC-UA 1.03 / 1.04 |
| Runtime | Python 3.13 |

---

## MCP Tools

| Tool | Description |
|---|---|
| `connect_server` | Connect to an OPC-UA endpoint (anonymous or username/password) |
| `browse_nodes` | List children of a node; defaults to the Objects root |
| `browse_tree` | Recursively browse a full subtree (default depth 4); returns binding paths for each variable node suitable for AVEVA System Platform InputSource configuration; capped at 500 nodes with a truncation notice |
| `read_node` | Read the current value, status code, and timestamp of a variable node |
| `get_node_info` | Get metadata for any node — class, data type, browse name, description |
| `discover_plant` | Browse the full plant hierarchy and return a discovery JSON ready to pass to graccess-mcp `onboard_from_discovery` — no file export or copy-paste needed |
| `disconnect_server` | Disconnect from the current server |

---

## Configuration

Copy `.env.example` to `.env` and adjust as needed.

| Environment Variable | Default | Description |
|---|---|---|
| `FASTMCP_PORT` | `8002` | Port for the FastMCP SSE server |
| `OPCUA_PORT` | `4841` | Port for the simulator OPC-UA server (4840 is reserved on Windows) |
| `PUBLISH_INTERVAL` | `2` | Seconds between simulator value updates |
| `OPCUA_ENDPOINT` | `opc.tcp://127.0.0.1:4841/avevawaterSimulator` | Endpoint for `opcua_discover.py` |
| `OPCUA_NAMESPACE_URI` | `urn:avevawaterSimulator` | Namespace URI used by the simulator |
| `OPCUA_WTP_PATH` | `Plant.WTP` | Root node path to browse for discovery |
| `OPCUA_DIO_PREFIX` | `OPCDataSim.Normal.OPCUA.WaterSimGroup` | OPCDataSim binding prefix for graccess-mcp |

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
python opcua_discover.py               # outputs opcua_discovery.json
python opcua_discover.py --out my.json # custom output path
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
2. Walks the WTP node hierarchy (type folders → instance folders → variable nodes)
3. Identifies the primary attribute per type (first float = PV) and marks remaining as UDAs
4. Derives OPCDataSim tag names using the convention `RawWater_01 + Flow → RawWater01_Flow`
5. Outputs a JSON with `types`, `instances`, `attributes`, and `bindings`

**Output JSON structure:**
```json
{
  "types": {
    "Pump": {
      "instances": ["RawWater_01", "RawWater_02", ...],
      "attributes": [{"name": "Flow", "uda_type": "float", "is_primary": true}, ...]
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

---

## Limitations and Known Constraints

- **Read-only.** The server cannot write values to OPC-UA nodes. All operations are observational.
- **Single server.** One OPC-UA connection is maintained at a time. Calling `connect_server` again replaces the existing connection.
- **No certificate authentication.** Anonymous and username/password auth are supported; X.509 certificate-based security is not yet implemented.
- **No subscriptions.** Values are read on demand rather than via OPC-UA subscriptions. For high-frequency monitoring, a subscription-based model would be more efficient.

---

## Roadmap Considerations

- **OPC-UA subscriptions** — push-based value updates for high-frequency signals
- **Write support** — `write_node` tool with confirmation gates for setting values
- **Certificate authentication** — X.509 security for production OPC-UA servers
- **Multi-server sessions** — maintain connections to more than one server simultaneously

---

## License

MIT
