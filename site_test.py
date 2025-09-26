# site_test.py
import sqlite3
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
import time

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/site_test.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

class SiteTester:
    def __init__(self, db_path='/config/data.db'):
        self.db_path = db_path
        self.driver = None
        self.sites = {}
        
    def setup_webdriver(self, instance_id=12):
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
        # 设置用户配置文件缓存目录，使用固定instance-id 12作为该程序特有的id
        user_data_dir = f'/app/ChromeCache/user-data-dir-inst-{instance_id}'
        options.add_argument(f'--user-data-dir={user_data_dir}')
        # 设置磁盘缓存目录，同样使用instance-id区分
        disk_cache_dir = f"/app/ChromeCache/disk-cache-dir-inst-{instance_id}"
        options.add_argument(f"--disk-cache-dir={disk_cache_dir}")
        
        prefs = {
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
            
    # 高清影视、高清剧集、不太灵 站点滑动验证、图片验证码处理
    def site_captcha(self, url):
        max_verification_attempts = 3  # 最大验证尝试次数
        verification_attempt = 0
        
        while verification_attempt < max_verification_attempts:
            verification_attempt += 1
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
                    elif captcha_prompt.text in ["请输入上面的验证码", "Please enter the verification code above"]:
                        logging.info("检测到图片验证码，开始识别")
                        
                        # 引入必要的库
                        try:
                            import requests
                            import base64
                            import re
                            from io import BytesIO
                        except ImportError as e:
                            logging.error(f"缺少必要的库，请安装: {e}")
                            raise
                        
                        max_retries = 10  # 最大重试次数
                        retry_count = 0
                        
                        while retry_count < max_retries:
                            retry_count += 1
                            logging.info(f"验证码识别尝试 {retry_count}/{max_retries}")
                            
                            # 等待验证码图片加载
                            captcha_img = WebDriverWait(self.driver, 10).until(
                                EC.presence_of_element_located((By.ID, "ui-captcha-image"))
                            )
                            
                            try:
                                # 使用Selenium截图方式直接获取验证码图片数据，不保存到本地
                                # 对验证码元素进行截图，直接获取图片的字节数据
                                screenshot_data = captcha_img.screenshot_as_png
                                logging.info("通过selenium截图方式获取验证码图片数据")
                                
                                # 将截图数据转换为base64编码
                                image_base64 = base64.b64encode(screenshot_data).decode('utf-8')
                                
                            except Exception as e:
                                logging.error(f"获取验证码图片失败: {e}")
                                raise
                            
                            # 使用OCR.space API进行验证码识别
                            try:
                                # 构造API请求
                                api_key = self.ocr_api_key
                                payload = {
                                    'base64Image': f'data:image/png;base64,{image_base64}',
                                    'apikey': api_key,
                                    'language': 'eng',
                                    'OCREngine': 2  # 指定使用OCR引擎2
                                }
                                
                                # 发送API请求
                                ocr_response = requests.post('https://api.ocr.space/parse/image', data=payload)
                                
                                captcha_code = None
                                if ocr_response.status_code == 200:
                                    result = ocr_response.json()
                                    if result['IsErroredOnProcessing']:
                                        logging.warning(f"OCR处理错误: {result['ErrorMessage']}")
                                    else:
                                        parsed_results = result['ParsedResults']
                                        if parsed_results:
                                            captcha_text = parsed_results[0]['ParsedText']
                                            # 使用正则表达式提取6位数字验证码
                                            match = re.search(r'\d{6}', captcha_text)
                                            if match:
                                                captcha_code = match.group()
                                                logging.info(f"识别出的验证码: {captcha_code}")
                                                break  # 成功识别6位数字验证码，跳出循环
                                            else:
                                                logging.warning(f"识别的文本不是6位数字: {captcha_text}")
                                        else:
                                            logging.warning("OCR没有返回解析结果")
                                else:
                                    logging.warning(f"OCR API请求失败，状态码: {ocr_response.status_code}")
                                    
                            except Exception as e:
                                logging.error(f"OCR识别失败: {e}")
                            
                            # 如果没有成功识别，刷新页面获取新的验证码
                            if retry_count < max_retries:
                                logging.info("刷新页面以获取新的验证码")
                                self.driver.refresh()
                                # 等待页面加载完成
                                time.sleep(2)
                                # 重新等待验证码提示出现
                                captcha_prompt = WebDriverWait(self.driver, 10).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, "p.ui-prompt"))
                                )
                        
                        # 如果超过最大重试次数仍未成功识别
                        if retry_count >= max_retries:
                            raise Exception("超过最大重试次数，未能识别出有效的6位数字验证码")
                        
                        # 输入验证码
                        try:
                            captcha_input = self.driver.find_element(By.ID, "GOEDGE_WAF_CAPTCHA_CODE")
                            captcha_input.send_keys(captcha_code)
                            
                            # 提交表单
                            submit_button = self.driver.find_element(By.CSS_SELECTOR, "button[type='submit']")
                            submit_button.click()
                            
                            # 等待页面跳转完成
                            WebDriverWait(self.driver, 30).until(
                                EC.url_changes(url)
                            )
                            
                            logging.info("验证码已提交，页面跳转成功")
                            
                            # 验证是否成功跳转到目标页面
                            # 检查页面源码中是否有"影视"或"剧集"关键词
                            time.sleep(5)  # 等待页面加载完成
                            page_source = self.driver.page_source
                            if "影视" not in page_source and "剧集" not in page_source:
                                logging.warning("验证可能失败，页面中未找到'影视'或'剧集'关键词")
                                # 如果不是最后一次尝试，继续下一次验证尝试
                                if verification_attempt < max_verification_attempts:
                                    logging.info(f"第 {verification_attempt} 次验证失败，准备进行第 {verification_attempt + 1} 次验证尝试")
                                    continue
                                else:
                                    # 重新检查验证码类型
                                    raise Exception("验证失败，已达到最大验证尝试次数")
                            else:
                                logging.info("验证成功，页面中包含'影视'或'剧集'关键词")
                                return  # 验证成功，退出方法
                                
                        except Exception as e:
                            logging.error(f"提交验证码时出错: {e}")
                            raise
                    
                    else:
                        logging.info("未检测到滑动验证码")
                except TimeoutException:
                    logging.info("未检测到滑动验证码")
                    # 如果没有验证码提示，检查页面是否已经加载成功
                    page_source = self.driver.page_source
                    if "影视" not in page_source and "剧集" not in page_source:
                        logging.warning("页面中未找到'影视'或'剧集'关键词，可能需要重新验证")
                        # 如果不是最后一次尝试，继续下一次验证尝试
                        if verification_attempt < max_verification_attempts:
                            logging.info(f"第 {verification_attempt} 次验证失败，准备进行第 {verification_attempt + 1} 次验证尝试")
                            continue
                        else:
                            raise Exception("验证失败，已达到最大验证尝试次数")
                    else:
                        logging.info("页面加载成功，包含'影视'或'剧集'关键词")
                        return  # 验证成功，退出方法
                        
            except Exception as e:
                logging.error(f"访问站点时出错: {e}")
                # 如果不是最后一次尝试，继续下一次验证尝试
                if verification_attempt < max_verification_attempts:
                    logging.info(f"第 {verification_attempt} 次验证失败，准备进行第 {verification_attempt + 1} 次验证尝试")
                    continue
                else:
                    logging.info("已达到最大验证尝试次数，程序将正常退出")
                    self.driver.quit()
                    exit(1)
        
        # 如果所有验证尝试都失败了
        logging.error("已达到最大验证尝试次数，验证失败")
        self.driver.quit()
        exit(1)

    # 观影站点验证码处理
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
    
        # 无论是否检测到验证码，都检查是否有提示框并点击"不再提醒"按钮
        try:
            popup_close_button = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.popup-footer button"))
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(popup_close_button).click().perform()
            logging.info("成功点击'不再提醒'按钮")
        except TimeoutException:
            logging.info("未检测到提示框，无需操作")

    def load_sites_config(self):
        """从数据库加载站点配置"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 读取站点配置
                sites_config = {
                    "BTHD": {
                        "url": "bt_movie_base_url",
                        "keyword": "高清影视"
                    },
                    "HDTV": {
                        "url": "bt_tv_base_url",
                        "keyword": "高清剧集"
                    },
                    "BT0": {
                        "url": "bt0_base_url",
                        "keyword": "影视"
                    },
                    "BTYS": {
                        "url": "btys_base_url",
                        "keyword": "BT影视"
                    },
                    "GY": {
                        "url": "gy_base_url",
                        "keyword": "观影"
                    }
                }
                
                # 查询配置值
                for site_name, config in sites_config.items():
                    cursor.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', (config["url"],))
                    result = cursor.fetchone()
                    if result and result[0]:
                        self.sites[site_name] = {
                            "base_url": result[0],
                            "keyword": config["keyword"]
                        }
                    else:
                        logging.warning(f"未找到站点 {site_name} 的配置")
                
                # 读取OCR API密钥
                cursor.execute('SELECT VALUE FROM CONFIG WHERE OPTION = ?', ('ocr_api_key',))
                result = cursor.fetchone()
                if result and result[0]:
                    self.ocr_api_key = result[0]
                    logging.info("OCR API密钥加载成功")
                else:
                    logging.warning("未找到OCR API密钥配置！")
                    
            logging.info(f"加载了 {len(self.sites)} 个站点配置")
            return self.sites
            
        except sqlite3.Error as e:
            logging.error(f"数据库加载配置错误: {e}")
            return {}

    def test_site(self, site_name, site_config):
        """测试单个站点"""
        base_url = site_config["base_url"]
        keyword = site_config["keyword"]
        
        try:
            logging.info(f"开始测试站点 {site_name}: {base_url}")
            
            # 根据站点类型处理验证码
            if site_name in ["BTHD", "HDTV", "BT0", "BTYS"]:
                self.site_captcha(base_url)
            elif site_name == "GY":
                self.gy_site_captcha(base_url)
            else:
                # 默认直接访问
                self.driver.get(base_url)
            
            # 等待页面加载完成
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # 获取页面源码
            page_source = self.driver.page_source.lower()
            keyword_lower = keyword.lower()
            
            # 检查是否包含关键词
            if keyword_lower in page_source:
                logging.info(f"站点 {site_name} 访问正常且包含关键词 '{keyword}'")
                return True
            else:
                logging.warning(f"站点 {site_name} 可以访问但不包含关键词 '{keyword}'")
                return False
                
        except TimeoutException:
            logging.error(f"站点 {site_name} 访问超时")
            return False
        except WebDriverException as e:
            logging.error(f"站点 {site_name} WebDriver错误: {e}")
            return False
        except Exception as e:
            logging.error(f"站点 {site_name} 测试过程中发生未知错误: {e}")
            return False

    def run_tests(self):
        """运行所有站点测试"""
        results = {}
        
        try:
            # 初始化WebDriver
            self.setup_webdriver()
            
            # 加载站点配置
            self.load_sites_config()
            
            if not self.sites:
                logging.error("未加载到任何站点配置")
                return results
                
            # 测试每个站点
            for site_name, site_config in self.sites.items():
                try:
                    result = self.test_site(site_name, site_config)
                    results[site_name] = result
                    # 添加短暂延迟避免请求过于频繁
                    time.sleep(2)
                except Exception as e:
                    logging.error(f"测试站点 {site_name} 时发生错误: {e}")
                    results[site_name] = False
                    
        except Exception as e:
            logging.error(f"运行测试时发生错误: {e}")
        finally:
            self.close_driver()
            
        return results

    def close_driver(self):
        """关闭WebDriver"""
        if self.driver:
            try:
                self.driver.quit()
                logging.info("WebDriver关闭完成")
            except Exception as e:
                logging.error(f"关闭WebDriver时发生错误: {e}")
            finally:
                self.driver = None

    def print_results(self, results):
        """打印测试结果"""
        logging.info("\n=== 站点检测结果 ===")
        for site_name, status in results.items():
            status_text = "正常" if status else "异常"
            logging.info(f"{site_name}: {status_text}")
            
        normal_sites = [site for site, status in results.items() if status]
        abnormal_sites = [site for site, status in results.items() if not status]
        
        logging.info(f"\n正常站点 ({len(normal_sites)}个): {', '.join(normal_sites) if normal_sites else '无'}")
        logging.info(f"异常站点 ({len(abnormal_sites)}个): {', '.join(abnormal_sites) if abnormal_sites else '无'}")

def main():
    tester = SiteTester()
    results = tester.run_tests()
    tester.print_results(results)
    
    # 返回异常站点数量，可用于脚本退出码
    abnormal_count = len([status for status in results.values() if not status])
    return abnormal_count

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)