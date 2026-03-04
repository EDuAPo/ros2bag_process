import os
import shutil
import re
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

def copy_rosbag_files(source_dir: str, output_root_dir: str, start_time_str: str, end_time_str: str):
    """
    复制指定时间段内的rosbag文件（db3），并通过ROS 2原生指令生成标准metadata.yaml
    
    核心流程（兼容所有ROS 2版本）：
    1. 匹配并复制目标db3文件到输出文件夹
    2. 调用 `ros2 bag info <bag文件夹> > metadata.yaml`（通过重定向生成yaml）
    3. 自动清理yaml格式（去除冗余文本，保留标准结构）
    """
    # 验证输入参数
    _validate_inputs(source_dir, output_root_dir, start_time_str, end_time_str)
    
    # # 验证ROS 2环境是否可用
    # if not _check_ros2_environment():
    #     raise EnvironmentError("未检测到ROS 2环境，请先source ROS 2工作空间（如：source /opt/ros/humble/setup.bash）")
    
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
        print(f"未找到与时间段 {start_time_str} - {end_time_str} 有交集的db3文件")
        return
    
    # 3. 创建输出文件夹并复制db3文件
    output_dir = _create_output_dir(output_root_dir, start_time_str, end_time_str)
    _copy_db3_files(matching_db3_files, output_dir)
    
    # 4. 调用ROS 2指令生成yaml（兼容版：重定向输出+格式清理）
    _generate_yaml_by_ros2_compatible(output_dir)
    
    print(f"\n操作完成！共复制 {len(matching_db3_files)} 个db3文件，并生成标准 metadata.yaml 到 {output_dir}")


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


def _find_and_parse_db3_files(source_dir: str) -> List[Dict]:
    """查找源文件夹中所有符合格式的db3文件，并解析基础信息"""
    db3_pattern = r"rosbag2_(\d{4}_\d{2}_\d{2})-(\d{2}_\d{2}_\d{2})_(\d+)\.db3"
    all_db3_files = []
    
    for filename in os.listdir(source_dir):
        match = re.match(db3_pattern, filename)
        if match:
            try:
                date_str = match.group(1)
                base_time_str = match.group(2)
                seq_num = int(match.group(3))
                
                # 计算实际开始时间
                base_hh, base_mm, base_ss = _parse_time_str(base_time_str.replace("_", ""))
                base_datetime = datetime.strptime(
                    f"{date_str} {base_hh:02d}:{base_mm:02d}:{base_ss:02d}",
                    "%Y_%m_%d %H:%M:%S"
                )
                actual_start = base_datetime + timedelta(minutes=seq_num)
                
                all_db3_files.append({
                    "filename": filename,
                    "date_str": date_str,
                    "seq_num": seq_num,
                    "actual_start": actual_start,
                    "path": os.path.join(source_dir, filename)
                })
            except Exception as e:
                print(f"警告：跳过格式异常的文件 {filename}，错误：{str(e)}")
                continue
    
    # 按实际开始时间排序
    return sorted(all_db3_files, key=lambda x: x["actual_start"])


def _match_db3_by_time(
    db3_files: List[Dict],
    user_hh_start: int, user_mm_start: int, user_ss_start: int,
    user_hh_end: int, user_mm_end: int, user_ss_end: int
) -> List[Dict]:
    """根据用户指定的时间段匹配db3文件（时间范围有交集即匹配）"""
    matching_files = []
    target_date = None
    
    for db3 in db3_files:
        # 构造用户时间段（与当前db3同日期）
        user_start = datetime(
            year=db3["actual_start"].year,
            month=db3["actual_start"].month,
            day=db3["actual_start"].day,
            hour=user_hh_start,
            minute=user_mm_start,
            second=user_ss_start
        )
        user_end = datetime(
            year=db3["actual_start"].year,
            month=db3["actual_start"].month,
            day=db3["actual_start"].day,
            hour=user_hh_end,
            minute=user_mm_end,
            second=user_ss_end
        )
        
        # 只匹配同一日期的文件
        if target_date is None:
            target_date = db3["date_str"]
        elif db3["date_str"] != target_date:
            continue
        
        # 计算db3的结束时间（正常1分钟，或下一包开始时间）
        db3_end = db3["actual_start"] + timedelta(minutes=1)
        next_idx = db3_files.index(db3) + 1
        if next_idx < len(db3_files) and db3_files[next_idx]["date_str"] == target_date:
            next_start = db3_files[next_idx]["actual_start"]
            if next_start < db3_end:
                db3_end = next_start
        
        # 时间范围交集判断
        if db3["actual_start"] < user_end and db3_end > user_start:
            matching_files.append(db3)
    
    return list({db["path"]: db for db in matching_files}.values())  # 去重


