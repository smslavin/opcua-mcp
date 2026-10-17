"""
opcua_discover.py — Browse an OPC-UA server's plant tree and emit a neutral
inventory of its equipment: types, their instances, and each instance's
variables with node ID, browse path, data type and current value.

The inventory says what the server has, nothing about System Platform: which
attribute becomes PV, the UDA types and the IO binding names are
graccess-mcp's decision (its onboard_from_discovery, opcua-mcp#3).

Run with the opcua-mcp venv (standard 64-bit Python):
    cd opcua-mcp
    .venv\\Scripts\\activate
    python opcua_discover.py

Output: opcua_discovery.json in the current directory (or --out path).
"""

import argparse
import asyncio
import json
import logging
import os
from datetime import datetime

from asyncua import Client, ua
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("opcua-mcp")

ENDPOINT      = os.environ.get("OPCUA_ENDPOINT",      "opc.tcp://127.0.0.1:4841/avevawaterSimulator")
NAMESPACE_URI = os.environ.get("OPCUA_NAMESPACE_URI",  "")
WTP_PATH      = os.environ.get("OPCUA_WTP_PATH",       "Plant.WTP")

# The inventory's format; a consumer checks it before reading the rest.
INVENTORY_FORMAT = "opcua-inventory/1"


async def _auto_detect_namespace(client: "Client") -> str:
    """Return the first non-standard namespace URI from the server's namespace array.

    Skips index 0 (OPC-UA Foundation) and index 1 (OPC-UA/XML schema).
    Falls back to the last namespace in the array if none are found beyond index 1.
    """
    ns_array = await client.get_namespace_array()
    custom = [uri for i, uri in enumerate(ns_array) if i > 1]
    if custom:
        return custom[0]
    if len(ns_array) > 1:
        return ns_array[-1]
    return ns_array[0]


def _jsonable(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


async def _data_type_name(client: "Client", node, cache: dict) -> str | None:
    """The variable's data type by name ("Float", "Boolean"), or None if unreadable."""
    try:
        type_id = await node.read_data_type()
    except Exception:
        return None
    key = type_id.to_string()
    if key not in cache:
        if type_id.NamespaceIndex == 0 and type_id.Identifier in ua.ObjectIdNames:
            cache[key] = ua.ObjectIdNames[type_id.Identifier]
        else:
            try:
                cache[key] = (await client.get_node(type_id).read_display_name()).Text
            except Exception:
                cache[key] = key
    return cache[key]


async def _run_discovery(
    client: "Client",
    namespace_uri: str | None = None,
    wtp_path: str | None = None,
) -> dict:
    """Core discovery logic — runs against an already-connected client.

    The plant root's children are the equipment types, their children the
    instances, and the instances' children the variables.

    Args:
        namespace_uri: Namespace URI to discover within. Auto-detected when None.
        wtp_path:      String node ID for the plant root (e.g. "Plant.WTP").
                       Uses OPCUA_WTP_PATH env var when None; falls back to
                       Objects root if the path cannot be resolved.
    """
    if not namespace_uri:
        namespace_uri = NAMESPACE_URI or await _auto_detect_namespace(client)
        logger.info("Auto-detected namespace: %s", namespace_uri)

    try:
        ns = await client.get_namespace_index(namespace_uri)
    except Exception as exc:
        raise ValueError(f"Cannot resolve namespace {namespace_uri!r}: {exc}") from exc

    if wtp_path is None:
        wtp_path = WTP_PATH  # env var default (may be empty string)

    root_node = None
    if wtp_path:
        try:
            candidate = client.get_node(ua.NodeId(wtp_path, ns))
            await candidate.read_browse_name()  # verify it exists
            root_node = candidate
            logger.info("Using plant root: ns=%d;s=%s", ns, wtp_path)
        except Exception:
            logger.warning("WTP path %r not found in ns=%d — falling back to Objects root", wtp_path, ns)
    if root_node is None:
        root_node = client.nodes.objects
        logger.info("Using Objects root for discovery")

    root_name = (await root_node.read_browse_name()).Name
    type_cache: dict = {}
    types = []
    for type_node in await root_node.get_children():
        type_name = (await type_node.read_browse_name()).Name
        instances = []
        for inst_node in await type_node.get_children():
            inst_name = (await inst_node.read_browse_name()).Name
            variables = []
            for var_node in await inst_node.get_children():
                var_name = (await var_node.read_browse_name()).Name
                try:
                    value = _jsonable(await var_node.read_value())
                except Exception:
                    value = None
                variables.append({
                    "name": var_name,
                    "node_id": var_node.nodeid.to_string(),
                    # Browse names from the plant root, "/"-separated.
                    "path": "/".join((root_name, type_name, inst_name, var_name)),
                    "data_type": await _data_type_name(client, var_node, type_cache),
                    "value": value,
                })
            instances.append({"name": inst_name, "node_id": inst_node.nodeid.to_string(), "variables": variables})
        types.append({"name": type_name, "node_id": type_node.nodeid.to_string(), "instances": instances})

    return {
        "format": INVENTORY_FORMAT,
        "endpoint": client.server_url.geturl(),
        "namespace_uri": namespace_uri,
        "namespace_index": ns,
        "root": {"node_id": root_node.nodeid.to_string(), "name": root_name},
        "types": types,
    }


async def discover(
    endpoint: str,
    client: "Client | None" = None,
    namespace_uri: str | None = None,
    wtp_path: str | None = None,
) -> dict:
    """Discover the plant's equipment on an OPC-UA server.

    If *client* is supplied (already connected), it is reused and not
    disconnected on return. Otherwise a new connection is opened and closed.

    Args:
        endpoint:      OPC-UA endpoint URL (used only when client is None).
        client:        Optional already-connected asyncua Client to reuse.
        namespace_uri: Namespace URI override. Auto-detected when None.
        wtp_path:      Plant root path override. Uses env var / fallback when None.
    """
    if client is not None:
        return await _run_discovery(client, namespace_uri=namespace_uri, wtp_path=wtp_path)

    async with Client(url=endpoint) as owned_client:
        return await _run_discovery(owned_client, namespace_uri=namespace_uri, wtp_path=wtp_path)


def main():
    parser = argparse.ArgumentParser(description="Browse an OPC-UA plant tree and emit its equipment inventory.")
    parser.add_argument("--endpoint", default=ENDPOINT, help="OPC-UA endpoint URL")
    parser.add_argument("--namespace-uri", default="", help="Namespace URI (auto-detected if blank)")
    parser.add_argument("--wtp-path", default=None, help="Plant root string node ID (uses env var if omitted)")
    parser.add_argument("--out", default="opcua_discovery.json", help="Output JSON file path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

    print(f"Connecting to {args.endpoint} ...")
    data = asyncio.run(
        discover(
            args.endpoint,
            namespace_uri=args.namespace_uri or None,
            wtp_path=args.wtp_path,
        )
    )

    n_inst = sum(len(t["instances"]) for t in data["types"])
    n_vars = sum(len(i["variables"]) for t in data["types"] for i in t["instances"])

    with open(args.out, "w") as f:
        json.dump(data, f, indent=2)

    print(f"Discovered {len(data['types'])} types, {n_inst} instances, {n_vars} variables.")
    print(f"Namespace: {data['namespace_uri']} (index {data['namespace_index']})")
    for t in data["types"]:
        print(f"  {t['name']}: {[i['name'] for i in t['instances']]}")
    print(f"Output: {args.out}")


if __name__ == "__main__":
    main()
