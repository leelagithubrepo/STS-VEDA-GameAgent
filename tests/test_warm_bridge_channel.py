"""Offline command-channel startup: local temporary sockets and fake SDK only."""
import asyncio
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import veda_reviewed_play, warm_bridge
from tests.test_bridge_health import Service
from veda.bridge_channel import DEFAULT_BRIDGE_SOCKET


class ChannelArgumentsTests(unittest.TestCase):
    def test_server_cleanup_option_is_compatible_with_old_and_new_asyncio(self):
        def modern(protocol_factory, *, sock=None, cleanup_socket=True):
            pass

        def legacy(protocol_factory, *, sock=None):
            pass

        self.assertEqual({"cleanup_socket": False}, warm_bridge._socket_cleanup_options(
            SimpleNamespace(create_unix_server=modern)))
        self.assertEqual({}, warm_bridge._socket_cleanup_options(
            SimpleNamespace(create_unix_server=legacy)))

    def test_default_channel_matches_reviewed_adapter(self):
        args = warm_bridge._parse_args([])
        self.assertEqual(DEFAULT_BRIDGE_SOCKET, args.socket)
        self.assertEqual(veda_reviewed_play.DEFAULT_BRIDGE_SOCKET, args.socket)
        self.assertFalse(args.stdio)

    def test_stdio_is_explicit_and_conflicts_with_socket(self):
        self.assertIsNone(warm_bridge._parse_args(["--stdio"]).socket)
        self.assertEqual("/tmp/example.sock", warm_bridge._parse_args(
            ["--socket", "/tmp/example.sock"]).socket)
        for arguments in (["--socket", "/tmp/example.sock", "--stdio"], ["--socket", ""]):
            with self.subTest(arguments=arguments), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    warm_bridge._parse_args(arguments)


class FakeService(Service):
    def __init__(self):
        super().__init__()
        self.connects = 0

    async def connect(self):
        self.connects += 1

    async def tap(self, *args, **kwargs):
        raise AssertionError("Startup test must never submit a gameplay tap")

    async def stick(self, *args, **kwargs):
        raise AssertionError("Startup test must never move a stick")


class WarmBridgeChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory(prefix="veda-channel-", dir="/tmp")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "bridge.sock"
        self.service = FakeService()
        self.sdk = {
            "ps5rmtctl.config": SimpleNamespace(get_default=lambda key: "fake"),
            "ps5rmtctl.service": SimpleNamespace(PS5Service=lambda *a, **kw: self.service),
            "pyremoteplay.stream_packets": SimpleNamespace(
                FeedbackHeader=SimpleNamespace(Type=SimpleNamespace(STATE="STATE"))),
        }

    async def test_default_startup_exposes_connectable_channel_and_cleans_owned_socket(self):
        ready = asyncio.Event()
        emitted = []

        def emit(value):
            emitted.append(value)
            if value.get("event") == "ready":
                ready.set()

        # Change only the destination of the shared default; exercise no --socket.
        with patch.object(warm_bridge, "DEFAULT_BRIDGE_SOCKET", str(self.path)), \
                patch.dict(sys.modules, self.sdk), patch.object(warm_bridge, "_emit", emit):
            task = asyncio.create_task(warm_bridge._run(warm_bridge._parse_args([])))
            try:
                await asyncio.wait_for(ready.wait(), 2)
                payload = next(item for item in emitted if item.get("event") == "ready")
                self.assertEqual("unix_socket", payload["command_channel"])
                self.assertEqual(str(self.path), payload["socket_path"])
                self.assertTrue(payload["session_ready"])
                self.assertEqual(0o600, self.path.stat().st_mode & 0o777)
                reader, writer = await asyncio.open_unix_connection(str(self.path))
                try:
                    # Exercise the same status/close JSONL protocol used by adapter.
                    for operation in ("status", "close"):
                        writer.write((json.dumps({"action": operation}) + "\n").encode())
                        await writer.drain()
                        reply = json.loads(await asyncio.wait_for(reader.readline(), 1))
                        self.assertTrue(reply["ok"], reply)
                    self.assertEqual(0, await asyncio.wait_for(task, 2))
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(1, self.service.connects)
        self.assertTrue(self.service.closed)
        self.assertFalse(self.path.exists())

    async def test_explicit_stdio_ready_has_no_socket_and_keeps_eof_cleanup(self):
        class StdinTransport:
            closed = False

            def close(self):
                self.closed = True

        transport = StdinTransport()

        async def fake_pipe(factory, stream):
            protocol = factory()
            protocol._stream_reader.feed_eof()
            return transport, protocol

        emitted = []
        with patch.dict(sys.modules, self.sdk), patch.object(warm_bridge, "_emit", emitted.append), \
                patch.object(asyncio.get_running_loop(), "connect_read_pipe", fake_pipe), \
                patch.object(warm_bridge, "_bind_command_socket") as bind:
            result = await warm_bridge._run(warm_bridge._parse_args(["--stdio"]))
        self.assertEqual(0, result)
        bind.assert_not_called()
        payload = next(item for item in emitted if item.get("event") == "ready")
        self.assertEqual("stdio", payload["command_channel"])
        self.assertIsNone(payload["socket_path"])
        self.assertTrue(transport.closed)
        self.assertTrue(self.service.closed)

    async def test_existing_file_symlink_and_active_socket_reject_before_service_start(self):
        for kind in ("file", "symlink", "socket"):
            with self.subTest(kind=kind):
                owner = None
                if kind == "file":
                    self.path.write_text("belongs to somebody else")
                elif kind == "symlink":
                    self.path.symlink_to(Path(self.directory.name) / "missing-target")
                else:
                    owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    owner.bind(str(self.path))
                    owner.listen(1)
                before = self.path.lstat()
                emitted = []
                factory = Mock(side_effect=AssertionError("must not initialize SDK"))
                try:
                    with patch.dict(sys.modules, {**self.sdk, "ps5rmtctl.service":
                            SimpleNamespace(PS5Service=factory)}), \
                            patch.object(warm_bridge, "_emit", emitted.append):
                        result = await warm_bridge._run(warm_bridge._parse_args(
                            ["--socket", str(self.path)]))
                    self.assertEqual(2, result)
                    self.assertEqual("socket_path_exists", emitted[0]["error"])
                    factory.assert_not_called()
                    after = self.path.lstat()
                    self.assertEqual((before.st_dev, before.st_ino), (after.st_dev, after.st_ino))
                    if kind == "file":
                        self.assertEqual("belongs to somebody else", self.path.read_text())
                finally:
                    if owner is not None:
                        owner.close()
                    self.path.unlink()

    async def test_atomic_bind_does_not_replace_owner_that_arrived_after_precheck(self):
        owner, identity = warm_bridge._bind_command_socket(self.path)
        try:
            with self.assertRaisesRegex(warm_bridge.CommandSocketError, "socket_path_exists"):
                warm_bridge._bind_command_socket(self.path)
            current = self.path.lstat()
            self.assertEqual(identity, (current.st_dev, current.st_ino))
        finally:
            owner.close()
            warm_bridge._unlink_owned_socket(self.path, identity)

    async def test_cleanup_keeps_replacement_and_never_unlinks_without_ownership(self):
        owner, identity = warm_bridge._bind_command_socket(self.path)
        try:
            self.assertTrue(warm_bridge._unlink_owned_socket(self.path, None))
            self.assertTrue(self.path.exists())
            self.path.unlink()
            self.path.write_text("replacement owner")
            self.assertTrue(warm_bridge._unlink_owned_socket(self.path, identity))
            self.assertEqual("replacement owner", self.path.read_text())
        finally:
            owner.close()

    async def test_full_server_rejects_replacement_before_or_after_asyncio_setup(self):
        real_start_server = asyncio.start_unix_server
        for moment in ("before", "after"):
            with self.subTest(moment=moment):
                self.service = FakeService()
                servers = []
                emitted = []

                def replace_path():
                    self.path.unlink()
                    self.path.write_text("replacement owner")
                    self.path.chmod(0o640)

                async def race_server(*args, **kwargs):
                    if moment == "before":
                        replace_path()
                    server = await real_start_server(*args, **kwargs)
                    servers.append(server)
                    if moment == "after":
                        replace_path()
                    return server

                try:
                    with patch.dict(sys.modules, self.sdk), \
                            patch.object(warm_bridge, "_emit", emitted.append), \
                            patch.object(asyncio, "start_unix_server", race_server):
                        result = await warm_bridge._run(warm_bridge._parse_args(
                            ["--socket", str(self.path)]))
                    self.assertEqual(2, result)
                    self.assertEqual("socket_path_changed", emitted[0]["error"])
                    self.assertNotIn("ready", [item.get("event") for item in emitted])
                    self.assertEqual("replacement owner", self.path.read_text())
                    self.assertEqual(0o640, self.path.stat().st_mode & 0o777)
                    self.assertEqual(1, len(servers))
                    self.assertFalse(servers[0].is_serving())
                    self.assertTrue(self.service.closed)
                finally:
                    self.path.unlink(missing_ok=True)

    async def test_bind_race_during_connect_is_reported_and_owner_preserved(self):
        owner = None

        async def connect():
            nonlocal owner
            owner = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            owner.bind(str(self.path))

        self.service.connect = connect
        emitted = []
        try:
            with patch.dict(sys.modules, self.sdk), patch.object(warm_bridge, "_emit", emitted.append):
                result = await warm_bridge._run(warm_bridge._parse_args(["--socket", str(self.path)]))
            self.assertEqual(2, result)
            self.assertEqual("socket_path_exists", emitted[0]["error"])
            self.assertTrue(self.path.exists())
            self.assertTrue(self.service.closed)
            self.assertNotIn("ready", [item.get("event") for item in emitted])
        finally:
            if owner is not None:
                owner.close()


if __name__ == "__main__":
    unittest.main()
