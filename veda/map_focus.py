"""Reconcile an inspected map focus result, without inferring controller edges."""
from copy import deepcopy

from .choice_execution import _require, _same_json, _text


def verify_focus(proposal, before, after):
    from .menu_controls import _MAP_SCREENS
    _require(proposal['step_kind'] == 'focus'
             and before['ui'].get('menu_family') == 'map_nodes'
             and after['ui'].get('menu_family') == 'map_nodes',
             'map focus reconciliation requires the same node menu')
    for key in ('context', 'resources', 'inventory_digest', 'facts'):
        _require(_same_json(before[key], after[key]), 'map focus changed gameplay state')
    outcome = after['review'].get('outcome', {})
    _require(outcome.get('action_id') == proposal['action_id']
             and outcome.get('before_frame_id') == before['frame']['frame_id']
             and outcome.get('before_sha256') == before['frame']['image_sha256']
             and outcome.get('choice_id') == proposal['choice']['choice_id']
             and outcome.get('option_ids') == proposal['choice']['option_ids']
             and _text(outcome.get('observed_result')), 'exact observed map focus outcome required')
    if outcome.get('map_reobservation') is True:
        from .menu_controls import _map_nodes
        _map_nodes(after)
        _require(before['frame']['image_sha256'] != after['frame']['image_sha256']
                 and after['ui']['choice_id'] == before['ui']['choice_id'],
                 'map correction needs a distinct inspected result of the same choice')
        # Correct a perception error without claiming entry or learning a
        # navigation edge from an incorrect before-declaration.
        return {'step_verified': True, 'choice_complete': False, 'observed_mismatches': [{
            'field': 'map_selectable_set', 'declared_before': deepcopy(before['ui']['options']),
            'observed': deepcopy(after['ui']['options']),
            'observed_focus': after['ui']['focused_id']}]}
    target = after['ui']['focused_id']
    _require(target in {o['id'] for o in before['ui']['options'] if o['enabled']},
             'actual map focus must be a current selectable sibling')
    _require(before['frame']['image_sha256'] != after['frame']['image_sha256'],
             'changed map focus needs distinct inspected pixels')
    expected = deepcopy(before['ui'])
    expected['focused_id'] = target
    corrections = []
    # A focus review can also correct a misread icon. IDs, coordinates,
    # connections, enabled state and sibling coverage must remain identical.
    _require([o['id'] for o in expected['options']] == [o['id'] for o in after['ui']['options']],
             'map focus changed selectable nodes')
    for old, observed in zip(expected['options'], after['ui']['options']):
        _require(observed['node']['kind'] in set(_MAP_SCREENS) | {'unknown'}
                 and _text(observed['node']['classification_evidence']),
                 'corrected map icon needs a supported kind and inspected evidence')
        if old['node']['kind'] != observed['node']['kind']:
            corrections.append({'field': 'map_node_classification', 'node_id': old['id'],
                'expected': old['node']['kind'], 'observed': observed['node']['kind']})
            old['node']['kind'] = observed['node']['kind']
            old['node']['classification_evidence'] = observed['node']['classification_evidence']
            old['label'] = observed['label']

    def semantics(value):
        if isinstance(value, dict):
            return {k: semantics(v) for k, v in value.items()
                    if k not in {'evidence', 'evidence_note', 'classification_evidence', 'reachability_evidence'}}
        if isinstance(value, list):
            return [semantics(v) for v in value]
        return value

    _require(semantics(expected) == semantics(after['ui']),
             'map focus changed nodes, reachability or unrelated UI')
    outcome = after['review'].get('outcome', {})
    _require(outcome.get('action_id') == proposal['action_id']
             and outcome.get('before_frame_id') == before['frame']['frame_id']
             and outcome.get('before_sha256') == before['frame']['image_sha256']
             and outcome.get('choice_id') == proposal['choice']['choice_id']
             and outcome.get('option_ids') == proposal['choice']['option_ids']
             and _text(outcome.get('observed_result')), 'exact observed map focus outcome required')
    # The prior focus declaration itself may be wrong. Retain the mismatch,
    # but never teach a physical from/button/to edge from this recovery.
    return {'step_verified': True, 'choice_complete': False,
            'observed_mismatches': corrections + [{'field': 'map_focus',
                'declared_before': before['ui']['focused_id'],
                'expected': proposal['expectation']['focused_id'], 'observed': target}]}
