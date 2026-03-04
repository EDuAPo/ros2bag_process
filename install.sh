#!/bin/bash

# ROS2 Bag 预处理流水线 - 自动安装脚本
# 适用于 Ubuntu 22.04 LTS

set -e  # 遇到错误立即退出

echo "=========================================="
echo "  ROS2 Bag 预处理流水线 - 环境配置"
echo "=========================================="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查是否为 root 用户
if [ "$EUID" -eq 0 ]; then
    echo -e "${RED}❌ 请不要使用 root 用户运行此脚本${NC}"
    echo "   使用普通用户运行，脚本会在需要时提示输入 sudo 密码"
    exit 1
fi

# 检查操作系统
if [ ! -f /etc/os-release ]; then
    echo -e "${RED}❌ 无法检测操作系统版本${NC}"
    exit 1
fi

source /etc/os-release
if [ "$ID" != "ubuntu" ]; then
    echo -e "${YELLOW}⚠️  警告: 此脚本仅在 Ubuntu 上测试过${NC}"
    echo "   当前系统: $ID $VERSION_ID"
    read -p "是否继续? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

echo -e "${GREEN}✓${NC} 操作系统: $PRETTY_NAME"
echo ""

# 步骤1: 更新包列表
echo "=========================================="
echo "步骤 1/5: 更新系统包列表"
echo "=========================================="
sudo apt update
echo -e "${GREEN}✓${NC} 包列表已更新"
echo ""

# 步骤2: 安装系统依赖
echo "=========================================="
echo "步骤 2/5: 安装系统依赖"
echo "=========================================="
echo "正在安装 GStreamer 和相关库..."

sudo apt install -y \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev \
    libgirepository1.0-dev \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gstreamer-1.0 \
    build-essential \
    cmake \
    git \
    python3-pip \
    python3-venv

echo -e "${GREEN}✓${NC} 系统依赖已安装"
echo ""

# 步骤3: 检查 ROS2
echo "=========================================="
echo "步骤 3/5: 检查 ROS2 环境"
echo "=========================================="

if [ -f /opt/ros/humble/setup.bash ]; then
    echo -e "${GREEN}✓${NC} 检测到 ROS2 Humble"
    source /opt/ros/humble/setup.bash
else
    echo -e "${YELLOW}⚠️  未检测到 ROS2 Humble${NC}"
    echo ""
    echo "请选择操作:"
    echo "  1) 自动安装 ROS2 Humble (推荐)"
    echo "  2) 跳过 ROS2 安装 (稍后手动安装)"
    echo "  3) 退出安装"
    read -p "请选择 [1-3]: " -n 1 -r
    echo

    case $REPLY in
        1)
            echo "正在安装 ROS2 Humble..."
            sudo apt install -y software-properties-common
            sudo add-apt-repository universe -y
            sudo apt update
            sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
            echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
            sudo apt update
            sudo apt install -y ros-humble-desktop
            echo -e "${GREEN}✓${NC} ROS2 Humble 安装完成"
            source /opt/ros/humble/setup.bash
            ;;
        2)
            echo -e "${YELLOW}⚠️  跳过 ROS2 安装${NC}"
            echo "   请稍后手动安装: https://docs.ros.org/en/humble/Installation.html"
            ;;
        3)
            echo "安装已取消"
            exit 0
            ;;
        *)
            echo -e "${RED}❌ 无效选择${NC}"
            exit 1
            ;;
    esac
fi
echo ""

# 步骤4: 安装 Python 依赖
echo "=========================================="
echo "步骤 4/5: 安装 Python 依赖"
echo "=========================================="

# 询问是否使用虚拟环境
echo "是否创建 Python 虚拟环境? (推荐)"
echo "  虚拟环境可以隔离项目依赖，避免污染系统 Python"
read -p "创建虚拟环境? (Y/n) " -n 1 -r
echo

