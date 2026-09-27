"""Synthetic saved pixels and literal OCR fixtures; no recognition calls."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

from veda.combat_evidence import extract_combat_evidence
from tests.test_native_ocr import observation


@unittest.skipIf(Image is None, "Pillow required; use bundled Python for pixel tests")
class CombatEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "combat.png"
        self.image = Image.new("RGB", (1000, 600), (25, 25, 25))
        self.draw = ImageDraw.Draw(self.image)
        self.font = ImageFont.load_default(size=16)
        self.rows = []

    def text(self, text, x, y, *, confidence=1):
        self.draw.text((x, y), text, font=self.font, fill=(240, 240, 240))
        left, top, right, bottom = self.draw.textbbox((x, y), text, font=self.font)
        row = observation(text, [left, top, right-left, bottom-top], confidence)
        self.rows.append(row)
        return row

    def enemy(self, text="12/50", *, x=600, y=425, bar=True, confidence=1):
        if bar:
            self.draw.rectangle((x-50, y+7, x+80, y+13), fill=(210, 40, 50))
        return self.text(text, x, y, confidence=confidence)

    def block(self, text="8", *, x=170, y=418, shield=True, bar=True):
        if shield:
            self.draw.polygon([(x-11, y-9), (x+21, y-9), (x+18, y+16),
                               (x+5, y+27), (x-8, y+16)], fill=(55, 160, 205))
        if bar:
            self.draw.rectangle((x+30, y+6, x+160, y+12), fill=(55, 160, 205))
        return self.text(text, x, y)

    def read(self, viewport=(0, 0, 1000, 600)):
        self.image.save(self.path)
        return extract_combat_evidence(self.path, viewport=viewport, observations=self.rows)

    def test_right_fraction_requires_red_bar_and_preserves_exact_numbers(self):
        self.enemy()
        result = self.read()
        self.assertEqual([(12, 50)], [(c["hp"], c["max_hp"]) for c in result["enemy_hp_candidates"]])
        self.assertTrue(result["enemy_hp_candidates"][0]["proof"]["health_bar"]["confirmed"])
        self.assertIsNone(result["enemy_count"])
        self.assertIsNone(result["incoming_damage"])
        self.assertIsNone(result["hand_complete"])
        for field in ("hand_ready", "combat_ready", "runtime_ready", "runtime_authorized", "controller_authorized"):
            self.assertIs(result[field], False)

    def test_fraction_without_pixel_context_stays_unknown(self):
        self.enemy(bar=False)
        result = self.read()
        self.assertEqual([], result["enemy_hp_candidates"])
        self.assertIn("no_independent_horizontal_red_health_bar", result["rejected_numeric_candidates"][0]["issues"])

    def test_red_portrait_blob_is_not_a_thin_health_bar(self):
        self.draw.rectangle((540, 395, 700, 465), fill=(210, 40, 50))
        self.enemy(bar=False)
        self.assertEqual([], self.read()["enemy_hp_candidates"])

    def test_hud_player_and_card_fractions_are_not_enemy_hp(self):
        self.enemy("60/80", x=280, y=15)
        self.enemy("60/80", x=250, y=425)
        self.enemy("12/50", x=600, y=525)
        self.assertEqual([], self.read()["enemy_hp_candidates"])

    def test_repeated_values_at_separate_red_bars_remain_distinct_candidates(self):
        self.enemy(x=480)
        self.enemy(x=780)
        result = self.read()
        self.assertEqual(2, len(result["enemy_hp_candidates"]))
        self.assertLess(result["enemy_hp_candidates"][0]["box"][0], result["enemy_hp_candidates"][1]["box"][0])

    def test_duplicate_health_observations_do_not_certify_two_enemies(self):
        row = self.enemy()
        self.rows.append(deepcopy(row))
        self.assertEqual([], self.read()["enemy_hp_candidates"])

    def test_invalid_overlapping_health_reading_also_vetoes_valid_one(self):
        row = self.enemy()
        conflict = deepcopy(row)
        conflict["candidates"] = [{"text": "1Z/50", "confidence": 1}]
        self.rows.append(conflict)
        self.assertEqual([], self.read()["enemy_hp_candidates"])

    def test_rejected_low_confidence_or_alternative_conflict_vetoes_same_health_pixels(self):
        row = self.enemy("17/30")
        for candidates in ([{"text": "18/30", "confidence": .2}],
                           [{"text": "18/30", "confidence": 1}, {"text": "19/30", "confidence": .01}]):
            with self.subTest(candidates=candidates):
                conflict = deepcopy(row)
                conflict["candidates"] = candidates
                self.rows = [row, conflict]
                self.assertEqual([], self.read()["enemy_hp_candidates"])

    def test_alternative_disagreement_or_nonliteral_alternative_vetoes_health(self):
        row = self.enemy()
        for text in ("13/50", "12/5O"):
            with self.subTest(text=text):
                row["candidates"] = [{"text": "12/50", "confidence": 1}, {"text": text, "confidence": .001}]
                self.assertEqual([], self.read()["enemy_hp_candidates"])

    def test_low_confidence_zero_denominator_and_excess_hp_are_unknown(self):
        row = self.enemy()
        for text, confidence in (("12/50", .79), ("12/0", 1), ("51/50", 1), ("HP 12/50", 1)):
            with self.subTest(text=text, confidence=confidence):
                row["candidates"] = [{"text": text, "confidence": confidence}]
                self.assertEqual([], self.read()["enemy_hp_candidates"])

    def test_literal_zero_hp_is_allowed_only_when_bar_pixels_exist(self):
        row = self.enemy()
        row["candidates"] = [{"text": "0/50", "confidence": 1}]
        self.assertEqual(0, self.read()["enemy_hp_candidates"][0]["hp"])

    def test_shield_numeral_and_adjacent_health_row_confirm_player_block(self):
        self.block()
        result = self.read()
        self.assertEqual(8, result["player_block"])
        proof = result["player_block_candidates"][0]["proof"]
        self.assertTrue(proof["shield"]["confirmed"])
        self.assertTrue(proof["adjacent_health_row"]["confirmed"])

    def test_absent_shield_never_becomes_zero(self):
        self.block(shield=False)
        self.assertIsNone(self.read()["player_block"])
        self.rows = []
        self.assertIsNone(self.read()["player_block"])

    def test_blue_icon_without_adjacent_health_row_is_not_block(self):
        self.block(bar=False)
        result = self.read()
        self.assertIsNone(result["player_block"])
        self.assertIn("no_adjacent_horizontal_player_health_row", result["rejected_numeric_candidates"][0]["issues"])

    def test_literal_zero_block_is_preserved_when_all_context_is_visible(self):
        row = self.block()
        row["candidates"] = [{"text": "0", "confidence": 1}]
        self.assertEqual(0, self.read()["player_block"])

    def test_duplicate_or_conflicting_block_evidence_remains_unknown(self):
        row = self.block()
        self.rows.append(deepcopy(row))
        self.assertIsNone(self.read()["player_block"])
        self.rows.pop()
        row["candidates"].append({"text": "3", "confidence": .001})
        self.assertIsNone(self.read()["player_block"])

    def test_rejected_alternative_conflict_vetoes_another_read_of_same_shield(self):
        row = self.block("3")
        conflict = deepcopy(row)
        conflict["candidates"] = [{"text": "3", "confidence": 1}, {"text": "8", "confidence": .001}]
        self.rows.append(conflict)
        result = self.read()
        self.assertIsNone(result["player_block"])
        self.assertTrue(any(c["kind"] == "player_block" for c in result["rejected_numeric_candidates"]))

    def test_misread_player_hp_integer_on_thin_blue_bar_is_not_shield_block(self):
        self.draw.rectangle((180, 425, 350, 430), fill=(55, 160, 205))
        self.text("58780", 230, 418)
        self.assertIsNone(self.read()["player_block"])

    def test_aligned_health_bar_segments_can_corroborate_shield(self):
        self.block("8", bar=False)
        self.draw.rectangle((210, 424, 234, 428), fill=(55, 160, 205))
        self.draw.rectangle((260, 424, 284, 428), fill=(55, 160, 205))
        result = self.read()
        self.assertEqual(8, result["player_block"])
        bars = result["player_block_candidates"][0]["proof"]["adjacent_health_row"]["bar_components"]
        self.assertTrue(any("text_split_segments" in bar for bar in bars))

    def test_split_bar_pieces_require_alignment_bounded_gap_and_shield(self):
        for second_x, second_y, shield in ((300, 424, True), (260, 431, True), (260, 424, False)):
            with self.subTest(second_x=second_x, second_y=second_y, shield=shield):
                self.image.paste((25, 25, 25), (0, 0, 1000, 600))
                self.rows = []
                self.block("8", bar=False, shield=shield)
                self.draw.rectangle((210, 424, 234, 428), fill=(55, 160, 205))
                self.draw.rectangle((second_x, second_y, second_x+24, second_y+4), fill=(55, 160, 205))
                self.assertIsNone(self.read()["player_block"])

    def test_two_digit_shield_keeps_short_visible_health_row_before_occlusion(self):
        for text in ("10", "18"):
            with self.subTest(text=text):
                self.image.paste((25, 25, 25), (0, 0, 1000, 600))
                self.rows = []
                self.block(text, bar=False)
                # This row meets the existing3×text-height requirement. A
                # right-edge-of-text margin would unnecessarily clip it below
                # that width for a two-digit numeral.
                self.draw.rectangle((192, 424, 224, 428), fill=(55, 160, 205))
                # A tall popup edge farther right cannot supply a health row.
                self.draw.rectangle((260, 400, 276, 455), fill=(55, 160, 205))
                result = self.read()
                self.assertEqual(int(text), result["player_block"])
                proof = result["player_block_candidates"][0]["proof"]
                self.assertEqual(190, proof["adjacent_health_row"]["sample_box_px"][0])
                self.assertEqual(179, proof["shield"]["center_px"][0])
                self.assertEqual(11, proof["shield"]["radius_px"])
                self.assertEqual([192, 424, 225, 429],
                                 proof["adjacent_health_row"]["bar_components"][0]["box_px"])

    def test_shield_geometry_does_not_relax_bar_width_or_accept_popup_edges(self):
        self.block("18", bar=False)
        self.draw.rectangle((192, 424, 212, 428), fill=(55, 160, 205))
        self.draw.rectangle((260, 400, 276, 455), fill=(55, 160, 205))
        result = self.read()
        self.assertIsNone(result["player_block"])
        rejected = result["rejected_numeric_candidates"][0]
        self.assertTrue(rejected["proof"]["shield"]["confirmed"])
        self.assertIn("no_adjacent_horizontal_player_health_row", rejected["issues"])

    def test_short_visible_row_cannot_replace_independent_shield_proof(self):
        self.block("18", bar=False, shield=False)
        self.draw.rectangle((192, 424, 224, 428), fill=(55, 160, 205))
        self.assertIsNone(self.read()["player_block"])

    def test_visible_intent_numerals_do_not_become_damage_predictions(self):
        self.text("13", 600, 250)
        self.text("7x3", 790, 250)
        result = self.read()
        self.assertEqual([], result["intent_number_cues"])
        self.assertIsNone(result["incoming_damage"])

    def test_exact_float_viewport_is_retained_and_desktop_text_excluded(self):
        self.enemy(x=800)
        viewport = [0.25, 0.5, 700.75, 599.5]
        result = self.read(viewport)
        self.assertEqual(viewport, result["viewport"])
        self.assertEqual([], result["enemy_hp_candidates"])

    def test_malformed_and_image_outside_observations_are_rejected(self):
        for rows in ([{}], [observation("12/50", [990, 400, 40, 20])]):
            with self.subTest(rows=rows):
                self.rows = rows
                with self.assertRaisesRegex(ValueError, "invalid_observation"):
                    self.read()

    def test_invalid_viewport_and_observation_count_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_viewport"):
            self.read([0, 0, 1001, 600])
        self.rows = [observation("1", [10, 10, 10, 10])]*2001
        with self.assertRaisesRegex(ValueError, "observation_bound"):
            self.read()

    def test_source_changed_after_pixel_analysis_rejects_result(self):
        self.image.save(self.path)
        raw = self.path.read_bytes()
        with patch("veda.combat_evidence._read", side_effect=[raw, raw+b"changed"]):
            with self.assertRaisesRegex(ValueError, "source_changed"):
                extract_combat_evidence(self.path, viewport=[0, 0, 1000, 600], observations=[])


if __name__ == "__main__":
    unittest.main()
