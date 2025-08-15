import sqlite3
import logging
import json
import requests
import random
import time

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
        
        logging.debug("加载配置文件成功")
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
        
        # 检查是否已经存在于 MISS_TVS 表中
        miss_row = cursor.execute(
            'SELECT missing_episodes FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
            (title, year, season)
        ).fetchone()
        
        # 检查LIB_TVS中是否已存在
        lib_exists = cursor.execute('SELECT 1 FROM LIB_TVS WHERE title = ? AND year = ?', (title, year)).fetchone()
        
        if not lib_exists:
            # 完全未入库的情况
            missing_episodes_str = ','.join(map(str, range(1, total_episodes + 1)))
            if not miss_row:
                # 完全新订阅
                cursor.execute(
                    'INSERT INTO MISS_TVS (title, year, season, missing_episodes, douban_id) VALUES (?, ?, ?, ?, ?)',
                    (title, year, season, missing_episodes_str, douban_id)
                )
                logging.info(f"电视剧：{title} 第{season}季 已添加订阅！")
                send_notification(f"电视剧：{title} 第{season}季 已添加订阅！")
            else:
                # 已存在订阅，检查是否需要更新（总集数是否变化）
                subscribed_missing = set(int(ep) for ep in miss_row[0].split(',') if ep.strip().isdigit()) if miss_row[0] else set()
                total_episodes_set = set(range(1, total_episodes + 1))
                
                # 如果总集数发生变化，则更新
                if len(total_episodes_set) != len(subscribed_missing):
                    # 更新缺失集数为最新的总集数范围
                    new_missing_episodes_str = ','.join(map(str, sorted(total_episodes_set)))
                    cursor.execute(
                        'UPDATE MISS_TVS SET missing_episodes = ? WHERE title = ? AND year = ? AND season = ?',
                        (new_missing_episodes_str, title, year, season)
                    )
                    logging.info(f"电视剧：{title} 第{season}季 总集数已更新为{total_episodes}集，已更新订阅！")
                else:
                    logging.warning(f"电视剧：{title} 第{season}季 已存在于订阅列表中，跳过插入。")
        else:
            # 部分或全部已入库的情况
            existing_episodes_str = cursor.execute(
                '''SELECT episodes FROM LIB_TV_SEASONS WHERE tv_id = (SELECT id FROM LIB_TVS WHERE title = ? AND year = ?) AND season = ?''',
                (title, year, season)
            ).fetchone()

            if existing_episodes_str:
                val = existing_episodes_str[0]
                if isinstance(val, int):
                    existing_episodes = set([val])
                elif isinstance(val, str):
                    existing_episodes = set(int(ep) for ep in val.split(',') if ep.strip().isdigit())
                else:
                    existing_episodes = set()
            else:
                existing_episodes = set()
                
            total_episodes_set = set(range(1, total_episodes + 1))
            missing_episodes_set = total_episodes_set - existing_episodes

            if miss_row and miss_row[0]:
                subscribed_missing = set(int(ep) for ep in miss_row[0].split(',') if ep.strip().isdigit())
            else:
                subscribed_missing = set()

            # 检查RSS_TVS中的总集数是否与当前订阅表中的总集数一致
            current_subscribed_total = len(subscribed_missing) + len(existing_episodes)
            
            # 需要补充的缺失集
            need_add_missing = missing_episodes_set - subscribed_missing
            
            if miss_row:
                # 如果总集数发生了变化，或者需要添加新的缺失集
                if len(total_episodes_set) != current_subscribed_total or need_add_missing:
                    # 合并后写回
                    new_missing_episodes = sorted(subscribed_missing | need_add_missing)
                    new_missing_episodes_str = ','.join(map(str, new_missing_episodes))
                    cursor.execute(
                        'UPDATE MISS_TVS SET missing_episodes = ? WHERE title = ? AND year = ? AND season = ?',
                        (new_missing_episodes_str, title, year, season)
                    )
                    logging.info(f"电视剧：{title} 第{season}季 缺失 {new_missing_episodes_str} 集，已更新订阅！")
                elif not missing_episodes_set:
                    # 没有缺失集，删除订阅
                    cursor.execute(
                        'DELETE FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?',
                        (title, year, season)
                    )
                    logging.info(f"电视剧：{title} 第{season}季 已入库，无需下载订阅！")
                else:
                    logging.info(f"电视剧：{title} 第{season}季 订阅未发生变化！")
            else:
                # 如果订阅表中没有记录且有缺失集，则插入
                if missing_episodes_set:
                    new_missing_episodes_str = ','.join(map(str, sorted(missing_episodes_set)))
                    cursor.execute(
                        'INSERT INTO MISS_TVS (title, year, season, missing_episodes, douban_id) VALUES (?, ?, ?, ?, ?)',
                        (title, year, season, new_missing_episodes_str, douban_id)
                    )
                    logging.info(f"电视剧：{title} 第{season}季 缺失 {new_missing_episodes_str} 集，已补充订阅！")

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

            # 只保留还未入库的缺失集数
            new_missing_episodes_set = missing_episodes_set - existing_episodes

            if not new_missing_episodes_set:
                cursor.execute('DELETE FROM MISS_TVS WHERE title = ? AND year = ? AND season = ?', (title, year, season))
                logging.info(f"电视剧：{title} 第{season}季 已完成订阅！")
                send_notification(f"电视剧：{title} 第{season}季 已完成订阅！")
            else:
                new_missing_episodes_str = ','.join(map(str, sorted(new_missing_episodes_set)))
                if new_missing_episodes_str != missing_episodes:  # 检查是否发生变化
                    cursor.execute('UPDATE MISS_TVS SET missing_episodes = ? WHERE title = ? AND year = ? AND season = ?', (new_missing_episodes_str, title, year, season))
                    logging.info(f"电视剧：{title} 第{season}季 缺失 {new_missing_episodes_str} 集，已更新订阅！")
                    send_notification(f"电视剧：{title} 第{season}季 缺失 {new_missing_episodes_str} 集，已更新订阅！")
                else:
                    logging.info(f"电视剧：{title} 第{season}季 订阅未发生变化！")

