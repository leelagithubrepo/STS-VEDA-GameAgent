"""Reference-only contracts; never gameplay, recognizer or source accuracy tests."""
from copy import deepcopy
from contextlib import closing
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from veda.reference_library import build_library, ReferenceLibrary, SCHEMA, GAME


def source(ident="source"):
    return {"id": ident, "url": "https://example.org/reference", "title": "Synthetic source",
            "publisher": "Fixture", "kind": "synthetic", "limitations": ["Not gameplay evidence."]}


def entry(ident, name, kind="card", **values):
    return {"id": ident, "name": name, "kind": kind, "aliases": [], "tags": [],
            "facts": {"cost": None}, "source_ids": ["source"], "unknowns": ["Unknown remains unknown."],
            "evidence_status": "community_reference", **values}


def catalog():
    return {"schema": SCHEMA, "game": GAME, "id": "test", "scope": "Synthetic reference fixtures only.",
        "sources": [source()], "entries": [
            entry("strike:red", "Strike", character="ironclad", facts={"cost": 1, "damage": 6}, tags=["Attack", "attack"]),
            entry("strike:green", "Strike", character="silent", facts={"cost": 1, "damage": 6}),
            entry("grit", "True Grit", facts={"cost": 1, "exhaust": "random"}),
            entry("grit:plus", "True Grit+", facts={"cost": 1, "exhaust": "chosen"}, aliases=["TrueGrit+"]),
            entry("pyramid", "Runic Pyramid", "relic", facts={"end_turn_discard": False}),
            entry("bane", "Ascender's Bane", facts={"unplayable": True}),
            *[entry(f"a{i}", f"Ascension {i}", "ascension", facts={"level": i, "cumulative": True}) for i in range(21)],
        ]}


class ReferenceLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path, self.database = self.root / "pack.json", self.root / "reference.sqlite3"
        self.pack = catalog()

    def build(self, value=None):
        self.path.write_text(json.dumps(self.pack if value is None else value))
        return build_library([self.path], self.database)

    def test_exact_names_preserve_upgrade_and_duplicate_character_identity(self):
        self.build()
        with ReferenceLibrary(self.database) as library:
            ambiguous = library.lookup("Strike")
            self.assertEqual("ambiguous", ambiguous["match"])
            self.assertEqual(2, len(ambiguous["entries"]))
            selected = library.lookup("Strike", character="ironclad")
            self.assertEqual("strike:red", selected["entries"][0]["id"])
            self.assertEqual("chosen", library.lookup("true grit+")["entries"][0]["facts"]["exhaust"])
            self.assertEqual("random", library.lookup("True Grit")["entries"][0]["facts"]["exhaust"])
            self.assertEqual("missing", library.lookup("True Grit++")["match"])

    def test_alias_punctuation_and_kind_are_bounded_and_explicit(self):
        self.build()
        with ReferenceLibrary(self.database) as library:
            self.assertEqual("grit:plus", library.lookup("TrueGrit+")["entries"][0]["id"])
            self.assertEqual("bane", library.lookup("Ascender’s Bane")["entries"][0]["id"])
            self.assertEqual("missing", library.lookup("Runic Pyramid", kind="card")["match"])
            with self.assertRaises(ValueError):
                library.lookup("Strike", character="regent")

    def test_context_deduplicates_references_and_keeps_all_cumulative_levels(self):
        self.build()
        with ReferenceLibrary(self.database) as library:
            result = library.context([{"name": "True Grit+"}, {"name": "True Grit+"}, {"name": "Missing"}], ascension=20)
            self.assertEqual(1, len(result["entries"]))
            self.assertEqual(1, len(result["sources"]))
            self.assertEqual(21, len(result["ascension"]["entries"]))
            self.assertEqual([], result["ascension"]["missing_levels"])
            self.assertEqual("missing", result["matches"][-1]["match"])
            self.assertFalse(result["runtime_authorized"])
            self.assertFalse(result["controller_authorized"])
            self.assertFalse(result["live_inventory_evidence"])

    def test_missing_ascensions_are_not_filled_from_memory(self):
        self.pack["entries"] = [e for e in self.pack["entries"] if e["id"] != "a18"]
        self.build()
        with ReferenceLibrary(self.database) as library:
            self.assertEqual([18], library.ascension(20)["missing_levels"])
            self.assertEqual([], library.ascension(0)["missing_levels"])
            for level in (True, -1, 21, 1.5):
                with self.assertRaises(ValueError):
                    library.ascension(level)

    def test_compact_context_omits_raw_duplicates_but_keeps_conflicts_and_unknowns(self):
        self.pack["entries"][0]["facts"].update(source_observations={"raw": 6}, source_conflicts=["Conflicting value."])
        self.build()
        with ReferenceLibrary(self.database) as library:
            found = library.context([{"name": "Strike", "character": "ironclad"}], ascension=0)["entries"][0]
            self.assertNotIn("source_observations", found["facts"])
            self.assertEqual(["Conflicting value."], found["facts"]["source_conflicts"])
            self.assertEqual(["Unknown remains unknown."], found["unknowns"])
            self.assertEqual(["source_observations"], found["omitted_audit_fields"])
            self.assertIn("source_observations", library.lookup("Strike", character="ironclad")["entries"][0]["facts"])

    def test_context_budget_fails_explicitly_instead_of_silently_dropping_facts(self):
        self.build()
        with ReferenceLibrary(self.database) as library, patch("veda.reference_library.MAX_CONTEXT_BYTES", 20):
            with self.assertRaisesRegex(ValueError, "exceeds byte budget"):
                library.context([{"name": "Strike"}], ascension=20)

    def test_conflicts_and_unknowns_survive_build_and_query(self):
        self.pack["entries"][0].update(evidence_status="source_conflict", facts={"damage": None, "competing_values": [6, 7]})
        result = self.build()
        with ReferenceLibrary(self.database) as library:
            found = library.lookup("Strike", character="ironclad")["entries"][0]
            self.assertIsNone(found["facts"]["damage"])
            self.assertEqual("source_conflict", found["evidence_status"])
            self.assertEqual(1, result["evidence_status_counts"]["source_conflict"])
            self.assertFalse(result["all_game_content_verified"])

    def test_source_attribution_and_snapshot_hash_are_retained(self):
        first = self.build()
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), first["catalogs"][0]["sha256"])
        with ReferenceLibrary(self.database) as library:
            self.assertEqual([source()], library.lookup("Runic Pyramid")["sources"])
        self.pack["entries"][0]["facts"]["damage"] = 7
        second = self.build()
        self.assertNotEqual(first["snapshot_sha256"], second["snapshot_sha256"])

    def test_invalid_imports_leave_the_old_database_untouched(self):
        self.build()
        original = self.database.read_bytes()
        mutations = [lambda p: p.update(game="slay_the_spire_2"),
            lambda p: p["entries"][0].update(character="necrobinder"),
            lambda p: p["entries"][0].update(source_ids=["missing"]),
            lambda p: p["entries"].append(deepcopy(p["entries"][0])),
            lambda p: p["entries"][0].update(runtime_authorized=True),
            lambda p: p["entries"][0].update(currently_owned=True),
            lambda p: p["entries"][0]["facts"].update(damage=float("nan"))]
        for mutate in mutations:
            document = deepcopy(self.pack); mutate(document)
            with self.subTest(document=document), self.assertRaises(ValueError):
                self.build(document)
            self.assertEqual(original, self.database.read_bytes())

    def test_duplicate_json_keys_cannot_replace_unknown_with_an_apparent_fact(self):
        self.build()
        previous = self.database.read_bytes()
        raw = json.dumps(self.pack).replace('"damage": 6', '"damage": null, "damage": 6', 1)
        self.path.write_text(raw)
        with self.assertRaisesRegex(ValueError, "duplicate JSON object key"):
            build_library([self.path], self.database)
        self.assertEqual(previous, self.database.read_bytes())

    def test_build_receipt_is_read_while_publication_lock_is_still_held(self):
        original = ReferenceLibrary.coverage
        def inspect_lock(library):
            with self.database.with_suffix(".sqlite3.build.lock").open("a+") as second:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return original(library)
        with patch.object(ReferenceLibrary, "coverage", inspect_lock):
            self.assertEqual(self.build()["catalogs"][0]["id"], "test")

    def test_duplicate_cross_pack_identity_or_conflicting_source_rejected(self):
        self.build()
        other = deepcopy(self.pack); other["id"] = "other"
        path = self.root / "other.json"; path.write_text(json.dumps(other))
        with self.assertRaisesRegex(ValueError, "IDs must be distinct"):
            build_library([self.path, path], self.database)
        other["entries"] = []; other["sources"][0]["url"] = "https://example.org/different"
        path.write_text(json.dumps(other))
        with self.assertRaisesRegex(ValueError, "conflicting definitions"):
            build_library([self.path, path], self.database)

    def test_does_not_replace_or_open_game_telemetry_as_reference(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("CREATE TABLE runs(id TEXT)")
            db.execute("INSERT INTO runs VALUES('real-run')")
        original = self.database.read_bytes()
        with self.assertRaisesRegex(ValueError, "non-reference database"):
            self.build()
        self.assertEqual(original, self.database.read_bytes())
        with self.assertRaisesRegex(ValueError, "not a VEDA reference"):
            ReferenceLibrary(self.database)

    def test_atomic_publish_failure_preserves_previous_cache(self):
        self.build(); original = self.database.read_bytes()
        with patch("veda.reference_library.os.replace", side_effect=OSError("fixture publish failure")):
            with self.assertRaises(OSError):
                self.build()
        self.assertEqual(original, self.database.read_bytes())
        self.assertEqual([], list(self.root.glob(".veda-reference-*")))

    def test_search_is_bounded_parameterized_and_does_not_treat_wildcards_as_commands(self):
        self.build()
        with ReferenceLibrary(self.database) as library:
            result = library.search("ascension", limit=2)
            self.assertEqual(2, len(result["matches"]))
            self.assertTrue(result["truncated"])
            self.assertEqual([], library.search("%")['matches'])
            self.assertEqual([], library.search("' OR 1=1 --")['matches'])
            self.assertEqual(1, len(library.search("attack")["matches"]))
            with self.assertRaises(ValueError):
                library.search("a", limit=True)

    def test_queries_are_read_only_and_missing_cache_is_not_created(self):
        with self.assertRaises(sqlite3.OperationalError):
            ReferenceLibrary(self.database)
        self.assertFalse(self.database.exists())
        self.build()
        with ReferenceLibrary(self.database) as library:
            with self.assertRaises(sqlite3.OperationalError):
                library.db.execute("DELETE FROM entries")


if __name__ == "__main__":
    unittest.main()
