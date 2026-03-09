#!/usr/bin/env python3
"""
配置迁移示例：展示如何将现有脚本迁移到使用统一配置
"""

import argparse
from config_loader import config

def example_pipline_migration():
    """示例：pipline.py 的迁移方式"""
    print("\n" + "=" * 60)
    print("示例1: pipline.py 迁移")
    print("=" * 60)

    print("\n【修改前】")
    print("""
DEFAULT_VEHICLE = "vehicle_000"
DEFAULT_MAIN_OUT = "/media/zgw/T7/0209out/"
MOVE_RECORD_DIR = "/media/zgw/T7/0209out/"
    """)

    print("【修改后】")
    print("""
from config_loader import config

DEFAULT_VEHICLE = config.vehicle_model
DEFAULT_MAIN_OUT = config.main_output_dir
MOVE_RECORD_DIR = config.move_record_dir
    """)

    print("【实际值】")
    print(f"DEFAULT_VEHICLE = '{config.vehicle_model}'")
    print(f"DEFAULT_MAIN_OUT = '{config.main_output_dir}'")
    print(f"MOVE_RECORD_DIR = '{config.move_record_dir}'")


def example_move_file_migration():
    """示例：move_file.py 的迁移方式"""
    print("\n" + "=" * 60)
    print("示例2: move_file.py 迁移")
    print("=" * 60)

    print("\n【修改前】")
    print("""
DEFAULT_SOURCE_DIRECTORY = "/home/zgw/Desktop/NAS/original_ros2bag/..."
DEFAULT_OUTPUT_ROOT_DIRECTORY = "/tmp/test_filter_out/"
DEFAULT_TARGET_START_TIME = "155143"
DEFAULT_TARGET_END_TIME = "155244"
    """)

    print("【修改后】")
    print("""
from config_loader import config

DEFAULT_SOURCE_DIRECTORY = config.source_bag_dir
DEFAULT_OUTPUT_ROOT_DIRECTORY = config.temp_filter_dir
# 时间从 config.time_periods[0] 获取
DEFAULT_TARGET_START_TIME = str(config.time_periods[0][0])
DEFAULT_TARGET_END_TIME = str(config.time_periods[0][1])
    """)

    print("【实际值】")
    print(f"DEFAULT_SOURCE_DIRECTORY = '{config.source_bag_dir}'")
    print(f"DEFAULT_OUTPUT_ROOT_DIRECTORY = '{config.temp_filter_dir}'")
    if config.time_periods:
        print(f"DEFAULT_TARGET_START_TIME = '{config.time_periods[0][0]}'")
        print(f"DEFAULT_TARGET_END_TIME = '{config.time_periods[0][1]}'")


def example_run_export_migration():
    """示例：run_export.py 的迁移方式"""
    print("\n" + "=" * 60)
    print("示例3: run_export.py 迁移")
    print("=" * 60)

    print("\n【修改前】")
    print("""
VEHICLE_MODEL = "vehicle_000"
SCALE_MIN = "0.2"
LOGTIME = "20260209"
UNDISTORTION_PARAMS_DIR = os.path.join(CURRENT_DIR, "undistortion", "intrinsic_param")
IMU_MSGS_INSTALL_PATH = os.path.join(CURRENT_DIR, "export_imu", "msg_interfaces", "install")
    """)

    print("【修改后】")
    print("""
from config_loader import config

VEHICLE_MODEL = config.vehicle_model
SCALE_MIN = str(config.scale_min)
LOGTIME = config.logtime
UNDISTORTION_PARAMS_DIR = config.undistortion_params_dir
IMU_MSGS_INSTALL_PATH = config.imu_msgs_install_path
    """)

    print("【实际值】")
    print(f"VEHICLE_MODEL = '{config.vehicle_model}'")
    print(f"SCALE_MIN = '{config.scale_min}'")
    print(f"LOGTIME = '{config.logtime}'")
    print(f"UNDISTORTION_PARAMS_DIR = '{config.undistortion_params_dir}'")
    print(f"IMU_MSGS_INSTALL_PATH = '{config.imu_msgs_install_path}'")


def example_check_and_compress_migration():
    """示例：check_and_compress.py 的迁移方式"""
    print("\n" + "=" * 60)
    print("示例4: check_and_compress.py 迁移")
    print("=" * 60)

    print("\n【修改前】")
    print("""
required_json_files = ['sensor_config_combined_latest.json', 'ins.json', 'sample.json']
min_zip_size_gb = 4
required_free_space_gb = 100
folder_groups = {
    'cameras': [...],
    'lidars': [...]
}
    """)

    print("【修改后】")
    print("""
from config_loader import config

required_json_files = config.required_json_files
min_zip_size_gb = config.min_zip_size_gb
required_free_space_gb = config.required_free_space_gb
folder_groups = config.folder_groups
    """)

    print("【实际值】")
    print(f"required_json_files = {config.required_json_files}")
    print(f"min_zip_size_gb = {config.min_zip_size_gb}")
    print(f"required_free_space_gb = {config.required_free_space_gb}")
    print(f"folder_groups keys = {list(config.folder_groups.keys())}")


def example_command_line_integration():
    """示例：与命令行参数集成"""
    print("\n" + "=" * 60)
    print("示例5: 命令行参数集成（保持向后兼容）")
    print("=" * 60)

    print("\n【代码示例】")
    print("""
import argparse
from config_loader import config

parser = argparse.ArgumentParser()
# 配置文件提供默认值，命令行参数可以覆盖
parser.add_argument("--bag", default=config.source_bag_dir)
parser.add_argument("--out", default=config.main_output_dir)
parser.add_argument("--vehicle", default=config.vehicle_model)
parser.add_argument("--logtime", default=config.logtime)

args = parser.parse_args()

# 用户可以通过命令行覆盖配置文件的值
# python script.py --vehicle vehicle_001 --logtime 20260304
    """)

    print("【默认值（来自配置文件）】")
    parser = argparse.ArgumentParser()
    parser.add_argument("--bag", default=config.source_bag_dir)
    parser.add_argument("--out", default=config.main_output_dir)
    parser.add_argument("--vehicle", default=config.vehicle_model)
    parser.add_argument("--logtime", default=config.logtime)

    # 模拟不带参数运行
    args = parser.parse_args([])
    print(f"--bag = '{args.bag}'")
    print(f"--out = '{args.out}'")
    print(f"--vehicle = '{args.vehicle}'")
    print(f"--logtime = '{args.logtime}'")


def main():
    print("\n" + "=" * 70)
    print(" " * 20 + "配置迁移示例演示")
    print("=" * 70)

    # 运行所有示例
    example_pipline_migration()
    example_move_file_migration()
    example_run_export_migration()
    example_check_and_compress_migration()
    example_command_line_integration()

    print("\n" + "=" * 70)
    print("✅ 迁移示例演示完成！")
    print("=" * 70)
    print("\n提示：")
    print("1. 配置文件路径: config.yaml")
    print("2. 使用方式: from config_loader import config")
    print("3. 详细文档: CONFIG_USAGE.md")
    print("4. 保持命令行参数支持，配置文件仅提供默认值")
    print()


if __name__ == "__main__":
    main()
