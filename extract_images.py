#!/usr/bin/env python3
"""
从ROS2 bag文件中提取图像并保存为图片
支持话题：/bev/cam_front_8m, /bev/cam_left, /bev/cam_right, /bev/cam_rear
"""

import os
import argparse
from pathlib import Path
import rclpy
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message
import cv2
from cv_bridge import CvBridge
import yaml
from tqdm import tqdm

def extract_images_from_bag(bag_path, output_dir, topics=None):
    """
    从ROS2 bag文件中提取图像
    
    Args:
        bag_path: bag文件路径
        output_dir: 输出目录
        topics: 要提取的话题列表，如果为None则提取所有图像话题
    """
    # 默认话题列表
    default_topics = [
        '/bev/cam_front_8m',
        '/bev/cam_left',
        '/bev/cam_right',
        '/bev/cam_rear'
    ]
    
    if topics is None:
        topics = default_topics
    
    # 确保输出目录存在
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # 为每个话题创建子目录
    topic_dirs = {}
    for topic in topics:
        # 从话题名提取目录名
        dir_name = topic.replace('/', '_').strip('_')
        topic_dir = output_dir / dir_name
        topic_dir.mkdir(parents=True, exist_ok=True)
        topic_dirs[topic] = topic_dir
        print(f"为话题 {topic} 创建目录: {topic_dir}")
    
    # 初始化CV桥接器
    bridge = CvBridge()
    
    # 获取存储选项
    storage_options = {'uri': str(bag_path), 'storage_id': 'sqlite3'}
    
    # 创建读取器
    from rosbag2_py import SequentialReader, StorageOptions, ConverterOptions
    
    reader = SequentialReader()
    storage_options = StorageOptions(uri=str(bag_path), storage_id='sqlite3')
    converter_options = ConverterOptions()
    reader.open(storage_options, converter_options)
    
    # 获取所有话题和类型
    topic_types = reader.get_all_topics_and_types()
    
    # 创建话题类型映射
    type_map = {topic_type.name: topic_type.type for topic_type in topic_types}
    
    # 计数器
    image_count = {topic: 0 for topic in topics}
    
    print(f"开始从 {bag_path} 提取图像...")
    
    try:
        # 遍历所有消息
        while reader.has_next():
            # 读取消息
            topic, data, timestamp = reader.read_next()
            
            # 检查是否是我们要的话题
            if topic in topics:
                try:
                    # 获取消息类型
                    msg_type = get_message(type_map[topic])
                    
                    # 反序列化消息
                    msg = deserialize_message(data, msg_type)
                    
                    # 检查消息类型并转换为图像
                    if hasattr(msg, 'encoding') and hasattr(msg, 'data'):
                        # 标准sensor_msgs/msg/Image格式
                        cv_image = bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
                    elif hasattr(msg, 'format') and hasattr(msg, 'data'):
                        # 尝试其他可能的图像格式
                        try:
                            cv_image = bridge.compressed_imgmsg_to_cv2(msg, desired_encoding='bgr8')
                        except:
                            # 尝试其他方法
                            import numpy as np
                            if hasattr(msg, 'data'):
                                # 假设是原始字节数据
                                np_arr = np.frombuffer(msg.data, np.uint8)
                                cv_image = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                            else:
                                print(f"无法处理话题 {topic} 的消息类型")
                                continue
                    else:
                        print(f"话题 {topic} 的消息类型不支持")
                        continue
                    
                    # 生成文件名（使用时间戳）
                    timestamp_str = str(timestamp)
                    filename = f"{topic_dirs[topic]}/image_{timestamp_str}.png"
                    
                    # 保存图像
                    success = cv2.imwrite(str(filename), cv_image)
                    
                    if success:
                        image_count[topic] += 1
                        if image_count[topic] % 100 == 0:
                            print(f"已保存 {image_count[topic]} 张图片来自话题 {topic}")
                    
                except Exception as e:
                    print(f"处理话题 {topic} 的消息时出错: {e}")
                    continue
    
    except KeyboardInterrupt:
        print("\n用户中断提取过程")
    except Exception as e:
        print(f"读取bag文件时出错: {e}")
    finally:
        # 清理
        pass
    
    # 打印统计信息
    print("\n图像提取完成！")
    print("各话题提取统计:")
    for topic, count in image_count.items():
        print(f"  {topic}: {count} 张图片")
    
    # 创建汇总文件
    summary_file = output_dir / "extraction_summary.txt"
    with open(summary_file, 'w') as f:
        f.write("ROS2 Bag图像提取汇总\n")
        f.write("=" * 50 + "\n")
        f.write(f"Bag文件: {bag_path}\n")
        f.write(f"输出目录: {output_dir}\n")
        f.write(f"提取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n各话题提取数量:\n")
        for topic, count in image_count.items():
            f.write(f"  {topic}: {count} 张图片\n")
    
    return image_count

def main():
    print("Starting extract_images.py script")
    parser = argparse.ArgumentParser(description='从ROS2 bag文件中提取图像')
    parser.add_argument('bag_path', type=str, help='ROS2 bag文件路径')
    parser.add_argument('-o', '--output', type=str, default='./extracted_images',
                       help='输出目录 (默认: ./extracted_images)')
    parser.add_argument('-t', '--topics', type=str, nargs='+',
                       help='要提取的话题列表 (空格分隔)')
    
    args = parser.parse_args()
    
    # 检查bag文件是否存在
    bag_path = Path(args.bag_path)
    if not bag_path.exists():
        print(f"错误: bag文件不存在: {bag_path}")
        return 1
    
    # 检查是否是目录（ROS2 bag存储为目录）
    if not bag_path.is_dir():
        # 尝试查找相关的bag文件
        parent_dir = bag_path.parent
        bag_name = bag_path.stem
        possible_dir = parent_dir / bag_name
        if possible_dir.exists():
            bag_path = possible_dir
            print(f"使用bag目录: {bag_path}")
        else:
            print(f"错误: {bag_path} 既不是有效的bag文件也不是目录")
            return 1
    
    # 检查是否有metadata.yaml文件
    metadata_file = bag_path / "metadata.yaml"
    if not metadata_file.exists():
        # 尝试其他可能的metadata文件位置
        for f in bag_path.glob("*.yaml"):
            if "metadata" in f.name:
                metadata_file = f
                break
        else:
            print("警告: 未找到metadata.yaml文件，但将继续尝试读取")
    
    print(f"Bag文件: {bag_path}")
    print(f"输出目录: {args.output}")
    
    if args.topics:
        print(f"提取的话题: {args.topics}")
    
    # 导入必要的ROS2模块
    try:
        import rosbag2_py
    except ImportError:
        print("错误: 未找到rosbag2_py模块")
        print("请确保已安装ROS2并设置了环境变量")
        print("尝试: source /opt/ros/<distro>/setup.bash")
        return 1
    
    # 提取图像
    extract_images_from_bag(bag_path, args.output, args.topics)
    
    return 0

if __name__ == "__main__":
    import sys
    from datetime import datetime
    
    # 初始化rclpy（如果使用ROS2接口）
    rclpy_initialized = False
    try:
        rclpy.init()
        rclpy_initialized = True
    except:
        pass
    
    try:
        sys.exit(main())
    finally:
        if rclpy_initialized:
            rclpy.shutdown()