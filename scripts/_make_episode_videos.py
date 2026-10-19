"""Build 20 FPS side-by-side external | ego videos from episode artifacts."""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import OrderedDict
import csv
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Sequence

FPS = 20
FRONT_BUMPER_OFFSET_M = 2.3959
ENV_BACKENDS = {
    "conda env marshal": "imageio",
    "conda env openemma": "opencv",
    "conda env transfuser_ui": "opencv, imageio",
}


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _truth(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"true", "1", "yes"}:
        return "true"
    if text in {"false", "0", "no"}:
        return "false"
    return "n/a"


def _officer_text(tick: dict[str, Any]) -> str:
    """Officer state as rendered: the no-police placeholder (_NullOfficer) logs officer_active=True with gesture IDLE
    and no duration, so show the gesture instead of the misleading flag."""
    gesture = str(tick.get("officer_gesture_id") or "").strip().upper()
    if gesture in ("", "IDLE", "NONE") and not str(tick.get("officer_duration_sec") or "").strip():
        return "  officer=none"
    return f"  officer={gesture or 'n/a'} active={_truth(tick.get('officer_active'))}"


def format_overlay(label: str, tick: dict[str, Any], action: str = "") -> tuple[str, str]:
    """Return the two overlay lines for one metrics tick."""
    parts = (label.split("|", 2) + ["unknown"] * 3)[:3]
    sim_time = _number(tick.get("t"))
    speed = _number(tick.get("speed_kmh"))
    stopline = _number(tick.get("stopline_forward_m"))
    bumper = None if stopline is None else stopline - FRONT_BUMPER_OFFSET_M
    line1 = f"model={parts[0]}  condition={parts[1]}  seed={parts[2]}"
    line2 = (
        f"t={sim_time:.2f}s" if sim_time is not None else "t=n/a"
    ) + (
        f"  speed={speed:.2f} km/h" if speed is not None else "  speed=n/a"
    ) + (
        f"  front_bumper_to_stopline={bumper:+.3f} m"
        if bumper is not None else "  front_bumper_to_stopline=n/a"
    ) + _officer_text(tick) + (
        f"  VLM={action}" if action else "  VLM=n/a"
    )
    return line1, line2


def align_frames_to_ticks(
    external: Sequence[Path],
    ego: Sequence[Path],
    ticks: Sequence[dict[str, Any]],
    frame_ids: Sequence[int],
) -> tuple[list[tuple[Path, Path, dict[str, Any]]], int, int]:
    """Align ticks by CARLA world frame, falling back only for lagging ego frames."""
    if len(frame_ids) != len(ticks):
        raise ValueError(
            f"tick/frame mapping length mismatch: frame_ids={len(frame_ids)}, metrics_ticks={len(ticks)}"
        )
    external_by_id = {int(path.stem): path for path in external}
    ego_by_id = {int(path.stem): path for path in ego}
    ego_ids = sorted(ego_by_id)
    aligned = []
    n_fallback_ego = 0
    n_skipped = 0
    for frame_id, tick in zip(frame_ids, ticks):
        external_path = external_by_id.get(frame_id)
        if external_path is None:
            n_skipped += 1
            continue
        ego_path = ego_by_id.get(frame_id)
        if ego_path is None:
            earlier_index = bisect_left(ego_ids, frame_id) - 1
            if earlier_index < 0:
                n_skipped += 1
                continue
            ego_path = ego_by_id[ego_ids[earlier_index]]
            n_fallback_ego += 1
        aligned.append((external_path, ego_path, tick))
    return aligned, n_fallback_ego, n_skipped


def _pngs(path: Path) -> list[Path]:
    files = list(path.glob("*.png"))
    invalid = [file.name for file in files if not file.stem.isdigit()]
    if invalid:
        raise ValueError(f"non-frame PNG names in {path}: {', '.join(sorted(invalid)[:3])}")
    return sorted(files, key=lambda file: int(file.stem))


def _metric_ticks(path: Path) -> list[dict[str, Any]]:
    ticks: OrderedDict[str, dict[str, Any]] = OrderedDict()
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            timestamp = row.get("t")
            if timestamp is None:
                raise ValueError(f"metrics file has no t column: {path}")
            tick = ticks.setdefault(timestamp, {"t": timestamp})
            tick[str(row.get("key") or "")] = row.get("value")
    return list(ticks.values())


def _anchor_frame_ids(external: Sequence[Path], ticks: Sequence[dict[str, Any]]) -> list[int]:
    if not ticks:
        return []
    if not external:
        raise ValueError("cannot anchor metrics ticks without external frames")
    last_frame = max(int(path.stem) for path in external)
    return list(range(last_frame - len(ticks) + 1, last_frame + 1))


