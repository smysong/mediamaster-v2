import os
import xml.etree.ElementTree as ET
import sqlite3
import logging
import requests
import time
import re
import random

# 已处理文件列表文件路径
PROCESSED_FILES_FILE = '/config/processed_nfo_files.txt'

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/actor_nfo.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def load_config(db_path='/config/data.db'):
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

# 从配置文件中读取值
config = load_config()
key = config.get('douban_api_key', '')
cookie = config.get('douban_cookie', '')
directory = config.get('media_dir', '')
excluded_filenames = config.get('nfo_excluded_filenames', '').split(',')
excluded_subdir_keywords = config.get('nfo_excluded_subdir_keywords', '').split(',')

class DoubanAPI:
    def __init__(self, key: str, cookie: str) -> None:
        self.host = "https://frodo.douban.com/api/v2"
        self.key = key
        self.cookie = cookie
        self.mobileheaders = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.27(0x18001b33) NetType/WIFI Language/zh_CN",
            "Referer": "https://servicewechat.com/wx2f9b06c1de1ccfca/85/page-frame.html",
            "content-type": "application/json",
            "Connection": "keep-alive",
        }
        self.pcheaders = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
            "Referer": "https://movie.douban.com/",
            "Cookie": self.cookie,
            "Connection": "keep-alive",
        }

    @staticmethod
    def remove_season_info(title: str) -> str:
        # 移除标题中的 "第X季" 信息
        return re.sub(r'\s*第\d+季', '', title).strip()

    def get_douban_id(self, title: str, year: str = None, media_type: str = 'tv') -> list:
        url = f"https://movie.douban.com/j/subject_suggest?q={title}"
        try:
            response = requests.get(url, headers=self.pcheaders)
            response.raise_for_status()  # 检查请求是否成功
            data = response.json()

            if data and isinstance(data, list):
                logging.debug(f"原始数据: {data}")  # 输出原始数据
                logging.debug(f"标题: {title} 年份：{year} 类型：{media_type}")
                # 初步筛选：根据 media_type 和 episode 字段，并且考虑年份
                matches = []
                for item in data:
                    item_title = self.remove_season_info(item.get('title', ''))
                    item_year = item.get('year')
                    if media_type == 'movie' and not item.get('episode'):
                        if year is None or item_year == year:
                            matches.append(item)
                    elif media_type == 'tv':
                        if item.get('episode') or item.get('type') == 'movie':  # 考虑 type 为 movie 且包含 episode 的项
                            if year is None or item_year == year:
                                matches.append(item)

                logging.debug(f"初步筛选后的匹配项: {matches}")  # 输出初步筛选后的匹配项

                if len(matches) == 1:
                    # 如果只有一个匹配项，直接使用这个结果
                    logging.info("找到一个匹配项")
                    return [matches[0].get('id')]
                elif len(matches) > 1:
                    # 如果有多个匹配项，选择标题相同或匹配度最高的结果
                    best_match = None
                    highest_match_score = 0
                    for match in matches:
                        match_score = self.calculate_match_score(self.remove_season_info(title), self.remove_season_info(match.get('title', '')))
                        if match_score > highest_match_score:
                            highest_match_score = match_score
                            best_match = match
                    if best_match:
                        logging.info("找到一个最佳匹配项")
                        return [best_match.get('id')]
                    else:
                        logging.warning(f"未找到标题为 {title} 且年份为 {year} 的最佳匹配项")
                        sleep_time = random.uniform(15, 30)  # 随机休眠15到30秒
                        logging.info(f"随机休眠 {sleep_time:.2f} 秒")
                        time.sleep(sleep_time)
                else:
                    logging.warning(f"未找到标题为 {title} 的匹配项")
                    sleep_time = random.uniform(15, 30)  # 随机休眠15到30秒
                    logging.info(f"随机休眠 {sleep_time:.2f} 秒")
                    time.sleep(sleep_time)
            else:
                logging.warning(f"未找到标题为 {title} 的结果")
                sleep_time = random.uniform(15, 30)  # 随机休眠15到30秒
                logging.info(f"随机休眠 {sleep_time:.2f} 秒")
                time.sleep(sleep_time)
        except Exception as e:
            logging.error(f"获取豆瓣 ID 失败，标题: {title}，错误: {e}")
        return []
    def calculate_match_score(self, title1: str, title2: str) -> int:
        # 简单的匹配度计算，可以根据需要进行更复杂的实现
        return sum(title1.lower() in part for part in title2.lower().split())

    def imdb_get_douban_id(self, imdb_id: str) -> str:
        url = f"https://movie.douban.com/j/subject_suggest?q={imdb_id}"
        try:
            response = requests.get(url, headers=self.pcheaders)
            response.raise_for_status()  # 检查请求是否成功
            data = response.json()
            if data and isinstance(data, list) and len(data) > 0:
                # 直接提取第一个结果的豆瓣 ID
                douban_id = data[0].get('id')
                if douban_id:
                    logging.info(f"找到 IMDb ID 为 {imdb_id} 的豆瓣 ID: {douban_id}")
                    return douban_id
                else:
                    logging.warning(f"未找到 IMDb ID 为 {imdb_id} 的豆瓣 ID")
            else:
                logging.warning(f"未找到 IMDb ID 为 {imdb_id} 的结果")
        except Exception as e:
            logging.error(f"通过 IMDb ID 获取豆瓣 ID 失败，IMDb ID: {imdb_id}，错误: {e}")
        return None

    def get_celebrities(self, douban_id: str, media_type: str) -> dict:
        if media_type == 'movie':
            url = f"{self.host}/movie/{douban_id}/celebrities?apikey={self.key}"
        elif media_type == 'tv':
            url = f"{self.host}/tv/{douban_id}/celebrities?apikey={self.key}"
        else:
            logging.warning(f"不支持的媒体类型: {media_type}")
            return {}

        try:
            response = requests.get(url, headers=self.mobileheaders)
            response.raise_for_status()  # 检查请求是否成功
            data = response.json()
            if 'directors' in data or 'actors' in data:
                simplified_data = {
                    'directors': [],
                    'actors': []
                }
                
                for key in ['directors', 'actors']:
                    if key in data:
                        simplified_data[key] = [
                            {
                                'name': celeb.get('name', ''),
                                'roles': celeb.get('roles', []),
                                'character': celeb.get('character', ''),
                                'large': celeb.get('avatar', {}).get('large', ''),
                                'latin_name': celeb.get('latin_name', '')
                            }
                            for celeb in data[key]
                        ]
                return simplified_data
            else:
                logging.warning(f"未找到媒体类型为 {media_type} 的豆瓣 ID {douban_id} 的演职人员")
                return {}
        except Exception as e:
            logging.error(f"获取演职人员失败，媒体类型: {media_type}，豆瓣 ID: {douban_id}，错误: {e}")
            return {}
        finally:
            # 随机休眠一段时间，避免频繁请求
            sleep_time = random.uniform(15, 30)  # 随机休眠15到30秒
            logging.info(f"获取演职人员完成，随机休眠 {sleep_time:.2f} 秒")
            time.sleep(sleep_time)

