#!/usr/bin/env python3
"""Package reviewed missing-note recovery. Reads the journal; never logs or sends input."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from veda.menu_results import _unique, _nonfinite
from veda.play_telemetry import outcome_request_digest, validate_outcome_request
from veda.reviewed_play import MAX_BYTES, SCHEMA
from veda.saved_frame_reader import _identity


def package(*, session, action_id, reviewer, evidence_note, notes, reviewed, output):
    if reviewed is not True:
        raise ValueError('inspect the retained before/result evidence and declare --reviewed')
    for value, bound in ((reviewer, 128), (evidence_note, 2048)):
        if not isinstance(value, str) or not value.strip() or len(value) > bound:
            raise ValueError('bounded reviewer and retained-evidence note required')
    with Path(session).open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('session exceeds byte bound')
    state = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
    pending = state.get('pending')
    if (state.get('schema') != SCHEMA or not isinstance(pending, dict)
            or pending.get('status') != 'verified_pending_log' or pending.get('action_id') != action_id):
        raise ValueError('exact retained verified_pending_log action required')
    original = pending['outcome_request']
    if (pending['request']['context'] != original['context']
            or original['context']['run_id'] != state['run_id']):
        raise ValueError('retained action, outcome and session context must match')
    for source in (pending['request']['source'], original['source']):
        if _identity(Path(source['path']))[0] != source['sha256']:
            raise ValueError('retained source bytes changed')
    repaired = deepcopy(original)
    if not isinstance(notes, list) or not 1 <= len(notes) <= 100:
        raise ValueError('bounded inventory event note list required')
    indexes = set()
    for note in notes:
        if not isinstance(note, dict) or set(note) != {'index', 'evidence_note'}:
            raise ValueError('each repair note needs only index and evidence_note')
        index = note['index']
        if type(index) is not int or index in indexes or not 0 <= index < len(repaired.get('inventory_events', [])):
            raise ValueError('distinct existing inventory event index required')
        if 'evidence_note' in repaired['inventory_events'][index]:
            raise ValueError('only absent evidence notes may be added')
        repaired['inventory_events'][index]['evidence_note'] = note['evidence_note']
        indexes.add(index)
    if not indexes:
        raise ValueError('at least one missing event note is required')
    validate_outcome_request(repaired)
    request = {'operation': 'repair_outcome_metadata', 'repair_id': str(uuid4()),
        'action_id': action_id, 'outcome_operation_id': original['operation_id'],
        'expected_outcome_sha256': outcome_request_digest(original), 'inventory_event_notes': notes,
        'review': {'kind': 'reviewed_retained_outcome_metadata', 'complete': True,
            'reviewer': reviewer, 'source': original['source'], 'evidence_note': evidence_note}}
    if Path(session).read_bytes() != raw:
        raise ValueError('pending journal changed while packaging recovery')
    payload = json.dumps(request, sort_keys=True, indent=2, allow_nan=False) + '\n'
    created = False
    try:
        with Path(output).open('x') as stream:
            created = True
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if created:
            Path(output).unlink(missing_ok=True)
        raise
    return {'request_file': str(Path(output).resolve())}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session', type=Path, required=True)
    p.add_argument('--action-id', required=True)
    p.add_argument('--reviewer', required=True)
    p.add_argument('--evidence-note', required=True)
    p.add_argument('--inventory-event-note', action='append', nargs=2, required=True,
                   metavar=('INDEX', 'OBSERVED_EVIDENCE'))
    p.add_argument('--reviewed', action='store_true')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        notes = [{'index': int(index), 'evidence_note': note} for index, note in args.inventory_event_note]
        result = package(session=args.session, action_id=args.action_id, reviewer=args.reviewer,
            evidence_note=args.evidence_note, notes=notes, reviewed=args.reviewed, output=args.output)
    except (ValueError, TypeError, KeyError, OSError, AttributeError) as error:
        print(json.dumps({'status': 'needs_review', 'reason': str(error), 'controller_input_sent': False}))
        return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
