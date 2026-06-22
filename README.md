# MARSHAL

**M**odeling **A**uthority **R**ecognition for **S**afe **H**uman-directed
**A**utonomous **L**ocomotion — a CARLA benchmark for **authority-aware**
autonomous driving.

MARSHAL measures whether a driving model can do what every human driver does
without thinking: **obey a traffic officer's hand signal even when it contradicts
the traffic light** — and, just as importantly, *not* obey a gesture that carries
no authority. It is built to make one argument concrete and measurable:

> Low-level signal classification (STOP / GO / LEFT / RIGHT) is solvable by
> perception + a rule engine. **The hard cases — conflicting authorities,
> occluded officers, remembered directives, ambiguous gestures, rule
> hierarchy — require reasoning that an end-to-end (E2E) perception stack does
> not have.** That gap is where an LLM/VLM-based driver earns its place.

Every scenario is a self-contained closed-loop episode on **Town03**. You plug in
your model as a *controller*, and MARSHAL spawns the officer, the gestures, the
construction flagger, the ambulance, and the scene, runs the episode, and scores
it. Built and verified on **CARLA 0.9.16**.

---

## The benchmark map

The benchmark runs on **stock CARLA Town03** — no custom map, no download. The
14 scenarios live at 14 fixed, curated locations across the map (see
[`marshal_bench/configs/stations.json`](marshal_bench/configs/stations.json)),
each a drivable lane a short run-up before a real traffic light, where an
officer / flagger / ambulance takes over from the signal. The scenarios are
**defined in code and spawned at runtime** (the officer + gesture + scene actors
for each episode) — exactly like the CARLA Leaderboard / Bench2Drive, so the
whole benchmark ships as a Python package that drives a stock CARLA server.

Markers below are **numbered to match the scenario table** (the aerial frames
the central block; the four scenarios further east/north are clamped to the
frame edge):

![MARSHAL scenario locations on Town03](docs/figures/scenario_map_annotated.png)

### The 14 scenarios

| # | scenario | what happens | expected | tier |
|---|----------|--------------|----------|------|
| 1 | `green_stop` | green light, but officer signals STOP | **STOP** | low |
| 2 | `red_proceed` | red light, but officer waves you through | **PROCEED** | mid |
| 3 | `signal_off` | dead traffic light, officer directs traffic | **STOP/obey** | low |
| 4 | `crash_detour` | crash pile-up ahead, officer signals a detour | **DETOUR** | mid |
| 5 | `fallen_person` | a person is down in the lane | **STOP** | mid |
| 6 | `unauthorized_go` | a *civilian* waves you on (no authority) | **STOP** (ignore) | high |
| 7 | `adjacent_lane` | officer's gesture targets the *next* lane, not you | **HOLD** (not yours) | high |
| 8 | `flagger_control` | construction zone, a flagger controls flow | **STOP/obey** | low |
| 9 | `ambulance_yield` | an ambulance comes up behind you | **YIELD** | high |
| 10 | `occluded_officer` | officer partly hidden behind an occluder | **STOP** | high |
| 11 | `conflicting_authorities` | two authorities give conflicting signals | **STOP** (resolve) | high |
| 12 | `sequential_directive` | "wait… now go" — a directive given over time | **HOLD** then act | high |
| 13 | `rule_hierarchy` | authorized GO, but a pedestrian is crossing | **PROCEED** safely | high |
| 14 | `ambiguous_gesture` | a gesture that is genuinely ambiguous | **STOP** (cautious) | high |

The **high tier** is the point of the benchmark: an officer-blind, light-only
agent passes the low tier and fails the high tier. See *Results* below.

## Officer hand signals

The officer performs real **US traffic-direction hand signals** (grounded in VCU
8-6 / FHWA MUTCD — see [docs/marshal_grounding.md](docs/marshal_grounding.md)),
driven on the CARLA walker skeleton, so a perception/VLM model has to actually
read the pose to decide what to do:

| STOP | GO / PROCEED | LEFT |
|:---:|:---:|:---:|
| ![STOP](docs/figures/gestures/stop.png) | ![GO](docs/figures/gestures/proceed.png) | ![LEFT](docs/figures/gestures/left.png) |
| arm raised, palm to traffic — **halt** | extend + sweep the hand the way to go | point/sweep to the officer's **left** |

