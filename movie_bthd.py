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
import argparse

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/movie_bthd.log", mode='w'),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

class MovieIndexer:
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
        # 忽略SSL证书错误
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--allow-insecure-localhost')
        options.add_argument('--ignore-ssl-errors')
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
        # 搜索电影并保存索引
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
    
                logging.info(f"找到 {len(search_results)} 个资源项")

                # 过滤搜索结果
                filtered_results = []
                for result in search_results:
                    # 检查年份是否匹配（允许年份为空）
                    if item['年份'] and str(item['年份']) not in result['title']:
                        continue
                    
                    # 检查是否包含排除关键词
                    exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')
                    if any(keyword.strip() in result['title'] for keyword in exclude_keywords):
                        continue
                    
                    filtered_results.append(result)
    
                logging.info(f"过滤后剩余 {len(filtered_results)} 个资源项")

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
                    
                    # 分类逻辑修复
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
    
                # 保存结果到 JSON 文件
                self.save_results_to_json(item['标题'], item['年份'], categorized_results)
    
            except TimeoutException:
                logging.error("搜索结果为空或加载超时")
            except Exception as e:
                logging.error(f"搜索过程中出错: {e}")
    
    def save_results_to_json(self, title, year, categorized_results):
        """将结果保存到 JSON 文件"""
        file_name = f"{title}-{year}-BTHD.json"
        file_path = os.path.join("/tmp/index", file_name)  # 替换为实际保存路径
    
        try:
            # 检查并创建目录
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
            # 检查文件是否存在
            if os.path.exists(file_path):
                logging.info(f"索引已存在，将覆盖: {file_path}")
    
            # 保存文件
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(categorized_results, f, ensure_ascii=False, indent=4)
            logging.info(f"结果已保存到 {file_path}")
        except Exception as e:
            logging.error(f"保存结果到 JSON 文件时出错: {e}")

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
            details["resolution"] = resolution_match.group(1).lower()
        elif "4K" in title.upper():  # 匹配4K规则
            details["resolution"] = "2160p"
    
        # 提取方括号内的内容
        bracket_content_matches = re.findall(r'\[([^\]]+)\]', title)
        for content in bracket_content_matches:
            # 检查是否包含 "+" 或 "/"，如果有则分隔为多个信息
            parts = [part.strip() for part in re.split(r'[+/]', content)]
    
            for part in parts:
                # 匹配音轨信息
                if re.search(r'(音轨|配音)', part):
                    details["audio_tracks"].append(part)
    
                # 匹配字幕信息
                if re.search(r'(字幕)', part):
                    details["subtitles"].append(part)
    
        # 增加对 "国语中字" 的匹配
        if "国语中字" in title:
            details["audio_tracks"].append("国语配音")
            details["subtitles"].append("中文字幕")
    
        return details

    def run(self):
        # 加载配置文件
        self.load_config()

        # 新增：检查程序启用状态
        program_enabled = self.config.get("bthd_enabled", False)
        # 支持字符串和布尔类型
        if isinstance(program_enabled, str):
            program_enabled = program_enabled.lower() == "true"
        if not program_enabled:
            logging.info("站点已被禁用，立即退出。")
            exit(0)

        # 获取订阅电影信息
        all_movie_info = self.extract_movie_info()

        # 检查数据库中是否有有效订阅
        if not all_movie_info:
            logging.info("数据库中没有有效订阅，无需执行后续操作")
            exit(0)  # 退出程序

        # 检查配置中的用户名和密码是否有效
        username = self.config.get("bt_login_username", "")
        password = self.config.get("bt_login_password", "")
        if username == "username" and password == "password":
            logging.error("用户名和密码为系统默认值，程序将不会继续运行，请在系统设置中配置有效的用户名和密码！")
            exit(0)

        # 初始化WebDriver
        self.setup_webdriver()

        # 获取基础 URL
        bt_movie_base_url = self.config.get("bt_movie_base_url", "")
        login_url = f"{bt_movie_base_url}/member.php?mod=logging&action=login"
        search_url = f"{bt_movie_base_url}/search.php?mod=forum"
    
        # 登录操作前先检查滑动验证码
        self.site_captcha(login_url)  # 检查并处理滑动验证码
        self.login(login_url, self.config["bt_login_username"], self.config["bt_login_password"])

        # 搜索和建立索引
        self.search(search_url, all_movie_info)

        # 清理工作，关闭浏览器
        self.driver.quit()
        logging.info("WebDriver关闭完成")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="电影索引器")
    parser.add_argument("--manual", action="store_true", help="手动搜索模式")
    parser.add_argument("--title", type=str, help="电影标题")
    parser.add_argument("--year", type=int, help="电影年份（可选）")
    args = parser.parse_args()

    indexer = MovieIndexer()

    if args.manual:
        # 加载配置文件
        indexer.load_config()

        # 新增：检查程序启用状态
        program_enabled = indexer.config.get("bthd_enabled", False)
        # 支持字符串和布尔类型
        if isinstance(program_enabled, str):
            program_enabled = program_enabled.lower() == "true"
        if not program_enabled:
            logging.info("站点已被禁用，立即退出。")
            exit(0)

        # 检查配置中的用户名和密码是否有效
        username = indexer.config.get("bt_login_username", "")
        password = indexer.config.get("bt_login_password", "")
        if username == "username" and password == "password":
            logging.error("用户名和密码为系统默认值，程序将不会继续运行，请在系统设置中配置有效的用户名和密码！")
            exit(0)

        # 初始化 WebDriver
        indexer.setup_webdriver()
    
        # 获取基础 URL
        bt_movie_base_url = indexer.config.get("bt_movie_base_url", "")
        login_url = f"{bt_movie_base_url}/member.php?mod=logging&action=login"
        search_url = f"{bt_movie_base_url}/search.php?mod=forum"
    
        # 登录操作前先检查滑动验证码
        indexer.site_captcha(login_url)  # 检查并处理滑动验证码
        indexer.login(login_url, indexer.config["bt_login_username"], indexer.config["bt_login_password"])
    
        # 执行手动搜索
        if args.title:
            # 将单个电影信息封装为列表并调用 search 方法
            movie_info = [{"标题": args.title, "年份": str(args.year) if args.year else ""}]
            indexer.search(search_url, movie_info)
        else:
            logging.error("手动搜索模式需要提供 --title 参数")
    
        # 清理工作，关闭浏览器
        indexer.driver.quit()
        logging.info("WebDriver关闭完成")
    else:
        indexer.run()