import unittest

from veda.controller_state_machine import ControllerStateMachine


class ControllerStateMachineTests(unittest.TestCase):
    def setUp(self):
        self.combat = {"screen_type": "combat", "selected_item": None}

    def test_card_requires_two_observed_cross_steps(self):
        machine = ControllerStateMachine()
        first = machine.plan_card_step({**self.combat, "selected_item": "Anger"}, card_name="Anger")
        second = machine.plan_card_step({**self.combat, "selected_item": "Anger"}, card_name="Anger")
        self.assertEqual(first["buttons"], ["cross"])
        self.assertEqual(second["buttons"], ["cross"])
        self.assertIn("re-observe", first["reason"])

    def test_focus_requires_explicit_navigation(self):
        machine = ControllerStateMachine()
        with self.assertRaises(ValueError):
            machine.plan_card_step(self.combat, card_name="Defend")
        step = machine.plan_card_step(self.combat, card_name="Defend", focus_button="right")
        self.assertEqual(step["buttons"], ["right"])

    def test_end_turn_does_not_move_card_focus_up_to_statuses(self):
        machine = ControllerStateMachine()
        clear = machine.plan_end_turn({**self.combat, "selected_item": "Defend"})
        commit = machine.plan_end_turn(self.combat)
        self.assertEqual(clear["buttons"], ["triangle"])
        self.assertEqual(commit["buttons"], ["triangle"])

    def test_noncombat_is_fail_closed(self):
        with self.assertRaises(ValueError):
            ControllerStateMachine().plan_end_turn({"screen_type": "reward"})


if __name__ == "__main__":
    unittest.main()
