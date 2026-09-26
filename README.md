# QQ Multi-Model AI Bot

一个在终端运行、支持多个模型服务商和多个 API Key 的 QQ AI 机器人。Windows 版由
[NapCat](https://github.com/NapNeko/NapCatQQ) 登录 QQ，再通过
[OneBot 11](https://github.com/botuniverse/onebot-11) 连接机器人；模型服务使用通用的
OpenAI Chat Completions 兼容接口。

> 登录 QQ 时需要你本人用手机 QQ 扫码确认。本项目的安装脚本从 NapCat 官方 Release
> 下载接入端，不包含 QQ 客户端。请遵守平台规则、接入端许可和云服务商条款。

QQ 号在 OneBot 接入端登录。控制台的“QQ / OneBot 连接”可填写这个机器人 QQ 号，
程序会核对消息中的 `self_id`，只处理该账号的消息。填写 QQ 号本身不会完成登录。

## 功能

- 不带参数运行 `qq-aibot`，进入完整交互式控制台
- 所有常用设置都有编号菜单，不需要记命令
- Windows 上 `start.cmd` 或 `qq-aibot start` 一条命令启动机器人和 NapCat
- DeepSeek、OpenAI 及其他 OpenAI 兼容服务
- 支持自建服务，例如提供 OpenAI 兼容接口的 Ollama、LM Studio 或 vLLM
- 保存多个服务商配置档，每个配置档拥有独立 URL、Key 和模型
- 通过 `GET /models` 检索模型并在终端选择
- 不支持模型检索的服务可以手工填写模型名
- 私聊默认向所有人开放；群聊默认被 `@` 时回复
- 每个私聊独立记忆，每个群共享群聊上下文
- 用户和群白名单、黑名单
- SQLite 本地记忆，无需额外数据库
- OneBot Token 鉴权、并发和长度限制

## Windows 一站式下载与安装

需要 Windows 10/11、Python 3.10 或更新版本，以及电脑上已安装的 QQ。

1. 从 [GitHub Releases](https://github.com/wldinhy/qq-aibot/releases/latest) 下载
   [`qq-aibot-windows.zip`](https://github.com/wldinhy/qq-aibot/releases/latest/download/qq-aibot-windows.zip)，解压到你自己的文件夹。
2. 在解压出的 `qq-aibot` 文件夹打开 PowerShell，运行 `.\install.cmd`。脚本会建立
   `.venv`、安装 Python 依赖、生成本机 OneBot 密钥，并从 NapCat 官方 Release 下载
   `NapCat.Shell.zip`；重复运行会保留现有配置。
3. 运行 `.\.venv\Scripts\qq-aibot.exe`，在菜单 `2. 服务商 / API / 模型` 中填写自己的
   API 地址和 Key，并选模型。
4. 运行 `.\start.cmd`。这**一条命令**会打开 NapCat 和机器人两个可见窗口；首次启动按
   NapCat 窗口的二维码用手机 QQ 扫码确认。启动脚本会自动把 NapCat 的反向 WebSocket
   接到机器人，不需要手工复制 Token。

从已安装的项目目录运行 `.\.venv\Scripts\qq-aibot.exe start` 也会同时启动两者。机器人的
运行日志显示在新打开的 PowerShell 窗口。保持两个窗口打开即可持续回复；关闭机器人窗口
按 `Ctrl+C`，关闭 NapCat 窗口按 `Ctrl+C`，若提示 `Terminate batch job (Y/N)?`，输入 `Y`。
如果同一个 QQ 已在普通电脑版 QQ 登录，先退出普通 QQ 再让 NapCat 登录。

开发者也可以 `git clone https://github.com/wldinhy/qq-aibot.git`，然后运行
`.\install.cmd`。Linux 用户可用 `python -m venv .venv`、`pip install -e .` 安装机器人，
并自行配置 OneBot 11 接入端；Windows 一键脚本不适用于 Linux。

本地会生成三个不会上传 GitHub 的文件或目录：

- `providers.json`：服务商、模型和 API Key
- `.env`：OneBot Token
- `data/`：聊天记忆
- `.local/`：NapCat 程序、QQ 登录态及本机设置

`providers.json` 中的 Key 以本地明文保存并限制文件权限。不要分享此文件，也不要从
`.gitignore` 中移除它。

## 交互式控制台（推荐）

安装后在项目目录直接运行，不加任何参数：

```powershell
.\.venv\Scripts\qq-aibot.exe
```

终端会显示当前服务商、模型、API 地址、Key 状态、群聊模式和 OneBot 状态，并提供：

```text
1. 快速配置向导
2. 服务商 / API / 模型
3. 机器人行为与权限
4. QQ / OneBot 连接
5. 终端测试当前模型
6. 检查配置
7. 启动机器人和 NapCat
8. 退出
```

选择“服务商 / API / 模型”后，可以继续从菜单中添加服务商、填写 API 地址、隐藏输入
Key、在线检索模型、按序号选择模型、切换配置档或删除配置档。选择“机器人行为与权限”
可以设置群聊回复方式、私聊开关、机器人性格、上下文长度、并发数及用户/群黑白名单。
“QQ / OneBot 连接”可以绑定已在接入端登录的机器人 QQ 号。

默认回复规则：私聊开启，任何人发来文字消息都会回复；群聊只有 `@` 机器人时回复。
把群聊模式改成“回复所有群消息”后，它会回复允许群内每位成员的每条文字消息。
用户白名单留空表示所有用户可用，群白名单留空表示所有群可用；黑名单始终排除对应号码。

也可以只打开首次配置向导：

```powershell
qq-aibot setup
```

下面的独立命令适合熟悉后做快速操作或编写自动化脚本。先在 PowerShell 运行
`.\.venv\Scripts\Activate.ps1`，或者把每条命令开头的 `qq-aibot` 替换为
`.\.venv\Scripts\qq-aibot.exe`。

## 服务商和模型

初始化后已有一个不含 Key 的 `deepseek` 配置档：

```powershell
qq-aibot provider set-key deepseek
qq-aibot provider models deepseek
qq-aibot provider select deepseek
```

`select` 会访问服务商的 `/models`，列出模型并让你输入序号。查看当前所有配置档：

```powershell
qq-aibot provider list
```

### 添加另一个云服务商

省略 `--model` 时会自动检索模型：

```powershell
qq-aibot provider add openai-main --base-url https://api.openai.com/v1
```

同一服务商的多个 Key 可以保存为多个配置档：

```powershell
qq-aibot provider add deepseek-backup --base-url https://api.deepseek.com
qq-aibot provider add deepseek-team --base-url https://api.deepseek.com
```

### 添加自建服务

不需要鉴权的本地 OpenAI 兼容接口：

```powershell
qq-aibot provider add local-ollama --base-url http://127.0.0.1:11434/v1 --no-key
```

如果自建服务没有实现 `/models`，直接指定模型：

```powershell
qq-aibot provider add my-server `
  --base-url http://127.0.0.1:8000/v1 `
  --model my-model-name `
  --no-key
```

在 Linux 中把 PowerShell 的续行符替换成 `\`，或者写成一行。

### 切换和修改

```powershell
# 切换配置档，同时切换 URL、Key 和模型
qq-aibot provider use deepseek-backup

# 当前配置档重新检索并选择模型
qq-aibot provider select

# 手工指定模型
qq-aibot provider set-model deepseek-backup deepseek-flash

# 更换 Key，输入内容不会显示在屏幕或命令历史中
qq-aibot provider set-key deepseek-backup

# 修改地址
qq-aibot provider set-url deepseek-backup https://api.deepseek.com

# 删除非当前配置档
qq-aibot provider remove deepseek-team --yes
```

切换服务商、Key 或模型后，即使机器人正在运行，下一条新消息也会自动使用新配置；已有
聊天历史会保留。

## 检查和启动

Windows 推荐直接运行 `.\start.cmd`。它会启动两个可见窗口，并在手机 QQ 扫码登录后
自动配置 OneBot Token 和反向 WebSocket。单独检查或测试模型可运行：

```powershell
qq-aibot doctor
qq-aibot chat
```

`chat` 用于在不连接 QQ 的情况下测试当前服务商和模型。若使用其他 OneBot 接入端，
可用 `qq-aibot serve` 仅启动机器人，再手工配置接入端。机器人默认监听：

```text
ws://127.0.0.1:8080/onebot/v11/ws
```

其他接入端需要配置相同的反向 WebSocket 地址和 `.env` 中的 Token。如果接入端与机器人
不在同一台机器，请使用安全内网或带 TLS 的反向代理。

## 修改机器人行为

```powershell
qq-aibot config list
qq-aibot config get bot.group_mode

# mention=仅被@时回复，always=每条都回复，off=关闭群聊
qq-aibot config set bot.group_mode mention

# 用户白名单；空数组表示不限用户
qq-aibot config set bot.allowed_users "[12345678, 87654321]"

# 群白名单
qq-aibot config set bot.allowed_groups "[123456789]"

# 修改人格
qq-aibot config set llm.system_prompt "你是一个耐心、简洁的编程学习助手。"
```

修改普通机器人设置后重启生效。聊天用户可以发送 `/reset` 清空当前会话记忆，发送
`/help` 查看帮助。

## 清除记忆

```powershell
qq-aibot memory clear --session private:12345678
qq-aibot memory clear --session group:123456789
qq-aibot memory clear --yes
```

## GitHub 发布前检查

```powershell
git status --short
git diff --check
python -m unittest discover -s tests -v
```

确认没有 `.env`、`providers.json`、`data/` 或任何真实密钥，再提交和推送。

## 接口兼容范围

服务商需要兼容以下接口：

- `GET {api_base}/models`：可选，用于自动检索模型
- `POST {api_base}/chat/completions`：必需，用于生成回复
- `Authorization: Bearer <api_key>`：需要鉴权时使用

若某个服务商不是 OpenAI Chat Completions 兼容格式，需要另外编写适配器。

## License

MIT
