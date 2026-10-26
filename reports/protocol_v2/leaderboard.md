| model | evaluated as | runs | MARSHAL-Graded (mean ± SD) | strict PASS per run (/25) | strict PASS, all runs | override cells, all runs |
|---|---:|---:|---:|---:|---:|---:|
| **oracle (privileged)** | A — privileged reference | 1 | 96.9 | 24 | 24/25 | 12/12 |
| InterFuser | B — closed-loop driving stack | 1 | 43.5 | 8 | 8/25 | 5/12 |
| NEAT | B — closed-loop driving stack | 1 | 42.9 | 5 | 5/25 | 4/12 |
| TCP | B — closed-loop driving stack | 1 | 30.3 | 3 | 3/25 | 3/12 |
| CILRS | B — closed-loop driving stack | 1 | 24.6 | 5 | 5/25 | 4/12 |
| Qwen2.5-VL-72B | C — VLM-based closed-loop controller | 5 | 20.8 ± 3.4 | 4.6 ± 1.1 | 23/125 | 14/60 |
| TransFuser | B — closed-loop driving stack | 1 | 14.3 | 2 | 2/25 | 2/12 |
| AIM | B — closed-loop driving stack | 1 | 13.2 | 1 | 1/25 | 1/12 |
| MPC (control) | B — closed-loop driving stack | 1 | 12.8 | 3 | 3/25 | 2/12 |
| PID (control) | B — closed-loop driving stack | 1 | 11.8 | 3 | 3/25 | 2/12 |
| Qwen3-VL-235B-A22B | C — VLM-based closed-loop controller | 5 | 11.7 ± 2.5 | 2.6 ± 0.9 | 13/125 | 4/60 |
| OpenEMMA | B — VLM trajectory planner | 1 | 10.6 | 2 | 2/25 | 2/12 |
| GLM-4.5V | C — VLM-based closed-loop controller | 5 | 8.1 ± 0.2 | 2.0 ± 0.0 | 10/125 | 0/60 |
| baseline (light-only, officer-blind) | reference (no authority input) | 1 | 7.7 | 0 | 0/25 | 0/12 |
