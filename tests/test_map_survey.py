from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

from veda.map_survey import merge_survey, read_json, validate_view_draft, write_artifact, write_bound_view


def node(name, row, lane=0, kind="enemy", complete=True):
    return {"node_id": name, "row": row, "lane": lane, "kind": kind, "confidence": 1,
            "outgoing_complete": complete, "classification_evidence": "Inspected symbol and connecting lines"}


def edge(start, end):
    return {"from_node_id": start, "to_node_id": end, "confidence": 1, "evidence_note": "Continuous line traced in this view"}


def make_view(source, name, nodes, edges, *, top=False, bottom=False, rows=()):
    return {"view_id": name, "source": source,
            "review": {"complete": True, "reviewer": "Test reviewer", "evidence_note": "Synthetic offline fixture; no live authority"},
            "coverage": {"top_visible": top, "bottom_visible": bottom, "complete_rows": list(rows)},
            "nodes": nodes, "edges": edges}


class SurveyFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.image = self.root / "map.png"
        raw = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 10, 10) + b"\x08\x02\0\0\0" + b"\0" * 4
        self.image.write_bytes(raw)
        self.source = {"path": str(self.image), "sha256": hashlib.sha256(raw).hexdigest(),
                       "captured_at": "2026-09-27T10:00:00+00:00"}

    def draft(self, views):
        return {"schema": "veda.map-survey.v1", "run_id": "test-run", "act": 1,
                "current_node_id": "current", "views": views}

    def bottom(self):
        return make_view(self.source, "bottom", [node("current", 0), node("left", 1, 0, complete=False),
                         node("middle", 1, 1, complete=False), node("right", 1, 2, complete=False)],
                         [edge("current", name) for name in ("left", "middle", "right")], bottom=True, rows=[0, 1])


