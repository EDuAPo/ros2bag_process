#!/usr/bin/env python3

# -*- coding: utf-8 -*-

import argparse
from datetime import datetime
import os
from pathlib import Path
import threading
import queue
import sys
from concurrent.futures import ThreadPoolExecutor

import cv2
import gi
import numpy as np
import time  # 添加导入time，用于fallback

from rosbags.highlevel import AnyReader
from rosbags.serde import deserialize_cdr

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

# 全局变量：记录已打印的警告，避免刷屏
_PRINTED_WARNINGS = set()

# 确保标准输出使用UTF-8编码，防止乱码
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

class ProgressMonitor(threading.Thread):
    """定期打印所有topic的解码进度，避免多线程打印冲突导致的乱码"""
    def __init__(self, counters_dict, interval=2.0):
        super().__init__(daemon=True)
        self.counters = counters_dict
        self.interval = interval
        self.running = True
        self.lock = threading.Lock()

    def run(self):
        while self.running:
            time.sleep(self.interval)
            self.print_stats()
    
    def print_stats(self):
        with self.lock:
            status_parts = []
            # 排序以保持打印顺序一致
            for topic in sorted(self.counters.keys()):
                counts = self.counters[topic]
                # 简化topic名称显示
                short_name = topic.split('/')[-1]
                status_parts.append(f"{short_name}: {counts['decoded']}")
            
            if status_parts:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] Progress: {' | '.join(status_parts)}", flush=True)

    def stop(self):
        self.running = False
        self.print_stats()

ALL_CAMERA_H265_TOPICS = [
    '/camera/cam_8M_wa_front',
    '/camera/cam_8M_pt_front',
    '/camera/cam_3M_front',
    '/camera/cam_3M_left',
    '/camera/cam_3M_right',
    '/camera/cam_3M_rear',
]

def create_pipeline(topic_name_sanitized, use_hw_accel="auto"):
    """
    为每个topic创建一个GStreamer解码管道。
    use_hw_accel: 'nvidia', 'vaapi', or 'none'
    """
    # 软件解码（默认和备用选项）
    decoder = "avdec_h265"
    
    # 尝试选择硬件解码器
    if use_hw_accel == "nvidia":
        # 注意: NVIDIA管道可能需要更复杂的元素，如nvvidconv
        # 这只是一个基础示例，可能需要根据具体驱动和GStreamer版本微调
        decoder = "nvv4l2decoder"
        print(f"[{topic_name_sanitized}] Using NVIDIA hardware decoder.")
    elif use_hw_accel == "vaapi":
        decoder = "vaapih265dec"
        print(f"[{topic_name_sanitized}] Using VA-API hardware decoder.")
    else:
        print(f"[{topic_name_sanitized}] Using software decoder (avdec_h265).")

    pipeline_str = (
        f"appsrc name={topic_name_sanitized} format=time stream-type=stream caps=video/x-h265,stream-format=byte-stream,alignment=au ! "
        f"h265parse config-interval=1 ! {decoder} ! "
        "videoconvert ! video/x-raw,format=BGR ! "
        "appsink name=sink emit-signals=True max-buffers=50 drop=False sync=false"
    )
    return Gst.parse_launch(pipeline_str)

def save_image_task(filepath, frame, semaphore):
    """用于在线程池中执行的图像保存任务"""
    try:
        if not cv2.imwrite(filepath, frame):
            print(f"Error: Failed to write image to {filepath}", file=sys.stderr)
    except Exception as e:
        print(f"Exception while writing image {filepath}: {e}", file=sys.stderr)
    finally:
        semaphore.release()

