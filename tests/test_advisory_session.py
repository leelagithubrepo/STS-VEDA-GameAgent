"""State-machine and evidence-retention contracts, not vision or live validation."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PIL import Image

from veda.advisory_session import AdvisorySession
from tests.test_evidence_ledger import CONTEXT, TIME, card, enemies, packet, player, receipt, section, statuses, ui


class AdvisorySessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root/'source.png'
        Image.new('RGB',(20,20),'black').save(self.image)
        self.sha = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.session = AdvisorySession(self.root/'session',context=CONTEXT,mode='replay')
        self.addCleanup(self.session.close)
        self.seq = 0

    def call(self, operation, **kwargs):
        self.seq += 1
        return self.session.handle({'request_id':str(self.seq),'operation':operation,**kwargs})

    def seed(self):
        enemy = enemies();enemy.update(encounter_name='Cultist',encounter_type='enemy')
        data = {'player':section({**player(),'energy':3}),
                'hand':section({'cards':[card()],'order':['c1']}),
                'enemies':section(enemy),'statuses':section(statuses()),'ui':section(ui())}
        self.offered = packet(data,digest=self.sha)
        self.call('observe',observation=self.offered,source=str(self.image))
        inv = {'current':{'card':['Strike'],'relic':[],'potion':[]},
               'coverage':{'card':'complete','relic':'complete','potion':'complete'}}
        self.call('inventory',receipt=receipt(self.session.ledger,data=inv),source=str(self.image))

    def advise(self, **kwargs):
        return self.call('advise',plan={'steps':[{'kind':'card','card_id':'c1','target':'e1'}]},as_of=TIME,**kwargs)

    def test_checked_action_is_single_use_and_never_authorizes_controller(self):
        self.seed()
        result=self.advise()
        self.assertTrue(result['advisory_ready'])
        self.assertFalse(result['controller_authorized'])
        self.assertTrue(result['historical_replay_only'])
        with self.assertRaisesRegex(ValueError,'pending'):
            self.advise()
        with self.assertRaisesRegex(ValueError,'pending'):
            self.call('observe',observation=self.offered,source=str(self.image))

    def test_reported_action_requires_later_source_and_explicit_outcome_review(self):
        self.seed();advice=self.advise();aid=advice['request_id']
        self.call('action_reported',action_id=aid,performed=True,evidence='Player reports Strike played')
        with self.assertRaisesRegex(ValueError,'reconcile'):
            self.advise()
        offered=deepcopy(self.offered);offered.update(epoch=1,frame_id='after',observed_at='2026-01-01T00:00:01+00:00')
        offered['sections']['player']['data']['energy']=2
        offered['sections']['hand']['data']={'cards':[],'order':[]}
        offered['sections']['enemies']['data']['enemies'][0]['hp']=34
        result=self.call('observe',source=str(self.image),observation=offered,outcome_review={
            'action_id':aid,'matches_expected':True,'reviewer':'independent fixture reader',
            'evidence':'energy2, enemy34, played card absent','actual_action':'Strike on e1'})
        self.assertTrue(result['summary']['last_action']['outcome_verified'])
        self.assertIn('reviewer declaration',result['summary']['last_action']['verification_basis'])

    def test_unexpected_outcome_invalidates_inventory_and_every_volatile_field(self):
        self.seed();aid=self.advise()['request_id']
        self.call('action_reported',action_id=aid,performed=True,evidence='reported play')
        offered=deepcopy(self.offered);offered.update(epoch=1,frame_id='after',observed_at='2026-01-01T00:00:01+00:00')
        result=self.call('observe',source=str(self.image),observation=offered,outcome_review={
            'action_id':aid,'matches_expected':False,'reviewer':'fixture reader',
            'evidence':'energy unchanged; unintended menu','actual_action':'unknown input'})
        self.assertIsNone(result['summary']['inventory'])
        self.assertIsNone(result['summary']['player'])
        self.assertFalse(self.advise()['advisory_ready'])

    def test_original_temporary_image_can_disappear_but_retained_proof_cannot_change(self):
        self.seed();self.image.unlink()
        self.assertTrue(self.advise()['advisory_ready'])
        retained=self.root/'session'/'sources'/f'{self.sha}.png'
        Image.new('RGB',(20,20),'red').save(retained)
        with self.assertRaisesRegex(ValueError,'changed'):
            self.session.handle({'operation':'summary'})

    def test_restart_keeps_pending_action_and_rechecks_all_source_hashes(self):
        self.seed();self.advise();self.session.close()
        self.session=AdvisorySession(self.root/'session',resume=True,mode='replay')
        self.addCleanup(self.session.close)
        self.assertIsNotNone(self.session.handle({'operation':'summary'})['pending'])
        with self.assertRaisesRegex(ValueError,'resynchronization'):self.advise()
        self.session.close()
        (self.root/'session'/'sources'/f'{self.sha}.png').unlink()
        with self.assertRaises(OSError):AdvisorySession(self.root/'session',resume=True,mode='replay')

    def test_request_retry_never_reissues_previous_recommendation(self):
        self.seed()
        request={'request_id':'once','operation':'advise','plan':{'steps':[{'kind':'card','card_id':'c1','target':'e1'}]},'as_of':TIME}
        first=self.session.handle(request);again=self.session.handle(request)
        self.assertTrue(first['advisory_ready'])
        self.assertTrue(again['already_recorded'])
        self.assertFalse(again['advisory_ready'])
        with self.assertRaisesRegex(ValueError,'reused'):
            self.session.handle({**request,'plan':{'steps':[{'kind':'end_turn'}]}})

    def test_wrong_image_is_rejected_and_persistence_failure_poison_requires_resync(self):
        offered=packet({'player':section(player())},digest='a'*64)
        with self.assertRaisesRegex(ValueError,'hash'):
            self.call('observe',observation=offered,source=str(self.image))
        self.assertIsNone(self.session.ledger.frame)
        self.seed();before=self.session.ledger.dump()
        with patch.object(self.session,'_save',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):self.call('unlogged_input',reason='manual end turn')
        self.assertEqual(before,self.session.ledger.dump())
        with self.assertRaisesRegex(ValueError,'persistence is uncertain'):
            self.advise()
        self.session.close()
        self.session=AdvisorySession(self.root/'session',resume=True,mode='replay')
        self.addCleanup(self.session.close)
        with self.assertRaisesRegex(ValueError,'resynchronization'):self.advise()
        summary=self.call('unlogged_input',reason='Recover after interrupted write')['summary']
        self.assertIsNone(summary['player']);self.assertIsNone(summary['inventory'])
        self.assertFalse(self.advise()['advisory_ready'])

    def test_cross_run_outcome_review_cannot_verify_previous_action(self):
        self.seed();aid=self.advise()['request_id']
        self.call('action_reported',action_id=aid,performed=True,evidence='reported play')
        other={**CONTEXT,'run_id':'other-run'}
        self.call('change_context',context=other,reason='different game')
        offered=deepcopy(self.offered);offered.update(context=other,epoch=2,frame_id='after',observed_at='2026-01-01T00:00:01+00:00')
        with self.assertRaisesRegex(ValueError,'different run'):
            self.call('observe',source=str(self.image),observation=offered,outcome_review={
                'action_id':aid,'matches_expected':True,'reviewer':'fixture reader',
                'evidence':'Unrelated resources','actual_action':'Strike on e1'})

    def test_duplicate_receipt_rechecks_retained_sources(self):
        self.seed()
        request={'request_id':'check-once','operation':'advise','as_of':TIME,
                 'plan':{'steps':[{'kind':'card','card_id':'c1','target':'e1'}]}}
        self.session.handle(request)
        Image.new('RGB',(20,20),'red').save(self.root/'session'/'sources'/f'{self.sha}.png')
        with self.assertRaisesRegex(ValueError,'changed'):self.session.handle(request)

    def test_end_turn_cannot_be_verified_in_the_same_turn(self):
        self.seed()
        result=self.call('advise',plan={'steps':[{'kind':'end_turn'}]},as_of=TIME)
        self.assertTrue(result['advisory_ready'])
        aid=result['request_id']
        self.call('action_reported',action_id=aid,performed=True,evidence='reported End Turn')
        offered=deepcopy(self.offered);offered.update(epoch=1,frame_id='after',observed_at='2026-01-01T00:00:01+00:00')
        with self.assertRaisesRegex(ValueError,'distinct observed turn'):
            self.call('observe',source=str(self.image),observation=offered,outcome_review={
                'action_id':aid,'matches_expected':True,'reviewer':'fixture reader',
                'evidence':'same turn frame','actual_action':'End Turn'})
        self.assertFalse(self.session.last_action['outcome_verified'])
        after_context={**CONTEXT,'turn_id':'next-turn'}
        self.call('change_context',context=after_context,reason='Observed next turn opening')
        offered.update(context=after_context,epoch=self.session.ledger.epoch)
        reviewed=self.call('observe',source=str(self.image),observation=offered,outcome_review={
            'action_id':aid,'matches_expected':True,'reviewer':'fixture reader',
            'evidence':'new turn opening independently inspected','actual_action':'End Turn'})
        self.assertTrue(reviewed['summary']['last_action']['outcome_verified'])

    def test_unlogged_turn_does_not_carry_stale_hand_or_potions(self):
        self.seed();self.advise()
        summary=self.call('unlogged_input',reason='New hand and refreshed energy after unreported end turn')['summary']
        self.assertIsNone(summary['pending'])
        self.assertIsNone(summary['hand'])
        self.assertIsNone(summary['inventory'])
        self.assertFalse(self.advise()['advisory_ready'])

    def test_review_does_not_accept_historical_clock_or_replay_mode_switch(self):
        self.session.close()
        with self.assertRaisesRegex(ValueError,'changed'):
            AdvisorySession(self.root/'session',resume=True,mode='review')
        current=AdvisorySession(self.root/'current',context=CONTEXT)
        self.addCleanup(current.close)
        with self.assertRaisesRegex(ValueError,'historical'):
            current.handle({'operation':'advise','request_id':'x','as_of':TIME,'plan':{'steps':[{'kind':'end_turn'}]}})

    def test_lock_excludes_second_writer_and_resume_requires_correct_context(self):
        with self.assertRaises(BlockingIOError):
            AdvisorySession(self.root/'session',resume=True,mode='replay')
        self.session.close()
        with self.assertRaisesRegex(ValueError,'context'):
            AdvisorySession(self.root/'session',resume=True,mode='replay',context={**CONTEXT,'run_id':'wrong'})

    def test_hot_check_does_not_replay_whole_journal(self):
        self.seed()
        with patch('veda.advisory_session.EvidenceLedger.from_dict',side_effect=AssertionError('replayed history')):
            result=self.advise()
        self.assertTrue(result['advisory_ready'])
        self.assertEqual(1,result['verified_source_count'])
        self.assertLess(len(json.dumps(result)),12000)


if __name__=='__main__':unittest.main()