def update_miss_titles(cursor):
    """检查并更新正在订阅中的标题与豆瓣想看保持一致"""
    # 更新MISS_MOVIES中的标题
    cursor.execute('''
        SELECT mm.douban_id, mm.title AS miss_title, rm.title AS rss_title
        FROM MISS_MOVIES mm
        JOIN RSS_MOVIES rm ON mm.douban_id = rm.douban_id
        WHERE mm.title != rm.title
    ''')
    movies_to_update = cursor.fetchall()
    
    for douban_id, miss_title, rss_title in movies_to_update:
        cursor.execute('UPDATE MISS_MOVIES SET title = ? WHERE douban_id = ?', (rss_title, douban_id))
        logging.info(f"已更新电影标题: '{miss_title}' -> '{rss_title}' (豆瓣ID: {douban_id})")
    
    # 更新MISS_TVS中的标题
    cursor.execute('''
        SELECT mt.douban_id, mt.title AS miss_title, rt.title AS rss_title
        FROM MISS_TVS mt
        JOIN RSS_TVS rt ON mt.douban_id = rt.douban_id
        WHERE mt.title != rt.title
    ''')
    tvs_to_update = cursor.fetchall()
    
    for douban_id, miss_title, rss_title in tvs_to_update:
        cursor.execute('UPDATE MISS_TVS SET title = ? WHERE douban_id = ?', (rss_title, douban_id))
        logging.info(f"已更新电视剧标题: '{miss_title}' -> '{rss_title}' (豆瓣ID: {douban_id})")
    
    if not movies_to_update and not tvs_to_update:
        logging.info("正在订阅列表中没有需要更新的标题")
    else:
        logging.info(f"共更新 {len(movies_to_update)} 个电影和 {len(tvs_to_update)} 个电视剧的标题")

