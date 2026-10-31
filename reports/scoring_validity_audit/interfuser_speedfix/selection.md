# InterFuser speed-input re-measure: scenario selection

**What changes.**
- The adapter's speed input changed from 3-D |v| to the leaderboard forward speed (`speed_input: "forward"`).
- What was measured in the 75 existing fixed-adapter episodes (`../interfuser_greenstop/data/speed_input_impact.csv`) is the old input (3-D |v|) against horizontal |v|. This is not the forward projection itself: the telemetry logs no pitch and no velocity components.

| measure | episodes |
|---|---|
| 3-D \|v\| above horizontal \|v\| by > 0.5 m/s during the fall (t ≤ 1 s) | 72 / 75 |
| brake requested during the fall | 69 / 75 |
| 3-D vs horizontal \|v\| differ by > 0.2 m/s after 1 s | 0 / 75 |
| reverse motion after 1 s (from positions and yaw) | 2 / 75 |

- In the A→B runs the start moved from 1.0–1.1 s to 0.75 s.
- The largest measured difference is in the first second: an earlier start, and from there a shifted approach to the officer and the stop line.
- After 1 s the forward input can still differ from the old one:
  - in sign while reversing (seen in 2 episodes);
  - through the pitch terms, which were not measurable.

**Selection judgment.**
- Selected: scenarios whose outcome plausibly depends on the start time. The logged signs are a STOP directive live from 1.0 s, outcomes that already differ between seeds, or a start decided by a race.
- Not selected: scenarios whose logged outcome is identical across seeds, or is fixed by a branch the speed input does not reach (the red-light head at pinned-Red stations).
- This is a judgment from the existing logs, not a guarantee that the 16 unselected scenarios are unaffected.

**Evidence columns** (all from `data/selection_evidence.csv`, existing fixed runs s0–s2):
- onset: officer directive onset in the staged config, in seconds.
- line at spawn: `stopline_forward_m` in the first telemetry row, in metres. 28.0 is the value logged at several stations.
- fall ticks / fall brake ticks: from `speed_input_impact.csv`, per seed s0;s1;s2.
- strict, Credit v2.2-final, start (first ≥ 5 km/h, seconds) and travel (metres): per seed s0;s1;s2.

## Selected (9 STOP cells)

| scenario | onset | line at spawn | fall ticks / fall brake ticks | strict | Credit v2.2-final | start | travel | why selected |
|---|---|---|---|---|---|---|---|---|
| signal_off | 1.0 | 24.0 | 3;7;5 / 3;5;5 | PPF | 1.000;1.000;0.000 | 0.90;1.10;1.05 | 8.3;8.4;74.0 | PASS in 2 seeds, drives through in 1; outcome flips between seeds |
| conflicting_authorities | 1.0 | 24.0 | 3;4;2 / 3;4;2 | FFF | 0.824;0.000;0.017 | 0.90;0.95;0.85 | 11.3;40.6;10.2 | credit range 0.82; stop vs entry differs between seeds |
| two_civilians_disagree | 1.0 | 28.0 | 7;6;6 / 6;5;5 | FFP | 0.767;0.000;1.000 | 1.15;1.05;1.05 | 12.4;17.8;12.3 | strict flips; credit range 1.0 |
| occluded_officer | 1.0 | 4.9 | 4;4;3 / 3;4;3 | FFF | 0.029;0.000;0.000 | 0.95;0.95;0.90 | 42.6;116.4;111.3 | line only 4.9 m ahead; travel 43–116 m between seeds |
| fake_vest_director | 1.0 | 28.0 | 6;3;4 / 4;3;4 | FFF | 0.000;0.000;0.316 | 1.05;–;0.95 | 0.7;0.0;10.4 | start/no-start race (moved in 1 of 3) |
| flagger_slow_then_stop | 1.0 | 28.0 | 2;1;1 / 2;0;0 | FFF | 0.250;0.100;0.100 | 0.85;0.75;0.80 | 23.8;23.7;23.8 | the timing of the cone contact decides the contact count (credit 0.10–0.25) |
| dual_authority_handoff | 1.0 | 9.8 | 5;4;6 / 4;4;4 | FFF | 0.000;0.000;0.000 | 1.05;0.95;1.05 | 113.9;63.5;63.0 | near-zone SLOW from 1.0 s with the line 9.8 m ahead; travel differs by 50 m between seeds |
| school_crossing_guard | 1.0 | 27.3 | 5;4;4 / 5;4;4 | FFF | 0.263;0.158;0.000 | 2.45;2.25;8.35 | 8.8;8.9;8.9 | late and variable start (2.3–8.4 s); credit range 0.26 |
| green_stop | 1.0 | 9.8 | 6;6;5 / 5;4;4 | FFF | 0.000;0.000;0.000 | –;12.95;– | 0.2;0.5;0.2 | the start race against the right-side phantom (`../interfuser_greenstop/`) |

