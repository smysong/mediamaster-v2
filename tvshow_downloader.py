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
        logging.FileHandler("/tmp/log/tvshow_downloader.log", mode='w'),  # 输出到文件
        logging.StreamHandler()  # 输出到控制台
    ]
)

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
            
            logging.info("加载配置文件成功")
            return self.config
        except sqlite3.Error as e:
            logging.error(f"数据库加载配置错误: {e}")
            exit(1)

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

    def extract_tv_info(self):
        """从数据库读取缺失的电视节目信息和缺失的集数信息"""
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
        
        logging.info("读取缺失的电视节目信息和缺失的集数信息完成")
        return all_tv_info

    def search(self, search_url, all_tv_info):
        # 搜索剧集
        for item in all_tv_info:
            logging.info(f"开始搜索剧集: {item['剧集']}  年份: {item['年份']}  季: {item['季']}")
            search_query = f"{item['剧集']} {item['年份']}"
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
                    if item['年份'] not in result['title']:
                        continue
                    
                    # 检查是否包含排除关键词
                    exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')
                    if any(keyword.strip() in result['title'] for keyword in exclude_keywords):
                        continue
                    
                    filtered_results.append(result)

                # 获取首选分辨率和备选分辨率
                preferred_resolution = self.config.get('preferred_resolution', "未知分辨率")
                fallback_resolution = self.config.get('fallback_resolution', "未知分辨率")

                # 按分辨率和集数范围分类搜索结果
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
                    start_episode = details['start_episode']
                    end_episode = details['end_episode']
                    episode_type = details.get('episode_type', "未知集数")
                    
                    if episode_type == "单集":
                        episode_type = "单集"
                    elif episode_type == "集数范围":
                        episode_type = "集数范围"
                    elif episode_type == "全集":
                        episode_type = "全集"
                    else:
                        episode_type = "未知集数"
                    
                    if resolution == preferred_resolution:
                        categorized_results["首选分辨率"][episode_type].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "start_episode": details['start_episode'],
                            "end_episode": details['end_episode']
                        })
                    elif resolution == fallback_resolution:
                        categorized_results["备选分辨率"][episode_type].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "start_episode": details['start_episode'],
                            "end_episode": details['end_episode']
                        })
                    else:
                        categorized_results["其他分辨率"][episode_type].append({
                            "title": result['title'],
                            "link": result['link'],
                            "resolution": details['resolution'],
                            "start_episode": details['start_episode'],
                            "end_episode": details['end_episode']
                        })

                # 匹配缺失的集数
                missing_episodes = item['缺失集数']
                matched_results = self.match_missing_episodes(categorized_results, missing_episodes)
                
                # 下载种子文件
                for result in matched_results:
                    title_text = result['title']
                    resolution = result['resolution']
                    self.download_torrent(result, item, title_text, resolution)

            except TimeoutException:
                logging.error("搜索结果加载超时")
            except Exception as e:
                logging.error(f"搜索过程中出错: {e}")

    def extract_details(self, title_text):
        # 处理搜索结果
        title_text = str(title_text)
        
        # 提取分辨率
        resolution_match = re.search(r'(\d+p)', title_text)
        resolution = resolution_match.group(1) if resolution_match else "未知分辨率"
        
        # 初始化集数范围
        start_episode = "未知集数"
        end_episode = "未知集数"
        episode_type = "未知集数"
        
        # 处理单集或集数范围格式
        episode_pattern = r"\[第(\d{1,3})(?:-(\d{1,3}))?集\]"
        episode_match = re.search(episode_pattern, title_text)
        
        if episode_match:
            start_episode = episode_match.group(1)
            end_episode = episode_match.group(2) or start_episode
            episode_type = "单集" if start_episode == end_episode else "集数范围"
        elif "全" in title_text:
            # 如果标题中包含“全”字，则认为是全集资源
            full_episode_pattern = r"全(\d{1,3})"
            full_episode_match = re.search(full_episode_pattern, title_text)
            
            if full_episode_match:
                start_episode = "1"
                end_episode = full_episode_match.group(1)
                episode_type = "全集"
            else:
                start_episode = "1"
                end_episode = "所有"
                episode_type = "全集"
        
        # 确保 start_episode 和 end_episode 是整数
        if start_episode != "未知集数":
            start_episode = int(start_episode)
        if end_episode != "未知集数":
            end_episode = int(end_episode)
        
        # 添加日志记录
        logging.debug(f"提取结果 - 标题: {title_text}, 分辨率: {resolution}, 开始集数: {start_episode}, 结束集数: {end_episode}, 集数类型: {episode_type}")
        
        return {
            "resolution": resolution,
            "start_episode": start_episode,
            "end_episode": end_episode,
            "episode_type": episode_type
        }
    
    def match_missing_episodes(self, categorized_results, missing_episodes):
        """匹配缺失的集数"""
        matched_results = []

        # 将缺失集数转换为整数列表并排序
        remaining_episodes = sorted(set(int(ep) for ep in missing_episodes))
        logging.debug(f"剩余集数：{remaining_episodes}")

        # 检查首选分辨率结果
        preferred_results = categorized_results["首选分辨率"]
        matched_results.extend(self.match_episodes(preferred_results, remaining_episodes))

        # 如果首选分辨率匹配成功且没有剩余集数，则跳过备选分辨率和其他分辨率
        if not remaining_episodes:
            return matched_results

        # 检查备选分辨率结果
        fallback_results = categorized_results["备选分辨率"]
        matched_results.extend(self.match_episodes(fallback_results, remaining_episodes))

        # 如果备选分辨率匹配成功且没有剩余集数，则跳过其他分辨率
        if not remaining_episodes:
            return matched_results

        # 检查其他分辨率结果
        other_results = categorized_results["其他分辨率"]
        matched_results.extend(self.match_episodes(other_results, remaining_episodes))

        return matched_results

    def match_episodes(self, results, remaining_episodes):
        matched_results = []

        # 先处理全集结果
        if results["全集"]:
            result = results["全集"][0]
            logging.info(f"找到全集匹配: {result['title']}")
            matched_results.append(result)
            remaining_episodes.clear()  # 清空剩余集数
            return matched_results

        # 处理集数范围结果
        for result in results["集数范围"]:
            start_episode = int(result["start_episode"])
            end_episode = int(result["end_episode"])
            matched_episodes = set(range(start_episode, end_episode + 1))

            # 记录日志
            logging.debug(f"尝试匹配集数范围: {result['title']} (范围: {start_episode}-{end_episode})")

            if matched_episodes.intersection(remaining_episodes):
                logging.info(f"找到集数范围匹配: {result['title']}")
                matched_results.append(result)
                # 使用列表操作更新 remaining_episodes
                remaining_episodes[:] = [ep for ep in remaining_episodes if ep not in matched_episodes]
                if not remaining_episodes:
                    return matched_results

        # 处理单集结果
        for episode in sorted(remaining_episodes.copy()):  # 使用副本避免修改集合
            for result in results["单集"]:
                if result["start_episode"] == episode:
                    logging.info(f"找到单集匹配: {result['title']}")
                    matched_results.append(result)
                    remaining_episodes.remove(episode)
                    if not remaining_episodes:
                        return matched_results
                    break  # 找到匹配后跳出循环，继续处理下一个集数

        return matched_results

    def download_torrent(self, result, item, title_text, resolution):
        """解析并下载种子文件"""
        try:
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")
            # 等待种子文件下载链接加载
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
            download_link = self.driver.find_element(By.PARTIAL_LINK_TEXT, "torrent")
            download_url = download_link.get_attribute('href')

            # 请求下载链接
            self.driver.get(download_url)
            logging.info("开始下载种子文件...")

            # 等待下载完成（这里假设下载完成后会有一个特定的文件名）
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
        self.search(search_url, all_tv_info)

        # 清理工作，关闭浏览器
        self.driver.quit()
        logging.info("WebDriver关闭完成")

if __name__ == "__main__":
    downloader = TVDownloader()
    downloader.run()