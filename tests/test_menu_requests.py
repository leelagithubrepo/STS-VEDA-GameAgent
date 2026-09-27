"""Compact draft and saved receipt packaging; synthetic files, no live I/O."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from tests import test_menu_controls as menus
from tests import test_reviewed_play as runtime
from veda.choice_execution import ChoiceError, plan_choice_step
from veda import menu_requests as requests
from veda.menu_controls import CONTROL_PROFILE
from veda.reviewed_play import inventory_digest


def draft():
    obs = menus.event()
    return {'schema': requests.SCHEMA, 'context': deepcopy(obs['context']),
        'inventory': {'current': {'card': [], 'relic': ['Burning Blood'], 'potion': []},
            'coverage': {'card': 'unknown', 'relic': 'complete', 'potion': 'complete'}, 'properties': {}},
        'resources': deepcopy(obs['resources']), 'facts': deepcopy(obs['facts']),
        'ui': {'menu_family': 'event_options', 'choice_id': 'neow-reward',
            'focused_id': 'upgrade', 'options': deepcopy(obs['ui']['options'])},
        'choice': {'kind': 'event', 'option_ids': ['upgrade'], 'postconditions': {
            'screen': 'selection', 'phase': 'choose', 'resources': deepcopy(obs['resources']),
            'inventory': 'unchanged', 'facts': {}, 'allow_changed_facts': []}},
        'reasoning': 'Synthetic fixture: open the upgrade picker before inspecting its cards.'}


def make_capture(root, captured, color=(20, 40, 60), index='a'):
    image = Path(root) / ('ps5_observation_' + captured.strftime('%Y%m%dT%H%M%S.%fZ') + '_' + index*32 + '.png')
    image.write_bytes(runtime.png_bytes(color))
    receipt = {'schema':'veda.game-window-capture.v1', 'image_path':str(image.resolve()),
        'image_sha256':hashlib.sha256(image.read_bytes()).hexdigest(), 'dimensions':[8,8],
        'capture_requested_at':captured.isoformat(),
        'capture_completed_at':(captured+timedelta(milliseconds=100)).isoformat(),
        'window':{'id':123,'title':'Synthetic fixture only'},
        'pixel_content_verified':False, 'controller_input_sent':False}
    image.with_suffix('.capture.json').write_text(json.dumps(receipt))
    return image


class MenuRequestTests(unittest.TestCase):
    def setUp(self):
        temp=TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name).resolve()
        self.now=datetime(2026,9,27,17,0,0,tzinfo=timezone.utc)
        self.captured=self.now-timedelta(seconds=2)
        self.image=make_capture(self.root,self.captured)
        self.output=self.root/'request.json'
        self.value=draft()

    def build(self, **overrides):
        args={'capture':self.image,'reviewer':'Synthetic fixture reviewer',
            'evidence_note':'I inspected this exact synthetic capture and its declared draft facts.',
            'reviewed':True,'control_profile':CONTROL_PROFILE,'output':self.output,'now':self.now}
        args.update(overrides)
        return requests.write_menu_request(self.value, **args)

    def test_draft_validation_is_source_free_non_dispatchable_and_immutable(self):
        original=deepcopy(self.value)
        with patch.object(requests,'reviewed_capture_source',side_effect=AssertionError('source accessed')), \
             patch('pathlib.Path.open',side_effect=AssertionError('file accessed')), \
             patch('veda.telemetry_database.TelemetryDatabase',side_effect=AssertionError('DB accessed')), \
             patch('veda.bridge_client.BridgeClient',side_effect=AssertionError('bridge accessed')):
            result=requests.validate_menu_draft(self.value,CONTROL_PROFILE)
        self.assertEqual(original,self.value)
        self.assertTrue(result['draft_valid']); self.assertTrue(result['validation_only'])
        self.assertFalse(result['dispatchable']); self.assertFalse(result['source_bound'])
        self.assertTrue(result['requires_exact_fresh_capture_review'])
        self.assertFalse(set(result)&{'source','frame','observation','proposal','operation','command','request_file'})
        self.assertFalse(self.output.exists())

    def test_structure_strategy_cost_and_profile_errors_are_found_before_any_capture(self):
        changes=[lambda d:d['choice'].update(option_ids=['missing']),
            lambda d:d['ui'].update(control_layout='custom'),
            lambda d:d['ui'].update(required_count=True),
            lambda d:d['choice'].update(kind='shop'),
            lambda d:d['ui'].update(focused_id='unread'),
            lambda d:d['inventory']['coverage'].update(relic='unknown')]
        for change in changes:
            bad=deepcopy(self.value); change(bad)
            with self.subTest(change=change), patch.object(requests,'reviewed_capture_source',side_effect=AssertionError('source accessed')), self.assertRaises(ValueError):
                requests.write_menu_request(bad,capture='',reviewer='fixture',evidence_note='fixture',
                    reviewed=True,control_profile=CONTROL_PROFILE,output=self.output,now=self.now)
        paid=deepcopy(self.value); paid['ui']['focused_id']='colorless'; paid['choice']['option_ids']=['colorless']
        with self.assertRaisesRegex(ChoiceError,'exact reviewed debit'):
            requests.validate_menu_draft(paid,CONTROL_PROFILE)
        with self.assertRaisesRegex(ChoiceError,'default PS5'):
            requests.validate_menu_draft(self.value,'unknown')

    def test_draft_cannot_supply_source_reviews_hashes_or_control_proofs(self):
        changes=[lambda d:d.update(source={}),lambda d:d.update(review={}),lambda d:d.update(frame={}),
            lambda d:d.update(inventory_digest='a'*64),lambda d:d['choice'].update(review={}),
            lambda d:d['ui'].update(confirm={'button':'cross'}),
            lambda d:d['ui']['options'][0].update(activate={'button':'cross'}),
            lambda d:d['choice']['postconditions'].update(inventory_digest='a'*64)]
        for change in changes:
            bad=deepcopy(self.value);change(bad)
            with self.subTest(change=change),self.assertRaises(ValueError):requests.validate_menu_draft(bad,CONTROL_PROFILE)

    def test_json_loader_is_bounded_duplicate_free_finite_and_does_not_mutate_files(self):
        path=self.root/'draft.json'
        for data in [b'{"schema":1,"schema":2}',b'{"number":NaN}',b'\xff',b'['*1200,
                     b' '*(requests.MAX_DRAFT_BYTES+1)]:
            path.write_bytes(data)
            with self.subTest(data=data[:30]),self.assertRaises(ValueError):requests.read_menu_draft(path)
            self.assertEqual(data,path.read_bytes())
        path.write_text(json.dumps(self.value))
        self.assertEqual(self.value,requests.read_menu_draft(path))

    def test_receipt_atomically_derives_every_source_review_and_inventory_field(self):
        before=deepcopy(self.value); result=self.build()
        packet=json.loads(self.output.read_bytes())
        self.assertEqual({'request_file':str(self.output)},result)
        self.assertEqual(before,self.value)
        self.assertEqual(packet['review'],packet['observation']['review'])
        self.assertEqual('reviewed_choice_ui',packet['review']['kind'])
        self.assertEqual('reviewed_choice',packet['choice']['review']['kind'])
        source=packet['source'];frame=packet['observation']['frame']
        self.assertEqual(str(self.image),source['path'])
        self.assertEqual(self.captured.isoformat(),source['captured_at'])
        self.assertEqual(source['captured_at'],frame['observed_at'])
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).hexdigest(),source['sha256'])
        for review in [packet['review'],packet['observation']['review'],packet['choice']['review']]:
            self.assertEqual(source['sha256'],review['image_sha256']);self.assertEqual(frame['frame_id'],review['frame_id'])
        self.assertEqual(inventory_digest(self.value['inventory']),packet['observation']['inventory_digest'])
        self.assertEqual(self.value['context'],packet['choice']['postconditions']['context'])
        self.assertEqual('unchanged',packet['choice']['postconditions']['inventory_digest'])
        self.assertEqual(['cross'],plan_choice_step(packet['observation'],packet['choice'],now=self.now)['command']['buttons'])
        second=self.root/'second.json';self.build(output=second)
        self.assertEqual(self.output.read_bytes(),second.read_bytes())

    def test_valid_draft_still_requires_explicit_review_of_each_exact_fresh_capture(self):
        requests.validate_menu_draft(self.value,CONTROL_PROFILE)
        for declared in (False,1,None):
            with self.subTest(declared=declared),self.assertRaisesRegex(ValueError,'exact_image_review_required'):
                self.build(reviewed=declared)
            self.assertFalse(self.output.exists())
        self.build();first=json.loads(self.output.read_bytes())
        new_capture=make_capture(self.root,self.now,color=(60,40,20),index='b')
        new_output=self.root/'fresh.json'
        with self.assertRaisesRegex(ValueError,'exact_image_review_required'):
            self.build(capture=new_capture,output=new_output,now=self.now+timedelta(seconds=1),reviewed=False)
        self.build(capture=new_capture,output=new_output,now=self.now+timedelta(seconds=1))
        fresh=json.loads(new_output.read_bytes())
        self.assertNotEqual(first['observation']['frame'],fresh['observation']['frame'])
        self.assertEqual(first['observation']['facts'],fresh['observation']['facts'])
        self.assertEqual(self.value['resources'],fresh['observation']['resources'])

    def test_empty_missing_stale_future_and_mixed_receipt_identity_never_publish(self):
        for args,match in [({'capture':''},'path_invalid'),({'capture':self.root/'missing.png'},'capture_metadata_unreadable'),
            ({'now':self.captured+timedelta(seconds=30,microseconds=1)},'capture_stale'),
            ({'now':self.captured-timedelta(microseconds=1)},'capture_future_dated')]:
            with self.subTest(args=args),self.assertRaisesRegex(ValueError,match):self.build(**args)
            self.assertFalse(self.output.exists())
        receipt=self.image.with_suffix('.capture.json'); metadata=json.loads(receipt.read_bytes())
        metadata['image_sha256']='f'*64;receipt.write_text(json.dumps(metadata))
        with self.assertRaisesRegex(ValueError,'capture_hash_mismatch'):self.build()
        self.assertFalse(self.output.exists())

    def test_exact_thirty_second_boundary_retains_capture_start(self):
        self.build(now=self.captured+timedelta(seconds=30))
        self.assertEqual(self.captured.isoformat(),json.loads(self.output.read_bytes())['source']['captured_at'])

    def test_changed_source_between_validation_and_publish_never_outputs(self):
        original=requests.reviewed_capture_source; calls=[]
        def change(**kwargs):
            calls.append(kwargs)
            if len(calls)==2:self.image.write_bytes(runtime.png_bytes((30,50,70)))
            return original(**kwargs)
        with patch.object(requests,'reviewed_capture_source',side_effect=change),self.assertRaisesRegex(ValueError,'capture_hash_mismatch'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_fully_serialized_exclusive_output_preserves_sources_and_cleans_write_failure(self):
        for target in (self.image,self.image.with_suffix('.capture.json')):
            contents=target.read_bytes()
            with self.assertRaisesRegex(ValueError,'overwrite capture'):self.build(output=target)
            self.assertEqual(contents,target.read_bytes())
        self.output.write_text('existing')
        with self.assertRaisesRegex(ValueError,'already exists'):self.build()
        self.assertEqual('existing',self.output.read_text());self.output.unlink()
        with patch.object(requests.os,'fsync',side_effect=OSError('synthetic disk error')),self.assertRaisesRegex(ValueError,'write failed'):
            self.build()
        self.assertFalse(self.output.exists())

    def test_upgrade_preview_binds_actual_hint_and_derived_expected_inventory(self):
        raw=menus.grid();raw['ui']['focused_id']='bash-1';observed=menus.preview(raw)
        ui=observed['ui'];hint=ui.pop('confirm');ui.pop('navigation')
        ui['confirm_hint']={'button':hint['button'],'hint_text':hint['evidence']['hint_text']}
        self.value['ui']=ui;self.value['facts']=observed['facts'];self.value['resources']=observed['resources']
        inventory=self.value['inventory'];inventory['current']['card']=['Strike','Strike','Defend','Bash'];inventory['coverage']['card']='complete'
        expected=deepcopy(inventory);expected['current']['card'].remove('Bash');expected['current']['card'].append('Bash+')
        self.value['choice']={'kind':'selection','option_ids':['bash-1'],'postconditions':{
            'screen':'event','phase':'result','resources':deepcopy(self.value['resources']),'inventory':expected,
            'facts':{'upgraded_card_id':'bash-1','upgraded_card_name':'Bash+'},'allow_changed_facts':[]}}
        receipt=requests.validate_menu_draft(self.value,CONTROL_PROFILE)
        self.assertFalse(receipt['source_bound'])
        self.build();packet=json.loads(self.output.read_bytes())
        self.assertEqual(inventory_digest(expected),packet['choice']['postconditions']['inventory_digest'])
        proof=packet['observation']['ui']['confirm']['evidence']
        self.assertEqual('visible_hint',proof['kind']);self.assertEqual(packet['source']['sha256'],proof['image_sha256'])
        self.assertEqual(['triangle'],plan_choice_step(packet['observation'],packet['choice'],now=self.now)['command']['buttons'])

    def test_upgrade_inventory_cannot_change_another_card_quantity_or_coverage(self):
        original=draft()
        raw=menus.grid();raw['ui']['focused_id']='bash-1'
        original['ui']={key:deepcopy(value) for key,value in raw['ui'].items() if key!='navigation'}
        before=original['inventory'];before['current']['card']=['Strike','Strike','Defend','Bash'];before['coverage']['card']='complete'
        expected=deepcopy(before);expected['current']['card'].remove('Bash');expected['current']['card'].append('Bash+')
        original['choice']={'kind':'selection','option_ids':['bash-1'],'postconditions':{
            'screen':'event','phase':'result','resources':deepcopy(original['resources']),'inventory':expected,
            'facts':{'upgraded_card_id':'bash-1','upgraded_card_name':'Bash+'},'allow_changed_facts':[]}}
        self.assertTrue(requests.validate_menu_draft(original,CONTROL_PROFILE)['draft_valid'])
        mutations=[lambda i:i['current'].update(card=['Strike','Strike','Defend+','Bash']),
            lambda i:i['current']['card'].append('Bash+'),
            lambda i:i['current']['card'].remove('Strike'),
            lambda i:i['coverage'].update(card='partial'),
            lambda i:i['current']['potion'].append('Fire Potion')]
        for mutate in mutations:
            bad=deepcopy(original);mutate(bad['choice']['postconditions']['inventory'])
            with self.subTest(mutate=mutate),self.assertRaisesRegex(ValueError,'exactly one selected card'):
                requests.validate_menu_draft(bad,CONTROL_PROFILE)
        bad=deepcopy(original);bad['inventory']['current']['card'].remove('Bash')
        with self.assertRaisesRegex(ValueError,'absent from reviewed inventory'):
            requests.validate_menu_draft(bad,CONTROL_PROFILE)

    def test_menu_packaging_calls_no_capture_controller_database_or_subprocess(self):
        with patch('veda.game_capture.capture_game_window',side_effect=AssertionError('capture called')), \
             patch('veda.reviewed_play.ReviewedPlaySession',side_effect=AssertionError('session called')), \
             patch('veda.bridge_client.BridgeClient',side_effect=AssertionError('bridge called')), \
             patch('veda.telemetry_database.TelemetryDatabase',side_effect=AssertionError('DB called')), \
             patch('subprocess.run',side_effect=AssertionError('process called')):
            self.build()
