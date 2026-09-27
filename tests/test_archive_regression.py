"""Audit bookkeeping/identity tests use tiny synthetic PNGs, not gameplay truth."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from veda.archive_regression import _document_bytes,_prediction_check,load_inputs,run_sweep,sha,summarize_records


class ArchiveRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.images=[]
        for i,color in enumerate(('red','blue')):
            path=self.root/f'{i}.png';Image.new('RGB',(40,20),color).save(path)
            self.images.append(dict(image=path.name,sha256=sha(path),dimensions=[40,20],viewport=[0,0,40,20],
                                    viewport_review='synthetic full image',group_id=f'g{i}',cohort='exposure_unknown'))
        self.doc={'schema':'veda.archive-regression-input.v1','images':deepcopy(self.images),'excluded':[]}
        self.path=self.root/'input.json';self.save()
        self.output=self.root/'results';self.fp={'reader':'fixed'}
        self.reader=Mock(side_effect=self.predict)

    def tearDown(self):self.tmp.cleanup()

    def save(self):self.path.write_text(json.dumps(self.doc))

    def predict(self,image,*,viewport,reader,expected_sha256):
        return dict(schema='veda.partial-saved-frame.v1',ok=True,partial=True,
                    runtime_authorized=False,controller_authorized=False,runtime_authorization_eligible=False,
                    image_path=str(image),image_sha256=expected_sha256,source_dimensions=[40,20],viewport=viewport,
                    hud={'hp':None},cards={'hand_complete':None,'card_candidates':[]},issues=[],timing_ms={'total':2})

    def run_audit(self,**kwargs):
        return run_sweep(self.path,output_dir=self.output,reader=object(),fingerprint=lambda:deepcopy(self.fp),
                         read_frame=self.reader,**kwargs)

    def test_all_paths_accounted_but_exact_duplicates_read_only_once(self):
        clone=self.root/'same.png';clone.write_bytes((self.root/'0.png').read_bytes())
        self.doc['images'].append({**deepcopy(self.images[0]),'image':'same.png'})
        self.doc['excluded']=[{'image':'not-png.png','reason':'JPEG signature, unsupported source format'}];self.save()
        result=self.run_audit()
        self.assertEqual(4,result['total_image_paths']);self.assertEqual(3,result['eligible_paths'])
        self.assertEqual(1,result['duplicate_paths']);self.assertEqual(2,self.reader.call_count)
        self.assertTrue(result['processing_complete']);self.assertIsNone(result['accuracy'])
        self.assertFalse(result['recognition_complete']);self.assertFalse(result['controller_authorized'])

    def test_resume_keeps_finished_reads_and_explicit_pending_work(self):
        first=self.run_audit(max_new=1)
        self.assertFalse(first['processing_complete']);self.assertEqual(1,first['unique_records'])
        final=self.run_audit(resume=True)
        self.assertTrue(final['processing_complete']);self.assertEqual(2,self.reader.call_count)
        again=self.run_audit(resume=True)
        self.assertEqual(2,self.reader.call_count);self.assertEqual(final['outcomes'],again['outcomes'])

    def test_labels_and_cross_group_split_leakage_rejected_before_reader(self):
        base=deepcopy(self.doc)
        variants=[]
        d=deepcopy(base);d['images'][0]['expected']={'energy':3};variants.append(d)
        d=deepcopy(base);d['images'][1]['group_id']='g0';d['images'][1]['cohort']='locked_evaluation';variants.append(d)
        d=deepcopy(base);d['images'].append({**d['images'][0],'image':'alias.png','cohort':'locked_evaluation'});variants.append(d)
        d=deepcopy(base);d['images'][0]['viewport']=[0,0,41,20];variants.append(d)
        for doc in variants:
            self.doc=doc;self.save()
            with self.assertRaises(ValueError):load_inputs(self.path)
        self.reader.assert_not_called()

    def test_resume_rejects_changed_reader_manifest_result_or_source(self):
        self.run_audit(max_new=1)
        self.fp['reader']='changed'
        with self.assertRaisesRegex(ValueError,'resume inputs'):self.run_audit(resume=True)
        self.fp['reader']='fixed'
        old=self.path.read_bytes();self.doc['notes']='changed';self.save()
        with self.assertRaisesRegex(ValueError,'resume inputs'):self.run_audit(resume=True)
        self.path.write_bytes(old)
        target=self.output/(self.images[0]['sha256']+'.json');old_result=target.read_bytes();target.write_bytes(old_result+b' ')
        with self.assertRaisesRegex(ValueError,'preserved audit result'):self.run_audit(resume=True)
        target.write_bytes(old_result)
        (self.root/'0.png').write_bytes((self.root/'1.png').read_bytes())
        with self.assertRaisesRegex(ValueError,'completed source changed'):self.run_audit(resume=True)

    def test_failures_stay_in_denominator_and_are_not_silently_retried(self):
        self.reader.side_effect=lambda *a,**kw:{**self.predict(*a,**kw),'ok':False,'issues':['native_reader_failed:helper_timeout']}
        result=self.run_audit()
        self.assertEqual(2,result['outcomes']['reader_failure']);self.assertTrue(result['processing_complete'])
        self.assertIsNone(result['accuracy']);self.run_audit(resume=True);self.assertEqual(2,self.reader.call_count)

    def test_source_mismatch_never_reaches_recognizer(self):
        (self.root/'0.png').write_bytes((self.root/'1.png').read_bytes())
        result=self.run_audit()
        self.assertEqual(1,result['outcomes']['source_failure']);self.assertEqual(1,self.reader.call_count)

    def test_unchanged_mismatched_source_resumes_without_retrying_its_failure(self):
        (self.root/'0.png').write_bytes((self.root/'1.png').read_bytes())
        first=self.run_audit(max_new=1)
        self.assertEqual({'source_failure':1},first['outcomes'])
        failed=json.loads((self.output/(self.images[0]['sha256']+'.json')).read_text())
        self.assertEqual(self.images[1]['sha256'],failed['source_observations'][0]['sha256'])
        final=self.run_audit(resume=True)
        self.assertTrue(final['processing_complete'])
        self.assertEqual(1,final['outcomes']['source_failure'])
        self.assertEqual(1,self.reader.call_count)
        self.run_audit(resume=True)
        self.assertEqual(1,self.reader.call_count)

    def test_unchanged_missing_source_resumes_but_missing_to_present_is_change(self):
        path=self.root/'0.png';original=path.read_bytes();path.unlink()
        self.run_audit(max_new=1)
        result=self.run_audit(resume=True)
        self.assertTrue(result['processing_complete'])
        self.assertEqual(1,result['outcomes']['source_failure'])
        self.assertEqual(1,self.reader.call_count)
        path.write_bytes(original)
        with self.assertRaisesRegex(ValueError,'completed source changed'):
            self.run_audit(resume=True)
        self.assertEqual(1,self.reader.call_count,'Retained failures cannot silently become a fresh read')

    def test_changed_invalid_bytes_block_resume_even_when_failure_kind_is_same(self):
        path=self.root/'0.png';path.write_bytes(b'invalid saved source A')
        self.run_audit(max_new=1)
        failed=json.loads((self.output/(self.images[0]['sha256']+'.json')).read_text())
        self.assertEqual('image_not_png',failed['source_observations'][0]['error'])
        path.write_bytes(b'invalid saved source B')
        with self.assertRaisesRegex(ValueError,'completed source changed'):
            self.run_audit(resume=True)
        self.reader.assert_not_called()

    def test_failed_duplicate_alias_is_bound_on_resume(self):
        clone=self.root/'same.png'
        self.doc['images'].append({**deepcopy(self.images[0]),'image':'same.png'});self.save()
        self.run_audit(max_new=1)
        clone.write_bytes((self.root/'0.png').read_bytes())
        with self.assertRaisesRegex(ValueError,'completed source changed'):
            self.run_audit(resume=True)
        self.reader.assert_not_called()

    def test_manifest_hash_is_bound_to_exact_parsed_bytes(self):
        original=self.path.read_bytes()
        def mutate(path,**kwargs):
            data=_document_bytes(path,**kwargs)
            changed=deepcopy(self.doc);changed['images'][0]['viewport']=[0,0,20,20]
            self.path.write_text(json.dumps(changed))
            return data
        with patch('veda.archive_regression._document_bytes',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'manifest changed while loading'):
                load_inputs(self.path)
        self.path.write_bytes(original)
        loaded=load_inputs(self.path)
        self.assertEqual(sha(self.path),loaded['manifest_sha256'])
        self.assertEqual([0,0,40,20],loaded['images'][0]['viewport'])
        self.reader.assert_not_called()

    def test_all_nested_authority_and_completeness_paths_reject_true_and_impostors(self):
        row=load_inputs(self.path)['images'][0]
        base=self.predict(Path(row['image']),viewport=row['viewport'],reader=None,expected_sha256=row['sha256'])
        paths=(('cards','hand_complete'),('cards','runtime_authorized'),
               ('combat_evidence','runtime_ready'),('combat_evidence','hand_ready'),
               ('combat_evidence','controller_authorized'),('combat_evidence','enemy_count'),
               ('combat_evidence','incoming_damage'),('combat_evidence','intent_evidence','enemy_roster_complete'),
               ('combat_evidence','intent_evidence','intent_coverage_complete'),
               ('combat_evidence','intent_evidence','merge','sources',0,'runtime_authorized'),
               ('combat_evidence','refinement','intent_crop_proposal','controller_authorized'),
               ('coverage','runtime_authorization_eligible'),('coverage','combat_ready'),
               ('coverage','intent_coverage_complete'),('coverage','hand','complete'),
               ('coverage','hand','card_count'),('coverage','hand','ordered_hand'))
        for path in paths:
            for value in (True,1,0):
                prediction=deepcopy(base);cursor=prediction
                for index,key in enumerate(path[:-1]):
                    if isinstance(cursor,list):
                        while len(cursor)<=key:cursor.append({})
                        cursor=cursor[key]
                    else:
                        cursor=cursor.setdefault(key,[] if isinstance(path[index+1],int) else {})
                cursor[path[-1]]=value
                with self.subTest(path=path,value=value),self.assertRaises(ValueError):
                    _prediction_check(prediction,row)
        base['coverage']={'hand':{'complete':None,'card_count':None,'ordered_hand':None}}
        base['combat_evidence']={'runtime_ready':False,'incoming_damage':None,
                                'intent_evidence':{'enemy_roster_complete':None}}
        _prediction_check(base,row)

    def test_nested_false_success_is_cleared_and_retained_as_protocol_failure(self):
        def forged(*args,**kwargs):
            prediction=self.predict(*args,**kwargs)
            prediction['combat_evidence']={'runtime_ready':True,'intent_evidence':{'enemy_roster_complete':True}}
            return prediction
        self.reader.side_effect=forged
        result=self.run_audit()
        self.assertEqual(2,result['outcomes']['reader_exception'])
        self.assertNotIn('processed',result['outcomes'])
        for entry in json.loads((self.output/'run.json').read_text())['records']:
            self.assertIsNone(json.loads((self.output/entry['file']).read_text())['prediction'])

    def test_malformed_nested_sections_are_recorded_not_uncaught_attribute_errors(self):
        self.reader.side_effect=lambda *a,**kw:{**self.predict(*a,**kw),'combat_evidence':None}
        result=self.run_audit()
        self.assertEqual(2,result['outcomes']['reader_exception'])

    def test_runtime_claim_or_wrong_source_is_protocol_failure(self):
        self.reader.side_effect=lambda *a,**kw:{**self.predict(*a,**kw),'controller_authorized':True}
        result=self.run_audit()
        self.assertEqual(2,result['outcomes']['reader_exception']);self.assertEqual(2,result['unique_records'])
        for entry in json.loads((self.output/'run.json').read_text())['records']:
            record=json.loads((self.output/entry['file']).read_text());self.assertIsNone(record['prediction'])

    def test_changed_implementation_during_read_aborts_before_publishing(self):
        def change(*a,**kw):
            pred=self.predict(*a,**kw);self.fp['reader']='new';return pred
        self.reader.side_effect=change
        with self.assertRaisesRegex(ValueError,'changed during audit'):self.run_audit()
        self.assertEqual([],json.loads((self.output/'run.json').read_text())['records'])

    def test_corrupt_unindexed_result_never_overwritten(self):
        self.run_audit(max_new=1)
        (self.output/(self.images[1]['sha256']+'.json')).write_text('interrupted output')
        with self.assertRaisesRegex(ValueError,'unindexed result'):self.run_audit(resume=True)
        self.assertEqual(1,self.reader.call_count)

    def test_nested_failed_regions_are_counted_once_not_as_processing_success(self):
        region={'id':'intent-0-white','box_original_pixels_ltrb':[1,2,3,4],'preprocessing':'white_text',
                'ok':False,'error':'native_ocr_failed'}
        prediction=self.predict(self.root/'0.png',viewport=[0,0,40,20],reader=None,expected_sha256=self.images[0]['sha256'])
        prediction['combat_evidence']={'refinement':{'regions':[region],'intent_regions':[deepcopy(region)]}}
        result=summarize_records([{'status':'processed','source':self.images[0],'prediction':prediction}])
        self.assertEqual(1,result['outcomes']['processed'])
        self.assertEqual({'native_ocr_failed':1},result['crop_failure_counts'])
        self.assertIsNone(result['accuracy'])


if __name__=='__main__':unittest.main()
