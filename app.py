#!/usr/bin/env python3
"""
Monarch v3 — Agnostic Autonomy Kernel (Console Demo)

Upgrades from v2:
- TelemetryAdapter abstraction (truly asset-agnostic)
- Deterministic RNG seed stored in state
- Clock abstraction for real vs. simulated time
- Raw telemetry journal for replay
- Structured LatestState via dataclasses
- Config-driven risk weights & policy thresholds
- Safer EventBus with error isolation
- Explicit "proposals-only" kernel contract; HumanGate is the only place
that can forward approved actions to any actuation layer.
"""

from __future__ import annotations

import argparse
import random
import time
import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, asdict, replace
from typing import Any, Callable, Deque, Dict, List, Optional

# ================================================================
# Time & Determinism Infrastructure
# ================================================================

class Clock(ABC):
"""Abstract clock so we can swap real vs. simulated time."""

@abstractmethod
def now(self) -> float:
...

@abstractmethod
def sleep(self, seconds: float) -> None:
...

class RealClock(Clock):
def now(self) -> float:
return time.time()

def sleep(self, seconds: float) -> None:
time.sleep(seconds)

# ================================================================
# Config & Data Models
# ================================================================

@dataclass
class RiskConfig:
weights: Dict[str, float]
thresholds: Dict[str, float]
policy_version: str = "3.0.0"

def default_risk_config() -> RiskConfig:
return RiskConfig(
weights={
"speed_norm": 0.25,
"temp_norm": 0.15,
"lateral_norm": 0.20,
"obstacle_norm": 0.25,
"comms_drop": 0.10,
"anomaly": 0.15,
},
thresholds={
"watch": 0.30,
"hold": 0.55,
"stop": 0.80,

},
policy_version="3.0.0",
)

@dataclass
class RawTelemetry:
id: str
tick: int
speed_kph: float
coolant_c: float
lane_offset_m: float
obstacle_m: float
comms_ok: bool
ts: float

@dataclass
class NormalizedTelemetry:
id: str
speed_norm: float
temp_norm: float
lateral_norm: float
obstacle_norm: float
comms_drop: float
raw_ts: float

ts: float

@dataclass
class AnomalyPacket:
id: str
anomaly_score: float
ts: float

@dataclass
class RiskPacket:
id: str
risk: float
anomaly: float
ts: float

@dataclass
class Proposal:
id: str
source_risk_id: str
level: str
suggested_action: str
risk: float
anomaly: float

ts: float

@dataclass
class DecisionRecord:
ts: float
level: str
action: str
risk: float

@dataclass
class LatestState:
raw: Optional[RawTelemetry] = None
normalized: Optional[NormalizedTelemetry] = None
anomaly: Optional[AnomalyPacket] = None
risk: Optional[RiskPacket] = None
proposal: Optional[Proposal] = None

# ================================================================
# Journaling (for replay & audit)
# ================================================================

class TelemetryJournal:

"""
Simple in-memory journal of raw telemetry packets.
This is the source-of-truth for deterministic replay.
"""

def __init__(self, max_len: int = 10000):
self.max_len = max_len
self._entries: List[RawTelemetry] = []

def append(self, raw: RawTelemetry) -> None:
self._entries.append(raw)
if len(self._entries) > self.max_len:
self._entries = self._entries[-self.max_len :]

@property
def entries(self) -> List[RawTelemetry]:
return list(self._entries)

# ================================================================
# Core Infrastructure
# ================================================================

class EventBus:
"""Simple pub/sub event bus with error isolation."""

def __init__(
self,
on_error: Optional[Callable[[str, Exception], None]] = None,
):
self.subscribers: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {}
self.on_error = on_error

def subscribe(self, event_type: str, callback: Callable[[Dict[str, Any]], None]):
self.subscribers.setdefault(event_type, []).append(callback)

def publish(self, event_type: str, payload: Dict[str, Any]):
for cb in self.subscribers.get(event_type, []):
try:
cb(payload)
except Exception as exc: # noqa: BLE001
if self.on_error:
self.on_error(event_type, exc)

