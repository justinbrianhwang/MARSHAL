from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from marshal_bench.scenarios._common import facing_ego_deg, yaw_toward_location


class Transform:
    def __init__(self, x: float, y: float, yaw: float):
        self.location = SimpleNamespace(x=x, y=y, z=0.0)
        self.rotation = SimpleNamespace(yaw=yaw)

    def get_forward_vector(self):
        yaw = math.radians(self.rotation.yaw)
        return SimpleNamespace(x=math.cos(yaw), y=math.sin(yaw), z=0.0)


@pytest.mark.parametrize(
    ("director", "ego"),
    [
        (Transform(10.0, 0.0, 180.0), Transform(0.0, 0.0, 0.0)),
        (Transform(8.0, 6.0, -143.130102), Transform(0.0, 0.0, 35.0)),
        (Transform(12.0, -4.0, 161.565051), Transform(0.0, 0.0, 0.0)),
    ],
)
def test_facing_computation_handles_straight_curved_and_opposite_offset(director, ego):
    assert facing_ego_deg(director, ego) == pytest.approx(0.0, abs=1e-5)
    assert yaw_toward_location(director.location, ego.location) == pytest.approx(
        director.rotation.yaw, abs=1e-5
    )


def test_facing_computation_flags_wrong_way():
    assert facing_ego_deg(Transform(10.0, 0.0, 0.0), Transform(0.0, 0.0, 0.0)) == 180.0
