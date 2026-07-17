from __future__ import annotations

from types import SimpleNamespace

from scripts import find_stations


class _Location:
    def __init__(self, x=0.0, y=0.0, z=0.0):
        self.x = x
        self.y = y
        self.z = z


class _Transform:
    def __init__(self, x, y, yaw):
        self.location = _Location(x, y, 0.0)
        self.rotation = SimpleNamespace(yaw=yaw)

    def get_right_vector(self):
        return SimpleNamespace(x=0.0, y=1.0, z=0.0)


class _Waypoint:
    def __init__(self, lane_id, yaw, *, x=0.0, y=0.0):
        self.lane_id = lane_id
        self.road_id = 7
        self.s = 10.0
        self.lane_type = _Carla.LaneType.Driving
        self.lane_width = 3.5
        self.is_junction = False
        self.transform = _Transform(x, y, yaw)
        self.left = None
        self.right = None
        self.next_waypoint = None

    def next(self, _distance):
        return [] if self.next_waypoint is None else [self.next_waypoint]

    def previous(self, _distance):
        return []

    def get_left_lane(self):
        return self.left

    def get_right_lane(self):
        return self.right


class _Rotation:
    def __init__(self, pitch=0.0, yaw=0.0, roll=0.0):
        self.pitch = pitch
        self.yaw = yaw
        self.roll = roll


class _PoseTransform:
    """Mirrors carla.Transform's (Location, Rotation) constructor."""

    def __init__(self, location, rotation):
        self.location = location
        self.rotation = rotation


class _Carla:
    Location = _Location
    Rotation = _Rotation
    Transform = _PoseTransform

    class LaneType:
        Driving = "driving"
        Sidewalk = "sidewalk"
        Shoulder = "shoulder"


class _Actors:
    def __init__(self, actors=None):
        self.actors = list(actors or [])

    def filter(self, _pattern):
        return self.actors


class _Map:
    def __init__(self, projected):
        self.projected = projected

    def get_waypoint(self, _location, project_to_road=True, lane_type=None):
        if project_to_road and lane_type == _Carla.LaneType.Driving:
            return self.projected
        return None


class _World:
    def __init__(self, carla_map, actors=None):
        self.carla_map = carla_map
        self.actors = _Actors(actors)

    def get_map(self):
        return self.carla_map

    def get_actors(self):
        return self.actors


class _TrafficLight:
    def __init__(self, stop_waypoint, *, actor_x):
        self.id = 42
        self.stop_waypoint = stop_waypoint
        self.transform = _Transform(actor_x, 0.0, 0.0)

    def get_stop_waypoints(self):
        return [self.stop_waypoint]

    def get_transform(self):
        return self.transform


def _pose(yaw=0.0):
    return {"x": 0.0, "y": 0.0, "z": 0.5, "yaw": yaw}


def test_at_pose_facts_use_compatible_heading_projection():
    projected = _Waypoint(lane_id=1, yaw=5.0, x=0.5)

    facts, reason = find_stations._topology_facts_at_pose(
        _World(_Map(projected)), _Carla, _pose(yaw=0.0)
    )

    assert reason is None
    assert facts is not None
    assert facts["id"].startswith("road7-lane1-")
    assert facts["spawn"] == _pose(yaw=0.0)
    assert facts["projection_distance_m"] == 0.5


def test_at_pose_facts_fall_back_to_heading_compatible_opposing_lane():
    wrong_way = _Waypoint(lane_id=-1, yaw=180.0, x=0.2)
    intended = _Waypoint(lane_id=1, yaw=0.0, x=0.8)
    wrong_way.left = intended

    facts, reason = find_stations._topology_facts_at_pose(
        _World(_Map(wrong_way)), _Carla, _pose(yaw=0.0)
    )

    assert reason is None
    assert facts is not None
    assert facts["id"].startswith("road7-lane1-")
    assert facts["projection_distance_m"] == 0.8


def test_at_pose_facts_report_precise_offroad_projection_failure():
    facts, reason = find_stations._topology_facts_at_pose(
        _World(_Map(None)), _Carla, _pose()
    )

    assert facts is None
    assert reason == "no driving waypoint within 3 m of witness pose"


def test_at_pose_runup_is_walked_route_distance_to_linked_stopline():
    step = find_stations.TRACE_STEP_M
    step_count = 7
    route = [_Waypoint(lane_id=1, yaw=0.0, x=index * step) for index in range(step_count + 1)]
    for current, following in zip(route, route[1:]):
        current.next_waypoint = following
    light = _TrafficLight(route[-1], actor_x=route[-1].transform.location.x)

    facts, reason = find_stations._topology_facts_at_pose(
        _World(_Map(route[0]), [light]), _Carla, _pose(yaw=0.0)
    )

    assert reason is None
    assert facts is not None
    assert facts["runup_m"] == step_count * step
    assert facts["stopline_distance_m"] == step_count * step
