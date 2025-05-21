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
import hashlib
import urllib.parse
import bencodepy
import base64

# 配置日志
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/xunlei.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

class XunleiDownloader:
    TORRENT_DIR = "/Torrent"  # 定义种子文件目录为类属性

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
            "profile.managed_default_content_settings.images": 1
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

    def generate_magnet_from_torrent(self, torrent_path):
        """
        使用 bencodepy 解析 .torrent 文件并生成磁力链接，包含名称和 trackers。
        """
        try:
            # 读取 .torrent 文件内容
            with open(torrent_path, 'rb') as f:
                torrent_data = f.read()

            # 解码 bencode 数据
            decoded = bencodepy.decode(torrent_data)

            # 获取 info 字典并重新编码以计算哈希值
            info = decoded[b'info']
            info_encoded = bencodepy.encode(info)

            # 计算 SHA-1 哈希，并将其转换为 Base32 形式
            info_hash_sha1 = hashlib.sha1(info_encoded).digest()
            info_hash_base32 = base64.b32encode(info_hash_sha1).decode('utf-8')

            # 构建基本的磁力链接
            magnet_link = f'magnet:?xt=urn:btih:{info_hash_base32}'

            # 添加名称（如果存在）
            if b'name' in info:
                name = info[b'name'].decode('utf-8', errors='ignore')
                magnet_link += f'&dn={urllib.parse.quote(name)}'

            # 添加主 tracker
            if b'announce' in decoded:
                announce = decoded[b'announce'].decode('utf-8', errors='ignore')
                magnet_link += f'&tr={urllib.parse.quote(announce)}'

            # 添加多个 trackers（如果存在 announce-list）
            if b'announce-list' in decoded:
                for item in decoded[b'announce-list']:
                    if isinstance(item, list):
                        for sub_announce in item:
                            magnet_link += f'&tr={urllib.parse.quote(sub_announce.decode("utf-8", errors="ignore"))}'
                    else:
                        magnet_link += f'&tr={urllib.parse.quote(item.decode("utf-8", errors="ignore"))}'

            return magnet_link

        except Exception as e:
            logging.error(f"解析 .torrent 文件失败: {e}")
            return None

    def login_to_xunlei(self, username, password):
        """
        打开 xunlei/xunlei.html 页面并执行迅雷登录。
        """
        html_path = os.path.abspath("xunlei/xunlei.html")  # 本地 HTML 路径
        if not os.path.exists(html_path):
            logging.error(f"HTML 文件不存在: {html_path}")
            return False

        self.driver.get(f"file:///{html_path.replace(os.path.sep, '/')}")

        logging.info("成功加载 xunlei.html 页面")

        time.sleep(2)
        try:
            # 切换到 iframe
            iframe = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "iframe"))
            )
            self.driver.switch_to.frame(iframe)
        except TimeoutException:
            logging.warning("未找到 iframe，继续尝试后续步骤")

        try:
            # 点击登录按钮
            login_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "span.button-login"))
            )
            login_button.click()
            time.sleep(1)
        except TimeoutException:
            logging.info("当前已是登录状态")
            return True

        try:
            account_login = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='账号密码登录']"))
            )
            account_login.click()
        except TimeoutException:
            logging.error("无法点击账号密码登录按钮")
            return False

        try:
            username_input = self.driver.find_element(By.XPATH, "//input[@placeholder='请输入手机号/邮箱/账号']")
            password_input = self.driver.find_element(By.XPATH, "//input[@placeholder='请输入密码']")
            username_input.send_keys(username)
            password_input.send_keys(password)
        except Exception as e:
            logging.error(f"填写用户名或密码失败: {e}")
            return False

        try:
            checkbox = self.driver.find_element(By.XPATH,
                                                "//input[@type='checkbox' and contains(@class, 'xlucommon-login-checkbox')]")
            if not checkbox.is_selected():
                checkbox.click()
        except Exception as e:
            logging.error(f"勾选协议失败: {e}")
            return False

        try:
            submit_button = self.driver.find_element(By.CSS_SELECTOR, "button.xlucommon-login-button")
            submit_button.click()
            time.sleep(5)  # 等待登录完成
        except Exception as e:
            logging.error(f"提交登录失败: {e}")
            return False

        try:
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "button-create"))
            )
            logging.info("迅雷登录成功")
            return True
        except TimeoutException:
            logging.error("登录失败，请检查用户名和密码")
            return False

    def check_device(self, device_name):
        """
        检查并切换迅雷设备。

        :param device_name: 配置中的设备名称
        :return: 成功返回 True，失败返回 False
        """
        try:
            # 检查当前设备
            header_home = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[@class='header-home']"))
            )
            if header_home.text != device_name:
                logging.info(f"当前设备为 '{header_home.text}'，正在切换到 '{device_name}'")
                actions = ActionChains(self.driver)
                actions.move_to_element(header_home).click().perform()
                time.sleep(1)

                device_option = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH,
                                                f"//span[contains(@class, 'device') and (text()='{device_name}' or text()='{device_name}(离线)')]"))
                )
                actions.move_to_element(device_option).click().perform()
                time.sleep(3)
            else:
                logging.info("已处于目标设备")

            return True

        except Exception as e:
            logging.error(f"检查设备失败: {e}")
            return False

    def check_download_directory(self, download_dir):
        """
        检查并切换迅雷的下载目录。

        :param download_dir: 下载目录路径
        :return: 成功返回 True，失败返回 False
        """
        try:
            # 分割路径
            path_parts = [p for p in download_dir.replace(os.path.sep, '/').split('/') if p]

            # 逐级进入文件夹
            for part in path_parts[:-1]:
                self._enter_folder(part)

            # 选择最终文件夹
            if not self._select_final_folder(path_parts[-1]):
                return False

            return True

        except Exception as e:
            logging.error(f"检查下载目录失败: {e}")
            return False

    def _enter_folder(self, folder_name):
        """
        进入指定名称的文件夹。
        """
        try:
            # 定位并点击更多选项按钮
            more_options_button = WebDriverWait(self.driver, 10).until(
                lambda d: d.find_element(By.CSS_SELECTOR, "i.qh-icon-more")
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(more_options_button).click().perform()
            time.sleep(1)  # 等待菜单展开或其他操作完成

            # 原有逻辑：定位文件夹并进入
            folder_element = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH,
                                                f"//p[contains(@class, 'history') and (text()='{folder_name}/' or text()='{folder_name}')]"))
            )
            enter_button = folder_element.find_element(By.XPATH, "../div[contains(@class, 'enter')]")
            actions = ActionChains(self.driver)
            actions.move_to_element(enter_button).click().perform()
            time.sleep(1)

        except Exception as e:
            logging.error(f"进入文件夹 {folder_name} 失败: {e}")
            raise

    def _select_final_folder(self, folder_name):
        """
        勾选指定名称的目标文件夹。
        """
        try:
            folder_element = WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH,
                                                f"//p[contains(@class, 'history') and (text()='{folder_name}' or text()='{folder_name}/')]"))
            )
            checkbox_container = folder_element.find_element(By.XPATH, "../span")
            folder_checkbox = checkbox_container.find_element(By.XPATH,
                                                            ".//span[contains(@class, 'nas-remote__checkbox')]")
            actions = ActionChains(self.driver)
            actions.move_to_element(folder_checkbox).click().perform()
            logging.info(f"已选择目标文件夹: {folder_name}")
            return True
        except Exception as e:
            logging.error(f"选择目标文件夹 {folder_name} 失败: {e}")
            return False

    def add_magnets_and_cleanup(self, magnet_link_tuples):
        """
        将 Torrent 目录下所有 .torrent 文件转换为磁力链接并添加到迅雷下载任务，
        最后清理所有 .torrent 文件。
        """
        torrent_dir = self.TORRENT_DIR

        if not os.path.exists(torrent_dir):
            logging.error(f"目录不存在: {torrent_dir}")
            return False

        success_count = 0
        for magnet_link, original_file_name in magnet_link_tuples:
            try:
                if self._add_magnet_link(magnet_link):
                    # 使用原始文件名删除 .torrent 文件
                    os.remove(os.path.join(torrent_dir, original_file_name))
                    logging.info(f"已添加并清理文件: {original_file_name}")
                    success_count += 1
                else:
                    logging.error(f"添加磁力链接失败: {magnet_link}")

            except Exception as e:
                logging.error(f"处理磁力链接 {magnet_link} 失败: {e}")

        if success_count > 0:
            logging.info(f"共处理 {success_count} 个 .torrent 文件")
            return True
        else:
            logging.warning("未成功添加任何磁力链接")
            return False

    def _add_magnet_link(self, magnet_link):
        """
        在当前页面中粘贴磁力链接并提交。

        :param magnet_link: 磁力链接字符串
        :return: 成功返回 True，失败返回 False
        """
        try:
            # 打开新建任务弹窗
            new_task_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "i.qh-icon-new"))
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(new_task_button).click().perform()
            time.sleep(1)

            # 填入磁力链接
            magnet_input = WebDriverWait(self.driver, 10).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "textarea.textarea__inner"))
            )
            magnet_input.clear()
            magnet_input.send_keys(magnet_link)
            time.sleep(1)

            # 点击确认按钮
            confirm_button = self.driver.find_element(By.CSS_SELECTOR, "a.file-upload__button")
            actions = ActionChains(self.driver)
            actions.move_to_element(confirm_button).click().perform()
            time.sleep(2)

            # 筛选文件大小小于 10MB 的文件并取消勾选
            if not self._select_files_by_size_threshold(min_size_mb=10):
                logging.error("文件筛选失败")
                return False

            # 点击开始下载按钮前检查下载目录
            if not self.check_download_directory(self.config.get("xunlei_dir")):
                logging.error("下载目录设置失败")
                return False
            time.sleep(2)
            # 等待 submit-frame 加载完成
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.submit-frame"))
            )

            # 定位并点击“立即下载”按钮
            start_download_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "div.submit-frame > div.submit-btn"))
            )

            self.driver.execute_script("arguments[0].scrollIntoView(true);", start_download_button)
            self.driver.execute_script("arguments[0].click();", start_download_button)
            logging.debug("成功点击‘立即下载’按钮")
            time.sleep(2)
            logging.info("成功添加磁力链接并跳过小文件")
            return True

        except Exception as e:
            logging.error(f"添加磁力链接失败: {e}")
            return False

    def _select_files_by_size_threshold(self, min_size_mb=10):
        """
        根据文件大小筛选并设置勾选状态：
        - 小于 min_size_mb 的文件取消勾选；
        - 大于等于 min_size_mb 的文件确保勾选。
        支持 KB、MB、GB 单位。

        :param min_size_mb: 最小文件大小（MB）
        :return: 成功返回 True，失败返回 False
        """
        try:
            # 等待文件列表加载
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.virtual-list-scroll div.file-node"))
            )
            time.sleep(2)

            virtual_list = self.driver.find_element(By.CSS_SELECTOR, "div.virtual-list-scroll")
            file_nodes = virtual_list.find_elements(By.XPATH, ".//div[@class='file-node']")

            for node in file_nodes:
                size_text = node.find_element(By.XPATH, ".//p[@class='file-node__size']").text

                if 'KB' in size_text:
                    # KB 直接视为小文件
                    check_icon = node.find_element(By.XPATH,
                                                ".//span[contains(@class, 'check-icon qh-icon-check')]")
                    is_checked = 'checked' in check_icon.get_attribute("class")

                    if is_checked:
                        actions = ActionChains(self.driver)
                        actions.move_to_element(check_icon).click().perform()
                        logging.info(f"取消勾选小文件 (KB): {size_text}")

                elif 'MB' in size_text or 'GB' in size_text:
                    size_value = float(size_text.replace('MB', '').replace('GB', ''))
                    size_value *= (1 if 'MB' in size_text else 1024)  # 转换为 MB

                    check_icon = node.find_element(By.XPATH,
                                                ".//span[contains(@class, 'check-icon qh-icon-check')]")

                    is_small_file = size_value < min_size_mb
                    is_checked = 'checked' in check_icon.get_attribute("class")

                    # 小文件且已勾选 → 取消勾选
                    if is_small_file and is_checked:
                        actions = ActionChains(self.driver)
                        actions.move_to_element(check_icon).click().perform()
                        logging.info(f"取消勾选小文件: {size_text}")

                    # 大文件且未勾选 → 勾选
                    elif not is_small_file and not is_checked:
                        actions = ActionChains(self.driver)
                        actions.move_to_element(check_icon).click().perform()
                        logging.info(f"勾选大文件: {size_text}")

            return True

        except Exception as e:
            logging.error(f"筛选文件大小失败: {e}")
            return False

    def close_driver(self):
        if self.driver:
            self.driver.quit()
            logging.info("WebDriver关闭完成")
            self.driver = None  # 重置 driver 变量

