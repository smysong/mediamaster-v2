import os
import logging
import subprocess
import sys
import shutil
import time
from datetime import datetime, timedelta

# 定义日志保存目录和处理记录保存目录
log_dir = "/tmp/log"  # 日志保存目录
indexer = "/tmp/index"  # 资源索引保存目录
config_dir = "/config"  # 配置文件目录
uploads_dir = "/app/static/uploads"  # 上传文件目录
avatars_dir = "/config/avatars"  # 配置文件目录
torrent_dir = "/Torrent"  # 种子文件目录
downloads_dir = "/Downloads"  # 下载文件目录
media_dir = "/Media"  # 媒体库根目录
movie_dir = "/Media/Movie"  # 电影目录
episodes_dir = "/Media/Episodes"  # 电视剧目录
chrome_cache_dir = "/app/ChromeCache"  # Chrome 缓存目录

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/check_db_dir.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def ensure_directory_exists(directory):
    """确保指定目录存在，如果不存在则创建"""
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
            logging.info(f"目录已创建: {directory}")
        except Exception as e:
            logging.error(f"无法创建目录 {directory}: {e}")
            sys.exit(0)
    else:
        logging.info(f"目录已存在: {directory}")

def clear_chrome_cache():
    """检查Chrome缓存目录是否为空，如果不为空则清空缓存目录"""
    if not os.path.exists(chrome_cache_dir):
        logging.info(f"Chrome缓存目录不存在: {chrome_cache_dir}")
        return
    
    try:
        # 检查目录是否为空
        if os.listdir(chrome_cache_dir):
            logging.info(f"正在清理Chrome缓存目录: {chrome_cache_dir}")
            # 清空目录中的所有内容
            for filename in os.listdir(chrome_cache_dir):
                file_path = os.path.join(chrome_cache_dir, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            logging.info(f"Chrome缓存目录已清空: {chrome_cache_dir}")
        else:
            logging.info(f"Chrome缓存目录为空: {chrome_cache_dir}")
    except Exception as e:
        logging.error(f"清空Chrome缓存目录失败: {e}")

def clear_old_logs(log_directory, days=3):
    """清理超过指定天数的日志文件"""
    if not os.path.exists(log_directory):
        logging.info(f"日志目录不存在: {log_directory}")
        return
    
    try:
        # 计算过期时间点
        cutoff_time = time.time() - (days * 24 * 60 * 60)
        cleared_files = 0
        
        # 遍历日志目录中的所有文件
        for filename in os.listdir(log_directory):
            file_path = os.path.join(log_directory, filename)
            
            # 检查是否为文件（而非目录）
            if os.path.isfile(file_path):
                # 获取文件最后修改时间
                file_modified_time = os.path.getmtime(file_path)
                
                # 如果文件修改时间早于过期时间，则删除文件
                if file_modified_time < cutoff_time:
                    os.remove(file_path)
                    cleared_files += 1
                    logging.info(f"已删除过期日志文件: {file_path}")
        
        logging.info(f"清理完成，共删除 {cleared_files} 个过期日志文件")
    except Exception as e:
        logging.error(f"清理过期日志文件失败: {e}")

def clear_torrent_directory():
    """检查torrent目录是否为空，如果不为空则清空torrent目录"""
    if not os.path.exists(torrent_dir):
        logging.info(f"Torrent目录不存在: {torrent_dir}")
        return
    
    try:
        # 检查目录是否为空
        if os.listdir(torrent_dir):
            logging.info(f"正在清理Torrent目录: {torrent_dir}")
            # 清空目录中的所有内容
            for filename in os.listdir(torrent_dir):
                file_path = os.path.join(torrent_dir, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            logging.info(f"Torrent目录已清空: {torrent_dir}")
        else:
            logging.info(f"Torrent目录为空: {torrent_dir}")
    except Exception as e:
        logging.error(f"清空Torrent目录失败: {e}")

def get_status_code_from_log(log_file):
    """从日志文件中提取状态码"""
    try:
        with open(log_file, 'r') as file:
            lines = file.readlines()
            if lines:
                last_line = lines[-1]
                if "数据库初始化状态码: " in last_line:
                    return int(last_line.split(":")[-1].strip())
        logging.warning("无法从日志文件中读取状态码，使用默认值 0。")
        return 0
    except Exception as e:
        logging.error(f"读取日志文件失败: {e}，使用默认值 0。")
        return 0

def check_database():
    """调用 database_manager.py 检查数据库"""
    try:
        # 确保日志目录存在
        ensure_directory_exists(log_dir)
        
        # 调用 database_manager.py 脚本
        subprocess.run(['python', 'database_manager.py'], check=True, capture_output=True, text=True)
        logging.info("数据库检查完成。")
        
        # 获取状态码并处理
        status_code = get_status_code_from_log(os.path.join(log_dir, "database_manager.log"))
        if status_code == 0:
            logging.info("系统配置是默认数据，请登录WEB管理修改配置，修改完成后重启容器。")
            logging.info("默认用户名：admin  默认密码：password  默认端口：8888")
        elif status_code == 1:
            logging.debug("系统配置非默认数据。")
        else:
            logging.warning(f"未知的状态码: {status_code}")
    except subprocess.CalledProcessError as e:
        logging.error(f"数据库管理程序执行失败，退出程序。错误信息: {e.stderr}")
        sys.exit(0)

if __name__ == "__main__":
    # 确保必要的目录存在
    ensure_directory_exists(log_dir)
    ensure_directory_exists(indexer)
    ensure_directory_exists(config_dir)
    ensure_directory_exists(uploads_dir)
    ensure_directory_exists(avatars_dir)
    ensure_directory_exists(torrent_dir)
    ensure_directory_exists(downloads_dir)
    ensure_directory_exists(media_dir)
    ensure_directory_exists(movie_dir)
    ensure_directory_exists(episodes_dir)
    logging.info("所有目录检查完成。")
    
    # 清理过期日志文件（超过3天）
    clear_old_logs(log_dir, days=3)
    
    # 清理 torrent 目录
    clear_torrent_directory()
    
    # 清理 Chrome 缓存目录
    clear_chrome_cache()
    
    # 执行数据库检查
    check_database()