def _trace_frame_ids(path: Path, ticks: Sequence[dict[str, Any]]) -> list[int] | None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not {"sim_time", "world_frame"}.issubset(reader.fieldnames or ()):
            return None
        by_time: dict[float, int] = {}
        for row in reader:
            sim_time = _number(row.get("sim_time"))
            world_frame = _number(row.get("world_frame"))
            if sim_time is None or world_frame is None or not world_frame.is_integer():
                raise ValueError(f"invalid sim_time/world_frame in controller trace: {path}")
            by_time[round(sim_time, 6)] = int(world_frame)
    frame_ids = []
    for tick in ticks:
        sim_time = _number(tick.get("t"))
        frame_id = None if sim_time is None else by_time.get(round(sim_time, 6))
        if frame_id is None:
            raise ValueError(f"controller trace has no world frame for metrics t={tick.get('t')}: {path}")
        frame_ids.append(frame_id)
    return frame_ids


def frame_ids_for_ticks(
    episode: Path, external: Sequence[Path], ticks: Sequence[dict[str, Any]]
) -> tuple[list[int], str, int | None]:
    """Return tick frame ids, the selected rule, and anchor/trace disagreement."""
    anchor_ids = _anchor_frame_ids(external, ticks)
    for trace_path in sorted(episode.glob("*_debug/*_trace.csv")):
        trace_ids = _trace_frame_ids(trace_path, ticks)
        if trace_ids is not None:
            disagreement = max(
                (abs(trace_id - anchor_id) for trace_id, anchor_id in zip(trace_ids, anchor_ids)),
                default=0,
            )
            return trace_ids, "controller_trace", disagreement
    return anchor_ids, "anchor_last_tick_to_max_external", None


def _decisions(path: Path) -> list[tuple[float, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        events = json.load(handle) or []
    decisions = []
    for event in events:
        if event.get("name") != "vlm_decision":
            continue
        payload = event.get("payload") or {}
        timestamp = _number(payload.get("t"))
        if timestamp is None:  # vlm_decision events carry sim time in the envelope "t"
            timestamp = _number(event.get("t"))
        action = str(payload.get("action") or "").strip().upper()
        if timestamp is not None and action:
            decisions.append((timestamp, action))
    return sorted(decisions)


def _actions_for_ticks(ticks: Sequence[dict[str, Any]], decisions: Sequence[tuple[float, str]]):
    actions = []
    current = ""
    decision_index = 0
    for tick in ticks:
        timestamp = _number(tick.get("t"))
        while (timestamp is not None and decision_index < len(decisions)
               and decisions[decision_index][0] <= timestamp + 1e-9):
            current = decisions[decision_index][1]
            decision_index += 1
        actions.append(current)
    return actions


def _infer_label(episode: Path) -> str:
    metadata = episode / "metadata.json"
    config = {}
    model = condition = None
    if metadata.exists():
        try:
            payload = json.loads(metadata.read_text(encoding="utf-8"))
            config = payload.get("config") or {}
            model = payload.get("model") or (config.get("vlm") or {}).get("model") or config.get("controller")
            weather = config.get("weather") or config.get("environment") or {}
            condition = weather.get("preset") or weather.get("weather") or weather.get("visibility")
        except Exception:  # noqa: BLE001
            pass
    parts = episode.resolve().parts
    model = str(model or (parts[-3] if len(parts) >= 3 else "unknown"))
    condition = str(condition or (parts[-2] if len(parts) >= 2 else "unknown"))
    seed = config.get("seed")
    if seed is None:
        match = re.search(r"(?:seed[_-]?)?(\d+)$", episode.name, re.IGNORECASE)
        seed = match.group(1) if match else "unknown"
    return f"{model}|{condition}|{seed}"


def _backend():
    try:
        import cv2
        return "opencv", cv2
    except ImportError:
        try:
            import imageio.v2 as imageio
            return "imageio", imageio
        except ImportError as exc:
            availability = "; ".join(f"{path}: {value}" for path, value in ENV_BACKENDS.items())
            raise RuntimeError(
                "video creation requires opencv-python or imageio. Available environments: "
                + availability
            ) from exc


def _fit_height_cv2(frame: Any, height: int, cv2: Any):
    if frame.shape[0] == height:
        return frame
    width = int(round(frame.shape[1] * height / frame.shape[0]))
    return cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)


def _write_opencv(aligned, actions, label: str, output: Path, cv2: Any) -> int:
    first_external = cv2.imread(str(aligned[0][0]), cv2.IMREAD_COLOR)
    first_ego = cv2.imread(str(aligned[0][1]), cv2.IMREAD_COLOR)
    if first_external is None or first_ego is None:
        raise RuntimeError("could not read the first camera frames")
    height = min(first_external.shape[0], first_ego.shape[0])
    first_external = _fit_height_cv2(first_external, height, cv2)
    first_ego = _fit_height_cv2(first_ego, height, cv2)
    size = (first_external.shape[1] + first_ego.shape[1], height)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), FPS, size)
    if not writer.isOpened():
        raise RuntimeError(f"OpenCV could not open MP4 writer for {output}")
    n_held = 0  # ticks whose PNG was unreadable (truncated async save): previous frame of that stream is held
    prev_external, prev_ego = first_external, first_ego
    try:
        for (external_path, ego_path, tick), action in zip(aligned, actions):
            external = cv2.imread(str(external_path), cv2.IMREAD_COLOR)
            ego = cv2.imread(str(ego_path), cv2.IMREAD_COLOR)
            if external is None or ego is None:
                n_held += 1
                external = prev_external if external is None else external
                ego = prev_ego if ego is None else ego
            prev_external, prev_ego = external, ego
            external = _fit_height_cv2(external, height, cv2)
            ego = _fit_height_cv2(ego, height, cv2)
            frame = np_hstack(external, ego)
            cv2.rectangle(frame, (0, 0), (frame.shape[1], 76), (0, 0, 0), -1)
            for y, line in zip((27, 60), format_overlay(label, tick, action)):
                cv2.putText(frame, line, (14, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.67, (255, 255, 255), 2, cv2.LINE_AA)
            writer.write(frame)
    finally:
        writer.release()
    return n_held


def np_hstack(left: Any, right: Any):
    import numpy as np
    return np.hstack((left, right))


def _write_imageio(aligned, actions, label: str, output: Path, imageio: Any) -> None:
    from PIL import Image, ImageDraw, ImageFont

    writer = imageio.get_writer(str(output), fps=FPS, codec="libx264", macro_block_size=None)
    try:
        for (external_path, ego_path, tick), action in zip(aligned, actions):
            external = Image.open(external_path).convert("RGB")
            ego = Image.open(ego_path).convert("RGB")
            height = min(external.height, ego.height)
            external.thumbnail((external.width, height))
            ego.thumbnail((ego.width, height))
            canvas = Image.new("RGB", (external.width + ego.width, height))
            canvas.paste(external, (0, 0))
            canvas.paste(ego, (external.width, 0))
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 0, canvas.width, 76), fill="black")
            font = ImageFont.load_default(size=20)
            for y, line in zip((8, 39), format_overlay(label, tick, action)):
                draw.text((14, y), line, fill="white", font=font)
            writer.append_data(__import__("numpy").asarray(canvas))
    finally:
        writer.close()


