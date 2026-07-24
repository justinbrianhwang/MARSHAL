# Oracle-assist ablation: where does authority-aware driving actually fail?

The headline gap (privileged oracle 100.0 vs best non-privileged graded ~63)
says models fail at authority-aware driving; it does not say **where** in the
stack — perception, authority verification, directive semantics, temporal
reasoning, decision, or execution. This experiment attributes the failure by
injecting ground truth into a Track-C VLM controller one link at a time and
measuring where the score moves.

This document describes the **second, corrected measurement**. The first
ladder (5 rungs, measured under the shipped 3-query wiring) was submitted to
a dual adversarial review, which proved that its headline attribution ("the
per-tick action interface is the binding constraint") was confounded — most
seriously by the leaderboard wiring's query budget (3 queries at t ≈ 0 / 1.5 /
3.0 s, after which the last decision is held: a decision made at spawn was
*locked in*, so "freezes at spawn" was partly an artifact), and by several
assist blocks that were not strictly truthful (out-of-vocabulary answer keys,
setup-time snapshots going stale mid-episode, single-director descriptions of
two-director scenes). Every one of those defects is fixed in the measurement
below; the review artifacts and fixes are in the round-7 commit history.

## Design

Eight rows: the leaderboard row for reference, then a seven-rung cumulative
ladder on the per-tick VLM controller (front camera → one of
STOP/GO/SLOW/HOLD; lane-keeping delegated to BasicAgent). **All ladder rungs
— including the ladder's own L0 baseline — run with an unbounded query
budget** on the same 1.5 s cadence, so no decision is ever locked in by the
query budget; this is deliberately NOT the leaderboard wiring, and the two
L0-type rows are reported separately for exactly that reason
(`vlm.ablation` config / `--ablation` on `scripts/_run_vlm_test.py`):

| rung | injected ground truth | link isolated by the delta to the previous rung |
|---|---|---|
| — leaderboard | none (3-query budget) | reference row, different wiring |
| L0 `none` | none (unbounded queries) | the ladder's true baseline |
| L1 `perception` | who is standing where (appearance, not legal class), what they are **visibly doing at this instant**, light state, every director in the scene | perception |
| L2 `authority` | whether each director is a legally valid traffic authority (bare classification — no "so obey/ignore them") | authority verification |
| L3 `semantics` | what the gesture, when given, commands **this** vehicle to do | directive interpretation |
| L4 `temporal` | whether the directive is active / not started / expired / never released, at this instant | temporal reasoning |
| L5 `action` | the episode-level expected action, phrased in the reply vocabulary | decision (episode granularity) |
| L6 `policy` | the per-tick output of the verified oracle policy, translated into the reply vocabulary | plan → the residual is pure execution |

Truthfulness rules (each pinned by `tests/test_vlm_ablation_assist.py` and
audited per query — the exact injected text is logged with every decision):

1. **Perception reports the instant, not the episode**, tracked LIVE: a
   mid-episode gesture switch (flagger SLOW→STOP) or a departed officer is
   described as it is at the query time. A departed officer whose HOLD was
   never released reads "no longer present … never released", not a false
   "standing there" or "expired".
2. Perception reports **appearance** ("a person in a high-visibility vest"),
   never the legal class — identity-as-authority arrives exactly at L2.
3. The L5 answer key speaks the reply vocabulary ("PROCEED — drive through
   (in your reply vocabulary: GO)"); where no reply token exists (DETOUR,
   YIELD) it says so instead of naming an un-emittable word.
4. Ablation runs read the privileged E-tuple by explicit, durably-recorded
   opt-in and live in separate episode ids and results files; the collector
   refuses privileged non-oracle episodes outright.

Backbone: GLM-4.5V (most strict passes among the VLM rows), 25 scenarios per
rung, one pass per cell, scored by the round-7 hardened strict scorer
(engagement-gated; the same ruleset as the current leaderboard).

## Result — knowledge doesn't climb; an external plan does

| row | strict | dominant failure |
|---|---:|---|
| leaderboard (3 queries) | 6 / 25 | mixed: drive-through on STOP cells, freeze elsewhere |
| L0 `none` (unbounded) | 1 / 25 | stationary, never engaged (×15) |
| L1 `perception` | 5 / 25 | stationary, never engaged (×12) |
| L2 `authority` | 4 / 25 | stationary, never engaged (×14) |
| L3 `semantics` | 7 / 25 | stationary, never engaged (×10) |
| L4 `temporal` | 6 / 25 | stationary, never engaged (×12) |
| L5 `action` | 3 / 25 | **stationary, never engaged (×15)** |
| L6 `policy` | **17 / 25** | **freeze eliminated**; residual = DETOUR collisions (×4) + clearance/zone precision (×4) |

