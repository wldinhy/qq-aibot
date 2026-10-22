"""Unified entry point with a foreground service supervisor on native Linux."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from .config import load_config, load_dotenv
from .installer import bot_python, comfy_python, find_comfy, linux_napcat, read_profile, system_name


def listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


def napcat_paths(root: Path, profile: dict) -> tuple[Path, Path]:
    directory = Path(profile.get("napcat_home", str(Path.home() / "Napcat")))
    qq, shell = linux_napcat(directory)
    return Path(profile.get("napcat_qq", str(qq))), Path(profile.get("napcat_shell", str(shell)))


def webui_settings(shell: Path) -> dict:
    path = shell / "config/webui.json"
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {"port": 6099}


def sync_onebot(shell: Path, config: dict, token: str) -> bool:
    """Use NapCat's WebUI API; preserve every other OneBot connection."""
    settings = webui_settings(shell)
    if not settings.get("token"):
        return False
    port = int(settings.get("port", 6099))
    opener = build_opener(ProxyHandler({}))  # Loopback credentials never go to a proxy.
    base = f"http://127.0.0.1:{port}/api"

    def post(route: str, data: dict | None = None, credential: str = "") -> dict:
        headers = {"Content-Type": "application/json"}
        if credential:
            headers["Authorization"] = "Bearer " + credential
        request = Request(base + route, data=json.dumps(data or {}).encode(), headers=headers)
        with opener.open(request, timeout=3) as response:
            result = json.load(response)
        if result.get("code") != 0:
            raise ValueError("NapCat 尚未完成登录或不接受连接配置")
        return result

    digest = hashlib.sha256((str(settings["token"]) + ".napcat").encode()).hexdigest()
    credential = post("/auth/login", {"hash": digest})["data"]["Credential"]
    existing = post("/OB11Config/GetConfig", credential=credential)["data"]
    if isinstance(existing, str):
        existing = json.loads(existing)
    network = existing.setdefault("network", {})
    clients = network.get("websocketClients", [])
    client = {"name": "qq-aibot", "enable": True,
              "url": f"ws://127.0.0.1:{int(config['onebot']['port'])}{config['onebot']['path']}",
              "messagePostFormat": "array", "reportSelfMessage": False,
              "reconnectInterval": 5000, "token": token, "debug": False, "heartInterval": 30000}
    current = next((item for item in clients if item.get("name") == "qq-aibot"), None)
    if current and all(current.get(key) == value for key, value in client.items()):
        return True
    network["websocketClients"] = [item for item in clients if item.get("name") != "qq-aibot"] + [client]
    post("/OB11Config/SetConfig", {"config": json.dumps(existing, ensure_ascii=False)}, credential)
    return True


def linux_plan(root: Path, config: dict, profile: dict) -> list[tuple[str, list[str], Path]]:
    """Build and validate all child commands before starting any services."""
    python = bot_python(root, "linux")
    if not python.is_file():
        raise ValueError("请先运行 python3 install.py。")
    plan = []
    qq, shell = napcat_paths(root, profile)
    if not listening(int(webui_settings(shell).get("port", 6099))):
        if not qq.is_file() or not (shell / "napcat.mjs").is_file():
            raise ValueError("缺少 Linux NapCat；请运行 python3 install.py，或用 --napcat-home 复用已有安装。")
        xvfb = shutil.which("xvfb-run")
        if not xvfb:
            raise ValueError("找不到 xvfb-run；请安装 xvfb，或重新运行 NapCat 官方安装器。")
        command = [xvfb, "-a", str(qq), "--no-sandbox"]
        if config["onebot"].get("qq_id"):
            command += ["-q", str(int(config["onebot"]["qq_id"]))]
        plan.append(("NapCat", command, qq.parent))
    image = config.get("image_generation", {})
    if image.get("enabled"):
        uri = urlsplit(image.get("api_base", "http://127.0.0.1:8188"))
        if uri.hostname not in {"127.0.0.1", "localhost"} or uri.scheme != "http":
            print("ComfyUI：使用配置的外部服务，不在本机启动。")
        elif not listening(uri.port or 80):
            directory = find_comfy(root, "linux")
            python_comfy = Path(profile["comfy_python"]) if profile.get("comfy_python") else (comfy_python(directory, "linux") if directory else None)
            if directory is None or python_comfy is None or not python_comfy.is_file():
                raise ValueError("生图已开启但没有找到 ComfyUI；运行 python3 install.py --with-comfyui。")
            probe = subprocess.run([str(python_comfy), "-c",
                                    "import torch; print(int(torch.cuda.is_available() or (hasattr(torch, 'xpu') and torch.xpu.is_available())))"],
                                   capture_output=True, text=True, timeout=60, check=True)
            command = [str(python_comfy), str(directory / "main.py"), "--listen", "127.0.0.1", "--port", str(uri.port or 80)]
            if probe.stdout.strip() == "0" or profile.get("torch_backend") == "cpu":
                command.append("--cpu")
            checkpoint_directory = profile.get("checkpoint_directory")
            if checkpoint_directory:
                extra = root / "data/comfyui/extra-model-paths.yaml"
                command += ["--extra-model-paths-config", str(extra)]
            plan.append(("ComfyUI", command, directory))
    plan.append(("Bot", [str(python), "-X", "utf8", "-m", "qq_aibot", "serve"], root))
    return plan


