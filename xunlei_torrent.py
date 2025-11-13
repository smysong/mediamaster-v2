import logging
import sqlite3
import os
import time
import subprocess

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/xunlei_torrent.log", mode='w'),
        logging.StreamHandler()
    ]
)

def load_config(db_path='/config/data.db'):
    """从数据库中加载配置"""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT OPTION, VALUE FROM CONFIG')
            config_items = cursor.fetchall()
            config = {option: value for option, value in config_items}

        logging.debug("加载配置文件成功")
        return config
    except sqlite3.Error as e:
        logging.error(f"数据库加载配置错误: {e}")
        exit(0)

def run_xunlei_script(torrent_file):
    """运行 xunlei.py 处理 .torrent 文件"""
    logging.info(f"发现种子文件：{torrent_file}")
    logging.info(f"启动：迅雷-添加下载任务")
    with open(os.devnull, 'w') as devnull:
        process = subprocess.Popen(['python', 'xunlei.py'], stdout=devnull, stderr=devnull)
    # 等待子进程完成
    process.wait()

def monitor_torrent_directory(directory):
    """持续监听 Torrent 目录中的 .torrent 文件"""
    processed_files = set()

    while True:
        try:
            # 确保 directory 是合法字符串
            if not isinstance(directory, str) or not os.path.isdir(directory):
                raise ValueError(f"无效的目录路径: {directory}")

            torrent_files = [f for f in os.listdir(directory) if f.endswith('.torrent')]

            if not torrent_files:
                logging.debug("未发现种子文件，持续监听中...")
                time.sleep(10)
                continue

            # 处理新发现的 .torrent 文件
            new_files = set(torrent_files) - processed_files
            if new_files:
                for file in new_files:
                    full_path = os.path.join(directory, file)
                    run_xunlei_script(full_path)
                    processed_files.add(file)
            else:
                logging.debug("没有新的种子文件文件需要处理")
                time.sleep(10)

        except Exception as e:
            logging.error(f"监听过程中发生错误: {e}")
            time.sleep(10)

if __name__ == "__main__":
    config = load_config()
    download_type = config.get("download_type")

    if download_type != "xunlei":
        logging.info(f"当前下载器为 {download_type}，无需运行：迅雷-种子监听服务")
        exit(0)

    torrent_dir = '/Torrent'

    # 检查目录是否存在
    if not os.path.isdir(torrent_dir):
        logging.error(f"指定的 Torrent 目录 '{torrent_dir}' 不存在或不可读！")
        exit(1)

    # 开始监听 /Torrent 目录
    monitor_torrent_directory(torrent_dir)