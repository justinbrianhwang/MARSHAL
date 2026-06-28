# Model Selection & Reporting

MARSHAL compares fast-moving systems (VLMs in particular). Results are
**version-sensitive**, so model identity and evaluation conditions must be
reported precisely enough to reproduce and to date.

## Principles

- **Use the strongest publicly accessible models available at the time of
  evaluation.** For Track-C VLMs this means the best generally-available backbones
  reachable through the inference API used, as of the evaluation date.
- **Report exact identity and conditions for every result row:**
  - exact **model name and version** (e.g. `Qwen2.5-VL-72B`, not "Qwen-VL");
  - **checkpoint** (or provider/router endpoint if weights are not loaded locally);
  - **inference API / provider** (e.g. Hugging Face router, local weights);
  - **evaluation date**;
  - **prompt template** (id or text — see
    [track_c_visual_decision_qa.md](track_c_visual_decision_qa.md));
  - **input protocol** (frame count, sampling interval, camera, resolution,
    frame timing vs gesture onset, traffic-light state source).
- **State availability limits explicitly.** If a newer or stronger model could
  not be evaluated due to API access, cost, or compute limits, say so — do not
  silently omit it or imply the tested set was exhaustive.

## Why this matters for MARSHAL

A Track-C number is a measurement of *a specific model, on a specific date,
under a specific input protocol*. A newer VLM, more frames, or a richer prompt
can move the number materially. Reporting the exact configuration is what lets a
later reader tell a genuine capability change from a protocol change.

## Current evaluation snapshot

- **Track-C VLMs:** Qwen2.5-VL-72B, Qwen3-VL-235B-A22B, GLM-4.5V — queried via the
  Hugging Face router (weights not loaded locally by the MARSHAL runner;
  provider/default precision), single forward ego camera, per-tick query
  (~1.5 s period), 1280×720.
- **Track-B learned controllers:** original public checkpoints loaded unchanged
  (no quantization / fp16 / layer removal); fp32 unless noted.
- Results are **single-seed (n = 1)**; multi-seed runs are future work.

> See `README.md` Results for the exact per-model integrity lines (checkpoint key
> counts, precision, query period, distinct-output counts) recorded for each run.
