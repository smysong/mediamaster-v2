import os
import re
import logging
import requests
import sqlite3
import shutil
import time
import json
import subprocess
from collections import defaultdict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# 新增：导入 guessit
try:
    import guessit
except ImportError:
    guessit = None

# 新增：导入下载器API
try:
    from transmission_rpc import Client as TransmissionClient
except ImportError:
    TransmissionClient = None
try:
    from qbittorrentapi import Client as QBittorrentClient
except ImportError:
    QBittorrentClient = None

# 定义常量
FILES_RECORD_PATH = '/config/files_record.txt'

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/sync.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

# 创建一个默认字典来存储缓存数据
cache = defaultdict(dict)

def load_config(db_path='/config/data.db'):
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

def get_task_label_from_downloader(folder_name, config):
    """
    根据文件夹名称查找下载器任务名称相同的任务，并返回标签（如有）。
    支持 Transmission 和 qBittorrent，xunlei 跳过。
    """
    download_mgmt = config.get('download_mgmt', 'False').lower() == 'true'
    download_type = config.get('download_type', 'transmission').lower()
    download_host = config.get('download_host', '127.0.0.1')
    download_port = int(config.get('download_port', 9091))
    download_username = config.get('download_username', '')
    download_password = config.get('download_password', '')

    logging.debug(f"尝试获取下载任务标签: folder_name={folder_name}, download_mgmt={download_mgmt}, download_type={download_type}")

    # 新增：迅雷无需查找任务和标签
    if not download_mgmt or download_type == 'xunlei':
        logging.info("下载管理未启用或为迅雷，跳过标签查找。")
        return None

    try:
        if download_type == 'transmission' and TransmissionClient:
            logging.debug("使用 TransmissionClient 连接下载器...")
            client = TransmissionClient(
                host=download_host,
                port=download_port,
                username=download_username,
                password=download_password
            )
            for t in client.get_torrents(arguments=['name', 'labels', 'label']):
                try:
                    # 直接用任务名和文件夹名比对
                    if t.name == folder_name:
                        logging.info(f"找到匹配的 Transmission 任务: {t.name}")
                        if hasattr(t, 'labels') and t.labels:
                            logging.info(f"Transmission 任务标签（labels）: {t.labels[0]}")
                            return t.labels[0]
                        elif hasattr(t, 'label') and t.label:
                            logging.info(f"Transmission 任务标签（label）: {t.label}")
                            return t.label
                        else:
                            logging.info("Transmission 任务未设置标签")
                            return None
                except Exception as ex:
                    logging.warning(f"获取 Transmission 任务异常: {ex}")
                    continue
        elif download_type == 'qbittorrent' and QBittorrentClient:
            logging.debug("使用 QBittorrentClient 连接下载器...")
            client = QBittorrentClient(
                host=f"http://{download_host}:{download_port}",
                username=download_username,
                password=download_password
            )
            client.auth_log_in()
            for t in client.torrents_info():
                try:
                    # 直接用任务名和文件夹名比对
                    if t.get('name', '') == folder_name:
                        tags = t.get('tags', '')
                        logging.info(f"找到匹配的 qBittorrent 任务: {t.get('name', '')}，标签: {tags}")
                        return tags.split(',')[0] if tags else None
                except Exception as ex:
                    logging.warning(f"qBittorrent 任务解析异常: {ex}")
                    continue
    except Exception as e:
        logging.error(f"获取下载任务标签失败: {e}")
    return None

def extract_info_from_label(label):
    logging.debug(f"解析下载任务标签: {label}")
    # 电视剧标签示例：临江仙 (2025)-S1-[1-10集]-1080p
    tv_pattern = r'^(?P<title>.+?) \((?P<year>\d{4})\)-S(?P<season>\d+)-\[(?P<episodes>.+?)\]-(?P<quality>\d{3,4}p)$'
    # 电影标签示例：唐探1900 (2025)-1080p
    movie_pattern = r'^(?P<title>.+?) \((?P<year>\d{4})\)-(?P<quality>\d{3,4}p)$'

    m = re.match(tv_pattern, label)
    if m:
        logging.info(f"标签解析为电视剧: {m.groupdict()}")
        return {
            '名称': m.group('title'),
            '发行年份': m.group('year'),
            '视频质量': m.group('quality'),
            '季': m.group('season'),
            '集': m.group('episodes'),
            '类型': 'tv'
        }
    m = re.match(movie_pattern, label)
    if m:
        logging.info(f"标签解析为电影: {m.groupdict()}")
        return {
            '名称': m.group('title'),
            '发行年份': m.group('year'),
            '视频质量': m.group('quality'),
            '类型': 'movie'
        }
    logging.warning(f"标签解析失败: {label}")
    return None