def _output_path(out: Path, episode: Path, used: set[str]) -> Path:
    stem = episode.name
    candidate = f"{stem}.mp4"
    index = 2
    while candidate.lower() in used:
        candidate = f"{stem}_{index}.mp4"
        index += 1
    used.add(candidate.lower())
    return out / candidate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Backends: marshal=imageio; openemma=opencv; transfuser_ui=opencv+imageio.",
    )
    parser.add_argument("--episode", required=True, action="append", type=Path,
                        help="episode directory (repeatable)")
    parser.add_argument("--label", action="append",
                        help='model|condition|seed (repeat once per episode)')
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.label and len(args.label) != len(args.episode):
        parser.error("--label must be repeated exactly once per --episode")

    backend_name, backend_module = _backend()
    args.out.mkdir(parents=True, exist_ok=True)
    used: set[str] = set()
    made = 0
    for index, episode in enumerate(args.episode):
        try:
            external = _pngs(episode / "frames")
            ego = _pngs(episode / "frames_ego")
            ticks = _metric_ticks(episode / "metrics.csv")
            frame_ids, mapping_rule, disagreement = frame_ids_for_ticks(
                episode, external, ticks
            )
            aligned, n_fallback_ego, n_skipped = align_frames_to_ticks(
                external, ego, ticks, frame_ids
            )
            if not aligned:
                raise ValueError("no metrics ticks have usable camera frames")
            decisions = _decisions(episode / "events.json")
            actions = _actions_for_ticks([item[2] for item in aligned], decisions)
            label = args.label[index] if args.label else _infer_label(episode)
            output = _output_path(args.out, episode, used)
            n_held_unreadable = 0
            if backend_name == "opencv":
                n_held_unreadable = _write_opencv(aligned, actions, label, output, backend_module)
            else:
                _write_imageio(aligned, actions, label, output, backend_module)
            model, condition, seed = (label.split("|", 2) + ["unknown"] * 3)[:3]
            sidecar = {
                "model": model,
                "condition": condition,
                "seed": seed,
                "mapping_rule": mapping_rule,
                "n_ticks": len(ticks),
                "n_fallback_ego": n_fallback_ego,
                "n_skipped": n_skipped,
                "n_held_unreadable_png": n_held_unreadable,
            }
            if disagreement is not None:
                sidecar["anchor_trace_max_disagreement_frames"] = disagreement
            Path(f"{output}.json").write_text(
                json.dumps(sidecar, indent=2) + "\n", encoding="utf-8"
            )
            validation = (
                f", anchor_trace_max_disagreement={disagreement} frames"
                if disagreement is not None else ""
            )
            print(
                f"WROTE {output} ({len(aligned)} frames, {backend_name}, "
                f"mapping={mapping_rule}, fallback_ego={n_fallback_ego}, "
                f"skipped={n_skipped}{validation})"
            )
            made += 1
        except Exception as exc:  # noqa: BLE001
            print(f"SKIP {episode}: {exc}", file=sys.stderr)
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
