import os
import shutil
import re
import sys
import subprocess
import json
import argparse
import sqlite3
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from config_loader import config

# ------------------- 默认配置 -------------------
# 从统一配置文件加载默认值，可被命令行参数覆盖
DEFAULT_SOURCE_DIRECTORY = config.source_bag_dir
DEFAULT_OUTPUT_ROOT_DIRECTORY = config.temp_filter_dir
# 时间段从配置文件读取（如果存在）
if config.time_periods:
    DEFAULT_TARGET_START_TIME = str(config.time_periods[0][0])
    DEFAULT_TARGET_END_TIME = str(config.time_periods[0][1])
else:
    DEFAULT_TARGET_START_TIME = "134448"
    DEFAULT_TARGET_END_TIME = "134518"
# ----------------------------------------------

def copy_rosbag_files(source_dir: str, output_root_dir: str, start_time_str: str, end_time_str: str,
                      move_mode: bool = False, record_path: Optional[str] = None) -> Dict[str, str]:
    """
    转移指定时间段内的rosbag文件（db3），并通过ROS 2原生指令生成标准metadata.yaml。
    移动模式下，record_path 指定记录文件路径，每移动一个文件立即写入，确保崩溃可恢复。
    """
    # 验证输入参数
    _validate_inputs(source_dir, output_root_dir, start_time_str, end_time_str)

    # 解析用户输入时间
    user_hh_start, user_mm_start, user_ss_start = _parse_time_str(start_time_str)
    user_hh_end, user_mm_end, user_ss_end = _parse_time_str(end_time_str)

    # 1. 查找并解析所有符合格式的db3文件
    all_db3_files = _find_and_parse_db3_files(source_dir)
    if not all_db3_files:
        raise FileNotFoundError(f"源文件夹 {source_dir} 中未找到符合格式的db3文件")

    # 2. 匹配用户指定时间段的db3文件（时间范围交集）
    matching_db3_files = _match_db3_by_time(
        all_db3_files,
        user_hh_start, user_mm_start, user_ss_start,
        user_hh_end, user_mm_end, user_ss_end
    )
    if not matching_db3_files:
        print(f"  [WARN] 未找到与时间段 {start_time_str}-{end_time_str} 有交集的db3文件")
        return {}

    # 3. 创建输出文件夹并转移db3文件（移动模式下实时写入记录）
    output_dir = _create_output_dir(output_root_dir, start_time_str, end_time_str)
    moved_files = _transfer_db3_files(matching_db3_files, output_dir, move_mode, record_path)

    # 4. 调用ROS 2指令生成yaml（兼容版：重定向输出+格式清理）
    _generate_yaml_by_ros2_compatible(output_dir)

    operation = "移动" if move_mode else "复制"
    print(f"  筛选完成: {operation} {len(matching_db3_files)} 个db3文件 -> {output_dir}")

    return moved_files


def _validate_inputs(source_dir: str, output_root_dir: str, start_time: str, end_time: str):
    """验证输入参数的合法性"""
    if not os.path.isdir(source_dir):
        raise NotADirectoryError(f"源文件夹不存在：{source_dir}")
    if not os.path.isdir(output_root_dir):
        raise NotADirectoryError(f"输出根文件夹不存在：{output_root_dir}")
    if not (len(start_time) == 6 and start_time.isdigit()):
        raise ValueError("起始时间格式错误，必须是6位数字（HHMMSS）")
    if not (len(end_time) == 6 and end_time.isdigit()):
        raise ValueError("结束时间格式错误，必须是6位数字（HHMMSS）")


def _parse_time_str(time_str: str) -> Tuple[int, int, int]:
    """将HHMMSS格式字符串解析为（时，分，秒）"""
    return int(time_str[:2]), int(time_str[2:4]), int(time_str[4:6])


def _query_db3_time_range(db3_path: str):
    """从 sqlite3 messages 表读取该 db3 文件的精确时间范围（纳秒时间戳）。
    返回 (min_ns, max_ns)，失败返回 (None, None)。
    """
    try:
        with sqlite3.connect(db3_path) as conn:
            cur = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM messages")
            tmin, tmax = cur.fetchone()
        if tmin is None or tmax is None:
            return None, None
        return int(tmin), int(tmax)
    except Exception as e:
        print(f"  [WARN] 读取 {os.path.basename(db3_path)} 时间范围失败: {e}")
        return None, None


