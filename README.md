# ROS2 Bag 预处理流水线

自动化处理 ROS2 bag 数据的完整流水线，包括时间筛选、数据导出、图像去畸变、文件清理和压缩打包。

## 功能特性

- ✅ 支持多时间段批量处理
- ✅ 自动导出相机图像、激光雷达点云、IMU数据
- ✅ 图像去畸变处理
- ✅ 智能文件清理（基于 sample.json）
- ✅ 自动压缩打包
- ✅ 支持多种 IMU 消息类型
- ✅ 统一配置管理

## 目录结构

```
export_ros2bag/
├── config.yaml              # 主配置文件
├── time_peridos.yaml        # 时间段配置文件
├── pipline.py              # 流水线主脚本
├── run_export.py           # 数据导出脚本
├── move_file.py            # 时间筛选脚本
├── export_camera.py        # 相机导出
├── export_lidar.py         # 激光雷达导出
├── export_imu/             # IMU 导出模块
│   ├── export_imu.py
│   ├── msg_interfaces/     # 自定义消息包1
│   └── imu_msgs/           # 自定义消息包2
├── undistortion/           # 去畸变模块
└── check_and_compress.py   # 压缩脚本
```

## 快速开始

### 1. 修改配置文件

#### `config.yaml` - 主配置文件

```yaml
# ---------- 路径配置 ----------
paths:
  # 原始bag文件存放目录
  source_bag_dir: "/media/zgw/T7/0304_z/"

  # 主输出目录（所有处理结果存放位置）
  main_output_dir: "/media/zgw/T7/pipeline_output/"

  # 临时筛选目录（移动模式下的中转目录）
  temp_filter_dir: "/media/zgw/T7/temp_filter/"

# ---------- 车辆配置 ----------
vehicle:
  # 车辆型号（用于查找对应的去畸变参数）
  model: "vehicle_000"

  # 日志时间戳（用于输出目录命名）
  logtime: "20260107"

# ---------- 处理参数 ----------
processing:
  # 图像缩放最小比例
  scale_min: 0.2

  # 点云导出格式：pcd_binary, pcd_ascii, ply
  lidar_format: "pcd_binary"

  # 是否使用移动模式（true=移动原始文件，false=复制文件）
  # 注意：使用T7硬盘数据，建议使用复制模式避免数据丢失
  move_mode: false

# ---------- 时间段配置 ----------
time_periods:
  # 格式：[开始时间HHMMSS, 结束时间HHMMSS]
  # 可配置多个时间段，流水线会依次处理
  - [143855, 143926]
```

#### `time_peridos.yaml` - 时间段配置

```yaml
- [143854, 144050]  # 格式：[开始时间HHMMSS, 结束时间HHMMSS]
```

**注意：** 这个文件的时间段应该比 `config.yaml` 中的时间段稍微宽一点（前后各留1-2秒），确保不会漏掉边界数据。

### 2. 运行流水线

#### 方式1：使用默认配置（推荐）

```bash
python3 pipline.py --logtime 20260107
```

这会：
- 从 `config.yaml` 读取所有配置
- 从 `time_peridos.yaml` 读取时间段列表
- 使用配置文件中的移动模式设置
- 自动执行：筛选 → 导出 → 去畸变 → 清理 → 压缩

#### 方式2：覆盖部分配置

```bash
# 指定车辆型号
python3 pipline.py --logtime 20260107 --vehicle vehicle_001

# 指定输出目录
python3 pipline.py --logtime 20260107 --main-out /path/to/output

# 使用复制模式（不移动原始文件）
python3 pipline.py --logtime 20260107 --no-move

# 跳过压缩步骤
python3 pipline.py --logtime 20260107 --skip-check-compress

# 跳过 sample.json 清理步骤
python3 pipline.py --logtime 20260107 --skip-clean-json
```

#### 方式3：完整参数示例

```bash
python3 pipline.py \
  --logtime 20260107 \
  --vehicle vehicle_000 \
  --main-out /media/zgw/T7/pipeline_output/ \
  --yaml-path ./time_peridos.yaml \
  --no-move
```

## 流水线处理步骤

流水线会自动执行以下7个步骤：

1. **步骤1：时间筛选** - 从原始bag中筛选指定时间段的数据
2. **步骤2：相机导出** - 导出相机图像
3. **步骤3：激光雷达导出** - 导出点云数据
4. **步骤4：IMU导出** - 导出IMU/INS数据到 `ins.json`
5. **步骤5：图像去畸变** - 对相机图像进行去畸变处理
6. **步骤6：样本提取** - 生成 `sample.json` 并清理不需要的文件
7. **步骤7：压缩打包** - 检查并压缩最终数据

