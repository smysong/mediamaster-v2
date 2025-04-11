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
import threading
import sqlite3

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/manual_search.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

class MediaDownloader:
    def __init__(self, db_path='/config/data.db'):
        self.db_path = db_path
        self.driver = None
        self.config = {}
        self.activity_time = time.time()  # 记录最后一次活动的时间
        self.timeout = 60  # 超时时间设置为1分钟
        self.timeout_thread = threading.Thread(target=self.check_timeout)
        self.timeout_thread.daemon = True  # 设置为守护线程
        self.timeout_thread.start()
        self.load_config()  # 调用方法加载配置
        # 读取配置文件中的登录信息
        self.bt_tv_base_url = self.config["bt_tv_base_url"]
        self.bt_movie_base_url = self.config["bt_movie_base_url"]
        self.tv_login_url = f"{self.bt_tv_base_url}/member.php?mod=logging&action=login"
        self.tv_search_url = f"{self.bt_tv_base_url}/search.php?mod=forum"
        self.movie_login_url = f"{self.bt_movie_base_url}/member.php?mod=logging&action=login"
        self.movie_search_url = f"{self.bt_movie_base_url}/search.php?mod=forum"

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

    def is_logged_in(self):
        try:
            # 检查页面中是否存在特定的提示文本
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '欢迎您回来')]"))
            )
            return True
        except TimeoutException:
            return False

    def login_movie_site(self, username, password):
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

    def login_tv_site(self, username, password):
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

    def search_movie(self, keyword, year=None):
        self.update_activity_time()
        self.setup_webdriver()
        self.login_movie_site(self.config["bt_login_username"], self.config["bt_login_password"])
        self.driver.get(self.movie_search_url)

        # 创建一个包含所有可能分辨率的列表
        resolutions = [self.config["preferred_resolution"], self.config["fallback_resolution"]]
        # 过滤掉空字符串
        resolutions = [res for res in resolutions if res]

        try:
            # 增加等待时间
            search_box = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.ID, 'scform_srchtxt'))
            )
            logging.info("搜索框加载完成")

            # 构建搜索查询
            search_query = f"{keyword} {year}" if year else keyword
            search_box.send_keys(search_query)
            search_box.send_keys(Keys.RETURN)
            logging.info("搜索请求发送完成")
            logging.info("等待电影结果")
            time.sleep(5)  # 假设结果5秒内加载完成

            results = []
            list_items = self.driver.find_elements(By.TAG_NAME, 'li')
            for li in list_items:
                try:
                    a_element = li.find_element(By.TAG_NAME, 'a')
                    title_text = a_element.text
                    link = a_element.get_attribute('href')

                    # 匹配分辨率
                    if any(res in title_text for res in resolutions):
                        logging.info(f"发现: {title_text}, Link: {link}")
                        results.append({
                            "title": title_text,
                            "link": link
                        })
                except NoSuchElementException:
                    logging.warning("未找到搜索结果元素")
                    continue
            self.close_driver()
            return results
        except TimeoutException:
            logging.error("搜索框加载超时，未找到预期元素！")
            self.close_driver()
            return []
        except Exception as e:
            logging.error(f"搜索过程中发生未知错误: {e}")
            self.close_driver()
            return []

    def download_movie(self, link, title, year):
        self.update_activity_time()
        self.setup_webdriver()
        self.login_movie_site(self.config["bt_login_username"], self.config["bt_login_password"])
        self.driver.get(link)
        try:
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "cl")))
            logging.info("进入详情页面")
            logging.info("找到匹配电影结果，开始查找种子文件")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                # 等待所有链接元素加载完成
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                all_links = self.driver.find_elements(By.TAG_NAME, "a")  # 每次循环重新获取元素
                for link_element in all_links:
                    link_text = link_element.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link_element.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到附件链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)  # 等待2秒后重新尝试
                    retries += 1

            if attachment_url:
                self.download_torrent(attachment_url, title, year)
                return True
            else:
                logging.error("经过多次重试后仍未找到附件链接。")
                return False

        except Exception as e:
            logging.error(f"下载过程中发生未知错误: {e}")
            self.close_driver()
            return False

    def search_tv_show(self, keyword, year=None):
        self.update_activity_time()
        self.setup_webdriver()
        self.login_tv_site(self.config["bt_login_username"], self.config["bt_login_password"])
        self.driver.get(self.tv_search_url)

        # 创建一个包含所有可能分辨率的列表
        resolutions = [self.config["preferred_resolution"], self.config["fallback_resolution"]]
        # 过滤掉空字符串
        resolutions = [res for res in resolutions if res]

        try:
            # 增加等待时间
            search_box = WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.ID, 'scform_srchtxt'))
            )
            logging.info("搜索框加载完成")

            # 构建搜索查询
            search_query = f"{keyword} {year}" if year else keyword
            search_box.send_keys(search_query)
            search_box.send_keys(Keys.RETURN)
            logging.info("搜索请求发送完成")
            logging.info("等待电视剧结果")
            time.sleep(5)  # 假设结果5秒内加载完成

            results = []
            list_items = self.driver.find_elements(By.TAG_NAME, 'li')
            for li in list_items:
                try:
                    a_element = li.find_element(By.TAG_NAME, 'a')
                    title_text = a_element.text
                    link = a_element.get_attribute('href')

                    # 匹配分辨率
                    if any(res in title_text for res in resolutions):
                        logging.info(f"发现: {title_text}, Link: {link}")
                        results.append({
                            "title": title_text,
                            "link": link
                        })
                except NoSuchElementException:
                    logging.warning("未找到搜索结果元素")
                    continue
            self.close_driver()
            return results
        except TimeoutException:
            logging.error("搜索框加载超时，未找到预期元素！")
            self.close_driver()
            return []
        except Exception as e:
            logging.error(f"搜索过程中发生未知错误: {e}")
            self.close_driver()
            return []

    def download_tv_show(self, link, title, year):
        self.update_activity_time()
        self.setup_webdriver()
        self.login_tv_site(self.config["bt_login_username"], self.config["bt_login_password"])
        self.driver.get(link)
        try:
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
            logging.info("进入详情页面")
            logging.info("找到匹配电视剧结果，开始查找种子文件")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                # 等待所有链接元素加载完成
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                all_links = self.driver.find_elements(By.TAG_NAME, "a")  # 每次循环重新获取元素
                for link_element in all_links:
                    link_text = link_element.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link_element.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到附件链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)  # 等待2秒后重新尝试
                    retries += 1

            if attachment_url:
                self.download_torrent(attachment_url, title, year)
                return True
            else:
                logging.error("经过多次重试后仍未找到附件链接。")
                return False

        except Exception as e:
            logging.error(f"下载过程中发生未知错误: {e}")
            self.close_driver()
            return False

    def download_torrent(self, torrent_url, title, year):
        self.driver.get(torrent_url)
        logging.info("开始下载种子文件")
        time.sleep(10)  # 设置等待时间为10秒，等待文件下载完成
        logging.info(f"下载完成: {title}_{year}")
        self.close_driver()  # 下载完成后关闭 WebDriver

    def close_driver(self):
        if self.driver:
            self.driver.quit()
            logging.info("WebDriver关闭完成")
            self.driver = None  # 重置 driver 变量

    def update_activity_time(self):
        self.activity_time = time.time()
        logging.debug("活动时间更新")

    def check_timeout(self):
        while True:
            if time.time() - self.activity_time > self.timeout:
                logging.error("程序超时，超过1分钟没有活动，关闭WebDriver")
                self.close_driver()
                break  # 退出超时检查线程
            time.sleep(10)  # 每10秒检查一次

if __name__ == "__main__":
    downloader = MediaDownloader()