#!/bin/bash
set -e
cd -- "$(dirname -- "$0")"

echo "QQ AI Bot - Ubuntu/WSL 安装脚本"
echo "================================"

if ! command -v python3 &> /dev/null; then
    echo "错误：未找到 python3，请先安装 Python 3.10 或更高版本"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python 版本：$PYTHON_VERSION"

if [ ! -d ".venv-ubuntu" ]; then
    echo "创建 Ubuntu 虚拟环境..."
    python3 -m venv .venv-ubuntu
    echo "虚拟环境已创建：.venv-ubuntu"
else
    echo "虚拟环境已存在：.venv-ubuntu"
fi

echo "激活虚拟环境并安装依赖..."
source .venv-ubuntu/bin/activate
pip install -e .

echo ""
echo "安装完成！"
echo ""
echo "下一步："
echo "1. 配置服务商和模型："
echo "   .venv-ubuntu/bin/qq-aibot provider add deepseek --base-url https://api.deepseek.com"
echo ""
echo "2. 设置 OneBot Token："
echo "   .venv-ubuntu/bin/qq-aibot secret set onebot"
echo ""
echo "3. 启动机器人（在 Windows 上启动 NapCat 和 ComfyUI）："
echo "   .venv-ubuntu/bin/qq-aibot serve"
echo ""
echo "注意：Ubuntu WSL 网络配置"
echo "- WSL2 镜像模式：localhost 直接可用"
echo "- WSL2 NAT 模式：需要使用 Windows 主机 IP（如 172.x.x.x）访问 ComfyUI 和 OneBot"
echo "- 机器人监听地址应设为 0.0.0.0 以便 Windows NapCat 连接"
