import os
import re
import sqlite3
import logging
import xml.etree.ElementTree as ET

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/scan_media.log"),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

def read_config_from_db(db_path, option):
    """
    从数据库 CONFIG 表中读取指定配置项的值。
    
    :param db_path: 数据库文件路径
    :param option: 配置项名称
    :return: 配置项的值
    """
    if not os.path.exists(db_path):
        logging.error(f"数据库文件不存在: {db_path}")
        raise FileNotFoundError(f"数据库文件不存在: {db_path}")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 查询 CONFIG 表中的值
        cursor.execute("SELECT VALUE FROM CONFIG WHERE OPTION = ?", (option,))
        result = cursor.fetchone()
        
        conn.close()
        
        if result:
            return result[0]
        else:
            raise ValueError(f"未在数据库中找到配置项: {option}")
    except Exception as e:
        logging.error(f"读取数据库配置时发生错误: {e}")
        raise

def scan_directories(path):
    # 检查路径是否存在
    if not os.path.exists(path):
        logging.error(f"指定的路径不存在: {path}")
        return []

    try:
        # 获取所有文件夹名称
        directories = [name for name in os.listdir(path) if os.path.isdir(os.path.join(path, name))]
        
        # 解析每个文件夹名称
        shows = []
        pattern = re.compile(r'^(.*)\s+\((\d{4})\)')
        for directory in directories:
            match = pattern.match(directory)
            if match:
                title = match.group(1).strip()
                year = int(match.group(2))
                shows.append({'title': title, 'year': year})
        
        return shows
    except Exception as e:
        logging.error(f"扫描目录时发生错误: {e}")
        return []

def scan_movies(path):
    # 检查路径是否存在
    if not os.path.exists(path):
        logging.error(f"电影目录不存在: {path}")
        return []

    movies = []

    # 定义所有可能的媒体文件后缀
    media_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.webm']

    try:
        for root, _, files in os.walk(path):
            nfo_files = [f for f in files if f.lower().endswith('.nfo')]

            for nfo_file in nfo_files:
                nfo_path = os.path.join(root, nfo_file)
                try:
                    tree = ET.parse(nfo_path)
                    root_element = tree.getroot()

                    # 提取电影标题
                    title_element = root_element.find('title')
                    if title_element is not None:
                        movie_name = title_element.text.strip()
                    else:
                        logging.warning(f"NFO 文件中未找到标题元素: {nfo_path}")
                        continue

                    # 提取电影年份
                    year_element = root_element.find('year')
                    if year_element is not None:
                        year = int(year_element.text.strip())  # 确保 year 是整数类型
                    else:
                        logging.warning(f"NFO 文件中未找到年份元素: {nfo_path}")
                        continue

                    # 提取 TMDB ID
                    tmdb_id = None
                    uniqueid_elements = root_element.findall('uniqueid')
                    for uniqueid in uniqueid_elements:
                        if uniqueid.attrib.get('type') == 'tmdb':
                            tmdb_id = uniqueid.text.strip()
                            break

                    # 构建媒体文件名
                    media_file_name = nfo_file[:-4]  # 去掉 .nfo 后缀

                    # 检查是否存在匹配的媒体文件
                    media_file_found = False
                    for ext in media_extensions:
                        media_file_path = os.path.join(root, media_file_name + ext)
                        if os.path.exists(media_file_path):
                            movies.append((movie_name, year, tmdb_id))  # 确保 year 是整数类型
                            media_file_found = True
                            break

                    if not media_file_found:
                        logging.warning(f"未找到匹配的媒体文件: {nfo_path}")

                except ET.ParseError:
                    logging.warning(f"无法解析 NFO 文件: {nfo_path}")
                    continue
    except Exception as e:
        logging.error(f"扫描电影目录时发生错误: {e}")

    return movies

