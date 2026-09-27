"""Offline, synthetic pixel/OCR contracts; not recognition calibration."""
from pathlib import Path
import tempfile
import unittest

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

from veda.card_regions import detect_card_regions
from veda.card_catalog import canonical_title


class CardCatalogTests(unittest.TestCase):
    def test_case_only_lookup_preserves_every_other_character(self):
        self.assertEqual(canonical_title("strike"), "Strike")
        self.assertEqual(canonical_title("tRuE GrIt+"), "True Grit+")
        for raw in ("Strikeo", "Strike++", "Strike +", "Strike ", "True  Grit", "Strıke", "Wound+"):
            self.assertIsNone(canonical_title(raw), raw)


@unittest.skipIf(Image is None, "Pillow is required for offline pixel tests; use bundled Python")
class CardRegionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.png"
        self.image = Image.new("RGB", (1000, 800), (25, 25, 25))
        self.draw = ImageDraw.Draw(self.image)
        self.font = ImageFont.load_default(size=24)
        self.observations = []

    def text(self, text, xy, colour=(235, 235, 235), *, confidence=1, candidates=None):
        self.draw.text(xy, text, font=self.font, fill=colour)
        box = self.draw.textbbox(xy, text, font=self.font)
        observation = {"box_original_pixels_top_left": [box[0], box[1], box[2] - box[0], box[3] - box[1]],
                       "candidates": candidates or [{"text": text, "confidence": confidence}]}
        self.observations.append(observation)
        return box

    def card(self, name, x=400, y=650, *, green=False, digit=None, orb=True):
        colour = (135, 225, 35) if green else (235, 235, 235)
        title = self.text(name, (x, y), colour)
        if digit is not None:
            cy = (title[1] + title[3]) / 2
            cx = x - 45
            if orb:
                self.draw.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=(185, 110, 15))
            cost_box = self.draw.textbbox((cx, cy), digit, font=self.font, anchor="mm")
            self.draw.text((cx, cy), digit, font=self.font, anchor="mm", fill=(240, 240, 240))
            self.observations.append({"box_original_pixels_top_left": [cost_box[0], cost_box[1], cost_box[2] - cost_box[0], cost_box[3] - cost_box[1]],
                                      "candidates": [{"text": digit, "confidence": 1}]})
        return title

    def result(self, *, viewport=(0, 0, 1000, 800)):
        self.image.save(self.path)
        return detect_card_regions(self.path, viewport=viewport, observations=self.observations)

    def test_exact_title_observed_nonstandard_cost_and_independent_colour(self):
        self.card("Strike+", green=True, digit="7")
        result = self.result()
        card = result["card_candidates"][0]
        self.assertEqual((card["name"], card["upgraded"], card["current_cost"]), ("Strike+", True, 7))
        self.assertIsNone(result["hand_complete"])
        self.assertFalse(result["runtime_authorized"])
        self.assertFalse(result["controller_authorized"])

    def test_boundary_card_is_recognized_without_effect_support_or_standard_cost(self):
        self.card("True Grit", x=250, digit="7")
        self.card("True Grit+", x=650, green=True)
        cards = self.result()["card_candidates"]
        self.assertEqual([(c["name"], c["upgraded"]) for c in cards], [("True Grit", False), ("True Grit+", True)])
        self.assertEqual(cards[0]["current_cost"], 7)
        self.assertIsNone(cards[1]["current_cost"])
        self.assertIn("data/spire_advisory_rules.json:rules/true-grit", cards[1]["provenance"]["display_title_sources"])

    def test_case_only_matching_keeps_raw_text_and_independent_upgrade_guard(self):
        self.card("strike", x=250)
        self.card("defend", x=650, green=True)
        cards = self.result()["card_candidates"]
        self.assertEqual(cards[0]["name"], "Strike")
        self.assertEqual(cards[0]["raw_text"], "strike")
        self.assertTrue(cards[0]["provenance"]["title_case_normalized"])
        self.assertIsNone(cards[1]["name"])
        self.assertIsNone(cards[1]["upgraded"])
        self.assertIn("disagree", cards[1]["field_issues"]["upgraded"][0])

    def test_grounded_white_popup_title_is_discovered_but_never_a_hand_card(self):
        self.card("Warcry", y=350, digit="0")
        result = self.result()
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(result["card_candidates"][0]["name"], "Warcry")
        self.assertEqual(result["card_candidates"][0]["location"], "popup_or_nonhand")
        self.assertEqual(result["hand_candidate_count"], 0)
        self.assertIs(result["hand_complete"], False)

    def test_case_variants_of_type_and_menu_text_do_not_create_card_candidates(self):
        self.text("SKILL", (250, 650))
        self.text("drink", (450, 250))
        result = self.result()
        self.assertEqual(result["candidate_count"], 0)
        self.assertTrue(result["menu_text_present"])

    def test_body_exhaust_keyword_below_a_preview_is_not_another_hand_card(self):
        self.card("True Grit", y=350)
        self.text("Exhaust", (500, 650))
        result = self.result()
        self.assertEqual(["True Grit"], [c["name"] for c in result["card_candidates"]])
        self.assertEqual(0, result["hand_candidate_count"])
        self.assertEqual("Exhaust", result["rejected_text_regions"][0]["raw_text"])

    def test_white_and_cyan_border_are_not_upgrade_evidence(self):
        box = self.card("Defend")
        self.draw.rectangle((box[0] - 4, box[1] - 4, box[2] + 4, box[3] + 4), outline=(40, 225, 225), width=3)
        # Even deliberately loose OCR bounds containing the frame do not turn
        # cyan into an upgraded title.
        self.observations[0]["box_original_pixels_top_left"] = [box[0] - 4, box[1] - 4, box[2] - box[0] + 8, box[3] - box[1] + 8]
        card = self.result()["card_candidates"][0]
        self.assertEqual(card["title_color"], "white")
        self.assertFalse(card["upgraded"])
        self.assertGreater(card["provenance"]["letter_colour"]["excluded_cyan_pixels"], 0)

    def test_plus_and_letter_colour_disagreement_stays_unknown(self):
        self.card("Strike+", x=250, green=False)
        self.card("Defend", x=650, green=True)
        cards = self.result()["card_candidates"]
        self.assertEqual(len(cards), 2)
        for card in cards:
            self.assertIsNone(card["name"])
            self.assertIsNone(card["upgraded"])
            self.assertIn("disagree", card["field_issues"]["upgraded"][0])

    def test_exact_title_without_independent_colour_does_not_assert_base_variant(self):
        self.text("Defend", (400, 650), colour=(140, 140, 140))
        card = self.result()["card_candidates"][0]
        self.assertEqual(card["raw_text"], "Defend")
        self.assertIsNone(card["title_color"])
        self.assertIsNone(card["name"])
        self.assertIsNone(card["upgraded"])

    def test_missing_numeral_and_nearby_number_without_orb_stay_unknown(self):
        self.card("Defend", x=250)
        self.card("Strike", x=650, digit="1", orb=False)
        cards = self.result()["card_candidates"]
        self.assertEqual(len(cards), 2)
        self.assertTrue(all(card["current_cost"] is None for card in cards))

    def test_repeated_names_at_different_locations_remain_distinct(self):
        self.card("Defend", x=250, digit="0")
        self.card("Defend", x=650, digit="2")
        cards = self.result()["card_candidates"]
        self.assertEqual([card["name"] for card in cards], ["Defend", "Defend"])
        self.assertEqual([card["current_cost"] for card in cards], [0, 2])

    def test_multiple_numeric_observations_are_not_guessed(self):
        self.card("Defend", digit="1")
        self.observations.append(dict(self.observations[1]))
        card = self.result()["card_candidates"][0]
        self.assertIsNone(card["current_cost"])
        self.assertIn("unique", card["field_issues"]["current_cost"][0])
        self.assertTrue(card["provenance"]["cost"]["ambiguous"])
        self.assertEqual(2, len(card["provenance"]["cost"]["possible_cost_boxes"]))

    def test_conflicting_numeric_alternative_cannot_be_overridden_by_top_confidence(self):
        self.card("Defend", digit="1")
        self.observations[1]["candidates"].append({"text": "7", "confidence": .2})
        card = self.result()["card_candidates"][0]
        self.assertIsNone(card["current_cost"])
        self.assertIn("conflicting numeric", card["field_issues"]["current_cost"][0])
        self.assertEqual(card["provenance"]["cost"]["numeric_alternatives"], ["1", "7"])

    def test_header_padding_keeps_title_plus_and_complete_orb_search(self):
        title = self.card("Strike+", green=True, digit="2")
        card = self.result()["card_candidates"][0]
        search = card["title_search_box_px"]
        header = card["header_box_px"]
        cost = card["cost_search_box_px"]
        self.assertLess(search[0], title[0])
        self.assertLess(search[1], title[1])
        self.assertGreater(search[2], title[2])
        self.assertGreater(search[3], title[3])
        self.assertLessEqual(header[0], cost[0])
        self.assertLessEqual(header[1], cost[1])
        self.assertGreaterEqual(header[2], search[2])
        self.assertGreaterEqual(header[3], cost[3])
        self.assertEqual(card["provenance"]["header_geometry"]["cost_region_source"], "unique_compact_gold_component")

    def test_card_effect_sentence_fragments_are_not_hand_candidates(self):
        self.card("Headbutt", y=350)
        self.text("Put a card from vour", (350, 650))
        self.text("discard pile on top of", (350, 680))
        result = self.result()
        self.assertEqual(result["candidate_count"], 1)
        self.assertEqual(len(result["rejected_text_regions"]), 2)
        self.assertIs(result["hand_complete"], False)

    def test_valid_native_quad_drives_font_height_and_anchor_is_stable(self):
        title = self.card("Defend", digit="1")
        x, y, right, bottom = title
        self.observations[0]["quadrilateral_original_pixels_top_left"] = [[x, y], [right, y], [right, bottom], [x, bottom]]
        first = self.result()["card_candidates"][0]
        second = self.result()["card_candidates"][0]
        self.assertEqual(first["anchor_id"], second["anchor_id"])
        self.assertEqual(first["provenance"]["header_geometry"]["axis_source"], "native_text_quad")
        self.assertEqual(first["provenance"]["header_geometry"]["font_height_px"], bottom - y)

    def test_duplicate_overlapping_ocr_does_not_certify_two_cards(self):
        self.card("Defend")
        self.observations.append(dict(self.observations[0]))
        result = self.result()
        self.assertIs(result["hand_complete"], False)
        self.assertTrue(any("overlapping" in issue for issue in result["issues"]))

    def test_unknown_ocr_is_not_repaired_from_an_alternative_or_catalog(self):
        self.card("Basho", green=True)
        self.observations[0]["candidates"].append({"text": "Bash+", "confidence": .99})
        card = self.result()["card_candidates"][0]
        self.assertEqual(card["raw_text"], "Basho")
        self.assertIsNone(card["name"])
        self.assertIsNone(card["upgraded"])

    def test_popup_does_not_become_an_extra_ordered_hand_card(self):
        self.card("Defend+", y=350, green=True, digit="1")
        self.card("Strike", x=250)
        result = self.result()
        self.assertIs(result["hand_complete"], False)
        self.assertTrue(result["popup_or_nonhand_candidate"])
        self.assertEqual(result["hand_candidate_count"], 1)
        self.assertEqual(result["candidate_count"], 2)

    def test_menu_and_missing_cards_cannot_certify_completeness(self):
        self.card("Uppercut")
        self.text("Drink", (450, 250))
        result = self.result()
        self.assertTrue(result["menu_text_present"])
        self.assertIsNone(result["hand_complete"])

    def test_browser_text_outside_viewport_is_ignored(self):
        self.card("Defend", x=750)
        result = self.result(viewport=(0, 0, 600, 800))
        self.assertEqual(result["candidate_count"], 0)
        self.assertIsNone(result["hand_complete"])

    def test_invalid_viewport_is_rejected(self):
        self.image.save(self.path)
        with self.assertRaises(ValueError):
            detect_card_regions(self.path, viewport=[0, 0, 1001, 800], observations=[])


if __name__ == "__main__":
    unittest.main()
