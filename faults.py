"""Fault injection for the OPC-UA WTP simulator.

Mirrors graccess-mcp/simulator/faults.py so fault injection from the chat UI
affects the same instances/attributes on both the MQTT and OPC-UA simulators.

Each FaultState is attached to one equipment instance. Call tick() once per
publish cycle (before apply()), then apply() per attribute to get the
(possibly transformed) published value.

Equipment type → applicable fault modes:
  Pump         suction_starvation, run_status_fault, pressure_drift, cavitation
  Clarifier    level_sensor_fault, turbidity_spike
  StorageTank  level_sensor_fault, turbidity_spike
  Dosing       dosing_blockage, tank_empty, run_status_fault
  UV           lamp_degradation, lamp_failure
"""

import random
from enum import Enum


class FaultMode(str, Enum):
    NORMAL = "normal"
    # Pump
    SUCTION_STARVATION = "suction_starvation"
    RUN_STATUS_FAULT = "run_status_fault"
    PRESSURE_DRIFT = "pressure_drift"
    CAVITATION = "cavitation"
    # Tank / Clarifier
    LEVEL_SENSOR_FAULT = "level_sensor_fault"
    TURBIDITY_SPIKE = "turbidity_spike"
    # Dosing
    DOSING_BLOCKAGE = "dosing_blockage"
    TANK_EMPTY = "tank_empty"
    # UV
    LAMP_DEGRADATION = "lamp_degradation"
    LAMP_FAILURE = "lamp_failure"


# Fault modes available per equipment type
EQUIPMENT_FAULT_MODES: dict[str, list[FaultMode]] = {
    "Pump":        [FaultMode.NORMAL, FaultMode.SUCTION_STARVATION, FaultMode.RUN_STATUS_FAULT,
                    FaultMode.PRESSURE_DRIFT, FaultMode.CAVITATION],
    "Clarifier":   [FaultMode.NORMAL, FaultMode.LEVEL_SENSOR_FAULT, FaultMode.TURBIDITY_SPIKE],
    "StorageTank": [FaultMode.NORMAL, FaultMode.LEVEL_SENSOR_FAULT, FaultMode.TURBIDITY_SPIKE],
    "Dosing":      [FaultMode.NORMAL, FaultMode.DOSING_BLOCKAGE, FaultMode.TANK_EMPTY,
                    FaultMode.RUN_STATUS_FAULT],
    "UV":          [FaultMode.NORMAL, FaultMode.LAMP_DEGRADATION, FaultMode.LAMP_FAILURE],
}

# Hardcoded instance → equipment type for WTP instances
INSTANCE_EQUIPMENT_TYPE: dict[str, str] = {
    "RawWater_01":    "Pump",
    "RawWater_02":    "Pump",
    "Clarifier_01":   "Clarifier",
    "FinishedWater_01": "StorageTank",
    "Chlorine_01":    "Dosing",
    "Fluoride_01":    "Dosing",
    "UV_01":          "UV",
    "UV_02":          "UV",
    "HighService_01": "Pump",
    "HighService_02": "Pump",
}

# Derived: instance → applicable fault modes
INSTANCE_FAULT_MODES: dict[str, list[FaultMode]] = {
    instance: EQUIPMENT_FAULT_MODES[eq_type]
    for instance, eq_type in INSTANCE_EQUIPMENT_TYPE.items()
}


