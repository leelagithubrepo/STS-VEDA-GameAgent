"""Compact explicit menu-result reviews correlated to a durable pending action.

These helpers do not observe pixels, send inputs or change the session/ledger.
An ``unchanged`` declaration means the caller inspected and confirmed that
field, not that a successful controller acknowledgement proves it unchanged.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .choice_execution import SCHEMA as OBSERVATION_SCHEMA, _checked, _require, verify_choice_step
from .menu_controls import CONTROL_PROFILE, bind_reviewed_menu_controls
from .menu_requests import _inventory, _json, _text, _ui
from .play_requests import reviewed_capture_source
from .reviewed_play import MAX_BYTES, SCHEMA as SESSION_SCHEMA, inventory_digest

SCHEMA = 'veda.menu-result.v1'


def read_pending(path, action_id):
    path = Path(path)
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    _require(len(raw) <= MAX_BYTES, 'session exceeds byte bound')
    # Ordinary sessions are bounded by MAX_BYTES; use the same strict JSON parser.
    value = json.loads(raw, object_pairs_hook=_unique, parse_constant=_nonfinite)
    pending = value.get('pending')
    _require(value.get('schema') == SESSION_SCHEMA and isinstance(pending, dict)
             and pending.get('status') == 'attempted' and pending.get('action_id') == action_id,
             'exact attempted action required; verified_pending_log needs finalize or metadata repair')
    _require(pending['request']['kind'] == 'choice'
             and pending['request']['context']['run_id'] == value['run_id'], 'pending menu action required')
    return pending, hashlib.sha256(raw).hexdigest()


def _unique(items):
    value = {}
    for key, item in items:
        _require(key not in value, 'duplicate session JSON key')
        value[key] = item
    return value


def _nonfinite(_):
    raise ValueError('session must contain finite JSON')


def _draft(value, pending):
    draft = _json(value)
    required = {'schema', 'action_id', 'resources', 'inventory', 'facts', 'result', 'observed_result'}
    _require(isinstance(draft, dict) and required <= set(draft)
             and not set(draft) - required - {'context', 'telemetry'} and draft['schema'] == SCHEMA,
             'compact veda.menu-result.v1 required; no source, hashes, reviews or frame IDs')
    _require(draft['action_id'] == pending['action_id'] and _text(draft['observed_result'], 256),
             'matching action ID and bounded observed-result description required')
    _require(isinstance(draft['result'], dict), 'explicit observed result required')
    return draft


def _unbound_ui(value):
    ui = deepcopy(value)
    for key in ('navigation', 'confirm'):
        ui.pop(key, None)
    for option in ui['options']:
        option.pop('activate', None)
        option.pop('shortcut', None)
    return ui


def _observed_ui(draft, pending, checked):
    result = draft['result']
    kind = result.get('kind')
    before_ui = pending['request']['observation']['ui']
    if kind == 'focus':
        _require(set(result) == {'kind', 'focused_id'} and pending['proposal']['step_kind'] == 'focus',
                 'focus review requires the pending focus step')
        ui = _unbound_ui(before_ui)
        ui['focused_id'] = result['focused_id']
        ui = _ui(ui)
    elif kind == 'upgrade_preview':
        _require(set(result) == {'kind', 'selected_id', 'observed_upgrade_text', 'confirm_hint'}
                 and pending['proposal']['step_kind'] == 'select'
                 and before_ui.get('menu_family') == 'card_upgrade', 'pending upgrade selection required')
        selected = result['selected_id']
        options = [o for o in before_ui['options'] if o['id'] == selected]
        _require(len(options) == 1, 'observed preview card must be in the reviewed picker')
        option = _unbound_ui({'options': options})['options'][0]
        ui = _ui({'menu_family': 'card_upgrade', 'choice_id': before_ui['choice_id'],
            'layout_id': before_ui['layout_id'], 'phase': 'confirm', 'focused_id': selected,
            'options': [option], 'selected_ids': [selected], 'pending_ids': [selected],
            'upgrade_preview': {'option_id': selected, 'before_name': option['card']['name'],
                'after_name': option['card']['upgrade_name'], 'observed_upgrade_text': result['observed_upgrade_text']},
            'confirm_hint': result['confirm_hint']})
    else:
        _require(kind == 'menu' and set(result) == {'kind', 'ui'} and isinstance(result['ui'], dict),
                 'result kind must be focus, upgrade_preview or an explicitly observed menu')
        value = result['ui']
        if value.get('phase') == 'result':
            _require(set(value) == {'screen', 'phase', 'choice_id', 'layout_id', 'options', 'focused_id'},
                     'result-only UI needs screen, phase, choice/layout IDs, options and focus; no controls')
            ui = deepcopy(value)
            _require(isinstance(ui['options'], list) and all(isinstance(o, dict) and
                     set(o) == {'id', 'label', 'enabled', 'costs'} for o in ui['options']),
                     'result-only options cannot carry input controls')
            ui.update(order=[o['id'] for o in ui['options']], selection_mode='immediate',
                      required_count=0, selected_ids=[], pending_ids=[], navigation=[])
        else:
            ui = _ui(value)
    hint = ui.pop('confirm_hint', None)
    if hint is not None:
        _require(isinstance(hint, dict) and set(hint) == {'button', 'hint_text'}, 'actual visible confirmation hint required')
        ui['confirm'] = {'button': hint['button'], 'evidence': {
            'kind': 'visible_hint', 'reviewer': checked['review']['reviewer'],
            'meaning': 'confirm:' + ui['choice_id'], 'layout_id': ui['layout_id'],
            'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
            'hint_text': hint['hint_text']}}
    return ui


def _request(draft, pending, checked, control_profile, clock):
    _require(control_profile == CONTROL_PROFILE, 'explicit default PS5 control profile required')
    before = pending['request']
    old = before['observation']
    def observed(name, previous):
        value = draft[name]
        return deepcopy(previous if value == 'unchanged' else value)
    if draft['inventory'] == 'selected_upgrade_applied':
        _require(old['ui'].get('menu_family') == 'card_upgrade'
                 and pending['proposal']['step_kind'] == 'commit', 'selected upgrade declaration requires its pending confirm')
        target = next(o for o in old['ui']['options'] if o['id'] == before['choice']['option_ids'][0])
        inventory = _inventory(before['inventory'])
        _require(inventory['coverage']['card'] == 'complete'
                 and target['card']['name'] in inventory['current']['card'], 'complete before inventory must contain selected card')
        inventory['current']['card'].remove(target['card']['name'])
        inventory['current']['card'].append(target['card']['upgrade_name'])
    else:
        inventory = _inventory(observed('inventory', before['inventory']))
    review = dict(checked['review'], kind='reviewed_choice_ui', outcome={
        'action_id': pending['action_id'], 'before_frame_id': old['frame']['frame_id'],
        'before_sha256': before['source']['sha256'], 'choice_id': before['choice']['choice_id'],
        'option_ids': before['choice']['option_ids'], 'observed_result': draft['observed_result']})
    context = deepcopy(draft.get('context', before['context']))
    observation = {'schema': OBSERVATION_SCHEMA,
        'frame': {'frame_id': checked['frame_id'], 'image_sha256': checked['source']['sha256'],
                  'observed_at': checked['source']['captured_at']},
        'review': review, 'context': context, 'inventory_digest': inventory_digest(inventory),
        'resources': observed('resources', old['resources']), 'facts': observed('facts', old['facts']),
        'ui': _observed_ui(draft, pending, checked)}
    if observation['ui'].get('menu_family'):
        observation = bind_reviewed_menu_controls(observation, control_profile=control_profile, now=clock)
    verified = verify_choice_step(pending['proposal'], old, observation, now=clock)
    changes = deepcopy(draft.get('telemetry', {}))
    _require(isinstance(changes, dict) and not set(changes) - {
        'inventory_events', 'inventory_baseline', 'zone_events', 'zone_baseline',
        'zone_coverage', 'transitions'}, 'unknown telemetry override')
    if old['ui'].get('menu_family') == 'card_upgrade' and pending['proposal']['step_kind'] == 'commit':
        target = next(o for o in old['ui']['options'] if o['id'] == before['choice']['option_ids'][0])
        event = {'kind': 'card', 'action': 'replaced', 'item': target['card']['name'],
                 'related_item': target['card']['upgrade_name'], 'evidence_note': draft['observed_result']}
        _require(not changes, 'upgrade telemetry is derived from the reviewed selected-card result')
        changes = {'inventory_events': [event]}
    after = {'kind': 'choice', 'context': context, 'source': checked['source'],
             'review': deepcopy(review), 'observation': observation, 'inventory': inventory}
    if changes:
        after['mutation_review'] = dict(checked['review'], kind='reviewed_mutation', changes=deepcopy(changes))
    operation_id = str(uuid5(NAMESPACE_URL, pending['action_id'] + ':' + checked['frame_id']))
    from .play_telemetry import validate_outcome_request
    validation_source = dict(checked['source'])
    # Only schema validation uses a private synthetic source; it never exits
    # validate_menu_result or becomes a request_file.
    validation_source.setdefault('path', '/validation-only/no-image.png')
    validation_source.setdefault('origin', 'reviewer')
    validation_source.setdefault('evidence_note', 'Offline structure validation only.')
    validate_outcome_request({'schema': 'veda.play-telemetry.v1', 'operation_id': operation_id,
        'context': before['context'], 'decision_id': pending['decision_id'], 'source': validation_source,
        'state': {k: observation[k] for k in ('resources', 'facts', 'ui')},
        'status': 'verified', 'evidence_note': draft['observed_result'], **changes})
    _require(verified['choice_complete'] or not changes, 'navigation cannot assert game mutations')
    return {'operation': 'verify', 'action_id': pending['action_id'], 'operation_id': operation_id,
            'after': after, 'telemetry': changes}


@_checked
def validate_menu_result(draft, *, session, action_id, control_profile):
    pending, digest = read_pending(session, action_id)
    draft = _draft(draft, pending)
    clock = datetime.fromisoformat(pending['attempted_at']) + timedelta(seconds=1)
    sha = 'f' * 64 if pending['request']['source']['sha256'] != 'f' * 64 else 'e' * 64
    checked = {'frame_id': 'private-result-validation-only',
        'source': {'sha256': sha, 'captured_at': clock.isoformat()},
        'review': {'complete': True, 'reviewer': 'offline result schema validation',
                   'frame_id': 'private-result-validation-only', 'image_sha256': sha}}
    _request(draft, pending, checked, control_profile, clock)
    return {'result_valid': True, 'validation_only': True, 'dispatchable': False,
            'source_bound': False, 'controller_input_sent': False, 'pending_digest': digest}


@_checked
def write_menu_result(draft, *, session, action_id, capture, reviewer, evidence_note,
                      reviewed, control_profile, output, now=None):
    pending, digest = read_pending(session, action_id)
    draft = _draft(draft, pending)
    checked = reviewed_capture_source(capture=capture, reviewer=reviewer,
        evidence_note=evidence_note, reviewed=reviewed, now=now)
    _require(datetime.fromisoformat(checked['source']['captured_at']) > datetime.fromisoformat(pending['attempted_at']),
             'result capture must follow the actual dispatch')
    packet = _request(draft, pending, checked, control_profile, now or datetime.now(timezone.utc))
    data = (json.dumps(packet, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()
    _require(len(data) <= MAX_BYTES, 'result packet exceeds byte bound')
    _require(reviewed_capture_source(capture=capture, reviewer=reviewer,
        evidence_note=evidence_note, reviewed=reviewed, now=now) == checked, 'capture changed during result packaging')
    _require(read_pending(session, action_id)[1] == digest, 'pending action changed during result packaging')
    _require(isinstance(output, (str, Path)) and _text(str(output)), 'new output path required')
    destination = Path(output).expanduser().absolute()
    # Exclusive output cannot replace a draft, receipt, pending journal or old request.
    created = False
    try:
        with destination.open('xb') as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {'request_file': str(destination)}
