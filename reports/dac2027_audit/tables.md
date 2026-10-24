## T1. green_stop behaviour (raw measurements, front bumper = centre - 2.3959 m)

| Model | Run set | Condition | Seed | End (s) | v at onset (km/h) | First stop (s) | Front at first stop (m) | Min front in window (m) | Longest stop in window (s) | Front crosses line (s) | Max v in enforcement (km/h) | Strict | First failed gate | Credit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oracle | leaderboard | police_stop | - | 5.05 early-term | 17.5 | 1.85 | 1.82 | 1.82 | 3.20 | - | 0.0 | PASS | - | 1.000 |
| baseline | leaderboard | police_stop | - | 14.05 timeout | 11.1 | - | - | -68.97 | 0.00 | 2.35 | 23.8 | FAIL | no_conflict_zone_entry | 0.000 |
| transfuser | leaderboard | police_stop | - | 14.05 timeout | 0.0 | - | - | 7.43 | 12.00 | - | 0.0 | FAIL | engaged | 0.000 |
| tcp | leaderboard | police_stop | - | 7.50 early-term | 0.0 | 2.45 | 5.16 | -0.61 | 0.20 | 7.10 | 6.8 | FAIL | stopped_through_enforcement | 0.171 |
| interfuser | leaderboard | police_stop | - | 14.05 timeout | 0.0 | - | - | 7.44 | 12.00 | - | 0.0 | FAIL | engaged | 0.000 |
| cilrs | leaderboard | police_stop | - | 14.05 timeout | 9.9 | - | - | -74.24 | 0.00 | 2.30 | 26.6 | FAIL | no_conflict_zone_entry | 0.000 |
| aim | leaderboard | police_stop | - | 5.10 early-term | 0.0 | 1.95 | 7.25 | 6.10 | 0.75 | - | 3.6 | FAIL | engaged | 0.000 |
| neat | leaderboard | police_stop | - | 5.05 early-term | 2.9 | 2.60 | 2.27 | 1.30 | 1.60 | - | 0.0 | PASS | - | 1.000 |
| pid | leaderboard | police_stop | - | 14.05 timeout | 11.8 | - | - | -69.15 | 0.00 | 2.30 | 23.3 | FAIL | no_conflict_zone_entry | 0.000 |
| mpc | leaderboard | police_stop | - | 14.05 timeout | 12.0 | - | - | -61.82 | 0.00 | 2.30 | 21.4 | FAIL | no_conflict_zone_entry | 0.000 |
| openemma | leaderboard | police_stop | - | 14.05 timeout | 10.3 | - | - | -62.54 | 0.00 | 2.40 | 21.4 | FAIL | no_conflict_zone_entry | 0.000 |
| glm-4.5v | leaderboard | police_stop | - | 14.05 timeout | 11.8 | - | - | -73.99 | 0.00 | 2.30 | 25.2 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen2.5-vl | leaderboard | police_stop | - | 14.05 timeout | 0.0 | - | - | -63.33 | 1.15 | 3.80 | 26.0 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen3-vl | leaderboard | police_stop | - | 5.05 early-term | 11.7 | 1.90 | 3.01 | 3.01 | 3.15 | - | 0.0 | PASS | - | 1.000 |
| glm45v | authority_wiring | police_stop | 1 | 25.05 timeout | 11.2 | 3.45 | -2.26 | -52.38 | 1.40 | 2.60 | 25.7 | FAIL | no_conflict_zone_entry | 0.000 |
| glm45v | authority_wiring | police_stop | 2 | 25.05 timeout | 0.0 | - | - | -63.30 | 1.15 | 3.80 | 25.5 | FAIL | no_conflict_zone_entry | 0.000 |
| glm45v | authority_wiring | police_stop | 3 | 25.05 timeout | 11.2 | - | - | -73.60 | 0.00 | 2.30 | 25.5 | FAIL | no_conflict_zone_entry | 0.000 |
| interfuser | authority_wiring | police_stop | 1 | 25.05 timeout | 0.0 | - | - | 7.44 | 12.00 | - | 0.0 | FAIL | engaged | 0.000 |
| neat | authority_wiring | police_stop | 1 | 25.05 timeout | 9.3 | 2.45 | 2.23 | 0.66 | 6.95 | 16.60 | 9.1 | FAIL | stopped_through_enforcement | 0.336 |
| neat | authority_wiring | police_stop | 2 | 25.05 timeout | 11.5 | 2.70 | 1.42 | 1.28 | 9.75 | - | 0.0 | PASS | - | 1.000 |
| neat | authority_wiring | police_stop | 3 | 25.05 timeout | 8.2 | 1.90 | 3.94 | 1.35 | 9.45 | - | 0.0 | PASS | - | 1.000 |
| oracle | authority_wiring | police_stop | 1 | 25.05 timeout | 14.7 | 1.70 | 3.64 | 3.64 | 11.30 | - | 0.0 | PASS | - | 1.000 |
| oracle | authority_wiring | police_stop | 2 | 25.05 timeout | 12.7 | 1.60 | 4.66 | 4.65 | 11.40 | - | 0.0 | PASS | - | 1.000 |
| oracle | authority_wiring | police_stop | 3 | 25.05 timeout | 14.6 | 1.70 | 3.69 | 3.69 | 11.30 | - | 0.0 | PASS | - | 1.000 |
| qwen25vl | authority_wiring | police_stop | 1 | 25.05 timeout | 11.1 | - | - | -73.59 | 0.00 | 2.30 | 25.5 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen25vl | authority_wiring | police_stop | 2 | 25.05 timeout | 0.0 | 3.55 | 2.47 | 2.47 | 9.45 | - | 0.0 | PASS | - | 1.000 |
| qwen25vl | authority_wiring | police_stop | 3 | 25.05 timeout | 11.1 | - | - | -73.60 | 0.00 | 2.30 | 25.5 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen3vl | authority_wiring | police_stop_near | 1 | 25.05 timeout | 0.0 | 9.60 | 3.25 | -4.92 | 7.35 | 12.20 | 25.4 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen3vl | authority_wiring | police_stop_near | 2 | 25.05 timeout | 0.0 | 3.55 | 2.45 | -47.61 | 1.30 | 5.95 | 25.7 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen3vl | authority_wiring | police_stop_near | 3 | 25.05 timeout | 0.0 | 18.60 | 3.26 | 7.44 | 12.00 | 21.20 | 0.0 | PASS | - | 1.000 |
| qwen3vl | authority_wiring | police_stop | 1 | 25.05 timeout | 0.0 | - | - | -63.32 | 1.15 | 3.80 | 25.5 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen3vl | authority_wiring | police_stop | 2 | 25.05 timeout | 11.0 | - | - | -73.53 | 0.00 | 2.35 | 25.5 | FAIL | no_conflict_zone_entry | 0.000 |
| qwen3vl | authority_wiring | police_stop | 3 | 25.05 timeout | 12.0 | - | - | -74.14 | 0.00 | 2.25 | 25.2 | FAIL | no_conflict_zone_entry | 0.000 |
| tcp | authority_wiring | police_stop_near | 1 | 25.05 timeout | 0.0 | 2.30 | 5.14 | -8.65 | 0.15 | 7.15 | 6.8 | FAIL | no_conflict_zone_entry | 0.025 |
| tcp | authority_wiring | police_stop_near | 2 | 25.05 timeout | 5.0 | 2.65 | 4.55 | -8.37 | 0.10 | 7.10 | 6.8 | FAIL | no_conflict_zone_entry | 0.096 |
| tcp | authority_wiring | police_stop_near | 3 | 25.05 timeout | 6.5 | 2.25 | 4.85 | -9.10 | 0.10 | 6.75 | 6.7 | FAIL | no_conflict_zone_entry | 0.097 |
| tcp | authority_wiring | police_stop | 1 | 25.05 timeout | 6.5 | 2.15 | 4.97 | -7.89 | 0.20 | 6.35 | 8.7 | FAIL | no_conflict_zone_entry | 0.059 |
| tcp | authority_wiring | police_stop | 2 | 25.05 timeout | 0.0 | 2.10 | 5.38 | -3.67 | 1.00 | 6.85 | 6.7 | FAIL | no_conflict_zone_entry | 0.034 |
| tcp | authority_wiring | police_stop | 3 | 25.05 timeout | 0.0 | 2.30 | 5.44 | -5.02 | 0.15 | 6.90 | 6.8 | FAIL | no_conflict_zone_entry | 0.025 |
| interfuser | new_leaderboard_wiring | police_stop | default | 14.05 timeout | 0.0 | - | - | 7.44 | 12.00 | - | 0.0 | FAIL | engaged | 0.000 |
| oracle | new_leaderboard_wiring | police_stop | default | 5.05 early-term | 17.8 | 1.30 | 3.76 | 3.76 | 3.75 | - | 0.0 | PASS | - | 1.000 |
| qwen3vl | new_leaderboard_wiring | police_stop | default | 14.05 timeout | 11.7 | - | - | -74.38 | 0.00 | 2.25 | 25.6 | FAIL | no_conflict_zone_entry | 0.000 |
| tcp | new_leaderboard_wiring | police_stop | default | 6.10 early-term | 0.0 | 1.00 | 7.44 | 0.88 | 0.15 | - | 6.7 | FAIL | stopped_through_enforcement | 0.172 |
| tcp | new_authority_wiring | police_stop | 1 | 25.05 timeout | 0.0 | 2.50 | 5.23 | -4.89 | 0.70 | 6.95 | 6.8 | FAIL | no_conflict_zone_entry | 0.025 |

