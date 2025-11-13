import os
import re
import sqlite3
import logging
import xml.etree.ElementTree as ET

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/scan_media.log", mode='w'),  # 输出到文件并清空之前的日志
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

def scan_movies(path):
    movies = []
    media_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.iso']
    
    # 多种电影命名格式
    movie_patterns = [
        re.compile(r'^(.*?)\s*-\s*\((\d{4})\)'),     # Title - (Year)
        re.compile(r'^(.*?)\s*\((\d{4})\)'),         # Title (Year)
        re.compile(r'^(.*?)\s*\[([12]\d{3})\]'),     # Title [Year]
        re.compile(r'^(.*?)\s*([12]\d{3})\s*-'),     # Title Year -
        re.compile(r'^(.*?)\s*\.\s*([12]\d{3})\s*\.'), # Title.Year.
    ]

    for root, _, files in os.walk(path):
        for file in files:
            if any(file.lower().endswith(ext) for ext in media_extensions):
                matched = False
                for pattern in movie_patterns:
                    match = pattern.match(os.path.splitext(file)[0])
                    if match:
                        movie_name = match.group(1).strip()
                        year = int(match.group(2))
                        tmdb_id = None
                        
                        # 检查NFO文件
                        media_file_name = os.path.splitext(file)[0]
                        nfo_file_path = os.path.join(root, media_file_name + '.nfo')
                        if os.path.exists(nfo_file_path):
                            try:
                                tree = ET.parse(nfo_file_path)
                                root_element = tree.getroot()
                                uniqueid_elements = root_element.findall('uniqueid')
                                for uniqueid in uniqueid_elements:
                                    if uniqueid.attrib.get('type') == 'tmdb':
                                        tmdb_id = uniqueid.text.strip()
                                        break
                            except ET.ParseError:
                                logging.warning(f"无法解析 NFO 文件: {nfo_file_path}")

                        movies.append((movie_name, year, tmdb_id))
                        matched = True
                        break
                
                if not matched:
                    logging.warning(f"无法从文件名提取标题和年份: {file}")

    return movies

def scan_episodes(path):
    episodes = {}
    
    # 多种季目录命名格式
    season_patterns = [
        re.compile(r'^Season\s+(\d+)$', re.IGNORECASE),    # Season 1
        re.compile(r'^S(\d+)$', re.IGNORECASE),            # S01
        re.compile(r'^Season\.?(\d+)$', re.IGNORECASE),    # Season1 or Season.1
        re.compile(r'^第(\d+)季$', re.IGNORECASE),          # 第1季 (中文)
    ]
    
    episode_pattern = re.compile(r'^(.*) - S(\d+)E(\d+) - (.*)$', re.IGNORECASE)
    episode_pattern_alt = re.compile(r'^(.*)\.S(\d+)E(\d+)\.(.*)$', re.IGNORECASE)  # 支持点号分隔

    for root, dirs, files in os.walk(path):
        # 检查 tvshow.nfo
        if 'tvshow.nfo' in files:
            tvshow_nfo_path = os.path.join(root, 'tvshow.nfo')
            try:
                tree = ET.parse(tvshow_nfo_path)
                root_element = tree.getroot()

                title_element = root_element.find('title')
                if title_element is not None:
                    show_name = title_element.text.strip()
                else:
                    logging.warning(f"tvshow.nfo 文件中未找到标题元素: {tvshow_nfo_path}")
                    continue

                tmdb_id = None
                uniqueid_elements = root_element.findall('uniqueid')
                for uniqueid in uniqueid_elements:
                    if uniqueid.attrib.get('type') == 'tmdb':
                        tmdb_id = uniqueid.text.strip()
                        break

                if show_name not in episodes:
                    episodes[show_name] = {'tmdb_id': tmdb_id, 'seasons': {}}

                # 支持多种季目录格式
                for dir_name in dirs:
                    season_number = None
                    for pattern in season_patterns:
                        match = pattern.match(dir_name)
                        if match:
                            season_number = int(match.group(1))
                            break
                    
                    if season_number is not None:
                        season_path = os.path.join(root, dir_name)
                        season_nfo_path = os.path.join(season_path, 'season.nfo')

                        year = None
                        if os.path.exists(season_nfo_path):
                            try:
                                season_tree = ET.parse(season_nfo_path)
                                season_root = season_tree.getroot()

                                season_number_element = season_root.find('seasonnumber')
                                if season_number_element is not None and season_number_element.text is not None:
                                    season_number = int(season_number_element.text.strip())

                                year_element = season_root.find('year')
                                if year_element is not None and year_element.text is not None:
                                    year = int(year_element.text.strip())
                                else:
                                    releasedate_element = season_root.find('releasedate')
                                    if releasedate_element is not None and releasedate_element.text:
                                        date_text = releasedate_element.text.strip()
                                        match = re.match(r'(\d{4})', date_text)
                                        if match:
                                            year_str = match.group(1)
                                            if not year_str.startswith('000'):
                                                year = int(year_str)

                                if season_number not in episodes[show_name]['seasons']:
                                    episodes[show_name]['seasons'][season_number] = {'year': year, 'episodes': []}

                                # 扫描该季的媒体文件
                                for file in os.listdir(season_path):
                                    if file.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.iso')):
                                        episode_match = episode_pattern.match(os.path.splitext(file)[0])
                                        if not episode_match:
                                            episode_match = episode_pattern_alt.match(os.path.splitext(file)[0])
                                        
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

        # 处理直接在根目录下的剧集文件
        for file in files:
            if file.lower().endswith(('.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.iso')):
                episode_match = episode_pattern.match(os.path.splitext(file)[0])
                if not episode_match:
                    episode_match = episode_pattern_alt.match(os.path.splitext(file)[0])
                
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

    return episodes

