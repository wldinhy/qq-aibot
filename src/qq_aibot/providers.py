from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .config import project_root


DEFAULT_PROVIDER_STORE: dict[str, Any] = {
    "version": 1,
    "active": "deepseek",
    "profiles": {
        "deepseek": {
            "api_base": "https://api.deepseek.com",
            "api_key": "",
            "model": "deepseek-flash",
            "api_key_required": True,
        }
    },
}

PROFILE_NAME = re.compile(r"^[\w.-]{1,64}$", re.UNICODE)


def providers_path(root: Path | None = None) -> Path:
    return (root or project_root()) / "providers.json"


def normalize_api_base(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        raise ValueError("API 地址必须以 http:// 或 https:// 开头")
    return value


def validate_profile_name(name: str) -> str:
    name = name.strip()
    if not PROFILE_NAME.fullmatch(name):
        raise ValueError("配置档名称只能包含字母、数字、中文、下划线、点和连字符，最长 64 字符")
    return name


def ensure_provider_store(root: Path | None = None) -> tuple[Path, bool]:
    root = root or project_root()
    path = providers_path(root)
    if path.exists():
        return path, False
    store = deepcopy(DEFAULT_PROVIDER_STORE)
    old_config = root / "config.json"
    if old_config.exists():
        try:
            legacy = json.loads(old_config.read_text(encoding="utf-8")).get("deepseek", {})
            if isinstance(legacy, dict):
                if legacy.get("api_base"):
                    store["profiles"]["deepseek"]["api_base"] = legacy["api_base"]
                if legacy.get("model"):
                    store["profiles"]["deepseek"]["model"] = legacy["model"]
        except (json.JSONDecodeError, OSError, AttributeError):
            pass
    legacy_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if legacy_key and legacy_key != "sk-your-key-here":
        store["profiles"]["deepseek"]["api_key"] = legacy_key
    save_provider_store(store, root)
    return path, True


def load_provider_store(root: Path | None = None) -> dict[str, Any]:
    path, _ = ensure_provider_store(root)
    try:
        store = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"服务商配置无法读取：{path}（{exc}）") from exc
    if not isinstance(store, dict) or not isinstance(store.get("profiles"), dict):
        raise ValueError("providers.json 格式错误")
    active = store.get("active")
    if not isinstance(active, str):
        raise ValueError("providers.json 缺少 active 配置")
    return store


def save_provider_store(store: dict[str, Any], root: Path | None = None) -> Path:
    path = providers_path(root)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def get_profile(store: dict[str, Any], name: str | None = None) -> tuple[str, dict[str, Any]]:
    selected = name or store["active"]
    profile = store["profiles"].get(selected)
    if not isinstance(profile, dict):
        raise KeyError(f"服务商配置档不存在：{selected}")
    return selected, profile


def validate_profile(name: str, profile: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    try:
        normalize_api_base(str(profile.get("api_base", "")))
    except ValueError as exc:
        issues.append(f"{name}: {exc}")
    if not str(profile.get("model", "")).strip():
        issues.append(f"{name}: 尚未选择模型")
    if profile.get("api_key_required", True) and not str(profile.get("api_key", "")).strip():
        issues.append(f"{name}: 尚未设置 API Key")
    return issues


def masked_key(value: str) -> str:
    if not value:
        return "未设置"
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}…{value[-4:]}"
