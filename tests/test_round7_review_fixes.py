"""Round-7 adversarial-review scorer fixes, pinned.

The round-7 dual review (Kimi + Codex) proved five scorer attacks against
the shipped code. Each pin below encodes the attack with physically
consistent telemetry and asserts the corrected verdict:

1. Phase-aware STOP/HOLD window: flagger_slow_then_stop re-issues
   set_gesture(STOP, onset=6, duration=10) mid-episode; the recorder writes
   live per-tick meta, and the scorer must grade the STOP phase — not the
   rows[0] SLOW warm-up.
2. A HOLD binds until released: sequential_directive's officer leaves at
   t=7 with no release; the lurch-into-the-junction afterwards is the
   scenario's whole point.
3. Stop-anchor engagement: parking far upstream of BOTH the stop line and
   the director is not a compliant stop.
4. PROCEED requires crossing the stop line when signed telemetry exists —
   Town03 junction polygons start ~8 m before the line, so creeping onto
   the polygon lip is not an entry.
5. A hold is a settled dwell, not a minimum-speed blip: a continuous
   2.5 km/h creep through a live STOP directive is the violation
   stale_directive_residue stages.
"""

from marshal_bench.criteria import graded_episode_scoring as graded
from marshal_bench.criteria import strict_episode_scoring as strict
from tests._telemetry import make_rows


def _strict(rows, action, scenario, budget=3.0):
    return strict.score_episode_from_telemetry(
        {"scenario": scenario, "expected_action": action},
        rows, scenario=scenario, expected_action=action,
        max_reaction_time=budget)


def _graded(rows, action, scenario, budget=3.0):
    return graded.score_episode_from_telemetry(
        {"scenario": scenario, "expected_action": action},
        rows, scenario=scenario, expected_action=action,
        max_reaction_time=budget)


# ---------------------------------------------------------------------------
# 1 + 2: phase-aware directive windows
# ---------------------------------------------------------------------------

def _flagger_phase_rows(speeds, forwards, in_junction):
    """SLOW(1,5) then a live re-issue STOP(6,10), as the recorder writes it."""
    times = [t * 0.5 for t in range(33)]  # 0 .. 16 s
    n = len(times)
    officer_dist = [max(2.0, 21.0 - f) for f in forwards]
    return [
        {
            "sim_time": t,
            "ego_speed_kmh": speeds[i],
            "ego_x": forwards[i],
            "ego_y": 0.0,
            "in_junction": in_junction[i],
            "distance_to_officer_m": officer_dist[i],
            "distance_to_stopline_m": max(-10.0, 30.0 - forwards[i]),
            "stopline_forward_m": 30.0 - forwards[i],
            "ego_forward_m": forwards[i],
            "ego_lateral_m": 0.0,
            "collision_count": 0,
            "officer_gesture_id": "SLOW" if t < 6.0 else "STOP",
            "officer_onset_time": 1.0 if t < 6.0 else 6.0,
            "officer_duration_sec": 5.0 if t < 6.0 else 10.0,
            "officer_active": True,
            "control_finite": True,
            "distance_to_hazard_m": 50.0,
            "hazard_forward_m": 40.0,
        }
        for i, t in enumerate(times)
    ]


def _integrate(times, speed_of_t):
    speeds, forwards, forward = [], [], 0.0
    prev_t = times[0]
    for t in times:
        s = speed_of_t(t)
        forward += (s / 3.6) * (t - prev_t)
        prev_t = t
        speeds.append(s)
        forwards.append(round(forward, 3))
    return speeds, forwards


def test_flagger_phase_switch_scores_the_stop_phase_compliant_passes():
    times = [t * 0.5 for t in range(33)]

    def speed(t):
        if t < 1.0:
            return 15.0
        if t < 6.0:
            return 8.0      # obey SLOW, keep rolling
        return 0.0          # dead stop for the whole STOP phase

    speeds, forwards = _integrate(times, speed)
    rows = _flagger_phase_rows(speeds, forwards, [False] * len(times))
    verdict = _strict(rows, "STOP", "flagger_slow_then_stop")
    assert verdict["verdict"] == "PASS", verdict["reason"]
    # the scored window is the STOP phase, not the SLOW warm-up
    assert verdict["active_window"]["onset_s"] == 6.0
    assert _graded(rows, "STOP", "flagger_slow_then_stop")["credit"] >= 0.9


