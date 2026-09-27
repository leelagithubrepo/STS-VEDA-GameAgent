"""Adversarial source, completeness and invalidation checks; no game/OCR calls."""
from copy import deepcopy
import unittest

from veda.evidence_ledger import EvidenceLedger, inventory_digest


CONTEXT = dict(run_id="r", floor_id="f", combat_id="c", turn_id="t")
TIME = "2026-01-01T00:00:00+00:00"


def section(data, complete=True):
    return dict(data=data, complete=complete, completeness_evidence="Inspected every listed field and all members" if complete else None)


def packet(sections, *, frame="f1", digest="a"*64, epoch=0, context=None, kind="reviewer", continuity=None):
    result = dict(context=deepcopy(context or CONTEXT), epoch=epoch, frame_id=frame,
                  image_sha256=digest, observed_at=TIME,
                  origin=dict(kind=kind, source="explicit test fixture", evidence_ref="fixture:source", verified=True),
                  sections=deepcopy(sections))
    if continuity is not None:
        result["continuity"] = continuity
    return result


def player():
    return dict(hp=50, max_hp=80, energy=0, block=0, ascension=1)


def card(ident="c1"):
    return dict(id=ident, name="Strike", type="Attack", cost=1, playable=True,
                upgraded=False, title_color="white")


def hand():
    return dict(cards=[card()], order=["c1"])


def enemies():
    return dict(enemies=[dict(id="e1", name="Cultist", hp=40, max_hp=40, block=0,
                             intent="attack", intent_hits=[6])], target_order=["e1"])


def statuses():
    return dict(player=dict(strength=0, dexterity=0, weak=0, vulnerable=0, frail=0,
                            no_block=0, powers={}, counters={}, end_turn_damage=0, unmodeled_effects=[]),
                enemies={"e1": dict(strength=0, weak=0, vulnerable=0, artifact=0, powers={})})


def ui():
    return dict(phase="combat", focused_card_id=None, selected_card_id=None, focused_target_id=None)


def inventory():
    return dict(current=dict(card=["Strike", "Strike"], relic=["Burning Blood"], potion=["Energy Potion"]),
                coverage=dict(card="complete", relic="complete", potion="complete"), properties={})


def receipt(ledger, *, event="i1", data=None, kind="baseline", prior=None):
    result = dict(context=deepcopy(ledger.context), epoch=ledger.epoch, **ledger.frame,
                  origin=dict(kind="reviewer", source="verified inventory view", evidence_ref="fixture:inventory", verified=True),
                  event_id=event, kind=kind, verification_evidence="All category members inspected", data=data or inventory())
    if prior is not None:
        result["prior_digest"] = prior
    return result


class EvidenceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.ledger = EvidenceLedger(**CONTEXT)

    def seed(self, values=None):
        self.ledger.observe(packet(values or {"player": section(player())}))
        self.ledger.record_inventory_event(receipt(self.ledger))

    def test_missing_is_not_empty_or_zero_or_controller_ready(self):
        snap = self.ledger.snapshot()
        self.assertIsNone(snap["sections"]["hand"]["data"])
        self.assertIsNone(snap["sections"]["inventory"]["data"])
        self.assertEqual(7, len(snap["inspection_requirements"]))
        self.assertFalse(snap["runtime_authorized"])
        self.assertFalse(snap["controller_authorized"])

    def test_partial_native_fields_can_be_completed_by_review_same_frame(self):
        self.ledger.observe(packet({"player": section({"hp":50,"energy":None},False)},kind="reader"))
        snap=self.ledger.observe(packet({"player":section(player())}))
        p=snap["sections"]["player"]
        self.assertTrue(p["complete"]); self.assertEqual(0,p["data"]["energy"])
        self.assertEqual(["reader","reviewer"],[e["origin"]["kind"] for e in p["evidence"]])

    def test_known_conflict_is_sticky_until_new_epoch_or_uncarried_frame(self):
        self.ledger.observe(packet({"player":section(player())}))
        other=player();other["hp"]=49
        snap=self.ledger.observe(packet({"player":section(other)}))
        self.assertEqual("conflict",snap["sections"]["player"]["status"])
        self.assertIsNone(snap["sections"]["player"]["data"])
        self.ledger.observe(packet({"player":section(player())}))
        self.assertFalse(self.ledger.snapshot()["sections"]["player"]["current"])
        self.ledger.invalidate(reason="new verification",kind="unknown_input")
        snap=self.ledger.observe(packet({"player":section(player())},frame="f2",epoch=1))
        self.assertTrue(snap["sections"]["player"]["complete"])

    def test_new_frame_does_not_silently_reuse_same_turn_or_inventory(self):
        self.seed({"hand":section(hand()),"ui":section(ui())})
        snap=self.ledger.observe(packet({"player":section(player())},frame="f2",digest="b"*64))
        self.assertEqual("stale",snap["sections"]["hand"]["status"])
        self.assertFalse(snap["sections"]["inventory"]["current"])

    def test_returning_to_a_superseded_frame_is_not_new_evidence(self):
        self.ledger.observe(packet({"player":section(player())}))
        self.ledger.observe(packet({"player":section(player())},frame="f2"))
        with self.assertRaisesRegex(ValueError,"superseded"):
            self.ledger.observe(packet({"player":section(player())}))

    def test_explicit_continuity_carries_facts_but_never_focus(self):
        self.seed({"hand":section(hand()),"ui":section(ui())})
        continuity=dict(previous_frame_id="f1",state_unchanged=True,evidence_ref="reviewed inspection-only transition")
        snap=self.ledger.observe(packet({"piles":section({"discard":["Defend"]},False)},frame="f2",digest="b"*64,continuity=continuity))
        self.assertTrue(snap["sections"]["hand"]["complete"])
        self.assertTrue(snap["sections"]["inventory"]["current"])
        self.assertEqual("stale",snap["sections"]["ui"]["status"])
        self.assertEqual("f1",snap["sections"]["hand"]["evidence"][0]["frame"]["frame_id"])
        self.assertEqual("f2",snap["sections"]["hand"]["evidence"][-1]["frame"]["frame_id"])

    def test_bad_continuity_is_atomic(self):
        self.seed();before=self.ledger.dump()
        with self.assertRaises(ValueError):
            self.ledger.observe(packet({"player":section(player())},frame="f2",continuity={"previous_frame_id":"other","state_unchanged":True,"evidence_ref":"x"}))
        self.assertEqual(before,self.ledger.dump())

    def test_input_invalidates_volatile_but_inventory_requires_explicit_event(self):
        self.seed()
        snap=self.ledger.invalidate(reason="verified card input",kind="input")
        self.assertEqual(1,snap["epoch"])
        self.assertFalse(snap["sections"]["player"]["current"])
        self.assertTrue(snap["sections"]["inventory"]["current"])
        with self.assertRaisesRegex(ValueError,"epoch"):
            self.ledger.observe(packet({"player":section(player())}))
        with self.assertRaisesRegex(ValueError,"frame identifier"):
            self.ledger.observe(packet({"player":section(player())},epoch=1))

    def test_unknown_input_and_inventory_change_invalidate_inventory(self):
        for kind in ("unknown_input","inventory_change"):
            with self.subTest(kind=kind):
                self.setUp();self.seed()
                snap=self.ledger.invalidate(reason="unobserved effect",kind=kind)
                self.assertFalse(snap["sections"]["inventory"]["current"])

    def test_context_binding_rejects_prior_turn_and_run_inventory_does_not_leak(self):
        self.seed()
        snap=self.ledger.change_context(**{**CONTEXT,"turn_id":"t2"},reason="observed turn transition")
        self.assertTrue(snap["sections"]["inventory"]["current"])
        with self.assertRaisesRegex(ValueError,"context"):
            self.ledger.observe(packet({"player":section(player())},epoch=1))
        snap=self.ledger.change_context(run_id="new",reason="new run")
        self.assertFalse(snap["sections"]["inventory"]["current"])

    def test_hash_timestamp_and_named_verified_origin_are_required(self):
        self.ledger.observe(packet({"player":section(player())}))
        for mutation in (lambda p:p.update(image_sha256="b"*64),
                         lambda p:p.update(observed_at="2025-01-01T00:00:00+00:00"),
                         lambda p:p["origin"].update(verified=False),
                         lambda p:p["origin"].update(source=""),
                         lambda p:p.update(observed_at="2999-01-01T00:00:00+00:00")):
            data=packet({"player":section(player())});mutation(data)
            with self.subTest(data=data),self.assertRaises(ValueError):self.ledger.observe(data)

    def test_unread_hand_details_never_become_complete_from_candidates(self):
        for field,value in (("cost",None),("playable",None),("upgraded",None),("title_color",None),("title_color","green")):
            h=hand();h["cards"][0][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                self.ledger.observe(packet({"hand":section(h)}))
        with self.assertRaises(ValueError):self.ledger.observe(packet({"hand":section({"card_candidates":[card()]})}))

    def test_unique_hand_ids_order_and_explicit_empty_proof(self):
        for h in ({"cards":[card(),card()],"order":["c1","c1"]},{"cards":[card()],"order":None}):
            with self.assertRaises(ValueError):self.ledger.observe(packet({"hand":section(h)}))
        bad=section({"cards":[],"order":[]});bad["completeness_evidence"]=""
        with self.assertRaises(ValueError):self.ledger.observe(packet({"hand":bad}))
        snap=self.ledger.observe(packet({"hand":section({"cards":[],"order":[]})}))
        self.assertEqual([],snap["sections"]["hand"]["data"]["cards"])

    def test_complete_hand_cannot_lose_partial_seen_card(self):
        self.ledger.observe(packet({"hand":section({"cards":[card()],"order":None},False)}))
        snap=self.ledger.observe(packet({"hand":section({"cards":[],"order":[]})}))
        self.assertEqual("conflict",snap["sections"]["hand"]["status"])

    def test_complete_statuses_require_known_counts_and_entire_roster(self):
        s=statuses();s["player"]["powers"]={"Barricade":None}
        with self.assertRaises(ValueError):self.ledger.observe(packet({"statuses":section(s)}))
        s=statuses();s["enemies"]={}
        snap=self.ledger.observe(packet({"enemies":section(enemies()),"statuses":section(s)}))
        self.assertEqual("conflict",snap["sections"]["enemies"]["status"])
        self.assertEqual("conflict",snap["sections"]["statuses"]["status"])

    def test_status_section_cannot_override_resources_identity_or_intents(self):
        for target, field, value in (("player", "hp", 999), ("player", "energy", 99),
                                     ("enemy", "intent_hits", []), ("enemy", "id", "other")):
            s=statuses()
            (s["player"] if target == "player" else s["enemies"]["e1"])[field]=value
            with self.subTest(target=target,field=field),self.assertRaisesRegex(ValueError,"cannot override"):
                self.ledger.observe(packet({"statuses":section(s)}))

    def test_conflicting_duplicate_enemy_status_fields_are_withheld(self):
        e=enemies();e["enemies"][0]["weak"]=2
        snap=self.ledger.observe(packet({"enemies":section(e),"statuses":section(statuses())}))
        self.assertFalse(snap["sections"]["statuses"]["current"])

    def test_focus_must_refer_to_current_hand_and_explicit_absence_cannot_flip(self):
        u=ui();u["selected_card_id"]="missing"
        snap=self.ledger.observe(packet({"hand":section(hand()),"ui":section(u)}))
        self.assertEqual("conflict",snap["sections"]["ui"]["status"])
        self.setUp();self.ledger.observe(packet({"ui":section(ui())}))
        u=ui();u["focused_card_id"]="c1"
        snap=self.ledger.observe(packet({"ui":section(u)}))
        self.assertEqual("conflict",snap["sections"]["ui"]["status"])

    def test_piles_preserve_duplicates_unknown_order_and_inspection_needed(self):
        self.ledger.observe(packet({"piles":section({"draw":["Strike","Strike"],"discard":None},False)}))
        p=dict(draw=["Strike","Strike","Defend"],discard=[],exhaust=[],draw_order=None)
        snap=self.ledger.observe(packet({"piles":section(p)}))
        self.assertIsNone(snap["sections"]["piles"]["data"]["draw_order"])
        p["draw_order"]=["Strike","Defend"]
        with self.assertRaises(ValueError):self.ledger.observe(packet({"piles":section(p)}))

    def test_partial_scroll_page_is_not_a_complete_pile(self):
        snap=self.ledger.observe(packet({"piles":section({"draw":["Barricade+"]},False)}))
        self.assertEqual("partial",snap["sections"]["piles"]["data"]["coverage"]["draw"])
        self.assertFalse(snap["sections"]["piles"]["complete"])
        self.assertNotIn("draw",snap["sections"]["piles"]["data"].get("zone_evidence",{}))

    def test_one_complete_zone_needs_its_own_proof_and_does_not_certify_other_piles(self):
        data={"draw":["Strike","Strike"],"coverage":{"draw":"complete"},"zone_evidence":{"draw":"every draw page inspected"}}
        snap=self.ledger.observe(packet({"piles":section(data,False)}))
        self.assertEqual("complete",snap["sections"]["piles"]["data"]["coverage"]["draw"])
        self.assertFalse(snap["sections"]["piles"]["complete"])
        data["zone_evidence"]={}
        with self.assertRaises(ValueError):self.ledger.observe(packet({"piles":section(data,False)}))

    def test_partial_page_cannot_add_a_card_to_confirmed_complete_zone(self):
        data={"draw":["Strike"],"coverage":{"draw":"complete"},"zone_evidence":{"draw":"all pages"}}
        self.ledger.observe(packet({"piles":section(data,False)}))
        snap=self.ledger.observe(packet({"piles":section({"draw":["Defend"]},False)}))
        self.assertEqual("conflict",snap["sections"]["piles"]["status"])

    def test_later_complete_zone_must_contain_every_previously_seen_copy(self):
        self.ledger.observe(packet({"piles":section({"draw":["Strike","Strike"]},False)}))
        data={"draw":["Strike"],"coverage":{"draw":"complete"},"zone_evidence":{"draw":"all pages"}}
        snap=self.ledger.observe(packet({"piles":section(data,False)}))
        self.assertEqual("conflict",snap["sections"]["piles"]["status"])

    def test_inventory_cannot_enter_as_volatile_observation(self):
        with self.assertRaises(ValueError):self.ledger.observe(packet({"inventory":section(inventory())}))
        with self.assertRaises(ValueError):self.ledger.record_inventory_event(dict(event_id="x"))

    def test_inventory_transition_uses_prior_digest_and_invalidates_predictions(self):
        self.seed();original=self.ledger.snapshot()["sections"]["inventory"]
        after=inventory();after["current"]["potion"]=[]
        event=receipt(self.ledger,event="used",data=after,kind="verified_transition",prior="b"*64)
        before=self.ledger.dump()
        with self.assertRaises(ValueError):self.ledger.record_inventory_event(event)
        self.assertEqual(before,self.ledger.dump())
        event["prior_digest"]=original["digest"]
        snap=self.ledger.record_inventory_event(event)
        self.assertEqual([],snap["sections"]["inventory"]["data"]["current"]["potion"])
        self.assertEqual(inventory_digest(after),snap["sections"]["inventory"]["digest"])
        self.assertFalse(snap["sections"]["player"]["current"])
        self.assertEqual(1,snap["epoch"])

    def test_duplicate_inventory_event_is_not_double_applied(self):
        self.ledger.observe(packet({"player":section(player())}));event=receipt(self.ledger)
        self.ledger.record_inventory_event(event);snap=self.ledger.record_inventory_event(event)
        self.assertEqual(1,len(snap["sections"]["inventory"]["evidence"]))
        changed=deepcopy(event);changed["data"]["current"]["potion"]=[]
        with self.assertRaises(ValueError):self.ledger.record_inventory_event(changed)

    def test_partial_inventory_baseline_does_not_erase_previously_seen_items(self):
        self.seed();old=self.ledger.snapshot()["sections"]["inventory"]
        data=inventory();data["current"]["card"]=["Strike"];data["coverage"]["card"]="partial"
        snap=self.ledger.record_inventory_event(receipt(self.ledger,event="partial",data=data,prior=old["digest"]))
        self.assertEqual(["Strike","Strike"],snap["sections"]["inventory"]["data"]["current"]["card"])

    def test_conflicting_inventory_baseline_cannot_masquerade_as_consumption(self):
        self.seed();old=self.ledger.snapshot()["sections"]["inventory"]
        data=inventory();data["current"]["potion"]=[]
        snap=self.ledger.record_inventory_event(receipt(self.ledger,event="conflict",data=data,prior=old["digest"]))
        self.assertEqual("conflict",snap["sections"]["inventory"]["status"])
        self.assertIsNone(snap["sections"]["inventory"]["data"])
        self.assertNotIn("digest",snap["sections"]["inventory"])
        self.assertEqual("conflict",snap["sections"]["inventory"]["evidence"][-1]["event_id"])
        with self.assertRaisesRegex(ValueError,"conflicted inventory"):
            self.ledger.record_inventory_event(receipt(self.ledger,event="overwrite"))

    def test_partial_deck_inventory_keeps_complete_potion_relic_categories(self):
        self.ledger.observe(packet({"player":section(player())}));data=inventory();data["coverage"]["card"]="partial"
        snap=self.ledger.record_inventory_event(receipt(self.ledger,data=data))
        self.assertTrue(snap["sections"]["inventory"]["current"])
        self.assertFalse(snap["sections"]["inventory"]["complete"])
        self.assertEqual("complete",snap["sections"]["inventory"]["data"]["coverage"]["potion"])

    def test_journal_roundtrip_preserves_provenance_and_invalidations(self):
        self.seed();self.ledger.invalidate(reason="unlogged input",kind="unknown_input")
        restored=EvidenceLedger.from_dict(self.ledger.dump())
        self.assertEqual(self.ledger.snapshot(),restored.snapshot())
        bad=self.ledger.dump();bad["controller_authorized"]=True
        with self.assertRaises(ValueError):EvidenceLedger.from_dict(bad)
        bad=self.ledger.dump();bad["events"][0]["payload"]["epoch"]=2
        with self.assertRaises(ValueError):EvidenceLedger.from_dict(bad)

    def test_returns_are_defensive_copies_and_invalid_packets_are_atomic(self):
        self.seed();snap=self.ledger.snapshot();snap["sections"]["inventory"]["data"]["current"]["potion"].clear()
        self.assertEqual(["Energy Potion"],self.ledger.snapshot()["sections"]["inventory"]["data"]["current"]["potion"])
        before=self.ledger.dump()
        with self.assertRaises(ValueError):self.ledger.observe(packet({"player":section(player()),"hand":section({"cards":[]})}))
        self.assertEqual(before,self.ledger.dump())


if __name__ == "__main__":
    unittest.main()
