import contextlib
import copy
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts.veda_memory import main
from veda.telemetry_database import TelemetryDatabase


class InventoryDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "inventory.sqlite3"
        self.db = TelemetryDatabase(self.path)
        self.run = self.db.start_or_resume_run(ascension=2)
        self.floor = self.db.record_floor(run_id=self.run, act=1, floor=0, node_type="event", outcome=None)

    def baseline(self, items, coverage=None):
        return self.db.record_inventory_baseline(
            run_id=self.run, floor_id=self.floor, items=items,
            coverage=coverage or dict(card="complete", relic="complete", potion="complete"),
            source="Original inspected inventory", screenshot_path="original-evidence.png", confidence=.98,
        )

    def discover(self, items=None, categories=None, **overrides):
        request = dict(run_id=self.run, floor_id=self.floor, items=items if items is not None else [],
                       reviewed_categories=categories if categories is not None else {"card": "complete"},
                       reviewer="Fixture reviewer", reviewed=True, source="Inspected deck only",
                       screenshot_path="deck-evidence.png", confidence=.99)
        request.update(overrides)
        return self.db.record_inventory_discovery(**request)

    def ledger(self):
        return self.db.inventory_ledger(run_id=self.run)

    def test_deck_discovery_preserves_burning_blood_and_potion_provenance(self):
        self.baseline([
            {"kind": "relic", "item": "Burning Blood", "property": "Heal 6 HP after combat."},
            {"kind": "potion", "item": "Energy Potion", "property": "Gain 2 Energy."},
            {"kind": "card", "item": "Bash"},
        ])
        before = self.ledger()
        deck = ["Strike"] * 5 + ["Defend"] * 4 + ["Bash+"]
        discovery = self.discover([{"kind": "card", "item": card} for card in deck])
        after = self.ledger()
        self.assertEqual(deck, after["current"]["card"])
        for category in ("relic", "potion"):
            self.assertEqual(before["current_items"][category], after["current_items"][category])
            self.assertEqual(before["coverage"][category], after["coverage"][category])
        self.assertEqual(before["properties"], after["properties"])
        self.assertEqual({"card": "complete", "relic": "unknown", "potion": "unknown"},
                         json.loads(after["latest_baseline"]["coverage_json"]))
        self.assertEqual({"card"}, {row["item_kind"] for row in after["latest_baseline"]["items"]})
        self.assertTrue(all(row["baseline_id"] == discovery for row in after["current_items"]["card"]))
        self.assertEqual([], after["history"])

    def test_unobserved_potions_remain_unknown_not_verified_empty(self):
        self.discover([{"kind": "card", "item": "Strike"}])
        ledger = self.ledger()
        self.assertEqual({"card": "complete", "relic": "unknown", "potion": "unknown"}, ledger["coverage"])
        self.assertEqual([], ledger["current"]["potion"])

    def test_explicit_complete_empty_category_clears_only_that_category(self):
        self.baseline([{"kind": "card", "item": "Bash+"}, {"kind": "relic", "item": "Burning Blood"},
                       {"kind": "potion", "item": "Energy Potion"}])
        before = self.ledger()
        self.discover([], {"potion": "complete"}, source="All potion slots inspected empty")
        after = self.ledger()
        self.assertEqual([], after["current"]["potion"])
        self.assertEqual("complete", after["coverage"]["potion"])
        for category in ("card", "relic"):
            self.assertEqual(before["current_items"][category], after["current_items"][category])

    def test_partial_discovery_retains_duplicate_items_and_properties(self):
        self.baseline([{"kind": "card", "item": "Strike", "property": "Deal 6 damage."}] * 3)
        before = self.ledger()
        self.discover([{"kind": "card", "item": "Strike"}] * 2, {"card": "partial"})
        after = self.ledger()
        self.assertEqual(before["current_items"], after["current_items"])
        self.assertEqual(before["properties"], after["properties"])
        self.discover([{"kind": "card", "item": "Strike"}] * 4, {"card": "partial"})
        self.assertEqual(["Strike"] * 4, self.ledger()["current"]["card"])

    def test_uninspected_event_inventory_retains_event_provenance(self):
        event = self.db.record_inventory_event(run_id=self.run, item_kind="potion", action="acquired",
                                              item_name="Fire Potion", source="Earlier reward", confidence=.97)
        before = self.ledger()
        self.discover([{"kind": "card", "item": "Bash+"}])
        after = self.ledger()
        self.assertEqual(before["current_items"]["potion"], after["current_items"]["potion"])
        self.assertEqual(event, after["current_items"]["potion"][0]["event_id"])
        self.assertEqual(before["coverage"]["potion"], after["coverage"]["potion"])

    def test_multiple_reviewed_categories_leave_third_category_untouched(self):
        self.baseline([{"kind": "potion", "item": "Energy Potion"}])
        before = self.ledger()
        self.discover([{"kind": "card", "item": "Bash+"}, {"kind": "relic", "item": "Burning Blood"}],
                      {"card": "complete", "relic": "complete"})
        after = self.ledger()
        self.assertEqual(before["current_items"]["potion"], after["current_items"]["potion"])
        self.assertEqual(["Burning Blood"], after["current"]["relic"])

    def test_out_of_scope_items_reject_without_writing(self):
        before = self.ledger()
        for item in ({"kind": "relic", "item": "Burning Blood"}, {"kind": ["card"], "item": "Strike"}, "Strike"):
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "explicitly reviewed"):
                self.discover([item])
        self.assertEqual(before, self.ledger())

    def test_review_and_valid_category_coverage_are_required(self):
        before = self.ledger()
        for changes in ({"reviewed": False}, {"reviewed": 1}, {"reviewer": " "},
                        {"reviewed_categories": {}}, {"reviewed_categories": {"card": "unknown"}},
                        {"reviewed_categories": {"card": {}}}, {"reviewed_categories": {"currency": "complete"}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.discover(**changes)
        self.assertEqual(before, self.ledger())

    def test_full_baseline_compatibility_still_replaces_all_complete_categories(self):
        self.baseline([{"kind": "relic", "item": "Burning Blood"}, {"kind": "potion", "item": "Energy Potion"}])
        self.baseline([{"kind": "card", "item": "Strike"}])
        self.assertEqual({"card": ["Strike"], "relic": [], "potion": []}, self.ledger()["current"])

    def test_duplicate_relic_and_cross_run_floor_reject_without_writing(self):
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, "same relic twice"):
            self.discover([{"kind": "relic", "item": "Burning Blood"}] * 2, {"relic": "complete"})
        other = self.db.start_or_resume_run(ascension=1)
        with self.assertRaisesRegex(ValueError, "different run"):
            self.discover(run_id=other)
        self.assertEqual(before, self.ledger())

    def test_cli_uses_explicit_review_and_only_declared_category(self):
        self.baseline([{"kind": "relic", "item": "Burning Blood"}])
        original = copy.deepcopy(self.ledger()["current_items"]["relic"])
        items_file = Path(self.temp.name) / "deck.json"
        items_file.write_text(json.dumps({"items": [{"kind": "card", "item": "Strike"}] * 5}))
        args = ["veda_memory.py", "--database", str(self.path), "inventory-discover", "--run-id", self.run,
                "--floor-id", self.floor, "--items", "@" + str(items_file),
                "--categories", '{"card":"complete"}', "--reviewer", "Test operator", "--reviewed",
                "--source", "Deck inspection", "--screenshot", "inspected-deck.png"]
        output = io.StringIO()
        with patch("sys.argv", args), contextlib.redirect_stdout(output):
            self.assertEqual(0, main())
        after = self.ledger()
        self.assertEqual(output.getvalue().strip(), after["latest_baseline"]["id"])
        self.assertEqual(["Strike"] * 5, after["current"]["card"])
        self.assertEqual(original, after["current_items"]["relic"])
        self.assertIn("reviewed by Test operator: card", after["latest_baseline"]["source"])

    def test_cli_missing_review_does_not_write(self):
        before = self.ledger()
        args = ["veda_memory.py", "--database", str(self.path), "inventory-discover", "--run-id", self.run,
                "--items", '{"items":[]}', "--categories", '{"potion":"complete"}',
                "--reviewer", "Test operator", "--source", "Visible slots"]
        with patch("sys.argv", args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(2, error.exception.code)
        self.assertEqual(before, self.ledger())


if __name__ == "__main__":
    unittest.main()