## 支持的 IMU 消息类型

流水线支持以下3种 IMU 消息类型（按优先级排序）：

1. `msg_interfaces/msg/Hcinspvatzcb` - CHCNAV INS 消息（优先）
2. `imu_msgs/msg/Imu` - 自定义 IMU 消息（包含位置信息）
3. `sensor_msgs/msg/Imu` - 标准 ROS2 IMU 消息（仅姿态）

系统会自动检测 bag 中的消息类型并选择优先级最高的进行处理。

## 输出目录结构

```
pipeline_output/
└── 143854_144050/              # 时间段目录
    └── undistorted/            # 最终输出目录
        ├── ins.json            # IMU数据
        ├── sample.json         # 采样信息
        ├── sensor_config_combined_latest.json  # 传感器配置
        ├── camera_cam_3M_front/
        │   └── scale_0.20/     # 去畸变后的图像
        ├── camera_cam_3M_left/
        ├── camera_cam_3M_rear/
        ├── camera_cam_3M_right/
        ├── camera_cam_8M_pt_front/
        ├── camera_cam_8M_wa_front/
        ├── iv_points_front_left/
        │   └── pcd_binary/     # 点云数据
        ├── iv_points_front_mid/
        ├── iv_points_front_right/
        ├── iv_points_left_mid/
        ├── iv_points_right_mid/
        ├── iv_points_rear_left/
        └── iv_points_rear_right/
```

## 运行前检查清单

- [ ] `config.yaml` 中的路径都正确
- [ ] `config.yaml` 中的 `logtime` 与数据日期匹配
- [ ] `time_peridos.yaml` 中的时间段正确
- [ ] 原始bag目录存在且包含 `.db3` 文件
- [ ] 输出目录有足够的磁盘空间（建议至少100GB）
- [ ] 如果使用移动模式（`move_mode: true`），确保原始数据有备份

## 常用命令

```bash
# 查看帮助
python3 pipline.py --help

# 清理移动记录（如果之前使用移动模式失败）
python3 pipline.py --logtime 20260107 --clean-records

# 只运行到导出步骤，不压缩
python3 pipline.py --logtime 20260107 --skip-check-compress

# 测试配置是否正确（不实际运行）
python3 -c "from config_loader import config; print(config)"
```

## 配置说明

### 移动模式 vs 复制模式

- **移动模式** (`move_mode: true`)：
  - 优点：节省磁盘空间
  - 缺点：原始数据会被移动，需要确保有备份
  - 适用场景：磁盘空间紧张，且有原始数据备份

- **复制模式** (`move_mode: false`)：
  - 优点：保留原始数据，更安全
  - 缺点：需要更多磁盘空间
  - 适用场景：磁盘空间充足，或使用外部硬盘数据

### 图像缩放比例

`scale_min: 0.2` 表示将图像缩放到原始尺寸的20%，可以根据需求调整：
- `0.2` - 20%（默认，节省空间）
- `0.5` - 50%（中等质量）
- `1.0` - 100%（原始尺寸）

### 点云格式

`lidar_format` 支持以下格式：
- `pcd_binary` - PCD二进制格式（推荐，文件小）
- `pcd_ascii` - PCD文本格式（可读性好，文件大）
- `ply` - PLY格式（通用性好）

## 故障排查

### 问题1：IMU 导出失败

**症状：** `ins.json` 未生成

**原因：** bag 中的 IMU 消息类型不支持

**解决：**
```bash
# 检查 bag 中的消息类型
ros2 bag info /path/to/bag

# 确认是否包含支持的 IMU 消息类型
```

### 问题2：压缩步骤被跳过

**症状：** 显示 "缺少JSON文件"

**原因：** 必需的 JSON 文件未生成

**解决：** 检查前面的步骤是否都成功执行，特别是 IMU 导出和样本提取步骤

### 问题3：磁盘空间不足

**症状：** 流水线中途失败

**解决：**
- 清理不需要的旧数据
- 使用更大的磁盘
- 调整 `scale_min` 减小图像尺寸
- 减少处理的时间段数量

## 依赖环境

- Python 3.8+
- ROS2 Humble
- 必需的 Python 包：
  - `pyyaml`
  - `numpy`
  - `opencv-python`
  - `scipy`
  - `pyproj`

## 许可证

内部使用项目

## 更新日志

### 2026-03-04
- 优化 `run_export.py`，从 `config.yaml` 读取配置，消除硬编码
- 新增支持 `imu_msgs/msg/Imu` 消息类型
- 改进 IMU 数据导出，支持多种消息类型优先级选择
- 更新文档，添加完整的使用说明

### 2026-01-07
- 初始版本
- 实现完整的数据处理流水线
