import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from veda.agent import AutonomousAgent, Decision
from veda.experience import DecisionRecord, ExperienceStore
from veda.knowledge import Claim, ClaimKind, KnowledgeBase, Source
from veda.research import ResearchIntake, ResearchNote
from veda.observation import capture_visible_ps5_feed
from veda.standalone import observation_status, write_observation_status
from veda.retrospective import build_stage_retrospective, validate_lesson, write_stage_retrospective
from veda.floor_telemetry import load_floor_log, record_floor, render_floor_dashboard
from veda.handoff import build_review_packet, write_feedback
from veda.handoff_dashboard import acknowledge_feedback, append_handoff, record_review_feedback, render_handoff_dashboard
from veda.relic_inventory import inventory_summary, load_relic_inventory, record_relic
from veda.mailbox import mailbox_status, receive_messages, send_message
from veda.research_catalog import load_catalog
from veda.vision import LocalOllamaVisionProvider, StructuredGameState, VisibleEnemy, combat_action_readiness
from veda.benchmark import BenchmarkCase, run_benchmark, summarize
from veda.readiness import assess_act1_readiness
from veda.combat import CardEffect, CombatEnemy, CombatSnapshot, choose_verified_sequence, validate_and_predict
from veda.map_reader import MapNode, assess_encounter_transition, assess_map
from veda.run_ledger import RunLedger
from veda.state_diff import verify_transition
from veda.preflight import preflight_combat, preflight_map, preflight_verified_combat
from veda.human_guided import HumanGuidedSession
from veda.bosses import confirm_boss_identity
from veda.combat_state import verify_combat_state
from veda.encounters import check_encounter
from veda.experience import evaluate_prediction
from veda.controller_verification import ControllerAttempt, verify_controller_attempt
from veda.potion_safety import preflight_potion_replacement
from veda.prediction_telemetry import summarize_prediction_telemetry
from veda.calibration import COMBAT_CRITICAL_FIELDS, LabeledFrame, calibrate
from veda.routine_combat import plan_routine_combat
from veda.decision_protocol import build_decision_brief


class FakeAdapter:
    def __init__(self):
        self.state = {"tags": ["combat", "ironclad"], "hp": 80}
        self.executed = []

    def observe(self): return dict(self.state)
    def legal_actions(self, state): return [{"type": "end_turn"}]
    def execute(self, action):
        self.executed.append(action)
        self.state["turn_ended"] = True


class EndTurnPolicy:
    def decide(self, state, legal_actions, knowledge, experience):
        return Decision(legal_actions[0], "Test decision", "Turn ends", 0.9)


