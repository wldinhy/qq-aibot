"""Standard-library-only setup shared by both operating systems."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import subprocess
import sys
from urllib.request import Request, urlopen
import venv
import zipfile

from .config import ensure_initialized, load_config, save_config


NAPCAT_INSTALLER = "https://raw.githubusercontent.com/NapNeko/NapCat-Installer/main/script/install.sh"
COMFY_REPOSITORY = "https://github.com/Comfy-Org/ComfyUI.git"


def system_name() -> str:
    name = platform.system().lower()
    if name not in {"windows", "linux"}:
        raise ValueError("统一安装目前支持 Windows 和 Linux。")
    return name


def profile_path(root: Path, system: str) -> Path:
    return root / ".local" / f"services-{system}.json"


def read_profile(root: Path, system: str) -> dict:
    path = profile_path(root, system)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_profile(root: Path, system: str, profile: dict) -> None:
    path = profile_path(root, system)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def env_python(directory: Path, system: str) -> Path:
    return directory / ("Scripts/python.exe" if system == "windows" else "bin/python")


def bot_python(root: Path, system: str) -> Path:
    profile = read_profile(root, system)
    candidates = [Path(profile["bot_python"])] if profile.get("bot_python") else []
    if system == "windows":
        candidates += [env_python(root / ".venv", system)]
    else:
        candidates += [env_python(root / ".venv-linux", system), env_python(root / ".venv-ubuntu", system)]
    return next((path for path in candidates if path.is_file()), candidates[0])


def run(command: list[str | Path], root: Path, *, quiet: bool = False) -> None:
    subprocess.run([str(item) for item in command], cwd=root, check=True,
                   stdout=subprocess.DEVNULL if quiet else None)


def download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        request = Request(url, headers={"User-Agent": "qq-aibot-installer"})
        with urlopen(request, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def extract_zip(archive: Path, destination: Path) -> None:
    base = destination.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename.replace("\\", "/")
            target = (base / name).resolve()
            if target != base and base not in target.parents:
                raise ValueError("下载的压缩包含目录之外的路径。")
            if ":" in name or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("下载的压缩包含不支持的路径或链接。")
        bundle.extractall(destination)


def initialize_local(root: Path) -> None:
    ensure_initialized(root)
    path = root / ".env"
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    lines = text.splitlines()
    token_present = False
    for index, line in enumerate(lines):
        if line.strip().startswith("ONEBOT_ACCESS_TOKEN="):
            value = line.split("=", 1)[1].strip().strip("\"'")
            if value and value != "change-this-to-a-long-random-string":
                token_present = True
            else:
                lines[index] = "ONEBOT_ACCESS_TOKEN=" + secrets.token_urlsafe(32)
                token_present = True
    if not token_present:
        lines.append("ONEBOT_ACCESS_TOKEN=" + secrets.token_urlsafe(32))
    updated = "\n".join(lines) + "\n"
    if updated != text:
        path.write_text(updated, encoding="utf-8")
        if os.name != "nt":
            path.chmod(0o600)


def linux_napcat(home: Path) -> tuple[Path, Path]:
    qq = home / "opt/QQ/qq"
    directories = [home / "opt/QQ/resources/app/app_launcher/napcat", home / "opt/QQ/resources/app/napcat"]
    shell = next((path for path in directories if (path / "napcat.mjs").is_file()), directories[0])
    return qq, shell


def install_napcat(root: Path, system: str, home: str | None, profile: dict) -> None:
    if system == "windows":
        shell = Path(home or profile.get("napcat_shell", str(root / ".local/napcat/shell"))).expanduser().resolve()
        if not (shell / "napcat.mjs").is_file():
            request = Request("https://api.github.com/repos/NapNeko/NapCatQQ/releases/latest",
                              headers={"User-Agent": "qq-aibot-installer"})
            with urlopen(request, timeout=60) as response:
                release = json.load(response)
            asset = next((item for item in release["assets"] if item["name"] == "NapCat.Shell.zip"), None)
            if not asset:
                raise ValueError("NapCat 官方 Release 中没有 NapCat.Shell.zip。")
            archive = root / ".local/downloads/NapCat.Shell.zip"
            download(asset["browser_download_url"], archive)
            extract_zip(archive, shell)
        if not (shell / "napcat.mjs").is_file():
            raise ValueError("NapCat 下载后缺少 napcat.mjs。")
        profile["napcat_shell"] = str(shell)
    else:
        directory = Path(home or profile.get("napcat_home", str(Path.home() / "Napcat"))).expanduser().resolve()
        qq, shell = linux_napcat(directory)
        if not qq.is_file() or not (shell / "napcat.mjs").is_file():
            if directory != Path.home() / "Napcat":
                raise ValueError("--napcat-home 需指向已安装的 Linux NapCat 目录；自动安装使用 ~/Napcat。")
            if platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}:
                raise ValueError("NapCat 官方 Linux 安装器支持 amd64 / arm64。")
            script = root / ".local/downloads/napcat-installer.sh"
            download(NAPCAT_INSTALLER, script)
            print("调用 NapCat 官方 Linux 安装器；它可能通过 sudo 安装系统依赖。", flush=True)
            run(["bash", script, "--docker", "n", "--cli", "n", "--github-proxy", "0"], script.parent)
            qq, shell = linux_napcat(directory)
        if not qq.is_file() or not (shell / "napcat.mjs").is_file():
            raise ValueError("NapCat 安装后没有找到 Linux QQ 或 napcat.mjs。")
        profile.update(napcat_home=str(directory), napcat_qq=str(qq), napcat_shell=str(shell))
    print("NapCat 就绪，已有登录和配置保留。")


def find_comfy(root: Path, system: str, supplied: str | None = None) -> Path | None:
    profile = read_profile(root, system)
    candidates = [supplied, os.environ.get("COMFYUI_HOME"), profile.get("comfy_root"), str(root / ".local/ComfyUI")]
    if system == "windows":
        candidates.append(r"D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI")
    for value in filter(None, candidates):
        path = Path(value).expanduser().resolve()
        for candidate in (path, path / "ComfyUI"):
            if (candidate / "main.py").is_file():
                return candidate
    return None


def comfy_python(directory: Path, system: str) -> Path | None:
    paths = [env_python(directory / f".venv-{system}", system), env_python(directory / ".venv", system)]
    if system == "windows":
        paths.append(directory.parent / "standalone-env/python.exe")
    return next((path for path in paths if path.is_file()), None)


def install_comfy(root: Path, system: str, args: argparse.Namespace, profile: dict) -> None:
    root = root.resolve()
    directory = find_comfy(root, system, args.comfyui_home)
    existing = directory is not None
    if args.comfyui_home and directory is None:
        raise ValueError("--comfyui-home 中找不到 main.py 或 ComfyUI/main.py。")
    if directory is None:
        if not shutil.which("git"):
            raise ValueError("安装 ComfyUI 需要先安装 Git。")
        directory = root / ".local/ComfyUI"
        run(["git", "clone", "--depth", "1", COMFY_REPOSITORY, directory], root)
    python = comfy_python(directory, system)
    managed = directory == root / ".local/ComfyUI"
    ready = directory / f".qq-aibot-ready-{system}"
    backend = args.torch_backend
    if backend == "auto" and (python is None or (managed and not ready.is_file())):
        backend = "cuda" if shutil.which("nvidia-smi") else "cpu"
        print(f"自动选择 PyTorch 后端：{backend}；AMD / Intel 可用 --torch-backend 指定。")
    if python is None or (managed and not ready.is_file()):
        if python is None:
            environment = directory / f".venv-{system}"
            venv.EnvBuilder(with_pip=True).create(environment)
            python = env_python(environment, system)
        # Use official stable torch indexes; drivers are installed by the OS owner.
        command = [python, "-m", "pip", "install", "torch", "torchvision", "torchaudio"]
        if backend != "auto":
            indexes = {"cpu": "https://download.pytorch.org/whl/cpu",
                       "cuda": "https://download.pytorch.org/whl/cu130",
                       "rocm": "https://download.pytorch.org/whl/rocm7.2",
                       "xpu": "https://download.pytorch.org/whl/xpu"}
            command += ["--index-url", indexes[backend]]
        run(command, root)
        run([python, "-m", "pip", "install", "-r", directory / "requirements.txt"], root)
        if managed:
            ready.touch()
    profile.update(comfy_root=str(directory), comfy_python=str(python), torch_backend=backend)
    if args.checkpoint:
        checkpoint = Path(args.checkpoint).expanduser().resolve()
        if not checkpoint.is_file() or checkpoint.suffix.lower() not in {".safetensors", ".ckpt"}:
            raise ValueError("--checkpoint 需指向已有的 .safetensors 或 .ckpt 模型文件。")
        config = load_config(root)
        config["image_generation"].update(enabled=True, checkpoint=checkpoint.name)
        save_config(config, root)
        profile["checkpoint_directory"] = str(checkpoint.parent)
    print(f"ComfyUI {'复用现有安装' if existing else '已安装'}：{directory}")
    if not args.checkpoint:
        print("模型权重不会自动下载；请放入 ComfyUI/models/checkpoints，或使用 --checkpoint 指定现有模型。")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QQ AI Bot · Windows / Linux 统一安装")
    parser.add_argument("--bot-only", action="store_true", help="仅安装机器人，保留已有外部服务")
    parser.add_argument("--with-comfyui", action="store_true", help="安装或复用 ComfyUI")
    parser.add_argument("--napcat-home", help="复用 NapCat 安装目录")
    parser.add_argument("--comfyui-home", help="复用已有 ComfyUI 目录")
    parser.add_argument("--checkpoint", help="使用已有模型文件并开启生图")
    parser.add_argument("--torch-backend", choices=["auto", "cpu", "cuda", "rocm", "xpu"], default="auto")
    parser.add_argument("--dry-run", action="store_true", help="显示安装计划，不下载或修改文件")
    return parser


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = (root or Path.cwd()).resolve()
    try:
        system = system_name()
        if sys.version_info < (3, 10):
            raise ValueError("需要 Python 3.10 或更新版本。")
        if args.bot_only and (args.with_comfyui or args.comfyui_home or args.checkpoint):
            raise ValueError("--bot-only 不能同时选择 ComfyUI。")
        if system == "windows" and args.torch_backend == "rocm":
            raise ValueError("ROCm 自动安装用于 Linux；Windows AMD 请复用已配置好的 ComfyUI。")
        python = bot_python(root, system)
        print(f"系统：{system} | 项目：{root}\n机器人环境：{python.parent.parent}", flush=True)
        want_comfy = bool(args.with_comfyui or args.comfyui_home or args.checkpoint)
        if args.dry_run:
            print("计划：创建/复用虚拟环境 → 安装机器人 → 保留已有配置并初始化连接密钥")
            if not args.bot_only:
                print("NapCat：复用已有安装，否则从官方来源安装")
            if want_comfy:
                print(f"ComfyUI：复用或安装；PyTorch 后端 {args.torch_backend}；不自动下载模型权重")
            return 0
        if not python.is_file():
            try:
                venv.EnvBuilder(with_pip=True).create(python.parent.parent)
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError("创建虚拟环境失败；Debian/Ubuntu 请先安装 python3-venv。") from exc
        run([python, "-m", "pip", "install", "-e", root], root)
        initialize_local(root)
        run([python, "-m", "qq_aibot", "init"], root, quiet=True)
        profile = read_profile(root, system)
        profile["bot_python"] = str(python)
        write_profile(root, system, profile)
        if not args.bot_only:
            install_napcat(root, system, args.napcat_home, profile)
            write_profile(root, system, profile)
        if want_comfy:
            install_comfy(root, system, args, profile)
            write_profile(root, system, profile)
        print("\n安装完成。配置模型：python start.py --console\n统一启动：python start.py")
        if system == "windows":
            print("Windows 也可双击 start.cmd；请确保已安装 QQ。")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        print(f"安装未完成：{exc}", file=sys.stderr)
        return 1
