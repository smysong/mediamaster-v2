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
import time
import sqlite3
import requests
import argparse 

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/downloader.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

class MediaDownloader:
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
        options.add_argument('--lang=zh-CN')  # 设置浏览器语言为简体中文
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
                "body": f"开始下载：{title_text}"  # 使用 title_text 作为 body 内容
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

    def gy_site_captcha(self, url):
        self.driver.get(url)
        try:
            # 检查新的验证码元素是否存在
            captcha_prompt = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.container .title"))
            )
            if "请确认您不是机器人" in captcha_prompt.text:
                logging.info("检测到验证码，开始验证")
    
                # 等待复选框元素出现
                checkbox = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, "checkbox"))
                )
    
                # 使用 ActionChains 模拟点击复选框
                actions = ActionChains(self.driver)
                actions.move_to_element(checkbox).click().perform()
    
                logging.info("复选框已成功点击")
    
                # 等待加载指示器消失，表示验证完成
                WebDriverWait(self.driver, 30).until_not(
                    EC.presence_of_element_located((By.ID, "loading-indicator"))
                )
    
                logging.info("验证码验证成功")
    
                # 等待页面跳转完成
                WebDriverWait(self.driver, 30).until(
                    EC.url_changes(url)
                )
    
                logging.info("页面已成功跳转")
            else:
                logging.info("未检测到验证码")
        except TimeoutException:
            logging.info("未检测到验证码")
        except Exception as e:
            logging.error(f"访问站点时出错: {e}")
    
        # 无论是否检测到验证码，都检查是否有提示框并点击“不再提醒”按钮
        try:
            popup_close_button = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.popup-footer button"))
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(popup_close_button).click().perform()
            logging.info("成功点击“不再提醒”按钮")
        except TimeoutException:
            logging.info("未检测到提示框，无需操作")

    def is_logged_in(self):
        try:
            # 检查页面中是否存在特定的提示文本
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '欢迎您回来')]"))
            )
            return True
        except TimeoutException:
            return False

    def is_logged_in_gy(self):
        try:
            # 检查页面中是否存在特定的提示文本
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '登录成功')]"))
            )
            return True
        except TimeoutException:
            return False

    def login_bthd_site(self, username, password):
        login_url = self.movie_login_url
        self.site_captcha(login_url)  # 调用 site_captcha 方法
        self.driver.get(login_url)
        try:
            # 检查是否已经自动登录
            if self.is_logged_in():
                logging.info("自动登录成功，无需再次登录")
                return
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            logging.info("电影站点登录页面加载完成")
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
            logging.info("电影站点登录成功！")
        except TimeoutException:
            logging.error("电影站点登录失败或页面未正确加载，未找到预期元素！")
            self.close_driver()
            raise

    def login_hdtv_site(self, username, password):
        login_url = self.tv_login_url
        self.site_captcha(login_url)  # 调用 site_captcha 方法
        self.driver.get(login_url)
        try:
            # 检查是否已经自动登录
            if self.is_logged_in():
                logging.info("自动登录成功，无需再次登录")
                return
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            logging.info("电视剧站点登录页面加载完成")
            username_input = self.driver.find_element(By.NAME, 'username')
            password_input = self.driver.find_element(By.NAME, 'password')
            username_input.send_keys(username)
            password_input.send_keys(password)
            # 勾选自动登录选项
            auto_login_checkbox = self.driver.find_element(By.CLASS_NAME, 'checkbox-style')
            auto_login_checkbox.click()
            submit_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.NAME, 'loginsubmit'))
            )
            submit_button.click()
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.PARTIAL_LINK_TEXT, '跳转'))
            )
            logging.info("电视剧站点登录成功！")
        except TimeoutException:
            logging.error("电视剧站点登录失败或页面未正确加载，未找到预期元素！")
            self.close_driver()
            raise

    def login_gy_site(self, username, password):
        login_url = self.gy_login_url
        user_info_url = self.gy_user_info_url
        self.gy_site_captcha(login_url)  # 调用 gy_site_captcha 方法
        self.driver.get(login_url)
        try:
            # 检查是否已经自动登录
            if self.is_logged_in_gy():
                logging.info("自动登录成功，无需再次登录")
                return
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            logging.info("观影站点登录页面加载完成")
            username_input = self.driver.find_element(By.NAME, 'username')
            password_input = self.driver.find_element(By.NAME, 'password')
            username_input.send_keys(username)
            password_input.send_keys(password)
            # 勾选自动登录选项
            auto_login_checkbox = self.driver.find_element(By.NAME, 'cookietime')
            auto_login_checkbox.click()
            submit_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.NAME, 'button'))
            )
            submit_button.click()
            # 等待页面跳转完成后去访问用户信息页面
            time.sleep(5)  # 等待页面跳转
            self.driver.get(user_info_url)
            # 检查页面中是否存在<h2>账户设置</h2>元素
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//h2[contains(text(), '账户设置')]"))
            )
            logging.info("观影站点登录成功！")
        except TimeoutException:
            logging.error("观影站点登录失败或页面未正确加载，未找到预期元素！")
            self.close_driver()
            raise

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
        
    def extract_tv_info(self):
        """从数据库读取订阅电视节目信息和缺失的集数信息"""
        all_tv_info = []
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 读取缺失的电视节目信息和缺失的集数信息
            cursor.execute('SELECT title, year, season, missing_episodes FROM MISS_TVS')
            tvs = cursor.fetchall()
            
            for title, year, season, missing_episodes in tvs:
                # 确保 year 和 season 是字符串类型
                if isinstance(year, int):
                    year = str(year)  # 将整数转换为字符串
                if isinstance(season, int):
                    season = str(season)  # 将整数转换为字符串
                
                # 将缺失的集数字符串转换为列表
                missing_episodes_list = [episode.strip() for episode in missing_episodes.split(',')] if missing_episodes else []
                
                all_tv_info.append({
                    "剧集": title,
                    "年份": year,
                    "季": season,
                    "缺失集数": missing_episodes_list
                })
        
        logging.debug("读取缺失的电视节目信息和缺失的集数信息完成")
        return all_tv_info

    def bthd_download_torrent(self, result, title_text):
        """高清影视之家解析并下载种子文件"""
        try:
            # 检查登录状态
            self.login_bthd_site(self.config["bt_login_username"], self.config["bt_login_password"])
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")
    
            # 等待种子文件下载链接加载
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "cl")))
            logging.info("页面加载完成")
    
            attachment_url = None
            max_retries = 5
            retries = 0
    
            while not attachment_url and retries < max_retries:
                # 等待所有链接元素加载完成
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                links = self.driver.find_elements(By.TAG_NAME, "a")  # 每次循环重新获取元素
                for link in links:
                    link_text = link.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link.get_attribute('href')
                        break
    
                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)  # 等待2秒后重新尝试
                    retries += 1
    
            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                # 请求下载链接
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                # 等待下载完成
                time.sleep(10)  # 等待10秒，确保文件下载完成
            else:
                logging.error("经过多次重试后仍未找到种子文件下载链接。")
    
        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")

    def hdtv_download_torrent(self, result, title_text):
        """高清剧集网解析并下载种子文件"""
        try:
            # 检查登录状态
            self.login_hdtv_site(self.config["bt_login_username"], self.config["bt_login_password"])
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")
    
            # 等待种子文件下载链接加载
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
            logging.info("页面加载完成")
    
            attachment_url = None
            max_retries = 5
            retries = 0
    
            while not attachment_url and retries < max_retries:
                # 等待所有链接元素加载完成
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                links = self.driver.find_elements(By.TAG_NAME, "a")  # 每次循环重新获取元素
                for link in links:
                    link_text = link.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link.get_attribute('href')
                        break
    
                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)  # 等待2秒后重新尝试
                    retries += 1
    
            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                # 请求下载链接
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                # 等待下载完成
                time.sleep(10)  # 等待10秒，确保文件下载完成
            else:
                logging.error("经过多次重试后仍未找到种子文件下载链接。")
    
        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")

    def bt0_download_torrent(self, result, title_text):
        """不太灵影视解析并下载种子文件"""
        try:
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")
    
            # 等待种子文件下载链接加载
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "tr-meta-actions")))
            logging.info("页面加载完成")
    
            attachment_url = None
            max_retries = 5
            retries = 0
    
            while not attachment_url and retries < max_retries:
                # 等待所有链接元素加载完成
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "download-link"))
                )
                links = self.driver.find_elements(By.CLASS_NAME, "download-link")  # 每次循环重新获取元素
                for link in links:
                    link_text = link.text.strip()
                    if "下载种子" in link_text:
                        attachment_url = link.get_attribute('href')
                        break
    
                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)  # 等待2秒后重新尝试
                    retries += 1
    
            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                # 请求下载链接
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                # 等待下载完成
                time.sleep(10)  # 等待10秒，确保文件下载完成
            else:
                logging.error("经过多次重试后仍未找到种子文件下载链接。")
    
        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")

    def gy_download_torrent(self, result, title_text):
        """观影解析并下载种子文件"""
        try:
            # 检查登录状态
            self.login_gy_site(self.config["gy_login_username"], self.config["gy_login_password"])
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")
    
            # 等待种子文件下载链接加载
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "alert-info")))
            logging.info("页面加载完成")
    
            attachment_urls = []  # 存储所有符合条件的链接
            max_retries = 5
            retries = 0
    
            while not attachment_urls and retries < max_retries:
                # 等待所有链接元素加载完成
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "alert-info"))
                )
                # 获取两种可能的链接元素
                links_case1 = self.driver.find_elements(By.CSS_SELECTOR, ".down123 li")
                links_case2 = self.driver.find_elements(By.CSS_SELECTOR, ".down321 .right span")
            
                # 处理第一种情况
                for link in links_case1:
                    link_text = link.text.strip()
                    if "种子下载" in link_text:
                        attachment_url = link.get_attribute('data-clipboard-text')
                        if attachment_url:
                            attachment_urls.append(attachment_url)
            
                # 处理第二种情况
                for link in links_case2:
                    link_text = link.text.strip()
                    if "种子下载" in link_text:
                        attachment_url = link.get_attribute('data-clipboard-text')
                        if attachment_url:
                            attachment_urls.append(attachment_url)
            
                if not attachment_urls:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)  # 等待2秒后重新尝试
                    retries += 1
    
            if attachment_urls:
                gy_base_url = self.config.get("gy_base_url", "")
                for attachment_url in attachment_urls:
                    # 替换 #host# 为 gy_base_url
                    attachment_url = attachment_url.replace("#host#", gy_base_url)
                    logging.info(f"找到种子文件下载链接: {attachment_url}")
    
                    # 请求下载链接
                    self.driver.get(attachment_url)
                    logging.info("开始下载种子文件...")
                    # 等待下载完成
                    time.sleep(10)  # 等待10秒，确保文件下载完成
            else:
                logging.error("经过多次重试后仍未找到种子文件下载链接。")
    
        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")

    def process_movie_downloads(self):
        """处理电影下载任务"""
        # 读取订阅的电影信息
        all_movie_info = self.extract_movie_info()
    
        # 定义来源优先级
        sources_priority = ["BTHD", "BT0", "GY"]
    
        # 遍历每部电影信息
        for movie in all_movie_info:
            title = movie["标题"]
            year = movie["年份"]
            download_result = None
    
            # 遍历来源优先级
            for source in sources_priority:
                index_file_name = f"{title}-{year}-{source}.json"
                index_file_path = os.path.join("/tmp/index", index_file_name)
    
                # 检查索引文件是否存在
                if not os.path.exists(index_file_path):
                    logging.warning(f"索引文件不存在: {index_file_path}，尝试下一个来源")
                    continue
    
                # 读取索引文件内容
                try:
                    with open(index_file_path, 'r', encoding='utf-8') as f:
                        index_data = json.load(f)
                except Exception as e:
                    logging.error(f"读取索引文件时出错: {index_file_path}, 错误: {e}")
                    continue
    
                # 按优先级选择下载结果
                if index_data.get("首选分辨率"):
                    download_result = index_data["首选分辨率"][0]
                elif index_data.get("备选分辨率"):
                    download_result = index_data["备选分辨率"][0]
                elif index_data.get("其他分辨率"):
                    download_result = index_data["其他分辨率"][0]
    
                # 如果找到匹配结果，停止尝试其他来源
                if download_result:
                    download_title = download_result.get("title")
                    logging.info(f"在来源 {source} 中找到匹配结果: {download_title}")
                    break
    
            # 如果没有可下载的结果
            if not download_result:
                logging.info(f"没有匹配的下载结果: {title} ({year})")
                continue
    
            # 获取下载链接和标题
            link = download_result.get("link")
            download_title = download_result.get("title")
            resolution = download_result.get("resolution")
    
            if not link:
                logging.warning(f"未找到种子下载链接: {title} ({year})")
                continue
    
            # 根据来源调用相应的下载方法
            logging.info(f"开始下载: {download_title} ({resolution}) 来源: {source}")
            if source == "BTHD":
                self.bthd_download_torrent(download_result, download_title)
            elif source == "BT0":
                self.bt0_download_torrent(download_result, download_title)
            elif source == "GY":
                self.gy_download_torrent(download_result, download_title)
    
            # 发送通知
            self.send_notification(movie, download_title, resolution)
    
    def process_tvshow_downloads(self):
        """处理电视节目下载任务"""
        # 读取订阅的电视节目信息
        all_tv_info = self.extract_tv_info()
    
        # 定义来源优先级
        sources_priority = ["HDTV", "BT0", "GY"]
    
        # 遍历每个电视节目信息
        for tvshow in all_tv_info:
            title = tvshow["剧集"]
            year = tvshow["年份"]
            season = tvshow["季"]
            missing_episodes = sorted(map(int, tvshow["缺失集数"]))  # 转换为整数集合并排序
            logging.debug(f"缺失集数: {missing_episodes}")
            download_results = []  # 存储所有匹配的下载结果
    
            # 遍历来源优先级
            for source in sources_priority:
                index_file_name = f"{title}-S{season}-{year}-{source}.json"
                index_file_path = os.path.join("/tmp/index", index_file_name)
    
                # 检查索引文件是否存在
                if not os.path.exists(index_file_path):
                    logging.warning(f"索引文件不存在: {index_file_path}，尝试下一个来源")
                    continue
    
                # 读取索引文件内容
                try:
                    with open(index_file_path, 'r', encoding='utf-8') as f:
                        index_data = json.load(f)
                except Exception as e:
                    logging.error(f"读取索引文件时出错: {index_file_path}, 错误: {e}")
                    continue
    
                # 定义分辨率优先级
                resolution_priorities = ["首选分辨率", "备选分辨率", "其他分辨率"]
    
                # 遍历分辨率优先级
                for resolution_priority in resolution_priorities:
                    # 对全集、集数范围和单集数据按 start_episode 排序
                    for key in ["全集", "集数范围", "单集"]:
                        if key in index_data.get(resolution_priority, {}):
                            index_data[resolution_priority][key] = sorted(
                                index_data[resolution_priority][key],
                                key=lambda x: int(x.get("start_episode", 0))
                            )
    
                    # 优先匹配全集
                    for item in index_data.get(resolution_priority, {}).get("全集", []):
                        if item.get("start_episode") is None or item.get("end_episode") is None:
                            logging.warning(f"无效数据，跳过处理: {item}")
                            continue
                        episode_range = set(range(int(item["start_episode"]), int(item["end_episode"]) + 1))
                        logging.debug(f"尝试匹配全集 ({resolution_priority}): {item['title']} 集数范围: {sorted(episode_range)}")
                        if episode_range.issubset(missing_episodes):
                            download_results.append((item, source))
                            missing_episodes = [ep for ep in missing_episodes if ep not in episode_range]
    
                    # 匹配集数范围
                    for item in index_data.get(resolution_priority, {}).get("集数范围", []):
                        if item.get("start_episode") is None or item.get("end_episode") is None:
                            logging.warning(f"无效数据，跳过处理: {item}")
                            continue
                        episode_range = set(range(int(item["start_episode"]), int(item["end_episode"]) + 1))
                        logging.debug(f"尝试匹配集数范围 ({resolution_priority}): {item['title']} 集数范围: {sorted(episode_range)}")
                        if episode_range.intersection(missing_episodes):
                            download_results.append((item, source))
                            missing_episodes = [ep for ep in missing_episodes if ep not in episode_range]
    
                    # 匹配单集
                    for item in index_data.get(resolution_priority, {}).get("单集", []):
                        if item.get("start_episode") is None:
                            logging.warning(f"无效数据，跳过处理: {item}")
                            continue
                        episode = int(item["start_episode"])
                        logging.debug(f"尝试匹配单集 ({resolution_priority}): {item['title']} 集数: {episode}")
                        if episode in missing_episodes:
                            download_results.append((item, source))
                            missing_episodes.remove(episode)
    
                    # 如果没有缺失集数，跳出分辨率优先级循环
                    if not missing_episodes:
                        break
    
                # 如果没有缺失集数，跳出来源优先级循环
                if not missing_episodes:
                    break
    
            # 下载所有匹配的结果
            for result, result_source in download_results:
                logging.info(f"在来源 {result_source} 中找到匹配结果: {result['title']}")
                if result_source == "HDTV":
                    self.hdtv_download_torrent(result, result["title"])
                elif result_source == "BT0":
                    self.bt0_download_torrent(result, result["title"])
                elif result_source == "GY":
                    self.gy_download_torrent(result, result["title"])
                self.send_notification(tvshow, result["title"], result["resolution"])
    
            # 如果仍有未匹配的缺失集数
            if missing_episodes:
                logging.warning(f"未找到匹配的下载结果: {title} S{season} ({year}) 缺失集数: {missing_episodes}")
    def close_driver(self):
        if self.driver:
            self.driver.quit()
            logging.info("WebDriver关闭完成")
            self.driver = None  # 重置 driver 变量

    def run(self):
        """运行程序的主逻辑"""
        try:
            # 加载配置文件
            self.load_config()
            # 初始化WebDriver
            self.setup_webdriver()
            # 获取基础 URL
            bt_movie_base_url = self.config.get("bt_movie_base_url", "")
            self.movie_login_url = f"{bt_movie_base_url}/member.php?mod=logging&action=login"
            bt_tv_base_url = self.config.get("bt_tv_base_url", "")
            self.tv_login_url = f"{bt_tv_base_url}/member.php?mod=logging&action=login"
            gy_base_url = self.config.get("gy_base_url", "")
            self.gy_login_url = f"{gy_base_url}/user/login"
            self.gy_user_info_url = f"{gy_base_url}/user/account"
    
            # 获取订阅电影信息
            all_movie_info = self.extract_movie_info()
    
            # 获取订阅电视节目信息
            all_tv_info = self.extract_tv_info()
    
            # 检查订阅信息并运行对应任务
            if not all_movie_info and not all_tv_info:
                logging.info("数据库中没有有效订阅，无需执行后续操作")
                return  # 退出程序
    
            if all_movie_info:
                logging.info("检测到有效的电影订阅信息，开始处理电影下载任务")
                self.process_movie_downloads()
            else:
                logging.info("没有检测到有效的电影订阅信息，跳过电影下载任务")
    
            if all_tv_info:
                logging.info("检测到有效的电视节目订阅信息，开始处理电视节目下载任务")
                self.process_tvshow_downloads()
            else:
                logging.info("没有检测到有效的电视节目订阅信息，跳过电视节目下载任务")
    
        except Exception as e:
            logging.error(f"程序运行时发生错误: {e}")
        finally:
            # 确保程序结束时关闭 WebDriver
            self.close_driver()