def update_tmdb_items(cursor):
    """检查并更新没有douban_id的电影和电视剧信息"""
    # 获取TMDB配置
    TMDB_API_KEY = config.get("tmdb_api_key", "")
    TMDB_BASE_URL = config.get("tmdb_base_url", "")
    
    if not TMDB_API_KEY:
        logging.warning("TMDB API Key未配置，跳过TMDB项目检查")
        return
    
    # 检查没有douban_id的电影
    cursor.execute('SELECT id, title, year FROM MISS_MOVIES WHERE douban_id IS NULL OR douban_id = ""')
    movies_without_douban = cursor.fetchall()
    
    for movie_id, local_title, year in movies_without_douban:
        try:
            # 使用TMDB搜索电影
            search_url = f"{TMDB_BASE_URL}/3/search/movie"
            params = {
                'api_key': TMDB_API_KEY,
                'query': local_title,
                'year': year,
                'language': 'zh-CN'
            }
            
            response = requests.get(search_url, params=params, timeout=10)
            if response.status_code == 200:
                search_data = response.json()
                if search_data.get('results'):
                    # 获取最匹配的结果
                    movie_result = search_data['results'][0]
                    tmdb_title = movie_result.get('title', '')
                    tmdb_id = movie_result.get('id', '')
                    
                    # 标准化标题比较
                    local_title_normalized = str(local_title).strip() if local_title is not None else ''
                    tmdb_title_normalized = str(tmdb_title).strip() if tmdb_title is not None else ''
                    
                    # 如果标题不一致，更新本地数据库
                    if tmdb_title_normalized != local_title_normalized:
                        cursor.execute('UPDATE MISS_MOVIES SET title = ? WHERE id = ?', 
                                     (tmdb_title_normalized, movie_id))
                        logging.info(f"已更新电影标题: '{local_title_normalized}' -> '{tmdb_title_normalized}' (TMDB ID: {tmdb_id})")
                else:
                    logging.warning(f"未找到电影 '{local_title}' ({year}) 的TMDB信息")
            else:
                logging.error(f"获取电影 '{local_title}' 信息失败，状态码: {response.status_code}")
        except requests.RequestException as e:
            logging.error(f"请求TMDB API时发生错误: {e}")
        except Exception as e:
            logging.error(f"处理电影 '{local_title}' 时发生未知错误: {e}")
        
        # 随机休眠避免频繁请求
        sleep_time = random.uniform(1, 3)
        time.sleep(sleep_time)
    
    # 检查没有douban_id的电视剧
    cursor.execute('SELECT id, title, year, season, missing_episodes FROM MISS_TVS WHERE douban_id IS NULL OR douban_id = ""')
    tvs_without_douban = cursor.fetchall()
    
    for tv_id, local_title, year, season, missing_episodes in tvs_without_douban:
        try:
            # 使用TMDB搜索电视剧
            search_url = f"{TMDB_BASE_URL}/3/search/tv"
            params = {
                'api_key': TMDB_API_KEY,
                'query': local_title,
                'year': year,
                'language': 'zh-CN'
            }
            
            response = requests.get(search_url, params=params, timeout=10)
            if response.status_code == 200:
                search_data = response.json()
                if search_data.get('results'):
                    # 获取最匹配的结果
                    tv_result = search_data['results'][0]
                    tmdb_title = tv_result.get('name', '')
                    tmdb_id = tv_result.get('id', '')
                    
                    # 获取详细季数信息
                    season_url = f"{TMDB_BASE_URL}/3/tv/{tmdb_id}/season/{season}"
                    season_params = {'api_key': TMDB_API_KEY}
                    season_response = requests.get(season_url, params=season_params, timeout=10)
                    
                    if season_response.status_code == 200:
                        season_data = season_response.json()
                        tmdb_episodes_count = len(season_data.get('episodes', []))
                        
                        # 标准化标题比较
                        local_title_normalized = str(local_title).strip() if local_title is not None else ''
                        tmdb_title_normalized = str(tmdb_title).strip() if tmdb_title is not None else ''
                        
                        # 更新标题（如果需要）
                        if tmdb_title_normalized != local_title_normalized:
                            cursor.execute('UPDATE MISS_TVS SET title = ? WHERE id = ?', 
                                         (tmdb_title_normalized, tv_id))
                            logging.info(f"已更新电视剧标题: '{local_title_normalized}' -> '{tmdb_title_normalized}' (TMDB ID: {tmdb_id})")
                        
                        # 检查并更新集数信息
                        if missing_episodes:
                            try:
                                local_missing_episodes_set = set(int(ep) for ep in missing_episodes.split(',') if ep.strip().isdigit())
                                
                                # 获取 TMDB 上的所有集数
                                tmdb_episodes_set = set(range(1, tmdb_episodes_count + 1))
                                
                                # 获取本地已存在的集数（从 LIB_TV_SEASONS 表中获取）
                                existing_episodes_str = cursor.execute(
                                    '''SELECT episodes FROM LIB_TV_SEASONS WHERE tv_id = (SELECT id FROM LIB_TVS WHERE title = ? AND year = ?) AND season = ?''',
                                    (local_title, year, season)
                                ).fetchone()
                                
                                if existing_episodes_str:
                                    val = existing_episodes_str[0]
                                    if isinstance(val, int):
                                        existing_episodes = set([val])
                                    elif isinstance(val, str):
                                        existing_episodes = set(int(ep) for ep in val.split(',') if ep.strip().isdigit())
                                    else:
                                        existing_episodes = set()
                                else:
                                    existing_episodes = set()
                                
                                # 计算新的缺失集数：TMDB 上的所有集数 - 本地已存在的集数
                                new_missing_episodes_set = tmdb_episodes_set - existing_episodes
                                new_missing_episodes_str = ','.join(map(str, sorted(new_missing_episodes_set)))
                                
                                # 如果缺失集数有变化，则更新
                                if set(local_missing_episodes_set) != new_missing_episodes_set:
                                    cursor.execute('UPDATE MISS_TVS SET missing_episodes = ? WHERE id = ?', 
                                                (new_missing_episodes_str, tv_id))
                                    logging.info(f"已更新电视剧 '{tmdb_title_normalized}' 第{season}季的缺失集数: {sorted(local_missing_episodes_set)} -> {sorted(new_missing_episodes_set)}")
                            except ValueError as e:
                                logging.error(f"处理电视剧 '{local_title}' 集数时发生错误: {e}")
                    else:
                        logging.error(f"获取电视剧 '{local_title}' 第{season}季信息失败，状态码: {season_response.status_code}")
                else:
                    logging.warning(f"未找到电视剧 '{local_title}' 的TMDB信息")
            else:
                logging.error(f"搜索电视剧 '{local_title}' 信息失败，状态码: {response.status_code}")
        except requests.RequestException as e:
            logging.error(f"请求TMDB API时发生错误: {e}")
        except Exception as e:
            logging.error(f"处理电视剧 '{local_title}' 时发生未知错误: {e}")
        
        # 随机休眠避免频繁请求
        sleep_time = random.uniform(1, 3)
        time.sleep(sleep_time)
    
    if not movies_without_douban and not tvs_without_douban:
        logging.info("没有需要检查的TMDB项目")
    else:
        logging.info(f"共检查了 {len(movies_without_douban)} 个电影和 {len(tvs_without_douban)} 个电视剧的TMDB信息")

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
        # 检查并更新豆瓣订阅项目的标题
        update_miss_titles(cursor)

        # 检查并更新TMDB订阅项目的标题和集数
        update_tmdb_items(cursor)

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