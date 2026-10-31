| model | episodes | world frames per tick | sim / wall time (median) | new decisions per sim s (median) | VLM query interval (sim s) | VLM input frame age (ticks: queries) | control readback lag (ticks: episodes) | episodes with sensor fallback after t0 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| oracle | 25 | 1 on every tick | 0.155 | 20.00 | — | — | 1 ×25 | 0 |
| baseline | 25 | 1 on every tick | 0.183 | 20.00 | — | — | 1 ×25 | 0 |
| TransFuser | 25 | 1 on every tick | 0.174 | 9.98 | — | — | 1 ×25 | 0 |
| TCP | 25 | 1 on every tick | 0.181 | 19.96 | — | — | 1 ×25 | 0 |
| InterFuser | 75 | 1 on every tick | 0.159 | 19.96 | — | — | 1 ×75 | 0 |
| CILRS | 25 | 1 on every tick | 0.152 | 19.92 | — | — | 1 ×25 | 0 |
| AIM | 25 | 1 on every tick | 0.151 | 19.92 | — | — | 1 ×25 | 0 |
| NEAT | 25 | 1 on every tick | 0.152 | 19.92 | — | — | 1 ×25 | 0 |
| PID | 25 | 1 on every tick | 0.154 | 20.00 | — | — | 1 ×25 | 0 |
| MPC | 25 | 1 on every tick | 0.152 | 20.00 | — | — | 1 ×25 | 0 |
| OpenEMMA | 25 | 1 on every tick | 0.080 | 0.36 | — | — | 1 ×25 | 0 |
| GLM-4.5V | 125 | 1 on every tick | 0.079 | 0.68 | 1.50, 1.55 | 0 ×1, 1 ×37, 2 ×136, 3 ×2, 4 ×50, 5 ×1440, 6 ×459 | 1 ×125 | 0 |
| Qwen2.5-VL | 125 | 1 on every tick | 0.149 | 0.68 | 1.50, 1.55 | 1 ×209, 2 ×517, 3 ×1, 4 ×41, 5 ×733, 6 ×624 | 1 ×125 | 0 |
| Qwen3-VL | 125 | 1 on every tick | 0.148 | 0.68 | 1.50, 1.55 | 1 ×63, 2 ×174, 3 ×2, 4 ×68, 5 ×1308, 6 ×510 | 1 ×125 | 0 |
