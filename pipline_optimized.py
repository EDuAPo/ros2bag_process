#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化的流水线脚本 - 批量处理模式
主要优化：
1. 将32个时间段分批处理（每批4-8个），减少bag文件重复打开
2. 相机导出使用批量模式，一次读取bag处理多个时间段
3. 其他步骤（激光雷达、IMU、去畸变）并行处理多个时间段
4. 更好的错误恢复机制
"""

import os
import sys
import subprocess
import yaml
import argparse
import shutil
from typing import List, Tuple
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

# 导入原有配置
try:
    from config_loader import config as global_config
    USE_UNIFIED_CONFIG = True
except ImportError:
    USE_UNIFIED_CONFIG = False
    global_config = None

# 脚本路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FILTER_SCRIPT = os.path.join(CURRENT_DIR, "move_file.py")
EXPORT_CAMERA_BATCH_SCRIPT = os.path.join(CURRENT_DIR, "export_camera_batch.py")
EXPORT_LIDAR_SCRIPT = os.path.join(CURRENT_DIR, "export_lidar.py")
EXPORT_IMU_SCRIPT = os.path.join(CURRENT_DIR, "export_imu", "export_imu.py")
UNDISTORTION_SCRIPT = os.path.join(CURRENT_DIR, "undistortion", "undistortion.py")
EXTRACT_SAMPLE_SCRIPT = os.path.join(CURRENT_DIR, "extract_sample_undistorted.py")
CHECK_COMPRESS_SCRIPT = os.path.join(CURRENT_DIR, "check_and_compress.py")

def load_time_periods(yaml_path: str) -> List[Tuple[str, str]]:
    """从YAML文件加载时间段列表"""
    if not os.path.exists(yaml_path):
        raise FileNotFoundError(f"未找到时间段配置文件：{yaml_path}")

    with open(yaml_path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    if not isinstance(data, list) or len(data) == 0:
        raise ValueError(f"YAML文件内容必须是非空列表")

    periods = []
    for idx, period in enumerate(data, 1):
        if not isinstance(period, list) or len(period) != 2:
            raise ValueError(f"YAML第{idx}行格式错误：必须是包含2个元素的列表")

        start = str(period[0]).zfill(6)
        end = str(period[1]).zfill(6)
        periods.append((start, end))

    return periods

def batch_time_periods(periods: List[Tuple[str, str]], batch_size: int) -> List[List[Tuple[str, str]]]:
    """将时间段分批"""
    batches = []
    for i in range(0, len(periods), batch_size):
        batches.append(periods[i:i+batch_size])
    return batches

def filter_bag_for_batch(source_bag: str, temp_filter_dir: str, batch_periods: List[Tuple[str, str]],
                         move_mode: bool) -> List[str]:
    """为一批时间段筛选bag文件"""
    filtered_dirs = []

    for start, end in batch_periods:
        output_dir = os.path.join(temp_filter_dir, f"{start}_{end}")

        cmd = [
            sys.executable, FILTER_SCRIPT,
            "--source", source_bag,
            "--output", temp_filter_dir,
            "--start", start,
            "--end", end
        ]

        if move_mode:
            cmd.append("--move")

        print(f"\n🔧 筛选时间段：{start} → {end}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"❌ 筛选失败：{start} → {end}")
            print(result.stderr)
            continue

        if os.path.exists(output_dir):
            filtered_dirs.append(output_dir)
            print(f"✅ 筛选完成：{output_dir}")

    return filtered_dirs

def export_cameras_batch(source_bag: str, batch_periods: List[Tuple[str, str]],
                        main_output_dir: str, logtime: str, max_concurrent: int = 3) -> bool:
    """批量导出相机数据"""
    print(f"\n📷 批量导出相机数据（{len(batch_periods)}个时间段）...")

    # 构建时间段参数：start1:end1:out1,start2:end2:out2
    time_ranges_str = []
    for start, end in batch_periods:
        output_dir = os.path.join(main_output_dir, f"20{logtime}_{start}_{end}")
        os.makedirs(output_dir, exist_ok=True)
        time_ranges_str.append(f"{start}:{end}:{output_dir}")

    cmd = [
        sys.executable, EXPORT_CAMERA_BATCH_SCRIPT,
        "--bag", source_bag,
        "--time-ranges", ",".join(time_ranges_str),
        "--max-concurrent-cameras", str(max_concurrent)
    ]

    result = subprocess.run(cmd, capture_output=False, text=True)
    return result.returncode == 0

def process_single_period_postprocessing(filtered_bag: str, output_dir: str,
                                        vehicle: str, logtime: str, scale_min: float) -> bool:
    """处理单个时间段的后续步骤（激光雷达、IMU、去畸变等）"""
    try:
        # 1. 导出激光雷达
        print(f"  📡 导出激光雷达...")
        cmd = [sys.executable, EXPORT_LIDAR_SCRIPT, "--bag", filtered_bag, "--out", output_dir]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            print(f"  ❌ 激光雷达导出失败")
            return False

        # 2. 导出IMU
        print(f"  🧭 导出IMU...")
        cmd = [sys.executable, EXPORT_IMU_SCRIPT, "--bag", filtered_bag, "--out", output_dir]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            print(f"  ⚠️  IMU导出失败（可能bag中无IMU数据）")

        # 3. 去畸变
        print(f"  🔧 图像去畸变...")
        undistorted_dir = os.path.join(output_dir, "undistorted")
        cmd = [
            sys.executable, UNDISTORTION_SCRIPT,
            "--input", output_dir,
            "--output", undistorted_dir,
            "--vehicle", vehicle,
            "--scale", str(scale_min)
        ]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            print(f"  ❌ 去畸变失败")
            return False

        # 4. 提取样本
        print(f"  📝 提取样本...")
        cmd = [sys.executable, EXTRACT_SAMPLE_SCRIPT, "--dir", undistorted_dir]
        if subprocess.run(cmd, capture_output=True).returncode != 0:
            print(f"  ⚠️  样本提取失败")

        # 5. 压缩
        print(f"  🗜️  压缩打包...")
        cmd = [sys.executable, CHECK_COMPRESS_SCRIPT, "--dir", output_dir]
        subprocess.run(cmd, capture_output=True)

        print(f"  ✅ 完成")
        return True

    except Exception as e:
        print(f"  ❌ 处理失败：{e}")
        return False

def main():
    parser = argparse.ArgumentParser(description="优化的ROS2 Bag处理流水线")
    parser.add_argument("--logtime", required=True, help="日志时间戳")
    parser.add_argument("--batch-size", type=int, default=4, help="每批处理的时间段数量（默认4）")
    parser.add_argument("--max-concurrent-cameras", type=int, default=3, help="最大并发相机数（默认3）")
    parser.add_argument("--parallel-postprocess", type=int, default=2, help="后处理并行数（默认2）")
    parser.add_argument("--yaml-path", default="./time_peridos.yaml", help="时间段配置文件")
    parser.add_argument("--no-move", action="store_true", help="使用复制模式而非移动模式")

    args = parser.parse_args()

    # 加载配置
    if not USE_UNIFIED_CONFIG:
        print("❌ 错误：需要config.yaml配置文件")
        sys.exit(1)

    source_bag = global_config.source_bag_dir
    main_output_dir = global_config.main_output_dir
    temp_filter_dir = global_config.temp_filter_dir
    vehicle = global_config.vehicle_model
    scale_min = global_config.scale_min
    move_mode = not args.no_move

    # 加载时间段
    periods = load_time_periods(args.yaml_path)
    print(f"✅ 加载了 {len(periods)} 个时间段")

    # 分批处理
    batches = batch_time_periods(periods, args.batch_size)
    print(f"📦 分为 {len(batches)} 批处理，每批 {args.batch_size} 个时间段\n")

    os.makedirs(temp_filter_dir, exist_ok=True)
    os.makedirs(main_output_dir, exist_ok=True)

    total_success = 0
    total_failed = 0

    for batch_idx, batch in enumerate(batches, 1):
        print(f"\n{'='*80}")
        print(f"📦 处理第 {batch_idx}/{len(batches)} 批（{len(batch)} 个时间段）")
        print(f"{'='*80}\n")

        # 步骤1：筛选bag文件
        print(f"🔧 步骤1：筛选bag文件...")
        filtered_dirs = filter_bag_for_batch(source_bag, temp_filter_dir, batch, move_mode)

        if not filtered_dirs:
            print(f"❌ 批次 {batch_idx} 筛选失败，跳过")
            total_failed += len(batch)
            continue

        # 步骤2：批量导出相机（一次读取bag，处理所有时间段）
        print(f"\n📷 步骤2：批量导出相机数据...")
        camera_success = export_cameras_batch(
            source_bag, batch, main_output_dir, args.logtime, args.max_concurrent_cameras
        )

        if not camera_success:
            print(f"❌ 批次 {batch_idx} 相机导出失败")
            total_failed += len(batch)
            continue

        # 步骤3：并行处理后续步骤（激光雷达、IMU、去畸变等）
        print(f"\n⚙️  步骤3：并行处理后续步骤（{args.parallel_postprocess}个并发）...")

        with ThreadPoolExecutor(max_workers=args.parallel_postprocess) as executor:
            futures = {}
            for filtered_dir, (start, end) in zip(filtered_dirs, batch):
                output_dir = os.path.join(main_output_dir, f"20{args.logtime}_{start}_{end}")
                future = executor.submit(
                    process_single_period_postprocessing,
                    filtered_dir, output_dir, vehicle, args.logtime, scale_min
                )
                futures[future] = (start, end)

            for future in as_completed(futures):
                start, end = futures[future]
                try:
                    success = future.result()
                    if success:
                        print(f"✅ {start}→{end} 处理完成")
                        total_success += 1
                    else:
                        print(f"❌ {start}→{end} 处理失败")
                        total_failed += 1
                except Exception as e:
                    print(f"❌ {start}→{end} 异常：{e}")
                    total_failed += 1

        # 清理临时文件
        print(f"\n🧹 清理临时文件...")
        for filtered_dir in filtered_dirs:
            if os.path.exists(filtered_dir):
                shutil.rmtree(filtered_dir)

    # 总结
    print(f"\n{'='*80}")
    print(f"📊 处理完成")
    print(f"{'='*80}")
    print(f"✅ 成功：{total_success}/{len(periods)}")
    print(f"❌ 失败：{total_failed}/{len(periods)}")
    print(f"{'='*80}\n")

if __name__ == "__main__":
    main()
