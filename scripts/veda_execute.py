#!/usr/bin/env python3
"""Run bounded offline replay or a separately armed persistent local loop.

Readiness: offline integration only. No complete validated live reader is
bundled; full autonomous play and live performance remain unvalidated.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from veda.calibration import CalibrationReport
from veda.execution import ActionJournal, ARM_PHRASE, ExecutionLoop, RUNTIME_FIELDS
from veda.execution_adapters import (AnalysisWorker, RecordedInterpreter,
                                     ReplayController, ReplaySource, WarmController)
from veda.pipeline_trace import TraceRecorder
from veda.runtime_frames import BoundedCaptureSource
from veda.runtime_authorization import authorize_runtime, RuntimeAuthorizationError


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Exit 0 means a report was produced without unresolved actions. It does not certify gameplay readiness or completion; inspect outcome, reason and cleanup_errors.")
    parser.add_argument("manifest", type=Path, nargs="?", help="saved frame/reading replay manifest")
    parser.add_argument("--mode", choices=("replay", "shadow", "live"), default="replay",
        help="replay: recorded readings and fake inputs; shadow: one offline proposal, no inputs; live: separately armed, independently validated reader and existing bridge required")
    parser.add_argument("--trace", type=Path, required=True, help="append-only pipeline trace JSONL")
    parser.add_argument("--journal", type=Path, required=True, help="durable pending-input journal JSONL")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-inputs", type=int, default=100)
    parser.add_argument("--max-seconds", type=float, default=120)
    parser.add_argument("--run-id")
    parser.add_argument("--arm", help="explicit per-run arming phrase; never inferred from replay")
    parser.add_argument("--calibration", type=Path, help="independent runtime recognition calibration report")
    parser.add_argument("--socket", type=Path, help="existing, already ready VEDA warm bridge socket")
    parser.add_argument("--reader-command", help="JSON array launching one calibrated persistent local reader")
    parser.add_argument("--reader-identity", type=Path, help="Explicit identity JSON for the validated reader configuration")
    parser.add_argument("--evidence-directory", type=Path, help="Retained boundary/mismatch frames; defaults beside the journal")
    args = parser.parse_args(argv)
    if args.max_inputs < 1 or not math.isfinite(args.max_seconds) or args.max_seconds <= 0:
        parser.error("runtime input/time limits must be positive and finite")
    input_paths = [p.resolve() for p in (args.manifest, args.calibration, args.reader_identity) if p]
    output_paths = [p.resolve() for p in (args.trace, args.journal, args.report) if p]
    if len(set(output_paths)) != len(output_paths) or set(input_paths) & set(output_paths):
        parser.error("trace, journal and report paths must differ from one another and all evidence inputs")
    evidence_directory = (args.evidence_directory or args.journal.parent / "evidence").resolve()
    if evidence_directory in set(input_paths + output_paths):
        parser.error("evidence directory must differ from input and output file paths")
    if evidence_directory.exists() and not evidence_directory.is_dir():
        parser.error("evidence directory is not a directory")
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    authorization = None
    if args.mode == "live":
        if args.manifest or args.arm != ARM_PHRASE or not all((args.run_id, args.calibration, args.socket, args.reader_command, args.reader_identity)):
            parser.error("live requires separate run arming, run ID, calibrated reader, and an existing ready bridge; replay files cannot arm it")
        try:
            evidence = json.loads(args.calibration.read_text())
            identity = json.loads(args.reader_identity.read_text())
            command = json.loads(args.reader_command)
            if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
                raise RuntimeAuthorizationError("reader command must be a nonempty JSON string array")
            authorization = authorize_runtime(evidence, reader_command=command, reader_identity=identity,
                source_kind="live_capture", required_fields=RUNTIME_FIELDS)
            code_paths = {Path(item["path"]).resolve() for item in identity["code_files"]}
            if code_paths & set(output_paths):
                raise RuntimeAuthorizationError("runtime outputs must not overwrite validated reader code or configuration")
        except (OSError, ValueError, TypeError, KeyError) as error:
            parser.error(f"live reader authorization failed: {error}")
        calibration = authorization.calibration
        from veda.bridge_client import BridgeClient
        source = BoundedCaptureSource()
        interpreter = AnalysisWorker(command, recognizer_identity=identity)
        controller = WarmController(BridgeClient(args.socket))
        run_id, evidence_kind = args.run_id, evidence["validation_provenance"]["evidence_kind"]
    else:
        if not args.manifest or any((args.arm, args.socket, args.reader_command, args.reader_identity, args.calibration)):
            parser.error("offline mode requires a manifest and forbids live bridge/reader/arming options")
        manifest = json.loads(args.manifest.read_text())
        source = ReplaySource(manifest, args.manifest.parent)
        interpreter = RecordedInterpreter(source)
        controller = ReplayController(manifest.get("expected_commands", []))
        record = manifest.get("calibration", {"field_accuracy": {}, "total_frames": 0})
        calibration = CalibrationReport(record["field_accuracy"], record["total_frames"])
        run_id, evidence_kind = manifest["run_id"], manifest.get("evidence_kind", "recorded_state_replay")
    trace = TraceRecorder(args.trace, run_id=run_id)
    loop = ExecutionLoop(source, interpreter, controller, calibration=calibration,
        run_id=run_id, mode=args.mode, arm=args.arm, trace=trace,
        journal=ActionJournal(args.journal), max_inputs=args.max_inputs,
        max_seconds=args.max_seconds, should_stop=lambda: stopped,
        authorization=authorization, evidence_directory=evidence_directory)
    result = loop.run()
    result["evidence_kind"] = evidence_kind
    result["recognition_accuracy_measured"] = False
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if not result["unresolved_actions"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
