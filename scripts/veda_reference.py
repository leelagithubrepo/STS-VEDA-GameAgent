#!/usr/bin/env python3
"""Build or query VEDA's attributed StS1 reference cache without game input."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.reference_library import ReferenceLibrary, build_library, DEFAULT_DATABASE, KINDS, ROOT


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    commands = parser.add_subparsers(dest="operation", required=True)
    build = commands.add_parser("build", help="atomically import local catalogs; no network")
    build.add_argument("--catalog", action="append", type=Path)
    find = commands.add_parser("lookup", help="exact name; duplicate names remain ambiguous")
    find.add_argument("name")
    find.add_argument("--kind", choices=sorted(KINDS))
    find.add_argument("--character", choices=("ironclad", "silent", "defect", "watcher", "neutral"))
    search = commands.add_parser("search", help="bounded names/tags search; facts require exact lookup")
    search.add_argument("text")
    search.add_argument("--kind", choices=sorted(KINDS))
    search.add_argument("--limit", type=int, default=12)
    commands.add_parser("coverage")
    ascension = commands.add_parser("ascension")
    ascension.add_argument("level", type=int)
    context = commands.add_parser("context", help="one batch for observed names plus current Ascension")
    context.add_argument("request", type=Path, help="JSON with references list and ascension number")
    args = parser.parse_args(argv)
    try:
        if args.operation == "build":
            result = build_library(args.catalog or sorted((ROOT / "data/spire_reference").glob("*.json")), args.database)
        else:
            with ReferenceLibrary(args.database) as library:
                if args.operation == "lookup":
                    result = library.lookup(args.name, kind=args.kind, character=args.character)
                elif args.operation == "search":
                    result = library.search(args.text, kind=args.kind, limit=args.limit)
                elif args.operation == "ascension":
                    result = library.ascension(args.level)
                elif args.operation == "context":
                    with args.request.open("rb") as stream:
                        raw = stream.read(65537)
                    if len(raw) > 65536:
                        raise ValueError("context request exceeds byte limit")
                    request = json.loads(raw)
                    result = library.context(request["references"], ascension=request["ascension"])
                else:
                    result = library.coverage()
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
