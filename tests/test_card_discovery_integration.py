"""Real saved-pixel analysis with mocked native OCR; never starts an OCR helper."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from tests.test_native_ocr import observation

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

from veda.saved_frame_reader import read_saved_frame
from veda.native_ocr import SCHEMA, REGIONS_SCHEMA, extract_hud


@unittest.skipIf(Image is None, "Pillow is required; use bundled Python for pixel integration tests")
class CardDiscoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "saved.png"
        self.image = Image.new("RGB", (2000, 1200), (25, 25, 25))
        self.draw = ImageDraw.Draw(self.image)
        self.font = ImageFont.load_default(size=24)
        self.viewport = [0, 0, *self.image.size]
        self.energy_row = self.text("0/3", 140, y=990)
        self.initial_rows = []
        self.hand_rows = []
        self.focused_rows = []
        self.region_mutation = None
        self.batch_error = None
        self.batch_mutation = None
        self.reader = Mock()
        self.reader.observe.side_effect = self.native_observe
        self.reader.observe_regions.side_effect = self.native_regions

    def text(self, text, x, *, y=980, green=False, raw=None):
        self.draw.text((x, y), text, font=self.font,
                       fill=(135, 225, 35) if green else (235, 235, 235))
        left, top, right, bottom = self.draw.textbbox((x, y), text, font=self.font)
        return observation(raw or text, [left, top, right-left, bottom-top])

    def add_card(self, name, x, *, y=980, green=False, raw=None, initial=True):
        row = self.text(name, x, y=y, green=green, raw=raw)
        self.hand_rows.append(deepcopy(row))
        if initial:
            self.initial_rows.append(deepcopy(row))
            self.focused_rows.append(deepcopy(row))
        return row

    def digit(self, digit, cx, cy, *, duplicate_in_discovery=False):
        self.draw.ellipse((cx-22, cy-22, cx+22, cy+22), fill=(185, 110, 15))
        self.draw.text((cx, cy), digit, font=self.font, anchor="mm", fill=(240, 240, 240))
        left, top, right, bottom = self.draw.textbbox((cx, cy), digit, font=self.font, anchor="mm")
        row = observation(digit, [left, top, right-left, bottom-top])
        for rows in (self.initial_rows, self.focused_rows, self.hand_rows):
            rows.append(deepcopy(row))
        if duplicate_in_discovery:
            self.hand_rows.append(deepcopy(row))
        return row

    def source(self, frame_id):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        return {"image_path": str(self.path.resolve()), "frame_id": frame_id,
                "parent_frame_id": frame_id, "image_sha256": digest,
                "parent_image_sha256": digest, "source_dimensions": list(self.image.size),
                "runtime_authorization_eligible": False}

    def native_observe(self, path, *, frame_id, regions):
        rows = deepcopy([self.energy_row, *self.initial_rows])
        return {**self.source(frame_id), "schema": SCHEMA, "ok": True, "error": None,
                "observations": rows,
                "hud": extract_hud(rows, source_dimensions=list(self.image.size), regions=regions),
                "timing_ms": {"total": 0}}

    @staticmethod
    def in_box(row, box):
        x, y, width, height = row["box_original_pixels_top_left"]
        return box[0] <= x and box[1] <= y and x+width <= box[2] and y+height <= box[3]

    def native_regions(self, path, *, frame_id, regions):
        source = self.source(frame_id)
        output = []
        for request in regions:
            rows = self.hand_rows if request["id"] == "hand-discovery" else self.focused_rows
            region = {**source, "id": request["id"], "ok": True,
                      "parent_image_sha256": source["image_sha256"],
                      "box_original_pixels_ltrb": list(request["box"]),
                      "preprocessing": request.get("preprocessing", "original"),
                      "observations": deepcopy([r for r in rows if self.in_box(r, request["box"])])}
            if self.region_mutation:
                self.region_mutation(region)
            output.append(region)
        response = {**source, "schema": REGIONS_SCHEMA, "ok": self.batch_error is None, "regions": output,
                    "error": self.batch_error, "timing_ms": {"total": 0}}
        if self.batch_mutation:
            self.batch_mutation(response)
        return response

    def read(self):
        self.image.save(self.path)
        result = read_saved_frame(self.path, viewport=self.viewport, reader=self.reader,
                                  refine_energy=False, read_combat=False)
        if result["ok"]:
            # The fixture has card/energy pixels but no heart-backed HP evidence.
            self.assertIsNone(result["hud"]["hp"])
            self.assertIsNone(result["hud"]["max_hp"])
            self.assertEqual(0, result["hud"]["energy"])
        return result

    def assert_no_readings(self, result):
        self.assertFalse(result["ok"])
        self.assertEqual([], result["cards"]["card_candidates"])
        self.assertTrue(all(value is None for value in result["hud"].values()))
        self.assertNotIn("native_evidence", result)

    def test_discovery_reserves_batch_budget_without_second_native_call(self):
        for count in (9, 10):
            with self.subTest(initial_cards=count):
                self.initial_rows = []
                self.hand_rows = []
                self.focused_rows = []
                self.reader.reset_mock()
                for index in range(count):
                    self.add_card("Strike", 280+index*145)
                result = self.read()
                self.assertTrue(result["ok"], result["issues"])
                self.reader.observe.assert_called_once()
                self.reader.observe_regions.assert_called_once()
                requests = self.reader.observe_regions.call_args.kwargs["regions"]
                self.assertLessEqual(len(requests), 20)
                self.assertEqual(sum(r["id"] == "hand-discovery" for r in requests), 1)
                self.assertEqual(len(requests), 19 if count == 9 else 20)
                self.assertEqual(result["cards"]["refinement"]["cost_region_budget_omissions"],
                                 [] if count == 9 else [9])

    def test_zero_seed_recovers_exact_titles_from_original_pixels_in_one_batch(self):
        self.add_card("Strike", 400, initial=False)
        self.add_card("Bash+", 900, green=True, initial=False)
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        cards = result["cards"]["card_candidates"]
        self.assertEqual(["Strike", "Bash+"], [card["name"] for card in cards])
        self.assertEqual([False, True], [card["upgraded"] for card in cards])
        self.assertEqual([None, None], [card["current_cost"] for card in cards])
        self.assertTrue(all(card["discovery"]["region_id"] == "hand-discovery" for card in cards))
        self.reader.observe_regions.assert_called_once()
        self.assertEqual(["hand-discovery"],
                         [r["id"] for r in self.reader.observe_regions.call_args.kwargs["regions"]])
        self.assertIsNone(result["cards"]["hand_complete"])
        self.assertFalse(result["runtime_authorized"])

    def test_zero_seed_does_not_add_popup_or_ambiguous_duplicate_titles(self):
        self.add_card("Strike", 400, initial=False)
        self.hand_rows.append(deepcopy(self.hand_rows[0]))
        self.add_card("Bash", 900, y=830, initial=False)
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([], result["cards"]["card_candidates"])
        discovery = result["cards"]["refinement"]["hand_discovery_evidence"]
        self.assertEqual(3, discovery["withheld_candidate_count"])
        self.assertEqual(2, sum(c["reason"] == "overlaps_discovery_candidate"
                                for c in discovery["withheld_candidates"]))
        self.assertTrue(result["cards"]["popup_or_nonhand_candidate"])
        self.assertFalse(result["cards"]["hand_complete"])

    def test_zero_seed_discovery_source_mismatch_clears_existing_hud(self):
        self.add_card("Strike", 400, initial=False)
        self.region_mutation = lambda region: region.update(parent_image_sha256="f"*64)
        result = self.read()
        self.assert_no_readings(result)
        self.assertIn("hand_discovery_source_mismatch", result["issues"])

    def test_zero_seed_unavailable_crop_remains_explicitly_partial(self):
        self.add_card("Strike", 400, initial=False)
        self.batch_error = "helper_timeout"
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual([], result["cards"]["card_candidates"])
        self.assertEqual("reader_failed", result["cards"]["refinement"]["status"])
        self.assertEqual("helper_timeout", result["cards"]["refinement"]["error"])
        self.assertIsNone(result["cards"]["hand_complete"])

    def test_independent_new_exact_depiction_retains_repeated_name_and_unknown_cost(self):
        self.add_card("Strike", 400)
        self.add_card("Strike", 900, initial=False)
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        cards = result["cards"]["card_candidates"]
        self.assertEqual(["Strike", "Strike"], [c["name"] for c in cards])
        self.assertEqual(2, len({c["anchor_id"] for c in cards}))
        self.assertTrue(all(c["current_cost"] is None for c in cards))
        self.assertEqual("hand-discovery", cards[1]["discovery"]["region_id"])
        self.assertIsNone(result["cards"]["hand_complete"])
        self.assertFalse(result["runtime_authorized"])
        self.assertFalse(result["controller_authorized"])

    def test_discovery_never_repairs_existing_unknown_anchor_or_adds_raw_unknown(self):
        self.add_card("Bash+", 400, green=True, raw="Basho")
        self.hand_rows[0]["candidates"][0]["text"] = "Bash+"
        self.add_card("Striko", 900, initial=False)
        self.add_card("Defend", 1300, green=True, initial=False)
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        cards = result["cards"]["card_candidates"]
        self.assertEqual(1, len(cards))
        self.assertIsNone(cards[0]["name"])
        self.assertIsNone(cards[0]["upgraded"])
        self.assertEqual([], result["cards"]["refinement"]["hand_discovery_evidence"]["card_candidates"])

    def test_discovery_cannot_append_same_exact_card_twice(self):
        self.add_card("Strike", 400)
        self.hand_rows.append(deepcopy(self.hand_rows[0]))
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(1, result["cards"]["candidate_count"])

    def test_discovery_source_mismatch_clears_hud_and_original_cards(self):
        self.add_card("Strike", 400)
        for key, value in (("image_sha256", "f"*64), ("parent_image_sha256", "f"*64),
                           ("source_dimensions", [1999, 1200])):
            with self.subTest(key=key):
                def corrupt(region):
                    if region["id"] == "hand-discovery":
                        region[key] = value
                self.region_mutation = corrupt
                result = self.read()
                self.assert_no_readings(result)
                self.assertIn("hand_discovery_source_mismatch", result["issues"])

    def test_batch_source_mismatch_clears_all_readings(self):
        self.add_card("Strike", 400)
        self.batch_mutation = lambda response: response.update(image_sha256="f"*64)
        self.assert_no_readings(self.read())

    def test_native_source_integrity_error_is_not_ordinary_refinement_abstention(self):
        self.add_card("Strike", 400)
        # The real native wrapper preserves the parent digest in these failure
        # envelopes. Matching metadata does not turn the failure into success.
        for error in ("helper_region_image_mismatch", "image_changed_during_ocr"):
            with self.subTest(error=error):
                self.batch_error = error
                self.assert_no_readings(self.read())

    def test_shared_cost_pixels_between_existing_and_discovered_cards_are_withheld(self):
        # Short adjacent titles have separate spatial anchors but overlapping
        # automatic cost searches. The sole numeral cannot establish two costs.
        first = self.add_card("Bash", 600)
        self.add_card("Anger", 658, initial=False)
        y = first["box_original_pixels_top_left"][1]
        height = first["box_original_pixels_top_left"][3]
        self.digit("1", 579, y+height/2)
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        cards = result["cards"]["card_candidates"]
        self.assertEqual(["Bash", "Anger"], [c["name"] for c in cards])
        self.assertEqual([None, None], [c["current_cost"] for c in cards])
        self.assertTrue(all(any("overlapping cost" in issue for issue in c["field_issues"]["current_cost"])
                            for c in cards))

    def test_ambiguous_discovery_numeral_still_vetoes_existing_shared_cost(self):
        first = self.add_card("Bash", 600)
        self.add_card("Anger", 658, initial=False)
        y = first["box_original_pixels_top_left"][1]
        height = first["box_original_pixels_top_left"][3]
        self.digit("1", 579, y+height/2, duplicate_in_discovery=True)
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        cards = result["cards"]["card_candidates"]
        self.assertEqual(["Bash", "Anger"], [c["name"] for c in cards])
        self.assertEqual([None, None], [c["current_cost"] for c in cards])


if __name__ == "__main__":
    unittest.main()
