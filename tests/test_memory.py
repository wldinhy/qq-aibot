import tempfile
import unittest
from pathlib import Path

from qq_aibot.memory import ConversationStore


class MemoryTests(unittest.TestCase):
    def test_sessions_are_isolated_and_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "chat.db")
            try:
                store.add("private:1", "user", "one", 2)
                store.add("private:1", "assistant", "two", 2)
                store.add("private:1", "user", "three", 2)
                store.add("private:2", "user", "other", 2)
                self.assertEqual(
                    [item["content"] for item in store.history("private:1", 10)],
                    ["two", "three"],
                )
                self.assertEqual(len(store.history("private:2", 10)), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
