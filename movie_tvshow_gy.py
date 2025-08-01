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
import argparse
from urllib.parse import quote

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/movie_tvshow_gy.log", mode='w'),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

class MediaIndexer:
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
        try:
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
            logging.info("由于访问失败，程序将正常退出")
            self.driver.quit()
            exit(1)
    
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

    def is_logged_in_gy(self):
        try:
            # 检查页面中是否存在特定的提示文本
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '登录成功')]"))
            )
            return True
        except TimeoutException:
            return False

    def login_gy_site(self, username, password):
        login_url = self.gy_login_url
        user_info_url = self.gy_user_info_url
        self.site_captcha(login_url)  # 调用 gy_site_captcha 方法
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

    def search_movie(self, search_url, all_movie_info):
        """搜索电影并保存索引"""
        for item in all_movie_info:
            logging.info(f"开始搜索电影: {item['标题']}  年份: {item['年份']}")
            search_query = quote(item['标题'])
            full_search_url = f"{search_url}{search_query}"
            try:
                self.driver.get(full_search_url)
                logging.debug(f"访问搜索URL: {full_search_url}")
            except Exception as e:
                logging.error(f"无法访问搜索页面: {full_search_url}，错误: {e}")
                logging.info("跳过当前搜索项，继续执行下一个媒体搜索")
                continue
            try:
                # 等待搜索结果加载
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "sr_lists"))
                )
                video_cards = self.driver.find_elements(By.CLASS_NAME, "v5d")
                logging.debug(f"找到 {len(video_cards)} 个视频卡片")
                found_match = False  # 标记是否找到匹配的卡片

                for card in video_cards:
                    try:
                        # 直接查找标题和年份信息
                        title_element = card.find_element(By.CLASS_NAME, "d16")
                        title_text = title_element.text  # 获取标题文本
                        
                        # 提取标题和年份
                        match = re.match(r"(.+?)\s*\((\d{4})\)", title_text)
                        if match:
                            hover_title = match.group(1)  # 提取标题
                            hover_year = match.group(2)   # 提取年份
                            logging.debug(f"提取的标题: {hover_title}, 年份: {hover_year}")
                        else:
                            logging.warning(f"无法解析标题和年份: {title_text}")
                            continue
    
                        # 检查标题和年份是否与搜索匹配
                        if item['标题'] == hover_title and str(item['年份']) == hover_year:
                            logging.info(f"找到匹配的电影卡片: {hover_title} ({hover_year})")
                            found_match = True  # 找到匹配的卡片
                            
                            # 使用 ActionChains 模拟鼠标点击标题
                            actions = ActionChains(self.driver)
                            actions.move_to_element(title_element).click().perform()
                            logging.info("成功点击匹配的电影标题")
    
                            # 等待影片详细信息页面加载完成
                            try:
                                WebDriverWait(self.driver, 10).until(
                                    EC.presence_of_element_located((By.CLASS_NAME, "movie-introduce"))
                                )
                                logging.info("成功进入影片详细信息页面")
                                time.sleep(5)  # 等待页面稳定
                            except TimeoutException:
                                logging.error("影片详细信息页面加载超时")
                                continue
                            
                            # 获取资源列表
                            resource_items = self.driver.find_elements(By.CSS_SELECTOR, "div.down-list > ul > li.down-list2")
                            logging.info(f"找到 {len(resource_items)} 个资源项")
                            categorized_results = {
                                "首选分辨率": [],
                                "备选分辨率": [],
                                "其他分辨率": []
                            }
                            
                            # 获取配置中的首选和备选分辨率
                            preferred_resolution = self.config.get('preferred_resolution', "未知分辨率")
                            fallback_resolution = self.config.get('fallback_resolution', "未知分辨率")
                            exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')

                            filtered_resources = []  # 用于存储过滤后的资源项

                            for resource in resource_items:
                                try:
                                    # 提取资源标题
                                    resource_title = resource.find_element(By.CSS_SELECTOR, "p.down-list3 a").get_attribute("title")
                                    resource_link = resource.find_element(By.CSS_SELECTOR, "span a[target='_blank']").get_attribute("href")
                                    logging.debug(f"资源标题: {resource_title}, 链接: {resource_link}")
                                    
                                    # 检查是否包含排除关键词
                                    if any(keyword.strip() in resource_title for keyword in exclude_keywords):
                                        logging.debug(f"跳过包含排除关键词的资源: {resource_title}")
                                        continue
                                    
                                    # 添加到过滤后的资源列表
                                    filtered_resources.append(resource)

                                    # 提取详细信息
                                    details = self.extract_details_movie(resource_title)
                                    resolution = details["resolution"]
                                    logging.debug(f"解析出的分辨率: {resolution}, 详细信息: {details}")
                                    
                                    # 分类资源
                                    if resolution == preferred_resolution:
                                        categorized_results["首选分辨率"].append({
                                            "title": resource_title,
                                            "link": resource_link,
                                            "resolution": resolution,
                                            "audio_tracks": details["audio_tracks"],
                                            "subtitles": details["subtitles"]
                                        })
                                    elif resolution == fallback_resolution:
                                        categorized_results["备选分辨率"].append({
                                            "title": resource_title,
                                            "link": resource_link,
                                            "resolution": resolution,
                                            "audio_tracks": details["audio_tracks"],
                                            "subtitles": details["subtitles"]
                                        })
                                    else:
                                        categorized_results["其他分辨率"].append({
                                            "title": resource_title,
                                            "link": resource_link,
                                            "resolution": resolution,
                                            "audio_tracks": details["audio_tracks"],
                                            "subtitles": details["subtitles"]
                                        })
                                except Exception as e:
                                    logging.warning(f"解析资源项时出错: {e}")
                            
                            logging.info(f"过滤后剩余 {len(filtered_resources)} 个资源项")

                            # 保存结果到 JSON 文件
                            logging.debug(f"分类结果: {categorized_results}")
                            self.save_results_to_json(
                                title=item['标题'],
                                year=item['年份'],
                                categorized_results=categorized_results
                            )
                            break  # 找到匹配的卡片后退出循环
                    except Exception as e:
                        logging.warning(f"解析影片信息时出错: {e}")
                if not found_match:
                    logging.info(f"未找到匹配的电影卡片: {item['标题']} ({item['年份']})")
            except TimeoutException:
                logging.error("搜索结果为空或加载超时")
            except Exception as e:
                logging.error(f"搜索过程中出错: {e}")

    def search_tvshow(self, search_url, all_tv_info):
        """搜索电视节目并保存索引"""
        # 关闭浏览器多余的标签页只保留当前标签页
        if len(self.driver.window_handles) > 1:
            for handle in self.driver.window_handles[1:]:
                self.driver.switch_to.window(handle)
                self.driver.close()
            self.driver.switch_to.window(self.driver.window_handles[0])
            logging.debug("关闭多余的标签页，只保留当前标签页")
    
        for item in all_tv_info:
            logging.info(f"开始搜索电视节目: {item['剧集']} 年份: {item['年份']} 季: {item['季']}")
            search_query = quote(item['剧集'])
            full_search_url = f"{search_url}{search_query}"
            try:
                self.driver.get(full_search_url)
                logging.debug(f"访问搜索URL: {full_search_url}")
            except Exception as e:
                logging.error(f"无法访问搜索页面: {full_search_url}，错误: {e}")
                logging.info("跳过当前搜索项，继续执行下一个媒体搜索")
                continue
            try:
                # 等待搜索结果加载
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "sr_lists"))
                )
                video_cards = self.driver.find_elements(By.CLASS_NAME, "v5d")
                logging.debug(f"找到 {len(video_cards)} 个视频卡片")
                found_match = False  # 标记是否找到匹配的卡片
                for card in video_cards:
                    try:
                        # 直接查找标题和年份信息
                        title_element = card.find_element(By.CLASS_NAME, "d16")
                        title_text = title_element.text  # 获取标题文本
    
                        # 提取标题和年份
                        match = re.match(r"(.+?)\s*\((\d{4})\)", title_text)
                        if match:
                            hover_title = match.group(1)  # 提取标题
                            hover_year = match.group(2)   # 提取年份
                            logging.debug(f"提取的标题: {hover_title}, 年份: {hover_year}")
                        else:
                            logging.warning(f"无法解析标题和年份: {title_text}")
                            continue
    
                        # 检查标题和年份是否与搜索匹配
                        if item['剧集'] == hover_title and str(item['年份']) == hover_year:
                            logging.info(f"找到匹配的电视节目卡片: {hover_title} ({hover_year})")
                            found_match = True  # 找到匹配的卡片
    
                            # 使用 ActionChains 模拟鼠标点击标题
                            actions = ActionChains(self.driver)
                            actions.move_to_element(title_element).click().perform()
                            logging.info("成功点击匹配的电视节目标题")
    
                            # 等待电视节目详细信息页面加载完成
                            try:
                                WebDriverWait(self.driver, 10).until(
                                    EC.presence_of_element_located((By.CLASS_NAME, "movie-introduce"))
                                )
                                logging.info("成功进入电视节目详细信息页面")
                                time.sleep(5)  # 等待页面稳定
                            except TimeoutException:
                                logging.error("电视节目详细信息页面加载超时")
                                continue
    
                            # 获取资源列表
                            resource_items = self.driver.find_elements(By.CSS_SELECTOR, "div.down-list > ul > li.down-list2")
                            logging.info(f"找到 {len(resource_items)} 个资源项")
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
    
                            filtered_resources = []  # 用于存储过滤后的资源项

                            # 获取配置中的首选和备选分辨率
                            preferred_resolution = self.config.get('preferred_resolution', "未知分辨率")
                            fallback_resolution = self.config.get('fallback_resolution', "未知分辨率")
                            exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')
    
                            for resource in resource_items:
                                try:
                                    # 提取资源标题
                                    try:
                                        resource_title = resource.find_element(By.CSS_SELECTOR, "p.down-list3 a").get_attribute("title")
                                    except Exception as e:
                                        logging.warning(f"无法找到资源标题元素: {e}")
                                        continue
    
                                    try:
                                        resource_link = resource.find_element(By.CSS_SELECTOR, "span a[target='_blank']").get_attribute("href")
                                    except Exception as e:
                                        logging.warning(f"无法找到资源链接元素: {e}")
                                        continue
    
                                    logging.debug(f"资源标题: {resource_title}, 链接: {resource_link}")
    
                                    # 检查是否包含排除关键词
                                    if any(keyword.strip() in resource_title for keyword in exclude_keywords):
                                        logging.debug(f"跳过包含排除关键词的资源: {resource_title}")
                                        continue
    
                                    # 添加到过滤后的资源列表
                                    filtered_resources.append(resource)

                                    # 提取详细信息
                                    try:
                                        details = self.extract_details_tvshow(resource_title)
                                    except Exception as e:
                                        logging.warning(f"解析资源详细信息时出错: {e}")
                                        continue
    
                                    resolution = details["resolution"]
                                    episode_type = details["episode_type"]
                                    logging.debug(f"解析出的分辨率: {resolution}, 类型: {episode_type}, 详细信息: {details}")
    
                                    # 如果集数类型为未知，跳过该结果
                                    if episode_type == "未知集数":
                                        logging.warning(f"跳过未知集数的资源: {resource_title}")
                                        continue

                                    # 根据分辨率和类型分类
                                    if resolution == preferred_resolution:
                                        categorized_results["首选分辨率"][episode_type].append({
                                            "title": resource_title,
                                            "link": resource_link,
                                            "resolution": resolution,
                                            "start_episode": details["start_episode"],
                                            "end_episode": details["end_episode"]
                                        })
                                    elif resolution == fallback_resolution:
                                        categorized_results["备选分辨率"][episode_type].append({
                                            "title": resource_title,
                                            "link": resource_link,
                                            "resolution": resolution,
                                            "start_episode": details["start_episode"],
                                            "end_episode": details["end_episode"]
                                        })
                                    else:
                                        categorized_results["其他分辨率"][episode_type].append({
                                            "title": resource_title,
                                            "link": resource_link,
                                            "resolution": resolution,
                                            "start_episode": details["start_episode"],
                                            "end_episode": details["end_episode"]
                                        })
                                except Exception as e:
                                    logging.warning(f"解析资源项时出错: {e}")
    
                            logging.info(f"过滤后剩余 {len(filtered_resources)} 个资源项")

                            # 保存结果到 JSON 文件
                            logging.debug(f"分类结果: {categorized_results}")
                            self.save_results_to_json(
                                title=item['剧集'],
                                year=item['年份'],
                                categorized_results=categorized_results,
                                season=item['季']
                            )
                            break  # 找到匹配的卡片后退出循环
                    except Exception as e:
                        logging.warning(f"解析节目信息时出错: {e}")
                if not found_match:
                    logging.info(f"未找到匹配的电视节目卡片: {item['剧集']} ({item['年份']})")
            except TimeoutException:
                logging.error("搜索结果为空或加载超时")
            except Exception as e:
                logging.error(f"搜索过程中出错: {e}")

    def save_results_to_json(self, title, year, categorized_results, season=None):
        """将结果保存到 JSON 文件"""
        # 根据是否有季信息来决定文件名格式
        if season:
            file_name = f"{title}-S{season}-{year}-GY.json"
        else:
            file_name = f"{title}-{year}-GY.json"
        
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

    def extract_details_movie(self, title):
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
    
    def extract_details_tvshow(self, title_text):
        """
        从标题中提取电视节目的详细信息，包括分辨率、集数范围等。
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
            # 如果标题中包含“全”字，则认为是全集资源
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
            # 匹配“更至XX集”或“更新至XX集”
            update_match = re.search(r"(更至|更新至)(\d{1,3})集", title_text)
            if update_match:
                start_episode = 1
                end_episode = int(update_match.group(2))
                episode_type = "集数范围"
    
        # 添加日志记录
        logging.debug(
            f"提取结果 - 标题: {title_text}, 分辨率: {resolution}, 开始集数: {start_episode}, "
            f"结束集数: {end_episode}, 集数类型: {episode_type}"
        )
    
        return {
            "resolution": resolution,
            "start_episode": start_episode,
            "end_episode": end_episode,
            "episode_type": episode_type
        }

    def run(self):
        # 加载配置文件
        self.load_config()

         # 新增：检查程序启用状态
        program_enabled = self.config.get("gy_enabled", False)
        # 支持字符串和布尔类型
        if isinstance(program_enabled, str):
            program_enabled = program_enabled.lower() == "true"
        if not program_enabled:
            logging.info("站点已被禁用，立即退出。")
            exit(0)

        # 获取订阅电影信息
        all_movie_info = self.extract_movie_info()

        # 获取订阅电视节目信息
        all_tv_info = self.extract_tv_info()

        # 检查数据库中是否有有效订阅
        if not all_movie_info and not all_tv_info:
            logging.info("数据库中没有有效订阅，无需执行后续操作")
            exit(0)  # 退出程序

        # 检查配置中的用户名和密码是否有效
        username = self.config.get("gy_login_username", "")
        password = self.config.get("gy_login_password", "")
        if username == "username" and password == "password":
            logging.error("用户名和密码为系统默认值，程序将不会继续运行，请在系统设置中配置有效的用户名和密码！")
            exit(0)

        # 初始化WebDriver
        self.setup_webdriver()

        # 获取基础 URL
        gy_base_url = self.config.get("gy_base_url", "")
        search_url = f"{gy_base_url}/s/1---1/"
        self.gy_login_url = f"{gy_base_url}/user/login"
        self.gy_user_info_url = f"{gy_base_url}/user/account"

        # 检查登录状态
        self.login_gy_site(self.config["gy_login_username"], self.config["gy_login_password"])

        # 搜索电影和建立索引
        if all_movie_info:
            self.search_movie(search_url, all_movie_info)

        # 搜索电视节目和建立索引
        if all_tv_info:
            self.search_tvshow(search_url, all_tv_info)

        # 清理工作，关闭浏览器
        self.driver.quit()
        logging.info("WebDriver关闭完成")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="媒体索引器")
    parser.add_argument("--manual", action="store_true", help="手动搜索模式")
    parser.add_argument("--type", type=str, choices=["movie", "tv"], help="搜索类型：movie 或 tv")
    parser.add_argument("--title", type=str, help="媒体标题")
    parser.add_argument("--year", type=int, help="媒体年份")
    parser.add_argument("--season", type=int, help="电视节目的季（可选，仅适用于电视节目）")
    parser.add_argument("--episodes", type=str, help="缺失的集数（可选，仅适用于电视节目），格式如：1,2,3")
    args = parser.parse_args()

    indexer = MediaIndexer()

    if args.manual:
        # 加载配置文件
        indexer.load_config()

        # 新增：检查程序启用状态
        program_enabled = indexer.config.get("gy_enabled", False)
        # 支持字符串和布尔类型
        if isinstance(program_enabled, str):
            program_enabled = program_enabled.lower() == "true"
        if not program_enabled:
            logging.info("站点已被禁用，立即退出。")
            exit(0)

        # 检查配置中的用户名和密码是否有效
        username = indexer.config.get("gy_login_username", "")
        password = indexer.config.get("gy_login_password", "")
        if username == "username" and password == "password":
            logging.error("用户名和密码为系统默认值，程序将不会继续运行，请在系统设置中配置有效的用户名和密码！")
            exit(0)

        # 初始化 WebDriver
        indexer.setup_webdriver()
    
        # 获取基础 URL
        gy_base_url = indexer.config.get("gy_base_url", "")
        search_url = f"{gy_base_url}/s/1---1/"
        indexer.gy_login_url = f"{gy_base_url}/user/login"
        indexer.gy_user_info_url = f"{gy_base_url}/user/account"

        # 检查登录状态
        indexer.login_gy_site(indexer.config["gy_login_username"], indexer.config["gy_login_password"])
    
        # 执行手动搜索
        if args.type == "movie":
            if args.title:
                # 确保年份为字符串类型
                movie_info = [{"标题": args.title, "年份": str(args.year)}]
                indexer.search_movie(search_url, movie_info)
            else:
                logging.error("手动搜索电影模式需要提供 --title 参数")
        elif args.type == "tv":
            if args.title:
                # 确保缺失集数为列表类型
                tv_info = [{
                    "剧集": args.title,
                    "年份": str(args.year),
                    "季": str(args.season) if args.season else "",
                    "缺失集数": [ep.strip() for ep in args.episodes.split(",")] if args.episodes else []
                }]
                indexer.search_tvshow(search_url, tv_info)
            else:
                logging.error("手动搜索电视节目模式需要提供 --title 参数")
        else:
            logging.error("手动搜索模式需要指定 --type 参数为 movie 或 tv")
    
        # 清理工作，关闭浏览器
        indexer.driver.quit()
        logging.info("WebDriver关闭完成")
    else:
        # 自动模式
        indexer.run()