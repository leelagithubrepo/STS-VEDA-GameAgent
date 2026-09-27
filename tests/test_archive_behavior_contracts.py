"""Archive-motivated synthetic contracts, not OCR or causal replay accuracy.

Game-specific expectations use the existing reviewed local advisory rules.
No test opens the private game-history database or operates a recognizer.
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from veda.advisory import boss_manifest, check_plan
from veda.card_catalog import canonical_title
from veda.evidence_ledger import EvidenceLedger
from veda.telemetry_database import TelemetryDatabase
from tests.test_advisory_current_deck import card, context, play
from tests.test_evidence_ledger import CONTEXT, enemies, hand, inventory, packet, player, receipt, section, statuses, ui
from tests.test_routine_current_deck import context as routine_context, plan as routine_plan


class ArchiveBehaviorContracts(unittest.TestCase):
    def test_recognized_title_does_not_grant_reviewed_effect_or_lethal_claim(self):
        for name in ("Uppercut", "Metallicize", "Cleave", "Fiend Fire", "Fiend Fire+",
                     "Combust", "Combust+", "Perfected Strike", "Warcry", "Wound"):
            with self.subTest(name=name):
                self.assertEqual(name, canonical_title(name))
                c = context([card(name)])
                c["state"]["enemies"][0]["hp"] = 1
                result = check_plan(c, {"steps": [play()], "claims_lethal": True})
                self.assertFalse(result["allowed"])
                self.assertIsNone(result["forecast"])
                self.assertTrue(any("no reviewed" in reason for reason in result["reasons"]))

    def test_complete_calibration_cannot_hide_unsupported_or_unknown_context(self):
        base = routine_context()
        self.assertTrue(routine_plan(base).ready)
        mutations = (
            lambda c: c["state"].update(powers={"Corruption": True}),
            lambda c: c["inventory"]["current"].update(relic=["Runic Pyramid"]),
            lambda c: c["inventory"]["coverage"].update(potion="partial"),
            lambda c: c["state"].update(unmodeled_effects=["unread status"]),
            lambda c: c["state"].update(dexterity=None),
        )
        for mutate in mutations:
            c = deepcopy(base)
            mutate(c)
            with self.subTest(mutation=mutate):
                self.assertFalse(routine_plan(c).ready)

    def test_energy_potion_requires_observation_before_spending_its_effect(self):
        c = context([card("Body Slam", cost=1)])
        c["state"]["energy"] = 0
        c["inventory"]["current"]["potion"] = ["Energy Potion"]
        before = deepcopy(c)
        drink = {"kind": "potion", "name": "Energy Potion"}
        result = check_plan(c, {"steps": [drink]})
        self.assertTrue(result["allowed"], result["reasons"])
        self.assertIsNone(result["steps"][0]["energy_after"])
        self.assertIsNone(result["forecast"])
        self.assertFalse(check_plan(c, {"steps": [drink, play()]})["allowed"])
        self.assertFalse(check_plan(c, {"steps": [play()]})["allowed"])
        self.assertEqual(before, c, "Advice cannot consume inventory or fabricate potion energy")

    def test_fairy_is_not_a_manual_action_or_implicit_survival_forecast(self):
        c = context([])
        c["state"].update(hp=18, block=13)
        c["state"]["enemies"][0]["intent_hits"] = [31]
        c["inventory"]["current"]["potion"] = ["Energy Potion", "Power Potion", "Fairy in a Bottle"]
        self.assertFalse(check_plan(c, {"steps": [{"kind": "potion", "name": "Fairy in a Bottle"}]})["allowed"])
        result = check_plan(c, {"steps": [{"kind": "end_turn"}],
            "potion_review": {"Energy Potion": "No remaining playable cards.",
                              "Power Potion": "Outcome cannot be assumed."}})
        self.assertFalse(result["allowed"])
        self.assertEqual(0, result["forecast"]["player_hp"])
        self.assertTrue(any("automatically" in note for note in result["notes"]))

    def test_multi_enemy_transition_needs_new_roster_not_partial_damage_sum(self):
        c = context([])
        c["state"].update(hp=50, block=13)
        c["state"]["enemies"].append({**deepcopy(c["state"]["enemies"][0]),
                                      "id": "second", "name": "Jaw Worm", "intent_hits": [25]})
        plan = {"steps": [{"kind": "end_turn"}]}
        before = check_plan(c, plan)
        self.assertEqual(31, before["forecast"]["incoming_displayed"])
        self.assertEqual(32, before["forecast"]["player_hp"])
        c["state"]["enemies"][1]["intent_hits"] = None
        self.assertFalse(check_plan(c, plan)["allowed"])
        c["state"]["enemies"] = [c["state"]["enemies"][0]]
        after = check_plan(c, plan)
        self.assertEqual(6, after["forecast"]["incoming_displayed"])
        self.assertEqual(50, after["forecast"]["player_hp"])
        # These are separately supplied states, not a claim that an action killed
        # the second enemy or that one visible enemy establishes completeness.

    def test_time_warp_potion_boundary_does_not_spend_card_counter(self):
        c = context([card("Strike")])
        c["state"].update(ascension=1, block=30)
        c["state"]["counters"]["time_warp"] = 11
        c["state"]["enemies"][0].update(name="Time Eater", hp=300, max_hp=456,
            move="Reverberate", intent_hits=[7, 7, 7])
        c["encounter_type"] = "boss"
        c["boss_manifest"] = boss_manifest("Time Eater", 1)
        c["inventory"]["current"]["potion"] = ["Energy Potion"]
        result = check_plan(c, {"steps": [{"kind": "potion", "name": "Energy Potion"}]})
        self.assertTrue(result["allowed"])
        self.assertEqual(11, c["state"]["counters"]["time_warp"])
        self.assertNotIn("time_warp_after", result["steps"][0])
        card_result = check_plan(c, {"steps": [play()],
                                   "potion_review": {"Energy Potion": "Existing energy is sufficient for this one card."}})
        self.assertTrue(card_result["allowed"], card_result["reasons"])
        self.assertEqual(12, card_result["steps"][0]["time_warp_after"])
        self.assertTrue(card_result["steps"][0]["observe_after"])
        self.assertEqual(27, card_result["forecast"]["incoming_displayed"])

    def test_card_selection_boundary_invalidates_piles_and_cannot_reuse_old_focus(self):
        ledger = EvidenceLedger(**CONTEXT)
        ledger.observe(packet({"player": section(player()), "hand": section(hand()),
            "enemies": section(enemies()), "statuses": section(statuses()), "ui": section(ui()),
            "piles": section({"draw": ["Strike"], "discard": ["Defend"], "exhaust": []})}))
        ledger.record_inventory_event(receipt(ledger))
        after = ledger.invalidate(reason="Observed Headbutt played; selection outcome not yet read", kind="input")
        self.assertIsNone(after["sections"]["piles"]["data"])
        self.assertIsNone(after["sections"]["ui"]["data"])
        self.assertTrue(after["sections"]["inventory"]["current"])
        ledger.observe(packet({"hand": section(hand())}, frame="after", digest="b"*64, epoch=1))
        needed = {request["section"] for request in ledger.inspection_requirements()}
        self.assertIn("piles", needed)
        self.assertIn("ui", needed)
        before_replay = ledger.dump()
        with self.assertRaises(ValueError):
            ledger.observe(packet({"ui": section(ui())}))
        self.assertEqual(before_replay, ledger.dump())

    def test_duplicate_potion_transition_removes_one_copy_and_blocks_old_epoch(self):
        ledger = EvidenceLedger(**CONTEXT)
        ledger.observe(packet({"player": section(player())}))
        data = inventory()
        data["current"]["potion"] = ["Energy Potion", "Energy Potion", "Fairy in a Bottle"]
        ledger.record_inventory_event(receipt(ledger, data=data))
        old_digest = ledger.snapshot()["sections"]["inventory"]["digest"]
        after = deepcopy(data)
        after["current"]["potion"].remove("Energy Potion")
        event = receipt(ledger, event="one-slot-consumed", data=after, kind="verified_transition", prior=old_digest)
        snapshot = ledger.record_inventory_event(event)
        self.assertEqual(["Energy Potion", "Fairy in a Bottle"], snapshot["sections"]["inventory"]["data"]["current"]["potion"])
        self.assertIsNone(snapshot["sections"]["player"]["data"])
        frozen = ledger.dump()
        with self.assertRaises(ValueError):
            ledger.record_inventory_event(event)
        self.assertEqual(frozen, ledger.dump(), "A stale receipt cannot consume the second copy")


class ArchiveMemoryBehaviorContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = TelemetryDatabase(Path(self.tmp.name)/"synthetic.sqlite3")
        self.run = self.db.start_new_run(ascension=1)
        self.floor = self.db.record_floor(run_id=self.run, act=1, floor=1, node_type="enemy", outcome=None, starting_state={})
        self.combat = self.db.start_combat(run_id=self.run, floor_id=self.floor, opening_state={}, encounter_name="Cultist", encounter_type="enemy")
        self.turn = self.db.start_combat_turn(combat_id=self.combat, turn_number=1, phase="combat", opening_state={})
        self.db.record_inventory_baseline(run_id=self.run, items=[{"kind": "card", "item": "Strike"},
            {"kind": "potion", "item": "Power Potion"}, {"kind": "potion", "item": "Energy Potion"}],
            coverage={"card": "complete", "relic": "complete", "potion": "complete"}, source="synthetic inspected inventory")

    def test_confirmed_consumption_invalidates_advice_without_guessing_energy(self):
        state = context([card("Strike")])["state"]
        self.db.record_advisory_snapshot(run_id=self.run, floor_id=self.floor, combat_id=self.combat,
            turn_id=self.turn, state=state, source="synthetic before frame")
        self.db.record_potion_use(run_id=self.run, floor_id=self.floor, combat_id=self.combat,
            turn_id=self.turn, item_name="Energy Potion", source="synthetic verified empty slot",
            observed_effect={"energy": 5})
        after = self.db.advisory_context(combat_id=self.combat)
        self.assertFalse(after["fresh"])
        self.assertEqual(3, after["state"]["energy"], "Event notes must not patch an old full snapshot")
        self.assertEqual(["Power Potion"], after["inventory"]["current"]["potion"])
        self.assertFalse(check_plan(after, {"steps": [play()]})["allowed"])
        state.update(observed_at=datetime.now(timezone.utc).isoformat(), energy=5)
        self.db.record_advisory_snapshot(run_id=self.run, floor_id=self.floor, combat_id=self.combat,
            turn_id=self.turn, state=state, source="synthetic after frame")
        fresh = self.db.advisory_context(combat_id=self.combat)
        self.assertTrue(fresh["fresh"])
        self.assertFalse(check_plan(fresh, {"steps": [{"kind": "potion", "name": "Energy Potion"}]})["allowed"])

    def test_observed_generated_card_stays_combat_local_and_unknown_movement_stays_unknown(self):
        self.db.start_combat_zones(combat_id=self.combat, deck=["Strike"], hand=["Strike"], source="synthetic known deck")
        self.db.record_potion_use(run_id=self.run, floor_id=self.floor, combat_id=self.combat,
            turn_id=self.turn, item_name="Power Potion", source="synthetic confirmed potion choice", observed_effect={"generated_card": "Berserk"})
        self.db.record_combat_zone_event(combat_id=self.combat, turn_id=self.turn, kind="generate",
            card_name="Berserk", to_zone="hand", source="synthetic observed generated card")
        zones = self.db.combat_zone_state(combat_id=self.combat)
        self.assertTrue(zones["known"])
        self.assertIn("Berserk", zones["zones"]["hand"])
        self.assertEqual(["Strike"], self.db.inventory_ledger(run_id=self.run)["current"]["card"])
        self.db.record_combat_zone_event(combat_id=self.combat, kind="unknown", source="synthetic obscured transition", reason="Unobserved discard/exhaust selection")
        self.db.record_combat_zone_event(combat_id=self.combat, kind="play", card_name="Strike", from_zone="hand", to_zone="discard", source="synthetic later visible movement")
        zones = self.db.combat_zone_state(combat_id=self.combat)
        self.assertFalse(zones["known"])
        self.assertEqual([], zones["eligible_discard_cards"])


if __name__ == "__main__":
    unittest.main()
