"""Additional discovery consumes archived ROI evidence only; no native calls."""
from copy import deepcopy
from pathlib import Path
import unittest
from unittest.mock import patch

from veda.card_discovery import discover_titles, hand_discovery_request


def title(name="Strike", x=200, location="hand_band"):
    return {"name": name, "upgraded": False if name else None, "location": location,
            "title_box_px": [x, 460, x+90, 480]}


class CardDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.viewport = [0, 0, 1000, 600]
        self.cards = {"image_sha256": "a"*64, "source_dimensions": [1000, 600], "card_candidates": []}
        request = hand_discovery_request(self.viewport)
        self.region = {**self.cards, "id": request["id"], "box_original_pixels_ltrb": request["box"],
                       "parent_image_sha256": "a"*64, "preprocessing": "original",
                       "ok": True, "observations": []}
        self.found = {**self.cards, "card_candidates": [title()]}

    def run_discovery(self, existing=()):
        with patch("veda.card_discovery.detect_card_regions", return_value=self.found):
            return discover_titles(Path("saved.png"), viewport=self.viewport, cards=self.cards,
                                   existing=existing, region=self.region)

    def test_only_exact_color_confirmed_titles_are_added(self):
        self.found["card_candidates"] += [title(None, 350), title("Bash", 500, "popup_or_nonhand")]
        before = deepcopy(self.found)
        result = self.run_discovery()
        self.assertEqual(["Strike"], [c["name"] for c in result["card_candidates"]])
        self.assertIsNone(result["hand_complete"])
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual(before, self.found)

    def test_same_pixels_do_not_add_a_second_depiction_or_repair_an_unknown(self):
        for old in (title(), title(None)):
            self.assertEqual([], self.run_discovery([old])["card_candidates"])

    def test_original_anchor_still_blocks_duplicate_after_refinement_moves_title(self):
        self.cards["card_candidates"] = [title(None)]
        self.assertEqual([], self.run_discovery([title(x=800)])["card_candidates"])

    def test_repeated_names_at_distinct_locations_remain_separate_depictions(self):
        self.found["card_candidates"].append(title(x=500))
        self.assertEqual(2, len(self.run_discovery()["card_candidates"]))

    def test_overlapping_new_titles_are_both_withheld_even_if_one_is_unknown(self):
        self.found["card_candidates"].append(title(None, 205))
        self.assertEqual([], self.run_discovery()["card_candidates"])

    def test_unknown_upgrade_is_not_published(self):
        self.found["card_candidates"][0]["upgraded"] = None
        self.assertEqual([], self.run_discovery()["card_candidates"])

    def test_source_and_region_mismatch_raise(self):
        for key, value in (("image_sha256", "b"*64), ("parent_image_sha256", "b"*64),
                           ("source_dimensions", [500,600]), ("id", "wrong"),
                           ("preprocessing", "green_text"), ("box_original_pixels_ltrb", [1,2,3,4])):
            with self.subTest(key=key):
                old, self.region[key] = self.region[key], value
                with self.assertRaises(ValueError): self.run_discovery()
                self.region[key] = old

    def test_pixel_source_mismatch_raises(self):
        self.found["image_sha256"] = "b"*64
        with self.assertRaisesRegex(ValueError, "pixel_source_mismatch"): self.run_discovery()

    def test_failed_region_keeps_no_new_assertions(self):
        self.region.update(ok=False, error="unavailable")
        result = self.run_discovery()
        self.assertEqual("region_failed", result["status"])
        self.assertEqual([], result["card_candidates"])

    def test_total_depictions_never_exceed_twenty(self):
        self.found["card_candidates"].append(title("Bash", 700))
        result = self.run_discovery([title(x=500)] * 20)
        self.assertEqual("candidate_limit", result["status"])
        self.assertEqual([], result["card_candidates"])
        self.assertEqual(2, result["withheld_candidate_count"])
        self.assertEqual(["candidate_limit"] * 2, [c["reason"] for c in result["withheld_candidates"]])

    def test_duplicate_and_unconfirmed_candidates_have_explicit_withholding_reasons(self):
        self.found["card_candidates"] = [title(), title("Bash", 205), title(None, 500),
                                         title("Defend", 700, "popup_or_nonhand")]
        result = self.run_discovery()
        self.assertEqual([], result["card_candidates"])
        self.assertEqual(4, result["withheld_candidate_count"])
        self.assertEqual(["overlaps_discovery_candidate", "overlaps_discovery_candidate",
                          "exact_title_and_original_colour_unconfirmed", "popup_or_nonhand_location"],
                         [c["reason"] for c in result["withheld_candidates"]])

    def test_detector_limit_preserves_unknown_omission_count_and_original_issues(self):
        self.found.update(issues=["candidate bound exceeded; extra candidates were not evaluated"],
                          hand_complete=False, popup_or_nonhand_candidate=True,
                          rejected_text_regions=[{"raw_text": "Deal 6 damage", "reason": "body text"}])
        result = self.run_discovery()
        self.assertIsNone(result["unexamined_candidate_count"])
        self.assertEqual(self.found["issues"], result["detector_limits_reached"])
        self.assertEqual(self.found["rejected_text_regions"], result["rejected_text_regions"])
        self.assertFalse(result["hand_complete"])
        self.assertTrue(result["popup_or_nonhand_candidate"])


if __name__ == "__main__":
    unittest.main()
