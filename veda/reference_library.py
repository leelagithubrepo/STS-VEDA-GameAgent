"""Indexed, attributed StS1 references. Never a run inventory or execution gate.

Catalogs contain research declarations, not instructions. Imports do not change
recognition, effect support, source confidence, or controller authorization.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import closing
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unicodedata
from urllib.parse import urlparse

SCHEMA = "spire.reference-catalog.v1"
GAME = "slay_the_spire_1"
APPLICATION_ID = 0x56454452
MAX_PACK_BYTES = 16_000_000
MAX_ENTRIES = 5000
MAX_CONTEXT_BYTES = 256_000
KINDS = {"card", "relic", "potion", "enemy", "ascension", "guide", "strategy", "mechanic"}
STATUSES = {"community_reference", "source_conflict", "metadata_only", "corroborated_reference"}
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "artifacts/veda-reference.sqlite3"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value, limit=1000):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def name_key(name):
    _require(_text(name, 256), "bounded nonempty reference name required")
    return " ".join(unicodedata.normalize("NFKC", name).replace("’", "'").casefold().split())


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON object key in reference catalog")
        result[key] = value
    return result


def _compact_entry(entry):
    """Keep meanings, reference values, conflicts and unknowns; omit duplicate raw extraction."""
    audit_keys = {"source_observations", "pinned_simulator_values", "source_expression", "candidate_base_values"}
    omitted = set()
    def compact(value):
        if isinstance(value, dict):
            omitted.update(set(value) & audit_keys)
            return {k: compact(v) for k, v in value.items() if k not in audit_keys}
        if isinstance(value, list):
            return [compact(v) for v in value]
        return value
    result = compact(entry)
    result["omitted_audit_fields"] = sorted(omitted)
    return result


def _load(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_PACK_BYTES + 1)
    _require(len(raw) <= MAX_PACK_BYTES, "reference catalog exceeds byte limit")
    document = json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite reference value")))
    _require(isinstance(document, dict) and document.get("schema") == SCHEMA
             and document.get("game") == GAME, "only original video-game StS1 reference catalogs are accepted")
    _require(_text(document.get("id"), 128) and _text(document.get("scope"), 4000), "catalog identity/scope required")
    _require(isinstance(document.get("sources"), list) and 0 < len(document["sources"]) <= 1000,
             "bounded source registry required")
    _require(isinstance(document.get("entries"), list) and len(document["entries"]) <= MAX_ENTRIES,
             "bounded reference entries required")
    sources = {}
    for source in document["sources"]:
        _require(isinstance(source, dict) and _text(source.get("id"), 128)
                 and all(_text(source.get(k), 4000) for k in ("url", "title", "publisher", "kind"))
                 and isinstance(source.get("limitations"), list)
                 and all(_text(x, 4000) for x in source["limitations"]), "source attribution and limitations required")
        _require(urlparse(source["url"]).scheme in {"https", "http"} and urlparse(source["url"]).hostname,
                 "source needs an actual web address")
        _require(source["id"] not in sources, "duplicate source identity")
        sources[source["id"]] = source
    identities = set()
    for entry in document["entries"]:
        _require(isinstance(entry, dict) and _text(entry.get("id"), 256)
                 and _text(entry.get("name"), 256) and entry.get("kind") in KINDS,
                 "reference entry identity, name and known kind required")
        _require(entry["id"] not in identities, "duplicate reference identity")
        identities.add(entry["id"])
        _require(entry.get("evidence_status") in STATUSES and isinstance(entry.get("facts"), dict)
                 and len(_json(entry).encode()) <= 128_000, "bounded facts with explicit research status required")
        _require(entry.get("character") in {None, "ironclad", "silent", "defect", "watcher", "neutral"},
                 "unknown or non-original-game character")
        for field, maximum in (("aliases", 64), ("tags", 128), ("unknowns", 128)):
            _require(isinstance(entry.get(field), list) and len(entry[field]) <= maximum
                     and all(_text(v, 4000 if field == "unknowns" else 256) for v in entry[field]),
                     "bounded aliases/tags/unknowns required")
        refs = entry.get("source_ids")
        _require(isinstance(refs, list) and refs and len(refs) == len(set(refs))
                 and all(ref in sources for ref in refs), "every entry needs resolvable source attribution")
        _require(not any(entry.get(k) for k in ("runtime_authorized", "controller_authorized", "currently_owned",
                                              "vision_validated", "executable")), "references cannot assert live ownership or authority")
        if entry["kind"] == "ascension":
            _require(type(entry["facts"].get("level")) is int and 0 <= entry["facts"]["level"] <= 20,
                     "Ascension level must be 0 through 20")
    return document, hashlib.sha256(raw).hexdigest()


def build_library(catalog_paths, database=DEFAULT_DATABASE):
    """Validate everything before an atomic cache replacement; no online work."""
    paths = [Path(p).resolve() for p in catalog_paths]
    _require(0 < len(paths) <= 16 and len(paths) == len(set(paths)), "one to sixteen distinct catalogs required")
    documents = [_load(path) for path in paths]
    sources, entries, packs = {}, {}, []
    for path, (document, digest) in zip(paths, documents):
        _require(document["id"] not in {p["id"] for p in packs}, "duplicate catalog identity")
        packs.append({"id": document["id"], "path": str(path), "sha256": digest, "scope": document["scope"]})
        for source in document["sources"]:
            _require(source["id"] not in sources or sources[source["id"]] == source,
                     "conflicting definitions for one source ID")
            sources[source["id"]] = source
        for entry in document["entries"]:
            _require(entry["id"] not in entries, "reference IDs must be distinct across catalogs")
            entries[entry["id"]] = entry
    _require(len(entries) <= MAX_ENTRIES, "combined reference catalog exceeds entry bound")
    levels = [e["facts"]["level"] for e in entries.values() if e["kind"] == "ascension"]
    _require(len(levels) == len(set(levels)), "duplicate Ascension levels cannot silently override each other")
    database = Path(database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    with database.with_suffix(database.suffix + ".build.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if database.exists():
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as existing:
                _require(existing.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID,
                         "refusing to replace a non-reference database, including game telemetry")
        handle, temporary = tempfile.mkstemp(prefix=".veda-reference-", suffix=".sqlite3", dir=database.parent)
        os.close(handle)
        try:
            with closing(sqlite3.connect(temporary)) as db, db:
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute("PRAGMA foreign_keys=ON")
                db.executescript("""
                    CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE sources(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                    CREATE TABLE entries(id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
                        character TEXT, evidence_status TEXT NOT NULL, level INTEGER, body TEXT NOT NULL);
                    CREATE INDEX entries_kind_character ON entries(kind,character);
                    CREATE TABLE aliases(name_key TEXT NOT NULL, entry_id TEXT NOT NULL REFERENCES entries(id),
                        PRIMARY KEY(name_key,entry_id));
                    CREATE TABLE tags(tag TEXT NOT NULL, entry_id TEXT NOT NULL REFERENCES entries(id),
                        PRIMARY KEY(tag,entry_id));
                    CREATE TABLE attribution(entry_id TEXT NOT NULL REFERENCES entries(id),
                        source_id TEXT NOT NULL REFERENCES sources(id), PRIMARY KEY(entry_id,source_id));
                """)
                metadata = {"schema": SCHEMA, "game": GAME, "packs": packs,
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "snapshot_sha256": hashlib.sha256(_json(sorted(p["sha256"] for p in packs)).encode()).hexdigest()}
                db.executemany("INSERT INTO metadata VALUES(?,?)", [(k, _json(v)) for k, v in metadata.items()])
                db.executemany("INSERT INTO sources VALUES(?,?)", [(k, _json(v)) for k, v in sources.items()])
                for entry in entries.values():
                    db.execute("INSERT INTO entries VALUES(?,?,?,?,?,?,?)", (entry["id"], entry["kind"], entry["name"],
                        entry.get("character"), entry["evidence_status"],
                        entry["facts"].get("level") if entry["kind"] == "ascension" else None, _json(entry)))
                    db.executemany("INSERT INTO aliases VALUES(?,?)", [(key, entry["id"]) for key in
                        sorted({name_key(n) for n in [entry["name"], *entry["aliases"]]})])
                    db.executemany("INSERT INTO tags VALUES(?,?)", [(tag, entry["id"]) for tag in sorted({name_key(t) for t in entry["tags"]})])
                    db.executemany("INSERT INTO attribution VALUES(?,?)", [(entry["id"], s) for s in entry["source_ids"]])
                _require(not db.execute("PRAGMA foreign_key_check").fetchall(), "broken source linkage")
            with open(temporary, "rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, database)
            descriptor = os.open(database.parent, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        finally:
            Path(temporary).unlink(missing_ok=True)
        with ReferenceLibrary(database) as library:
            return library.coverage()


class ReferenceLibrary:
    def __init__(self, database=DEFAULT_DATABASE):
        self.path = Path(database).resolve()
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        try:
            _require(self.db.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID,
                     "not a VEDA reference database")
            self.metadata = {row["key"]: json.loads(row["value"]) for row in self.db.execute("SELECT * FROM metadata")}
            _require(self.metadata["schema"] == SCHEMA and self.metadata["game"] == GAME, "reference database version/game mismatch")
        except BaseException:
            self.db.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.db.close()

    def _result(self, rows):
        entries = [json.loads(row["body"]) for row in rows]
        references = sorted({source for entry in entries for source in entry["source_ids"]})
        sources = [json.loads(self.db.execute("SELECT body FROM sources WHERE id=?", (ref,)).fetchone()[0]) for ref in references]
        return {"game": GAME, "snapshot_sha256": self.metadata["snapshot_sha256"], "entries": entries,
                "sources": sources, "runtime_authorized": False, "controller_authorized": False,
                "live_inventory_evidence": False, "ps5_mechanics_validated": False}

    def lookup(self, name, *, kind=None, character=None):
        _require(kind is None or kind in KINDS, "unknown reference kind")
        _require(character in {None, "ironclad", "silent", "defect", "watcher", "neutral"}, "unknown character")
        query = "SELECT e.body FROM aliases a JOIN entries e ON e.id=a.entry_id WHERE a.name_key=?"
        parameters = [name_key(name)]
        if kind is not None:
            query += " AND e.kind=?"; parameters.append(kind)
        if character is not None:
            query += " AND (e.character=? OR e.character IS NULL OR e.character='neutral')"; parameters.append(character)
        rows = self.db.execute(query + " ORDER BY e.id LIMIT 101", parameters).fetchall()
        _require(len(rows) <= 100, "ambiguous name exceeds result bound; use a narrower query")
        return {**self._result(rows), "query": name, "match": "missing" if not rows else "exact" if len(rows) == 1 else "ambiguous"}

    def search(self, text, *, kind=None, limit=12):
        _require(type(limit) is int and 1 <= limit <= 50, "search limit must be 1 through 50")
        _require(kind is None or kind in KINDS, "unknown reference kind")
        key = name_key(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self.db.execute("WITH matching AS (SELECT entry_id FROM aliases WHERE name_key LIKE ? ESCAPE '\\' "
            "UNION SELECT entry_id FROM tags WHERE tag LIKE ? ESCAPE '\\') "
            "SELECT e.id,e.kind,e.name,e.character,e.evidence_status FROM entries e "
            "JOIN matching m ON m.entry_id=e.id WHERE (? IS NULL OR e.kind=?) "
            "ORDER BY e.kind,e.name,e.id LIMIT ?", ("%" + key + "%", "%" + key + "%", kind, kind, limit + 1)).fetchall()
        return {"matches": [dict(r) for r in rows[:limit]], "truncated": len(rows) > limit,
                "snapshot_sha256": self.metadata["snapshot_sha256"], "runtime_authorized": False}

    def ascension(self, level):
        _require(type(level) is int and 0 <= level <= 20, "Ascension must be 0 through 20")
        rows = self.db.execute("SELECT body FROM entries WHERE kind='ascension' AND level<=? ORDER BY level", (level,)).fetchall()
        result = self._result(rows)
        found = {e["facts"]["level"] for e in result["entries"]}
        return {**result, "level": level, "cumulative": True, "missing_levels": sorted(set(range(level + 1)) - found)}

    def context(self, requests, *, ascension):
        _require(isinstance(requests, list) and 1 <= len(requests) <= 64, "context requires one to 64 named references")
        matches, entries, sources = [], {}, {}
        for request in requests:
            _require(isinstance(request, dict) and set(request) <= {"name", "kind", "character"}, "invalid reference request")
            result = self.lookup(request["name"], kind=request.get("kind"), character=request.get("character"))
            matches.append({**request, "match": result["match"], "ids": [e["id"] for e in result["entries"]]})
            entries.update({e["id"]: e for e in result["entries"]})
            sources.update({s["id"]: s for s in result["sources"]})
        modifiers = self.ascension(ascension)
        sources.update({s["id"]: s for s in modifiers["sources"]})
        result = {"game": GAME, "snapshot_sha256": self.metadata["snapshot_sha256"], "matches": matches,
                "view": "compact_reference; full audit fields available by lookup",
                "entries": [_compact_entry(e) for e in entries.values()], "ascension": {k: v for k, v in modifiers.items() if k not in {"sources", "snapshot_sha256"}},
                "sources": list(sources.values()), "runtime_authorized": False, "controller_authorized": False,
                "live_inventory_evidence": False, "ps5_mechanics_validated": False}
        _require(len(_json(result).encode()) <= MAX_CONTEXT_BYTES,
                 "reference context exceeds byte budget; request fewer names or use exact lookup")
        return result

    def coverage(self):
        counts = dict(self.db.execute("SELECT kind,count(*) FROM entries GROUP BY kind"))
        statuses = dict(self.db.execute("SELECT evidence_status,count(*) FROM entries GROUP BY evidence_status"))
        rows = self.db.execute("SELECT body FROM entries").fetchall()
        entries = [json.loads(r[0]) for r in rows]
        unknown = sum(bool(entry["unknowns"]) for entry in entries)
        base_cards = sum(entry["kind"] == "card" and entry["facts"].get("requested_variant") == "base" for entry in entries)
        first_upgrades = sum(entry["kind"] == "card" and entry["facts"].get("requested_variant") == "first_upgrade" for entry in entries)
        return {"schema": "spire.reference-coverage.v1", "game": GAME, "database": str(self.path),
            "catalogs": self.metadata["packs"], "snapshot_sha256": self.metadata["snapshot_sha256"],
            "counts": counts, "evidence_status_counts": statuses, "entries_with_explicit_unknowns": unknown,
            "card_identity_counts": {"base": base_cards, "first_upgrade_references": first_upgrades,
                                     "other_or_unspecified": counts.get("card", 0) - base_cards - first_upgrades},
            "source_count": self.db.execute("SELECT count(*) FROM sources").fetchone()[0],
            "all_game_content_verified": False, "runtime_authorized": False,
            "controller_authorized": False, "ps5_mechanics_validated": False}
