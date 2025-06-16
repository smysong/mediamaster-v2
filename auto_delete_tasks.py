import sqlite3
import logging
import os
import shutil
from transmission_rpc import Client as TransmissionClient
from qbittorrentapi import Client as QBittorrentClient, LoginFailed

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 修改为 INFO 级别
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
        """
        初始化下载器客户端，增加连接失败的异常处理，避免程序崩溃。
        """
        self.client = None
        if not download_mgmt:
            logging.info("下载管理功能未启用")
            return
        
        try:
            if download_type == 'transmission':
                self.client = TransmissionClient(
                    host=download_host,
                    port=download_port,
                    username=download_username,
                    password=download_password
                )
                logging.info("已连接到 Transmission")
            elif download_type == 'qbittorrent':
                self.client = QBittorrentClient(
                    host=f"http://{download_host}:{download_port}",
                    username=download_username,
                    password=download_password
                )
                self.client.auth_log_in()
                logging.info("已连接到 qBittorrent")
            else:
                logging.error("未知的下载管理类型")
                self.client = None
        except Exception as e:
            logging.error(f"连接下载器失败: {e}")
            self.client = None

    def get_torrents(self):
        """获取所有任务，连接失败时直接返回空列表"""
        if not download_mgmt or not self.client:
            logging.info("下载管理功能未启用或下载器未连接，跳过获取任务")
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
        """
        删除下载进度为100%并且是停止状态的种子任务，
        同时删除任务下载的文件和所有未使用的标签（qBittorrent支持标签直接删除）。
        增加连接失败的保护。
        """
        if not download_mgmt or not self.client:
            logging.info("下载管理功能未启用或下载器未连接，跳过删除停止状态的任务")
            return
    
        if not torrents:
            logging.info("没有需要删除的停止状态任务")
        else:
            for torrent in torrents:
                # 判断进度为100%且状态为停止
                is_stopped = torrent['status'] in ['stopped', 'error', 'stopped_download', 'stoppedDL', 'stoppedUP']
                is_finished = torrent['percent_done'] >= 0.9999 or torrent['percent_done'] == 1.0
                if is_stopped and is_finished:
                    try:
                        if download_type == 'transmission':
                            self.client.remove_torrent(torrent['id'], delete_data=True)
                            logging.info(f"任务 {torrent['name']} 已删除（含数据）")
                            # Transmission标签随任务删除自动消失
                        elif download_type == 'qbittorrent':
                            # 删除任务及数据
                            self.client.torrents_delete(delete_files=True, torrent_hashes=torrent['id'])
                            logging.info(f"任务 {torrent['name']} 已删除（含数据）")
                    except Exception as e:
                        logging.error(f"删除任务失败: {e}")
                else:
                    logging.debug(f"任务 {torrent['name']} 状态为 {torrent['status']}，进度为 {torrent['percent_done']:.2%}，不满足删除条件")
    
        # 无论是否有任务，都检查并删除未使用的标签（仅 qBittorrent 支持）
        if download_type == 'qbittorrent':
            try:
                all_tags = self.client.torrents_tags()
                if isinstance(all_tags, str):
                    all_tags = [t.strip() for t in all_tags.split(',') if t.strip()]
                logging.info(f"当前所有标签: {all_tags}")
                for tag in all_tags:
                    if not tag:
                        continue  # 跳过空标签
                    torrents_with_tag = self.client.torrents_info(tag=tag)
                    logging.info(f"标签 {tag} 关联任务数: {len(torrents_with_tag)}")
                    logging.debug(f"标签 {tag} 关联任务详细: {torrents_with_tag}")
                    if not torrents_with_tag:
                        # 直接删除未使用标签
                        self.client.torrent_tags.delete_tags(tags=tag)
                        logging.info(f"未使用的标签 {tag} 已删除")
            except Exception as e:
                logging.warning(f"删除未使用标签失败: {e}")

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
    
    if download_type == 'xunlei':
        logging.info("当前下载器为：迅雷。无需执行自动删除已完成任务，程序退出。")
        exit(0)

    manager = DownloadManager()
    if manager.client:
        torrents = manager.get_torrents()
        manager.delete_stopped_torrents(torrents)
    else:
        logging.error("未能连接到下载器，跳过后续操作")
    # 可选：如需清理Torrent目录
    check_and_delete_torrent_files()
else:
    logging.info("下载管理功能未启用，程序退出")