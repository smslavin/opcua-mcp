"""OPC-UA Water Treatment Plant simulator.

Mirrors brownfield_demo.py (MQTT) but exposes data over OPC-UA.
Node hierarchy: Objects/Plant/WTP/{Type}/{Instance}/{Attribute}

Run:
    python simulator.py

Environment:
    OPCUA_PORT   Server port (default: 4840)
    PUBLISH_INTERVAL  Seconds between value updates (default: 2)
"""

import asyncio
import os
import random

from asyncua import Server, ua
from asyncua.server import internal_session as _is
from dotenv import load_dotenv


# OI Gateway requests EventNotifier (attr=12) on Variable nodes during discovery.
# asyncua returns BadAttributeIdInvalid (attr=12 is only valid for Object/View nodes),
# which causes AVEVA SP to report "Communication error: Invalid attribute ID".
# Patch: return Good + Byte(0) for attr=12 on any node so the OI Gateway stays happy.
_orig_read = _is.InternalSession.read

_OPERATION_LIMIT_NODES = {"i=2992", "i=2993"}

async def _patched_read(self, params):
    for item in params.NodesToRead:
        nid = item.NodeId.to_string()
        if nid != "i=2259":  # suppress noisy ServerStatus health-check reads
            print(f"[READ] attr={item.AttributeId} node={nid}", flush=True)
    results = await _orig_read(self, params)
    for i, item in enumerate(params.NodesToRead):
        nid = item.NodeId.to_string()
        sc = results[i].StatusCode  # Optional — can be None for non-Value attributes
        if sc is not None and sc.is_bad():
            print(f"[PATCH] bad attr={item.AttributeId} sc={sc.name}", flush=True)
        if item.AttributeId == 12 and (sc is None or sc.is_bad()):
            results[i] = ua.DataValue(
                Value=ua.Variant(ua.Byte(0), ua.VariantType.Byte),
                StatusCode_=ua.StatusCode(ua.StatusCodes.Good),
            )
            print(f"[PATCH] fixed attr=12 -> Good", flush=True)
        # Log OperationLimits values; patch None/0 -> 1000 so OI Gateway doesn't treat as "zero allowed"
        if nid in _OPERATION_LIMIT_NODES and item.AttributeId == 13:
            val = results[i].Value.Value if (results[i].Value is not None) else None
            print(f"[OPLIMIT] node={nid} raw_value={val}", flush=True)
            if not val:
                results[i] = ua.DataValue(
                    Value=ua.Variant(ua.UInt32(1000), ua.VariantType.UInt32),
                    StatusCode_=ua.StatusCode(ua.StatusCodes.Good),
                )
                print(f"[PATCH] fixed OperationLimit {nid} -> 1000", flush=True)
    return results

_is.InternalSession.read = _patched_read

_orig_create_sub = _is.InternalSession.create_subscription

async def _patched_create_sub(self, params, callback, **kwargs):
    print(f"[SUB] CreateSubscription: interval={params.RequestedPublishingInterval:.0f}ms", flush=True)
    result = await _orig_create_sub(self, params, callback, **kwargs)
    print(f"[SUB] Created: id={result.SubscriptionId}", flush=True)
    return result

_is.InternalSession.create_subscription = _patched_create_sub

_orig_create_mi = _is.InternalSession.create_monitored_items

async def _patched_create_mi(self, params):
    print(f"[MI] CreateMonitoredItems: {len(params.ItemsToCreate)} items for sub={params.SubscriptionId}", flush=True)
    for item in params.ItemsToCreate:
        print(f"  node={item.ItemToMonitor.NodeId.to_string()} attr={item.ItemToMonitor.AttributeId}", flush=True)
    results = await _orig_create_mi(self, params)
    for i, r in enumerate(results):
        sc = r.StatusCode.name if r.StatusCode else "None"
        print(f"  -> [{i}] MonitoredItemId={r.MonitoredItemId} status={sc}", flush=True)
    return results

_is.InternalSession.create_monitored_items = _patched_create_mi

_orig_transfer_subs = _is.InternalSession.transfer_subscriptions

async def _patched_transfer_subs(self, params):
    print(f"[TRANSFER] TransferSubscriptions: {params}", flush=True)
    result = await _orig_transfer_subs(self, params)
    print(f"[TRANSFER] result: {result}", flush=True)
    return result

_is.InternalSession.transfer_subscriptions = _patched_transfer_subs


load_dotenv(override=True)

PORT     = int(os.environ.get("OPCUA_PORT", 4841))
INTERVAL = float(os.environ.get("PUBLISH_INTERVAL", 2))
URI      = "urn:avevawaterSimulator"


class RandomWalk:
    def __init__(self, initial: float, lo: float, hi: float, step: float):
        self.value = float(initial)
        self.lo, self.hi, self.step = float(lo), float(hi), float(step)

    def next(self) -> float:
        self.value += random.uniform(-self.step, self.step)
        self.value = max(self.lo, min(self.hi, self.value))
        return round(self.value, 2)