def insert_or_update_movies(db_path, movies):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    for title, year, tmdb_id in movies:
        cursor.execute('SELECT id, tmdb_id FROM LIB_MOVIES WHERE title = ? AND year = ?', (title, year))
        existing_movie = cursor.fetchone()
        
        if existing_movie:
            logging.debug(f"电影 '{title} ({year})' 已存在于数据库中。")
            existing_tmdb_id = existing_movie[1]
            
            # 添加调试日志
            logging.debug(f"比较 TMDB ID: 当前值={tmdb_id}, 数据库值={existing_tmdb_id}")
            
            # 确保数据类型一致
            if tmdb_id and str(tmdb_id).strip() != str(existing_tmdb_id).strip():
                cursor.execute('UPDATE LIB_MOVIES SET tmdb_id = ? WHERE id = ?', (tmdb_id, existing_movie[0]))
                logging.info(f"已更新电影 '{title} ({year})' 的 TMDB ID: {tmdb_id}")
            else:
                logging.debug(f"电影 '{title} ({year})' 的 TMDB ID 未发生变化。")
        else:
            cursor.execute('INSERT INTO LIB_MOVIES (title, year, tmdb_id) VALUES (?, ?, ?)', (title, year, tmdb_id))
            logging.info(f"已将电影 '{title} ({year})' 插入数据库。")

    conn.commit()
    conn.close()

def insert_or_update_episodes(db_path, episodes):
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
                
                # 确保 existing_episodes_str 是字符串
                if isinstance(existing_episodes_str, int):
                    existing_episodes_str = str(existing_episodes_str)
                    logging.warning(f"集数字符串被错误地存储为整数，已转换为字符串: {existing_episodes_str}")

                if existing_episodes_str:  # 检查是否为空字符串
                    existing_episodes = set(map(int, existing_episodes_str.split(',')))
                else:
                    existing_episodes = set()
                    logging.debug(f"现有集数为空，初始化为空集。")

                # 检查数据库中季年份是否为空，如果为空且本次扫描到季年份，则更新
                cursor.execute('SELECT year FROM LIB_TV_SEASONS WHERE id = ?', (existing_season[0],))
                db_year = cursor.fetchone()[0]
                if (db_year is None or db_year == 0 or db_year == '') and year:
                    cursor.execute('UPDATE LIB_TV_SEASONS SET year = ? WHERE id = ?', (year, existing_season[0]))
                    logging.info(f"已更新电视剧 '{show_name}' 第 {season} 季的年份：{year}")

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

def delete_obsolete_movies(db_path, current_movies):
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

