import os
import re
import xml.etree.ElementTree as ET
import logging
import sqlite3
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/tmdb_id.log"),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

def validate_directory_path(directory, context):
    """验证目录路径是否存在，若不存在则记录错误日志"""
    if not os.path.exists(directory):
        logging.error(f"{context} 路径不存在: {directory}")
        return False
    return True

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

def parse_nfo(file_path):
    """解析NFO文件，返回title, year和tmdb id"""
    logging.debug(f"解析NFO文件: {file_path}")
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 查找<title>元素
        title_element = root.find('title')
        title = title_element.text.strip().lower() if title_element is not None else None
        
        # 查找<year>元素
        year_element = root.find('year')
        year = year_element.text.strip() if year_element is not None else None
        
        # 查找<uniqueid type="tmdb">元素
        tmdb_id_element = root.find(".//uniqueid[@type='tmdb']")
        tmdb_id = tmdb_id_element.text.strip() if tmdb_id_element is not None else None
        
        logging.debug(f"解析结果: 标题: {title}, 年份: {year}, tmdb_id: {tmdb_id}")
        return title, year, tmdb_id
    except Exception as e:
        logging.error(f"解析 {file_path} 时出错: {e}")
        return None, None, None

def find_and_parse_nfo_files(directory, title, year):
    """在给定目录中查找所有NFO文件并解析它们，返回匹配的tmdb_id"""
    if not validate_directory_path(directory, "NFO文件搜索目录"):
        return None

    logging.info(f"在目录 {directory} 中查找所有NFO文件，标题: {title}, 年份: {year}")
    title = title.lower().strip()
    year = str(year).strip()  # 确保 year 是字符串类型
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.nfo'):
                file_path = os.path.join(root, file)
                parsed_title, parsed_year, tmdb_id = parse_nfo(file_path)
                if parsed_title == title and parsed_year == year:
                    logging.info(f"找到匹配的NFO文件: {file_path}, tmdb_id: {tmdb_id}")
                    return tmdb_id
                else:
                    logging.debug(f"不匹配的NFO文件: {file_path}, 解析标题: {parsed_title}, 解析年份: {parsed_year}")
    logging.info(f"未找到匹配的NFO文件，标题: {title}, 年份: {year}")
    return None

def query_tmdb_api(title, year, media_type, config):
    """通过TMDB API查询获取tmdb_id"""
    TMDB_API_KEY = config['tmdb']['api_key']
    TMDB_BASE_URL = config['tmdb']['base_url'].rstrip('/') + '/3'
    url = f"{TMDB_BASE_URL}/search/{media_type}"
    params = {
        'api_key': TMDB_API_KEY,
        'query': title,
        'language': 'zh-CN',
        'include_adult': 'false'
    }
    logging.info(f"通过TMDB API查询 {title} 获取tmdb_id")
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        search_results = response.json().get('results', [])
        for result in search_results:
            if media_type == 'movie':
                release_date = result.get('release_date', '')
                if release_date and release_date.startswith(str(year)):
                    logging.info(f"找到匹配的电影, tmdb_id: {result.get('id')}")
                    return result.get('id')
            elif media_type == 'tv':
                first_air_date = result.get('first_air_date', '')
                if first_air_date and first_air_date.startswith(str(year)):
                    logging.info(f"找到匹配的电视剧, tmdb_id: {result.get('id')}")
                    return result.get('id')
    except Exception as e:
        logging.error(f"查询TMDB API时出错: {e}")
    logging.info(f"未找到匹配的tmdb_id, 标题: {title}, 年份: {year}")
    return None

