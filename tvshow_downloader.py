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
import re
import time
import requests

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/tvshow_downloader.log"),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

DOWNLOAD_RECORD_FILE = '/tmp/record/tvshow_download_records.json'

class TVDownloader:
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
            auto_login_checkbox = self.driver.find_element(By.CLASS_NAME, 'checkbox-style')
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
            with open(DOWNLOAD_RECORD_FILE, 'r', encoding='utf-8') as file:
                records = json.load(file)
                logging.debug("加载下载记录成功")
                return records
        logging.info("下载记录文件不存在，创建新文件")
        return []

    def save_download_records(self, records):
        """保存已下载记录"""
        with open(DOWNLOAD_RECORD_FILE, 'w', encoding='utf-8') as file:
            json.dump(records, file, ensure_ascii=False, indent=4)
            logging.debug("保存下载记录成功")

    def build_record_key(self, item, resolution, title_text):
        # 构建记录键，使用实际的标题
        return f"{item['剧集']}_{resolution}_{title_text}"

    def extract_tv_info(self):
        """从数据库读取缺失的电视节目信息"""
        all_tv_info = []
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # 假设数据库中有一个额外的列 'year' 存储年份信息
            cursor.execute('SELECT title, year, missing_episodes FROM MISS_TVS')
            tvs = cursor.fetchall()
            
            for title, year, missing_episodes_str in tvs:
                # 确保 year 是字符串类型
                if isinstance(year, int):
                    year = str(year)  # 将整数转换为字符串
                
                # 确保 missing_episodes_str 是字符串类型
                if isinstance(missing_episodes_str, int):
                    missing_episodes_str = str(missing_episodes_str)  # 将整数转换为字符串
                
                # 处理缺失集数
                missing_episodes = [int(ep.strip()) for ep in missing_episodes_str.split(',') if ep.strip()]
                if missing_episodes:
                    min_episode_num = min(missing_episodes)
                    formatted_episode_number = f'{"0" if min_episode_num < 10 else ""}{min_episode_num}'
                else:
                    formatted_episode_number = '01'  # 如果无缺失集数信息，则认为是从第01集开始缺失
                
                preferred_resolution = self.config.get('bt_preferred_resolution', "")
                all_tv_info.append({
                    "剧集": title,
                    "年份": year,
                    "分辨率": preferred_resolution,
                    "集数": formatted_episode_number
                })
        logging.debug("读取缺失的电视节目信息完成")
        return all_tv_info

    def find_episode_links(self, list_items, item, resolution):
        found_links = []
        exclude_keywords = self.config.get("resources", {}).get("exclude_keywords", "")
        exclude_keywords = [keyword.strip() for keyword in exclude_keywords.split(',')]
        
        # 获取当前条目的年份
        year = item.get("年份", "")
        
        for li in list_items:
            try:
                a_element = li.find_element(By.TAG_NAME, 'a')
                title_text = a_element.text.lower()

                # 检查是否需要排除此条目
                if any(keyword in title_text for keyword in exclude_keywords):
                    continue

                # 检查年份是否匹配
                if year and year not in title_text:
                    logging.debug(f"年份不匹配，跳过条目: {title_text}")
                    continue

                # 匹配集数范围
                range_match = f"[第{item['集数']}-" in title_text
                # 匹配精确集数
                exact_match = f"[第{item['集数']}集]" in title_text
                if resolution.lower() in title_text and (exact_match or range_match):
                    link = a_element.get_attribute('href')
                    found_links.append((link, title_text))
                    logging.info(f"发现: {title_text}, Link: {link}")
            except NoSuchElementException:
                logging.warning("未找到搜索结果元素")
                continue
        return found_links


    def find_full_set_resource(self, resolution, download_records, item):
        list_items = self.driver.find_elements(By.TAG_NAME, 'li')
        # 获取当前条目的年份
        year = item.get("年份", "")
        
        for li in list_items:
            try:
                a_element = li.find_element(By.TAG_NAME, 'a')
                title_text = a_element.text.lower()

                # 获取排除关键字列表
                exclude_keywords = self.config.get("resources", {}).get("exclude_keywords", "")
                exclude_keywords = [keyword.strip() for keyword in exclude_keywords.split(',')]
                
                # 检查是否需要排除此条目
                if any(keyword in title_text for keyword in exclude_keywords):
                    continue
                
                # 检查年份是否匹配
                if year and year not in title_text:
                    logging.debug(f"年份不匹配，跳过条目: {title_text}")
                    continue
                
                # 匹配全集，并且包含分辨率
                if '全' in title_text and any(char.isdigit() for char in title_text) and resolution.lower() in title_text:
                    link = a_element.get_attribute('href')
                    logging.info(f"发现全集资源: {title_text}, Link: {link}")
                    self.handle_full_set(link, item, resolution, download_records)
                    return True
            except NoSuchElementException:
                logging.warning("未找到搜索结果元素")
                continue
        return False

    def search_and_download(self, search_url, items):
        download_records = self.load_download_records()
        
        for item in items:
            # 构建下载记录的键
            preferred_resolution = item.get('分辨率', "")
            fallback_resolution = self.config.get('bt_fallback_resolution', "")
            
            # 尝试首选分辨率
            record_key = self.build_record_key(item, preferred_resolution, "未知标题")
            if record_key not in download_records:
                logging.info(f"开始搜索: 剧集 {item['剧集']}, 分辨率 {preferred_resolution}, 集数 {item['集数']}")
                self.driver.get(search_url)
                search_box = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, 'scform_srchtxt'))
                )
                search_box.send_keys(item['剧集'])
                search_box.send_keys(Keys.RETURN)
                logging.info("搜索请求发送完成")
                logging.info("等待剧集结果")
                time.sleep(5)  # 假设结果5秒内加载完成

                # 查找全集资源优先
                full_set_found = self.find_full_set_resource(preferred_resolution, download_records, item)
                if full_set_found:
                    continue
                
                # 没有找到全集资源，按单集搜索
                found_links = self.find_episode_links(self.driver.find_elements(By.TAG_NAME, 'li'), item, preferred_resolution)
                if found_links:
                    self.handle_single_episodes(found_links, item, preferred_resolution, download_records)
                    continue
                
                # 没有找到首选分辨率的资源，尝试次级分辨率
                logging.warning("未找到首选分辨率资源，尝试备选分辨率搜索")
                self.search_with_fallback_resolution(item, search_url, download_records, preferred_resolution, fallback_resolution)
            else:
                logging.debug(f"记录已存在，跳过下载: {record_key}")

    def search_with_fallback_resolution(self, item, search_url, download_records, preferred_resolution, fallback_resolution):
        record_key = self.build_record_key(item, fallback_resolution, "未知标题")
        if record_key not in download_records:
            logging.info(f"开始搜索: 剧集 {item['剧集']}, 分辨率 {fallback_resolution}, 集数 {item['集数']}")
            self.driver.get(search_url)
            search_box = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.ID, 'scform_srchtxt'))
            )
            search_box.send_keys(item['剧集'])
            search_box.send_keys(Keys.RETURN)
            logging.info("搜索请求发送完成")
            logging.info("等待剧集结果")
            time.sleep(5)  # 假设结果5秒内加载完成

            # 查找全集资源优先
            full_set_found = self.find_full_set_resource(fallback_resolution, download_records, item)
            if full_set_found:
                return
            
            # 没有找到全集资源，按单集搜索
            found_links = self.find_episode_links(self.driver.find_elements(By.TAG_NAME, 'li'), item, fallback_resolution)
            if found_links:
                self.handle_single_episodes(found_links, item, fallback_resolution, download_records)
            else:
                logging.warning("没有找到匹配的下载链接。")
        else:
            logging.debug(f"记录已存在，跳过下载: {record_key}")

    def handle_full_set(self, link, item, resolution, download_records):
        # 处理全集资源的方法
        self.driver.get(link)
        WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
        logging.debug("进入详情页面")
        logging.info("找到匹配全集剧集结果，开始查找种子文件")
        
        # 构建记录键
        record_key = self.build_record_key(item, resolution, "全集")
        if record_key in download_records:
            logging.info(f"记录已存在，跳过下载: {record_key}")
            return
        
        try:
            attachment_link = self.driver.find_element(By.PARTIAL_LINK_TEXT, "torrent")
            attachment_url = attachment_link.get_attribute('href')
            self.download_torrent(attachment_url, item, "全集", resolution, download_records)
        except NoSuchElementException:
            logging.warning("没有找到附件链接。")

    def handle_single_episodes(self, found_links, item, resolution, download_records):
        # 处理单集资源的方法
        if found_links:
            # 只处理第一个匹配结果
            first_link, first_title_text = found_links[0]
            # 构建记录键
            record_key = self.build_record_key(item, resolution, first_title_text)
            if record_key in download_records:
                logging.info(f"记录已存在，跳过下载: {record_key}")
                return
            self.driver.get(first_link)
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
            logging.debug("进入详情页面")
            logging.info("找到匹配剧集结果，开始查找种子文件")
            try:
                attachment_link = self.driver.find_element(By.PARTIAL_LINK_TEXT, "torrent")
                attachment_url = attachment_link.get_attribute('href')
                self.download_torrent(attachment_url, item, first_title_text, resolution, download_records)
            except NoSuchElementException:
                logging.warning("没有找到附件链接。")
        else:
            logging.warning("没有找到匹配的下载链接。")

    def download_torrent(self, torrent_url, item, title_text, resolution, download_records):
        self.driver.get(torrent_url)
        logging.info("开始下载种子文件")
        time.sleep(10)  # 设置等待时间为10秒，等待文件下载完成
        self.send_notification(item, title_text, resolution)
        
        # 从标题中提取集数范围
        episode_range = self.extract_episode_number(title_text)
        
        # 构建记录键
        record_key = self.build_record_key(item, resolution, title_text)
        if record_key not in download_records:
            download_records.append(record_key)
            self.save_download_records(download_records)
            logging.info(f"下载记录更新完成: {record_key}")
        else:
            logging.info(f"记录已存在，跳过更新: {record_key}")
        
        # 尝试下载下一集
        if episode_range is not None:
            start_episode, end_episode = episode_range
            next_episode_number = str(int(end_episode) + 1).zfill(2)
            logging.info(f"尝试下载下一集：第{next_episode_number}集")
            self.search_and_download_next_episode(item, next_episode_number, resolution)

    def extract_episode_number(self, title_text):
        # 正则表达式匹配集数或集数范围
        episode_pattern = r"(?:第)?(\d{1,2})(?:-(\d{1,2}))?(?:集)?"
        match = re.search(episode_pattern, title_text)
        
        if match:
            start_episode = match.group(1)
            end_episode = match.group(2) or start_episode
            
            # 返回集数范围的元组
            return (start_episode, end_episode)
        else:
            logging.error("无法从标题中提取集数")
            return None

    def search_and_download_next_episode(self, item, next_episode_number, resolution):
        search_url = self.config.get("urls", {}).get("tv_search_url", "")
        search_term = f"{item['剧集']}"
        self.driver.get(search_url)
        search_box = WebDriverWait(self.driver, 10).until(
            EC.presence_of_element_located((By.ID, 'scform_srchtxt'))
        )
        search_box.send_keys(search_term)
        search_box.send_keys(Keys.RETURN)
        logging.info("搜索请求发送完成")
        logging.info("等待剧集结果")
        time.sleep(5)  # 假设结果5秒内加载完成
        
        # 查找符合的资源
        list_items = self.driver.find_elements(By.TAG_NAME, 'li')
        found_links = []
        exclude_keywords = self.config.get("resources", {}).get("exclude_keywords", "")
        exclude_keywords = [keyword.strip() for keyword in exclude_keywords.split(',')]
        
        # 获取当前条目的年份
        year = item.get("年份", "")
        
        for li in list_items:
            try:
                a_element = li.find_element(By.TAG_NAME, 'a')
                title_text = a_element.text.lower()

                # 检查是否需要排除此条目
                if any(keyword in title_text for keyword in exclude_keywords):
                    continue

                # 检查年份是否匹配
                if year and year not in title_text:
                    logging.debug(f"年份不匹配，跳过条目: {title_text}")
                    continue

                # 匹配精确集数
                exact_match = f"[第{next_episode_number}集]" in title_text
                # 匹配集数范围
                range_match = f"[第{next_episode_number}-" in title_text
                if resolution.lower() in title_text and (exact_match or range_match):
                    link = a_element.get_attribute('href')
                    found_links.append((link, title_text))
                    logging.info(f"发现: {title_text}, Link: {link}")
            except NoSuchElementException:
                logging.warning("未找到搜索结果元素")
                continue

        if found_links:
            # 只处理第一个匹配结果
            first_link, first_title_text = found_links[0]
            # 更新记录键为实际的标题
            record_key = self.build_record_key(item, resolution, first_title_text)
            download_records = self.load_download_records()
            if record_key in download_records:
                logging.info(f"记录已存在，跳过下载: {record_key}")
                return
            self.driver.get(first_link)
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
            logging.debug("进入详情页面")
            logging.info("找到匹配剧集结果，开始查找种子文件")
            try:
                attachment_link = self.driver.find_element(By.PARTIAL_LINK_TEXT, "torrent")
                attachment_url = attachment_link.get_attribute('href')
                self.download_torrent(attachment_url, item, first_title_text, resolution, download_records)
            except NoSuchElementException:
                logging.warning("没有找到附件链接。")
        else:
            logging.warning("没有找到匹配的下载链接。")

    def send_notification(self, item, title_text, resolution):
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

    def run(self):
        # 加载配置文件
        self.load_config()
        
        # 检查配置中的必要信息是否存在
        if not self.config.get("bt_login_username") or \
        not self.config.get("bt_login_password") or \
        not self.config.get("notification_api_key"):
            logging.error("请在系统设置中修改必要配置并填写正确的用户名、密码及API key等参数。")
            exit(1)  # 提示后立即退出程序

        # 提取电视节目信息
        all_tv_info = self.extract_tv_info()

        # 检查数据库中是否有有效订阅
        if not all_tv_info:
            logging.info("数据库中没有有效订阅，无需执行后续操作")
            exit(0)  # 退出程序

        # 初始化WebDriver
        self.setup_webdriver()

        # 获取基础 URL
        bt_tv_base_url = self.config.get("bt_tv_base_url", "")
        login_url = f"{bt_tv_base_url}/member.php?mod=logging&action=login"
        search_url = f"{bt_tv_base_url}/search.php?mod=forum"
    
        # 登录操作前先检查滑动验证码
        self.site_captcha(login_url)  # 检查并处理滑动验证码
        self.login(login_url, self.config["bt_login_username"], self.config["bt_login_password"])

        # 搜索和下载操作
        self.search_and_download(search_url, all_tv_info)

        # 清理工作，关闭浏览器
        self.driver.quit()
        logging.info("WebDriver关闭完成")

if __name__ == "__main__":
    downloader = TVDownloader()
    downloader.run()