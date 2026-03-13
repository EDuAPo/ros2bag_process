#!/usr/bin/env python3

import os
import json
import shutil
import argparse
from datetime import datetime, timedelta

# --- 配置参数 ---
OUTPUT_DIR_NAME = 'samples'
BASE_LASER_TOPIC = 'iv_points_front_mid'
TIME_INTERVAL_MS = 480  # 关键帧间隔：500毫秒
MAX_TIME_DIFF_MS = 600  # 同一关键帧内各传感器之间的最大时间差（毫秒）

# 所有数据目录
ALL_DIRS = {
    'camera_cam_3M_front', 'camera_cam_3M_left', 'camera_cam_3M_rear',
    'camera_cam_3M_right', 'camera_cam_8M_pt_front', 'camera_cam_8M_wa_front',
    'iv_points_front_left', 'iv_points_front_mid', 'iv_points_front_right',
    'iv_points_rear_left', 'iv_points_rear_right',
    'iv_points_left_mid', 'iv_points_right_mid'
}

# 图像目录
IMAGE_TOPICS = [d for d in ALL_DIRS if d.startswith('camera_cam')]
# 激光雷达目录
LIDAR_TOPICS = [d for d in ALL_DIRS if d.startswith('iv_points')]
# 所有需要处理的 Topic
ALL_TOPICS_TO_PROCESS = IMAGE_TOPICS + LIDAR_TOPICS

# 图像数据实际所在的子目录名
IMAGE_SUBDIR = 'scale_0.20' 
# 激光雷达数据实际所在的子目录名
LIDAR_SUBDIR = 'pcd_binary'
INS_FILE_NAME = 'ins.json'


# --- 工具函数 ---

def parse_filename_timestamp(filename):
    """
    从文件名解析出 datetime 对象。
    支持格式: 
    1. '20251104_155912_894.pcd' (激光雷达)
    2. '20251104_155913_034_scale_0.20_undistorted.jpg' (去畸变图像)
    """
    try:
        base_name = os.path.splitext(filename)[0]
        
        # 1. 处理去畸变图像文件名: 移除 '_scale_0.20_undistorted' 后缀
        if base_name.endswith('_scale_0.20_undistorted'):
            # 提取时间戳部分: '20251104_155913_034'
            timestamp_str = base_name.rsplit('_scale_0.20_undistorted', 1)[0]
        else:
            # 2. 处理标准时间戳文件名 (如激光雷达文件)
            timestamp_str = base_name

        # 格式: YYYYMMDD_HHMMSS_mmm
        dt_object = datetime.strptime(timestamp_str, '%Y%m%d_%H%M%S_%f')
        return dt_object
    except ValueError:
        print(f"Warning: Could not parse timestamp from filename: {filename}")
        return None

def find_nearest_file_in_time_window(target_dt, file_list_with_dt, time_window_ms):
    """
    在时间窗口内寻找最接近的文件。
    返回 (文件名, datetime_object, 时间差毫秒数) 或 (None, None, None)
    """
    if not file_list_with_dt:
        return None, None, None
    
    time_window = timedelta(milliseconds=time_window_ms)
    half_window = timedelta(milliseconds=time_window_ms/2)
    
    # 首先寻找时间窗口内的文件
    candidates = []
    for filename, dt in file_list_with_dt:
        time_diff = abs((dt - target_dt).total_seconds() * 1000)  # 转换为毫秒
        if time_diff <= time_window_ms/2:  # 在时间窗口内
            candidates.append((filename, dt, time_diff))
    
    if candidates:
        # 选择时间窗口内最接近的文件
        candidates.sort(key=lambda x: x[2])  # 按时间差排序
        return candidates[0]
    else:
        # 如果没有在时间窗口内的文件，返回最接近的一个
        min_diff = float('inf')
        nearest = None
        nearest_dt = None
        
        for filename, dt in file_list_with_dt:
            time_diff = abs((dt - target_dt).total_seconds() * 1000)
            if time_diff < min_diff:
                min_diff = time_diff
                nearest = filename
                nearest_dt = dt
        
        return nearest, nearest_dt, min_diff

