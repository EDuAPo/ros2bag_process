import argparse
import subprocess
import os
import sys
import shutil
import yaml
from typing import List, Dict, Any

# --- 配置常量 ---
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(CURRENT_DIR, "config.yaml")

EXPORT_CAMERA_SCRIPT = os.path.join(CURRENT_DIR, "export_camera.py")
EXPORT_LIDAR_SCRIPT = os.path.join(CURRENT_DIR, "export_lidar.py")
EXPORT_IMU_SCRIPT = os.path.join(CURRENT_DIR, "export_imu", "export_imu.py")
UNDISTORTION_SCRIPT = os.path.join(CURRENT_DIR, "undistortion", "undistortion.py")
EXTRACT_SAMPLE_SCRIPT = os.path.join(CURRENT_DIR, "extract_sample_undistorted.py")
CHECK_AND_COMPRESS_SCRIPT = os.path.join(CURRENT_DIR, "check_and_compress.py")


def load_config() -> Dict[str, Any]:
    """从 config.yaml 加载配置"""
    if not os.path.exists(CONFIG_FILE):
        print(f"  [FAIL] 配置文件不存在: {CONFIG_FILE}", file=sys.stderr)
        sys.exit(1)

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config
    except Exception as e:
        print(f"  [FAIL] 无法读取配置文件: {e}", file=sys.stderr)
        sys.exit(1)

def get_shell_setup_command(config: Dict[str, Any]) -> str:
    """
    检测当前运行的 shell 类型 (bash/zsh) 并返回 ROS 2 setup 命令。
    需要 source 两个自定义消息包：msg_interfaces 和 imu_msgs
    """
    current_shell = os.environ.get('SHELL', 'bash').split('/')[-1]

    if 'zsh' in current_shell:
        setup_file = "setup.zsh"
    elif 'bash' in current_shell:
        setup_file = "setup.bash"
    else:
        setup_file = "setup.bash"

    # 从配置文件读取 IMU 消息包基础路径
    imu_base_path = config['paths']['imu_msgs_install_path']

    # 构建绝对路径（如果是相对路径）
    if not os.path.isabs(imu_base_path):
        imu_base_path = os.path.join(CURRENT_DIR, imu_base_path)

    # 两个包的路径（imu_base_path 指向 export_imu/msg_interfaces/install）
    # 需要回退到 export_imu 目录
    export_imu_dir = os.path.dirname(os.path.dirname(imu_base_path))
    imu_msgs_install_path_1 = os.path.join(export_imu_dir, "msg_interfaces", "install")
    imu_msgs_install_path_2 = os.path.join(export_imu_dir, "imu_msgs", "install")

    # Source 两个包
    setup_cmd1 = f"source {os.path.join(imu_msgs_install_path_1, setup_file)}"
    setup_cmd2 = f"source {os.path.join(imu_msgs_install_path_2, setup_file)}"
    return f"{setup_cmd1} && {setup_cmd2}"


