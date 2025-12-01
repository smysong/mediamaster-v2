import os
import re
import sqlite3
import logging
import requests
import xml.etree.ElementTree as ET
import xml.dom.minidom
from datetime import datetime
import time
import random

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/scrape_metadata.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

""" 全局缓存：(title, year, media_type) -> tmdb_id """
tmdb_id_cache = {}

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

def get_movie_info_from_tmdb(tmdb_id, config):
    """通过TMDB API获取详细电影信息"""
    TMDB_API_KEY = config['tmdb_api_key']
    TMDB_BASE_URL = config['tmdb_base_url']
    url = f"{TMDB_BASE_URL}/3/movie/{tmdb_id}"
    params = {
        'api_key': TMDB_API_KEY,
        'language': 'zh',
        'append_to_response': 'credits,keywords,images'
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        info = {
            "title": data.get("title"),
            "originaltitle": data.get("original_title"),
            "plot": data.get("overview", ""),
            "year": int(data.get("release_date", "1900-01-01")[:4]) if data.get("release_date") else "",
            "premiered": data.get("release_date"),
            "releasedate": data.get("release_date"),
            "runtime": data.get("runtime"),
            "country": data.get("production_countries", [{}])[0].get("name", ""),
            "genres": [g["name"] for g in data.get("genres", [])],
            "studios": [c["name"] for c in data.get("production_companies", [])],
            "imdbid": data.get("imdb_id"),
            "tmdbid": data.get("id"),
            "rating": data.get("vote_average", 0),
            "sorttitle": data.get("title"),
            "dateadded": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actors": [],
            "director": "",
            "director_tmdbid": "",
            "director_thumb": "",
            "poster": f"https://image.tmdb.org/t/p/original{data['poster_path']}" if data.get("poster_path") else "",
            "fanart": f"https://image.tmdb.org/t/p/original{data['backdrop_path']}" if data.get("backdrop_path") else "",
            "tags": []
        }
        
        # 获取标签信息
        if data.get("keywords") and data["keywords"].get("keywords"):
            info["tags"] = [k["name"] for k in data["keywords"]["keywords"]]
        
        # 演员
        for cast in data.get("credits", {}).get("cast", [])[:20]:
            actor_info = {
                "name": cast.get("name"),
                "role": cast.get("character"),
                "tmdbid": cast.get("id"),
                "imdbid": None
            }
            # 添加演员头像
            if cast.get("profile_path"):
                actor_info["thumb"] = f"https://image.tmdb.org/t/p/original{cast['profile_path']}"
            info["actors"].append(actor_info)
            
        # 导演
        for crew in data.get("credits", {}).get("crew", []):
            if crew.get("job") == "Director":
                info["director"] = crew.get("name")
                info["director_tmdbid"] = crew.get("id")
                # 添加导演头像
                if crew.get("profile_path"):
                    info["director_thumb"] = f"https://image.tmdb.org/t/p/original{crew['profile_path']}"
                break

        # 获取 clearlogo
        if data.get("images"):
            logos = data["images"].get("logos", [])
            logging.debug(f"电影 {data.get('title')} 找到 {len(logos)} 个 logo 候选项")
            if logos:
                # 优先选择中文或英文的logo，且评分较高的
                preferred_logos = [logo for logo in logos if logo.get("iso_639_1") in ("zh", "en", "zh-CN", "zh-TW")]
                if preferred_logos:
                    logging.debug(f"找到 {len(preferred_logos)} 个中英文 logo")
                    # 选择评分最高的
                    best_logo = max(preferred_logos, key=lambda x: x.get("vote_average", 0))
                else:
                    # 如果没有中英文logo，则选择评分最高的
                    best_logo = max(logos, key=lambda x: x.get("vote_average", 0))
                logging.debug(f"选择的 logo 信息: language={best_logo.get('iso_639_1')}, "
                            f"rating={best_logo.get('vote_average')}, path={best_logo.get('file_path')}")
                info["clearlogo"] = f"https://image.tmdb.org/t/p/original{best_logo['file_path']}"
            else:
                logging.debug("未找到任何 logo")
                info["clearlogo"] = None
        else:
            logging.debug("未返回 images 数据")
            info["clearlogo"] = None

        return info
    except Exception as e:
        logging.error(f"获取TMDB详细信息失败: {e}")
        return None

def get_tv_info_from_tmdb(tmdb_id, config):
    """通过TMDB API获取详细剧集信息"""
    TMDB_API_KEY = config['tmdb_api_key']
    TMDB_BASE_URL = config['tmdb_base_url']
    url = f"{TMDB_BASE_URL}/3/tv/{tmdb_id}"
    params = {
        'api_key': TMDB_API_KEY,
        'language': 'zh',
        'append_to_response': 'credits,external_ids,images,keywords'
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        info = {
            "title": data.get("name"),
            "originaltitle": data.get("original_name"),
            "plot": data.get("overview", ""),
            "outline": data.get("overview", ""),
            "year": int(data.get("first_air_date", "1900-01-01")[:4]) if data.get("first_air_date") else "",
            "premiered": data.get("first_air_date"),
            "releasedate": data.get("first_air_date"),
            "runtime": 0,
            "country": data.get("origin_country", [""])[0] if data.get("origin_country") else "",
            "genres": [g["name"] for g in data.get("genres", [])],
            "studios": [c["name"] for c in data.get("production_companies", [])],
            "imdb_id": data.get("external_ids", {}).get("imdb_id"),
            "tmdbid": data.get("id"),
            "rating": data.get("vote_average", 0),
            "votes": data.get("vote_count", 1),
            "sorttitle": data.get("name"),
            "dateadded": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "actors": [],
            "tags": [],
            "tvdbid": data.get("external_ids", {}).get("tvdb_id"),
            "episodeguide": "",
            "id": data.get("external_ids", {}).get("tvdb_id"),
            "season": -1,
            "episode": -1,
            "displayorder": "aired",
            "status": data.get("status", "Continuing"),
            "showtitle": data.get("name"),
            "top250": 0,
            "userrating": 0,
            "poster": "",
            "fanart": "",
            "namedseason": "",
        }
        # 演员
        for cast in data.get("credits", {}).get("cast", [])[:20]:
            actor_info = {
                "name": cast.get("name"),
                "role": cast.get("character"),
                "tmdbid": cast.get("id"),
            }
            # 添加演员头像
            if cast.get("profile_path"):
                actor_info["thumb"] = f"https://image.tmdb.org/t/p/original{cast['profile_path']}"
            info["actors"].append(actor_info)
            
        # tag
        if data.get("keywords", {}).get("results"):
            info["tags"] = [k["name"] for k in data["keywords"]["results"]]
        # episodeguide
        eg = {}
        if info.get("tmdbid"):
            eg["tmdb"] = str(info["tmdbid"])
        if info.get("imdb_id"):
            eg["imdb"] = info["imdb_id"]
        if info.get("tvdbid"):
            eg["tvdb"] = str(info["tvdbid"])
        info["episodeguide"] = str(eg) if eg else ""
        # 图片
        if data.get("poster_path"):
            info["poster"] = f"https://image.tmdb.org/t/p/original{data['poster_path']}"
        if data.get("backdrop_path"):
            info["fanart"] = f"https://image.tmdb.org/t/p/original{data['backdrop_path']}"
        # 获取 clearlogo
        if data.get("images"):
            logos = data["images"].get("logos", [])
            logging.debug(f"剧集 {data.get('name')} 找到 {len(logos)} 个 logo 候选项")
            if logos:
                # 优先选择中文或英文的logo，且评分较高的
                preferred_logos = [logo for logo in logos if logo.get("iso_639_1") in ("zh", "en", "zh-CN", "zh-TW")]
                if preferred_logos:
                    logging.debug(f"找到 {len(preferred_logos)} 个中英文 logo")
                    # 选择评分最高的
                    best_logo = max(preferred_logos, key=lambda x: x.get("vote_average", 0))
                else:
                    # 如果没有中英文logo，则选择评分最高的
                    best_logo = max(logos, key=lambda x: x.get("vote_average", 0))
                logging.debug(f"选择的 logo 信息: language={best_logo.get('iso_639_1')}, "
                            f"rating={best_logo.get('vote_average')}, path={best_logo.get('file_path')}")
                info["clearlogo"] = f"https://image.tmdb.org/t/p/original{best_logo['file_path']}"
            else:
                logging.debug("未找到任何 logo")
                info["clearlogo"] = None
        else:
            logging.debug("未返回 images 数据")
            info["clearlogo"] = None

        # namedseason
        if data.get("seasons") and len(data["seasons"]) > 0:
            info["namedseason"] = f"第 1 季"
        return info
    except Exception as e:
        logging.error(f"获取TMDB剧集详细信息失败: {e}")
        return None

def get_episode_info_from_tmdb(tv_id, season_num, episode_num, config):
    """通过TMDB API获取单集详细信息"""
    TMDB_API_KEY = config['tmdb_api_key']
    TMDB_BASE_URL = config['tmdb_base_url']
    url = f"{TMDB_BASE_URL}/3/tv/{tv_id}/season/{season_num}/episode/{episode_num}"
    params = {
        'api_key': TMDB_API_KEY,
        'language': 'zh',
        'append_to_response': 'credits'
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        info = {
            "plot": data.get("overview", ""),
            "outline": "",
            "lockdata": "false",
            "dateadded": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "title": data.get("name", f"第 {episode_num} 集"),
            "originaltitle": data.get("name", f"第 {episode_num} 集"),
            "actors": [],
            "director": "",
            "director_tmdbid": "",
            "director_thumb": "",
            "rating": 0,
            "year": int(data.get("air_date", "1900-01-01")[:4]) if data.get("air_date") else "",
            "sorttitle": f"第 {episode_num} 集",
            "tmdbid": data.get("id"),
            "runtime": data.get("runtime", ""),
            "studio": "",
            "episode": episode_num,
            "season": season_num,
            "aired": data.get("air_date", ""),
            "showtitle": "",
            "userrating": 0,
            "watched": "false",
            "playcount": 0,
            "source": "UNKNOWN",
            "edition": "NONE",
            "original_filename": "",
            "episode_groups": [
                {"episode": episode_num, "id": "AIRED", "name": "", "season": season_num},
                {"episode": -1, "id": "DISPLAY", "name": "", "season": -1}
            ]
        }
        # 演员
        for cast in data.get("credits", {}).get("cast", [])[:20]:
            actor_info = {
                "name": cast.get("name"),
                "role": cast.get("character"),
                "tmdbid": cast.get("id"),
            }
            # 添加演员头像
            if cast.get("profile_path"):
                actor_info["thumb"] = f"https://image.tmdb.org/t/p/original{cast['profile_path']}"
            info["actors"].append(actor_info)
            
        # 导演
        for crew in data.get("credits", {}).get("crew", []):
            if crew.get("job") == "Director":
                info["director"] = crew.get("name")
                info["director_tmdbid"] = crew.get("id")
                # 添加导演头像
                if crew.get("profile_path"):
                    info["director_thumb"] = f"https://image.tmdb.org/t/p/original{crew['profile_path']}"
                break
        # studio
        if data.get("production_companies"):
            info["studio"] = data["production_companies"][0].get("name", "")
        return info
    except Exception as e:
        logging.error(f"获取TMDB单集详细信息失败: {e}")
        return None

def write_pretty_xml(root, nfo_path):
    """格式化写入xml文件，并支持CDATA"""
    # 创建 DOM 文档
    doc = xml.dom.minidom.Document()
    
    # 将 ElementTree 元素转换为 DOM 元素
    dom_root = _convert_node(root, doc)
    doc.appendChild(dom_root)

    # 格式化并写入文件
    pretty_xml = doc.toprettyxml(indent="  ", encoding="utf-8")
    with open(nfo_path, "wb") as f:
        f.write(pretty_xml)
        logging.debug(f"已保存 NFO 文件: {nfo_path}")
        # 添加随机休眠
        sleep_time = random.uniform(5, 10)
        logging.debug(f"随机休眠 {sleep_time:.2f} 秒，避免频繁请求 API")
        time.sleep(sleep_time)

def _convert_node(element, doc):
    """递归将 ElementTree 节点转换为 minidom 节点"""
    node = doc.createElement(element.tag)

    # 添加属性
    for key, value in element.items():
        node.setAttribute(key, value)

    # 处理子节点或文本
    if element.text and element.text.strip():
        # 如果是 plot 或 outline 字段，则使用 CDATA
        if element.tag in ["plot", "outline"]:
            cdata = doc.createCDATASection(element.text.strip())
            node.appendChild(cdata)
        else:
            text_node = doc.createTextNode(element.text.strip())
            node.appendChild(text_node)

    for child in element:
        child_node = _convert_node(child, doc)
        node.appendChild(child_node)

    return node

def download_image(url, save_path):
    """下载图片并保存"""
    try:
        response = requests.get(url, stream=True, timeout=10)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            logging.info(f"图片已保存: {save_path}")
            return True
        else:
            logging.warning(f"下载失败，状态码: {response.status_code} - {url}")
    except Exception as e:
        logging.error(f"下载图片时出错: {e}")
    return False

def generate_movie_nfo(nfo_path, info, config):
    """生成电影NFO文件，info为包含所有字段的dict"""
    root = ET.Element("movie")
    
    # 根据配置决定是否刮削剧情简介
    if config.get('scrape_plot', 'True') == 'True':
        ET.SubElement(root, "plot").text = f"{info.get('plot','')}"
        ET.SubElement(root, "outline")
    else:
        ET.SubElement(root, "plot")
        ET.SubElement(root, "outline")
        
    ET.SubElement(root, "lockdata").text = "false"
    ET.SubElement(root, "dateadded").text = info.get("dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ET.SubElement(root, "title").text = info.get("title", "")
    ET.SubElement(root, "originaltitle").text = info.get("originaltitle", info.get("title", ""))
    
    # 根据配置决定是否刮削演员信息
    if config.get('scrape_actors', 'True') == 'True':
        # 演员
        for actor in info.get("actors", []):
            actor_el = ET.SubElement(root, "actor")
            ET.SubElement(actor_el, "name").text = actor.get("name", "")
            ET.SubElement(actor_el, "role").text = actor.get("role", "")
            ET.SubElement(actor_el, "type").text = "Actor"
            if actor.get("tmdbid"):
                ET.SubElement(actor_el, "tmdbid").text = str(actor["tmdbid"])
            if actor.get("imdbid"):
                ET.SubElement(actor_el, "imdbid").text = actor["imdbid"]
            # 根据配置决定是否刮削演员头像
            if config.get('scrape_actor_thumb', 'True') == 'True' and actor.get("thumb"):
                ET.SubElement(actor_el, "thumb").text = actor["thumb"]
    
    # 根据配置决定是否刮削导演信息
    if config.get('scrape_director', 'True') == 'True':
        # 导演
        if info.get("director"):
            director_attrs = {"tmdbid": str(info.get("director_tmdbid", ""))}
            # 根据配置决定是否刮削导演头像
            if config.get('scrape_actor_thumb', 'True') == 'True' and info.get("director_thumb"):
                director_attrs["thumb"] = info["director_thumb"]
            director_el = ET.SubElement(root, "director", director_attrs)
            director_el.text = info["director"]
    
    # 根据配置决定是否刮削评分信息
    if config.get('scrape_ratings', 'True') == 'True':
        ET.SubElement(root, "rating").text = str(info.get("rating", 0))
    else:
        ET.SubElement(root, "rating").text = "0"
        
    ET.SubElement(root, "year").text = str(info.get("year", ""))
    ET.SubElement(root, "sorttitle").text = info.get("sorttitle", info.get("title", ""))
    
    if info.get("imdbid"):
        ET.SubElement(root, "imdbid").text = info["imdbid"]
    if info.get("tmdbid"):
        ET.SubElement(root, "tmdbid").text = str(info["tmdbid"])
    if info.get("premiered"):
        ET.SubElement(root, "premiered").text = info["premiered"]
    if info.get("releasedate"):
        ET.SubElement(root, "releasedate").text = info["releasedate"]
    if info.get("runtime"):
        ET.SubElement(root, "runtime").text = str(info["runtime"])
    if info.get("country"):
        ET.SubElement(root, "country").text = info["country"]
        
    # 根据配置决定是否刮削类型信息
    if config.get('scrape_genres', 'True') == 'True':
        for genre in info.get("genres", []):
            ET.SubElement(root, "genre").text = genre
            
    # 根据配置决定是否刮削制片公司信息
    if config.get('scrape_studios', 'True') == 'True':
        for studio in info.get("studios", []):
            ET.SubElement(root, "studio").text = studio
            
    # 根据配置决定是否刮削标签信息
    if config.get('scrape_tags', 'True') == 'True':
        for tag in info.get("tags", []):
            ET.SubElement(root, "tag").text = tag
            
    # 唯一ID
    if info.get("tmdbid"):
        ET.SubElement(root, "uniqueid", type="tmdb").text = str(info["tmdbid"])
    if info.get("imdbid"):
        ET.SubElement(root, "uniqueid", type="imdb").text = info["imdbid"]
    if info.get("imdbid"):
        ET.SubElement(root, "id").text = info["imdbid"]
        
    # 根据配置决定是否刮削海报
    if config.get('scrape_poster', 'True') == 'True':
        if info.get("poster"):
            thumb_el = ET.SubElement(root, "thumb", aspect="poster")
            thumb_el.text = info["poster"]
            
    # 根据配置决定是否刮削背景图
    if config.get('scrape_fanart', 'True') == 'True':
        if info.get("fanart"):
            fanart_el = ET.SubElement(root, "fanart")
            fanart_thumb = ET.SubElement(fanart_el, "thumb")
            fanart_thumb.text = info["fanart"]
            
    movie_dir = os.path.dirname(nfo_path)
    
    # 下载海报
    if config.get('scrape_poster', 'True') == 'True':
        poster_url = info.get("poster")
        if poster_url:
            poster_path = os.path.join(movie_dir, "poster.jpg")
            if not os.path.exists(poster_path):
                download_image(poster_url, poster_path)
                
    # 下载背景图
    if config.get('scrape_fanart', 'True') == 'True':
        fanart_url = info.get("fanart")
        if fanart_url:
            fanart_path = os.path.join(movie_dir, "fanart.jpg")
            if not os.path.exists(fanart_path):
                download_image(fanart_url, fanart_path)
                
    # 下载ClearLogo
    if config.get('scrape_clearlogo', 'True') == 'True':
        clearlogo_url = info.get("clearlogo")
        if clearlogo_url:
            clearlogo_path = os.path.join(movie_dir, "clearlogo.png")
            if not os.path.exists(clearlogo_path):
                download_image(clearlogo_url, clearlogo_path)
                
    logging.info(f"生成影片NFO: {nfo_path}")
    write_pretty_xml(root, nfo_path)

def generate_tvshow_nfo(nfo_path, info, config):
    """生成剧集NFO文件，info为包含所有字段的dict"""
    root = ET.Element("tvshow")
    
    # 根据配置决定是否刮削剧情简介
    if config.get('scrape_plot', 'True') == 'True':
        ET.SubElement(root, "plot").text = f"{info.get('plot','')}"
        ET.SubElement(root, "outline").text = f"{info.get('outline','')}"
    else:
        ET.SubElement(root, "plot")
        ET.SubElement(root, "outline")
        
    ET.SubElement(root, "lockdata").text = "false"
    ET.SubElement(root, "dateadded").text = info.get("dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ET.SubElement(root, "title").text = info.get("title", "")
    ET.SubElement(root, "originaltitle").text = info.get("originaltitle", info.get("title", ""))
    
    # 根据配置决定是否刮削演员信息
    if config.get('scrape_actors', 'True') == 'True':
        # 演员
        for actor in info.get("actors", []):
            actor_el = ET.SubElement(root, "actor")
            ET.SubElement(actor_el, "name").text = actor.get("name", "")
            ET.SubElement(actor_el, "role").text = actor.get("role", "")
            ET.SubElement(actor_el, "type").text = "Actor"
            if actor.get("tmdbid"):
                ET.SubElement(actor_el, "tmdbid").text = str(actor["tmdbid"])
            # 根据配置决定是否刮削演员头像
            if config.get('scrape_actor_thumb', 'True') == 'True' and actor.get("thumb"):
                ET.SubElement(actor_el, "thumb").text = actor["thumb"]
                
    # 根据配置决定是否刮削评分信息
    if config.get('scrape_ratings', 'True') == 'True':
        ET.SubElement(root, "rating").text = str(info.get("rating", 0))
        # ratings
        ratings = ET.SubElement(root, "ratings")
        rating = ET.SubElement(ratings, "rating", default="true", max="10", name="themoviedb")
        ET.SubElement(rating, "value").text = str(info.get("rating", 0))
        ET.SubElement(rating, "votes").text = str(info.get("votes", 1))
    else:
        ET.SubElement(root, "rating").text = "0"
        
    ET.SubElement(root, "year").text = str(info.get("year", ""))
    ET.SubElement(root, "sorttitle").text = info.get("sorttitle", info.get("title", ""))
    
    if info.get("imdb_id"):
        ET.SubElement(root, "imdb_id").text = info["imdb_id"]
    if info.get("tmdbid"):
        ET.SubElement(root, "tmdbid").text = str(info["tmdbid"])
    if info.get("premiered"):
        ET.SubElement(root, "premiered").text = info["premiered"]
    if info.get("releasedate"):
        ET.SubElement(root, "releasedate").text = info["releasedate"]
    if info.get("runtime"):
        ET.SubElement(root, "runtime").text = str(info["runtime"])
    if info.get("country"):
        ET.SubElement(root, "country").text = info["country"]
        
    # 根据配置决定是否刮削类型信息
    if config.get('scrape_genres', 'True') == 'True':
        for genre in info.get("genres", []):
            ET.SubElement(root, "genre").text = genre
            
    # 根据配置决定是否刮削制片公司信息
    if config.get('scrape_studios', 'True') == 'True':
        for studio in info.get("studios", []):
            ET.SubElement(root, "studio").text = studio
            
    # 根据配置决定是否刮削标签
    if config.get('scrape_tags', 'True') == 'True':
        for tag in info.get("tags", []):
            ET.SubElement(root, "tag").text = tag
            
    # 唯一ID
    if info.get("tmdbid"):
        ET.SubElement(root, "uniqueid", type="tmdb").text = str(info["tmdbid"])
    if info.get("imdb_id"):
        ET.SubElement(root, "uniqueid", type="imdb").text = info["imdb_id"]
    if info.get("tvdbid"):
        ET.SubElement(root, "uniqueid", type="tvdb").text = str(info["tvdbid"])
        ET.SubElement(root, "tvdbid").text = str(info["tvdbid"])
        
    # episodeguide
    if info.get("episodeguide"):
        ET.SubElement(root, "episodeguide").text = info["episodeguide"]
    if info.get("id"):
        ET.SubElement(root, "id").text = str(info["id"])
        
    ET.SubElement(root, "season").text = str(info.get("season", -1))
    ET.SubElement(root, "episode").text = str(info.get("episode", -1))
    ET.SubElement(root, "displayorder").text = info.get("displayorder", "aired")
    ET.SubElement(root, "status").text = info.get("status", "Continuing")
    ET.SubElement(root, "showtitle").text = info.get("showtitle", info.get("title", ""))
    ET.SubElement(root, "top250").text = str(info.get("top250", 0))
    ET.SubElement(root, "userrating").text = str(info.get("userrating", 0))
    
    # 根据配置决定是否刮削海报和背景图
    if config.get('scrape_poster', 'True') == 'True':
        if info.get("poster"):
            ET.SubElement(root, "thumb", aspect="poster").text = info["poster"]
            ET.SubElement(root, "thumb", aspect="poster", season="1", type="season").text = info["poster"]
            
    if config.get('scrape_fanart', 'True') == 'True':
        if info.get("fanart"):
            fanart = ET.SubElement(root, "fanart")
            ET.SubElement(fanart, "thumb").text = info["fanart"]
            
    ET.SubElement(root, "certification")
    ET.SubElement(root, "watched").text = "false"
    ET.SubElement(root, "playcount")
    ET.SubElement(root, "user_note")
    
    # namedseason
    if info.get("namedseason"):
        ET.SubElement(root, "namedseason", number="1").text = info["namedseason"]
        
    tv_dir = os.path.dirname(nfo_path)
    
    # 下载海报
    if config.get('scrape_poster', 'True') == 'True':
        poster_url = info.get("poster")
        if poster_url:
            poster_path = os.path.join(tv_dir, "poster.jpg")
            if not os.path.exists(poster_path):
                download_image(poster_url, poster_path)
                
    # 下载背景图
    if config.get('scrape_fanart', 'True') == 'True':
        fanart_url = info.get("fanart")
        if fanart_url:
            fanart_path = os.path.join(tv_dir, "fanart.jpg")
            if not os.path.exists(fanart_path):
                download_image(fanart_url, fanart_path)
                
    # 下载ClearLogo
    if config.get('scrape_clearlogo', 'True') == 'True':
        clearlogo_url = info.get("clearlogo")
        if clearlogo_url:
            clearlogo_path = os.path.join(tv_dir, "clearlogo.png")
            if not os.path.exists(clearlogo_path):
                download_image(clearlogo_url, clearlogo_path)
                
    logging.info(f"生成剧集NFO: {nfo_path}")
    write_pretty_xml(root, nfo_path)

def generate_season_nfo(nfo_path, info, season_number=1):
    """生成季NFO文件，info为包含所有字段的dict"""
    root = ET.Element("season")
    ET.SubElement(root, "seasonnumber").text = str(season_number)
    ET.SubElement(root, "title").text = info.get("showtitle", info.get("title", ""))
    ET.SubElement(root, "showtitle").text = info.get("showtitle", info.get("title", ""))
    ET.SubElement(root, "sorttitle").text = f"季 {season_number:02d}"
    ET.SubElement(root, "year")
    ET.SubElement(root, "plot")
    if info.get("poster"):
        ET.SubElement(root, "thumb", aspect="poster").text = info["poster"]
    ET.SubElement(root, "tvdbid")
    if info.get("imdb_id"):
        ET.SubElement(root, "imdbid").text = info["imdb_id"]
    else:
        ET.SubElement(root, "imdbid")
    if info.get("tmdbid"):
        ET.SubElement(root, "tmdbid").text = str(info["tmdbid"])
        ET.SubElement(root, "uniqueid", type="tmdb").text = str(info["tmdbid"])
    else:
        ET.SubElement(root, "tmdbid")
    if info.get("premiered"):
        ET.SubElement(root, "premiered").text = info["premiered"]
    else:
        ET.SubElement(root, "premiered")
    ET.SubElement(root, "outline")
    ET.SubElement(root, "dateadded").text = info.get("dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if info.get("releasedate"):
        ET.SubElement(root, "releasedate").text = info["releasedate"]
    else:
        ET.SubElement(root, "releasedate")
    ET.SubElement(root, "user_note")
    logging.info(f"生成季NFO: {nfo_path}")
    write_pretty_xml(root, nfo_path)

def generate_episode_nfo(nfo_path, episode_info):
    """生成集NFO文件，episode_info为包含所有字段的dict"""
    root = ET.Element("episodedetails")
    ET.SubElement(root, "plot").text = f"{episode_info.get('plot', '')}"
    ET.SubElement(root, "outline")
    ET.SubElement(root, "lockdata").text = "false"
    ET.SubElement(root, "dateadded").text = episode_info.get("dateadded", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    ET.SubElement(root, "title").text = episode_info.get("title", "")
    ET.SubElement(root, "originaltitle").text = episode_info.get("originaltitle", episode_info.get("title", ""))
    # 演员
    for actor in episode_info.get("actors", []):
        actor_el = ET.SubElement(root, "actor")
        ET.SubElement(actor_el, "name").text = actor.get("name", "")
        ET.SubElement(actor_el, "role").text = actor.get("role", "")
        ET.SubElement(actor_el, "type").text = "Actor"
        if actor.get("tmdbid"):
            ET.SubElement(actor_el, "tmdbid").text = str(actor["tmdbid"])
        # 根据配置决定是否刮削演员头像
        if actor.get("thumb"):
            ET.SubElement(actor_el, "thumb").text = actor["thumb"]
    # 导演
    if episode_info.get("director"):
        director_attrs = {"tmdbid": str(episode_info.get("director_tmdbid", ""))}
        # 根据配置决定是否刮削导演头像
        if episode_info.get("director_thumb"):
            director_attrs["thumb"] = episode_info["director_thumb"]
        director_el = ET.SubElement(root, "director", director_attrs)
        director_el.text = episode_info["director"]
    ET.SubElement(root, "rating").text = str(episode_info.get("rating", 0))
    ET.SubElement(root, "year").text = str(episode_info.get("year", ""))
    ET.SubElement(root, "sorttitle").text = episode_info.get("sorttitle", "")
    if episode_info.get("tmdbid"):
        ET.SubElement(root, "tmdbid").text = str(episode_info["tmdbid"])
        ET.SubElement(root, "uniqueid", type="tmdb").text = str(episode_info["tmdbid"])
    if episode_info.get("runtime"):
        ET.SubElement(root, "runtime").text = str(episode_info["runtime"])
    if episode_info.get("studio"):
        ET.SubElement(root, "studio").text = episode_info["studio"]
    ET.SubElement(root, "episode").text = str(episode_info.get("episode", ""))
    ET.SubElement(root, "season").text = str(episode_info.get("season", ""))
    if episode_info.get("aired"):
        ET.SubElement(root, "aired").text = episode_info["aired"]
    ET.SubElement(root, "showtitle").text = episode_info.get("showtitle", "")
    ET.SubElement(root, "ratings")
    ET.SubElement(root, "userrating").text = str(episode_info.get("userrating", 0))
    ET.SubElement(root, "watched").text = str(episode_info.get("watched", "false")).lower()
    ET.SubElement(root, "playcount").text = str(episode_info.get("playcount", 0))
    ET.SubElement(root, "epbookmark")
    ET.SubElement(root, "code")
    ET.SubElement(root, "source").text = episode_info.get("source", "UNKNOWN")
    ET.SubElement(root, "edition").text = episode_info.get("edition", "NONE")
    ET.SubElement(root, "original_filename").text = episode_info.get("original_filename", "")
    ET.SubElement(root, "user_note")
    # episode_groups
    if episode_info.get("episode_groups"):
        egroups_el = ET.SubElement(root, "episode_groups")
        for group in episode_info["episode_groups"]:
            ET.SubElement(
                egroups_el, "group",
                episode=str(group.get("episode", "")),
                id=group.get("id", ""),
                name=group.get("name", ""),
                season=str(group.get("season", ""))
            )
    logging.info(f"生成集NFO: {nfo_path}")
    write_pretty_xml(root, nfo_path)

def scan_metadata(path, config, path_type):
    """扫描指定路径下的媒体文件，只生成缺失的NFO文件（区分电影/剧集路径）"""
    media_extensions = ['.mkv', '.mp4', '.avi', '.mov', '.flv', '.wmv', '.iso']
    
    for root, dirs, files in os.walk(path):
        if path_type == 'movie':
            # 仅处理电影文件，不执行任何剧集相关逻辑
            process_movies(root, files, media_extensions, config)
        elif path_type == 'tv':
            # 仅处理剧集相关逻辑，不执行电影处理逻辑
            process_tvshow_directory(root, dirs, config)
            process_season_directory(root, dirs, config)
            process_episode_files(root, files, media_extensions, config)

def process_movies(root, files, media_extensions, config):
    """处理电影文件"""
    
    # 多种电影命名格式（与 scan_media.py 保持一致）
    movie_patterns = [
        re.compile(r'^(.*?)\s*-\s*\((\d{4})\)'),     # Title - (Year)
        re.compile(r'^(.*?)\s*\((\d{4})\)'),         # Title (Year)
        re.compile(r'^(.*?)\s*\[([12]\d{3})\]'),     # Title [Year]
        re.compile(r'^(.*?)\s*([12]\d{3})\s*-'),     # Title Year -
        re.compile(r'^(.*?)\s*\.\s*([12]\d{3})\s*\.'), # Title.Year.
    ]

    for file in files:
        if any(file.lower().endswith(ext) for ext in media_extensions):
            matched = False
            for pattern in movie_patterns:
                match = pattern.match(os.path.splitext(file)[0])
                if match:
                    movie_name = match.group(1).strip()
                    year = int(match.group(2))
                    media_file_name = os.path.splitext(file)[0]
                    nfo_file_path = os.path.join(root, media_file_name + '.nfo')
                    
                    if os.path.exists(nfo_file_path):
                        logging.debug(f"电影NFO已存在，跳过: {nfo_file_path}")
                        matched = True
                        break
                    
                    tmdb_id = query_tmdb_id(movie_name, year, 'movie', config)
                    if not tmdb_id:
                        tmdb_id = query_tmdb_api(movie_name, year, 'movie', config)
                    if tmdb_id:
                        info = get_movie_info_from_tmdb(tmdb_id, config)
                        if info:
                            generate_movie_nfo(nfo_file_path, info, config)  # 传递配置
                    else:
                        logging.warning(f"未找到TMDB_ID: {movie_name} ({year})，跳过NFO生成")
                    
                    matched = True
                    break
            
            if not matched:
                logging.warning(f"无法从文件名提取标题和年份: {file}")

def process_tvshow_directory(root, dirs, config):
    """处理剧集主目录"""
    
    # 多种电视剧目录命名格式（与 scan_media.py 保持一致）
    tv_patterns = [
        re.compile(r'^(.*?)\s*-\s*\((\d{4})\)'),     # Title - (Year)
        re.compile(r'^(.*?)\s*\((\d{4})\)'),         # Title (Year)
        re.compile(r'^(.*?)\s*\[([12]\d{3})\]'),     # Title [Year]
        re.compile(r'^(.*?)\s*([12]\d{3})\s*-'),     # Title Year -
        re.compile(r'^(.*?)\s*\.\s*([12]\d{3})\s*\.'), # Title.Year.
    ]
    
    dir_name = os.path.basename(root)
    dir_matched = False
    
    for pattern in tv_patterns:
        dir_match = pattern.match(dir_name)
        if dir_match:
            tv_name = dir_match.group(1).strip()
            tv_year = int(dir_match.group(2))
            dir_matched = True
            break
    
    if not dir_matched:
        return  # 不是剧集主目录，跳过
    
    tvshow_nfo_path = os.path.join(root, 'tvshow.nfo')
    if os.path.exists(tvshow_nfo_path):
        logging.debug(f"剧集NFO已存在，跳过: {tvshow_nfo_path}")
        return  # 跳出函数，不处理此目录

    tmdb_id = query_tmdb_id(tv_name, tv_year, 'tv', config)
    if not tmdb_id:
        tmdb_id = query_tmdb_api(tv_name, tv_year, 'tv', config)
    if tmdb_id:
        info = get_tv_info_from_tmdb(tmdb_id, config)
        if info:
            generate_tvshow_nfo(tvshow_nfo_path, info, config)

def process_season_directory(root, dirs, config):
    """处理剧集季目录"""
    parent_dir = os.path.dirname(root)
    parent_name = os.path.basename(parent_dir)
    
    # 多种电视剧目录命名格式（与 scan_media.py 保持一致）
    tv_patterns = [
        re.compile(r'^(.*?)\s*-\s*\((\d{4})\)'),     # Title - (Year)
        re.compile(r'^(.*?)\s*\((\d{4})\)'),         # Title (Year)
        re.compile(r'^(.*?)\s*\[([12]\d{3})\]'),     # Title [Year]
        re.compile(r'^(.*?)\s*([12]\d{3})\s*-'),     # Title Year -
        re.compile(r'^(.*?)\s*\.\s*([12]\d{3})\s*\.'), # Title.Year.
    ]
    
    parent_matched = False
    for pattern in tv_patterns:
        parent_match = pattern.match(parent_name)
        if parent_match:
            tv_name = parent_match.group(1).strip()
            tv_year = int(parent_match.group(2))
            parent_matched = True
            break
    
    if not parent_matched:
        return  # 没有找到父目录中的剧集信息，跳过
    
    # 多种季目录命名格式（与 scan_media.py 保持一致）
    season_patterns = [
        re.compile(r'^Season\s+(\d+)$', re.IGNORECASE),    # Season 1
        re.compile(r'^S(\d+)$', re.IGNORECASE),            # S01
        re.compile(r'^Season\.?(\d+)$', re.IGNORECASE),    # Season1 or Season.1
        re.compile(r'^第(\d+)季$', re.IGNORECASE),          # 第1季 (中文)
    ]
    
    dir_name = os.path.basename(root)
    
    # 检查是否为季目录
    season_number = None
    for pattern in season_patterns:
        match = pattern.match(dir_name)
        if match:
            season_number = int(match.group(1))
            break
    
    if season_number is None:
        return  # 不是季目录，跳过
    
    season_nfo_path = os.path.join(root, 'season.nfo')
    if os.path.exists(season_nfo_path):
        logging.debug(f"季NFO已存在，跳过: {season_nfo_path}")
        return  # 跳出函数，不处理此目录

    tmdb_id = query_tmdb_id(tv_name, tv_year, 'tv', config)
    if not tmdb_id:
        tmdb_id = query_tmdb_api(tv_name, tv_year, 'tv', config)
    if tmdb_id:
        info = get_tv_info_from_tmdb(tmdb_id, config)
        if info:
            generate_season_nfo(season_nfo_path, info, season_number=season_number)

def process_episode_files(root, files, media_extensions, config):
    """处理单集文件"""
    
    # 多种剧集文件命名格式（扩展自 scan_media.py 的模式）
    episode_patterns = [
        re.compile(r'^(.*) - S(\d{1,2})E(\d{1,4}) - (.*)$', re.IGNORECASE),  # 格式: Show Name - S1E1 - Episode Title
        re.compile(r'^(.*)\.S(\d{1,2})E(\d{1,4})\.(.*)$', re.IGNORECASE),    # 格式: Show.Name.S1E1.Episode.Title
        re.compile(r'^(.*) - (\d{1,2})x(\d{1,4}) - (.*)$', re.IGNORECASE),   # 格式: Show Name - 1x1 - Episode Title
        re.compile(r'^(.*)\.(\d{1,2})x(\d{1,4})\.(.*)$', re.IGNORECASE),     # 格式: Show.Name.1x1.Episode.Title
        re.compile(r'^(.*) - S(\d{1,2})E(\d{1,4})$', re.IGNORECASE),         # 格式: Show Name - S1E1 (无标题)
        re.compile(r'^(.*)\.S(\d{1,2})E(\d{1,4})$', re.IGNORECASE),          # 格式: Show.Name.S1E1 (无标题)
    ]
    
    for file in files:
        # 首先检查是否为支持的媒体文件
        if not any(file.lower().endswith(ext) for ext in media_extensions):
            logging.debug(f"文件不是媒体文件，跳过: {file}")
            continue
            
        # 对于媒体文件，检查是否符合剧集命名规范
        episode_match = None
        for pattern in episode_patterns:
            episode_match = pattern.match(os.path.splitext(file)[0])
            if episode_match:
                break
                
        if not episode_match:
            # 不是剧集文件命名格式，跳过
            logging.debug(f"文件不符合剧集命名格式，跳过: {file}")
            continue
        
        show_name = episode_match.group(1).strip()
        season_num = int(episode_match.group(2))
        episode_num = int(episode_match.group(3))
        episode_nfo_path = os.path.join(root, os.path.splitext(file)[0] + '.nfo')
        
        if os.path.exists(episode_nfo_path):
            logging.debug(f"集NFO已存在，跳过: {episode_nfo_path}")
            continue
        
        # 向上查找剧集主目录
        parent = root
        info = None
        found_tv = False
        
        # 多种电视剧目录命名格式（用于向上搜索）
        tv_patterns = [
            re.compile(r'^(.*?)\s*-\s*\((\d{4})\)'),     # Title - (Year)
            re.compile(r'^(.*?)\s*\((\d{4})\)'),         # Title (Year)
            re.compile(r'^(.*?)\s*\[([12]\d{3})\]'),     # Title [Year]
            re.compile(r'^(.*?)\s*([12]\d{3})\s*-'),     # Title Year -
            re.compile(r'^(.*?)\s*\.\s*([12]\d{3})\s*\.'), # Title.Year.
        ]
        
        while not found_tv and parent != os.path.dirname(parent):  # 防止无限循环
            parent_dir = os.path.dirname(parent)
            parent_name = os.path.basename(parent_dir)
            
            for pattern in tv_patterns:
                parent_match = pattern.match(parent_name)
                if parent_match:
                    tvshow_nfo_path = os.path.join(parent_dir, 'tvshow.nfo')
                    if os.path.exists(tvshow_nfo_path):
                        tv_name = parent_match.group(1).strip()
                        tv_year = int(parent_match.group(2))
                        tmdb_id = query_tmdb_id(tv_name, tv_year, 'tv', config)
                        if not tmdb_id:
                            tmdb_id = query_tmdb_api(tv_name, tv_year, 'tv', config)
                        if tmdb_id:
                            info = get_tv_info_from_tmdb(tmdb_id, config)
                            found_tv = True
                        break
                    else:
                        tv_name = parent_match.group(1).strip()
                        tv_year = int(parent_match.group(2))
                        tmdb_id = query_tmdb_id(tv_name, tv_year, 'tv', config)
                        if not tmdb_id:
                            tmdb_id = query_tmdb_api(tv_name, tv_year, 'tv', config)
                        if tmdb_id:
                            info = get_tv_info_from_tmdb(tmdb_id, config)
                            found_tv = True
                        break
            if found_tv:
                break
            parent = parent_dir
        
        if info:
            episode_info = get_episode_info_from_tmdb(info["tmdbid"], season_num, episode_num, config)
            if episode_info:
                episode_info["showtitle"] = info.get("showtitle", info.get("title", ""))
                episode_info["original_filename"] = file
                if not episode_info.get("studio"):
                    episode_info["studio"] = info.get("studios", [""])[0] if info.get("studios") else ""
                generate_episode_nfo(episode_nfo_path, episode_info)
        else:
            logging.warning(f"未找到TMDB_ID，跳过NFO生成: {file}")

def query_tmdb_id(title, year, media_type, config):
    """通过数据库查询获取tmdb_id"""
    cache_key = (title, year, media_type)
    if cache_key in tmdb_id_cache:
        logging.debug(f"从缓存中找到TMDB_ID: {tmdb_id_cache[cache_key]}")
        return tmdb_id_cache[cache_key]

    db_path = config['db_path']
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            if media_type == 'movie':
                cursor.execute("SELECT tmdb_id FROM LIB_MOVIES WHERE title = ? AND year = ?", (title, year))
            elif media_type == 'tv':
                cursor.execute("SELECT tmdb_id FROM LIB_TVS WHERE title = ? AND year = ?", (title, year))
            result = cursor.fetchone()
            if result and result[0]:
                tmdb_id = result[0]
                tmdb_id_cache[cache_key] = tmdb_id  # 写入缓存
                logging.info(f"从数据库中找到TMDB_ID: {tmdb_id}")
                return tmdb_id
            else:
                logging.info(f"数据库中未找到标题：{title}, 年份：{year}, 类型：{media_type} 的有效TMDB_ID")
                return None
    except sqlite3.Error as e:
        logging.error(f"查询数据库时出错: {e}")
    return None

def query_tmdb_api(title, year, media_type, config):
    """通过TMDB API查询获取tmdb_id"""
    cache_key = (title, year, media_type)
    if cache_key in tmdb_id_cache:
        logging.debug(f"从缓存中找到TMDB_ID: {tmdb_id_cache[cache_key]}")
        return tmdb_id_cache[cache_key]

    TMDB_API_KEY = config['tmdb_api_key']
    TMDB_BASE_URL = config['tmdb_base_url']
    url = f"{TMDB_BASE_URL}/3/search/{media_type}"
    params = {
        'api_key': TMDB_API_KEY,
        'query': title,
        'language': 'zh-CN',
        'include_adult': 'false'
    }
    logging.info(f"通过TMDB API查询 {title} 获取TMDB_ID")
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        search_results = response.json().get('results', [])
        for result in search_results:
            if media_type == 'movie':
                release_date = result.get('release_date', '')
                if release_date and release_date.startswith(str(year)):
                    tmdb_id = result.get('id')
                    tmdb_id_cache[cache_key] = tmdb_id  # 写入缓存
                    logging.info(f"查询到匹配的电影, TMDB_ID: {tmdb_id}")
                    return tmdb_id
            elif media_type == 'tv':
                first_air_date = result.get('first_air_date', '')
                if first_air_date and first_air_date.startswith(str(year)):
                    tmdb_id = result.get('id')
                    tmdb_id_cache[cache_key] = tmdb_id  # 写入缓存
                    logging.info(f"查询到匹配的电视剧, TMDB_ID: {tmdb_id}")
                    return tmdb_id
    except Exception as e:
        logging.error(f"查询TMDB API时出错: {e}")
    logging.info(f"未查询到标题: {title}, 年份: {year}所匹配的TMDB_ID")
    return None

def main():
    # 从配置文件中读取路径信息
    db_path = '/config/data.db'
    config = load_config(db_path)
    config['db_path'] = db_path
    
    # 新增：检查程序启用状态
    program_enabled = config['scrape_metadata']
    # 支持字符串和布尔类型
    if isinstance(program_enabled, str):
        program_enabled = program_enabled.lower() == "true"
    if not program_enabled:
        logging.info("媒体元数据刮削功能未启用，程序无需运行。")
        exit(0)
        
    movies_path = config['movies_path']
    episodes_path = config['episodes_path']
    anime_path = config['anime_path']
    variety_path = config['variety_path']
    
    # 扫描电影路径（指定path_type为'movie'）
    scan_metadata(movies_path, config, path_type='movie')
    
    # 扫描剧集路径（指定path_type为'tv'）
    scan_metadata(episodes_path, config, path_type='tv')

    # 扫描动漫路径（指定path_type为'tv'）
    scan_metadata(anime_path, config, path_type='tv')

    # 扫描综艺路径（指定path_type为'variety'）
    scan_metadata(variety_path, config, path_type='tv')

if __name__ == "__main__":
    main()