class StateManager:
"""Central state and audit log, plus latest snapshot."""

def __init__(self, clock: Clock, config: RiskConfig, rng_seed: int):
self.clock = clock
self.config = config

self.latest: LatestState = LatestState()
self.raw_journal = TelemetryJournal()

self.state: Dict[str, Any] = {
"system_status": "INIT",
"last_tick": 0,
"modules": [],
"events_received": 0,
"decisions": [],
"run_id": str(uuid.uuid4()),
"rng_seed": rng_seed,
"policy_version": config.policy_version,
"state_version": "1.0.0",
}
self.audit: List[Dict[str, Any]] = []

# ------------------------------------------------------------
# Basic state helpers
# ------------------------------------------------------------

def now(self) -> float:
return self.clock.now()

def update(self, key: str, value: Any) -> None:
self.state[key] = value

def get(self, key: str, default: Any = None) -> Any:
return self.state.get(key, default)

def bump_tick(self) -> None:
self.state["last_tick"] += 1

def increment_events(self) -> None:
self.state["events_received"] += 1

# ------------------------------------------------------------
# Latest snapshot helpers
# ------------------------------------------------------------

def update_latest(self, **kwargs) -> None:
"""Functional-style update for LatestState."""
self.latest = replace(self.latest, **kwargs)

# ------------------------------------------------------------
# Journaling & audit
# ------------------------------------------------------------

def add_raw(self, raw: RawTelemetry) -> None:
self.raw_journal.append(raw)

def add_audit(
self,

kind: str,
summary: str,
meta: Optional[Dict[str, Any]] = None,
) -> None:
entry = {
"id": str(uuid.uuid4()),
"ts": self.now(),
"kind": kind,
"summary": summary,
"meta": meta or {},
}
self.audit.append(entry)
# Keep last N
self.audit = self.audit[-200:]

def add_decision(self, record: DecisionRecord) -> None:
history: List[DecisionRecord] = self.state.get("decisions", [])
history.append(record)
self.state["decisions"] = history[-50:]

def snapshot(self) -> Dict[str, Any]:
"""Return a serializable snapshot of core state (excluding journal)."""
latest = self.latest
decisions: List[DecisionRecord] = self.state.get("decisions", [])
return {
"state": dict(self.state),

"latest": {
"raw": asdict(latest.raw) if latest.raw else None,
"normalized": asdict(latest.normalized) if latest.normalized else None,
"anomaly": asdict(latest.anomaly) if latest.anomaly else None,
"risk": asdict(latest.risk) if latest.risk else None,
"proposal": asdict(latest.proposal) if latest.proposal else None,
},
"recent_decisions": [asdict(d) for d in decisions[-10:]],
"audit_tail": list(self.audit[-20:]),
}

# ================================================================
# Module Base
# ================================================================

class KernelModule(ABC):
"""Base class for Monarch modules."""

def __init__(self, module_id: str, state: StateManager, bus: EventBus):
self.module_id = module_id
self.state = state
self.bus = bus

@abstractmethod

def on_register(self) -> None:
"""Called once when module is registered."""
...

@abstractmethod
def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
"""Optional direct event hook (rarely needed if using bus.subscribe)."""
...

@abstractmethod
def tick(self) -> None:
"""Called once per kernel tick."""
...

def reset(self) -> None:
"""
Optional hook to reset internal state between runs / replays.
Default is no-op.
"""
# Intentionally empty; override where needed.
return

# Helper to safely convert dataclasses to dicts for bus payloads
def _to_dict(obj: Any) -> Dict[str, Any]:
if hasattr(obj, "__dataclass_fields__"):

return asdict(obj)
if isinstance(obj, dict):
return obj
raise TypeError(f"Cannot convert {type(obj)!r} to dict")

# ================================================================
# Telemetry Adapter (Agnostic Front-End)
# ================================================================

class TelemetryAdapter(ABC):
"""Abstract generator for raw telemetry from any asset."""

@abstractmethod
def generate_raw(self, tick: int) -> RawTelemetry:
...

