"""Load one bounded JSON request. File envelopes cannot modify or nest actions."""
import json
import os
import stat
from pathlib import Path

MAX_BYTES = 1_000_000


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate request key')
        result[key] = value
    return result


def _nonfinite(_):
    raise ValueError('request must contain finite JSON')


def decode(raw):
    if len(raw) > MAX_BYTES:
        raise ValueError('request exceeds byte limit')
    try:
        return json.loads(raw, object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except RecursionError:
        raise ValueError('request nesting exceeds JSON limit') from None


def load_request(raw):
    value = decode(raw)
    if not isinstance(value, dict):
        raise ValueError('request must be a JSON object')
    pointer = 'request_file' in value or value.get('operation') == 'request_file'
    if not pointer:
        return value
    if set(value) == {'request_file'}:
        path = value['request_file']
    elif set(value) == {'operation', 'path'} and value['operation'] == 'request_file':
        path = value['path']
    else:
        raise ValueError('Use {"request_file":"/absolute/request.json"}; do not add action fields')
    if not isinstance(path, str) or not path.strip() or '\0' in path:
        raise ValueError('request_file needs a nonempty file path')
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('request_file must be a regular JSON file')
        packet = decode(stream.read(MAX_BYTES + 1))
    if not isinstance(packet, dict) or 'request_file' in packet or packet.get('operation') == 'request_file':
        raise ValueError('request_file must contain one action packet, not another file envelope')
    return packet