def update_database(db_path, table, title, year, tmdb_id):
    """更新数据库中的tmdb_id字段"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 检查是否存在tmdb_id字段，如果不存在则创建
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [column[1] for column in cursor.fetchall()]
    if 'tmdb_id' not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN tmdb_id TEXT")
        logging.info(f"在表 {table} 中添加了 tmdb_id 字段")
    
    # 查询是否存在相同的title和year
    cursor.execute(f"SELECT tmdb_id FROM {table} WHERE title = ? AND year = ?", (title, year))
    row = cursor.fetchone()
    
    if row:
        existing_tmdb_id = row[0]
        if existing_tmdb_id:
            logging.debug(f"跳过处理：标题 '{title}'，年份 '{year}' 和 tmdb_id '{existing_tmdb_id}'")
            return
        
        # 更新tmdb_id字段
        cursor.execute(f"UPDATE {table} SET tmdb_id = ? WHERE title = ? AND year = ?", (tmdb_id, title, year))
        conn.commit()
        logging.info(f"更新数据库记录：标题: {title}, 年份: {year}, tmdb_id: {tmdb_id}")
    else:
        logging.info(f"在表 {table} 中未找到标题 '{title}' 和年份 '{year}'")
    
    conn.close()

def fetch_data_without_tmdb_id(db_path, table):
    """从数据库中获取没有tmdb_id的数据"""
    logging.debug(f"从数据库 {db_path} 获取没有tmdb_id的数据, 表: {table}")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(f"SELECT title, year FROM {table} WHERE tmdb_id IS NULL OR tmdb_id = ''")
    rows = cursor.fetchall()
    conn.close()
    logging.debug(f"获取到 {len(rows)} 条没有tmdb_id的数据")
    return rows

def main():
    db_path = '/config/data.db'

    # 检查数据库路径是否存在
    if not validate_directory_path(os.path.dirname(db_path), "数据库文件所在目录"):
        return

    try:
        # 从数据库中读取路径配置
        try:
            movies_path = read_config_from_db(db_path, 'movies_path')
            episodes_path = read_config_from_db(db_path, 'episodes_path')
            
            # 初始化 config 变量
            tmdb_api_key = read_config_from_db(db_path, 'tmdb_api_key')
            tmdb_base_url = read_config_from_db(db_path, 'tmdb_base_url')
            config = {
                'tmdb': {
                    'api_key': tmdb_api_key,
                    'base_url': tmdb_base_url
                }
            }
        except ValueError as e:
            logging.error(e)
            return

        # 验证路径有效性
        if not validate_directory_path(movies_path, "电影媒体库路径"):
            movies_path = None
        if not validate_directory_path(episodes_path, "电视剧媒体库路径"):
            episodes_path = None

        # 获取数据库中没有tmdb_id的电影记录
        movies_without_tmdb_id = fetch_data_without_tmdb_id(db_path, 'LIB_MOVIES')

        # 获取数据库中没有tmdb_id的电视剧记录
        episodes_without_tmdb_id = fetch_data_without_tmdb_id(db_path, 'LIB_TVS')

        # 处理电影记录
        if movies_path:
            for title, year in movies_without_tmdb_id:
                logging.info(f"处理电影记录, 标题: {title}, 年份: {year}")
                # 尝试从NFO文件中读取tmdb_id
                tmdb_id = find_and_parse_nfo_files(movies_path, title, year)
                if not tmdb_id:
                    # 调用TMDB API获取tmdb_id，传入 config
                    tmdb_id = query_tmdb_api(title, year, 'movie', config)
                update_database(db_path, 'LIB_MOVIES', title, year, tmdb_id)

        # 处理电视剧记录
        if episodes_path:
            for title, year in episodes_without_tmdb_id:
                logging.info(f"处理电视剧记录, 标题: {title}, 年份: {year}")
                # 尝试从NFO文件中读取tmdb_id
                tmdb_id = find_and_parse_nfo_files(episodes_path, title, year)
                if not tmdb_id:
                    # 调用TMDB API获取tmdb_id，传入 config
                    tmdb_id = query_tmdb_api(title, year, 'tv', config)
                update_database(db_path, 'LIB_TVS', title, year, tmdb_id)

    except Exception as e:
        logging.error(f"主程序运行时发生错误: {e}")

if __name__ == "__main__":
    main()