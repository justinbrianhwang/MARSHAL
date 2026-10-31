| model | query schedule | runs (seeds 0–4) | queries per episode | strict PASS | credit mean | credit SD |
|---|---:|---:|---:|---:|---:|---:|
| GLM-4.5V | every 1.5 s (no cap) | 5 | 17 | 0/5 | 0.000 | 0.000 |
| GLM-4.5V | 3-query cap | 5 | 3 | 0/5 | 0.031 | 0.070 |
| Qwen2.5-VL-72B | every 1.5 s (no cap) | 5 | 17 | 2/5 | 0.400 | 0.548 |
| Qwen2.5-VL-72B | 3-query cap | 5 | 3 | 2/5 | 0.400 | 0.548 |
| Qwen3-VL-235B-A22B | every 1.5 s (no cap) | 5 | 17 | 0/5 | 0.000 | 0.000 |
| Qwen3-VL-235B-A22B | 3-query cap | 5 | 3 | 0/5 | 0.000 | 0.000 |