if __name__ == "__main__":
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description="Media Downloader")
    parser.add_argument("--site", type=str, help="下载站点名称，例如 BT0、BTHD、GY 等")
    parser.add_argument("--title", type=str, help="下载的标题")
    parser.add_argument("--link", type=str, help="下载链接")
    args = parser.parse_args()

    downloader = MediaDownloader()

    if args.site and args.title and args.link:
        # 如果提供了命令行参数，则手动运行下载功能
        try:
            downloader.load_config()
            downloader.setup_webdriver()
    
            # 初始化相关 URL 属性
            bt_movie_base_url = downloader.config.get("bt_movie_base_url", "")
            downloader.movie_login_url = f"{bt_movie_base_url}/member.php?mod=logging&action=login"
            bt_tv_base_url = downloader.config.get("bt_tv_base_url", "")
            downloader.tv_login_url = f"{bt_tv_base_url}/member.php?mod=logging&action=login"
            gy_base_url = downloader.config.get("gy_base_url", "")
            downloader.gy_login_url = f"{gy_base_url}/user/login"
            downloader.gy_user_info_url = f"{gy_base_url}/user/account"
    
            logging.info(f"手动运行下载功能，站点: {args.site}, 标题: {args.title}, 链接: {args.link}")
            if args.site.upper() == "BTHD":
                downloader.bthd_download_torrent({"link": args.link}, args.title)
            elif args.site.upper() == "BT0":
                downloader.bt0_download_torrent({"link": args.link}, args.title)
            elif args.site.upper() == "GY":
                downloader.gy_download_torrent({"link": args.link}, args.title)
            elif args.site.upper() == "HDTV":
                downloader.hdtv_download_torrent({"link": args.link}, args.title)
            else:
                logging.error(f"未知的站点名称: {args.site}")
        except Exception as e:
            logging.error(f"手动运行下载功能时发生错误: {e}")
        finally:
            downloader.close_driver()
    else:
        # 如果未提供命令行参数，则运行默认逻辑
        downloader.run()