class DemoVehicleTelemetryAdapter(TelemetryAdapter):
"""
Synthetic vehicle-like telemetry for demos.

NOTE: This is where asset-specific semantics live.
The rest of the kernel just sees RawTelemetry.
"""

def __init__(self, rng: random.Random, clock: Clock, asset_id: str = "demo_vehicle_1"):
self.rng = rng
self.clock = clock
self.asset_id = asset_id

def generate_raw(self, tick: int) -> RawTelemetry:
r = self.rng

speed = 40 + 30 * (1 + r.random()) # 40–100-ish
if r.random() < 0.05:
speed = r.randint(110, 140)

coolant = r.randint(60, 120)
lane_offset = r.uniform(-1.5, 1.5)
if r.random() < 0.03:
lane_offset = r.uniform(-3.0, 3.0)

obstacle_distance = r.uniform(10, 100)
if r.random() < 0.04:
obstacle_distance = r.uniform(1, 15) # sudden obstacle

comms_ok = r.random() > 0.03

return RawTelemetry(
id=f"{self.asset_id}:{uuid.uuid4()}",

tick=tick,
speed_kph=round(speed, 1),
coolant_c=float(coolant),
lane_offset_m=round(lane_offset, 2),
obstacle_m=round(obstacle_distance, 1),
comms_ok=comms_ok,
ts=self.clock.now(),
)

# ================================================================
# Modules
# ================================================================

class TelemetryNormalizer(KernelModule):
"""
Converts RawTelemetry to NormalizedTelemetry and emits `telemetry.normalized`.
"""

def on_register(self) -> None:
self.bus.subscribe("telemetry.raw", self.handle_raw)
self.state.add_audit("module_registered", self.module_id)

def handle_raw(self, payload: Dict[str, Any]) -> None:
self.state.increment_events()

speed = payload["speed_kph"] # 0–140
temp = payload["coolant_c"] # 40–140
lateral = payload["lane_offset_m"] # -3–3
obstacle = payload["obstacle_m"] # 0–100
comms_ok = payload["comms_ok"] # bool

def clamp01(x: float) -> float:
return max(0.0, min(1.0, x))

norm_obj = NormalizedTelemetry(
id=payload["id"],
speed_norm=round(clamp01(speed / 140.0), 3),
temp_norm=round(clamp01((temp - 40.0) / 100.0), 3),
lateral_norm=round(clamp01(abs(lateral) / 3.0), 3),
obstacle_norm=round(1.0 - clamp01(obstacle / 100.0), 3),
comms_drop=0.0 if comms_ok else 1.0,
raw_ts=payload["ts"],
ts=self.state.now(),
)

self.state.update_latest(normalized=norm_obj)
self.bus.publish("telemetry.normalized", _to_dict(norm_obj))

def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
# Not used in this demo; hook kept for future direct event wiring.

_ = event_type, payload

def tick(self) -> None:
# Stateles per-tick for this module; reacts via bus subscriptions.
return

class AnomalyDetector(KernelModule):
"""
Maintains a sliding window of normalized telemetry and emits anomaly scores.
- Listens to: telemetry.normalized
- Emits: telemetry.anomaly
"""

def __init__(self, module_id: str, state: StateManager, bus: EventBus):
super().__init__(module_id, state, bus)
self.window: Deque[Dict[str, Any]] = deque(maxlen=30)

def on_register(self) -> None:
self.bus.subscribe("telemetry.normalized", self.handle_norm)
self.state.add_audit("module_registered", self.module_id)

def handle_norm(self, payload: Dict[str, Any]) -> None:
self.state.increment_events()
self.window.append(payload)

if len(self.window) < 5:
score = 0.0
else:
speeds = [p["speed_norm"] for p in self.window]
temps = [p["temp_norm"] for p in self.window]
s_mean = sum(speeds) / len(speeds)
t_mean = sum(temps) / len(temps)
latest = self.window[-1]
dist = abs(latest["speed_norm"] - s_mean) + abs(
latest["temp_norm"] - t_mean
)
score = max(0.0, min(1.0, dist * 1.5))