class FaultState:
    def __init__(self) -> None:
        self.mode: FaultMode = FaultMode.NORMAL
        self._intensity: float = 0.0
        self._drift_offset: float = 0.0

    def set_mode(self, mode: FaultMode) -> None:
        if mode != self.mode:
            self.mode = mode
            self._intensity = 0.0
            self._drift_offset = 0.0

    def tick(self) -> None:
        match self.mode:
            case FaultMode.SUCTION_STARVATION:
                self._intensity = min(1.0, self._intensity + 0.04)
            case FaultMode.PRESSURE_DRIFT:
                self._drift_offset += random.uniform(0.05, 0.15)
            case FaultMode.DOSING_BLOCKAGE:
                self._intensity = min(1.0, self._intensity + 0.04)
            case FaultMode.TANK_EMPTY:
                self._intensity = min(1.0, self._intensity + 0.015)
            case FaultMode.LAMP_DEGRADATION:
                self._intensity = min(1.0, self._intensity + 0.016)

    def apply(self, attr: str, raw: float | bool) -> float | bool:
        match self.mode:
            case FaultMode.NORMAL:
                return raw
            case FaultMode.SUCTION_STARVATION:
                return self._starvation(attr, raw)
            case FaultMode.RUN_STATUS_FAULT:
                return self._run_status(attr, raw)
            case FaultMode.PRESSURE_DRIFT:
                return self._pressure_drift(attr, raw)
            case FaultMode.CAVITATION:
                return self._cavitation(attr, raw)
            case FaultMode.LEVEL_SENSOR_FAULT:
                return self._level_sensor(attr, raw)
            case FaultMode.TURBIDITY_SPIKE:
                return self._turbidity_spike(attr, raw)
            case FaultMode.DOSING_BLOCKAGE:
                return self._dosing_blockage(attr, raw)
            case FaultMode.TANK_EMPTY:
                return self._tank_empty(attr, raw)
            case FaultMode.LAMP_DEGRADATION:
                return self._lamp_degradation(attr, raw)
            case FaultMode.LAMP_FAILURE:
                return self._lamp_failure(attr, raw)
        return raw

    # ── Pump ──────────────────────────────────────────────────────────────────

    def _starvation(self, attr: str, raw: float | bool) -> float | bool:
        i = self._intensity
        if attr == "Running":
            return True
        if attr == "Flow":
            return round(max(0.0, float(raw) * (1.0 - i) + random.uniform(-2.0, 2.0) * i), 2)
        if attr == "Power":
            return round(max(0.0, float(raw) * (1.0 - i * 0.85)), 2)
        if attr == "Pressure":
            return round(float(raw) + random.uniform(-3.0, 3.0) * i, 2)
        return raw

    def _run_status(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "Running":
            return True
        if attr in ("Flow", "Power", "FlowRate"):
            return round(random.uniform(0.0, 1.0), 2)
        if attr == "Pressure":
            return round(random.uniform(0.0, 0.5), 2)
        return raw

    def _pressure_drift(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "Pressure":
            return round(float(raw) + self._drift_offset + random.uniform(-0.1, 0.1), 2)
        return raw

    def _cavitation(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "Running":
            return True
        if attr == "Flow":
            if random.random() < 0.15:
                return round(float(raw) * random.uniform(0.0, 0.2), 2)
            return round(float(raw) * random.uniform(0.6, 1.4), 2)
        if attr == "Pressure":
            return round(float(raw) * random.uniform(0.7, 1.3), 2)
        if attr == "Power":
            return round(float(raw) * random.uniform(1.0, 1.15), 2)
        return raw

    # ── Tank / Clarifier ──────────────────────────────────────────────────────

    def _level_sensor(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "Level":
            return round(max(0.0, min(100.0, float(raw) + random.uniform(-20.0, 20.0))), 1)
        return raw

    def _turbidity_spike(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "Turbidity":
            return round(10.0 + random.uniform(-1.5, 4.0), 2)
        return raw

    # ── Dosing ────────────────────────────────────────────────────────────────

    def _dosing_blockage(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "FlowRate":
            return round(max(0.0, float(raw) * (1.0 - self._intensity)), 2)
        if attr == "Running":
            return True
        return raw

    def _tank_empty(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "TankLevel":
            return round(max(0.0, float(raw) * (1.0 - self._intensity * 1.1)), 1)
        if attr == "FlowRate":
            return round(max(0.0, float(raw) * (1.0 - self._intensity)), 2)
        return raw

    # ── UV ────────────────────────────────────────────────────────────────────

    def _lamp_degradation(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "Intensity":
            return round(max(0.0, float(raw) * (1.0 - self._intensity * 0.8)) + random.uniform(-0.5, 0.3), 1)
        return raw

    def _lamp_failure(self, attr: str, raw: float | bool) -> float | bool:
        if attr == "Intensity":
            return round(random.uniform(0.0, 2.5), 1)
        if attr == "Running":
            return True
        return raw
