# Oracle-assist ablation: where does authority-aware driving actually fail?

The headline gap (privileged oracle 100.0 vs best non-privileged graded ~64)
says models fail at authority-aware driving; it does not say **where** in the
stack — perception, authority verification, directive semantics, temporal
reasoning, or action execution. This experiment attributes the failure by
injecting ground truth into a Track-C VLM controller one link at a time and
measuring where the score moves.

## Design

Six rungs, cumulative, on the per-tick VLM controller (front camera → one of
STOP/GO/SLOW/HOLD every ~1.5 s; lane-keeping delegated to BasicAgent, exactly
the shipped Track-C configuration). Each rung adds one block of ground truth
to the prompt (`vlm.ablation` config / `--ablation` on
`scripts/_run_vlm_test.py`):

| rung | injected ground truth | link isolated by the delta to the previous rung |
|---|---|---|
| L0 `none` | — (leaderboard configuration) | — |
| L1 `perception` | who is standing where, what they are **visibly doing at this instant**, light state | perception |
| L2 `authority` | whether that person is a legally valid traffic authority | authority verification |
| L3 `semantics` | what the gesture, when given, commands **this** vehicle to do | directive interpretation |
| L4 `temporal` | whether the directive is active / not yet started / expired right now | temporal reasoning |
| L5 `action` | the ground-truth expected action itself | decision → the residual is action execution |

Two truthfulness rules keep the ladder interpretable:

1. **Perception reports the instant, not the episode.** Before gesture onset
   the assist says the person is *standing idle*; whether a past directive
   still binds is the L4 link. (The first draft leaked the scripted gesture
   before onset — that is false information, not less information, and it
   broke rung monotonicity by design error rather than by model behaviour.)
2. Ablation runs read the privileged E-tuple by explicit opt-in and live in
   separate episode ids (`vlm_ablate-<level>_...`); they are diagnostics and
   are never collected into Track-B/C leaderboard rows.

Backbone: GLM-4.5V (the VLM with the most strict passes on the leaderboard),
25 scenarios per rung, one pass per cell, scored by the shipped strict scorer.

## Result — the ladder does not climb

| rung | strict | dominant failure reason |
|---|---:|---|
| L0 `none` | 7 / 25 | **entered the conflict zone under a STOP directive (×7)** — the go-prior |
| L1 `perception` | 4 / 25 | **stationary, never engaged (×12)** |
| L2 `authority` | 2 / 25 | stationary, never engaged |
| L3 `semantics` | 3 / 25 | stationary, never engaged |
| L4 `temporal` | 3 / 25 | stationary, never engaged |
| L5 `action` | 4 / 25 | **stationary, never engaged (×15)** |

The assists *do* fix what they claim to fix: the seven drive-through-the-
officer failures at L0 **disappear entirely from L1 onward** — with scene
ground truth in the prompt, GLM-4.5V never again blows through an authority
STOP. But they are replaced by the opposite failure: primed with any scene
information — up to and including the literal answer key — the model freezes
at spawn and never approaches the scene, so the engagement-gated scorer
(correctly) refuses to credit the stop. Even at L5, "STOP" as a per-tick
token cannot express *"drive forward, then stop at the right place"*: the
model emits STOP from the first query and parks 40 m upstream.

## Attribution

1. **The binding constraint is the action interface, not the knowledge
   chain.** No rung of added knowledge (perception → authority → semantics →
   temporal → the answer itself) recovers the oracle's behaviour, because the
   per-tick action vocabulary has no spatial anchor. This is the controlled
   confirmation of the finding that *how the model is wired to the vehicle*
   decides whether its authority reading is usable.
2. **The knowledge chain is not innocent either — it changes the failure
   direction.** Scene information flips GLM-4.5V from under-compliance
   (driving through STOPs) to total over-compliance (never moving). Both
   poles are failures the suite's STOP/non-STOP balance and engagement gate
   are designed to expose; a benchmark without the engagement gate would have
   scored L5 near the ceiling and reported the problem solved.
3. **Scoring-metric implication.** The ladder is invisible to naive metrics:
   strict-pass counts under-measure L0 (lucky-prior passes) and a
   distance-to-stop metric would over-credit L1–L5 (parked forever counts as
   "stopped"). The engagement-gated design is what makes the interface
   failure measurable.

## Caveats

- Single backbone (GLM-4.5V), single API pass per cell — rung-to-rung deltas
  of ±1 scenario (e.g. the isolated `conflicting_authorities` pass at L1) are
  within single-sample noise and are not interpreted.
- The result attributes failure for **this per-tick QA wiring**; a planner
  that consumes the same assists could fail elsewhere. That comparison
  (assisted OpenEMMA) is the natural follow-up.
- Assist blocks are English prose; prompt-wording sensitivity is
  uncontrolled. The blocks are in
  `marshal_bench/controllers/vlm_model.py::_ablation_assist` and pinned by
  `tests/test_vlm_ablation_assist.py`.

## Reproduce

```bash
# one rung (25 scenarios, staged Town03, CARLA running):
python scripts/_run_vlm_test.py --model zai-org/GLM-4.5V --ablation semantics \
    --results-json tmp/_ablation_semantics.json --report tmp/_ablation_semantics.md
```