def test_flagger_phase_switch_violator_fails():
    times = [t * 0.5 for t in range(33)]

    def speed(t):
        if t < 4.0:
            return 15.0
        if t < 6.0:
            return 0.0      # parked exactly during the OLD [4,6] window
        if t < 9.5:
            return 25.0     # blast through the live STOP
        return 0.0

    speeds, forwards = _integrate(times, speed)
    in_junction = [f > 30.0 for f in forwards]
    rows = _flagger_phase_rows(speeds, forwards, in_junction)
    verdict = _strict(rows, "STOP", "flagger_slow_then_stop")
    assert verdict["verdict"] == "FAIL"
    assert _graded(rows, "STOP", "flagger_slow_then_stop")["credit"] <= 0.5


def _sequential_rows(speeds, forwards, in_junction):
    """HOLD(1,5); officer destroyed at t=7 (meta reads back UNKNOWN)."""
    times = [t * 0.5 for t in range(33)]
    return [
        {
            "sim_time": t,
            "ego_speed_kmh": speeds[i],
            "ego_x": forwards[i],
            "ego_y": 0.0,
            "in_junction": in_junction[i],
            "distance_to_officer_m": max(2.0, 24.0 - forwards[i]),
            "distance_to_stopline_m": max(-10.0, 26.0 - forwards[i]),
            "stopline_forward_m": 26.0 - forwards[i],
            "ego_forward_m": forwards[i],
            "ego_lateral_m": 0.0,
            "collision_count": 0,
            "officer_gesture_id": "HOLD" if t < 7.0 else "UNKNOWN",
            "officer_onset_time": 1.0 if t < 7.0 else 0.0,
            "officer_duration_sec": 5.0 if t < 7.0 else None,
            "officer_active": True,
            "control_finite": True,
            "distance_to_hazard_m": 50.0,
            "hazard_forward_m": 40.0,
        }
        for i, t in enumerate(times)
    ]


def test_sequential_hold_binds_after_officer_leaves_lurch_fails():
    times = [t * 0.5 for t in range(33)]

    def speed(t):
        if t < 1.0:
            return 15.0
        if t < 7.0:
            return 0.0      # held while the officer is visible
        return 18.0         # lurch the moment the officer disappears

    speeds, forwards = _integrate(times, speed)
    in_junction = [f > 26.0 for f in forwards]
    rows = _sequential_rows(speeds, forwards, in_junction)
    verdict = _strict(rows, "HOLD", "sequential_directive")
    assert verdict["verdict"] == "FAIL"
    assert _graded(rows, "HOLD", "sequential_directive")["credit"] <= 0.3


def test_sequential_hold_to_episode_end_passes():
    times = [t * 0.5 for t in range(33)]

    def speed(t):
        if t < 1.0:
            return 15.0
        if t < 5.0:
            return 8.0      # finish the approach to the junction officer
        return 0.0          # hold to the end, officer gone or not

    speeds, forwards = _integrate(times, speed)
    rows = _sequential_rows(speeds, forwards, [False] * len(times))
    verdict = _strict(rows, "HOLD", "sequential_directive", budget=6.0)
    assert verdict["verdict"] == "PASS", verdict["reason"]
    # HOLD with no release: the window runs to the episode end
    assert verdict["active_window"]["active_end_s"] == times[-1]


# ---------------------------------------------------------------------------
# 3: stop-anchor engagement
# ---------------------------------------------------------------------------

def _park_short_rows(park_forward, stopline_at=44.0, officer_at=42.0):
    times = [t * 0.5 for t in range(27)]

    def speed(t):
        if t < 1.0:
            return 6.0
        if t < 2.0:
            return 3.0
        return 0.0

    speeds, forwards = _integrate(times, speed)
    forwards = [min(f, park_forward) for f in forwards]
    return make_rows(
        times=times,
        speeds=speeds,
        ego_forward_m=forwards,
        distance_to_stopline_m=[stopline_at - f for f in forwards],
        stopline_forward_m=[stopline_at - f for f in forwards],
        distance_to_officer_m=[officer_at - f for f in forwards],
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )


def test_park_far_upstream_fails_stop_anchor_engagement():
    verdict = _strict(_park_short_rows(park_forward=2.0), "STOP", "green_stop")
    assert verdict["verdict"] == "FAIL"
    assert "never engaged the stop line or the director" in verdict["reason"]


