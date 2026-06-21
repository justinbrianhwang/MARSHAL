# Benchmarking your model on MARSHAL

This guide shows how to score *your* autonomous-driving model on the 14 MARSHAL
scenarios. You write one small class; MARSHAL does the rest (spawns the officer,
gestures, flagger, ambulance, scene; runs each closed-loop episode; computes the
metric suite and the MARSHAL Score).

## 1. Prerequisites

- CARLA 0.9.16 server running (`CarlaUE4.exe` / `./CarlaUE4.sh`).
- `pip install -r requirements.txt` and a matching `carla` wheel.
- Sanity-check with the built-ins first:

  ```bash
  python start.py --controller baseline --tag baseline   # lower bound
  python start.py --controller oracle   --tag oracle      # upper bound
  ```

## 2. Write a controller

A controller is a subclass of
[`marshal_bench.controllers.base.EpisodeController`](../marshal_bench/controllers/base.py).
The benchmark drives it identically every episode:

```python
setup(world, ego, ground_truth, carla)        # once, before the loop
step(observation, dt) -> carla.VehicleControl  # every sim tick (~20 Hz)
teardown()                                     # once, after the loop
report_target() -> Optional[str]               # optional, for the TAA metric
```

Start from the template:
[`marshal_bench/controllers/example_model.py`](../marshal_bench/controllers/example_model.py).

### The `observation` dict (each tick)

| key | meaning |
|-----|---------|
| `sim_time` | seconds since episode start |
| `ego_x`, `ego_y`, `ego_z` | ego world location (m) |
| `ego_yaw` | ego heading (deg) |
| `ego_speed`, `ego_speed_kmh` | ego speed (m/s, km/h) |
| `tl_state` | nearest traffic-light state (`"Red"`/`"Green"`/`"Yellow"`/…) |
| `in_junction` | bool — ego inside the intersection box |
| `ground_truth` | privileged E-tuple — **oracle only** (see fairness rule) |

**Camera frames.** Each episode writes RGB to a `frames_ego/` folder under the
run's output directory. A sensor model (Track B) or VLM (Track C) reads the
latest frame from there inside `step`.

### Fairness rule

`observation["ground_truth"]` contains the answer (the officer's true gesture,
authority validity, the expected action). **Only the oracle (Track A) may read
it.** A model under test must decide from `ego_*` state, `tl_state`, and the
camera frames. The template ignores `ground_truth` on purpose.

## 3. Run

```bash
python start.py --controller my_pkg.my_model:MyController --tag my_model
```

Useful flags:

| flag | default | meaning |
|------|---------|---------|
| `--scenarios a b c` | all 14 | run a subset |
| `--town` | `Town03` | benchmark map (`Town03_MARSHAL` once baked) |
| `--host` / `--port` | `127.0.0.1` / `2000` | CARLA server |
| `--fps` | `20` | fixed-delta sim rate |
| `--episode-timeout` | `300` | wall-clock seconds before abandoning an episode |
| `--debug` | off | stream per-episode logs + officer debug visuals |

## 4. Read the score

`start.py` prints, and writes to `outputs/benchmark/<tag>/scoreboard.json`:

- per-scenario pass/fail with reasoning tier,
- the **reasoning-tier pass-rate** (low / mid / high) — the headline,
- the metric suite, the R-subscores, and the weighted **MARSHAL Score**.

Copy the final `scoreboard.json` into [`../results/`](../results/) to commit it.

## 5. Track conventions

| track | what it sees | examples |
|-------|--------------|----------|
| **A — oracle** | privileged `ground_truth` | reference upper bound (shipped) |
| **B — sensor/E2E** | camera frames + ego state | TransFuser, InterFuser, TCP |
| **C — VLM** | frames → prompt → action | OpenEMMA, OpenDriveVLA, … |

Set `track = "B"` (or `"C"`) on your controller class so the scoreboard records
which family it belongs to.
