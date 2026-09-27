"""Offline JSONL transport tests: local test sockets and fake services only."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.warm_bridge import _CommandDispatcher, _read_command, _serve_client
from veda.bridge_client import BridgeClient


class FakeHealth:
    def __init__(self):
        self.failed = asyncio.Event()

    async def execute(self, operation, *, input_action=None):
        return await operation()

    def check_ready(self):
        if self.failed.is_set():
            raise RuntimeError("synthetic transport stopped")

    def status(self):
        return {"transport_ready": True, "refresh_running": True, "error_present": False,
                "error_code": None, "input_delivery_verified": False}


class FakeService:
    def __init__(self):
        self.calls = []
        self.in_flight = 0
        self.max_in_flight = 0

    async def tap(self, buttons, *, delay, gap):
        self.calls.append(list(buttons))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(delay)
            return buttons
        finally:
            self.in_flight -= 1

    async def status(self):
        return {"on": True, "session_ready": True, "private": "DO_NOT_PRINT"}


class ClientSocketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="veda-bridge-test-", dir="/tmp")
        self.path = str(Path(self.temp.name) / "test.sock")
        self.service, self.health = FakeService(), FakeHealth()
        self.dispatcher = _CommandDispatcher(self.service, self.health)
        self.connections = 0
        self.server = None
        self.tasks = set()
        self.clients = []

    async def start_server(self, handler=None):
        async def serve(reader, writer):
            self.connections += 1
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                if handler:
                    await handler(reader, writer)
                else:
                    await _serve_client(reader, writer, self.dispatcher, self.health)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass
                self.tasks.discard(task)

        self.server = await asyncio.start_unix_server(serve, path=self.path, limit=16384)

    def client(self, **kwargs):
        client = BridgeClient(self.path, **kwargs)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        for client in self.clients:
            client.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.temp.cleanup()

    async def test_multiple_calls_reuse_one_connection_and_correlate_unique_ids(self):
        await self.start_server()
        client = self.client()
        results = [await asyncio.to_thread(client.call, {"action": "status"}) for _ in range(5)]
        self.assertEqual(1, self.connections)
        self.assertEqual(5, len({r["request_id"] for r in results}))
        self.assertTrue(all(r["status"] == "ok" for r in results))
        self.assertNotIn("DO_NOT_PRINT", json.dumps(results))
        self.assertFalse(results[0]["health"]["input_delivery_verified"])

    async def test_duplicate_id_across_connections_never_reexecutes(self):
        await self.start_server()
        command = {"request_id": str(uuid4()), "action": "tap", "buttons": ["cross"], "delay": .02}
        first, second = await asyncio.gather(
            asyncio.to_thread(self.client().call, command),
            asyncio.to_thread(self.client().call, command))
        self.assertTrue(first["ok"] and second["ok"])
        self.assertTrue(first.get("duplicate") or second.get("duplicate"))
        self.assertEqual([["cross"]], self.service.calls)
        conflict = await asyncio.to_thread(self.client().call, dict(command, buttons=["circle"]))
        self.assertEqual("request_id_conflict", conflict["error"])
        self.assertEqual([["cross"]], self.service.calls)

    async def test_separate_persistent_clients_share_one_execution_lock(self):
        await self.start_server()
        commands = [{"action": "tap", "buttons": [b], "delay": .03} for b in ("left", "right")]
        results = await asyncio.gather(*[
            asyncio.to_thread(self.client().call, command) for command in commands])
        self.assertTrue(all(r["ok"] for r in results))
        self.assertEqual(2, len(self.service.calls))
        self.assertEqual(1, self.service.max_in_flight)

    async def test_unknown_response_timeout_latches_without_reconnect_or_replay(self):
        seen = []

        async def no_response(reader, writer):
            seen.append(await reader.readline())
            await asyncio.sleep(.2)

        await self.start_server(no_response)
        client = self.client(read_timeout=.03)
        began = time.monotonic()
        result = await asyncio.to_thread(client.call, {"action": "tap", "buttons": ["cross"]})
        self.assertEqual("unknown_outcome", result["status"])
        self.assertLess(time.monotonic() - began, .2)
        again = await asyncio.to_thread(client.call, {"action": "tap", "buttons": ["cross"]})
        self.assertEqual("reconciliation_required", again["error"])
        self.assertEqual("not_sent", again["status"])
        self.assertEqual(1, len(seen))
        self.assertEqual(1, self.connections)

    async def test_slow_drip_response_has_total_deadline_not_per_read_deadline(self):
        async def slow_response(reader, writer):
            request = json.loads(await reader.readline())
            response = json.dumps({"ok": True, "request_id": request["request_id"]}).encode() + b"\n"
            try:
                for byte in response:
                    writer.write(bytes([byte]))
                    await writer.drain()
                    await asyncio.sleep(.01)
            except (ConnectionResetError, BrokenPipeError):
                pass  # The deadline test intentionally closes the client mid-response.

        await self.start_server(slow_response)
        began = time.monotonic()
        result = await asyncio.to_thread(self.client(read_timeout=.05).call, {"action": "status"})
        self.assertEqual("unknown_outcome", result["status"])
        self.assertLess(time.monotonic() - began, .2)

    async def test_oversized_response_is_bounded_and_ambiguous(self):
        async def oversized(reader, writer):
            await reader.readline()
            writer.write(b"x" * 1000 + b"\n")
            await writer.drain()

        await self.start_server(oversized)
        result = await asyncio.to_thread(self.client(max_response_bytes=128).call, {"action": "status"})
        self.assertEqual("unknown_outcome", result["status"])

    async def test_mismatched_uuid_is_not_accepted_as_success(self):
        async def mismatch(reader, writer):
            await reader.readline()
            writer.write((json.dumps({"ok": True, "request_id": str(uuid4())}) + "\n").encode())
            await writer.drain()

        await self.start_server(mismatch)
        self.assertEqual("unknown_outcome", (await asyncio.to_thread(
            self.client().call, {"action": "status"}))["status"])

    async def test_old_socket_payload_without_request_id_still_works(self):
        await self.start_server()
        reader, writer = await asyncio.open_unix_connection(self.path)
        writer.write(b'{"action":"status"}\n')
        await writer.drain()
        response = json.loads(await reader.readline())
        self.assertTrue(response["ok"])
        self.assertEqual("status", response["action"])
        self.assertIn("request_id", response)
        writer.close()
        await writer.wait_closed()

    async def test_timeout_stops_server_and_cached_id_cannot_repeat(self):
        self.dispatcher = _CommandDispatcher(self.service, self.health, command_timeout=.02)
        await self.start_server()
        command = {"action": "tap", "buttons": ["cross"], "delay": .2, "request_id": str(uuid4())}
        result = await asyncio.to_thread(self.client().call, command)
        self.assertEqual("unknown_outcome", result["status"])
        self.assertEqual("command_timeout", result["error"])
        self.assertTrue(self.dispatcher.stopped.is_set())
        cached, _ = await self.dispatcher.dispatch(command)
        self.assertTrue(cached["duplicate"])
        self.assertEqual("unknown_outcome", cached["status"])
        rejected, _ = await self.dispatcher.dispatch({"action": "tap", "buttons": ["circle"]})
        self.assertEqual("not_sent", rejected["status"])
        self.assertEqual([["cross"]], self.service.calls)


class DispatcherAndFramingTests(unittest.IsolatedAsyncioTestCase):
    async def test_capacity_rejects_new_ids_without_evicting_old_results(self):
        service, health = FakeService(), FakeHealth()
        dispatcher = _CommandDispatcher(service, health, max_requests=1)
        command = {"action": "tap", "buttons": ["cross"], "delay": 0, "request_id": str(uuid4())}
        await dispatcher.dispatch(command)
        rejected, _ = await dispatcher.dispatch({"action": "status"})
        self.assertEqual("request_capacity_reached", rejected["error"])
        cached, _ = await dispatcher.dispatch(command)
        self.assertTrue(cached["duplicate"])
        self.assertEqual([["cross"]], service.calls)

    async def test_partial_frame_times_out_without_dispatch(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"action":')
        with self.assertRaises(asyncio.TimeoutError):
            await _read_command(reader, FakeHealth(), request_timeout=.01)

    async def test_oversized_and_truncated_frames_are_rejected(self):
        for payload in (b"x" * 200 + b"\n", b'{"action":"tap"}'):
            reader = asyncio.StreamReader(limit=256)
            reader.feed_data(payload)
            reader.feed_eof()
            with self.assertRaises(ValueError):
                await _read_command(reader, FakeHealth(), max_request_bytes=64)

    async def test_invalid_command_and_nonfinite_values_do_not_execute(self):
        service, health = FakeService(), FakeHealth()
        dispatcher = _CommandDispatcher(service, health)
        for command in ({"action": "reconnect"}, {"action": "tap", "buttons": []},
                        {"action": "tap", "buttons": ["cross"], "delay": float("inf")},
                        {"action": "tap", "buttons": ["cross"], "request_id": "not-uuid"}):
            response, _ = await dispatcher.dispatch(command)
            self.assertFalse(response["ok"])
        self.assertEqual([], service.calls)


class ClientLocalValidationTests(unittest.TestCase):
    def test_invalid_or_oversized_request_never_creates_a_socket(self):
        with patch("veda.bridge_client.socket.socket") as factory:
            client = BridgeClient("unused.sock", max_request_bytes=128)
            for command in ([], {"request_id": "bad"}, {"x": float("nan")}, {"x": "a" * 1000}):
                self.assertEqual("not_sent", client.call(command)["status"])
            factory.assert_not_called()

    def test_connect_timeout_is_not_sent_and_never_calls_send(self):
        with patch("veda.bridge_client.socket.socket") as factory:
            connection = factory.return_value
            connection.connect.side_effect = socket.timeout("DO_NOT_PRINT")
            response = BridgeClient("unused.sock", connect_timeout=.25).call({"action": "status"})
            self.assertEqual("not_sent", response["status"])
            connection.settimeout.assert_called_once_with(.25)
            connection.sendall.assert_not_called()
            connection.close.assert_called_once()
            self.assertNotIn("DO_NOT_PRINT", json.dumps(response))

    def test_possible_partial_write_is_unknown_and_never_replayed(self):
        with patch("veda.bridge_client.socket.socket") as factory:
            connection = factory.return_value
            connection.sendall.side_effect = socket.timeout("DO_NOT_PRINT")
            client = BridgeClient("unused.sock")
            response = client.call({"action": "tap", "buttons": ["cross"]})
            self.assertEqual("unknown_outcome", response["status"])
            self.assertEqual("not_sent", client.call({"action": "status"})["status"])
            connection.sendall.assert_called_once()
            factory.assert_called_once()

    def test_context_manager_close_is_idempotent_and_prevents_future_send(self):
        with patch("veda.bridge_client.socket.socket") as factory:
            with BridgeClient("unused.sock") as client:
                pass
            client.close()
            self.assertEqual("client_closed", client.call({"action": "status"})["error"])
            factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