def find_consistent_frame_files(base_dt, all_topic_files_dt, max_time_diff_ms):
    """
    寻找与基准时间一致的所有传感器文件。
    返回字典: {topic: (filename, timestamp, time_diff_ms)} 和 平均时间戳
    """
    result = {}
    time_diffs = []
    valid_topics = []
    
    # 首先确保基准激光雷达有文件
    base_topic = BASE_LASER_TOPIC
    if base_topic in all_topic_files_dt:
        base_files = all_topic_files_dt[base_topic]
        base_match, base_match_dt, base_diff = find_nearest_file_in_time_window(
            base_dt, [(fn, dt) for fn, dt, _ in base_files], max_time_diff_ms
        )
        
        if base_match:
            result[base_topic] = (base_match, base_match_dt, base_diff)
            time_diffs.append(base_diff)
            valid_topics.append(base_topic)
        else:
            return None, None
    
    # 获取实际匹配到的基准时间
    actual_base_dt = result[base_topic][1] if base_topic in result else base_dt
    
    # 对其他传感器，基于实际匹配到的基准时间进行匹配
    for topic in ALL_TOPICS_TO_PROCESS:
        if topic == base_topic:
            continue
            
        if topic in all_topic_files_dt:
            topic_files = all_topic_files_dt[topic]
            match, match_dt, time_diff = find_nearest_file_in_time_window(
                actual_base_dt, [(fn, dt) for fn, dt, _ in topic_files], max_time_diff_ms
            )
            
            if match and time_diff <= max_time_diff_ms:
                result[topic] = (match, match_dt, time_diff)
                time_diffs.append(time_diff)
                valid_topics.append(topic)
    
    # 计算平均时间戳（使用有效文件的时间戳）
    if valid_topics:
        timestamps = [result[topic][1] for topic in valid_topics]
        avg_timestamp = timestamps[0]  # 简单起见，使用第一个有效时间戳
        
        # 或者可以计算平均时间（更复杂但更精确）
        # total_seconds = sum(ts.timestamp() for ts in timestamps)
        # avg_timestamp = datetime.fromtimestamp(total_seconds / len(timestamps))
        
        return result, avg_timestamp
    
    return None, None

def find_nearest_ins_record(target_dt, all_ins_data, max_time_diff_ms):
    """
    寻找最接近的INS记录。
    返回 (timestamp_str, datetime_object, 时间差毫秒数)
    """
    if not all_ins_data:
        return None, None, None
    
    min_diff = float('inf')
    nearest_ts = None
    nearest_dt = None
    
    for ts_str, dt_obj in all_ins_data.items():
        time_diff = abs((dt_obj - target_dt).total_seconds() * 1000)
        if time_diff < min_diff:
            min_diff = time_diff
            nearest_ts = ts_str
            nearest_dt = dt_obj
    
    if nearest_ts and min_diff <= max_time_diff_ms:
        return nearest_ts, nearest_dt, min_diff
    
    return None, None, None


# --- 主要流程 ---