def _find_and_parse_db3_files(source_dir: str) -> List[Dict]:
    """查找源文件夹中所有符合格式的db3文件，通过查询 sqlite3 获取精确时间范围。"""
    db3_pattern = r"rosbag2_(\d{4}_\d{2}_\d{2})-(\d{2}_\d{2}_\d{2})_(\d+)\.db3"
    all_db3_files = []

    for root, _, files in os.walk(source_dir):
        for filename in files:
            if not filename.endswith('.db3'):
                continue

            match = re.match(db3_pattern, filename)
            if not match:
                continue

            path = os.path.join(root, filename)
            date_str = match.group(1)

            tmin_ns, tmax_ns = _query_db3_time_range(path)
            if tmin_ns is None:
                continue

            actual_start = datetime.fromtimestamp(tmin_ns / 1e9)
            actual_end   = datetime.fromtimestamp(tmax_ns / 1e9)

            all_db3_files.append({
                "filename":     filename,
                "date_str":     date_str,
                "actual_start": actual_start,
                "actual_end":   actual_end,
                "path":         path,
            })

    return sorted(all_db3_files, key=lambda x: x["actual_start"])


def _match_db3_by_time(
    db3_files: List[Dict],
    user_hh_start: int, user_mm_start: int, user_ss_start: int,
    user_hh_end: int, user_mm_end: int, user_ss_end: int
) -> List[Dict]:
    """根据用户指定的时间段匹配db3文件（时间范围有交集即匹配）。
    每个 db3 的精确起止时间已由 sqlite3 查询得到，直接做区间相交判断。
    """
    matching_files = []

    for db3 in db3_files:
        # 将 HHMMSS 时间构造为与该 db3 同日期的 datetime，便于比较
        base = db3["actual_start"].date()
        user_start = datetime(base.year, base.month, base.day,
                              user_hh_start, user_mm_start, user_ss_start)
        user_end   = datetime(base.year, base.month, base.day,
                              user_hh_end,   user_mm_end,   user_ss_end)

        # 区间相交：db3 开始 < 目标结束，且 db3 结束 > 目标开始
        if db3["actual_start"] < user_end and db3["actual_end"] > user_start:
            matching_files.append(db3)

    return matching_files


def _create_output_dir(output_root: str, start_time: str, end_time: str) -> str:
    """创建输出文件夹（命名为"开始时间-结束时间"）"""
    output_dir_name = f"{start_time}_{end_time}"
    output_dir = os.path.join(output_root, output_dir_name)
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _transfer_db3_files(db3_files: List[Dict], output_dir: str, move_mode: bool,
                        record_path: Optional[str] = None) -> Dict[str, str]:
    """
    转移db3文件到输出文件夹。
    移动模式下，每移动一个文件就立即追加写入记录文件，保证崩溃后可恢复。
    返回: {目标路径: 原始路径}
    """
    moved_files = {}
    operation = "移动" if move_mode else "复制"

    # 移动模式：预先写入空记录，确保记录文件存在
    if move_mode and record_path:
        os.makedirs(os.path.dirname(record_path), exist_ok=True)
        with open(record_path, 'w', encoding='utf-8') as f:
            json.dump({}, f)

    for db3 in db3_files:
        dest_path = os.path.join(output_dir, db3["filename"])

        if move_mode:
            shutil.move(db3["path"], dest_path)
            if not os.path.exists(dest_path):
                raise RuntimeError(f"移动文件失败：目标文件 {dest_path} 不存在")
            moved_files[dest_path] = db3["path"]

            # 每移动一个文件立即更新记录，防止中途崩溃导致无法恢复
            if record_path:
                with open(record_path, 'w', encoding='utf-8') as f:
                    json.dump(moved_files, f, indent=2, ensure_ascii=False)
        else:
            shutil.copy2(db3["path"], dest_path)

    time_range = f"{db3_files[0]['actual_start'].strftime('%H:%M:%S')}-{db3_files[-1]['actual_end'].strftime('%H:%M:%S')}"
    print(f"  {operation} {len(db3_files)} 个db3文件 ({time_range})")

    return moved_files