packet = AnomalyPacket(
id=payload["id"],
anomaly_score=round(score, 3),
ts=self.state.now(),
)

self.state.update_latest(anomaly=packet)
self.bus.publish("telemetry.anomaly", _to_dict(packet))

def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
_ = event_type, payload

def tick(self) -> None:

return

def reset(self) -> None:
self.window.clear()

class RiskScorer(KernelModule):
"""
Combines normalized signals and anomaly score into a scalar risk index [0,1].
- Listens to: telemetry.normalized, telemetry.anomaly
- Emits: risk.updated
"""

def __init__(
self,
module_id: str,
state: StateManager,
bus: EventBus,
config: RiskConfig,
):
super().__init__(module_id, state, bus)
self.latest_norm: Optional[Dict[str, Any]] = None
self.latest_anom: Optional[Dict[str, Any]] = None
self.config = config

def on_register(self) -> None:

self.bus.subscribe("telemetry.normalized", self.handle_norm)
self.bus.subscribe("telemetry.anomaly", self.handle_anom)
self.state.add_audit("module_registered", self.module_id)

def handle_norm(self, payload: Dict[str, Any]) -> None:
self.state.increment_events()
self.latest_norm = payload
self._maybe_emit()

def handle_anom(self, payload: Dict[str, Any]) -> None:
self.state.increment_events()
self.latest_anom = payload
self._maybe_emit()

def _maybe_emit(self) -> None:
if not self.latest_norm or not self.latest_anom:
return

n = self.latest_norm
a = self.latest_anom["anomaly_score"]

w = self.config.weights
risk = (
w["speed_norm"] * n["speed_norm"]
+ w["temp_norm"] * n["temp_norm"]
+ w["lateral_norm"] * n["lateral_norm"]

+ w["obstacle_norm"] * n["obstacle_norm"]
+ w["comms_drop"] * n["comms_drop"]
+ w["anomaly"] * a
)
risk = max(0.0, min(1.0, risk))

packet = RiskPacket(
id=n["id"],
risk=round(risk, 3),
anomaly=a,
ts=self.state.now(),
)

self.state.update_latest(risk=packet)
self.bus.publish("risk.updated", _to_dict(packet))

def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
_ = event_type, payload

def tick(self) -> None:
return

def reset(self) -> None:
self.latest_norm = None
self.latest_anom = None

class DecisionGate(KernelModule):
"""
Applies a simple policy to risk index and emits proposals.
- Listens to: risk.updated
- Emits: decision.proposal

NOTE: This module only emits proposals, not direct actuation commands.
"""

def __init__(
self,
module_id: str,
state: StateManager,
bus: EventBus,
config: RiskConfig,
):
super().__init__(module_id, state, bus)
self.policy = {
"watch_threshold": config.thresholds["watch"],
"hold_threshold": config.thresholds["hold"],
"stop_threshold": config.thresholds["stop"],
}

def on_register(self) -> None:
self.bus.subscribe("risk.updated", self.handle_risk)

self.state.add_audit("module_registered", self.module_id)

def handle_risk(self, payload: Dict[str, Any]) -> None:
self.state.increment_events()
r = payload["risk"]

if r >= self.policy["stop_threshold"]:
level = "STOP"
action = "Immediate controlled stop"
elif r >= self.policy["hold_threshold"]:
level = "HOLD"
action = "Reduce speed, tighten safety margins"
elif r >= self.policy["watch_threshold"]:
level = "WATCH"
action = "Elevate monitoring, no control change"
else:
level = "LOW"
action = "Nominal"

proposal_obj = Proposal(
id=str(uuid.uuid4()),
source_risk_id=payload["id"],
level=level,
suggested_action=action,
risk=r,
anomaly=payload["anomaly"],

ts=self.state.now(),
)

self.state.update_latest(proposal=proposal_obj)
self.bus.publish("decision.proposal", _to_dict(proposal_obj))

def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
_ = event_type, payload

def tick(self) -> None:
return

