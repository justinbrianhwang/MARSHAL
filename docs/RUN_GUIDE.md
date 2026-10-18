# MARSHAL Run Guide: re-analysis and simulation re-run

This guide gives a new collaborator two routes:

- **Route A (re-analysis):** regenerate the tables and analysis from the logs we provide. No simulator or GPU is needed.
- **Route B (simulation re-run):** load a model, drive it in CARLA, and produce new logs.

**First-milestone check:** you can run the representative scenario (`green_stop`) with at least one model and read the files it produces (§6).

For writing your *own* controller instead of the bundled ones, see [`benchmarking_your_model.md`](benchmarking_your_model.md).

---

## 1. Repository and version

| Item | Value |
|---|---|
| Repository | <https://github.com/justinbrianhwang/MARSHAL> (public, read access for everyone) |
| Branch | `main` |
| Commit to use | The commit that contains this guide (`git log -1 -- docs/RUN_GUIDE.md`). It includes the env-gated diagnostic logging used by the October green_stop experiments. |
| Earlier results | Leaderboard and diagnostic logs were produced by **earlier builds**. The exact code state per result group is listed in the analysis package (`provenance.csv`, Route A). Do not assume every past log came from the current commit. |

```bash
git clone https://github.com/justinbrianhwang/MARSHAL.git
cd MARSHAL
```

All commands below run from the repository root (`<repo>`).

---

## 2. Route A: re-analysis from the provided logs (no CARLA)

The analysis package (`green_stop_share_2026-10-16.zip`, shared separately because of its size) contains:

- the raw episode logs (71 episodes, 21 diagnostic source logs, all brake-calibration runs)
- the analysis scripts
- the full outputs

It uses the Python standard library only (Python ≥ 3.10).

```bash
unzip green_stop_share_2026-10-16.zip -d share && cd share
python scripts/extract_and_analyze.py --root data --out out
python scripts/stop_feasibility.py --audit out \
    --calib data/tmp/_gs_followup/calib4_vlm/brake_calibration_summary.json
```

Expected result:
- Both commands exit with code 0.
- `out/extraction_summary.json` shows 7/7 validation checks `true`.
- `out/stop_feasibility.csv` is byte-identical to `outputs/stop_feasibility.csv`.

Start reading with `share_2026-10-16.md` and `README.md` inside the package.

---

## 3. Execution environment (Route B)

The setup below is what the reported runs used. Other setups may work, but they are untested.

| Component | Version used |
|---|---|
| OS | Windows 11 (scripts are Python; Linux should work but is untested) |
| CARLA | **0.9.16** packaged release, server on `127.0.0.1:2000` |
| GPU | NVIDIA RTX 5090 (32 GB), driver 616.56. Learned models need a CUDA GPU (minimum VRAM not measured) |
| Python | 3.12 in every environment |
| CUDA (PyTorch build) | cu128 (PyTorch 2.10–2.11) |

One conda environment per model family. Exact package lists (`pip freeze`) are in [`run_guide_envs/`](run_guide_envs/):

| Environment | Used for | Key packages |
|---|---|---|
| `marshal` | oracle, baseline, VLM agents (Qwen3-VL, Qwen2.5-VL, GLM-4.5V), scoring, brake calibration | `carla==0.9.16`, numpy, pyyaml, pillow |
| `transfuser_ui` | TransFuser, TCP, AIM, CILRS, NEAT, PID, MPC | torch 2.11+cu128, timm, torch_scatter |
| `interfuser_fl` | InterFuser | torch 2.11 (cu128), timm |
| `openemma` | OpenEMMA (local Qwen2-VL backend or GPT-4o API) | torch 2.10+cu128, transformers, openai |

```bash
conda create -n marshal python=3.12 -y
conda activate marshal
pip install -r requirements.txt
pip install <CARLA_0.9.16>/PythonAPI/carla/dist/carla-0.9.16-cp312-cp312-win_amd64.whl
# model environments: create the same way, then  pip install -r docs/run_guide_envs/<env>.txt
```

