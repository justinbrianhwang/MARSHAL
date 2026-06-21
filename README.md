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

The benchmark is built on **CARLA's Town03** — a mid-size urban map whose
signature feature is the central **roundabout with a fountain**. The 14 scenarios
live at 14 fixed, curated locations across the map (see
[`marshal_bench/configs/stations.json`](marshal_bench/configs/stations.json)),
each a drivable lane a short run-up before a real traffic light, where an
officer / flagger / ambulance can take over from the signal.

![Town03 — the MARSHAL benchmark map](docs/figures/town03_aerial.png)

MARSHAL places three lab-logo signposts (**SJB / RAISE / MPS-LAB**) on the
fountain, so the landmark is in view from the surrounding ring road in every
episode:

![Lab-logo landmarks on the Town03 fountain](docs/figures/fountain_logos.png)

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

3. **(Optional) the logo-baked map** — see *The Town03_MARSHAL map* below.

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

These roll up into per-requirement subscores (R1–R9) and a weighted
**MARSHAL Score** in [0, 100]. The headline comparison is the **reasoning-tier
pass-rate** (low vs high) — the direct measure of the E2E-vs-LLM gap.

---

## The Town03_MARSHAL map

The benchmark runs on **stock Town03** out of the box — everything (officer,
gestures, flagger, ambulance, fountain lab-logo landmarks) is spawned at runtime,
so no custom map is required.

A **logo-baked variant, `Town03_MARSHAL`**, bakes the three lab-logo signposts
permanently into the map so the branding is present even without the runtime
spawn. Because a packaged CARLA map is large, it is distributed via Google Drive
rather than committed here:

```bash
python tools/download_map.py        # downloads + installs Town03_MARSHAL
# then:
python start.py --controller baseline --town Town03_MARSHAL
```

> **Status:** the bake/packaging step is in progress; the download link is added
> to `tools/download_map.py` when the packaged map is published. Until then, use
> the default `--town Town03`.

---

## Results

> Scoreboards are committed under [`results/`](results/) and summarized here after
> each experiment run.

| model | track | MARSHAL Score | low-tier pass | high-tier pass |
|-------|-------|--------------:|--------------:|---------------:|
| baseline (TM, officer-blind) | — | _TBD_ | _TBD_ | _TBD_ |
| oracle (privileged) | A | _TBD_ | _TBD_ | _TBD_ |
| _your model_ | B/C | _TBD_ | _TBD_ | _TBD_ |

_(Filled in after running `start.py` on each model; raw JSON in `results/`.)_

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
  utils/                     # CARLA-API compat, landmarks, logging, weather
scripts/                     # run_marshal_officer_demo.py, run_marshal_sweep.py
tools/                       # map figure, landmark prop build, station verify, map download
docs/                        # grounding, oracle spec, officer import, your-model guide
results/                     # committed scoreboards
assets/                      # lab logos + fountain landmark prop source
```

---

## Grounding & credits

Authority precedence follows real traffic-control policy (officer signals
override traffic-control devices) — see [`docs/marshal_grounding.md`](docs/marshal_grounding.md).
Built on [CARLA](https://carla.org) 0.9.16.
