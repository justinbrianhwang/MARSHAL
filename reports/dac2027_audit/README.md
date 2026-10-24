# Evaluation and timing audit (green_stop as the worked case)

What MARSHAL counts as good behaviour, how measurements become partial credits and scores, how simulation,
sensor, decision and control timing actually behave, and how far the existing experiments meet those
definitions. green_stop is the fully worked case; `05_benchmark_extension.md` maps the structure to all 25
scenarios.

| File | Content |
|---|---|
| `01_requirements_status.md` | requirement × code × logs × status × missing evidence × work type (A/B/C/D) × priority |
| `02_evaluation_spec_green_stop.md` | variables, Ideal / Worst acceptable / Oracle, Strict vs Graded vs MARSHAL Score, formulas, per-command functions, worked examples, open design issues |
| `03_timing_audit.md` | simulator clock, sensor and input frames, decision cadence, decision → control, latency, what lock-step results do and do not show |
| `04_green_stop_results.md` | reference vs model behaviour, raw → component → score tables, hold/release, timelines |
| `05_benchmark_extension.md` | common vs command-specific items, aggregation, re-scoring feasibility |
| `06_reproduction.md` | commands, environments, original vs re-analysed data, code changes |
| `tables.md` | generated tables T1–T3 |
| `data/` | per-episode CSVs (score trace, strict gates, timing, suite cells, variants, synthetic cases) |
| `figures/` | stop-line/speed time series, lateral offset, hold panels, per-run timelines |

Evidence labels: [CODE] source, [LOG] recorded episode, [CONFIG] configured but not measured, [DOC] existing
document claim, [PROPOSAL] new and not implemented, [UNVERIFIED] not checkable with available data.

Headline facts:

- 350/350 leaderboard cells and all 14 model scores re-score exactly to the published values from telemetry.
- The world advances exactly one 0.05 s step per tick; inference never advances simulation time (the
  simulation is about 4–15× slower than real time and waits for every model).
- E2E adapters consume the current sensor frame; the VLM dash-cam frame is 1–6 ticks old.
- Decisions: 20 Hz (oracle, TCP, InterFuser, NEAT, AIM, CILRS, PID, MPC), 10 Hz (TransFuser), every 3 s
  (OpenEMMA), 3 queries per episode (Track-C leaderboard) or every 1.5 s (uncapped diagnostics); a VLM
  decision acts on its own query tick and is held until the next one.
- Open issues: whole-episode engagement, 1.05 s hold verification on the leaderboard, centre-based clearance,
  "too far" unbounded inside the band, undetected stale inputs, single-episode VLM cells.