def _create_output_dir(output_root: str, start_time: str, end_time: str) -> str:
    """创建输出文件夹（命名为"开始时间-结束时间"）"""
    output_dir_name = f"{start_time}_{end_time}"
    output_dir = os.path.join(output_root, output_dir_name)
    os.makedirs(output_dir, exist_ok=True)
    print(f"输出文件夹已创建：{output_dir}")
    return output_dir


def _copy_db3_files(db3_files: List[Dict], output_dir: str):
    """复制db3文件到输出文件夹"""
    print("\n匹配到的db3文件：")
    for db3 in db3_files:
        dest_path = os.path.join(output_dir, db3["filename"])
        shutil.copy2(db3["path"], dest_path)
        print(f"  - 已复制：{db3['filename']}（开始时间：{db3['actual_start'].strftime('%H:%M:%S')}）")


def _generate_yaml_by_ros2_compatible(output_dir: str):
    """
    兼容所有ROS 2版本的yaml生成方式：
    1. 执行 `ros2 bag info <bag文件夹>` 获取元数据（默认输出yaml格式）
    2. 通过重定向将输出写入metadata.yaml
    3. 清理冗余文本（部分ROS 2版本会在开头输出非yaml文本）
    """
    yaml_filename = "metadata.yaml"
    yaml_path = os.path.join(output_dir, yaml_filename)
    bag_folder_path = output_dir  # bag文件夹=输出文件夹（含db3文件）
    
    print(f"\n正在通过ROS 2生成 {yaml_filename}...")
    try:
        # 核心指令：ros2 bag info 输出默认是yaml格式，直接重定向到文件
        # 使用shell=True支持重定向符号 ">"
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
        
        # 清理yaml格式（去除开头可能的非yaml文本，如警告、提示信息）
        _clean_yaml_format(yaml_path)
        
        print(f"成功生成标准metadata.yaml：{yaml_path}")
    
    except subprocess.CalledProcessError as e:
        # 若指令失败，尝试直接读取stderr（部分错误会输出到stderr）
        error_msg = e.stderr.strip() or "未知错误"
        raise RuntimeError(f"ROS 2指令执行失败：{error_msg}\n请检查：1. 输出文件夹是否有db3文件 2. ROS 2环境是否正常 3. 文件夹路径无空格/特殊字符")
    except Exception as e:
        raise RuntimeError(f"生成yaml时发生异常：{str(e)}")


def _clean_yaml_format(yaml_path: str):
    """清理yaml文件格式：去除开头非yaml内容，确保以rosbag2_bagfile_information开头"""
    with open(yaml_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    # 找到yaml起始行（以rosbag2_bagfile_information开头）
    start_idx = 0
    for i, line in enumerate(lines):
        if line.strip().startswith("rosbag2_bagfile_information:"):
            start_idx = i
            break
    
    # 保留从起始行开始的内容，覆盖原文件
    cleaned_lines = lines[start_idx:]
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.writelines(cleaned_lines)
    
    # 验证清理后的格式
    with open(yaml_path, "r", encoding="utf-8") as f:
        first_line = f.readline().strip()
        if not first_line.startswith("rosbag2_bagfile_information:"):
            raise RuntimeError(f"yaml格式清理失败，文件开头不是标准结构：{first_line}")


# ------------------- 配置参数（请根据Linux系统路径修改）-------------------
if __name__ == "__main__":
    # 源文件夹路径（Linux格式，例如 "/home/your_username/rosbag_data"）
    SOURCE_DIRECTORY = "/media/xl/MyPass/zgw1201/140356_140541/"  # <-- 请修改为你的源文件夹路径
    
    # 输出根文件夹路径（Linux格式，例如 "/home/your_username/selected_rosbags"）
    OUTPUT_ROOT_DIRECTORY = "/media/xl/5fed7169-56a0-4d89-8970-3db49acc85dc/1203test/out1"  # <-- 请修改为你的输出根文件夹路径
    
    # 目标时间段（精确到秒，格式：HHMMSS）
    TARGET_START_TIME = "140356"  # 自动更新于 2025-12-04 18:34:21
    TARGET_END_TIME = "140400"    # 自动更新于 2025-12-04 18:34:21
    
    # 执行复制操作
    try:
        copy_rosbag_files(
            source_dir=SOURCE_DIRECTORY,
            output_root_dir=OUTPUT_ROOT_DIRECTORY,
            start_time_str=TARGET_START_TIME,
            end_time_str=TARGET_END_TIME
        )
    except Exception as e:
        print(f"执行过程中出现错误：{str(e)}")
