"""Unit and monotonicity tests for MARSHAL Score (candidate) on synthetic inputs (no CARLA, no real logs).
usage: python -I reports/marshal_score/test_marshal_score.py   (prints 'ALL TESTS PASSED')"""
import itertools, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import marshal_score as MS  # noqa: E402

GRID = [0.0, 0.25, 0.5, 0.75, 1.0]


def rows_stop(v_profile, front_profile, dt=0.05, x_profile=None):
    out = []
    for i, (v, f) in enumerate(zip(v_profile, front_profile)):
        x = x_profile[i] if x_profile else (front_profile[0] - f)
        out.append({"sim_time": round((i + 1) * dt, 2), "ego_speed_kmh": v, "stopline_front_m": f, "ego_forward_m": x,
                    "in_junction": f < 0, "ego_lateral_m": 0.0})
    return out


def approach_then_stop(stop_front, t_stop, v=20.0, hold_until=25.0, n=500, f0=15.0):
    """Drive at v from front f0 to stop_front, arriving at t_stop, then hold (or release at hold_until)."""
    vs, fs = [], []
    for i in range(n):
        t = (i + 1) * 0.05
        if t < t_stop:
            vs.append(v); fs.append(f0 - (f0 - stop_front) * t / t_stop)
        elif t < hold_until:
            vs.append(0.0); fs.append(stop_front)
        else:
            vs.append(v); fs.append(stop_front - (t - hold_until) * v / 3.6)
    return rows_stop(vs, fs)


def score_stop(rows, strict="FAIL", contacts=0):
    el, _ = MS.stop_hold(rows, 1.0, 13.0, 3.0)
    return MS.combine(el, contacts, strict)["score"], el


def test_combine_basic():
    assert MS.combine({"R": 1, "B": 1, "T": 1, "P": 1}, 0, "PASS")["score"] == 100.0
    s = MS.combine({"R": 1, "B": 1, "T": 1, "P": 1}, 0, "FAIL")["score"]
    assert s == 99.0, s  # FAIL never reaches or displays 100
    assert MS.combine({"R": 0.9999, "B": 1, "T": 1, "P": 1}, 0, "FAIL")["score"] <= 99.0
    assert MS.combine({"R": 1, "B": 1, "T": 1, "P": 1}, 0, "INVALID")["score"] is None
    assert MS.combine({"R": 1, "B": 1, "T": 1, "P": 1}, 0, "NOT_SCORED")["score"] is None
    assert MS.combine({"R": 0, "B": 0, "T": 0, "P": 0}, 0, "FAIL")["score"] == 0.0
    # not-applicable elements are excluded, never counted as 1
    a = MS.combine({"R": 0.5, "B": None, "T": 0.0, "P": None}, 0, "FAIL")["score"]
    assert abs(a - 100 * (0.4 * 0.5) / 0.6) < 0.11, a


def test_combine_monotone():
    for R, B, T, P in itertools.product(GRID, GRID, GRID, GRID):
        base = MS.combine({"R": R, "B": B, "T": T, "P": P}, 0, "FAIL")["score"]
        for k in "RBTP":
            for lower in GRID:
                el = {"R": R, "B": B, "T": T, "P": P}
                if lower < el[k]:
                    el[k] = lower
                    assert MS.combine(el, 0, "FAIL")["score"] <= base + 1e-9, (k, R, B, T, P)
        for c in (1, 2, 5):  # adding a contact never raises the score
            assert MS.combine({"R": R, "B": B, "T": T, "P": P}, c, "FAIL")["score"] <= base + 1e-9
        assert MS.combine({"R": R, "B": B, "T": T, "P": P}, 2, "FAIL")["score"] <= \
            MS.combine({"R": R, "B": B, "T": T, "P": P}, 1, "FAIL")["score"] + 1e-9


def test_stop_overrun_monotone():
    prev = None
    for stop_front in (3.0, 1.0, 0.5, 0.0, -0.5, -1.0, -2.0, -3.0):
        s, _ = score_stop(approach_then_stop(stop_front, 3.0))
        assert prev is None or s <= prev + 1e-9, (stop_front, s, prev)
        prev = s
    # stopping further upstream than the margin is not penalised (no upper bound in the spec)
    assert score_stop(approach_then_stop(8.0, 2.5))[0] == score_stop(approach_then_stop(2.0, 3.0))[0]


