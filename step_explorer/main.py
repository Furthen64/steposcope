from __future__ import annotations

import sys


def _default_snapshot_config() -> dict:
    return {
        "interval": 5,
        "views": ["iso", "front", "top", "iso-above-left", "iso-below-left"],
        "width": 1280,
        "height": 960,
        "out_root": "output",
        "max_snapshots": 0,
        "show_arrows": True,
        "show_points": False,
        "caption": True,
        "show_axes": True,
        "confirm_above": 200,
    }


def _load_snapshot_config(path) -> dict:
    import json
    from pathlib import Path

    config = _default_snapshot_config()
    if path is None:
        candidate = Path("headless/snapshots.json")
        if candidate.is_file():
            path = candidate
        else:
            return config
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"steposcope: could not read config {path}: {exc}", file=sys.stderr)
        return config
    if isinstance(loaded, dict):
        config.update({key: value for key, value in loaded.items() if value is not None})
    return config


def _snapshots_main(argv: list[str]) -> int:
    import argparse
    from pathlib import Path

    from .headless import CAMERA_DIRECTIONS
    from .progress import Progress
    from .snapshots import (
        SnapshotPlan,
        bounding_box,
        collect_elements,
        geometry_event_orders,
        normalize_view,
        plan_orders,
        run,
        timestamp_dir,
    )
    from .step.parser import StepDocument

    def views(value: str) -> tuple[str, ...]:
        requested = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
        invalid = [view for view in requested if view not in CAMERA_DIRECTIONS]
        if not requested or invalid:
            detail = f"unknown view(s): {', '.join(invalid)}; " if invalid else ""
            raise argparse.ArgumentTypeError(
                f"{detail}choose at least one view from: {', '.join(CAMERA_DIRECTIONS)}"
            )
        return tuple(dict.fromkeys(normalize_view(view) for view in requested))

    parser = argparse.ArgumentParser(
        prog="steposcope snapshots",
        description="Render a fixed-camera time-lapse of a STEP file's geometry building up.",
    )
    parser.add_argument("file", type=Path, help="the STEP file to snapshot")
    parser.add_argument("--config", type=Path, default=None, help="JSON config (default: ./headless/snapshots.json if present)")
    parser.add_argument("--interval", type=int, default=None, help="geometry events per frame (overrides config)")
    parser.add_argument("--views", type=views, default=None, help="comma-separated views (overrides config)")
    parser.add_argument("--out", type=Path, default=None, help="output root; a datetime folder is made inside (overrides config)")
    parser.add_argument("--width", type=int, default=None)
    parser.add_argument("--height", type=int, default=None)
    parser.add_argument("--max-snapshots", type=int, default=None, metavar="N", help="cap frame count by widening the stride (0 = no cap)")
    parser.add_argument("--yes", action="store_true", help="never prompt; render the planned cadence as-is")
    parser.add_argument("--no-arrows", action="store_true", help="omit the per-face yellow normal arrows")
    parser.add_argument("--show-points", action="store_true", help="also draw every raw cartesian point")
    parser.add_argument("--no-caption", action="store_true", help="omit the on-image text overlay")
    parser.add_argument("--no-axes", action="store_true", help="omit the centered XYZ orientation triad")
    parser.add_argument("--quiet", action="store_true", help="suppress progress reporting on stderr")
    args = parser.parse_args(argv)

    config = _load_snapshot_config(args.config)
    interval = args.interval if args.interval is not None else int(config["interval"])
    if args.views is not None:
        views_value = args.views
    else:
        configured_views = config.get("views")
        if not isinstance(configured_views, list) or not all(isinstance(view, str) for view in configured_views):
            parser.error("config views must be a nonempty list of view names")
        try:
            views_value = views(",".join(configured_views))
        except argparse.ArgumentTypeError as exc:
            parser.error(f"invalid config views: {exc}")
    width = args.width if args.width is not None else int(config["width"])
    height = args.height if args.height is not None else int(config["height"])
    max_snapshots = args.max_snapshots if args.max_snapshots is not None else int(config["max_snapshots"])
    if max_snapshots < 0:
        parser.error("--max-snapshots must be zero or positive")
    out_root = args.out if args.out is not None else Path(config["out_root"])
    show_arrows = config["show_arrows"] and not args.no_arrows
    show_points = config["show_points"] or args.show_points
    caption = config["caption"] and not args.no_caption
    show_axes = config["show_axes"] and not args.no_axes

    source = args.file
    if not source.is_file():
        print(f"steposcope: no such file: {source}", file=sys.stderr)
        return 2

    progress = Progress(enabled=not args.quiet)
    try:
        progress.stage(f"reading and tessellating {source.name} (once)")
        document = StepDocument.from_file(source)
        from .geometry.builder import GeometryBuilder

        from .llm_report import long_playback_warning

        warning = long_playback_warning(document)
        if warning:
            print(f"steposcope: {source.name}: {warning}", file=sys.stderr)
        elements = collect_elements(document, GeometryBuilder(document).build())
        events = geometry_event_orders(elements)
        box = bounding_box(elements)
    except Exception as exc:
        print(f"steposcope: {source}: {exc}", file=sys.stderr)
        return 2

    if box is None or not events:
        print("steposcope: no renderable geometry in this file.", file=sys.stderr)
        return 2

    orders = plan_orders(events, interval, max_snapshots)
    frames = len(orders)
    total_images = frames * len(views_value)
    progress.note(
        f"{len(events)} geometry events -> {frames} frame(s) x {len(views_value)} view(s) = {total_images} PNG(s) at interval {interval}"
    )

    interval, max_snapshots = _snapshot_gate(
        interval, max_snapshots, frames, int(config["confirm_above"]), views_value, args.yes
    )
    if interval is None:
        print("steposcope: cancelled.", file=sys.stderr)
        return 130
    orders = plan_orders(events, interval, max_snapshots)

    out_dir = timestamp_dir(out_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = SnapshotPlan(
        source=source,
        out_dir=out_dir,
        interval=interval,
        views=tuple(views_value),
        orders=orders,
        total_events=len(events),
        width=width,
        height=height,
    )
    progress.stage(f"writing {len(orders)} frame(s) to {out_dir}")
    try:
        manifest = run(plan, elements, box, progress=progress, show_arrows=show_arrows, show_points=show_points, caption=caption, show_axes=show_axes)
    except Exception as exc:
        print(f"steposcope: {exc}", file=sys.stderr)
        return 2
    progress.done(f"wrote {manifest['frame_count']} frame(s)", 0)
    print(str(out_dir))
    return 0


def _snapshot_gate(interval, max_snapshots, frames, confirm_above, views, use_default):
    """Offer to shrink a very large batch before committing to it.

    Returns (interval, max_snapshots) to use, or (None, ...) if the user cancels.
    With --yes, a non-interactive stdin, or a modest frame count it is a no-op.
    """

    if use_default or frames <= confirm_above or not sys.stdin.isatty():
        return interval, max_snapshots
    total_images = frames * len(views)
    print(
        f"\nThat is {frames} frames ({total_images} images) at interval {interval}.\n"
        f"Adjust? [Enter=keep, number=new interval, a=auto cap ~250, n=cancel] ",
        end="",
        file=sys.stderr,
        flush=True,
    )
    try:
        answer = input().strip().lower()
    except EOFError:
        return interval, max_snapshots
    if answer in {"n", "no", "cancel"}:
        return None, max_snapshots
    if answer == "" :
        return interval, max_snapshots
    if answer == "a":
        return interval, 250
    try:
        value = int(answer)
    except ValueError:
        return interval, max_snapshots
    return (value if value > 0 else interval), max_snapshots


def _render_main(argv: list[str]) -> int:
    import argparse
    import glob
    import json
    import time
    from pathlib import Path

    from .headless import CAMERA_DIRECTIONS, process_file
    from .progress import Progress

    def views(value: str) -> tuple[str, ...]:
        requested = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
        invalid = [view for view in requested if view not in CAMERA_DIRECTIONS]
        if not requested or invalid:
            choices = ", ".join(CAMERA_DIRECTIONS)
            detail = f"unknown view(s): {', '.join(invalid)}; " if invalid else ""
            raise argparse.ArgumentTypeError(f"{detail}choose a comma-separated list from: {choices}")
        return requested

    parser = argparse.ArgumentParser(prog="steposcope render", description="Render STEP files and judge raw shell orientation.")
    parser.add_argument("files", nargs="+", help="STEP files, directories, or glob patterns")
    parser.add_argument("--out", required=True, type=Path, help="output directory")
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--camera", choices=("iso", "front", "top", "right"), default="iso")
    parser.add_argument("--views", type=views, help="comma-separated views to render (iso,front,top,right); overrides --camera")
    parser.add_argument("--mode", choices=("normals", "shaded", "faces"), default="normals", help="normals colors consistent faces blue and inconsistent faces red; shaded uses neutral material; faces colors and labels each face by STEP id")
    parser.add_argument("--quiet", action="store_true", help="suppress progress reporting on stderr")
    parser.add_argument("--max-labels", type=int, default=0, metavar="N",
                        help="cap faces-mode labels to the N largest faces (0 = no cap). "
                             "Text actors dominate the render on dense files: ~35s/view for "
                             "3500 labels versus ~5s for 400.")
    parser.add_argument("--fit-box", metavar="X0,Y0,Z0,X1,Y1,Z1", default=None,
                        help="frame the camera on these world bounds instead of each file's own. "
                             "Pass the SAME box for every file in a comparison: the default "
                             "per-file auto-fit rescales every render to fill the frame, so a "
                             "uniform size difference between two files is invisible.")
    args = parser.parse_args(argv)
    fit_box = None
    if args.fit_box:
        try:
            values = tuple(float(v) for v in args.fit_box.split(","))
        except ValueError:
            parser.error(f"--fit-box must be 6 comma-separated numbers, got {args.fit_box!r}")
        if len(values) != 6 or not all(values[i] < values[i + 3] for i in range(3)):
            parser.error(f"--fit-box needs X0<X1, Y0<Y1, Z0<Z1; got {args.fit_box!r}")
        fit_box = values
    paths: list[Path] = []
    for item in args.files:
        expanded = [Path(path) for path in glob.glob(item, recursive=True)] or [Path(item)]
        for path in expanded:
            paths.extend(sorted(candidate for candidate in path.rglob("*") if candidate.suffix.lower() in {".step", ".stp"}) if path.is_dir() else [path])
    paths = list(dict.fromkeys(path.resolve() for path in paths))
    args.out.mkdir(parents=True, exist_ok=True)
    reports, errors = [], []
    used: dict[str, int] = {}
    progress = Progress(enabled=not args.quiet)
    total = len(paths)
    wall = time.time()
    progress.stage(f"{total} file(s), mode={args.mode}")
    for index, path in enumerate(paths, start=1):
        stem = path.stem
        used[stem] = used.get(stem, 0) + 1
        name = stem if used[stem] == 1 else f"{stem}-{used[stem]}"
        progress.end_item()
        progress.stage(f"[{index}/{total}] {path.name}")
        try:
            reports.append(process_file(path, args.out / name, args.width, args.height, args.camera, args.mode, args.views, progress=progress, max_labels=args.max_labels, fit_box=fit_box))
        except Exception as exc:
            errors.append({"source": str(path), "error": str(exc)})
            print(f"steposcope: {path}: {exc}", file=sys.stderr)
    progress.note(f"total {time.time() - wall:.2f}s for {len(reports)} file(s), {len(errors)} error(s)")
    summary = {"reports": reports, "errors": errors}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if errors:
        return 2
    return 1 if any(solid["verdict"] != "consistent" for report in reports for solid in report["solids"]) else 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "render":
        return _render_main(args[1:])
    if args and args[0] == "snapshots":
        return _snapshots_main(args[1:])
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow
    app = QApplication([sys.argv[0], *args])
    window = MainWindow(args[0] if args and not args[0].startswith("-") else None)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