class SurveyTests(SurveyFixture):
    def view_draft(self):
        view = self.bottom()
        del view["source"], view["review"]
        return {"schema": "veda.map-survey-view-draft.v1", "run_id": "test-run", "act": 1,
                "current_node_id": "current", "view": view}

    def captured_image(self):
        captured = datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)
        path = self.root / "ps5_observation_20260927T100000Z.png"
        path.write_bytes(self.image.read_bytes())
        path.with_suffix(".capture.json").write_text(json.dumps({
            "schema": "veda.game-window-capture.v1", "image_path": str(path),
            "image_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "dimensions": [10, 10],
            "capture_requested_at": captured.isoformat(),
            "capture_completed_at": (captured + timedelta(seconds=1)).isoformat()}))
        return path, captured + timedelta(seconds=2)

    def test_source_free_view_validation_needs_no_image_or_derived_review_fields(self):
        draft = self.view_draft()
        self.image.unlink()
        result = validate_view_draft(draft)
        self.assertTrue(result["valid"])
        self.assertFalse(result["image_review_established"])
        self.assertNotIn("source", result)
        self.assertNotIn("review", result)
        draft["view"]["source"] = self.source
        with self.assertRaises(ValueError):
            validate_view_draft(draft)

    def test_bind_view_derives_source_from_original_receipt_and_preserves_context(self):
        image, now = self.captured_image()
        path = self.root / "bound-view.json"
        result = write_bound_view(self.view_draft(), capture=image, reviewer="Fixture reviewer",
                                  evidence_note="Inspected all three starting nodes", reviewed=True, output=path, now=now)
        bound = json.loads(path.read_text())
        self.assertEqual(result["view_file"], str(path))
        self.assertEqual(bound["schema"], "veda.bound-map-view.v1")
        self.assertEqual(bound["view"]["source"]["sha256"], hashlib.sha256(image.read_bytes()).hexdigest())
        self.assertEqual(bound["view"]["review"]["reviewer"], "Fixture reviewer")
        self.assertFalse(bound["controller_authorized"])
        self.assertEqual(len(merge_survey(self.draft([bound["view"]]))["edges"]), 3)

    def test_upper_view_can_bind_without_current_node_but_merged_survey_requires_it(self):
        image, now = self.captured_image()
        draft = self.view_draft()
        draft["view"] = {key: value for key, value in make_view(self.source, "top",
            [node("boss", 16, kind="boss")], [], top=True, rows=[16]).items() if key not in {"source", "review"}}
        path = self.root / "upper.json"
        write_bound_view(draft, capture=image, reviewer="Fixture reviewer", evidence_note="Inspected boss row only",
                         reviewed=True, output=path, now=now)
        bound = json.loads(path.read_text())
        self.assertEqual(bound["current_node_id"], "current")
        with self.assertRaisesRegex(ValueError, "current_node_id"):
            merge_survey(self.draft([bound["view"]]))

    def test_archival_view_preserves_old_source_but_rejects_missing_review_or_bad_receipt(self):
        image, now = self.captured_image()
        write_bound_view(self.view_draft(), capture=image, reviewer="Fixture reviewer", evidence_note="Inspected archival fixture",
                         reviewed=True, output=self.root / 'archive.json', now=now + timedelta(days=1))
        saved = json.loads((self.root / 'archive.json').read_text())
        self.assertFalse(saved['controller_authorized'])
        self.assertEqual('2026-09-27T10:00:00+00:00', saved['view']['source']['captured_at'])
        with self.assertRaises(ValueError):
            write_bound_view(self.view_draft(), capture=image, reviewer="Fixture reviewer", evidence_note="Inspected fixture",
                             reviewed=False, output=self.root / "invalid.json", now=now)
        image.write_bytes(image.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            write_bound_view(self.view_draft(), capture=image, reviewer="Fixture reviewer", evidence_note="Inspected fixture",
                             reviewed=True, output=self.root / "invalid.json", now=now)
        self.assertFalse((self.root / "invalid.json").exists())

    def test_cropped_middle_view_cannot_claim_top_without_boss_evidence(self):
        view = self.bottom()
        view['coverage']['top_visible'] = True
        with self.assertRaisesRegex(ValueError, 'top coverage'):
            merge_survey(self.draft([view]))
        view['coverage']['top_visible'] = False
        self.assertFalse(merge_survey(self.draft([view]))['coverage']['graph_complete'])

    def test_cli_source_free_validation_and_capture_options_are_exclusive(self):
        path = self.root / "view-draft.json"
        path.write_text(json.dumps(self.view_draft()))
        result = subprocess.run([sys.executable, "scripts/veda_map.py", "--bind-view", str(path), "--validate"],
                                capture_output=True, text=True, check=True)
        self.assertTrue(json.loads(result.stdout)["valid"])
        invalid = subprocess.run([sys.executable, "scripts/veda_map.py", "--bind-view", str(path), "--validate", "--reviewed"],
                                 capture_output=True, text=True)
        self.assertEqual(invalid.returncode, 2)

    def test_three_starting_nodes_and_cropped_map_stay_available(self):
        result = merge_survey(self.draft([self.bottom()]))
        self.assertEqual(len(result["edges"]), 3)
        self.assertEqual({item["node_id"] for item in result["nodes"]}, {"current", "left", "middle", "right"})
        self.assertIn("upper_map_not_reviewed", result["unknowns"])
        self.assertFalse(result["coverage"]["graph_complete"])
        self.assertIsNone(result["expected_boss"])
        self.assertFalse(result["controller_authorized"])

    def test_overlapping_dynamic_views_merge_observed_edges_only(self):
        lower = self.bottom()
        upper = make_view(self.source, "upper", [node("left", 1, 0), node("middle", 1, 1), node("right", 1, 2),
                         node("rest", 2, 0, "rest"), node("boss", 3, 0, "boss")],
                         [edge(key, "rest") for key in ("left", "middle", "right")] + [edge("rest", "boss")],
                         top=True, rows=[1, 2, 3])
        upper["expected_boss"] = {"name": "Hexaghost", "confidence": .95, "evidence_note": "Reviewed top-map portrait"}
        result = merge_survey(self.draft([lower, upper]))
        self.assertTrue(result["coverage"]["graph_complete"])
        self.assertEqual(len(result["edges"]), 7)
        self.assertFalse(result["expected_boss"]["encounter_confirmed"])
        self.assertFalse(result["encounter_confirmed"])
        self.assertFalse(result["freshness_established"])

    def test_omitted_third_start_conflicts_with_complete_overlap(self):
        duplicate = deepcopy(self.bottom())
        duplicate["view_id"] = "incomplete-copy"
        duplicate["nodes"] = duplicate["nodes"][:-1]
        duplicate["edges"] = duplicate["edges"][:-1]
        with self.assertRaisesRegex(ValueError, "contradictory complete outgoing|complete row"):
            merge_survey(self.draft([self.bottom(), duplicate]))

    def test_partial_view_cannot_add_an_edge_to_previous_complete_exits(self):
        second = deepcopy(self.bottom())
        second["view_id"] = "second"
        second["coverage"]["complete_rows"] = []
        second["nodes"][0]["outgoing_complete"] = False
        second["nodes"].append(node("fourth", 1, 3, complete=False))
        second["edges"].append(edge("current", "fourth"))
        with self.assertRaisesRegex(ValueError, "omitted from a declared complete row|omitted from declared complete outgoing"):
            merge_survey(self.draft([self.bottom(), second]))

    def test_partial_overlapping_nodes_do_not_erase_existing_information(self):
        second = make_view(self.source, "second", [node("middle", 1, 1, kind="unknown", complete=False)], [], rows=[])
        result = merge_survey(self.draft([self.bottom(), second]))
        middle = next(item for item in result["nodes"] if item["node_id"] == "middle")
        self.assertEqual(middle["kind"], "enemy")
        self.assertEqual(middle["view_ids"], ["bottom", "second"])

    def test_conflicting_identity_position_or_kind_is_rejected(self):
        for field, value in (("node_id", "different"), ("lane", 4), ("kind", "elite")):
            second = make_view(self.source, "second", [node("middle", 1, 1, complete=False)], [])
            second["nodes"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                merge_survey(self.draft([self.bottom(), second]))

    def test_unobserved_cross_view_edges_are_not_inferred(self):
        upper = make_view(self.source, "upper", [node("rest", 2, 0, "rest"), node("boss", 3, 0, "boss")],
                          [edge("rest", "boss")], top=True, rows=[2, 3])
        result = merge_survey(self.draft([self.bottom(), upper]))
        self.assertIn("view_overlap_gaps", result["unknowns"])
        self.assertFalse(result["coverage"]["graph_complete"])
        self.assertEqual(len(result["edges"]), 4)

    def test_boss_requires_top_view_and_consistent_identity(self):
        lower = self.bottom()
        lower["expected_boss"] = {"name": "Hexaghost", "confidence": 1, "evidence_note": "portrait"}
        with self.assertRaisesRegex(ValueError, "top-map"):
            merge_survey(self.draft([lower]))
        lower["coverage"]["top_visible"] = True
        second = deepcopy(lower)
        second["view_id"] = "second"
        second["expected_boss"]["name"] = "Slime Boss"
        with self.assertRaisesRegex(ValueError, "contradictory expected boss"):
            merge_survey(self.draft([lower, second]))
        lower["expected_boss"]["name"] = "The Collector"
        with self.assertRaisesRegex(ValueError, "not a map boss for this act"):
            merge_survey(self.draft([lower]))

    def test_original_image_hash_is_checked_again_before_writing(self):
        survey = merge_survey(self.draft([self.bottom()]))
        self.image.write_bytes(self.image.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "hash"):
            write_artifact(self.root / "output.json", survey, survey)
        self.assertFalse((self.root / "output.json").exists())

    def test_artifact_cannot_overwrite_input_or_existing_output(self):
        survey = merge_survey(self.draft([self.bottom()]))
        path = self.root / "output.json"
        write_artifact(path, {"survey": survey}, survey)
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_artifact(path, {"survey": survey}, survey)
        self.assertEqual(path.read_bytes(), before)

    def test_duplicate_json_keys_rejected_and_cli_has_no_input_authority(self):
        path = self.root / "survey.json"
        path.write_text('{"schema": 1, "schema": 2}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            read_json(path)
        path.write_text(json.dumps(self.draft([self.bottom()])))
        run = subprocess.run([sys.executable, "scripts/veda_map.py", "--survey", str(path)],
                             capture_output=True, text=True, check=True)
        result = json.loads(run.stdout)
        self.assertTrue(result["valid"])
        self.assertFalse(result["controller_authorized"])


if __name__ == "__main__":
    unittest.main()