def test_stop_late_reaction_monotone():
    prev = None
    for t_stop in (2.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0):
        s, _ = score_stop(approach_then_stop(2.0, t_stop))
        assert prev is None or s <= prev + 1e-9, (t_stop, s, prev)
        prev = s


def test_stop_shorter_hold_monotone():
    prev = None
    for release in (25.0, 12.0, 10.0, 8.0, 6.0):
        s, _ = score_stop(approach_then_stop(2.0, 3.0, hold_until=release))
        assert prev is None or s <= prev + 1e-9, (release, s, prev)
        prev = s


def test_no_start_and_drive_through():
    still = rows_stop([0.0] * 500, [7.4] * 500, x_profile=[0.0] * 500)
    s_still, el = score_stop(still)
    assert el["R"] == 0 and el["T"] == 0 and el["P"] == 0 and el["B"] == 1
    assert s_still <= 25.0  # required approach not performed: low, though the boundary was kept
    thru = rows_stop([25.0] * 500, [15.0 - 25 / 3.6 * 0.05 * i for i in range(500)])
    s_thru, _ = score_stop(thru)
    assert s_thru == 0.0 and s_still > s_thru


def test_proceed_progress_and_deadline():
    def run(stop_after_m, t_start, v=10.0):
        out, x = [], 0.0
        for i in range(500):
            t = (i + 1) * 0.05
            sp = v if (t >= t_start and x < stop_after_m) else 0.0
            x += sp / 3.6 * 0.05
            out.append({"sim_time": round(t, 2), "ego_speed_kmh": sp, "ego_forward_m": x, "stopline_front_m": 5.0 - x,
                        "in_junction": x > 7.0, "ego_lateral_m": 0.0})
        return out
    prev = None
    for dist in (40.0, 6.9, 6.0, 4.0, 1.0, 0.0):  # less task progress -> not higher
        el, _ = MS.proceed(run(dist, 2.0), 1.0, 3.0, x_entry=7.0)
        s = MS.combine(el, 0, "FAIL")["score"]
        assert prev is None or s <= prev + 1e-9, (dist, s, prev)
        prev = s
    prev = None
    for t0 in (1.5, 4.0, 6.0, 8.0, 10.0):  # later start -> not higher
        el, _ = MS.proceed(run(40.0, t0), 1.0, 3.0, x_entry=7.0)
        s = MS.combine(el, 0, "FAIL")["score"]
        assert prev is None or s <= prev + 1e-9, (t0, s, prev)
        prev = s
    el, _ = MS.proceed(run(0.0, 99.0), 1.0, 3.0, x_entry=7.0)  # required PROCEED never started
    assert MS.combine(el, 0, "FAIL")["score"] == 0.0


def test_detour_forward_only_gets_no_task_credit():
    rows = [{"sim_time": round((i + 1) * 0.05, 2), "ego_speed_kmh": 20.0, "ego_forward_m": i * 0.3, "ego_lateral_m": 0.0,
             "stopline_front_m": None, "in_junction": False} for i in range(500)]
    el, _ = MS.detour(rows, 1.0, 30.0, 3.0)
    assert el["R"] == 0.0 and el["T"] == 0.0
    rows2 = [dict(r, ego_lateral_m=min(2.0, r["sim_time"] * 0.5)) for r in rows]
    el2, _ = MS.detour(rows2, 1.0, 30.0, 3.0)
    assert el2["R"] == 1.0


def test_yield_requires_slowdown_first():
    el, _ = MS.yield_(20.0, 15.0, 2.0, 20.0, None, 1.0, 3.0)  # never slowed: lateral/resume not credited
    assert el["R"] < 0.5
    el2, _ = MS.yield_(20.0, 1.0, 2.0, 20.0, 2.0, 1.0, 3.0)
    assert el2["R"] == 1.0


def test_approach_band_far_park_vs_near_miss():
    def run(park_front):  # approach at 20 km/h, park with the front park_front m before the line, hold
        rows = approach_then_stop(park_front, 3.0, f0=park_front + 15.0)
        for r in rows:
            r["stopline_forward_m"] = r["stopline_front_m"] + 2.0  # vehicle centre ~2 m behind the front
        return score_stop(rows)
    s_in, el_in = run(10.0)       # inside the 16.59 m band
    s_near, el_near = run(15.6)   # centre 17.6 m: 1.0 m beyond the band
    s_far, el_far = run(30.0)     # far-upstream park: core task not done
    assert s_in == 99.0, s_in
    assert 60 < s_near < s_in, s_near            # small threshold miss: small loss
    assert el_far["R"] == 0 and s_far <= 25.0, s_far  # core task not done: low, boundary still credited
    assert s_far < s_near


