import requests
import logging
import uuid
import os
import schedule
import time
import threading

# 配置日志记录
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# 定义版本号
def get_app_version():
    """
    从 versions 文件中读取版本号
    """
    try:
        with open("versions", "r") as file:
            return file.read().strip()
    except FileNotFoundError:
        logging.warning("versions 文件未找到，使用默认版本号")
        return "unknown"

# 程序当前版本号
CURRENT_VERSION = get_app_version()

# 客户端唯一 ID 文件路径
CLIENT_ID_FILE = "/config/client_id"

def get_client_id():
    """
    获取客户端唯一 ID，如果不存在则生成并保存
    """
    if os.path.exists(CLIENT_ID_FILE):
        with open(CLIENT_ID_FILE, "r") as file:
            client_id = file.read().strip()
            if client_id:
                return client_id

    # 如果文件不存在或内容为空，生成新的 UUID
    client_id = str(uuid.uuid4())
    with open(CLIENT_ID_FILE, "w") as file:
        file.write(client_id)
    return client_id

def send_client_data(client_id, version):
    """
    发送用户统计数据到服务器
    """
    url = "http://status.songmy.top:5000/submit"
    payload = {
        "client_id": client_id,
        "version": version
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            logging.debug("报告程序版本信息")
        else:
            logging.error(f"版本统计数据发送失败，状态码: {response.status_code}, 响应: {response.text}")
    except requests.RequestException as e:
        logging.error(f"发送版本统计数据时发生错误: {e}")

def send_heartbeat():
    """
    发送心跳包以保持在线状态
    """
    client_id = get_client_id()
    send_client_data(client_id, CURRENT_VERSION)
    logging.debug("发送心跳包以更新在线状态")

def run_scheduler():
    """
    运行调度器发送心跳包
    """
    # 每15分钟发送一次心跳包
    schedule.every(15).minutes.do(send_heartbeat)
    
    while True:
        schedule.run_pending()
        time.sleep(1)

def main():
    client_id = get_client_id()
    logging.info(f"版本: {CURRENT_VERSION}")
    logging.info(f"UUID: {client_id}")

    # 发送客户端数据到服务器
    send_client_data(client_id, CURRENT_VERSION)
    
    # 启动心跳包线程
    heartbeat_thread = threading.Thread(target=run_scheduler, daemon=True)
    heartbeat_thread.start()
    
    # 为了保持程序运行，添加一个简单的循环
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("程序退出")

if __name__ == "__main__":
    main()