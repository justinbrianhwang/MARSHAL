"""High-level TrafficOfficer actor module for the MARSHAL benchmark.

A TrafficOfficer wraps a CARLA walker (and optional scene props: cones, a
police vehicle) and exposes:

  - spawn() / destroy() lifecycle
  - set_gesture() to command a STOP / PROCEED / LEFT / RIGHT / SLOW / IDLE
  - tick() to drive the per-frame skeleton animation
  - get_metadata() for benchmark ground-truth logging
  - draw_debug() for optional in-world annotations

The class is intentionally tolerant of CARLA API gaps: missing skeleton
control, freeze_pose, or specific blueprints all degrade to logged warnings,
not exceptions.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional

from marshal_bench.actors.gesture_engine import (
    GestureEngine,
    GestureID,
    GestureState,
)
from marshal_bench.actors.officer_blueprint_selector import (
    select_cone_blueprints,
    select_officer_blueprint,
    select_police_vehicle_blueprint,
    select_warning_prop_blueprints,
)
from marshal_bench.utils.carla_api_compat import detect_capabilities, import_carla
from marshal_bench.utils.debug_viz import (
    draw_authority_arrow,
    draw_gesture_label,
    draw_target_lane_line,
)

log = logging.getLogger("marshal_bench.actors.traffic_officer")


class TrafficOfficer:
    """Spawn-and-control wrapper around a CARLA walker acting as traffic officer."""

    # z-lift candidates applied on spawn collisions to ride over uneven terrain
    _SPAWN_Z_RETRIES_M: tuple[float, ...] = (0.0, 0.5, 1.0)
    _PROCEED_BODY_YAW_OFFSET_DEG: float = -90.0
    _PADDLE_RADIUS_M: float = 0.32

    def __init__(
        self,
        world: Any,
        transform: Any,
        authority_type: str = "police",
        authorized: bool = True,
        blueprint_id: Optional[str] = None,
        role_name: str = "traffic_officer",
        use_debug_visuals: bool = False,
        use_skeleton: bool = True,
        fixed_location: bool = True,
        spawn_cones: bool = False,
        spawn_police_vehicle: bool = False,
        hand_prop: Optional[str] = None,
        hand_prop_yaw_offset: float = 0.0,
        hand_prop_z_offset: float = 0.30,
    ) -> None:
        self.world = world
        self.transform = transform
        self.authority_type = authority_type
        self.authorized = authorized
        self.blueprint_id = blueprint_id
        self.role_name = role_name
        self.use_debug_visuals = use_debug_visuals
        self.use_skeleton = use_skeleton
        self.fixed_location = fixed_location
        self.spawn_cones = spawn_cones
        self.spawn_police_vehicle = spawn_police_vehicle
        # Prop the officer holds in the right hand (e.g. "static.prop.stopquad").
        # CARLA cannot attach an actor to a walker bone, so the prop is
        # world-spawned and snapped onto the hand bone every tick().
        self.hand_prop = hand_prop
        self.hand_prop_yaw_offset = float(hand_prop_yaw_offset)
        self.hand_prop_z_offset = float(hand_prop_z_offset)
        self._hand_prop_actor: Optional[Any] = None

        self._caps = detect_capabilities(world)
        self._gesture_engine = GestureEngine(self._caps)

        self._actor: Optional[Any] = None
        self._base_transform: Optional[Any] = None
        self._actor_blueprint_id: Optional[str] = None
        self._blueprint_reason: str = ""
        self._aux_actors: list[Any] = []
        self._skeleton_ok: bool = False
        self._current_gesture: Optional[GestureState] = None
        self._using_fallback: bool = False
        self._destroyed: bool = False
        self._hand_prop_blueprint_id: Optional[str] = None
        self._hand_prop_debug_paddle: bool = False

    # ============================================================ lifecycle
    def spawn(self) -> Optional[Any]:
        """Select blueprint, spawn the walker and optional aux actors. Returns the walker actor."""
        bp, reason = select_officer_blueprint(self.world, preferred=self.blueprint_id)
        if bp is None:
            log.error("TrafficOfficer.spawn: no walker blueprint available — aborting.")
            return None
        self._blueprint_reason = reason
        self._actor_blueprint_id = getattr(bp, "id", None)
        log.info("Selected officer blueprint: %s (%s)", self._actor_blueprint_id, reason)

        self._safe_set_attribute(bp, "role_name", self.role_name)
        if getattr(bp, "has_attribute", None) and bp.has_attribute("is_invincible"):
            self._safe_set_attribute(bp, "is_invincible", "true")

        actor = self._try_spawn_with_z_lift(bp, self.transform)
        if actor is None:
            log.error("Failed to spawn TrafficOfficer walker after z-lift retries.")
            return None
        self._actor = actor
        if self._base_transform is None:
            self._base_transform = self.transform

        if self.use_skeleton and self._gesture_engine.supports_skeleton_control(actor):
            self._skeleton_ok = True
        else:
            log.info("Skeleton control unavailable for this officer; debug fallback will be used.")

        if self.spawn_cones:
            self._spawn_cones_around_officer()
        if self.spawn_police_vehicle:
            self._spawn_police_vehicle_behind()
        if self.hand_prop:
            self._spawn_hand_prop()

        return actor

    def destroy(self) -> None:
        if self._destroyed:
            return
        for ax in list(self._aux_actors):
            self._safe_destroy(ax)
        self._aux_actors.clear()
        if self._actor is not None:
            self._safe_destroy(self._actor)
            self._actor = None
        self._destroyed = True

    # ============================================================ commands
    def set_gesture(
        self,
        gesture_id: GestureID,
        onset_time: Optional[float] = None,
        duration: Optional[float] = None,
        target_relation: str = "ego",
        target_lane_id: Optional[int] = None,
    ) -> None:
        if onset_time is None:
            onset_time = self._world_time()
        self._current_gesture = GestureState(
            gesture_id=gesture_id,
            onset_time=onset_time,
            duration=duration,
            target_relation=target_relation,
            target_lane_id=target_lane_id,
            authority_valid=self.authorized,
        )
        self._using_fallback = False
        log.info(
            "TrafficOfficer set_gesture: %s onset=%.2f duration=%s target=%s lane=%s",
            gesture_id.value, onset_time, duration, target_relation, target_lane_id,
        )

    def tick(self, timestamp: float) -> None:
        """Per-frame update; safe to call regardless of whether a gesture is active."""
        if self._actor is None:
            return

        # Keep any hand-held prop (STOP sign, baton, ...) locked to the hand
        # bone — independent of the gesture window, so the officer always
        # carries it.
        gs = self._current_gesture
        self._apply_body_orientation(gs.gesture_id if gs is not None else GestureID.IDLE)
        if gs is None or gs.gesture_id is GestureID.IDLE:
            if self._skeleton_ok:
                self._gesture_engine.apply_idle(self._actor)
            self._track_hand_prop()
            if self.use_debug_visuals:
                self.draw_debug()
            return

        # active window check (None duration -> persistent)
        in_window = True
        if gs.duration is not None:
            in_window = (gs.onset_time <= timestamp <= gs.onset_time + gs.duration)
        if not in_window:
            if self._skeleton_ok:
                self._gesture_engine.apply_idle(self._actor)
            self._track_hand_prop()
            if self.use_debug_visuals:
                self.draw_debug()
            return

        applied_via_skeleton = False
        if self._skeleton_ok:
            applied_via_skeleton = self._gesture_engine.apply_gesture(self._actor, gs, timestamp)
            if not applied_via_skeleton:
                log.warning("Skeleton gesture failed; activating debug fallback.")
                self._using_fallback = True

        if not applied_via_skeleton:
            self._draw_fallback_label(gs)

        # Keep any hand-held prop (STOP/SLOW paddle, baton, ...) locked to the
        # hand after the pose is applied, so it renders at the current pose.
        self._track_hand_prop()

        if self.use_debug_visuals:
            self.draw_debug()

    # ============================================================ metadata
    def get_metadata(self) -> dict:
        gs = self._current_gesture
        return {
            "authority_valid": bool(self.authorized),
            "authority_type": self.authority_type,
            "gesture_id": gs.gesture_id.value if gs else GestureID.IDLE.value,
            "target_relation": gs.target_relation if gs else "ego",
            "target_lane_id": gs.target_lane_id if gs else None,
            "onset_time": gs.onset_time if gs else None,
            "duration": gs.duration if gs else None,
            "role_name": self.role_name,
            "blueprint_id": self._actor_blueprint_id,
            "skeleton_control": bool(self._skeleton_ok),
            "custom_asset": bool(getattr(self._caps, "custom_asset_walker", False)),
            "debug_visuals": bool(self.use_debug_visuals or self._using_fallback),
            "actor_id": getattr(self._actor, "id", None) if self._actor else None,
            "hand_prop": self.hand_prop,
            "hand_prop_blueprint_id": self._hand_prop_blueprint_id,
            "hand_prop_actor_id": getattr(self._hand_prop_actor, "id", None)
            if self._hand_prop_actor else None,
            "hand_prop_debug_paddle": bool(self._hand_prop_debug_paddle),
        }

    def get_actor(self) -> Optional[Any]:
        return self._actor

    def get_transform(self) -> Optional[Any]:
        if self._actor is None:
            return self.transform
        try:
            return self._actor.get_transform()
        except Exception:
            return self.transform

    # ============================================================ visuals
    def draw_debug(self) -> None:
        if self._actor is None:
            return
        try:
            carla = import_carla()
            tf = self.get_transform()
            if tf is None:
                return
            label_loc = carla.Location(
                x=tf.location.x, y=tf.location.y, z=tf.location.z + 2.2
            )
            gid = self._current_gesture.gesture_id.value if self._current_gesture else "IDLE"
            draw_gesture_label(self.world, label_loc, gid, life_time=0.2)

            if self._current_gesture is not None and self._current_gesture.target_relation == "ego":
                # caller-supplied ego location is unknown here; arrow is best-effort
                # along officer forward vector as a hint for "active authority".
                fwd = tf.get_forward_vector()
                dst = carla.Location(
                    x=tf.location.x + fwd.x * 5.0,
                    y=tf.location.y + fwd.y * 5.0,
                    z=tf.location.z + 1.2,
                )
                src = carla.Location(
                    x=tf.location.x, y=tf.location.y, z=tf.location.z + 1.2
                )
                draw_authority_arrow(self.world, src, dst, life_time=0.2)

            if self._current_gesture is not None and self._current_gesture.target_lane_id is not None:
                fwd = tf.get_forward_vector()
                dst = carla.Location(
                    x=tf.location.x + fwd.x * 8.0,
                    y=tf.location.y + fwd.y * 8.0,
                    z=tf.location.z + 0.1,
                )
                src = carla.Location(
                    x=tf.location.x, y=tf.location.y, z=tf.location.z + 0.1
                )
                draw_target_lane_line(self.world, src, dst, life_time=0.2)
        except Exception as e:
            log.debug("draw_debug failed: %s", e)

    def freeze_pose(self) -> None:
        """Snapshot the current animation pose and display it (stops looping anim)."""
        if self._actor is None:
            return
        try:
            if hasattr(self._actor, "get_pose_from_animation"):
                self._actor.get_pose_from_animation()
            if hasattr(self._actor, "show_pose"):
                self._actor.show_pose()
            elif hasattr(self._actor, "blend_pose"):
                self._actor.blend_pose(1.0)
        except Exception as e:
            log.warning("freeze_pose failed: %s", e)

    def reset_pose(self) -> None:
        """Revert to the underlying animation (cancel any frozen pose)."""
        if self._actor is None:
            return
        try:
            if hasattr(self._actor, "hide_pose"):
                self._actor.hide_pose()
            elif hasattr(self._actor, "blend_pose"):
                self._actor.blend_pose(0.0)
        except Exception as e:
            log.warning("reset_pose failed: %s", e)

    # ============================================================ helpers
    def _world_time(self) -> float:
        try:
            snap = self.world.get_snapshot()
            return float(snap.timestamp.elapsed_seconds)
        except Exception:
            return 0.0

    def _apply_body_orientation(self, gesture_id: GestureID) -> None:
        """Rotate in place for the canonical facing of the active signal."""
        if (
            not self.fixed_location
            or self._actor is None
            or gesture_id is not GestureID.PROCEED
        ):
            return
        base = self._base_transform or self.transform
        carla = import_carla()
        try:
            current = self._actor.get_transform()
        except Exception as e:
            log.debug("body orientation read failed: %s", e)
            return
        dx = current.location.x - base.location.x
        dy = current.location.y - base.location.y
        dz = current.location.z - base.location.z
        if dx * dx + dy * dy > 25.0 or abs(dz) > 5.0:
            # CARLA may briefly report an uninitialized walker transform right
            # after spawn. Wait until it settles instead of teleporting it.
            return
        tf = carla.Transform(
            carla.Location(
                x=current.location.x,
                y=current.location.y,
                z=current.location.z,
            ),
            carla.Rotation(
                pitch=current.rotation.pitch,
                yaw=base.rotation.yaw + self._PROCEED_BODY_YAW_OFFSET_DEG,
                roll=current.rotation.roll,
            ),
        )
        try:
            self._actor.set_transform(tf)
        except Exception as e:
            log.debug("body orientation update failed: %s", e)

    def _safe_set_attribute(self, bp: Any, key: str, value: str) -> None:
        try:
            if hasattr(bp, "has_attribute") and bp.has_attribute(key):
                bp.set_attribute(key, value)
        except Exception as e:
            log.debug("set_attribute(%s=%s) failed: %s", key, value, e)

    def _try_spawn_with_z_lift(self, bp: Any, transform: Any) -> Optional[Any]:
        carla = import_carla()
        try:
            fwd = transform.get_forward_vector()
            right = transform.get_right_vector()
        except Exception:
            fwd = type("F", (), {"x": 1.0, "y": 0.0})()
            right = type("R", (), {"x": 0.0, "y": 1.0})()
        offsets = (
            (0.0, 0.0),
            (0.0, -0.6),
            (0.0, 0.6),
            (-1.0, 0.0),
            (1.0, 0.0),
            (-1.0, -0.6),
            (1.0, 0.6),
            (0.0, -1.2),
            (0.0, 1.2),
        )
        z_lifts = tuple(dict.fromkeys((*self._SPAWN_Z_RETRIES_M, 1.5, 2.0)))
        for forward_offset, lateral_offset in offsets:
            base_x = (
                transform.location.x
                + fwd.x * forward_offset
                + right.x * lateral_offset
            )
            base_y = (
                transform.location.y
                + fwd.y * forward_offset
                + right.y * lateral_offset
            )
            for z_lift in z_lifts:
                try:
                    tf = carla.Transform(
                        carla.Location(
                            x=base_x,
                            y=base_y,
                            z=transform.location.z + z_lift,
                        ),
                        carla.Rotation(
                            pitch=transform.rotation.pitch,
                            yaw=transform.rotation.yaw,
                            roll=transform.rotation.roll,
                        ),
                    )
                    actor = self.world.try_spawn_actor(bp, tf)
                    if actor is not None:
                        self._base_transform = tf
                        if z_lift > 0 or forward_offset or lateral_offset:
                            log.info(
                                "Spawned officer with z-lift=%.2fm, forward=%.1fm, lateral=%.1fm",
                                z_lift,
                                forward_offset,
                                lateral_offset,
                            )
                        return actor
                except Exception as e:
                    log.debug("try_spawn_actor z_lift=%s raised: %s", z_lift, e)
        return None

    def _safe_destroy(self, actor: Any) -> None:
        try:
            actor.destroy()
        except Exception as e:
            log.debug("destroy(actor=%s) failed: %s", getattr(actor, "id", "?"), e)

    def _draw_fallback_label(self, gs: GestureState) -> None:
        try:
            carla = import_carla()
            tf = self.get_transform()
            if tf is None:
                return
            loc = carla.Location(
                x=tf.location.x, y=tf.location.y, z=tf.location.z + 2.4
            )
            draw_gesture_label(self.world, loc, f"OFFICER: {gs.gesture_id.value}", life_time=0.2)
        except Exception as e:
            log.debug("fallback label draw failed: %s", e)

    # -------------------------------------------------- hand-held prop
    def _spawn_hand_prop(self) -> None:
        """World-spawn the prop the officer holds in the right hand.

        It is placed roughly at the officer for one frame; :meth:`tick` then
        snaps it onto the ``crl_hand__R`` bone every frame. Physics is disabled
        so it does not topple between spawn and the first track.
        """
        carla = import_carla()
        try:
            bp = self.world.get_blueprint_library().find(self.hand_prop)
        except Exception as e:
            if self._should_draw_debug_paddle():
                self._hand_prop_debug_paddle = True
                log.warning(
                    "Flagger hand prop %r not found: %s; using drawn STOP/SLOW paddle.",
                    self.hand_prop,
                    e,
                )
            else:
                log.warning("Officer hand prop %r not found: %s", self.hand_prop, e)
            return
        self._hand_prop_blueprint_id = getattr(bp, "id", self.hand_prop)
        tf = self.get_transform()
        spawn_tf = carla.Transform(
            carla.Location(tf.location.x, tf.location.y, tf.location.z + 1.2),
            carla.Rotation(yaw=tf.rotation.yaw),
        )
        try:
            actor = self.world.try_spawn_actor(bp, spawn_tf)
        except Exception as e:
            log.warning("Officer hand prop %s spawn raised: %s", self.hand_prop, e)
            return
        if actor is None:
            log.warning("Officer hand prop %s spawn returned None", self.hand_prop)
            return
        try:
            actor.set_simulate_physics(False)
        except Exception as e:
            log.debug("hand prop set_simulate_physics failed: %s", e)
        self._hand_prop_actor = actor
        self._aux_actors.append(actor)
        log.info("Officer hand prop %s spawned (id=%s)", self.hand_prop, actor.id)

    def _track_hand_prop(self) -> None:
        """Snap the hand prop onto the officer's right-hand bone this frame."""
        if (
            self._actor is None
            or (self._hand_prop_actor is None and not self._hand_prop_debug_paddle)
        ):
            return
        carla = import_carla()
        try:
            bones = self._actor.get_bones().bone_transforms
        except Exception as e:
            log.debug("get_bones for hand prop failed: %s", e)
            return
        for bt in bones:
            if bt.name != "crl_hand__R":
                continue
            w = bt.world
            loc = carla.Location(
                w.location.x, w.location.y, w.location.z + self.hand_prop_z_offset
            )
            try:
                o_yaw = self._actor.get_transform().rotation.yaw
            except Exception:
                o_yaw = 0.0
            # Absolute yaw (officer body facing) keeps the STOP face square to
            # the ego instead of spinning with the wrist joint.
            rot = carla.Rotation(0.0, o_yaw + self.hand_prop_yaw_offset, 0.0)
            if self._hand_prop_actor is not None:
                try:
                    self._hand_prop_actor.set_transform(carla.Transform(loc, rot))
                except Exception as e:
                    log.debug("hand prop set_transform failed: %s", e)
            if self._hand_prop_debug_paddle:
                self._draw_debug_paddle(loc, rot.yaw)
            return

    def _should_draw_debug_paddle(self) -> bool:
        if str(self.authority_type).lower() != "flagger":
            return False
        prop = str(self.hand_prop or "").lower()
        return any(token in prop for token in ("stop", "slow", "paddle", "stopquad"))

    def _draw_debug_paddle(self, center: Any, yaw_deg: float) -> None:
        """Draw a visible STOP/SLOW paddle at the hand when no prop BP exists."""
        carla = import_carla()
        debug = getattr(self.world, "debug", None)
        if debug is None:
            return
        gid = self._current_gesture.gesture_id if self._current_gesture else GestureID.STOP
        label = "SLOW" if gid is GestureID.SLOW else "STOP"
        face = carla.Color(175, 0, 0) if label == "STOP" else carla.Color(220, 185, 0)
        ink = carla.Color(150, 150, 150) if label == "STOP" else carla.Color(10, 10, 10)
        yaw = math.radians(yaw_deg)
        right = (math.cos(yaw + math.pi / 2.0), math.sin(yaw + math.pi / 2.0))
        r = self._PADDLE_RADIUS_M

        def pt(dx: float, dz: float) -> Any:
            return carla.Location(
                x=center.x + right[0] * dx,
                y=center.y + right[1] * dx,
                z=center.z + dz,
            )

        # Dense horizontal strokes make the debug paddle read like a filled
        # sign in the RGB camera, not just a wireframe outline.
        for i in range(-5, 6):
            dz = r * i / 6.0
            half = r * (0.72 if abs(i) <= 2 else 0.48)
            try:
                debug.draw_line(
                    pt(-half, dz),
                    pt(half, dz),
                    thickness=0.014,
                    color=face,
                    life_time=0.055,
                    persistent_lines=False,
                )
            except Exception as e:
                log.debug("debug paddle fill failed: %s", e)
                return

        verts = []
        for k in range(8):
            theta = math.radians(22.5 + 45.0 * k)
            verts.append(pt(r * math.cos(theta), r * math.sin(theta)))
        for a, b in zip(verts, verts[1:] + verts[:1]):
            debug.draw_line(
                a,
                b,
                thickness=0.014,
                color=ink,
                life_time=0.055,
                persistent_lines=False,
            )
        self._draw_paddle_label_strokes(debug, pt, label, ink)

    def _draw_paddle_label_strokes(
        self,
        debug: Any,
        pt: Any,
        label: str,
        ink: Any,
    ) -> None:
        """Draw STOP/SLOW using compact line strokes in the paddle plane."""
        scale = 0.055 if label == "STOP" else 0.047
        gap = scale * 0.45
        width = scale
        height = scale * 2.0
        total = len(label) * width + (len(label) - 1) * gap
        x = -total / 2.0
        z = -height / 2.0

        def seg(x1: float, z1: float, x2: float, z2: float) -> None:
            debug.draw_line(
                pt(x1, z1),
                pt(x2, z2),
                thickness=0.011,
                color=ink,
                life_time=0.055,
                persistent_lines=False,
            )

        for ch in label:
            x0 = x
            x1 = x + width
            xm = x + width * 0.5
            z0 = z
            z1 = z + height
            zm = z + height * 0.5
            if ch == "S":
                seg(x0, z1, x1, z1)
                seg(x0, zm, x1, zm)
                seg(x0, z0, x1, z0)
                seg(x0, zm, x0, z1)
                seg(x1, z0, x1, zm)
            elif ch == "T":
                seg(x0, z1, x1, z1)
                seg(xm, z0, xm, z1)
            elif ch == "O":
                seg(x0, z0, x0, z1)
                seg(x1, z0, x1, z1)
                seg(x0, z1, x1, z1)
                seg(x0, z0, x1, z0)
            elif ch == "P":
                seg(x0, z0, x0, z1)
                seg(x0, z1, x1, z1)
                seg(x0, zm, x1, zm)
                seg(x1, zm, x1, z1)
            elif ch == "L":
                seg(x0, z0, x0, z1)
                seg(x0, z0, x1, z0)
            elif ch == "W":
                seg(x0, z1, x0 + width * 0.2, z0)
                seg(x0 + width * 0.2, z0, xm, zm)
                seg(xm, zm, x0 + width * 0.8, z0)
                seg(x0 + width * 0.8, z0, x1, z1)
            x += width + gap

    # -------------------------------------------------- optional aux actors
    def _spawn_cones_around_officer(self) -> None:
        cones = select_cone_blueprints(self.world)
        if not cones:
            log.info("No traffic-cone blueprints available; skipping cones.")
            return
        carla = import_carla()
        cone_bp = cones[0]
        # half-circle of 4 cones in front of the officer (relative offsets in meters)
        offsets = [(-1.5, -1.0), (-0.5, -1.5), (0.5, -1.5), (1.5, -1.0)]
        tf = self.transform
        try:
            fwd = tf.get_forward_vector()
            right_x, right_y = fwd.y, -fwd.x  # 2D perpendicular (right-hand)
        except Exception:
            fwd = type("F", (), {"x": 1.0, "y": 0.0})()
            right_x, right_y = 0.0, -1.0
        for rx, fy in offsets:
            cx = tf.location.x + right_x * rx + fwd.x * (-fy)
            cy = tf.location.y + right_y * rx + fwd.y * (-fy)
            cz = tf.location.z
            ctf = carla.Transform(carla.Location(x=cx, y=cy, z=cz), carla.Rotation())
            try:
                a = self.world.try_spawn_actor(cone_bp, ctf)
                if a is not None:
                    self._aux_actors.append(a)
            except Exception as e:
                log.debug("cone spawn failed: %s", e)

    def _spawn_police_vehicle_behind(self) -> None:
        vbp = select_police_vehicle_blueprint(self.world)
        if vbp is None:
            log.info("No police-like vehicle blueprint available; skipping.")
            return
        carla = import_carla()
        tf = self.transform
        try:
            fwd = tf.get_forward_vector()
        except Exception:
            fwd = type("F", (), {"x": 1.0, "y": 0.0})()
        vtf = carla.Transform(
            carla.Location(
                x=tf.location.x - fwd.x * 6.0,
                y=tf.location.y - fwd.y * 6.0,
                z=tf.location.z + 0.2,
            ),
            carla.Rotation(
                pitch=tf.rotation.pitch, yaw=tf.rotation.yaw, roll=tf.rotation.roll
            ),
        )
        try:
            a = self.world.try_spawn_actor(vbp, vtf)
            if a is not None:
                self._aux_actors.append(a)
        except Exception as e:
            log.debug("police vehicle spawn failed: %s", e)

    # convenience: optional warning prop ahead of officer
    def spawn_warning_props(self) -> None:
        props = select_warning_prop_blueprints(self.world)
        if not any(props.values()):
            return
        carla = import_carla()
        tf = self.transform
        try:
            fwd = tf.get_forward_vector()
        except Exception:
            fwd = type("F", (), {"x": 1.0, "y": 0.0})()
        # place each available prop at increasing forward distance
        for i, (_cat, bp) in enumerate(p for p in props.items() if p[1] is not None):
            d = 2.5 + i * 1.2
            ptf = carla.Transform(
                carla.Location(
                    x=tf.location.x + fwd.x * d,
                    y=tf.location.y + fwd.y * d,
                    z=tf.location.z,
                ),
                carla.Rotation(yaw=tf.rotation.yaw),
            )
            try:
                a = self.world.try_spawn_actor(bp, ptf)
                if a is not None:
                    self._aux_actors.append(a)
            except Exception as e:
                log.debug("warning prop spawn failed: %s", e)
