"""Synthetic binding-contract tests. No live evidence, reader or model is run."""
from contextlib import redirect_stderr
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from veda.calibration import CalibrationReport
from veda.execution import ARM_PHRASE, RUNTIME_FIELDS, ExecutionLoop, RuntimeStop
from veda.runtime_authorization import (
    RuntimeAuthorizationError, authorize_runtime, recognizer_fingerprint,
    validation_provenance_errors,
)
from veda.vision_validation import (
    ValidationManifest, evaluate_predictions, load_manifest,
)


def identity(directory):
    executable = Path(directory) / "synthetic-executable.dat"
    reader = Path(directory) / "synthetic-reader.dat"
    executable.write_bytes(b"not executable: synthetic binding test")
    reader.write_bytes(b"not executable: synthetic reader fixture")
    return {
        "schema": "veda.recognizer-identity.v1",
        "command": [str(executable), str(reader)],
        "code_files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                       for path in (executable, reader)],
        "model": {"id": "synthetic unit-test model", "sha256": "1" * 64},
        "prompt": {"sha256": "2" * 64}, "preprocessing": {"sha256": "3" * 64},
        "output_contract": "veda.runtime-reading.v1",
    }


def provenance(reader):
    return {"evidence_kind": "independent_saved_frame_validation", "source_kind": "live_capture",
            "reader_identity": copy.deepcopy(reader), "recognizer_fingerprint": recognizer_fingerprint(reader)}


def synthetic_report(reader):
    """An in-memory gate-contract fixture, never a production calibration file."""
    return {"schema": "veda.saved-frame-validation.v1", "runtime_authorized": True,
            "authorization_blockers": [], "validation_provenance": provenance(reader),
            "label_provenance": {"independent_labels_declared": True, "source": "synthetic test"},
            "unflagged_critical_errors": 0, "distinct_image_hashes": 12,
            "fields": {field: {"runtime_labeled_unique_images": 12, "runtime_accuracy": 1.0,
                               "runtime_field_validated": True} for field in RUNTIME_FIELDS}}


def authorize(report, reader, **kwargs):
    return authorize_runtime(report, reader_command=reader["command"], reader_identity=reader,
                             source_kind=kwargs.get("source_kind", "live_capture"),
                             required_fields=kwargs.get("required_fields", RUNTIME_FIELDS))