def run_command(command: List[str], step_name: str, use_shell: bool = False):
    """
    执行一个外部命令，并在失败时退出。
    """
    print(f"\n  >> {step_name}")

    if use_shell:
        full_command = command[0]
    else:
        full_command = command

    try:
        subprocess.run(full_command, check=True, text=True, shell=use_shell, executable=os.environ.get('SHELL', '/bin/bash'))
        print(f"  [OK] {step_name}")
    except subprocess.CalledProcessError as e:
        print(f"  [FAIL] {step_name} (错误码: {e.returncode})", file=sys.stderr)
        if e.stderr:
            print(f"    stderr: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"  [FAIL] {step_name} - 找不到脚本或命令", file=sys.stderr)
        sys.exit(1)


def adjust_directories(export_dir: str, undistorted_dir: str):
    """
    步骤 5: 调整目录结构，将 iv_points* 和 ins.json 移动到最终的 undistorted 目录。
    """
    print(f"\n  >> [Export Step 5/6] 调整目录结构")

    files_to_move = []
    try:
        for item in os.listdir(export_dir):
            if item.startswith("iv_points") or item == "ins.json":
                files_to_move.append(item)

        for filename in files_to_move:
            src = os.path.join(export_dir, filename)
            dst = os.path.join(undistorted_dir, filename)
            shutil.move(src, dst)

        print(f"  [OK] 目录调整完成, 移动 {len(files_to_move)} 项")

    except Exception as e:
        print(f"  [FAIL] 目录调整失败: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    # 加载配置文件
    config = load_config()

    parser = argparse.ArgumentParser(
        description="ROS 2 Bag 数据导出与预处理流程调度脚本。"
    )
    parser.add_argument(
        "--bag",
        type=str,
        required=False,
        help="输入 ROS 2 Bag 目录的路径（可选，默认从 config.yaml 读取）"
    )
    parser.add_argument(
        "--out",
        type=str,
        required=False,
        help="主输出目录的路径（可选，默认从 config.yaml 读取）"
    )
    parser.add_argument("--vehicle",
                        type=str,
                        required=False,
                        help="指定车辆型号/配置（可选，默认从 config.yaml 读取）")
    parser.add_argument("--logtime",
                        type=str,
                        required=False,
                        help="指定日志时间戳（可选，默认从 config.yaml 读取）")
    parser.add_argument("--start-time", type=str, help="开始时间 (HHMMSS 格式)")
    parser.add_argument("--end-time", type=str, help="结束时间 (HHMMSS 格式)")
    parser.add_argument("--skip-compress", action="store_true",
                        help="跳过压缩步骤（由 pipline.py 调度时使用，避免重复压缩）")

    args = parser.parse_args()

    # --- 从配置文件或命令行参数获取配置 ---
    INPUT_BAG_DIR = args.bag or config['paths']['source_bag_dir']
    MAIN_OUTPUT_DIR = args.out or config['paths']['main_output_dir']
    VEHICLE_MODEL = args.vehicle or config['vehicle']['model']
    LOGTIME = args.logtime or config['vehicle']['logtime']
    SCALE_MIN = str(config['processing']['scale_min'])
    LIDAR_FORMAT = config['processing']['lidar_format']

    # 去畸变参数目录
    undistortion_params_dir = config['paths']['undistortion_params_dir']
    if not os.path.isabs(undistortion_params_dir):
        undistortion_params_dir = os.path.join(CURRENT_DIR, undistortion_params_dir)

    # --- 目录变量定义 ---
    EXPORT_DIR = os.path.join(MAIN_OUTPUT_DIR)
    UNDISTORTED_DIR = os.path.join(EXPORT_DIR, "undistorted")
    IMU_JSON_PATH = os.path.join(EXPORT_DIR, "ins.json")

    # 确保输出目录存在
    os.makedirs(EXPORT_DIR, exist_ok=True)
    os.makedirs(UNDISTORTED_DIR, exist_ok=True)

    print(f"  预处理流程启动: bag={INPUT_BAG_DIR}")
    print(f"    输出: {MAIN_OUTPUT_DIR} | 车辆: {VEHICLE_MODEL} | 缩放: {SCALE_MIN}")

    # 获取 shell setup 命令
    SHELL_SETUP_COMMAND = get_shell_setup_command(config)

    # --- 1. 导出 Camera 图像 ---
    camera_command_string = (
        f"{sys.executable} {EXPORT_CAMERA_SCRIPT} "
        f"--bag {INPUT_BAG_DIR} "
        f"--out {EXPORT_DIR}"
    )
    if args.start_time and args.end_time:
        camera_command_string += f" --start-time {args.start_time} --end-time {args.end_time}"
    run_command([camera_command_string], "[Export Step 1/6] 导出 Camera 图像", use_shell=True)

    # --- 2. 导出 Lidar 点云 ---
    lidar_command_string = (
        f"{sys.executable} {EXPORT_LIDAR_SCRIPT} "
        f"--bag {INPUT_BAG_DIR} "
        f"--out {EXPORT_DIR} "
        f"--format {LIDAR_FORMAT}"
    )
    if args.start_time and args.end_time:
        lidar_command_string += f" --start-time {args.start_time} --end-time {args.end_time}"
    run_command([lidar_command_string], "[Export Step 2/6] 导出 Lidar 点云", use_shell=True)
    
    # --- 3. 导出 IMU/INS 数据 (需要 source) ---
    imu_command_string = (
        f"{SHELL_SETUP_COMMAND} && "
        f"{sys.executable} {EXPORT_IMU_SCRIPT} "
        f"--bag {INPUT_BAG_DIR} "
        f"--out {IMU_JSON_PATH}"
    )
    run_command([imu_command_string], "[Export Step 3/6] 导出 IMU/INS 数据 (需 Shell Setup)", use_shell=True)
    
    # --- 4. 图像去畸变 ---
    undistort_command_string = (
        f"{sys.executable} {UNDISTORTION_SCRIPT} "
        f"--images {EXPORT_DIR} "
        f"--params {undistortion_params_dir} "
        f"--vehicle {VEHICLE_MODEL} "
        f"--out {UNDISTORTED_DIR} "
        f"--scale_min {SCALE_MIN} "
        f"--logtime {LOGTIME}"
    )
    run_command([undistort_command_string], "[Export Step 4/6] 图像去畸变", use_shell=True)
    
    # --- 5. 调整目录结构 (调用单独的函数) ---
    adjust_directories(EXPORT_DIR, UNDISTORTED_DIR)
    
    # --- 6. 提取样本 ---
    extract_command_string = (
        f"{sys.executable} {EXTRACT_SAMPLE_SCRIPT} "
        f"{UNDISTORTED_DIR}"
    )
    run_command([extract_command_string], "[Export Step 6/6] 提取样本", use_shell=True)

    # --- 7. 压缩数据 (可选，使用 bag 时间戳作为日期) ---
    if args.skip_compress:
        print("\n  [SKIP] 压缩步骤（由上层调度脚本负责）")
    else:
        compress_command_string = (
            f"{sys.executable} {CHECK_AND_COMPRESS_SCRIPT} "
            f"--undistorted-path {UNDISTORTED_DIR} "
            f"--bag-path {INPUT_BAG_DIR}"
        )
        run_command([compress_command_string], "[Export Step 7/7] 压缩数据", use_shell=True)

    print(f"\n  [OK] 预处理导出阶段完成 -> {UNDISTORTED_DIR}")


if __name__ == "__main__":
    # 建议将此脚本保存为 run_export.py 或 run_export_optimized.py
    main()