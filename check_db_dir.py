import os
import logging
import subprocess
import sys

# 定义日志保存目录和处理记录保存目录
log_dir = "/tmp/log"  # 日志保存目录
record_dir = "/tmp/record"  # 处理记录保存目录
config_dir = "/config"  # 配置文件目录
torrent_dir = "/Torrent"  # 种子文件目录
downloads_dir = "/Downloads"  # 下载文件目录
media_dir = "/Media"  # 媒体库根目录
movie_dir = "/Media/Movie"  # 电影目录
episodes_dir = "/Media/Episodes"  # 电视剧目录

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s', encoding='utf-8')

def ensure_directory_exists(directory):
    """确保指定目录存在，如果不存在则创建"""
    if not os.path.exists(directory):
        try:
            os.makedirs(directory)
            logging.info(f"目录已创建: {directory}")
        except Exception as e:
            logging.error(f"无法创建目录 {directory}: {e}")
            sys.exit(1)
    else:
        logging.info(f"目录已存在: {directory}")

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
        logging.info("数据库已检查完成。")
        
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
        sys.exit(1)

if __name__ == "__main__":
    # 确保必要的目录存在
    ensure_directory_exists(log_dir)
    ensure_directory_exists(record_dir)
    ensure_directory_exists(config_dir)
    ensure_directory_exists(torrent_dir)
    ensure_directory_exists(downloads_dir)
    ensure_directory_exists(media_dir)
    ensure_directory_exists(movie_dir)
    ensure_directory_exists(episodes_dir)
    # 执行数据库检查
    check_database()