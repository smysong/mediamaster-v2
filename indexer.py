import subprocess
import logging
import os
import glob

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/indexer.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def clear_index_directory():
    index_dir = "/tmp/index/"
    if os.path.exists(index_dir):
        logging.info(f"清理目录: {index_dir}")
        json_files = glob.glob(os.path.join(index_dir, "*.json"))
        for file in json_files:
            try:
                os.remove(file)
                logging.info(f"已删除文件: {file}")
            except Exception as e:
                logging.error(f"删除文件 {file} 时出错: {e}")
    else:
        logging.info(f"目录不存在: {index_dir}")

def run_script(script_name, friendly_name):
    try:
        logging.info("-" * 80)
        logging.info(f"正在建立索引: {friendly_name}")
        # 捕获子进程的输出，将标准输出和错误输出合并
        result = subprocess.run(
            ["python", script_name],
            check=True,
            stdout=subprocess.PIPE,  # 捕获标准输出
            stderr=subprocess.STDOUT,  # 将标准错误重定向到标准输出
            text=True  # 确保输出为字符串
        )
        # 记录合并后的输出
        logging.info(f"索引程序日志:\n{result.stdout}")
        logging.info(f"建立索引完成: {friendly_name}")
        logging.info("-" * 80)
    except subprocess.CalledProcessError as e:
        logging.error(f"运行 {friendly_name} 索引程序 ({script_name}) 时出错，退出码: {e.returncode}")
        # 记录异常时的输出
        if e.stdout:
            logging.error(f"{friendly_name} 索引程序输出:\n{e.stdout}")
        logging.info("-" * 80)

def main():
    # 清理 /tmp/index/ 目录
    clear_index_directory()

    scripts = {
        "movie_bthd.py": "高清影视之家",
        "tvshow_hdtv.py": "高清剧集网",
        "movie_tvshow_btys.py": "BT影视",
        "movie_tvshow_bt0.py": "不太灵影视",
        "movie_tvshow_gy.py": "观影"
    }

    for script_name, friendly_name in scripts.items():
        run_script(script_name, friendly_name)

if __name__ == "__main__":
    main()