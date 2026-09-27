"""Offline bridge readiness contract through the actual local JSONL path.

Only a temporary Unix socket and temporary synthetic SQLite/PNG files are used.
No SDK import, discovery, capture, hardware connection or gameplay input occurs.
"""
from copy import deepcopy
import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from scripts.bridge_health import BridgeHealth
from scripts.warm_bridge import _CommandDispatcher, _serve_client
from tests.test_bridge_health import Service
import tests.test_reviewed_play as reviewed_fixtures
from veda.bridge_client import BridgeClient
from veda.reviewed_play import RuntimeStop


class DiscoveryTrapService(Service):
    """A ready fake session with unavailable, stale or slow discovery."""

    def __init__(self):
        super().__init__()
        self.discovery_mode = "unreachable"
        self.discovery_calls = 0
        self.tap_calls = 0
        self.stick_calls = 0
        self.host = "PRIVATE_HOST_MUST_NOT_LEAK"
        self.user = "PRIVATE_USER_MUST_NOT_LEAK"
        self.device.is_on = False  # Cached discovery is not transport health.
        self.device.app_name = "PRIVATE_APP_MUST_NOT_LEAK"

    async def status(self):
        self.discovery_calls += 1
        if self.discovery_mode == "slow":
            await asyncio.sleep(1.0)  # Longer than this fixture's command deadline.
        elif self.discovery_mode == "unreachable":
            raise OSError("PRIVATE_DISCOVERY_FAILURE_MUST_NOT_LEAK")
        return {"on": False, "session_ready": True, "host": self.host,
                "user": self.user, "app": self.device.app_name}

    async def tap(self, *args, **kwargs):
        self.tap_calls += 1
        raise AssertionError("Readiness must never submit a gameplay tap")

    async def stick(self, *args, **kwargs):
        self.stick_calls += 1
        raise AssertionError("Readiness must never move a stick")