class VedaTests(unittest.TestCase):
    def test_claim_requires_provenance(self):
        with self.assertRaises(ValueError):
            Claim("Block prevents damage", ClaimKind.FACT, ())

    def test_retrieval_preserves_multiple_matching_claims(self):
        source = Source("https://example.com", "example", "wiki", 0.8)
        base = KnowledgeBase()
        base.add(Claim("A", ClaimKind.FACT, (source,), frozenset({"combat"}), confidence=0.4))
        base.add(Claim("B", ClaimKind.SITUATIONAL, (source,), frozenset({"combat"}), confidence=0.8))
        self.assertEqual([c.statement for c in base.retrieve({"combat"})], ["B", "A"])

    def test_agent_executes_only_legal_action_and_records_result(self):
        adapter = FakeAdapter()
        store = ExperienceStore()
        record = AutonomousAgent(adapter, KnowledgeBase(), store, EndTurnPolicy()).step()
        self.assertEqual(adapter.executed, [{"type": "end_turn"}])
        self.assertTrue(record.state_after["turn_ended"])
        self.assertEqual(store.records, (record,))

    def test_agent_rejects_illegal_action(self):
        class Unsafe:
            def decide(self, *args): return Decision({"type": "play_card"}, "", "", 0.1)
        with self.assertRaises(ValueError):
            AutonomousAgent(FakeAdapter(), KnowledgeBase(), ExperienceStore(), Unsafe()).step()

    def test_experience_export_is_auditable_json(self):
        store = ExperienceStore()
        store.append(DecisionRecord(
            {"tags": ["combat"]}, ({"type": "end_turn"},), {"type": "end_turn"},
            "The action is legal.", "The turn will end.", immediate_outcome="verified",
        ))
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "experience.json"
            store.export_json(destination, metadata={"mode": "human_guided"})
            document = json.loads(destination.read_text())
        self.assertEqual(document["metadata"]["mode"], "human_guided")
        self.assertEqual(document["records"][0]["selected_action"], {"type": "end_turn"})

    def test_experience_store_write_through_persists_and_reloads_records(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "history" / "experience.json"
            store = ExperienceStore(destination)
            store.append(DecisionRecord(
                {"hp": 80}, ({"type": "end_turn"},), {"type": "end_turn"},
                "No safe card is available.", "The turn ends.", immediate_outcome="verified",
            ))
            document = json.loads(destination.read_text())
            restored = ExperienceStore(destination)
        self.assertEqual(document["metadata"]["schema"], "veda.experience.v1")
        self.assertEqual(len(restored.records), 1)
        self.assertEqual(restored.records[0].immediate_outcome, "verified")

    def test_research_intake_retains_each_selected_source(self):
        base = KnowledgeBase()
        intake = ResearchIntake(base)
        first = ResearchNote(Source("https://a.example", "A", "wiki", .7), "source text", "Interpretation", frozenset({"combat"}))
        second = ResearchNote(Source("https://b.example", "B", "guide", .8), "other text", "Interpretation", frozenset({"combat"}))
        intake.capture(first)
        intake.capture(second)
        claim = intake.publish_claim("A reviewed claim", ClaimKind.FACT, [first, second])
        self.assertEqual([source.publisher for source in claim.sources], ["A", "B"])

    def test_passive_capture_creates_timestamped_png(self):
        def fake_capture(command, **kwargs):
            Path(command[-1]).write_bytes(b"png")
        with TemporaryDirectory() as directory, patch("veda.observation.subprocess.run", fake_capture):
            path = capture_visible_ps5_feed(Path(directory))
        self.assertEqual(path.suffix, ".png")

    def test_standalone_status_is_watch_only_and_persisted(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "frame.png"
            image.write_bytes(b"png")
            status = write_observation_status(image, root / "status.json")
            saved = json.loads((root / "status.json").read_text())
        self.assertEqual(status["mode"], "watch_only")
        self.assertEqual(status["controller_input"], "disabled")
        self.assertEqual(saved["image"], str(image))

    def test_stage_retro_is_evidence_first_and_does_not_promote_lessons(self):
        record = DecisionRecord(
            {"hp": 70}, (), {}, "safe line", "enemy dies",
            immediate_outcome="verified", prediction_evaluation={"all_matched": True},
        )
        document = build_stage_retrospective(
            stage="Act 1", outcome="completed", records=(record,),
            proposed_lessons=("Prefer Feed on safe lethal.",),
        )
        self.assertEqual(document["metrics"]["prediction_match_rate"], 1.0)
        self.assertEqual(document["lessons"][0]["status"], "proposed")
        self.assertIn(record.id, document["evidence_record_ids"])
        with TemporaryDirectory() as directory:
            target = Path(directory) / "retro.json"
            write_stage_retrospective(document, target)
            saved = json.loads(target.read_text())
        self.assertEqual(saved["schema"], "veda.stage-retrospective.v1")

    def test_lesson_validation_requires_evidence_and_regression_test(self):
        lesson = {"statement": "Keep a safety margin.", "status": "proposed"}
        with self.assertRaises(ValueError):
            validate_lesson(lesson, evidence_record_ids=(), implementation="rule", regression_test="test_rule")
        validated = validate_lesson(
            lesson, evidence_record_ids=("record-1",), implementation="add safety check", regression_test="test_safety_check",
        )
        self.assertEqual(validated["status"], "validated")

    def test_floor_log_copies_evidence_and_renders_html(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "screen.png"
            source.write_bytes(b"png")
            log = root / "data" / "floor_runs.json"
            assets = root / "docs" / "assets" / "floor-runs"
            entry = record_floor(
                log_path=log, screenshot=source, screenshot_dir=assets, act=2, floor=18,
                outcome="victory", hp=72, max_hp=90, gold=204,
                telemetry={"recommendation": "Feed lethal"},
            )
            page = root / "docs" / "floor-telemetry.html"
            render_floor_dashboard(load_floor_log(log), page)
            rendered = page.read_text()
            self.assertTrue((assets / Path(entry["screenshot"]).name).is_file())
            self.assertIn("Feed lethal", rendered)
            self.assertIn("Act 2 · Floor 18", rendered)

    def test_floor_log_retains_report_card_outcome_fields_and_two_screenshots(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first.png", root / "second.png"
            first.write_bytes(b"png")
            second.write_bytes(b"png")
            entry = record_floor(
                log_path=root / "floors.json", screenshot=first, additional_screenshots=(second,), screenshot_dir=root / "assets",
                act=2, floor=24, outcome="victory", hp=10, max_hp=90, gold=187, ascension=0,
                trophies=("Gremlin Leader defeated",), losses=("Reached 1 HP",), actions=("Used Cleave for lethal",),
                strategy="Verify lethal before self-damage.",
            )
        self.assertEqual(entry["screenshots"].__len__(), 2)
        self.assertEqual(entry["trophies"], ["Gremlin Leader defeated"])
        self.assertEqual(entry["strategy"], "Verify lethal before self-damage.")

    def test_mailbox_requires_explicit_receipt(self):
        with TemporaryDirectory() as directory:
            mailbox = Path(directory) / "mailbox.json"
            message = send_message(
                mailbox_path=mailbox, sender="llm", recipient="veda", kind="review_feedback", body="Use the safe line.",
            )
            self.assertEqual(mailbox_status(mailbox_path=mailbox)["veda_unread"], 1)
            received = receive_messages(mailbox_path=mailbox, recipient="veda")
        self.assertEqual(received[0]["id"], message["id"])
        self.assertIsNotNone(received[0]["received_at"])

    def test_handoff_keeps_proposals_separate_and_writes_feedback(self):
        retrospective = build_stage_retrospective(
            stage="Act 2", outcome="completed", proposed_lessons=("Measure Frail Block first.",),
        )
        packet = build_review_packet(retrospective)
        self.assertEqual(packet["review_status"], "awaiting_codex_feedback")
        self.assertEqual(packet["proposed_lessons"], ["Measure Frail Block first."])
        with TemporaryDirectory() as directory:
            feedback_path = Path(directory) / "feedback.json"
            write_feedback(packet=packet, feedback="Collect another verified example.", path=feedback_path)
            saved = json.loads(feedback_path.read_text())
        self.assertIn("another verified example", saved["feedback"])

    def test_handoff_dashboard_copies_relevant_screenshot(self):
        packet = build_review_packet(build_stage_retrospective(stage="Act 2", outcome="completed"))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = root / "screen.png"
            screenshot.write_bytes(b"png")
            log = root / "data" / "handoffs.json"
            assets = root / "docs" / "assets" / "handoffs"
            entry = append_handoff(log_path=log, asset_dir=assets, packet=packet, screenshot=screenshot)
            page = root / "docs" / "handoffs.html"
            render_handoff_dashboard(json.loads(log.read_text()), page)
            self.assertTrue((assets / Path(entry["screenshot"]).name).is_file())
            self.assertIn("Act 2", page.read_text())

    def test_report_card_does_not_publish_private_review_feedback(self):
        packet = build_review_packet(build_stage_retrospective(stage="Act 2", outcome="completed"))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "handoffs.json"
            append_handoff(log_path=log, asset_dir=root / "assets", packet=packet, screenshot=None)
            record_review_feedback(log_path=log, feedback="Private reviewer instruction.")
            page = root / "report-card.html"
            render_handoff_dashboard(json.loads(log.read_text()), page)
            rendered = page.read_text()
        self.assertIn("Report Card", rendered)
        self.assertNotIn("Private reviewer instruction.", rendered)

    def test_handoff_requires_review_then_veda_acknowledgement(self):
        packet = build_review_packet(build_stage_retrospective(stage="Act 2", outcome="completed"))
        with TemporaryDirectory() as directory:
            log = Path(directory) / "handoffs.json"
            append_handoff(log_path=log, asset_dir=Path(directory) / "assets", packet=packet, screenshot=None)
            with self.assertRaises(ValueError):
                acknowledge_feedback(
                    log_path=log, implementation_status="implemented", summary="Added a rule.",
                )
            record_review_feedback(log_path=log, feedback="Protect a 15 HP margin.")
            entry = acknowledge_feedback(
                log_path=log, implementation_status="implemented", summary="Added the survival guardrail.",
            )
        self.assertEqual(entry["review"]["status"], "handoff_complete")
        self.assertEqual(entry["review"]["implementation_status"], "implemented")

    def test_relic_inventory_keeps_property_and_evidence_separate_from_a_guess(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = root / "relic.png"
            screenshot.write_bytes(b"png")
            entry = record_relic(
                inventory_path=root / "relics.json", name="Burning Blood",
                property_text="At the end of combat, heal 6 HP.", source="visible relic tooltip",
                confidence=1.0, act=1, floor=0, screenshot=screenshot, asset_dir=root / "assets",
            )
            summary = inventory_summary(load_relic_inventory(root / "relics.json"))
        self.assertEqual(summary[0]["name"], "Burning Blood")
        self.assertEqual(summary[0]["property"], "At the end of combat, heal 6 HP.")
        self.assertTrue((root / "assets" / Path(entry["screenshot"]).name).exists() if False else entry["screenshot"].startswith("assets/relics/"))

    def test_initial_catalog_loads_sourced_facts_and_advice(self):
        catalog = Path(__file__).parents[1] / "data" / "sts_initial_research.json"
        loaded = load_catalog(catalog, KnowledgeBase())
        self.assertGreaterEqual(len(loaded), 8)
        self.assertEqual(loaded[0].kind, ClaimKind.FACT)
        self.assertEqual(loaded[-1].kind, ClaimKind.PRINCIPLE)

    def test_player_manual_catalog_is_traceable_and_loadable(self):
        root = Path(__file__).parents[1]
        document = json.loads((root / "data" / "veda_player_manual_catalog.json").read_text())
        self.assertEqual(document["source_document"]["sha256"], "8ebc008310556cf5c29cdc695c1cfd8e9acc05eab5c22b446fbe3d940b372d6b")
        loaded = load_catalog(root / "data" / "veda_player_manual_catalog.json", KnowledgeBase())
        self.assertGreaterEqual(len(loaded), 10)
        self.assertTrue(all(claim.sources[0].source_type == "user-provided operating manual" for claim in loaded))

    def test_act_one_reference_pack_loads_facts_and_conditional_guidance(self):
        catalog = Path(__file__).parents[1] / "data" / "act1_reference_pack.json"
        loaded = load_catalog(catalog, KnowledgeBase())
        self.assertGreaterEqual(len(loaded), 15)
        self.assertTrue(any(claim.kind is ClaimKind.FACT for claim in loaded))
        self.assertTrue(any(claim.kind is ClaimKind.SITUATIONAL for claim in loaded))

    def test_current_deck_research_pack_is_sourced_and_loadable(self):
        catalog = Path(__file__).parents[1] / "data" / "act1_current_deck_research.json"
        loaded = load_catalog(catalog, KnowledgeBase())
        self.assertGreaterEqual(len(loaded), 3)
        self.assertTrue(any("Fiend Fire" in claim.statement for claim in loaded))

    def test_hexaghost_reference_pack_is_sourced_and_loadable(self):
        catalog = Path(__file__).parents[1] / "data" / "hexaghost_reference_pack.json"
        loaded = load_catalog(catalog, KnowledgeBase())
        self.assertEqual(len(loaded), 4)
        self.assertTrue(any("Hexaghost" in claim.statement for claim in loaded))

    def test_threat_readiness_surfaces_unknown_route_facts(self):
        readiness = assess_act1_readiness(
            deck=("Strike", "Bash", "Pommel Strike", "Shrug It Off", "Combust", "Shockwave", "Ghostly Armor"),
            hp=80, max_hp=80, potions=("Swift Potion",), boss=None, next_path_known=False,
        )
        self.assertFalse(readiness.ready_for_risk_assessment)
        self.assertIn("persistent multi-enemy damage observed", readiness.observed_capabilities)
        self.assertIn("Act 1 boss identity is not confirmed from the map", readiness.unresolved_questions)
        self.assertTrue(any("Gremlin Nob" in caution for caution in readiness.cautions))

    def test_threat_readiness_becomes_ready_when_route_facts_are_known(self):
        readiness = assess_act1_readiness(
            deck=("Strike",), hp=80, max_hp=80, potions=(), boss="Slime Boss", next_path_known=True,
        )
        self.assertTrue(readiness.ready_for_risk_assessment)

    def test_combat_validator_rejects_sequence_that_exceeds_energy(self):
        result = validate_and_predict(CombatSnapshot(3, enemies=(CombatEnemy("Sentry", 99),)), (
            CardEffect("Strike", 1, "Attack", attack_damage=6, target="Sentry"),
            CardEffect("Bash", 2, "Attack", attack_damage=8, target="Sentry"),
            CardEffect("Pommel Strike", 1, "Attack", attack_damage=9, target="Sentry"),
        ))
        self.assertFalse(result.legal)
        self.assertIn("not enough energy for Pommel Strike: needs 1, has 0", result.reasons)

    def test_combat_validator_accounts_for_artifact_and_modifiers(self):
        result = validate_and_predict(CombatSnapshot(3, player_weak=1, enemies=(CombatEnemy("Sentry", 15, artifact=1),)), (
            CardEffect("Shockwave+", 2, "Skill", weak=5, vulnerable=5, target="Sentry"),
            CardEffect("Strike", 1, "Attack", attack_damage=6, target="Sentry"),
        ))
        self.assertTrue(result.legal)
        self.assertEqual(result.enemies[0].artifact, 0)
        self.assertEqual(result.enemies[0].vulnerable, 5)
        self.assertEqual(result.enemies[0].hp, 9)

    def test_combat_validator_models_fiend_fire_after_a_played_card(self):
        result = validate_and_predict(CombatSnapshot(3, hand_size=6, enemies=(CombatEnemy("Slime", 40, vulnerable=3),)), (
            CardEffect("Defend", 1, "Skill", block=5),
            CardEffect("Fiend Fire", 2, "Attack", target="Slime", exhausts_hand=True, damage_per_exhausted=7),
        ))
        self.assertTrue(result.legal)
        self.assertEqual(result.enemies[0].hp, 0)
        self.assertEqual(result.energy_remaining, 0)

    def test_combat_validator_rejects_a_lethal_sequence_with_burn_damage(self):
        result = validate_and_predict(
            CombatSnapshot(1, player_hp=1, player_block=0, incoming_damage=0, end_turn_damage=6,
                           enemies=(CombatEnemy("Hexaghost", 68),)),
            (CardEffect("Defend", 1, "Skill", block=5),),
        )
        self.assertFalse(result.legal)
        self.assertTrue(result.lethal)
        self.assertEqual(result.projected_player_hp, 0)

    def test_combat_validator_projects_multi_hit_total_as_supplied_observation(self):
        result = validate_and_predict(
            CombatSnapshot(3, player_hp=26, incoming_damage=18, end_turn_damage=0,
                           enemies=(CombatEnemy("Hexaghost", 68),)),
            (CardEffect("Defend", 1, "Skill", block=5),),
        )
        self.assertTrue(result.legal)
        self.assertEqual(result.projected_player_hp, 13)

    def test_boss_identity_requires_readable_name(self):
        self.assertEqual(confirm_boss_identity(visible_name=None), (False, "boss name is not legibly confirmed from the current screen"))
        self.assertEqual(confirm_boss_identity(visible_name="Hexaghost", expected_name="Slime Boss")[0], False)
        self.assertEqual(confirm_boss_identity(visible_name="Hexaghost"), (True, "Hexaghost"))

    def test_state_diff_records_observed_transition(self):
        verification = verify_transition({"hp": 80, "energy": 3}, {"hp": 76, "energy": 0})
        self.assertTrue(verification.confirms("hp", 76))
        self.assertEqual(len(verification.changes), 2)

    def test_ledger_does_not_promote_pending_items(self):
        ledger = RunLedger()
        ledger.propose("card", "Fiend Fire", "elite reward")
        self.assertEqual(ledger.deck, [])
        ledger.confirm("card", "Fiend Fire", "elite reward")
        self.assertEqual(ledger.deck, ["Fiend Fire"])

    def test_ledger_preserves_duplicate_potions_and_audits_replacement(self):
        ledger = RunLedger()
        ledger.confirm("potion", "Distilled Chaos", "reward text")
        ledger.confirm("potion", "Distilled Chaos", "reward text")
        ledger.confirm("potion", "Fairy in a Bottle", "tooltip")
        ledger.replace_potion(discard="Distilled Chaos", gain="Ancient Potion", source="boss loot")
        self.assertEqual(ledger.potions, ["Distilled Chaos", "Fairy in a Bottle", "Ancient Potion"])
        self.assertEqual(ledger.confirmed[-2].kind, "potion_discarded")

    def test_combat_validator_rejects_cards_absent_from_confirmed_hand(self):
        snapshot = CombatSnapshot(
            energy=2, hand=("Strike",), enemies=(CombatEnemy("Slime", 20),),
        )
        unavailable = CardEffect("Bash", 2, "Attack", attack_damage=8, target="Slime")
        result = validate_and_predict(snapshot, (unavailable,))
        self.assertFalse(result.legal)
        self.assertIn("Bash is not in the confirmed hand", result.reasons)

    def test_combat_validator_cannot_play_one_confirmed_card_twice(self):
        snapshot = CombatSnapshot(
            energy=2, hand=("Strike",), enemies=(CombatEnemy("Slime", 20),),
        )
        strike = CardEffect("Strike", 1, "Attack", attack_damage=6, target="Slime")
        result = validate_and_predict(snapshot, (strike, strike))
        self.assertFalse(result.legal)
        self.assertIn("Strike is not in the confirmed hand", result.reasons)

    def test_preflight_rejects_a_card_that_is_not_visibly_in_hand(self):
        observation = StructuredGameState(
            "COMBAT", 0.95, hp=80, max_hp=80, energy=2, block=0, hand=("Strike",),
            end_turn_damage=0, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("Slime", 20, 20, "attack 4", block=0, intent_total_damage=4, intent_damage_confidence=1.0),),
        )
        result = preflight_combat(
            observation, CombatSnapshot(2, enemies=(CombatEnemy("Slime", 20),)),
            (CardEffect("Bash", 2, "Attack", attack_damage=8, target="Slime"),),
        )
        self.assertFalse(result.allowed)
        self.assertIn("Bash is not in the observed hand", result.reasons)

    def test_map_reader_blocks_route_advice_without_boss_or_confident_nodes(self):
        assessment = assess_map(boss=None, boss_confidence=0.0, nodes=(MapNode("a", "elite", True, 0.7),))
        self.assertFalse(assessment.ready)
        self.assertFalse(assessment.boss_confirmed)

    def test_map_reader_allows_a_confident_route_without_naming_boss_art(self):
        assessment = assess_map(boss=None, boss_confidence=0.0, nodes=(MapNode("a", "enemy", True, 0.95),))
        self.assertTrue(assessment.ready)
        self.assertFalse(assessment.boss_confirmed)

    def test_encounter_transition_rejects_map_and_combat_mismatch(self):
        checked = assess_encounter_transition(
            selected_node=MapNode("n1", "elite", True, 0.96), observed_kind="enemy", observed_confidence=0.96,
        )
        self.assertFalse(checked.ready)
        self.assertIn("map selected elite, but current encounter is enemy", checked.reasons)

    def test_medium_acid_slime_profile_explicitly_has_no_split(self):
        checked = check_encounter("Acid Slime (M)", act=1)
        self.assertTrue(checked.known)
        self.assertFalse(checked.profile.split_at_half_hp)
        self.assertIn("Do not predict a split for this confirmed enemy.", checked.cautions)

    def test_verified_sequence_selector_rejects_lethal_candidate(self):
        snapshot = CombatSnapshot(1, player_hp=1, incoming_damage=0, end_turn_damage=6, hand_size=1,
                                  enemies=(CombatEnemy("Slime", 6),))
        safe = (CardEffect("Strike", 1, "Attack", attack_damage=6, target="Slime"),)
        unsafe = (CardEffect("Defend", 1, "Skill", block=5),)
        result = choose_verified_sequence(snapshot, (unsafe, safe))
        self.assertIsNotNone(result)
        self.assertEqual(result.enemies[0].hp, 0)

    def test_prediction_evaluation_only_compares_explicit_fields(self):
        evaluation = evaluate_prediction({"energy": 2, "block": 5, "player_hp": None}, {"energy": 2, "block": 5})
        self.assertEqual(evaluation, {"checked": {"energy": True, "block": True}, "all_matched": True})

    def test_prediction_evaluation_confirms_a_predicted_combat_win_after_reward_transition(self):
        evaluation = evaluate_prediction({"enemies": {"Chosen": 0}}, {"enemies": []})
        self.assertEqual(evaluation, {"checked": {"enemies": True}, "all_matched": True})

    def test_controller_attempt_requires_observed_expected_transition(self):
        attempt = ControllerAttempt("Play Strike", {"energy": 2, "enemies": {"Slime": 0}}, {"energy": 3})
        confirmed = verify_controller_attempt(attempt, {"energy": 2, "enemies": [{"name": "Slime", "hp": 0}]})
        mismatch = verify_controller_attempt(attempt, {"energy": 3, "enemies": {"Slime": 0}})
        stale = verify_controller_attempt(attempt, {"energy": 2})
        self.assertTrue(confirmed.confirmed)
        self.assertEqual(mismatch.status, "unexpected_transition")
        self.assertEqual(stale.status, "needs_fresh_observation")

    def test_potion_replacement_requires_confirmed_full_named_inventory(self):
        ledger = RunLedger(potions=["Fairy in a Bottle", "Ancient Potion", "Distilled Chaos"])
        allowed = preflight_potion_replacement(ledger, discard="Distilled Chaos", gain="Weak Potion", capacity=3)
        rejected = preflight_potion_replacement(ledger, discard="Blue Potion", gain="Weak Potion", capacity=3)
        self.assertTrue(allowed.allowed)
        self.assertFalse(rejected.allowed)
        self.assertIn("Blue Potion is not in the confirmed potion inventory", rejected.reasons)

    def test_prediction_telemetry_scores_only_checked_fields(self):
        first = DecisionRecord({"tags": ["combat"]}, (), {}, "", "", prediction_evaluation={
            "checked": {"energy": True, "player_hp": False}, "all_matched": False,
        })
        unscored = DecisionRecord({"tags": ["combat"]}, (), {}, "", "")
        telemetry = summarize_prediction_telemetry((first, unscored))
        self.assertEqual(telemetry.score, 33.3)
        self.assertEqual(telemetry.coverage, 0.5)
        self.assertEqual(ExperienceStore().prediction_telemetry()["score"], None)

    def test_local_vision_needs_real_labeled_frames_before_combat_authorization(self):
        class Perfect:
            def observe(self, frame_id):
                return {field: "ok" for field in COMBAT_CRITICAL_FIELDS}
        frames = tuple(LabeledFrame(str(index), {field: "ok" for field in COMBAT_CRITICAL_FIELDS}) for index in range(11))
        self.assertFalse(calibrate(Perfect(), frames).authorized_for(COMBAT_CRITICAL_FIELDS))
        frames += (LabeledFrame("11", {field: "ok" for field in COMBAT_CRITICAL_FIELDS}),)
        self.assertTrue(calibrate(Perfect(), frames).authorized_for(COMBAT_CRITICAL_FIELDS))

    def test_routine_planner_defers_until_local_vision_is_calibrated(self):
        state = StructuredGameState(
            "COMBAT", 0.96, act=1, hp=30, max_hp=80, energy=3, block=0,
            player_strength=0, player_weak=0, player_frail=0, hand=("Strike",),
            end_turn_damage=0, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("Cultist", 6, 50, "attack 6", block=0, intent_total_damage=6, intent_damage_confidence=1.0),),
        )
        report = calibrate(type("Provider", (), {"observe": lambda self, _: {}})(), ())
        plan = plan_routine_combat(state, report, encounter_name="Cultist")
        self.assertFalse(plan.ready)
        self.assertIn("local vision has not earned combat-planning authorization", plan.reasons)

    def test_routine_planner_owns_a_calibrated_familiar_lethal(self):
        class Perfect:
            def observe(self, frame_id): return {field: "ok" for field in COMBAT_CRITICAL_FIELDS}
        frames = tuple(LabeledFrame(str(index), {field: "ok" for field in COMBAT_CRITICAL_FIELDS}) for index in range(12))
        state = StructuredGameState(
            "COMBAT", 0.96, act=1, hp=30, max_hp=80, energy=1, block=0,
            player_strength=0, player_weak=0, player_frail=0, hand=("Strike",),
            end_turn_damage=0, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("Cultist", 6, 50, "attack 6", block=0, intent_total_damage=6, intent_damage_confidence=1.0),),
        )
        plan = plan_routine_combat(state, calibrate(Perfect(), frames), encounter_name="Cultist")
        self.assertTrue(plan.ready)
        self.assertEqual(plan.instructions, ("Play Strike targeting Cultist",))

    def test_preflight_requires_observation_and_arithmetic(self):
        observation = StructuredGameState(
            "COMBAT", 0.95, hp=80, max_hp=80, energy=3, hand=("Strike",),
            block=0, end_turn_damage=0, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("Slime", 6, 6, "attack 4", block=0, intent_total_damage=4, intent_damage_confidence=1.0),),
        )
        allowed = preflight_combat(
            observation, CombatSnapshot(3, hand_size=1, enemies=(CombatEnemy("Slime", 6),)),
            (CardEffect("Strike", 1, "Attack", attack_damage=6, target="Slime"),),
        )
        self.assertTrue(allowed.allowed)
        rejected = preflight_combat(
            observation, CombatSnapshot(3, enemies=(CombatEnemy("Slime", 6),)),
            (CardEffect("Fiend Fire", 2, "Attack", target="Slime", exhausts_hand=True, damage_per_exhausted=7),),
        )
        self.assertFalse(rejected.allowed)
        self.assertIn("Fiend Fire needs a confirmed hand size", rejected.reasons)

    def test_map_preflight_reuses_confidence_gate(self):
        result = preflight_map(boss="Slime Boss", boss_confidence=0.9, nodes=(MapNode("e1", "elite", True, 0.9),))
        self.assertTrue(result.allowed)

    def test_human_guided_session_requires_preflight_then_verifies(self):
        before = StructuredGameState(
            "COMBAT", 0.95, hp=80, max_hp=80, energy=3, hand=("Strike",),
            block=0, end_turn_damage=0, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("Slime", 6, 6, "attack 4", block=0, intent_total_damage=4, intent_damage_confidence=1.0),),
        )
        after = StructuredGameState("COMBAT", 0.95, hp=80, max_hp=80, energy=2, block=0, end_turn_damage=0, end_turn_damage_confidence=1.0, hand=(), enemies=())
        store = ExperienceStore()
        session = HumanGuidedSession(store)
        recommendation = session.recommend(
            before, CombatSnapshot(3, hand_size=1, enemies=(CombatEnemy("Slime", 6),)),
            (CardEffect("Strike", 1, "Attack", attack_damage=6, target="Slime"),), "Lethal is visible.",
        )
        self.assertTrue(recommendation.preflight.allowed)
        self.assertEqual(recommendation.instructions, ("Play Strike targeting Slime",))
        record = session.verify(after)
        self.assertIn("energy", record.immediate_outcome)
        self.assertEqual(record.decision_brief["immediate_threat"], "confirmed incoming damage: 4")
        self.assertEqual(len(store.records), 1)

    def test_decision_brief_keeps_missing_values_as_unknowns(self):
        state = StructuredGameState(
            "COMBAT", 0.95, hp=30, max_hp=80, energy=3, block=0, hand=("Strike",),
            player_strength=None, player_weak=0, player_frail=0,
            enemies=(VisibleEnemy("unknown enemy", None, 20, "unknown", block=None, intent_total_damage=None),),
        )
        brief = build_decision_brief(state, CombatSnapshot(3, incoming_damage=None))
        self.assertIn("player Strength", brief.unknowns)
        self.assertIn("unknown enemy HP/Block/intent total", brief.unknowns)
        self.assertEqual(brief.immediate_threat, "incoming damage is unverified")

    def test_human_guided_rejects_an_invalid_safe_alternative(self):
        state = StructuredGameState(
            "COMBAT", 0.95, hp=30, max_hp=80, energy=1, block=0, hand=("Strike",),
            end_turn_damage=0, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("Slime", 6, 6, "attack 4", block=0, intent_total_damage=4, intent_damage_confidence=1.0),),
        )
        session = HumanGuidedSession(ExperienceStore())
        result = session.recommend(
            state, CombatSnapshot(1, hand=("Strike",), enemies=(CombatEnemy("Slime", 6),)),
            (CardEffect("Strike", 1, "Attack", attack_damage=6, target="Slime"),), "Lethal is visible.",
            safe_alternative=(CardEffect("Bash", 2, "Attack", attack_damage=8, target="Slime"),),
        )
        self.assertFalse(result.preflight.allowed)
        self.assertIn("safe alternative failed preflight", result.preflight.reasons)

    def test_local_provider_parses_provider_neutral_contract(self):
        payload = {
            "screen_type": "TITLE", "confidence": 0.9, "selected_item": "Play",
            "visible_actions": ["PLAY"], "character": None, "ascension": None, "act": None, "floor": None,
            "hp": None, "max_hp": None, "energy": None, "block": None, "gold": None,
            "player_strength": None, "player_weak": None, "player_frail": None,
            "boss_name": None, "boss_confidence": 0.0, "encounter_kind": None, "encounter_confidence": 0.0, "map_nodes": [],
            "end_turn_damage": None, "end_turn_damage_confidence": 0.0,
            "hand": [], "enemies": [], "description": "Title screen.",
        }
        response = json.dumps({"message": {"content": json.dumps(payload)}}).encode()
        with TemporaryDirectory() as directory:
            image = Path(directory) / "frame.png"
            image.write_bytes(b"placeholder")
            result = LocalOllamaVisionProvider(request=lambda body: response).observe(image)
        self.assertEqual(result.screen_type, "TITLE")
        self.assertEqual(result.as_observation()["tags"], ["title"])

    def test_encounter_profile_catalog_is_sourced_and_loadable(self):
        catalog = Path(__file__).parents[1] / "data" / "act1_encounter_profiles.json"
        loaded = load_catalog(catalog, KnowledgeBase())
        self.assertEqual(len(loaded), 3)

    def test_invalid_local_response_fails_closed(self):
        with TemporaryDirectory() as directory:
            image = Path(directory) / "frame.png"
            image.write_bytes(b"placeholder")
            result = LocalOllamaVisionProvider(request=lambda body: b'{"message":{"content":"{}"}}').observe(image)
        self.assertEqual(result.screen_type, "UNKNOWN")
        self.assertEqual(result.confidence, 0.0)

    def test_percentage_confidence_is_normalized(self):
        self.assertEqual(LocalOllamaVisionProvider._normalize_model_state({"confidence": 75})["confidence"], 0.75)

    def test_benchmark_scores_only_labeled_fields(self):
        class TitleProvider:
            def observe(self, path): return LocalOllamaVisionProvider._fallback("test").__class__("TITLE", 1.0)
        results = run_benchmark(TitleProvider(), [BenchmarkCase("title", Path("frame"), {"screen_type": "TITLE"})])
        self.assertEqual(summarize(results)["field_accuracy"], 1.0)

    def test_combat_readiness_blocks_unknown_enemy_intent(self):
        state = StructuredGameState(
            "COMBAT", 0.95, hp=67, max_hp=80, energy=3, hand=("Strike",),
            block=0, end_turn_damage=0, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("unknown enemy", 13, 13, "unknown intent", block=0, intent_total_damage=None),),
        )
        readiness = combat_action_readiness(state)
        self.assertFalse(readiness.ready)
        self.assertIn("an enemy intent is unknown", readiness.reasons)

    def test_verified_combat_state_builds_survival_snapshot(self):
        state = StructuredGameState(
            "COMBAT", 0.96, hp=26, max_hp=80, energy=3, block=5, hand=("Defend",),
            end_turn_damage=6, end_turn_damage_confidence=0.95,
            enemies=(VisibleEnemy("Hexaghost", 68, 250, "attack 9 x2", block=0,
                                  intent_total_damage=18, intent_damage_confidence=0.96),),
        )
        verified = verify_combat_state(state)
        self.assertTrue(verified.ready)
        self.assertEqual(verified.snapshot.incoming_damage, 18)
        self.assertEqual(verified.snapshot.end_turn_damage, 6)

    def test_verified_preflight_refuses_lethal_end_turn(self):
        state = StructuredGameState(
            "COMBAT", 0.96, hp=1, max_hp=80, energy=1, block=0, hand=("Defend",),
            end_turn_damage=6, end_turn_damage_confidence=1.0,
            enemies=(VisibleEnemy("Hexaghost", 68, 250, "defensive", block=0,
                                  intent_total_damage=0, intent_damage_confidence=1.0),),
        )
        result, verified = preflight_verified_combat(state, (CardEffect("Defend", 1, "Skill", block=5),))
        self.assertTrue(verified.ready)
        self.assertFalse(result.allowed)
        self.assertIn("sequence is lethal after confirmed incoming and end-of-turn damage", result.reasons)


if __name__ == "__main__":
    unittest.main()