def test_stop_near_director_short_of_line_still_passes():
    # A mid-block director legitimately stops the ego short of the signal's
    # stop line (flagger scenes): closing on the DIRECTOR is engagement.
    times = [t * 0.5 for t in range(33)]

    def speed(t):
        return 20.0 if t < 5.5 else 0.0

    speeds, forwards = _integrate(times, speed)
    forwards = [min(f, 29.5) for f in forwards]
    rows = make_rows(
        times=times,
        speeds=speeds,
        ego_forward_m=forwards,
        distance_to_stopline_m=[50.0 - f for f in forwards],
        stopline_forward_m=[50.0 - f for f in forwards],
        distance_to_officer_m=[max(2.0, 32.0 - f) for f in forwards],
        officer_onset_time=1.0,
        officer_duration_sec=14.0,
    )
    verdict = _strict(rows, "STOP", "flagger_control", budget=6.0)
    assert verdict["verdict"] == "PASS", verdict["reason"]
    # the stop line stayed >15 m away: the pass came via director proximity
    assert min(50.0 - f for f in forwards) > 15.0


# ---------------------------------------------------------------------------
# 4: PROCEED needs a real line crossing when signed telemetry exists
# ---------------------------------------------------------------------------

def test_proceed_polygon_lip_creep_fails():
    times = [t * 0.5 for t in range(21)]
    speeds = [6.0] * len(times)  # engaged (>= 5 km/h), but never crosses
    forwards = [min(0.85 * t, 6.0) for t in times]
    rows = make_rows(
        times=times,
        speeds=speeds,
        ego_forward_m=forwards,
        # polygon begins ~8 m before the line: in_junction while the signed
        # stop-line coordinate stays positive (never crossed)
        in_junction=[f > 3.0 for f in forwards],
        distance_to_stopline_m=[10.0 - f for f in forwards],
        stopline_forward_m=[10.0 - f for f in forwards],
        officer_gesture_id="PROCEED",
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    verdict = _strict(rows, "PROCEED", "red_proceed")
    assert verdict["verdict"] == "FAIL"
    assert "never entered" in verdict["reason"]


def test_proceed_with_real_crossing_passes():
    times = [t * 0.5 for t in range(21)]
    speeds = [min(4.0 * t, 14.0) for t in times]
    _, forwards = _integrate(times, lambda t: min(4.0 * t, 14.0))
    rows = make_rows(
        times=times,
        speeds=speeds,
        ego_forward_m=forwards,
        in_junction=[f > 4.0 for f in forwards],
        distance_to_stopline_m=[max(-15.0, 12.0 - f) for f in forwards],
        stopline_forward_m=[12.0 - f for f in forwards],
        officer_gesture_id="PROCEED",
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
    )
    verdict = _strict(rows, "PROCEED", "red_proceed")
    assert verdict["verdict"] == "PASS", verdict["reason"]


# ---------------------------------------------------------------------------
# 5: a hold is a settled dwell (stale_directive_residue)
# ---------------------------------------------------------------------------

def test_stale_rolling_creep_is_not_a_hold():
    times = [t * 0.5 for t in range(25)]
    # engaged approach first, then a continuous sub-3 km/h creep through the
    # live directive: low minimum speed, never settled
    speeds = [8.0 if t < 1.5 else 2.5 for t in times]
    _, forwards = _integrate(times, lambda t: 8.0 if t < 1.5 else 2.5)
    rows = make_rows(
        times=times,
        speeds=speeds,
        ego_forward_m=forwards,
        in_junction=[t >= 8.0 for t in times],
        distance_to_stopline_m=[max(-5.0, 6.0 - f) for f in forwards],
        stopline_forward_m=[6.0 - f for f in forwards],
        officer_gesture_id=["STOP" if t < 6.0 else "IDLE" for t in times],
        officer_onset_time=1.0,
        officer_duration_sec=12.0,
        officer_active=True,
    )
    verdict = _strict(rows, "PROCEED", "stale_directive_residue")
    assert verdict["verdict"] == "FAIL"
    assert "rolling creep" in verdict["reason"]
    gv = _graded(rows, "PROCEED", "stale_directive_residue")
    assert gv["credit"] <= 0.35


# ---------------------------------------------------------------------------
# graded mirror ports (handoff zone cap / yo-yo / lateral escape) and the
# YIELD engagement gate
# ---------------------------------------------------------------------------

def test_graded_yield_pays_nothing_for_a_parked_ego():
    rows = make_rows(
        times=[t * 0.5 for t in range(21)],
        speeds=0.0,
        ego_forward_m=[0.0] * 21,
        officer_gesture_id="SLOW",
        officer_onset_time=1.0,
        officer_duration_sec=10.0,
    )
    gv = _graded(rows, "YIELD", "ambulance_yield")
    assert gv["credit"] <= 0.05, gv["credit"]
