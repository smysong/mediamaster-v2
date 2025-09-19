import os
import json
import subprocess
import time
import logging
import sys
import signal
import sqlite3
import psutil
import threading

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/main.log", mode='w'),
        logging.StreamHandler()
    ]
)

def get_run_interval_from_db():
    try:
        conn = sqlite3.connect('/config/data.db')
        cursor = conn.cursor()
        cursor.execute("SELECT VALUE FROM CONFIG WHERE OPTION = 'run_interval_hours';")
        result = cursor.fetchone()
        cursor.close()
        conn.close()
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

def start_xunlei_torrent():
    try:
        process = subprocess.Popen(['python', 'xunlei_torrent.py'])
        logging.info("迅雷-种子监听服务已启动。")
        return process.pid
    except Exception as e:
        logging.error(f"无法启动迅雷-种子监听服务: {e}")
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

def monitor_chrome_process():
    chrome_started_time = None
    chromedriver_started_time = None

    for proc in psutil.process_iter(['pid', 'name', 'create_time']):
        try:
            process_name = proc.info['name'].lower()
            create_time = proc.info['create_time']

            if 'chrome' in process_name:
                if chrome_started_time is None or create_time < chrome_started_time:
                    chrome_started_time = create_time
            if 'chromedriver' in process_name:
                if chromedriver_started_time is None or create_time < chromedriver_started_time:
                    chromedriver_started_time = create_time

        except psutil.NoSuchProcess:
            continue

    def terminate_process(process_name_filter, started_time, threshold_seconds, log_prefix):
        if started_time:
            run_time = time.time() - started_time
            if run_time > threshold_seconds:
                logging.warning(f"{log_prefix} 进程已运行超过 {threshold_seconds // 60} 分钟，判定为异常，正在终止。")
                for proc in psutil.process_iter(['pid', 'name']):
                    try:
                        if process_name_filter in proc.info['name'].lower():
                            p = psutil.Process(proc.info['pid'])
                            p.terminate()
                            logging.info(f"已终止 {log_prefix} 进程 PID: {proc.info['pid']}")
                    except psutil.NoSuchProcess:
                        pass

    # 监控 Chrome 进程
    terminate_process('chrome', chrome_started_time, 20 * 60, "Chrome")

    # 监控 Chromedriver 进程
    terminate_process('chromedriver', chromedriver_started_time, 20 * 60, "Chromedriver")

    def kill_zombie_processes():
        """
        检测并记录僵尸进程
        僵尸进程只能由其父进程清理，这里仅做记录
        """
        zombie_count = 0
        for proc in psutil.process_iter(['pid', 'name', 'status']):
            try:
                if proc.info['status'] == psutil.STATUS_ZOMBIE:
                    zombie_count += 1
                    logging.debug(f"检测到僵尸进程 PID: {proc.info['pid']}, NAME: {proc.info['name']}")
            except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError):
                # 忽略进程已不存在、无访问权限或键不存在的情况
                pass
        
        if zombie_count > 0:
            logging.info(f"检测到 {zombie_count} 个僵尸进程，等待系统自动清理")

    # 调用清理僵尸进程函数
    kill_zombie_processes()

def chrome_monitor_thread():
    while running:
        monitor_chrome_process()
        time.sleep(300)  # 每5分钟检查一次

def start_chrome_monitor():
    thread = threading.Thread(target=chrome_monitor_thread, daemon=True)
    thread.start()
    logging.info("Chrome 进程监控已启动")

def check_site_status_and_save():
    """检查站点状态并保存到文件"""
    try:
        # 导入站点测试模块
        import sys
        sys.path.append('/app')
        
        import site_test
            
        # 运行站点测试
        tester = site_test.SiteTester()
        results = tester.run_tests()
        
        # 保存结果到文件
        status_data = {
            'status': results,
            'last_checked': time.strftime('%Y-%m-%d %H:%M:%S')
        }
        
        with open('/tmp/site_status.json', 'w', encoding='utf-8') as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)
            
        logging.info("站点状态检测完成并已保存到 /tmp/site_status.json")
        
    except Exception as e:
        logging.error(f"站点状态检测失败: {e}")

def site_status_thread():
    """在后台线程中执行站点状态检测"""
    logging.info("开始后台站点状态检测...")
    check_site_status_and_save()
    logging.info("后台站点状态检测完成")

# 全局变量
running = True
app_pid = None
sync_pid = None
xunlei_started = False

# 信号处理函数
def shutdown_handler(signum, frame):
    global running, app_pid, sync_pid
    logging.info(f"收到信号 {signum}，正在关闭程序...")

    running = False

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

    time.sleep(5)
    logging.info("程序已关闭。")
    sys.exit(0)

signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)

def main():
    global app_pid, sync_pid, running, xunlei_started

    run_interval_hours = get_run_interval_from_db()
    run_interval_seconds = run_interval_hours * 3600

    app_pid = start_app()
    sync_pid = start_sync()
    start_chrome_monitor()  # 启动 Chrome 监控线程
    
    # 在后台线程中启动站点状态检测，不阻塞主程序
    site_thread = threading.Thread(target=site_status_thread, daemon=True)
    site_thread.start()
    logging.info("站点状态检测已在后台启动")

    while running:
        run_script('scan_media.py')
        logging.info("-" * 80)
        logging.info("扫描媒体库：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('subscr.py')
        logging.info("-" * 80)
        logging.info("获取最新豆瓣订阅：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('check_subscr.py')
        logging.info("-" * 80)
        logging.info("检查是否有新增订阅：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('indexer.py')
        logging.info("-" * 80)
        logging.info("建立订阅资源索引：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('downloader.py')
        logging.info("-" * 80)
        logging.info("下载订阅媒体资源：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        if not xunlei_started:
            start_xunlei_torrent()
            xunlei_started = True

        run_script('tmdb_id.py')
        logging.info("-" * 80)
        logging.info("更新数据库TMDB_ID：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('dateadded.py')
        logging.info("-" * 80)
        logging.info("更新媒体NFO文件添加日期：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('actor_nfo.py')
        logging.info("-" * 80)
        logging.info("更新演职人员中文信息：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('episodes_nfo.py')
        logging.info("-" * 80)
        logging.info("更新集演职人员中文信息：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        run_script('auto_delete_tasks.py')
        logging.info("-" * 80)
        logging.info("自动删除已完成做种任务：已执行完毕，等待5秒...")
        logging.info("-" * 80)
        time.sleep(5)

        logging.info(f"所有任务已完成，等待 {run_interval_hours} 小时后再次运行...")
        time.sleep(run_interval_seconds)

if __name__ == "__main__":
    start_check_db_dir()
    report_versions()
    logging.info("等待初始化检查...")
    time.sleep(8)
    main()