import sqlite3
import requests
import logging
import os
import shutil

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s', encoding='utf-8')
logger = logging.getLogger(__name__)

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
        exit(1)

# 创建ConfigParser对象
config = load_config()

# 获取download_mgmt部分的信息
download_mgmt = config.get('download_mgmt', '')
internal_download_mgmt_url = config.get('download_mgmt_url', '')

# 用于存储传输会话ID
session_id = ''

# 后端URL
backend_url = f'{internal_download_mgmt_url}/transmission/rpc'

# Torrent目录路径
TORRENT_DIR = '/Torrent'

def get_torrents():
    global session_id
    try:
        headers = {
            'Content-Type': 'application/json',
            'X-Transmission-Session-Id': session_id
        }
        payload = {
            'method': 'torrent-get',
            'arguments': {
                'fields': ['id', 'name', 'percentDone', 'status', 'rateDownload', 'rateUpload', 'magnetLink']
            }
        }
        response = requests.post(backend_url, headers=headers, json=payload)

        if 'X-Transmission-Session-Id' in response.headers:
            session_id = response.headers['X-Transmission-Session-Id']

        if response.status_code == 409:
            # 如果是409错误，重新尝试
            logger.debug('收到409错误，重新尝试获取任务列表')
            return get_torrents()

        response.raise_for_status()

        data = response.json()
        torrents = data['arguments']['torrents']
        
        if not torrents:
            logger.info('任务列表为空')
            check_and_delete_torrent_files()
        else:
            delete_stopped_torrents(torrents)
    except requests.exceptions.RequestException as e:
        logger.error(f'获取任务列表失败: {e}')

def delete_stopped_torrents(torrents):
    global session_id
    for torrent in torrents:
        if torrent['status'] == 0:  # 0 表示停止状态
            try:
                headers = {
                    'Content-Type': 'application/json',
                    'X-Transmission-Session-Id': session_id
                }
                payload = {
                    'method': 'torrent-remove',
                    'arguments': {
                        'ids': [torrent['id']],
                        'delete-local-data': True
                    }
                }
                response = requests.post(backend_url, headers=headers, json=payload)

                if 'X-Transmission-Session-Id' in response.headers:
                    session_id = response.headers['X-Transmission-Session-Id']

                response.raise_for_status()

                logger.info(f'任务 {torrent["name"]} 已删除')
            except requests.exceptions.RequestException as e:
                logger.error(f'删除任务失败: {e}')

def check_and_delete_torrent_files():
    if os.path.exists(TORRENT_DIR) and os.path.isdir(TORRENT_DIR):
        try:
            # 删除目录中的所有文件
            for filename in os.listdir(TORRENT_DIR):
                file_path = os.path.join(TORRENT_DIR, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            logger.info(f'已删除 {TORRENT_DIR} 目录中的所有文件')
        except Exception as e:
            logger.error(f'删除 {TORRENT_DIR} 目录中的文件失败: {e}')
    else:
        logger.info(f'{TORRENT_DIR} 目录不存在或不是一个目录')

# 执行一次任务获取和删除操作
get_torrents()