"""Protocol-v2 graded credit rules: distinct contacts (F3) and held-stop response latency (F6h)."""
from marshal_bench.criteria import graded_episode_scoring as G


def _rows(speeds, dt=0.05, fwd=None, collisions=None):
    out, x = [], 0.0
    for i, v in enumerate(speeds):
        x += v / 3.6 * dt
        out.append({"sim_time": round(i * dt, 4), "ego_speed_kmh": v,
                    "ego_forward_m": x if fwd is None else fwd[i],
                    "collision_count": 0 if collisions is None else collisions[i]})
    return out


def test_one_contact_reported_as_many_callbacks_counts_once():
    rows = _rows([10.0] * 40, collisions=[min(i, 30) for i in range(40)])
    car = (7, "vehicle.tesla.model3")
    assert G._distinct_contacts(rows, [car] * 30) == 1
    assert G._safety_factor(rows, [car] * 30, distinct=True)[0] == 0.25
    assert G._safety_factor(rows)[0] == 0.0                      # callback count (v1): 30 events
    assert G._distinct_contacts(rows, [car] * 15 + [(9, "walker.pedestrian.0001")] * 15) == 2


def test_contacts_split_by_time_gap_without_identities():
    counts = [0] * 10 + [1] * 30 + [2] * 10                       # second event 1.5 s after the first
    assert G._distinct_contacts(_rows([5.0] * 50, collisions=counts)) == 2


def test_held_stop_latency():
    onset = 1.0
    braking = [20.0] * 20 + [max(0.0, 20.0 - 2.0 * i) for i in range(10)] + [0.0] * 60   # stopped from 1.50 s
    lat, trig = G._held_stop_latency(_rows(braking), onset)
    assert trig == "held_stop" and abs(lat - 0.5) < 1e-6
    creep = [3.0] * 200                                           # slow driving is not a response
    assert G._held_stop_latency(_rows(creep), onset) == (None, "no_held_stop")
    parked = [0.0] * 200
    assert G._held_stop_latency(_rows(parked), onset) == (0.0, "at_rest_never_moved")
