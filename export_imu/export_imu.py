#!/usr/bin/env python3

import os
import json
import argparse
import numpy as np
from pyproj import Proj
from scipy.spatial.transform import Rotation as R
# 新增的导入：用于处理时间戳
from datetime import datetime
from typing import Dict, Any, List

# ROS 2 库 (需要在 ROS 2 环境中运行)
try:
    from rclpy.serialization import deserialize_message
    from rosidl_runtime_py.utilities import get_message
    
    # 修正导入：只导入最基础的类
    from rosbag2_py import SequentialReader, StorageOptions, ConverterOptions
    import rclpy
    
except ImportError as e:
    print(f"致命错误: 无法导入 ROS 2 相关的库。请确保您已激活 ROS 2 环境 (source /opt/ros/humble/setup.bash) 且 rosbag2_py 已安装。错误: {e}")
    exit(1)


# --- 常量 ---
# 支持的 IMU 消息类型（按优先级排序）
SUPPORTED_MSG_TYPES = [
    "msg_interfaces/msg/Hcinspvatzcb",  # CHCNAV INS 消息类型（优先）
    "imu_msgs/msg/Imu",                 # 自定义 IMU 消息类型（包含位置信息）
    "sensor_msgs/msg/Imu",              # 标准 ROS2 IMU 消息类型（仅姿态）
]


# --- 辅助函数：时间戳转换 (新增) ---

def timestamp_to_desc(timestamp_nanosec: int) -> str:
    """将纳秒级时间戳转换为 YYYYMMDD_HHMMSS_mmm 格式的描述字符串。"""
    # 将纳秒转换为秒
    timestamp_sec = timestamp_nanosec / 1e9
    
    # 提取整数秒和毫秒部分
    sec = int(timestamp_sec)
    msec = int((timestamp_sec - sec) * 1000)

    # 使用系统本地时间进行转换（符合一般数据解析习惯）
    dt_object = datetime.fromtimestamp(timestamp_sec) 
    
    # 格式化 YYYYMMDD_HHMMSS_mmm
    return dt_object.strftime("%Y%m%d_%H%M%S") + f"_{msec:03d}"


# --- 辅助函数：UTM 和四元数转换 (保持不变) ---

def get_utm_proj(latitude: float, longitude: float) -> Proj:
    """根据WGS84经纬度创建 pyproj.Proj 对象进行 UTM 转换。"""
    zone_number = int((longitude + 180) / 6) + 1
    return Proj(
        proj='utm', 
        zone=zone_number, 
        ellps='WGS84', 
        south=latitude < 0
    )

def convert_latlonalt_to_utm(latitude: float, longitude: float, altitude: float) -> Dict[str, Any]:
    """将WGS84经纬高转换为UTM坐标。"""
    try:
        utm_proj = get_utm_proj(latitude, longitude)
        easting, northing = utm_proj(longitude, latitude)
        zone_number = int((longitude + 180) / 6) + 1
        
        return {
            "utm_x": easting,
            "utm_y": northing,
            "utm_z": altitude, 
            "utm_zone": zone_number
        }
    except Exception:
        return {"utm_x": None, "utm_y": None, "utm_z": None, "utm_zone": None}


def convert_rpy_to_quaternion(roll: float, pitch: float, azimuth: float) -> Dict[str, float]:
    """将欧拉角 (Roll, Pitch, Azimuth/Yaw) 转换为四元数。

    Args:
        roll: 横滚角（度）
        pitch: 俯仰角（度）
        azimuth: 航向角/偏航角（度）
    """
    # 将度转换为弧度
    roll_rad = np.deg2rad(roll)
    pitch_rad = np.deg2rad(pitch)
    yaw_rad = np.deg2rad(azimuth)

    try:
        # 使用 ZYX 顺序 (Yaw-Pitch-Roll)
        r = R.from_euler('zyx', [yaw_rad, pitch_rad, roll_rad])
        quaternion = r.as_quat() # 返回 (x, y, z, w) 格式

        return {
            "quaternion_x": quaternion[0],
            "quaternion_y": quaternion[1],
            "quaternion_z": quaternion[2],
            "quaternion_w": quaternion[3],
        }
    except Exception:
        return {"quaternion_x": None, "quaternion_y": None, "quaternion_z": None, "quaternion_w": None}


