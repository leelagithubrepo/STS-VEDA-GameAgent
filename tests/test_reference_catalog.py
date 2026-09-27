"""Regression checks for source import/identity boundaries, not game certification."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from veda.reference_library import build_library, ReferenceLibrary, ROOT


class ReferenceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.database = Path(cls.temp.name) / "references.sqlite3"
        cls.coverage = build_library(sorted((ROOT / "data/spire_reference").glob("*.json")), cls.database)

    def test_complete_pinned_manifests_are_distinguished_from_upgrade_references(self):
        self.assertEqual({"base": 370, "first_upgrade_references": 352, "other_or_unspecified": 0},
                         self.coverage["card_identity_counts"])
        for kind, expected in {"relic": 180, "potion": 42, "enemy": 65, "ascension": 21}.items():
            self.assertEqual(expected, self.coverage["counts"][kind])
        self.assertFalse(self.coverage["all_game_content_verified"])
        self.assertFalse(self.coverage["runtime_authorized"])

    def test_obsolete_and_placeholder_entities_are_not_available_to_lookup(self):
        with ReferenceLibrary(self.database) as library:
            for name in ("Impulse", "Unraveling", "Discerning Monocle", "Potion Slot", "INVALID"):
                with self.subTest(name=name):
                    self.assertEqual("missing", library.lookup(name)["match"])
            self.assertEqual("exact", library.lookup("Entropic Brew", kind="potion")["match"])

    def test_generated_and_special_card_variants_are_explicit(self):
        with ReferenceLibrary(self.database) as library:
            for name in ("Shiv", "Miracle", "Beta", "Omega", "Expunger", "Insight", "Safety", "Smite", "Through Violence"):
                self.assertEqual("exact", library.lookup(name, kind="card")["match"])
            for name in ("True Grit+", "Barricade+", "Burn+", "Become Almighty+"):
                result = library.lookup(name, kind="card")
                self.assertEqual("exact", result["match"])
                self.assertEqual("first_upgrade", result["entries"][0]["facts"]["requested_variant"])
            self.assertEqual("missing", library.lookup("Shame+")["match"])
            self.assertEqual("missing", library.lookup("Searing Blow+2")["match"])
            result = library.lookup("Strike", kind="card")
            self.assertEqual("ambiguous", result["match"])
            self.assertEqual(4, len(result["entries"]))
            self.assertEqual("exact", library.lookup("Strike", kind="card", character="ironclad")["match"])

    def test_conflicts_survive_normalization_and_are_not_presented_as_executable_truth(self):
        with ReferenceLibrary(self.database) as library:
            for name, kind in (("Blood Potion", "potion"), ("Fairy in a Bottle", "potion"), ("Awakened One", "enemy")):
                result = library.lookup(name, kind=kind)
                self.assertEqual("source_conflict", result["entries"][0]["evidence_status"])
                self.assertTrue(result["entries"][0]["unknowns"])
                self.assertTrue(result["entries"][0]["facts"]["source_conflicts"])
                self.assertFalse(result["runtime_authorized"])
            awakened = library.lookup("Awakened One", kind="enemy")["entries"][0]
            self.assertIsNone(awakened["facts"]["hp"]["reference_values"])

    def test_every_entity_has_an_effect_or_behavior_reference_and_resolvable_sources(self):
        with ReferenceLibrary(self.database) as library:
            for row in library.db.execute("SELECT body FROM entries WHERE kind IN ('card','relic','potion','enemy')"):
                entry = json.loads(row[0])
                with self.subTest(entry=entry["id"]):
                    self.assertTrue(entry["source_ids"])
                    self.assertTrue(entry["unknowns"])
                    if entry["kind"] == "enemy":
                        self.assertTrue(entry["facts"]["reviewed_behavior_facts"])
                    else:
                        mechanics = entry["facts"]["mechanics"]
                        self.assertTrue(mechanics.get("mechanics_summary") or mechanics.get("effect_summary"))

    def test_a20_is_cumulative_and_creator_metadata_stays_distinct_from_read_guidance(self):
        with ReferenceLibrary(self.database) as library:
            result = library.ascension(20)
            self.assertEqual([], result["missing_levels"])
            self.assertEqual(list(range(21)), [e["facts"]["level"] for e in result["entries"]])
            self.assertEqual(20, self.coverage["counts"]["guide"])
            self.assertEqual(6, self.coverage["counts"]["strategy"])
            for row in library.db.execute("SELECT body FROM entries WHERE kind='guide'"):
                self.assertEqual("metadata_only", json.loads(row[0])["evidence_status"])


if __name__ == "__main__":
    unittest.main()
