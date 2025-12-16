import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse, parse_qs
import time
import random
import sqlite3
import logging
import re

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/subscr.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

# 中文数字转阿拉伯数字的字典
chinese_to_arabic = {
    '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
    '十': 10, '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15, '十六': 16, '十七': 17,
    '十八': 18, '十九': 19, '二十': 20, '二十一': 21, '二十二': 22, '二十三': 23, '二十四': 24,
    '二十五': 25, '二十六': 26, '二十七': 27, '二十八': 28, '二十九': 29, '三十': 30
}

def chinese_to_int(chinese_num):
    """将中文数字转换为阿拉伯数字"""
    parts = chinese_num.split('十')
    if len(parts) == 1:
        return chinese_to_arabic[chinese_num]
    elif len(parts) == 2:
        tens = 10 if parts[0] == '' else chinese_to_arabic[parts[0]]
        units = 0 if parts[1] == '' else chinese_to_arabic[parts[1]]
        return tens + units
    else:
        raise ValueError(f"无法解析中文数字: {chinese_num}")

class DouBanRSSParser:
    def __init__(self):
        self.load_config()
        self.cookie = self.config.get("douban_cookie", "")
        self.douban_user_ids = self.config.get("douban_user_ids", "your_douban_id")  # 修改为读取用户ID列表
        self.db_path = '/config/data.db'
        self.pcheaders = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/105.0.0.0 Safari/537.36 Edg/105.0.1343.27",
            "Referer": "https://movie.douban.com/",
            "Cookie": self.cookie,
            "Connection": "keep-alive",
        }
        self.db_connection = sqlite3.connect(self.db_path)

    def load_config(self, db_path='/config/data.db'):
        """从数据库中加载配置"""
        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT OPTION, VALUE FROM CONFIG')
                config_items = cursor.fetchall()
                self.config = {option: value for option, value in config_items}
            
            logging.debug("加载配置文件成功")
        except sqlite3.Error as e:
            logging.error(f"数据库加载配置错误: {e}")
            exit(0)

    def config(self, key, default=None):
        """获取配置项的值"""
        return self.config.get(key, default)

    def fetch_rss_data(self):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
        }
        
        # 解析多个用户ID
        user_ids = [uid.strip() for uid in self.douban_user_ids.split(',') if uid.strip() and uid.strip() != "your_douban_id"]
        all_rss_data = []
        
        for user_id in user_ids:
            rss_url = f"https://www.douban.com/feed/people/{user_id}/interests"
            try:
                response = requests.get(rss_url, headers=headers, timeout=10)
                if response.status_code == 200:
                    logging.info(f"成功获取豆瓣用户 {user_id} 的兴趣数据")
                    all_rss_data.append(response.text)
                else:
                    logging.error(f"获取豆瓣用户 {user_id} 的兴趣数据失败，状态码: {response.status_code}")
            except requests.RequestException as e:
                logging.error(f"请求豆瓣用户 {user_id} 的兴趣数据时发生错误: {e}")
        
        return all_rss_data

    def parse_rss_data(self, rss_data_list):
        # 如果传入的是单个字符串而非列表，则转换为列表
        if isinstance(rss_data_list, str):
            rss_data_list = [rss_data_list]
            
        all_parsed_items = []
        seen_douban_ids = set()  # 用于跟踪已处理的豆瓣ID
        
        for rss_data in rss_data_list:
            if not rss_data:
                continue

            try:
                root = ET.fromstring(rss_data)
                items = root.findall('.//item')
                for item in items:
                    title = item.find('title').text
                    link = item.find('link').text
                    
                    # 提取豆瓣ID
                    parsed_url = urlparse(link)
                    path_segments = parsed_url.path.split('/')
                    douban_id = path_segments[-2] if path_segments[-1] == '' else path_segments[-1]
                    douban_id = int(douban_id)  # 将豆瓣ID转换为int类型

                    # 检查项目状态：想看、在看、看过
                    status = "想看"  # 默认状态
                    if title.startswith('看过'):
                        status = "看过"
                        # 移除标题开头的"看过"
                        title = title.replace('看过', '', 1)
                    elif title.startswith('想看'):
                        status = "想看"
                        # 移除标题开头的"想看"
                        title = title.replace('想看', '', 1)
                    elif title.startswith('在看'):
                        status = "在看"
                        # 移除标题开头的"在看"
                        title = title.replace('在看', '', 1)
                    else:
                        logging.warning(f"未知状态的项目: {title}（豆瓣ID: {douban_id}）")
                        continue

                    # 检查是否已经处理过这个豆瓣ID，避免重复
                    if douban_id not in seen_douban_ids:
                        seen_douban_ids.add(douban_id)
                        all_parsed_items.append((title, douban_id, status))
                    else:
                        logging.info(f"多用户重复{status}: {title}（豆瓣ID: {douban_id}）将忽略并保留一份有效订阅")
                        
                logging.info("成功解析豆瓣兴趣项目")
            except ET.ParseError as e:
                logging.error(f"解析豆瓣兴趣项目时发生错误: {e}")
        
        return all_parsed_items

    def fetch_existing_douban_ids(self):
        cursor = self.db_connection.cursor()
        cursor.execute('SELECT douban_id FROM RSS_MOVIES')
        existing_movie_ids = {int(row[0]) for row in cursor.fetchall()}  # 将豆瓣ID转换为整数类型
        cursor.execute('SELECT douban_id FROM RSS_TVS')
        existing_tv_ids = {int(row[0]) for row in cursor.fetchall()}  # 将豆瓣ID转换为整数类型
        return existing_movie_ids.union(existing_tv_ids)

    def fetch_movie_details(self, title, douban_id, status):
        # 去除常见标点符号和空白符
        cleaned_title = re.sub(r'[：:.，,！!？?“”‘’"\'（）()【】\[\]「」{}《》<>\u00B7\u2027]', '', title)
        logging.info(f"正在获取标题为 {cleaned_title} 的详细信息，豆瓣ID: {douban_id}，状态: {status}")
        api_url = f'https://movie.douban.com/j/subject_suggest?q={cleaned_title}'
        try:
            response = requests.get(api_url, headers=self.pcheaders, timeout=10)
            if response.status_code == 200:
                api_data = response.json()
                if api_data:
                    # 使用豆瓣ID匹配最佳结果
                    for movie_info in api_data:
                        if movie_info.get('id') == str(douban_id):  # 将豆瓣ID转换为字符串进行匹配
                            episode = movie_info.get('episode', '')
                            # 新增：跳过无效集数
                            if str(episode).lower() == 'unknow':
                                logging.warning(f"跳过集数无效的项目: {title} (获取到集数为：{episode})")
                                return None
                            year = movie_info.get('year', '')
                            img = movie_info.get('img', '')
                            title = movie_info.get('title', '')
                            url = movie_info.get('url', '')
                            sub_title = movie_info.get('sub_title', '')
                            douban_id = int(movie_info.get('id', ''))  # 确保豆瓣ID为整数类型

                            # 判断影片类型
                            media_type = '电影' if episode == '' else '电视剧'

                            # 提取季数
                            season_match = re.search(r'第(\d+|零|一|二|三|四|五|六|七|八|九|十|十一|十二|十三|十四|十五|十六|十七|十八|十九|二十|二十一|二十二|二十三|二十四|二十五|二十六|二十七|二十八|二十九|三十)季', title)
                            if season_match:
                                season_str = season_match.group(1)
                                if season_str.isdigit():
                                    season = int(season_str)
                                else:
                                    season = chinese_to_int(season_str)
                                # 去除标题中的"第X季"
                                title = re.sub(r'第\d+季|第零季|第一季|第二季|第三季|第四季|第五季|第六季|第七季|第八季|第九季|第十季|第十一季|第十二季|第十三季|第十四季|第十五季|第十六季|第十七季|第十八季|第十九季|第二十季|第二十一季|第二十二季|第二十三季|第二十四季|第二十五季|第二十六季|第二十七季|第二十八季|第二十九季|第三十季', '', title)
                            else:
                                season = 1

                            # 去除标题中的多余空格
                            title = re.sub(r'\s+', ' ', title).strip()

                            return {
                                'title': title,
                                'douban_id': douban_id,
                                'episode': episode,
                                'year': year,
                                'img': img,
                                'url': url,
                                'sub_title': sub_title,
                                'media_type': media_type,
                                'season': season,
                                'status': status  # 添加状态信息
                            }
                    logging.warning(f"未找到豆瓣ID为 {douban_id} 的信息")
                    return None
                else:
                    logging.warning(f"未找到标题为 {title} 的信息")
                    return None
            else:
                logging.error(f"获取标题为 {title} 的详细信息失败，状态码: {response.status_code}")
                return None
        except requests.RequestException as e:
            logging.error(f"请求豆瓣API时发生错误: {e}")
            return None

    def insert_into_db(self, movie_details):
        cursor = self.db_connection.cursor()
        try:
            if movie_details['media_type'] == '电影':
                cursor.execute('''INSERT INTO RSS_MOVIES (title, douban_id, year, url, sub_title, status)
                                VALUES (?, ?, ?, ?, ?, ?)''',
                            (movie_details['title'], movie_details['douban_id'], movie_details['year'], 
                             movie_details['url'], movie_details['sub_title'], movie_details['status']))
            elif movie_details['media_type'] == '电视剧':
                cursor.execute('''INSERT INTO RSS_TVS (title, douban_id, episode, year, url, sub_title, season, status)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                            (movie_details['title'], movie_details['douban_id'], movie_details['episode'], 
                             movie_details['year'], movie_details['url'], movie_details['sub_title'], 
                             movie_details['season'], movie_details['status']))
            self.db_connection.commit()
            logging.info(f"成功插入 {movie_details['title']} 到数据库，状态: {movie_details['status']}")
            logging.info("-" * 80)
        except sqlite3.IntegrityError:
            logging.warning(f"已存在相同的豆瓣ID {movie_details['douban_id']}，跳过插入")
        except sqlite3.Error as e:
            logging.error(f"插入数据库时发生错误: {e}")

    def delete_old_data(self, existing_douban_ids, new_douban_ids):
        cursor = self.db_connection.cursor()
        old_douban_ids = existing_douban_ids - new_douban_ids
        for douban_id in old_douban_ids:
            cursor.execute('DELETE FROM RSS_MOVIES WHERE douban_id = ?', (douban_id,))
            cursor.execute('DELETE FROM RSS_TVS WHERE douban_id = ?', (douban_id,))
        self.db_connection.commit()
        if old_douban_ids:
            logging.info(f"删除过时的数据: {old_douban_ids}")
        else:
            logging.info("没有过时的数据需要删除")

    def check_and_update_media_info(self):
        """
        统一检查并更新数据库中电影和剧集的信息
        包括电影标题和TV剧集的标题及集数
        通过合并查询减少API请求次数
        """
        cursor = self.db_connection.cursor()
        
        # 查询RSS_MOVIES表中的所有电影
        cursor.execute('SELECT douban_id, title FROM RSS_MOVIES')
        movies_list = cursor.fetchall()
        
        # 查询RSS_TVS表中的所有剧集（包含标题和集数）
        cursor.execute('SELECT douban_id, title, episode FROM RSS_TVS')
        tvs_list = cursor.fetchall()
        
        if not movies_list and not tvs_list:
            logging.info("数据库中没有项目需要检查信息")
            return
        
        logging.info(f"开始检查媒体信息更新：共 {len(movies_list)} 个电影和 {len(tvs_list)} 个剧集")
        
        # 处理电影标题检查
        for douban_id, local_title in movies_list:
            logging.info(f"检查电影: {local_title} (豆瓣ID: {douban_id})")
            self._check_and_update_single_item("电影", douban_id, local_title, cursor)
            
            # 随机休眠避免频繁请求
            sleep_time = random.uniform(10, 15)
            time.sleep(sleep_time)
        
        # 处理TV剧集标题和集数检查
        for douban_id, local_title, local_episode in tvs_list:
            logging.info(f"检查剧集: {local_title} (豆瓣ID: {douban_id})")
            self._check_and_update_single_item("剧集", douban_id, local_title, cursor, local_episode)
            
            # 随机休眠避免频繁请求
            sleep_time = random.uniform(10, 15)
            time.sleep(sleep_time)
        
        logging.info("媒体信息检查完成")

    def _check_and_update_single_item(self, media_type, douban_id, local_title, cursor, local_episode=None):
        """
        检查并更新单个项目的信息
        
        Args:
            media_type: 媒体类型("电影"或"剧集")
            douban_id: 豆瓣ID
            local_title: 本地标题
            cursor: 数据库游标
            local_episode: 本地集数(仅对剧集有效)
        """
        # 获取豆瓣上的最新信息
        api_url = f'https://movie.douban.com/j/subject_suggest?q={local_title}'
        try:
            response = requests.get(api_url, headers=self.pcheaders, timeout=10)
            if response.status_code == 200:
                api_data = response.json()
                if api_data:
                    # 查找匹配的豆瓣ID
                    for movie_info in api_data:
                        if movie_info.get('id') == str(douban_id):
                            latest_title = movie_info.get('title', '')
                            
                            # 标准化标题数据格式
                            local_title_normalized = str(local_title).strip() if local_title is not None else ''
                            latest_title_normalized = str(latest_title).strip() if latest_title is not None else ''
                            
                            # 初始化更新标志
                            title_updated = False
                            
                            # 比较标题是否有变化
                            if latest_title_normalized != local_title_normalized:
                                logging.info(f"发现{media_type}标题更新: 本地 '{local_title_normalized}' -> 豆瓣 '{latest_title_normalized}'")
                                
                                # 更新数据库中的标题
                                if media_type == "电影":
                                    cursor.execute('UPDATE RSS_MOVIES SET title = ? WHERE douban_id = ?', 
                                                (latest_title_normalized, douban_id))
                                else:  # 剧集
                                    cursor.execute('UPDATE RSS_TVS SET title = ? WHERE douban_id = ?', 
                                                (latest_title_normalized, douban_id))
                                
                                title_updated = True
                            
                            # 如果是剧集，还要检查集数
                            episode_updated = False
                            if media_type == "剧集" and local_episode is not None:
                                latest_episode = movie_info.get('episode', '')
                                
                                # 跳过无效集数
                                if str(latest_episode).lower() in ['unknow', 'unknown', 'n/a', 'null', 'none']:
                                    logging.warning(f"跳过集数无效的剧集: {local_title} (豆瓣集数为: {latest_episode})")
                                else:
                                    # 标准化集数数据格式
                                    local_episode_normalized = str(local_episode).strip() if local_episode is not None else ''
                                    latest_episode_normalized = str(latest_episode).strip() if latest_episode is not None else ''
                                    
                                    # 比较集数是否有变化
                                    if latest_episode_normalized != local_episode_normalized:
                                        logging.info(f"发现剧集 {local_title} 集数更新: 本地 {local_episode_normalized} -> 豆瓣 {latest_episode_normalized}")
                                        
                                        # 更新数据库中的集数
                                        cursor.execute('UPDATE RSS_TVS SET episode = ? WHERE douban_id = ?', 
                                                    (latest_episode_normalized, douban_id))
                                        episode_updated = True
                            
                            # 提交数据库更改
                            if title_updated or episode_updated:
                                self.db_connection.commit()
                                if title_updated and episode_updated:
                                    logging.info(f"已更新{media_type} '{local_title_normalized}' 的标题和集数信息")
                                elif title_updated:
                                    logging.info(f"已更新{media_type} '{local_title_normalized}' 的标题信息")
                                elif episode_updated:
                                    logging.info(f"已更新{media_type} '{local_title_normalized}' 的集数信息")
                            else:
                                logging.info(f"{media_type} '{local_title_normalized}' 的信息无变化")
                            
                            return  # 找到匹配项后退出循环
                    else:
                        logging.warning(f"未找到豆瓣ID为 {douban_id} 的{media_type}信息")
                else:
                    logging.warning(f"未找到{media_type} '{local_title}' 的豆瓣信息")
            else:
                logging.error(f"获取{media_type} '{local_title}' 信息失败，状态码: {response.status_code}")
        except requests.RequestException as e:
            logging.error(f"请求豆瓣API时发生错误: {e}")
        except sqlite3.Error as e:
            logging.error(f"更新数据库时发生错误: {e}")
        except Exception as e:
            logging.error(f"处理{media_type} '{local_title}' 时发生未知错误: {e}")

    def run(self):
        rss_data_list = self.fetch_rss_data()  # 获取所有用户的RSS数据
        if rss_data_list:
            items = self.parse_rss_data(rss_data_list)  # 解析所有数据
            if items:
                # 获取数据库中已存在的豆瓣ID
                existing_douban_ids = self.fetch_existing_douban_ids()
                new_douban_ids = {douban_id for _, douban_id, _ in items}
                # 删除数据库中不在新RSS数据中的过时数据
                self.delete_old_data(existing_douban_ids, new_douban_ids)

                logging.info("开始处理豆瓣兴趣中的所有项目")
                for title, douban_id, status in items:
                    # 检查数据库中是否已存在相同的豆瓣ID
                    if douban_id in existing_douban_ids:
                        # 更新现有条目的状态
                        cursor = self.db_connection.cursor()
                        # 检查是电影还是电视剧
                        cursor.execute('SELECT COUNT(*) FROM RSS_MOVIES WHERE douban_id = ?', (douban_id,))
                        is_movie = cursor.fetchone()[0] > 0
                        
                        if is_movie:
                            cursor.execute('UPDATE RSS_MOVIES SET status = ? WHERE douban_id = ?', (status, douban_id))
                        else:
                            cursor.execute('UPDATE RSS_TVS SET status = ? WHERE douban_id = ?', (status, douban_id))
                        
                        self.db_connection.commit()
                        logging.info(f"更新了豆瓣ID {douban_id} 的状态为: {status}")
                        continue

                    movie_details = self.fetch_movie_details(title, douban_id, status)  # 使用标题、豆瓣ID和状态获取详细信息
                    if movie_details:
                        logging.info("-" * 80)
                        logging.info(f"处理项目: {movie_details['title']}")
                        logging.info(f"豆瓣ID: {movie_details['douban_id']}")
                        logging.info(f"季数: {movie_details['season']}")
                        logging.info(f"集数: {movie_details['episode']}")
                        logging.info(f"年份: {movie_details['year']}")
                        logging.info(f"类型: {movie_details['media_type']}")
                        logging.info(f"状态: {movie_details['status']}")
                        logging.info(f"图片URL: {movie_details['img']}")
                        logging.info(f"URL: {movie_details['url']}")
                        logging.info(f"副标题: {movie_details['sub_title']}")
                        # 插入数据库
                        self.insert_into_db(movie_details)
                    
                    # 随机休眠10到15秒，避免频繁请求
                    sleep_time = random.uniform(10, 15)
                    time.sleep(sleep_time)
            else:
                logging.warning("豆瓣兴趣中没有找到项目")
        else:
            logging.error("未能获取豆瓣兴趣数据")

    def close_db(self):
        self.db_connection.close()
        logging.info("关闭数据库连接")

# 主程序入口
if __name__ == "__main__":
    parser = DouBanRSSParser()
    parser.run()
    parser.check_and_update_media_info()
    parser.close_db()