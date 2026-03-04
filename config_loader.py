#!/usr/bin/env python3
"""
配置加载工具类
用于从 config.yaml 加载统一配置，避免在多个脚本中硬编码路径
"""

import os
import yaml
from typing import Dict, Any, List, Optional
from pathlib import Path


class ConfigLoader:
    """配置加载器，单例模式"""

    _instance = None
    _config = None

    def __new__(cls, config_path: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_path: Optional[str] = None):
        """
        初始化配置加载器

        Args:
            config_path: 配置文件路径，默认为当前目录下的 config.yaml
        """
        if self._config is not None:
            return

        if config_path is None:
            # 默认配置文件路径：脚本所在目录的 config.yaml
            script_dir = Path(__file__).parent
            config_path = script_dir / "config.yaml"

        self.config_path = Path(config_path)
        self._load_config()

    def _load_config(self):
        """从YAML文件加载配置"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

        with open(self.config_path, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)

        # 展开路径中的环境变量和 ~
        self._expand_paths()

    def _expand_paths(self):
        """展开配置中的路径（支持 ~ 和环境变量）"""
        if 'paths' in self._config:
            for key, value in self._config['paths'].items():
                if isinstance(value, str):
                    self._config['paths'][key] = os.path.expanduser(os.path.expandvars(value))

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        获取配置值，支持点号分隔的路径

        Args:
            key_path: 配置键路径，如 "paths.source_bag_dir"
            default: 默认值

        Returns:
            配置值

        Examples:
            >>> config = ConfigLoader()
            >>> config.get("paths.source_bag_dir")
            '/home/zgw/Desktop/NAS/original_ros2bag/...'
            >>> config.get("vehicle.model")
            'vehicle_000'
        """
        keys = key_path.split('.')
        value = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    # ========== 便捷访问方法 ==========

    @property
    def source_bag_dir(self) -> str:
        """原始bag文件目录"""
        return self.get("paths.source_bag_dir")

    @property
    def main_output_dir(self) -> str:
        """主输出目录"""
        return self.get("paths.main_output_dir")

    @property
    def temp_filter_dir(self) -> str:
        """临时筛选目录"""
        return self.get("paths.temp_filter_dir")

    @property
    def move_record_dir(self) -> str:
        """移动记录目录"""
        return self.get("paths.move_record_dir")

    @property
    def undistortion_params_dir(self) -> str:
        """去畸变参数目录"""
        return self.get("paths.undistortion_params_dir")

    @property
    def imu_msgs_install_path(self) -> str:
        """IMU消息安装路径"""
        return self.get("paths.imu_msgs_install_path")

    @property
    def time_periods(self) -> List[List[int]]:
        """时间段列表"""
        return self.get("time_periods", [])

    @property
    def vehicle_model(self) -> str:
        """车辆型号"""
        return self.get("vehicle.model", "vehicle_000")

    @property
    def logtime(self) -> str:
        """日志时间戳"""
        return self.get("vehicle.logtime", "")

    @property
    def scale_min(self) -> float:
        """图像缩放最小比例"""
        return self.get("processing.scale_min", 0.2)

    @property
    def lidar_format(self) -> str:
        """点云导出格式"""
        return self.get("processing.lidar_format", "pcd_binary")

    @property
    def move_mode(self) -> bool:
        """是否使用移动模式"""
        return self.get("processing.move_mode", False)

    @property
    def min_zip_size_gb(self) -> int:
        """最小压缩包大小（GB）"""
        return self.get("compression.min_zip_size_gb", 4)

    @property
    def required_free_space_gb(self) -> int:
        """所需最小剩余空间（GB）"""
        return self.get("compression.required_free_space_gb", 100)

    @property
    def required_json_files(self) -> List[str]:
        """必需的JSON文件列表"""
        return self.get("compression.required_json_files", [])

    @property
    def folder_groups(self) -> Dict[str, List[str]]:
        """文件夹结构验证规则"""
        return self.get("compression.folder_groups", {})

    @property
    def cameras(self) -> List[str]:
        """相机列表"""
        return self.get("sensors.cameras", [])

    @property
    def lidars(self) -> List[str]:
        """激光雷达列表"""
        return self.get("sensors.lidars", [])

    def reload(self):
        """重新加载配置文件"""
        self._config = None
        self._load_config()

    def __repr__(self) -> str:
        return f"ConfigLoader(config_path='{self.config_path}')"


# ========== 全局配置实例 ==========
# 其他脚本可以直接导入使用：from config_loader import config
config = ConfigLoader()


if __name__ == "__main__":
    # 测试配置加载
    print("=" * 60)
    print("配置文件测试")
    print("=" * 60)

    cfg = ConfigLoader()

    print(f"\n【路径配置】")
    print(f"源bag目录: {cfg.source_bag_dir}")
    print(f"主输出目录: {cfg.main_output_dir}")
    print(f"临时筛选目录: {cfg.temp_filter_dir}")
    print(f"移动记录目录: {cfg.move_record_dir}")

    print(f"\n【时间段配置】")
    for i, period in enumerate(cfg.time_periods, 1):
        print(f"时间段{i}: {period[0]} - {period[1]}")

    print(f"\n【车辆配置】")
    print(f"车辆型号: {cfg.vehicle_model}")
    print(f"日志时间: {cfg.logtime}")

    print(f"\n【处理参数】")
    print(f"缩放比例: {cfg.scale_min}")
    print(f"点云格式: {cfg.lidar_format}")
    print(f"移动模式: {cfg.move_mode}")

    print(f"\n【压缩配置】")
    print(f"最小压缩包大小: {cfg.min_zip_size_gb} GB")
    print(f"所需剩余空间: {cfg.required_free_space_gb} GB")
    print(f"必需JSON文件: {cfg.required_json_files}")

    print(f"\n【传感器配置】")
    print(f"相机数量: {len(cfg.cameras)}")
    print(f"激光雷达数量: {len(cfg.lidars)}")

    print("\n" + "=" * 60)
    print("配置加载成功！")
    print("=" * 60)
