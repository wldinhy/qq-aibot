from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import secrets
import shutil
import subprocess
import sys
import math
from pathlib import Path
from typing import Any

from . import __version__
from .bot import run_bot
from .config import config_path, data_dir, ensure_initialized, get_value, load_config, load_dotenv, parse_cli_value, project_root, save_config, set_value
from .llm import LLMClient, LLMError
from .memory import ConversationStore, persona_key
from .providers import ensure_provider_store, get_profile, load_provider_store, masked_key, normalize_api_base, providers_path, save_provider_store, validate_profile, validate_profile_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qq-aibot", description="终端版多模型 QQ AI 机器人")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("setup", help="打开交互式配置向导")
    sub.add_parser("init", help="初始化配置和数据目录")
    sub.add_parser("start", help="在 Windows / Linux 上启动机器人和配套服务")
    sub.add_parser("serve", help="仅启动机器人服务（由启动脚本调用）")
    sub.add_parser("doctor", help="检查运行环境和配置")
    sub.add_parser("chat", help="在终端中测试当前模型")

    provider = sub.add_parser("provider", help="管理模型服务商、Key 和模型")
    provider_sub = provider.add_subparsers(dest="provider_command", required=True)
    provider_sub.add_parser("list", help="列出所有配置档")
    add = provider_sub.add_parser("add", help="新增服务商配置档并选择模型")
    add.add_argument("name", help="例如 deepseek-main 或 local-ollama")
    add.add_argument("--base-url", help="OpenAI 兼容 API 根地址，例如 https://host/v1")
    add.add_argument("--model", help="直接指定模型名；省略则在线检索")
    add.add_argument("--no-key", action="store_true", help="自建服务不需要 API Key")
    use = provider_sub.add_parser("use", help="切换当前配置档")
    use.add_argument("name")
    models = provider_sub.add_parser("models", help="从服务商检索可用模型")
    models.add_argument("name", nargs="?", help="省略时使用当前配置档")
    select = provider_sub.add_parser("select", help="检索并选择模型")
    select.add_argument("name", nargs="?", help="省略时使用当前配置档")
    set_key = provider_sub.add_parser("set-key", help="安全修改 API Key")
    set_key.add_argument("name", nargs="?", help="省略时使用当前配置档")
    set_key.add_argument("--clear", action="store_true", help="清空 Key 并标记为无需鉴权")
    set_url = provider_sub.add_parser("set-url", help="修改 API 根地址")
    set_url.add_argument("name")
    set_url.add_argument("url")
    set_model = provider_sub.add_parser("set-model", help="不检索，直接填写模型名")
    set_model.add_argument("name")
    set_model.add_argument("model")
    remove = provider_sub.add_parser("remove", help="删除配置档")
    remove.add_argument("name")
    remove.add_argument("--yes", action="store_true", help="确认删除")

    secret = sub.add_parser("secret", help="管理 OneBot Token（兼容旧版模型 Key 命令）")
    secret_sub = secret.add_subparsers(dest="secret_command", required=True)
    secret_sub.add_parser("list", help="查看密钥状态")
    secret_set = secret_sub.add_parser("set", help="设置或生成密钥")
    secret_set.add_argument("name", choices=["onebot", "provider", "deepseek"])

    config = sub.add_parser("config", help="查看或修改机器人设置")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("list", help="列出全部设置")
    get_parser = config_sub.add_parser("get", help="读取一项设置")
    get_parser.add_argument("key", help="例如 bot.group_mode")
    set_parser = config_sub.add_parser("set", help="修改一项设置")
    set_parser.add_argument("key", help="例如 bot.group_mode")
    set_parser.add_argument("value", help='JSON 值；字符串可直接输入，例如 mention 或 "[123]"')

    memory = sub.add_parser("memory", help="管理聊天记忆")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)
    clear_parser = memory_sub.add_parser("clear", help="清除聊天记忆")
    clear_parser.add_argument("--session", help="例如 private:123456；不填则清除全部")
    clear_parser.add_argument("--yes", action="store_true", help="确认清除全部记忆")
    return parser


def _redraw_screen() -> None:
    """Redraw interactive pages in place; redirected output remains plain text."""
    if not (sys.stdout.isatty() and sys.stdin.isatty()) or os.environ.get("TERM") == "dumb":
        return
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.GetStdHandle(-11)
        mode = ctypes.c_ulong()
        if not kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            return
        if not kernel.SetConsoleMode(handle, mode.value | 4):
            return
    print("\033[H\033[2J", end="", flush=True)


