import json
import subprocess
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
import glob
from captcha_handler import CaptchaHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/downloader.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def get_latest_torrent_file(download_dir="/Torrent"):
    """获取下载目录下最新的.torrent文件路径"""
    torrent_files = glob.glob(os.path.join(download_dir, "*.torrent"))
    if not torrent_files:
        return None
    return max(torrent_files, key=os.path.getctime)

def rename_torrent_file(old_path, new_name, download_dir="/Torrent"):
    """重命名.torrent文件，失败时使用原始文件添加下载任务"""
    new_path = os.path.join(download_dir, new_name)
    try:
        os.rename(old_path, new_path)
        logging.info(f"种子文件已重命名为: {new_path}")
        run_task_adder(new_path)  # 添加任务到下载器
    except Exception as e:
        logging.error(f"重命名种子文件失败: {e}")
        # 重命名失败时，直接用原始文件添加下载任务
        logging.info(f"使用原始种子文件添加下载任务: {old_path}")
        run_task_adder(old_path)

def run_task_adder(torrent_path):
    """使用 download_task_adder.py 添加任务，增加连接失败等异常处理，避免程序崩溃"""
    try:
        logging.info(f"向下载器添加下载任务：{torrent_path}")
        # 使用with打开devnull，避免未定义报错
        with open(os.devnull, 'w') as devnull:
            subprocess.run(
                ['python', 'download_task_adder.py', torrent_path],
                check=True,
                stdout=devnull,
                stderr=devnull
            )
        logging.info("下载任务添加完成")
    except subprocess.CalledProcessError as e:
        logging.error(f"添加下载任务失败: {e}")
    except FileNotFoundError as e:
        logging.error(f"调用 添加下载器任务程序 失败，文件未找到: {e}")
    except Exception as e:
        logging.error(f"添加下载任务时发生未知错误: {e}")