class OscillatingBool:
    def __init__(self, initial: bool, flip_chance: float = 0.01):
        self.value = initial
        self.flip_chance = flip_chance

    def next(self) -> bool:
        if random.random() < self.flip_chance:
            self.value = not self.value
        return self.value


def rw(lo, hi, step=None):
    mid = (lo + hi) / 2
    return RandomWalk(mid + random.uniform(-(hi - lo) * 0.1, (hi - lo) * 0.1),
                      lo, hi, step or (hi - lo) * 0.01)

def ob(initial=True, flip=0.01):
    return OscillatingBool(initial, flip)


INSTANCES = [
    ("Pump",   "RawWater_01",     {"Flow": rw(0, 500, 4.0),  "Pressure": rw(0, 10, 0.08),  "Running": ob(True,  0.01), "Power": rw(0, 75, 0.6)}),
    ("Pump",   "RawWater_02",     {"Flow": rw(0, 500, 4.0),  "Pressure": rw(0, 10, 0.08),  "Running": ob(True,  0.01), "Power": rw(0, 75, 0.6)}),
    ("Pump",   "HighService_01",  {"Flow": rw(0, 500, 3.5),  "Pressure": rw(2, 10, 0.06),  "Running": ob(True,  0.01), "Power": rw(0, 75, 0.5)}),
    ("Pump",   "HighService_02",  {"Flow": rw(0, 500, 3.5),  "Pressure": rw(2, 10, 0.06),  "Running": ob(False, 0.01), "Power": rw(0, 75, 0.5)}),
    ("Tank",   "Clarifier_01",    {"Level": rw(0, 100, 0.5), "pH": rw(6.5, 8.5, 0.02),     "Turbidity": rw(0, 5, 0.03)}),
    ("Tank",   "FinishedWater_01",{"Level": rw(0, 100, 0.4), "pH": rw(6.8, 7.8, 0.01),     "Turbidity": rw(0, 1, 0.01)}),
    ("Dosing", "Chlorine_01",     {"FlowRate": rw(0, 10, 0.05), "Running": ob(True,  0.01), "TankLevel": rw(20, 100, 0.2)}),
    ("Dosing", "Fluoride_01",     {"FlowRate": rw(0, 10, 0.04), "Running": ob(True,  0.01), "TankLevel": rw(20, 100, 0.2)}),
    ("UV",     "UV_01",           {"Intensity": rw(85, 100, 0.3), "Running": ob(True,  0.005), "LampHours": rw(0, 10000, 0.01)}),
    ("UV",     "UV_02",           {"Intensity": rw(85, 100, 0.3), "Running": ob(False, 0.005), "LampHours": rw(0, 10000, 0.01)}),
]


async def main():
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://127.0.0.1:{PORT}/avevawaterSimulator")
    server.set_server_name("AvevaWater OPC-UA Simulator")
    server.iserver.max_sessions = 100

    idx = await server.register_namespace(URI)

    objects = server.nodes.objects
    plant_folder = await objects.add_folder(ua.NodeId("Plant", idx), "Plant")
    wtp_folder   = await plant_folder.add_folder(ua.NodeId("Plant.WTP", idx), "WTP")

    variable_nodes: list[tuple] = []
    type_folders: dict = {}

    for obj_type, instance_id, attrs in INSTANCES:
        if obj_type not in type_folders:
            type_folders[obj_type] = await wtp_folder.add_folder(ua.NodeId(f"Plant.WTP.{obj_type}", idx), obj_type)
        inst_folder = await type_folders[obj_type].add_folder(ua.NodeId(f"Plant.WTP.{obj_type}.{instance_id}", idx), instance_id)

        for attr_name, gen in attrs.items():
            node_id = ua.NodeId(f"Plant.WTP.{obj_type}.{instance_id}.{attr_name}", idx)
            if isinstance(gen, OscillatingBool):
                var = await inst_folder.add_variable(node_id, attr_name, bool(gen.value))
            else:
                var = await inst_folder.add_variable(
                    node_id, attr_name,
                    ua.Variant(float(gen.value), ua.VariantType.Float)
                )
            # read-only to external clients; simulator writes via server-side API
            variable_nodes.append((var, gen))

    attr_count = sum(len(a) for _, _, a in INSTANCES)
    print(f"OPC-UA WTP Simulator")
    print(f"  Endpoint:  opc.tcp://127.0.0.1:{PORT}/avevawaterSimulator")
    print(f"  Namespace: {URI} (idx={idx})")
    print(f"  Instances: {len(INSTANCES)}  ({attr_count} variables total)")
    print(f"  Interval:  {INTERVAL}s")
    print()
    print("Running... (Ctrl+C to stop)")

    async with server:
        while True:
            for var, gen in variable_nodes:
                value = gen.next()
                if isinstance(gen, OscillatingBool):
                    await var.write_value(bool(value))
                else:
                    await var.write_value(ua.Variant(float(value), ua.VariantType.Float))
            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
