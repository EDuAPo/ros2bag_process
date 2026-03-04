# 环境配置指南

本文档说明如何在新设备上配置项目运行环境。

## 系统要求

- **操作系统**: Ubuntu 22.04 LTS (推荐)
- **Python**: 3.10+
- **ROS2**: Humble (必需)
- **磁盘空间**: 至少 100GB 可用空间

## 快速安装

### 方式1：自动安装（推荐）

```bash
# 克隆仓库
git clone https://github.com/EDuAPo/ros2bag_process.git
cd ros2bag_process

# 运行安装脚本
chmod +x install.sh
./install.sh
```

### 方式2：手动安装

#### 步骤1：安装系统依赖

```bash
# 更新包列表
sudo apt update

# 安装 GStreamer 及相关依赖
sudo apt install -y \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
    gstreamer1.0-plugins-ugly \
    gstreamer1.0-libav \
    libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev

# 安装 GObject 和 Cairo 开发库
sudo apt install -y \
    libgirepository1.0-dev \
    libcairo2-dev \
    pkg-config \
    python3-dev \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gstreamer-1.0

# 安装其他系统依赖
sudo apt install -y \
    build-essential \
    cmake \
    git
```

#### 步骤2：安装 ROS2 Humble

如果尚未安装 ROS2，请参考官方文档：
https://docs.ros.org/en/humble/Installation/Ubuntu-Install-Debians.html

```bash
# 快速安装命令（Ubuntu 22.04）
sudo apt install -y software-properties-common
sudo add-apt-repository universe
sudo apt update && sudo apt install -y curl
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" | sudo tee /etc/apt/sources.list.d/ros2.list > /dev/null
sudo apt update
sudo apt install -y ros-humble-desktop
```

#### 步骤3：安装 Python 依赖

```bash
# 创建虚拟环境（可选但推荐）
python3 -m venv venv
source venv/bin/activate

# 安装 Python 包
pip install --upgrade pip
pip install -r requirements.txt
```

**注意事项：**
- `PyGObject` 和 `pycairo` 需要先安装系统依赖才能成功编译
- 如果安装失败，请确保已执行步骤1中的系统依赖安装
- `open3d` 包较大（约500MB），安装可能需要几分钟

#### 步骤4：编译自定义 IMU 消息包

```bash
# 进入 IMU 消息包目录
cd export_imu/msg_interfaces
colcon build
source install/setup.bash

cd ../imu_msgs
colcon build
source install/setup.bash

# 返回项目根目录
cd ../..
```

#### 步骤5：配置项目

```bash
# 复制配置模板
cp config.yaml.example config.yaml

# 编辑配置文件，修改路径和参数
nano config.yaml
```

## 验证安装

运行以下命令验证环境配置是否正确：

```bash
# 检查 Python 依赖
python3 -c "import cv2, numpy, rosbags, open3d, gi; print('✅ 所有依赖已安装')"

# 检查 GStreamer
gst-inspect-1.0 --version

# 检查 ROS2
ros2 --version

# 测试 IMU 消息包
python3 -c "from msg_interfaces.msg import Hcinspvatzcb; print('✅ IMU 消息包1正常')"
python3 -c "from imu_msgs.msg import Imu; print('✅ IMU 消息包2正常')"
```

## 常见问题

### 问题1：PyGObject 安装失败

**错误信息：** `ERROR: Failed building wheel for PyGObject`

**解决方法：**
```bash
# 确保安装了所有系统依赖
sudo apt install -y libgirepository1.0-dev libcairo2-dev pkg-config python3-dev

# 重新安装
pip install --no-cache-dir PyGObject
```

### 问题2：pycairo 安装失败

**错误信息：** `ERROR: Failed building wheel for pycairo`

**解决方法：**
```bash
# 安装 Cairo 开发库
sudo apt install -y libcairo2-dev pkg-config python3-dev

# 重新安装
pip install --no-cache-dir pycairo
```

### 问题3：open3d 安装超时

**解决方法：**
```bash
# 使用国内镜像源
pip install -i https://pypi.tuna.tsinghua.edu.cn/simple open3d
```

### 问题4：rosbags 无法读取 bag 文件

**原因：** 缺少压缩库

**解决方法：**
```bash
pip install lz4 zstandard
```

### 问题5：IMU 消息包导入失败

**原因：** 未 source ROS2 环境

**解决方法：**
```bash
# 每次使用前需要 source
source /opt/ros/humble/setup.bash
source export_imu/msg_interfaces/install/setup.bash
source export_imu/imu_msgs/install/setup.bash
```

## 不同操作系统的注意事项

### Ubuntu 20.04

- 需要手动安装 Python 3.10+
- GStreamer 版本可能较旧，建议升级到 1.20+

### Ubuntu 24.04

- 系统自带 Python 3.12，完全兼容
- GStreamer 版本更新，无需额外配置

### 其他 Linux 发行版

- 需要手动安装对应的 GStreamer 包
- 包名可能不同，请参考发行版文档

## 性能优化建议

1. **使用 SSD 存储数据**：机械硬盘会严重影响处理速度
2. **增加内存**：建议至少 16GB RAM
3. **多核 CPU**：流水线支持多线程并行处理
4. **GPU 加速**（可选）：Open3D 支持 CUDA 加速点云处理

## 下一步

环境配置完成后，请参考 [README.md](README.md) 了解如何使用流水线。
