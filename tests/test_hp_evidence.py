"""Pure saved-pixel fixtures and fake full-frame OCR; no native invocation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image, ImageDraw, ImageFont
from veda.hp_evidence import refine_hp_reading, _red, _components
from veda.native_ocr import SCHEMA, extract_hud
from veda.saved_frame_reader import read_saved_frame


def observation(text, box, confidence=1, alternatives=()):
    x,y,w,h=box
    return {'box_original_pixels_top_left':list(box),
            'quadrilateral_original_pixels_top_left':[[x,y],[x+w,y],[x+w,y+h],[x,y+h]],
            'candidates':[{'text':text,'confidence':confidence},
                          *[{'text':other,'confidence':.95} for other in alternatives]]}


class HpEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=(Path(self.temp.name)/'saved.png').resolve();self.view=[0,0,1000,600]
        self.image=Image.new('RGB',(1000,600),(65,72,78));self.rows=[]
        self.template=json.loads((ROOT/'data/hp_heart_template.json').read_text())

    def heart(self, x=135, y=10, width=28, height=21):
        mask=Image.new('L',(24,24));mask.putdata([255 if c=='1' else 0 for c in ''.join(self.template['mask_rows'])])
        mask=mask.resize((width,height),Image.Resampling.NEAREST)
        self.image.paste((210,60,65),(x,y,x+width,y+height),mask)
        return [x,y,x+width,y+height]

    def fraction(self, text='13/41', x=170, y=13, *, colour=(210,90,90), confidence=1, alternatives=(), heart=True):
        if heart:self.heart(x-35,y-3)
        draw=ImageDraw.Draw(self.image);font=ImageFont.load_default(size=20)
        drawn=text if '/' in text else '13/41'
        bounds=draw.textbbox((x,y),drawn,font=font,anchor='lt')
        draw.text((x,y),drawn,font=font,anchor='lt',fill=colour)
        row=observation(text,[x,y,max(bounds[2]-x,45),20],confidence,alternatives)
        self.rows.append(row);return row

    def native(self):
        self.image.save(self.path)
        checksum=hashlib.sha256(self.path.read_bytes()).hexdigest()
        return {'schema':SCHEMA,'ok':True,'frame_id':'saved','parent_frame_id':'saved',
                'image_path':str(self.path),'image_sha256':checksum,'parent_image_sha256':checksum,
                'source_dimensions':[1000,600],'runtime_authorization_eligible':False,
                'observations':deepcopy(self.rows),
                'hud':extract_hud(self.rows,source_dimensions=[1000,600],regions={'viewport':self.view})}

    def read(self, native=None):
        return refine_hp_reading(self.path,viewport=self.view,native=native or self.native(),frame_id='saved')

    def assert_unknown(self, result):
        self.assertIsNone(result['hp']);self.assertIsNone(result['max_hp']);self.assertIsNotNone(result['error'])
        self.assertFalse(result['refinement']['runtime_authorized'])
        self.assertFalse(result['refinement']['controller_authorized'])

    def test_shifted_literal_fraction_recovers_without_ocr_or_original_mutation(self):
        self.fraction();native=self.native();original=deepcopy(native)
        self.assertIsNone(native['hud']['hp'])
        result=self.read(native)
        self.assertEqual((result['hp'],result['max_hp']),(13,41))
        self.assertEqual(result['refinement']['native_invocations'],0)
        self.assertEqual(native,original)
        self.assertTrue(result['refinement']['selected'][0]['glyph_color']['confirmed'])

    def test_nominal_location_also_requires_heart_and_red_glyphs(self):
        self.fraction(x=300)
        self.assertEqual(self.read()['hp'],13)
        self.image=Image.new('RGB',(1000,600),(65,72,78));self.rows=[]
        self.fraction(x=300,colour=(240,240,240))
        native=self.native();self.assertEqual(native['hud']['hp'],13)
        self.assert_unknown(self.read(native))

    def test_white_username_fraction_near_heart_is_not_hp(self):
        self.fraction(colour=(240,240,240))
        result=self.read();self.assert_unknown(result)
        self.assertEqual(result['refinement']['rejected'][0]['issues'],['fraction_glyphs_not_red'])

    def test_red_fraction_without_heart_is_not_hp(self):
        self.fraction(heart=False);self.assert_unknown(self.read())

    def test_zero_hp_is_literal_not_absence(self):
        self.fraction('0/41');result=self.read()
        self.assertEqual((result['hp'],result['max_hp']),(0,41))

    def test_conflicting_alternatives_withhold(self):
        self.fraction(alternatives=['14/41']);result=self.read();self.assert_unknown(result)
        self.assertIn('conflicting_exact_fraction_alternatives',result['refinement']['issues'])

    def test_valid_alternative_cannot_repair_nonliteral_top_or_low_confidence(self):
        for top,confidence in [('13/',1),('13/41',.7)]:
            with self.subTest(top=top):
                self.rows=[];self.image=Image.new('RGB',(1000,600),(65,72,78))
                self.fraction(top,confidence=confidence,alternatives=['13/41'])
                self.assert_unknown(self.read())

    def test_invalid_denominator_and_hp_above_maximum(self):
        for text in ('13/0','42/41'):
            with self.subTest(text=text):
                self.rows=[];self.image=Image.new('RGB',(1000,600),(65,72,78));self.fraction(text)
                self.assert_unknown(self.read())

    def test_same_or_different_multiple_fraction_rows_are_ambiguous(self):
        for text in ('13/41','14/41'):
            self.rows=[];self.image=Image.new('RGB',(1000,600),(65,72,78))
            self.fraction();self.fraction(text,x=320)
            self.assert_unknown(self.read())

    def test_previous_known_hp_conflict_is_not_overwritten(self):
        self.fraction();self.fraction('14/41',x=320,colour=(240,240,240),heart=False)
        native=self.native();self.assertEqual(native['hud']['hp'],14)
        result=self.read(native);self.assert_unknown(result)
        self.assertIn('conflict_with_nominal_region_hp',result['refinement']['issues'])

    def test_occluded_heart_is_unknown(self):
        self.fraction();ImageDraw.Draw(self.image).rectangle([135,10,150,31],fill=(65,72,78))
        self.assert_unknown(self.read())

    def test_circle_rectangle_triangle_and_partial_heart_do_not_certify_hp(self):
        for shape in ('circle','rectangle','triangle'):
            with self.subTest(shape=shape):
                self.rows=[];self.image=Image.new('RGB',(1000,600),(65,72,78))
                self.fraction(heart=False);draw=ImageDraw.Draw(self.image)
                if shape=='circle':draw.ellipse([135,10,162,30],fill=(210,60,65))
                elif shape=='rectangle':draw.rectangle([135,10,162,30],fill=(210,60,65))
                else:draw.polygon([(135,10),(162,10),(149,30)],fill=(210,60,65))
                self.assert_unknown(self.read())

    def test_outside_top_hud_remains_unknown(self):
        self.fraction(y=100);self.assert_unknown(self.read())

    def test_ocr_box_may_include_heart_but_glyph_pixels_stay_separate(self):
        row=self.fraction();right=row['box_original_pixels_top_left'][0]+row['box_original_pixels_top_left'][2]
        self.rows=[observation('13/41',[137,13,right-137,20])]
        result=self.read();self.assertEqual(result['hp'],13)
        self.assertGreater(result['refinement']['selected'][0]['glyph_color']['sample_box_px'][0],163)

    def test_source_and_parent_identity_mismatches_raise_for_atomic_clear(self):
        self.fraction();base=self.native()
        for key,value in [('image_sha256','0'*64),('parent_image_sha256','0'*64),
                          ('source_dimensions',[1001,600]),('image_path','other.png'),
                          ('frame_id','other'),('parent_frame_id','other'),('runtime_authorization_eligible',True)]:
            with self.subTest(key=key):
                changed=deepcopy(base);changed[key]=value
                with self.assertRaises(ValueError):self.read(changed)

    def test_stale_original_hud_and_invalid_geometry_rejected(self):
        self.fraction();native=self.native();native['hud']['hp']=13
        with self.assertRaisesRegex(ValueError,'original_hud'):self.read(native)
        native=self.native();native['observations'][0]['box_original_pixels_top_left'][0]=-100
        with self.assertRaises(ValueError):self.read(native)

    def test_after_read_source_mutation_raises(self):
        self.fraction();native=self.native()
        from veda import hp_evidence
        actual=hp_evidence._png_identity(self.path)
        with patch.object(hp_evidence,'_png_identity',side_effect=[actual,('0'*64,actual[1])]):
            with self.assertRaisesRegex(ValueError,'image_changed'):self.read(native)

    def test_work_bounds_abstain_without_publishing_partial_result(self):
        self.fraction();native=self.native()
        with patch('veda.hp_evidence._MAX_PIXELS',1):
            result=self.read(native)
        self.assert_unknown(result);self.assertIn('hp_pixel_sample_bound',result['refinement']['issues'])
        self.rows=[];self.image=Image.new('RGB',(1000,600),(65,72,78))
        row=self.fraction(colour=(240,240,240));self.rows=[deepcopy(row) for _ in range(65)]
        result=self.read();self.assert_unknown(result)
        self.assertIn('hp_candidate_sample_bound_exceeded',result['refinement']['issues'])

    def test_coordinator_uses_saved_observations_without_additional_native_invocation(self):
        self.fraction();native=self.native();reader=Mock();reader.observe.return_value=native
        result=read_saved_frame(self.path,viewport=self.view,reader=reader,
                                refine_energy=False,refine_cards=False,read_combat=False)
        self.assertTrue(result['ok']);self.assertEqual(result['hud']['hp'],13)
        reader.observe.assert_called_once();reader.observe_regions.assert_not_called()

    def test_hp_recovery_runs_after_energy_copy_without_overwriting_energy(self):
        self.fraction();native=self.native();reader=Mock();reader.observe.return_value=native
        energy_hud=deepcopy(native['hud']);energy_hud['energy']=0;energy_hud['energy_max']=3
        energy={'hud':energy_hud,'refinement':{'timing_ms':1,'regions':[]}}
        with patch('veda.energy_refinement.refine_energy_reading',return_value=energy):
            result=read_saved_frame(self.path,viewport=self.view,reader=reader,
                                    refine_energy=True,refine_cards=False,read_combat=False)
        self.assertTrue(result['ok']);self.assertEqual(result['hud']['hp'],13)
        self.assertEqual(result['hud']['energy'],0);self.assertIsNone(energy_hud['hp'])

    def test_coordinator_source_failure_clears_all_published_fields(self):
        self.fraction();native=self.native();reader=Mock();reader.observe.return_value=native
        with patch('veda.hp_evidence.refine_hp_reading',side_effect=ValueError('hp_source_changed')):
            result=read_saved_frame(self.path,viewport=self.view,reader=reader,
                                    refine_energy=False,refine_cards=False,read_combat=False)
        self.assertFalse(result['ok']);self.assertIsNone(result['hud']['hp'])
        self.assertEqual(result['cards']['card_candidates'],[])
        self.assertNotIn('hp_refinement',result)

    def test_optional_pixel_dependency_withholds_hp_and_reports_unavailable(self):
        self.fraction(x=300);native=self.native();reader=Mock();reader.observe.return_value=native
        with patch('veda.hp_evidence.refine_hp_reading',side_effect=ImportError('Pillow unavailable')):
            result=read_saved_frame(self.path,viewport=self.view,reader=reader,
                                    refine_energy=False,refine_cards=False,read_combat=False)
        self.assertTrue(result['ok']);self.assertIsNone(result['hud']['hp'])
        self.assertIn('hp_analysis_dependency_unavailable',result['issues'])

    def test_missing_template_withholds_hp_preserves_energy_and_reports_cause(self):
        self.fraction(x=300);self.rows.append(observation('0/3',[70,490,40,20]))
        native=self.native();reader=Mock();reader.observe.return_value=native
        with patch('veda.hp_evidence._TEMPLATE_PATH',ROOT/'data/missing-hp-template.json'):
            result=read_saved_frame(self.path,viewport=self.view,reader=reader,
                                    refine_energy=False,refine_cards=False,read_combat=False)
        self.assertTrue(result['ok']);self.assertIsNone(result['hud']['hp'])
        self.assertEqual(result['hud']['energy'],0)
        self.assertEqual(result['hud']['errors']['hp'],'hp_template_unavailable')
        self.assertIn('hp_unknown:hp_template_unavailable',result['issues'])

    def test_nonobject_or_oversized_template_preserves_other_partial_fields(self):
        self.fraction(x=300);self.rows.append(observation('0/3',[70,490,40,20]))
        native=self.native();reader=Mock();reader.observe.return_value=native
        template=Path(self.temp.name)/'malformed-template.json'
        cards={'card_candidates':[{'name':None,'upgraded':None,'current_cost':None}],
               'image_sha256':native['image_sha256'],'source_dimensions':[1000,600],
               'hand_complete':None}
        for payload in ('[]','null','"not an object"',' '*9000):
            with self.subTest(payload=payload[:20]):
                template.write_text(payload)
                with patch('veda.hp_evidence._TEMPLATE_PATH',template), \
                     patch('veda.card_regions.detect_card_regions',return_value=cards):
                    result=read_saved_frame(self.path,viewport=self.view,reader=reader,
                                            refine_energy=False,refine_cards=False,read_combat=False)
                self.assertTrue(result['ok']);self.assertIsNone(result['hud']['hp'])
                self.assertEqual(result['hud']['energy'],0)
                self.assertEqual(result['cards']['card_candidates'],cards['card_candidates'])
                self.assertEqual(result['hud']['errors']['hp'],'hp_template_invalid')
                self.assertIn('hp_unknown:hp_template_invalid',result['issues'])

    def test_missing_or_malformed_template_source_box_is_hp_only_failure(self):
        self.fraction(x=300);self.rows.append(observation('0/3',[70,490,40,20]))
        native=self.native();reader=Mock();reader.observe.return_value=native
        template=Path(self.temp.name)/'bad-source-box-template.json'
        invalid_boxes=('missing',None,[],[1,2,3],[False,1,10,10],[0,0,0,5],
                       [0,0,5,-1],[1.5,2,5,6],[0,0,float('inf'),10],'0,0,5,5')
        for box in invalid_boxes:
            with self.subTest(box=box):
                document=deepcopy(self.template)
                if box=='missing':document.pop('source_box_px')
                else:document['source_box_px']=box
                template.write_text(json.dumps(document))
                with patch('veda.hp_evidence._TEMPLATE_PATH',template):
                    result=read_saved_frame(self.path,viewport=self.view,reader=reader,
                                            refine_energy=False,refine_cards=False,read_combat=False)
                self.assertTrue(result['ok']);self.assertIsNone(result['hud']['hp'])
                self.assertEqual(result['hud']['energy'],0)
                self.assertEqual(result['hud']['errors']['hp'],'hp_template_invalid')

    def test_template_matches_original_source_connected_component(self):
        # This fixture checks the real artifact grounding independently of OCR.
        source=next(base/self.template['source_image'] for base in (ROOT,*ROOT.parents)
                    if (base/self.template['source_image']).is_file())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),self.template['source_sha256'])
        with Image.open(source) as image:
            crop=image.convert('RGB').crop(self.template['inspected_crop_px'])
        values=list(crop.get_flattened_data());mask=bytearray(_red(v) for v in values)
        points=max(_components(mask,crop.width,crop.height),key=len)
        xs=[p%crop.width for p in points];ys=[p//crop.width for p in points]
        a,b,c,d=min(xs),min(ys),max(xs)+1,max(ys)+1
        original=Image.new('L',(c-a,d-b))
        for p in points:original.putpixel((p%crop.width-a,p//crop.width-b),255)
        small=original.resize((24,24),Image.Resampling.NEAREST);bits=list(small.get_flattened_data())
        rows=[''.join('1' if v else '0' for v in bits[y*24:(y+1)*24]) for y in range(24)]
        self.assertEqual(rows,self.template['mask_rows'])
        x,y,_,_=self.template['inspected_crop_px']
        self.assertEqual([a+x,b+y,c+x,d+y],self.template['source_box_px'])
        self.assertEqual(len(points),self.template['source_red_pixels'])


if __name__=='__main__':unittest.main()
