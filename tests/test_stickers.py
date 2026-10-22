from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from qq_aibot.stickers import StickerLibrary, StickerError, sticker_segment


class TestStickerLibrary(unittest.TestCase):
    def test_load_manifest(self) -> None:
        with TemporaryDirectory() as tmpdir:
            sticker_dir = Path(tmpdir)
            manifest = sticker_dir / "manifest.json"
            manifest.write_text(json.dumps({
                "stickers": [
                    {"id": "test1", "file": "test1.png", "tags": ["tag1"], "caption": "Test 1"},
                    {"id": "test2", "file": "test2.png", "tags": ["tag2", "tag3"], "caption": "Test 2"},
                ]
            }), encoding="utf-8")
            (sticker_dir / "test1.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
            (sticker_dir / "test2.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

            library = StickerLibrary(manifest)
            self.assertEqual(len(library.stickers), 2)
            self.assertIn("test1", library.stickers)
            self.assertIn("test2", library.stickers)

            sticker1 = library.get("test1")
            self.assertIsNotNone(sticker1)
            self.assertEqual(sticker1.id, "test1")
            self.assertEqual(sticker1.caption, "Test 1")
            self.assertEqual(sticker1.tags, ("tag1",))

    def test_path_containment_validation(self) -> None:
        with TemporaryDirectory() as tmpdir:
            sticker_dir = Path(tmpdir)
            manifest = sticker_dir / "manifest.json"
            manifest.write_text(json.dumps({
                "stickers": [
                    {"id": "evil", "file": "../../../etc/passwd", "tags": [], "caption": ""},
                ]
            }), encoding="utf-8")

            library = StickerLibrary(manifest)
            with self.assertRaises(StickerError):
                library.encode_base64("evil")

    def test_file_size_limit(self) -> None:
        with TemporaryDirectory() as tmpdir:
            sticker_dir = Path(tmpdir)
            manifest = sticker_dir / "manifest.json"
            manifest.write_text(json.dumps({
                "stickers": [{"id": "large", "file": "large.png", "tags": [], "caption": ""}]
            }), encoding="utf-8")
            (sticker_dir / "large.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 3000000)

            library = StickerLibrary(manifest, max_file_bytes=1000000)
            with self.assertRaises(StickerError):
                library.encode_base64("large")

    def test_missing_file(self) -> None:
        with TemporaryDirectory() as tmpdir:
            sticker_dir = Path(tmpdir)
            manifest = sticker_dir / "manifest.json"
            manifest.write_text(json.dumps({
                "stickers": [{"id": "missing", "file": "missing.png", "tags": [], "caption": ""}]
            }), encoding="utf-8")

            library = StickerLibrary(manifest)
            with self.assertRaises(StickerError):
                library.encode_base64("missing")

    def test_invalid_file_type(self) -> None:
        with TemporaryDirectory() as tmpdir:
            sticker_dir = Path(tmpdir)
            manifest = sticker_dir / "manifest.json"
            manifest.write_text(json.dumps({
                "stickers": [{"id": "txt", "file": "file.txt", "tags": [], "caption": ""}]
            }), encoding="utf-8")
            (sticker_dir / "file.txt").write_text("hello", encoding="utf-8")

            library = StickerLibrary(manifest)
            with self.assertRaises(StickerError):
                library.encode_base64("txt")

    def test_encode_base64(self) -> None:
        with TemporaryDirectory() as tmpdir:
            sticker_dir = Path(tmpdir)
            manifest = sticker_dir / "manifest.json"
            manifest.write_text(json.dumps({
                "stickers": [{"id": "valid", "file": "valid.png", "tags": [], "caption": "Valid"}]
            }), encoding="utf-8")
            (sticker_dir / "valid.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"test_data")

            library = StickerLibrary(manifest)
            encoded = library.encode_base64("valid")
            self.assertIsInstance(encoded, str)
            self.assertTrue(len(encoded) > 0)

    def test_sticker_segment(self) -> None:
        segment = sticker_segment("base64data", "test caption")
        self.assertIsInstance(segment, list)
        self.assertEqual(len(segment), 1)
        self.assertEqual(segment[0]["type"], "image")
        self.assertEqual(segment[0]["data"]["file"], "base64://base64data")
        self.assertEqual(segment[0]["data"]["sub_type"], 1)
        self.assertEqual(segment[0]["data"]["summary"], "test caption")


if __name__ == "__main__":
    unittest.main()
