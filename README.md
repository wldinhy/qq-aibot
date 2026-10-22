# QQ Multi-Model AI Bot

一个在终端运行、支持 Windows / Linux、多个模型服务商和多个 API Key 的 QQ AI 机器人。由
[NapCat](https://github.com/NapNeko/NapCatQQ) 登录 QQ，再通过
[OneBot 11](https://github.com/botuniverse/onebot-11) 连接机器人；模型服务使用通用的
OpenAI Chat Completions 兼容接口。

> 登录 QQ 时需要你本人用手机 QQ 扫码确认。Windows 从 NapCat 官方 Release 下载接入端，Linux 调用 NapCat 官方安装器安装 QQ 和接入端。请遵守平台规则、接入端许可和云服务商条款。

QQ 号在 OneBot 接入端登录。控制台的“QQ / OneBot 连接”可填写这个机器人 QQ 号，
程序会核对消息中的 `self_id`，只处理该账号的消息。填写 QQ 号本身不会完成登录。

## 功能

- 不带参数运行 `qq-aibot`，进入完整交互式控制台
- 所有常用设置都有编号菜单，不需要记命令
- Windows / Linux 使用同一个 `install.py` 安装、`start.py` 配置和启动
- 自动初始化 OneBot Token，保留现有性格、服务商 Key、QQ 登录态和聊天数据
- 可安装或复用 ComfyUI；支持指定已有模型文件和 PyTorch 后端
- 纯 Linux 同一终端管理机器人、NapCat 和 ComfyUI；Windows 保留独立服务窗口
- 保留 Ubuntu/WSL 终端兼容入口和已有 `.venv-ubuntu` 环境
- DeepSeek、OpenAI 及其他 OpenAI 兼容服务
- 支持自建服务，例如提供 OpenAI 兼容接口的 Ollama、LM Studio 或 vLLM
- 保存多个服务商配置档，每个配置档拥有独立 URL、Key 和模型
- 通过 `GET /models` 检索模型并在终端选择
- 不支持模型检索的服务可以手工填写模型名
- 私聊默认向所有人开放；群聊默认被 `@` 时回复
- **结构化回复**：AI 自主决定发送 0-3 条文本和 0-1 个表情包，不再一问一答
- **内置表情包库**：8 个情绪表情，支持本地自定义扩展
- **身份保护**：拒绝用户通过对话修改 AI 名字、身份和人设，只能本地配置文件设定
- 每个私聊独立记忆，每个群共享群聊上下文
- 用户和群白名单、黑名单
- SQLite 本地记忆，无需额外数据库
- OneBot Token 鉴权、并发和长度限制
- 改进的终端界面：分组显示功能开关、回复限制和安全状态
- 可选 QQ 空间：评论指定好友的新文字动态、回复自己说说下的新评论、按时间自动发文字说说；全部由本地配置控制

## Windows / Linux 统一安装

需要 Python 3.10 或更新版本。安装 ComfyUI 还需要 Git；Windows 需要预先安装 QQ。Linux 的 NapCat 自动安装支持 apt / dnf 的 amd64、arm64 系统，安装系统依赖时可能需要 sudo；Debian/Ubuntu 需有 `python3-venv`。

下载 [最新源码 ZIP](https://github.com/wldinhy/qq-aibot/archive/refs/heads/main.zip) 并解压，或：

```bash
git clone https://github.com/wldinhy/qq-aibot.git
cd qq-aibot
```

两种系统运行相同的文件，Linux 的 Python 命令一般叫 `python3`，Windows 也可用 `py -3`：

| 步骤 | Windows | Linux |
| --- | --- | --- |
| 安装机器人和 NapCat | `py -3 install.py` 或 `install.cmd` | `python3 install.py` 或 `bash install.sh` |
| 配置模型 / Key / 性格 | `py -3 start.py --console` | `python3 start.py --console` |
| 启动配套服务 | `py -3 start.py` 或 `start.cmd` | `python3 start.py` 或 `bash start.sh` |
| 只检查安装计划 | `py -3 install.py --dry-run` | `python3 install.py --dry-run` |

重复安装会复用环境并保留配置。Windows 环境为 `.venv`；Linux 为 `.venv-linux`，已有 `.venv-ubuntu` 会继续复用。服务安装位置记录在 `.local/services-windows.json` / `.local/services-linux.json`，不会上传 GitHub。

首次登录请扫描 NapCat 二维码。启动器会在登录后配置本机器人的 OneBot 连接，并保留其他机器人的连接。Linux 日志显示在同一终端并保存到 `data/logs`；按 `Ctrl+C` 关闭本次启动的服务，复用的已有服务继续运行。Windows 仍打开独立窗口，保持窗口运行即可聊天。

### 同时安装本地生图

在上述安装命令后添加 `--with-comfyui`。有现成安装可指定 `--comfyui-home`，目录中应有 `main.py` 或 `ComfyUI/main.py`：

```text
python install.py --with-comfyui
python install.py --comfyui-home "已有的 ComfyUI 目录"
python install.py --with-comfyui --torch-backend cuda --checkpoint "已有模型文件.safetensors"
```

Linux 将 `python` 换成 `python3`。已有 ComfyUI 的环境和模型优先复用；新安装位于 `.local/ComfyUI`，使用独立虚拟环境。`--torch-backend` 支持 `auto`、`cuda`、`cpu`、`rocm`（Linux AMD）、`xpu`（Intel）；新装时 `auto` 检测到 `nvidia-smi` 会选择 CUDA，否则使用 CPU。显卡驱动需要由系统安装，AMD / Intel 应显式选择后端。

模型权重不会自动下载。`--checkpoint` 指向已有的 `.safetensors` 或 `.ckpt` 文件，会把它接入模型搜索路径并开启生图，无需复制大文件。内置工作流使用 SDXL / Animagine 节点，应选兼容的模型。只安装 ComfyUI 时不会自动开启生图；放好模型后可在菜单中开启。

已有外部 QQ 接入端时，可用 `python install.py --bot-only` 只安装机器人，再用 `python start.py serve` 只启动机器人服务。`--napcat-home` 可复用已有 NapCat：Linux 目录包含 `opt/QQ/qq`，Windows 目录包含 `napcat.mjs`。

安装器依照 [NapCat 官方 Linux 安装说明](https://napneko.github.io/guide/boot/Shell) 和 [ComfyUI 官方手动安装说明](https://docs.comfy.org/installation/manual_install) 调用官方来源。

### Windows 上的 WSL 终端

`start-ubuntu.cmd` 保留原来同时启动三个服务的行为，机器人日志显示在 Ubuntu 终端，NapCat 和 ComfyUI 在 Windows 运行。已有 `QQ AI Bot.lnk` 可继续使用；快捷方式含本机绝对路径，不随源码发布。

WSL 中已有 Windows `.venv` 时，`bash start.sh` 保持 Windows Python 兼容模式，便于访问 Windows 的本机服务。`bash start.sh --native` 则启动纯 Linux 服务流程；配置界面使用 `bash start.sh --console`。Windows 服务和原生 WSL 服务需要混合连接时，再根据 WSL 镜像 / NAT 网络模式配置地址；纯 Linux 本机部署直接使用 `127.0.0.1`。

本地会生成以下不会上传 GitHub 的文件或目录：

- `providers.json`：服务商、模型和 API Key
- `config.json`：机器人性格、QQ 号和本机功能配置
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

# 修改人格（只能本地配置，用户无法通过对话修改）
qq-aibot config set llm.system_prompt "你是一个耐心、简洁的编程学习助手。"

# 回复限制
qq-aibot config set llm.max_tokens 700
qq-aibot config set bot.max_reply_messages 3
qq-aibot config set bot.max_reply_chars_per_message 280
qq-aibot config set bot.max_reply_stickers 1

# 表情包功能；空路径使用随程序附带的表情库
qq-aibot config set stickers.enabled true
qq-aibot config set stickers.manifest '""'
```

修改普通机器人设置后重启生效。聊天用户可以发送 `/reset` 清空当前会话记忆，发送
`/help` 查看帮助。

## 结构化回复和表情包

机器人使用结构化回复计划，AI 自主决定：
- 发送 0-3 条文本消息（默认最多 3 条）
- 可选发送 0-1 个表情包
- 每条消息最多 280 字符（可配置）
- 总字符数不超过 1800

内置 8 个基础情绪表情：happy、laugh、think、confused、cry、angry、cool、surprised。
可通过 `stickers.manifest` 配置本地表情包库，支持 PNG、JPG、GIF、WebP 格式。

表情包清单格式：
```json
{
  "stickers": [
    {
      "id": "happy",
      "file": "happy.png",
      "tags": ["开心", "高兴"],
      "caption": "开心"
    }
  ]
}
```

## 身份保护

机器人拒绝通过 QQ 对话修改名字、身份、角色和人设。以下请求会被礼貌拒绝：
- "你叫小明"
- "你现在是医生"
- "扮演一个老师"
- "改变你的身份"
- "增加设定"

正常的命名请求（给宠物起名、故事角色等）和创作请求不受影响。身份只能通过本地 `llm.system_prompt` 设定。
本地性格设置在每条新消息开始处理时重新读取，不需要为性格修改重启机器人或手动 `/reset`。同一条请求的性格和 token 参数使用独立快照，避免并发时互相覆盖。聊天上下文按性格提示词分开保存，旧设定的回复不会混入新设定；升级时会先用 SQLite 备份接口备份旧数据库，原记录保留。首次加载新性格会从独立上下文开始；将性格改回原来的完整提示词，可继续该设定下的新版本上下文。其他运行参数和程序代码更新仍需重新加载进程。
所有会话请求都会附加系统保护规则，显式改名和改人设命令还会经过程序规则检查。对话内容没有修改配置文件的入口；这些保护降低模型被诱导改口的概率，但不声称提示词能绝对阻止所有提示注入。

## 本地图片生成（ComfyUI）

在原有聊天功能上增加一个可选工具，由当前 DeepSeek 根据消息和对话上下文决定是否生成图片、选择文生图或图生图，并编写英文提示词。普通聊天仍在同一次模型请求中返回文字。启用时服务商必须支持 Chat Completions 的 `tools` / `tool_calls`，推荐使用现有 DeepSeek 配置。

内置两套 API 工作流来自 `D:\Comfy-Desktop` 中已执行图片的工作流元数据，只保留模型、节点连接、负面提示词和采样参数。原来的正面提示词和私人图片路径没有保留：

- 文生图：`animagine-xl-4.0 .safetensors`，768×1024，28 步，CFG 7，DPM++ 2M / Karras，denoise 1。
- 图生图：同一模型，30 步，CFG 7，DPM++ 2M / Karras，默认 denoise 0.45；原图按比例缩放到约 0.75 兆像素、尺寸对齐 64 像素后进行 VAE 编码，避免大图占用过多显存。

使用方式：

1. 双击 `start.cmd`（Ubuntu 终端用 `start-ubuntu.cmd`）；启用生图时会自动启动 ComfyUI 本地服务、NapCat 和机器人。ComfyUI 默认使用 `D:\Comfy-Desktop\ComfyUI-Installs\ComfyUI` 的现有 Python 和模型，服务地址为 `http://127.0.0.1:8188`。已运行的服务会直接复用。自动启动的 ComfyUI 输入、输出和数据库放在机器人目录的 `data/comfyui` 中，控制台显示启动日志；无需另开 ComfyUI Desktop。其他安装位置可通过环境变量 `COMFYUI_HOME` 指定安装目录。
2. 首次安装默认关闭生图；运行下面的命令启用，然后重启机器人。本次开发使用的本地 `config.json` 已启用。
3. 在 QQ 私聊中发送“画一只坐在花园里的小猫”；修改图片时，把图片附件和“把这张图改成水彩风格”的文字放在**同一条消息**中。

```powershell
qq-aibot config set image_generation.enabled true
qq-aibot config set image_generation.api_base "http://127.0.0.1:8188"
qq-aibot config set image_generation.timeout_seconds 300

# 关闭功能后重启，即恢复原有纯文字聊天
qq-aibot config set image_generation.enabled false
```

图生图只使用当前消息的第一张附件，不自动读取引用消息或历史图片。启用看图后，图片会发送给当前支持视觉的模型 API 理解内容；图片生成仍在本机 ComfyUI 执行。机器人先回复生成提示，再通过 OneBot 图片消息段发送结果；模型错误、未启动或超时会明确回复失败。GPU 任务在机器人内串行执行，避免同时加载多个生成任务。超时结束机器人等待，ComfyUI 已入队任务可能继续执行，可在 ComfyUI 队列中取消。

聊天权限和群聊开关继续生效；群聊仍需按原有设置开启。原图上传到 ComfyUI 的 `input/qq_aibot`，生成结果保存在其输出目录。机器人使用 base64 回传，NapCat 无需共享输出目录。输入和输出文件保留在 ComfyUI 中，可按需清理；`/reset` 只清空聊天记忆。

工作流文件位于 `src/qq_aibot/workflows/`，会随安装包一起发布。可通过 `image_generation.text_workflow` / `image_generation.image_workflow` 指定 API 格式 JSON 路径（相对路径基于机器人目录）。自定义工作流需保留当前绑定：正面文本节点 `2`、采样种子节点 `4`、输出节点 `7`，图生图加载原图节点 `9`。前端画布 JSON 需要先导出为 API 格式。

## 图片、表情包识别与旧消息过滤

默认启用 `vision.enabled`，当前 `deepseek-flash` 支持真实图片输入。发送图片或表情包即可让机器人根据图片内容、图中文字和对话上下文回复，单独发图不会自动触发生图。QQ 自带表情会保留名称或 ID；自定义图片表情包会作为图片发送给模型。默认每条消息最多识别 3 张图片，每张最多 10 MB，支持 PNG、JPEG、GIF 和 WebP。

机器人先下载 QQ 提供的图片链接；链接因 Rkey 失效或网络问题无法下载时，改用 OneBot `get_image` 请求 NapCat 下载原图，读取其返回的 base64 或本地文件。若两种方式均失败，会明确告诉模型图片未读取，避免猜测内容。这个回退不修复第三方 Rkey 服务本身，NapCat 的 Rkey 错误日志仍可能出现。

默认 `bot.ignore_before_start=true`，按 QQ 消息的 `time` 时间戳忽略机器人启动前的消息，也忽略没有有效时间戳的消息；同一次运行内对已收到的消息 ID 去重，防止连接恢复后重复回复。已有聊天记忆继续作为新消息的上下文，不会主动补发历史回复。图片二进制不会写入聊天数据库。

```powershell
qq-aibot config set vision.enabled true
qq-aibot config set bot.ignore_before_start true
```

代码更新后，停止机器人窗口中的旧实例，再用 `start.cmd` 或 `start-ubuntu.cmd` 启动即可。性格和聊天参数等支持热加载的设置无需重启，保存提示会说明生效时间。NapCat 和 ComfyUI 已运行时会复用。

## 请求排队和生图提示词优化

默认 `bot.processing_mode=parallel`：同一私聊或同一群的消息按到达顺序逐条处理，前一条的回复写入记忆后才处理后一条；不同对话最多 `bot.max_concurrency` 路并行，默认 4 路。同一个人连续发送多条消息只占一个处理名额，其他人的对话仍能被调度。需要等待时发送排队提示；单条失败会回复失败原因，后续请求继续执行。ComfyUI 的 GPU 生成任务按提交顺序串行执行。

如果需要所有人的请求都严格按全局到达顺序处理，设置 `serial`：

```powershell
qq-aibot config set bot.processing_mode serial

# 多路模式：每段对话保持顺序，不同对话并行
qq-aibot config set bot.processing_mode parallel
qq-aibot config set bot.max_concurrency 4
```

队列保存在当前进程内，不会因为忙碌丢弃已接收请求；停止进程会结束当前任务和等待队列。建议等待排队任务完成后再重启。启动前的 QQ 消息继续被过滤，不会重新补回。

默认 `image_generation.optimize_prompt=true`。DeepSeek 判断需要生图后，会再用一轮请求整理适合 Animagine XL 的英文标签和负面提示词：主体和修改目标优先、去掉重复或矛盾描述、忠实保留用户要求。图生图结合第一张真实原图识别特征，写清最终画面的主体、构图和修改内容，并选择重绘强度：小幅细节修改通常 0.25–0.35，背景或服装改动 0.4–0.5，风格重绘 0.55–0.65。优化失败会回退到第一轮提示词继续生成。这个步骤增加一轮模型调用和少量等待时间。

这是普通图生图流程，会重绘整张图；低重绘强度有助于保留特征，但不能保证人物身份和未修改区域完全一致，也不是按遮罩局部编辑。可调整默认值，或关闭额外提示词整理：

```powershell
qq-aibot config set image_generation.img2img_denoise 0.45
qq-aibot config set image_generation.img2img_megapixels 0.75
qq-aibot config set image_generation.optimize_prompt false
```

## 终端设置与聊天上下文

Ubuntu 中在项目目录运行 `bash start.sh --console` 打开配置界面。交互终端会在原位置刷新页面，保存提示留在页面顶部；输出重定向到文件时使用普通文本。主菜单选择 **3 → 10** 设置自然聊天、回复风格、Token、消息条数、字符上限、表情包数量和群聊上下文；**3 → 11** 设置识图、生图和提示词优化；**3 → 12** 设置排队与旧消息过滤。每次保存都会提示生效时间。

默认启用 `llm.natural_style=true` 和 `llm.reply_style=brief`：日常聊天简短接话，减少客服套话、复述、说教和机械追问；需要解释的问题可以展开。`llm.reply_style=adaptive` 可改为按问题决定详细程度。本地性格提示词仍由管理员配置。性格、聊天风格、回复上限和上下文设置从下一条请求读取；并发数、排队模式及生图服务参数在下次启动生效。

默认 `bot.group_context=true`。群聊回复开启后，机器人将同一群中启动后实际收到的近期消息作为背景，包括未 @ 机器人的发言，保留昵称、QQ 号和当前运行期间收到过的引用内容；是否触发回复仍由群聊模式决定。黑名单、白名单和旧消息过滤继续生效。群聊关闭时不收集群聊上下文，不同群和私聊互相隔离。群聊现场上下文保存在有大小上限的内存中，重启后从新收到的消息重新积累；原有聊天数据库记录保留。私聊继续使用双方实际对话记录。模型被要求优先依据用户发言，被纠正时更新理解，不能把自己的旧猜测当成事实。

当前消息的图片仍可实际识别；背景里的历史图片只有文本摘要，不会猜测未看到的图片。尚未收到过的引用内容会标为未知。`/reset` 同时清空当前会话的数据库记忆和群聊现场上下文。

## QQ 空间评论与自动发说说

终端主菜单 **3 → 13「QQ空间」** 提供总开关、评论指定好友、回复自己空间新评论、自动发说说，以及好友 QQ 列表、每日次数、检查间隔、发布时间、主题、文案长度和可见范围。默认全部关闭，好友列表为空时不评论任何好友。设置在后台下一轮检查时生效，关闭总开关会停止空间后台自行启动的桥接进程，现有聊天继续运行。

**仅自动发文字说说**走 NapCat 原生 `send_qzone_msg`，需要当前 NapCat 具备该接口，无需安装其他组件。默认好友可见，每日次数不限（`max_posts_per_day=0`）；发布时间采用机器人所在电脑的本地时区。模型使用本地性格和你配置的主题写正文，不读取私聊、群聊或聊天数据库作为发空间的素材。

**好友动态评论和自己空间评论回复**需要可选的 [onebot-qzone 桥接组件](https://github.com/Gu-Heping/onebot-qzone)（MIT 许可）。它补充 NapCat 尚未原生提供的读取与评论能力。先安装 Node.js 18+、npm 和 Git，再在项目目录执行：

```text
# Windows
py -3 install.py --with-qzone
# Linux
python3 install.py --with-qzone
```

也可与 `--with-comfyui` 一起安装；仅使用外部 NapCat 时可选 `--bot-only --with-qzone`。组件源码与依赖放在 `.local/qzone-bridge`，重复安装会复用已构建的组件并保留凭证。安装不启动空间任务，也不发布内容。启用相应功能后，机器人通过现有 NapCat 连接获取同一登录账号的空间 Cookie，启动仅监听本机的桥接服务；无需把 Cookie 填进公开配置。桥接与 NapCat 账号不一致时停止操作。当前登录态必须能提供有效的空间 `p_skey`，凭证失效时空间任务暂停。

后台只评论指定好友的**新动态**，回复自己说说下的**新评论**或明确回复机器人的楼中楼，不插入其他人之间的楼中楼对话。初始化和每次启用先记录已有内容作为基线，启动前、未带有效时间戳的内容不补回复。黑名单继续生效；默认每日评论和回复次数不限（`max_comments_per_day=0`），操作间隔至少 120 秒。两个每日次数设置中，`0` 表示不限，正整数表示你自行设置的每日上限；自动发说说仍按配置时间执行。AI 可判断无需回应；仅有未读取图片时不猜测图片内容。

空间接口的评论读取属于 best-effort：部分说说的评论区不会出现在服务返回的 HTML 中。遇到不可读、权限不足或登录失效时跳过，不把它误判为空评论。首版自动创作和互动使用文字，不自动配图。

任务记录保存在 `data/qzone.db`，桥接日志位于 `data/logs/qzone-bridge.log`，凭证和缓存位于 `.local`。终端空间菜单可查看最近执行记录。请求写入前会持久化占位，防止重启、重连重复发布；`pending` / `uncertain` 表示结果待人工核实，程序不会自动重发，避免服务已发布却超时导致重复。安装包与 GitHub 源码不包含这些数据。

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