class Supervisor:
    def __init__(self, root: Path):
        self.root = root
        self.children: list[tuple[str, subprocess.Popen]] = []
        self.threads: list[threading.Thread] = []

    def start(self, name: str, command: list[str], cwd: Path) -> None:
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1,
                                   start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"})
        self.children.append((name, process))

        def pump() -> None:
            directory = self.root / "data/logs"
            directory.mkdir(parents=True, exist_ok=True)
            with (directory / f"{name.lower()}.log").open("a", encoding="utf-8") as log:
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    print(f"[{name}] {line.rstrip()}", flush=True)

        thread = threading.Thread(target=pump, daemon=True)
        self.threads.append(thread)
        thread.start()

    def close(self) -> None:
        # Only child groups created in this invocation are stopped. Reused services
        # are absent from children and must keep running.
        for _, process in reversed(self.children):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + 8
        for _, process in reversed(self.children):
            try:
                process.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        for thread in self.threads:
            thread.join(timeout=1)


def start_linux(root: Path, *, check: bool = False) -> int:
    config = load_config(root)
    profile = read_profile(root, "linux")
    plan = linux_plan(root, config, profile)
    print("启动计划：" + " → ".join(name for name, _, _ in plan))
    if check:
        return 0
    python = bot_python(root, "linux")
    if subprocess.call([str(python), "-m", "qq_aibot", "doctor"], cwd=root):
        raise ValueError("先运行 python3 start.py --console 配置模型。")
    if listening(int(config["onebot"]["port"])):
        raise ValueError("机器人端口已被占用；请检查已有实例，当前不会重复启动。")
    if config.get("image_generation", {}).get("enabled") and profile.get("checkpoint_directory"):
        extra = root / "data/comfyui/extra-model-paths.yaml"
        extra.parent.mkdir(parents=True, exist_ok=True)
        extra.write_text("qq_aibot:\n  checkpoints: " + json.dumps(profile["checkpoint_directory"]) + "\n", encoding="utf-8")
    load_dotenv(root)
    token = os.environ.get("ONEBOT_ACCESS_TOKEN", "")
    _, shell = napcat_paths(root, profile)
    supervisor = Supervisor(root)
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupted(*_: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        for name, command, cwd in plan:
            supervisor.start(name, command, cwd)
        print("服务日志显示在当前终端并保存到 data/logs。请扫描 NapCat 二维码登录；Ctrl+C 停止本次启动的服务。", flush=True)
        connected = False
        next_sync = 0.0
        while True:
            for name, process in supervisor.children:
                result = process.poll()
                if result is not None:
                    raise ValueError(f"{name} 已退出（{result}），请查看 data/logs/{name.lower()}.log。")
            if time.monotonic() >= next_sync:
                next_sync = time.monotonic() + 3
                try:
                    if sync_onebot(shell, config, token) and not connected:
                        print("QQ 登录和 OneBot 连接已配置。", flush=True)
                        connected = True
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # QQ login is interactive; keep streaming its QR/logs.
            time.sleep(0.3)
    except KeyboardInterrupt:
        print("\n正在停止本次启动的服务…", flush=True)
        return 0
    finally:
        supervisor.close()
        signal.signal(signal.SIGTERM, previous_handler)


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="QQ AI Bot · Windows / Linux 统一启动")
    parser.add_argument("--console", action="store_true", help="打开配置界面")
    parser.add_argument("--check", action="store_true", help="只检查启动条件")
    parser.add_argument("--native", action="store_true", help="使用原生环境（兼容 start.sh 参数）")
    args, commands = parser.parse_known_args(argv)
    root = (root or Path.cwd()).resolve()
    try:
        system = system_name()
        python = bot_python(root, system)
        if not python.is_file():
            raise ValueError("请先运行 python install.py（Linux 可用 python3）。")
        if args.console or commands:
            return subprocess.call([str(python), "-X", "utf8", "-m", "qq_aibot", *commands], cwd=root)
        if system == "windows":
            command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "start.ps1")]
            if args.check:
                command.append("-CheckOnly")
            return subprocess.call(command, cwd=root)
        return start_linux(root, check=args.check)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"启动未完成：{exc}", file=sys.stderr)
        return 1
