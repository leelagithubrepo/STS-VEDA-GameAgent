"""Saved synthetic pixels and mocked native crop replies, never game input."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

from tests.test_native_ocr import observation
from veda.combat_refinement import combat_region_requests, refine_combat_reading
from veda.native_ocr import REGIONS_SCHEMA, SCHEMA, extract_hud


@unittest.skipIf(Image is None, "Pillow required; use bundled Python for pixel tests")
class CombatRefinementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"saved.png"
        image = Image.new("RGB", (1000, 600), (25, 25, 25))
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=16)
        draw.polygon([(159,409),(191,409),(188,434),(175,445),(162,434)], fill=(55,160,205))
        draw.rectangle((200,424,330,430), fill=(55,160,205))
        draw.text((170,418), "8", font=font, fill=(240,240,240))
        self.block = self.row(draw.textbbox((170,418), "8", font=font), "8")
        self.enemies = []
        for x in (540, 780):
            draw.rectangle((x-50,432,x+80,438), fill=(210,40,50))
            draw.text((x,425), "12/50", font=font, fill=(240,240,240))
            self.enemies.append(self.row(draw.textbbox((x,425), "12/50", font=font), "12/50"))
        image.save(self.path)
        self.digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.viewport = [0,0,1000,600]
        self.source = {"image_path": str(self.path.resolve()), "image_sha256": self.digest,
                       "parent_image_sha256": self.digest, "source_dimensions": [1000,600],
                       "frame_id": "saved", "parent_frame_id": "saved"}
        self.native = {**self.source, "schema": SCHEMA, "ok": True, "error": None}
        self.set_original()
        self.focused = {"combat-block": [], "combat-enemy-health": [], "combat-intent-white": []}
        self.region_errors = {}
        self.batch_error = None
        self.mutate = None
        self.reader = Mock()
        self.reader.observe_regions.side_effect = self.respond
        self.proposal = {**self.source,"schema":"veda.intent-crop-requests.v1","viewport":self.viewport,
                         "regions":[],"runtime_authorized":False,"controller_authorized":False}
        proposal_patch = patch("veda.intent_evidence.intent_crop_requests", create=True,
                               side_effect=lambda *args,**kwargs:deepcopy(self.proposal))
        proposal_patch.start()
        self.addCleanup(proposal_patch.stop)

    @staticmethod
    def row(box, text):
        x,y,r,b = box
        return observation(text, [x,y,r-x,b-y])

    def set_original(self, *rows):
        self.native["observations"] = deepcopy(list(rows))
        self.native["hud"] = extract_hud(self.native["observations"], source_dimensions=[1000,600],
                                         regions={"viewport":self.viewport})

    def respond(self, path, *, frame_id, regions):
        result = {**self.source, "schema":REGIONS_SCHEMA, "scale":3, "ok":self.batch_error is None,
                  "error":self.batch_error, "regions":[]}
        for request in regions:
            error = self.batch_error or self.region_errors.get(request["id"])
            region = {**self.source, "id":request["id"], "scale":3, "ok":error is None, "error":error,
                      "box_original_pixels_ltrb":deepcopy(request["box"]),
                      "preprocessing":request["preprocessing"],
                      "observations":[] if error else deepcopy(self.focused.get(request["id"], []))}
            if error == "native_ocr_outside_region":
                region["rejected_observation_count"] = 1
            result["regions"].append(region)
        if self.mutate:
            self.mutate(result)
        return result

    def read(self):
        return refine_combat_reading(self.path, viewport=self.viewport, native=self.native,
                                     reader=self.reader, frame_id="saved")

    def test_one_batch_recovers_block_and_health_without_runtime_authority(self):
        self.focused["combat-block"] = [self.block]
        self.focused["combat-enemy-health"] = self.enemies
        before = deepcopy(self.native)
        result = self.read()
        evidence = result["combat_evidence"]
        self.assertEqual(8,evidence["player_block"])
        self.assertEqual([(12,50)]*2,[(c["hp"],c["max_hp"]) for c in evidence["enemy_hp_candidates"]])
        self.assertEqual(before,self.native)
        self.reader.observe_regions.assert_called_once()
        self.assertEqual(4,len(self.reader.observe_regions.call_args.kwargs["regions"]))
        for key in ("enemy_count","incoming_damage","hand_complete"):
            self.assertIsNone(evidence[key])
        for key in ("runtime_ready","combat_ready","hand_ready","runtime_authorized","controller_authorized"):
            self.assertIs(evidence[key],False)

    def test_agreement_deduplicates_same_pixels_but_not_separate_enemies(self):
        self.set_original(self.block,*self.enemies)
        self.focused["combat-block"] = [self.block]
        self.focused["combat-enemy-health"] = self.enemies
        result = self.read()["combat_evidence"]
        self.assertEqual(1,len(result["player_block_candidates"]))
        self.assertEqual(2,len(result["enemy_hp_candidates"]))
        for candidate in result["enemy_hp_candidates"]+result["player_block_candidates"]:
            self.assertEqual(2,len(candidate["proof"]["corroborating_readings"]))

    def test_missing_focus_retains_valid_original_and_never_defaults_zero(self):
        self.set_original(self.block,self.enemies[0])
        result = self.read()["combat_evidence"]
        self.assertEqual(8,result["player_block"])
        self.assertEqual(1,len(result["enemy_hp_candidates"]))
        self.set_original()
        self.assertIsNone(self.read()["combat_evidence"]["player_block"])

    def test_literal_zero_remains_valid(self):
        row = deepcopy(self.block)
        row["candidates"][0]["text"] = "0"
        self.focused["combat-block"] = [row]
        self.assertEqual(0,self.read()["combat_evidence"]["player_block"])

    def test_conflicting_accepted_health_or_block_withholds_same_pixels(self):
        self.set_original(self.block,self.enemies[0])
        block,enemy = deepcopy(self.block),deepcopy(self.enemies[0])
        block["candidates"][0]["text"] = "3"
        enemy["candidates"][0]["text"] = "13/50"
        self.focused["combat-block"] = [block]
        self.focused["combat-enemy-health"] = [enemy,self.enemies[1]]
        result = self.read()
        self.assertIsNone(result["combat_evidence"]["player_block"])
        self.assertEqual(1,len(result["combat_evidence"]["enemy_hp_candidates"]))
        self.assertEqual(2,len(result["refinement"]["conflicts"]))

    def test_rejected_same_pixel_numbers_veto_in_both_directions(self):
        for original_conflict in (True,False):
            for kind,base,other in (("combat-block",self.block,"3"),("combat-enemy-health",self.enemies[0],"13/50")):
                for mode in ("low_confidence","alternatives","malformed"):
                    with self.subTest(original_conflict=original_conflict,kind=kind,mode=mode):
                        bad = deepcopy(base)
                        if mode == "low_confidence":
                            bad["candidates"] = [{"text":other,"confidence":.2}]
                        elif mode == "alternatives":
                            bad["candidates"].append({"text":other,"confidence":.001})
                        else:
                            bad["candidates"].insert(0,{"text":"?","confidence":1})
                        self.set_original(bad if original_conflict else base)
                        self.focused = {kind:[base if original_conflict else bad]}
                        result = self.read()["combat_evidence"]
                        self.assertIsNone(result["player_block"])
                        self.assertEqual([],result["enemy_hp_candidates"])

    def test_duplicate_inside_a_pass_is_not_repaired_by_other_pass(self):
        self.set_original(self.block,self.block,self.enemies[0],self.enemies[0])
        self.focused["combat-block"] = [self.block]
        self.focused["combat-enemy-health"] = [self.enemies[0]]
        result = self.read()["combat_evidence"]
        self.assertIsNone(result["player_block"])
        self.assertEqual([],result["enemy_hp_candidates"])

    def test_low_confidence_and_fragments_cannot_supply_numbers(self):
        block = deepcopy(self.block)
        block["candidates"] = [{"text":"8","confidence":.79}]
        enemy = deepcopy(self.enemies[0])
        enemy["candidates"] = [{"text":"12 /","confidence":1}]
        self.focused["combat-block"] = [block]
        self.focused["combat-enemy-health"] = [enemy]
        result = self.read()["combat_evidence"]
        self.assertIsNone(result["player_block"])
        self.assertEqual([],result["enemy_hp_candidates"])

    def test_intent_observations_are_separate_and_never_predict_damage(self):
        row = observation("7x3",[650,220,50,20])
        self.focused["combat-intent-white"] = [row]
        result = self.read()
        self.assertEqual([row],result["refinement"]["intent_regions"][1]["observations"])
        self.assertEqual([],result["combat_evidence"]["intent_number_cues"])
        self.assertIsNone(result["combat_evidence"]["incoming_damage"])

    def test_operational_or_explicit_region_errors_preserve_original(self):
        self.set_original(self.block,self.enemies[0])
        for scope,error in (("batch","helper_timeout"),("region","native_ocr_failed"),("region","native_ocr_outside_region")):
            with self.subTest(scope=scope,error=error):
                self.batch_error = error if scope == "batch" else None
                self.region_errors = {"combat-block":error} if scope == "region" else {}
                self.assertEqual(8,self.read()["combat_evidence"]["player_block"])

    def test_original_source_and_hud_are_bound_before_crop_call(self):
        for field,value in (("image_sha256","f"*64),("frame_id","wrong"),("source_dimensions",[999,600])):
            with self.subTest(field=field):
                old = self.native[field]
                self.native[field] = value
                with self.assertRaises(ValueError): self.read()
                self.native[field] = old
        for hud in ({**self.native["hud"],"hp":2},extract_hud([],source_dimensions=[1000,600],regions={"viewport":[1,0,1000,600]})):
            original = self.native["hud"]
            self.native["hud"] = hud
            with self.assertRaisesRegex(ValueError,"original_hud_mismatch"): self.read()
            self.native["hud"] = original
        self.reader.observe_regions.assert_not_called()

    def test_all_focused_source_fields_and_region_geometry_are_bound(self):
        self.set_original(self.block)
        changes = [("image_path","wrong"),("image_sha256","f"*64),("parent_image_sha256","f"*64),
                   ("source_dimensions",[999,600]),("frame_id","wrong"),("parent_frame_id","wrong"),("scale",2)]
        for region in (False,True):
            for field,value in changes:
                with self.subTest(region=region,field=field):
                    def mutate(reply):
                        target = reply["regions"][1] if region else reply
                        target[field] = value
                    self.mutate = mutate
                    with self.assertRaises(ValueError): self.read()
        for field,value in (("id","wrong"),("box_original_pixels_ltrb",[1,2,3,4]),("preprocessing","green_text")):
            self.mutate = lambda reply:reply["regions"][0].update({field:value})
            with self.assertRaises(ValueError): self.read()

    def test_bad_intent_sibling_invalidates_otherwise_valid_block(self):
        self.focused["combat-block"] = [self.block]
        self.focused["combat-intent-white"] = [observation("7",[0,0,10,10])]
        with self.assertRaisesRegex(ValueError,"invalid_region"): self.read()

    def test_missing_parent_bindings_are_rejected_in_original_batch_and_region(self):
        for field in ("parent_image_sha256","parent_frame_id"):
            old = self.native.pop(field)
            with self.assertRaisesRegex(ValueError,"source_mismatch"): self.read()
            self.native[field] = old
            for region in (False,True):
                def mutate(response):
                    target = response["regions"][0] if region else response
                    target.pop(field)
                self.mutate = mutate
                with self.assertRaises(ValueError): self.read()

    def test_malformed_or_unsolicited_batch_is_not_operational_failure(self):
        mutations = [lambda r:r["regions"].append(deepcopy(r["regions"][0])),
                     lambda r:r["regions"].pop(),
                     lambda r:r.update(ok=False,error="image_changed_during_ocr"),
                     lambda r:r["regions"][0].update(ok=False,error="native_ocr_failed",observations=[self.block]),
                     lambda r:r["regions"][0].update(ok=False,error="native_ocr_outside_region",observations=[],rejected_observation_count=True)]
        for mutate in mutations:
            self.mutate = mutate
            with self.assertRaises(ValueError): self.read()

    def test_source_change_during_crop_call_rejects_result(self):
        self.mutate = lambda response:self.path.write_bytes(self.path.read_bytes()+b"changed")
        with self.assertRaisesRegex(ValueError,"image_changed"): self.read()

    def test_fixed_roi_geometry_translates_and_rounds_without_labels(self):
        result = combat_region_requests([100.2,50.5,900.5,551.2])
        self.assertEqual([212,360,293,442],result[0]["box"])
        for invalid in ([0,0,0,600],[0,0,float("nan"),600],[True,0,1000,600]):
            with self.assertRaises(ValueError): combat_region_requests(invalid)

    def test_bounded_source_verified_icon_crops_replace_fallback_in_same_batch(self):
        self.proposal["regions"] = [{"id":f"intent-{i}","box":[410+i*70,200,465+i*70,260],
                                      "preprocessing":"white_text"} for i in range(6)]
        self.focused["intent-2-white"] = [observation("7x3",[552,220,40,20])]
        result = self.read()
        requests = self.reader.observe_regions.call_args.kwargs["regions"]
        self.assertEqual(14,len(requests))
        self.assertEqual(["original","original"]+["original","white_text"]*6,[r["preprocessing"] for r in requests])
        self.assertEqual(12,len(result["refinement"]["intent_regions"]))
        self.assertEqual("intent-2-white",result["refinement"]["intent_regions"][5]["id"])
        self.assertEqual(14,len({r["id"] for r in requests}))
        for i in range(2,14,2):
            self.assertEqual(requests[i]["box"],requests[i+1]["box"])
        self.reader.observe_regions.assert_called_once()

    def test_alternate_rows_retain_primary_evidence_within_twenty_region_batch(self):
        self.proposal["regions"] = [
            {"id":f"intent-{i}","box":[410,200,465,260],"preprocessing":"white_text"}
            for i in range(6)] + [
            {"id":f"intent-alt-{i}","box":[410,210,465,250],"preprocessing":"white_text"}
            for i in range(3)]
        self.focused["intent-0-white"] = [observation("6",[420,220,20,20])]
        self.focused["intent-alt-0-white"] = [observation("9",[420,220,20,20])]
        result = self.read()
        requests = self.reader.observe_regions.call_args.kwargs["regions"]
        self.assertEqual(20,len(requests))
        regions = result["refinement"]["intent_regions"]
        self.assertEqual(18,len(regions))
        retained = {r["id"]:r["observations"] for r in regions}
        # Conflicting original evidence must survive for the intent merger.
        self.assertEqual("6",retained["intent-0-white"][0]["candidates"][0]["text"])
        self.assertEqual("9",retained["intent-alt-0-white"][0]["candidates"][0]["text"])
        self.reader.observe_regions.assert_called_once()

    def test_bad_proposal_source_geometry_or_count_never_reaches_reader(self):
        base = deepcopy(self.proposal)
        mutations = [lambda p:p.update(image_sha256="f"*64),lambda p:p.update(viewport=[0,1,1000,600]),
                     lambda p:p.update(regions=[{"id":f"intent-{i}","box":[410,200,465,260],"preprocessing":"white_text"} for i in range(10)]),
                     lambda p:p.update(regions=[{"id":"intent-0","box":[410,200,1001,260],"preprocessing":"white_text"}]),
                     lambda p:p.update(regions=[{"id":"intent-0","box":[410,200,465,260],"preprocessing":"original"}])]
        for mutate in mutations:
            self.proposal = deepcopy(base)
            mutate(self.proposal)
            with self.assertRaises(ValueError): self.read()
        self.reader.observe_regions.assert_not_called()

    def test_fallback_is_paired_and_modes_cannot_be_swapped_in_reply(self):
        result = self.read()
        regions = result["refinement"]["intent_regions"]
        self.assertEqual(["combat-intent-original","combat-intent-white"],[r["id"] for r in regions])
        self.assertEqual(["original","white_text"],[r["preprocessing"] for r in regions])
        self.assertEqual(regions[0]["box_original_pixels_ltrb"],regions[1]["box_original_pixels_ltrb"])
        self.mutate = lambda response:response["regions"][-1].update(preprocessing="original")
        with self.assertRaisesRegex(ValueError,"region_mismatch"): self.read()


if __name__ == "__main__":
    unittest.main()
