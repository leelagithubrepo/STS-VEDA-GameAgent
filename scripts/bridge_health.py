"""Bounded feedback refresh for VEDA's already-connected Remote Play session.

This adapter owns no credentials and never creates or reconnects a session.
It uses the pyremoteplay 0.7.6 controller/transport interface, injected at runtime
so its safety and timing behavior can be tested without the SDK or a console.
"""
from __future__ import annotations

import asyncio
import math
import threading
import time
from typing import Any, Callable


class BridgeHealthError(RuntimeError):
    """A latched, sanitized failure: further gameplay input is prohibited."""


class BridgeHealth:
    STATE_INTERVAL_SECONDS = 0.200

    def __init__(self, service: Any, state_type: Any, *, clock: Callable = time.monotonic,
                 wall_clock: Callable = time.time, sleep: Callable = asyncio.sleep) -> None:
        self.service = service
        self.session = service.device.session
        self.controller = service.controller
        self.stream = self.session.stream
        self.state_type = state_type
        self.clock = clock
        self.wall_clock = wall_clock
        self.sleep = sleep
        self.failed = asyncio.Event()
        self._command_lock = asyncio.Lock()
        self._task = None
        self._loop = None
        self._thread_id = None
        self._restores = []
        self._original_send_feedback = self.stream.send_feedback
        self._started = False
        self._closing = False
        self._closed = False
        self._cleanup = False
        self._error = None
        self._last_send = None
        self._last_state_send = None
        self._last_receive = None
        self._last_input = None
        self._last_input_action = None
        self._feedback_count = 0
        self._state_count = 0

    def _replace(self, obj: Any, name: str, value: Any) -> None:
        # Restore instance overrides accurately, including originally inherited methods.
        existing = vars(obj).get(name)
        self._restores.append((obj, name, name in vars(obj), existing))
        setattr(obj, name, value)

    def _fail(self, code: str) -> None:
        if self._closing or self._closed:
            return
        if self._error is None:
            self._error = code
        if self._loop is not None and threading.get_ident() != self._thread_id:
            self._loop.call_soon_threadsafe(self.failed.set)
        else:
            self.failed.set()

    def _transport_ready(self) -> bool:
        if self.service.device.session is not self.session:
            return False
        if self.service.controller is not self.controller or self.session.stream is not self.stream:
            return False
        if not self.session.is_ready or self.session.is_stopped:
            return False
        stop_event = getattr(self.stream, "stop_event", None)
        if stop_event is not None and stop_event.is_set():
            return False
        for owner in (self.session, self.stream):
            protocol = getattr(owner, "_protocol", None)
            if protocol is None or getattr(protocol, "closed", False):
                return False
            # pyremoteplay's actual TCP/UDP protocols expose transport rather
            # than a closed property; catch local close before its callback runs.
            if hasattr(protocol, "transport"):
                transport = protocol.transport
                if transport is None or transport.is_closing():
                    return False
        return True

    def check_ready(self) -> None:
        if self._closed or (self._closing and not self._cleanup):
            raise BridgeHealthError("controller session is closed")
        if self._error is not None and not self._cleanup:
            raise BridgeHealthError("controller transport failed; session must be closed")
        if not self._transport_ready():
            self._fail("transport_not_ready")
            raise BridgeHealthError("controller transport is not ready; reconnect was not attempted")

    async def _no_reconnect(self, *, wake: bool = True) -> None:
        # Replaces PS5Service.ensure_connected only for this owned session.
        self.check_ready()

    def _send_feedback(self, feedback_type: Any, sequence: int, data=b"", state=None) -> None:
        if threading.get_ident() != self._thread_id:
            self._fail("feedback_thread_conflict")
            raise BridgeHealthError("feedback must use the bridge event loop")
        self.check_ready()
        try:
            # FeedbackHeader packs an unsigned 16-bit sequence. Normalize both
            # channels; periodic refresh itself emits STATE only, never EVENT.
            self._original_send_feedback(feedback_type, sequence & 0xFFFF, data=data, state=state)
        except Exception:
            self._fail("feedback_send_failed")
            raise BridgeHealthError("controller feedback send failed") from None
        now = self.clock()
        self._last_send = now
        self._feedback_count += 1
        if feedback_type == self.state_type:
            self._last_state_send = now
            self._state_count += 1

    def _send_current_state(self) -> None:
        self.check_ready()
        sequence = self.controller._sequence_state & 0xFFFF
        # Never invent movement or overwrite a held stick with a neutral state.
        state = self.controller.stick_state
        self.stream.send_feedback(self.state_type, sequence, state=state)
        self.controller._sequence_state = (sequence + 1) & 0xFFFF
        self.controller._last_state.left = state.left
        self.controller._last_state.right = state.right

    def refresh_due(self) -> None:
        """Send a current-state refresh if due; called only on the owner loop."""
        self.check_ready()
        if self._last_state_send is None or self.clock() - self._last_state_send >= self.STATE_INTERVAL_SECONDS:
            self._send_current_state()

    def _watch_protocol(self, protocol: Any) -> None:
        for name in ("datagram_received", "data_received"):
            original = getattr(protocol, name, None)
            if original is None:
                continue

            def received(*args, _original=original, **kwargs):
                self._last_receive = self.clock()
                try:
                    return _original(*args, **kwargs)
                except Exception:
                    self._fail("transport_receive_failed")
                    return None

            self._replace(protocol, name, received)

        original_lost = getattr(protocol, "connection_lost", None)
        if original_lost is not None:
            def lost(exc):
                self._fail("transport_connection_lost")
                # The SDK logs exception contents, potentially including endpoints.
                return original_lost(None)

            self._replace(protocol, "connection_lost", lost)
        if hasattr(protocol, "error_received"):
            def error_received(exc):
                # The SDK's handler only logs; latch the fault without raw text.
                self._fail("transport_error_received")

            self._replace(protocol, "error_received", error_received)

    async def start(self) -> None:
        if self._started:
            raise BridgeHealthError("controller refresh is already started")
        self._loop = asyncio.get_running_loop()
        self._thread_id = threading.get_ident()
        self.check_ready()
        # PS5Service starts an SDK worker. Retire and join it before replacing
        # its sender: all subsequent feedback is synchronous on this event loop.
        worker = getattr(self.controller, "_thread", None)
        self.controller.stop()
        if worker is not None:
            await asyncio.to_thread(worker.join, 1.0)
            if worker.is_alive():
                self._fail("controller_worker_stop_failed")
                raise BridgeHealthError("controller worker did not stop")
        self._replace(self.service, "ensure_connected", self._no_reconnect)
        self._replace(self.controller, "update_sticks", self._send_current_state)
        self._replace(self.stream, "send_feedback", self._send_feedback)
        for protocol in (self.session._protocol, self.stream._protocol):
            self._watch_protocol(protocol)
        self._started = True
        self.refresh_due()
        self._task = asyncio.create_task(self._refresh_loop())

    async def _refresh_loop(self) -> None:
        try:
            while not self._closing:
                elapsed = 0.0 if self._last_state_send is None else self.clock() - self._last_state_send
                await self.sleep(max(0.001, self.STATE_INTERVAL_SECONDS - elapsed))
                self.refresh_due()
        except asyncio.CancelledError:
            pass
        except BridgeHealthError:
            pass  # The sender/check has already latched a sanitized fault.
        except Exception:
            self._fail("controller_refresh_failed")

    async def execute(self, operation: Callable, *, input_action: str | None = None) -> Any:
        """Serialize requested commands, allowing STATE refresh during tap delays.

        Every feedback send is synchronous on one event loop, so refresh cannot
        interleave packet construction even while a command awaits its release.
        """
        async with self._command_lock:
            self.check_ready()
            if input_action is not None:
                self._last_input = self.clock()
                self._last_input_action = input_action
            operation_task = None
            fault_task = None
            try:
                operation_task = asyncio.create_task(operation())
                fault_task = asyncio.create_task(self.failed.wait())
                done, _ = await asyncio.wait((operation_task, fault_task), return_when=asyncio.FIRST_COMPLETED)
                if fault_task in done:
                    raise BridgeHealthError("controller transport failed during command")
                result = operation_task.result()
            except (ValueError, TypeError):
                raise
            except BridgeHealthError:
                raise
            except Exception:
                self._fail("controller_command_failed")
                raise BridgeHealthError("controller command failed") from None
            finally:
                tasks = [task for task in (operation_task, fault_task) if task is not None]
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            self.check_ready()
            return result

    def status(self) -> dict[str, Any]:
        now = self.clock()

        def age(value):
            return None if value is None else round(max(0.0, now - value) * 1000)

        heartbeat = getattr(self.session, "_hb_last", None)
        heartbeat_age = None
        if isinstance(heartbeat, (int, float)) and math.isfinite(heartbeat) and heartbeat > 0:
            heartbeat_age = round(max(0.0, self.wall_clock() - heartbeat) * 1000)
        info = getattr(self.stream, "_stream_info", None) or {}
        afk = {}
        for key in ("afk_timeout", "afk_timeout_disconnect"):
            value = info.get(key)
            afk[key] = value if type(value) is int and 0 <= value <= 0xFFFFFFFF else None
        return {
            "refresh_running": self._task is not None and not self._task.done() and not self._closing,
            "state_refresh_interval_ms": 200,
            "transport_ready": not self._closed and self._error is None and self._transport_ready(),
            "feedback_send_count": self._feedback_count,
            "state_send_count": self._state_count,
            "last_feedback_send_age_ms": age(self._last_send),
            "last_state_send_age_ms": age(self._last_state_send),
            "last_receive_age_ms": age(self._last_receive),
            "last_heartbeat_age_ms": heartbeat_age,
            "last_input_age_ms": age(self._last_input),
            "last_input_action": self._last_input_action,
            "error_present": self._error is not None,
            "error_code": self._error,
            "afk_raw": afk,
            "afk_units": "unknown",
            "input_delivery_verified": False,
        }

    async def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass  # Also covers cancellation before the task first ran.
        try:
            # Best-effort safety releases are permitted after a latched fault;
            # no gameplay command, event replay, or reconnect is permitted.
            self._cleanup = True
            if self._started:
                await self.service.release_all()
        except Exception:
            pass
        finally:
            self._cleanup = False
            try:
                await self.service.close()
            finally:
                self._closed = True
                for obj, name, had_override, old in reversed(self._restores):
                    if had_override:
                        setattr(obj, name, old)
                    else:
                        delattr(obj, name)
                self._restores.clear()
