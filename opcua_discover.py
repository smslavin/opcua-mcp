"""
opcua_discover.py — Browse an OPC-UA server's plant tree and emit a JSON
description of types, instances, attributes, and OPCDataSim tag names.

Run with the opcua-mcp venv (standard 64-bit Python):
    cd mcp-servers\\opcua-mcp
    .venv\\Scripts\\activate
    python opcua_discover.py

Output: opcua_discovery.json in the current directory (or --out path).

The JSON is consumed by bind_opcua_galaxy.py (graccess-mcp) to create
Galaxy templates, instances, and IO bindings automatically.
"""

import argparse
import asyncio
import json
import os

from asyncua import Client, ua
from dotenv import load_dotenv

load_dotenv()

ENDPOINT      = os.environ.get("OPCUA_ENDPOINT",      "opc.tcp://127.0.0.1:4841/avevawaterSimulator")
NAMESPACE_URI = os.environ.get("OPCUA_NAMESPACE_URI",  "urn:avevawaterSimulator")
WTP_PATH      = os.environ.get("OPCUA_WTP_PATH",       "Plant.WTP")
DIO_PREFIX    = os.environ.get("OPCUA_DIO_PREFIX",     "OPCDataSim.Normal.OPCUA.WaterSimGroup")


def python_type_to_uda(value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, float):
        return "float"
    if isinstance(value, int):
        return "integer"
    return "float"


def make_tag_name(instance_id: str, attr_name: str) -> str:
    """RawWater_01 + Flow  ->  RawWater01_Flow"""
    parts = instance_id.rsplit("_", 1)
    prefix = (parts[0] + parts[1]) if len(parts) == 2 and parts[1].isdigit() else instance_id
    return f"{prefix}_{attr_name}"


async def _run_discovery(client: "Client") -> dict:
    """Core discovery logic — runs against an already-connected client."""
    result: dict = {
        "endpoint": str(client.server_url),
        "namespace_uri": NAMESPACE_URI,
        "types": {},
        "bindings": [],
    }

    ns = await client.get_namespace_index(NAMESPACE_URI)
    result["namespace_index"] = ns

    wtp_node = client.get_node(ua.NodeId(WTP_PATH, ns))
    type_nodes = await wtp_node.get_children()

    for type_node in type_nodes:
        type_name = (await type_node.read_browse_name()).Name
        type_info: dict = {"instances": [], "attributes": []}
        result["types"][type_name] = type_info

        instance_nodes = await type_node.get_children()
        first_float_seen = False

        for inst_idx, inst_node in enumerate(instance_nodes):
            inst_name = (await inst_node.read_browse_name()).Name
            type_info["instances"].append(inst_name)

            var_nodes = await inst_node.get_children()

            for var_node in var_nodes:
                attr_name = (await var_node.read_browse_name()).Name

                try:
                    val = await var_node.read_value()
                    uda_type = python_type_to_uda(val)
                except Exception:
                    uda_type = "float"

                if inst_idx == 0:
                    is_primary = uda_type in ("float", "double") and not first_float_seen
                    if is_primary:
                        first_float_seen = True
                    type_info["attributes"].append({
                        "name": attr_name,
                        "uda_type": uda_type,
                        "is_primary": is_primary,
                    })

                tag = make_tag_name(inst_name, attr_name)
                result["bindings"].append({
                    "tagname": inst_name,
                    "type": type_name,
                    "attr_name": attr_name,
                    "galaxy_attr": "PV" if (inst_idx == 0 and type_info["attributes"] and
                                             type_info["attributes"][-1]["is_primary"] if inst_idx == 0 else False
                                             ) else attr_name,
                    "tag": tag,
                    "dio_binding": f"{DIO_PREFIX}.{tag}",
                })

    # Re-derive galaxy_attr now that type_info["attributes"] is fully populated.
    attr_map = {}
    for type_name, type_info in result["types"].items():
        attr_map[type_name] = {
            a["name"]: "PV" if a["is_primary"] else a["name"]
            for a in type_info["attributes"]
        }

    for b in result["bindings"]:
        b["galaxy_attr"] = attr_map[b["type"]].get(b["attr_name"], b["attr_name"])

    return result


async def discover(endpoint: str, client: "Client | None" = None) -> dict:
    """Discover plant hierarchy from an OPC-UA server.

    If *client* is supplied (already connected), it is reused and not
    disconnected on return. Otherwise a new connection is opened and closed.
    """
    if client is not None:
        return await _run_discovery(client)

    async with Client(url=endpoint) as owned_client:
        return await _run_discovery(owned_client)


def main():
    parser = argparse.ArgumentParser(description="Browse OPC-UA plant tree and emit discovery JSON.")
    parser.add_argument("--endpoint", default=ENDPOINT, help="OPC-UA endpoint URL")
    parser.add_argument("--out", default="opcua_discovery.json", help="Output JSON file path")
    args = parser.parse_args()

    print(f"Connecting to {args.endpoint} ...")
    data = asyncio.run(discover(args.endpoint))

    n_types = len(data["types"])
    n_inst  = sum(len(t["instances"]) for t in data["types"].values())
    n_bind  = len(data["bindings"])

    with open(args.out, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Discovered {n_types} types, {n_inst} instances, {n_bind} bindings.")
    for type_name, info in data["types"].items():
        attrs = ", ".join(
            f"{a['name']}({'PV' if a['is_primary'] else a['uda_type']})"
            for a in info["attributes"]
        )
        print(f"  {type_name}: {info['instances']}  [{attrs}]")
    print(f"Output: {args.out}")


if __name__ == "__main__":
    main()
