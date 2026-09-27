"""Second-pass orchestration tests use only fake native/pixel readers."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from veda.card_refinement import refine_card_regions


def candidate(name=None, cost=None, *, x=300):
    return {"anchor_id": f"anchor-{x}", "raw_text": name or "Basho", "name": name,
            "upgraded": name.endswith("+") if name else None, "current_cost": cost,
            "title_color": "green" if name else None, "title_box_px": [x, 460, x+110, 480],
            "title_search_box_px": [x-10, 450, x+125, 490],
            "header_box_px": [x-70, 430, x+130, 510],
            "field_issues": {"name": [], "upgraded": [], "current_cost": []},
            "provenance": {"cost": {"box_px": [x-50, 445, x-35, 475]} if cost is not None else None}}


class CardRefinementTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("saved-fixture.png").resolve()
        self.cards = {"image_sha256": "a"*64, "source_dimensions": [1000, 600],
                      "card_candidates": [candidate()], "issues": [], "hand_complete": None}
        self.reader = Mock()
        self.response = {"ok": True, "frame_id": "fixture", "image_path": str(self.path),
                         "image_sha256": "a"*64, "source_dimensions": [1000, 600], "regions": []}
        self.sync_regions()
        self.refined = [candidate("Bash+", 2)]

    def sync_regions(self):
        self.response["regions"] = [{"id": f"header-{i}", "ok": True,
                                     "box_original_pixels_ltrb": c["header_box_px"], "observations": []}
                                    for i, c in enumerate(self.cards["card_candidates"])]
        self.reader.observe_regions.return_value = self.response

    def run_reader(self, found=None):
        found = found if found is not None else {**self.cards, "card_candidates": self.refined}
        with patch("veda.card_refinement.detect_card_regions", return_value=found):
            return refine_card_regions(self.path, viewport=[0, 0, 1000, 600], cards=self.cards,
                                       reader=self.reader, frame_id="fixture")

    def test_unknown_title_can_be_resolved_by_new_exact_independently_checked_evidence(self):
        result = self.run_reader()
        card = result["card_candidates"][0]
        self.assertEqual(("Bash+", True, 2), (card["name"], card["upgraded"], card["current_cost"]))
        self.assertEqual("Basho", card["refinement"]["original_candidate"]["raw_text"])
        self.assertIsNone(result["hand_complete"])
        self.assertIsNone(self.cards["card_candidates"][0]["name"])
        self.reader.observe_regions.assert_called_once()

    def test_conflicting_asserted_names_and_costs_are_withheld(self):
        self.cards["card_candidates"][0] = candidate("Strike+", 1)
        result = self.run_reader()
        card = result["card_candidates"][0]
        self.assertIsNone(card["name"])
        self.assertIsNone(card["upgraded"])
        self.assertIsNone(card["current_cost"])

    def test_unknown_focused_title_retains_previously_confirmed_identity_but_not_cost(self):
        self.cards["card_candidates"][0] = candidate("Strike+", 1)
        self.refined = [candidate()]
        card = self.run_reader()["card_candidates"][0]
        self.assertEqual("Strike+", card["name"])
        self.assertIsNone(card["current_cost"])

    def test_neighbour_title_is_not_used_to_repair_anchor(self):
        self.refined = [candidate("Strike+", 1, x=520)]
        result = self.run_reader()
        self.assertIsNone(result["card_candidates"][0]["name"])
        self.assertEqual("unresolved", result["card_candidates"][0]["refinement"]["status"])

    def test_two_matching_titles_are_ambiguous(self):
        self.refined = [candidate("Bash+", 2), candidate("Strike+", 1, x=305)]
        card = self.run_reader()["card_candidates"][0]
        self.assertEqual("ambiguous_anchor", card["refinement"]["status"])
        self.assertIsNone(card["name"])
        self.assertIsNone(card["current_cost"])

    def test_reader_source_mismatch_is_rejected(self):
        for key, value in (("image_sha256", "b"*64), ("image_path", "wrong"),
                           ("frame_id", "wrong"), ("source_dimensions", [900, 600])):
            with self.subTest(key=key):
                old = self.response[key]
                self.response[key] = value
                with self.assertRaisesRegex(ValueError, "source_mismatch"):
                    self.run_reader()
                self.response[key] = old

    def test_region_id_and_bounds_must_match_requested_crop(self):
        for key, value in (("id", "neighbour"), ("box_original_pixels_ltrb", [1, 2, 3, 4])):
            with self.subTest(key=key):
                old = self.response["regions"][0][key]
                self.response["regions"][0][key] = value
                with self.assertRaises(ValueError):
                    self.run_reader()
                self.response["regions"][0][key] = old

    def test_pixel_reader_identity_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "refined_card_source_mismatch"):
            self.run_reader({**self.cards, "image_sha256": "b"*64})

    def test_native_failure_retains_only_original_partial_evidence(self):
        self.response.update(ok=False, error="helper_timeout")
        result = self.run_reader()
        self.assertEqual("reader_failed", result["refinement"]["status"])
        self.assertEqual(self.cards["card_candidates"], result["card_candidates"])

    def test_repeated_titles_remain_distinct_but_shared_cost_pixels_are_withheld(self):
        self.cards["card_candidates"] = [candidate(x=300), candidate(x=420)]
        self.sync_regions()
        self.refined = [candidate("Strike+", 1, x=300), candidate("Strike+", 1, x=420)]
        self.refined[1]["provenance"]["cost"]["box_px"] = self.refined[0]["provenance"]["cost"]["box_px"]
        result = self.run_reader()
        self.assertEqual(["Strike+", "Strike+"], [c["name"] for c in result["card_candidates"]])
        self.assertEqual([None, None], [c["current_cost"] for c in result["card_candidates"]])

    def test_empty_candidates_do_not_start_header_reader(self):
        self.cards["card_candidates"] = []
        result = self.run_reader()
        self.assertEqual("no_candidates", result["refinement"]["status"])
        self.reader.observe_regions.assert_not_called()

    def test_contrast_pass_requires_exact_plus_and_original_colour_confirmation(self):
        self.cards["card_candidates"][0]["title_color"] = "green"
        self.response["regions"].append({"id": "title-0", "ok": True,
            "preprocessing": "green_text", "observations": [],
            "box_original_pixels_ltrb": self.cards["card_candidates"][0]["title_search_box_px"]})
        header = candidate(cost=2)
        title = candidate("Bash+")
        found = [{**self.cards, "card_candidates": [header]}, {**self.cards, "card_candidates": [title]}]
        with patch("veda.card_refinement.detect_card_regions", side_effect=found):
            result = refine_card_regions(self.path, viewport=[0,0,1000,600], cards=self.cards,
                                         reader=self.reader, frame_id="fixture")
        card = result["card_candidates"][0]
        self.assertEqual(("Bash+", True, 2), (card["name"], card["upgraded"], card["current_cost"]))
        self.assertEqual("literal_plus_and_original_pixel_colour", card["refinement"]["title_confirmation"])

    def test_contrast_pass_without_plus_cannot_certify_upgrade(self):
        self.cards["card_candidates"][0]["title_color"] = "green"
        self.response["regions"].append({"id": "title-0", "ok": True,
            "preprocessing": "green_text", "observations": [],
            "box_original_pixels_ltrb": self.cards["card_candidates"][0]["title_search_box_px"]})
        found = [{**self.cards, "card_candidates": [candidate(cost=2)]},
                 {**self.cards, "card_candidates": [candidate("Bash")]}]
        with patch("veda.card_refinement.detect_card_regions", side_effect=found):
            result = refine_card_regions(self.path, viewport=[0,0,1000,600], cards=self.cards,
                                         reader=self.reader, frame_id="fixture")
        self.assertIsNone(result["card_candidates"][0]["name"])
        self.assertIsNone(result["card_candidates"][0]["upgraded"])

    def test_ambiguous_contrast_matches_withhold_a_header_identity(self):
        self.cards["card_candidates"][0]["title_color"] = "green"
        self.response["regions"].append({"id": "title-0", "ok": True,
            "preprocessing": "green_text", "observations": [],
            "box_original_pixels_ltrb": self.cards["card_candidates"][0]["title_search_box_px"]})
        found = [{**self.cards, "card_candidates": [candidate("Bash+", 2)]},
                 {**self.cards, "card_candidates": [candidate("Bash+"), candidate("Strike+", x=305)]}]
        with patch("veda.card_refinement.detect_card_regions", side_effect=found):
            result = refine_card_regions(self.path, viewport=[0,0,1000,600], cards=self.cards,
                                         reader=self.reader, frame_id="fixture")
        self.assertIsNone(result["card_candidates"][0]["name"])
        self.assertIsNone(result["card_candidates"][0]["upgraded"])

    def test_shared_orb_is_unresolved_even_when_one_cost_already_conflicts(self):
        self.cards["card_candidates"] = [candidate("Strike+", 2, x=300), candidate("Strike+", 1, x=420)]
        self.sync_regions()
        self.refined = [candidate("Strike+", 1, x=300), candidate("Strike+", 1, x=420)]
        self.refined[1]["provenance"]["cost"]["box_px"] = self.refined[0]["provenance"]["cost"]["box_px"]
        result = self.run_reader()
        self.assertEqual([None, None], [c["current_cost"] for c in result["card_candidates"]])

    def test_contrast_conflict_cannot_restore_original_confirmed_identity(self):
        # Exercise merge directly: an unreadable focused title can retain an
        # old proof, but an explicitly contradictory pass cannot do so.
        from veda.card_refinement import _merge_candidate
        result = _merge_candidate(candidate("Strike+"), candidate(), {"identity_conflict": True})
        self.assertIsNone(result["name"])
        self.assertIsNone(result["upgraded"])

    def test_multiple_original_numerals_cannot_be_repaired_by_later_single_reading(self):
        from veda.card_refinement import _merge_candidate, _merge_cost
        original = candidate()
        original["provenance"]["cost"] = {"ambiguous": True, "box_px": None,
                                          "possible_cost_boxes": [[1,2,3,4], [2,3,4,5]]}
        result = _merge_candidate(original, candidate(cost=1), {})
        self.assertIsNone(result["current_cost"])
        _merge_cost(original, result, {"current_cost": 1, "ambiguous": False, "evidence": {}})
        self.assertIsNone(result["current_cost"])


class ReservedHandDiscoveryTests(unittest.TestCase):
    """Discovery uses reserved capacity and cannot manufacture a complete hand."""

    def setUp(self):
        self.path = Path("saved-fixture.png").resolve()
        self.cards = {"image_sha256": "a"*64, "source_dimensions": [1000, 600],
                      "card_candidates": [], "issues": [], "hand_complete": None}
        self.discovered = []
        self.reader = Mock()
        self.mutate_response = None
        self.reader.observe_regions.side_effect = self.native_response

    def native_response(self, path, *, frame_id, regions):
        response = {"ok": True, "frame_id": frame_id, "image_path": str(path),
                    "image_sha256": "a"*64, "source_dimensions": [1000, 600],
                    "regions": [{"id": request["id"], "ok": True,
                                 "image_sha256": "a"*64, "parent_image_sha256": "a"*64,
                                 "source_dimensions": [1000, 600],
                                 "preprocessing": request.get("preprocessing", "original"),
                                 "box_original_pixels_ltrb": request["box"], "observations": []}
                                for request in regions]}
        if self.mutate_response:
            self.mutate_response(response)
        return response

    def read(self):
        with patch("veda.card_refinement.detect_card_regions",
                   return_value={**self.cards, "card_candidates": []}), \
             patch("veda.card_discovery.detect_card_regions",
                   return_value={**self.cards, "card_candidates": deepcopy(self.discovered)}):
            return refine_card_regions(self.path, viewport=[0, 0, 1000, 600], cards=self.cards,
                                       reader=self.reader, frame_id="fixture", discover_hand_titles=True)

    def test_empty_initial_candidates_still_schedule_exactly_one_discovery_region(self):
        result = self.read()
        self.reader.observe_regions.assert_called_once()
        requests = self.reader.observe_regions.call_args.kwargs["regions"]
        self.assertEqual(["hand-discovery"], [r["id"] for r in requests])
        self.assertEqual("processed", result["refinement"]["status"])
        self.assertEqual(0, result["candidate_count"])
        self.assertIsNone(result["hand_complete"])

    def test_twentieth_header_is_retained_and_omitted_refinements_are_all_reported(self):
        for index in range(20):
            entry = candidate(x=300)
            entry["anchor_id"] = f"original-{index}"
            entry["title_color"] = "green"
            entry["cost_search_box_px"] = [230, 430, 270, 480]
            self.cards["card_candidates"].append(entry)
        added = candidate("Bash+", x=600)
        added["location"] = "hand_band"
        self.discovered = [added]
        result = self.read()
        requests = self.reader.observe_regions.call_args.kwargs["regions"]
        self.assertEqual(20, len(requests))
        self.assertEqual(19, sum(r["id"].startswith("header-") for r in requests))
        self.assertEqual(1, sum(r["id"] == "hand-discovery" for r in requests))
        refinement = result["refinement"]
        self.assertEqual([19], refinement["header_region_budget_omissions"])
        self.assertEqual(list(range(20)), refinement["title_region_budget_omissions"])
        self.assertEqual(list(range(20)), refinement["cost_region_budget_omissions"])
        self.assertEqual(20, result["candidate_count"])
        last = next(c for c in result["card_candidates"] if c["anchor_id"] == "original-19")
        self.assertEqual("budget_omitted", last["refinement"]["status"])
        self.assertEqual(self.cards["card_candidates"][19]["raw_text"], last["raw_text"])
        self.assertNotIn("refinement", self.cards["card_candidates"][19])
        self.assertEqual(1, refinement["hand_discovery_evidence"]["withheld_candidate_count"])
        self.assertEqual("candidate_limit", refinement["hand_discovery_evidence"]["status"])
        self.reader.observe_regions.assert_called_once()

    def test_reserved_slot_reports_contrast_and_cost_omissions_under_saturation(self):
        for index in range(10):
            entry = candidate(x=200+index*20)
            entry["title_color"] = "green"
            entry["cost_search_box_px"] = [180, 430, 220, 480]
            self.cards["card_candidates"].append(entry)
        result = self.read()
        refinement = result["refinement"]
        self.assertEqual(20, refinement["region_count"])
        self.assertEqual(10, refinement["header_region_count"])
        self.assertEqual(9, refinement["title_region_count"])
        self.assertEqual([9], refinement["title_region_budget_omissions"])
        self.assertEqual(list(range(10)), refinement["cost_region_budget_omissions"])
        self.assertEqual("requested", refinement["hand_discovery"])

    def test_zero_seed_batch_source_and_protocol_errors_raise(self):
        mutations = [lambda r: r.update(image_sha256="b"*64),
                     lambda r: r.update(ok=False, error="image_changed_during_ocr"),
                     lambda r: r.update(regions=[]),
                     lambda r: r["regions"][0].update(id="header-0"),
                     lambda r: r["regions"][0].update(parent_image_sha256="b"*64)]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.mutate_response = mutate
                with self.assertRaises(ValueError):
                    self.read()

    def test_empty_discovery_does_not_preserve_an_input_claim_of_hand_completeness(self):
        self.cards["hand_complete"] = True
        self.assertIsNone(self.read()["hand_complete"])


class DedicatedCostRefinementTests(unittest.TestCase):
    """Cost fusion is exercised with fake source-bound region results only."""

    def setUp(self):
        self.path = Path("saved-cost-fixture.png").resolve()
        self.cards = {"image_sha256": "a"*64, "source_dimensions": [1000, 600],
                      "card_candidates": [self.with_cost_box()], "issues": [], "hand_complete": None}
        self.header_results = []
        self.cost_results = {}
        self.native_failures = set()
        self.reader = Mock()
        self.reader.observe_regions.side_effect = self.native_response

    @staticmethod
    def with_cost_box(name=None, cost=None, *, x=300):
        card = candidate(name, cost, x=x)
        card["cost_search_box_px"] = [x-65, 430, x-20, 490]
        return card

    def cost_reading(self, value=None, *, ambiguous=False, box=(250, 445, 265, 475), possible=None):
        return {"schema": "veda.card-cost.v1", "image_sha256": "a"*64,
                "source_dimensions": [1000, 600], "current_cost": value,
                "ambiguous": ambiguous, "issues": ["ambiguous"] if ambiguous else [],
                "evidence": {"box_px": list(box) if value is not None else None,
                             "numeric_alternatives": [str(value)] if value is not None else [],
                             "possible_cost_boxes": (possible if possible is not None else
                                                      [list(box)] if value is not None else [])}}

    def native_response(self, image_path, *, frame_id, regions):
        return {"ok": True, "frame_id": frame_id, "image_path": str(image_path),
                "image_sha256": "a"*64, "source_dimensions": [1000, 600],
                "regions": [{"id": request["id"], "ok": request["id"] not in self.native_failures,
                             "preprocessing": request.get("preprocessing", "original"),
                             "image_sha256": "a"*64, "parent_image_sha256": "a"*64,
                             "source_dimensions": [1000, 600],
                             "box_original_pixels_ltrb": request["box"], "observations": []}
                            for request in regions]}

    def run_reader(self, *, refine_cost_symbols=True, cost_error=None):
        def cost_response(image_path, *, viewport, candidate, observations):
            if cost_error:
                raise cost_error
            index = int(observations["id"].split("-")[-1])
            return deepcopy(self.cost_results.get(index, self.cost_reading()))

        with patch("veda.card_refinement.detect_card_regions",
                   return_value={**self.cards, "card_candidates": deepcopy(self.header_results)}):
            with patch("veda.card_refinement.read_card_cost", side_effect=cost_response) as costs:
                result = refine_card_regions(self.path, viewport=[0, 0, 1000, 600], cards=self.cards,
                                             reader=self.reader, frame_id="fixture",
                                             refine_cost_symbols=refine_cost_symbols)
                self.cost_calls = costs.call_args_list
                return result

    def test_cost_recovers_without_any_recognized_header_title(self):
        self.cost_results[0] = self.cost_reading(7)
        result = self.run_reader()
        card = result["card_candidates"][0]
        self.assertIsNone(card["name"])
        self.assertEqual(card["current_cost"], 7)
        self.assertEqual(card["refinement"]["status"], "unresolved")
        self.assertEqual(card["cost_refinement"]["current_cost"], 7)
        self.assertIsNone(self.cards["card_candidates"][0]["current_cost"])
        self.assertIsNone(result["hand_complete"])

    def test_literal_zero_is_merged_without_truthiness_loss(self):
        self.cost_results[0] = self.cost_reading(0)
        card = self.run_reader()["card_candidates"][0]
        self.assertEqual(card["current_cost"], 0)
        self.assertEqual(card["provenance"]["cost"]["numeric_alternatives"], ["0"])

    def test_independent_cost_runs_when_header_region_failed(self):
        self.native_failures.add("header-0")
        self.cost_results[0] = self.cost_reading(2)
        card = self.run_reader()["card_candidates"][0]
        self.assertEqual(card["current_cost"], 2)
        self.assertEqual(len(self.cost_calls), 1)

    def test_missing_cost_pass_abstains_but_ambiguity_vetoes_header_cost(self):
        self.header_results = [candidate("Bash+", 2)]
        self.cost_results[0] = self.cost_reading()
        self.assertEqual(self.run_reader()["card_candidates"][0]["current_cost"], 2)
        self.cost_results[0] = self.cost_reading(ambiguous=True)
        self.assertIsNone(self.run_reader()["card_candidates"][0]["current_cost"])

    def test_all_original_header_and_cost_assertions_must_agree(self):
        for old, header, focused in ((1, 2, 1), (1, 2, 2), (None, 2, 1), (1, None, 2), (0, 1, 0)):
            with self.subTest(original=old, header=header, focused=focused):
                self.cards["card_candidates"] = [self.with_cost_box("Bash+", old)]
                self.header_results = [candidate("Bash+", header)]
                self.cost_results[0] = self.cost_reading(focused)
                card = self.run_reader()["card_candidates"][0]
                self.assertIsNone(card["current_cost"])

    def test_original_or_header_numeric_alternative_conflict_vetoes_new_cost(self):
        for conflict_source in ("original", "header"):
            with self.subTest(source=conflict_source):
                self.cards["card_candidates"] = [self.with_cost_box()]
                self.header_results = [candidate()]
                conflicted = (self.cards["card_candidates"][0] if conflict_source == "original"
                              else self.header_results[0])
                conflicted["provenance"]["cost"] = {"box_px": [250, 445, 265, 475],
                                                     "numeric_alternatives": ["1", "2"]}
                self.cost_results[0] = self.cost_reading(1)
                self.assertIsNone(self.run_reader()["card_candidates"][0]["current_cost"])

    def test_original_cost_search_geometry_is_used_after_header_moves_geometry(self):
        original_box = deepcopy(self.cards["card_candidates"][0]["cost_search_box_px"])
        header = self.with_cost_box("Bash+", x=305)
        header["cost_search_box_px"] = [1, 2, 20, 25]
        self.header_results = [header]
        self.cost_results[0] = self.cost_reading(2)
        self.run_reader()
        request = next(r for r in self.reader.observe_regions.call_args.kwargs["regions"] if r["id"] == "cost-0")
        self.assertEqual(request["box"], original_box)
        self.assertEqual(self.cost_calls[0].kwargs["candidate"]["cost_search_box_px"], original_box)

    def test_source_mismatch_from_cost_extractor_is_rejected(self):
        for key, value in (("image_sha256", "b"*64), ("source_dimensions", [999, 600])):
            self.cost_results[0] = {**self.cost_reading(1), key: value}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "cost_reader_source_mismatch"):
                self.run_reader()

    def test_cost_region_source_protocol_failure_is_not_partial_success(self):
        with self.assertRaisesRegex(ValueError, "card_cost_source_mismatch"):
            self.run_reader(cost_error=ValueError("card_cost_source_mismatch"))

    def test_unresolved_shared_numeral_vetoes_another_cards_asserted_cost(self):
        self.cards["card_candidates"] = [self.with_cost_box(x=300), self.with_cost_box(x=420)]
        shared_box = [250, 445, 265, 475]
        self.cost_results[0] = self.cost_reading(possible=[shared_box])
        self.cost_results[1] = self.cost_reading(1, box=shared_box)
        result = self.run_reader()
        self.assertEqual([None, None], [c["current_cost"] for c in result["card_candidates"]])
        self.assertTrue(all(any("overlapping cost" in issue for issue in c["field_issues"]["current_cost"])
                            for c in result["card_candidates"]))

    def test_distinct_observed_zeros_remain_distinct(self):
        self.cards["card_candidates"] = [self.with_cost_box(x=300), self.with_cost_box(x=420)]
        self.cost_results[0] = self.cost_reading(0, box=[250, 445, 265, 475])
        self.cost_results[1] = self.cost_reading(0, box=[370, 445, 385, 475])
        self.assertEqual([0, 0], [c["current_cost"] for c in self.run_reader()["card_candidates"]])

    def test_twenty_region_budget_records_omitted_cost_crops(self):
        self.cards["card_candidates"] = [self.with_cost_box(x=200+i*20) for i in range(12)]
        result = self.run_reader()
        requests = self.reader.observe_regions.call_args.kwargs["regions"]
        self.assertEqual(len(requests), 20)
        self.assertEqual(result["refinement"]["cost_region_count"], 8)
        self.assertEqual(result["refinement"]["cost_region_budget_omissions"], [8, 9, 10, 11])
        self.assertEqual(len(self.cost_calls), 8)

    def test_title_contrast_and_cost_crops_share_one_twenty_region_budget(self):
        self.cards["card_candidates"] = [self.with_cost_box(x=200+i*20) for i in range(8)]
        for card in self.cards["card_candidates"]:
            card["title_color"] = "green"
        result = self.run_reader()
        requests = self.reader.observe_regions.call_args.kwargs["regions"]
        self.assertEqual(len(requests), 20)
        self.assertEqual(sum(r["id"].startswith("title-") for r in requests), 8)
        self.assertEqual(result["refinement"]["cost_region_count"], 4)
        self.assertEqual(result["refinement"]["cost_region_budget_omissions"], [4, 5, 6, 7])

    def test_explicit_header_only_comparison_does_not_call_cost_extractor(self):
        result = self.run_reader(refine_cost_symbols=False)
        self.assertEqual(result["refinement"]["region_count"], 1)
        self.assertEqual(result["refinement"]["cost_region_count"], 0)
        self.assertEqual(self.cost_calls, [])


if __name__ == "__main__":
    unittest.main()