def on_new_sample(sink, user_data):
    """appsink的回调函数，现在将保存任务提交给线程池"""
    output_dir, topic_name, counters, writer_pool, semaphore = user_data  # 移除timestamps

    sample = sink.emit('pull-sample')
    if not sample: return Gst.FlowReturn.OK

    buf, caps = sample.get_buffer(), sample.get_caps()
    height = caps.get_structure(0).get_value('height')
    width = caps.get_structure(0).get_value('width')

    result, mapinfo = buf.map(Gst.MapFlags.READ)
    if result:
        try:
            timestamp = buf.pts  # 从PTS获取timestamp
            if timestamp == Gst.CLOCK_TIME_NONE:
                # Fallback: 如果PTS无效，用当前系统时间（纳秒）
                warning_msg = f"\n[{topic_name}] Warning: Invalid PTS, using current time."
                if warning_msg not in _PRINTED_WARNINGS:
                    print(warning_msg)
                    _PRINTED_WARNINGS.add(warning_msg)
                timestamp = int(time.time_ns())  # 需要import time

            sec, nsec = timestamp // 1_000_000_000, timestamp % 1_000_000_000
            timestamp_sec = timestamp / 1e9
            dt = datetime.fromtimestamp(timestamp_sec)
            timestamp_str = dt.strftime("%Y%m%d_%H%M%S_%f")[:-3]  # 精确到毫秒

            filename = f"{timestamp_str}.jpg"
            filepath = os.path.join(output_dir, filename)
            
            frame = np.ndarray((height, width, 3), buffer=mapinfo.data, dtype=np.uint8)
            
            # 异步写入，必须复制frame
            # 获取信号量，限制待处理任务数量，防止内存爆炸
            semaphore.acquire()
            writer_pool.submit(save_image_task, filepath, frame.copy(), semaphore)
            
            counters['decoded'] += 1
            # 移除每帧打印，改由ProgressMonitor统一打印
            # if counters['decoded'] % 1 == 0:
            #     print(f"\r[{topic_name}] Decoded frames: {counters['decoded']}", end='')
        except Exception as e:  # 通用捕获，替换原Empty异常
            print(f"\n[{topic_name}] Error in callback: {e}", file=sys.stderr)
        finally:
            buf.unmap(mapinfo)
    return Gst.FlowReturn.OK

def decode_worker(topic_name, data_queue, base_output_dir, hw_accel_flag, shared_counters):
    """解码工作线程，现在包含一个用于写入的线程池"""
    topic_name_sanitized = topic_name.replace('/', '_')
    topic_name_sanitized = topic_name_sanitized.lstrip('_')
    output_dir = os.path.join(base_output_dir, topic_name_sanitized)
    os.makedirs(output_dir, exist_ok=True)

    print(f"[{topic_name}] Worker started. Outputting to: {output_dir}")

    # 初始化共享计数器
    if topic_name not in shared_counters:
        shared_counters[topic_name] = {'pushed': 0, 'decoded': 0}
    counters = shared_counters[topic_name]

    # 初始化对象引用（用于finally清理）
    pipeline = None
    writer_pool = None
    loop_thread = None
    sink = None
    appsrc = None
    bus = None
    main_loop = None

    try:
        # 创建写入池和信号量
        writer_pool = ThreadPoolExecutor(max_workers=6)
        semaphore = threading.BoundedSemaphore(value=100)

        main_loop = GLib.MainLoop()
        eos_received = threading.Event()

        pipeline = create_pipeline(topic_name_sanitized, hw_accel_flag)

        user_data_for_callback = (output_dir, topic_name, counters, writer_pool, semaphore)
        sink = pipeline.get_by_name('sink')
        sink.connect("new-sample", on_new_sample, user_data_for_callback)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        def on_bus_message(bus_msg, message):
            t = message.type
            if t == Gst.MessageType.EOS:
                print(f"\n[{topic_name}] Received EOS from pipeline.")
                eos_received.set()
                main_loop.quit()
            elif t == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                print(f"\n[{topic_name}] GStreamer Error: {err}. {debug}", file=sys.stderr)
                main_loop.quit()
            return True
        bus.connect("message", on_bus_message)

        appsrc = pipeline.get_by_name(topic_name_sanitized)
        appsrc.set_property('is-live', False)
        appsrc.set_property('format', Gst.Format.TIME)
        appsrc.set_property('max-bytes', 50 * 1024 * 1024)  # 50MB buffer
        appsrc.set_property('block', True)

        pipeline.set_state(Gst.State.PLAYING)
        loop_thread = threading.Thread(target=main_loop.run, daemon=True)
        loop_thread.start()

        try:
            while True:
                item = data_queue.get()
                if item is None: break

                timestamp, h265_data = item
                counters['pushed'] += 1
                buf = Gst.Buffer.new_wrapped(h265_data)
                buf.pts = timestamp
                buf.dts = Gst.CLOCK_TIME_NONE

                ret = appsrc.emit('push-buffer', buf)
                if ret != Gst.FlowReturn.OK:
                    print(f"[{topic_name}] push-buffer returned {ret}", file=sys.stderr)
                    break
        except Exception as e:
            print(f"[{topic_name}] Error in push loop: {e}", file=sys.stderr)

        print(f"[{topic_name}] Sending EOS to appsrc...")
        appsrc.emit('end-of-stream')

        print(f"[{topic_name}] Waiting for pipeline to finish processing...")
        eos_received.wait()

        if loop_thread:
            loop_thread.join(timeout=5.0)
            if loop_thread.is_alive():
                print(f"[{topic_name}] Main loop still running, forcing quit...")
                main_loop.quit()
                loop_thread.join(timeout=2.0)

        print(f"\r[{topic_name}] Finalizing...")
        print(f"--- Summary for Topic: {topic_name} ---")
        print(f"  Frames pushed to decoder: {counters['pushed']}")
        print(f"  Frames successfully decoded: {counters['decoded']}")
        print("--------------------------------------------------")

    finally:
        # 显式清理资源，防止内存泄漏
        if pipeline:
            pipeline.set_state(Gst.State.NULL)
        if writer_pool:
            writer_pool.shutdown(wait=True)

        # 强制释放GStreamer对象引用
        pipeline = None
        sink = None
        appsrc = None
        bus = None
        main_loop = None

        # 建议垃圾回收
        import gc
        gc.collect()

