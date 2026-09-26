import unittest
from unittest.mock import patch

from qq_aibot.cli import _menu, _parse_id_list, build_parser


class InteractiveCliTests(unittest.TestCase):
    def test_no_subcommand_opens_interactive_mode(self):
        args = build_parser().parse_args([])
        self.assertIsNone(args.command)

    def test_menu_retries_and_returns_zero_based_choice(self):
        with patch("builtins.input", side_effect=["wrong", "2"]):
            self.assertEqual(_menu("Test", ["A", "B"]), 1)

    def test_menu_back(self):
        with patch("builtins.input", return_value="0"):
            self.assertEqual(_menu("Test", ["A"]), -1)

    def test_id_list(self):
        self.assertEqual(_parse_id_list("123, 456"), [123, 456])
        self.assertEqual(_parse_id_list(""), [])
        with self.assertRaises(ValueError):
            _parse_id_list("123,abc")


if __name__ == "__main__":
    unittest.main()
