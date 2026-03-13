import os
import json
import zipfile
import re
import shutil
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import platform

try:
    from rosbags.highlevel import AnyReader
except ImportError:
    AnyReader = None

class FolderCompressor:
    def __init__(self, root_dir, bag_path=None, bag_date=None):
        self.root_dir = Path(root_dir)
        self.bag_path = bag_path  # ROS2 bag 路径，用于获取时间戳
        self.bag_date = bag_date  # 直接指定的日期（YYYYMMDD），优先于 bag_path
        # 配置项 - 请根据实际情况修改
        self.required_json_files = ['sensor_config_combined_latest.json', 'ins.json', 'sample.json']  # 必需的JSON文件列表
        self.folder_groups = [
            ['camera_cam_3M_front/scale_0.20','camera_cam_3M_rear/scale_0.20','camera_cam_3M_right/scale_0.20','camera_cam_3M_left/scale_0.20'],     # 这些文件夹内的文件数量必须相同
            ['iv_points_front_left/pcd_binary', 'iv_points_front_right/pcd_binary', 'iv_points_rear_left/pcd_binary','iv_points_front_mid/pcd_binary','iv_points_rear_right/pcd_binary', 'iv_points_left_mid/pcd_binary', 'iv_points_right_mid/pcd_binary'],  # 这些文件夹内的文件数量必须相同
            ['combined_scales']                   # 单个文件夹也要检查不为空
        ]
        self.time_sensitive_folders = ['camera_cam_3M_front/scale_0.20','camera_cam_3M_rear/scale_0.20','camera_cam_3M_right/scale_0.20','camera_cam_3M_left/scale_0.20','iv_points_front_left/pcd_binary', 'iv_points_front_right/pcd_binary', 'iv_points_rear_left/pcd_binary','iv_points_front_mid/pcd_binary','iv_points_rear_right/pcd_binary', 'iv_points_left_mid/pcd_binary', 'iv_points_right_mid/pcd_binary']  # 包含时间信息的文件夹
        self.min_zip_size_gb = 4  # 最小压缩包大小（GB）
        self.min_zip_size_bytes = self.min_zip_size_gb * 1024 * 1024 * 1024  # 转换为字节
        self.keep_folder_name = "undistorted"  # 需要保留的文件夹名
        self.required_free_space_gb = 100  # 所需最小剩余空间（GB）
        self.required_free_space_bytes = self.required_free_space_gb * 1024 * 1024 * 1024  # 转换为字节
    
    def get_free_disk_space(self, path):
        """获取指定路径所在磁盘的剩余空间（字节）"""
        try:
            if platform.system() == 'Windows':
                # Windows系统
                import ctypes
                free_bytes = ctypes.c_ulonglong(0)
                total_bytes = ctypes.c_ulonglong(0)
                # 获取磁盘空间信息
                ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    ctypes.c_wchar_p(str(path)),
                    None,
                    ctypes.pointer(total_bytes),
                    ctypes.pointer(free_bytes)
                )
                return free_bytes.value
            else:
                # Linux/macOS系统
                statvfs = os.statvfs(str(path))
                # 计算剩余空间：块大小 * 可用块数
                return statvfs.f_frsize * statvfs.f_bavail
        except Exception as e:
            print(f"  [FAIL] 获取磁盘空间失败: {e}")
            return -1
    
    def check_disk_space(self):
        """检查目标目录的剩余空间是否满足要求"""
        free_space = self.get_free_disk_space(self.root_dir)

        if free_space < 0:
            print(f"  [FAIL] 无法获取磁盘空间信息")
            return False

        free_space_gb = free_space / (1024 * 1024 * 1024)

        if free_space >= self.required_free_space_bytes:
            return True
        else:
            print(f"  [FAIL] 磁盘空间不足: {free_space_gb:.1f}GB < {self.required_free_space_gb}GB")
            return False
    
    def get_undistorted_folder(self, target_folder_path):
        """获取undistorted文件夹路径，不存在则返回None"""
        if target_folder_path.name == self.keep_folder_name:
            if target_folder_path.exists() and target_folder_path.is_dir():
                return target_folder_path
            else:
                print(f"  [FAIL] 文件夹不存在: {target_folder_path}")
                return None

        undistorted_folder = target_folder_path / self.keep_folder_name
        if not undistorted_folder.exists() or not undistorted_folder.is_dir():
            print(f"  [FAIL] 未找到 '{self.keep_folder_name}' 文件夹: {undistorted_folder}")
            return None
        return undistorted_folder
    
    def find_json_files(self, undistorted_folder):
        """在undistorted文件夹内递归查找JSON文件"""
        found_jsons = {}
        for json_file in self.required_json_files:
            # 递归查找JSON文件（在undistorted目录下）
            for file_path in undistorted_folder.rglob(f"*{json_file}"):
                if file_path.name == json_file:
                    found_jsons[json_file] = file_path
                    break  # 找到第一个匹配的就停止
        
        return found_jsons
    
    def check_json_files(self, target_folder_path):
        """检查undistorted文件夹内的JSON文件是否存在且不为空"""
        # 先获取undistorted文件夹
        undistorted_folder = self.get_undistorted_folder(target_folder_path)
        if not undistorted_folder:
            return False
        
        print(f"  检查JSON文件...")
        found_jsons = self.find_json_files(undistorted_folder)
        
        for json_file in self.required_json_files:
            if json_file not in found_jsons:
                print(f"  [FAIL] 缺少JSON文件: {json_file}")
                return False
            
            json_path = found_jsons[json_file]
            # 检查JSON文件是否为空
            if json_path.stat().st_size == 0:
                print(f"  [FAIL] JSON文件为空: {json_file}")
                return False
            
        
        print(f"  [OK] JSON文件检查通过")
        return True
    
    def check_folder_structure(self, target_folder_path):
        """检查undistorted文件夹内的结构和文件数量"""
        # 先获取undistorted文件夹
        undistorted_folder = self.get_undistorted_folder(target_folder_path)
        if not undistorted_folder:
            return False
        
        print(f"  检查文件夹结构...")
        
        # 首先收集实际存在的文件夹
        existing_folders = {}
        for folder_group in self.folder_groups:
            existing_in_group = []
            for folder_path_str in folder_group:
                # 处理嵌套文件夹路径（相对于undistorted文件夹）
                folder_path = undistorted_folder / folder_path_str
                if folder_path.exists() and folder_path.is_dir():
                    existing_in_group.append(folder_path_str)
                else:
                    print(f"  [WARN] 文件夹不存在(跳过): {folder_path_str}")
            
            if existing_in_group:
                existing_folders[tuple(folder_group)] = existing_in_group
        
        if not existing_folders:
            print(f"  [FAIL] 没有找到任何配置的文件夹")
            return False
        
        # 检查文件夹文件数量（仅检查实际存在的文件夹）
        # 允许一定的文件数量差异（例如5%），因为不同传感器可能有轻微的帧率差异
        tolerance_percent = 0.05  # 5% tolerance
        
        for folder_group, existing_in_group in existing_folders.items():
            if len(existing_in_group) > 1:
                file_counts = []
                for folder_path_str in existing_in_group:
                    folder_path = undistorted_folder / folder_path_str
                    file_count = len([f for f in folder_path.iterdir() if f.is_file()])
                    file_counts.append(file_count)
                
                # 检查同一组内文件夹文件数量是否在合理范围内
                if file_counts:
                    min_count = min(file_counts)
                    max_count = max(file_counts)
                    avg_count = sum(file_counts) / len(file_counts)
                    
                    # 计算最大偏差百分比
                    if avg_count > 0:
                        max_deviation = max(abs(max_count - avg_count), abs(min_count - avg_count)) / avg_count
                        
                        if max_deviation > tolerance_percent:
                            print(f"  [WARN] 文件夹组文件数量偏差 {max_deviation*100:.1f}%: {dict(zip(existing_in_group, file_counts))}")
                        else:
                            print(f"  [OK] 文件夹组数量一致: {dict(zip(existing_in_group, file_counts))}")
            
            elif len(existing_in_group) == 1:  # 单个文件夹检查是否为空
                folder_path_str = existing_in_group[0]
                folder_path = undistorted_folder / folder_path_str
                file_count = len([f for f in folder_path.iterdir() if f.is_file()])
                if file_count == 0:
                    print(f"  [FAIL] 文件夹为空: {folder_path_str}")
                    return False
        
        print(f"  [OK] 文件夹结构检查通过 ({sum(len(v) for v in existing_folders.values())} 个文件夹)")
        return True
    
    def extract_time_from_filename(self, filename):
        """从文件名中提取时分秒字符串，返回格式为 HH:MM:SS，失败返回 None"""
        # 优先匹配：文件名开头的 YYYYMMDD_HHMMSS 格式
        combined_pattern = r'^(\d{8})_(\d{6})'
        match = re.search(combined_pattern, str(filename))
        if match:
            hms_str = match.group(2)  # 提取 6 位时分秒（HHMMSS）
            try:
                # 验证是否为有效时分秒
                datetime.strptime(hms_str, '%H%M%S')
                return f"{hms_str[:2]}:{hms_str[2:4]}:{hms_str[4:6]}"
            except ValueError:
                print(f"  [WARN] 文件名时间格式无效: {filename} ({hms_str})")
                return None
        
        # 备用匹配：仅匹配 6 位数字（HHMMSS）
        hms_pattern = r'(\d{6})'
        match = re.search(hms_pattern, str(filename))
        if match:
            hms_str = match.group(1)
            try:
                datetime.strptime(hms_str, '%H%M%S')
                return f"{hms_str[:2]}:{hms_str[2:4]}:{hms_str[4:6]}"
            except ValueError:
                print(f"  [WARN] 文件名时间格式无效: {filename} ({hms_str})")
                return None
        
        return None

    
    def parse_folder_time_range(self, folder_name):
        """从文件夹名解析时间范围（支持 HHMMSS_HHMMSS 格式）"""
        pattern = r'^(\d{6})_(\d{6})$'
        match = re.search(pattern, folder_name)
        if not match:
            return None, None
        
        start_str, end_str = match.groups()
        try:
            # 解析为时间对象并格式化为 HH:MM:SS
            start_time = datetime.strptime(start_str, '%H%M%S').strftime('%H:%M:%S')
            end_time = datetime.strptime(end_str, '%H%M%S').strftime('%H:%M:%S')
            return start_time, end_time
        except ValueError as e:
            print(f"  [WARN] 无法解析文件夹时间范围: {folder_name}: {e}")
            return None, None
    
    def check_time_consistency(self, target_folder_path, folder_name):
        """检查undistorted文件夹内的时间一致性"""
        # 先获取undistorted文件夹
        undistorted_folder = self.get_undistorted_folder(target_folder_path)
        if not undistorted_folder:
            return False
        
        folder_start, folder_end = self.parse_folder_time_range(folder_name)
        if not folder_start or not folder_end:
            print(f"  [FAIL] 无法解析文件夹时间范围: {folder_name}")
            return False

        print(f"  检查时间一致性 ({folder_start} - {folder_end})...")
        all_time_folders_valid = True
        time_tolerance = timedelta(seconds=3)
        fmt = "%H:%M:%S"
        
        for time_folder_path_str in self.time_sensitive_folders:
            # 时间敏感文件夹路径相对于undistorted文件夹
            time_folder_path = undistorted_folder / time_folder_path_str
            if not time_folder_path.exists():
                print(f"  [WARN] 时间敏感文件夹不存在: {time_folder_path_str}")
                continue
            
            # 获取文件夹内所有非json、非npy文件并按文件名排序
            files = sorted([f for f in time_folder_path.iterdir() if f.is_file() 
                          and not (f.name.lower().endswith('.json') or f.name.lower().endswith('.npy'))])
            if not files:
                print(f"  [FAIL] 时间敏感文件夹为空: {time_folder_path_str}")
                all_time_folders_valid = False
                continue
            
            # 检查第一个文件的时间
            first_file_time = self.extract_time_from_filename(files[0].name)
            if not first_file_time:
                print(f"  [FAIL] 无法从文件提取时间: {files[0].name}")
                all_time_folders_valid = False
                continue
            
            # 检查最后一个文件的时间
            last_file_time = self.extract_time_from_filename(files[-1].name)
            if not last_file_time:
                print(f"  [FAIL] 无法从文件提取时间: {files[-1].name}")
                all_time_folders_valid = False
                continue
            
            # 转换为datetime对象进行比较
            folder_start_dt = datetime.strptime(folder_start, fmt)
            folder_end_dt = datetime.strptime(folder_end, fmt)
            file_start_dt = datetime.strptime(first_file_time, fmt)
            file_end_dt = datetime.strptime(last_file_time, fmt)
            
            # 检查时间差
            if abs(file_start_dt - folder_start_dt) > time_tolerance:
                print(f"  [FAIL] 起始时间不匹配: {time_folder_path_str}")
                print(f"     文件夹: {folder_start}, 文件: {first_file_time}")
                all_time_folders_valid = False
            
            if abs(file_end_dt - folder_end_dt) > time_tolerance:
                print(f"  [FAIL] 结束时间不匹配: {time_folder_path_str}")
                print(f"     文件夹: {folder_end}, 文件: {last_file_time}")
                all_time_folders_valid = False
        
        if all_time_folders_valid:
            print(f"  [OK] 时间一致性检查通过")
        return all_time_folders_valid
    
    def format_file_size(self, size_bytes):
        """格式化文件大小（B/KB/MB/GB）"""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} TB"
    
    def clean_folder_before_compress(self, target_folder_path):
        """清理文件夹：直接删除除指定保留文件夹外的所有内容（无确认）"""
        print(f"  清理文件夹: {target_folder_path.name}")
        print(f"  仅保留 '{self.keep_folder_name}' 文件夹")

        keep_folder = target_folder_path / self.keep_folder_name
        if not keep_folder.exists() or not keep_folder.is_dir():
            print(f"  [WARN] 未找到 '{self.keep_folder_name}' 文件夹")
        
        # 列出所有要删除的内容（不包括保留文件夹）
        items_to_delete = []
        for item in target_folder_path.iterdir():
            if item.name != self.keep_folder_name:
                items_to_delete.append(item)
        
        if not items_to_delete:
            print(f"  [OK] 无需清理")
            return True

        deleted_count = 0
        failed_items = []
        for item in items_to_delete:
            try:
                if item.is_file():
                    item.unlink()  # 删除文件
                else:
                    shutil.rmtree(item)  # 删除文件夹及其内容
                deleted_count += 1
            except Exception as e:
                failed_items.append(f"{item.name}: {str(e)}")
        
        # 输出删除结果
        print(f"  [OK] 清理完成: 删除 {deleted_count} 项")
        if failed_items:
            print(f"  [WARN] {len(failed_items)} 项删除失败")
        
        # 最后检查保留文件夹状态
        if keep_folder.exists() and keep_folder.is_dir():
            keep_folder_size = sum(f.stat().st_size for f in keep_folder.rglob('*') if f.is_file())
            if keep_folder_size == 0:
                print(f"  [WARN] 保留的 '{self.keep_folder_name}' 文件夹为空")
            return True
        else:
            print(f"  [FAIL] 保留的 '{self.keep_folder_name}' 文件夹不存在或已被删除")
            return False
    
    def compress_folder(self, target_folder_path, output_path=None):
        """压缩文件夹，并检查压缩包大小"""
        # 获取日期：优先使用直接指定的日期，其次从 bag 获取，最后使用当前日期
        if self.bag_date:
            bag_date = self.bag_date
            print(f"  Bag数据日期: {bag_date} (直接指定)")
        elif self.bag_path and AnyReader:
            try:
                with AnyReader([Path(self.bag_path)]) as reader:
                    bag_start_time_ns = reader.start_time
                    bag_datetime = datetime.fromtimestamp(bag_start_time_ns / 1e9)
                    bag_date = bag_datetime.strftime('%Y%m%d')
                    print(f"  Bag数据日期: {bag_date} ({bag_datetime.strftime('%Y-%m-%d %H:%M:%S')})")
            except Exception as e:
                print(f"  [WARN] 无法从bag获取时间戳，使用当前日期: {e}")
                bag_date = datetime.now().strftime('%Y%m%d')
        else:
            if not self.bag_path:
                print(f"  [WARN] 未提供bag路径，使用当前日期")
            bag_date = datetime.now().strftime('%Y%m%d')

        if output_path:
            zip_path = Path(output_path)
            # 如果文件名包含 PLACEHOLDER，替换为实际日期
            if 'PLACEHOLDER' in zip_path.name:
                # 提取时间段部分（HHMMSS_HHMMSS）
                import re
                time_pattern = r'(\d{6}_\d{6})'
                match = re.search(time_pattern, zip_path.name)
                if match:
                    time_range = match.group(1)
                    new_filename = f"{bag_date}_{time_range}.zip"
                    zip_path = zip_path.parent / new_filename
                    print(f"  压缩文件名: {new_filename}")
            zip_filename = zip_path.name
        else:
            # 压缩包保存到root_dir下，添加日期前缀
            zip_filename = f"{bag_date}_{target_folder_path.name}.zip"
            zip_path = self.root_dir / zip_filename

        # 如果压缩包已存在，直接覆盖（无需确认）
        if zip_path.exists():
            print(f"  [WARN] 压缩包 {zip_filename} 已存在，将覆盖")
            zip_path.unlink()  # 删除已存在的压缩包

        try:
            print(f"  压缩中...")
            skipped_files = 0
            compressed_files = 0

            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(target_folder_path):
                    for file in files:
                        file_path = Path(root) / file

                        # 检查文件是否存在（可能被cleanup删除）
                        if not file_path.exists():
                            skipped_files += 1
                            continue

                        try:
                            # 在ZIP文件中保持相对路径（相对于root_dir）
                            try:
                                arcname = file_path.relative_to(self.root_dir)
                            except ValueError:
                                # 如果不在root_dir下（例如单文件夹模式），则相对于target_folder_path的父目录
                                arcname = file_path.relative_to(target_folder_path.parent)

                            zipf.write(file_path, arcname)
                            compressed_files += 1
                        except FileNotFoundError:
                            # 文件在压缩过程中被删除
                            skipped_files += 1
                            continue
                        except Exception as e:
                            print(f"  [WARN] 跳过文件 {file_path.name}: {str(e)}")
                            skipped_files += 1
                            continue

            # 检查压缩包大小
            zip_size_bytes = zip_path.stat().st_size
            zip_size_formatted = self.format_file_size(zip_size_bytes)

            print(f"  [OK] 压缩完成: {zip_filename} ({zip_size_formatted})")
            print(f"  路径: {zip_path}")

            if zip_size_bytes < self.min_zip_size_bytes:
                print(f"  [WARN] 压缩包 < {self.min_zip_size_gb}GB，可能数据不完整")

            return str(zip_path)  # 返回实际生成的压缩包路径
        except Exception as e:
            print(f"  [FAIL] 压缩失败: {e}")
            # 如果压缩失败且文件已创建，删除不完整的压缩包
            if zip_path.exists():
                zip_path.unlink()
            return None
    
    def is_time_format_folder(self, folder_name):
        """判断文件夹名是否为时间格式（HHMMSS_HHMMSS）"""
        pattern = r'^\d{6}_\d{6}$'
        return bool(re.match(pattern, folder_name))
    
    def process_single_undistorted_folder(self, undistorted_path, compress_path):
        """处理单个undistorted文件夹（Pipeline模式），返回实际生成的压缩包路径"""
        target_folder = Path(undistorted_path)
        print(f"\n  处理文件夹: {target_folder}")

        compress_dir = Path(compress_path).parent
        if not compress_dir.exists():
            compress_dir.mkdir(parents=True, exist_ok=True)

        free_space = self.get_free_disk_space(compress_dir)
        if free_space >= 0:
            free_space_gb = free_space / (1024 * 1024 * 1024)
            if free_space < self.required_free_space_bytes:
                print(f"  [FAIL] 磁盘空间不足: {free_space_gb:.1f}GB < {self.required_free_space_gb}GB")
                return None

        # 执行检查
        checks_passed = True

        # 检查1: JSON文件
        if not self.check_json_files(target_folder):
            checks_passed = False

        # 检查2: 文件夹结构
        if not self.check_folder_structure(target_folder):
            checks_passed = False

        if checks_passed:
            print(f" 所有检查通过，开始压缩...")
            # 注意：Pipeline模式下不执行 clean_folder_before_compress，由Pipeline脚本负责清理

            actual_compress_path = self.compress_folder(target_folder, output_path=compress_path)
            if actual_compress_path:
                print(f"  [OK] 压缩成功: {actual_compress_path}")
                return actual_compress_path
        else:
            print(f"  [FAIL] 检查未通过，跳过压缩")

        return None

    def process_all_target_folders(self):
        """处理root_dir下所有时间格式的子文件夹"""
        # 验证根目录是否存在
        if not self.root_dir.exists():
            print(f"  [FAIL] 根目录不存在: {self.root_dir}")
            return
        
        # 查找所有时间格式的子文件夹（直接子目录）并按名称排序
        target_folders = sorted([f for f in self.root_dir.iterdir() 
                        if f.is_dir() and self.is_time_format_folder(f.name)])
        
        if not target_folders:
            print(f"在 {self.root_dir} 中未找到符合格式的时间文件夹（需为 HHMMSS_HHMMSS 格式）")
            return
        
        print(f"找到 {len(target_folders)} 个需要处理的时间文件夹")
        
        successful_compressions = 0
        
        for idx, target_folder in enumerate(target_folders, 1):
            print(f"\n  处理 [{idx}/{len(target_folders)}]: {target_folder.name}")
            
            # 处理每个文件夹前先检查磁盘空间
            if not self.check_disk_space():
                # 空间不足，直接终止程序
                print(f"\n  [FAIL] 磁盘空间不足，终止! 已处理 {successful_compressions}/{idx-1}")
                return
            
            # 执行所有检查（均基于undistorted目录）
            checks_passed = True
            
            # 检查1: JSON文件（undistorted目录下）
            if not self.check_json_files(target_folder):
                checks_passed = False
            
            # 检查2: 文件夹结构（undistorted目录下）
            if not self.check_folder_structure(target_folder):
                checks_passed = False
            
            # 检查3: 时间一致性（undistorted目录下）
            # if not self.check_time_consistency(target_folder, target_folder.name):
            #     checks_passed = False
            
            # 如果所有检查通过，执行清理然后压缩
            if checks_passed:
                print(f" 所有检查通过，开始清理文件夹...")
                # 清理文件夹（无确认）
                if not self.clean_folder_before_compress(target_folder):
                    print(f"  [FAIL] 清理失败，跳过压缩")
                    continue
                
                # 清理成功后进行压缩
                if self.compress_folder(target_folder):
                    successful_compressions += 1
            else:
                print(f"  [FAIL] 检查未通过，跳过压缩")
            print("-" * 50)

        print(f"\n  处理完成: 成功压缩 {successful_compressions}/{len(target_folders)}")

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="文件夹批量压缩工具")
    parser.add_argument("--undistorted-path", type=str, help="单个undistorted文件夹路径")
    parser.add_argument("--compress-path", type=str, help="输出压缩包路径")
    parser.add_argument("--compress-format", type=str, default="zip", help="压缩格式")
    parser.add_argument("--period", type=str, help="时间段标识")
    parser.add_argument("--bag-path", type=str, help="ROS2 bag 路径，用于获取时间戳作为压缩文件名日期")
    parser.add_argument("--bag-date", type=str, help="直接指定bag数据日期（YYYYMMDD格式），优先于--bag-path")

    args, unknown = parser.parse_known_args()

    if args.undistorted_path and args.compress_path:
        # Pipeline模式
        print("  Pipeline 单文件夹处理模式")
        # root_dir 设置为 undistorted_path 的父目录，以便计算相对路径
        root_dir = Path(args.undistorted_path).parent
        compressor = FolderCompressor(root_dir, args.bag_path, bag_date=args.bag_date)
        actual_compress_path = compressor.process_single_undistorted_folder(args.undistorted_path, args.compress_path)

        # 输出实际生成的压缩包路径，供 pipline.py 读取
        if actual_compress_path:
            print(f"\nCOMPRESS_SUCCESS: {actual_compress_path}")
        else:
            print(f"\nCOMPRESS_FAILED")
        return

    print("  文件夹批量压缩工具 (基于undistorted目录)")
    print("=" * 60)
    print("  注意: 会自动删除目标文件夹中除 'undistorted' 外的所有内容")
    print(f"  要求: 目标目录剩余空间 > 50 GB")
    print("=" * 60)

    # 根目录：包含所有时间格式子文件夹的目录
    root_dir = "/media/zgw/T7/0209out/"

    # 创建压缩器实例并处理
    compressor = FolderCompressor(root_dir, args.bag_path)
    compressor.process_all_target_folders()

if __name__ == "__main__":
    main()
