#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量相机导出脚本 - 一次性处理多个时间段，减少bag文件重复打开
优化点：
1. 一次打开bag，处理多个时间段
2. 根据时间戳自动分发到不同输出目录
3. 降低磁盘I/O开销
"""

import argparse
from datetime import datetime
import os
from pathlib import Path
import threading
import queue
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple

import cv2
import gi
import numpy as np
import time

from rosbags.highlevel import AnyReader
from rosbags.serde import deserialize_cdr

gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib

Gst.init(None)

_PRINTED_WARNINGS = set()

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

ALL_CAMERA_H265_TOPICS = [
    '/camera/cam_8M_wa_front',
    '/camera/cam_8M_pt_front',
    '/camera/cam_3M_front',
    '/camera/cam_3M_left',
    '/camera/cam_3M_right',
    '/camera/cam_3M_rear',
]

class TimeRange:
    """时间段范围"""
    def __init__(self, start_hhmmss: str, end_hhmmss: str, output_dir: str):
        self.start_hhmmss = start_hhmmss
        self.end_hhmmss = end_hhmmss
        self.output_dir = output_dir

        # 转换为秒数（假设同一天内）
        self.start_sec = int(start_hhmmss[:2]) * 3600 + int(start_hhmmss[2:4]) * 60 + int(start_hhmmss[4:6])
        self.end_sec = int(end_hhmmss[:2]) * 3600 + int(end_hhmmss[2:4]) * 60 + int(end_hhmmss[4:6])

    def contains(self, timestamp_ns: int) -> bool:
        """检查时间戳是否在此时间段内"""
        dt = datetime.fromtimestamp(timestamp_ns / 1e9)
        time_sec = dt.hour * 3600 + dt.minute * 60 + dt.second
        return self.start_sec <= time_sec <= self.end_sec

class ProgressMonitor(threading.Thread):
    """进度监控"""
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
            for topic in sorted(self.counters.keys()):
                counts = self.counters[topic]
                short_name = topic.split('/')[-1]
                status_parts.append(f"{short_name}: {counts['decoded']}")

            if status_parts:
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] Progress: {' | '.join(status_parts)}", flush=True)

    def stop(self):
        self.running = False
        self.print_stats()

def create_pipeline(topic_name_sanitized, use_hw_accel="none"):
    """创建GStreamer解码管道"""
    decoder = "avdec_h265"

    if use_hw_accel == "nvidia":
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
        "appsink name=sink emit-signals=True max-buffers=30 drop=False sync=false"
    )
    return Gst.parse_launch(pipeline_str)

def save_image_task(filepath, frame, semaphore):
    """图像保存任务"""
    try:
        if not cv2.imwrite(filepath, frame):
            print(f"Error: Failed to write image to {filepath}", file=sys.stderr)
    except Exception as e:
        print(f"Exception while writing image {filepath}: {e}", file=sys.stderr)
    finally:
        semaphore.release()

def on_new_sample(sink, user_data):
    """appsink回调函数"""
    output_dirs, topic_name, counters, writer_pool, semaphore, time_ranges = user_data

    sample = sink.emit('pull-sample')
    if not sample: return Gst.FlowReturn.OK

    buf, caps = sample.get_buffer(), sample.get_caps()
    height = caps.get_structure(0).get_value('height')
    width = caps.get_structure(0).get_value('width')

    result, mapinfo = buf.map(Gst.MapFlags.READ)
    if result:
        try:
            timestamp = buf.pts
            if timestamp == Gst.CLOCK_TIME_NONE:
                timestamp = int(time.time_ns())

            # 查找匹配的时间段
            matched_range = None
            for tr in time_ranges:
                if tr.contains(timestamp):
                    matched_range = tr
                    break

            if not matched_range:
                # 不在任何时间段内，跳过
                return Gst.FlowReturn.OK

            sec, nsec = timestamp // 1_000_000_000, timestamp % 1_000_000_000
            timestamp_sec = timestamp / 1e9
            dt = datetime.fromtimestamp(timestamp_sec)
            timestamp_str = dt.strftime("%Y%m%d_%H%M%S_%f")[:-3]

            filename = f"{timestamp_str}.jpg"
            topic_sanitized = topic_name.replace('/', '_').lstrip('_')
            output_dir = os.path.join(matched_range.output_dir, topic_sanitized)
            os.makedirs(output_dir, exist_ok=True)
            filepath = os.path.join(output_dir, filename)

            frame = np.ndarray((height, width, 3), buffer=mapinfo.data, dtype=np.uint8)

            semaphore.acquire()
            writer_pool.submit(save_image_task, filepath, frame.copy(), semaphore)

            counters['decoded'] += 1
        except Exception as e:
            print(f"\n[{topic_name}] Error in callback: {e}", file=sys.stderr)
        finally:
            buf.unmap(mapinfo)
    return Gst.FlowReturn.OK

def decode_worker(topic_name, data_queue, time_ranges, hw_accel_flag, shared_counters, camera_semaphore=None):
    """解码工作线程"""
    if camera_semaphore:
        camera_semaphore.acquire()
        print(f"[{topic_name}] Acquired camera slot. Starting worker...")

    try:
        topic_name_sanitized = topic_name.replace('/', '_').lstrip('_')
        print(f"[{topic_name}] Worker started for {len(time_ranges)} time ranges")

        main_loop = GLib.MainLoop()
        eos_received = threading.Event()

        if topic_name not in shared_counters:
            shared_counters[topic_name] = {'pushed': 0, 'decoded': 0}
        counters = shared_counters[topic_name]

        writer_pool = ThreadPoolExecutor(max_workers=4)
        semaphore = threading.BoundedSemaphore(value=50)

        pipeline = create_pipeline(topic_name_sanitized, hw_accel_flag)

        # 传递所有输出目录
        output_dirs = [tr.output_dir for tr in time_ranges]
        user_data_for_callback = (output_dirs, topic_name, counters, writer_pool, semaphore, time_ranges)
        sink = pipeline.get_by_name('sink')
        sink.connect("new-sample", on_new_sample, user_data_for_callback)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        def on_bus_message(bus, message):
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
        appsrc.set_property('max-bytes', 50 * 1024 * 1024)
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

        loop_thread.join(timeout=5.0)
        if loop_thread.is_alive():
            print(f"[{topic_name}] Main loop still running, forcing quit...")
            main_loop.quit()
            loop_thread.join(timeout=2.0)

        pipeline.set_state(Gst.State.NULL)
        writer_pool.shutdown(wait=True)

        print(f"\r[{topic_name}] Finalizing...")
        print(f"--- Summary for Topic: {topic_name} ---")
        print(f"  Frames pushed to decoder: {counters['pushed']}")
        print(f"  Frames successfully decoded: {counters['decoded']}")
        print("--------------------------------------------------")
    finally:
        if camera_semaphore:
            camera_semaphore.release()
            print(f"[{topic_name}] Released camera slot.")

def get_all_bags(input_path):
    """获取所有bag目录"""
    bag_root = os.path.abspath(input_path)
    bag_paths = []

    meta_file = os.path.join(bag_root, "metadata.yaml")
    if os.path.exists(meta_file):
        print(f"✅ 输入路径是单个bag目录：{bag_root}")
        bag_paths.append(Path(bag_root))
        return bag_paths

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

def main():
    parser = argparse.ArgumentParser(
        description="批量解码H.265数据，一次处理多个时间段",
        formatter_class=argparse.RawTextHelpFormatter
    )

    parser.add_argument("--bag", required=True, help="ROS2 bag目录")
    parser.add_argument("--time-ranges", required=True, help="时间段配置，格式：start1:end1:out1,start2:end2:out2")
    parser.add_argument("--hwaccel", type=str, default="none", choices=['none', 'nvidia', 'vaapi'],
                        help="硬件加速方法")
    parser.add_argument("--max-concurrent-cameras", type=int, default=3,
                        help="最大并发相机数（默认3）")
    args = parser.parse_args()

    # 解析时间段
    time_ranges = []
    for tr_str in args.time_ranges.split(','):
        parts = tr_str.split(':')
        if len(parts) != 3:
            print(f"错误：时间段格式错误：{tr_str}")
            sys.exit(1)
        time_ranges.append(TimeRange(parts[0], parts[1], parts[2]))

    print(f"📋 将处理 {len(time_ranges)} 个时间段")
    for i, tr in enumerate(time_ranges, 1):
        print(f"  {i}. {tr.start_hhmmss} → {tr.end_hhmmss} → {tr.output_dir}")

    threads, data_queues = {}, {}
    shared_counters = {}
    camera_semaphore = threading.Semaphore(args.max_concurrent_cameras)

    monitor = ProgressMonitor(shared_counters, interval=2.0)
    monitor.start()

    print(f"\nStarting bag file processing with hardware acceleration: {args.hwaccel}")
    bag_paths = get_all_bags(args.bag)

    try:
        with AnyReader(bag_paths) as reader:
            for connection, timestamp, rawdata in reader.messages():
                if connection.msgtype != 'sensor_msgs/msg/Image': continue
                topic_name = connection.topic
                if topic_name not in ALL_CAMERA_H265_TOPICS:
                    continue

                if topic_name not in threads:
                    print(f"\nDiscovered new topic: {topic_name}. Starting worker thread.")
                    q = queue.Queue(maxsize=1000)
                    data_queues[topic_name] = q
                    thread = threading.Thread(target=decode_worker,
                                            args=(topic_name, q, time_ranges, args.hwaccel, shared_counters, camera_semaphore))
                    threads[topic_name] = thread
                    thread.start()

                msg = reader.deserialize(rawdata, connection.msgtype)
                data_queues[topic_name].put((timestamp, msg.data.tobytes()))

    except Exception as e:
        print(f"\nAn error occurred: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
    finally:
        print("\nEnd of all bag files reached. Signaling worker threads to finalize...")
        for q in data_queues.values(): q.put(None)

        monitor.stop()
        monitor.join()

        for t in threads.values(): t.join()

        print(f"\nAll decoding threads have finished.")

if __name__ == '__main__':
    main()
