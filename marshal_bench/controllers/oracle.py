"""Track-A Oracle controller, the expected-behaviour reference driver.

The oracle is privileged: it receives the full episode E-tuple in ``setup``
and drives the expected authority-aware behaviour for the MARSHAL scenarios.
Lane tracking is delegated to CARLA's BasicAgent; this controller then applies
the scenario-level authority rule as a longitudinal/offset override.
"""
from __future__ import annotations

import json
import logging
import math
import os
from typing import Any, Dict, Optional

from marshal_bench.controllers.base import EpisodeController
from marshal_bench.utils.carla_api_compat import ensure_agents_on_path


log = logging.getLogger(__name__)


def _finite_directive_wait_until(
    officer_cfg: Dict[str, Any], onset_time: float
) -> Optional[float]:
    """When to release a PROCEED held by a finite ego-addressed directive.

    stale_directive_residue: a STOP/HOLD gesture aimed at the ego with a
    finite ``duration`` suppresses progress only while it is live; the oracle
    proceeds shortly after it expires. Returns ``None`` when no such
    directive applies (open-ended window, other addressee, or a gesture that
    never suppressed progress).
    """
    gesture = str(officer_cfg.get("gesture") or "").upper()
    duration = officer_cfg.get("duration")
    target = str(officer_cfg.get("target_relation") or "ego").lower()
    if (gesture in {"STOP", "HOLD"} and duration is not None
            and target in {"ego", "self", "vehicle"}):
        return float(onset_time) + float(duration) + 0.5
    return None


class _RouteTarget:
    """Waypoint-compatible target with an overridden transform location.

    CARLA's LocalPlanner only needs waypoint metadata plus ``transform``.  A
    proxy lets the detour follow the mission waypoint sequence while its PID
    targets are displaced laterally, without switching to another lane's
    topology chain.
    """

    def __init__(self, waypoint: Any, transform: Any) -> None:
        self._waypoint = waypoint
        self.transform = transform

    def __getattr__(self, name: str) -> Any:
        return getattr(self._waypoint, name)


