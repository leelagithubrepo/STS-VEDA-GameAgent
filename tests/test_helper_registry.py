import unittest
from veda.helper_registry import helper_path


class HelperRegistryTests(unittest.TestCase):
    def test_known_helper_resolves(self):
        self.assertTrue(helper_path("scripts/veda_loot.py").is_file())

    def test_missing_inventory_helper_is_rejected_before_execution(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            helper_path("scripts/veda_inventory.py")


if __name__ == "__main__":
    unittest.main()
