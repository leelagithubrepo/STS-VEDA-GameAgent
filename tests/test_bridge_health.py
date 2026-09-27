"""Offline tests: fake transports only; never import or connect the live SDK."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from contextlib import redirect_stdout
from dataclasses import dataclass
import io
import json
import struct
from types import SimpleNamespace
import unittest

from scripts.bridge_health import BridgeHealth, BridgeHealthError
from scripts.warm_bridge import _close_bridge, _handle, _read_command


class Clock:
    def __init__(self):
        self.now = 10.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Protocol:
    def __init__(self):
        self.closed = False
        self.received = 0
        self.raw_error_handler_called = False

    def datagram_received(self, data, addr):
        self.received += 1

    def data_received(self, data):
        self.received += 1

    def connection_lost(self, exc):
        self.closed = True

    def error_received(self, exc):
        self.raw_error_handler_called = True


@dataclass
class State:
    left: tuple = (0.0, 0.0)
    right: tuple = (0.0, 0.0)


class Stream:
    def __init__(self):
        self._protocol = Protocol()
        self.stop_event = asyncio.Event()
        self._stream_info = {"afk_timeout": 100, "afk_timeout_disconnect": 200,
                             "unrelated_secret": "DO_NOT_PRINT"}
        self.packets = []
        self.fail_sends = False

    def send_feedback(self, kind, sequence, data=b"", state=None):
        struct.pack("!H", sequence)  # Same 16-bit bound as FeedbackHeader.
        if self.fail_sends:
            raise OSError("secret endpoint DO_NOT_PRINT")
        self.packets.append((kind, sequence, data, deepcopy(state)))


class Worker:
    def __init__(self):
        self.alive = True
        self.joined = False
        self.stubborn = False

    def join(self, timeout):
        self.joined = True

    def is_alive(self):
        return self.alive


class Controller:
    def __init__(self, stream):
        self.stream = stream
        self.stick_state = State()
        self._last_state = State()
        self._sequence_state = 0
        self._sequence_event = 0
        self._thread = Worker()
        self.tap_wait = None

    def stop(self):
        if not self._thread.stubborn:
            self._thread.alive = False

    def stick(self, side, point):
        setattr(self.stick_state, side, point)

    def update_sticks(self):
        raise AssertionError("SDK sender must be replaced before use")

    async def async_button(self, button, action, delay=0):
        if action == "tap":
            await self.async_button(button, "press")
            if self.tap_wait is not None:
                await self.tap_wait.wait()
            else:
                await asyncio.sleep(0)
            await self.async_button(button, "release")
            return
        self.stream.send_feedback("EVENT", self._sequence_event, data=(button, action))
        self._sequence_event += 1


class Service:
    def __init__(self):
        self.stream = Stream()
        self.session = SimpleNamespace(stream=self.stream, _protocol=Protocol(),
                                       is_ready=True, is_stopped=False, _hb_last=0)
        self.controller = Controller(self.stream)
        self.device = SimpleNamespace(session=self.session)
        self.reconnects = 0
        self.released = False
        self.closed = False

    async def ensure_connected(self, *, wake=True):
        if not self.session.is_ready:
            self.reconnects += 1
            self.session.is_ready = True

    async def tap(self, buttons, delay=0.1, gap=0.1):
        await self.ensure_connected()
        for button in buttons:
            await self.controller.async_button(button, "tap", delay)
        return buttons

    async def stick(self, side, x, y):
        await self.ensure_connected()
        self.controller.stick(side, point=(x, y))
        self.controller.update_sticks()
        return {"stick": side, "x": x, "y": y}

    async def status(self):
        return {"on": True, "session_ready": self.session.is_ready,
                "host": "DO_NOT_PRINT", "user": "DO_NOT_PRINT"}

    async def release_all(self):
        self.released = True
        if not self.session.is_ready:
            return
        await self.controller.async_button("cross", "release")
        self.controller.stick("left", point=(0.0, 0.0))
        self.controller.stick("right", point=(0.0, 0.0))
        self.controller.update_sticks()

    async def close(self):
        self.closed = True
        self.session.is_ready = False
        self.session.is_stopped = True
        self.stream._protocol.closed = True
        self.session._protocol.closed = True


class BridgeHealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = Clock()
        self.service = Service()
        self.health = BridgeHealth(self.service, "STATE", clock=self.clock, wall_clock=lambda: 1000.0)
        self.addAsyncCleanup(self.health.close)

    async def test_retires_worker_and_refreshes_unchanged_neutral_state(self):
        await self.health.start()
        self.assertTrue(self.service.controller._thread.joined)
        self.assertFalse(self.service.controller._thread.alive)
        self.assertEqual(1, len(self.service.stream.packets))
        self.clock.advance(0.199)
        self.health.refresh_due()
        self.assertEqual(1, len(self.service.stream.packets))
        self.clock.advance(0.002)
        self.health.refresh_due()
        self.assertEqual(["STATE", "STATE"], [p[0] for p in self.service.stream.packets])
        self.assertTrue(all(p[3] == State() for p in self.service.stream.packets))

    async def test_refresh_loop_runs_while_input_is_waiting_for_release(self):
        requests = asyncio.Queue()

        async def manual_sleep(delay):
            wake = asyncio.get_running_loop().create_future()
            requests.put_nowait((delay, wake))
            await wake

        self.health.sleep = manual_sleep
        await self.health.start()
        self.service.controller.tap_wait = asyncio.Event()
        tap = asyncio.create_task(self.health.execute(
            lambda: self.service.tap(["cross"]), input_action="tap"))
        delay, wake = await requests.get()
        await asyncio.sleep(0)
        self.assertAlmostEqual(0.2, delay)
        self.assertEqual(("cross", "press"), self.service.stream.packets[-1][2])
        self.clock.advance(0.201)
        wake.set_result(None)
        await requests.get()
        self.assertEqual("STATE", self.service.stream.packets[-1][0])
        self.service.controller.tap_wait.set()
        await tap
        events = [p[2] for p in self.service.stream.packets if p[0] == "EVENT"]
        self.assertEqual([("cross", "press"), ("cross", "release")], events)

    async def test_refresh_preserves_current_non_neutral_sticks(self):
        await self.health.start()
        await self.health.execute(lambda: self.service.stick("left", 0.5, -0.25), input_action="stick")
        await self.health.execute(lambda: self.service.stick("right", -0.4, 0.7), input_action="stick")
        self.clock.advance(0.201)
        self.health.refresh_due()
        self.assertEqual(State((0.5, -0.25), (-0.4, 0.7)), self.service.stream.packets[-1][3])
        self.assertEqual("stick", self.health.status()["last_input_action"])

    async def test_state_and_event_wire_sequences_wrap_at_16_bits(self):
        await self.health.start()
        self.service.controller._sequence_state = 65535
        for _ in range(2):
            self.clock.advance(0.201)
            self.health.refresh_due()
        self.assertEqual([65535, 0], [p[1] for p in self.service.stream.packets[-2:]])
        self.assertEqual(1, self.service.controller._sequence_state)
        self.service.controller._sequence_event = 65535
        await self.health.execute(lambda: self.service.tap(["cross"]), input_action="tap")
        self.assertEqual([65535, 0], [p[1] for p in self.service.stream.packets[-2:]])

    async def test_send_error_latches_blocks_inputs_and_never_reconnects(self):
        await self.health.start()
        self.service.stream.fail_sends = True
        self.clock.advance(0.201)
        with self.assertRaisesRegex(BridgeHealthError, "send failed"):
            self.health.refresh_due()
        self.assertTrue(self.health.failed.is_set())
        with self.assertRaises(BridgeHealthError):
            await self.health.execute(lambda: self.service.tap(["cross"]), input_action="tap")
        self.assertEqual(1, len(self.service.stream.packets))
        self.assertEqual(0, self.service.reconnects)
        self.assertNotIn("DO_NOT_PRINT", json.dumps(self.health.status()))
        await self.health.close()
        self.assertTrue(self.service.closed)

    async def test_cached_ready_does_not_override_closed_transport(self):
        await self.health.start()
        self.service.stream._protocol.closed = True
        self.assertTrue(self.service.session.is_ready)
        with self.assertRaises(BridgeHealthError):
            await self.service.ensure_connected()
        self.assertFalse(self.health.status()["transport_ready"])
        self.assertEqual(0, self.service.reconnects)

    async def test_sdk_style_transport_closing_is_detected_before_callback(self):
        await self.health.start()
        protocol = self.service.stream._protocol
        del protocol.closed
        protocol.transport = SimpleNamespace(is_closing=lambda: True)
        with self.assertRaises(BridgeHealthError):
            self.health.refresh_due()
        self.assertTrue(self.health.failed.is_set())
        self.assertEqual(1, len(self.service.stream.packets))

    async def test_replaced_session_is_rejected(self):
        await self.health.start()
        self.service.device.session = SimpleNamespace(is_ready=True)
        with self.assertRaises(BridgeHealthError):
            await self.health.execute(self.service.status)
        self.assertEqual(0, self.service.reconnects)

    async def test_protocol_error_is_sanitized_and_wakes_failure_waiter(self):
        await self.health.start()
        protocol = self.service.stream._protocol
        protocol.error_received(OSError("DO_NOT_PRINT"))
        self.assertTrue(self.health.failed.is_set())
        self.assertEqual("transport_error_received", self.health.status()["error_code"])
        self.assertFalse(protocol.raw_error_handler_called)
        self.assertNotIn("DO_NOT_PRINT", json.dumps(self.health.status()))

    async def test_fault_cancels_active_command_without_waiting_for_its_delay(self):
        await self.health.start()
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def blocked_command():
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        command = asyncio.create_task(self.health.execute(blocked_command, input_action="tap"))
        await entered.wait()
        self.service.stream._protocol.error_received(OSError("DO_NOT_PRINT"))
        with self.assertRaises(BridgeHealthError):
            await asyncio.wait_for(command, 1.0)
        self.assertTrue(cancelled.is_set())
        await self.health.close()
        self.assertTrue(self.service.released)
        self.assertTrue(self.service.closed)

    async def test_connection_lost_latches_and_preserves_transport_close(self):
        await self.health.start()
        self.service.session._protocol.connection_lost(OSError("DO_NOT_PRINT"))
        self.assertTrue(self.health.failed.is_set())
        self.assertTrue(self.service.session._protocol.closed)

    async def test_health_ages_unknowns_and_raw_afk_units(self):
        await self.health.start()
        status = self.health.status()
        self.assertIsNone(status["last_receive_age_ms"])
        self.assertIsNone(status["last_heartbeat_age_ms"])
        self.assertIsNone(status["last_input_age_ms"])
        self.service.stream._protocol.datagram_received(b"not logged", ("DO_NOT_PRINT", 1))
        self.service.session._hb_last = 997.0
        self.clock.advance(0.05)
        status = self.health.status()
        self.assertEqual(50, status["last_receive_age_ms"])
        self.assertEqual(3000, status["last_heartbeat_age_ms"])
        self.assertEqual({"afk_timeout": 100, "afk_timeout_disconnect": 200}, status["afk_raw"])
        self.assertEqual("unknown", status["afk_units"])
        self.assertFalse(status["input_delivery_verified"])
        self.assertNotIn("DO_NOT_PRINT", json.dumps(status))

    async def test_normal_shutdown_cancels_refresh_releases_and_closes(self):
        await self.health.start()
        await self.health.execute(lambda: self.service.stick("left", 0.8, 0), input_action="stick")
        await self.health.close()
        self.assertTrue(self.service.released)
        self.assertTrue(self.service.closed)
        self.assertTrue(self.health._task.done())
        self.assertEqual(State(), self.service.stream.packets[-1][3])
        packet_count = len(self.service.stream.packets)
        await self.health.close()
        self.assertEqual(packet_count, len(self.service.stream.packets))
        with self.assertRaises(BridgeHealthError):
            await self.health.execute(lambda: self.service.tap(["cross"]))

    async def test_stubborn_worker_blocks_start_without_feedback(self):
        self.service.controller._thread.stubborn = True
        with self.assertRaisesRegex(BridgeHealthError, "worker did not stop"):
            await self.health.start()
        await self.health.close()
        self.assertEqual([], self.service.stream.packets)
        self.assertTrue(self.service.closed)

    async def test_wrapper_status_preserves_protocol_without_identifiers(self):
        await self.health.start()
        response, closing = await _handle(self.service, self.health, {"action": "status"})
        self.assertFalse(closing)
        self.assertIsNone(response["on"])
        self.assertFalse(response["power_state_probed"])
        self.assertEqual("owned_live_transport", response["readiness_source"])
        self.assertTrue(response["session_ready"])
        self.assertIn("health", response)
        self.assertNotIn("DO_NOT_PRINT", json.dumps(response))

    async def test_wrapper_rejects_infinite_tap_delay_without_event(self):
        await self.health.start()
        with self.assertRaises(ValueError):
            await _handle(self.service, self.health, {"action": "tap", "buttons": ["cross"], "delay": float("inf")})
        self.assertEqual(["STATE"], [p[0] for p in self.service.stream.packets])

    async def test_fault_interrupts_pending_stdin_without_another_command(self):
        await self.health.start()
        reader = asyncio.StreamReader()
        pending = asyncio.create_task(_read_command(reader, self.health))
        await asyncio.sleep(0)
        self.service.stream._protocol.error_received(OSError("private"))
        with self.assertRaises(BridgeHealthError):
            await asyncio.wait_for(pending, 1.0)

    async def test_stdin_command_eof_and_explicit_idle_timeout(self):
        await self.health.start()
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"action":"status"}\n')
        self.assertEqual(b'{"action":"status"}\n', await _read_command(reader, self.health))
        self.assertIsNone(await _read_command(reader, self.health, 0.001))
        reader.feed_eof()
        self.assertEqual(b"", await _read_command(reader, self.health))

    async def test_cleanup_exception_is_contained_without_sensitive_output(self):
        await self.health.start()

        async def fail_close():
            raise OSError("DO_NOT_PRINT endpoint/account")

        self.service.close = fail_close
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertFalse(await _close_bridge(self.service, self.health))
        response = json.loads(output.getvalue())
        self.assertFalse(response["ok"])
        self.assertEqual("controller_fault", response["event"])
        self.assertIn("not confirmed", response["error"])
        self.assertNotIn("DO_NOT_PRINT", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


if __name__ == "__main__":
    unittest.main()