class OracleController(EpisodeController):
    name = "oracle"
    track = "A"

    def __init__(self, config: Optional[dict] = None) -> None:
        self.config = config or {}
        self.world = None
        self.ego = None
        self.gt: Dict[str, Any] = {}
        self.carla = None
        self._target_pred: Optional[str] = None
        self._map = None
        self._agent = None
        self._road_option = None
        self._target_speed_kmh = 25.0
        self._action = "STOP"
        self._onset_time = 1.0
        self._route_offset = 0.0
        self._last_steer = 0.0
        self._detour_committed = False
        self._detour_merge_started = False
        self._original_route = []
        self._yield_resume_time = 0.0
        self._proceed_wait_until = 0.0
        self._directive_hold_until = 0.0
        self._pedestrian_yield_done = False
        self._route_origin = None
        self._route_forward = None
        self._route_right = None
        self._lateral_watchdog_engaged = False
        self._lateral_watchdog_stood_down = False
        self._lateral_watchdog_baseline: Optional[float] = None
        self._stop_roll_anchor = None
        self._approach_stop_gap_m: Optional[float] = None
        self._episode_dir: Optional[str] = None
        self._debug_file = None
        self._merge_start_forward_m: Optional[float] = None
        self._merge_blend_distance_m = 12.0
        self._merge_start_offset = 0.0
        self._merge_fallback_active = False
        self._merge_progress_distance_m = 0.0
        self._merge_last_location = None
        self._route_plan_failure_logged = False
        self._route_plan_failure: Optional[str] = None
        self._route_reference_index = 0

    def set_episode_dir(self, episode_dir: str) -> None:
        """Supply the runner-owned artifact directory for optional diagnostics."""
        self._episode_dir = episode_dir

    def setup(self, world: Any, ego: Any, ground_truth: Dict[str, Any],
              carla: Any) -> None:
        self.world = world
        self.ego = ego
        self.gt = ground_truth or {}
        self.carla = carla
        self._target_pred = self.gt.get("T_target_relation", "ego")
        self._map = world.get_map() if world is not None else None
        self._target_speed_kmh = float(self.gt.get("target_speed_kmh") or 25.0)
        self._action = self._resolve_action()
        self._onset_time = self._resolve_onset_time()
        self._route_offset = 0.0
        self._detour_committed = False
        self._detour_merge_started = False
        self._original_route = []
        self._lateral_watchdog_engaged = False
        self._lateral_watchdog_stood_down = False
        self._lateral_watchdog_baseline = None
        self._stop_roll_anchor = None
        self._merge_start_forward_m = None
        self._merge_blend_distance_m = 12.0
        self._merge_start_offset = 0.0
        self._merge_fallback_active = False
        self._merge_progress_distance_m = 0.0
        self._merge_last_location = None
        self._route_plan_failure_logged = False
        self._route_plan_failure = None
        self._route_reference_index = 0
        self._pedestrian_yield_done = False
        try:
            route_tf = ego.get_transform()
            self._route_origin = route_tf.location
            self._route_forward = route_tf.get_forward_vector()
            self._route_right = route_tf.get_right_vector()
        except Exception:
            self._route_origin = None
            self._route_forward = None
            self._route_right = None
        self._configure_debug_output()
        expected = (self.config.get("expected_behavior") or {})
        margin = expected.get("approach_stop_after_second_authority_m")
        if margin is not None:
            # Zone-handoff approach target, anchored to the SECOND authority
            # (config-placed relative to the ego spawn, so town-independent —
            # unlike the officer, whose lateral placement varies per station).
            # Staged sweeps that move the flagger re-scale this automatically.
            sa = (self.config.get("second_authority") or {})
            self._approach_stop_gap_m = (
                float(sa.get("distance", 16.0)) + float(margin))
        else:
            self._approach_stop_gap_m = None
        scene = self.gt.get("S_safety_context") or {}
        if self._action == "YIELD":
            self._yield_resume_time = self._onset_time + float(
                scene.get("yield_stop_sec") or 3.5
            )
        if self._action == "PROCEED" and scene.get("pedestrian_distance") is not None:
            # The pedestrian starts crossing at scenario start; crawl forward
            # slowly enough that strict telemetry records a real safety yield.
            self._proceed_wait_until = self._onset_time + float(
                scene.get("pedestrian_clear_sec") or 5.8
            )
        self._directive_hold_until = 0.0
        if self._action == "PROCEED" and not self._proceed_wait_until:
            # A progress-suppressing directive addressed to the ego with a
            # finite window (stale_directive_residue): hold a full stop until
            # it expires, then proceed on the green. Distinct from
            # _proceed_wait_until — that path first rolls 4.5 m toward the
            # hazard window, which here could cross the stop line while the
            # directive is still live.
            wait = _finite_directive_wait_until(
                self.config.get("officer") or {}, self._onset_time
            )
            if wait is not None:
                self._directive_hold_until = wait

        self._agent = self._make_basic_agent()
        self._set_straight_plan()

        # Give stop/yield scenarios a tiny rolling start before the criteria
        # begin sampling; several scenario onsets are at t=0/1s and the
        # compliance criterion only times a stop after upstream motion exists.
        try:
            if self._action in {"STOP", "HOLD", "YIELD"}:
                # Establish a real rolling approach before the directive. Low
                # throttle alone does not break static resistance on every
                # CARLA map (notably Town10HD), which made compliant STOP/HOLD
                # episodes indistinguishable from a frozen controller.
                self._seed_initial_velocity(1.8)
                ego.apply_control(
                    carla.VehicleControl(throttle=0.70, brake=0.0, steer=0.0)
                )
            else:
                ego.apply_control(
                    carla.VehicleControl(throttle=0.0, brake=0.2, steer=0.0)
                )
        except Exception:
            pass

    def step(self, observation: Dict[str, Any], dt: float) -> Any:
        carla = self.carla
        if carla is None:
            return None

        obs = observation or {}
        sim_time = float(obs.get("sim_time") or 0.0)
        speed = float(obs.get("ego_speed") or 0.0)

        base = self._run_agent_step()
        self._last_steer = float(getattr(base, "steer", self._last_steer) or 0.0)

        if self._action == "PROCEED":
            control = self._proceed_control(base, sim_time, speed)
        elif self._action == "DETOUR":
            control = self._detour_control(base, sim_time, speed, obs)
        elif self._action == "YIELD":
            control = self._yield_control(base, sim_time, speed)
        else:
            control = self._stop_control(base, sim_time, speed)
        return control

    def _step_with_debug(self, observation: Dict[str, Any], dt: float) -> Any:
        control = OracleController.step(self, observation, dt)
        obs = observation or {}
        self._write_debug_record(obs, float(obs.get("sim_time") or 0.0))
        return control

    def report_target(self) -> Optional[str]:
        return self._target_pred

    # ------------------------------------------------------------------
    # Setup helpers
    # ------------------------------------------------------------------
    def _make_basic_agent(self) -> Any:
        if self.ego is None:
            return None
        try:
            ensure_agents_on_path()
            from agents.navigation.basic_agent import BasicAgent

            opt = {
                "dt": 1.0 / 20.0,
                "target_speed": self._target_speed_kmh,
                "ignore_traffic_lights": True,
                "ignore_vehicles": True,
                "base_tlight_threshold": 0.0,
                "base_vehicle_threshold": 0.0,
                "max_throttle": 0.65,
                "max_brake": 0.7,
                "sampling_radius": 2.0,
            }
            agent = BasicAgent(
                self.ego,
                target_speed=self._target_speed_kmh,
                opt_dict=opt,
                map_inst=self._map,
            )
            agent.ignore_traffic_lights(True)
            agent.ignore_vehicles(True)
            try:
                agent.ignore_stop_signs(True)
            except Exception:
                pass
            try:
                from agents.navigation.local_planner import RoadOption

                self._road_option = RoadOption.LANEFOLLOW
            except Exception:
                self._road_option = None
            return agent
        except Exception as exc:
            log.warning(
                "oracle BasicAgent setup failed: %s: %s",
                type(exc).__name__, exc, exc_info=True,
            )
            return None

    def _set_straight_plan(self, horizon_m: float = 300.0, step_m: float = 2.0) -> None:
        if self._map is None or self.ego is None:
            return
        try:
            wp = self._map.get_waypoint(
                self.ego.get_location(), project_to_road=True
            )
        except Exception:
            return
        if wp is None:
            return

        plan = []
        option = self._road_option
        prev_yaw = float(wp.transform.rotation.yaw)
        n_steps = max(8, int(horizon_m / max(0.5, step_m)))
        for _ in range(n_steps):
            plan.append((wp, option))
            try:
                nxt = list(wp.next(step_m))
            except Exception:
                break
            if not nxt:
                break
            wp = min(
                nxt,
                key=lambda cand: abs(
                    self._angle_delta(float(cand.transform.rotation.yaw), prev_yaw)
                ),
            )
            prev_yaw = float(wp.transform.rotation.yaw)
        try:
            if plan:
                self._original_route = list(plan)
            if plan and self._agent is not None:
                self._agent.set_global_plan(
                    plan, stop_waypoint_creation=True, clean_queue=True
                )
        except Exception:
            pass

    def _resolve_action(self) -> str:
        action = str(self.gt.get("Y_expected_action") or "STOP").upper()
        target = str(self.gt.get("T_target_relation") or "ego").lower()
        valid = bool((self.gt.get("A_authority") or {}).get("valid"))
        gesture = str(self.gt.get("G_gesture") or "").upper()
        light = str(self.gt.get("L_light_state") or "").lower()

        if target not in {"ego", "self", "vehicle"}:
            # The directive is addressed to someone else. If the privileged
            # expectation is to keep moving (out_of_jurisdiction_director:
            # own green governs), spatial scoping means the ego proceeds;
            # otherwise hold on its own signal (adjacent_lane keeps its red).
            return action if action == "PROCEED" else "HOLD"
        # False-obedience avoidance: an UNAUTHORIZED actor's go/turn gesture is
        # ignored — but ONLY when the privileged expected action is itself to not
        # proceed (STOP/HOLD), e.g. unauthorized_go / fake_vest_director. When the
        # ground truth is a hazard-driven maneuver (DETOUR/PROCEED/YIELD, e.g.
        # civilian_warning_accident where a crash makes the civilian's warning
        # credible), trust the expected action rather than refusing to move.
        if (not valid and action in {"STOP", "HOLD"}
                and gesture in {"PROCEED", "GO", "RIGHT", "LEFT"}):
            return "STOP" if light.startswith("red") else "HOLD"
        return action

    def _resolve_onset_time(self) -> float:
        # The E-tuple omits onset_time, but all MARSHAL officer configs use 1s.
        # The fallen-person null-officer uses onset_time=0 in the criterion.
        authority = self.gt.get("A_authority") or {}
        if authority.get("type") in (None, "none"):
            return 0.0
        return float((self.config.get("officer") or {}).get("onset_time", 1.0))

    def _is_no_officer_stop(self) -> bool:
        authority = self.gt.get("A_authority") or {}
        return self._action == "STOP" and authority.get("type") in (None, "none")

    def _seed_initial_velocity(self, speed_mps: float) -> None:
        if self.ego is None or self.carla is None:
            return
        try:
            fwd = self.ego.get_transform().get_forward_vector()
            self.ego.set_target_velocity(
                self.carla.Vector3D(
                    x=fwd.x * speed_mps,
                    y=fwd.y * speed_mps,
                    z=0.0,
                )
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Action policies
    # ------------------------------------------------------------------
    def _approach_stop_control(self, base: Any, speed: float) -> Any:
        """Zone-handoff STOP (dual_authority_handoff): transit the near-zone
        flagger at a SLOW-compatible speed, then hold a full stop a few
        metres beyond it (just short of the junction officer) instead of
        parking wherever the STOP is first perceived.

        The roll target is anchored to the ego's own travelled distance, not
        to the officer: the flagger's spawn is config-relative (identical in
        every town), so the transit-then-stop timing is deterministic across
        the whole town matrix.
        """
        if self._stop_roll_anchor is None:
            try:
                self._stop_roll_anchor = self.ego.get_location()
            except Exception:
                self._stop_roll_anchor = None
        advanced = 0.0
        if self._stop_roll_anchor is not None:
            try:
                advanced = float(
                    self.ego.get_location().distance(self._stop_roll_anchor))
            except Exception:
                advanced = 0.0
        if advanced < float(self._approach_stop_gap_m or 0.0):
            ctrl = self._copy_control(base)
            # Creep cap ~14 km/h: well under the strict 18 km/h zone cap and
            # slow enough to read as obeying the flagger's SLOW.
            if speed < 2.5:
                ctrl.throttle = 0.70
            elif speed > 3.9:
                ctrl.throttle = 0.0
            else:
                ctrl.throttle = 0.50
            ctrl.brake = 0.0
            return ctrl
        ctrl = self._copy_control(base)
        ctrl.throttle = 0.0
        ctrl.brake = 1.0 if speed > 0.25 else 0.85
        return ctrl

    def _stop_control(self, base: Any, sim_time: float, speed: float) -> Any:
        if self._approach_stop_gap_m is not None:
            return self._approach_stop_control(base, speed)
        if self._is_no_officer_stop():
            if getattr(self, "_stop_roll_anchor", None) is None:
                try:
                    self._stop_roll_anchor = self.ego.get_location()
                except Exception:
                    self._stop_roll_anchor = None
            advanced = 0.0
            if self._stop_roll_anchor is not None:
                try:
                    advanced = float(self.ego.get_location().distance(self._stop_roll_anchor))
                except Exception:
                    advanced = 0.0
            if advanced < 2.0:
                ctrl = self._copy_control(base)
                ctrl.throttle = 0.70
                ctrl.brake = 0.0
                return ctrl
        if sim_time < self._onset_time:
            ctrl = self._copy_control(base)
            ctrl.throttle = min(max(float(getattr(base, "throttle", 0.0)), 0.65), 0.75)
            ctrl.brake = 0.0
            return ctrl

        ctrl = self._copy_control(base)
        ctrl.throttle = 0.0
        ctrl.brake = 1.0 if speed > 0.25 else 0.85
        return ctrl

    def _proceed_control(self, base: Any, sim_time: float, speed: float) -> Any:
        # Safety yield for a crossing pedestrian (rule_hierarchy): the ego spawns
        # ~16 m back from the walker, which is OUTSIDE the strict yield window
        # (distance<=14 m). A plain time-based stop therefore halts too far back
        # and is already >3 km/h by the time it reaches the window, so the strict
        # scorer never sees a row with distance<=14 m AND speed<=3 km/h.
        #
        # Instead: ROLL forward a fixed displacement (gated on the ego's own
        # forward travel, which is monotonic and deterministic) until the ego is
        # well inside the window, then HOLD a full stop there while the walker
        # clears. A stationary ego at distance~=11 m produces a long, stable
        # band of yield rows that is robust to the walker wandering +/-1 m.
        # After pedestrian_clear_sec, accelerate to reach the junction.
        ctrl = self._copy_control(base)
        if sim_time < self._onset_time:
            ctrl.throttle = 0.0
            ctrl.brake = 0.35
            return ctrl

        if self._directive_hold_until and sim_time < self._directive_hold_until:
            # A live STOP/HOLD directive addressed to the ego: hold a full
            # stop with no approach roll — the stop line may be only metres
            # ahead and must not be crossed while the directive is active.
            # Latching the roll state makes the release a clean launch (the
            # 4.5 m creep exists for the pedestrian window, not for this).
            self._proceed_stop_latched = True
            if getattr(self, "_proceed_anchor", None) is None:
                try:
                    self._proceed_anchor = self.ego.get_location()
                except Exception:
                    self._proceed_anchor = None
            ctrl.throttle = 0.0
            ctrl.brake = 1.0 if speed > 0.25 else 0.85
            return ctrl

        roll_target_m = 4.5
        if getattr(self, "_proceed_anchor", None) is None:
            try:
                self._proceed_anchor = self.ego.get_location()
            except Exception:
                self._proceed_anchor = None
            self._proceed_stop_latched = False
        advanced = 0.0
        if self._proceed_anchor is not None:
            try:
                advanced = float(self.ego.get_location().distance(self._proceed_anchor))
            except Exception:
                advanced = 0.0

        if not getattr(self, "_proceed_stop_latched", False):
            if advanced < roll_target_m:
                # Gentle approach roll, capped low so we settle inside the window.
                if speed < 6.0:
                    ctrl.throttle = 0.30
                    ctrl.brake = 0.0
                else:
                    ctrl.throttle = 0.0
                    ctrl.brake = 0.20
                return ctrl
            self._proceed_stop_latched = True

        if self._proceed_wait_until and sim_time < self._proceed_wait_until:
            ctrl.throttle = 0.0
            ctrl.brake = 1.0 if speed > 0.25 else 0.75
            return ctrl
        if speed < self._target_speed_kmh / 3.6:
            ctrl.throttle = max(float(getattr(base, "throttle", 0.0)), 0.55)
            ctrl.brake = 0.0
        return ctrl

    def _detour_control(
        self, base: Any, sim_time: float, speed: float, obs: Dict[str, Any]
    ) -> Any:
        if sim_time >= self._onset_time and not self._detour_committed:
            self._prepare_detour_plan()
        if self._detour_committed and not self._detour_merge_started:
            if self._hazard_cleared(obs):
                self._start_detour_merge()
        if self._merge_fallback_active:
            self._update_fallback_merge_target()
        ctrl = self._copy_control(base)
        if sim_time >= self._onset_time:
            if speed > 7.0:
                ctrl.throttle = 0.0
                ctrl.brake = 0.10
            else:
                ctrl.throttle = min(
                    max(float(getattr(base, "throttle", 0.0)), 0.35),
                    0.50,
                )
                ctrl.brake = 0.0
        else:
            ctrl.throttle = min(max(float(getattr(base, "throttle", 0.0)), 0.25), 0.38)
            ctrl.brake = 0.0
        return self._ensure_lateral_response(ctrl, sim_time)

    def _yield_control(self, base: Any, sim_time: float, speed: float) -> Any:
        if sim_time >= self._onset_time and self._route_offset < 1.2:
            self._route_offset = 1.6
            self._set_agent_offset(self._route_offset)
        if sim_time < self._onset_time:
            ctrl = self._copy_control(base)
            ctrl.throttle = min(max(float(getattr(base, "throttle", 0.0)), 0.55), 0.70)
            ctrl.brake = 0.0
            return ctrl
        ctrl = self._copy_control(base)
        if sim_time >= self._yield_resume_time:
            if speed < self._target_speed_kmh / 3.6:
                ctrl.throttle = max(float(getattr(base, "throttle", 0.0)), 0.45)
            ctrl.brake = 0.0
            return self._ensure_lateral_response(ctrl, sim_time)
        ctrl.throttle = 0.0
        ctrl.brake = 0.85 if speed > 0.4 else 0.55
        return self._ensure_lateral_response(ctrl, sim_time)

    def _nearest_pedestrian_distance(self) -> Optional[float]:
        if self.world is None or self.ego is None:
            return None
        try:
            ego_loc = self.ego.get_location()
            walkers = self.world.get_actors().filter("walker.pedestrian.*")
        except Exception:
            return None
        best = None
        for walker in walkers:
            try:
                loc = walker.get_location()
                dist = math.hypot(float(loc.x) - float(ego_loc.x),
                                  float(loc.y) - float(ego_loc.y))
            except Exception:
                continue
            if best is None or dist < best:
                best = dist
        return best

    def _pedestrian_crawl_control(self, ctrl: Any, speed: float) -> Any:
        ctrl.throttle = 0.20 if speed < 0.55 else 0.0
        ctrl.brake = 0.0 if speed <= 0.75 else 0.30
        return ctrl

    # ------------------------------------------------------------------
    # Control / geometry utilities
    # ------------------------------------------------------------------
    def _run_agent_step(self) -> Any:
        carla = self.carla
        if self._agent is not None:
            try:
                return self._agent.run_step()
            except Exception:
                pass
        return carla.VehicleControl(throttle=0.0, brake=0.0, steer=self._last_steer)

    def _copy_control(self, control: Any) -> Any:
        carla = self.carla
        out = carla.VehicleControl()
        if control is not None:
            out.throttle = float(getattr(control, "throttle", 0.0) or 0.0)
            out.brake = float(getattr(control, "brake", 0.0) or 0.0)
            out.steer = float(getattr(control, "steer", 0.0) or 0.0)
            out.hand_brake = False
            out.reverse = False
            out.manual_gear_shift = False
            out.gear = 0
        return out

    def _set_agent_offset(self, offset: float) -> None:
        if self._agent is None:
            return
        try:
            self._agent.set_offset(offset)
        except Exception:
            try:
                self._agent.get_local_planner().set_offset(offset)
            except Exception:
                pass

    def _route_displacement(self) -> tuple[float, float]:
        """Return ego forward/lateral displacement in the setup route frame."""
        if (
            self.ego is None
            or self._route_origin is None
            or self._route_forward is None
            or self._route_right is None
        ):
            return 0.0, 0.0
        try:
            loc = self.ego.get_location()
            dx = float(loc.x) - float(self._route_origin.x)
            dy = float(loc.y) - float(self._route_origin.y)
            forward = dx * float(self._route_forward.x) + dy * float(self._route_forward.y)
            lateral = dx * float(self._route_right.x) + dy * float(self._route_right.y)
            return forward, lateral
        except Exception:
            return 0.0, 0.0

    def _route_heading_error(self) -> float:
        reference = self._nearest_original_route_waypoint()
        if self.ego is None or reference is None:
            return 0.0
        try:
            fwd = self.ego.get_transform().get_forward_vector()
            route_tf = reference.transform
            route_forward = route_tf.get_forward_vector()
            route_right = route_tf.get_right_vector()
            return math.atan2(
                float(fwd.x) * float(route_right.x)
                + float(fwd.y) * float(route_right.y),
                float(fwd.x) * float(route_forward.x)
                + float(fwd.y) * float(route_forward.y),
            )
        except Exception:
            return 0.0

    def _nearest_original_route_waypoint(self) -> Any:
        if not self._original_route or self.ego is None:
            return None
        try:
            location = self.ego.get_location()
            start = max(0, self._route_reference_index - 3)
            end = min(len(self._original_route), self._route_reference_index + 11)
            index = min(
                range(start, end),
                key=lambda candidate: (
                    float(self._original_route[candidate][0].transform.location.x)
                    - float(location.x)
                ) ** 2 + (
                    float(self._original_route[candidate][0].transform.location.y)
                    - float(location.y)
                ) ** 2,
            )
            self._route_reference_index = max(self._route_reference_index, index)
            return self._original_route[self._route_reference_index][0]
        except Exception:
            return None

    def _route_lateral_offset(self) -> float:
        reference = self._nearest_original_route_waypoint()
        if reference is None or self.ego is None:
            return self._route_displacement()[1]
        try:
            location = self.ego.get_location()
            route_tf = reference.transform
            right = route_tf.get_right_vector()
            return (
                (float(location.x) - float(route_tf.location.x)) * float(right.x)
                + (float(location.y) - float(route_tf.location.y)) * float(right.y)
            )
        except Exception:
            return self._route_displacement()[1]

    def _ensure_lateral_response(self, ctrl: Any, sim_time: float) -> Any:
        """Dead-man fallback for a lateral plan that produces no response.

        The normal local-planner path stays authoritative. The watchdog engages
        only after 5 m of forward travel with neither lateral motion nor steering,
        then tracks the exact offset selected by that plan in the route frame.
        """
        if (
            sim_time < self._onset_time
            or (abs(self._route_offset) < 1.0 and not self._merge_fallback_active)
            or self._lateral_watchdog_stood_down
        ):
            return ctrl
        forward, _spawn_lateral = self._route_displacement()
        lateral = self._route_lateral_offset()
        base_steer = float(getattr(ctrl, "steer", 0.0) or 0.0)
        if abs(base_steer) >= 0.05:
            # A planner response is conclusive: never let the fallback replace
            # it later in the episode, even if a subsequent segment is straight.
            self._lateral_watchdog_engaged = False
            self._lateral_watchdog_stood_down = True
            return ctrl
        if not self._lateral_watchdog_engaged:
            # "No lateral response" must be measured as drift from the
            # ego's own initial route projection: a spawn can legitimately
            # sit 0.3 m off the route frame's centreline (Town02 cwa), and
            # gating on the absolute value made engagement a coin flip.
            if self._lateral_watchdog_baseline is None:
                self._lateral_watchdog_baseline = lateral
            if forward < 5.0 or abs(lateral - self._lateral_watchdog_baseline) >= 0.25:
                return ctrl
            self._lateral_watchdog_engaged = True

        error = self._route_offset - lateral
        heading_error = self._route_heading_error()
        steer = (0.50 * error) - (1.00 * heading_error)
        ctrl.steer = max(-0.55, min(0.55, steer))
        return ctrl

    def _update_fallback_merge_target(self) -> None:
        """Track the same 12 m route-offset taper when BasicAgent is absent."""
        if self.ego is None:
            return
        try:
            location = self.ego.get_location()
            if self._merge_last_location is not None:
                self._merge_progress_distance_m += float(
                    location.distance(self._merge_last_location)
                )
            self._merge_last_location = location
        except Exception:
            return
        fraction = min(
            1.0,
            self._merge_progress_distance_m / max(0.01, self._merge_blend_distance_m),
        )
        self._route_offset = self._merge_start_offset * (1.0 - fraction)
        # After the taper completes the fallback must keep holding the
        # route centreline: with BasicAgent absent nothing else steers, and
        # releasing lateral control here sends the ego straight off curved
        # roads (post-merge pole/guardrail collisions on Town01/02/03).

    def _prepare_detour_plan(self) -> None:
        self._route_offset = self._detour_route_offset()
        self._set_route_offset_plan(self._route_offset)
        try:
            self._agent.ignore_vehicles(True)
        except Exception:
            pass
        try:
            self._agent.set_target_speed(22.0)
        except Exception:
            pass
        self._detour_committed = True

    def _detour_route_offset(self) -> float:
        """Select the baseline lane-centre-plus-clearance displacement."""
        cur = self._current_route_waypoint()
        if cur is None:
            return self._fallback_detour_offset()
        adjacent = self._select_detour_lane(cur)
        if adjacent is not None:
            _side, lane, lateral_to_lane = adjacent
            return self._offset_into_adjacent_lane(cur, lane, lateral_to_lane)

        # No qualifying neighbour can be inspected. Derive the fallback from
        # road geometry rather than a town-specific/bare 3.6 m literal.
        side = self._detour_side_order()[0]
        sign = -1.0 if side == "left" else 1.0
        try:
            lane_width = float(cur.lane_width)
        except Exception:
            lane_width = 3.5
        margin = float(self.config.get("detour_safety_margin_m", 0.55))
        return sign * (lane_width + max(0.45, min(0.70, margin)))

    def _current_route_waypoint(self) -> Any:
        if self._map is None or self.ego is None:
            return None
        try:
            return self._map.get_waypoint(
                self.ego.get_location(), project_to_road=True
            )
        except Exception:
            return None

    def _remaining_original_route(self) -> list[tuple[Any, Any]]:
        if not self._original_route or self.ego is None:
            return []
        try:
            loc = self.ego.get_location()
            index = min(
                range(len(self._original_route)),
                key=lambda i: (
                    float(self._original_route[i][0].transform.location.x) - float(loc.x)
                ) ** 2
                + (
                    float(self._original_route[i][0].transform.location.y) - float(loc.y)
                ) ** 2,
            )
        except Exception:
            index = 0
        return self._original_route[index:]

    def _shift_route_waypoint(self, waypoint: Any, offset: float) -> Any:
        transform = waypoint.transform
        loc = transform.location
        right = transform.get_right_vector()
        shifted = self.carla.Location(
            x=float(loc.x) + float(right.x) * offset,
            y=float(loc.y) + float(right.y) * offset,
            z=float(getattr(loc, "z", 0.0)),
        )
        shifted_transform = self.carla.Transform(shifted, transform.rotation)
        return _RouteTarget(waypoint, shifted_transform)

    def _set_route_offset_plan(
        self, offset: float, *, blend_distance_m: Optional[float] = None
    ) -> bool:
        route = self._remaining_original_route()
        if self._agent is None:
            self._report_route_plan_failure("BasicAgent is unavailable")
            return False
        if not route:
            self._report_route_plan_failure(
                f"no remaining original route (original waypoints={len(self._original_route)})"
            )
            return False
        plan = []
        travelled = 0.0
        previous = None
        for waypoint, option in route:
            loc = waypoint.transform.location
            if previous is not None:
                try:
                    travelled += float(loc.distance(previous))
                except Exception:
                    travelled += math.hypot(
                        float(loc.x) - float(previous.x),
                        float(loc.y) - float(previous.y),
                    )
            target_offset = offset
            if blend_distance_m is not None:
                fraction = (
                    1.0
                    if blend_distance_m <= 0.0
                    else min(1.0, travelled / blend_distance_m)
                )
                target_offset = offset * (1.0 - fraction)
            plan.append((self._shift_route_waypoint(waypoint, target_offset), option))
            previous = loc
        try:
            self._agent.set_global_plan(
                plan, stop_waypoint_creation=True, clean_queue=True
            )
            return True
        except Exception as exc:
            self._report_route_plan_failure(
                "set_global_plan rejected "
                f"offset={float(offset):.3f}, blend_distance_m={blend_distance_m}, "
                f"waypoints={len(plan)}: {type(exc).__name__}: {exc}",
                exc_info=True,
            )
            return False

    def _report_route_plan_failure(self, message: str, *, exc_info: bool = False) -> None:
        self._route_plan_failure = message
        if not self._route_plan_failure_logged:
            log.warning("oracle route-offset plan failure: %s", message, exc_info=exc_info)
            self._route_plan_failure_logged = True

    def _hazard_cleared(self, obs: Dict[str, Any]) -> bool:
        signal = "blocking_hazard_forward_m"
        if signal not in obs:
            # Compatibility for scenario hooks that predate an explicit
            # blocking set: their physical signal covered all managed actors.
            signal = "hazard_forward_m"
        try:
            relative_forward = float(obs.get(signal))
        except (TypeError, ValueError):
            relative_forward = float("nan")
        if math.isfinite(relative_forward):
            return relative_forward < -5.0

        # Compatibility for callers that do not yet supply physical hazard
        # telemetry. The fallback mirrors the configured actor layouts.
        scene = self.gt.get("S_safety_context") or {}
        forward, _lateral = self._route_displacement()
        if scene.get("crash_distance") is not None:
            count = max(1, min(5, int(scene.get("crash_vehicles", 4))))
            clear_at = float(scene["crash_distance"]) + 6.0 * (count - 1) + 5.0
            return forward > clear_at
        if scene.get("block_distance") is not None:
            return forward > float(scene["block_distance"]) + 12.0
        return False

    def _start_detour_merge(self, blend_distance_m: float = 12.0) -> None:
        if self._detour_merge_started:
            return
        offset = self._route_offset
        installed = self._set_route_offset_plan(
            offset, blend_distance_m=blend_distance_m
        )
        self._detour_merge_started = True
        forward, _lateral = self._route_displacement()
        self._merge_start_forward_m = forward
        self._merge_start_offset = offset
        self._merge_blend_distance_m = float(blend_distance_m)
        self._merge_progress_distance_m = 0.0
        try:
            self._merge_last_location = self.ego.get_location()
        except Exception:
            self._merge_last_location = None
        if installed:
            # The merge plan itself tapers to zero. Disable the constant-offset
            # dead-man target so it cannot fight that planned return.
            self._route_offset = 0.0
            self._lateral_watchdog_engaged = False
            self._lateral_watchdog_stood_down = True
            self._merge_fallback_active = False
        else:
            # BasicAgent is optional in the CARLA wheel. The outbound watchdog
            # already follows the route-frame displacement; continue it with
            # the identical bounded taper instead of holding the adjacent lane.
            self._merge_fallback_active = True
            self._lateral_watchdog_engaged = True
            self._lateral_watchdog_stood_down = False

    def _configure_debug_output(self) -> None:
        self._close_debug_output()
        if os.environ.get("MARSHAL_ORACLE_DEBUG") != "1" or not self._episode_dir:
            return
        try:
            path = os.path.join(self._episode_dir, "oracle_debug.jsonl")
            self._debug_file = open(path, "w", encoding="utf-8", buffering=1)
            self.step = self._step_with_debug
        except OSError:
            self._debug_file = None

    def _write_debug_record(self, obs: Dict[str, Any], sim_time: float) -> None:
        if self._debug_file is None:
            return
        try:
            forward, _spawn_lateral = self._route_displacement()
            lateral = self._route_lateral_offset()
            if sim_time < self._onset_time:
                phase = "pre_onset"
            elif self._action == "DETOUR" and self._detour_merge_started:
                phase = "merge"
            elif self._action == "DETOUR" and self._detour_committed:
                phase = "detour"
            elif self._action == "DETOUR":
                phase = "detour_pending"
            else:
                phase = self._action.lower()
            blocking = obs.get("blocking_hazard_forward_m")
            try:
                blocking = float(blocking)
                if not math.isfinite(blocking):
                    blocking = None
            except (TypeError, ValueError):
                blocking = None
            merge_progress = 0.0
            if self._detour_merge_started:
                if self._merge_fallback_active or self._merge_progress_distance_m > 0.0:
                    merge_progress = self._merge_progress_distance_m
                elif self._merge_start_forward_m is not None:
                    merge_progress = max(0.0, forward - self._merge_start_forward_m)
            record = {
                "sim_time": sim_time,
                "phase": phase,
                "route_offset_target": self._route_offset,
                "applied_offset_estimate": lateral,
                "blocking_hazard_forward_m": blocking,
                "merge_active": self._detour_merge_started,
                "merge_progress_m": merge_progress,
            }
            if self._route_plan_failure is not None:
                record["route_plan_failure"] = self._route_plan_failure
            self._debug_file.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._debug_file.flush()
        except Exception:
            # Diagnostics must never change controller behavior.
            self._close_debug_output()

    def _close_debug_output(self) -> None:
        self.__dict__.pop("step", None)
        debug_file, self._debug_file = self._debug_file, None
        if debug_file is not None:
            try:
                debug_file.flush()
                debug_file.close()
            except Exception:
                pass

    def teardown(self) -> None:
        self._close_debug_output()

    def _select_detour_lane(self, cur: Any) -> Optional[tuple[str, Any, float]]:
        origin_yaw = float(cur.transform.rotation.yaw)
        cur_loc = cur.transform.location
        cur_right = cur.transform.get_right_vector()
        for side in self._detour_side_order():
            try:
                lane = cur.get_left_lane() if side == "left" else cur.get_right_lane()
            except Exception:
                lane = None
            if lane is None or not self._is_driving_lane(lane):
                continue
            if abs(self._angle_delta(
                float(lane.transform.rotation.yaw), origin_yaw)) > 30.0:
                continue
            loc = lane.transform.location
            lateral = (
                (float(loc.x) - float(cur_loc.x)) * float(cur_right.x)
                + (float(loc.y) - float(cur_loc.y)) * float(cur_right.y)
            )
            if side == "left" and lateral >= -0.5:
                continue
            if side == "right" and lateral <= 0.5:
                continue
            return side, lane, lateral
        return None

    def _detour_side_order(self) -> tuple[str, str]:
        scene = self.gt.get("S_safety_context") or {}
        requested = str(scene.get("detour_side") or self.gt.get("G_gesture") or "")
        if "right" in requested.lower():
            return ("right", "left")
        return ("left", "right")

    @staticmethod
    def _is_driving_lane(wp: Any) -> bool:
        try:
            return str(wp.lane_type).lower().endswith("driving")
        except Exception:
            return True

    def _offset_into_adjacent_lane(
        self, cur: Any, lane: Any, lateral_to_lane: float
    ) -> float:
        """Use the committed baseline's lane centre plus outward clearance."""
        sign = -1.0 if lateral_to_lane < 0.0 else 1.0
        try:
            lane_width = float(lane.lane_width)
        except Exception:
            lane_width = 3.5
        try:
            ego_half_width = float(self.ego.bounding_box.extent.y)
        except Exception:
            ego_half_width = 0.95
        outer_bias = min(0.65, max(0.50, lane_width * 0.18))
        max_abs_offset = (
            abs(float(lateral_to_lane)) + lane_width * 0.5 - ego_half_width - 0.20
        )
        target_abs_offset = min(
            abs(float(lateral_to_lane)) + outer_bias,
            max_abs_offset,
        )
        return sign * max(abs(float(lateral_to_lane)), target_abs_offset)

    def _fallback_detour_offset(self) -> float:
        side = self._detour_side_order()[0]
        margin = float(self.config.get("detour_safety_margin_m", 0.55))
        magnitude = 3.5 + max(0.45, min(0.70, margin))
        return -magnitude if side == "left" else magnitude

    @staticmethod
    def _angle_delta(a: float, b: float) -> float:
        return (a - b + 180.0) % 360.0 - 180.0

    def _progress_from_spawn(self, obs: Dict[str, Any]) -> float:
        spawn = self.gt.get("ego_spawn") or {}
        try:
            sx = float(spawn.get("x"))
            sy = float(spawn.get("y"))
            yaw = float(spawn.get("yaw"))
            ex = float(obs.get("ego_x"))
            ey = float(obs.get("ego_y"))
        except Exception:
            return 0.0
        rad = yaw * 3.141592653589793 / 180.0
        return (ex - sx) * math.cos(rad) + (ey - sy) * math.sin(rad)
