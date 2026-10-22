from copy import deepcopy
import io
import os
from unittest.mock import patch
import unittest

from qq_aibot.cli import _interactive_extra_settings, _menu, _redraw_screen
from qq_aibot.config import DEFAULT_CONFIG


class SettingsScreenTests(unittest.TestCase):
    def test_interactive_menu_redraws_in_place(self):
        output = io.StringIO()
        with patch("qq_aibot.cli.sys.stdout", output), patch("qq_aibot.cli.sys.stdin.isatty", return_value=True), \
                patch.object(output, "isatty", return_value=True), patch("qq_aibot.cli.sys.platform", "linux"), \
                patch.dict(os.environ, {"TERM": "xterm"}), patch("builtins.input", side_effect=["1", "0"]):
            _menu("配置", ["开关"], notice="已保存")
            _menu("配置", ["开关"])
        self.assertEqual(output.getvalue().count("\033[H\033[2J"), 2)
        self.assertIn("已保存", output.getvalue())
        self.assertNotIn("\033[3J", output.getvalue())

    def test_redirected_output_has_no_screen_controls(self):
        output = io.StringIO()
        with patch("qq_aibot.cli.sys.stdout", output):
            _redraw_screen()
        self.assertEqual(output.getvalue(), "")

    def test_reply_settings_validate_ranges_and_save_new_values(self):
        config = deepcopy(DEFAULT_CONFIG)
        with patch("qq_aibot.cli.load_config", return_value=config), \
                patch("qq_aibot.cli.save_config") as save, \
                patch("qq_aibot.cli._menu", side_effect=[2, 0, 8, -1]), \
                patch("builtins.input", side_effect=["99999", "512"]):
            _interactive_extra_settings("reply")
        self.assertEqual(config["llm"]["max_tokens"], 512)
        self.assertFalse(config["llm"]["natural_style"])
        self.assertFalse(config["bot"]["group_context"])
        self.assertEqual(save.call_count, 3)