def scan_episodes(path):
    # 检查路径是否存在
    if not os.path.exists(path):
        logging.error(f"电视剧目录不存在: {path}")
        return {}

    episodes = {}

    try:
        for root, dirs, files in os.walk(path):
            # 检查是否存在 tvshow.nfo 文件
            if 'tvshow.nfo' in files:
                tvshow_nfo_path = os.path.join(root, 'tvshow.nfo')
                try:
                    tree = ET.parse(tvshow_nfo_path)
                    root_element = tree.getroot()

                    # 提取电视剧标题
                    title_element = root_element.find('title')
                    if title_element is not None:
                        show_name = title_element.text.strip()
                    else:
                        logging.warning(f"tvshow.nfo 文件中未找到标题元素: {tvshow_nfo_path}")
                        continue

                    # 提取 TMDB ID
                    tmdb_id = None
                    uniqueid_elements = root_element.findall('uniqueid')
                    for uniqueid in uniqueid_elements:
                        if uniqueid.attrib.get('type') == 'tmdb':
                            tmdb_id = uniqueid.text.strip()
                            break

                    # 初始化电视剧信息
                    if show_name not in episodes:
                        episodes[show_name] = {'tmdb_id': tmdb_id, 'seasons': {}}

                    # 检查每个子目录（假设为 Season X）
                    for dir_name in dirs:
                        if dir_name.lower().startswith('season '):
                            season_number = int(dir_name.split(' ')[1])
                            season_path = os.path.join(root, dir_name)
                            season_nfo_path = os.path.join(season_path, 'season.nfo')

                            if os.path.exists(season_nfo_path):
                                try:
                                    season_tree = ET.parse(season_nfo_path)
                                    season_root = season_tree.getroot()

                                    # 提取季编号
                                    season_number_element = season_root.find('seasonnumber')
                                    if season_number_element is not None and season_number_element.text is not None:
                                        season_number = int(season_number_element.text.strip())

                                    # 提取年份
                                    year_element = season_root.find('year')
                                    if year_element is not None and year_element.text is not None:
                                        year = int(year_element.text.strip())
                                    else:
                                        year = None
                                        logging.warning(f"season.nfo 文件中未找到年份元素或年份为空: {season_nfo_path}")

                                    # 初始化季信息
                                    if season_number not in episodes[show_name]['seasons']:
                                        episodes[show_name]['seasons'][season_number] = {'year': year, 'episodes': []}

                                    # 扫描该季的媒体文件
                                    for file in os.listdir(season_path):
                                        if file.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.webm')):
                                            episode_match = re.match(r'^(.*) - S(\d+)E(\d+) - (.*)\.(mkv|mp4|avi|mov|flv|wmv|webm)$', file, re.IGNORECASE)
                                            if episode_match:
                                                episode_number = int(episode_match.group(3))
                                                if episode_number not in episodes[show_name]['seasons'][season_number]['episodes']:
                                                    episodes[show_name]['seasons'][season_number]['episodes'].append(episode_number)
                                except ET.ParseError:
                                    logging.warning(f"无法解析 season.nfo 文件: {season_nfo_path}")
                                    continue
                except ET.ParseError:
                    logging.warning(f"无法解析 tvshow.nfo 文件: {tvshow_nfo_path}")
                    continue

            for file in files:
                # 将文件扩展名转换为小写
                if file.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.webm')):
                    # 匹配电视剧文件名模式
                    episode_match = re.match(r'^(.*) - S(\d+)E(\d+) - (.*)\.(mkv|mp4|avi|mov|flv|wmv|webm)$', file, re.IGNORECASE)
                    if episode_match:
                        show_name = episode_match.group(1).strip()
                        season = int(episode_match.group(2))
                        episode = int(episode_match.group(3))

                        if show_name not in episodes:
                            episodes[show_name] = {'tmdb_id': None, 'seasons': {}}
                        if season not in episodes[show_name]['seasons']:
                            episodes[show_name]['seasons'][season] = {'year': None, 'episodes': []}

                        if episode not in episodes[show_name]['seasons'][season]['episodes']:
                            episodes[show_name]['seasons'][season]['episodes'].append(episode)
    except Exception as e:
        logging.error(f"扫描电视剧目录时发生错误: {e}")

    return episodes

def insert_or_update_movies(db_path, movies):
    if not os.path.exists(db_path):
        logging.error(f"数据库文件不存在: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for title, year, tmdb_id in movies:
            cursor.execute('SELECT id, tmdb_id FROM LIB_MOVIES WHERE title = ? AND year = ?', (title, year))
            existing_movie = cursor.fetchone()
            
            if existing_movie:
                logging.debug(f"电影 '{title} ({year})' 已存在于数据库中。")
                existing_tmdb_id = existing_movie[1]
                if tmdb_id and tmdb_id != existing_tmdb_id:
                    cursor.execute('UPDATE LIB_MOVIES SET tmdb_id = ? WHERE id = ?', (tmdb_id, existing_movie[0]))
                    logging.info(f"已更新电影 '{title} ({year})' 的 TMDB ID: {tmdb_id}")
                else:
                    logging.debug(f"电影 '{title} ({year})' 的 TMDB ID 未发生变化。")
            else:
                cursor.execute('INSERT INTO LIB_MOVIES (title, year, tmdb_id) VALUES (?, ?, ?)', (title, year, tmdb_id))
                logging.info(f"已将电影 '{title} ({year})' 插入数据库。")

        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"插入或更新电影数据时发生错误: {e}")