def _generate_yaml_by_ros2_compatible(output_dir: str):
    """
    兼容所有ROS 2版本的yaml生成方式
    """
    yaml_filename = "metadata.yaml"
    yaml_path = os.path.join(output_dir, yaml_filename)
    bag_folder_path = output_dir
    
    print(f"  生成 metadata.yaml...")
    try:
        result = subprocess.run(
            f"ros2 bag reindex {bag_folder_path} --storage sqlite3",
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True
        )
        
        # 验证文件是否生成
        if not os.path.exists(yaml_path) or os.path.getsize(yaml_path) == 0:
            raise RuntimeError(f"yaml文件生成失败，文件为空或不存在")
        
        # 清理yaml格式
        _clean_yaml_format(yaml_path)
    
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() or "未知错误"
        raise RuntimeError(f"ROS 2指令执行失败：{error_msg}")
    except Exception as e:
        raise RuntimeError(f"生成yaml时发生异常：{str(e)}")


def _clean_yaml_format(yaml_path: str):
    """清理yaml文件格式：去除开头非yaml内容"""
    with open(yaml_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # 找到yaml起始行
    start_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("rosbag2_bagfile_information:"):
            start_idx = i
            break
    
    cleaned_lines = lines[start_idx:]
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.writelines(cleaned_lines)
    
    with open(yaml_path, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
        if not first_line.startswith("rosbag2_bagfile_information:"):
            raise RuntimeError(f"yaml格式清理失败，文件开头不是标准结构：{first_line}")


def save_move_record(moved_files: Dict[str, str], record_path: str):
    """保存移动记录到JSON文件，并验证文件是否存在"""
    # 验证所有移动的文件都存在
    for dest_path, src_path in moved_files.items():
        if not os.path.exists(dest_path):
            print(f"  [WARN] 移动记录中的目标文件不存在: {dest_path}")

    try:
        with open(record_path, 'w', encoding='utf-8') as f:
            json.dump(moved_files, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"  [FAIL] 移动记录保存失败: {e}")
        raise


def load_move_record(record_path: str) -> Dict[str, str]:
    """从JSON文件加载移动记录"""
    with open(record_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def main():
    """主函数，支持命令行参数和默认配置"""
    parser = argparse.ArgumentParser(description="筛选并转移指定时间段的db3文件")
    parser.add_argument("--move", action="store_true", help="使用移动模式（默认：复制模式）")
    parser.add_argument("--source", type=str, default=DEFAULT_SOURCE_DIRECTORY, 
                       help=f"源目录路径（默认：{DEFAULT_SOURCE_DIRECTORY}）")
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT_ROOT_DIRECTORY, 
                       help=f"输出目录路径（默认：{DEFAULT_OUTPUT_ROOT_DIRECTORY}）")
    parser.add_argument("--start", type=str, default=DEFAULT_TARGET_START_TIME, 
                       help=f"开始时间（HHMMSS，默认：{DEFAULT_TARGET_START_TIME}）")
    parser.add_argument("--end", type=str, default=DEFAULT_TARGET_END_TIME, 
                       help=f"结束时间（HHMMSS，默认：{DEFAULT_TARGET_END_TIME}）")
    parser.add_argument("--save-record", type=str, help="保存移动记录的文件路径（仅在移动模式下有效）")
    
    args = parser.parse_args()
    
    # 执行转移操作
    try:
        moved_files = copy_rosbag_files(
            source_dir=args.source,
            output_root_dir=args.output,
            start_time_str=args.start,
            end_time_str=args.end,
            move_mode=args.move,
            record_path=args.save_record if args.move else None,
        )

        if args.move and moved_files and args.save_record:
            print(f"  移动记录已写入: {args.save_record}")
            
    except Exception as e:
        print(f"执行过程中出现错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()