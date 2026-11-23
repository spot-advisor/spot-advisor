import unittest

from settings import setting


class SettingTests(unittest.TestCase):
    def test_false_is_a_valid_override(self):
        self.assertIs(setting({"enabled": False}, {"enabled": True}, "enabled"), False)

    def test_zero_is_a_valid_override(self):
        self.assertEqual(setting({"retries": 0}, {"retries": 3}, "retries"), 0)

    def test_empty_string_is_a_valid_override(self):
        self.assertEqual(setting({"label": ""}, {"label": "default"}, "label"), "")

    def test_missing_key_uses_default(self):
        self.assertEqual(setting({}, {"mode": "safe"}, "mode"), "safe")


if __name__ == "__main__":
    unittest.main()