def test_handoff_uses_common_scales():
    req = MS.CFG["handoff_strict_requirement"]
    rows = []
    for i in range(500):
        t = (i + 1) * 0.05
        x = min(t, 6.0) * 3.0                     # 3 m/s, stops at x = 18 m
        rows.append({"sim_time": round(t, 2), "ego_speed_kmh": 10.8 if t < 6.0 else 0.0, "ego_forward_m": x,
                     "ego_lateral_m": 0.0, "hazard_forward_m": 10.0, "distance_to_hazard_m": abs(10.0 - x) + 3.0,
                     "distance_to_officer_m": abs(19.0 - x) + 3.0})
    el, obs = MS.handoff(rows, 1.0, 13.0, 3.0, req)
    h = obs["handoff_elements"]
    assert h["transit"] == 1.0 and h["officer"] == 1.0 and h["band"] == 1.0  # stop at 18 m, band [11.5, 19]
    rows2 = [dict(r, ego_forward_m=min(r["ego_forward_m"], 9.0)) for r in rows]  # stops 2.5 m short of the band
    h2 = MS.handoff(rows2, 1.0, 13.0, 3.0, req)[1]["handoff_elements"]
    assert abs(h2["band"] - (1 - 2.5 / 3.0)) < 1e-9, h2["band"]  # common 3 m position scale


def test_empty_enforcement_window_is_not_credited():
    rows = approach_then_stop(3.0, 2.0)
    el, obs = MS.stop_hold(rows, 1.0, 3.0, 5.0)  # directive ends before the reaction deadline: no enforced rows
    assert obs["v_enforcement_max_kmh"] is None and el["R"] == 0.0 and el["P"] == 0.0, el
    assert MS.combine(el, 0, "FAIL")["score"] <= 25.0


def test_rocking_beyond_drift_limit_is_not_a_held_stop():
    xs = [0.0] * 10 + [0.25] * 10 + [-0.35] * 30  # span 0.60 m > 0.5 m, every tick at rest
    rows = [{"sim_time": round((i + 1) * 0.05, 2), "ego_speed_kmh": 0.0, "ego_forward_m": x} for i, x in enumerate(xs)]
    assert MS.held_stop_start(rows, 0.0, 2.5) is None
    steady = [dict(r, ego_forward_m=0.1) for r in rows]
    assert MS.held_stop_start(steady, 0.0, 2.5) == 0.05


def test_handoff_timing_gated_by_preceding_stages():
    req = MS.CFG["handoff_strict_requirement"]
    rows = []
    for i in range(500):
        t = (i + 1) * 0.05
        x = min(t, 1.5) * 3.0                     # stops at 4.5 m: no transit, no officer approach, short of the band
        rows.append({"sim_time": round(t, 2), "ego_speed_kmh": 10.8 if t < 1.5 else 0.0, "ego_forward_m": x,
                     "ego_lateral_m": 0.0, "hazard_forward_m": 20.0, "distance_to_hazard_m": abs(20.0 - x) + 3.0,
                     "distance_to_officer_m": abs(29.0 - x) + 3.0})
    el, obs = MS.handoff(rows, 1.0, 13.0, 3.0, req)
    h = obs["handoff_elements"]
    assert h["transit"] == 0.0 and el["R"] == 0.0 and el["T"] == 0.0, (h, el)


def test_combine_rejects_invalid_elements():
    for bad in ({"R": float("nan"), "B": 1, "T": 1, "P": 1}, {"R": None, "B": 1, "T": 1, "P": 1},
                {"R": -0.5, "B": 1, "T": 1, "P": 1}, {"R": 1, "B": 1.5, "T": None, "P": None}):
        try:
            MS.combine(bad, 0, "FAIL")
        except ValueError:
            continue
        raise AssertionError(bad)


if __name__ == "__main__":
    n = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); n += 1; print("ok", name)
    print(f"ALL TESTS PASSED ({n})")
