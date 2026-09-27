"""Energy coordinator coverage with real adapters and fake native transport."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont

from tests.test_native_ocr import observation, png_bytes
from veda.native_ocr import NativeTextReader
from veda.saved_frame_reader import read_saved_frame


class SavedFrameEnergyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.image = (Path(temp.name)/"energy.png").resolve()
        # HP must have matching source pixels even when OCR transport is mocked.
        source = Image.new("RGB", (1000,600), (65,72,78))
        template = json.loads((Path(__file__).resolve().parents[1]/"data/hp_heart_template.json").read_text())
        heart = Image.new("L", (24,24))
        heart.putdata([255 if c == "1" else 0 for c in "".join(template["mask_rows"])])
        heart = heart.resize((28,21), Image.Resampling.NEAREST)
        source.paste((210,60,65), (245,10,273,31), heart)
        draw = ImageDraw.Draw(source)
        font = ImageFont.load_default(size=20)
        bounds = draw.textbbox((280,13), "17/91", font=font, anchor="lt")
        draw.text((280,13), "17/91", font=font, anchor="lt", fill=(210,90,90))
        source.save(self.image)
        self.sha = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.full = [observation("17/91", [280,13,max(bounds[2]-280,45),20]), observation("3", [70,490,40,20])]
        self.focused = [observation("0/3", [70,490,40,20])]
        self.mutate_region = None
        self.batch_exit = 0
        self.calls = []
        self.cards = {"image_sha256": self.sha, "source_dimensions": [1000,600],
                      "card_candidates": [], "hand_complete": None, "issues": []}

    def transport(self, command, *_):
        self.calls.append(command)
        source = {"image_path": str(self.image), "image_sha256": self.sha,
                  "source_dimensions": [1000,600], "orientation": "up",
                  "timing_ms": {"ocr": 1, "load_and_ocr": 2}}
        if len(command) == 2:
            return 0, json.dumps({**source, "schema": "veda.native-text.raw.v1", "ok": True,
                                 "observations": self.full}).encode()
        regions = []
        for request in json.loads(command[3]):
            region = {"id": request["id"], "box_original_pixels_ltrb": request["box"],
                      "scale": 3, "preprocessing": request["preprocessing"], "ok": True,
                      "error": None, "image_sha256": self.sha, "source_dimensions": [1000,600],
                      "observations": copy.deepcopy(self.focused),
                      "timing_ms": {"ocr": 1, "region_total": 2}}
            if self.mutate_region: self.mutate_region(region)
            regions.append(region)
        return self.batch_exit, json.dumps({**source, "schema": "veda.native-text-regions.raw.v1",
                                             "ok": True, "scale": 3, "regions": regions}).encode()

    def read(self, **kwargs):
        with patch("veda.native_ocr._run_bounded", side_effect=self.transport), \
             patch("veda.card_regions.detect_card_regions", return_value=self.cards):
            return read_saved_frame(self.image, viewport=[0,0,1000,600],
                                    reader=NativeTextReader(Path(sys.executable)), refine_cards=kwargs.pop("refine_cards", False),
                                    read_combat=False, **kwargs)

    def test_default_recovers_literal_zero_even_without_card_candidates(self):
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual((17,91,0,3), tuple(result["hud"][k] for k in ("hp","max_hp","energy","energy_max")))
        self.assertEqual("refined", result["energy_refinement"]["status"])
        self.assertEqual(2, len(self.calls))
        self.assertEqual("energy", json.loads(self.calls[1][3])[0]["id"])
        self.assertTrue(result["partial"])
        self.assertFalse(result["runtime_authorized"])
        self.assertFalse(result["controller_authorized"])

    def test_disabling_refinements_is_a_true_single_native_pass(self):
        result = self.read(refine_cards=False, refine_energy=False)
        self.assertTrue(result["ok"])
        self.assertIsNone(result["hud"]["energy"])
        self.assertNotIn("energy_refinement", result)
        self.assertEqual(1, len(self.calls))

    def test_absent_display_is_not_zero(self):
        self.full = self.full[:1]
        self.focused = []
        result = self.read()
        self.assertTrue(result["ok"])
        self.assertIsNone(result["hud"]["energy"])
        self.assertIsNone(result["hud"]["energy_max"])

    def test_conflicting_energy_retains_hp_but_no_energy_components(self):
        self.full[1] = observation("1/3", [70,490,40,20])
        result = self.read()
        self.assertTrue(result["ok"])
        self.assertEqual(17, result["hud"]["hp"])
        self.assertIsNone(result["hud"]["energy"])
        self.assertIsNone(result["hud"]["energy_max"])
        self.assertEqual("conflict", result["energy_refinement"]["status"])

    def test_source_or_geometry_failure_discards_all_derived_evidence(self):
        for key, value in (("image_sha256", "f"*64),
                           ("box_original_pixels_ltrb", [0,0,100,100]),
                           ("observations", [observation("0/3", [500,490,40,20])])):
            with self.subTest(key=key):
                self.mutate_region = lambda r: r.update({key:value})
                result = self.read()
                self.assertFalse(result["ok"])
                self.assertTrue(all(v is None for v in result["hud"].values()))
                self.assertEqual([], result["cards"]["card_candidates"])
                self.assertNotIn("energy_refinement", result)
                self.assertNotIn("native_evidence", result)

    def test_operational_failure_may_retain_strict_current_image_proof(self):
        self.full[1] = observation("0/3", [70,490,40,20])
        self.batch_exit = 1
        result = self.read()
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(0, result["hud"]["energy"])
        self.assertEqual("reader_failed", result["energy_refinement"]["focused_status"])

    def test_source_replaced_after_energy_analysis_clears_refinement_evidence(self):
        def replace(*_, **__):
            self.image.write_bytes(png_bytes(900,600))
            return self.cards
        with patch("veda.native_ocr._run_bounded", side_effect=self.transport), \
             patch("veda.card_regions.detect_card_regions", side_effect=replace):
            result = read_saved_frame(self.image, viewport=[0,0,1000,600],
                                      reader=NativeTextReader(Path(sys.executable)), refine_cards=False, read_combat=False)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["hud"]["energy"])
        self.assertNotIn("energy_refinement", result)

    def test_energy_refinement_does_not_require_optional_card_pixel_dependency(self):
        with patch("veda.native_ocr._run_bounded", side_effect=self.transport), \
             patch("veda.card_regions.detect_card_regions", side_effect=ModuleNotFoundError("PIL")):
            result = read_saved_frame(self.image, viewport=[0,0,1000,600],
                                      reader=NativeTextReader(Path(sys.executable)), refine_cards=False, read_combat=False)
        self.assertTrue(result["ok"], result["issues"])
        self.assertEqual(0, result["hud"]["energy"])
        self.assertIn("card_analysis_dependency_unavailable", result["issues"])


if __name__ == "__main__": unittest.main()
