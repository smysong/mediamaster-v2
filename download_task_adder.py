import sqlite3
import logging
import os
import sys
from transmission_rpc import Client as TransmissionClient
from qbittorrentapi import Client as QBittorrentClient, LoginFailed

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/download_task_adder.log", mode='w'),
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
        sys.exit(1)

def add_task_to_transmission(client, torrent_path, label):
    try:
        with open(torrent_path, 'rb') as f:
            torrent_data = f.read()
        result = client.add_torrent(torrent_data)
        # 设置标签
        if label:
            try:
                client.change_torrent(result.id, labels=[label])
            except Exception:
                pass
        # 设置分类
        try:
            client.change_torrent(result.id, group="mediamaster")
        except Exception:
            pass
        logging.info(f"已添加任务到 Transmission: {os.path.basename(torrent_path)}，标签: {label}，分类: mediamaster")
        return True
    except Exception as e:
        logging.error(f"添加任务到 Transmission 失败: {e}")
        return False

def add_task_to_qbittorrent(client, torrent_path, label):
    try:
        with open(torrent_path, 'rb') as f:
            torrent_data = f.read()
        # 添加任务时设置标签和分类
        client.torrents_add(torrent_files=torrent_data, tags=label, category="mediamaster")
        logging.info(f"已添加任务到 qBittorrent: {os.path.basename(torrent_path)}，标签: {label}，分类: mediamaster")
        return True
    except Exception as e:
        logging.error(f"添加任务到 qBittorrent 失败: {e}")
        return False

def main():
    import argparse
    parser = argparse.ArgumentParser(description="向下载器添加种子任务并设置标签")
    parser.add_argument("torrent_file", help="种子文件路径")
    args = parser.parse_args()

    torrent_path = args.torrent_file
    if not os.path.isfile(torrent_path):
        logging.error(f"种子文件不存在: {torrent_path}")
        sys.exit(1)

    label = os.path.splitext(os.path.basename(torrent_path))[0]

    config = load_config()

    download_mgmt = config.get('download_mgmt', 'False').lower() == 'true'
    download_type = config.get('download_type', 'transmission').lower()
    download_host = config.get('download_host', '127.0.0.1')
    download_port = int(config.get('download_port', 9091))
    download_username = config.get('download_username', '')
    download_password = config.get('download_password', '')

    if not download_mgmt:
        logging.error("下载管理功能未启用")
        sys.exit(1)

    # 迅雷直接退出
    if download_type == 'xunlei':
        logging.info("下载器为迅雷，无需执行当前程序添加下载任务。")
        sys.exit(0)

    success = False
    if download_type == 'transmission':
        try:
            client = TransmissionClient(
                host=download_host,
                port=download_port,
                username=download_username,
                password=download_password
            )
            success = add_task_to_transmission(client, torrent_path, label)
        except Exception as e:
            logging.error(f"连接 Transmission 失败: {e}")
            sys.exit(1)
    elif download_type == 'qbittorrent':
        try:
            client = QBittorrentClient(
                host=f"http://{download_host}:{download_port}",
                username=download_username,
                password=download_password
            )
            client.auth_log_in()
            success = add_task_to_qbittorrent(client, torrent_path, label)
        except LoginFailed as e:
            logging.error(f"qBittorrent 登录失败: {e}")
            sys.exit(1)
        except Exception as e:
            logging.error(f"连接 qBittorrent 失败: {e}")
            sys.exit(1)
    else:
        logging.error("未知的下载管理类型")
        sys.exit(1)

    if success:
        sys.exit(0)
    else:
        sys.exit(2)

if __name__ == "__main__":
    main()