import tempfile
import unittest
import json
from pathlib import Path

from qq_aibot.config import get_value, load_config, parse_cli_value, set_value


class ConfigTests(unittest.TestCase):
    def test_initializes_defaults_and_sets_value(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = load_config(root)
            self.assertEqual(config["bot"]["group_mode"], "mention")
            set_value(config, "bot.group_mode", "always")
            self.assertEqual(get_value(config, "bot.group_mode"), "always")

    def test_cli_json_values(self) -> None:
        self.assertEqual(parse_cli_value("true"), True)
        self.assertEqual(parse_cli_value("[123, 456]"), [123, 456])
        self.assertEqual(parse_cli_value("mention"), "mention")

    def test_rejects_wrong_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(Path(directory))
            with self.assertRaises(TypeError):
                set_value(config, "onebot.port", "8080")

    def test_legacy_deepseek_generation_settings_are_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps({"deepseek": {"temperature": 0.25, "model": "old-model"}}),
                encoding="utf-8",
            )
            config = load_config(root)
            self.assertEqual(config["llm"]["temperature"], 0.25)
            self.assertNotIn("deepseek", config)


if __name__ == "__main__":
    unittest.main()
