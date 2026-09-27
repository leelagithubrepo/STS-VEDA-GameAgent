#!/usr/bin/env python3
"""Evaluate saved images only. No screen capture, controller, or run resumption.

Use --predictions for fully offline scoring. Without it, this explicit command
calls the configured local vision provider on the hash-verified saved images.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.vision_validation import evaluate_predictions, load_manifest, load_predictions, observe_saved_frames


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Exit 0 means a validation report was produced, even when runtime_authorized is false. Inspect the report before considering live use. Provider requests run sequentially; worst-case request time scales with image count, plus local processing overhead.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, help="Offline prediction JSON; makes no model calls")
    parser.add_argument("--output", type=Path, help="Report JSON; defaults to stdout")
    parser.add_argument("--save-predictions", type=Path, help="Save actual provider predictions for repeatable offline scoring")
    parser.add_argument("--model", default="blaifa/InternVL3_5:4b")
    parser.add_argument("--timeout-seconds", type=float, default=30,
        help="local provider request deadline per image, not for the entire manifest")
    parser.add_argument("--max-frames", type=int, default=24,
        help="maximum saved images allowed in an explicit provider run")
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout_seconds) or not 0 < args.timeout_seconds <= 120:
        parser.error("--timeout-seconds must be >0 and <=120")
    if not 1 <= args.max_frames <= 100:
        parser.error("--max-frames must be between 1 and 100")
    if args.predictions and args.save_predictions:
        parser.error("--save-predictions applies only to provider evaluation")
    try:
        manifest = load_manifest(args.manifest)
        protected = {args.manifest.resolve()} | {frame.image for frame in manifest.frames}
        if args.predictions:
            protected.add(args.predictions.resolve())
        destinations = [path.resolve() for path in (args.output, args.save_predictions) if path]
        if len(set(destinations)) != len(destinations) or any(path in protected for path in destinations):
            raise ValueError("output paths must differ from all evidence inputs and each other")
        if args.predictions:
            predictions = load_predictions(args.predictions)
            source = {"mode": "offline_predictions", "path": str(args.predictions.resolve())}
        else:
            if len(manifest.frames) > args.max_frames:
                raise ValueError("manifest exceeds --max-frames; no provider calls were made")
            from veda.pipeline_trace import TraceRecorder
            from veda.vision import LocalOllamaVisionProvider

            trace = TraceRecorder()
            provider = LocalOllamaVisionProvider(model=args.model, timeout_seconds=args.timeout_seconds, trace=trace)
            try:
                predictions = observe_saved_frames(manifest, provider, trace)
            finally:
                trace.close()
            source = {"mode": "saved_frame_local_provider", "model": args.model,
                      "timeout_seconds_per_request": args.timeout_seconds, "max_frames": args.max_frames}
        report = evaluate_predictions(manifest, predictions)
        report["prediction_source"] = source
        if args.save_predictions:
            args.save_predictions.parent.mkdir(parents=True, exist_ok=True)
            args.save_predictions.write_text(json.dumps({"predictions": predictions, "source": source}, indent=2) + "\n")
        rendered = json.dumps(report, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
        else:
            print(rendered, end="")
        return 0  # A valid report may truthfully deny runtime authorization.
    except (OSError, ValueError, TypeError, KeyError) as error:
        parser.exit(2, f"validation failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
