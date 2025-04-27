import os
import subprocess
import time
import logging
import sys
import signal
import sqlite3

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/main.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def get_run_interval_from_db():
    """
    从 database/data.db 数据库的 CONFIG 表中读取 run_interval_hours 的值。
    如果读取失败或值不存在，返回默认值 6 小时。
    """
    try:
        # 连接到数据库
        conn = sqlite3.connect('/config/data.db')
        cursor = conn.cursor()
        
        # 查询 run_interval_hours 的值
        cursor.execute("SELECT VALUE FROM CONFIG WHERE OPTION = 'run_interval_hours';")
        result = cursor.fetchone()
        
        # 关闭数据库连接
        cursor.close()
        conn.close()
        
        # 如果查询结果存在，返回整数值；否则返回默认值 6
        if result:
            return int(result[0])
        else:
            logging.warning("未找到 run_interval_hours 配置项，使用默认值 6 小时。")
            return 6
    except Exception as e:
        logging.error(f"无法从数据库读取 run_interval_hours: {e}，使用默认值 6 小时。")
        return 6

def run_script(script_name):
    try:
        result = subprocess.run(['python', script_name], check=True)
        logging.debug(f"{script_name} 已执行完毕。")
    except subprocess.CalledProcessError as e:
        logging.error(f"{script_name} 执行失败，退出程序。错误信息: {e}")
        sys.exit(0)

def start_app():
    try:
        with open(os.devnull, 'w') as devnull:
            process = subprocess.Popen(['python', 'app.py'], stdout=devnull, stderr=devnull)
            logging.info("WEB管理已启动。")
            return process.pid
    except Exception as e:
        logging.error(f"无法启动WEB管理程序: {e}")
        sys.exit(0)

def start_sync():
    try:
        process = subprocess.Popen(['python', 'sync.py'])
        logging.info("目录监控服务已启动。")
        return process.pid
    except Exception as e:
        logging.error(f"无法启动目录监控服务: {e}")
        sys.exit(0)

def start_check_db_dir():
    try:
        process = subprocess.Popen(['python', 'check_db_dir.py'])
        logging.info("启动数据库和目录检查服务")
        return process.pid
    except Exception as e:
        logging.error(f"无法启动数据库和目录检查服务: {e}")
        sys.exit(0)

def report_versions():
    try:
        process = subprocess.Popen(['python', 'report_versions.py'])
        logging.info("启动版本检测及统计服务")
        return process.pid
    except Exception as e:
        logging.error(f"无法启动版本检测及统计服务: {e}")
        sys.exit(0)

# 全局变量，用于控制主循环
running = True
app_pid = None
sync_pid = None

# 定义信号处理器函数
def shutdown_handler(signum, frame):
    global running, app_pid, sync_pid
    logging.info(f"收到信号 {signum}，正在关闭程序...")

    # 停止主循环
    running = False

    # 终止子进程
    if app_pid:
        logging.info(f"终止 app.py 进程 (PID: {app_pid})")
        try:
            os.kill(app_pid, signal.SIGTERM)
        except ProcessLookupError:
            logging.warning(f"进程 {app_pid} 不存在，跳过终止操作。")

    if sync_pid:
        logging.info(f"终止 sync.py 进程 (PID: {sync_pid})")
        try:
            os.kill(sync_pid, signal.SIGTERM)
        except ProcessLookupError:
            logging.warning(f"进程 {sync_pid} 不存在，跳过终止操作。")

    # 等待子进程优雅地关闭
    time.sleep(5)  # 可以根据实际情况调整等待时间

    logging.info("程序已关闭。")
    sys.exit(0)

# 注册信号处理器
signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

def main():
    global app_pid, sync_pid, running

    # 从数据库读取运行间隔时间
    run_interval_hours = get_run_interval_from_db()
    run_interval_seconds = run_interval_hours * 3600

    # 启动 app.py
    app_pid = start_app()

    # 启动 sync.py
    sync_pid = start_sync()

    while running:
        # 执行所有任务脚本
        run_script('subscr.py')
        logging.info("-" * 80)
        logging.info("获取最新豆瓣订阅，已执行完毕，等待10秒...")
        logging.info("-" * 80)
        time.sleep(10)

        run_script('check_subscr.py')
        logging.info("-" * 80)
        logging.info("检查是否有新增订阅，已执行完毕，等待10秒...")
        logging.info("-" * 80)
        time.sleep(10)

        run_script('tvshow_downloader.py')
        logging.info("-" * 80)
        logging.info("电视剧检索下载，已执行完毕，等待10秒...")
        logging.info("-" * 80)
        time.sleep(10)

        run_script('movie_downloader.py')
        logging.info("-" * 80)
        logging.info("电影检索下载，已执行完毕，等待10秒...")
        logging.info("-" * 80)
        time.sleep(10)

        run_script('scan_media.py')
        logging.info("-" * 80)
        logging.info("扫描媒体库，已执行完毕，等待10秒...")
        logging.info("-" * 80)
        time.sleep(10)

        run_script('tmdb_id.py')
        logging.info("-" * 80)
        logging.info("更新数据库TMDB_ID，已执行完毕，等待10秒...")
        logging.info("-" * 80)
        time.sleep(10)

        run_script('dateadded.py')
        logging.info("-" * 80)
        logging.info("更新媒体NFO文件添加日期，已执行完毕，等待10秒...")
        logging.info("-" * 80)
        time.sleep(10)

        run_script('actor_nfo.py')
        logging.info("-" * 80)
        logging.info("更新演职人员中文信息，已执行完毕。")
        logging.info("-" * 80)

        run_script('episodes_nfo.py')
        logging.info("-" * 80)
        logging.info("更新集演职人员中文信息，已执行完毕。")
        logging.info("-" * 80)

        run_script('auto_delete_tasks.py')
        logging.info("-" * 80)
        logging.info("自动删除已完成做种任务已执行完毕。")
        logging.info("-" * 80)

        logging.info(f"所有任务已完成，等待 {run_interval_hours} 小时后再次运行...")
        time.sleep(run_interval_seconds)

if __name__ == "__main__":
    start_check_db_dir()
    report_versions()
    logging.info("等待初始化检查...")
    time.sleep(5)
    main()
