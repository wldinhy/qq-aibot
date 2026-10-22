from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from qq_aibot.config import DEFAULT_CONFIG
from qq_aibot import launcher
from test_platform_install import touch


class PlatformLauncherTests(unittest.TestCase):
    def test_linux_plan_reuses_running_services_and_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            touch(root / ".venv-linux/bin/python")
            config = deepcopy(DEFAULT_CONFIG)
            with patch.object(launcher, "listening", return_value=True):
                plan = launcher.linux_plan(root, config, {})
            self.assertEqual([item[0] for item in plan], ["Bot"])
            self.assertFalse((root / "data").exists())

    def test_linux_comfy_cpu_and_external_model_path_are_in_launch_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            touch(root / ".venv-linux/bin/python")
            comfy = root / ".local/ComfyUI"
            touch(comfy / "main.py")
            python = touch(comfy / ".venv/bin/python")
            profile = {"comfy_root": str(comfy), "comfy_python": str(python), "checkpoint_directory": str(root / "weights")}
            config = deepcopy(DEFAULT_CONFIG)
            config["image_generation"]["enabled"] = True
            with patch.object(launcher, "listening", side_effect=[True, False]), \
                    patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "0\n", "")):
                plan = launcher.linux_plan(root, config, profile)
            self.assertEqual([item[0] for item in plan], ["ComfyUI", "Bot"])
            self.assertIn("--cpu", plan[0][1])
            self.assertIn("--extra-model-paths-config", plan[0][1])
            self.assertFalse((root / "data").exists())

    def test_linux_validates_missing_napcat_before_spawning_any_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            touch(root / ".venv-linux/bin/python")
            with patch.object(launcher, "listening", return_value=False), patch.object(launcher.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "NapCat"):
                    launcher.linux_plan(root, deepcopy(DEFAULT_CONFIG), {"napcat_home": str(root / "missing")})
            popen.assert_not_called()

    def test_webui_connection_preserves_other_clients_and_does_not_rewrite_on_every_poll(self):
        state = {"network": {"websocketClients": [{"name": "other-bot", "enable": True, "url": "ws://example.invalid"}]}}
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((self.path, data))
                if self.path.endswith("/auth/login"):
                    result = {"Credential": "test-credential"}
                elif self.path.endswith("/GetConfig"):
                    result = state
                else:
                    state.clear()
                    state.update(json.loads(data["config"]))
                    result = {}
                payload = json.dumps({"code": 0, "data": result}).encode()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                shell = Path(directory)
                touch(shell / "config/webui.json", json.dumps({"port": server.server_port, "token": "webui-test-token"}))
                for _ in range(2):
                    self.assertTrue(launcher.sync_onebot(shell, deepcopy(DEFAULT_CONFIG), "onebot-test-token"))
            clients = state["network"]["websocketClients"]
            self.assertEqual(clients[0]["name"], "other-bot")
            self.assertEqual(clients[1]["url"], "ws://127.0.0.1:8080/onebot/v11/ws")
            self.assertEqual(clients[1]["token"], "onebot-test-token")
            self.assertEqual(sum(path.endswith("/SetConfig") for path, _ in requests), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    @unittest.skipUnless(os.name == "posix", "POSIX process groups")
    def test_supervisor_stops_own_children_and_leaves_external_service_running(self):
        external = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            with tempfile.TemporaryDirectory() as directory:
                supervisor = launcher.Supervisor(Path(directory))
                try:
                    supervisor.start("Bot", [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"], Path(directory))
                    owned = supervisor.children[0][1]
                    self.assertIsNone(owned.poll())
                finally:
                    supervisor.close()
                self.assertIsNotNone(owned.poll())
                self.assertIsNone(external.poll())
        finally:
            external.terminate()
            external.wait(timeout=5)
