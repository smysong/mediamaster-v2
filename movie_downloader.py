import sqlite3
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.action_chains import ActionChains
import logging
import os
import re
import time
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/movie_downloader.log", mode='w'),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

class MovieDownloader:
    def __init__(self, db_path='/config/data.db'):
        self.db_path = db_path
        self.driver = None
        self.config = {}

    def setup_webdriver(self):
        if hasattr(self, 'driver') and self.driver is not None:
            logging.info("WebDriver已经初始化，无需重复初始化")
            return
        options = Options()
        options.add_argument('--headless')  # 无头模式运行
        options.add_argument('--no-sandbox')  # 在非root用户下需要禁用沙盒
        options.add_argument('--disable-dev-shm-usage')  # 解决/dev/shm空间不足的问题
        options.add_argument('--window-size=1920x1080')  # 设置窗口大小
        options.add_argument('--disable-gpu')  # 禁用GPU加速
        options.add_argument('--disable-extensions')  # 禁用扩展插件
        # 设置用户配置文件缓存目录
        user_data_dir = '/app/ChromeCache/user-data-dir'
        options.add_argument(f'--user-data-dir={user_data_dir}')
        # 设置磁盘缓存目录
        disk_cache_dir = "/app/ChromeCache/disk-cache-dir"
        options.add_argument(f"--disk-cache-dir={disk_cache_dir}")
        
        # 设置默认下载目录
        prefs = {
            "download.default_directory": "/Torrent",
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "intl.accept_languages": "zh-CN",
            "profile.managed_default_content_settings.images": 2
        }
        options.add_experimental_option("prefs", prefs)

        # 指定 chromedriver 的路径
        service = Service(executable_path='/usr/local/bin/chromedriver')
        
        try:
            self.driver = webdriver.Chrome(service=service, options=options)
            logging.info("WebDriver初始化完成")
        except Exception as e:
            logging.error(f"WebDriver初始化失败: {e}")
            raise

    def load_config(self):
        """从数据库中加载配置"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT OPTION, VALUE FROM CONFIG')
                config_items = cursor.fetchall()
                self.config = {option: value for option, value in config_items}
            
            logging.info("加载配置文件成功")
            return self.config
        except sqlite3.Error as e:
            logging.error(f"数据库加载配置错误: {e}")
            exit(0)

    def send_notification(self, item, title_text, resolution):
        # 通知功能
        try:
            notification_enabled = self.config.get("notification", "")
            if notification_enabled.lower() != "true":  # 显式检查是否为 "true"
                logging.info("通知功能未启用，跳过发送通知。")
                return
            api_key = self.config.get("notification_api_key", "")
            if not api_key:
                logging.error("通知API Key未在配置文件中找到，无法发送通知。")
                return
            api_url = f"https://api.day.app/{api_key}"
            data = {
                "title": "下载通知",
                "body": f"{item['剧集']} - {resolution} - {title_text}"  # 使用 title_text 作为 body 内容
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

    def site_captcha(self, url):
        self.driver.get(url)
        try:
            # 检查滑动验证码元素是否存在
            captcha_prompt = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "p.ui-prompt"))
            )
            if captcha_prompt.text in ["滑动上面方块到右侧解锁", "Slide to Unlock"]:
                logging.info("检测到滑动验证码，开始验证")

                # 等待滑块元素出现
                handler = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "handler"))
                )

                # 等待目标位置元素出现
                target = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "handler-placeholder"))
                )

                # 获取滑块的初始位置
                handler_location = handler.location

                # 获取目标位置的初始位置
                target_location = target.location

                # 计算滑块需要移动的距离
                move_distance = target_location['x'] - handler_location['x']

                # 使用 ActionChains 模拟拖动滑块
                actions = ActionChains(self.driver)
                actions.click_and_hold(handler).move_by_offset(move_distance, 0).release().perform()

                logging.info("滑块已成功拖动到目标位置")

                # 等待页面跳转完成
                WebDriverWait(self.driver, 30).until(
                    EC.url_changes(url)
                )

                logging.info("页面已成功跳转")
            else:
                logging.info("未检测到滑动验证码")
        except TimeoutException:
            logging.info("未检测到滑动验证码")
        except Exception as e:
            logging.error(f"访问站点时出错: {e}")

    def login(self, url, username, password):
        self.driver.get(url)
        try:
            # 检查是否已经自动登录
            if self.is_logged_in():
                logging.info("自动登录成功，无需再次登录")
                return
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            logging.info("登录页面加载完成")
            username_input = self.driver.find_element(By.NAME, 'username')
            password_input = self.driver.find_element(By.NAME, 'password')
            username_input.send_keys(username)
            password_input.send_keys(password)
            
            # 勾选自动登录选项
            auto_login_checkbox = self.driver.find_element(By.NAME, 'cookietime')
            auto_login_checkbox.click()
            
            submit_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.NAME, 'loginsubmit'))
            )
            submit_button.click()
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, '跳转'))
            )
            logging.info("登录成功！")
        except TimeoutException:
            logging.error("登录失败或页面未正确加载，未找到预期元素！")
            self.driver.quit()
            exit(0)

    def is_logged_in(self):
        try:
            # 检查页面中是否存在特定的提示文本
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '欢迎您回来')]"))
            )
            return True
        except TimeoutException:
            return False

    def extract_movie_info(self):
        """从数据库读取订阅电影信息"""
        all_movie_info = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT title, year FROM MISS_MOVIES')
                movies = cursor.fetchall()
                for title, year in movies:
                    all_movie_info.append({
                        "标题": title,
                        "年份": year
                    })
            logging.debug("读取订阅电影信息完成")
            return all_movie_info
        except Exception as e:
            logging.error(f"提取电影信息时发生错误: {e}")
            return []

    def search(self, search_url, all_movie_info):
        # 搜索电影
        for item in all_movie_info:
            logging.info(f"开始搜索电影: {item['标题']}  年份: {item['年份']}")
            search_query = f"{item['标题']} {item['年份']}"
            self.driver.get(search_url)
            try:
                search_box = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "srchtxt"))
                )
                search_box.send_keys(search_query)
                search_box.send_keys(Keys.RETURN)
                logging.debug(f"搜索关键词: {search_query}")

                # 等待搜索结果加载
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "threadlist"))
                )
                # 查找搜索结果中的链接
                results = self.driver.find_elements(By.CSS_SELECTOR, "#threadlist li.pbw h3.xs3 a")
                search_results = []
                for result in results:
                    title_text = result.text
                    link = result.get_attribute('href')
                    search_results.append({
                        "title": title_text,
                        "link": link
                    })

                # 过滤搜索结果
                filtered_results = []
                for result in search_results:
                    # 检查年份是否匹配
                    if str(item['年份']) not in result['title']:  # 将 item['年份'] 转换为字符串
                        continue
                    
                    # 检查是否包含排除关键词
                    exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')
                    if any(keyword.strip() in result['title'] for keyword in exclude_keywords):
                        continue
                    
                    filtered_results.append(result)

                # 获取首选分辨率和备选分辨率
                preferred_resolution = self.config.get('preferred_resolution', "未知分辨率")
                fallback_resolution = self.config.get('fallback_resolution', "未知分辨率")

                # 按分辨率分类搜索结果
                categorized_results = {
                    "首选分辨率": [],
                    "备选分辨率": [],
                    "其他分辨率": []
                }
                for result in filtered_results:
                    details = self.extract_details(result['title'])
                    resolution = details['resolution']
                    
                    if resolution == preferred_resolution:
                        categorized_results["首选分辨率"].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "audio_tracks": details['audio_tracks'],
                            "subtitles": details['subtitles']
                        })
                    elif resolution == fallback_resolution:
                        categorized_results["备选分辨率"].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "audio_tracks": details['audio_tracks'],
                            "subtitles": details['subtitles']
                        })
                    else:
                        categorized_results["其他分辨率"].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "audio_tracks": details['audio_tracks'],
                            "subtitles": details['subtitles']
                        })

                # 定义一个函数来评估结果的优先级
                def evaluate_result(result):
                    # 优先级评分
                    score = 0
                    if result['audio_tracks']:
                        score += 1  # 有音轨
                    if result['subtitles']:
                        score += 1  # 有字幕
                    return score

                # 对每个分辨率类别的结果进行排序
                categorized_results["首选分辨率"].sort(key=evaluate_result, reverse=True)
                categorized_results["备选分辨率"].sort(key=evaluate_result, reverse=True)
                categorized_results["其他分辨率"].sort(key=evaluate_result, reverse=True)

                # 匹配结果
                matched_results = categorized_results["首选分辨率"] + categorized_results["备选分辨率"] + categorized_results["其他分辨率"]
                
                # 下载种子文件
                if matched_results:
                    for result in matched_results:
                        title_text = result['title']
                        resolution = result['resolution']
                        self.download_torrent(result, item, title_text, resolution)
                        break  # 只下载第一个匹配的结果
                else:
                    logging.info(f"没有找到匹配的搜索结果: {item['标题']}  年份: {item['年份']}")

            except TimeoutException:
                logging.error("搜索结果加载超时")
            except Exception as e:
                logging.error(f"搜索过程中出错: {e}")

    def extract_details(self, title):
        """从标题中提取详细信息，如分辨率、音轨、字幕"""
        details = {
            "resolution": "未知分辨率",
            "audio_tracks": [],
            "subtitles": []
        }
        
        # 使用正则表达式提取分辨率
        resolution_match = re.search(r'(\d{3,4}p)', title, re.IGNORECASE)
        if resolution_match:
            details["resolution"] = resolution_match.group(1).upper()
        
        # 使用正则表达式提取音轨信息
        audio_track_match = re.search(r'\[(.*?)音轨', title)
        if audio_track_match:
            audio_tracks_str = audio_track_match.group(1).strip()
            details["audio_tracks"] = [track.strip() for track in audio_tracks_str.split('/')]
        
        # 使用正则表达式提取字幕信息
        subtitle_match = re.search(r'\[(.*?)字幕', title)
        if subtitle_match:
            subtitles_str = subtitle_match.group(1).strip()
            details["subtitles"] = [subtitle.strip() for subtitle in subtitles_str.split('/')]
        
        return details

    def download_torrent(self, result, item, title_text, resolution):
        """解析并下载种子文件"""
        try:
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            # 等待种子文件下载链接加载
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "cl")))
            # 查找所有 <a> 标签
            links = self.driver.find_elements(By.TAG_NAME, "a")
            download_link = None

            # 遍历所有 <a> 标签，查找包含 "torrent" 的链接（不区分大小写）
            for link in links:
                link_text = link.text.strip().lower()  # 转为小写并去除多余空格
                if "torrent" in link_text:
                    download_link = link
                    break
            if download_link is None:
                logging.error("未找到种子文件下载链接")
                return
            download_url = download_link.get_attribute('href')
            # 请求下载链接
            self.driver.get(download_url)
            logging.info("开始下载种子文件...")
            # 等待下载完成
            time.sleep(10)  # 等待10秒，确保文件下载完成
            # 发送通知
            self.send_notification(item, title_text, resolution)

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")

    def run(self):
        # 加载配置文件
        self.load_config()
        
        # 提取电视节目信息
        all_movie_info = self.extract_movie_info()

        # 检查数据库中是否有有效订阅
        if not all_movie_info:
            logging.info("数据库中没有有效订阅，无需执行后续操作")
            exit(0)  # 退出程序

        # 初始化WebDriver
        self.setup_webdriver()

        # 获取基础 URL
        bt_movie_base_url = self.config.get("bt_movie_base_url", "")
        login_url = f"{bt_movie_base_url}/member.php?mod=logging&action=login"
        search_url = f"{bt_movie_base_url}/search.php?mod=forum"
    
        # 登录操作前先检查滑动验证码
        self.site_captcha(login_url)  # 检查并处理滑动验证码
        self.login(login_url, self.config["bt_login_username"], self.config["bt_login_password"])

        # 搜索和下载操作
        self.search(search_url, all_movie_info)

        # 清理工作，关闭浏览器
        self.driver.quit()
        logging.info("WebDriver关闭完成")

if __name__ == "__main__":
    downloader = MovieDownloader()
    downloader.run()