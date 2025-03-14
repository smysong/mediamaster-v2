import os
import subprocess
import time
import logging
import sys
import signal
import sqlite3

# 定义日志保存目录和处理记录保存目录
log_dir = "/tmp/log"  # 日志保存目录
record_dir = "/tmp/record"  # 处理记录保存目录

# 检查并创建目录
for directory in [log_dir, record_dir]:
    if not os.path.exists(directory):  # 检查目录是否存在
        os.makedirs(directory)  # 如果不存在，则创建目录
        logging.debug(f"目录已创建: {directory}")
    else:
        logging.debug(f"目录已存在: {directory}")

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler(os.path.join(log_dir, "main.log")),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

def get_status_code_from_log(log_file):
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

def get_run_interval_from_db():
    """
    从 database/data.db 数据库的 CONFIG 表中读取 run_interval_hours 的值。
    如果读取失败或值不存在，返回默认值 6 小时。
    """
    try:
        # 连接到数据库
        conn = sqlite3.connect('database/data.db')
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
        sys.exit(1)

def start_app():
    try:
        with open(os.devnull, 'w') as devnull:
            process = subprocess.Popen(['python', 'app.py'], stdout=devnull, stderr=devnull)
            logging.info("WEB管理已启动。")
            return process.pid
    except Exception as e:
        logging.error(f"无法启动WEB管理程序: {e}")
        sys.exit(1)

def start_sync():
    try:
        process = subprocess.Popen(['python', 'sync.py'])
        logging.info("目录监控服务已启动。")
        return process.pid
    except Exception as e:
        logging.error(f"无法启动目录监控服务: {e}")
        sys.exit(1)

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

    # 启动数据库管理程序并等待其完成
    try:
        subprocess.run(['python', 'database_manager.py'], check=True, capture_output=True, text=True)
        logging.info("数据库已检查完成。")
        status_code = get_status_code_from_log("/tmp/log/database_manager.log")
        if status_code == 0:
            logging.info("系统配置是默认数据，请登录WEB管理修改配置,修改完成后重启容器。")
            logging.info("默认用户名：admin  默认密码：password  默认端口：8888")
            # 启动 app.py
            app_pid = start_app()
            # 退出 main 函数，不运行后续任务脚本
            return
        elif status_code == 1:
            logging.debug("系统配置非默认数据。")
        else:
            logging.warning(f"未知的状态码: {status_code}")
    except subprocess.CalledProcessError as e:
        logging.error(f"数据库管理程序执行失败，退出程序。错误信息: {e.stderr}")
        sys.exit(1)

    # 从数据库读取运行间隔时间
    run_interval_hours = get_run_interval_from_db()
    run_interval_seconds = run_interval_hours * 3600

    # 启动 app.py
    app_pid = start_app()

    # 启动 sync.py
    sync_pid = start_sync()

    while running:
        # 执行所有任务脚本
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

        run_script('actor_nfo.py')
        logging.info("-" * 80)
        logging.info("更新电视剧演职人员中文信息，已执行完毕。")
        logging.info("-" * 80)

        run_script('episodes_nfo.py')
        logging.info("-" * 80)
        logging.info("更新每集演职人员中文信息，已执行完毕。")
        logging.info("-" * 80)

        run_script('auto_delete_tasks.py')
        logging.info("-" * 80)
        logging.info("自动删除已完成做种任务已执行完毕。")
        logging.info("-" * 80)

        logging.info(f"所有任务已完成，等待 {run_interval_hours} 小时后再次运行...")
        time.sleep(run_interval_seconds)

if __name__ == "__main__":
    main()