def extract_imu_data(ins_msg, msg_type: str) -> Dict[str, Any]:
    """
    从不同类型的 IMU 消息中提取数据。

    Args:
        ins_msg: 反序列化后的消息对象
        msg_type: 消息类型字符串

    Returns:
        包含 latitude, longitude, altitude, roll, pitch, azimuth 的字典
    """
    if msg_type == "msg_interfaces/msg/Hcinspvatzcb":
        # CHCNAV INS 消息格式
        return {
            "latitude": ins_msg.latitude,
            "longitude": ins_msg.longitude,
            "altitude": ins_msg.altitude,
            "roll": ins_msg.roll,
            "pitch": ins_msg.pitch,
            "azimuth": ins_msg.yaw,
        }
    elif msg_type == "imu_msgs/msg/Imu":
        # imu_msgs/msg/Imu 消息格式（包含 ASENSING 子消息）
        return {
            "latitude": ins_msg.imu_msg.latitude,
            "longitude": ins_msg.imu_msg.longitude,
            "altitude": ins_msg.imu_msg.altitude,
            "roll": ins_msg.imu_msg.roll,
            "pitch": ins_msg.imu_msg.pitch,
            "azimuth": ins_msg.imu_msg.azimuth,
        }
    elif msg_type == "sensor_msgs/msg/Imu":
        # 标准 ROS2 IMU 消息格式（只有姿态，没有位置信息）
        # 从四元数转换为欧拉角
        quat = [
            ins_msg.orientation.x,
            ins_msg.orientation.y,
            ins_msg.orientation.z,
            ins_msg.orientation.w,
        ]
        r = R.from_quat(quat)
        euler = r.as_euler('zyx', degrees=True)  # 返回 [yaw, pitch, roll]

        return {
            "latitude": 0.0,       # sensor_msgs/Imu 不包含位置信息
            "longitude": 0.0,
            "altitude": 0.0,
            "roll": euler[2],      # roll
            "pitch": euler[1],     # pitch
            "azimuth": euler[0],   # yaw/azimuth
        }
    else:
        raise ValueError(f"不支持的消息类型: {msg_type}")


# --- ROS 2 Bag 解析逻辑 ---