def generate_key_frames(root_dir, copy_sample, max_time_diff_ms):
    """
    根据需求生成关键帧，拷贝文件并生成 JSON 记录。
    """
    ROOT_DIR = root_dir
    OUTPUT_DIR = os.path.join(ROOT_DIR, OUTPUT_DIR_NAME)
    INS_FILE = os.path.join(ROOT_DIR, INS_FILE_NAME)
    JSON_OUTPUT_FILE = os.path.join(ROOT_DIR, 'sample.json')
    
    print(f"🚀 开始处理，根目录: {ROOT_DIR}")
    print(f"📏 时间一致性阈值: {max_time_diff_ms}ms")

    # --- 1. 准备输出目录结构 ---
    if copy_sample:
        if os.path.exists(OUTPUT_DIR):
            shutil.rmtree(OUTPUT_DIR)
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        for topic in ALL_TOPICS_TO_PROCESS:
            os.makedirs(os.path.join(OUTPUT_DIR, topic), exist_ok=True)
        print(f"✅ 创建输出目录: {OUTPUT_DIR} 及其所有 Topic 子目录。")
    else:
        print("ℹ️ 跳过创建/清理 samples 目录和子目录 (--copy_sample 为 False)。")

    # --- 2. 选出基准激光雷达关键帧时间戳 ---
    base_lidar_path = os.path.join(ROOT_DIR, BASE_LASER_TOPIC, LIDAR_SUBDIR)
    if not os.path.isdir(base_lidar_path):
        print(f"❌ 错误: 基准激光雷达数据目录不存在: {base_lidar_path}")
        return

    all_base_lidar_files_with_dt = []
    for filename in os.listdir(base_lidar_path):
        if filename.endswith('.pcd'):
            dt = parse_filename_timestamp(filename)
            if dt:
                all_base_lidar_files_with_dt.append((filename, dt))

    if not all_base_lidar_files_with_dt:
        print("❌ 错误: 未找到任何基准激光雷达文件。")
        return

    all_base_lidar_files_with_dt.sort(key=lambda x: x[1])
    
    # 选择关键帧（保持原有逻辑）
    key_frames_dt = []
    first_frame = all_base_lidar_files_with_dt[0]
    key_frames_dt.append(first_frame[1])
    last_key_frame_dt = first_frame[1]
    time_delta = timedelta(milliseconds=TIME_INTERVAL_MS)

    for _, dt in all_base_lidar_files_with_dt[1:]:
        if dt >= last_key_frame_dt + time_delta:
            key_frames_dt.append(dt)
            last_key_frame_dt = dt
    
    print(f"✅ 初步选出 {len(key_frames_dt)} 个基准关键帧时间戳 (间隔 {TIME_INTERVAL_MS}ms)")

    # --- 3. 准备所有待匹配 Topic 的文件时间戳列表 ---
    all_topic_files_dt = {}
    
    for topic in ALL_TOPICS_TO_PROCESS:
        is_lidar = topic in LIDAR_TOPICS
        
        if is_lidar:
            topic_path = os.path.join(ROOT_DIR, topic, LIDAR_SUBDIR)
        else:
            topic_path = os.path.join(ROOT_DIR, topic, IMAGE_SUBDIR)
        
        all_topic_files_dt[topic] = []
        if os.path.isdir(topic_path):
            file_extension = '.pcd' if is_lidar else '.jpg'
            for filename in os.listdir(topic_path):
                if filename.endswith(file_extension):
                    dt = parse_filename_timestamp(filename) 
                    if dt:
                        all_topic_files_dt[topic].append((filename, dt, topic_path))
        else:
            print(f"Warning: Topic 目录 {topic_path} 不存在，跳过。")

    # --- 4. 准备 INS 数据 ---
    all_ins_data = {}
    try:
        with open(INS_FILE, 'r', encoding='utf-8') as f:
            ins_records = json.load(f)

        if not isinstance(ins_records, list):
            ins_records = [ins_records]
            
        print(f"✅ 成功加载 {len(ins_records)} 条 INS 记录。")

        for ins_record in ins_records:
            timestamp_desc = ins_record.get('timestamp_desc')
            if timestamp_desc:
                try:
                    ins_dt = datetime.strptime(timestamp_desc, '%Y%m%d_%H%M%S_%f')
                    all_ins_data[timestamp_desc] = ins_dt
                except ValueError:
                    print(f"Warning: INS时间戳格式错误: {timestamp_desc}")

    except FileNotFoundError:
        print(f"❌ 错误: INS 文件不存在: {INS_FILE}")
        return 
    except json.JSONDecodeError as e:
        print(f"❌ 错误: INS 文件格式错误 (JSONDecodeError): {e}")
        return 

    if not all_ins_data:
        print("❌ 错误: INS 文件中未找到有效的 'timestamp_desc' 字段。")
        return

    # --- 5. 进行一致性匹配、拷贝和记录 ---
    sample_records = []
    frame_id = 0
    skipped_frames = 0
    
    for lidar_dt in key_frames_dt:
        # 寻找时间一致的所有传感器文件
        consistent_files, avg_timestamp = find_consistent_frame_files(
            lidar_dt, all_topic_files_dt, max_time_diff_ms
        )
        
        if not consistent_files:
            print(f"Warning: 无法为关键帧时间戳 {lidar_dt} 找到时间一致的传感器文件，跳过该帧。")
            skipped_frames += 1
            continue
        
        # 检查除了camera_cam_8M_pt_front外，其他实际存在的topic是否都有文件
        has_missing_required_topic = False
        for topic in ALL_TOPICS_TO_PROCESS:
            # 跳过不存在的topic目录和camera_cam_8M_pt_front
            if topic == 'camera_cam_8M_pt_front':
                continue
            # 只检查实际存在目录的topic
            if topic in all_topic_files_dt and len(all_topic_files_dt[topic]) > 0:
                if topic not in consistent_files:
                    print(f"Warning: 关键帧时间戳 {lidar_dt} 缺少必要的topic '{topic}'，跳过该帧。")
                    has_missing_required_topic = True
                    break
        
        if has_missing_required_topic:
            skipped_frames += 1
            continue
        
        record = {}
        record['id'] = frame_id
        record['frame_timestamp'] = avg_timestamp.strftime('%Y%m%d_%H%M%S_%f')[:-3]  # 保留毫秒
        
        # 记录各传感器文件
        all_found = True
        time_diff_sum = 0
        time_diff_count = 0
        
        for topic in ALL_TOPICS_TO_PROCESS:
            if topic in consistent_files:
                filename, file_dt, time_diff = consistent_files[topic]
                record[topic] = filename
                
                # 统计时间差
                time_diff_sum += time_diff
                time_diff_count += 1
                
                # 拷贝文件
                if copy_sample:
                    # 查找原始路径
                    src_info = next((item for item in all_topic_files_dt[topic] 
                                    if item[0] == filename), None)
                    if src_info:
                        _, _, src_topic_path = src_info
                        src_path = os.path.join(src_topic_path, filename)
                        dest_path = os.path.join(OUTPUT_DIR, topic, filename)
                        
                        if os.path.exists(src_path):
                            shutil.copy2(src_path, dest_path)
                        else:
                            print(f"Warning: 源文件不存在，无法拷贝: {src_path}")
            else:
                record[topic] = "NOT_FOUND"
        
        # 匹配 INS 记录
        if all_ins_data:
            ins_ts, ins_dt, ins_time_diff = find_nearest_ins_record(
                avg_timestamp, all_ins_data, max_time_diff_ms
            )
            
            if ins_ts:
                record['ins'] = ins_ts
                record['ins_time_diff_ms'] = round(ins_time_diff, 1)
            else:
                record['ins'] = "NOT_FOUND"
                record['ins_time_diff_ms'] = None
        else:
            record['ins'] = "NOT_FOUND"
            record['ins_time_diff_ms'] = None
        
        # 添加统计信息
        if time_diff_count > 0:
            record['avg_time_diff_ms'] = round(time_diff_sum / time_diff_count, 1)
            record['max_allowed_diff_ms'] = max_time_diff_ms
        
        sample_records.append(record)
        frame_id += 1
        
        # # 打印进度
        # if frame_id % 10 == 0:
        #     print(f"✅ 已处理 {frame_id} 个关键帧...")

    # --- 6. 生成 JSON 文件 ---
    with open(JSON_OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(sample_records, f, ensure_ascii=False, indent=4)
    
    print(f"\n🎉 关键帧选择和记录完成！")
    print(f"📊 统计信息:")
    print(f"   - 初始关键帧数量: {len(key_frames_dt)}")
    print(f"   - 有效关键帧数量: {len(sample_records)}")
    print(f"   - 跳过的关键帧: {skipped_frames}")
    
    if sample_records:
        avg_diffs = [r.get('avg_time_diff_ms', 0) for r in sample_records if 'avg_time_diff_ms' in r]
        if avg_diffs:
            print(f"   - 平均时间差: {sum(avg_diffs)/len(avg_diffs):.1f}ms")
    
    if copy_sample:
        print(f"📂 所有关键帧文件已拷贝到: {OUTPUT_DIR} 下的各自 Topic 目录中。")
    else:
        print(f"📂 已跳过文件拷贝操作。")
    print(f"📝 关键帧记录文件已生成: {JSON_OUTPUT_FILE}")


# --- 命令行参数处理 ---
def main():
    parser = argparse.ArgumentParser(
        description="基于激光雷达时间戳，从多个传感器Topic中提取关键帧并生成记录文件。",
        formatter_class=argparse.RawTextHelpFormatter
    )
    
    parser.add_argument(
        'root_dir',
        type=str,
        help=(
            "数据集的根目录路径，包含所有 sensor_datas 的 Topic 目录和 ins.json 文件。\n"
            "例如: /home/shucdong/workspace/dataset/test/sensor_datas"
        )
    )
    
    parser.add_argument(
        '--copy_sample',
        action='store_true',
        default=False,
        help=(
            "是否将关键帧文件拷贝到 samples 目录下。\n"
            "如果设置此参数 (例如: --copy_sample)，则执行拷贝操作；\n"
            "如果省略此参数 (默认)，则只生成 sample.json 文件。"
        )
    )
    
    # 可选参数：可以调整时间差阈值
    parser.add_argument(
        '--max_time_diff',
        type=int,
        default=MAX_TIME_DIFF_MS,
        help=f"同一关键帧内各传感器之间的最大时间差（毫秒），默认: {MAX_TIME_DIFF_MS}ms"
    )

    args = parser.parse_args()
    
    # 使用命令行参数的时间差阈值，如果没有指定则使用默认值
    max_time_diff_ms = args.max_time_diff
    
    if max_time_diff_ms != MAX_TIME_DIFF_MS:
        print(f"⚠️ 使用自定义时间差阈值: {max_time_diff_ms}ms")
    
    root_dir = args.root_dir.rstrip(os.sep)
    generate_key_frames(root_dir, args.copy_sample, max_time_diff_ms)

if __name__ == '__main__':
    main()