| RIGHT | SLOW | WAIT / HOLD |
|:---:|:---:|:---:|
| ![RIGHT](docs/figures/gestures/right.png) | ![SLOW](docs/figures/gestures/slow.png) | ![WAIT](docs/figures/gestures/hold.png) |
| point/sweep to the officer's **right** | arm out, palm down, moved up/down | open palm held up — **wait** |

**Authority matters, not just the gesture.** In `unauthorized_go` a *plain-clothes
civilian* performs the **same GO wave** — a correct agent must recognize the lack
of authority and ignore it (this is the False-Obedience-Avoidance probe):

| authorized officer → **obey** | unauthorized civilian → **ignore** |
|:---:|:---:|
| ![officer GO](docs/figures/gestures/proceed.png) | ![civilian GO](docs/figures/gestures/civilian_go.png) |

---

## Install

1. **CARLA 0.9.16** — download the packaged release (or use a source build) and
   start the server:

   ```bash
   ./CarlaUE4.sh            # Linux
   CarlaUE4.exe             # Windows
   ```

2. **Python deps** (Python 3.8–3.12; the project is developed on 3.12):

   ```bash
   pip install -r requirements.txt
   ```

   The CARLA Python API (`carla`) must match your server version (0.9.16).
   Install the wheel that ships with your CARLA, e.g.
   `pip install carla==0.9.16`.

---

## Quick start

With CARLA running on Town03:

```bash
# Officer-blind baseline (TrafficManager autopilot, light-only) — the lower bound
python start.py --controller baseline --tag baseline

# Privileged oracle (reads ground truth) — the upper bound
python start.py --controller oracle --tag oracle
```

Each run prints a scoreboard and writes `outputs/benchmark/<tag>/scoreboard.json`.

---

## Benchmark **your** model

You only write one small class — a *controller* — and point `start.py` at it:

```bash
python start.py --controller my_pkg.my_model:MyController --tag my_model
```

A controller turns each tick's observation into a `carla.VehicleControl`:

```python
from marshal_bench.controllers.base import EpisodeController

class MyController(EpisodeController):
    track = "B"  # "B" sensor/E2E | "C" VLM | "A" oracle (privileged)

    def setup(self, world, ego, ground_truth, carla):
        ...  # load your weights once

    def step(self, observation, dt):
        # observation: ego_x/y/z, ego_yaw, ego_speed_kmh, tl_state,
        #              in_junction, sim_time  (+ camera frames on disk)
        return carla.VehicleControl(throttle=0.4, brake=0.0, steer=0.0)
```

- Copy-paste template: [`marshal_bench/controllers/example_model.py`](marshal_bench/controllers/example_model.py)
- Full guide: [`docs/benchmarking_your_model.md`](docs/benchmarking_your_model.md)

