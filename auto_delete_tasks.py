import sqlite3
import logging
import os
import shutil
from transmission_rpc import Client as TransmissionClient
from qbittorrentapi import Client as QBittorrentClient, LoginFailed

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 修改为 DEBUG 级别
    format="%(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/auto_delete_tasks.log", mode='w'),
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
        
        logging.info("加载配置文件成功")
        return config
    except sqlite3.Error as e:
        logging.error(f"数据库加载配置错误: {e}")
        exit(0)

# 加载配置
config = load_config()

# 获取下载管理相关配置
download_mgmt = config.get('download_mgmt', 'False').lower() == 'true'
download_type = config.get('download_type', 'transmission').lower()
download_host = config.get('download_host', '127.0.0.1')
download_port = int(config.get('download_port', 9091))
download_username = config.get('download_username', '')
download_password = config.get('download_password', '')

# Torrent目录路径
TORRENT_DIR = '/Torrent'

class DownloadManager:
    def __init__(self):
        if not download_mgmt:
            logging.info("下载管理功能未启用")
            return
        
        if download_type == 'transmission':
            self.client = TransmissionClient(
                host=download_host,
                port=download_port,
                username=download_username,
                password=download_password
            )
            logging.info("已连接到 Transmission")
        elif download_type == 'qbittorrent':
            try:
                self.client = QBittorrentClient(
                    host=f"http://{download_host}:{download_port}",
                    username=download_username,
                    password=download_password
                )
                self.client.auth_log_in()
                logging.info("已连接到 qBittorrent")
            except LoginFailed as e:
                logging.error(f"qBittorrent 登录失败: {e}")
                exit(0)
        else:
            logging.error("未知的下载管理类型")
            exit(0)

    def get_torrents(self):
        """获取所有任务"""
        if not download_mgmt:
            logging.info("下载管理功能未启用，跳过获取任务")
            return []
        
        try:
            if download_type == 'transmission':
                torrents = self.client.get_torrents()
                logging.debug(f"Transmission 中找到的任务: {torrents}")  # 增加调试日志
                if not torrents:
                    logging.info("Transmission 中没有找到任何任务")
                return [{'id': t.id, 'name': t.name, 'status': t.status, 'percent_done': t.percent_done} for t in torrents]
            elif download_type == 'qbittorrent':
                torrents = self.client.torrents_info()
                logging.debug(f"qBittorrent 中找到的任务: {torrents}")  # 增加调试日志
                if not torrents:
                    logging.info("qBittorrent 中没有找到任何任务")
                return [{'id': t['hash'], 'name': t['name'], 'status': t['state'], 'percent_done': t['progress']} for t in torrents]
        except Exception as e:
            logging.error(f"获取种子任务失败: {e}")
            return []

    def delete_stopped_torrents(self, torrents):
        """删除停止状态的种子任务"""
        if not download_mgmt:
            logging.info("下载管理功能未启用，跳过删除停止状态的任务")
            return
        
        if not torrents:
            logging.info("没有需要删除的停止状态任务")
            return
        
        for torrent in torrents:
            if torrent['status'] in ['stopped', 'error', 'stopped_download', 'stoppedDL']:  # 增加 'stoppedDL' 状态
                try:
                    if download_type == 'transmission':
                        self.client.remove_torrent(torrent['id'], delete_data=True)
                        logging.info(f"任务 {torrent['name']} 已删除")
                    elif download_type == 'qbittorrent':
                        self.client.torrents_delete(delete_files=True, torrent_hashes=torrent['id'])
                        logging.info(f"任务 {torrent['name']} 已删除")
                except Exception as e:
                    logging.error(f"删除任务失败: {e}")
            else:
                logging.debug(f"任务 {torrent['name']} 状态为 {torrent['status']}，不满足删除条件")

def check_and_delete_torrent_files():
    """检查并删除 Torrent 目录中的文件"""
    if os.path.exists(TORRENT_DIR) and os.path.isdir(TORRENT_DIR):
        try:
            files = os.listdir(TORRENT_DIR)
            if not files:
                logging.info(f"{TORRENT_DIR} 目录为空，无需删除文件")
                return
            
            for filename in files:
                file_path = os.path.join(TORRENT_DIR, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            logging.info(f"已删除 {TORRENT_DIR} 目录中的所有文件")
        except Exception as e:
            logging.error(f"删除 {TORRENT_DIR} 目录中的文件失败: {e}")
    else:
        logging.info(f"{TORRENT_DIR} 目录不存在或不是一个目录")

# 主流程
if download_mgmt:
    logging.info("下载管理功能已启用，开始执行主流程")
    manager = DownloadManager()
    torrents = manager.get_torrents()
    if torrents:
        manager.delete_stopped_torrents(torrents)
    else:
        logging.info("任务列表为空，跳过删除下载任务")
        check_and_delete_torrent_files()
else:
    logging.info("下载管理功能未启用，程序退出")