class MediaDownloader:
    def __init__(self, db_path='/config/data.db'):
        self.db_path = db_path
        self.driver = None
        self.config = {}

    def setup_webdriver(self, instance_id=10):
        if hasattr(self, 'driver') and self.driver is not None:
            logging.info("WebDriver已经初始化，无需重复初始化")
            return
        options = Options()
        options.add_argument('--headless=new')  # 使用新版无头模式
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920x1080')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-background-timer-throttling')  # 禁用后台定时器节流
        options.add_argument('--disable-renderer-backgrounding')       # 禁用渲染器后台运行
        options.add_argument('--disable-features=VizDisplayCompositor') # 禁用Viz显示合成器
        # 忽略SSL证书错误
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--allow-insecure-localhost')
        options.add_argument('--ignore-ssl-errors')
        # 设置浏览器语言为中文
        options.add_argument('--lang=zh-CN')
        # 设置用户配置文件缓存目录，使用固定instance-id 10作为该程序特有的id
        user_data_dir = f'/app/ChromeCache/user-data-dir-inst-{instance_id}'
        options.add_argument(f'--user-data-dir={user_data_dir}')
        # 设置磁盘缓存目录，同样使用instance-id区分
        disk_cache_dir = f"/app/ChromeCache/disk-cache-dir-inst-{instance_id}"
        options.add_argument(f"--disk-cache-dir={disk_cache_dir}")
        
        # 设置默认下载目录，使用instance-id区分
        download_dir = f"/Torrent"
        os.makedirs(download_dir, exist_ok=True)  # 确保下载目录存在
        prefs = {
            "download.default_directory": download_dir,
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "safebrowsing.enabled": True,
            "intl.accept_languages": "zh-CN",
            "profile.managed_default_content_settings.images": 1
        }
        options.add_experimental_option("prefs", prefs)

        # 指定 chromedriver 的路径
        service = Service(executable_path='/usr/lib/chromium/chromedriver')
        
        try:
            self.driver = webdriver.Chrome(service=service, options=options)
            logging.info(f"WebDriver初始化完成 (Instance ID: {instance_id})")
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
        """
        使用 CaptchaHandler 统一处理所有类型的验证码
        """
        try:
            # 创建 CaptchaHandler 实例
            ocr_api_key = self.config.get("ocr_api_key", "")
            captcha_handler = CaptchaHandler(self.driver, ocr_api_key)
            
            # 使用 CaptchaHandler 处理验证码
            captcha_handler.handle_captcha(url)
            
        except Exception as e:
            logging.error(f"验证码处理失败: {e}")
            logging.info("由于验证码处理失败，程序将正常退出")
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
            try:
                # 检查是否存在用户信息元素 (第一种结构)
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.ID, "um"))
                )
                # 进一步检查用户信息元素中的关键子元素
                self.driver.find_element(By.CLASS_NAME, "vwmy")
                return True
            except TimeoutException:
                try:
                    # 检查是否存在用户信息元素 (第二种结构)
                    WebDriverWait(self.driver, 5).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "dropdown-avatar"))
                    )
                    # 检查用户名元素是否存在
                    self.driver.find_element(By.CSS_SELECTOR, ".dropdown-avatar .dropdown-toggle")
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
            try:
                # 访问用户账户页面检查是否已登录
                self.driver.get(self.gy_user_info_url)
                # 检查是否存在账户设置相关的元素
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//h2[contains(text(), '账户设置')]"))
                )
                # 检查用户名输入框是否存在且被禁用（表明已登录）
                username_input = self.driver.find_element(By.NAME, "username")
                if username_input.get_attribute("disabled") == "true":
                    logging.info("通过账户设置页面确认用户已登录")
                    return True
            except TimeoutException:
                pass
            except Exception as e:
                logging.warning(f"检查登录状态时发生错误: {e}")
            return False

    def login_bthd_site(self, username, password):
        login_url = self.movie_login_url
        self.site_captcha(login_url)  # 使用新的统一验证码处理方法
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
        self.site_captcha(login_url)  # 使用新的统一验证码处理方法
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
        self.site_captcha(login_url)  # 使用新的统一验证码处理方法
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
            if not auto_login_checkbox.is_selected():
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

    def bthd_download_torrent(self, result, title_text, year=None, resolution=None, title=None):
        """高清影视之家解析并下载种子文件"""
        try:
            self.login_bthd_site(self.config["bt_login_username"], self.config["bt_login_password"])
            # 检查页面是否有验证码
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "cl")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                links = self.driver.find_elements(By.TAG_NAME, "a")
                for link in links:
                    link_text = link.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not title:
                            title = title_text
                        new_name = f"{title} ({year})-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise
    
    def hdtv_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """高清剧集网解析并下载种子文件"""
        try:
            self.login_hdtv_site(self.config["bt_login_username"], self.config["bt_login_password"])
            # 检查页面是否有验证码
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                links = self.driver.find_elements(By.TAG_NAME, "a")
                for link in links:
                    link_text = link.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not season:
                            season = ""
                        if not episode_range:
                            episode_range = "未知集数"
                        if not title:
                            title = title_text
                        new_name = f"{title} ({year})-S{season}-[{episode_range}]-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise

    def btys_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """BT影视解析并下载种子文件"""
        try:
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载页面按钮...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "video-info-main")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "btn-aux"))
                )
                links = self.driver.find_elements(By.CLASS_NAME, "btn-aux")
                for link in links:
                    link_text = link.text.strip()
                    if "下载种子" in link_text:
                        attachment_url = link.get_attribute('href')
                        # 点击“下载种子文件”按钮
                        self.driver.execute_script("arguments[0].click();", link)
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                        logging.info("已点击“下载种子文件”按钮，进入下载页面")
                        break

                if not attachment_url:
                    self.driver.close()  # 关闭新标签页
                    self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                    logging.warning(f"没有找到种子文件下载页面按钮，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                # 等待“点击下载”按钮可点击，并用 ActionChains 模拟真实鼠标点击
                try:
                    # 最长等待15秒，直到按钮可点击
                    download_btn = WebDriverWait(self.driver, 15).until(
                        EC.element_to_be_clickable((By.ID, "link"))
                    )
                    # 再加一点点延迟，确保倒计时动画和事件都绑定完毕
                    time.sleep(0.5)
                    actions = ActionChains(self.driver)
                    actions.move_to_element(download_btn).click().perform()
                    logging.info("已点击“点击下载”按钮，开始下载种子文件...")
                    self.driver.close()  # 关闭新标签页
                    self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                except TimeoutException:
                    logging.error("未找到“点击下载”按钮，无法下载种子文件")
                    self.driver.close()  # 关闭新标签页
                    self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                    raise Exception("未找到“点击下载”按钮，无法下载种子文件")

                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not title:
                            title = title_text
                        # 判断是否为电视剧命名
                        if season and episode_range:
                            new_name = f"{title} ({year})-S{season}-[{episode_range}]-{resolution}.torrent"
                        else:
                            new_name = f"{title} ({year})-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise

    def bt0_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """不太灵影视解析并下载种子文件"""
        try:
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "tr-actions")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "download-link"))
                )
                links = self.driver.find_elements(By.CLASS_NAME, "download-link")
                for link in links:
                    link_text = link.text.strip()
                    if "下载种子" in link_text:
                        attachment_url = link.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not title:
                            title = title_text
                        # 判断是否为电视剧命名
                        if season and episode_range:
                            new_name = f"{title} ({year})-S{season}-[{episode_range}]-{resolution}.torrent"
                        else:
                            new_name = f"{title} ({year})-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise
    
    def gy_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """观影解析并下载种子文件"""
        try:
            self.login_gy_site(self.config["gy_login_username"], self.config["gy_login_password"])
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "alert-info")))
            logging.info("页面加载完成")

            attachment_urls = []
            max_retries = 5
            retries = 0

            while not attachment_urls and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "alert-info"))
                )
                links_case1 = self.driver.find_elements(By.CSS_SELECTOR, ".down123 li")
                links_case2 = self.driver.find_elements(By.CSS_SELECTOR, ".down321 .right span")

                for link in links_case1:
                    link_text = link.text.strip()
                    if "种子下载" in link_text:
                        attachment_url = link.get_attribute('data-clipboard-text')
                        if attachment_url:
                            attachment_urls.append(attachment_url)

                for link in links_case2:
                    link_text = link.text.strip()
                    if "种子下载" in link_text:
                        attachment_url = link.get_attribute('data-clipboard-text')
                        if attachment_url:
                            attachment_urls.append(attachment_url)

                if not attachment_urls:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_urls:
                gy_base_url = self.config.get("gy_base_url", "")
                for attachment_url in attachment_urls:
                    attachment_url = attachment_url.replace("#host#", gy_base_url)
                    logging.info(f"找到种子文件下载链接: {attachment_url}")
                    self.driver.get(attachment_url)
                    logging.info("开始下载种子文件...")
                    time.sleep(10)
                    # 新增：重命名种子文件
                    latest_torrent = get_latest_torrent_file()
                    if latest_torrent:
                        # 判断是否为手动模式（通过title参数与title_text相同来判断）
                        if title and title == title_text:
                            # 手动模式下只使用标题命名
                            new_name = f"{title}.torrent"
                        else:
                            # 自动模式下使用完整命名
                            if not resolution:
                                resolution = "未知分辨率"
                            if not year:
                                year = ""
                            if not title:
                                title = title_text
                            # 判断是否为电视剧命名
                            if season and episode_range:
                                new_name = f"{title} ({year})-S{season}-[{episode_range}]-{resolution}.torrent"
                            else:
                                new_name = f"{title} ({year})-{resolution}.torrent"
                        rename_torrent_file(latest_torrent, new_name)
                    else:
                        raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise

    def process_movie_downloads(self):
        """处理电影下载任务"""
        # 读取订阅的电影信息
        all_movie_info = self.extract_movie_info()

        # 定义来源优先级
        sources_priority = ["BTHD", "BT0", "BTYS", "GY"]
        
        # 获取优先关键词配置
        prefer_keywords = self.config.get("resources_prefer_keywords", "")
        prefer_keywords_list = [kw.strip() for kw in prefer_keywords.split(",") if kw.strip()]

        # 遍历每部电影信息
        for movie in all_movie_info:
            title = movie["标题"]
            year = movie["年份"]
            
            # 遍历来源优先级
            download_success = False
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

                # 收集所有可能的下载结果，按优先级排序
                all_download_results = []
                
                # 按优先级添加结果
                if index_data.get("首选分辨率"):
                    all_download_results.extend([(result, "首选分辨率") for result in index_data["首选分辨率"]])
                if index_data.get("备选分辨率"):
                    all_download_results.extend([(result, "备选分辨率") for result in index_data["备选分辨率"]])
                if index_data.get("其他分辨率"):
                    all_download_results.extend([(result, "其他分辨率") for result in index_data["其他分辨率"]])

                # 根据优先关键词对下载结果进行排序
                if prefer_keywords_list:
                    def keyword_priority(result_tuple):
                        result, _ = result_tuple
                        title_text = result.get("title", "").lower()
                        # 计算匹配的优先关键词数量，匹配越多优先级越高
                        match_count = sum(1 for kw in prefer_keywords_list if kw.lower() in title_text)
                        # 返回负值，因为匹配越多优先级越高，而sort是升序排列
                        return -match_count
                    
                    all_download_results.sort(key=keyword_priority)

                # 遍历所有可能的下载结果
                for download_result, resolution_type in all_download_results:
                    download_title = download_result.get("title")
                    logging.info(f"在来源 {source} 中找到匹配结果: {download_title} (分辨率类型: {resolution_type})")
                    
                    # 获取下载链接和标题
                    link = download_result.get("link")
                    resolution = download_result.get("resolution")

                    if not link:
                        logging.warning(f"未找到种子下载链接: {title} ({year})，尝试下一个结果")
                        continue

                    # 根据来源调用相应的下载方法，重命名时传递title参数
                    logging.info(f"开始下载: {download_title} ({resolution}) 来源: {source}")
                    try:
                        if source == "BTHD":
                            self.bthd_download_torrent(download_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "BTYS":
                            self.btys_download_torrent(download_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "BT0":
                            self.bt0_download_torrent(download_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "GY":
                            self.gy_download_torrent(download_result, download_title, year=year, resolution=resolution, title=title)
                        
                        # 下载成功
                        download_success = True
                        logging.info(f"电影下载成功: {title} ({year})")
                        
                        # 下载成功后，更新数据库，标记该电影已完成订阅
                        try:
                            with sqlite3.connect(self.db_path) as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "DELETE FROM MISS_MOVIES WHERE title=? AND year=?",
                                    (title, year)
                                )
                                conn.commit()
                            logging.info(f"已更新订阅数据库，移除已完成的电影订阅: {title} ({year})")
                        except Exception as e:
                            logging.error(f"更新订阅数据库时出错: {e}")
                        
                        # 只有下载成功时才发送通知
                        self.send_notification(movie, download_title, resolution)
                        break  # 不再尝试当前来源的其他结果
                        
                    except Exception as e:
                        logging.error(f"下载过程中发生错误: {e}，尝试当前来源的下一个结果")
                        continue  # 继续尝试当前来源的其他结果
                
                # 如果当前来源中有成功下载的，就不再尝试其他来源
                if download_success:
                    break
            
            if not download_success:
                logging.error(f"所有来源都尝试失败，未能下载电影: {title} ({year})")

    def process_tvshow_downloads(self):
        """处理电视节目下载任务"""
        # 读取订阅的电视节目信息
        all_tv_info = self.extract_tv_info()

        # 定义来源优先级
        sources_priority = ["HDTV", "BT0", "BTYS", "GY"]
        
        # 获取优先关键词配置
        prefer_keywords = self.config.get("resources_prefer_keywords", "")
        prefer_keywords_list = [kw.strip() for kw in prefer_keywords.split(",") if kw.strip()]

        # 遍历每个电视节目信息
        for tvshow in all_tv_info:
            title = tvshow["剧集"]
            year = tvshow["年份"]
            season = tvshow["季"]
            missing_episodes = sorted(map(int, tvshow["缺失集数"]))  # 转换为整数集合并排序
            logging.debug(f"缺失集数: {missing_episodes}")
            
            original_missing_episodes = missing_episodes[:]  # 保存原始缺失集数列表
            successfully_downloaded_episodes = []  # 记录成功下载的集数

            # 创建一个集合来跟踪已经处理过的资源，避免重复下载
            processed_resources = set()
            
            # 添加标志位，用于标识是否已经下载了全集
            full_season_downloaded = False

            # 按集数分组处理，确保每集都能尝试不同来源
            for episode in missing_episodes[:]:  # 使用副本以避免在迭代时修改列表
                # 如果这一集已经被下载过了（在多集资源中），则跳过
                if episode in successfully_downloaded_episodes or full_season_downloaded:
                    continue
                    
                episode_downloaded = False
                
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
                    
                    # 定义资源类型的优先级映射
                    item_type_priority = {
                        "全集": 0,
                        "集数范围": 1,
                        "单集": 2
                    }
                    
                    # 收集所有可能的下载结果
                    all_download_options = []
                    
                    # 按分辨率优先级收集结果
                    for resolution_priority in resolution_priorities:
                        # 收集全集
                        for item in index_data.get(resolution_priority, {}).get("全集", []):
                            if (item.get("start_episode") is not None and item.get("end_episode") is not None and
                                int(item["start_episode"]) <= episode <= int(item["end_episode"])):
                                all_download_options.append((item, source, resolution_priority, "全集"))
                        
                        # 收集集数范围
                        for item in index_data.get(resolution_priority, {}).get("集数范围", []):
                            if (item.get("start_episode") is not None and item.get("end_episode") is not None and
                                int(item["start_episode"]) <= episode <= int(item["end_episode"])):
                                all_download_options.append((item, source, resolution_priority, "集数范围"))
                        
                        # 收集单集
                        for item in index_data.get(resolution_priority, {}).get("单集", []):
                            if item.get("start_episode") is not None and int(item["start_episode"]) == episode:
                                all_download_options.append((item, source, resolution_priority, "单集"))

                    # 根据优先关键词对下载选项进行排序
                    if prefer_keywords_list:
                        def keyword_priority(option_tuple):
                            item, _, _, _ = option_tuple
                            title_text = item.get("title", "").lower()
                            # 计算匹配的优先关键词数量，匹配越多优先级越高
                            match_count = sum(1 for kw in prefer_keywords_list if kw.lower() in title_text)
                            # 返回负值，因为匹配越多优先级越高，而sort是升序排列
                            return -match_count
                        
                        # 先按类型和分辨率排序，再按关键词优先级排序
                        all_download_options.sort(key=lambda x: (
                            item_type_priority.get(x[3], 99),  # x[3] 是 item_type
                            resolution_priorities.index(x[2]),  # x[2] 是 resolution_priority
                            keyword_priority(x)  # 关键词优先级
                        ))
                    else:
                        # 按照类型优先级、分辨率优先级对下载选项进行排序
                        all_download_options.sort(key=lambda x: (
                            item_type_priority.get(x[3], 99),  # x[3] 是 item_type
                            resolution_priorities.index(x[2])  # x[2] 是 resolution_priority
                        ))
                    
                    # 遍历所有可能的下载选项（已按优先级排序）
                    for download_result, result_source, resolution_priority, item_type in all_download_options:
                        # 创建资源唯一标识符
                        resource_identifier = (result_source, download_result.get("title"), 
                                            download_result.get("link"), 
                                            download_result.get("start_episode"), 
                                            download_result.get("end_episode"))
                        
                        # 如果这个资源已经被处理过，跳过
                        if resource_identifier in processed_resources:
                            continue
                        
                        # 找到匹配结果，尝试下载
                        logging.info(f"在来源 {result_source} 中找到匹配结果: {download_result['title']} (类型: {item_type}, 分辨率优先级: {resolution_priority})")
                        
                        # 处理集数范围命名
                        start_ep = download_result.get("start_episode")
                        end_ep = download_result.get("end_episode")
                        
                        # 计算本次下载包含的集数
                        if start_ep and end_ep:
                            episode_nums = list(range(int(start_ep), int(end_ep) + 1))
                        elif start_ep:
                            episode_nums = [int(start_ep)]
                        else:
                            episode_nums = []
                        
                        # 检查这些集数是否都已经下载过了
                        already_downloaded = any(ep in successfully_downloaded_episodes for ep in episode_nums)
                        if already_downloaded:
                            # 标记这个资源已处理，避免重复尝试
                            processed_resources.add(resource_identifier)
                            continue
                        
                        # 处理集数范围
                        if start_ep and end_ep:
                            if int(start_ep) == int(end_ep):
                                episode_range = f"{start_ep}集"
                            elif int(start_ep) == 1 and int(end_ep) > 1 and download_result.get("is_full_season", False):
                                episode_range = f"全{end_ep}集"
                            else:
                                episode_range = f"{start_ep}-{end_ep}集"
                        elif start_ep:
                            episode_range = f"{start_ep}集"
                        else:
                            episode_range = "未知集数"
                            
                        resolution = download_result.get("resolution")
                        
                        # 尝试下载
                        try:
                            if result_source == "HDTV":
                                self.hdtv_download_torrent(download_result, download_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif result_source == "BTYS":
                                self.btys_download_torrent(download_result, download_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif result_source == "BT0":
                                self.bt0_download_torrent(download_result, download_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif result_source == "GY":
                                self.gy_download_torrent(download_result, download_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            
                            # 下载成功
                            successfully_downloaded_episodes.extend(episode_nums)
                            logging.info(f"成功下载集数: {episode_nums}")
                            self.send_notification(tvshow, download_result["title"], resolution)
                            
                            # 标记这个资源已处理
                            processed_resources.add(resource_identifier)
                            
                            # 如果下载的是全集，则标记全集已下载
                            if item_type == "全集":
                                full_season_downloaded = True
                                logging.info(f"全集已下载，跳过该季其余集数的处理")
                                # 标记该全集包含的所有集数为已下载
                                if start_ep and end_ep:
                                    all_episodes_in_full = list(range(int(start_ep), int(end_ep) + 1))
                                    for ep in all_episodes_in_full:
                                        if ep not in successfully_downloaded_episodes:
                                            successfully_downloaded_episodes.append(ep)
                            
                            episode_downloaded = True
                            break  # 不再尝试当前来源的其他结果
                            
                        except Exception as e:
                            logging.error(f"下载失败: {download_result['title']}, 错误: {e}，尝试当前来源的下一个结果")
                            # 标记这个资源已处理，避免重复尝试
                            processed_resources.add(resource_identifier)
                            # 继续尝试当前来源的其他结果
                    
                    # 如果当前来源中有成功下载的，就不再尝试其他来源
                    if episode_downloaded:
                        break
                
                if not episode_downloaded:
                    logging.warning(f"集数 {episode} 下载失败，所有来源均已尝试")
                
                # 如果已经下载了全集，则跳出集数循环
                if full_season_downloaded:
                    break

            # 只对实际下载成功的集数更新数据库
            if successfully_downloaded_episodes:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        cursor = conn.cursor()
                        # 查询当前缺失集数
                        cursor.execute(
                            "SELECT missing_episodes FROM MISS_TVS WHERE title=? AND year=? AND season=?",
                            (title, year, season)
                        )
                        row = cursor.fetchone()
                        if row:
                            current_missing = [ep.strip() for ep in row[0].split(',') if ep.strip()]
                            # 计算剩余缺失集（从原始缺失集中移除成功下载的集数）
                            updated_missing = [ep for ep in current_missing if ep and int(ep) not in successfully_downloaded_episodes]
                            if updated_missing:
                                # 还有未下载的缺失集，更新数据库
                                cursor.execute(
                                    "UPDATE MISS_TVS SET missing_episodes=? WHERE title=? AND year=? AND season=?",
                                    (",".join(updated_missing), title, year, season)
                                )
                                logging.info(f"部分集数已下载，剩余缺失集数已更新: {title} S{season} ({year})，剩余缺失集: {updated_missing}")
                            else:
                                # 所有缺失集已下载，删除订阅
                                cursor.execute(
                                    "DELETE FROM MISS_TVS WHERE title=? AND year=? AND season=?",
                                    (title, year, season)
                                )
                                logging.info(f"所有缺失集已下载，已完成订阅并移除: {title} S{season} ({year})")
                            conn.commit()
                except Exception as e:
                    logging.error(f"更新订阅数据库时出错: {e}")

            # 计算仍然未找到匹配的集数
            still_missing = [ep for ep in original_missing_episodes if ep not in successfully_downloaded_episodes]
            if still_missing:
                logging.warning(f"未找到匹配的下载结果或下载失败: {title} S{season} ({year}) 缺失集数: {still_missing}")

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
            # 修改各下载函数调用，添加is_manual参数
            if args.site.upper() == "BTHD":
                downloader.bthd_download_torrent({"link": args.link}, args.title, title=args.title)
            elif args.site.upper() == "BTYS":
                downloader.btys_download_torrent({"link": args.link}, args.title, title=args.title)
            elif args.site.upper() == "BT0":
                downloader.bt0_download_torrent({"link": args.link}, args.title, title=args.title)
            elif args.site.upper() == "GY":
                downloader.gy_download_torrent({"link": args.link}, args.title, title=args.title)
            elif args.site.upper() == "HDTV":
                downloader.hdtv_download_torrent({"link": args.link}, args.title, title=args.title)
            else:
                logging.error(f"未知的站点名称: {args.site}")
        except Exception as e:
            logging.error(f"手动运行下载功能时发生错误: {e}")
        finally:
            downloader.close_driver()
    else:
        # 如果未提供命令行参数，则运行默认逻辑
        downloader.run()