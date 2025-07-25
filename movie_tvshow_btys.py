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
from urllib.parse import quote

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/movie_tvshow_btys.log", mode='w'),  # 输出到文件
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
            logging.info("由于访问失败，程序将正常退出")
            self.driver.quit()
            exit(1)

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
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "module-items"))
                )
                video_cards = self.driver.find_elements(By.CLASS_NAME, "module-item")
                logging.debug(f"找到 {len(video_cards)} 个视频卡片")
                found_match = False  # 标记是否找到匹配的卡片
                for card in video_cards:
                    try:
                        # 直接提取标题和年份
                        title_elem = card.find_element(By.CSS_SELECTOR, ".module-item-titlebox .module-item-title")
                        card_title = title_elem.get_attribute("title").strip()
                        year_elem = card.find_element(By.CSS_SELECTOR, ".module-item-caption span")
                        card_year = year_elem.text.strip()

                        logging.debug(f"卡片提取的标题: {card_title}, 年份: {card_year}")

                        # 检查标题和年份是否与搜索匹配
                        if item['标题'] in card_title and str(item['年份']) == card_year:
                            logging.info(f"找到匹配的电影卡片: {card_title} ({card_year})")
                            found_match = True  # 找到匹配的卡片

                            # 点击进入详情页
                            detail_link = title_elem.get_attribute("href")
                            self.driver.execute_script("window.open(arguments[0]);", detail_link)
                            WebDriverWait(self.driver, 15).until(
                                lambda d: len(d.window_handles) > 1
                            )
                            self.driver.switch_to.window(self.driver.window_handles[-1])
                            logging.debug("切换到新标签页")

                            # 等待影片详细信息页面加载完成
                            try:
                                WebDriverWait(self.driver, 15).until(
                                    EC.presence_of_element_located((By.ID, "download-list"))
                                )
                                logging.info("成功进入影片详细信息页面")
                            except TimeoutException:
                                logging.error("影片详细信息页面加载超时")
                                self.driver.close()  # 关闭新标签页
                                self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                                continue

                            # 检查资源类型标签
                            try:
                                category_element = self.driver.find_element(By.CSS_SELECTOR, ".video-tag-icon")
                                category_text = category_element.text.strip()
                                if category_text != "Movie":
                                    logging.info(f"资源类型不匹配，跳过采集: {category_text}")
                                    self.driver.close()
                                    self.driver.switch_to.window(self.driver.window_handles[0])
                                    continue
                            except Exception as e:
                                logging.warning("无法找到资源类型标签，默认继续采集")

                            # 新版资源采集逻辑
                            categorized_results = {
                                "首选分辨率": [],
                                "备选分辨率": [],
                                "其他分辨率": []
                            }
                            preferred_resolution = self.config.get('preferred_resolution', "未知分辨率")
                            fallback_resolution = self.config.get('fallback_resolution', "未知分辨率")
                            exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')

                            # 获取所有分辨率tab，排除“夸克网盘”
                            tab_items = self.driver.find_elements(By.CSS_SELECTOR, ".module-tab-item.downtab-item")
                            valid_tab_indices = []
                            for idx, tab in enumerate(tab_items):
                                label = tab.find_element(By.TAG_NAME, "span").get_attribute("data-dropdown-value")
                                if label and "夸克网盘" not in label:
                                    valid_tab_indices.append(idx)

                            all_resources = []
                            for idx in valid_tab_indices:
                                # 切换到对应分辨率tab
                                tab_items = self.driver.find_elements(By.CSS_SELECTOR, ".module-tab-item.downtab-item")
                                self.driver.execute_script("arguments[0].click();", tab_items[idx])
                                WebDriverWait(self.driver, 5).until(
                                    lambda d: d.find_elements(By.CSS_SELECTOR, ".module-list.module-downlist.selected .module-row-info")
                                )
                                # 获取当前tab下所有资源项
                                resource_infos = self.driver.find_elements(By.CSS_SELECTOR, ".module-list.module-downlist.selected .module-row-info")
                                for info in resource_infos:
                                    try:
                                        a_tag = info.find_element(By.CSS_SELECTOR, "a.module-row-text.copy")
                                        resource_title = a_tag.get_attribute("title")
                                        resource_link = a_tag.get_attribute("href")
                                        # 检查是否包含排除关键词
                                        if any(keyword.strip() in resource_title for keyword in exclude_keywords):
                                            logging.debug(f"跳过包含排除关键词的资源: {resource_title}")
                                            continue
                                        all_resources.append({
                                            "title": resource_title,
                                            "link": resource_link
                                        })
                                    except Exception as e:
                                        logging.warning(f"解析资源项时出错: {e}")

                            logging.info(f"过滤后剩余 {len(all_resources)} 个资源项")

                            # 分类资源
                            for res in all_resources:
                                details = self.extract_details_movie(res["title"])
                                resolution = details["resolution"]
                                if resolution == preferred_resolution:
                                    categorized_results["首选分辨率"].append({
                                        "title": res["title"],
                                        "link": res["link"],
                                        "resolution": resolution,
                                        "audio_tracks": details["audio_tracks"],
                                        "subtitles": details["subtitles"]
                                    })
                                elif resolution == fallback_resolution:
                                    categorized_results["备选分辨率"].append({
                                        "title": res["title"],
                                        "link": res["link"],
                                        "resolution": resolution,
                                        "audio_tracks": details["audio_tracks"],
                                        "subtitles": details["subtitles"]
                                    })
                                else:
                                    categorized_results["其他分辨率"].append({
                                        "title": res["title"],
                                        "link": res["link"],
                                        "resolution": resolution,
                                        "audio_tracks": details["audio_tracks"],
                                        "subtitles": details["subtitles"]
                                    })

                            # 保存结果到 JSON 文件
                            logging.debug(f"分类结果: {categorized_results}")
                            self.save_results_to_json(
                                title=item['标题'],
                                year=item['年份'],
                                categorized_results=categorized_results
                            )

                            # 关闭新标签页并切回原标签页
                            self.driver.close()
                            self.driver.switch_to.window(self.driver.window_handles[0])
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
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "module-items"))
                )
                video_cards = self.driver.find_elements(By.CLASS_NAME, "module-item")
                logging.debug(f"找到 {len(video_cards)} 个视频卡片")
                found_match = False  # 标记是否找到匹配的卡片
                for card in video_cards:
                    try:
                        # 直接提取标题和年份
                        title_elem = card.find_element(By.CSS_SELECTOR, ".module-item-titlebox .module-item-title")
                        card_title = title_elem.get_attribute("title").strip()
                        year_elem = card.find_element(By.CSS_SELECTOR, ".module-item-caption span")
                        card_year = year_elem.text.strip()

                        logging.debug(f"卡片提取的标题: {card_title}, 年份: {card_year}")

                        # 检查标题和年份是否与搜索匹配
                        if item['剧集'] in card_title and str(item['年份']) == card_year:
                            logging.info(f"找到匹配的电视节目卡片: {card_title} ({card_year})")
                            found_match = True  # 找到匹配的卡片

                            # 点击进入详情页
                            detail_link = title_elem.get_attribute("href")
                            self.driver.execute_script("window.open(arguments[0]);", detail_link)
                            WebDriverWait(self.driver, 15).until(
                                lambda d: len(d.window_handles) > 1
                            )
                            self.driver.switch_to.window(self.driver.window_handles[-1])
                            logging.debug("切换到新标签页")

                            # 等待节目详细信息页面加载完成
                            try:
                                WebDriverWait(self.driver, 15).until(
                                    EC.presence_of_element_located((By.ID, "download-list"))
                                )
                                logging.info("成功进入节目详细信息页面")
                            except TimeoutException:
                                logging.error("节目详细信息页面加载超时")
                                self.driver.close()  # 关闭新标签页
                                self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                                continue

                            # 检查资源类型标签
                            try:
                                category_element = self.driver.find_element(By.CSS_SELECTOR, ".video-tag-icon")
                                category_text = category_element.text.strip()
                                if category_text != "TV":
                                    logging.info(f"资源类型不匹配，跳过采集: {category_text}")
                                    self.driver.close()
                                    self.driver.switch_to.window(self.driver.window_handles[0])
                                    continue
                            except Exception as e:
                                logging.warning("无法找到资源类型标签，默认继续采集")

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
                            preferred_resolution = self.config.get('preferred_resolution', "未知分辨率")
                            fallback_resolution = self.config.get('fallback_resolution', "未知分辨率")
                            exclude_keywords = self.config.get("resources_exclude_keywords", "").split(',')

                            # 获取所有分辨率tab，排除“夸克网盘”
                            tab_items = self.driver.find_elements(By.CSS_SELECTOR, ".module-tab-item.downtab-item")
                            valid_tab_indices = []
                            for idx, tab in enumerate(tab_items):
                                label = tab.find_element(By.TAG_NAME, "span").get_attribute("data-dropdown-value")
                                if label and "夸克网盘" not in label:
                                    valid_tab_indices.append(idx)

                            all_resources = []
                            for idx in valid_tab_indices:
                                # 切换到对应分辨率tab
                                tab_items = self.driver.find_elements(By.CSS_SELECTOR, ".module-tab-item.downtab-item")
                                self.driver.execute_script("arguments[0].click();", tab_items[idx])
                                WebDriverWait(self.driver, 5).until(
                                    lambda d: d.find_elements(By.CSS_SELECTOR, ".module-list.module-downlist.selected .module-row-info")
                                )
                                # 获取当前tab下所有资源项
                                resource_infos = self.driver.find_elements(By.CSS_SELECTOR, ".module-list.module-downlist.selected .module-row-info")
                                for info in resource_infos:
                                    try:
                                        a_tag = info.find_element(By.CSS_SELECTOR, "a.module-row-text.copy")
                                        resource_title = a_tag.get_attribute("title")
                                        resource_link = a_tag.get_attribute("href")
                                        # 检查是否包含排除关键词
                                        if any(keyword.strip() in resource_title for keyword in exclude_keywords):
                                            logging.debug(f"跳过包含排除关键词的资源: {resource_title}")
                                            continue
                                        all_resources.append({
                                            "title": resource_title,
                                            "link": resource_link
                                        })
                                    except Exception as e:
                                        logging.warning(f"解析资源项时出错: {e}")

                            logging.info(f"过滤后剩余 {len(all_resources)} 个资源项")

                            # 分类资源
                            for res in all_resources:
                                details = self.extract_details_tvshow(res["title"])
                                resolution = details["resolution"]
                                episode_type = details["episode_type"]
                                if episode_type == "未知集数":
                                    logging.warning(f"跳过未知集数的资源: {res['title']}")
                                    continue
                                if resolution == preferred_resolution:
                                    categorized_results["首选分辨率"][episode_type].append({
                                        "title": res["title"],
                                        "link": res["link"],
                                        "resolution": resolution,
                                        "start_episode": details["start_episode"],
                                        "end_episode": details["end_episode"]
                                    })
                                elif resolution == fallback_resolution:
                                    categorized_results["备选分辨率"][episode_type].append({
                                        "title": res["title"],
                                        "link": res["link"],
                                        "resolution": resolution,
                                        "start_episode": details["start_episode"],
                                        "end_episode": details["end_episode"]
                                    })
                                else:
                                    categorized_results["其他分辨率"][episode_type].append({
                                        "title": res["title"],
                                        "link": res["link"],
                                        "resolution": resolution,
                                        "start_episode": details["start_episode"],
                                        "end_episode": details["end_episode"]
                                    })

                            # 保存结果到 JSON 文件
                            logging.debug(f"分类结果: {categorized_results}")
                            self.save_results_to_json(
                                title=item['剧集'],
                                year=item['年份'],
                                categorized_results=categorized_results,
                                season=item['季']
                            )

                            # 关闭新标签页并切回原标签页
                            self.driver.close()
                            self.driver.switch_to.window(self.driver.window_handles[0])
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
            file_name = f"{title}-S{season}-{year}-BTYS.json"
        else:
            file_name = f"{title}-{year}-BTYS.json"
        
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
        program_enabled = self.config.get("btys_enabled", False)
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

        # 初始化WebDriver
        self.setup_webdriver()

        # 获取基础 URL
        btys_base_url = self.config.get("btys_base_url", "")
        search_url = f"{btys_base_url}/search/"

        # 检查滑动验证码
        self.site_captcha(btys_base_url)

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
        program_enabled = indexer.config.get("btys_enabled", False)
        # 支持字符串和布尔类型
        if isinstance(program_enabled, str):
            program_enabled = program_enabled.lower() == "true"
        if not program_enabled:
            logging.info("站点已被禁用，立即退出。")
            exit(0)

        # 初始化 WebDriver
        indexer.setup_webdriver()
    
        # 获取基础 URL
        btys_base_url = indexer.config.get("btys_base_url", "")
        search_url = f"{btys_base_url}/search/"
    
        # 检查滑动验证码
        indexer.site_captcha(btys_base_url)
    
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