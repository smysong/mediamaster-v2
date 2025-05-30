import sqlite3
import logging
import json
import sqlite3
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/check_subscr.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def load_config(db_path):
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

def subscribe_movies(cursor):
    """订阅电影"""
    cursor.execute('SELECT title, year, douban_id FROM RSS_MOVIES')
    rss_movies = cursor.fetchall()

    for title, year, douban_id in rss_movies:
        if not cursor.execute('SELECT 1 FROM LIB_MOVIES WHERE title = ? AND year = ?', (title, year)).fetchone():
            cursor.execute(
                'INSERT OR IGNORE INTO MISS_MOVIES (title, year, douban_id) VALUES (?, ?, ?)',
                (title, year, douban_id)
            )
            if cursor.rowcount > 0:
                logging.info(f"影片：{title}（{year}) 已添加订阅！")
                send_notification(f"影片：{title}（{year}) 已添加订阅！")
            else:
                logging.warning(f"影片：{title}（{year}) 已存在于订阅列表中，跳过插入。")
        else:
            logging.info(f"影片：{title}（{year}) 已入库，无需下载订阅！")

def subscribe_tvs(cursor):
    """订阅电视剧"""
    cursor.execute('SELECT title, season, episode, year, douban_id FROM RSS_TVS')
    rss_tvs = cursor.fetchall()

    for title, season, total_episodes, year, douban_id in rss_tvs:
        if total_episodes is None:
            logging.warning(f"电视剧：{title} 第{season}季 缺少总集数信息，跳过处理！")
            continue
        try:
            total_episodes = int(total_episodes)
        except (ValueError, TypeError):
            logging.warning(f"电视剧：{title} 第{season}季 总集数无效（{total_episodes}），跳过处理！")
            continue

        total_episodes = int(total_episodes)
        if not cursor.execute('SELECT 1 FROM LIB_TVS WHERE title = ? AND year = ?', (title, year)).fetchone():
            missing_episodes_str = ','.join(map(str, range(1, total_episodes + 1)))
            # 检查是否已经存在于 MISS_TVS 表中
            if not cursor.execute('SELECT 1 FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?', (title, year, season)).fetchone():
                cursor.execute(
                    'INSERT INTO MISS_TVS (title, year, season, missing_episodes, douban_id) VALUES (?, ?, ?, ?, ?)',
                    (title, year, season, missing_episodes_str, douban_id)
                )
                logging.info(f"电视剧：{title} 第{season}季 已添加订阅！")
                send_notification(f"电视剧：{title} 第{season}季 已添加订阅！")
            else:
                logging.warning(f"电视剧：{title} 第{season}季 已存在于订阅列表中，跳过插入。")
        else:
            existing_episodes_str = cursor.execute(
                '''SELECT episodes FROM LIB_TV_SEASONS WHERE tv_id = (SELECT id FROM LIB_TVS WHERE title = ? AND year = ?) AND season = ?''',
                (title, year, season)
            ).fetchone()

            if existing_episodes_str:
                # 兼容 episodes 字段为 int 或 str
                val = existing_episodes_str[0]
                if isinstance(val, int):
                    existing_episodes = set([val])
                elif isinstance(val, str):
                    existing_episodes = set(int(ep) for ep in val.split(',') if ep.strip().isdigit())
                else:
                    existing_episodes = set()
                total_episodes_set = set(range(1, total_episodes + 1))
                missing_episodes = total_episodes_set - existing_episodes

                if missing_episodes:
                    pass
                else:
                    logging.info(f"电视剧：{title} 第{season}季 已入库，无需下载订阅！")

