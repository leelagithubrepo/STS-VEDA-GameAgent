"""Saved-pixel attack evidence fixtures; no native OCR, model or live input."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

from tests.test_native_ocr import observation
from veda.intent_evidence import extract_intent_evidence, intent_crop_requests, merge_intent_evidence


@unittest.skipIf(Image is None, "Pillow required for original-pixel fixture tests")
class IntentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "saved.png"
        self.image = Image.new("RGB", (1000, 600), (25, 25, 25))
        self.draw = ImageDraw.Draw(self.image)
        self.font = ImageFont.load_default(size=22)
        self.rows = []
        self.viewport = [0, 0, 1000, 600]
        self.template_path = Path(__file__).resolve().parents[1] / "data/intent_templates.json"
        self.templates = json.loads(self.template_path.read_text())

    def sword(self, x=620, y=235, *, size=30, companion=None):
        if companion == "shield":
            self.draw.polygon([(x-10, y-6), (x+size+6, y-6), (x+size, y+size),
                               (x+size//2, y+size+12), (x-4, y+size)], fill=(50, 155, 180))
        elif companion == "debuff":
            self.draw.arc((x+size+3, y, x+size+15, y+12), 0, 310, fill=(160, 220, 55), width=3)
        mask = Image.new("L", (24, 24))
        mask.putdata([255 if c == "1" else 0 for c in "".join(self.templates["templates"][0]["mask_rows"])])
        mask = mask.resize((size, size), Image.Resampling.NEAREST)
        self.image.paste((210, 75, 95), (x, y, x+size, y+size), mask)

    def weapon(self, template_id, *, x=620, y=230, transform=None, clipped=False):
        template = next(t for t in self.templates["templates"] if t["id"] == template_id)
        mask = Image.new("L", (24, 24))
        mask.putdata([255 if c == "1" else 0 for c in "".join(template["mask_rows"])])
        # The compact template grid can disconnect a thin diagonal during
        # sampling. Paint a connected synthetic silhouette, like the original
        # full-resolution component, rather than pretending the grid is a PNG.
        original = mask.copy()
        for py in range(23):
            for px in range(23):
                if (original.getpixel((px, py)) and original.getpixel((px+1, py+1))
                        and not original.getpixel((px+1, py)) and not original.getpixel((px, py+1))):
                    mask.putpixel((px+1, py), 255)
        if transform is not None:
            mask = mask.transpose(transform)
        if clipped:
            ImageDraw.Draw(mask).rectangle((0, 12, 23, 23), fill=0)
        mask = mask.resize((36, 36), Image.Resampling.NEAREST)
        self.image.paste((210, 75, 95), (x, y, x+36, y+36), mask)

    def reset_pixels(self):
        self.image.paste((25, 25, 25), (0, 0, 1000, 600))
        self.rows = []

    def text(self, text="14", x=605, y=265, *, confidence=1):
        self.draw.text((x, y), text, font=self.font, fill=(240, 240, 240))
        left, top, right, bottom = self.draw.textbbox((x, y), text, font=self.font)
        row = observation(text, [left, top, right-left, bottom-top], confidence)
        self.rows.append(row)
        return row

    def attack(self, text="14", *, x=620, y=235, companion=None):
        self.sword(x, y, companion=companion)
        return self.text(text, x-15, y+30)

    def read(self, rows=None, mode="original", viewport=None):
        self.image.save(self.path)
        return extract_intent_evidence(self.path, viewport=viewport or self.viewport,
                                       observations=self.rows if rows is None else rows,
                                       ocr_preprocessing=mode)

    def paired(self, first_rows=None, second_rows=None):
        return merge_intent_evidence(self.read(first_rows), self.read(second_rows, "white_text"))

    def test_single_mode_is_explicitly_unconfirmed_and_cannot_supply_subtotal(self):
        self.attack("1x6")
        result = self.read()
        self.assertEqual([], result["attack_candidates"])
        self.assertEqual(1, len(result["unconfirmed_attack_candidates"]))
        card = result["unconfirmed_attack_candidates"][0]
        self.assertEqual((1, 6, 6), (card["damage_per_hit"], card["hits"], card["attack_total"]))
        self.assertFalse(card["cross_mode_confirmed"])
        self.assertIsNone(result["supported_attack_subtotal"])
        self.assertIsNone(result["incoming_damage"])

    def test_registered_weapon_variants_require_real_literal_cross_mode_evidence(self):
        for template in self.templates["templates"][2:]:
            with self.subTest(template=template["id"]):
                self.reset_pixels()
                self.weapon(template["id"])
                self.text("23x2", 605, 266)
                raw = self.read()
                self.assertEqual([], raw["attack_candidates"])
                self.assertEqual(1, len(raw["unconfirmed_attack_candidates"]))
                result = self.paired()
                candidate = result["attack_candidates"][0]
                self.assertEqual((23, 2, 46), (candidate["damage_per_hit"], candidate["hits"], candidate["attack_total"]))
                self.assertIsNone(result["incoming_damage"])
                self.assertIsNone(result["enemy_count"])

    def test_all_weapon_variants_keep_missing_hits_unknown_and_veto_disagreement(self):
        for template in self.templates["templates"][2:]:
            with self.subTest(template=template["id"]):
                self.reset_pixels()
                self.weapon(template["id"])
                row = self.text("27", 605, 266)
                result = self.paired()
                self.assertEqual(1, len(result["attack_candidates"]))
                self.assertIsNone(result["attack_candidates"][0]["hits"])
                self.assertIsNone(result["supported_attack_subtotal"])
                different = deepcopy(row)
                different["candidates"][0]["text"] = "27x2"
                conflict = self.paired([row], [different])
                self.assertEqual([], conflict["attack_candidates"])
                self.assertEqual([], conflict["unconfirmed_attack_candidates"])

    def test_transformed_or_severely_truncated_registered_weapons_are_not_generic_red_proof(self):
        for template in self.templates["templates"][2:]:
            for transform, clipped in ((Image.Transpose.ROTATE_180, False),
                                       (Image.Transpose.FLIP_LEFT_RIGHT, False), (None, True)):
                with self.subTest(template=template["id"], transform=transform, clipped=clipped):
                    self.reset_pixels()
                    self.weapon(template["id"], transform=transform, clipped=clipped)
                    self.text("27", 605, 266)
                    self.assertEqual([], self.paired()["attack_candidates"])

    def test_blue_shield_or_green_debuff_without_red_weapon_cannot_support_numeral(self):
        for colour in ((50, 155, 180), (130, 220, 40)):
            with self.subTest(colour=colour):
                self.reset_pixels()
                self.draw.polygon(((620, 230), (650, 230), (656, 247), (636, 266), (614, 247)), fill=colour)
                self.text("27", 605, 266)
                self.assertEqual([], self.paired()["attack_candidates"])

    def test_template_count_and_duplicate_identifiers_are_bounded(self):
        from veda.intent_evidence import _templates
        manifest = Path(self.temp.name) / "templates.json"
        for entries in (self.templates["templates"] + [self.templates["templates"][0]],
                        [self.templates["templates"][0], self.templates["templates"][0]]):
            manifest.write_text(json.dumps({**self.templates, "templates": entries}))
            with patch("veda.intent_evidence._TEMPLATE_PATH", manifest):
                with self.assertRaisesRegex(ValueError, "invalid_templates"):
                    _templates()

    def test_corroborated_plain_number_never_infers_one_hit_or_total(self):
        self.attack()
        result = self.paired()
        self.assertEqual(1, len(result["attack_candidates"]))
        card = result["attack_candidates"][0]
        self.assertEqual(14, card["damage_per_hit"])
        self.assertIsNone(card["hits"])
        self.assertIsNone(card["attack_total"])
        self.assertFalse(card["multiplier_visible"])
        self.assertTrue(card["cross_mode_confirmed"])
        self.assertEqual(["original", "white_text"], card["verification_modes"])
        self.assertIsNone(result["incoming_damage"])

    def test_explicit_multiplier_subset_only_and_zero_preserved(self):
        self.attack("0x3", x=500)
        self.attack("2x4", x=750)
        result = self.paired()
        self.assertEqual([0, 8], [c["attack_total"] for c in result["attack_candidates"]])
        self.assertEqual(8, result["supported_attack_subtotal"])
        self.assertEqual(2, result["subtotal_candidate_count"])
        self.assertIsNone(result["incoming_damage"])
        self.assertIsNone(result["enemy_count"])

    def test_white_only_wrong_digit_is_withheld_without_original_confirmation(self):
        row = self.attack("9")
        wrong = deepcopy(row)
        wrong["candidates"][0]["text"] = "6"
        result = self.paired([], [wrong])
        self.assertEqual([], result["attack_candidates"])
        self.assertEqual(6, result["unconfirmed_attack_candidates"][0]["damage_per_hit"])
        self.assertIsNone(result["supported_attack_subtotal"])

    def test_same_mode_repetition_does_not_replace_cross_mode_confirmation(self):
        self.attack()
        original = self.read()
        result = merge_intent_evidence(original, deepcopy(original))
        self.assertEqual([], result["attack_candidates"])
        self.assertEqual(1, len(result["unconfirmed_attack_candidates"]))

    def test_differing_damage_or_explicit_multiplier_vetoes_both_modes(self):
        row = self.attack("1x6")
        for text in ("1", "1x5", "2x6"):
            wrong = deepcopy(row)
            wrong["candidates"][0]["text"] = text
            with self.subTest(text=text):
                result = self.paired([row], [wrong])
                self.assertEqual([], result["attack_candidates"])
                self.assertEqual([], result["unconfirmed_attack_candidates"])
                self.assertEqual(1, result["merge"]["conflict_count"])

    def test_literal_and_confidence_guards_do_not_repair_fragments(self):
        row = self.attack("1x6")
        for text, confidence in (("1x6'", 1), ("12 .", 1), ("C6", 1), ("1x0", 1),
                                 ("1x6", .79), ("1/6", 1), ("x6", 1)):
            test_row = deepcopy(row)
            test_row["candidates"] = [{"text": text, "confidence": confidence}]
            with self.subTest(text=text, confidence=confidence):
                result = self.read([test_row])
                self.assertEqual([], result["unconfirmed_attack_candidates"])
                self.assertEqual([], result["attack_candidates"])

    def test_alternative_missing_multiplier_vetoes_even_low_confidence_alternative(self):
        row = self.attack("1x6")
        row["candidates"].append({"text": "1", "confidence": .001})
        result = self.read()
        self.assertEqual([], result["unconfirmed_attack_candidates"])
        self.assertIn("alternative_attack_readings_disagree", result["rejected_numeric_candidates"][0]["issues"])

    def test_duplicate_rows_cannot_become_two_attacks(self):
        row = self.attack()
        self.rows.append(deepcopy(row))
        result = self.read()
        self.assertEqual([], result["unconfirmed_attack_candidates"])
        self.assertEqual(2, len(result["rejected_numeric_candidates"]))

    def test_overlapping_rejected_numeric_alternatives_veto_a_usable_row(self):
        row = self.attack()
        other = deepcopy(row)
        other["candidates"] = [{"text": "15", "confidence": 1}, {"text": "16", "confidence": .1}]
        result = self.read([row, other])
        self.assertEqual([], result["unconfirmed_attack_candidates"])

    def test_ambiguous_original_vetoes_a_previously_corroborated_reading(self):
        row = self.attack()
        confirmed = self.paired()
        ambiguous = deepcopy(row)
        ambiguous["candidates"].append({"text": "15", "confidence": .01})
        result = merge_intent_evidence(self.read([ambiguous]), confirmed)
        self.assertEqual([], result["attack_candidates"])
        self.assertEqual(1, result["merge"]["conflict_count"])

    def test_shield_and_debuff_companions_do_not_become_nonattack_or_extra_hits(self):
        self.attack("10", x=500, companion="shield")
        self.attack("6", x=750, companion="debuff")
        result = self.paired()
        self.assertEqual([10, 6], [c["damage_per_hit"] for c in result["attack_candidates"]])
        self.assertTrue(all(c["hits"] is None for c in result["attack_candidates"]))
        self.assertTrue(all(c["icon_evidence"]["companion_icons_classified"] is False
                            for c in result["attack_candidates"]))

    def test_number_without_sword_or_with_red_blob_is_not_an_attack(self):
        self.text()
        self.assertEqual([], self.read()["unconfirmed_attack_candidates"])
        self.draw.ellipse((620, 235, 650, 265), fill=(210, 75, 95))
        self.assertEqual([], self.read()["unconfirmed_attack_candidates"])

    def test_red_horizontal_health_bar_does_not_supply_sword_proof(self):
        self.text()
        self.draw.rectangle((610, 246, 666, 253), fill=(210, 75, 95))
        self.assertEqual([], self.read()["unconfirmed_attack_candidates"])

    def test_green_curl_inside_literal_numeric_box_is_not_damage(self):
        row = self.attack("52")
        x, y, w, h = row["box_original_pixels_top_left"]
        self.draw.rectangle((x+w-6, y, x+w-1, y+h-1), fill=(130, 220, 40))
        result = self.read()
        self.assertEqual([], result["unconfirmed_attack_candidates"])
        self.assertIn("numeric_pixels_unconfirmed_or_green_contaminated",
                      result["rejected_numeric_candidates"][0]["issues"])

    def test_card_status_and_desktop_positions_are_excluded_even_with_matching_sword(self):
        self.attack("5", x=220)
        self.attack("5", x=620, y=445)
        self.attack("5", x=970)
        self.assertEqual([], self.read()["unconfirmed_attack_candidates"])

    def test_proposals_are_pixels_only_and_include_full_numeric_extent(self):
        row = self.attack("1x6")
        self.image.save(self.path)
        result = intent_crop_requests(self.path, viewport=self.viewport)
        self.assertEqual(1, result["primary_region_count"])
        request = result["regions"][0]
        self.assertEqual("white_text", request["preprocessing"])
        x, y, w, h = row["box_original_pixels_top_left"]
        a, b, c, d = request["box"]
        self.assertTrue(a <= x and b <= y and x+w <= c and y+h <= d)
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), result["parent_image_sha256"])
        self.assertEqual(0, result["omitted_icon_count"])
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual(request["box"], result["crop_evidence"][0]["box_px"])

    def numeral_crop(self):
        from veda.intent_evidence import _Pixels, _numeral_crop
        return _numeral_crop(_Pixels(self.image, self.viewport), {"box_px": [620, 235, 650, 265]})

    def test_pixel_row_keeps_zero_and_every_visible_multiplier_component(self):
        for text in ("0", "1x6", "12x3"):
            with self.subTest(text=text):
                self.image.paste((25, 25, 25), (0, 0, 1000, 600))
                row = self.text(text, 604, 255)
                result = self.numeral_crop()
                if text == "12x3":
                    # This fixture font splits 3 into two sizable components;
                    # a fallback preserves both instead of guessing a repair.
                    self.assertEqual("original_wide_fallback", result["selection"])
                    self.assertIn("white_row_baselines_disagree", result["issues"])
                else:
                    self.assertEqual("neutral_white_row", result["selection"])
                x, y, w, h = row["box_original_pixels_top_left"]
                a, b, c, d = result["box_px"]
                self.assertTrue(a <= x and b <= y and c >= x+w and d >= y+h)

    def test_tiny_or_split_multiplier_fragments_force_wide_fallback(self):
        self.draw.rectangle((607, 258, 613, 274), fill=(240, 240, 240))
        for fragments in ([(630, 266, 630, 266)],
                          [(629, 263, 631, 265), (632, 268, 634, 270), (640, 266, 643, 268)]):
            with self.subTest(fragments=fragments):
                self.draw.rectangle((620, 254, 670, 278), fill=(25, 25, 25))
                for box in fragments:
                    self.draw.rectangle(box, fill=(240, 240, 240))
                result = self.numeral_crop()
                self.assertEqual("original_wide_fallback", result["selection"])
                self.assertIn("unresolved_white_component_outside_row", result["issues"])
                self.assertEqual([578, 247, 674, 291], result["box_px"])

    def test_split_or_edge_truncated_numerals_do_not_produce_narrow_crop(self):
        for boxes, reason in (([(607, 258, 610, 260), (612, 265, 615, 268)], "no_unique_bounded_white_row"),
                              ([(607, 247, 615, 264)], "white_component_touches_search_boundary")):
            with self.subTest(boxes=boxes):
                self.image.paste((25, 25, 25), (0, 0, 1000, 600))
                for box in boxes:
                    self.draw.rectangle(box, fill=(240, 240, 240))
                result = self.numeral_crop()
                self.assertEqual("original_wide_fallback", result["selection"])
                self.assertIn(reason, result["issues"])

    def test_white_fragments_above_or_below_expected_row_force_wide_fallback(self):
        for fragment in ((620, 248, 623, 250), (620, 283, 623, 286)):
            with self.subTest(fragment=fragment):
                self.image.paste((25, 25, 25), (0, 0, 1000, 600))
                self.draw.rectangle((605, 259, 611, 271), fill=(240, 240, 240))
                self.draw.rectangle(fragment, fill=(240, 240, 240))
                result = self.numeral_crop()
                self.assertEqual("original_wide_fallback", result["selection"])
                self.assertIn("unresolved_white_component_outside_row", result["issues"])
                self.assertEqual([578, 247, 674, 291], result["search_box_px"])

    def test_alternatives_keep_primary_and_exclude_known_unsafe_tight_glyph_crop(self):
        from veda.intent_evidence import _alternative_crops
        self.draw.rectangle((605, 259, 611, 271), fill=(240, 240, 240))
        self.draw.rectangle((620, 283, 623, 286), fill=(240, 240, 240))
        crop = self.numeral_crop()
        primary = {"id": "intent-0", "box": crop["box_px"], "preprocessing": "white_text"}
        before = deepcopy(primary)
        alternatives = _alternative_crops({"box_px": [620, 235, 650, 265]}, primary, crop)
        self.assertEqual(before, primary)
        self.assertEqual(["intent-alt-0-padded", "intent-alt-0-fixed"], [r["id"] for r in alternatives])
        self.assertNotIn([602, 256, 615, 275], [r["box_px"] for r in alternatives])
        self.assertTrue(all(r["primary_region_id"] == "intent-0" for r in alternatives))
        self.assertTrue(all("absent multiplier" in r["issues"][0] for r in alternatives))

    def test_wrong_primary_reading_vetoes_later_agreed_alternative_digit(self):
        row = self.attack("9")
        wrong = deepcopy(row)
        wrong["candidates"][0]["text"] = "6"
        primary = merge_intent_evidence(self.read([]), self.read([wrong], "white_text"))
        result = merge_intent_evidence(primary, self.read([row]))
        result = merge_intent_evidence(result, self.read([row], "white_text"))
        self.assertEqual([], result["attack_candidates"])
        self.assertEqual([], result["unconfirmed_attack_candidates"])
        self.assertTrue(result["rejected_numeric_candidates"])

    def test_partial_alternative_literal_cannot_erase_primary_multiplier(self):
        row = self.attack("1x6")
        shortened = deepcopy(row)
        shortened["candidates"][0]["text"] = "1"
        result = merge_intent_evidence(self.read([row]), self.read([shortened]))
        result = merge_intent_evidence(result, self.read([shortened], "white_text"))
        self.assertEqual([], result["attack_candidates"])
        self.assertIsNone(result["supported_attack_subtotal"])

    def test_extra_significant_white_component_is_not_silently_excluded(self):
        self.draw.rectangle((607, 258, 613, 274), fill=(240, 240, 240))
        self.draw.rectangle((660, 258, 667, 274), fill=(240, 240, 240))
        result = self.numeral_crop()
        self.assertEqual("original_wide_fallback", result["selection"])
        self.assertIn("white_row_gap_is_ambiguous", result["issues"])
        self.assertEqual(2, len(result["white_components"]))

    def test_pixel_row_samples_full_previous_horizontal_extent_for_long_multiplier(self):
        # The final glyph is outside the earlier experimental tight geometry;
        # it still belongs in the selected row instead of becoming a cropped 1.
        for box in ((607, 258, 613, 274), (622, 263, 628, 274), (640, 258, 648, 274),
                    (659, 258, 666, 274)):
            self.draw.rectangle(box, fill=(240, 240, 240))
        result = self.numeral_crop()
        self.assertEqual("neutral_white_row", result["selection"])
        self.assertGreaterEqual(result["box_px"][2], 667)
        self.assertEqual(4, len(result["white_components"]))

    def test_ocr_box_width_does_not_truncate_the_same_allowed_sword(self):
        from veda.intent_evidence import _Pixels, _sword_proof, _templates
        self.sword(640, 235)
        templates, _ = _templates()
        first = _sword_proof(_Pixels(self.image, self.viewport), [619, 265, 631, 281], templates, 600)
        second = _sword_proof(_Pixels(self.image, self.viewport), [619, 265, 632, 281], templates, 600)
        self.assertTrue(first["confirmed"])
        self.assertTrue(second["confirmed"])
        self.assertEqual(first["matches"], second["matches"])
        self.assertLessEqual(first["matches"][0]["box_px"][2], first["sample_box_px"][2])

    def test_larger_search_does_not_accept_an_unrelated_adjacent_sword(self):
        from veda.intent_evidence import _Pixels, _sword_proof, _templates
        self.sword(659, 235, size=26)
        templates, _ = _templates()
        result = _sword_proof(_Pixels(self.image, self.viewport), [619, 265, 631, 281], templates, 600)
        self.assertFalse(result["confirmed"])
        self.assertEqual([], result["matches"])

    def test_oversized_overlay_search_abstains_without_clearing_other_candidates(self):
        self.attack()
        self.rows.append(observation("Gain 8 Block.", [405, 150, 525, 24], 1))
        result = self.read()
        self.assertEqual([14], [entry["damage_per_hit"] for entry in result["unconfirmed_attack_candidates"]])
        rejected = result["rejected_numeric_candidates"][0]
        self.assertEqual("Gain 8 Block.", rejected["proof"]["raw_observation"]["candidates"][0]["text"])
        self.assertIn("sword_search_exceeds_per_candidate_pixel_bound", rejected["issues"])
        self.assertEqual(0, rejected["icon_evidence"]["sampled_pixels"])
        self.assertIsNone(rejected["icon_evidence"]["sample_box_px"])
        merged = merge_intent_evidence(result, self.read(mode="white_text"))
        self.assertEqual([14], [entry["damage_per_hit"] for entry in merged["attack_candidates"]])

    def test_pixel_proposal_still_rejects_source_replacement(self):
        from veda.intent_evidence import _numeral_crop
        self.attack()
        self.image.save(self.path)
        def replace(*args):
            result = _numeral_crop(*args)
            Image.new("RGB", (999, 600)).save(self.path)
            return result
        with patch("veda.intent_evidence._numeral_crop", side_effect=replace):
            with self.assertRaisesRegex(ValueError, "source_changed"):
                intent_crop_requests(self.path, viewport=self.viewport)

    def test_proposal_limit_reports_every_omitted_detected_icon(self):
        for i in range(7):
            self.sword(405+i*70, 180)
        self.image.save(self.path)
        result = intent_crop_requests(self.path, viewport=self.viewport)
        self.assertEqual(6, result["primary_region_count"])
        self.assertEqual(9, len(result["regions"]))
        self.assertEqual(3, result["alternative_region_count"])
        self.assertEqual(3, result["omitted_alternative_count"])
        self.assertEqual(3, sum(not entry["requested"] for entry in result["alternative_evidence"]))
        self.assertEqual(1, result["omitted_icon_count"])
        self.assertEqual(9, len({r["id"] for r in result["regions"]}))

    def test_no_icon_proposals_mean_unknown_not_absence_of_attacks(self):
        self.text()
        self.image.save(self.path)
        result = intent_crop_requests(self.path, viewport=self.viewport)
        self.assertEqual([], result["regions"])
        self.assertTrue(any("fallback" in issue for issue in result["issues"]))

    def test_template_provenance_is_retained(self):
        self.attack()
        result = self.read()
        evidence = result["unconfirmed_attack_candidates"][0]["icon_evidence"]["matches"][0]
        self.assertEqual(self.templates["templates"][0]["source_sha256"], evidence["template_source_sha256"])
        self.assertEqual(hashlib.sha256(self.template_path.read_bytes()).hexdigest(),
                         result["provenance"]["template_manifest_sha256"])

    def test_new_template_masks_reconstruct_from_archived_connected_red_pixels(self):
        from veda.intent_evidence import _components, _red
        root = Path(__file__).resolve().parents[1]
        for template in self.templates["templates"][4:]:
            path = root / template["source_image"]
            if not path.is_file():
                self.skipTest("Private archived template sources are unavailable")
            with self.subTest(template=template["id"]):
                self.assertEqual(template["source_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
                with Image.open(path) as image:
                    crop = image.convert("RGB").crop(template["source_box_px"])
                values = list(crop.get_flattened_data())
                components = _components(bytearray(_red(value) for value in values), *crop.size)
                points = max(components, key=len)
                mask = Image.new("L", crop.size)
                for point in points:
                    mask.putpixel((point % crop.width, point // crop.width), 255)
                sampled = list(mask.resize((24, 24), Image.Resampling.NEAREST).get_flattened_data())
                rows = ["".join("1" if value else "0" for value in sampled[i:i+24])
                        for i in range(0, 24*24, 24)]
                self.assertEqual(template["mask_rows"], rows)

    def test_archived_tight_crop_with_agreed_wrong_digit_is_not_requested(self):
        path = (Path(__file__).resolve().parents[1] /
                "artifacts/observations/ps5_observation_20260926T041619Z.png")
        if not path.is_file():
            self.skipTest("Private archived crop regression source is unavailable")
        # Both probe modes read 9 from this tight crop of a visible 6. Retain
        # wider context, even when a tighter box would improve other examples.
        result = intent_crop_requests(path, viewport=[0, 168, 2140, 1372])
        self.assertNotIn([1141, 731, 1162, 759], [region["box"] for region in result["regions"]])
        self.assertEqual(3, result["primary_region_count"])
        self.assertGreater(result["alternative_region_count"], 0)
        self.assertTrue(all(item["selection"] in {
            "padded_row_with_primary_retained", "fixed_row_with_primary_retained"}
            for item in result["alternative_evidence"]))

    def test_source_viewport_and_template_mismatch_reject_merge(self):
        self.attack()
        first, second = self.read(), self.read(mode="white_text")
        for key, value in (("image_sha256", "f"*64), ("image_path", "/different.png"),
                           ("viewport", [1, 0, 1000, 600]), ("source_dimensions", [1001, 600])):
            bad = deepcopy(second)
            bad[key] = value
            if key == "image_sha256":bad["parent_image_sha256"] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                merge_intent_evidence(first, bad)
        bad = deepcopy(second)
        bad["provenance"]["template_manifest_sha256"] = "f"*64
        with self.assertRaisesRegex(ValueError, "template_mismatch"):
            merge_intent_evidence(first, bad)

    def test_malformed_evidence_or_unsupported_readiness_cannot_be_merged(self):
        self.attack()
        first, second = self.read(), self.read(mode="white_text")
        for mutate in (lambda r:r.update(runtime_ready=True),
                       lambda r:r.update(incoming_damage=14),
                       lambda r:r['unconfirmed_attack_candidates'][0].update(damage_per_hit=15),
                       lambda r:r['unconfirmed_attack_candidates'][0].update(hits=True),
                       lambda r:r.update(ocr_preprocessing='guessed')):
            bad = deepcopy(second);mutate(bad)
            with self.assertRaisesRegex(ValueError, "invalid_evidence"):
                merge_intent_evidence(first, bad)

    def test_invalid_source_boxes_viewports_and_mode_are_rejected(self):
        row = self.attack()
        with self.assertRaises(ValueError):self.read(viewport=[False, 0, 1000, 600])
        with self.assertRaises(ValueError):self.read(mode="green_text")
        bad = deepcopy(row);bad["box_original_pixels_top_left"] = [990, 270, 50, 20]
        with self.assertRaises(ValueError):self.read([bad])
        with self.assertRaises(ValueError):self.read([row]*2001)

    def test_image_replacement_during_parsing_rejects_all_evidence(self):
        self.attack()
        from veda.intent_evidence import _number_pixels
        def replace(*args):
            result = _number_pixels(*args)
            Image.new("RGB", (999, 600)).save(self.path)
            return result
        with patch("veda.intent_evidence._number_pixels", side_effect=replace):
            with self.assertRaisesRegex(ValueError, "source_changed"):
                self.read()

    def test_unknown_raw_pass_can_retain_already_corroborated_evidence(self):
        self.attack()
        confirmed = self.paired()
        result = merge_intent_evidence(self.read([]), confirmed)
        self.assertEqual(14, result["attack_candidates"][0]["damage_per_hit"])
        self.assertEqual([], result["unconfirmed_attack_candidates"])

    def test_claimed_modes_cannot_replace_real_source_leaf_evidence(self):
        self.attack()
        original = self.read()
        forged = self.paired([], [])
        leaf = deepcopy(original["unconfirmed_attack_candidates"][0])
        candidate = deepcopy(leaf)
        candidate.update(verification_modes=["original", "white_text"],
                         cross_mode_confirmed=True,
                         pass_evidence=[{"ocr_pass": "original", "candidate": leaf}])
        forged["attack_candidates"] = [candidate]
        with self.assertRaisesRegex(ValueError, "invalid_evidence"):
            merge_intent_evidence(forged, self.read([], "white_text"))

    def test_raw_source_mode_and_observation_box_bind_every_candidate(self):
        self.attack()
        original, focused = self.read(), self.read(mode="white_text")
        for mutate in (
            lambda c: c["proof"].update(ocr_preprocessing="original"),
            lambda c: c["box"].__setitem__(0, c["box"][0]-1),
        ):
            bad = deepcopy(focused)
            mutate(bad["unconfirmed_attack_candidates"][0])
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, "invalid_evidence"):
                merge_intent_evidence(original, bad)

    def test_merged_history_must_match_flat_source_candidates(self):
        self.attack()
        confirmed = self.paired()
        for mutate in (
            lambda r: r["attack_candidates"][0]["pass_evidence"][0]["candidate"]["issues"].append("forged"),
            lambda r: r["attack_candidates"][0]["pass_evidence"].pop(),
            lambda r: r["attack_candidates"][0]["pass_evidence"][0]["candidate"].update(pass_evidence=[]),
            lambda r: r["merge"]["sources"][0].update(image_sha256="f"*64, parent_image_sha256="f"*64),
        ):
            bad = deepcopy(confirmed)
            mutate(bad)
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, "invalid_evidence"):
                merge_intent_evidence(bad, self.read([]))

    def test_merge_rejects_excessive_and_recursive_data_before_copy(self):
        self.attack()
        original, focused = self.read(), self.read(mode="white_text")
        nested = {}; cursor = nested
        for _ in range(18):
            cursor["next"] = {}; cursor = cursor["next"]
        cyclic = {}; cyclic["self"] = cyclic
        for payload in ("x"*32_001, [None]*2001, nested, cyclic):
            bad = deepcopy(focused)
            bad["extra"] = payload
            with self.subTest(kind=type(payload).__name__), patch(
                    "veda.intent_evidence.deepcopy", side_effect=AssertionError("copy occurred before validation")):
                with self.assertRaisesRegex(ValueError, "size_or_depth_bound"):
                    merge_intent_evidence(original, bad)

    def test_sequential_maximum_wrapper_passes_keep_flat_bounded_history(self):
        self.attack("1x6")
        original, focused = self.read(), self.read(mode="white_text")
        result = original
        for i in range(12):
            result = merge_intent_evidence(result, focused if i % 2 == 0 else original)
        self.assertEqual(13, result["merge"]["source_count"])
        self.assertTrue(all("merge" not in source for source in result["merge"]["sources"]))
        history = result["attack_candidates"][0]["pass_evidence"]
        self.assertEqual(13, len(history))
        self.assertTrue(all("pass_evidence" not in item["candidate"] for item in history))
        self.assertLess(len(json.dumps(result)), 100_000)
        self.assertEqual(6, result["supported_attack_subtotal"])
        for _ in range(7):
            result = merge_intent_evidence(result, original)
        self.assertEqual(20, result["merge"]["source_count"])
        with self.assertRaisesRegex(ValueError, "source_count_bound"):
            merge_intent_evidence(result, original)


if __name__ == "__main__":
    unittest.main()
