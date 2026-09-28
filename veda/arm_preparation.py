"""Stage an unreviewed arm preview; explicit inspection still precedes authority."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path

from .execution import ARM_PHRASE
from .play_requests import (MAX_AGE, PlayRequestError, SCREENS,
                            _receipt_bytes, _require, _text, capture_source_identity,
                            write_arm_request)

DRAFT_SCHEMA = 'veda.arm-draft.v1'
STAGE_SCHEMA = 'veda.arm-stage.v1'
MAX_STAGE_BYTES = 32768


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def read_document(path, *, with_bytes=False):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, 'arm_document_duplicate_key')
            result[key] = value
        return result
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(MAX_STAGE_BYTES + 1)
        _require(len(raw) <= MAX_STAGE_BYTES, 'arm_document_too_large')
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(PlayRequestError('arm_document_invalid')))
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        if isinstance(error, PlayRequestError):
            raise
        raise PlayRequestError('arm_document_unreadable') from None
    _require(isinstance(value, dict), 'arm_document_invalid')
    return (value, raw) if with_bytes else value


def validate_arm_draft(draft):
    _require(isinstance(draft, dict) and set(draft) == {
        'schema', 'run_id', 'screen', 'reviewer', 'phrase', 'exclusive_client_confirmed'}, 'arm_draft_fields_invalid')
    _require(draft['schema'] == DRAFT_SCHEMA, 'arm_draft_schema_invalid')
    _text(draft['run_id'], 128, 'run_id_invalid')
    _text(draft['reviewer'], 128, 'reviewer_invalid')
    _require(draft['screen'] in SCREENS, 'screen_unknown')
    _require(draft['phrase'] == ARM_PHRASE, 'current_run_arming_phrase_required')
    _require(draft['exclusive_client_confirmed'] is True, 'exclusive_client_declaration_required')
    return {'status': 'arm_draft_valid', 'review_complete': False,
            'runtime_authorized': False, 'controller_input_sent': False}


def _new_path(value):
    _text(str(value), 4096, 'path_invalid')
    path = Path(value).expanduser().absolute()
    _require(path.parent.is_dir(), 'output_directory_missing')
    _require(not path.exists() and not path.is_symlink(), 'output_already_exists')
    return path.parent.resolve() / path.name


def validate_outputs(stage, request):
    stage, request = _new_path(stage), _new_path(request)
    _require(stage != request, 'arm_outputs_conflict')
    return stage, request


def stage_arm_review(draft, *, capture, output, request_output, now=None):
    """Publish non-dispatchable source metadata, with no completed review field."""
    validate_arm_draft(draft)
    target, request = validate_outputs(output, request_output)
    identity = capture_source_identity(capture=capture, now=now)
    path = Path(identity['source']['path'])
    receipt_path = path.with_suffix('.capture.json')
    _require(target not in {path, receipt_path} and request not in {path, receipt_path}, 'output_is_capture_source')
    receipt_digest = hashlib.sha256(_receipt_bytes(receipt_path)).hexdigest()
    stage = {'schema': STAGE_SCHEMA, 'draft': draft, 'draft_sha256': _digest(draft),
             'identity': identity, 'receipt_sha256': receipt_digest,
             'request_output': str(request), 'review_complete': False}
    data = (json.dumps(stage, sort_keys=True, allow_nan=False, indent=2) + '\n').encode()
    _require(len(data) <= MAX_STAGE_BYTES, 'arm_document_too_large')
    _require(capture_source_identity(capture=path, now=now) == identity
             and hashlib.sha256(_receipt_bytes(receipt_path)).hexdigest() == receipt_digest,
             'capture_changed_during_packaging')
    created = False
    try:
        with target.open('xb') as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise PlayRequestError('output_already_exists') from None
    except OSError:
        if created:
            target.unlink(missing_ok=True)
        raise PlayRequestError('output_write_failed') from None
    expiry = datetime.fromisoformat(identity['source']['captured_at']) + timedelta(seconds=MAX_AGE)
    return {'status': 'awaiting_exact_image_review', 'stage_file': str(target),
            'stage_sha256': hashlib.sha256(data).hexdigest(), 'image_path': str(path),
            'run_id': draft['run_id'], 'expected_screen': draft['screen'],
            'expires_at': expiry.isoformat(), 'review_complete': False,
            'runtime_authorized': False, 'controller_input_sent': False}


def confirm_arm_review(stage_path, *, stage_sha256, run_id, screen, note, reviewed, now=None):
    """Acknowledge the emitted preview, then delegate to the unchanged arm checks."""
    _require(reviewed is True, 'exact_image_review_required')
    _text(stage_sha256, 64, 'stage_hash_required')
    stage, raw = read_document(stage_path, with_bytes=True)
    _require(len(raw) <= MAX_STAGE_BYTES and hashlib.sha256(raw).hexdigest() == stage_sha256,
             'arm_stage_changed')
    _require(set(stage) == {'schema', 'draft', 'draft_sha256', 'identity', 'receipt_sha256',
                           'request_output', 'review_complete'} and stage['schema'] == STAGE_SCHEMA
             and stage['review_complete'] is False, 'arm_stage_invalid')
    draft = stage['draft']
    validate_arm_draft(draft)
    _require(stage['draft_sha256'] == _digest(draft), 'arm_draft_changed')
    _require(run_id == draft['run_id'], 'review_run_mismatch')
    _require(screen == draft['screen'], 'review_screen_mismatch')
    _text(note, 4096, 'evidence_note_invalid')
    identity = stage['identity']
    _require(isinstance(identity, dict) and isinstance(identity.get('source'), dict), 'arm_stage_invalid')
    capture = identity['source'].get('path')
    _require(capture_source_identity(capture=capture, now=now) == identity, 'arm_capture_changed')
    _require(hashlib.sha256(_receipt_bytes(Path(capture).with_suffix('.capture.json'))).hexdigest()
             == stage['receipt_sha256'], 'arm_receipt_changed')
    _require(Path(stage_path).read_bytes() == raw, 'arm_stage_changed')
    return write_arm_request(run_id=run_id, capture=capture, screen=screen,
        reviewer=draft['reviewer'], evidence_note=note, phrase=draft['phrase'],
        reviewed=True, exclusive_client_confirmed=draft['exclusive_client_confirmed'],
        output=stage['request_output'], now=now, expected_identity=identity,
        expected_receipt_sha256=stage['receipt_sha256'])
