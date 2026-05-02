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

from asyncua import Server
from dotenv import load_dotenv

load_dotenv()

PORT     = int(os.environ.get("OPCUA_PORT", 4840))
INTERVAL = float(os.environ.get("PUBLISH_INTERVAL", 2))
URI      = "urn:avevawaterSimulator"


class RandomWalk:
    def __init__(self, initial: float, lo: float, hi: float, step: float):
        self.value = float(initial)
        self.lo, self.hi, self.step = lo, hi, step

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
    server.set_endpoint(f"opc.tcp://0.0.0.0:{PORT}/avevawaterSimulator")
    server.set_server_name("AvevaWater OPC-UA Simulator")

    idx = await server.register_namespace(URI)

    objects = server.nodes.objects
    plant_folder = await objects.add_folder(idx, "Plant")
    wtp_folder   = await plant_folder.add_folder(idx, "WTP")

    variable_nodes: list[tuple] = []
    type_folders: dict = {}

    for obj_type, instance_id, attrs in INSTANCES:
        if obj_type not in type_folders:
            type_folders[obj_type] = await wtp_folder.add_folder(idx, obj_type)
        inst_folder = await type_folders[obj_type].add_folder(idx, instance_id)

        for attr_name, gen in attrs.items():
            if isinstance(gen, OscillatingBool):
                var = await inst_folder.add_variable(idx, attr_name, bool(gen.value))
            else:
                var = await inst_folder.add_variable(idx, attr_name, float(gen.value))
            await var.set_writable()
            variable_nodes.append((var, gen))

    attr_count = sum(len(a) for _, _, a in INSTANCES)
    print(f"OPC-UA WTP Simulator")
    print(f"  Endpoint:  opc.tcp://0.0.0.0:{PORT}/avevawaterSimulator")
    print(f"  Namespace: {URI} (idx={idx})")
    print(f"  Instances: {len(INSTANCES)}  ({attr_count} variables total)")
    print(f"  Interval:  {INTERVAL}s")
    print()
    print("Running... (Ctrl+C to stop)")

    async with server:
        while True:
            for var, gen in variable_nodes:
                value = gen.next()
                await var.write_value(value)
            await asyncio.sleep(INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