class HumanGateAdapter(KernelModule):
"""
Collects decision proposals as if they were sent to a human operator.

In a real system, this is where you plug UI / comms / workflow engines,
and it's the ONLY allowed path from the kernel into any actuation layer.
"""

def on_register(self) -> None:
self.bus.subscribe("decision.proposal", self.handle_proposal)
self.state.add_audit("module_registered", self.module_id)

def handle_proposal(self, payload: Dict[str, Any]) -> None:

self.state.increment_events()

record = DecisionRecord(
ts=payload["ts"],
level=payload["level"],
action=payload["suggested_action"],
risk=payload["risk"],
)
self.state.add_decision(record)

self.state.add_audit(
"proposal",
f"{payload['level']} → {payload['suggested_action']}",
{"risk": payload["risk"], "anomaly": payload["anomaly"]},
)

# NOTE: In a real deployment, this is the ONLY component allowed
# to translate proposals into actuation commands, and only after
# explicit human approval / workflows.

def on_event(self, event_type: str, payload: Dict[str, Any]) -> None:
_ = event_type, payload

def tick(self) -> None:
return

# ================================================================
# Monarch Kernel v3
# ================================================================

class MonarchKernel:
"""
Monarch v3:
- Uses a TelemetryAdapter for asset-agnostic telemetry ingestion
- Journals raw telemetry for deterministic replay
- Provides a human-gated, proposals-only autonomy kernel
"""

def __init__(
self,
config: Optional[RiskConfig] = None,
clock: Optional[Clock] = None,
seed: Optional[int] = None,
adapter: Optional[TelemetryAdapter] = None,
):
self.clock = clock or RealClock()
self.config = config or default_risk_config()

# Seed RNG deterministically for each run
if seed is None:

seed = int(self.clock.now() * 1000) & 0xFFFFFFFF
self.rng_seed = seed
self.rng = random.Random(self.rng_seed)

self.state = StateManager(clock=self.clock, config=self.config, rng_seed=self.rng_seed)
self.bus = EventBus(on_error=self._handle_bus_error)
self.adapter: TelemetryAdapter = adapter or DemoVehicleTelemetryAdapter(self.rng,
self.clock)
self.modules: Dict[str, KernelModule] = {}
self._tick_order: List[str] = [] # explicit, deterministic tick order

# Register default modules in a well-defined order
self._register("telemetry_normalizer", TelemetryNormalizer, tick_enabled=True)
self._register("anomaly_detector", AnomalyDetector, tick_enabled=True)
self._register(
"risk_scorer",
lambda mid, st, bus: RiskScorer(mid, st, bus, self.config),
tick_enabled=True,
)
self._register(
"decision_gate",
lambda mid, st, bus: DecisionGate(mid, st, bus, self.config),
tick_enabled=True,
)
self._register("human_gate", HumanGateAdapter, tick_enabled=True)

self.state.update("system_status", "READY")

# ------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------

def _handle_bus_error(self, event_type: str, exc: Exception) -> None:
self.state.add_audit(
"bus_error",
f"Error handling event {event_type}: {exc!r}",
)

def _register(self, module_id: str, cls_or_factory, tick_enabled: bool = True) -> None:
if isinstance(cls_or_factory, type):
module = cls_or_factory(module_id, self.state, self.bus)
else:
module = cls_or_factory(module_id, self.state, self.bus)

self.modules[module_id] = module
module.on_register()
self.state.update("modules", list(self.modules.keys()))

if tick_enabled and module_id not in self._tick_order:
self._tick_order.append(module_id)

# ------------------------------------------------------------

# Core Tick & Replay
# ------------------------------------------------------------

def _process_raw(self, raw: RawTelemetry) -> None:
self.state.update_latest(raw=raw)
self.state.add_raw(raw)
self.state.add_audit("telemetry", "raw_tick", {"tick": raw.tick})
self.bus.publish("telemetry.raw", _to_dict(raw))

# Deterministic tick order with per-module error isolation
for module_id in self._tick_order:
module = self.modules.get(module_id)
if not module:
continue
try:
module.tick()
except Exception as exc: # noqa: BLE001
self.state.add_audit(
"module_error",
f"Error in tick() of {module_id}: {exc!r}",
)

