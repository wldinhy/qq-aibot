from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot.image_generation import ComfyImageGenerator
from qq_aibot import installer


def touch(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class PlatformInstallTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows PowerShell launcher")
    def test_windows_comfy_home_environment_finds_managed_virtual_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "ComfyUI with spaces"
            touch(home / "main.py")
            touch(home / ".venv-windows/Scripts/python.exe")
            script = Path(__file__).resolve().parents[1] / "start-comfy.ps1"
            result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-CheckOnly"],
                                    env={**os.environ, "COMFYUI_HOME": str(home)}, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def test_dry_run_never_installs_or_initializes_files_on_either_platform(self):
        for system in ("windows", "linux"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                with patch.object(installer, "system_name", return_value=system), \
                        patch.object(installer, "download") as download, patch.object(installer, "run") as run:
                    self.assertEqual(installer.main(["--with-comfyui", "--dry-run"], root=root), 0)
                self.assertEqual(list(root.iterdir()), [])
                download.assert_not_called()
                run.assert_not_called()

    def test_reinstall_preserves_persona_secrets_and_chat_data(self):
        for system in ("windows", "linux"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config = deepcopy(DEFAULT_CONFIG)
                config["llm"]["system_prompt"] = "管理员自己的性格"
                config["bot"]["allowed_users"] = [123]
                config_file = touch(root / "config.json", json.dumps(config))
                provider_file = touch(root / "providers.json", '{"keep":"my-key"}')
                env_file = touch(root / ".env", "ONEBOT_ACCESS_TOKEN=existing-owner-token\nOTHER_VALUE=keep\n")
                chat_file = touch(root / "data/chat.db", "existing database")
                originals = {path: path.read_bytes() for path in (config_file, provider_file, env_file, chat_file)}
                python = installer.bot_python(root, system)
                touch(python)
                with patch.object(installer, "system_name", return_value=system), patch.object(installer, "run"):
                    self.assertEqual(installer.main(["--bot-only"], root=root), 0)
                    self.assertEqual(installer.main(["--bot-only"], root=root), 0)
                for path, content in originals.items():
                    self.assertEqual(path.read_bytes(), content, str(path))
                self.assertEqual(installer.read_profile(root, system)["bot_python"], str(python))

    def test_initial_connection_token_is_generated_once_and_not_printed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            touch(root / ".env", "# keep comment\nONEBOT_ACCESS_TOKEN=change-this-to-a-long-random-string\n")
            installer.initialize_local(root)
            first = (root / ".env").read_text()
            installer.initialize_local(root)
            self.assertEqual((root / ".env").read_text(), first)
            self.assertIn("# keep comment", first)
            self.assertNotIn("change-this", first)
            self.assertGreater(len(first.split("ONEBOT_ACCESS_TOKEN=")[1].strip()), 30)

    def test_linux_reuses_existing_ubuntu_virtual_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = touch(root / ".venv-ubuntu/bin/python")
            self.assertEqual(installer.bot_python(root, "linux"), python)
            self.assertEqual(installer.bot_python(root, "windows"), root / ".venv/Scripts/python.exe")

    def test_reusing_napcat_does_not_download_or_touch_login_state(self):
        for system in ("windows", "linux"):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                home = root / "NapCat with spaces"
                if system == "windows":
                    shell = home
                else:
                    touch(home / "opt/QQ/qq")
                    shell = home / "opt/QQ/resources/app/app_launcher/napcat"
                touch(shell / "napcat.mjs")
                login = touch(shell / "config/login.json", "keep login")
                profile = {}
                with patch.object(installer, "download") as download, patch.object(installer, "run") as run:
                    installer.install_napcat(root, system, str(home), profile)
                download.assert_not_called()
                run.assert_not_called()
                self.assertEqual(login.read_text(), "keep login")
                self.assertEqual(profile["napcat_shell"], str(shell.resolve()))

    def test_archive_traversal_and_symlinks_are_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("good.txt", "safe")
                bundle.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                installer.extract_zip(archive, root / "output")
            self.assertFalse((root / "escape.txt").exists())
            self.assertFalse((root / "output/good.txt").exists())
            with zipfile.ZipFile(archive, "w") as bundle:
                link = zipfile.ZipInfo("link")
                link.external_attr = 0o120777 << 16
                bundle.writestr(link, "../outside")
            with self.assertRaises(ValueError):
                installer.extract_zip(archive, root / "output")

    def test_managed_comfy_dependency_failure_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy = root / ".local/ComfyUI"
            touch(comfy / "main.py")
            touch(comfy / "requirements.txt")
            touch(comfy / ".venv/bin/python")
            args = installer.build_parser().parse_args(["--with-comfyui", "--torch-backend", "cpu"])
            with patch.object(installer, "run", side_effect=subprocess.CalledProcessError(1, "pip")):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install_comfy(root, "linux", args, {})
            self.assertFalse((comfy / ".qq-aibot-ready-linux").exists())
            with patch.object(installer, "run") as run:
                installer.install_comfy(root, "linux", args, {})
            self.assertEqual(run.call_count, 2)
            self.assertTrue((comfy / ".qq-aibot-ready-linux").exists())

    def test_comfy_never_mistakes_the_bots_environment_for_its_own(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            touch(root / ".venv/bin/python")
            comfy = root / "ComfyUI"
            touch(comfy / "main.py")
            self.assertIsNone(installer.comfy_python(comfy, "linux"))

    def test_checkpoint_outside_comfy_is_reused_without_copying_weights(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            comfy = root / "Existing ComfyUI"
            touch(comfy / "main.py")
            touch(comfy / ".venv/bin/python")
            checkpoint = touch(root / "models/我的模型.safetensors", "weights")
            installer.initialize_local(root)
            args = installer.build_parser().parse_args(["--comfyui-home", str(comfy), "--checkpoint", str(checkpoint)])
            profile = {}
            with patch.object(installer, "run") as run:
                installer.install_comfy(root, "linux", args, profile)
            run.assert_not_called()
            config = json.loads((root / "config.json").read_text(encoding="utf-8"))
            self.assertTrue(config["image_generation"]["enabled"])
            self.assertEqual(config["image_generation"]["checkpoint"], checkpoint.name)
            self.assertEqual(profile["checkpoint_directory"], str(checkpoint.parent))
            graph = ComfyImageGenerator(config["image_generation"]).workflow("text_to_image", "cat")
            self.assertEqual(graph["1"]["inputs"]["ckpt_name"], checkpoint.name)
            self.assertFalse((comfy / "models").exists())

    def test_platform_profiles_do_not_replace_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            installer.write_profile(root, "windows", {"bot_python": "windows-path"})
            installer.write_profile(root, "linux", {"bot_python": "linux-path"})
            self.assertEqual(installer.read_profile(root, "windows")["bot_python"], "windows-path")
            self.assertEqual(installer.read_profile(root, "linux")["bot_python"], "linux-path")