## T2. green_stop graded decomposition (credit = action x latency factor x safety; action = base x conflict x evidence x engagement)

| Model | Run set | Condition | Seed | Scored speed (km/h) | Scored centre clearance (m) | Speed credit | Clearance credit | Base | Conflict | Evidence | Engagement | Action | Latency (s) | Latency factor | Safety | Credit |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| oracle | leaderboard | police_stop | - | 0.0 | 4.21 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.15 | 1.00 | 1.00 | 1.000 |
| baseline | leaderboard | police_stop | - | 23.8 | -66.57 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.886 | 0.000 | - | 0.00 | 1.00 | 0.000 |
| transfuser | leaderboard | police_stop | - | 0.0 | 9.82 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 0.000 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| tcp | leaderboard | police_stop | - | 6.8 | 1.79 | 0.473 | 1.000 | 0.684 | 1.00 | 1.00 | 0.250 | 0.171 | 0.00 | 1.00 | 1.00 | 0.171 |
| interfuser | leaderboard | police_stop | - | 0.0 | 9.84 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 0.000 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| cilrs | leaderboard | police_stop | - | 26.6 | -71.84 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.921 | 0.000 | 1.25 | 1.00 | 1.00 | 0.000 |
| aim | leaderboard | police_stop | - | 3.6 | 8.50 | 0.767 | 1.000 | 0.860 | 0.35 | 1.00 | 0.000 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| neat | leaderboard | police_stop | - | 0.0 | 3.69 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.75 | 1.00 | 1.00 | 1.000 |
| pid | leaderboard | police_stop | - | 23.3 | -66.76 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.887 | 0.000 | - | 0.00 | 1.00 | 0.000 |
| mpc | leaderboard | police_stop | - | 21.4 | -59.42 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.883 | 0.000 | 1.40 | 1.00 | 1.00 | 0.000 |
| openemma | leaderboard | police_stop | - | 21.4 | -60.14 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.906 | 0.000 | 11.30 | 0.00 | 1.00 | 0.000 |
| glm-4.5v | leaderboard | police_stop | - | 25.2 | -71.59 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.888 | 0.000 | 1.45 | 1.00 | 1.00 | 0.000 |
| qwen2.5-vl | leaderboard | police_stop | - | 26.0 | -60.93 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.250 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| qwen3-vl | leaderboard | police_stop | - | 0.0 | 5.40 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.65 | 1.00 | 1.00 | 1.000 |
| glm45v | authority_wiring | police_stop | 1 | 25.7 | -49.98 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.897 | 0.000 | 0.65 | 1.00 | 1.00 | 0.000 |
| glm45v | authority_wiring | police_stop | 2 | 25.5 | -60.91 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.250 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| glm45v | authority_wiring | police_stop | 3 | 25.5 | -71.20 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.890 | 0.000 | 1.45 | 1.00 | 1.00 | 0.000 |
| interfuser | authority_wiring | police_stop | 1 | 0.0 | 9.83 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 0.000 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| neat | authority_wiring | police_stop | 1 | 9.1 | 3.06 | 0.266 | 1.000 | 0.559 | 1.00 | 1.00 | 0.601 | 0.336 | 0.30 | 1.00 | 1.00 | 0.336 |
| neat | authority_wiring | police_stop | 2 | 0.0 | 3.68 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.30 | 1.00 | 1.00 | 1.000 |
| neat | authority_wiring | police_stop | 3 | 0.0 | 3.75 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.65 | 1.00 | 1.00 | 1.000 |
| oracle | authority_wiring | police_stop | 1 | 0.0 | 6.03 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.15 | 1.00 | 1.00 | 1.000 |
| oracle | authority_wiring | police_stop | 2 | 0.0 | 7.05 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.15 | 1.00 | 1.00 | 1.000 |
| oracle | authority_wiring | police_stop | 3 | 0.0 | 6.08 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.15 | 1.00 | 1.00 | 1.000 |
| qwen25vl | authority_wiring | police_stop | 1 | 25.5 | -71.19 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.892 | 0.000 | 1.45 | 1.00 | 1.00 | 0.000 |
| qwen25vl | authority_wiring | police_stop | 2 | 0.0 | 4.87 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.00 | 1.00 | 1.00 | 1.000 |
| qwen25vl | authority_wiring | police_stop | 3 | 25.5 | -71.20 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.890 | 0.000 | 1.45 | 1.00 | 1.00 | 0.000 |
| qwen3vl | authority_wiring | police_stop_near | 1 | 25.4 | -2.53 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.250 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| qwen3vl | authority_wiring | police_stop_near | 2 | 25.7 | -45.21 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.250 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| qwen3vl | authority_wiring | police_stop_near | 3 | 0.0 | 9.84 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.00 | 1.00 | 1.00 | 1.000 |
| qwen3vl | authority_wiring | police_stop | 1 | 25.5 | -60.92 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.250 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| qwen3vl | authority_wiring | police_stop | 2 | 25.5 | -71.13 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.898 | 0.000 | 1.50 | 1.00 | 1.00 | 0.000 |
| qwen3vl | authority_wiring | police_stop | 3 | 25.2 | -71.74 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.867 | 0.000 | 0.55 | 1.00 | 1.00 | 0.000 |
| tcp | authority_wiring | police_stop_near | 1 | 6.8 | -6.26 | 0.471 | 0.000 | 0.282 | 0.35 | 1.00 | 0.250 | 0.025 | 0.00 | 1.00 | 1.00 | 0.025 |
| tcp | authority_wiring | police_stop_near | 2 | 6.8 | -5.97 | 0.470 | 0.000 | 0.282 | 0.35 | 1.00 | 0.968 | 0.096 | 0.40 | 1.00 | 1.00 | 0.096 |
| tcp | authority_wiring | police_stop_near | 3 | 6.7 | -6.71 | 0.480 | 0.000 | 0.288 | 0.35 | 1.00 | 0.958 | 0.097 | 0.40 | 1.00 | 1.00 | 0.097 |
| tcp | authority_wiring | police_stop | 1 | 8.7 | -5.50 | 0.296 | 0.000 | 0.177 | 0.35 | 1.00 | 0.958 | 0.059 | 0.40 | 1.00 | 1.00 | 0.059 |
| tcp | authority_wiring | police_stop | 2 | 6.7 | -1.27 | 0.480 | 0.242 | 0.385 | 0.35 | 1.00 | 0.250 | 0.034 | 0.00 | 1.00 | 1.00 | 0.034 |
| tcp | authority_wiring | police_stop | 3 | 6.8 | -2.63 | 0.475 | 0.000 | 0.285 | 0.35 | 1.00 | 0.250 | 0.025 | 0.00 | 1.00 | 1.00 | 0.025 |
| interfuser | new_leaderboard_wiring | police_stop | default | 0.0 | 9.84 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 0.000 | 0.000 | 0.00 | 1.00 | 1.00 | 0.000 |
| oracle | new_leaderboard_wiring | police_stop | default | 0.0 | 6.16 | 1.000 | 1.000 | 1.000 | 1.00 | 1.00 | 1.000 | 1.000 | 0.10 | 1.00 | 1.00 | 1.000 |
| qwen3vl | new_leaderboard_wiring | police_stop | default | 25.6 | -71.98 | 0.000 | 0.000 | 0.000 | 0.35 | 1.00 | 0.883 | 0.000 | 1.35 | 1.00 | 1.00 | 0.000 |
| tcp | new_leaderboard_wiring | police_stop | default | 6.7 | 3.28 | 0.483 | 1.000 | 0.690 | 1.00 | 1.00 | 0.250 | 0.172 | 0.00 | 1.00 | 1.00 | 0.172 |
| tcp | new_authority_wiring | police_stop | 1 | 6.8 | -2.49 | 0.471 | 0.000 | 0.283 | 0.35 | 1.00 | 0.250 | 0.025 | 0.00 | 1.00 | 1.00 | 0.025 |