def _menu(title: str, options: list[str], *, allow_back: bool = True, clear: bool = True, notice: str = "") -> int:
    """Return a zero-based menu choice, or -1 when the user chooses back."""
    if clear:
        _redraw_screen()
    if notice:
        print(notice)
    print(f"\n╭─ {title}")
    for index, option in enumerate(options, start=1):
        print(f"│  {index}. {option}")
    if allow_back:
        print("│  0. 返回")
    print("╰" + "─" * 42)
    while True:
        answer = input("请选择：").strip()
        if allow_back and answer == "0":
            return -1
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("请输入菜单中的数字。")


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    value = input(f"{prompt}{suffix}：").strip()
    return value if value else (default or "")


def _ask_int(prompt: str, current: int, minimum: int = 1) -> int:
    while True:
        raw = _ask(prompt, str(current))
        try:
            value = int(raw)
            if value >= minimum:
                return value
        except ValueError:
            pass
        print(f"请输入不小于 {minimum} 的整数。")


def _ask_number(prompt: str, current: int | float, minimum: float, maximum: float) -> int | float:
    while True:
        raw = _ask(f"{prompt}（{minimum:g}–{maximum:g}）", str(current))
        try:
            value = int(raw) if isinstance(current, int) else float(raw)
            if math.isfinite(value) and minimum <= value <= maximum:
                return value
        except ValueError:
            pass
        print("请输入范围内的数值。")


