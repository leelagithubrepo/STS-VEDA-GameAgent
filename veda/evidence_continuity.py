"""Read-only binding to an owned session's input epoch; no controller access.

A capture retains its original time. A binding is invalidated by dispatch,
explicit external change, disarm, or a new adapter process. It is not a claim
that pixels were automatically recognized or that external input is detectable.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path


def check_binding(binding, current, context, source):
    if not isinstance(current, dict) or not current.get('active') or binding != current:
        raise ValueError('evidence epoch changed; inspect the current state and bind again')
    if context['run_id'] != current['run_id'] or (current.get('context') is not None and context != current['context']):
        raise ValueError('evidence belongs to another run, floor or turn')
    captured, barrier = datetime.fromisoformat(source['captured_at']), datetime.fromisoformat(current['since'])
    if captured < barrier or (current['sequence'] > 0 and captured == barrier):
        raise ValueError('capture precedes the latest input/state change; inspect its result')


def bind_session(session, context, source):
    path = Path(session)
    if path.is_dir():
        path /= 'state.json'
    with path.open('rb') as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('session state exceeds byte limit')
    state = json.loads(raw)
    if state.get('pending'):
        raise ValueError('verify the pending action before binding another action')
    binding = deepcopy(state.get('evidence_continuity'))
    check_binding(binding, binding, context, source)
    return binding
