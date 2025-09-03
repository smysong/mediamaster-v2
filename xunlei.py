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

    def setup_webdriver(self, instance_id=11):
        if hasattr(self, 'driver') and self.driver is not None:
            logging.info("WebDriver已经初始化，无需重复初始化")
            return
        options = Options()
        # 模拟 iPhone SE
        mobile_emulation = {
            "deviceName": "iPhone SE"
        }
        options.add_experimental_option("mobileEmulation", mobile_emulation)
        options.add_argument('--headless')  # 无头模式运行
        options.add_argument('--no-sandbox')  # 在非root用户下需要禁用沙盒
        options.add_argument('--disable-dev-shm-usage')  # 解决/dev/shm空间不足的问题
        options.add_argument('--window-size=1920x1080')  # 设置窗口大小
        options.add_argument('--lang=zh-CN')  # 设置浏览器语言为简体中文
        options.add_argument('--disable-gpu')  # 禁用GPU加速
        options.add_argument('--disable-extensions')  # 禁用扩展插件
        # 设置用户配置文件缓存目录，使用固定instance-id 11作为该程序特有的id
        user_data_dir = f'/app/ChromeCache/user-data-dir-{instance_id}'
        options.add_argument(f'--user-data-dir={user_data_dir}')
        # 设置磁盘缓存目录，使用instance-id区分
        disk_cache_dir = f"/app/ChromeCache/disk-cache-dir-{instance_id}"
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

    def login_to_xunlei(self, username, password, max_retries=3):
        """
        打开 迅雷-远程设备 页面并执行迅雷登录。
        在找不到 iframe 或点击登录按钮失败时刷新页面并重试。
        """
        for attempt in range(1, max_retries + 1):
            self.driver.get(f"https://pan.xunlei.com/yc/home/")
            logging.info("成功加载 迅雷-远程设备 页面")
            time.sleep(5)

            # 点击登录按钮前检查是否已登录
            if self.check_login_status():
                logging.info("已在登录状态，跳过登录流程")
                return True

            try:
                # 点击立即登录按钮
                login_button = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, "span.button-login"))
                )
                login_button.click()
                time.sleep(5)
                logging.info("成功点击“立即登录”按钮")
            except TimeoutException:
                logging.warning(f"第 {attempt} 次尝试：点击“立即登录”按钮失败，刷新页面并重试")
                if attempt < max_retries:
                    continue
                else:
                    return False

            # 点击账号密码登录按钮前检查登录状态
            if self.check_login_status():
                logging.info("已在登录状态，跳过账号密码登录步骤")
                return True

            try:
                account_login = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//a[text()='账号密码登录']"))
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
                if attempt < max_retries:
                    logging.info(f"准备第 {attempt + 1} 次尝试...")
                else:
                    logging.error("达到最大重试次数，登录失败")
                    return False

        logging.error("登录失败，未知错误")
        return False

    def check_login_status(self, timeout=10):
        try:
            # 等待页面加载并检查是否存在“小工具”或“个人片库”
            WebDriverWait(self.driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'小工具') or contains(text(),'个人片库')]"))
            )
            logging.info("检测到已登录状态")
            return True
        except Exception as e:
            logging.error("检测到未登录状态")
            return False

    def check_device(self, device_name):
        """
        检查并切换迅雷设备。
        :param device_name: 配置中的设备名称
        :return: 成功返回 True，失败返回 False
        """
        try:
            # 检查当前设备
            time.sleep(5)
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
        检查并切换迅雷的下载目录，兼容一级和多级目录。
        :param download_dir: 下载目录路径
        :return: 成功返回 True，失败返回 False
        """
        try:
            # 统一路径格式并拆分
            path_parts = [p for p in download_dir.replace(os.path.sep, '/').split('/') if p]
            if not path_parts:
                return True

            # 获取当前页面显示的下载目录路径
            current_dir_element = WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.file-upload__folder > span"))
            )
            current_dir_text = current_dir_element.text.strip()

            # 提取路径部分
            if current_dir_text.startswith("下载到："):
                current_path = current_dir_text[len("下载到："):]
            else:
                current_path = current_dir_text

            normalized_current = [p for p in current_path.replace(os.path.sep, '/').split('/') if p]
            normalized_target = path_parts

            if normalized_current == normalized_target:
                logging.info(f"当前下载目录已是目标目录: {download_dir}")
                return True

            # 打开目录选择器
            more_options_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "i.qh-icon-more"))
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(more_options_button).click().perform()
            time.sleep(1)

            # 进入每一级目录
            current_level = 0
            while current_level < len(normalized_target):
                folder_name = normalized_target[current_level]
                escaped_name = folder_name.replace("'", "\\'")
                xpath = f"//p[contains(@class, 'history') and (text()='{escaped_name}' or text()='{escaped_name}/')]"

                folder_element = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, xpath))
                )

                # 勾选 checkbox
                checkbox_container = folder_element.find_element(By.XPATH, "../span")
                folder_checkbox = checkbox_container.find_element(
                    By.XPATH,
                    ".//span[contains(@class, 'nas-remote__checkbox')]"
                )
                if 'checked' not in folder_checkbox.get_attribute("class"):
                    actions.move_to_element(folder_checkbox).click().perform()
                    time.sleep(0.5)

                # 如果不是最后一级，进入目录
                if current_level < len(normalized_target) - 1:
                    enter_button = folder_element.find_element(By.XPATH, "../div[contains(@class, 'enter')]")
                    actions.move_to_element(enter_button).click().perform()
                    time.sleep(1)
                else:
                    # 最后一级，点击确认
                    if not self._click_confirm_button():
                        return False

                current_level += 1

            logging.info(f"成功切换至下载目录: {download_dir}")
            return True

        except Exception as e:
            logging.error(f"检查下载目录失败: {e}")
            return False

    def _click_confirm_button(self):
        """
        点击文件夹选择后的确认按钮。
        """
        try:
            confirm_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.button-base.primary-button"))
            )
            actions = ActionChains(self.driver)
            actions.move_to_element(confirm_button).click().perform()
            logging.info("已点击确认按钮")
            return True
        except Exception as e:
            logging.error(f"点击确认按钮失败: {e}")
            return False

    def _select_files_by_size_threshold(self, min_size_mb=5):
        """
        筛选并取消勾选小于指定大小（默认 5MB）的文件。
        支持 KB、MB、GB 单位。
        :param min_size_mb: 最小文件大小（MB）
        :return: 成功返回 True，失败返回 False
        """
        try:
            # 等待文件列表加载
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "div.virtual-list-scroll div.file-node"))
            )

            file_nodes = self.driver.find_elements(By.CSS_SELECTOR, "div.virtual-list-scroll div.file-node")
            actions = ActionChains(self.driver)

            for node in file_nodes:
                size_text = node.find_element(By.CSS_SELECTOR, "p.file-node__size").text
                check_icon = node.find_element(By.CSS_SELECTOR, "span.check-icon")

                # 解析文件大小
                if 'KB' in size_text:
                    size_value = float(size_text.replace('KB', '')) / 1024  # 转换为 MB
                elif 'MB' in size_text:
                    size_value = float(size_text.replace('MB', ''))
                elif 'GB' in size_text:
                    size_value = float(size_text.replace('GB', '')) * 1024  # 转换为 MB
                else:
                    continue  # 忽略无法识别的格式

                is_checked = 'checked' in check_icon.get_attribute("class")

                # 取消小于阈值的小文件勾选
                if size_value < min_size_mb and is_checked:
                    actions.move_to_element(check_icon).click().perform()
                    logging.info(f"取消勾选小文件: {size_text}")

            return True

        except Exception as e:
            logging.error(f"筛选文件大小失败: {e}")
            return False

    def generate_magnet_from_torrent(self, torrent_path):
        """
        使用 bencodepy 解析种子文件并生成磁力链接，包含名称和 trackers。
        """
        try:
            # 读取种子文件内容
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
            logging.error(f"解析种子文件失败: {e}")
            return None

    def add_magnets_and_cleanup(self, magnet_link_tuples):
        """
        将 Torrent 目录下所有种子文件转换为磁力链接并添加到迅雷下载任务，
        最后清理所有种子文件。
        """
        torrent_dir = self.TORRENT_DIR

        if not os.path.exists(torrent_dir):
            logging.error(f"目录不存在: {torrent_dir}")
            return False

        success_count = 0
        for magnet_link, original_file_name in magnet_link_tuples:
            try:
                if self._add_magnet_link(magnet_link, original_file_name):  # 磁力链接和传入文件名
                    logging.info(f"添加任务成功: {original_file_name}")
                    success_count += 1
                else:
                    logging.error(f"添加任务失败: {original_file_name}")

            except Exception as e:
                logging.error(f"处理种子文件 {original_file_name} 失败: {e}")

        if success_count > 0:
            logging.info(f"共处理 {success_count} 个种子文件")
            return True
        else:
            logging.warning("未成功添加任何磁力链接")
            return False

    def _add_magnet_link(self, magnet_link, original_file_name=None):
        """
        在当前页面中粘贴磁力链接并提交。
        :param magnet_link: 磁力链接字符串
        :param original_file_name: 原始种子文件名（用于清理）
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

            # 检查下载目录
            if not self.check_download_directory(self.config.get("xunlei_dir")):
                logging.error("下载目录设置失败")
                return False
            time.sleep(2)

            # 筛选文件大小小于 10MB 的文件并取消勾选
            if not self._select_files_by_size_threshold(min_size_mb=5):
                logging.error("文件筛选失败")
                return False
            time.sleep(2)

            # 定位并点击“立即下载”按钮
            start_download_button = WebDriverWait(self.driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "div.submit-frame > div.submit-btn"))
            )
            self.driver.execute_script("arguments[0].scrollIntoView(true);", start_download_button)
            self.driver.execute_script("arguments[0].click();", start_download_button)
            logging.debug("成功点击‘立即下载’按钮")
            time.sleep(2)

            # 检查是否出现“任务已存在”提示窗口
            try:
                task_exists_dialog = WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//div[@class='content']//h2[text()='任务已存在']"))
                )
                if task_exists_dialog:
                    logging.info("检测到‘任务已存在’提示，跳过添加任务")

                    # 查找并点击“查看”按钮
                    view_button = self.driver.find_element(By.XPATH, "//div[@class='buttons vertical']//button[text()='查看']")
                    actions.move_to_element(view_button).click().perform()
                    time.sleep(1)

                    # 删除种子文件（任务已存在）
                    if original_file_name:
                        os.remove(os.path.join(self.TORRENT_DIR, original_file_name))
                        logging.info(f"已清理种子文件（任务已存在）: {original_file_name}")

                    return True
            except TimeoutException:
                pass  # 如果没有提示，则继续正常流程

            logging.info("已成功添加迅雷下载任务")
            # 成功添加任务后删除种子文件
            if original_file_name:
                os.remove(os.path.join(self.TORRENT_DIR, original_file_name))
                logging.info(f"已清理种子文件（添加成功）: {original_file_name}")
            return True

        except Exception as e:
            logging.error(f"添加任务失败: {e}")
            # 添加任务失败时重命名种子文件
            if original_file_name:
                old_path = os.path.join(self.TORRENT_DIR, original_file_name)
                new_path = old_path + ".添加失败"
                if os.path.exists(old_path):
                    os.rename(old_path, new_path)
                    logging.info(f"种子文件重命名为: {new_path}")
                    logging.info(f"请手动对添加下载任务失败的种子文件进行处理！")
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
        logging.info(f"当前下载下载器为 {download_type}，无需执行迅雷-添加下载任务")
        exit(0)

    # 检查 Torrent 目录是否有种子文件
    torrent_dir = XunleiDownloader.TORRENT_DIR
    if not os.path.exists(torrent_dir):
        logging.info(f"目录 {torrent_dir} 不存在，程序结束")
        exit(0)

    torrent_files = [
        f for f in os.listdir(torrent_dir)
        if f.lower().endswith(".torrent")
    ]

    if not torrent_files:
        logging.info("没有发现种子文件，程序结束")
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

    # 添加磁力链接并清理种子文件
    if downloader.add_magnets_and_cleanup(magnet_links):
        logging.info("所有种子文件已成功处理并清理")
    else:
        logging.warning("部分或全部种子文件处理失败")

    downloader.close_driver()