def read_nfo_file(file_path):
    # 尝试打开并解析nfo文件
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 判断是电影、电视剧还是季
        media_type = None
        if root.tag == 'movie':
            logging.debug(f"这是电影 nfo 文件: {file_path}")
            media_type = 'movie'
        elif root.tag == 'tvshow':
            logging.debug(f"这是电视剧 nfo 文件: {file_path}")
            media_type = 'tv'
        elif root.tag == 'season':
            logging.debug(f"这是季 nfo 文件: {file_path}")
            media_type = 'season'
        else:
            logging.warning(f"未知文件类型: {file_path}")
            return None, None, None, None
        
        # 查找并获取标题
        title = None
        for element in root.findall('.//title'):
            title = element.text
            break  # 只需要第一个匹配到的标题
        
        # 查找并获取年份
        year = None
        for element in root.findall('.//year'):
            year = element.text
            break  # 只需要第一个匹配到的年份
        
        # 如果 <year> 标签中没有找到年份，则尝试从 <premiered> 和 <releasedate> 标签中提取年份
        if not year:
            for tag in ['premiered', 'releasedate']:
                for element in root.findall(f'.//{tag}'):
                    date_str = element.text
                    if date_str:
                        try:
                            year = date_str.split('-')[0]  # 提取年份部分
                            break
                        except IndexError:
                            logging.warning(f"日期格式不正确: {date_str}")
        
        # 查找并获取 IMDb ID
        imdb_id = None
        for element in root.findall('.//uniqueid[@type="imdb"]'):
            imdb_id = element.text
            break  # 只需要第一个匹配到的 IMDb ID
        
        if title:
            logging.debug(f"标题: {title}, 年份: {year}, IMDb ID: {imdb_id}")
            return media_type, title, year, imdb_id
        else:
            logging.warning(f"未找到文件 {file_path} 中的标题")
            return None, None, None, None
    except Exception as e:
        logging.error(f"读取 nfo 文件 {file_path} 时出错: {e}")
        return None, None, None, None