def _parse_id_list(raw: str) -> list[int]:
    if not raw.strip():
        return []
    try:
        return [int(item.strip()) for item in raw.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("QQ 号或群号必须是数字，多个号码用英文逗号分隔") from exc


def _interactive_ready() -> None:
    load_dotenv()
    ensure_initialized()
    ensure_provider_store()
    _copy_env_example(project_root())


def _status_lines() -> list[str]:
    store = load_provider_store()
    name, profile = get_profile(store)
    config = load_config()
    token = os.environ.get("ONEBOT_ACCESS_TOKEN", "")
    token_ready = bool(token and token != "change-this-to-a-long-random-string")
    group_labels = {"mention": "仅被 @", "always": "所有消息", "off": "关闭"}
    mode_label = {"serial": "串行", "parallel": "并行"}.get(config['bot']['processing_mode'], config['bot']['processing_mode'])

    vision_enabled = config.get('vision', {}).get('enabled', True)
    image_gen_enabled = config.get('image_generation', {}).get('enabled', False)
    stickers_enabled = config.get('stickers', {}).get('enabled', True)

    max_tokens = config['llm'].get('max_tokens', 700)
    max_messages = config['bot'].get('max_reply_messages', 3)
    max_stickers = config['bot'].get('max_reply_stickers', 1)

    return [
        f"模型：{name} / {profile.get('model') or '未选择'}",
        f"QQ：{config['onebot'].get('qq_id') or '未绑定'} | Token：{'已设置' if token_ready else '未设置'} | Key：{masked_key(str(profile.get('api_key', '')))}",
        f"看图：{'开' if vision_enabled else '关'} | 生图：{'开' if image_gen_enabled else '关'} | 表情包：{'开' if stickers_enabled else '关'}",
        f"队列：{mode_label} / {config['bot']['max_concurrency']} 并发 | 群聊：{group_labels.get(config['bot']['group_mode'], config['bot']['group_mode'])}",
        f"回复：≤{max_messages} 条文字 + {max_stickers} 个表情 / 每条≤{config['bot'].get('max_reply_chars_per_message', 280)} 字符",
        f"Token：{max_tokens} | 自然聊天：{'开' if config['llm'].get('natural_style', True) else '关'} | 风格：{'简短' if config['llm'].get('reply_style', 'brief') == 'brief' else '按需展开'}",
        f"上下文：{'同群实际对话' if config['bot'].get('group_context', True) else '仅与机器人的对话'} / {config['bot']['history_messages']} 条 | 性格：仅本地修改",
        f"QQ空间：{'开' if config['qzone']['enabled'] else '关'} | 好友评论：{'开' if config['qzone']['auto_comment'] else '关'} | 评论回复：{'开' if config['qzone']['auto_reply'] else '关'} | 自动发说说：{'开' if config['qzone']['auto_post'] else '关'}",
    ]


async def _interactive_add_provider() -> None:
    preset = _menu(
        "选择服务商类型",
        ["DeepSeek", "OpenAI", "自定义云服务商", "本机或自建服务"],
    )
    if preset < 0:
        return
    presets = [
        ("deepseek", "https://api.deepseek.com", False),
        ("openai", "https://api.openai.com/v1", False),
        ("custom", "", False),
        ("local", "http://127.0.0.1:11434/v1", True),
    ]
    default_name, default_url, default_no_key = presets[preset]
    name = _ask("给这个配置档起名", default_name)
    url = _ask("API 根地址", default_url)
    no_key = default_no_key
    if preset in {2, 3}:
        auth_choice = _menu("鉴权方式", ["使用 API Key", "不需要 Key"], allow_back=False)
        no_key = auth_choice == 1
    args = argparse.Namespace(
        provider_command="add",
        name=name,
        base_url=url,
        model=None,
        no_key=no_key,
    )
    await provider_command(args)
    store = load_provider_store()
    store["active"] = name
    save_provider_store(store)
    print(f"已将 {name} 设为当前配置档。")


async def _interactive_provider_manager() -> None:
    notice = ""
    while True:
        store = load_provider_store()
        names = list(store["profiles"])
        labels = [
            f"{name}{'（当前）' if name == store['active'] else ''} — "
            f"{store['profiles'][name].get('model') or '未选模型'}"
            for name in names
        ]
        choice = _menu("服务商和模型", ["＋ 添加服务商", *labels], notice=notice)
        if choice < 0:
            return
        if choice == 0:
            try:
                await _interactive_add_provider()
            except (KeyError, TypeError, ValueError, LLMError) as exc:
                notice = f"操作失败：{exc}"
            continue
        name = names[choice - 1]
        action = _menu(
            f"配置档：{name}",
            ["设为当前配置", "检索并选择模型", "查看可用模型", "更换 API Key", "修改 API 地址", "手工填写模型名", "删除配置档"],
        )
        if action < 0:
            continue
        try:
            if action == 0:
                await provider_command(argparse.Namespace(provider_command="use", name=name))
            elif action == 1:
                await provider_command(argparse.Namespace(provider_command="select", name=name))
            elif action == 2:
                await provider_command(argparse.Namespace(provider_command="models", name=name))
                if sys.stdout.isatty() and sys.stdin.isatty():
                    input("按回车返回服务商设置…")
            elif action == 3:
                await provider_command(argparse.Namespace(provider_command="set-key", name=name, clear=False))
            elif action == 4:
                url = _ask("新的 API 根地址", store["profiles"][name]["api_base"])
                await provider_command(argparse.Namespace(provider_command="set-url", name=name, url=url))
            elif action == 5:
                model = _ask("模型名称", store["profiles"][name].get("model", ""))
                await provider_command(argparse.Namespace(provider_command="set-model", name=name, model=model))
            elif action == 6:
                confirm = _menu("确认删除？", ["取消", "确认删除"], allow_back=False)
                if confirm == 1:
                    await provider_command(argparse.Namespace(provider_command="remove", name=name, yes=True))
            notice = "操作已完成。"
        except (KeyError, TypeError, ValueError, LLMError) as exc:
            notice = f"操作失败：{exc}"


def _interactive_extra_settings(category: str) -> None:
    # key, label, either inclusive numeric bounds or enum values/labels.
    specs = {
        "reply": [
            ("llm.natural_style", "自然聊天约束", None),
            ("llm.reply_style", "回复风格", [("brief", "简短随口聊"), ("adaptive", "按问题展开")]),
            ("llm.max_tokens", "回复 Token 上限", (64, 4096)),
            ("llm.temperature", "表达随机性", (0.0, 2.0)),
            ("bot.max_reply_messages", "最多文字条数", (1, 3)),
            ("bot.max_reply_chars_per_message", "单条字符上限", (20, 280)),
            ("bot.max_reply_chars", "每次总字符上限", (20, 840)),
            ("bot.max_reply_stickers", "每次最多表情包", (0, 1)),
            ("bot.group_context", "结合同群实际对话（未 @ 的消息也作为背景）", None),
            ("bot.history_messages", "上下文消息数", (2, 100)),
        ],
        "media": [
            ("vision.enabled", "识别 QQ 图片和表情包", None),
            ("vision.max_images", "每次最多识图张数", (1, 3)),
            ("stickers.enabled", "发送表情包", None),
            ("image_generation.enabled", "本地生图服务", None),
            ("image_generation.optimize_prompt", "生图提示词优化", None),
            ("image_generation.optimizer_max_tokens", "提示词优化 Token 上限", (256, 4096)),
            ("image_generation.img2img_denoise", "图生图修改强度", (0.2, 0.7)),
            ("image_generation.img2img_megapixels", "图生图分辨率（百万像素）", (0.1, 2.0)),
            ("image_generation.timeout_seconds", "生图最长等待（秒）", (30, 1800)),
        ],
        "queue": [
            ("bot.processing_mode", "请求处理模式", [("parallel", "不同会话并行，同一会话按顺序"), ("serial", "所有会话按顺序")]),
            ("bot.max_concurrency", "最大并发（下次启动生效）", (1, 16)),
            ("bot.queue_notice", "发送排队提示", None),
            ("bot.ignore_before_start", "忽略启动前收到的旧消息", None),
            ("bot.max_input_chars", "每条输入字符上限", (100, 12000)),
        ],
    }[category]
    titles = {"reply": "聊天风格、长度与上下文", "media": "识图、生图与表情包", "queue": "排队与消息处理"}
    notice = "开关直接切换，数值可按回车保留。"
    while True:
        config = load_config()
        labels = []
        for key, label, rule in specs:
            value = get_value(config, key)
            if isinstance(value, bool):
                display = "开启" if value else "关闭"
            elif isinstance(rule, list):
                display = dict(rule).get(value, value)
            else:
                display = str(value)
            labels.append(f"{label}：{display}")
        choice = _menu(titles[category], labels, notice=notice)
        if choice < 0:
            return
        key, label, rule = specs[choice]
        current = get_value(config, key)
        if isinstance(current, bool):
            value = not current
        elif isinstance(rule, list):
            selected = _menu(label, [item[1] for item in rule])
            if selected < 0:
                continue
            value = rule[selected][0]
        else:
            value = _ask_number(label, current, *rule)
        set_value(config, key, value)
        save_config(config)
        restart = key in {"bot.max_concurrency", "bot.processing_mode"} or key.startswith("image_generation.") or key == "stickers.enabled"
        notice = f"已保存：{label}。" + ("下次启动生效。" if restart else "从下一条消息生效。")


def _interactive_qzone_settings() -> None:
    from .qzone import validate_settings
    notice = "评论需要先运行 python install.py --with-qzone；仅原生发说说无需该组件。"
    fields = [
        ("enabled", "空间功能总开关", None),
        ("auto_comment", "评论指定好友的新动态", None),
        ("auto_reply", "回复自己空间下的新评论", None),
        ("auto_post", "按时间自动发说说", None),
        ("friend_ids", "指定好友 QQ（留空不评论好友）", "ids"),
        ("post_times", "每天发说说时间", "times"),
        ("post_topic", "发说说的主题与要求", "text"),
        ("post_visibility", "说说可见范围", "visibility"),
        ("max_posts_per_day", "每日发说说上限", (0, 10)),
        ("max_comments_per_day", "每日评论和回复合计上限", (0, 100)),
        ("poll_seconds", "检查间隔（秒）", (30, 3600)),
        ("min_action_seconds", "两次空间操作最短间隔（秒）", (30, 86400)),
        ("max_comment_chars", "评论字符上限", (10, 500)),
        ("max_post_chars", "说说字符上限", (20, 2000)),
        ("bridge_url", "空间桥接地址", "text"),
    ]
    while True:
        config = load_config()
        settings = config["qzone"]
        labels = []
        for key, title, _ in fields:
            value = settings[key]
            if isinstance(value, bool):
                value = "开启" if value else "关闭"
            elif isinstance(value, list):
                value = ",".join(map(str, value)) or "空"
            else:
                value = str(value)
                if len(value) > 40:
                    value = value[:40] + "…"
            labels.append(f"{title}：{value}")
        choice = _menu("QQ 空间 · 仅处理开启后新收到的动态/评论", [*labels, "查看最近执行记录"], notice=notice)
        if choice < 0:
            return
        if choice == len(fields):
            import sqlite3
            path = data_dir() / "qzone.db"
            if not path.exists():
                notice = "还没有空间执行记录。"
                continue
            with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
                rows = connection.execute("SELECT account,kind,status,content FROM jobs WHERE status!='baseline' ORDER BY updated DESC LIMIT 15").fetchall()
            for account, kind, status, content in rows:
                print(f"QQ {account} | {kind} | {status} | {content[:90]}")
            print("pending / uncertain 表示结果待核实，程序不会自动重发这些任务。")
            input("按回车返回…")
            continue
        key, title, rule = fields[choice]
        try:
            current = settings[key]
            if isinstance(current, bool):
                value = not current
            elif rule == "ids":
                raw = input(f"{title}（逗号分隔，留空清空）：").strip()
                value = _parse_id_list(raw)
            elif rule == "times":
                value = [part.strip() for part in _ask("HH:MM，多个用逗号分隔", ",".join(current)).split(",") if part.strip()]
            elif rule == "visibility":
                selected = _menu("空间可见范围", ["好友可见", "所有人可见", "仅自己可见"])
                if selected < 0:
                    continue
                value = ["friends", "public", "private"][selected]
            elif rule == "text":
                value = _ask(title, current)
            else:
                value = _ask_number(title, current, *rule)
            settings[key] = value
            validate_settings(settings)
            save_config(config)
            notice = f"已保存：{title}；空间后台下一轮检查时生效。"
        except ValueError as exc:
            notice = f"未保存：{exc}"


def _interactive_bot_settings() -> None:
    notice = "白名单留空表示不限制；黑名单中的号码永不回复。"
    while True:
        config = load_config()
        bot = config["bot"]
        choice = _menu(
            "机器人设置",
            [
                f"群聊回复模式：{bot['group_mode']}",
                f"私聊回复：{'开启' if bot['private_reply'] else '关闭'}",
                "设置机器人性格",
                f"上下文消息数：{bot['history_messages']}",
                f"最大并发：{bot['max_concurrency']}",
                f"用户白名单：{len(bot['allowed_users'])} 个",
                f"用户黑名单：{len(bot['blocked_users'])} 个",
                f"群白名单：{len(bot['allowed_groups'])} 个",
                f"群黑名单：{len(bot['blocked_groups'])} 个",
                "聊天风格 / Token / 回复条数 / 群聊上下文",
                "识图 / 生图 / 提示词优化 / 表情包",
                "排队模式 / 排队提示 / 旧消息过滤",
                "QQ空间 / 好友动态评论 / 评论回复 / 自动发说说",
            ],
            notice=notice,
        )
        if choice < 0:
            return
        if choice == 12:
            _interactive_qzone_settings()
            notice = "已返回机器人设置。"
            continue
        if choice >= 9:
            _interactive_extra_settings(["reply", "media", "queue"][choice - 9])
            notice = "已返回机器人设置，可继续选择。"
            continue
        try:
            if choice == 0:
                modes = ["mention", "always", "off"]
                bot["group_mode"] = modes[_menu("群聊模式", ["仅被 @ 时回复", "回复所有群消息", "关闭群聊回复"], allow_back=False)]
            elif choice == 1:
                bot["private_reply"] = _menu("私聊回复", ["开启", "关闭"], allow_back=False) == 0
            elif choice == 2:
                config["llm"]["system_prompt"] = _ask("机器人性格提示词", config["llm"]["system_prompt"])
            elif choice == 3:
                bot["history_messages"] = _ask_number("保留多少条上下文消息", int(bot["history_messages"]), 2, 100)
            elif choice == 4:
                bot["max_concurrency"] = _ask_number("最大同时请求数", int(bot["max_concurrency"]), 1, 16)
            else:
                keys = ["allowed_users", "blocked_users", "allowed_groups", "blocked_groups"]
                key = keys[choice - 5]
                current = ",".join(str(item) for item in bot[key])
                bot[key] = _parse_id_list(_ask("输入号码，多个用英文逗号分隔，留空表示清空", current))
        except ValueError as exc:
            notice = f"设置失败：{exc}"
            continue
        save_config(config)
        if choice == 2:
            notice = "性格已保存，从下一条消息生效；旧性格记录保留。"
        else:
            notice = "设置已保存。" + ("并发数下次启动生效。" if choice == 4 else "从下一条消息生效。")


def _interactive_onebot_settings() -> None:
    notice = "连接设置下次启动生效。"
    while True:
        config = load_config()
        onebot = config["onebot"]
        choice = _menu(
            "QQ / OneBot 设置",
            [
                f"绑定机器人 QQ 号：{onebot.get('qq_id') or '未设置'}",
                f"监听地址：{onebot['host']}",
                f"监听端口：{onebot['port']}",
                f"WebSocket 路径：{onebot['path']}",
                "生成新的 OneBot Token",
            ],
            notice=notice,
        )
        if choice < 0:
            return
        if choice == 0:
            onebot["qq_id"] = _ask_int("机器人 QQ 号（需先在 OneBot 接入端登录）", int(onebot.get("qq_id", 0)), 1)
        elif choice == 1:
            onebot["host"] = _ask("监听地址", onebot["host"])
        elif choice == 2:
            onebot["port"] = _ask_int("监听端口", int(onebot["port"]), 1)
        elif choice == 3:
            path = _ask("WebSocket 路径", onebot["path"])
            onebot["path"] = path if path.startswith("/") else "/" + path
        else:
            secret_command(argparse.Namespace(secret_command="set", name="onebot"))
            notice = "OneBot Token 已更新，下次启动生效。"
            continue
        save_config(config)
        notice = "OneBot 设置已保存，下次启动生效。"


async def interactive_setup() -> None:
    _interactive_ready()
    print("\nQQ AI Bot 快速配置向导")
    await _interactive_provider_manager()
    print("\nQQ 登录由 OneBot 接入端完成；这里填写 QQ 号用于核对收到的消息。")
    _interactive_onebot_settings()
    token = os.environ.get("ONEBOT_ACCESS_TOKEN", "")
    if not token or token == "change-this-to-a-long-random-string":
        if _menu("还没有 OneBot Token", ["现在生成", "稍后设置"], allow_back=False) == 0:
            secret_command(argparse.Namespace(secret_command="set", name="onebot"))
    print("\n配置向导结束，可以从主菜单测试模型或启动机器人。")


async def interactive_console() -> int:
    _interactive_ready()
    use_color = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    while True:
        _redraw_screen()
        if use_color:
            print(f"\033[1;36mQQ AI Bot {__version__} · 控制中心\033[0m")
        else:
            print(f"QQ AI Bot {__version__} · 控制中心")

        for line in _status_lines():
            if use_color and line.startswith("═══"):
                print(f"\033[1;33m{line}\033[0m")
            elif use_color and ("✓" in line or "已设置" in line or "已启用" in line):
                print(f"\033[32m{line}\033[0m")
            elif use_color and ("✗" in line or "未设置" in line or "未绑定" in line):
                print(f"\033[31m{line}\033[0m")
            else:
                print(line)

        choice = _menu(
            "控制中心",
            ["快速配置向导", "服务商 / API / 模型", "机器人行为与权限", "QQ / OneBot 连接", "终端测试当前模型", "检查配置", "启动机器人和配套服务", "退出"],
            allow_back=False,
            clear=False,
        )
        if choice == 0:
            await interactive_setup()
        elif choice == 1:
            await _interactive_provider_manager()
        elif choice == 2:
            _interactive_bot_settings()
        elif choice == 3:
            _interactive_onebot_settings()
        elif choice == 4:
            await chat_command()
        elif choice == 5:
            doctor_command()
            if sys.stdout.isatty() and sys.stdin.isatty():
                input("按回车返回控制中心…")
        elif choice == 6:
            if (project_root() / "start.py").is_file():
                _start_services_launcher()
            elif doctor_command() == 0:
                provider_store, config = load_provider_store(), load_config()
                _, provider = get_profile(provider_store)
                loader = lambda: get_profile(load_provider_store())[1]
                store = ConversationStore(data_dir() / "chat.db")
                try:
                    await run_bot(config, store, provider, loader)
                finally:
                    store.close()
            else:
                print("配置尚未完成，不能启动。")
        else:
            print("已退出。")
            return 0


def _start_services_launcher() -> int:
    launcher = project_root() / "start.py"
    return subprocess.call(
        [sys.executable, str(launcher)],
        cwd=project_root(),
    )


def _copy_env_example(root: Path) -> bool:
    target = root / ".env"
    if target.exists():
        return False
    candidates = [root / ".env.example", Path(__file__).resolve().parents[2] / ".env.example"]
    source = next((item for item in candidates if item.exists()), None)
    if source:
        shutil.copyfile(source, target)
    else:
        target.write_text("ONEBOT_ACCESS_TOKEN=change-this-to-a-long-random-string\n", encoding="utf-8")
    return True


def _write_env_value(key: str, value: str, root: Path | None = None) -> Path:
    path = (root or project_root()) / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    replacement, found, updated = f"{key}={value}", False, []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            updated.append(replacement)
            found = True
        else:
            updated.append(line)
    if not found:
        updated.append(replacement)
    path.write_text("\n".join(updated).rstrip() + "\n", encoding="utf-8")
    return path


def init_command() -> int:
    root = project_root()
    load_dotenv(root)
    path, config_created = ensure_initialized(root)
    provider_path, providers_created = ensure_provider_store(root)
    env_created = _copy_env_example(root)
    print(f"项目目录：{root}")
    print(f"机器人配置：{path}" + ("（已创建）" if config_created else "（已存在）"))
    print(f"服务商配置：{provider_path}" + ("（已创建）" if providers_created else "（已存在）"))
    print(f"OneBot 密钥：{root / '.env'}" + ("（已创建）" if env_created else "（已存在）"))
    print("下一步：运行 qq-aibot provider set-key deepseek，然后检索并选择模型。")
    return 0


async def _fetch_models(profile: dict[str, Any], config: dict[str, Any]) -> list[str]:
    async with LLMClient(profile, config["llm"]) as client:
        return await client.list_models()


def _print_models(models: list[str]) -> None:
    for index, model in enumerate(models, start=1):
        print(f"{index:>3}. {model}")


def _choose_model(models: list[str]) -> str:
    visible = models
    if len(models) > 30:
        keyword = input(f"检索到 {len(models)} 个模型，输入关键词筛选（回车显示全部）：").strip()
        if keyword:
            visible = [item for item in models if keyword.casefold() in item.casefold()]
            if not visible:
                raise ValueError(f"没有找到包含“{keyword}”的模型")
    _print_models(visible)
    answer = input("输入序号或完整模型名称：").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(visible):
        return visible[int(answer) - 1]
    if answer in models:
        return answer
    raise ValueError("模型选择无效")


async def provider_command(args: argparse.Namespace) -> int:
    store, config = load_provider_store(), load_config()
    profiles, action = store["profiles"], args.provider_command
    if action == "list":
        if not profiles:
            print("还没有服务商配置档。")
            return 0
        for name, profile in profiles.items():
            marker = "*" if name == store["active"] else " "
            print(f"{marker} {name} | {profile.get('model') or '未选模型'} | {profile.get('api_base', '')} | Key: {masked_key(str(profile.get('api_key', '')))}")
        print("* 表示当前使用的配置档")
        return 0
    if action == "add":
        name = validate_profile_name(args.name)
        if name in profiles:
            raise ValueError(f"配置档已存在：{name}")
        base = normalize_api_base(args.base_url or input("API 根地址（例如 https://api.example.com/v1）：").strip())
        key = "" if args.no_key else getpass.getpass("API Key（输入内容不会显示）：").strip()
        if not args.no_key and not key:
            raise ValueError("API Key 不能为空；无鉴权的自建服务请添加 --no-key")
        profile = {"api_base": base, "api_key": key, "model": args.model or "", "api_key_required": not args.no_key}
        if not profile["model"]:
            try:
                profile["model"] = _choose_model(await _fetch_models(profile, config))
            except LLMError as exc:
                print(f"模型自动检索失败：{exc}")
                profile["model"] = input("请输入模型名称：").strip()
                if not profile["model"]:
                    raise ValueError("模型名称不能为空")
        profiles[name] = profile
        if len(profiles) == 1:
            store["active"] = name
        save_provider_store(store)
        print(f"已保存配置档 {name}，模型：{profile['model']}")
        return 0

    name, profile = get_profile(store, getattr(args, "name", None))
    if action == "use":
        issues = validate_profile(name, profile)
        if issues:
            raise ValueError("；".join(issues))
        store["active"] = name
        save_provider_store(store)
        print(f"已切换到 {name} / {profile['model']}，下次对话立即生效。")
    elif action == "models":
        models = await _fetch_models(profile, config)
        print(f"{name} 共检索到 {len(models)} 个模型：")
        _print_models(models)
    elif action == "select":
        profile["model"] = _choose_model(await _fetch_models(profile, config))
        save_provider_store(store)
        print(f"{name} 已选择模型：{profile['model']}")
    elif action == "set-key":
        if args.clear:
            profile["api_key"], profile["api_key_required"] = "", False
            print(f"{name} 已清空 Key，并设置为无需鉴权。")
        else:
            key = getpass.getpass("新 API Key（输入内容不会显示）：").strip()
            if not key:
                raise ValueError("没有输入 Key，未做修改")
            profile["api_key"], profile["api_key_required"] = key, True
            print(f"{name} 的 API Key 已更新。")
        save_provider_store(store)
    elif action == "set-url":
        profile["api_base"] = normalize_api_base(args.url)
        save_provider_store(store)
        print(f"{name} 的 API 地址已更新；建议运行 provider select {name} 重新检索模型。")
    elif action == "set-model":
        if not args.model.strip():
            raise ValueError("模型名称不能为空")
        profile["model"] = args.model.strip()
        save_provider_store(store)
        print(f"{name} 已设置模型：{profile['model']}")
    elif action == "remove":
        if not args.yes:
            raise ValueError("删除配置档需要添加 --yes")
        if name == store["active"]:
            raise ValueError("不能删除当前配置档，请先使用 provider use 切换")
        del profiles[name]
        save_provider_store(store)
        print(f"已删除配置档：{name}")
    return 0


def secret_command(args: argparse.Namespace) -> int:
    ensure_initialized()
    _copy_env_example(project_root())
    load_dotenv()
    store = load_provider_store()
    active_name, active = get_profile(store)
    if args.secret_command == "list":
        token = os.environ.get("ONEBOT_ACCESS_TOKEN", "")
        token_set = bool(token and token != "change-this-to-a-long-random-string")
        print(f"当前模型配置档：{active_name}")
        print(f"当前服务商 API Key：{masked_key(str(active.get('api_key', '')))}")
        print(f"OneBot Access Token：{'已设置' if token_set else '未设置'}")
        return 0
    if args.name in {"provider", "deepseek"}:
        value = getpass.getpass(f"请输入 {active_name} 的 API Key（输入内容不会显示）：").strip()
        if not value:
            print("未输入内容，没有修改。", file=sys.stderr)
            return 1
        active["api_key"], active["api_key_required"] = value, True
        save_provider_store(store)
        print(f"{active_name} 的 API Key 已保存。")
    else:
        value = secrets.token_urlsafe(32)
        _write_env_value("ONEBOT_ACCESS_TOKEN", value)
        os.environ["ONEBOT_ACCESS_TOKEN"] = value
        print("已生成并保存新的 OneBot Access Token：")
        print(value)
        print("请把它复制到 OneBot 接入端；再次执行本命令会使旧 Token 失效。")
    return 0


def doctor_command() -> int:
    load_dotenv()
    issues: list[str] = []
    try:
        store, config = load_provider_store(), load_config()
        active_name, active = get_profile(store)
        print(f"[通过] 机器人配置：{config_path()}")
        print(f"[通过] 服务商配置：{providers_path()}")
        print(f"[当前] {active_name} / {active.get('model') or '未选择模型'}")
        issues.extend(validate_profile(active_name, active))
        if config["bot"]["group_mode"] not in {"off", "mention", "always"}:
            issues.append("bot.group_mode 必须是 off、mention 或 always")
    except (KeyError, ValueError) as exc:
        print(f"[失败] {exc}")
        return 1
    token = os.environ.get("ONEBOT_ACCESS_TOKEN", "")
    if not token or token == "change-this-to-a-long-random-string":
        issues.append("请运行 qq-aibot secret set onebot")
    else:
        print("[通过] ONEBOT_ACCESS_TOKEN 已设置")
    print(f"[通过] 数据目录：{data_dir()}")
    for issue in issues:
        print(f"[待处理] {issue}")
    return 1 if issues else 0


async def chat_command() -> int:
    provider_store, config = load_provider_store(), load_config()
    provider_name, provider = get_profile(provider_store)
    issues = validate_profile(provider_name, provider)
    if issues:
        print("；".join(issues), file=sys.stderr)
        return 1
    store = ConversationStore(data_dir() / "chat.db")
    session_id = "terminal:local"
    print(f"终端测试：{provider_name} / {provider['model']}")
    print("输入 /reset 清空记忆，/exit 退出。")
    try:
        async with LLMClient(provider, config["llm"]) as client:
            while True:
                try:
                    text = await asyncio.to_thread(input, "你> ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                text = text.strip()
                if not text:
                    continue
                if text.lower() in {"/exit", "/quit", "退出"}:
                    break
                if text.lower() in {"/reset", "/clear", "/清空"}:
                    store.clear(session_id)
                    print("记忆已清空。")
                    continue
                current_config = load_config()
                generation = dict(current_config["llm"])
                revision = persona_key(generation["system_prompt"])
                history = store.history(session_id, int(current_config["bot"]["history_messages"]), persona=revision)
                history.append({"role": "user", "content": text})
                try:
                    _, current_provider = get_profile(load_provider_store())
                    client.profile = current_provider
                    with client.using_generation(generation):
                        reply = await client.complete(history)
                except LLMError as exc:
                    print(f"错误：{exc}")
                    continue
                keep = int(config["bot"]["history_messages"])
                store.add(session_id, "user", text, keep, persona=revision)
                store.add(session_id, "assistant", reply, keep, persona=revision)
                print(f"AI> {reply}")
    finally:
        store.close()
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    load_dotenv()
    try:
        if args.command is None:
            code = asyncio.run(interactive_console())
        elif args.command == "setup":
            asyncio.run(interactive_setup())
            code = 0
        elif args.command == "init":
            code = init_command()
        elif args.command == "doctor":
            code = doctor_command()
        elif args.command == "provider":
            code = asyncio.run(provider_command(args))
        elif args.command == "secret":
            code = secret_command(args)
        elif args.command == "config":
            config = load_config()
            if args.config_command == "list":
                print(json.dumps(config, ensure_ascii=False, indent=2))
            elif args.config_command == "get":
                print(json.dumps(get_value(config, args.key), ensure_ascii=False, indent=2))
            else:
                value = parse_cli_value(args.value)
                set_value(config, args.key, value)
                save_config(config)
                print(f"已更新 {args.key} = {json.dumps(value, ensure_ascii=False)}")
            code = 0
        elif args.command == "memory":
            store = ConversationStore(data_dir() / "chat.db")
            try:
                if args.session:
                    count = store.clear(args.session)
                elif args.yes:
                    count = store.clear()
                else:
                    print("清除全部记忆需要添加 --yes", file=sys.stderr)
                    raise SystemExit(2)
                print(f"已清除 {count} 条消息")
            finally:
                store.close()
            code = 0
        elif args.command == "chat":
            code = asyncio.run(chat_command())
        elif args.command == "start" and (project_root() / "start.py").is_file():
            code = _start_services_launcher()
        else:
            if doctor_command() != 0:
                print("配置检查未通过，机器人没有启动。", file=sys.stderr)
                code = 1
            else:
                provider_store, config = load_provider_store(), load_config()
                _, provider = get_profile(provider_store)
                provider_loader = lambda: get_profile(load_provider_store())[1]
                store = ConversationStore(data_dir() / "chat.db")
                try:
                    asyncio.run(run_bot(config, store, provider, provider_loader))
                except KeyboardInterrupt:
                    print("\n机器人已停止。")
                finally:
                    store.close()
                code = 0
    except (KeyError, TypeError, ValueError, LLMError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        code = 2
    except KeyboardInterrupt:
        print("\n已退出。")
        code = 130
    raise SystemExit(code)