def get_all_bags(input_path):
    """获取所有bag目录，支持单目录和多目录模式"""
    bag_root = os.path.abspath(input_path)
    bag_paths = []
    
    # 检查输入路径本身是否是一个bag目录（包含metadata.yaml）
    meta_file = os.path.join(bag_root, "metadata.yaml")
    if os.path.exists(meta_file):
        print(f"✅ 输入路径是单个bag目录：{bag_root}")
        bag_paths.append(Path(bag_root))
        return bag_paths
    
    # 否则，按原逻辑遍历子目录
    print(f"📁 按多目录模式处理：遍历 {bag_root} 的子目录")
    for entry in sorted(os.listdir(bag_root)):
        bag_path = os.path.join(bag_root, entry)
        if not os.path.isdir(bag_path):
            continue
        
        meta_file = os.path.join(bag_path, "metadata.yaml")
        if not os.path.exists(meta_file):
            continue
        
        bag_paths.append(Path(bag_path))
    
    print(f"找到 {len(bag_paths)} 个bag目录")
    return bag_paths

def rename_topic(topic_name):
    topic_name_sanitized = topic_name.replace('/', '_')
    topic_name_sanitized = topic_name_sanitized.lstrip('_')
    return topic_name_sanitized

def main():
    parser = argparse.ArgumentParser(
        description="Decode H.265 data from multiple continuous ROS2 bags.",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("--bag", required=True, help="要导出ROS2 bag 的根目录，里面包含多个 bag 子目录")
    parser.add_argument("--out", required=True, help="输出根目录,目录若存在将会删除")

    parser.add_argument("--hwaccel", type=str, default="none", choices=['none', 'nvidia', 'vaapi'],
                        help="Specify hardware acceleration method.")
    parser.add_argument("--start-time", type=str, help="开始时间 (HHMMSS 格式)")
    parser.add_argument("--end-time", type=str, help="结束时间 (HHMMSS 格式)")
    args = parser.parse_args()

    # 解析时间参数
    start_time_ns = None
    end_time_ns = None
    if args.start_time and args.end_time:
        try:
            # 从 bag 获取日期
            temp_bag_path = args.bag
            if not os.path.exists(os.path.join(temp_bag_path, "metadata.yaml")):
                for entry in sorted(os.listdir(temp_bag_path)):
                    candidate = os.path.join(temp_bag_path, entry)
                    if os.path.isdir(candidate) and os.path.exists(os.path.join(candidate, "metadata.yaml")):
                        temp_bag_path = candidate
                        break

            with AnyReader([Path(temp_bag_path)]) as reader:
                bag_start_time_ns = reader.start_time
                bag_datetime = datetime.fromtimestamp(bag_start_time_ns / 1e9)
                bag_date = bag_datetime.date()

                # 解析用户时间
                start_hh = int(args.start_time[:2])
                start_mm = int(args.start_time[2:4])
                start_ss = int(args.start_time[4:6])
                end_hh = int(args.end_time[:2])
                end_mm = int(args.end_time[2:4])
                end_ss = int(args.end_time[4:6])

                start_dt = datetime.combine(bag_date, datetime.min.time()).replace(
                    hour=start_hh, minute=start_mm, second=start_ss
                )
                end_dt = datetime.combine(bag_date, datetime.min.time()).replace(
                    hour=end_hh, minute=end_mm, second=end_ss
                )

                start_time_ns = int(start_dt.timestamp() * 1e9)
                end_time_ns = int(end_dt.timestamp() * 1e9)

                print(f"⏰ 时间范围过滤: {args.start_time} - {args.end_time}")
                print(f"   转换为: {start_dt} - {end_dt}")
                print(f"   纳秒时间戳: {start_time_ns} - {end_time_ns}")
        except Exception as e:
            print(f"⚠️  警告: 无法解析时间参数，将导出所有数据: {e}")
            start_time_ns = None
            end_time_ns = None

    threads, data_queues = {}, {}
    shared_counters = {}  # 所有线程共享的计数器字典

    # 启动进度监控线程
    monitor = ProgressMonitor(shared_counters, interval=2.0)
    monitor.start()

    print(f"Starting bag file processing with hardware acceleration: {args.hwaccel}")
    # 打印将要按顺序处理的所有bag文件
    print(f"Input bags (will be processed in this order): {args.bag}")

    out_dir = os.path.abspath(args.out)
    input_bag_dir = os.path.abspath(args.bag)
    if input_bag_dir == out_dir:
        print(f"Error: Output directory '{out_dir}' cannot be the same as input bag directory '{input_bag_dir}'.", file=sys.stderr)
        sys.exit(1)

    # 如果输出目录存在，则先删除
    for topic in ALL_CAMERA_H265_TOPICS:
        topic_name_sanitized = rename_topic(topic)
        topic_output_dir = os.path.join(out_dir, topic_name_sanitized)
        if os.path.exists(topic_output_dir):
            print(f"Output directory for topic '{topic}' exists at '{topic_output_dir}'. Deleting...")
            import shutil
            shutil.rmtree(topic_output_dir)
            os.makedirs(topic_output_dir, exist_ok=True)

    bag_paths = get_all_bags(args.bag)

    total_messages_read = 0
    total_messages_filtered = 0

    try:
        with AnyReader(bag_paths) as reader:

            # 这个循环现在会无缝地遍历所有bag文件中的所有消息
            for connection, timestamp, rawdata in reader.messages():
                if connection.msgtype != 'sensor_msgs/msg/Image': continue
                topic_name = connection.topic
                if topic_name not in ALL_CAMERA_H265_TOPICS:
                    print(f"\nSkipping topic: {topic_name}")
                    continue

                total_messages_read += 1

                # 时间范围过滤
                if start_time_ns is not None and end_time_ns is not None:
                    if timestamp < start_time_ns or timestamp > end_time_ns:
                        total_messages_filtered += 1
                        continue

                # 线程和管线只在第一次遇到topic时创建
                if topic_name not in threads:
                    print(f"\nDiscovered new topic: {topic_name}. Starting worker thread.")
                    q = queue.Queue(maxsize=5000)
                    data_queues[topic_name] = q
                    # 这个线程将存活，直到所有bag文件都被处理完毕
                    thread = threading.Thread(target=decode_worker, args=(topic_name, q, args.out, args.hwaccel, shared_counters))
                    threads[topic_name] = thread
                    thread.start()

                msg = reader.deserialize(rawdata, connection.msgtype)  # 修复DeprecationWarning
                # 持续推送数据，无需关心它来自哪个bag文件
                data_queues[topic_name].put((timestamp, msg.data.tobytes()))

    except Exception as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
    finally:
        # 这个 'finally' 块只在所有bag文件都被处理完毕后才会执行
        print("\nEnd of all bag files reached. Signaling worker threads to finalize...")
        if start_time_ns is not None and end_time_ns is not None:
            print(f"[INFO] 时间过滤统计: 总读取 {total_messages_read} 帧，过滤掉 {total_messages_filtered} 帧，保留 {total_messages_read - total_messages_filtered} 帧")
        for q in data_queues.values(): q.put(None)

        # 停止监控线程（在join之前，避免继续打印）
        monitor.stop()
        monitor.join()

        # 等待所有worker线程完成
        for t in threads.values(): t.join()

        print(f"\nAll decoding threads have finished. Program terminated. Output is in '{args.out}'")

if __name__ == '__main__':
    main()