def update_nfo_file(file_path, directors, actors):
    # 尝试打开并解析nfo文件
    try:
        tree = ET.parse(file_path)
        root = tree.getroot()
        
        # 更新<director>标签
        director_elements = root.findall('.//director')
        for director_element in director_elements:
            original_name = director_element.text.strip().lower()
            matched_director = next(
                (d for d in directors if d['name'].lower() == original_name or d['latin_name'].lower() == original_name),
                None
            )
            if matched_director:
                director_element.text = matched_director['name']
        
        # 更新<actor>标签
        actor_elements = root.findall('.//actor')
        for actor_element in actor_elements:
            name_element = actor_element.find('name')
            if name_element is not None:
                original_name = name_element.text.strip().lower()
                matched_actor = next(
                    (a for a in actors if a['name'].lower() == original_name or a['latin_name'].lower() == original_name),
                    None
                )
                if matched_actor:
                    # 更新<name>
                    name_element.text = matched_actor['name']
                    # 更新<role>
                    role_element = actor_element.find('role')
                    if role_element is not None:
                        role_element.text = re.sub(r'饰\s*', '', matched_actor['character']).strip()
                    else:
                        role_element = ET.SubElement(actor_element, 'role')
                        role_element.text = re.sub(r'饰\s*', '', matched_actor['character']).strip()

        # 写回修改后的内容
        tree.write(file_path, encoding='utf-8', xml_declaration=True)
        logging.info(f"更新文件: {file_path}")
    except Exception as e:
        logging.error(f"更新 nfo 文件 {file_path} 时出错: {e}")

def load_processed_files():
    """加载已处理的文件列表"""
    processed_files = set()
    if os.path.exists(PROCESSED_FILES_FILE):
        with open(PROCESSED_FILES_FILE, 'r', encoding='utf-8') as f:
            processed_files = set(line.strip() for line in f)
    return processed_files

def save_processed_file(file_path):
    """保存已处理的文件"""
    with open(PROCESSED_FILES_FILE, 'a+', encoding='utf-8') as f:
        f.write(file_path + '\n')

def should_exclude_file(file_path):
    """检查文件是否应被排除"""
    file_name = os.path.basename(file_path)
    if file_name in excluded_filenames:
        logging.debug(f"排除文件: {file_name}")
        return True
    return False

def should_exclude_directory(dir_path):
    """检查目录是否应被排除"""
    for keyword in excluded_subdir_keywords:
        if keyword in dir_path:
            logging.debug(f"排除目录: {dir_path}")
            return True
    return False