def update_subscriptions(cursor):
    """检查并更新当前订阅"""
    # 检查并删除已入库的电影
    cursor.execute('SELECT title, year FROM MISS_MOVIES')
    miss_movies = cursor.fetchall()

    for title, year in miss_movies:
        if cursor.execute('SELECT 1 FROM LIB_MOVIES WHERE title = ? AND year = ?', (title, year)).fetchone():
            cursor.execute('DELETE FROM MISS_MOVIES WHERE title = ? AND year = ?', (title, year))
            logging.info(f"影片：{title}（{year}) 已完成订阅！")
            send_notification(f"影片：{title}（{year}) 已完成订阅！")

    # 检查并删除已完整订阅的电视剧
    cursor.execute('SELECT title, year, season, missing_episodes FROM MISS_TVS')
    miss_tvs = cursor.fetchall()

    for title, year, season, missing_episodes in miss_tvs:
        existing_episodes_str = cursor.execute(
            '''SELECT episodes FROM LIB_TV_SEASONS WHERE tv_id = (SELECT id FROM LIB_TVS WHERE title = ? AND year = ?) AND season = ?''',
            (title, year, season)
        ).fetchone()

        if existing_episodes_str:
            # 兼容 episodes 字段为 int 或 str
            val = existing_episodes_str[0]
            if isinstance(val, int):
                existing_episodes = set([val])
            elif isinstance(val, str):
                try:
                    existing_episodes = set(int(ep) for ep in val.split(',') if ep.strip().isdigit())
                except ValueError as e:
                    logging.error(f"无效的集数数据：{val}，跳过处理。")
                    continue
            else:
                existing_episodes = set()

            if missing_episodes:
                missing_episodes_set = set(map(int, missing_episodes.split(',')))
            else:
                missing_episodes_set = set()

            total_episodes_set = existing_episodes | missing_episodes_set

            if len(total_episodes_set) == len(existing_episodes):
                cursor.execute('DELETE FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?', (title, year, season))
                logging.info(f"电视剧：{title} 第{season}季 已完成订阅！")
                send_notification(f"电视剧：{title} 第{season}季 已完成订阅！")
            else:
                new_missing_episodes_str = ','.join(map(str, sorted(total_episodes_set - existing_episodes)))
                if new_missing_episodes_str != missing_episodes:  # 检查是否发生变化
                    cursor.execute('UPDATE MISS_TVS SET missing_episodes = ? WHERE title = ? AND year = ? AND season = ?', (new_missing_episodes_str, title, year, season))
                    logging.info(f"电视剧：{title} 第{season}季 缺失 {new_missing_episodes_str} 集，已更新订阅！")
                    send_notification(f"电视剧：{title} 第{season}季 缺失 {new_missing_episodes_str} 集，已更新订阅！")
                else:
                    logging.info(f"电视剧：{title} 第{season}季 订阅未发生变化！")

def send_notification(title_text):
    # 通知功能
    try:
        notification_enabled = config.get("notification", "")
        if notification_enabled.lower() != "true":  # 显式检查是否为 "true"
            logging.info("通知功能未启用，跳过发送通知。")
            return
        api_key = config.get("notification_api_key", "")
        if not api_key:
            logging.error("通知API Key未在配置文件中找到，无法发送通知。")
            return
        api_url = f"https://api.day.app/{api_key}"
        data = {
            "title": "订阅通知",
            "body": title_text  # 使用 title_text 作为 body 内容
        }
        headers = {'Content-Type': 'application/json'}
        response = requests.post(api_url, data=json.dumps(data), headers=headers)
        if response.status_code == 200:
            logging.info("通知发送成功: %s", response.text)
        else:
            logging.error("通知发送失败: %s %s", response.status_code, response.text)
    except KeyError as e:
        logging.error(f"配置文件中缺少必要的键: {e}")
    except requests.RequestException as e:
        logging.error(f"网络请求出现错误: {e}")

def main():
    # 读取配置文件
    global config
    config = load_config(db_path)
    # 连接到数据库
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        # 订阅电影
        subscribe_movies(cursor)

        # 订阅电视剧
        subscribe_tvs(cursor)

        # 更新订阅
        update_subscriptions(cursor)

        # 提交事务
        conn.commit()
    except sqlite3.Error as e:
        logging.error(f"发生错误：{e}")
        conn.rollback()
    finally:
        # 关闭连接
        conn.close()

if __name__ == "__main__":
    db_path='/config/data.db'
    main()