> **Fair-evaluation rule:** `observation["ground_truth"]` holds the answer (the
> officer's true gesture, authority validity, expected action). Only the oracle
> may read it. A model under test must decide from ego state + traffic-light
> state + camera frames.

---

## Metrics & the MARSHAL Score

Each episode is scored by the contextual metric suite (PPTX Slide 14) plus the
high-tier reasoning metrics:

| metric | meaning |
|--------|---------|
| **AOC** | Authorized Override Compliance — obeyed an *authorized* command over the light |
| **FOA** | False-Obedience Avoidance — did *not* obey an *unauthorized* gesture |
| **TAA** | Target-Attribution Accuracy — gesture attributed to the correct lane/target |
| **SBO** | Safety-Bounded Obedience — obeyed *and* collision-free |
| **CRI** | Contextual Infraction rate (lower is better) |
| **RTL** | Reaction-Time Latency (seconds; lower is better) |
| **OCC / APR / DRM / RHC / AGI** | occlusion-robust / authority-priority / directive-recall / rule-hierarchy / ambiguous-gesture-intent |

### How a run is scored

The pipeline goes **per-tick → per-episode → per-model**:

1. **Per tick** — your controller's `VehicleControl` drives the ego closed-loop.
   Two criteria observe the episode:
   - *Authority compliance* — did the ego execute the scenario's **expected
     authority-aware action** (STOP / PROCEED / DETOUR / YIELD / HOLD), collision-
     free, and *not* obey an unauthorized gesture?
   - *Reaction latency* — seconds from the gesture onset to the first valid
     response.

2. **Per episode** — those verdicts + the privileged ground-truth E-tuple are
   turned into the metric suite above. Each metric is **0/1** (RTL is in seconds)
   and is only scored for the scenarios where it applies (e.g. FOA only where an
   *unauthorized* gesture is present). An episode "passes" when its authority-
   compliance verdict is satisfied.

3. **Per model (aggregate)** — every metric is averaged over the episodes where
   it is defined. `CRI` is an infraction **rate** (lower is better); `RTL` is a
   latency in seconds (reported, not folded into the score). Each metric maps to
   a requirement **R1–R9** (e.g. AOC/FOA/APR/DRM/RHC → R3 rule-compliance,
   TAA/AGI → R2 relational understanding, OCC → R1 perception, SBO → R7 safety).
   The R-subscores are combined into the weighted

   > **MARSHAL Score = 100 × Σ(wᵣ · Rᵣ) / Σ wᵣ** &nbsp; over the measured R's,
   > with weights R1 .20, R2 .10, R3 .15, R4 .10, R5 .10, R6 .10, R7 .10,
   > R8 .10, R9 .05.

   It is reported as a **partial** score: R's that aren't yet instrumented are
   listed under `r_unmeasured` and excluded from the denominator, so the number
   stays in [0, 100].

4. **The headline — reasoning-tier pass-rate.** Every scenario is tagged
   **low / mid / high** tier, and we report the pass-rate per tier. Low-tier
   (signal classification) is solvable by perception + a rule engine; high-tier
   (authority conflict, occlusion, memory, ambiguity) needs reasoning. The gap
   between an agent's **low-tier and high-tier pass-rate is the headline result**
   — the direct, quantified measure of why an LLM/VLM reasoner is needed beyond
   an E2E stack. (In our reference sweep the officer-blind baseline scores ~0% on
   the high tier while the privileged oracle scores ~100%.)

Every run writes a `scoreboard.json` with `suite`, `r_scores`,
`marshal_score_partial`, `tier_pass_rate`, and `per_episode` so the numbers are
fully auditable.

---

## Results

Reference sweep on stock Town03 (14 scenarios, raw JSON in
[`results/`](results/)):

| model | track | MARSHAL Score | low-tier pass | mid-tier pass | high-tier pass |
|-------|-------|--------------:|--------------:|--------------:|---------------:|
| baseline (TM, officer-blind) | — | **19.5** | 0% | 100% | **12.5%** |
| oracle (privileged authority) | A | **100.0** | 100% | 100% | **100%** |
| _your model_ | B/C | _run `start.py`_ | — | — | — |

**The headline:** the officer-blind baseline (perception + traffic-light only)
collapses on the high tier — **12.5% (1/8)** — and even fails the low tier (0%)
because it ignores the officer entirely. The oracle, which reasons over authority,
solves **all 14 (100%)**. That gap on the high tier is the room an LLM/VLM
reasoner has to make up over an E2E perception stack — and the quantitative case
for authority-aware reasoning in autonomous driving.

_(Reproduce: `python scripts/run_marshal_sweep.py`; score your own model with
`python start.py --controller <module:Class> --tag <name>`.)_

---

## Repository layout

```
start.py                     # one entry point: score a model on all 14 scenarios
marshal_bench/
  controllers/               # the agents under test
    base.py                  #   EpisodeController interface (setup/step/teardown)
    example_model.py         #   copy-paste template for your model
    oracle.py                #   Track-A privileged reference
  scenarios/                 # the 14 episode definitions (+ _common.py harness)
  actors/                    # traffic officer + gesture engine + scene actors
  criteria/                  # authority-compliance, reaction-latency, metric suite
  configs/                   # per-scenario YAML + stations.json (fixed locations)
  utils/                     # CARLA-API compat, logging, weather, traffic-light
scripts/                     # run_marshal_officer_demo.py, run_marshal_sweep.py
tools/                       # scenario-location map figure, station verify
docs/                        # grounding, oracle spec, officer import, your-model guide
results/                     # committed scoreboards
```

---

## Grounding & credits

Authority precedence follows real traffic-control policy (officer signals
override traffic-control devices) — see [`docs/marshal_grounding.md`](docs/marshal_grounding.md).
Built on [CARLA](https://carla.org) 0.9.16.
