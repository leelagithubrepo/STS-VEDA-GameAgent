"""Retain verified combat state and expand explicit observed deltas. No input."""
from copy import deepcopy
from uuid import uuid4

from .combat_requests import read_pending, SCHEMA, RESULT_SCHEMA
from .map_survey import read_json
from .menu_requests import _require
from .shop_results import session_directory
from .shop import session_context


def output_path(session, *, result=False):
    directory = session_directory(session)/'combat-packets'
    directory.mkdir(exist_ok=True)
    return directory/(('result-' if result else 'action-') + uuid4().hex + '.json')


def from_reading(after):
    reading = after['reading']; context = reading['context']
    state = deepcopy(context['state'])
    state.pop('schema', None); state.pop('observed_at', None)
    return {'schema': SCHEMA, 'context': deepcopy(after['context']), 'state': state,
        'inventory': deepcopy(context['inventory']), 'ui': deepcopy(reading['ui']),
        'encounter': {'name': reading['encounter_name'], 'type': context['encounter_type'],
                      'confidence': reading['state']['encounter_confidence']},
        'perception': {k: reading['state'][k] for k in ('confidence', 'end_turn_damage_confidence')},
        'unknowns': deepcopy(context['unknowns']), 'decision_policy': after.get('decision_policy', 'strict'),
        **{k: deepcopy(context[k]) for k in ('rules', 'boss_manifest') if k in context}}


def snapshot_from_result(packet, session):
    state = read_json(session_directory(session)/'state.json')
    last = state.get('last_verified', {})
    _require(not state.get('pending') and packet.get('operation') == 'verify'
             and packet.get('action_id') == last.get('action_id'), 'exact already-verified combat result required')
    after = packet['after']
    from .evidence_continuity import verified_result_digest
    _require(after.get('kind') == 'combat' and last.get('verified_after_digest') == verified_result_digest(after)
             and all(after['source'][k] == last['source'][k] for k in ('sha256', 'captured_at')),
             'combat result contents differ from the sealed verified observation')
    value = from_reading(after)
    # SQLite replaces provisional turn/floor IDs with canonical IDs on finalization.
    value['context'] = session_context(session)
    if last.get('combat_continuation'):
        value.update(deepcopy(last['combat_continuation']))
    return value


def last_snapshot(session):
    state = read_json(session_directory(session)/'state.json')
    last = state.get('last_verified', {})
    _require(last.get('combat_after') is not None,
             'no retained combat observation; supply an inspected draft or exact --after-result packet')
    return snapshot_from_result({'operation': 'verify', 'action_id': last['action_id'],
                                 'after': last['combat_after']}, session)


def observed_result(session, *, note, unchanged=False, focus=None, selected=None,
                    target=None, ui=None, actual=None, boundary=None, tooltip='none'):
    """Copy unchanged facts only when the reviewer explicitly attests to them.

    Changed state/UI are supplied as actual observations, never predicted by
    the plan. IDs, inventory and lifecycle bookkeeping come from the pending
    action; this function cannot finalize it or repeat its input.
    """
    state = read_json(session_directory(session)/'state.json')
    identity = (state.get('pending') or {}).get('action_id')
    pending, _ = read_pending(session, identity)
    before = pending['request']; prior = from_reading(before)
    value = {'schema': RESULT_SCHEMA, 'action_id': identity, 'observed_result': note,
             'inventory': 'unchanged', 'encounter': 'unchanged', 'state': 'unchanged',
             'perception': prior['perception'], 'unknowns': prior['unknowns'],
             **{k: 'unchanged' for k in ('rules', 'boss_manifest') if k in prior}}
    _require(sum(x is not None for x in (focus, selected, ui, actual, boundary)) == 1,
             'supply one actual focus, selection, UI, changed-state or boundary result')
    if boundary is not None:
        _require(unchanged is True and target is None, 'boundary requires inspected unchanged inventory')
        return {k: value[k] for k in ('schema', 'action_id', 'observed_result', 'inventory')} | {'boundary': deepcopy(boundary)}
    if actual is not None:
        _require(not unchanged and isinstance(actual, dict) and actual.get('others_unchanged') is True
                 and {'state', 'ui'} <= set(actual) and target is None,
                 'supply actual state/UI and others_unchanged; do not use --unchanged after a card effect')
        allowed = {'state', 'ui', 'inventory', 'encounter', 'perception', 'unknowns', 'rules', 'boss_manifest',
                   'next_turn', 'card_destination', 'telemetry', 'others_unchanged'}
        _require(not set(actual) - allowed and isinstance(actual['state'], dict) and isinstance(actual['ui'], dict),
                 'actual state and UI objects required; unsupported result fields')
        value.update({k: deepcopy(v) for k,v in actual.items() if k != 'others_unchanged'})
        return value
    _require(unchanged is True, 'inspect and declare unchanged gameplay state, inventory and encounter')
    if ui is not None:
        _require(target is None, 'UI result already supplies the actual target')
        value['ui'] = deepcopy(ui)
    else:
        actual_ui = deepcopy(prior['ui'])
        actual_ui.pop('recovery_direction', None)
        actual_ui.update(phase='targeting' if target is not None else 'card_selected' if selected else 'hand',
                         focus_domain='enemy' if target is not None else 'hand',
                         focused_card_id=focus if focus is not None else selected,
                         selected_card_id=selected, focused_target_id=target,
                         focused_subject_id=target, tooltip_kind=tooltip,
                         focus_evidence_note=note)
        _require(selected is not None or target is None, 'target requires actual selected card')
        value['ui'] = actual_ui
    return value