def delete_obsolete_episodes(db_path, current_episodes):
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
            cursor.execute('SELECT id, season, episodes FROM LIB_TV_SEASONS WHERE tv_id = ?', (tv_id,))
            all_seasons = cursor.fetchall()

            for season_id, season, episodes_str in all_seasons:
                # 类型检查，确保 episodes_str 是字符串
                if isinstance(episodes_str, int):
                    episodes_str = str(episodes_str)
                    logging.warning(f"电视剧 '{title}' 第 {season} 季的集数字段为整数，已转换为字符串: {episodes_str}")

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

                # 获取当前扫描到的集数，如果不存在则默认为空集
                current_episodes_for_season = current_episodes.get(title, {}).get('seasons', {}).get(season, {}).get('episodes', [])
                current_episodes_set = set(current_episodes_for_season)

                # 检查是否有集数被删除
                removed_episodes = existing_episodes - current_episodes_set
                if removed_episodes:
                    # 从数据库中移除被删除的集数
                    updated_episodes = existing_episodes - removed_episodes
                    if updated_episodes:
                        updated_episodes_str = ','.join(map(str, sorted(updated_episodes)))
                        cursor.execute('UPDATE LIB_TV_SEASONS SET episodes = ? WHERE id = ?', (updated_episodes_str, season_id))
                        logging.info(f"已从电视剧 '{title}' 第 {season} 季中移除集数: {sorted(removed_episodes)}")
                    else:
                        # 如果该季所有集数都被删除，则删除该季记录
                        cursor.execute('DELETE FROM LIB_TV_SEASONS WHERE id = ?', (season_id,))
                        logging.info(f"电视剧 '{title}' 第 {season} 季所有集数已被删除，移除该季记录。")
                else:
                    logging.debug(f"电视剧 '{title}' 第 {season} 季没有集数被删除。")

    conn.commit()
    conn.close()

def update_tv_year(base_path, db_path):
    # 正则表达式用于匹配电视剧标题和年份
    pattern = re.compile(r'^(.*)\s+\((\d{4})\)')
    
    def scan_directories(path):
        # 检查路径是否存在
        if not os.path.exists(path):
            logging.warning(f"路径不存在，跳过扫描: {path}")
            return []
        
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

    # 扫描目录并提取信息
    shows = scan_directories(base_path)
    
    # 更新数据库
    if shows:  # 只有当有数据时才更新数据库
        update_database(db_path, shows)

def main():
    db_path = '/config/data.db'
    config = load_config(db_path)
    movies_path = config['movies_path']
    episodes_path = config['episodes_path']
    anime_path = config.get('anime_path', episodes_path)  # 如果没有设置动漫路径，则使用电视剧路径
    variety_path = config.get('variety_path', episodes_path)  # 如果没有设置综艺路径，则使用电视剧路径

    # 扫描电影目录
    if os.path.exists(movies_path):
        movies = scan_movies(movies_path)
        # 插入或更新电影数据
        insert_or_update_movies(db_path, movies)
        # 删除数据库中多余的电影记录
        delete_obsolete_movies(db_path, movies)
    else:
        logging.warning(f"电影目录不存在: {movies_path}")

    # 收集所有电视剧类型的媒体（电视剧、动漫、综艺）
    all_episodes = {}

    # 扫描电视剧目录
    if os.path.exists(episodes_path):
        episodes = scan_episodes(episodes_path)
        # 合并到all_episodes
        for show_name, show_info in episodes.items():
            all_episodes[show_name] = show_info
    else:
        logging.warning(f"电视剧目录不存在: {episodes_path}")

    # 扫描动漫目录
    if os.path.exists(anime_path) and anime_path != episodes_path:
        anime_episodes = scan_episodes(anime_path)
        # 合并到all_episodes
        for show_name, show_info in anime_episodes.items():
            all_episodes[show_name] = show_info
    elif anime_path != episodes_path:
        logging.warning(f"动漫目录不存在: {anime_path}")

    # 扫描综艺目录
    if os.path.exists(variety_path) and variety_path != episodes_path:
        variety_episodes = scan_episodes(variety_path)
        # 合并到all_episodes
        for show_name, show_info in variety_episodes.items():
            all_episodes[show_name] = show_info
    elif variety_path != episodes_path:
        logging.warning(f"综艺目录不存在: {variety_path}")

    # 插入或更新电视剧数据
    if all_episodes:
        insert_or_update_episodes(db_path, all_episodes)
        # 删除数据库中多余的电视剧记录
        delete_obsolete_episodes(db_path, all_episodes)
    
    # 更新电视剧年份信息（对所有目录进行操作）
    if os.path.exists(episodes_path):
        update_tv_year(episodes_path, db_path)
    if os.path.exists(anime_path):
        update_tv_year(anime_path, db_path)
    if os.path.exists(variety_path):
        update_tv_year(variety_path, db_path)

if __name__ == "__main__":
    main()