Three structural facts, all visible in the per-scenario transition matrix
(`tmp/_ablation_ladder_summary.json`, shipped under `outputs/`):

1. **The unassisted model is worse than its leaderboard number suggests.**
   With unlimited chances to reconsider (L0 = 1/25 vs 6/25 under the 3-query
   budget), GLM-4.5V talks itself out of nearly everything — including the
   PROCEED cells it "passes" on the leaderboard, where an early GO locked in
   by the budget happens to be right. Part of the leaderboard score is
   query-budget luck, now disclosed in the Track-C table.
2. **The knowledge rungs fix exactly what they name, and nothing else.**
   `occluded_officer` passes the moment perception GT names the hidden
   officer (L1); `conflicting_authorities` passes when semantics resolves the
   two directors (L3); `stale_directive_residue` passes when the directive's
   timing is stated (L4). But every rung's total stays in the 3–7 band
   because the dominant failure is not missing knowledge: primed with ANY
   scene ground truth the model parks at spawn, and the engagement-gated
   scorer (correctly) refuses to credit a stop that never engaged the scene.
   The episode-level answer key (L5) makes it *worst of all* (15 freezes,
   tied with the bare L0): told "the expected outcome is STOP" at t = 0, the
   model stops at t = 0.
3. **Given a per-tick plan, the same model executes it: 17/25.** L6 feeds
   the verified oracle policy's current token each query, and the freeze
   disappears completely — the model approaches, stops at the line, waits
   out red lights it must not run, and proceeds when released. The 8
   residual failures are not freezes: the four contextual-DETOUR scenes
   collide with the blockage (no reply token or lane-keeping mode can
   command a lane change — a hard expressiveness ceiling of this wiring),
   and four precision cases (clearance overshoot, an over-fast SLOW-zone
   transit, one occluded-scene zone entry) show the 1.5 s × 4-token
   quantisation is too coarse for metre-level stop geometry even when every
   token is correct.

## Attribution

1. **The binding constraint is plan synthesis, not the token interface and
   not the knowledge chain.** The v1 measurement blamed the per-tick action
   interface; the L6 rung — added at the reviewers' insistence — refutes
   that: the interface executes an externally supplied time-indexed plan to
   17/25. What no knowledge rung buys (L1–L5, including the literal answer
   key) is the step *between* knowing and acting: compiling scene facts into
   "keep approaching now, brake now, hold now, go now". That compilation is
   exactly what the privileged oracle contributes at L6 — and what the model
   cannot do for itself at L5, where it holds all the same facts.
2. **Scene information flips the failure direction; it does not remove
   failure.** Unassisted (either wiring), the model under-complies
   (drive-throughs on the leaderboard; disengaged drift at L0). With any
   ground truth in the prompt it over-complies (parks at spawn). Both poles
   are failures the suite's STOP/non-STOP balance and engagement gates are
   built to expose.
3. **The residual interface ceiling is real but narrow.** After plan
   synthesis is externalised, what remains is lateral vocabulary (DETOUR is
   inexpressible — the four contextual-DETOUR cells are a structural 0 for
   this wiring) and control granularity (metre-level clearance and zone
   speed caps overrun by quantised tokens). Interface work can recover
   these; no amount of prompt knowledge can.

## Caveats

- Single backbone (GLM-4.5V), single API pass per cell: adjacent-rung deltas
  of ±1–2 scenarios sit within single-sample noise; the load-bearing
  contrasts (the 1–7 band for L0–L5 vs L6 = 17; freeze count 10–15 vs 0)
  are far outside it.
- L1 grants *perfect* perception — including seeing through the occluder in
  `occluded_officer` and disambiguating `ambiguous_gesture` (clarity 0.45 by
  design). That is what "inject perception ground truth" means, but it
  should be read as granting the cells' tested perceptual capability, not
  merely sharpening pixels.
- Assist blocks are English prose appended cumulatively; prompt-length and
  wording effects between adjacent rungs are not controlled (a fixed-length
  factorial design is future work). The L0-vs-L6 contrast does not depend on
  adjacent-rung deltas.
- The ladder attributes failure for **this per-tick QA wiring**; the section
  below runs the same assists on a planner wiring.

## Replication on a planner wiring (assisted OpenEMMA)