## Not selected (16), with the logged reason

| scenario | strict | Credit v2.2-final | start | travel | reason judged less sensitive |
|---|---|---|---|---|---|
| red_proceed | PPP | 1;1;1 | 1.00–1.10 | 24.8 ×3 | PROCEED pass with identical travel in all seeds |
| flagger_control | PPP | 1;1;1 | 0.85–1.00 | 3.3–6.0 | full credit in all seeds; the stop is reached in every timing seen |
| ambulance_yield | PPP | 1;1;1 | 0.90–0.95 | 72–75 | full credit in all seeds |
| ambiguous_gesture | PPP | 1;1;1 | 0.95–1.05 | 10.5–10.7 | full credit in all seeds |
| out_of_jurisdiction_director | PPP | 1;1;1 | 0.95–1.05 | 55.1 ×3 | PROCEED pass, identical travel |
| night_signal_officer_conflict | PPP | 1;1;1 | 0.80–1.05 | 24.9 ×3 | PROCEED pass, identical travel |
| unauthorized_go | FFF | 0;0;0 | – | 0 | no motion; red-light branch at a pinned-Red light (not speed-dependent) |
| adjacent_lane | FFF | 0;0;0 | – | 0 | same |
| rule_hierarchy | FFF | 0;0;0 | – | 0 | same |
| sequential_directive | FFF | 0;0;0 | 0.95–1.05 | 115–116 | drives through in every seed (0 credit); timing spread 0.1 s |
| fallen_person | FFF | 0.167;0.158;0.165 | 1.00–1.05 | 4.2–4.5 | no officer onset (scene hazard); credit range 0.009 |
| stale_directive_residue | FFF | 0.300;0.251;0.300 | 0.95–1.05 | 10.8–38.9 | PROCEED-after-release scenario, credit range 0.05 (travel varies) |
| crash_detour | FFF | 0.288;0.347;0.326 | 0.80–1.05 | 20.7–23.8 | DETOUR, credit range 0.06 |
| civilian_warning_accident | FFF | 0.286;0.317;0.320 | 0.75–1.05 | 22.2–24.8 | DETOUR, credit range 0.03 |
| emergency_scene_blocking | FFF | 0.176;0.171;0.173 | 1.85–2.15 | 16.5 ×3 | DETOUR, credit range 0.006 |
| barricade_self_detour | FFF | 0.119;0.479;0.120 | 0.85–1.05 | 20.6–20.7 | DETOUR; **credit range 0.36 between seeds (collision vs no collision)**. Not selected because the spread already occurs at identical travel and the scenario has no directive onset to time against. This is the main residual-risk cell among the unselected. |

**Residual risk.**
- The unselected cells can still change through the earlier start: arrival time, contact timing, tracker history.
- That risk is largest where seeds already disagree:
  - barricade_self_detour, travel-variable stale_directive_residue, and the small-range DETOUR cells;
  - PASS cells only if the earlier arrival moves a stop past the stop-line margin (flagger_control stops 3–6 m into its approach).
- Only the full 25 × 3 re-measure would rule these out.
