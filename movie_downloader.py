import sqlite3
import json
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from selenium.webdriver.common.action_chains import ActionChains
import logging
import os
import time
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/movie_downloader.log"),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

DOWNLOAD_RECORD_FILE = '/tmp/record/movie_download_records.json'

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
            
            logging.debug("加载配置文件成功")
            return self.config
        except sqlite3.Error as e:
            logging.error(f"数据库加载配置错误: {e}")
            exit(1)

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
            exit(1)

    def is_logged_in(self):
        try:
            # 检查页面中是否存在特定的提示文本
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '欢迎您回来')]"))
            )
            return True
        except TimeoutException:
            return False

    def load_download_records(self):
        """加载已下载记录"""
        if os.path.exists(DOWNLOAD_RECORD_FILE):
            try:
                with open(DOWNLOAD_RECORD_FILE, 'r', encoding='utf-8') as file:
                    records = json.load(file)
                    logging.debug("加载下载记录成功")
                    return records
            except Exception as e:
                logging.error(f"加载下载记录时发生错误: {e}")
                return []
        logging.info("下载记录文件不存在，创建新文件")
        return []

    def save_download_records(self, records):
        """保存已下载记录"""
        try:
            with open(DOWNLOAD_RECORD_FILE, 'w', encoding='utf-8') as file:
                json.dump(records, file, ensure_ascii=False, indent=4)
                logging.debug("保存下载记录成功")
        except Exception as e:
            logging.error(f"保存下载记录时发生错误: {e}")

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

    def search_and_download(self, search_url, items):
        download_records = self.load_download_records()

        for item in items:
            # 构建下载记录的键
            record_key = f"{item['标题']}_{item['年份']}"

            if record_key in download_records:
                logging.info(f"记录已存在，跳过下载: {record_key}")
                continue

            logging.info(f"开始搜索: 标题 {item['标题']}, 年份 {item['年份']}")
            self.driver.get(search_url)
            search_box = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, 'scform_srchtxt'))
            )
            search_box.send_keys(f"{item['标题']}")
            search_box.send_keys(Keys.RETURN)
            logging.info("搜索请求发送完成")
            logging.info("等待电影结果")
            time.sleep(5)  # 假设结果5秒内加载完成

            # 查找所有可能的分辨率链接
            list_items = self.driver.find_elements(By.TAG_NAME, 'li')
            found_links = False
            exclude_keywords = self.config.get("resources", {}).get("exclude_keywords", "")
            exclude_keywords = [keyword.strip() for keyword in exclude_keywords.split(',')]

            for li in list_items:
                try:
                    a_element = li.find_element(By.TAG_NAME, 'a')
                    title_text = a_element.text.lower()
                    link = a_element.get_attribute('href')

                    # 检查是否需要排除此条目
                    if any(keyword in title_text for keyword in exclude_keywords):
                        logging.debug(f"排除资源: {title_text}")
                        continue

                    # 从配置文件中获取首选分辨率和备用分辨率
                    preferred_resolution = self.config.get("preferred_resolution", "")
                    fallback_resolution = self.config.get("fallback_resolution", "")
                    # 创建一个包含所有可能分辨率的列表
                    resolutions = [preferred_resolution, fallback_resolution]
                    # 过滤掉空字符串
                    resolutions = [res for res in resolutions if res]
                    # 检查标题文本中是否包含任何分辨率，并且年份是否匹配
                    if any(res in title_text for res in resolutions) and str(item['年份']) in title_text:
                        logging.info(f"发现: {title_text}, Link: {link}")
                        self.driver.get(link)
                        WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "cl")))
                        logging.debug("进入详情页面")
                        logging.info("找到匹配电影结果，开始查找种子文件")
                        try:
                            attachment_link = self.driver.find_element(By.PARTIAL_LINK_TEXT, "Torrent")
                            attachment_url = attachment_link.get_attribute('href')
                            self.download_torrent(attachment_url, item, title_text)
                            found_links = True
                            break  # 成功下载后跳出循环
                        except NoSuchElementException:
                            logging.warning("没有找到附件链接。")
                except NoSuchElementException:
                    logging.warning("未找到搜索结果元素")
                    continue

            if not found_links:
                logging.warning(f"没有找到首选和备用分辨率匹配的下载链接。")

    def download_torrent(self, torrent_url, item, title_text):
        self.driver.get(torrent_url)
        logging.info("开始下载种子文件")
        time.sleep(10)  # 设置等待时间为10秒，等待文件下载完成
        self.send_notification(item, title_text)
        # 更新下载记录
        download_records = self.load_download_records()
        download_records.append(f"{item['标题']}_{item['年份']}")  # 只记录电影名称和年份
        self.save_download_records(download_records)
        logging.debug(f"下载记录更新完成: {item['标题']}_{item['年份']}")

    def send_notification(self, item, title_text):
        api_key = self.config.get("notification_api_key", "")
        if not api_key:
            logging.error("通知API Key未在配置文件中找到，无法发送通知。")
            return
        api_url = f"https://api.day.app/{api_key}"
        data = {
            "title": "下载通知",
            "body": title_text  # 使用 title_text 作为 body 内容
        }
        headers = {'Content-Type': 'application/json'}
        response = requests.post(api_url, data=json.dumps(data), headers=headers)
        if response.status_code == 200:
            logging.info("通知发送成功: %s", response.text)
        else:
            logging.error("通知发送失败: %s %s", response.status_code, response.text)

    def run(self):
        # 加载配置文件
        self.load_config()
        
        # 检查配置中的必要信息是否存在
        if not self.config.get("bt_login_username") or \
        not self.config.get("bt_login_password") or \
        not self.config.get("notification_api_key"):
            logging.error("请在系统设置中修改必要配置并填写正确的用户名、密码及API key等参数。")
            exit(1)  # 提示后立即退出程序

        # 提取电影信息
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
        self.search_and_download(search_url, all_movie_info)

        # 清理工作，关闭浏览器
        self.driver.quit()
        logging.info("WebDriver关闭完成")

if __name__ == "__main__":
    downloader = MovieDownloader()
    downloader.run()