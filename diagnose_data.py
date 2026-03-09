#!/usr/bin/env python3
"""
诊断脚本：检查原始数据是否真的异常
"""
import os
import sys
from pathlib import Path
from datetime import datetime
from collections import defaultdict

def parse_timestamp(filename):
    """从文件名提取时间戳"""
    try:
        base = os.path.splitext(filename)[0]
        if '_scale_0.20_undistorted' in base:
            base = base.replace('_scale_0.20_undistorted', '')
        # YYYYMMDD_HHMMSS_mmm
        return datetime.strptime(base, '%Y%m%d_%H%M%S_%f')
    except:
        return None

def analyze_directory(root_dir):
    """分析目录中的文件时间分布"""
    root = Path(root_dir)
    
    # 定义要检查的传感器
    sensors = {
        'camera_cam_3M_front': 'scale_0.20',
        'camera_cam_3M_left': 'scale_0.20',
        'camera_cam_3M_right': 'scale_0.20',
        'camera_cam_3M_rear': 'scale_0.20',
        'camera_cam_8M_wa_front': 'scale_0.20',
        'iv_points_front_mid': 'pcd_binary',
        'iv_points_front_left': 'pcd_binary',
        'iv_points_front_right': 'pcd_binary',
        'iv_points_rear_left': 'pcd_binary',
        'iv_points_rear_right': 'pcd_binary',
    }
    
    results = {}
    
    for sensor, subdir in sensors.items():
        sensor_path = root / sensor / subdir
        if not sensor_path.exists():
            results[sensor] = {'exists': False}
            continue
        
        files = []
        ext = '.jpg' if 'camera' in sensor else '.pcd'
        
        for f in sensor_path.iterdir():
            if f.name.endswith(ext):
                ts = parse_timestamp(f.name)
                if ts:
                    files.append((f.name, ts))
        
        files.sort(key=lambda x: x[1])
        
        results[sensor] = {
            'exists': True,
            'count': len(files),
            'first': files[0] if files else None,
            'last': files[-1] if files else None,
            'files': files
        }
    
    return results

def find_gaps(files, gap_threshold_seconds=5):
    """查找时间间隙"""
    gaps = []
    for i in range(len(files) - 1):
        _, ts1 = files[i]
        _, ts2 = files[i+1]
        diff = (ts2 - ts1).total_seconds()
        if diff > gap_threshold_seconds:
            gaps.append({
                'start': ts1,
                'end': ts2,
                'duration': diff,
                'start_file': files[i][0],
                'end_file': files[i+1][0]
            })
    return gaps

def main():
    if len(sys.argv) < 2:
        print("用法: python diagnose_data.py <undistorted_directory>")
        sys.exit(1)
    
    root_dir = sys.argv[1]
    
    print(f"🔍 分析目录: {root_dir}")
    print("=" * 80)
    
    results = analyze_directory(root_dir)
    
    # 打印基本统计
    print("\n📊 传感器文件统计:")
    print("-" * 80)
    for sensor, data in results.items():
        if not data['exists']:
            print(f"❌ {sensor}: 目录不存在")
        else:
            print(f"✅ {sensor}: {data['count']} 个文件")
            if data['first']:
                print(f"   首个文件: {data['first'][0]} ({data['first'][1].strftime('%H:%M:%S.%f')[:-3]})")
            if data['last']:
                print(f"   最后文件: {data['last'][0]} ({data['last'][1].strftime('%H:%M:%S.%f')[:-3]})")
    
    # 查找时间间隙
    print("\n🔍 检查时间间隙 (>5秒):")
    print("-" * 80)
    
    for sensor, data in results.items():
        if data['exists'] and data['count'] > 0:
            gaps = find_gaps(data['files'], gap_threshold_seconds=5)
            if gaps:
                print(f"\n⚠️  {sensor} 发现 {len(gaps)} 个时间间隙:")
                for gap in gaps:
                    print(f"   间隙: {gap['start'].strftime('%H:%M:%S')} -> {gap['end'].strftime('%H:%M:%S')} "
                          f"(持续 {gap['duration']:.1f}秒)")
                    print(f"      前: {gap['start_file']}")
                    print(f"      后: {gap['end_file']}")
    
    # 对比不同传感器的时间范围
    print("\n📅 时间范围对比:")
    print("-" * 80)
    
    time_ranges = {}
    for sensor, data in results.items():
        if data['exists'] and data['first'] and data['last']:
            time_ranges[sensor] = {
                'start': data['first'][1],
                'end': data['last'][1]
            }
    
    if time_ranges:
        # 找出最早和最晚的时间
        all_starts = [r['start'] for r in time_ranges.values()]
        all_ends = [r['end'] for r in time_ranges.values()]
        global_start = min(all_starts)
        global_end = max(all_ends)
        
        print(f"全局时间范围: {global_start.strftime('%H:%M:%S')} -> {global_end.strftime('%H:%M:%S')}")
        print()
        
        for sensor, range_data in time_ranges.items():
            start_diff = (range_data['start'] - global_start).total_seconds()
            end_diff = (global_end - range_data['end']).total_seconds()
            
            status = "✅"
            if start_diff > 1 or end_diff > 1:
                status = "⚠️ "
            
            print(f"{status} {sensor}:")
            print(f"   开始: {range_data['start'].strftime('%H:%M:%S.%f')[:-3]} "
                  f"({'晚' if start_diff > 0 else '早'} {abs(start_diff):.1f}秒)")
            print(f"   结束: {range_data['end'].strftime('%H:%M:%S.%f')[:-3]} "
                  f"({'早' if end_diff > 0 else '晚'} {abs(end_diff):.1f}秒)")

if __name__ == '__main__':
    main()
