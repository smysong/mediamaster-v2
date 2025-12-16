import json
import datetime
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
import concurrent.futures

# 导入新的验证码处理模块
from captcha_handler import CaptchaHandler

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
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
        self.captcha_handler = None
        
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
        # 设置页面加载策略为急切模式
        options.page_load_strategy = 'eager'
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
            
    def load_sites_config(self):
        """从数据库加载站点配置"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # 读取站点配置
                sites_config = {
                    "BTHD": {
                        "url": "bt_movie_base_url",
                        "keyword": "高清影视",
                        "use_login_url": True  # 标记使用登录URL
                    },
                    "HDTV": {
                        "url": "bt_tv_base_url",
                        "keyword": "高清剧集",
                        "use_login_url": True  # 标记使用登录URL
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
                        base_url = result[0]
                        site_info = {
                            "base_url": base_url,
                            "keyword": config["keyword"]
                        }
                        
                        # 对于BTHD和HDTV，添加登录URL
                        if config.get("use_login_url", False):
                            login_url = f"{base_url}/member.php?mod=logging&action=login"
                            site_info["login_url"] = login_url
                        
                        self.sites[site_name] = site_info
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
        # 对于BTHD和HDTV使用登录URL，其他站点使用基础URL
        if "login_url" in site_config:
            url = site_config["login_url"]
            url_type = "登录"
        else:
            url = site_config["base_url"]
            url_type = "基础"
        
        keyword = site_config["keyword"]
        
        try:
            logging.info(f"开始测试站点 {site_name}: {url} (使用{url_type}URL)")
            
            # 使用统一的验证码处理方法
            self.captcha_handler = CaptchaHandler(self.driver, self.ocr_api_key)
            self.captcha_handler.handle_captcha(url)
            
            # 访问相应URL
            self.driver.get(url)
            
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

    def _test_site_with_timeout(self, site_name, site_config, timeout_seconds=180):
        """带超时控制的站点测试包装函数"""
        try:
            # 创建一个新的线程来执行测试
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                # 提交测试任务
                future = executor.submit(self.test_site, site_name, site_config)
                
                # 等待结果，最多等待指定的超时时间
                result = future.result(timeout=timeout_seconds)
                return result
        except concurrent.futures.TimeoutError:
            logging.error(f"站点 {site_name} 测试超时 ({timeout_seconds}秒)")
            return False
        except Exception as e:
            logging.error(f"站点 {site_name} 测试过程中发生错误: {e}")
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
                
            # 测试每个站点，设置3分钟(180秒)超时
            for site_name, site_config in self.sites.items():
                try:
                    result = self._test_site_with_timeout(site_name, site_config, timeout_seconds=180)
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
        
        # 保存结果到JSON文件
        status_data = {
            "status": results,
            "last_checked": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        try:
            with open('/tmp/site_status.json', 'w', encoding='utf-8') as f:
                json.dump(status_data, f, ensure_ascii=False, indent=2)
            logging.info("站点状态已保存到 /tmp/site_status.json")
        except Exception as e:
            logging.error(f"保存站点状态到文件时出错: {e}")

def main():
    tester = SiteTester()
    results = tester.run_tests()
    tester.print_results(results)
    
    # 返回异常站点数量，可用于脚本退出码
    abnormal_count = len([status for status in results.values() if not status])
    return abnormal_count

if __name__ == "__main__":
    main()
    exit(0)