The obvious objection to the attribution above is that it might be an
artifact of the QA wiring: a 4-token vocabulary invites freezing, and a
model that *plans* — regresses a trajectory instead of answering STOP/GO —
might consume injected knowledge differently. So the same ladder was run on
the OpenEMMA controller (Qwen2-VL-7B, four-stage prompt pipeline, waypoint
regression at its native 3.0 s planner cadence — the leaderboard wiring
itself, so no query-budget caveat applies and the `none` rung doubles as the
reference row). The assist text is **byte-identical** to the VLM ladder's
(pinned by `tests/test_openemma_ablation_assist.py`), prepended as a
delimited section to all four stage prompts; at the policy rung the shadow
oracle's per-tick token line is injected verbatim, which for a trajectory
regressor is necessarily *advisory* — its outputs are waypoints, not tokens.
Five rungs were measured (the authority and temporal rungs were skipped: on
the VLM ladder their adjacent-rung deltas sat inside single-sample noise,
and the load-bearing contrast is knowledge-vs-policy):

| row | strict | freezes (STOP-family + PROCEED-family) |
|---|---:|---|
| `none` = leaderboard (3.0 s planner cadence) | 3 / 25 | 7 (7+0) |
| `perception` | 0 / 25 | 10 (7+3) |
| `semantics` | 0 / 25 | 16 (12+4) |
| `action` | 3 / 25 | 17 (15+2) |
| `policy` | **13 / 25** | 4 (1+3) |

The structure replicates, and more sharply than in the QA wiring:

1. **Knowledge injection makes the planner no better than no assist — and at
   two of the three knowledge rungs, strictly worse.** Unassisted it fails in
   mixed directions (5 drive-throughs, 7 freezes, collisions). Any scene
   ground truth in the prompt flips it into over-compliance — parking at
   spawn climbs monotonically to 17/25 at the answer-key rung, the perception
   and semantics rungs score **0/25**, and the answer-key rung ties the
   unassisted 3/25 on a different pass set. The direction flip observed in
   the QA wiring is not a token-vocabulary artifact; it is how this
   generation of VLM backbones responds to being told the scene is
   authority-critical.
2. **The externally supplied plan collapses the freeze — but does not
   eliminate it here**: 13/25 at the policy rung, STOP-family freezes down
   to one, while three PROCEED-family cells stay frozen (point 3). Plan
   synthesis, not knowledge, is the binding constraint in both wirings.
3. **Seven of the twelve residual failures separate interface ceiling from
   prior dominance.** The four lane-change DETOUR cells fail here too — but unlike
   the QA wiring, waypoints CAN express a lane change; the regressor simply
   never emits one, converting what was a structural interface ceiling into
   a behavioural prior. And the three PROCEED cells that stay frozen at the
   policy rung (`red_proceed`, `rule_hierarchy`,
   `night_signal_officer_conflict`) are exactly the proceed-against-the-rule
   family: the planner refuses to enter on red even while being told GO each
   tick — its learned traffic prior overrides the plan. The QA wiring, which
   has no such prior over trajectories, executed those cells. Neither
   residual is recoverable by more knowledge; both are properties of the
   acting head. The remaining five residuals are precision/timing failures
   (`ambulance_yield` lateral clearance, `sequential_directive` window
   breach, `stale_directive_residue` never resuming, `dual_authority_handoff`
   parked short) plus the one surviving STOP-family freeze
   (`flagger_slow_then_stop`).

Two fidelity caveats specific to this wiring: the `none` rung *re-measures*
the leaderboard wiring under identical config rather than reusing its
episodes — the equivalence is empirical (the same 3/25 on the same three
cells), not by construction. And the shadow oracle is conditioned on the
ego's actual trajectory (for a frozen ego its token is the oracle's reaction
from standstill, not a replay of the oracle's own episode), while the 3.0 s
planner cadence can sample past short phases — `flagger_slow_then_stop`'s
1–3 s SLOW phase falls between queries. Decoding is greedy and each cell is
a single closed-loop pass, so ±1–2 cell deltas between adjacent rungs sit
within noise; the 13-vs-3 contrast does not.

## Reproduce

```bash
# one rung (25 scenarios, staged Town03, CARLA running). --ablation switches
# to the diagnostic wiring (unbounded queries, separate episode ids/results):
python scripts/_run_vlm_test.py --model zai-org/GLM-4.5V --ablation policy \
    --results-json tmp/_ablation_v2_policy.json --report tmp/_ablation_v2_policy.md

# same rung on the planner wiring (openemma conda env):
python scripts/_run_fullplanner_sweep.py --controller openemma --ablation policy \
    --results-json tmp/_openemma_ablation_policy.json --report tmp/_openemma_ablation_policy.md
```

Per-rung results (full per-decision logs including the injected assist text)
and the ladder summary ship under `outputs/oracle_ablation/`.