## T3. Suite aggregation, re-scored from telemetry with the current scorers

| Model | Strict PASS / 25 | Sum w x credit | Sum w | MARSHAL-Graded | Published graded | MARSHAL Score (partial, published) | Reproduced |
|---|---|---|---|---|---|---|---|
| oracle | 25 | 42.500 | 42.50 | 100.0 | 100.0 | 98.27 | True |
| baseline | 2 | 5.877 | 42.50 | 13.83 | 13.83 | 24.08 | True |
| transfuser | 2 | 5.861 | 42.50 | 13.79 | 13.79 | 29.16 | True |
| tcp | 2 | 11.934 | 42.50 | 28.08 | 28.08 | 28.85 | True |
| interfuser | 6 | 17.279 | 42.50 | 40.66 | 40.66 | 41.29 | True |
| cilrs | 5 | 11.210 | 42.50 | 26.38 | 26.38 | 36.4 | True |
| aim | 1 | 6.904 | 42.50 | 16.25 | 16.25 | 27.75 | True |
| neat | 5 | 16.696 | 42.50 | 39.28 | 39.28 | 35.87 | True |
| pid | 3 | 5.037 | 42.50 | 11.85 | 11.85 | 23.92 | True |
| mpc | 3 | 5.418 | 42.50 | 12.75 | 12.75 | 23.92 | True |
| openemma | 3 | 7.047 | 42.50 | 16.58 | 16.58 | 32.97 | True |
| glm-4.5v | 6 | 12.494 | 42.50 | 29.4 | 29.4 | 32.11 | True |
| qwen2.5-vl | 5 | 9.716 | 42.50 | 22.86 | 22.86 | 40.61 | True |
| qwen3-vl | 4 | 7.971 | 42.50 | 18.75 | 18.75 | 43.57 | True |
