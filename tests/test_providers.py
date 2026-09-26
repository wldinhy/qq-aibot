import json
import os
import tempfile
import unittest
from pathlib import Path

from qq_aibot.providers import (
    get_profile,
    load_provider_store,
    masked_key,
    normalize_api_base,
    save_provider_store,
)


class ProviderTests(unittest.TestCase):
    def test_default_profile_and_multiple_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = load_provider_store(root)
            store["profiles"]["second"] = {
                "api_base": "http://127.0.0.1:11434/v1",
                "api_key": "second-secret",
                "model": "local-model",
                "api_key_required": False,
            }
            store["active"] = "second"
            save_provider_store(store, root)
            loaded = load_provider_store(root)
            name, profile = get_profile(loaded)
            self.assertEqual(name, "second")
            self.assertEqual(profile["api_key"], "second-secret")

    def test_legacy_key_is_migrated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = os.environ.get("DEEPSEEK_API_KEY")
            os.environ["DEEPSEEK_API_KEY"] = "legacy-secret"
            try:
                store = load_provider_store(root)
                self.assertEqual(store["profiles"]["deepseek"]["api_key"], "legacy-secret")
            finally:
                if old is None:
                    os.environ.pop("DEEPSEEK_API_KEY", None)
                else:
                    os.environ["DEEPSEEK_API_KEY"] = old

    def test_url_normalization_and_masking(self):
        self.assertEqual(normalize_api_base("https://example.com/v1/"), "https://example.com/v1")
        self.assertEqual(masked_key("abcdefghijkl"), "abc…ijkl")
        with self.assertRaises(ValueError):
            normalize_api_base("example.com/v1")


if __name__ == "__main__":
    unittest.main()
