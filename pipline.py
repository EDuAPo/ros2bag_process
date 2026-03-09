import os
import sys
import subprocess
import yaml
import re
import shutil
import json
import tempfile
import time
import signal
import atexit
import gc
from datetime import datetime
from typing import List, Tuple, Optional, Dict, Set

# 尝试导入统一配置
try:
    from config_loader import config as global_config
    USE_UNIFIED_CONFIG = True
except ImportError:
    USE_UNIFIED_CONFIG = False
    global_config = None

# ===================== 全局状态管理 =====================
# 用于跟踪所有移动记录文件，确保异常退出时能恢复
ACTIVE_MOVE_RECORDS = []  # 存储所有活跃的移动记录文件路径
CLEANUP_REGISTERED = False  # 标记是否已注册清理函数

# ===================== 配置区域 =====================
# 1. 核心脚本路径
FILTER_SCRIPT_PATH = "./move_file.py"  # 筛选脚本
RUN_EXPORT_SCRIPT_PATH = "./run_export.py"  # 预处理脚本
CHECK_COMPRESS_SCRIPT_PATH = "./check_and_compress.py"  # 检查压缩脚本

# 2. 基础配置（优先使用统一配置文件）
if USE_UNIFIED_CONFIG:
    DEFAULT_VEHICLE = global_config.vehicle_model
    DEFAULT_MAIN_OUT = global_config.main_output_dir
    TIME_PERIODS_YAML = "./time_peridos.yaml"
    MOVE_RECORD_DIR = global_config.move_record_dir
else:
    DEFAULT_VEHICLE = "vehicle_000"
    DEFAULT_MAIN_OUT = "/media/zgw/T7/0209out/"
    TIME_PERIODS_YAML = "./time_peridos.yaml"
    MOVE_RECORD_DIR = "/media/zgw/T7/0209out/"

# 3. 新增：移动模式配置
MOVE_MODE = True  # 是否使用移动模式（默认True，最节省空间）

# 4. 新增：检查压缩功能配置
SKIP_CHECK_COMPRESS = False  # 是否跳过压缩流程（默认不跳过）
COMPRESS_FORMAT = "zip"  # 压缩格式
DELETE_RAW_UNDISTORTED = True  # 压缩后是否删除原始 undistorted 目录（建议启用以节省空间）
DELETE_PREPROCESS_DIR = True  # 压缩后是否删除整个预处理目录（仅保留压缩包）

# 5. 新增：simple.json 清理配置
CLEAN_BY_SIMPLE_JSON = True  # 是否根据simple.json清理文件
SIMPLE_JSON_NAME = "sample.json"  # simple.json文件名

# 6. 全局日志数据结构
PIPELINE_LOG = {
    "session_info": {},
    "periods_processed": [],
    "summary": {}
}
SESSION_START_TIME = 0  # 会话开始时间
# ===================================================