class RuntimeAuthorizationTests(unittest.TestCase):
    def test_exact_configuration_binds_and_actual_report_fields_make_calibration(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            report = synthetic_report(reader)
            report["fields"]["hp"]["runtime_accuracy"] = .95
            result = authorize(report, reader)
            self.assertEqual(result.calibration.field_accuracy["hp"], .95)
            self.assertEqual(result.calibration.total_frames, 12)
            self.assertEqual(result.recognizer_fingerprint, recognizer_fingerprint(reader))
            result.assert_runtime(reader_command=reader["command"], reader_identity=reader, source_kind="live_capture")
            result.assert_frame_source("live_capture")

    def test_every_identity_component_changes_the_fingerprint_and_is_rejected(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            report = synthetic_report(reader)
            for component in ("model", "prompt", "preprocessing"):
                changed = copy.deepcopy(reader)
                changed[component]["sha256"] = "e" * 64
                with self.subTest(component=component), self.assertRaisesRegex(RuntimeAuthorizationError, "fingerprint differs"):
                    authorize(report, changed)
            changed = copy.deepcopy(reader)
            changed["command"].append("--different-mode")
            with self.assertRaisesRegex(RuntimeAuthorizationError, "fingerprint differs"):
                authorize(report, changed)

    def test_actual_command_must_match_the_declared_command(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            with self.assertRaisesRegex(RuntimeAuthorizationError, "command differs"):
                authorize_runtime(synthetic_report(reader), reader_command=["/different/reader"],
                    reader_identity=reader, source_kind="live_capture", required_fields=RUNTIME_FIELDS)

    def test_changed_code_is_rechecked_even_after_authorization(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            authorization = authorize(synthetic_report(reader), reader)
            Path(reader["command"][1]).write_bytes(b"changed implementation")
            with self.assertRaisesRegex(RuntimeAuthorizationError, "changed since"):
                authorization.assert_runtime(reader_command=reader["command"], reader_identity=reader, source_kind="live_capture")
            with self.assertRaisesRegex(RuntimeAuthorizationError, "changed since"):
                authorize(synthetic_report(reader), reader)

    def test_executable_and_script_file_arguments_need_digests(self):
        with TemporaryDirectory() as directory:
            for missing in (0, 1):
                reader = identity(directory)
                reader["code_files"].pop(missing)
                with self.subTest(missing=missing), self.assertRaisesRegex(RuntimeAuthorizationError, "not covered"):
                    authorize(synthetic_report(reader), reader)

    def test_missing_identity_or_any_component_cannot_be_inferred(self):
        self.assertTrue(validation_provenance_errors(None))
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            for component in ("command", "code_files", "model", "prompt", "preprocessing", "output_contract"):
                changed = copy.deepcopy(reader)
                changed.pop(component)
                with self.subTest(component=component), self.assertRaises(RuntimeAuthorizationError):
                    recognizer_fingerprint(changed)

    def test_frame_and_source_provenance_cannot_be_replay(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            report = synthetic_report(reader)
            for source in ("saved_frame", "recorded_state_replay", "", None):
                with self.subTest(source=source), self.assertRaises(RuntimeAuthorizationError):
                    authorize(report, reader, source_kind=source)
            authorization = authorize(report, reader)
            with self.assertRaisesRegex(RuntimeAuthorizationError, "saved, replayed"):
                authorization.assert_frame_source("saved_frame")
            report["validation_provenance"]["source_kind"] = "synthetic_contract_not_vision_validation"
            with self.assertRaisesRegex(RuntimeAuthorizationError, "live-capture provenance"):
                authorize(report, reader)

    def test_sparse_counts_accuracy_errors_and_boolean_counts_cannot_authorize(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            mutations = [
                lambda r: r["fields"]["hp"].update(runtime_labeled_unique_images=11),
                lambda r: r["fields"]["hp"].update(runtime_labeled_unique_images=True),
                lambda r: r["fields"]["hp"].update(runtime_accuracy=.94),
                lambda r: r["fields"]["hp"].update(runtime_accuracy=float("nan")),
                lambda r: r["fields"]["hp"].update(runtime_accuracy=True),
                lambda r: r.update(unflagged_critical_errors=1),
                lambda r: r.update(unflagged_critical_errors=False),
                lambda r: r.update(distinct_image_hashes=11),
                lambda r: r["fields"].pop("hand_order"),
                lambda r: r["fields"]["hp"].update(runtime_labeled_unique_images=13),
            ]
            for mutate in mutations:
                report = synthetic_report(reader)
                mutate(report)
                with self.subTest(report=report), self.assertRaises(RuntimeAuthorizationError):
                    authorize(report, reader)

    def test_legacy_unbound_calibration_schema_is_not_accepted(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            old = {"field_accuracy": {field: 1 for field in RUNTIME_FIELDS}, "total_frames": 12,
                   "runtime_authorized": True, "critical_error_count": 0,
                   "field_counts": {field: 12 for field in RUNTIME_FIELDS}}
            with self.assertRaisesRegex(RuntimeAuthorizationError, "saved-frame validation report"):
                authorize(old, reader)
            with self.assertRaisesRegex(RuntimeAuthorizationError, "every current runtime field"):
                authorize(synthetic_report(reader), reader, required_fields={"hp"})

    def test_direct_live_loop_cannot_bypass_binding_with_perfect_unbound_scores(self):
        with self.assertRaisesRegex(RuntimeStop, "reader-bound authorization"):
            ExecutionLoop(object(), object(), object(), mode="live", arm=ARM_PHRASE,
                run_id="synthetic-test", calibration=CalibrationReport({field: 1 for field in RUNTIME_FIELDS}, 12))

    def test_evaluator_retains_declared_provenance_without_inventing_authorization(self):
        with TemporaryDirectory() as directory:
            reader = identity(directory)
            declared = provenance(reader)
            labels = ValidationManifest((), "synthetic test", True, declared)
            report = evaluate_predictions(labels, [])
            self.assertEqual(report["validation_provenance"], declared)
            self.assertFalse(report["runtime_authorized"])
            self.assertIn("predictions lack a matching runtime reader fingerprint", report["recognizer_binding_errors"])
            path = Path(directory) / "labels.json"
            path.write_text(json.dumps({"frames": [], "validation_provenance": declared}))
            self.assertEqual(load_manifest(path).validation_provenance, declared)

    def test_live_cli_rejects_limits_paths_and_unbound_report_before_adapters(self):
        script = Path(__file__).resolve().parents[1] / "scripts" / "veda_execute.py"
        spec = importlib.util.spec_from_file_location("veda_execute_binding_tests", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            reader = identity(directory)
            identity_path, report_path = directory / "identity.json", directory / "calibration.json"
            identity_path.write_text(json.dumps(reader))
            report_path.write_text(json.dumps({"runtime_authorized": True}))
            base = ["--mode", "live", "--trace", str(directory / "trace.jsonl"), "--journal", str(directory / "journal.jsonl"),
                    "--run-id", "unit-test", "--arm", ARM_PHRASE, "--socket", str(directory / "not-a-socket"),
                    "--reader-command", json.dumps(reader["command"]), "--reader-identity", str(identity_path),
                    "--calibration", str(report_path)]
            variants = [base, base + ["--max-seconds", "nan"], base + ["--max-inputs", "0"],
                        base + ["--report", str(report_path)], base + ["--report", str(directory / "trace.jsonl")]]
            with patch.object(module, "BoundedCaptureSource", side_effect=AssertionError("no capture adapter")), \
                 patch.object(module, "AnalysisWorker", side_effect=AssertionError("no reader adapter")), \
                 patch.object(module.signal, "signal"):
                for args in variants:
                    with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                        module.main(args)
                    self.assertEqual(error.exception.code, 2)
            self.assertFalse((directory / "trace.jsonl").exists())
            self.assertFalse((directory / "journal.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
