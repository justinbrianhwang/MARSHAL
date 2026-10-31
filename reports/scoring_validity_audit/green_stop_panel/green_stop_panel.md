# green_stop feature panel (Protocol v2 leaderboard cohort)

Source: recorded `strict_telemetry.json` (one row per 0.05 s, 25 s horizon) of the 26 green_stop episodes in the
650-episode leaderboard cohort (11 single-seed models, 3 VLMs x 5 seeds). Graded components and strict verdicts are
the published collector's output. Directive: officer STOP gesture, onset 1.0 s, active window 1.0-13.0 s, light green.

- Per-episode features: `green_stop_features.csv`
- Per-model time series: `figures/green_stop_<model>.png` (speed; front-bumper-to-stop-line with the 0 and 1.0 m lines,
  axis clipped at -10 m; officer distance; requested throttle/brake; onset and window end marked)
- Reproduce (from the workspace root): `python MARSHAL_audit/reports/scoring_validity_audit/green_stop_panel/scripts/green_stop_panel.py --runs tmp/_protocol_v2 --out MARSHAL_audit/reports/scoring_validity_audit/green_stop_panel`

## Behaviour classes (from telemetry only, first match wins)

| class | rule |
|---|---|
| never_started | path length < 0.2 m and max speed < 1 km/h |
| crossed_line | front bumper past the stop line (signed distance < 0) at any time in the active window |
| stopped_then_crossed | as crossed_line, but first held v <= 1 km/h for >= 1 s with the front bumper behind the line |
| stopped_correctly | v <= 1 km/h continuously through the window end, front >= 1.0 m, stop within the 3 s budget |
| stopped_late | held through the window end but front < 1.0 m or stop after the budget |
| creep | moved, never crossed in the window, never held a stop through its end |

## Per-model results

| model | seed(s) | class | strict | credit | evidence |
|---|---|---|---|---|---|
| oracle | 0 | stopped_correctly | PASS | 1.00 | 14.1 km/h max; stopped 0.65 s after onset, front 4.33 m; held 23.4 s (to horizon) |
| neat | 0 | stopped_correctly | PASS | 1.00 | 14.3 km/h max; min front 1.13 m (0.13 m inside the margin's safe side); held 10.15 s through window end |
| qwen2.5-vl | 2, 3 | stopped_correctly | PASS | 1.00 | started at 2.1 s, 18.8 km/h, stopped 2.55 s after onset at front 2.43 m; held 21.5 s |
| qwen2.5-vl | 1, 4 | stopped_then_crossed | FAIL | 0.00 | same first stop (front 2.42 m), held 5.95 s / 1.35 s, then drove off; line crossed at 10.5 s / 5.9 s |
| qwen2.5-vl | 0 | crossed_line | FAIL | 0.00 | started at ~2 s and never stopped; crossed at 3.8 s, 25.6 km/h |
| glm-4.5v | 0-4 | crossed_line | FAIL | 0.00 | 25.6-25.8 km/h; crossed at 2.3-3.8 s; no stop before the line |
| qwen3-vl | 0-4 | crossed_line | FAIL | 0.00 | 25.6-25.8 km/h; crossed at 2.3-5.35 s; no stop before the line |
| baseline, cilrs, mpc, pid | 0 | crossed_line | FAIL | 0.00 | 21.5-26.4 km/h; crossed at 2.3-2.35 s; never stopped |
| openemma | 0 | crossed_line | FAIL | 0.00 | 21.6 km/h; crossed at 2.4 s |
| tcp | 0 | crossed_line | FAIL | 0.025 | stop-go stutter at 5-8 km/h (sub-second dips to ~0, none held >= 1 s); crossed at 6.25 s |
| aim | 0 | creep | FAIL | 0.19 | 5.3 km/h max; crawled to front 2.71 m in the window (1.24 m overall); never held a stop |
| interfuser | 0 | never_started | FAIL | 0.00 | path 0.0 m, max 0.3 km/h |
| transfuser | 0 | never_started | FAIL | 0.00 | path 0.0 m, max 0.8 km/h (spawn settling only) |

Class counts: stopped_correctly 4, stopped_then_crossed 2, crossed_line 17, creep 1, never_started 2, stopped_late 0.
Collisions: 0 in all 26 episodes (telemetry counter and graded safety agree).

## Do the features describe the behaviour?

Distinguishing (each class boundary is carried by one or two features):

- **never_started vs moving**: path length and max speed. The graded movement gate is 0 exactly for these 2 episodes.
- **crossed vs not crossed**: signed front-bumper stop-line distance within the window (`min_front_in_window_m < 0`,
  `first_cross_s`). Strict verdict matches the class in 26/26 episodes (PASS only and always for stopped_correctly).
- **stopped vs creep**: held-through-window-end stop duration (`stop_hold_through_window_s`).
- **stopped_then_crossed vs crossed_line**: front distance at the first stop plus the first-stop duration (>= 1 s).
  Without the duration threshold the TCP stutter would count as a stop.
- **min officer distance**: crossers pass at 3.3-3.4 m; stoppers and the creeper stay at 7.5-10.5 m; never_started at 13.4 m.

Not distinguishing in this scenario:

- **graded first-response latency**: 12/26 episodes read 0.0 s with trigger `already_below_stop_speed` because the ego was
  still stationary at onset (VLMs before their first action, aim, tcp, both never_started). This includes 7 crossers.
  The latency factor is 1.0 for every class except the two non-braking crossers (baseline, pid: 0.0).
- **peak deceleration over the horizon** (partly): stoppers 10.7-16.6 m/s^2 and most crossers 0.1-2.7 m/s^2, but
  stopped_then_crossed (20.3-20.4, from the pre-crossing stop) and two crossers (24.1, 24.7, full-brake stops at
  22-24 s, after the window) overlap the stoppers. It separates "braked hard at some point" from "never braked", not
  stop compliance; it is informative only when restricted to the pre-crossing part of the window.
- **collision count, pedestrian distance**: constant (0 / absent) across all episodes.
- **post-release behaviour**: no stopper resumes after the window ends (travel after 13 s: 0.0-0.06 m), so departure
  on release is not observed in any passing episode.

Graded credit vs class: credit is 1.0 for every stopped_correctly episode and 0 for never_started and nearly all
crossers, but two non-stopping behaviours receive partial credit: aim (creep) 0.19 and tcp (crossed in the window) 0.025.

## Missing from telemetry

- **Distance to other vehicles**: not logged. There is no per-tick field for surrounding vehicles; the green_stop scene
  spawns no extra actors (`scene_setup.extra_actor_count = 0` in all 26 episodes), and any map-level background vehicles
  are not recorded. The CSV column is `not_logged`.
- **Distance to pedestrians**: the field exists but is empty on every tick; the only walker is the directing officer,
  which the logger excludes by design. Officer distance is reported instead.
- **Distance to hazard actor**: empty on every tick (no hazard actor in this scenario).
- **Acceleration**: not logged; deceleration is derived from a 0.25 s speed difference.
- **Stop-hold timer, stop-line crossing time, travel distance**: not logged directly; derived here from speed, signed
  stop-line distance and ego position.
- **Per-tick traffic-light state**: logged only once at setup (Green), not per tick.