if __name__ == "__main__":
    downloader = XunleiDownloader()

    # 加载配置
    config = downloader.load_config()
    download_type = config.get("download_type")
    
    if download_type != "xunlei":
        logging.info(f"当前下载类型为 {download_type}，不是 xunlei，程序结束")
        exit(0)

    # 检查 Torrent 目录是否有 .torrent 文件
    torrent_dir = XunleiDownloader.TORRENT_DIR
    if not os.path.exists(torrent_dir):
        logging.info(f"目录 {torrent_dir} 不存在，程序结束")
        exit(0)

    torrent_files = [
        f for f in os.listdir(torrent_dir)
        if f.lower().endswith(".torrent")
    ]

    if not torrent_files:
        logging.info("没有发现 .torrent 文件，程序结束")
        exit(0)

    # 初始化浏览器
    downloader.setup_webdriver()

    # 从配置中获取用户名和密码
    username = config.get("download_username")
    password = config.get("download_password")

    # 登录迅雷
    if not downloader.login_to_xunlei(username, password):
        logging.error("登录失败")
        downloader.close_driver()
        exit(1)

    # 获取配置参数
    xunlei_device_name = config.get("xunlei_device_name")
    xunlei_dir = config.get("xunlei_dir")

    # 在打开新建任务弹窗前检查设备
    if not downloader.check_device(xunlei_device_name):
        logging.error("设备切换失败")
        downloader.close_driver()
        exit(1)

    # 生成磁力链接
    magnet_links = []
    for file_name in torrent_files:
        torrent_path = os.path.join(torrent_dir, file_name)
        magnet_link = downloader.generate_magnet_from_torrent(torrent_path)
        if magnet_link:
            magnet_links.append((magnet_link, file_name))  # 同时保存磁力链接和原始文件名
        else:
            logging.error(f"生成磁力链接失败: {file_name}")

    if not magnet_links:
        logging.warning("未生成有效的磁力链接，程序结束")
        downloader.close_driver()
        exit(0)

    # 添加磁力链接并清理 .torrent 文件
    if downloader.add_magnets_and_cleanup(magnet_links):
        logging.info("所有 .torrent 文件已成功处理并清理")
    else:
        logging.warning("部分或全部 .torrent 文件处理失败")

    downloader.close_driver()