def get_tmdb_info(title, year, media_type, season=None):
    def search_tmdb(language):
        TMDB_API_KEY = config.get("tmdb_api_key", "")
        TMDB_BASE_URL = config.get("tmdb_base_url", "")
        url = f"{TMDB_BASE_URL}/3/search/{media_type}"
        params = {
            'api_key': TMDB_API_KEY,
            'query': title,
            'language': language,
            'include_adult': 'false'
        }
        
        # 增加重试机制
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = requests.get(url, params=params, timeout=10)
                response.raise_for_status()
                return response.json().get('results', [])
            except requests.RequestException as e:
                logging.warning(f"TMDB请求失败 (尝试 {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    # 随机等待1-5秒后重试
                    wait_time = random.randint(1, 5)
                    logging.info(f"等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                else:
                    raise e

    try:
        import random
        cache_key = (title, year, season) if season else (title, year)
        if cache_key in cache[media_type]:
            tmdb_id, tmdb_title, tmdb_year = cache[media_type][cache_key]
            return tmdb_id, tmdb_title, tmdb_year

        # 先用zh-CN查找
        search_results = search_tmdb('zh-CN')

        # 1. 只有一个结果直接返回，无需匹配标题
        if len(search_results) == 1:
            result = search_results[0]
            tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
            tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
            cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
            return result['id'], tmdb_title, tmdb_year

        # 2. 多结果时，优先用标题和年份精确匹配
        for result in search_results:
            tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
            tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
            if tmdb_title == title and tmdb_year == str(year):
                cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                return result['id'], tmdb_title, tmdb_year

        # 如果zh-CN没找到标题精确匹配，再用zh-SG查一次
        search_results_sg = search_tmdb('zh-SG')
        for result in search_results_sg:
            tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
            tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
            if tmdb_title == title and tmdb_year == str(year):
                cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                return result['id'], tmdb_title, tmdb_year

        # 3. 针对电视剧多季情况：如果有季号，且没找到年份匹配，则只用名称匹配首个有首播年份的结果
        if media_type == 'tv' and season:
            for result in search_results:
                tmdb_title = result.get('name', '')
                tmdb_year = result.get('first_air_date', '')[:4]
                if tmdb_title == title and tmdb_year:
                    cache[media_type][(title, tmdb_year, season)] = (result['id'], tmdb_title, tmdb_year)
                    return result['id'], tmdb_title, tmdb_year
            for result in search_results_sg:
                tmdb_title = result.get('name', '')
                tmdb_year = result.get('first_air_date', '')[:4]
                if tmdb_title == title and tmdb_year:
                    cache[media_type][(title, tmdb_year, season)] = (result['id'], tmdb_title, tmdb_year)
                    return result['id'], tmdb_title, tmdb_year

        # 4. 只用名称匹配首个有年份的结果
        for result in search_results:
            tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
            tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
            if tmdb_title == title and tmdb_year:
                cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                return result['id'], tmdb_title, tmdb_year
        # 如果zh-CN没找到标题匹配，再用zh-SG查一次
        for result in search_results_sg:
            tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
            tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
            if tmdb_title == title and tmdb_year:
                cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                return result['id'], tmdb_title, tmdb_year

        # 如果中文查询都没有结果，尝试用英文(en-US)查询
        search_results_en = search_tmdb('en-US')
        if search_results_en:
            # 英文查询结果处理逻辑
            # 1. 只有一个结果直接返回
            if len(search_results_en) == 1:
                result = search_results_en[0]
                tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
                tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
                cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                return result['id'], tmdb_title, tmdb_year

            # 2. 多结果时，优先用标题和年份精确匹配
            for result in search_results_en:
                tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
                tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
                if tmdb_title.lower() == title.lower() and tmdb_year == str(year):
                    cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                    return result['id'], tmdb_title, tmdb_year

            # 3. 只用名称匹配首个有年份的结果
            for result in search_results_en:
                tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
                tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
                if tmdb_title and tmdb_year == str(year):
                    cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                    return result['id'], tmdb_title, tmdb_year

    except requests.RequestException as e:
        logging.error(f"请求错误: {e}")
    return None, None, year

def get_tv_episode_name(tmdb_id, season_number, episode_number):
    # 增加重试机制
    max_retries = 3
    for attempt in range(max_retries):
        try:
            TMDB_API_KEY = config.get("tmdb_api_key", "")
            TMDB_BASE_URL = config.get("tmdb_base_url", "")
            url = f"{TMDB_BASE_URL}/3/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}"
            params = {
                'api_key': TMDB_API_KEY,
                'language': 'zh-CN'
            }
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            episode_info = response.json()
            return episode_info.get('name', f"第{episode_number}集")
        except requests.RequestException as e:
            logging.warning(f"获取剧集名称失败 (尝试 {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                # 随机等待1-5秒后重试
                import random
                wait_time = random.randint(1, 5)
                logging.info(f"等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
            else:
                logging.error(f"请求错误: {e}")
    return f"第{episode_number}集"

def preprocess_filename(filename):
    """
    预处理文件名，移除广告、无效文字等
    """
    # 移除常见的广告关键词和域名
    ad_patterns = [
        r'UIndex', 
        r'dygod\.org', 
        r'阳光电影',
        r'\.com', 
        r'\.cn', 
        r'\.net',
        r'【更多', 
        r'不太灵影视',
        r'高清电影',
        r'高清剧集',
        r'BT影视',
        r'www\.[a-zA-Z0-9]+\.[a-zA-Z]+',  # 通用域名格式 www.xxx.com
        r'[a-zA-Z0-9]+\.(com|cn|net|org|cc|tk|ml|ga|cf)',  # 常见域名后缀
        r'发布者', 
        r'GM-Team',
        r'国漫',
        r'日漫',
        r'动漫',
        r'官方',
        r'正版',
        r'付费',
        r'VIP',
        r'会员'
    ]
    
    processed_filename = filename
    for pattern in ad_patterns:
        processed_filename = re.sub(pattern, '', processed_filename, flags=re.IGNORECASE)
    
    # 只去除首尾空格，不清理中间的符号
    processed_filename = processed_filename.strip()
    
    logging.debug(f"文件名预处理: '{filename}' -> '{processed_filename}'")
    return processed_filename

def preprocess_folder_name(folder_name):
    """
    预处理文件夹名称，移除广告、无效文字等
    """
    # 移除常见的广告关键词和域名
    ad_patterns = [
        r'UIndex', 
        r'dygod\.org', 
        r'阳光电影',
        r'\.com', 
        r'\.cn', 
        r'\.net',
        r'【更多', 
        r'不太灵影视',
        r'高清电影',
        r'高清剧集',
        r'BT影视',
        r'www\.[a-zA-Z0-9]+\.[a-zA-Z]+',  # 通用域名格式 www.xxx.com
        r'[a-zA-Z0-9]+\.(com|cn|net|org|cc|tk|ml|ga|cf)',  # 常见域名后缀
        r'发布者', 
        r'GM-Team',
        r'国漫',
        r'日漫',
        r'动漫',
        r'官方',
        r'正版',
        r'付费',
        r'VIP',
        r'会员'
    ]
    
    processed_folder_name = folder_name
    for pattern in ad_patterns:
        processed_folder_name = re.sub(pattern, '', processed_folder_name, flags=re.IGNORECASE)
    
    # 只去除首尾空格，不清理中间的符号
    processed_folder_name = processed_folder_name.strip()
    
    logging.debug(f"文件夹名预处理: '{folder_name}' -> '{processed_folder_name}'")
    return processed_folder_name

def extract_info_with_guessit(filename):
    """
    使用guessit库从文件名中提取信息，并进行优化处理
    """
    # 预处理文件名
    processed_filename = preprocess_filename(filename)
    
    # 移除扩展名
    filename_without_ext = '.'.join(processed_filename.split('.')[:-1]) if '.' in processed_filename else processed_filename
    
    # 使用guessit解析文件名
    info = guessit.guessit(filename_without_ext)
    
    # 提取关键信息
    title = info.get('title', '')
    year = info.get('year')
    media_type = 'tv' if info.get('type') == 'episode' else 'movie'
    season = info.get('season')
    episode = info.get('episode')
    screen_size = info.get('screen_size')  # 视频质量信息
    
    # 对标题进行优化处理
    if title:
        # 处理中英文混合标题
        if re.search(r'[\u4e00-\u9fff]', title) and re.search(r'[a-zA-Z]', title):
            # 分离中英文标题
            # 提取中文部分（包括中文标点）
            chinese_part = re.findall(r'[\u4e00-\u9fff\·\：\-\_\！\？\，\。\、\；\“\”\‘\’\（\）]+', title)
            chinese_part = ''.join(chinese_part).strip() if chinese_part else ''
            
            # 提取英文部分
            english_part = re.findall(r'[a-zA-Z0-9\s\-\_\:]+', title)
            english_part = ' '.join(english_part).strip() if english_part else ''
            
            # 清理英文标题中的多余符号和空格
            english_part = re.sub(r'[\-\_\:]+', ' ', english_part)
            english_part = re.sub(r'\s+', ' ', english_part).strip()
            
            # 如果中文部分不为空，优先使用中文标题
            if chinese_part:
                title = chinese_part
            elif english_part:
                title = english_part
        else:
            # 单一语言标题的清理
            title = re.sub(r'\s+', ' ', title).strip()
    
    # 特殊处理：如果guessit提取的标题是通用词，尝试从原始文件名中提取更好的标题
    if title.lower() in ['movie', 'the movie', 'film']:
        # 尝试从原始文件名中提取更具体的标题
        parts = filename_without_ext.split('.')
        # 移除年份和质量信息
        filtered_parts = [part for part in parts if not re.match(r'^(19|20)\d{2}$', part) 
                          and not re.match(r'\d{3,4}[ip]', part.lower()) 
                          and not part.lower() in ['bluray', 'webrip', 'webdl', 'hdtv', 'dvdrip']]
        
        if len(filtered_parts) > 1:
            # 尝试组合前几个部分作为标题
            potential_title = ' '.join(filtered_parts[:min(3, len(filtered_parts))])
            # 如果这个标题包含更多具体信息，则使用它
            if len(potential_title) > len(title):
                title = potential_title
    
    # 清理标题中的多余空格
    title = re.sub(r'\s+', ' ', title).strip()
    
    # 优化视频质量信息提取
    quality = None
    if screen_size:
        # 将screen_size转换为标准格式
        if isinstance(screen_size, str):
            # 处理如"1080p"、"720p"等格式
            quality_match = re.search(r'(\d{3,4})[pP]', screen_size)
            if quality_match:
                quality = quality_match.group(1) + 'p'
        elif isinstance(screen_size, int):
            # 处理如1080、720等数字格式
            quality = str(screen_size) + 'p'
    
    # 优化季信息处理，避免年份被误认为季
    if media_type == 'tv' and season:
        # 如果季号看起来像年份（如2023, 2024, 2025），则认为是无效的，使用默认值
        if isinstance(season, int) and season > 1900:
            logging.debug(f"检测到季号 {season} 看起来像年份，将其视为无效")
            season = 1  # 使用默认第一季
        elif isinstance(season, list) and len(season) > 0:
            # 处理列表形式的季号
            first_season = season[0]
            if isinstance(first_season, int) and first_season > 1900:
                logging.debug(f"检测到季号 {first_season} 看起来像年份，将其视为无效")
                season = 1  # 使用默认第一季
    
    return {
        'title': title,
        'year': year,
        'media_type': media_type,
        'season': season,
        'episode': episode,
        'screen_size': quality  # 使用处理后的质量信息
    }

def extract_info(filename, folder_name=None, label_info=None):
    """
    综合标签、文件夹名和文件名信息提取媒体信息。
    使用 guessit 增强识别能力。
    标签只提供全局信息（如名称、年份、季、清晰度），集号和后缀等依然需从文件名解析。
    优化：如果文件名为纯数字，按电视剧处理，数字为集数，其他信息补全。
    """
    # 预处理文件名和文件夹名
    processed_filename = preprocess_filename(filename)
    processed_folder_name = preprocess_folder_name(folder_name) if folder_name else folder_name
    
    # 如果 guessit 可用，先使用 guessit 解析
    guessit_info = {}
    if guessit:
        try:
            guessit_info = extract_info_with_guessit(filename)  # 使用原始文件名给guessit解析
            logging.debug(f"Guessit 解析结果: {guessit_info}")
        except Exception as e:
            logging.warning(f"Guessit 解析失败: {e}")

    # 提取电影信息
    def extract_movie_info(filename, folder_name=None):
        result = {}
        
        # 优先使用 guessit 的结果
        if guessit_info:
            result['名称'] = guessit_info.get('title')
            result['发行年份'] = guessit_info.get('year')
            result['视频质量'] = guessit_info.get('screen_size')  # 先获取guessit的视频质量
            result['后缀名'] = os.path.splitext(filename)[1][1:]
            
            # 检查视频质量是否有效，如果无效则设为None以便后续补全
            if result.get('视频质量'):
                quality_str = str(result['视频质量']).lower()
                if quality_str in ['none', 'null', '']:
                    result['视频质量'] = None
            else:
                result['视频质量'] = None

        # 原有逻辑作为备选方案
        chinese_name_pattern_filename = r'([\u4e00-\u9fa5A-Za-z0-9："“”·]+)(?=\.)'
        chinese_name_pattern_folder = r'】([\u4e00-\u9fa5A-Za-z0-9：$$(). ]+)'
        english_name_pattern = r'([A-Za-z0-9\.\s]+)(?=\.\d{4}(?:\.|$))'
        year_pattern = r'(19\d{2}|20\d{2})'
        # 统一的视频质量匹配正则表达式，支持更多格式
        quality_pattern = r'(?:\b(?:HD)?(?:\d{3,4})[pP]?\b)|(?:\b(\d{3,4})[pP]\b)|(?:\b([Hh][Dd])\b)|(?:\b([Ff][Uu][Ll][Ll][Hh][Dd])\b)|(?:\b([Uu][Ll][Tt][Rr][Aa][Hh][Dd])\b)|(?:\b(4[Kk])\b)|(?:\b(8[Kk])\b)|(?:\b([Ww][Ee][Bb][Dd][Ll])\b)|(?:\b([Bb][Ll][Uu][Rr][Aa][Yy])\b)|(?:\b([Bb][Rr][Rr][Ii][Pp])\b)'
        suffix_pattern = r'\.(\w+)$'

        if not result.get('名称'):
            chinese_name = re.search(chinese_name_pattern_filename, processed_filename)  # 使用预处理后的文件名
            if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                result['名称'] = chinese_name.group(1)
            elif folder_name:
                # 使用预处理后的文件夹名
                processed_folder = preprocess_folder_name(folder_name)
                chinese_name = re.search(chinese_name_pattern_folder, processed_folder)
                if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                    result['名称'] = chinese_name.group(1)

        if not result.get('名称'):
            english_name = re.search(english_name_pattern, processed_filename)  # 使用预处理后的文件名
            english_name = english_name.group(1).strip() if english_name else None
            if english_name:
                result['名称'] = english_name.replace('.', ' ').replace('-', ' ')

        if not result.get('发行年份'):
            years = re.findall(year_pattern, processed_filename)  # 使用预处理后的文件名
            result['发行年份'] = years[-1] if years else None
            if not result['发行年份'] and folder_name:
                # 使用预处理后的文件夹名
                processed_folder = preprocess_folder_name(folder_name)
                folder_year = re.search(year_pattern, processed_folder)
                if folder_year:
                    result['发行年份'] = folder_year.group()

        # 改进视频质量提取逻辑：只有当guessit没有提供有效质量信息时才使用默认值
        video_quality = result.get('视频质量', '').lower() if result.get('视频质量') else ''  # 转为小写
        if not video_quality or video_quality in ['none', 'null', '']:
            quality_match = re.search(quality_pattern, processed_filename)  # 使用预处理后的文件名
            if quality_match:
                # 检查哪个组匹配到了
                matched_groups = [group for group in quality_match.groups() if group]
                if matched_groups:
                    raw_quality = matched_groups[0]
                    raw_quality = raw_quality.lower()
                    
                    # 处理特定关键词映射
                    quality_map = {
                        'fullhd': '1080p',
                        'ultrahd': '2160p',
                        '4k': '2160p',
                        '8k': '4320p',
                        'hd': '720p',
                        'webdl': '1080p',
                        'bluray': '1080p',
                        'brrip': '1080p'
                    }
                    
                    if raw_quality in quality_map:
                        result['视频质量'] = quality_map[raw_quality]
                    elif re.search(r'(?:hd)?(\d{3,4})p?', raw_quality, re.IGNORECASE):
                        # 处理如 720p, 1080p, 2160p, hd1080, hd1080p 等格式
                        resolution_match = re.search(r'(?:hd)?(\d{3,4})p?', raw_quality, re.IGNORECASE)
                        if resolution_match:
                            resolution = resolution_match.group(1)
                            # 根据分辨率映射到标准格式
                            resolution_map = {
                                '720': '720p',
                                '1080': '1080p',
                                '1440': '1440p',
                                '2160': '2160p',
                                '4320': '4320p'
                            }
                            result['视频质量'] = resolution_map.get(resolution, resolution + 'p')
                    else:
                        result['视频质量'] = raw_quality
            else:
                # 增强的视频质量匹配逻辑
                # 支持HD2160P, HD1080P, HD720P等格式
                enhanced_quality_patterns = [
                    (r'hd\s*4k', '2160p'),
                    (r'hd\s*2160\s*p?', '2160p'),
                    (r'hd\s*1440\s*p?', '1440p'),
                    (r'hd\s*1080\s*p?', '1080p'),
                    (r'full\s*hd', '1080p'),
                    (r'hd\s*720\s*p?', '720p'),
                    (r'hd', '720p')
                ]
                
                for pattern, quality_value in enhanced_quality_patterns:
                    if re.search(pattern, processed_filename, re.IGNORECASE):  # 使用预处理后的文件名
                        result['视频质量'] = quality_value
                        break

        # 只有在完全没有获取到质量信息时才使用默认值
        if not result.get('视频质量'):
            result['视频质量'] = '1080p'  # 默认使用1080p

        if not result.get('后缀名'):
            suffix = re.search(suffix_pattern, processed_filename)  # 使用预处理后的文件名
            result['后缀名'] = suffix.group(1) if suffix else None

        return result

    # 提取电视剧信息
    def extract_tv_info(filename, folder_name=None):
        """
        提取电视剧信息，支持文件名为纯数字（如01、02、1、2、3）时，将数字作为集数。
        其他信息（如名称、年份、季、清晰度）优先从文件夹名和下载任务标签获取。
        """
        result = {}
        
        # 优先使用 guessit 的结果
        if guessit_info:
            result['名称'] = guessit_info.get('title')
            result['发行年份'] = guessit_info.get('year')
            result['视频质量'] = guessit_info.get('screen_size')  # 先获取guessit的视频质量
            result['后缀名'] = os.path.splitext(filename)[1][1:]
            
            # 检查视频质量是否有效，如果无效则设为None以便后续补全
            if result.get('视频质量'):
                quality_str = str(result['视频质量']).lower()
                if quality_str in ['none', 'null', '']:
                    result['视频质量'] = None
            else:
                result['视频质量'] = None
            
            # 处理季信息
            if guessit_info.get('season'):
                season_value = guessit_info['season']
                # 如果季号看起来像年份（如2023, 2024, 2025），则认为是无效的，使用默认值
                if isinstance(season_value, int) and season_value > 1900:
                    logging.debug(f"检测到季号 {season_value} 看起来像年份，将其视为无效")
                    result['季'] = '01'  # 使用默认第一季
                else:
                    result['季'] = str(season_value).zfill(2) if isinstance(season_value, int) else season_value
            else:
                result['季'] = '01'  # 默认第一季
            
            # 处理集信息
            if guessit_info.get('episode'):
                episode_number = guessit_info['episode']
                if isinstance(episode_number, list):
                    # 处理多集情况，取第一集
                    episode_number = episode_number[0]
                result['集'] = str(episode_number).zfill(2) if isinstance(episode_number, int) else episode_number
            
            # 如果 guessit 提供了完整信息，直接返回
            if result.get('名称') and result.get('集'):
                return result

        chinese_name_pattern_filename = r'([\u4e00-\u9fa5A-Za-z0-9："“”·]+)(?=\.)'
        chinese_name_pattern_folder = r'】([\u4e00-\u9fa5A-Za-z0-9：$$(). ]+)'
        english_name_pattern = r'([A-Za-z0-9\.\s]+)(?=\.(?:S\d{1,2}|E\d{1,2}|EP\d{1,2}))'
        season_pattern = r'S(\d{1,2})'
        episode_pattern = r'(?:E|EP)(\d{1,3})|第\s?(\d{1,3})\s?集|(?<!\d)(\d{1,3})集'
        # 年份只匹配19xx或20xx
        year_pattern = r'(19\d{2}|20\d{2})'
        # 统一的视频质量匹配正则表达式，支持更多格式
        quality_pattern = r'(?:\b(?:HD)?(?:\d{3,4})[pP]?\b)|(?:\b(\d{3,4})[pP]\b)|(?:\b([Hh][Dd])\b)|(?:\b([Ff][Uu][Ll][Ll][Hh][Dd])\b)|(?:\b([Uu][Ll][Tt][Rr][Aa][Hh][Dd])\b)|(?:\b(4[Kk])\b)|(?:\b(8[Kk])\b)|(?:\b([Ww][Ee][Bb][Dd][Ll])\b)|(?:\b([Bb][Ll][Uu][Rr][Aa][Yy])\b)|(?:\b([Bb][Rr][Rr][Ii][Pp])\b)'
        suffix_pattern = r'\.(\w+)$'

        # 检查是否为纯数字命名的文件（如01.mp4、2.mkv等）
        pure_number_pattern = r'^(\d{1,3})\.\w+$'
        pure_number_match = re.match(pure_number_pattern, processed_filename)  # 使用预处理后的文件名
        episode_number = None

        if pure_number_match:
            # 文件名为纯数字，直接作为集数
            episode_number = pure_number_match.group(1).zfill(2)
        else:
            # 正常匹配E01、EP01、以及"第09集""09集""9集"
            episode = re.search(episode_pattern, processed_filename, re.IGNORECASE)  # 使用预处理后的文件名
            if episode:
                # 优先英文匹配，否则取中文匹配，否则取纯数字匹配
                episode_number = episode.group(1) or episode.group(2) or episode.group(3)
                if episode_number:
                    episode_number = episode_number.zfill(2)
            else:
                episode_number = None

        # 季号优先从文件名Sxx获取
        season = re.search(season_pattern, processed_filename)  # 使用预处理后的文件名
        season_number = season.group(1) if season else None
        
        # 验证季号，避免年份被误认为季
        if season_number and int(season_number) > 1900:
            logging.debug(f"检测到季号 {season_number} 看起来像年份，将其视为无效")
            season_number = None

        # 其他信息
        if not result.get('名称'):
            chinese_name = re.search(chinese_name_pattern_filename, processed_filename)  # 使用预处理后的文件名
            if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                result['名称'] = chinese_name.group(1)
            elif folder_name:
                # 使用预处理后的文件夹名
                processed_folder = preprocess_folder_name(folder_name)
                chinese_name = re.search(chinese_name_pattern_folder, processed_folder)
                if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                    result['名称'] = chinese_name.group(1)

        if not result.get('名称'):
            english_name = re.search(english_name_pattern, processed_filename)  # 使用预处理后的文件名
            english_name = english_name.group(1).strip() if english_name else None
            if english_name:
                result['名称'] = english_name.replace('.', ' ').replace('-', ' ')

        # 匹配所有年份，取最后一个
        if not result.get('发行年份'):
            years = re.findall(year_pattern, processed_filename)  # 使用预处理后的文件名
            result['发行年份'] = years[-1] if years else None
            if not result['发行年份'] and folder_name:
                # 使用预处理后的文件夹名
                processed_folder = preprocess_folder_name(folder_name)
                folder_year = re.search(year_pattern, processed_folder)
                if folder_year:
                    result['发行年份'] = folder_year.group()

        # 改进的视频质量提取：只有当guessit没有提供有效质量信息时才使用原有逻辑
        video_quality = result.get('视频质量', '').lower() if result.get('视频质量') else ''  # 转为小写
        if not video_quality or video_quality in ['none', 'null', '']:
            quality_match = re.search(quality_pattern, processed_filename)  # 使用预处理后的文件名
            if quality_match:
                # 检查哪个组匹配到了
                matched_groups = [group for group in quality_match.groups() if group]
                if matched_groups:
                    raw_quality = matched_groups[0]
                    raw_quality = raw_quality.lower()
                    
                    # 处理特定关键词映射
                    quality_map = {
                        'fullhd': '1080p',
                        'ultrahd': '2160p',
                        '4k': '2160p',
                        '8k': '4320p',
                        'hd': '720p',
                        'webdl': '1080p',
                        'bluray': '1080p',
                        'brrip': '1080p'
                    }
                    
                    if raw_quality in quality_map:
                        result['视频质量'] = quality_map[raw_quality]
                    elif re.search(r'(?:hd)?(\d{3,4})p?', raw_quality, re.IGNORECASE):
                        # 处理如 720p, 1080p, 2160p, hd1080, hd1080p 等格式
                        resolution_match = re.search(r'(?:hd)?(\d{3,4})p?', raw_quality, re.IGNORECASE)
                        if resolution_match:
                            resolution = resolution_match.group(1)
                            # 根据分辨率映射到标准格式
                            resolution_map = {
                                '720': '720p',
                                '1080': '1080p',
                                '1440': '1440p',
                                '2160': '2160p',
                                '4320': '4320p'
                            }
                            result['视频质量'] = resolution_map.get(resolution, resolution + 'p')
                    else:
                        result['视频质量'] = raw_quality
            else:
                # 增强的视频质量匹配逻辑
                # 支持HD2160P, HD1080P, HD720P等格式
                enhanced_quality_patterns = [
                    (r'hd\s*4k', '2160p'),
                    (r'hd\s*2160\s*p?', '2160p'),
                    (r'hd\s*1440\s*p?', '1440p'),
                    (r'hd\s*1080\s*p?', '1080p'),
                    (r'full\s*hd', '1080p'),
                    (r'hd\s*720\s*p?', '720p'),
                    (r'hd', '720p')
                ]
                
                for pattern, quality_value in enhanced_quality_patterns:
                    if re.search(pattern, processed_filename, re.IGNORECASE):  # 使用预处理后的文件名
                        result['视频质量'] = quality_value
                        break

        # 只有在完全没有获取到质量信息时才使用默认值
        if not result.get('视频质量'):
            result['视频质量'] = '1080p'  # 默认使用1080p

        if not result.get('后缀名'):
            suffix = re.search(suffix_pattern, processed_filename)  # 使用预处理后的文件名
            result['后缀名'] = suffix.group(1) if suffix else None

        if episode_number:
            if not season_number and not result.get('季'):
                # 尝试从文件夹名或标签补全季号，否则默认为01
                folder_season = None
                if folder_name:
                    # 使用预处理后的文件夹名
                    processed_folder = preprocess_folder_name(folder_name)
                    folder_season_match = re.search(r'S(\d{1,2})', processed_folder, re.IGNORECASE)
                    if folder_season_match:
                        folder_season = folder_season_match.group(1)
                result['季'] = folder_season if folder_season else '01'
            elif season_number:
                result['季'] = season_number.zfill(2) if isinstance(season_number, str) else season_number
            result['集'] = episode_number

        return result

    # 判断是否为纯数字文件名（如01.mkv、2.mp4），此时强制按tv处理
    pure_number_pattern = r'^(\d{1,3})\.\w+$'
    if re.match(pure_number_pattern, processed_filename):  # 使用预处理后的文件名
        raw_info = extract_tv_info(filename, folder_name)
    else:
        # 判断是电影还是电视剧
        is_tv = re.search(r'(?:S\d{1,2}|E\d{1,2}|EP\d{1,2}|第\s?\d{1,3}\s?集|(?<!\d)\d{1,3}集)', processed_filename, re.IGNORECASE)  # 使用预处理后的文件名
        # 也参考 guessit 的判断
        if guessit_info and guessit_info.get('media_type') == 'tv':
            is_tv = True
        elif guessit_info and guessit_info.get('media_type') == 'movie':
            is_tv = False
            
        raw_info = extract_tv_info(filename, folder_name) if is_tv else extract_movie_info(filename, folder_name)

    # 优先合并标签信息
    if label_info and isinstance(label_info, dict):
        for key in ['名称', '发行年份', '视频质量', '季', '类型']:
            if key in label_info and label_info[key]:
                raw_info[key] = label_info[key]

    # 如果名称依然为空，尝试从文件夹名补全
    if not raw_info.get('名称') and folder_name:
        # 使用预处理后的文件夹名
        processed_folder = preprocess_folder_name(folder_name)
        # 尝试从文件夹名提取"名称"和"年份"
        # 例如：黄雀-S1-2025、黄雀-2025、黄雀2025
        folder_match = re.match(r'([\u4e00-\u9fa5A-Za-z0-9]+)(?:-S\d+)?-?(\d{4})?', processed_folder)
        if folder_match:
            raw_info['名称'] = folder_match.group(1)
            if not raw_info.get('发行年份') and folder_match.group(2):
                raw_info['发行年份'] = folder_match.group(2)

    # 新增：如果是纯数字文件名，且只有名称没有年份，则尝试通过TMDB查询年份
    if re.match(pure_number_pattern, processed_filename) and raw_info.get('名称') and not raw_info.get('发行年份'):  # 使用预处理后的文件名
        try:
            # 确定媒体类型
            media_type = 'tv'  # 纯数字文件名强制按电视剧处理
            season = raw_info.get('季', '01')
            
            # 通过TMDB查询年份信息
            tmdb_id, tmdb_name, tmdb_year = get_tmdb_info(raw_info['名称'], None, media_type, season)
            if tmdb_year:
                raw_info['发行年份'] = tmdb_year
                logging.info(f"通过TMDB查询补全年份信息: {raw_info['名称']} -> {tmdb_year}")
        except Exception as e:
            logging.warning(f"通过TMDB查询年份信息失败: {e}")

    return raw_info

def move_or_copy_file(src, dst, action, media_type):
    try:
        # 检查目标文件夹中是否存在相同的文件
        target_dir = os.path.dirname(dst)
        target_filename = os.path.basename(dst)
        if os.path.exists(target_dir):
            for existing_file in os.listdir(target_dir):
                if media_type == 'tv':
                    # 检查是否为相同的剧集文件（SxxExx）
                    if re.search(r'S\d{2}E\d{2}', existing_file) and re.search(r'S\d{2}E\d{2}', target_filename):
                        if existing_file.split(' - ')[1] == target_filename.split(' - ')[1]:
                            existing_file_path = os.path.join(target_dir, existing_file)
                            os.remove(existing_file_path)
                            logging.info(f"删除旧剧集文件: {existing_file_path}")
                elif media_type == 'movie':
                    # 检查是否为相同的电影文件（标题 + 年份）
                    if existing_file.split(' - ')[0] == target_filename.split(' - ')[0]:
                        existing_file_path = os.path.join(target_dir, existing_file)
                        os.remove(existing_file_path)
                        logging.info(f"删除旧电影文件: {existing_file_path}")

        # 执行文件转移操作
        if action == 'move':
            shutil.move(src, dst)
            logging.info(f"文件已移动: {src} -> {dst}")
        elif action == 'copy':
            shutil.copy2(src, dst)
            logging.info(f"文件已复制: {src} -> {dst}")
        else:
            logging.error(f"未知操作: {action}")
    except Exception as e:
        logging.error(f"文件操作失败: {e}")

def is_common_video_file(filename):
    common_video_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.iso']
    extension = os.path.splitext(filename)[1].lower()
    return extension in common_video_extensions

def is_unfinished_download_file(filename):
    unfinished_extensions = ['.xltd', '.!qB', '.part']
    extension = os.path.splitext(filename)[1].lower()
    return extension in unfinished_extensions

def is_small_file(file_path, min_size_mb=5):
    """检查文件是否小于指定大小（默认5MB）"""
    try:
        return os.path.getsize(file_path) < min_size_mb * 1024 * 1024
    except FileNotFoundError:
        return False

def load_processed_files():
    processed_filenames = set()
    if os.path.exists(FILES_RECORD_PATH):
        with open(FILES_RECORD_PATH, 'r') as f:
            for line in f.read().splitlines():
                processed_filenames.add(line.split('/')[-1])
    return processed_filenames

def save_processed_files(processed_filenames):
    with open(FILES_RECORD_PATH, 'w') as f:
        for filename in processed_filenames:
            f.write(filename + '\n')

def get_alias_mapping(db_path, alias_name):
    """从数据库获取指定关系映射"""
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT target_title, target_season FROM LIB_TV_ALIAS WHERE alias = ?", (alias_name,))
            row = cursor.fetchone()
            if row:
                return {'target_title': row[0], 'target_season': row[1]}
    except Exception as e:
        logging.error(f"查询指定关系失败: {e}")
    return None

def refresh_media_library():
    # 刷新媒体库
    subprocess.run(['python', 'scan_media.py'])  
    # 刷新正在订阅
    subprocess.run(['python', 'check_subscr.py'])   
    # 刮削NFO元数据
    subprocess.run(['python', 'scrape_metadata.py'])
    # 刷新媒体库tmdb_id
    subprocess.run(['python', 'tmdb_id.py'])

# 全局未识别计数字典，记录每个文件夹未能获取到TMDB ID的次数
unrecognized_count = {}

def process_file(file_path, processed_filenames):
    """
    处理单个文件：
    1. 提取媒体信息，尝试获取 TMDB ID。
    2. 若同一文件夹下连续3次未能获取到 TMDB ID，则将该文件夹移动到 unknown_directory。
    3. 若 result 字典关键字段（如"名称"）为 None，也直接转移文件夹。
    4. 识别成功则清零计数。
    5. 其它原有逻辑保持不变。
    6. 优化：无论本地是否有年份，均尝试 TMDB 查询，并用 TMDB 年份覆盖本地年份（只要查到）。
    """
    try:
        excluded_filenames = config.get("download_excluded_filenames", "").split(',')
        action = config.get("download_action", "")
        movie_directory = config.get("movies_path", "")
        episode_directory = config.get("episodes_path", "")
        unknown_directory = config.get("unknown_path", "")

        filename = os.path.basename(file_path)
        folder_name = os.path.basename(os.path.dirname(file_path))

        # 跳过未完成下载的文件
        if is_unfinished_download_file(filename):
            logging.debug(f"跳过下载未完成文件：{file_path}")
            return

        # 跳过非常见视频文件
        if not is_common_video_file(filename):
            logging.debug(f"跳过非视频文件：{file_path}")
            return

        extension = os.path.splitext(filename)[1].lower()
        if filename in excluded_filenames:
            logging.debug(f"跳过文件（文件名在排除列表中）: {file_path}")
            return
        # 排除特定后缀文件
        excluded_extensions = ['.cfg', '.txt', '.pdf','.doc','.docx', '.html']
        if extension in excluded_extensions:
            logging.debug(f"跳过文件（后缀在排除列表中）: {file_path}")
            return
        if '【更多' in filename:
            logging.debug(f"跳过文件（包含特定字符）: {file_path}")
            return

        # 优先用下载任务标签辅助解析
        task_label = get_task_label_from_downloader(folder_name, config)
        label_info = extract_info_from_label(task_label) if task_label else None

        # 将标签解析结果传递给 extract_info 进行辅助提取
        result = extract_info(filename, folder_name, label_info=label_info)

        # 新增：指定关系替换逻辑
        if result and result.get('名称'):
            db_path = config.get("db_path", "/config/data.db")
            alias_map = get_alias_mapping(db_path, result['名称'])
            if alias_map:
                logging.info(f"应用剧集关联: {result['名称']} -> {alias_map['target_title']}")
                result['名称'] = alias_map['target_title']
                # 如果指定了目标季号，则覆盖
                if alias_map.get('target_season'):
                    result['季'] = alias_map['target_season']

        # 判断关键名称字段是否缺失，缺失则转移到unknown_directory
        if not result or not result.get('名称'):
            logging.warning(f"无法识别文件或关键信息缺失: {filename}，解析结果: {result}")
            if unknown_directory:
                src_folder = os.path.dirname(file_path)
                dst_folder = os.path.join(unknown_directory, os.path.basename(src_folder))
                try:
                    if not os.path.exists(dst_folder):
                        os.makedirs(dst_folder)
                    dst_file_path = os.path.join(dst_folder, filename)
                    # 只复制/移动当前文件
                    if action == 'copy':
                        shutil.copy2(file_path, dst_file_path)
                        logging.info(f"已将无法识别的文件复制到未识别目录: {dst_file_path}")
                    else:
                        shutil.move(file_path, dst_file_path)
                        logging.info(f"已将无法识别的文件移动到未识别目录: {dst_file_path}")
                    processed_filenames.add(filename)
                    save_processed_files(processed_filenames)
                except Exception as e:
                    logging.error(f"转移未识别文件失败: {e}")
            return

        if result:
            logging.info(f"文件名: {filename}")
            logging.info(f"解析结果: {result}")

            media_type = result.get('类型') or ('tv' if '季' in result and '集' in result else 'movie')
            target_directory = episode_directory if media_type == 'tv' else movie_directory

            # 始终尝试TMDB查询，不管本地是否有年份
            season = result.get('季') if media_type == 'tv' else None
            tmdb_id, tmdb_name, tmdb_year = get_tmdb_info(result['名称'], result.get('发行年份'), media_type, season)
            # 只要本地年份与tmdb年份不一致，就用tmdb年份覆盖
            if tmdb_year and result.get('发行年份') != tmdb_year:
                result['发行年份'] = tmdb_year

            # 如果TMDB查不到年份，且本地也没有，则判定为关键信息缺失
            if not result.get('发行年份'):
                logging.warning(f"无法识别文件或关键信息缺失: {filename}，解析结果: {result}")
                if unknown_directory:
                    src_folder = os.path.dirname(file_path)
                    dst_folder = os.path.join(unknown_directory, os.path.basename(src_folder))
                    try:
                        if not os.path.exists(dst_folder):
                            os.makedirs(dst_folder)
                        dst_file_path = os.path.join(dst_folder, filename)
                        if action == 'copy':
                            shutil.copy2(file_path, dst_file_path)
                            logging.info(f"已将无法识别的文件复制到未识别目录: {dst_file_path}")
                        else:
                            shutil.move(file_path, dst_file_path)
                            logging.info(f"已将无法识别的文件移动到未识别目录: {dst_file_path}")
                        processed_filenames.add(filename)
                        save_processed_files(processed_filenames)
                    except Exception as e:
                        logging.error(f"转移未识别文件失败: {e}")
                return

            # 新增：如果未能获取到 TMDB ID，且名称包含"第X季"，则去掉"第X季"再查一次
            if not tmdb_id and media_type == 'tv' and result['名称']:
                import re
                new_title = re.sub(r'\s*第[一二三四五六七八九十1234567890]+季', '', result['名称'])
                if new_title != result['名称']:
                    logging.info(f"尝试去除季信息后再次查询TMDB: {new_title}")
                    tmdb_id, tmdb_name, tmdb_year = get_tmdb_info(new_title, result.get('发行年份'), media_type, season)
                    if tmdb_id:
                        result['名称'] = new_title
                        if tmdb_year and result.get('发行年份') != tmdb_year:
                            result['发行年份'] = tmdb_year

            if tmdb_id:
                logging.info(f"获取到 TMDB ID: {tmdb_id}，名称：{tmdb_name}")

                # 识别成功，清零该文件夹的未识别计数
                unrecognized_count.pop(folder_name, None)

                # 获取媒体的中文和英文信息
                title_cn, title_en = get_media_titles_with_language(tmdb_id, media_type)
                
                # 优先使用中文标题，如果没有则使用英文标题
                title = title_cn if title_cn else (title_en if title_en else result['名称'])
                year = result['发行年份']
                target_base_dir = os.path.join(target_directory, f"{title} ({year})")

                if not os.path.exists(target_base_dir):
                    os.makedirs(target_base_dir)
                    logging.info(f"创建目录: {target_base_dir}")

                if media_type == 'tv':
                    season_number = result.get('季', '01')
                    episode_number = result.get('集', '01')
                    # 补零：始终保证季号为两位数
                    season_number_str = str(season_number).zfill(2)
                    season_dir = os.path.join(target_base_dir, f"Season {int(season_number)}")
                    if not os.path.exists(season_dir):
                        os.makedirs(season_dir)
                        logging.info(f"创建目录: {season_dir}")

                    # 获取剧集中文名称
                    episode_name_cn = get_tv_episode_name_with_language(tmdb_id, season_number, episode_number, 'zh-CN')
                    # 如果没有中文名称，则获取英文名称
                    episode_name = episode_name_cn if episode_name_cn else get_tv_episode_name(tmdb_id, season_number, episode_number)
                    new_filename = f"{title} - S{season_number_str}E{str(episode_number).zfill(2)} - {episode_name}.{result['后缀名']}"
                    target_file_path = os.path.join(season_dir, new_filename)
                else:
                    new_filename = f"{title} - ({year}) {result['视频质量']}.{result['后缀名']}"
                    target_file_path = os.path.join(target_base_dir, new_filename)

                if filename in processed_filenames:
                    logging.debug(f"文件已处理，跳过: {filename}")
                    return

                move_or_copy_file(file_path, target_file_path, action, media_type)
                processed_filenames.add(filename)

                video_dir = os.path.dirname(file_path)
                nfo_filename = os.path.splitext(filename)[0] + '.nfo'
                nfo_file_path = os.path.join(video_dir, nfo_filename)
                if os.path.exists(nfo_file_path):
                    new_nfo_filename = f"{title} - S{season_number_str}E{str(episode_number).zfill(2)} - {episode_name}.nfo" if media_type == 'tv' else f"{title} - ({year}) {result['视频质量']}.nfo"
                    nfo_target_path = os.path.join(target_base_dir if media_type == 'movie' else season_dir, new_nfo_filename)
                    move_or_copy_file(nfo_file_path, nfo_target_path, action, media_type)
                    logging.info(f"转移NFO文件: {nfo_file_path} -> {nfo_target_path}")

                send_notification(new_filename)
                logging.info(f"文件处理完成，刷新本地数据库")
                refresh_media_library()

                # 保存已处理的文件列表
                save_processed_files(processed_filenames)
            else:
                # 未能获取到 TMDB ID，计数+1
                count = unrecognized_count.get(folder_name, 0) + 1
                unrecognized_count[folder_name] = count
                logging.warning(f"未能获取到 TMDB ID: {result['名称']} ({result['发行年份']})，累计失败次数: {count}")

                # 超过3次则移动/复制当前文件到 unknown_directory 下以文件夹名为名的文件夹
                # 或者如果这是单个文件（不是文件夹中的多个文件），即使失败1次也转移
                should_move = count >= 3
                
                # 对于单个文件的情况，即使失败1次也应该移动
                if unknown_directory:
                    # 检查文件夹中是否还有其他视频文件
                    folder_path = os.path.dirname(file_path)
                    other_video_files = []
                    if os.path.exists(folder_path):
                        for f in os.listdir(folder_path):
                            if is_common_video_file(f) and f != filename:
                                other_video_files.append(f)
                    
                    # 如果文件夹中没有其他视频文件，则即使失败1次也应该移动
                    if len(other_video_files) == 0:
                        should_move = True
                        logging.debug(f"文件夹中无其他视频文件，立即转移未识别文件")
                    
                    if should_move:
                        src_folder = os.path.dirname(file_path)
                        dst_folder = os.path.join(unknown_directory, os.path.basename(src_folder))
                        try:
                            if not os.path.exists(dst_folder):
                                os.makedirs(dst_folder)
                            dst_file_path = os.path.join(dst_folder, filename)
                            if action == 'copy':
                                shutil.copy2(file_path, dst_file_path)
                                logging.info(f"已将无法识别的文件复制到未识别目录: {dst_file_path}")
                            else:
                                shutil.move(file_path, dst_file_path)
                                logging.info(f"已将无法识别的文件移动到未识别目录: {dst_file_path}")
                            processed_filenames.add(filename)
                            save_processed_files(processed_filenames)
                            # 清零计数
                            unrecognized_count.pop(folder_name, None)
                        except Exception as e:
                            logging.error(f"转移未识别文件失败: {e}")
    except Exception as e:
        logging.error(f"处理文件时发生错误: {file_path}, 错误信息: {e}")

# 新增函数：根据TMDB ID获取媒体的中英文标题
def get_media_titles_with_language(tmdb_id, media_type):
    """
    根据TMDB ID获取媒体的中英文标题
    返回: (中文标题, 英文标题)
    """
    try:
        TMDB_API_KEY = config.get("tmdb_api_key", "")
        TMDB_BASE_URL = config.get("tmdb_base_url", "")
        
        # 先尝试获取中文标题
        url = f"{TMDB_BASE_URL}/3/{media_type}/{tmdb_id}"
        params = {
            'api_key': TMDB_API_KEY,
            'language': 'zh-CN'
        }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        title_cn = data.get('title', '') if media_type == 'movie' else data.get('name', '')
        
        # 再获取英文标题
        params['language'] = 'en-US'
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        title_en = data.get('title', '') if media_type == 'movie' else data.get('name', '')
        
        return title_cn, title_en
    except Exception as e:
        logging.warning(f"获取媒体标题失败: {e}")
        return None, None

# 新增函数：根据TMDB ID获取剧集的指定语言名称
def get_tv_episode_name_with_language(tmdb_id, season_number, episode_number, language='zh-CN'):
    """
    根据TMDB ID获取剧集的指定语言名称
    """
    try:
        TMDB_API_KEY = config.get("tmdb_api_key", "")
        TMDB_BASE_URL = config.get("tmdb_base_url", "")
        url = f"{TMDB_BASE_URL}/3/tv/{tmdb_id}/season/{season_number}/episode/{episode_number}"
        params = {
            'api_key': TMDB_API_KEY,
            'language': language
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        episode_info = response.json()
        return episode_info.get('name', '')
    except Exception as e:
        logging.warning(f"获取剧集{language}名称失败: {e}")
        return ''
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
            "title": "文件转移",
            "body": f"{title_text}已入库"
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

class CustomFileHandler(FileSystemEventHandler):
    def __init__(self):
        self.original_filenames = {}
        self.unfinished_files = set()
        self.processed_files = load_processed_files()

    def on_created(self, event):
        if event.is_directory:
            dir_name = os.path.basename(event.src_path)
            # 忽略特定目录：云盘缓存文件
            if "云盘缓存文件" in dir_name:
                logging.debug(f"忽略目录: {event.src_path}")
                return
            return

        file_path = event.src_path
        filename = os.path.basename(file_path)

        # 忽略隐藏文件
        if filename.startswith('.'):
            logging.debug(f"忽略隐藏文件: {file_path}")
            return

        # 忽略小于5MB的文件
        if is_small_file(file_path):
            logging.debug(f"忽略小于5MB的文件: {file_path}")
            return

        # 其他下载未完成文件监控逻辑
        if is_unfinished_download_file(filename):
            self.unfinished_files.add(file_path)
            logging.debug(f"发现下载未完成文件: {file_path}，开始监控")
        else:
            logging.debug(f"新文件创建: {file_path}")
            process_file(file_path, self.processed_files)

    def on_modified(self, event):
        if event.is_directory:
            return

        file_path = event.src_path
        filename = os.path.basename(file_path)

        # 忽略隐藏文件
        if filename.startswith('.'):
            logging.debug(f"忽略隐藏文件: {file_path}")
            return

        # 忽略小于5MB的文件
        if is_small_file(file_path):
            logging.debug(f"忽略小于5MB的文件: {file_path}")
            return

        # 原始文件处理逻辑保持不变
        if file_path in self.unfinished_files:
            if not is_unfinished_download_file(filename):
                self.unfinished_files.remove(file_path)
                logging.info(f"下载文件已完成: {file_path}，开始处理")
                process_file(file_path, self.processed_files)
        else:
            logging.debug(f"文件修改: {file_path}")
            if filename not in self.processed_files:
                process_file(file_path, self.processed_files)
            else:
                logging.debug(f"文件已处理，跳过: {filename}")

    def on_moved(self, event):
        if event.is_directory:
            return
        old_file_path = event.src_path
        new_file_path = event.dest_path
        logging.debug(f"文件重命名: {old_file_path} -> {new_file_path}")
        if os.path.basename(new_file_path) not in self.processed_files:
            process_file(new_file_path, self.processed_files)
        else:
            logging.debug(f"文件已处理，跳过: {os.path.basename(new_file_path)}")

def start_monitoring(directory):
    logging.info(f"开始监控目录: {directory}")
    event_handler = CustomFileHandler()
    observer = Observer()
    observer.schedule(event_handler, directory, recursive=True)
    observer.start()
    try:
        # 处理已存在的文件
        for root, dirs, files in os.walk(directory):
            # 排除特定目录
            dirs[:] = [d for d in dirs if "云盘缓存文件" not in d and not d.startswith('.')]

            for file in files:
                file_path = os.path.join(root, file)
                filename = os.path.basename(file_path)

                # 忽略隐藏文件和小于5MB的文件
                if filename.startswith('.') or is_small_file(file_path):
                    continue

                if is_common_video_file(filename) or is_unfinished_download_file(filename):
                    if filename not in event_handler.processed_files:
                        process_file(file_path, event_handler.processed_files)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    logging.info("实时监控已停止")

if __name__ == "__main__":
    config = load_config()
    directory = config.get("download_dir", "")
    start_monitoring(directory)