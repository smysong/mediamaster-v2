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
        
        logging.info("加载配置文件成功")
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

def get_tmdb_info(title, year, media_type):
    try:
        if (title, year) in cache[media_type]:
            tmdb_id, tmdb_title, tmdb_year = cache[media_type][(title, year)]
            return tmdb_id, tmdb_title, tmdb_year
        TMDB_API_KEY = config.get("tmdb_api_key", "")
        TMDB_BASE_URL = config.get("tmdb_base_url", "")
        url = f"{TMDB_BASE_URL}/3/search/{media_type}"
        params = {
            'api_key': TMDB_API_KEY,
            'query': title,
            'language': 'zh-CN',
            'include_adult': 'false'
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        search_results = response.json().get('results', [])

        # 如果只有一个结果且标题完全匹配，则补全年份
        if len(search_results) == 1:
            result = search_results[0]
            tmdb_title = result.get('title', '') if media_type == 'movie' else result.get('name', '')
            tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
            if tmdb_title == title:
                # 只要查到tmdb_year就用tmdb_year
                cache[media_type][(title, tmdb_year)] = (result['id'], tmdb_title, tmdb_year)
                return result['id'], tmdb_title, tmdb_year

        for result in search_results:
            tmdb_year = result.get('release_date', '')[:4] if media_type == 'movie' else result.get('first_air_date', '')[:4]
            if media_type == 'movie' and str(result.get('release_date', '')).startswith(str(year)):
                cache[media_type][(title, tmdb_year)] = (result['id'], result.get('title', ''), tmdb_year)
                return result['id'], result.get('title', ''), tmdb_year
            elif media_type == 'tv' and result.get('first_air_date', '').startswith(str(year)):
                cache[media_type][(title, tmdb_year)] = (result['id'], result.get('name', ''), tmdb_year)
                return result['id'], result.get('name', ''), tmdb_year
    except requests.RequestException as e:
        logging.error(f"请求错误: {e}")
    return None, None, year

def get_tv_episode_name(tmdb_id, season_number, episode_number):
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
        logging.error(f"请求错误: {e}")
    return f"第{episode_number}集"

def extract_info(filename, folder_name=None, label_info=None):
    """
    综合标签、文件夹名和文件名信息提取媒体信息。
    标签只提供全局信息（如名称、年份、季、清晰度），集号和后缀等依然需从文件名解析。
    优化：如果文件名为纯数字，按电视剧处理，数字为集数，其他信息补全。
    """
    # 提取电影信息
    def extract_movie_info(filename, folder_name=None):
        chinese_name_pattern_filename = r'([\u4e00-\u9fa5A-Za-z0-9：]+)(?=\.)'
        chinese_name_pattern_folder = r'】([\u4e00-\u9fa5A-Za-z0-9：$$(). ]+)'
        english_name_pattern = r'([A-Za-z0-9\.\s]+)(?=\.\d{4}(?:\.|$))'
        year_pattern = r'(19\d{2}|20\d{2})'
        quality_pattern = r'(\d{1,4}[pPkK])'
        suffix_pattern = r'\.(\w+)$'

        chinese_name = re.search(chinese_name_pattern_filename, filename)
        if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
            chinese_name = chinese_name.group(1)
        else:
            chinese_name = None

        if not chinese_name and folder_name:
            chinese_name = re.search(chinese_name_pattern_folder, folder_name)
            if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                chinese_name = chinese_name.group(1)
            else:
                chinese_name = None

        english_name = re.search(english_name_pattern, filename)
        english_name = english_name.group(1).strip() if english_name else None
        if english_name:
            english_name = english_name.replace('.', ' ').replace('-', ' ')

        # 年份：匹配所有，取最后一个
        years = re.findall(year_pattern, filename)
        year = years[-1] if years else None

        quality = re.search(quality_pattern, filename)
        if quality:
            raw_quality = quality.group()
            raw_quality = raw_quality.lower()
            if 'k' in raw_quality:
                resolution_map = {
                    '2k': '1440p',
                    '4k': '2160p',
                    '8k': '4320p'
                }
                k_value = ''.join(filter(str.isdigit, raw_quality)) + 'k'
                raw_quality = resolution_map.get(k_value, raw_quality)
            quality = raw_quality
        else:
            quality = None

        suffix = re.search(suffix_pattern, filename)
        suffix = suffix.group(1) if suffix else None

        result = {
            '名称': chinese_name if chinese_name else english_name,
            '发行年份': year,
            '视频质量': quality,
            '后缀名': suffix
        }

        if not year and folder_name:
            folder_year = re.search(year_pattern, folder_name)
            if folder_year:
                result['发行年份'] = folder_year.group()

        return result

    # 提取电视剧信息
    def extract_tv_info(filename, folder_name=None):
        """
        提取电视剧信息，支持文件名为纯数字（如01、02、1、2、3）时，将数字作为集数。
        其他信息（如名称、年份、季、清晰度）优先从文件夹名和下载任务标签获取。
        """
        chinese_name_pattern_filename = r'([\u4e00-\u9fa5A-Za-z0-9：]+)(?=\.)'
        chinese_name_pattern_folder = r'】([\u4e00-\u9fa5A-Za-z0-9：$$(). ]+)'
        english_name_pattern = r'([A-Za-z0-9\.\s]+)(?=\.(?:S\d{1,2}|E\d{1,2}|EP\d{1,2}))'
        season_pattern = r'S(\d{1,2})'
        episode_pattern = r'(?:E|EP)(\d{1,2})'
        # 年份只匹配19xx或20xx
        year_pattern = r'(19\d{2}|20\d{2})'
        quality_pattern = r'(\d{1,4}[pPkK])'
        suffix_pattern = r'\.(\w+)$'
    
        # 检查是否为纯数字命名的文件（如01.mp4、2.mkv等）
        pure_number_pattern = r'^(\d{1,3})\.\w+$'
        pure_number_match = re.match(pure_number_pattern, filename)
        episode_number = None
    
        if pure_number_match:
            # 文件名为纯数字，直接作为集数
            episode_number = pure_number_match.group(1).zfill(2)
        else:
            # 正常匹配E01、EP01等
            episode = re.search(episode_pattern, filename)
            episode_number = episode.group(1) if episode else None
    
        # 季号优先从文件名Sxx获取
        season = re.search(season_pattern, filename)
        season_number = season.group(1) if season else None
    
        # 其他信息
        chinese_name = re.search(chinese_name_pattern_filename, filename)
        if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
            chinese_name = chinese_name.group(1)
        else:
            chinese_name = None
    
        if not chinese_name and folder_name:
            chinese_name = re.search(chinese_name_pattern_folder, folder_name)
            if chinese_name and re.search(r'[\u4e00-\u9fa5]', chinese_name.group(1)):
                chinese_name = chinese_name.group(1)
            else:
                chinese_name = None
    
        english_name = re.search(english_name_pattern, filename)
        english_name = english_name.group(1).strip() if english_name else None
        if english_name:
            english_name = english_name.replace('.', ' ').replace('-', ' ')
    
        # 匹配所有年份，取最后一个
        years = re.findall(year_pattern, filename)
        year = years[-1] if years else None
    
        quality = re.search(quality_pattern, filename)
        if quality:
            raw_quality = quality.group()
            raw_quality = raw_quality.lower()
            if 'k' in raw_quality:
                resolution_map = {
                    '2k': '1440p',
                    '4k': '2160p',
                    '8k': '4320p'
                }
                k_value = ''.join(filter(str.isdigit, raw_quality)) + 'k'
                raw_quality = resolution_map.get(k_value, raw_quality)
            quality = raw_quality
        else:
            quality = None
    
        suffix = re.search(suffix_pattern, filename)
        suffix = suffix.group(1) if suffix else None
    
        result = {
            '名称': chinese_name if chinese_name else english_name,
            '发行年份': year,
            '视频质量': quality,
            '后缀名': suffix
        }
    
        if episode_number:
            if not season_number:
                # 尝试从文件夹名或标签补全季号，否则默认为01
                folder_season = None
                if folder_name:
                    folder_season_match = re.search(r'S(\d{1,2})', folder_name, re.IGNORECASE)
                    if folder_season_match:
                        folder_season = folder_season_match.group(1)
                season_number = folder_season if folder_season else '01'
            result.update({
                '季': season_number,
                '集': episode_number
            })

        if not year and folder_name:
            folder_year = re.search(year_pattern, folder_name)
            if folder_year:
                result['发行年份'] = folder_year.group()

        return result

    # 判断是否为纯数字文件名（如01.mkv、2.mp4），此时强制按tv处理
    pure_number_pattern = r'^(\d{1,3})\.\w+$'
    if re.match(pure_number_pattern, filename):
        raw_info = extract_tv_info(filename, folder_name)
    else:
        # 判断是电影还是电视剧
        is_tv = re.search(r'(?:S\d{1,2}|E\d{1,2}|EP\d{1,2})', filename)
        raw_info = extract_tv_info(filename, folder_name) if is_tv else extract_movie_info(filename, folder_name)

    # 优先合并标签信息
    if label_info and isinstance(label_info, dict):
        for key in ['名称', '发行年份', '视频质量', '季', '类型']:
            if key in label_info and label_info[key]:
                raw_info[key] = label_info[key]

    # 如果名称依然为空，尝试从文件夹名补全
    if not raw_info.get('名称') and folder_name:
        # 尝试从文件夹名提取“名称”和“年份”
        # 例如：黄雀-S1-2025、黄雀-2025、黄雀2025
        folder_match = re.match(r'([\u4e00-\u9fa5A-Za-z0-9]+)(?:-S\d+)?-?(\d{4})?', folder_name)
        if folder_match:
            raw_info['名称'] = folder_match.group(1)
            if not raw_info.get('发行年份') and folder_match.group(2):
                raw_info['发行年份'] = folder_match.group(2)

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

def refresh_media_library():
    # 刷新媒体库
    subprocess.run(['python', 'scan_media.py'])  
    # 刷新正在订阅
    subprocess.run(['python', 'check_subscr.py'])   
    # 刷新媒体库tmdb_id
    subprocess.run(['python', 'tmdb_id.py'])

# 全局未识别计数字典，记录每个文件夹未能获取到TMDB ID的次数
unrecognized_count = {}

def process_file(file_path, processed_filenames):
    """
    处理单个文件：
    1. 提取媒体信息，尝试获取 TMDB ID。
    2. 若同一文件夹下连续3次未能获取到 TMDB ID，则将该文件夹移动到 unknown_directory。
    3. 若 result 字典关键字段（如“名称”）为 None，也直接转移文件夹。
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
            tmdb_id, tmdb_name, tmdb_year = get_tmdb_info(result['名称'], result.get('发行年份'), media_type)
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

            if tmdb_id:
                logging.info(f"获取到 TMDB ID: {tmdb_id}，名称：{tmdb_name}")

                # 识别成功，清零该文件夹的未识别计数
                unrecognized_count.pop(folder_name, None)

                title = tmdb_name if tmdb_name else result['名称']
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

                    episode_name = get_tv_episode_name(tmdb_id, season_number, episode_number)
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
                    move_or_copy_file(file_path, target_file_path, action, media_type)
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

                # 超过1次则移动/复制当前文件到 unknown_directory 下以文件夹名为名的文件夹
                if count >= 1 and unknown_directory:
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