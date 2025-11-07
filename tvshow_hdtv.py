import sqlite3
import json
import time
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
from captcha_handler import CaptchaHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/tvshow_hdtv.log", mode='w'),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

class TvshowIndexer:
    def __init__(self, db_path='/config/data.db', instance_id=None):
        self.db_path = db_path
        self.driver = None
        self.config = {}
        self.instance_id = instance_id
        # 如果有实例ID，修改日志文件路径以避免冲突
        if instance_id:
            logging.getLogger().handlers.clear()
            logging.basicConfig(
                level=logging.INFO,
                format=f"%(levelname)s - INST - {instance_id} - %(message)s",
                handlers=[
                    logging.FileHandler(f"/tmp/log/tvshow_hdtv_inst_{instance_id}.log", mode='w'),
                    logging.StreamHandler()
                ]
            )

    def setup_webdriver(self):
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
        # 设置页面加载策略为急切模式
        options.page_load_strategy = 'eager'
        # 设置浏览器语言为中文
        options.add_argument('--lang=zh-CN')
        # 设置用户配置文件缓存目录，添加实例ID以避免冲突
        user_data_dir = '/app/ChromeCache/user-data-dir'
        if self.instance_id:
            user_data_dir = f'/app/ChromeCache/user-data-dir-inst-{self.instance_id}'
        options.add_argument(f'--user-data-dir={user_data_dir}')
        # 设置磁盘缓存目录，添加实例ID以避免冲突
        disk_cache_dir = "/app/ChromeCache/disk-cache-dir"
        if self.instance_id:
            disk_cache_dir = f"/app/ChromeCache/disk-cache-dir-inst-{self.instance_id}"
        options.add_argument(f"--disk-cache-dir={disk_cache_dir}")
        
        # 设置默认下载目录
        prefs = {
            "download.default_directory": "/Torrent",
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

    def login(self, url, username, password):
        try:
            # 使用新的验证码处理方法
            self.site_captcha(url)
            self.driver.get(url)
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
        except Exception as e:
            logging.error(f"访问登录页面失败: {e}")
            logging.info("由于访问失败，程序将正常退出")
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

    def extract_tv_info(self):
        """从数据库读取订阅的电视节目信息和缺失的集数信息"""
        all_tv_info = []
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 读取订阅的电视节目信息和缺失的集数信息
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
        
        logging.debug("读取订阅电视节目信息和缺失的集数信息完成")
        return all_tv_info

    def search(self, search_url, all_tv_info):
        """搜索剧集并整理结果"""
        for item in all_tv_info:
            logging.info(f"开始搜索剧集: {item['剧集']}  年份: {item['年份']}  季: {item['季']}")
            search_query = f"{item['剧集']} {item['年份']}"
            search_results = []
            
            try:
                # 首先执行搜索
                # 使用新的验证码处理方法
                self.site_captcha(search_url)
                self.driver.get(search_url)
                search_box = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.NAME, "srchtxt"))
                )
                search_box.send_keys(search_query)
                search_box.send_keys(Keys.RETURN)
                logging.debug(f"搜索关键词: {search_query}")

                # 处理所有页面的搜索结果
                page = 1
                while True:
                    logging.info(f"正在处理第 {page} 页搜索结果")
                    
                    # 等待搜索结果加载
                    WebDriverWait(self.driver, 10).until(
                        EC.presence_of_element_located((By.ID, "threadlist"))
                    )
                    
                    # 查找当前页面搜索结果中的链接和热度数据
                    results = self.driver.find_elements(By.CSS_SELECTOR, "#threadlist li.pbw")
                    for result in results:
                        try:
                            # 提取标题和链接
                            title_link = result.find_element(By.CSS_SELECTOR, "h3.xs3 a")
                            title_text = title_link.text
                            link = title_link.get_attribute('href')
                            
                            # 提取查看次数（热度数据）
                            view_count_text = result.find_element(By.CSS_SELECTOR, "p.xg1").text
                            popularity = self.extract_popularity(view_count_text)
                            
                            search_results.append({
                                "title": title_text,
                                "link": link,
                                "popularity": popularity
                            })
                        except Exception as e:
                            logging.warning(f"解析搜索结果项时出错: {e}")
                            continue

                    logging.debug(f"第 {page} 页找到 {len(results)} 个资源项")
                    
                    # 检查是否有下一页
                    try:
                        # 查找"下一页"链接
                        next_page_link = self.driver.find_element(By.CSS_SELECTOR, "div.pg a.nxt")
                        if next_page_link.is_displayed() and next_page_link.is_enabled():
                            # 点击下一页
                            next_page_link.click()
                            page += 1
                            # 等待页面加载完成
                            time.sleep(2)  # 等待页面加载
                        else:
                            logging.info("已到达最后一页")
                            break
                    except Exception as e:
                        logging.debug(f"没有找到下一页链接或已到达最后一页: {e}")
                        break

                logging.info(f"总共找到 {len(search_results)} 个资源项")

                # 过滤搜索结果
                filtered_results = []
                for result in search_results:
                    # 检查年份是否匹配
                    if item['年份'] and item['年份'] not in result['title']:
                        continue

                    # 检查是否包含排除关键词
                    exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')
                    if any(keyword.strip() in result['title'] for keyword in exclude_keywords if keyword.strip()):
                        continue

                    filtered_results.append(result)

                logging.info(f"过滤后剩余 {len(filtered_results)} 个资源项")

                # 获取首选分辨率和备选分辨率
                preferred_resolution = self.config.get('preferred_resolution', "未知分辨率")
                fallback_resolution = self.config.get('fallback_resolution', "未知分辨率")

                # 按分辨率和类型（单集、集数范围、全集）分类搜索结果
                categorized_results = {
                    "首选分辨率": {
                        "单集": [],
                        "集数范围": [],
                        "全集": []
                    },
                    "备选分辨率": {
                        "单集": [],
                        "集数范围": [],
                        "全集": []
                    },
                    "其他分辨率": {
                        "单集": [],
                        "集数范围": [],
                        "全集": []
                    }
                }
                for result in filtered_results:
                    details = self.extract_details(result['title'])
                    resolution = details['resolution']
                    episode_type = details['episode_type']

                    # 如果集数类型为未知，跳过该结果
                    if episode_type == "未知集数":
                        logging.warning(f"跳过未知集数的资源: {result['title']}")
                        continue

                    # 根据分辨率和类型分类
                    if resolution == preferred_resolution:
                        categorized_results["首选分辨率"][episode_type].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "start_episode": details['start_episode'],
                            "end_episode": details['end_episode'],
                            "size": details['size'],
                            "popularity": result['popularity']  # 添加热度数据
                        })
                    elif resolution == fallback_resolution:
                        categorized_results["备选分辨率"][episode_type].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "start_episode": details['start_episode'],
                            "end_episode": details['end_episode'],
                            "size": details['size'],
                            "popularity": result['popularity']  # 添加热度数据
                        })
                    else:
                        categorized_results["其他分辨率"][episode_type].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "start_episode": details['start_episode'],
                            "end_episode": details['end_episode'],
                            "size": details['size'],
                            "popularity": result['popularity']  # 添加热度数据
                        })

                # 保存结果到 JSON 文件
                self.save_results_to_json(item['剧集'], item['季'], item['年份'], categorized_results)

            except TimeoutException:
                logging.error("搜索结果为空或加载超时")
            except Exception as e:
                logging.error(f"搜索过程中出错: {e}")

    def extract_popularity(self, text):
        """
        从文本中提取热度数据（查看次数）
        文本格式类似: "0 个回复 - 38 次查看"
        """
        try:
            # 使用正则表达式提取查看次数
            match = re.search(r'(\d+)\s*次查看', text)
            if match:
                return int(match.group(1))
            else:
                return 0
        except Exception as e:
            logging.warning(f"提取热度数据时出错: {e}")
            return 0
    
    def save_results_to_json(self, title, season, year, categorized_results):
        """将结果保存到 JSON 文件"""
        # 根据是否有季信息来决定文件名格式
        if season:
            file_name = f"{title}-S{season}-{year}-HDTV.json"
        else:
            file_name = f"{title}-{year}-HDTV.json"
        
        file_path = os.path.join("/tmp/index", file_name)

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

    def extract_details(self, title_text):
        """
        从标题中提取电视节目的详细信息，包括分辨率、集数范围和文件大小等。
        """
        title_text = str(title_text)

        # 提取分辨率
        resolution_match = re.search(r'(\d{3,4}p)', title_text, re.IGNORECASE)
        if resolution_match:
            resolution = resolution_match.group(1).lower()
        elif "4K" in title_text.upper():  # 匹配4K规则
            resolution = "2160p"
        else:
            resolution = "未知分辨率"

        # 初始化集数范围
        start_episode = None
        end_episode = None
        episode_type = "未知集数"

        # 处理单集或集数范围格式
        episode_pattern = r"(?:EP|第)(\d{1,3})(?:[-~](?:EP|第)?(\d{1,3}))?"
        episode_match = re.search(episode_pattern, title_text, re.IGNORECASE)

        if episode_match:
            start_episode = int(episode_match.group(1))
            end_episode = int(episode_match.group(2)) if episode_match.group(2) else start_episode
            episode_type = "单集" if start_episode == end_episode else "集数范围"
        elif "全" in title_text:
            # 如果标题中包含"全"字，则认为是全集资源
            full_episode_pattern = r"全(\d{1,3})"
            full_episode_match = re.search(full_episode_pattern, title_text)

            if full_episode_match:
                start_episode = 1
                end_episode = int(full_episode_match.group(1))
                episode_type = "全集"
            else:
                start_episode = 1
                end_episode = None  # 表示未知的全集结束集数
                episode_type = "全集"
        elif re.search(r"(更至|更新至)(\d{1,3})集", title_text):
            # 匹配"更至XX集"或"更新至XX集"
            update_match = re.search(r"(更至|更新至)(\d{1,3})集", title_text)
            if update_match:
                start_episode = 1
                end_episode = int(update_match.group(2))
                episode_type = "集数范围"
                
        # 提取文件大小信息
        size = "未知大小"
        size_match = re.search(r'(\d+\.?\d*)\s*(GB|MB|TB)', title_text, re.IGNORECASE)
        if size_match:
            size = f"{size_match.group(1)} {size_match.group(2).upper()}"

        # 添加日志记录
        logging.debug(
            f"提取结果 - 标题: {title_text}, 分辨率: {resolution}, 开始集数: {start_episode}, "
            f"结束集数: {end_episode}, 集数类型: {episode_type}, 文件大小: {size}"
        )

        return {
            "resolution": resolution,
            "start_episode": start_episode,
            "end_episode": end_episode,
            "episode_type": episode_type,
            "size": size
        }
    
    def run(self):
        # 加载配置文件
        self.load_config()

         # 新增：检查程序启用状态
        program_enabled = self.config.get("hdtv_enabled", False)
        # 支持字符串和布尔类型
        if isinstance(program_enabled, str):
            program_enabled = program_enabled.lower() == "true"
        if not program_enabled:
            logging.info("站点已被禁用，立即退出。")
            exit(0)

        # 获取订阅电视节目信息
        all_tv_info = self.extract_tv_info()

        # 检查数据库中是否有有效订阅
        if not all_tv_info:
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
        bt_tv_base_url = self.config.get("bt_tv_base_url", "")
        login_url = f"{bt_tv_base_url}/member.php?mod=logging&action=login"
        search_url = f"{bt_tv_base_url}/search.php?mod=forum"
    
        # 登录操作
        self.login(login_url, self.config["bt_login_username"], self.config["bt_login_password"])

        # 搜索和建立索引
        self.search(search_url, all_tv_info)

        # 清理工作，关闭浏览器
        self.driver.quit()
        logging.info("WebDriver关闭完成")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="电视节目索引器")
    parser.add_argument("--manual", action="store_true", help="手动搜索模式")
    parser.add_argument("--title", type=str, help="电视节目名称")
    parser.add_argument("--year", type=int, help="电视节目年份（可选）")
    parser.add_argument("--season", type=int, help="电视节目季数（可选）")
    parser.add_argument("--instance-id", type=str, help="实例唯一标识符")
    args = parser.parse_args()

    indexer = TvshowIndexer(instance_id=args.instance_id)

    if args.manual:
        # 检查手动模式下是否提供了 --title 参数
        if not args.title:
            logging.error("手动搜索模式需要提供 --title 参数")
            exit(1)

        # 加载配置文件
        indexer.load_config()

        # 新增：检查程序启用状态
        program_enabled = indexer.config.get("hdtv_enabled", False)
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
        bt_tv_base_url = indexer.config.get("bt_tv_base_url", "")
        login_url = f"{bt_tv_base_url}/member.php?mod=logging&action=login"
        search_url = f"{bt_tv_base_url}/search.php?mod=forum"

        # 登录操作
        indexer.login(login_url, indexer.config["bt_login_username"], indexer.config["bt_login_password"])

        # 执行手动搜索
        tv_info = [{
            "剧集": args.title,
            "年份": str(args.year) if args.year else "",  # 年份为空时传递空字符串
            "季": str(args.season) if args.season else "",  # 季数为空时传递空字符串
            "缺失集数": []  # 手动搜索时不指定缺失集数
        }]
        indexer.search(search_url, tv_info)

        # 清理工作，关闭浏览器
        indexer.driver.quit()
        logging.info("WebDriver关闭完成")
    else:
        # 按默认逻辑运行
        indexer.run()