import sqlite3
import requests
import logging
import os
import shutil

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/auto_delete_tasks.log"),
        logging.StreamHandler()
    ]
)

global_config = {}

def load_config():
    try:
        conn = sqlite3.connect('/config/data.db')
        cursor = conn.cursor()
        cursor.execute("SELECT OPTION, VALUE FROM CONFIG")
        rows = cursor.fetchall()
        config_dict = {option: value for option, value in rows}
        global_config.update({
            "download_mgmt": config_dict.get("download_mgmt"),
            "download_mgmt_url": config_dict.get("download_mgmt_url"),
            "download_username": config_dict.get("download_username"),
            "download_password": config_dict.get("download_password")
        })
        logging.info("从数据库加载配置文件成功")
    except sqlite3.Error as e:
        logging.error(f"从数据库加载配置失败: {e}")
        exit(1)
    finally:
        if 'conn' in locals():
            conn.close()

def get_torrents():
    global session_id
    internal_download_mgmt_url = global_config.get("download_mgmt_url")
    backend_url = f'{internal_download_mgmt_url}/transmission/rpc'

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
            logging.debug('收到409错误，重新尝试获取任务列表')
            return get_torrents()

        response.raise_for_status()

        data = response.json()
        torrents = data['arguments']['torrents']
        
        if not torrents:
            logging.info('任务列表为空')
            check_and_delete_torrent_files()
        else:
            delete_stopped_torrents(torrents)
    except requests.exceptions.RequestException as e:
        logging.error(f'获取任务列表失败: {e}')

def delete_stopped_torrents(torrents):
    global session_id
    for torrent in torrents:
        if torrent['status'] == 0:
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

                logging.info(f'任务 {torrent["name"]} 已删除')
            except requests.exceptions.RequestException as e:
                logging.error(f'删除任务失败: {e}')

def check_and_delete_torrent_files():
    if os.path.exists(TORRENT_DIR) and os.path.isdir(TORRENT_DIR):
        try:
            for filename in os.listdir(TORRENT_DIR):
                file_path = os.path.join(TORRENT_DIR, filename)
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            logging.info(f'已删除 {TORRENT_DIR} 目录中的所有文件')
        except Exception as e:
            logging.error(f'删除 {TORRENT_DIR} 目录中的文件失败: {e}')
    else:
        logging.info(f'{TORRENT_DIR} 目录不存在或不是一个目录')

# 全局变量
session_id = ''
TORRENT_DIR = '/Torrent'
backend_url = ''

if __name__ == "__main__":
    load_config()
    download_mgmt = global_config.get("download_mgmt")
    if download_mgmt == "False":  # 假设数据库中的值是字符串 "False"
        logging.info("下载管理未启用，程序不运行")
        exit(0)
    elif download_mgmt is None:
        logging.error("未找到下载管理相关配置，程序不运行")
        exit(1)
    elif download_mgmt != "True":  # 假设数据库中的值是字符串 "True"
        logging.error(f"下载管理配置值无效: {download_mgmt}，程序不运行")
        exit(1)
    
    get_torrents()