def insert_or_update_episodes(db_path, episodes):
    if not os.path.exists(db_path):
        logging.error(f"数据库文件不存在: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        for show_name, show_info in episodes.items():
            tmdb_id = show_info['tmdb_id']
            cursor.execute('SELECT id FROM LIB_TVS WHERE title = ?', (show_name,))
            existing_tv = cursor.fetchone()
            if existing_tv:
                tv_id = existing_tv[0]
                logging.debug(f"电视剧 '{show_name}' 已存在于数据库中。")
            else:
                cursor.execute('INSERT INTO LIB_TVS (title, tmdb_id) VALUES (?, ?)', (show_name, tmdb_id))
                tv_id = cursor.lastrowid
                logging.info(f"已将电视剧 '{show_name}' 插入数据库。")

            for season, season_info in show_info['seasons'].items():
                year = season_info['year']
                current_episodes = set(season_info['episodes'])

                cursor.execute('SELECT id, episodes FROM LIB_TV_SEASONS WHERE tv_id = ? AND season = ?', (tv_id, season))
                existing_season = cursor.fetchone()

                if existing_season:
                    existing_episodes_str = existing_season[1]
                    logging.debug(f"现有集数字符串: {existing_episodes_str}")
                    if existing_episodes_str:  # 检查是否为空字符串
                        existing_episodes = set(map(int, existing_episodes_str.split(',')))
                    else:
                        existing_episodes = set()
                        logging.debug(f"现有集数为空，初始化为空集。")

                    # 只更新新增的集数
                    new_episodes = current_episodes - existing_episodes
                    if new_episodes:
                        updated_episodes_str = ','.join(map(str, sorted(existing_episodes.union(new_episodes))))
                        cursor.execute('UPDATE LIB_TV_SEASONS SET episodes = ?, year = ? WHERE id = ?', (updated_episodes_str, year, existing_season[0]))
                        logging.info(f"已更新电视剧 '{show_name}' 第 {season} 季的集数和年份：{updated_episodes_str}, {year}")
                    else:
                        logging.debug(f"电视剧 '{show_name}' 第 {season} 季已是最新状态。")
                else:
                    episodes_str = ','.join(map(str, sorted(current_episodes)))
                    cursor.execute('INSERT INTO LIB_TV_SEASONS (tv_id, season, year, episodes) VALUES (?, ?, ?, ?)', (tv_id, season, year, episodes_str))
                    logging.info(f"已将电视剧 '{show_name}' 第 {season} 季的集数 {episodes_str} 和年份 {year} 插入数据库。")

        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"插入或更新电视剧数据时发生错误: {e}")

def delete_obsolete_movies(db_path, current_movies):
    if not os.path.exists(db_path):
        logging.error(f"数据库文件不存在: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT title, year FROM LIB_MOVIES')
        all_movies = cursor.fetchall()

        # 将 current_movies 转换为 {(title, year)} 集合，并确保 year 是整数类型
        current_movies_set = {(title, int(year)) for title, year, _ in current_movies}

        for title, year in all_movies:
            if (title, year) not in current_movies_set:
                cursor.execute('DELETE FROM LIB_MOVIES WHERE title = ? AND year = ?', (title, year))
                logging.info(f"已从数据库中删除电影 '{title} ({year})'。")

        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"删除多余电影记录时发生错误: {e}")