class InspectingDispatcher(_CommandDispatcher):
    """Retains the production dispatcher and adds an optional wire fault."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.commands = []
        self.status_mutation = None

    async def dispatch(self, command):
        self.commands.append(deepcopy(command))
        response, closing = await super().dispatch(command)
        if command.get("action") == "status" and self.status_mutation is not None:
            response = deepcopy(response)
            self.status_mutation(response)
        return response, closing


class RecordingClient(BridgeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.responses = []

    def call(self, command):
        response = super().call(command)
        self.responses.append(deepcopy(response))
        return response


class BridgeReadinessIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.socket_temp = TemporaryDirectory(prefix="veda-ready-test-", dir="/tmp")
        self.socket_path = str(Path(self.socket_temp.name) / "bridge.sock")
        # Reuse data-only helpers rather than importing or creating real-run state.
        self.fixture = reviewed_fixtures.ReviewedPlayTests(methodName="runTest")
        self.fixture.setUp()
        self.service = DiscoveryTrapService()
        self.health = BridgeHealth(self.service, "STATE")
        self.clients = []
        self.tasks = set()
        self.server = None
        self.dispatcher = InspectingDispatcher(self.service, self.health, command_timeout=.1)
        self.fixture.factory = self.client_factory
        self.fixture.create()
        await self.health.start()

        async def serve(reader, writer):
            task = asyncio.current_task()
            self.tasks.add(task)
            try:
                # Capture the admitted session dispatcher for this connection.
                await _serve_client(reader, writer, self.dispatcher, self.health)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass  # Rejection deliberately closes the client promptly.
                self.tasks.discard(task)

        self.server = await asyncio.start_unix_server(serve, path=self.socket_path, limit=16384)

    def client_factory(self):
        client = RecordingClient(self.socket_path, connect_timeout=.3,
                                 write_timeout=.3, read_timeout=.5)
        self.clients.append(client)
        return client

    async def asyncTearDown(self):
        # Let server handlers run while the synchronous reviewed adapter closes.
        if self.fixture.session is not None:
            await asyncio.to_thread(self.fixture.close_session)
            self.fixture.session = None
        for client in self.clients:
            client.close()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.health.close()
        self.fixture.doCleanups()
        self.socket_temp.cleanup()

    async def arm(self):
        return await asyncio.to_thread(self.fixture.arm)

    def assert_no_gameplay_or_private_data(self):
        self.assertEqual(0, self.service.tap_calls)
        self.assertEqual(0, self.service.stick_calls)
        self.assertEqual(0, self.service.discovery_calls)
        self.assertEqual([], self.fixture.decisions())
        self.assertTrue(all(command["action"] in {"status", "close"}
                            for command in self.dispatcher.commands))
        encoded = json.dumps([response for client in self.clients for response in client.responses])
        for marker in ("PRIVATE_", "DO_NOT_PRINT"):
            self.assertNotIn(marker, encoded)

    async def test_unreachable_discovery_is_never_called_and_owned_transport_arms(self):
        result = await self.arm()
        self.assertEqual("armed_codex_reviewed", result["status"])
        self.assertTrue(self.fixture.session.armed)
        self.assertFalse(result["runtime_authorized"])
        self.assertEqual(["status"], [c["action"] for c in self.dispatcher.commands])
        response = self.clients[0].responses[0]
        self.assertEqual("status", response["action"])
        self.assertEqual("ok", response["status"])
        self.assertEqual(self.dispatcher.commands[0]["request_id"], response["request_id"])
        self.assertEqual("owned_live_transport", response["readiness_source"])
        self.assertIsNone(response["on"])
        self.assertIs(response["power_state_probed"], False)
        self.assertIs(response["session_ready"], True)
        self.assertIs(response["health"]["transport_ready"], True)
        self.assertIs(response["health"]["refresh_running"], True)
        self.assertIs(response["health"]["error_present"], False)
        self.assertIsNone(response["health"]["error_code"])
        self.assertIs(response["health"]["input_delivery_verified"], False)
        self.assertFalse(self.dispatcher.stopped.is_set())
        self.assert_no_gameplay_or_private_data()

    async def test_discovery_longer_than_command_deadline_is_never_started(self):
        self.service.discovery_mode = "slow"
        result = await self.arm()
        self.assertEqual("armed_codex_reviewed", result["status"])
        self.assertFalse(self.dispatcher.stopped.is_set())
        self.assert_no_gameplay_or_private_data()

    async def test_live_transport_loss_rejects_without_discovery_or_input(self):
        # Exercise actual BridgeHealth readiness checks, not a forged Boolean.
        self.service.session._protocol.closed = True
        with self.assertRaises(RuntimeStop):
            await self.arm()
        self.assertFalse(self.fixture.session.armed)
        self.assertTrue(self.health.failed.is_set())
        self.assertFalse(self.health.status()["transport_ready"])
        self.assert_no_gameplay_or_private_data()

    async def test_malformed_or_uncorrelated_readiness_never_arms(self):
        def missing(key):
            return lambda response: response.pop(key, None)

        def set_value(key, value):
            return lambda response: response.__setitem__(key, value)

        def health_value(key, value):
            return lambda response: response["health"].__setitem__(key, value)

        def missing_health_value(key):
            return lambda response: response["health"].pop(key, None)

        cases = {
            "missing_action": missing("action"),
            "wrong_action": set_value("action", "tap"),
            "missing_source": missing("readiness_source"),
            "discovery_source": set_value("readiness_source", "udp_discovery"),
            "uncorrelated_id": set_value("request_id", str(uuid4())),
            "missing_outcome": missing("ok"),
            "contradictory_status": set_value("status", "error"),
            "missing_session": missing("session_ready"),
            "session_integer": set_value("session_ready", 1),
            "missing_health": missing("health"),
            "health_not_mapping": set_value("health", []),
            "missing_transport": missing_health_value("transport_ready"),
            "transport_false": health_value("transport_ready", False),
            "transport_integer": health_value("transport_ready", 1),
            "missing_refresh": missing_health_value("refresh_running"),
            "refresh_false": health_value("refresh_running", False),
            "refresh_integer": health_value("refresh_running", 1),
            "missing_error_present": missing_health_value("error_present"),
            "error_present": health_value("error_present", True),
            "error_numeric_false": health_value("error_present", 0),
            "missing_error_code": missing_health_value("error_code"),
            "error_code": health_value("error_code", "transport_not_ready"),
        }
        for name, mutation in cases.items():
            with self.subTest(name=name):
                # Each rejected arm closes its owned bridge process. A new fake
                # dispatcher models a separately started session, never a retry
                # of a submitted gameplay action.
                self.dispatcher = InspectingDispatcher(self.service, self.health, command_timeout=.1)
                self.dispatcher.status_mutation = mutation
                with self.assertRaises(RuntimeStop):
                    await self.arm()
                self.assertFalse(self.fixture.session.armed)
                self.assert_no_gameplay_or_private_data()


if __name__ == "__main__":
    unittest.main()