def fmt_time(seconds: float) -> str:
    """将秒数格式化为可读时间字符串"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        m, s = divmod(int(seconds), 60)
        return f"{m}m{s}s"
    else:
        h, rem = divmod(int(seconds), 3600)
        m, s = divmod(rem, 60)
        return f"{h}h{m}m{s}s"


def log_header(msg: str, level: int = 1) -> None:
    """打印分级标题
    level 1: 会话级（双线）  level 2: 时间段级（单线）  level 3: 步骤级（短线）
    """
    width = 72
    if level == 1:
        print(f"\n{'='*width}")
        print(f"  {msg}")
        print(f"{'='*width}")
    elif level == 2:
        print(f"\n{'─'*width}")
        print(f"  {msg}")
        print(f"{'─'*width}")
    else:
        print(f"\n  ── {msg}")


def log_kv(key: str, value: str, indent: int = 4) -> None:
    """打印 key: value 对"""
    print(f"{' '*indent}{key}: {value}")


def log_ok(msg: str) -> None:
    print(f"  [OK] {msg}")


def log_fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def log_warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def log_skip(msg: str) -> None:
    print(f"  [SKIP] {msg}")


def emergency_cleanup():
    """紧急清理函数：在程序异常退出时恢复所有移动的文件"""
    if not ACTIVE_MOVE_RECORDS:
        return

    log_header("!! 异常退出 - 正在恢复db3文件 !!", level=1)

    total_restored = 0
    total_failed = 0

    for record_path in ACTIVE_MOVE_RECORDS:
        if not os.path.exists(record_path):
            continue

        try:
            success, total = restore_moved_files(record_path)
            total_restored += success
            total_failed += (total - success)
        except Exception as e:
            log_fail(f"恢复失败: {e}")
            total_failed += 1

    print(f"\n  紧急恢复完成: 成功 {total_restored}, 失败 {total_failed}")

def signal_handler(signum, frame):
    """信号处理器：捕获Ctrl+C等中断信号"""
    signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    print(f"\n\n  收到 {signal_name} 信号，正在安全退出...")
    emergency_cleanup()
    sys.exit(1)

def register_cleanup_handlers():
    """注册清理处理器（只注册一次）"""
    global CLEANUP_REGISTERED
    if CLEANUP_REGISTERED:
        return

    # 注册信号处理器
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # kill命令

    # 注册atexit处理器（正常退出时也检查）
    atexit.register(emergency_cleanup)

    CLEANUP_REGISTERED = True
    print("  [OK] 已注册紧急恢复处理器（Ctrl+C安全）")


def cleanup_after_period(period_idx: int) -> None:
    """每个时间段处理完成后的清理工作，防止内存累积"""
    # 1. 强制垃圾回收
    collected_total = 0
    for _ in range(3):
        collected_total += gc.collect()

    # 2. 同步磁盘缓冲区
    try:
        subprocess.run(['sync'], check=False, timeout=10, capture_output=True)
    except Exception:
        pass

    # 3. 获取内存状态（单行摘要）
    mem_info = ""
    try:
        result = subprocess.run(['free', '-h'], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().split('\n')
        if len(lines) >= 2:
            parts = lines[1].split()
            if len(parts) >= 4:
                mem_info = f"内存: {parts[2]}已用/{parts[1]}总计"
    except Exception:
        pass

    parts = [f"GC回收{collected_total}对象"]
    if mem_info:
        parts.append(mem_info)
    print(f"  [OK] 清理完成 ({', '.join(parts)})")


def save_pipeline_log(output_dir: str) -> None:
    """保存流程日志到JSON文件"""
    log_filename = "pipeline_log.json"
    log_path = os.path.join(output_dir, log_filename)
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(PIPELINE_LOG, f, ensure_ascii=False, indent=2)
        print(f"  [OK] 流程日志已保存: {log_filename}")
        return log_path
    except Exception as e:
        log_warn(f"保存流程日志失败: {e}")
        return None


def load_time_periods(yaml_path: str) -> List[Tuple[str, str]]:
    """从YAML文件加载时间段列表"""
    if not os.path.exists(yaml_path):
        raise FileNotFoundError(f"未找到时间段配置文件：{yaml_path}")
    
    with open(yaml_path, 'r', encoding='utf-8') as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"YAML文件格式错误：{str(e)}")
    
    if not isinstance(data, list) or len(data) == 0:
        raise ValueError(f"YAML文件内容必须是非空列表")
    
    periods = []
    for idx, period in enumerate(data, 1):
        if not isinstance(period, list) or len(period) != 2:
            raise ValueError(f"YAML第{idx}行格式错误：必须是包含2个元素的列表")
        
        # 转换为字符串并补零
        start = str(period[0]).zfill(6)
        end = str(period[1]).zfill(6)
        
        if not validate_time_format(start):
            raise ValueError(f"YAML第{idx}行开始时间错误：{period[0]} 不是有效的HHMMSS格式")
        if not validate_time_format(end):
            raise ValueError(f"YAML第{idx}行结束时间错误：{period[1]} 不是有效的HHMMSS格式")
        if start > end:
            raise ValueError(f"YAML第{idx}行时间错误：结束时间 {end} 早于开始时间 {start}")
        
        periods.append((start, end))
    
    return periods


def get_filter_script_config() -> tuple[str, str]:
    """从统一配置文件或 move_file.py 中读取默认配置"""
    # 优先从统一配置文件读取
    try:
        from config_loader import config
        return config.source_bag_dir, config.temp_filter_dir
    except ImportError:
        pass  # 配置加载器不存在，回退到旧方式

    # 回退：从 move_file.py 文件中解析配置
    if not os.path.exists(FILTER_SCRIPT_PATH):
        raise FileNotFoundError(f"未找到筛选脚本：{FILTER_SCRIPT_PATH}")

    with open(FILTER_SCRIPT_PATH, 'r', encoding='utf-8') as f:
        content = f.read()

    # 匹配默认配置（修改后的模式）
    source_match = re.search(r'DEFAULT_SOURCE_DIRECTORY\s*=\s*"([^"]+)"', content)
    if not source_match:
        # 回退到旧模式
        source_match = re.search(r'SOURCE_DIRECTORY\s*=\s*"([^"]+)"', content)
        if not source_match:
            raise ValueError(f"未在 {FILTER_SCRIPT_PATH} 中找到源目录配置")

    output_match = re.search(r'DEFAULT_OUTPUT_ROOT_DIRECTORY\s*=\s*"([^"]+)"', content)
    if not output_match:
        # 回退到旧模式
        output_match = re.search(r'OUTPUT_ROOT_DIRECTORY\s*=\s*"([^"]+)"', content)
        if not output_match:
            raise ValueError(f"未在 {FILTER_SCRIPT_PATH} 中找到输出目录配置")

    return source_match.group(1).strip(), output_match.group(1).strip()


def run_shell_command(command: str, step_name: str, capture_output: bool = False) -> dict:
    """执行命令，实时打印日志，返回执行信息

    Args:
        command: 要执行的命令
        step_name: 步骤名称
        capture_output: 是否捕获输出（用于解析返回值）

    Returns:
        包含执行信息的字典，如果 capture_output=True，还会包含 'output' 字段
    """
    start_time = time.time()
    log_header(step_name, level=3)

    process = subprocess.Popen(
        command,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        executable=os.environ.get('SHELL', '/bin/bash')
    )

    output_lines = []
    try:
        if process.stdout:
            for line in process.stdout:
                try:
                    decoded_line = line.decode('utf-8', errors='ignore').strip()
                except Exception:
                    decoded_line = line.decode(sys.getdefaultencoding(), errors='ignore').strip()

                print(decoded_line)
                if capture_output:
                    output_lines.append(decoded_line)

        process.wait()
    except Exception:
        # 确保子进程被清理，避免僵尸进程
        process.kill()
        process.wait()
        raise
    duration = time.time() - start_time

    result = {
        "step_name": step_name,
        "command": command,
        "return_code": process.returncode,
        "duration_seconds": round(duration, 2),
        "status": "success" if process.returncode == 0 else "failed"
    }

    if capture_output:
        result["output"] = output_lines

    if process.returncode != 0:
        log_fail(f"{step_name} (错误码: {process.returncode}, 耗时: {fmt_time(duration)})")
        raise RuntimeError(f"步骤 [{step_name}] 执行失败！错误码：{process.returncode}")

    log_ok(f"{step_name} ({fmt_time(duration)})")
    return result


def get_filtered_folder_path(output_root: str, start_time: str, end_time: str) -> str:
    """根据你的原有逻辑，计算筛选后的目标文件夹路径"""
    return os.path.join(output_root, f"{start_time}_{end_time}")


def get_bag_date(bag_path: str) -> str:
    """从 bag 文件获取实际数据日期（YYYYMMDD格式）

    Args:
        bag_path: bag 文件夹路径

    Returns:
        日期字符串（YYYYMMDD），如果获取失败则返回当前日期
    """
    try:
        from rosbags.highlevel import AnyReader
        from pathlib import Path

        with AnyReader([Path(bag_path)]) as reader:
            bag_start_time_ns = reader.start_time
            bag_datetime = datetime.fromtimestamp(bag_start_time_ns / 1e9)
            bag_date = bag_datetime.strftime('%Y%m%d')
            log_kv("Bag数据日期", f"{bag_date} ({bag_datetime.strftime('%Y-%m-%d %H:%M:%S')})")
            return bag_date
    except Exception as e:
        log_warn(f"无法从bag获取日期，使用当前日期: {e}")
        return datetime.now().strftime('%Y%m%d')



def validate_time_format(time_str: str) -> bool:
    """验证时间格式是否为 HHMMSS（6位数字）"""
    if len(time_str) != 6 or not time_str.isdigit():
        return False
    hh = int(time_str[:2])
    mm = int(time_str[2:4])
    ss = int(time_str[4:6])
    return 0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60


def modify_filter_script(start_time: str, end_time: str) -> None:
    """
    修改 move_file.py 的默认时间配置
    注意：使用统一配置文件后，此功能已不再需要，保留仅为兼容性
    """
    # 检查是否使用统一配置
    if USE_UNIFIED_CONFIG:
        return

    # 旧方式：直接修改脚本文件（已废弃）
    with open(FILTER_SCRIPT_PATH, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    updated_lines = []
    for line in lines:
        # 保留原有缩进
        indent = len(line) - len(line.lstrip())
        indent_str = line[:indent]

        if line.strip().startswith("DEFAULT_TARGET_START_TIME"):
            updated_lines.append(f'{indent_str}DEFAULT_TARGET_START_TIME = "{start_time}"  # 自动更新于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        elif line.strip().startswith("DEFAULT_TARGET_END_TIME"):
            updated_lines.append(f'{indent_str}DEFAULT_TARGET_END_TIME = "{end_time}"    # 自动更新于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        # 也处理旧的配置名称
        elif line.strip().startswith("TARGET_START_TIME") and not line.strip().startswith("DEFAULT_"):
            updated_lines.append(f'{indent_str}TARGET_START_TIME = "{start_time}"  # 自动更新于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        elif line.strip().startswith("TARGET_END_TIME") and not line.strip().startswith("DEFAULT_"):
            updated_lines.append(f'{indent_str}TARGET_END_TIME = "{end_time}"    # 自动更新于 {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}\n')
        else:
            updated_lines.append(line)

    with open(FILTER_SCRIPT_PATH, 'w', encoding='utf-8') as f:
        f.writelines(updated_lines)

    print(f"  [OK] 已更新筛选脚本时间段: {start_time} -> {end_time}")


def save_move_record(period_idx: int, start_time: str, end_time: str, moved_files: Dict[str, str]) -> str:
    """保存移动文件记录到JSON文件"""
    os.makedirs(MOVE_RECORD_DIR, exist_ok=True)
    
    record_filename = f"move_record_{period_idx:03d}_{start_time}_{end_time}.json"
    record_path = os.path.join(MOVE_RECORD_DIR, record_filename)
    
    with open(record_path, 'w', encoding='utf-8') as f:
        json.dump({
            'period_idx': period_idx,
            'start_time': start_time,
            'end_time': end_time,
            'moved_files': moved_files,
            'timestamp': datetime.now().isoformat()
        }, f, indent=2, ensure_ascii=False)
    
    print(f"    移动记录已保存: {record_filename}")
    return record_path


def restore_moved_files(record_path: str) -> Tuple[int, int]:
    """根据记录文件恢复移动的文件，返回（成功数，总数）。
    记录文件格式：{目标路径: 原始路径}，由 move_file.py 实时写入。
    """
    if not os.path.exists(record_path):
        log_warn(f"记录文件不存在: {record_path}")
        return 0, 0

    try:
        with open(record_path, 'r', encoding='utf-8') as f:
            moved_files = json.load(f)  # 直接是 {dest: src} 字典

        total_files = len(moved_files)
        success_count = 0
        failed_files = []

        for dest_path, src_path in moved_files.items():
            try:
                if os.path.exists(dest_path):
                    src_dir = os.path.dirname(src_path)
                    os.makedirs(src_dir, exist_ok=True)
                    shutil.move(dest_path, src_path)
                    if os.path.exists(src_path):
                        success_count += 1
                    else:
                        failed_files.append(os.path.basename(dest_path))
                elif os.path.exists(src_path):
                    success_count += 1
                else:
                    failed_files.append(os.path.basename(dest_path))
            except Exception as e:
                failed_files.append(f"{os.path.basename(dest_path)}({e})")

        if success_count == total_files:
            log_ok(f"db3文件恢复完成: {success_count}/{total_files}")
            os.remove(record_path)
        else:
            log_warn(f"db3文件恢复不完整: {success_count}/{total_files}")
            for f in failed_files:
                print(f"      失败: {f}")
            print(f"      记录文件保留供排查: {record_path}")

        return success_count, total_files

    except Exception as e:
        log_fail(f"读取记录文件失败: {e}")
        return 0, 0


def cleanup_move_records():
    """清理所有移动记录文件"""
    if os.path.exists(MOVE_RECORD_DIR):
        try:
            shutil.rmtree(MOVE_RECORD_DIR)
            log_ok(f"已清理移动记录目录: {MOVE_RECORD_DIR}")
        except Exception as e:
            log_warn(f"清理移动记录目录失败: {e}")


def find_undistorted_folder(preprocess_out_dir: str) -> Optional[str]:
    """在预处理输出目录下查找 undistorted 文件夹"""
    for root, dirs, files in os.walk(preprocess_out_dir):
        if "undistorted" in dirs:
            return os.path.join(root, "undistorted")
    return None


def run_check_and_compress(
    undistorted_path: str,
    compress_output_dir: str,
    period_idx: int,
    start_time: str,
    end_time: str,
    bag_path: str = None
) -> str:
    """调用外部检查压缩脚本，执行压缩流程，返回实际生成的压缩包路径"""
    # 注意：不在这里生成文件名，让 check_and_compress.py 根据 bag 时间戳生成
    # 这样可以确保使用实际数据时间而非本地处理时间
    compress_filename = f"PLACEHOLDER_{start_time}_{end_time}.{COMPRESS_FORMAT}"
    compress_path = os.path.join(compress_output_dir, compress_filename)

    check_compress_cmd = (
        f"{sys.executable} {CHECK_COMPRESS_SCRIPT_PATH} "
        f"--undistorted-path {undistorted_path} "
        f"--compress-path {compress_path} "
        f"--compress-format {COMPRESS_FORMAT} "
        f"--period {start_time}_{end_time}"
    )

    if bag_path:
        check_compress_cmd += f" --bag-path {bag_path}"

    result = run_shell_command(
        check_compress_cmd,
        f"检查+压缩",
        capture_output=True
    )

    # 从输出中解析实际生成的压缩包路径
    actual_compress_path = None
    if "output" in result:
        for line in result["output"]:
            if "COMPRESS_SUCCESS:" in line:
                actual_compress_path = line.split("COMPRESS_SUCCESS:")[-1].strip()
                break

    if not actual_compress_path:
        log_warn("未能从输出解析压缩包路径，使用预期路径")
        actual_compress_path = compress_path

    return actual_compress_path


def delete_raw_undistorted(undistorted_path: str) -> None:
    """压缩完成后，删除原始 undistorted 目录"""
    if DELETE_RAW_UNDISTORTED and os.path.exists(undistorted_path):
        try:
            shutil.rmtree(undistorted_path)
            log_ok(f"已删除 undistorted 目录")
        except Exception as e:
            log_warn(f"删除 undistorted 目录失败: {e}")


def delete_preprocess_dir(preprocess_dir: str, compress_path: str) -> None:
    """压缩完成后，删除整个预处理目录，仅保留压缩包

    Args:
        preprocess_dir: 预处理输出目录（如 20260107_143854_144050/）
        compress_path: 压缩包路径（应该在 preprocess_dir 内）
    """
    if not DELETE_PREPROCESS_DIR:
        return

    if not os.path.exists(preprocess_dir):
        return

    if not os.path.exists(compress_path):
        log_warn(f"压缩包不存在，不删除预处理目录")
        return

    try:
        compress_filename = os.path.basename(compress_path)
        parent_dir = os.path.dirname(preprocess_dir)
        new_compress_path = os.path.join(parent_dir, compress_filename)

        if os.path.abspath(compress_path) != os.path.abspath(new_compress_path):
            shutil.move(compress_path, new_compress_path)

        shutil.rmtree(preprocess_dir)
        log_ok(f"已清理预处理目录，最终产物: {compress_filename}")

        return new_compress_path

    except Exception as e:
        log_warn(f"清理预处理目录失败: {e}")
        return compress_path



def cleanup_by_simple_json(preprocess_out_dir: str, period_idx: int) -> dict:
    """根据simple.json清理文件，返回清理信息"""
    start_time = time.time()

    undistorted_path = find_undistorted_folder(preprocess_out_dir)
    if not undistorted_path:
        log_skip("未找到 undistorted 文件夹，跳过JSON清理")
        return {"status": "skipped", "reason": "undistorted folder not found", "duration_seconds": round(time.time() - start_time, 2)}

    json_path = os.path.join(undistorted_path, SIMPLE_JSON_NAME)
    if not os.path.exists(json_path):
        log_skip(f"未找到 {SIMPLE_JSON_NAME}，跳过JSON清理")
        return {"status": "skipped", "reason": "simple.json not found", "duration_seconds": round(time.time() - start_time, 2)}

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            json_data = json.load(f)

        if not isinstance(json_data, list):
            log_warn(f"{SIMPLE_JSON_NAME} 格式错误: 根元素必须是列表")
            return {"status": "failed", "reason": "invalid json format", "duration_seconds": round(time.time() - start_time, 2)}

        required_files = {}
        deleted_count = 0

        for item in json_data:
            for key, value in item.items():
                if (key.startswith("camera_") or key.startswith("iv_points_")) and value != "NOT_FOUND":
                    folder_name = key
                    if folder_name not in required_files:
                        required_files[folder_name] = set()
                    required_files[folder_name].add(value)

        if not required_files:
            log_skip(f"{SIMPLE_JSON_NAME} 中没有有效字段")
            return {"status": "skipped", "reason": "no valid fields in json", "duration_seconds": round(time.time() - start_time, 2)}

        for folder_name, files_to_keep in required_files.items():
            folder_path = os.path.join(undistorted_path, folder_name)
            if not os.path.exists(folder_path):
                continue

            for root, dirs, filenames in os.walk(folder_path):
                for filename in filenames:
                    if filename.endswith('.npy'):
                        continue

                    file_path = os.path.join(root, filename)
                    basename = os.path.basename(filename)

                    should_delete = True
                    for required_file in files_to_keep:
                        if basename == required_file or basename in required_file or required_file in basename:
                            should_delete = False
                            break

                    if should_delete:
                        try:
                            os.remove(file_path)
                            deleted_count += 1
                        except Exception:
                            pass

        duration = round(time.time() - start_time, 2)
        log_ok(f"JSON清理完成: 删除 {deleted_count} 个文件, 涉及 {len(required_files)} 个文件夹 ({fmt_time(duration)})")
        return {
            "status": "success",
            "folders_cleaned": len(required_files),
            "files_deleted": deleted_count,
            "duration_seconds": duration
        }

    except Exception as e:
        log_fail(f"JSON清理异常: {e}")
        return {
            "status": "failed",
            "error": str(e),
            "duration_seconds": round(time.time() - start_time, 2)
        }


def check_disk_space(path: str, min_free_gb: float = 50.0) -> bool:
    """检查指定路径的磁盘剩余空间是否充足

    Args:
        path: 要检查的路径
        min_free_gb: 最小剩余空间（GB）

    Returns:
        True 表示空间充足，False 表示不足
    """
    try:
        statvfs = os.statvfs(path)
        free_bytes = statvfs.f_frsize * statvfs.f_bavail
        free_gb = free_bytes / (1024 ** 3)
        if free_gb < min_free_gb:
            log_fail(f"磁盘空间不足: {free_gb:.1f}GB < {min_free_gb}GB")
            return False
        return True
    except Exception as e:
        log_warn(f"无法检查磁盘空间: {e}")
        return True  # 检查失败时不阻塞流程


def process_single_period(
    period_idx: int,
    start_time: str,
    end_time: str,
    source_dir: str,
    output_root: str,
    logtime: str,
    vehicle: str,
    main_out: str,
    total_periods: int
) -> dict:
    """处理单个时间段的全流程（筛选+预处理+清理+检查压缩）"""
    period_start_time = time.time()
    period_label = f"时间段 {period_idx}/{total_periods}: {start_time} -> {end_time}"
    log_header(period_label, level=2)
    print(f"    模式: {'移动' if MOVE_MODE else '复制'} | 车辆: {vehicle} | 日志时间: {logtime}")
    
    # 初始化日志记录
    period_log = {
        "period_index": f"{period_idx}/{total_periods}",
        "start_time": start_time,
        "end_time": end_time,
        "start_timestamp": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "status": "processing",
        "steps": []
    }
    
    # 初始化
    filtered_folder = get_filtered_folder_path(output_root, start_time, end_time)
    move_record_path = os.path.join("move_records", f"move_record_{start_time}_{end_time}.json")

    try:
        # 1. 检查磁盘空间
        if not check_disk_space(main_out, min_free_gb=50.0):
            period_log["status"] = "failed"
            period_log["reason"] = "insufficient disk space"
            period_log["duration_seconds"] = round(time.time() - period_start_time, 2)
            period_log["end_timestamp"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return period_log

        # 2. 更新筛选脚本的时间段
        modify_filter_script(start_time, end_time)

        # 3. 执行筛选db3文件（使用移动模式）
        if MOVE_MODE:
            move_record_path = os.path.join(tempfile.gettempdir(), f"move_{period_idx}_{start_time}_{end_time}.json")
            ACTIVE_MOVE_RECORDS.append(move_record_path)

        # 构建筛选命令
        filter_cmd = (
            f"{sys.executable} {FILTER_SCRIPT_PATH} "
            f"--source {source_dir} "
            f"--output {output_root} "
            f"--start {start_time} "
            f"--end {end_time}"
        )

        if MOVE_MODE:
            filter_cmd += f" --move --save-record {move_record_path}"

        filter_result = run_shell_command(filter_cmd, f"筛选db3文件")
        period_log["steps"].append(filter_result)

        # 4. 检查筛选结果
        if not os.path.exists(filtered_folder):
            log_fail(f"筛选失败: 未生成目标文件夹 {filtered_folder}")
            print(f"    跳过当前时间段，继续下一个...")
            period_log["status"] = "failed"
            period_log["reason"] = "filter output folder not created"
            period_log["duration_seconds"] = round(time.time() - period_start_time, 2)
            period_log["end_timestamp"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return period_log

        # 4.5. 从筛选后的 bag 获取实际日期
        bag_date = get_bag_date(filtered_folder)
        preprocess_out_dir = os.path.join(main_out, f"{bag_date}_{start_time}_{end_time}")

        # 5. 执行预处理
        run_export_cmd = (
            f"{sys.executable} {RUN_EXPORT_SCRIPT_PATH} "
            f"--bag {filtered_folder} "
            f"--out {preprocess_out_dir} "
            f"--vehicle {vehicle} "
            f"--logtime {logtime} "
            f"--start-time {start_time} "
            f"--end-time {end_time} "
            f"--skip-compress"
        )
        export_result = run_shell_command(run_export_cmd, f"预处理(camera+lidar+imu+undistort+sample)")
        period_log["steps"].append(export_result)

        # 6. 恢复与清理
        log_header("恢复与清理", level=3)
        
        # 6. 恢复db3文件（在预处理完成后）
        restore_start = time.time()
        if MOVE_MODE and move_record_path and os.path.exists(move_record_path):
            success_count, total_count = restore_moved_files(move_record_path)

            if move_record_path in ACTIVE_MOVE_RECORDS:
                ACTIVE_MOVE_RECORDS.remove(move_record_path)

            period_log["steps"].append({
                "step_name": "恢复db3文件",
                "status": "success" if success_count == total_count else "partial",
                "restored_files": success_count,
                "total_files": total_count,
                "duration_seconds": round(time.time() - restore_start, 2)
            })
            if success_count < total_count:
                log_warn("部分文件恢复失败，请检查源目录和目标目录")
        elif MOVE_MODE:
            log_warn("移动记录文件不存在，无法恢复db3文件")
            period_log["steps"].append({
                "step_name": "恢复db3文件",
                "status": "skipped",
                "reason": "no move record file",
                "duration_seconds": round(time.time() - restore_start, 2)
            })
        
        # 7. 清理临时筛选文件夹（只保留metadata.yaml）
        if os.path.exists(filtered_folder):
            try:
                # 只删除db3文件，保留metadata.yaml
                for filename in os.listdir(filtered_folder):
                    if filename.endswith('.db3'):
                        os.remove(os.path.join(filtered_folder, filename))
                
                # 如果文件夹为空，删除整个文件夹
                if len(os.listdir(filtered_folder)) == 0:
                    os.rmdir(filtered_folder)
            except Exception:
                pass
        
        # 8. 其他后续步骤
        if CLEAN_BY_SIMPLE_JSON:
            cleanup_result = cleanup_by_simple_json(preprocess_out_dir, period_idx)
            period_log["steps"].append({
                "step_name": "simple.json清理",
                **cleanup_result
            })
        
        # 9. 检查压缩流程
        compress_path = None
        if not SKIP_CHECK_COMPRESS:
            undistorted_path = find_undistorted_folder(preprocess_out_dir)
            if undistorted_path:
                compress_start = time.time()
                compress_path = run_check_and_compress(
                    undistorted_path=undistorted_path,
                    compress_output_dir=preprocess_out_dir,
                    period_idx=period_idx,
                    start_time=start_time,
                    end_time=end_time,
                    bag_path=source_dir  # 传递原始 bag 路径以获取实际数据时间
                )
                period_log["steps"].append({
                    "step_name": "检查+压缩",
                    "status": "success" if compress_path and os.path.exists(compress_path) else "failed",
                    "compress_path": compress_path if compress_path else None,
                    "duration_seconds": round(time.time() - compress_start, 2)
                })

                # 10. 清理预处理目录，仅保留压缩包
                if compress_path and os.path.exists(compress_path):
                    if DELETE_PREPROCESS_DIR:
                        # 整个目录都会被删除，无需单独删 undistorted
                        final_compress_path = delete_preprocess_dir(preprocess_out_dir, compress_path)
                        if final_compress_path:
                            compress_path = final_compress_path
                            period_log["compress_path"] = final_compress_path
                    else:
                        # 只删除 undistorted 原始目录，保留预处理目录
                        delete_raw_undistorted(undistorted_path)

        # 11. 打印完成信息
        period_duration = time.time() - period_start_time
        if DELETE_PREPROCESS_DIR and compress_path:
            log_ok(f"时间段 {period_idx}/{total_periods} 完成 ({fmt_time(period_duration)}) -> {os.path.basename(str(compress_path))}")
        else:
            log_ok(f"时间段 {period_idx}/{total_periods} 完成 ({fmt_time(period_duration)}) -> {preprocess_out_dir}")
        
        # 记录成功完成
        period_log["status"] = "success"
        period_log["output_dir"] = preprocess_out_dir
        if compress_path:
            period_log["compress_path"] = compress_path
        period_log["duration_seconds"] = round(time.time() - period_start_time, 2)
        period_log["end_timestamp"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 性能优化：每个时间段后清理内存
        cleanup_after_period(period_idx)

        return period_log
        
    except Exception as e:
        log_fail(f"时间段 {period_idx} 异常: {e}")

        # 发生异常时也要尝试恢复文件
        if MOVE_MODE and move_record_path and os.path.exists(move_record_path):
            log_warn("发生异常，尝试恢复db3文件...")
            restore_moved_files(move_record_path)

            if move_record_path in ACTIVE_MOVE_RECORDS:
                ACTIVE_MOVE_RECORDS.remove(move_record_path)

        # 清理临时文件
        if os.path.exists(filtered_folder):
            try:
                shutil.rmtree(filtered_folder)
            except Exception:
                pass

        # 记录异常
        period_log["status"] = "failed"
        period_log["error"] = str(e)
        period_log["duration_seconds"] = round(time.time() - period_start_time, 2)
        period_log["end_timestamp"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return period_log


def main():
    global SESSION_START_TIME
    SESSION_START_TIME = time.time()

    # 注册紧急恢复处理器（必须在最开始）
    register_cleanup_handlers()

    import argparse
    parser = argparse.ArgumentParser(description="ROS 2 Bag 批量时间筛选 + 预处理 + 检查压缩全流程脚本")
    parser.add_argument("--logtime", type=str, required=True, help="日志时间戳（如：20251124_111515，用于 run_export.py）")
    parser.add_argument("--vehicle", type=str, default=DEFAULT_VEHICLE, help=f"车辆型号（默认：{DEFAULT_VEHICLE}）")
    parser.add_argument("--main-out", type=str, default=DEFAULT_MAIN_OUT, help=f"预处理主输出目录（默认：{DEFAULT_MAIN_OUT}）")
    parser.add_argument("--yaml-path", type=str, default=TIME_PERIODS_YAML, help=f"时间段配置YAML文件路径（默认：{TIME_PERIODS_YAML}）")
    parser.add_argument("--skip-check-compress", action="store_true", help=f"跳过检查压缩流程（默认不跳过，优先级高于配置文件）")
    parser.add_argument("--skip-clean-json", action="store_true", help=f"跳过simple.json清理流程（默认不跳过）")
    parser.add_argument("--no-move", action="store_true", help=f"禁用移动模式，使用复制模式（默认使用移动模式）")
    parser.add_argument("--clean-records", action="store_true", help=f"清理所有移动记录文件")
    args = parser.parse_args()
    
    # 覆盖配置
    global MOVE_MODE, SKIP_CHECK_COMPRESS, CLEAN_BY_SIMPLE_JSON
    MOVE_MODE = not args.no_move
    if args.skip_check_compress:
        SKIP_CHECK_COMPRESS = True
    if args.skip_clean_json:
        CLEAN_BY_SIMPLE_JSON = False
    
    # 清理移动记录（如果指定）
    if args.clean_records:
        cleanup_move_records()
        return
    
    # 1. 加载时间段配置
    try:
        time_periods = load_time_periods(args.yaml_path)
        total_periods = len(time_periods)
        print(f"  [OK] 加载 {total_periods} 个时间段 (从 {time_periods[0][0]} 到 {time_periods[-1][1]})")
    except Exception as e:
        log_fail(f"加载时间段配置失败: {e}")
        sys.exit(1)
    
    # 2. 读取 filter_by_time.py 的真实配置
    try:
        SOURCE_DIRECTORY, OUTPUT_ROOT_DIRECTORY = get_filter_script_config()
    except Exception as e:
        log_fail(f"读取筛选脚本配置失败: {e}")
        sys.exit(1)
    
    # 3. 检查基础路径
    required_scripts = [
        (FILTER_SCRIPT_PATH, "筛选脚本"),
        (RUN_EXPORT_SCRIPT_PATH, "预处理脚本"),
    ]
    if not SKIP_CHECK_COMPRESS:
        required_scripts.append((CHECK_COMPRESS_SCRIPT_PATH, "检查压缩脚本"))
    
    for script_path, script_name in required_scripts:
        if not os.path.exists(script_path):
            log_fail(f"未找到{script_name}: {script_path}")
            sys.exit(1)
    
    # 4. 检查目录
    if not os.path.exists(SOURCE_DIRECTORY):
        log_fail(f"源db3目录不存在: {SOURCE_DIRECTORY}")
        sys.exit(1)

    if not os.path.exists(OUTPUT_ROOT_DIRECTORY):
        try:
            os.makedirs(OUTPUT_ROOT_DIRECTORY, exist_ok=True)
            log_ok(f"已创建筛选输出根目录: {OUTPUT_ROOT_DIRECTORY}")
        except Exception as e:
            log_fail(f"创建筛选输出根目录失败: {e}")
            sys.exit(1)
    
    # 5. 创建主输出目录
    os.makedirs(args.main_out, exist_ok=True)
    
    # 6. 记录会话信息到日志
    PIPELINE_LOG["session_info"] = {
        "start_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "source_directory": SOURCE_DIRECTORY,
        "filter_output_root": OUTPUT_ROOT_DIRECTORY,
        "preprocess_output": args.main_out,
        "vehicle": args.vehicle,
        "logtime": args.logtime,
        "yaml_config": args.yaml_path,
        "total_periods": total_periods,
        "config": {
            "move_mode": MOVE_MODE,
            "move_record_dir": MOVE_RECORD_DIR if MOVE_MODE else None,
            "clean_by_simple_json": CLEAN_BY_SIMPLE_JSON,
            "skip_check_compress": SKIP_CHECK_COMPRESS,
            "compress_format": COMPRESS_FORMAT if not SKIP_CHECK_COMPRESS else None
        }
    }
    
    # 打印全局配置信息
    log_header("ROS2 Bag 批量处理流水线", level=1)
    log_kv("源db3目录", SOURCE_DIRECTORY)
    log_kv("筛选输出", OUTPUT_ROOT_DIRECTORY)
    log_kv("预处理输出", args.main_out)
    log_kv("车辆型号", args.vehicle)
    log_kv("日志时间戳", args.logtime)
    log_kv("时间段", f"{total_periods} 个 ({time_periods[0][0]} ~ {time_periods[-1][1]})")
    log_kv("文件模式", '移动' if MOVE_MODE else '复制')
    log_kv("JSON清理", '启用' if CLEAN_BY_SIMPLE_JSON else '禁用')
    log_kv("检查压缩", '启用' if not SKIP_CHECK_COMPRESS else '禁用')
    
    # 7. 批量处理每个时间段
    success_count = 0
    fail_count = 0
    
    for period_idx, (start_time, end_time) in enumerate(time_periods, 1):
        try:
            period_log = process_single_period(
                period_idx=period_idx,
                start_time=start_time,
                end_time=end_time,
                source_dir=SOURCE_DIRECTORY,
                output_root=OUTPUT_ROOT_DIRECTORY,
                logtime=args.logtime,
                vehicle=args.vehicle,
                main_out=args.main_out,
                total_periods=total_periods
            )
            PIPELINE_LOG["periods_processed"].append(period_log)
            if period_log["status"] == "success":
                success_count += 1
            else:
                fail_count += 1
        except Exception as e:
            log_fail(f"时间段 {period_idx} 异常: {e}")
            print(f"    跳过当前时间段，继续下一个...")
            fail_count += 1
            # 记录异常的时间段
            PIPELINE_LOG["periods_processed"].append({
                "period_index": f"{period_idx}/{total_periods}",
                "start_time": start_time,
                "end_time": end_time,
                "status": "exception",
                "error": str(e)
            })
            continue
    
    # 8. 记录汇总信息
    total_duration = time.time() - SESSION_START_TIME
    PIPELINE_LOG["summary"] = {
        "end_time": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "total_periods": total_periods,
        "successful_periods": success_count,
        "failed_periods": fail_count,
        "success_rate": f"{(success_count/total_periods*100):.2f}%" if total_periods > 0 else "0%",
        "total_duration_seconds": round(total_duration, 2),
        "total_duration_formatted": f"{int(total_duration//3600)}h {int((total_duration%3600)//60)}m {int(total_duration%60)}s",
        "average_time_per_period_seconds": round(total_duration / total_periods, 2) if total_periods > 0 else 0
    }
    
    # 输出总体统计结果
    log_header("批量处理完成", level=1)
    log_kv("总时间段", str(total_periods))
    log_kv("成功", str(success_count))
    log_kv("失败/跳过", str(fail_count))
    log_kv("成功率", PIPELINE_LOG['summary']['success_rate'])
    log_kv("总耗时", PIPELINE_LOG['summary']['total_duration_formatted'])
    log_kv("平均耗时", f"{PIPELINE_LOG['summary']['average_time_per_period_seconds']:.1f}s/时间段")
    log_kv("输出目录", args.main_out)
    if MOVE_MODE:
        log_kv("db3文件", "已全部恢复原始位置")
    
    # 保存日志
    save_pipeline_log(args.main_out)


if __name__ == "__main__":
    main()
