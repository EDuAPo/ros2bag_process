# 配置管理使用指南

## 概述

为了解决多个脚本中路径和参数分散配置的问题，引入了统一的配置管理方案：
- **配置文件**: `config.yaml` - 所有路径和参数的中心化配置
- **加载工具**: `config_loader.py` - 提供便捷的配置访问接口

## 优势

1. **集中管理**: 所有配置集中在一个文件，修改方便
2. **避免重复**: 不需要在多个脚本中修改相同的路径
3. **类型安全**: 提供属性访问，IDE有代码提示
4. **易于维护**: 配置和代码分离，便于版本控制
5. **环境适配**: 支持环境变量和 `~` 路径展开

## 快速开始

### 1. 修改配置文件

编辑 `config.yaml`，修改你需要的路径和参数：

```yaml
paths:
  source_bag_dir: "/your/bag/directory/"
  main_output_dir: "/your/output/directory/"

vehicle:
  model: "vehicle_001"
  logtime: "20260304"

time_periods:
  - [100000, 110000]
  - [120000, 130000]
```

### 2. 在脚本中使用配置

#### 方式1：导入全局配置实例（推荐）

```python
from config_loader import config

# 直接使用属性访问
source_dir = config.source_bag_dir
output_dir = config.main_output_dir
vehicle = config.vehicle_model

# 访问嵌套配置
time_periods = config.time_periods
cameras = config.cameras
```

#### 方式2：使用 get() 方法

```python
from config_loader import config

# 使用点号路径访问
source_dir = config.get("paths.source_bag_dir")
scale_min = config.get("processing.scale_min", default=0.2)
```

#### 方式3：创建新实例（指定配置文件）

```python
from config_loader import ConfigLoader

# 使用自定义配置文件
config = ConfigLoader("/path/to/custom_config.yaml")
```

## 配置项说明

### 路径配置 (paths)

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `source_bag_dir` | 原始bag文件目录 | - |
| `main_output_dir` | 主输出目录 | - |
| `temp_filter_dir` | 临时筛选目录 | - |
| `move_record_dir` | 移动记录目录 | - |
| `undistortion_params_dir` | 去畸变参数目录 | - |
| `imu_msgs_install_path` | IMU消息安装路径 | - |

### 时间段配置 (time_periods)

格式：`[[开始时间, 结束时间], ...]`
- 时间格式：HHMMSS（6位数字）
- 可配置多个时间段

### 车辆配置 (vehicle)

| 配置项 | 说明 |
|--------|------|
| `model` | 车辆型号（用于查找去畸变参数） |
| `logtime` | 日志时间戳（用于输出目录命名） |

### 处理参数 (processing)

| 配置项 | 说明 | 可选值 |
|--------|------|--------|
| `scale_min` | 图像缩放最小比例 | 0.0-1.0 |
| `lidar_format` | 点云导出格式 | pcd_binary, pcd_ascii, ply |
| `move_mode` | 是否使用移动模式 | true/false |

### 压缩配置 (compression)

| 配置项 | 说明 |
|--------|------|
| `min_zip_size_gb` | 最小压缩包大小（GB） |
| `required_free_space_gb` | 所需最小剩余空间（GB） |
| `required_json_files` | 必需的JSON文件列表 |
| `folder_groups` | 文件夹结构验证规则 |

## 迁移指南

### 迁移现有脚本

以 `pipline.py` 为例：

**修改前：**
```python
DEFAULT_VEHICLE = "vehicle_000"
DEFAULT_MAIN_OUT = "/media/zgw/T7/0209out/"
MOVE_RECORD_DIR = "/media/zgw/T7/0209out/"
```

**修改后：**
```python
from config_loader import config

# 使用配置文件中的值作为默认值
DEFAULT_VEHICLE = config.vehicle_model
DEFAULT_MAIN_OUT = config.main_output_dir
MOVE_RECORD_DIR = config.move_record_dir
```

### 命令行参数优先级

配置文件提供默认值，命令行参数可以覆盖：

```python
import argparse
from config_loader import config

parser = argparse.ArgumentParser()
parser.add_argument("--vehicle", default=config.vehicle_model)
parser.add_argument("--out", default=config.main_output_dir)
args = parser.parse_args()

# args.vehicle 和 args.out 会使用配置文件的默认值
# 用户可以通过命令行参数覆盖
```

## 最佳实践

1. **配置文件版本控制**
   - 提交 `config.yaml.example` 作为模板
   - 将 `config.yaml` 加入 `.gitignore`（包含敏感路径）

2. **环境变量支持**
   ```yaml
   paths:
     source_bag_dir: "${BAG_DIR}/original_ros2bag/"
     main_output_dir: "~/output/"
   ```

3. **配置验证**
   ```python
   from config_loader import config
   import os

   # 验证路径存在
   if not os.path.exists(config.source_bag_dir):
       raise FileNotFoundError(f"源目录不存在: {config.source_bag_dir}")
   ```

4. **多环境配置**
   ```bash
   # 开发环境
   python3 pipline.py --config config.dev.yaml

   # 生产环境
   python3 pipline.py --config config.prod.yaml
   ```

## 常见问题

### Q: 如何重新加载配置？
```python
from config_loader import config
config.reload()
```

### Q: 如何访问不存在的配置项？
```python
# 使用 get() 方法并提供默认值
value = config.get("non.existent.key", default="default_value")
```

### Q: 配置文件路径在哪里？
默认在脚本所在目录的 `config.yaml`，可以通过以下方式查看：
```python
from config_loader import config
print(config.config_path)
```

## 示例：完整的脚本改造

```python
#!/usr/bin/env python3
import argparse
from config_loader import config

def main():
    parser = argparse.ArgumentParser()

    # 使用配置文件的值作为默认值
    parser.add_argument("--bag", default=config.source_bag_dir)
    parser.add_argument("--out", default=config.main_output_dir)
    parser.add_argument("--vehicle", default=config.vehicle_model)
    parser.add_argument("--logtime", default=config.logtime)

    args = parser.parse_args()

    # 使用参数（优先使用命令行，否则使用配置文件）
    print(f"处理bag: {args.bag}")
    print(f"输出到: {args.out}")
    print(f"车辆型号: {args.vehicle}")

if __name__ == "__main__":
    main()
```

## 总结

通过统一配置管理，你只需要：
1. 修改 `config.yaml` 中的路径和参数
2. 在脚本中导入 `from config_loader import config`
3. 使用 `config.xxx` 访问配置

不再需要在多个脚本中重复修改相同的路径！