def delete_obsolete_episodes(db_path, current_episodes):
    if not os.path.exists(db_path):
        logging.error(f"数据库文件不存在: {db_path}")
        return

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT id, title FROM LIB_TVS')
        all_shows = cursor.fetchall()

        for tv_id, title in all_shows:
            if title not in current_episodes:
                cursor.execute('DELETE FROM LIB_TV_SEASONS WHERE tv_id = ?', (tv_id,))
                cursor.execute('DELETE FROM LIB_TVS WHERE id = ?', (tv_id,))
                logging.info(f"已从数据库中删除电视剧 '{title}' 及其所有季。")
            else:
                cursor.execute('SELECT season, episodes FROM LIB_TV_SEASONS WHERE tv_id = ?', (tv_id,))
                all_seasons = cursor.fetchall()

                for season, episodes_str in all_seasons:
                    # 检查 episodes_str 是否为空字符串
                    if not episodes_str or not episodes_str.strip():
                        existing_episodes = set()
                        logging.warning(f"电视剧 '{title}' 第 {season} 季的集数为空，初始化为空集。")
                    else:
                        try:
                            existing_episodes = set(map(int, episodes_str.split(',')))
                        except ValueError as e:
                            logging.error(f"无法解析电视剧 '{title}' 第 {season} 季的集数: {episodes_str}. 错误: {e}")
                            existing_episodes = set()

                    current_episodes_set = set(current_episodes[title].get(season, {}).get('episodes', []))

                    if not current_episodes_set.issubset(existing_episodes):
                        cursor.execute('DELETE FROM LIB_TV_SEASONS WHERE tv_id = ? AND season = ?', (tv_id, season))
                        logging.info(f"已从数据库中删除电视剧 '{title}' 第 {season} 季。")

        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"删除多余电视剧记录时发生错误: {e}")

def update_tv_year(episodes_path, db_path):
    if not os.path.exists(episodes_path):
        logging.error(f"电视剧目录不存在: {episodes_path}")
        return

    if not os.path.exists(db_path):
        logging.error(f"数据库文件不存在: {db_path}")
        return

    # 正则表达式用于匹配电视剧标题和年份
    pattern = re.compile(r'^(.*)\s+\((\d{4})\)')

    def scan_directories(path):
        # 获取所有文件夹名称
        directories = [name for name in os.listdir(path) if os.path.isdir(os.path.join(path, name))]
        
        # 解析每个文件夹名称
        shows = []
        for directory in directories:
            match = pattern.match(directory)
            if match:
                title = match.group(1).strip()
                year = int(match.group(2))
                shows.append({'title': title, 'year': year})
        
        return shows

    def update_database(db_path, shows):
        # 连接到数据库
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 更新数据库中的记录
        for show in shows:
            title = show['title']
            year = show['year']
            
            # 查询数据库中是否存在相同的标题和年份
            cursor.execute("SELECT id FROM LIB_TVS WHERE title = ? AND year = ?", (title, year))
            result = cursor.fetchone()
            
            if result:
                logging.debug(f"已存在相同数据，跳过更新：{title} ({year})")
            else:
                # 查询数据库中是否存在相同的标题
                cursor.execute("SELECT id FROM LIB_TVS WHERE title = ?", (title,))
                result = cursor.fetchone()
                
                if result:
                    show_id = result[0]
                    # 更新年份
                    cursor.execute("UPDATE LIB_TVS SET year = ? WHERE id = ?", (year, show_id))
                    logging.info(f"更新 {title} 的年份：{year}")
                else:
                    logging.warning(f"没有匹配条目：{title}")

        # 提交并关闭数据库连接
        conn.commit()
        conn.close()

    try:
        # 扫描目录并提取信息
        shows = scan_directories(episodes_path)
        
        # 更新数据库
        update_database(db_path, shows)
    except Exception as e:
        logging.error(f"更新电视剧年份时发生错误: {e}")

def main():
    db_path = '/config/data.db'

    # 检查数据库路径是否存在
    if not os.path.exists(db_path):
        logging.error(f"数据库文件不存在: {db_path}")
        return

    try:
        # 从数据库中读取路径配置
        try:
            movies_path = read_config_from_db(db_path, 'movies_path')
            episodes_path = read_config_from_db(db_path, 'episodes_path')
        except ValueError as e:
            logging.error(e)
            return

        # 扫描电影目录
        movies = scan_movies(movies_path)

        # 扫描电视剧目录
        episodes = scan_episodes(episodes_path)

        # 插入或更新电影数据
        insert_or_update_movies(db_path, movies)

        # 插入或更新电视剧数据
        insert_or_update_episodes(db_path, episodes)
        update_tv_year(episodes_path, db_path)

        # 删除数据库中多余的电影记录
        delete_obsolete_movies(db_path, movies)

        # 删除数据库中多余的电视剧记录
        delete_obsolete_episodes(db_path, episodes)
    except Exception as e:
        logging.error(f"主程序运行时发生错误: {e}")

if __name__ == "__main__":
    main()