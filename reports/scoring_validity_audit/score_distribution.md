# Score distribution: endpoints, the 114 partial cases, and zero causes

Generated tables:

- [`data/distribution_tables.md`](data/distribution_tables.md): cross-tabs by strict verdict, action, model and
  scenario; zero-term overlaps, combinations and the exclusive class; motion classes.
- [`data/episode_decomposition.csv`](data/episode_decomposition.csv): every component of all 665 episodes.
- [`data/partial_credit_episodes.csv`](data/partial_credit_episodes.csv): the 114 partial cases, each with every
  component and a per-episode explanation.

All counts below are **executed**, recomputed from the recorded telemetry with the pinned scorers.

## 1. Verified counts (leaderboard, 650 episodes)

| class | episodes | share (episode-weighted) | share (model-balanced) |
|---|---:|---:|---:|
| credit = 1 | 95 | 0.146 | 0.180 |
| 0 < credit < 1 | 114 | 0.175 | 0.218 |
| credit = 0 | 441 | 0.678 | 0.602 |

- **Endpoints are exact, not display rounding.** All 95 full-credit episodes store exactly 1.0, and all 441 zeros
  store exactly 0.0. Partial credits range from 0.021416 to 0.9405.
- **Tolerance:** 1e-6 is used, against stored credit at 6 decimals.
- **Model-balanced share:** each of the 14 models gets weight 1, with VLM runs averaged. This removes the 5× weight of
  the five-run VLMs.

## 2. Strict × graded

| | credit = 1 | partial | credit = 0 |
|---|---:|---:|---:|
| PASS (102) | 95 | 7 (all `ambulance_yield`) | 0 |
| FAIL (548) | 0 | 107 | 441 |

- **credit = 1 ⇒ PASS** holds without exception.
- **PASS ⇒ credit = 1** fails for 7 episodes. They are a strict/graded window mismatch on YIELD
  ([scoring_formulation.md §3](scoring_formulation.md)).

## 3. What the 114 partial cases are

| expected action | partial | median credit | max | typical limiting term |
|---|---:|---:|---:|---|
| STOP | 57 | 0.178 | 0.821 | approach-speed credit = 0, giving the 0.25 creep cap (finding F1); speed margin when rolling during enforcement |
| PROCEED | 20 | 0.269 | 0.941 | no junction entry (no-entry branch ≤ 0.30); hold factor 0.3 on `stale_directive_residue` |
| DETOUR | 18 | 0.132 | 0.358 | lateral clearance < 1.5 m |
| YIELD | 17 | 0.701 | 0.935 | slow-or-stop speed inside the ambulance window (7 of these are strict PASS) |
| HOLD | 2 | 0.359 | 0.691 | engagement / speed margin |

**Smallest term per partial episode:**

| smallest term | episodes |
|---|---:|
| `approach_speed` | 31 |
| `lateral_clearance` | 21 |
| `speed_margin` | 16 |
| `slow_or_stop` | 7 |
| `forward_progress_without_entry` | 5 |
| `directive_hold` | 5 |
| other | 29 |

**Partial episodes by model:**

| model | partial episodes |
|---|---:|
| Qwen2.5-VL | 25 |
| TCP | 17 |
| NEAT | 16 |
| AIM | 12 |
| GLM-4.5V | 11 |
| Qwen3-VL | 11 |
| InterFuser | 7 |
| baseline | 4 |
| CILRS | 4 |
| OpenEMMA | 3 |
| TransFuser | 2 |
| MPC | 1 |
| oracle | 1 |

The oracle's partial cell is `civilian_warning_accident`, at 0.25 after its single pedestrian contact.

The partial cases do not form one category. They are the episodes where every multiplicative term is non-zero, and
at least one is fractional.

## 4. Why 441 episodes are exactly zero

**Exclusive classification** by the first zero term, in the order invalid → movement gate → action credit → latency →
safety:

| first zero term | episodes |
|---|---:|
| movement gate | 217 |
| action credit | 146 |
| safety factor | 70 |
| latency factor | 8 |
| invalid | 0 |