def process_single_bag(bag_path: str) -> List[Dict[str, Any]]:
    """
    处理单个 ROS 2 bag 文件，读取所有消息，并手动过滤目标话题。
    """
    extracted_data: List[Dict[str, Any]] = []
    
    # 用于存储第一个有效帧的 UTM 坐标 (新增)
    first_utm: Dict[str, float] = {}

    # 1. 设置 Reader
    storage_options = StorageOptions(uri=bag_path, storage_id='sqlite3') 
    converter_options = ConverterOptions(
        input_serialization_format='cdr',
        output_serialization_format='cdr'
    )
    
    reader = SequentialReader()
    try:
        reader.open(storage_options, converter_options)

        # 2. 自动检测话题，查找支持的消息类型
        topics_and_types = reader.get_all_topics_and_types()

        target_topic_info = None
        detected_msg_type = None
        topic_map = {}

        for topic_info in topics_and_types:
            try:
                # 消息类型对象
                msg_type_obj = get_message(topic_info.type)
                topic_map[topic_info.name] = (topic_info.type, msg_type_obj)

                # 按优先级查找支持的消息类型
                if topic_info.type in SUPPORTED_MSG_TYPES:
                    if target_topic_info is None or SUPPORTED_MSG_TYPES.index(topic_info.type) < SUPPORTED_MSG_TYPES.index(detected_msg_type):
                        target_topic_info = topic_info
                        detected_msg_type = topic_info.type
            except ImportError:
                # 忽略无法导入的消息类型
                continue

        if not target_topic_info:
            print(f"❌ 警告: Bag '{bag_path}' 中未找到支持的消息类型。")
            print(f"   支持的类型: {', '.join(SUPPORTED_MSG_TYPES)}")
            return extracted_data

        print(f"✅ 找到目标话题: {target_topic_info.name}, 类型: {target_topic_info.type}")

        # 获取目标消息对象
        _, msg_type_obj = topic_map[target_topic_info.name]


        # 3. 循环读取消息 (不设置过滤器，兼容性最好)
        while reader.has_next():
            topic_name, data, timestamp = reader.read_next()
            
            # 4. 手动过滤非目标话题的消息
            if topic_name != target_topic_info.name:
                continue

            try:
                # 反序列化消息
                ins_msg = deserialize_message(data, msg_type_obj)

                # 使用统一的数据提取函数
                imu_data = extract_imu_data(ins_msg, detected_msg_type)
                latitude = imu_data["latitude"]
                longitude = imu_data["longitude"]
                altitude = imu_data["altitude"]
                roll = imu_data["roll"]
                pitch = imu_data["pitch"]
                azimuth = imu_data["azimuth"]

                # 5. 执行转换
                utm_coords = convert_latlonalt_to_utm(latitude, longitude, altitude)
                quaternions = convert_rpy_to_quaternion(roll, pitch, azimuth)

                # --- 新增逻辑：计算相对 UTM 坐标 ---
                if not first_utm and utm_coords['utm_x'] is not None:
                    # 记录第一个有效帧的 UTM 坐标作为原点
                    first_utm = {
                        'utm_x': utm_coords['utm_x'],
                        'utm_y': utm_coords['utm_y'],
                        'utm_z': utm_coords['utm_z'],
                    }
                
                # 计算相对坐标
                if first_utm:
                    tran_utm_x = utm_coords['utm_x'] - first_utm['utm_x']
                    tran_utm_y = utm_coords['utm_y'] - first_utm['utm_y']
                    tran_utm_z = utm_coords['utm_z'] - first_utm['utm_z']
                else:
                    # 如果第一个有效帧的坐标无效，则无法计算相对坐标
                    tran_utm_x, tran_utm_y, tran_utm_z = None, None, None

                # --- 新增逻辑：时间戳描述 ---
                timestamp_desc = timestamp_to_desc(timestamp)


                # 6. 存储结果 (包含所有新增字段)
                result = {
                    "timestamp_nanosec": timestamp,
                    "timestamp_desc": timestamp_desc, # 新增字段 1
                    "latitude": latitude,
                    "longitude": longitude,
                    "altitude": altitude,
                    "roll": roll,
                    "pitch": pitch,
                    "azimuth": azimuth,
                    "tran_utm_x": tran_utm_x, # 新增字段 2
                    "tran_utm_y": tran_utm_y, # 新增字段 3
                    "tran_utm_z": tran_utm_z, # 新增字段 4
                }
                result.update(utm_coords) # 包含 utm_x, utm_y, utm_z
                result.update(quaternions)
                
                extracted_data.append(result)

            except Exception as e:
                # 打印具体的话题名和错误，便于调试
                print(f"❌ 警告: Bag '{bag_path}' (Topic: {target_topic_info.name}) 中一条消息处理失败: {e}")
                continue

    except Exception as e:
        # 在这里捕获的错误通常是打开文件失败或底层存储插件的错误
        print(f"❌ 错误: 无法打开或读取 Bag 文件 '{bag_path}': {e}")
        
    finally:
        # 避免 AttributeError 崩溃
        pass
        
    return extracted_data