if [[ $REPLY =~ ^[Nn]$ ]]; then
    echo "使用系统 Python 环境"
    PYTHON_CMD="python3"
    PIP_CMD="pip3"
else
    echo "创建虚拟环境: venv"
    python3 -m venv venv
    source venv/bin/activate
    PYTHON_CMD="python"
    PIP_CMD="pip"
    echo -e "${GREEN}✓${NC} 虚拟环境已创建并激活"
fi

echo "正在安装 Python 包..."
$PIP_CMD install --upgrade pip

# 检查 requirements.txt 是否存在
if [ ! -f requirements.txt ]; then
    echo -e "${RED}❌ 错误: requirements.txt 文件不存在${NC}"
    exit 1
fi

# 安装依赖
$PIP_CMD install -r requirements.txt

echo -e "${GREEN}✓${NC} Python 依赖已安装"
echo ""

# 步骤5: 编译 IMU 消息包
echo "=========================================="
echo "步骤 5/5: 编译自定义 IMU 消息包"
echo "=========================================="

if [ ! -d "export_imu/msg_interfaces" ]; then
    echo -e "${YELLOW}⚠️  未找到 msg_interfaces 目录，跳过编译${NC}"
else
    echo "编译 msg_interfaces..."
    cd export_imu/msg_interfaces
    colcon build
    source install/setup.bash
    cd ../..
    echo -e "${GREEN}✓${NC} msg_interfaces 编译完成"
fi

if [ ! -d "export_imu/imu_msgs" ]; then
    echo -e "${YELLOW}⚠️  未找到 imu_msgs 目录，跳过编译${NC}"
else
    echo "编译 imu_msgs..."
    cd export_imu/imu_msgs
    colcon build
    source install/setup.bash
    cd ../..
    echo -e "${GREEN}✓${NC} imu_msgs 编译完成"
fi

echo ""

# 步骤6: 创建配置文件
echo "=========================================="
echo "配置文件设置"
echo "=========================================="

if [ ! -f config.yaml ]; then
    if [ -f config.yaml.example ]; then
        echo "从模板创建配置文件..."
        cp config.yaml.example config.yaml
        echo -e "${GREEN}✓${NC} 已创建 config.yaml"
        echo -e "${YELLOW}⚠️  请编辑 config.yaml 配置路径和参数${NC}"
    else
        echo -e "${YELLOW}⚠️  未找到配置文件模板${NC}"
        echo "   请手动创建 config.yaml"
    fi
else
    echo -e "${GREEN}✓${NC} config.yaml 已存在"
fi

echo ""

# 验证安装
echo "=========================================="
echo "验证安装"
echo "=========================================="

echo -n "检查 Python 依赖... "
if $PYTHON_CMD -c "import cv2, numpy, rosbags, open3d, gi" 2>/dev/null; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    echo -e "${YELLOW}⚠️  部分 Python 依赖可能未正确安装${NC}"
fi

echo -n "检查 GStreamer... "
if command -v gst-inspect-1.0 &> /dev/null; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
fi

echo -n "检查 ROS2... "
if command -v ros2 &> /dev/null; then
    echo -e "${GREEN}✓${NC}"
else
    echo -e "${RED}✗${NC}"
    echo -e "${YELLOW}⚠️  ROS2 未安装或未添加到 PATH${NC}"
fi

echo ""

# 完成
echo "=========================================="
echo -e "${GREEN}✓ 安装完成！${NC}"
echo "=========================================="
echo ""
echo "下一步操作:"
echo "  1. 编辑配置文件: nano config.yaml"
echo "  2. 编辑时间段配置: nano time_peridos.yaml"
echo "  3. 运行流水线: python3 pipline.py --logtime 20260107"
echo ""

if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    echo "注意: 每次使用前需要激活虚拟环境:"
    echo "  source venv/bin/activate"
    echo ""
fi

echo "详细使用说明请参考: README.md"
echo ""