def process_nfo_files(directory, douban_api):
    # 加载已处理的文件列表
    processed_files = load_processed_files()
    
    # 遍历指定目录及其所有子目录下的所有.nfo文件
    for root, dirs, files in os.walk(directory):
        # 检查当前目录是否应被排除
        if should_exclude_directory(root):
            continue
        
        # 读取父目录的 tvshow.nfo 文件信息
        tvshow_nfo_path = os.path.join(root, 'tvshow.nfo')
        tvshow_title = None
        tvshow_year = None
        if os.path.exists(tvshow_nfo_path):
            tvshow_media_type, tvshow_title, tvshow_year, tvshow_imdb_id = read_nfo_file(tvshow_nfo_path)
            if tvshow_title is None or tvshow_year is None:
                logging.warning(f"未找到文件 {tvshow_nfo_path} 中的标题或年份")
                tvshow_title = None
                tvshow_year = None
        else:
            logging.debug(f"未找到文件: {tvshow_nfo_path}")
        
        for filename in files:
            if filename.endswith('.nfo'):
                file_path = os.path.join(root, filename)
                # 检查文件是否应被排除
                if should_exclude_file(file_path):
                    continue
                
                if file_path in processed_files:
                    #logging.info(f"已处理文件: {file_path}，跳过")
                    continue
                
                logging.info(f"正在处理文件: {file_path}")
                
                # 读取nfo文件信息
                media_type, title, year, imdb_id = read_nfo_file(file_path)
                if media_type is None:
                    # 如果 media_type 为 None，则表示文件类型未知，直接跳过
                    logging.warning(f"未知文件类型: {file_path}，跳过")
                    continue
                
                if title:
                    if media_type == 'movie':
                        # 获取豆瓣 ID
                        douban_ids = douban_api.get_douban_id(title, year, media_type='movie')
                        if douban_ids:
                            logging.info(f"提取到的豆瓣 IDs 是: {douban_ids}")
                            all_directors = []
                            all_actors = []
                            for douban_id in douban_ids:
                                celebs_data = douban_api.get_celebrities(douban_id, media_type)
                                all_directors.extend(celebs_data.get('directors', []))
                                all_actors.extend(celebs_data.get('actors', []))
                            if all_directors or all_actors:
                                update_nfo_file(file_path, all_directors, all_actors)
                                save_processed_file(file_path)
                        else:
                            logging.warning(f"未能提取豆瓣 ID 对于文件: {file_path}")
                    elif media_type == 'tv':
                        # 处理 tvshow.nfo 文件
                        all_directors = []
                        all_actors = []
                        season_dirs = [d for d in dirs if d.startswith('Season')]
                        for season_dir in season_dirs:
                            season_path = os.path.join(root, season_dir)
                            season_nfo_path = os.path.join(season_path, 'season.nfo')
                            if os.path.exists(season_nfo_path):
                                season_media_type, season_title, season_year, season_imdb_id = read_nfo_file(season_nfo_path)
                                if season_year:
                                    # 确保年份是从正确的元素中提取的
                                    if not season_year.isdigit():
                                        # 如果年份不是数字，则从 tvshow.nfo 文件中获取年份
                                        season_year = tvshow_year if tvshow_year else year
                                    # 使用父目录的标题和当前季的年份
                                    if tvshow_title:
                                        douban_ids = douban_api.get_douban_id(tvshow_title, season_year, media_type='tv')
                                    else:
                                        logging.warning(f"未找到父目录的 tvshow.nfo 文件中的标题，使用当前文件的标题: {title}")
                                        douban_ids = douban_api.get_douban_id(title, season_year, media_type='tv')
                                    if douban_ids:
                                        logging.info(f"提取到的豆瓣 IDs 是: {douban_ids}")
                                        for douban_id in douban_ids:
                                            celebs_data = douban_api.get_celebrities(douban_id, media_type)
                                            all_directors.extend(celebs_data.get('directors', []))
                                            all_actors.extend(celebs_data.get('actors', []))
                                    else:
                                        logging.warning(f"未能提取豆瓣 ID 对于文件: {season_nfo_path}")
                                else:
                                    logging.warning(f"未找到文件 {season_nfo_path} 中的年份")
                            else:
                                logging.debug(f"未找到文件: {season_nfo_path}")
                        
                        if all_directors or all_actors:
                            update_nfo_file(file_path, all_directors, all_actors)
                            save_processed_file(file_path)
                    else:
                        logging.warning(f"不支持的媒体类型: {media_type}")
                else:
                    logging.warning(f"未能提取标题 对于文件: {file_path}")

                # 随机休眠一段时间，避免频繁请求
                sleep_time = random.uniform(15, 30)  # 随机休眠15到30秒
                logging.info(f"处理完成，随机休眠 {sleep_time:.2f} 秒")
                time.sleep(sleep_time)

if __name__ == "__main__":
    config = load_config()
    douban_api = DoubanAPI(key, cookie)
    process_nfo_files(directory, douban_api)