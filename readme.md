Monarch v3 — Agnostic Autonomy Kernel

Monarch v3 is a human-gated, proposals-only autonomy kernel.
The goal: deterministic, auditable safety logic that works with any asset — vehicles, robots,
heavy equipment, whatever — without ever letting autonomy take direct control. The kernel only
proposes, and a human (or human-run workflow) must approve.

This repo includes a console demo using synthetic vehicle telemetry.

What’s New in v3
• Fully abstracted TelemetryAdapter (true asset-agnostic ingestion)
• Deterministic RNG seed stored in state
• Pluggable Clock (real vs. simulated time)
• Raw telemetry journal for deterministic replay
• Strong LatestState snapshots via dataclasses
• Config-driven risk weights & policy thresholds
• Safer EventBus with error isolation
• Strict “proposal-only” contract — only HumanGate can pass actions outward

Architecture Overview

Pipeline
RawTelemetry → Normalization → Anomaly Detection → Risk Scoring →
DecisionGate (Proposals) → HumanGate
Core Concepts

Component Purpose
TelemetryAdapter Asset-specific front-end producing raw telemetry. Swap in anything.
TelemetryNormalizer Converts raw → normalized signals.
AnomalyDetector Sliding-window deviation scoring.
RiskScorer Weighted aggregation → scalar risk index [0,1].
DecisionGate Policy thresholds → WATCH / HOLD / STOP proposals.
HumanGateAdapter Only component allowed to forward actions outside the kernel.
TelemetryJournal Deterministic replay source.
StateManager Global state, audit log, decisions, snapshots.
Determinism
• Every run stores:
o RNG seed
o Raw telemetry sequence
o Audit tail
o Versioned policy + state
• Replays use the exact same inputs, guaranteeing bit-for-bit behavioral reproducibility.

File Structure (Conceptual)
monarch_v3/
│
├── kernel.py # Full Monarch v3 implementation
├── adapters/
│ └── demo_vehicle.py # Synthetic vehicle telemetry generator
├── modules/
│ ├── normalizer.py
│ ├── anomaly.py
│ ├── risk.py
│ ├── decision_gate.py
│ └── human_gate.py
└── README.md
Note: The provided code is self-contained in one file for the demo; the structure
above represents how you’d split it in production.

Running the Console Demo

Basic run

python3 monarch_v3.py
Control runtime
python3 monarch_v3.py --ticks 50 --interval 0.2
Deterministic run
python3 monarch_v3.py --seed 12345
The console prints each tick’s risk + the proposed action:
[tick 07] risk=0.412 (anom=0.033)
→ WATCH :: Elevate monitoring, no control change

Replay Mode

Monarch records all RawTelemetry in a journal.
Replay is guaranteed deterministic:
kernel.reset_for_replay()
kernel.replay_raw_sequence(kernel.state.raw_journal.entries)
Useful for:
• Post-incident review
• Model validation
• Policy tuning
• Deterministic simulation runs

Module Contracts

1. TelemetryAdapter

Your only asset-specific code.
class TelemetryAdapter(ABC):
@abstractmethod
def generate_raw(self, tick: int) -> RawTelemetry:
...
Swap in loaders for:
• CAN bus
• PLC streams
• Industrial sensors
• Drone telemetry
• API / message brokers

2. Proposal Contract

No module can actuate anything.
They can only emit a Proposal:
Proposal(
level="HOLD",
suggested_action="Reduce speed",
risk=0.61,
anomaly=0.12,
)
3. HumanGateAdapter

All real-world effectors must sit behind this adapter.
You must not bypass it.

Risk Model

Default weights:

weights = {
"speed_norm": 0.25,
"temp_norm": 0.15,
"lateral_norm": 0.20,
"obstacle_norm":0.25,
"comms_drop": 0.10,
"anomaly": 0.15,
}
Thresholds:
LOW < 0.30
WATCH >= 0.30
HOLD >= 0.55
STOP >= 0.80
Monarch enforces separation of:
• Signal processing
• Risk scoring
• Policy layer
• Human-controlled actuation

Safety & Philosophy

Monarch assumes:
• Humans must stay in the loop
• Determinism > cleverness
• Replayability = accountability
• Autonomy is assistive, not controlling

Anything that moves heavy equipment or affects safety-critical systems must go through the
HumanGate.

License

MIT
