"""Independent lifecycle timing checks: temporary SQLite/images, fake input only."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import timedelta
import importlib.util
from importlib.machinery import SourceFileLoader
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from tests import test_reviewed_play as combat
from tests import test_map_result_flow as maps
from tests.test_execution import context
from veda.play_timing import PlayTiming


def load_cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "veda_reviewed_play.py"
    spec = importlib.util.spec_from_file_location("veda_reviewed_play_timing_integration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlayTimingIntegrationTests(unittest.TestCase):
    def fixture(self):
        fixture = combat.ReviewedPlayTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def map_fixture(self):
        fixture = maps.MapResultFlowTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def launcher_clock(self, fixture, seconds_before=80):
        tracker = PlayTiming(fixture.root / "session" / "timing.json", run_id=fixture.context_ids["run_id"],
                             clock=lambda: fixture.base_time - timedelta(seconds=seconds_before),
                             monotonic_clock=lambda: 0, clock_id="fixture-launcher")
        tracker.event("phase", name="preflight")
        return tracker

    def verify(self, fixture, prepared, after, telemetry=None):
        return fixture.session.handle({"operation": "verify", "action_id": prepared["action_id"],
            "operation_id": str(uuid4()), "after": after, "telemetry": telemetry or {"zone_coverage": "complete"}})

    def test_adapter_resumes_prelaunch_clock_and_first_verified_input_includes_setup(self):
        fixture = self.fixture()
        tracker = self.launcher_clock(fixture)
        fixture.create()
        fixture.arm()
        prepared = fixture.prepare()
        fixture.send(prepared)
        result = fixture.verify_navigation(prepared)
        self.assertEqual(result["timing"]["session_id"], tracker.session_id)
        self.assertTrue(result["timing"]["startup"]["observed_from_launch"])
        self.assertEqual(result["timing"]["startup"]["active_seconds"], 81)
        self.assertFalse(result["timing"]["floor"]["full_floor_target_verifiable"])
        self.assertEqual(result["timing"]["completed_move_count"], 0)
        self.assertEqual(len(fixture.controller.inputs), 1)

    def test_whole_logical_card_spans_draft_focus_targeting_and_verified_effect(self):
        fixture = self.fixture()
        tracker = self.launcher_clock(fixture, 20)
        tracker.event("begin_move", move_id="planned-strike", kind="combat_card")
        fixture.db.start_combat_zones(combat_id=fixture.context_ids["combat_id"], deck=["Strike", "Defend"],
                                     hand=["Strike", "Defend"], source="Synthetic timing integration")
        fixture.create()
        fixture.arm()
        first = fixture.prepare()
        fixture.send(first)
        focus = fixture.verify_navigation(first, 1)
        self.assertFalse(focus["logical_action_complete"])
        self.assertEqual(focus["timing"]["move"]["id"], "planned-strike")
        fixture.before = fixture.combat_request(1, focus="s")
        second = fixture.prepare()
        fixture.send(second)
        fixture.now = fixture.base_time + timedelta(seconds=2)
        targeting = fixture.combat_request(2, focus="s", ui_override={"phase": "targeting", "selected_card_id": "s",
                            "focused_target_id": "enemy", "target_order": ["enemy"]})
        selected = self.verify(fixture, second, targeting)
        self.assertFalse(selected["logical_action_complete"])
        fixture.before = targeting
        third = fixture.prepare()
        fixture.send(third)
        fixture.now = fixture.base_time + timedelta(seconds=3)
        game = context()
        game["state"]["hand"] = game["state"]["hand"][1:]
        game["state"]["energy"] = 0
        game["state"]["enemies"][0]["hp"] = 6
        after = fixture.combat_request(3, focus="d", game=game)
        changes = {"zone_coverage": "complete", "zone_events": [{"kind": "play", "card_name": "Strike",
            "from_zone": "hand", "to_zone": "discard", "evidence_note": "Synthetic selected card effect and discard inspected"}]}
        after["mutation_review"] = {**after["review"], "changes": changes}
        result = self.verify(fixture, third, after, changes)
        timing = fixture.session.timing.snapshot()
        self.assertTrue(result["logical_action_complete"])
        self.assertEqual(timing["completed_move_count"], 1)
        self.assertEqual(timing["completed_moves"][0]["id"], "planned-strike")
        self.assertEqual(timing["completed_moves"][0]["active_seconds"], 23)
        self.assertEqual(timing["verified_input_count"], 3)
        self.assertEqual(len(fixture.controller.inputs), 3)

    def test_measurement_write_failure_does_not_replay_or_block_attempted_verification(self):
        fixture = self.fixture()
        fixture.create()
        fixture.arm()
        prepared = fixture.prepare()
        fixture.send(prepared)
        with patch.object(fixture.session.timing, "event", side_effect=OSError("synthetic timing storage unavailable")):
            result = fixture.verify_navigation(prepared)
        self.assertEqual(result["status"], "verified")
        self.assertIn("timing storage unavailable", result["timing"]["measurement_error"])
        self.assertIsNone(fixture.session.state["pending"])
        self.assertEqual(len(fixture.controller.inputs), 1)
        self.assertEqual(fixture.decisions()[0]["status"], "resolved")

    def test_malformed_timing_data_does_not_block_a_dispatched_outcome(self):
        fixture = self.fixture()
        fixture.create()
        fixture.arm()
        prepared = fixture.prepare()
        fixture.send(prepared)
        path = fixture.session.directory / "timing.json"
        corrupted = fixture.session.timing.snapshot()
        del corrupted["updated_at"]
        path.write_text(json.dumps(corrupted))
        result = fixture.verify_navigation(prepared)
        self.assertEqual(result["status"], "verified")
        self.assertIn("measurement_error", result["timing"])
        self.assertIsNone(fixture.session.state["pending"])
        self.assertEqual(len(fixture.controller.inputs), 1)

    def test_finalization_retry_does_not_inflate_verified_inputs_or_restart_move(self):
        fixture = self.fixture()
        fixture.create()
        fixture.arm()
        prepared = fixture.prepare()
        fixture.send(prepared)
        move_id = fixture.session.timing.snapshot()["move"]["id"]
        original_save = fixture.session._save
        def fail_final_save():
            if fixture.session.state["pending"] is None:
                raise OSError("synthetic final session write failure")
            original_save()
        with patch.object(fixture.session, "_save", side_effect=fail_final_save), self.assertRaises(OSError):
            fixture.verify_navigation(prepared)
        fixture.session.close()
        fixture.create()
        result = fixture.session.handle({"operation": "finalize"})
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["timing"]["verified_input_count"], 1)
        self.assertEqual(result["timing"]["move"]["id"], move_id)
        self.assertEqual(result["timing"]["completed_move_count"], 0)
        self.assertEqual(len(fixture.controller.inputs), 1)

    def test_codex_adapter_reopen_counts_preflight_after_explicit_stopped_gap(self):
        fixture = self.fixture()
        self.launcher_clock(fixture, 0)
        fixture.create()
        fixture.session.close()
        fixture.now = fixture.base_time + timedelta(seconds=2)
        fixture.create()
        self.assertIsNone(fixture.session.timing.snapshot()["pause"])
        fixture.now = fixture.base_time + timedelta(seconds=5)
        result = fixture.session.handle({"operation": "bridge_preflight"})
        self.assertEqual(result["timing"]["active_seconds"], 3)
        self.assertEqual(result["timing"]["excluded_seconds"], 2)
        self.assertFalse(result["armed"])
        self.assertEqual(fixture.controller.inputs, [])

    def test_shadow_recovery_open_keeps_declared_pause_and_does_not_resume_play_clock(self):
        fixture = self.fixture()
        self.launcher_clock(fixture, 0)
        fixture.create()
        fixture.session.close()
        fixture.now = fixture.base_time + timedelta(seconds=5)
        fixture.create("shadow")
        result = fixture.session.summary()["timing"]
        self.assertIsNotNone(result["pause"])
        self.assertEqual(result["active_seconds"], 0)
        self.assertEqual(result["excluded_seconds"], 5)
        self.assertEqual(fixture.controller.inputs, [])

    def test_canonical_room_arrival_closes_old_floor_clock_and_starts_combat_clock(self):
        fixture = self.map_fixture()
        old_floor = fixture.floor
        prepared = fixture.prepare(fixture.draft())
        answer, _ = fixture.verify(prepared, fixture.arrival(),
            facts=dict(fixture.facts, floor=1, current_node_id="center", node_type="enemy"))
        timing = fixture.session.timing.snapshot()
        self.assertEqual(timing["completed_floor_count"], 1)
        self.assertEqual(timing["completed_floors"][0]["id"], old_floor)
        self.assertEqual(timing["floor"]["id"], answer["next_context"]["floor_id"])
        self.assertEqual(timing["floor"]["kind"], "combat")
        self.assertEqual(timing["floor"]["target_seconds"], 240)
        self.assertTrue(answer["timing"]["floor"]["full_floor_target_verifiable"])
        self.assertEqual(len(fixture.controller.inputs), 1)

    def test_room_arrival_save_retry_does_not_report_spurious_floor_measurement_error(self):
        fixture = self.map_fixture()
        prepared = fixture.prepare(fixture.draft())
        original_save = fixture.session._save
        def fail_final_save():
            if fixture.session.state["pending"] is None:
                raise OSError("synthetic arrival session write failure")
            original_save()
        with patch.object(fixture.session, "_save", side_effect=fail_final_save), self.assertRaises(OSError):
            fixture.verify(prepared, fixture.arrival(),
                facts=dict(fixture.facts, floor=1, current_node_id="center", node_type="enemy"))
        fixture.session.close()
        fixture.session = fixture.new_session()
        result = fixture.session.handle({"operation": "finalize"})
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["timing"]["completed_floor_count"], 1)
        self.assertEqual(result["timing"]["verified_input_count"], 1)
        self.assertNotIn("measurement_error", result["timing"])
        self.assertEqual(len(fixture.controller.inputs), 1)


class TimingJsonlIntegrationTests(unittest.TestCase):
    def test_idle_poll_alert_and_already_buffered_second_line_need_no_more_pipe_read(self):
        cli = load_cli()
        read_fd, write_fd = os.pipe()
        self.addCleanup(os.close, read_fd)
        self.addCleanup(os.close, write_fd)
        stream = type("Stream", (), {"fileno": lambda _: read_fd})()
        class Session:
            closed = False
            def timing_summary(self, *, poll=False):
                self.poll = poll
                return {"new_alerts": [{"code": "target_exceeded", "scope": "move"}], "phase": "draft"}
        session = Session()
        output = io.StringIO()
        # If the second buffered JSONL line incorrectly re-enters select/read,
        # the finite mocks raise immediately rather than hiding the regression.
        with (patch.object(cli.select, "select", side_effect=[([], [], []), ([read_fd], [], [])]) as select_mock,
             patch.object(cli.os, "read", return_value=b'{"operation":"summary"}\n{"operation":"summary"}\n') as read_mock,
             redirect_stdout(output)):
            iterator = cli.timed_requests(stream, session)
            self.assertEqual(json.loads(next(iterator))["operation"], "summary")
            self.assertEqual(json.loads(next(iterator))["operation"], "summary")
        self.assertTrue(session.poll)
        self.assertEqual(select_mock.call_count, 2)
        self.assertEqual(read_mock.call_count, 1)
        alert = json.loads(output.getvalue())
        self.assertEqual(alert["status"], "timing_overrun")
        self.assertFalse(alert["controller_input_sent"])

    def test_partial_json_at_eof_is_never_a_request(self):
        cli = load_cli()
        stream = type("Stream", (), {"fileno": lambda _: 123})()
        session = type("Session", (), {"closed": False})()
        with (patch.object(cli.select, "select", return_value=([123], [], [])),
              patch.object(cli.os, "read", side_effect=[b'{"operation":"summary"}', b''])):
            with self.assertRaisesRegex(ValueError, "newline required"):
                next(cli.timed_requests(stream, session))


class LauncherTimingIntegrationTests(unittest.TestCase):
    def load_launcher(self, root):
        source = Path(__file__).resolve().parents[1] / "scripts" / "orchestrator"
        loader = SourceFileLoader("veda_orchestrator_timing_integration", str(source))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        module.__file__ = str(root / "scripts" / "orchestrator")
        return module

    def test_launcher_creates_clock_before_codex_exec_without_game_or_real_run_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.load_launcher(root)
            run_id = str(uuid4())
            observed = []
            def fake_exec(executable, command):
                timing = json.loads((root / "artifacts" / "reviewed-play" / run_id / "timing.json").read_text())
                observed.append((executable, command, timing))
            with (patch.object(module.sys, "argv", ["orchestrator", "--run-id", run_id]),
                  patch.object(module.shutil, "which", return_value="/synthetic/codex"),
                  patch.object(module.os, "execv", side_effect=fake_exec), redirect_stdout(io.StringIO())):
                module.main()
            self.assertEqual(len(observed), 1)
            timing = observed[0][2]
            self.assertEqual(timing["run_id"], run_id)
            self.assertTrue(timing["startup"]["observed_from_launch"])
            self.assertEqual(timing["phase"], "preflight")
            self.assertEqual(timing["verified_input_count"], 0)
            self.assertIn("gpt-5.6-luna", observed[0][1])

    def test_print_command_does_not_start_or_write_a_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            module = self.load_launcher(root)
            with (patch.object(module.sys, "argv", ["orchestrator", "--run-id", str(uuid4()), "--print-command"]),
                  patch.object(module.os, "execv") as execute, redirect_stdout(io.StringIO())):
                self.assertEqual(module.main(), 0)
            execute.assert_not_called()
            self.assertFalse((root / "artifacts").exists())


if __name__ == "__main__":
    unittest.main()