def tick(self) -> None:
"""Generate one synthetic telemetry tick and process it."""
self.state.bump_tick()
tick_no = self.state.get("last_tick", 0)

raw = self.adapter.generate_raw(tick_no)
self._process_raw(raw)

def reset_for_replay(self) -> None:
"""
Reset kernel state so a raw sequence can be deterministically re-run.
"""
self.state.latest = LatestState()
self.state.raw_journal = TelemetryJournal()
self.state.update("system_status", "INIT")
self.state.update("last_tick", 0)
self.state.update("events_received", 0)
self.state.update("decisions", [])
self.state.audit = []

for module in self.modules.values():
module.reset()

def replay_raw_sequence(self, sequence: List[RawTelemetry]) -> None:
"""
Deterministically re-run the kernel over a stored raw telemetry sequence.
Assumes reset_for_replay() has been called if needed.
"""
self.state.update("system_status", "REPLAY")
for raw in sequence:
self._process_raw(raw)

self.state.update("system_status", "REPLAY_COMPLETE")

# ------------------------------------------------------------
# Heartbeat / Snapshot
# ------------------------------------------------------------

def heartbeat(self) -> Dict[str, Any]:
latest = self.state.latest
decisions: List[DecisionRecord] = self.state.get("decisions", [])
return {
"tick": self.state.get("last_tick", 0),
"status": self.state.get("system_status"),
"modules": self.state.get("modules", []),
"policy_version": self.state.get("policy_version"),
"rng_seed": self.state.get("rng_seed"),
"events_received": self.state.get("events_received"),
"latest_risk": asdict(latest.risk) if latest.risk else None,
"latest_proposal": asdict(latest.proposal) if latest.proposal else None,
"recent_decisions": [asdict(d) for d in decisions[-3:]],
}

# ================================================================
# Console Demo Entry Point
# ================================================================

def run_console_demo(ticks: int, tick_interval: float, seed: Optional[int]) -> None:
cfg = default_risk_config()
kernel = MonarchKernel(config=cfg, seed=seed)

print("Monarch v3 initialized.")
print(f"Run ID: {kernel.state.get('run_id')}")
print(f"RNG seed: {kernel.state.get('rng_seed')}")
print(f"Policy version: {kernel.state.get('policy_version')}")
print("Modules:", ", ".join(kernel.state.get("modules")))
print("----------------------------------------")

try:
for _ in range(ticks):
kernel.tick()
hb = kernel.heartbeat()
tick = hb["tick"]
lr = hb["latest_risk"]
lp = hb["latest_proposal"]

line = f"[tick {tick:02d}] "
if lr:
line += f"risk={lr['risk']:.3f} (anom={lr['anomaly']:.3f}) "
print(line, end="")

if lp:

print()
print(f"→ {lp['level']} :: {lp['suggested_action']}", end="")

kernel.clock.sleep(tick_interval)
print()

except KeyboardInterrupt:
print("\nInterrupted by user, stopping demo...")

print("\nRecent decisions:")
for d in kernel.state.get("decisions", [])[-5:]:
ts_str = time.strftime("%H:%M:%S", time.localtime(d.ts))
print(f" [{ts_str}] {d.level} — {d.action} (risk={d.risk:.3f})")

print("\nMonarch v3 demo complete.")

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
parser = argparse.ArgumentParser(
description="Monarch v3 — Agnostic Autonomy Kernel (Console Demo)",
)
parser.add_argument(
"--ticks",
type=int,
default=20,
help="Number of telemetry ticks to simulate (default: 20)",

)
parser.add_argument(
"--interval",
type=float,
default=0.3,
help="Seconds between ticks (default: 0.3)",
)
parser.add_argument(
"--seed",
type=int,
default=None,
help="Optional RNG seed for deterministic runs (default: derived from time)",
)
return parser.parse_args(argv)

if __name__ == "__main__":
args = parse_args()
run_console_demo(ticks=args.ticks, tick_interval=args.interval, seed=args.seed)
