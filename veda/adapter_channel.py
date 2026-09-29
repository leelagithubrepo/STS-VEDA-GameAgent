"""Local request/reply transport for an already-owned reviewed adapter.

This channel grants no game authority. The session validates every request.
Clients send once and return on a complete correlated reply, never process exit.
"""
from pathlib import Path
import hashlib
import json
import os
import socket
import stat
from uuid import uuid4

from .request_envelope import MAX_BYTES, decode

SCHEMA = 'veda.adapter-submit.v1'


def session_directory(path):
    path = Path(path).expanduser().resolve()
    return path.parent if path.name == 'state.json' else path


def socket_path(session):
    digest = hashlib.sha256(str(session_directory(session)).encode()).hexdigest()[:24]
    return Path('/tmp')/('veda-reviewed-' + digest + '.sock')


def receive_line(connection):
    chunks = bytearray()
    while len(chunks) <= MAX_BYTES:
        piece = connection.recv(min(65536, MAX_BYTES + 1 - len(chunks)))
        if not piece:
            raise ValueError('incomplete adapter reply/request')
        chunks.extend(piece)
        if b'\n' in chunks:
            line, rest = bytes(chunks).split(b'\n', 1)
            if rest.strip():
                raise ValueError('one request per connection required')
            return decode(line)
    raise ValueError('adapter message exceeds byte bound')


class Submission:
    def __init__(self, connection, envelope, session):
        self.connection = connection
        if (not isinstance(envelope, dict) or set(envelope) != {'schema', 'session', 'submission_id', 'request'}
                or envelope['schema'] != SCHEMA or envelope['session'] != str(session)
                or not isinstance(envelope['submission_id'], str) or len(envelope['submission_id']) != 32
                or not isinstance(envelope['request'], dict)):
            raise ValueError('invalid adapter submission or session identity')
        self.identity = envelope['submission_id']
        self.raw = json.dumps(envelope['request'], allow_nan=False)

    def reply(self, result):
        try:
            self.connection.sendall((json.dumps({'schema': SCHEMA, 'submission_id': self.identity,
                'result': result}, allow_nan=False) + '\n').encode())
        except OSError:
            # A lost reply does not undo an executed input or permit a retry.
            pass
        finally:
            self.connection.close()


class RequestServer:
    def __init__(self, session):
        self.session = session_directory(session)
        self.path = socket_path(session)
        self.listener = None
        self.owned_inode = None

    def __enter__(self):
        # The caller must already own ReviewedPlaySession's exclusive lock.
        if self.path.exists():
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('adapter endpoint is not an owned socket')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(.2)
                try:
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()  # Dead endpoint, under the exclusive session lock.
                else:
                    raise ValueError('adapter request endpoint already active')
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            self.owned_inode = self.path.stat().st_ino
            self.listener.listen(4)
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def accept(self):
        connection, _ = self.listener.accept()
        connection.settimeout(2)
        try:
            return Submission(connection, receive_line(connection), self.session)
        except (OSError, ValueError, TypeError):
            connection.close()
            return None

    def __exit__(self, *_):
        if self.listener is not None:
            self.listener.close()
        if self.owned_inode is not None:
            try:
                if self.path.lstat().st_ino == self.owned_inode:
                    self.path.unlink()
            except FileNotFoundError:
                pass


class ReplyUnknown(RuntimeError):
    """Submission crossed transport; inspect session before another game input."""


def submit(session, request, *, timeout=8):
    if not isinstance(request, dict) or not 0 < timeout <= 60:
        raise ValueError('one request object and bounded reply timeout required')
    identity = uuid4().hex
    envelope = {'schema': SCHEMA, 'session': str(session_directory(session)),
                'submission_id': identity, 'request': request}
    raw = (json.dumps(envelope, allow_nan=False) + '\n').encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('adapter submission exceeds byte bound')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(socket_path(session)))
        try:
            connection.sendall(raw)
            reply = receive_line(connection)
            if (not isinstance(reply, dict) or reply.get('schema') != SCHEMA
                    or reply.get('submission_id') != identity or not isinstance(reply.get('result'), dict)):
                raise ValueError('adapter reply identity differs')
            return reply['result']
        except (OSError, ValueError) as error:
            raise ReplyUnknown('Reply unavailable after submission; inspect session pending/last_verified. Never resend the input.') from error