The PyTorch `+cu128` wheels need `--extra-index-url https://download.pytorch.org/whl/cu128`. `interfuser_fl` used a PyTorch nightly build (`2.11.0.dev…`); a stable 2.11 cu128 build is expected to work, but it is untested.

The `carla` wheel must come from the same CARLA 0.9.16 release as the server.

---

## 4. Model preparation (code + checkpoints)

Model code and weights are **not** in this repository. Place them under `<repo>/Models/`; the runners look there by default. Every path can also be overridden with the runner flags shown below. Get each checkpoint from the upstream project's README.

| Model | Upstream code (version used) | Checkpoint placement (default path) | Runner flag to override |
|---|---|---|---|
| TransFuser | [TransFuser_UI_V2](https://github.com/justinbrianhwang/TransFuser_UI_V2) (wrapper around autonomousvision/transfuser, 2022 models) → `Models/TransFuser/TransFuser_UI_V2` | `Models/TransFuser/checkpoints/models_2022/transfuser/` | `--ckpt-dir` |
| TCP | [Thinklab-SJTU/Bench2DriveZoo](https://github.com/Thinklab-SJTU/Bench2DriveZoo) @ `8a08b07` (TCP/ADMLP branch) → `Models/TCP/Bench2DriveZoo_tcp_admlp` | `Models/TCP/checkpoints/tcp_b2d.ckpt` | `--tcp-ckpt` |
| AIM, CILRS | [autonomousvision/transfuser](https://github.com/autonomousvision/transfuser) @ `6d1d042` (2021 release) → `Models/AIM/src`, `Models/CILRS/src` | `Models/AIM/aim/best_model.pth`, `Models/CILRS/cilrs/best_model.pth` | `--aim-ckpt/--aim-src`, `--cilrs-ckpt/--cilrs-src` |
| NEAT | [autonomousvision/neat](https://github.com/autonomousvision/neat) @ `3498789` → `Models/NEAT/src` | `Models/NEAT/neat/best_encoder.pth`, `best_decoder.pth` | `--neat-encoder/--neat-decoder/--neat-src` |
| InterFuser | [opendilab/InterFuser](https://github.com/opendilab/InterFuser) @ `f0be8ea` → `Models/InterFuser` | `Models/InterFuser_ckpt/interfuser.pth` | `--interfuser-ckpt/--interfuser-root` |
| OpenEMMA | [taco-group/OpenEMMA](https://github.com/taco-group/OpenEMMA) → `Models/OpenEMMA/OpenEMMA` | Qwen2-VL weights fetched by the upstream loader; GPT-4o backend uses the OpenAI API | `--backend qwen2vl \| gpt-4o` |
| VLM agents (Qwen3-VL, Qwen2.5-VL, GLM-4.5V) | none (Hugging Face Inference API) | no local weights | `--model <HF model id>` |
| oracle, baseline, PID, MPC | built into `marshal_bench/controllers/` | — | — |

**API keys** (never commit them):
- VLM agents read a Hugging Face token from `HF_API_KEY` / `HUGGINGFACEHUB_API_TOKEN`, or from a local `API KEYS.txt` next to the repository.
- The OpenEMMA GPT-4o backend reads `OPENAI_API_KEY`.

---

## 5. Running a simulation (Route B)

### 5.1 Start CARLA and load Town03

```bash
CarlaUE4.exe -carla-rpc-port=2000 -quality-level=Epic -windowed -ResX=640 -ResY=480   # Linux: ./CarlaUE4.sh ...
conda activate marshal
python scripts/_load_town03.py
```

### 5.2 Run one scenario with one model

All runners take scenario keys as positional arguments. `green_stop` is the representative scenario: green light, a police officer signals STOP.

**Always** pass your own output paths. The defaults write into shared `tmp/` files and would overwrite earlier results.

```bash
# oracle / baseline (env: marshal). Output root via env var; summary JSON is fixed to tmp/_reference_sweep.json
MARSHAL_OUT_ROOT=runs/oracle python scripts/_run_reference_staging_sweep.py green_stop --controller oracle

# VLM agent (env: marshal)
MARSHAL_OUT_ROOT=runs/qwen3 python scripts/_run_vlm_test.py green_stop \
    --model Qwen/Qwen3-VL-235B-A22B-Instruct --results-json runs/qwen3.json --report runs/qwen3.md

# TCP / AIM / CILRS / NEAT / PID / MPC (env: transfuser_ui; InterFuser: env interfuser_fl)
MARSHAL_OUT_ROOT=runs/tcp python scripts/_run_more_e2e_sweep.py green_stop --models tcp \
    --results-json runs/tcp.json --report runs/tcp.md

# TransFuser (env: transfuser_ui)
MARSHAL_OUT_ROOT=runs/transfuser python scripts/_run_transfuser_sweep.py green_stop \n    --results-json runs/transfuser.json --report runs/transfuser.md

# OpenEMMA (env: openemma)
python scripts/_run_fullplanner_sweep.py green_stop --controller openemma --backend qwen2vl \
    --out-root runs/openemma --results-json runs/openemma.json --report runs/openemma.md
```

On Windows PowerShell, set environment variables with `$env:MARSHAL_OUT_ROOT = "runs/tcp"` before the command.

- **Full suite:** omit the scenario key to run all 25 scenarios. `python scripts/_run_full_sweep.py` runs every model × scenario with automatic CARLA restarts. It writes into the shared `tmp/` result roots, so use it on a fresh clone only.
- **Built-in quick start:** `python start.py --controller oracle --tag oracle` runs and scores the oracle on all scenarios and writes `outputs/benchmark/<tag>/scoreboard.json`.

### 5.3 Optional diagnostic switches

All of these are unset by default; unset = leaderboard behaviour.

| Variable / flag | Effect |
|---|---|
| `MARSHAL_SEED=<n>` | Override the episode seed (repeatability runs) |
| `MARSHAL_OFFICER_CONDITION=absent \| proceed \| civilian_stop` | Paired controls: no officer / PROCEED gesture / civilian in the same pose |
| `MARSHAL_SPAWN_BACK_M=<m>` | Spawn the ego further back (longer approach) |
| `MARSHAL_NO_STOP_EARLY_TERMINATION=1`, `MARSHAL_TIMEOUT_S=<s>` | Keep recording after the ego stops (e.g. to observe the 13 s gesture end) |
| `MARSHAL_VLM_SAVE_INPUTS=1` | Save the exact image sent at every VLM query |
| `--tcp-no-postprocess` (`_run_more_e2e_sweep.py`) | Disable the TCP throttle post-processing (see §7) |
| `scripts/_brake_calibration.py --out DIR [--prior-throttle 0.65] [--seed-velocity 1.8]` | Brake calibration of the ego vehicle (no model) |

Turn CARLA off when you are finished.

---

## 6. Reading the results

Each episode writes a directory `<out-root>/<episode_id>/`:

| File | Content |
|---|---|
| `metrics.csv` | Per-tick log in long format `t,key,value` (sim time in s): `speed_kmh` (horizontal), `throttle/brake/steer` (applied control, `get_control`), `ego_x/ego_y`, `stopline_forward_m` (signed distance from the vehicle centre to the stop line along the lane, + = before the line), `distance_to_stopline_m` (straight-line), `officer_active`, `tl_state`, `in_junction`, `collision_count` |
| `strict_telemetry.json` / `.csv` | Telemetry rows used for scoring. The JSON also has `raw_*` (controller output), `applied_*`, `wall_monotonic_s`, `ego_yaw_deg` and, on the current commit, `speed_limit_kmh`, `ego_z_m`, `ego_vz_mps` |
| `strict_scoring.json` | Strict verdict: `PASS` / `FAIL` / `INVALID`, the reason and the thresholds used |
| `events.json` | Scene setup, stop-line position, model decisions (`vlm_decision`), end reason (`scenario_finished`) |
| `metadata.json`, `result.json` | Exact configuration (spawn, officer schedule, checkpoint), ground truth, legacy metrics |
| `<model>_debug/<model>_trace.csv` | Per-tick model trace (mode, speed input, control; TCP also `desired_speed` and pre-post-processing control) and a few input images |
| `vlm_inputs/*.png` | Exact VLM query images (only with `MARSHAL_VLM_SAVE_INPUTS=1`) |

**Timing:** a control command sent at tick *t* is applied on tick *t + 1*. `applied_*` / `brake` in `metrics.csv` is the applied value.

**Scoring:**
- **Strict** (`marshal_bench/criteria/strict_episode_scoring.py`) is a gate cascade, not a weighted sum. For STOP scenarios the ego must:
  - not collide;
  - have moved (≥ 5 km/h and ≥ 1 m) at some point;
  - not enter the junction past the line;
  - keep ≥ 1.0 m clearance (vehicle centre);
  - stay below 1 km/h from onset + 3 s until the gesture ends (onset 1.0 s + duration 12.0 s = 13.0 s in `green_stop`).
- **MARSHAL-Graded** (`marshal_bench/criteria/graded_episode_scoring.py`) is a continuous 0–1 credit per episode. `python scripts/_collect_sweep.py` re-scores every recorded episode with the current scorers and writes `outputs/full_sweep_results.json`.
- Metric definitions: [`metrics.md`](metrics.md), [`marshal_graded_score.md`](marshal_graded_score.md), [`evaluation_methodology.md`](evaluation_methodology.md).

**Known scoring caveat:** strict clearance is measured at the vehicle centre. 1.0 m at the centre equals −1.40 m at the front bumper (Tesla Model 3 half-length 2.40 m), so a strict PASS does not guarantee that the bumper stayed behind the line.

---

## 7. What MARSHAL adds to or changes in each original model

Every adapter lives in `marshal_bench/controllers/`. Sensors are attached by MARSHAL; the scene runs synchronously at 20 FPS.

| Model | MARSHAL-side changes / settings |
|---|---|
| All | Ego is a Tesla Model 3 spawned at a curated station (lifted to z = 3.0 m and settled). The speed fed to learned models is the 3D norm, so it includes the settle drop for the first ≈0.4 s. Learned E2E adapters (TransFuser, TCP, AIM, InterFuser): if no synced sensor frame arrives within the timeout, the last control is repeated. |
| TransFuser | Code from TransFuser_UI_V2. `action_repeat 2`, sensor timeout 0.5 s. **Locally modified LiDAR safety box**: always active, fires on ≥ 30 points (brake = 1). |
| TCP | Uses only the trajectory branch (`control_pid`), no blend with the control branch. Post-processing mirrors the Bench2Drive TCP agent: throttle capped at **0.05 above 1.5 m/s** (1.0 m/s when \|steer\| > 0.07), any brake → 1.0. This cap limits TCP to ≈2.2 m/s; disable it with `--tcp-no-postprocess`. |
| InterFuser | Native safety controller (max speed 5 m/s). Adapter zeroes brake < 0.05 and sets throttle to 0 when brake > 0.1. The native controller does not return a brake reason. |
| AIM, NEAT, CILRS | AIM and NEAT use the native `control_pid`; CILRS uses its network's direct control output. Adapter (all three): brake < 0.05 → 0; throttle > brake → brake 0. No speed cap. |
| VLM agents | Decision every 1.5 s, **at most 3 queries per episode** (t = 0.05, 1.55, 3.10 s); a PID follows the last decision. The prompt and parsing are in `marshal_bench/controllers/vlm_model.py`. |
| OpenEMMA | Planner query every 3.0 s; GPT-4o backend with bounded retry on malformed answers. |
| oracle | Privileged comparison reference. It reads ground truth. Do **not** use it as a model under test or as a release-behaviour reference (it does not resume after a STOP gesture ends). |

**Fair-evaluation rule:** only the oracle may read `observation["ground_truth"]`. Any analysis-only information (for example officer visibility) must be logged, never fed to a model's control input.