def main():
    """
    主函数：解析命令行参数，遍历目录并调用处理函数。
    """
    try:
        import rclpy
    except ImportError:
        print("致命错误: 无法导入 rclpy。请确保 ROS 2 环境已激活。")
        return
        
    rclpy.init(args=None) # 初始化 rclpy

    parser = argparse.ArgumentParser(
        description=f"解析 ROS 2 bag 文件中的 INS/IMU 消息，转换为 UTM 坐标和四元数，并输出 JSON 文件。支持的消息类型: {', '.join(SUPPORTED_MSG_TYPES)}"
    )
    parser.add_argument(
        "--bag",
        type=str,
        dest="input_dir",
        required=True,
        help="包含一个或多个 ROS 2 bag 文件夹的父路径。"
    )
    parser.add_argument(
        "--out",
        type=str,
        dest="output_file", 
        required=True,
        help="所有转换数据将存储到的最终 JSON 文件路径。"
    )

    args = parser.parse_args()

    
    input_dir = args.input_dir
    output_file = args.output_file

    all_data = []
    
    # 检查输入路径是否存在
    if not os.path.exists(input_dir):
        print(f"❌ 错误：输入路径不存在：{input_dir}")
        return
    
    # 判断输入路径本身是否是一个bag目录（包含metadata.yaml）
    meta_file = os.path.join(input_dir, "metadata.yaml")
    if os.path.exists(meta_file):
        # 单目录模式：直接处理这个目录
        print(f"✅ 输入路径是单个bag目录：{input_dir}")
        bag_paths = [input_dir]
    else:
        # 多目录模式：遍历子目录
        print(f"📁 按多目录模式处理：遍历 {input_dir} 的子目录")
        bag_paths = []
        
        try:
            entries = os.listdir(input_dir)
        except Exception as e:
            print(f"❌ 无法读取目录 {input_dir}: {e}")
            return
        
        if not entries:
            print(f"⚠️  目录为空：{input_dir}")
            return
        
        # 遍历子目录，查找bag目录
        bag_count = 0
        for entry in sorted(entries):
            bag_path = os.path.join(input_dir, entry)
            
            # 只处理目录
            if not os.path.isdir(bag_path):
                continue
            
            # 检查是否是有效的bag目录
            meta_file = os.path.join(bag_path, "metadata.yaml")
            if not os.path.exists(meta_file):
                # 不是bag目录，跳过
                continue
            
            bag_paths.append(bag_path)
            bag_count += 1
            print(f"  找到bag目录 {bag_count}: {entry}")
        
        if bag_count == 0:
            print(f"⚠️  警告：在 {input_dir} 中未找到有效的bag目录")
            print(f"   检查目录结构：应有子目录，每个子目录包含 metadata.yaml")
            return

    print(f"总计找到 {len(bag_paths)} 个bag目录")
    
    # 处理每个bag目录
    for bag_path in bag_paths:
        print(f"\n🚀 正在处理 Bag: {bag_path}")
        data = process_single_bag(bag_path)
        if data:
            print(f"✅ 完成处理。提取了 {len(data)} 条消息。")
            all_data.extend(data)
        else:
            print(f"🛑 Bag '{bag_path}' 处理完成，但未提取到有效数据。")

    # 写入最终的 JSON 文件
    if all_data:
        print(f"\n🎉 所有 Bag 处理完毕。总共提取了 {len(all_data)} 条数据。")
        try:
            # 自定义编码器处理 numpy 类型
            class CustomJSONEncoder(json.JSONEncoder):
                def default(self, obj):
                    if isinstance(obj, np.integer):
                        return int(obj)
                    elif isinstance(obj, np.floating):
                        return float(obj)
                    return json.JSONEncoder.default(self, obj)

            # 确保输出目录存在
            os.makedirs(os.path.dirname(output_file), exist_ok=True)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(all_data, f, ensure_ascii=False, indent=4, cls=CustomJSONEncoder)
            print(f"💾 数据已成功写入到 JSON 文件: {output_file}")
        except IOError as e:
            print(f"❌ 错误: 无法写入输出文件 '{output_file}': {e}")
    else:
        print("\n⚠️ 警告: 未从任何 Bag 文件中提取到数据，未生成输出文件。")
        
    rclpy.shutdown()

if __name__ == "__main__":
    main()