**Overlapping flags** (one episode can have several zero terms):

| term | episodes where it is 0 |
|---|---:|
| action credit | 299 |
| movement gate | 217 |
| safety | 76 |
| latency | 74 |

The exact combinations are in the distribution tables. The largest is "movement gate + action credit", with 152
episodes. Removing one zero term would often leave another, so the episode would stay at 0.

**The earlier informal categories do not partition the zeros.** Those categories were 197 movement, 122 pass-through,
77 collision and 22 detour, totalling 418. They are **behavioural labels taken from the strict failure reason**, not
the mathematical cause of zero credit. With all labels counted they total 441:

| strict-reason label | episodes |
|---|---:|
| never engaged / no movement | 197 |
| conflict-zone entry | 122 |
| collision | 77 |
| detour incomplete | 22 |
| other strict reasons (handoff transit, near-line, yield and PROCEED-start failures) | 23 |

The labels and the zero terms differ. For example:

- "never engaged" (197) is the strict reason for 197 episodes, but the graded movement gate is 0 in 217. The extra 20
  are AIM sub-5 km/h motion, landing jitter, post-window motion, and one reverse case.
- 77 episodes have "collision" as their strict reason, but the safety factor is the first zero term in only 70 and is
  0 in 76. A collision with 1 callback event leaves F_safe = 0.25, and some colliding episodes are already zero
  through the movement gate.

## 5. Endpoint mechanism

Three structural properties of the v2 credit put mass at the endpoints. All three are inspected in code and
reproduced on synthetic telemetry.

1. **Calibration to strict.** Every curve saturates at the strict threshold: speed ≤ 1 km/h, f ≥ 1.0 m, and the
   strict-compliant STOP/HOLD branch sets e = 1. So meeting the strict conditions gives exactly 1.
   - NEAT (f = 1.19 m) and the oracle (4.32 m) are both 1.0. Clearance beyond 1 m is not rewarded.
2. **Multiplicative gates.**
   - The movement gate is binary.
   - The safety factor is effectively binary, because collision events are counted per callback (finding F3).
   - Pass-through at speed drives both STOP speed and clearance terms to 0.
   - Any one of these zeroes the product.
3. **What the systems actually do.** The episodes are dominated by behaviour far from every threshold:
   - parked vehicles (197 no meaningful motion);
   - 18–26 km/h drive-throughs (`green_stop`: f_min −48 to −74 m);
   - sustained contacts with hundreds of callback events.

   Threshold perturbations of ±25% flip at most 6 strict verdicts and change at most 6 zeros
   ([threshold_sensitivity.md](threshold_sensitivity.md)). The concentration is therefore driven by behaviour and
   structure, not by threshold placement.

**What this does not show.** The concentration alone neither invalidates continuous scoring nor validates it. The
validity problems found are specific:

- F1: onset-dependent engagement;
- F2: an instruction-independent "lurch-then-stop" policy reaches 59.8 graded (synthetic);
- F3: collision-event counting;
- F4: a faster overshoot outscoring a slow creep.

They are listed in [audit_summary.md](audit_summary.md).

## 6. Component sensitivity

These are diagnostic sensitivity analyses on the recorded telemetry. They are not official replacements.

| change (one at a time) | effect | source |
|---|---|---|
| A′: motion-anchored engagement window | 27 STOP/HOLD episodes change; TCP 30.33 → 44.12, AIM 13.23 → 16.89; τ = 0.912 vs A | [scoring_alternatives.md](scoring_alternatives.md) |
| distinct-contact collision count (events < 1 s apart on the same actor merged) | 71 episodes become non-zero (lower bound: only 20 events are logged per episode) | `data/collision_semantics.json` |
| movement-gate speed threshold ×0.75 … ×1.25 | zeros 435 … 447; 0 strict flips; τ ≥ 0.912 | [threshold_sensitivity.md](threshold_sensitivity.md) |
| creep cap ×0.75 … ×1.25 | 0 flips; max Δgraded 1.46 | same |
