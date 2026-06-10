import json
import subprocess
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
import tempfile
import sqlite3
import requests
import argparse 
import glob
from captcha_handler import CaptchaHandler
from pathlib import Path
import shutil
from urllib.parse import urljoin
import html as _html
import re
import base64

from transmission_rpc import Client as TransmissionClient
from qbittorrentapi import Client as QBittorrentClient, LoginFailed

# 配置日志
os.makedirs("/tmp/log", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,  # 设置日志级别为 INFO
    format="%(asctime)s - %(levelname)s - %(message)s",  # 设置日志格式
    handlers=[
        logging.FileHandler("/tmp/log/downloader.log", mode='w'),  # 输出到文件并清空之前的日志
        logging.StreamHandler()  # 输出到控制台
    ]
)

def get_default_torrent_dir() -> str:
    """获取默认种子目录。

    - 若设置了环境变量 TORRENT_DIR，则使用该值
    - Windows 下默认使用当前工作目录下的 Torrent 目录（避免写入磁盘根目录）
    - 其他系统默认 /Torrent（兼容 Docker 镜像习惯）
    """
    env_dir = (os.environ.get("TORRENT_DIR") or "").strip()
    if env_dir:
        return env_dir
    if os.name == "nt":
        return os.path.join(os.getcwd(), "Torrent")
    return "/Torrent"

def get_latest_torrent_file(download_dir=None):
    """获取下载目录下最新的.torrent文件路径"""
    if not download_dir:
        download_dir = get_default_torrent_dir()
    torrent_files = glob.glob(os.path.join(download_dir, "*.torrent"))
    if not torrent_files:
        return None
    return max(torrent_files, key=os.path.getctime)

def rename_torrent_file(old_path, new_name, download_dir=None):
    """重命名.torrent文件，失败时使用原始文件添加下载任务"""
    if not download_dir:
        download_dir = get_default_torrent_dir()
    new_path = os.path.join(download_dir, new_name)
    try:
        os.rename(old_path, new_path)
        logging.info(f"种子文件已重命名为: {new_path}")
        run_task_adder(new_path)  # 添加任务到下载器
    except Exception as e:
        logging.error(f"重命名种子文件失败: {e}")
        # 重命名失败时，直接用原始文件添加下载任务
        logging.info(f"使用原始种子文件添加下载任务: {old_path}")
        run_task_adder(old_path)

def run_task_adder(torrent_path):
    """使用 download_task_adder.py 添加任务，增加连接失败等异常处理，避免程序崩溃"""
    try:
        logging.info(f"向下载器添加下载任务：{torrent_path}")
        # 使用with打开devnull，避免未定义报错
        with open(os.devnull, 'w') as devnull:
            result = subprocess.run(
                ['python', 'download_task_adder.py', torrent_path],
                check=False,  # 不自动抛出异常
                stdout=devnull,
                stderr=devnull
            )
            
        if result.returncode == 0:
            logging.info("下载任务添加完成")
        elif result.returncode == 1:
            logging.info("下载器未启用或配置异常，跳过自动添加下载任务")
        else:
            logging.error(f"添加下载任务失败，返回码: {result.returncode}")
    except FileNotFoundError as e:
        logging.error(f"调用 添加下载器任务程序 失败，文件未找到: {e}")
    except Exception as e:
        logging.error(f"添加下载任务时发生未知错误: {e}")

class MediaDownloader:
    def __init__(self, db_path=None):
        self.db_path = db_path
        self.driver = None
        self.config = {}
        if not self.db_path:
            self.db_path = os.environ.get("DB_PATH") or os.environ.get("DATABASE") or '/config/data.db'

    def setup_webdriver(self, instance_id=10, headless: bool = True):
        if hasattr(self, 'driver') and self.driver is not None:
            logging.info("WebDriver已经初始化，无需重复初始化")
            return
        options = Options()
        if headless:
            options.add_argument('--headless=new')  # 使用新版无头模式
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--window-size=1920x1080')
        options.add_argument('--disable-gpu')
        options.add_argument('--disable-extensions')
        options.add_argument('--disable-background-timer-throttling')  # 禁用后台定时器节流
        options.add_argument('--disable-renderer-backgrounding')       # 禁用渲染器后台运行
        options.add_argument('--disable-features=VizDisplayCompositor') # 禁用Viz显示合成器
        # 更强的反检测措施
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option('useAutomationExtension', False)
        options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.6943.90 Safari/537.36')
        # 忽略SSL证书错误
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('--allow-insecure-localhost')
        options.add_argument('--ignore-ssl-errors')
        # 设置页面加载策略为急切模式
        options.page_load_strategy = 'eager'
        # 设置浏览器语言为中文
        options.add_argument('--lang=zh-CN')
        # 设置用户配置文件缓存目录（Windows 本地默认使用 LOCALAPPDATA/TEMP，避免 /app 不可写/冲突）
        cache_base_dir = os.environ.get("CHROME_CACHE_DIR")
        if not cache_base_dir:
            if os.name == "nt":
                cache_base_dir = os.path.join(
                    os.environ.get("LOCALAPPDATA") or tempfile.gettempdir(),
                    "MediaMaster",
                    "ChromeCache",
                )
            else:
                cache_base_dir = "/app/ChromeCache"

        user_data_dir = os.path.join(cache_base_dir, f'user-data-dir-inst-{instance_id}')
        try:
            os.makedirs(user_data_dir, exist_ok=True)
        except Exception:
            pass
        options.add_argument(f'--user-data-dir={user_data_dir}')
        # 设置磁盘缓存目录，同样使用instance-id区分
        disk_cache_dir = os.path.join(cache_base_dir, f"disk-cache-dir-inst-{instance_id}")
        try:
            os.makedirs(disk_cache_dir, exist_ok=True)
        except Exception:
            pass
        options.add_argument(f"--disk-cache-dir={disk_cache_dir}")
        
        # 设置默认下载目录
        download_dir = get_default_torrent_dir()
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

        # 降低被识别为自动化的概率（一般不会影响现有站点）
        try:
            options.add_experimental_option("excludeSwitches", ["enable-automation"])
            options.add_experimental_option("useAutomationExtension", False)
        except Exception:
            pass

        # 指定 chromedriver 的路径（优先环境变量/系统设置；Docker/Linux 再用默认路径）
        configured_driver_path = ""
        try:
            configured_driver_path = (self.config.get("chromedriver_path") or "").strip()
        except Exception:
            configured_driver_path = ""
        driver_path = os.environ.get("CHROMEDRIVER_PATH") or configured_driver_path or "/usr/lib/chromium/chromedriver"
        service = Service(executable_path=driver_path) if driver_path and os.path.exists(driver_path) else None
        
        try:
            if service is not None:
                self.driver = webdriver.Chrome(service=service, options=options)
            else:
                try:
                    self.driver = webdriver.Chrome(options=options)
                except Exception as e:
                    msg = str(e)
                    if ("only supports Chrome version" in msg) or ("error decoding response body" in msg) or ("Unable to obtain driver" in msg):
                        try:
                            cache_root = Path.home() / ".cache" / "selenium"
                            shutil.rmtree(cache_root / "chromedriver", ignore_errors=True)
                            try:
                                (cache_root / "se-metadata.json").unlink(missing_ok=True)
                            except Exception:
                                pass
                            self.driver = webdriver.Chrome(options=options)
                        except Exception:
                            logging.warning("Selenium Manager获取驱动失败，尝试使用PATH中的chromedriver")
                            self.driver = webdriver.Chrome(service=Service(), options=options)
                    else:
                        raise

            try:
                self.driver.set_page_load_timeout(30)
                self.driver.set_script_timeout(30)
            except Exception:
                pass
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
                "body": f"开始下载：{title_text}"  # 使用 title_text 作为 body 内容
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
    def add_magnet_task(self, magnet_link: str, title_text: str) -> bool:
        """将磁力链接直接添加到下载器（Transmission / qBittorrent）。"""
        try:
            if not magnet_link or not str(magnet_link).startswith("magnet:"):
                logging.error("无效磁力链接，无法添加任务")
                return False

            download_mgmt = self.config.get('download_mgmt', 'False').lower() == 'true'
            download_type = (self.config.get('download_type', 'transmission') or 'transmission').lower()
            download_host = self.config.get('download_host', '127.0.0.1')
            download_port = int(self.config.get('download_port', 9091))
            download_username = self.config.get('download_username', '')
            download_password = self.config.get('download_password', '')

            if not download_mgmt:
                logging.error("下载管理功能未启用，跳过添加磁力任务")
                return False

            label = title_text or "mediamaster"

            if download_type == 'transmission':
                client = TransmissionClient(
                    host=download_host,
                    port=download_port,
                    username=download_username,
                    password=download_password,
                )
                result = client.add_torrent(magnet_link)
                torrent_id = getattr(result, 'id', None)
                if torrent_id is None:
                    try:
                        torrent_id = result.get('id')
                    except Exception:
                        torrent_id = None
                if torrent_id is not None:
                    try:
                        client.change_torrent(torrent_id, labels=[label])
                    except Exception:
                        pass
                    try:
                        client.change_torrent(torrent_id, group="mediamaster")
                    except Exception:
                        pass
                logging.info(f"已添加磁力任务到 Transmission: {label}")
                return True

            if download_type == 'qbittorrent':
                client = QBittorrentClient(
                    host=f"http://{download_host}:{download_port}",
                    username=download_username,
                    password=download_password,
                )
                try:
                    client.auth_log_in()
                except LoginFailed as e:
                    logging.error(f"qBittorrent 登录失败: {e}")
                    return False
                client.torrents_add(urls=magnet_link, tags=label, category="mediamaster")
                logging.info(f"已添加磁力任务到 qBittorrent: {label}")
                return True

            if download_type == 'xunlei':
                # 复用项目内的迅雷远程添加逻辑（Selenium）。
                try:
                    from xunlei import XunleiDownloader
                except Exception as e:
                    logging.error(f"导入迅雷下载器失败: {e}")
                    return False

                x = XunleiDownloader(db_path=self.db_path)
                x.load_config()
                x.setup_webdriver()

                username = self.config.get('download_username', '')
                password = self.config.get('download_password', '')
                if not x.login_to_xunlei(username, password):
                    logging.error("登录迅雷失败")
                    x.close_driver()
                    return False

                xunlei_device_name = self.config.get('xunlei_device_name')
                if xunlei_device_name and not x.check_device(xunlei_device_name):
                    logging.error("迅雷设备切换失败")
                    x.close_driver()
                    return False

                ok = x._add_magnet_link(magnet_link, original_file_name=None)
                x.close_driver()
                if ok:
                    logging.info(f"已添加磁力任务到 迅雷: {label}")
                return bool(ok)

            logging.error(f"当前下载器类型 {download_type} 不支持直接添加磁力链接")
            return False

        except Exception as e:
            logging.error(f"添加磁力任务失败: {e}")
            return False

    def jackett_download_torrent(self, result, title_text, referer: str | None = None, **_kwargs):
        """Jackett：link 可能是 magnet 或 .torrent URL。"""
        link = (result.get("link") or "").strip()
        if not link:
            raise Exception("Jackett 下载链接为空")

        # Jackett/Indexer 通常会给 magneturl；优先走 magnet->torrent（便于本地落盘）并尝试直接添加磁力任务
        if link.startswith("magnet:"):
            torrent_path = self._save_torrent_from_magnet(link, title_text)
            if torrent_path:
                return
            ok = self.add_magnet_task(link, title_text)
            if ok:
                return
            raise Exception("Jackett 磁力链接添加失败")

        # 其他情况：尝试按 .torrent 直链下载
        torrent_path = self._download_torrent_via_http(link, title_text, referer=referer)
        if torrent_path:
            return

        raise Exception("Jackett 种子下载失败")

    def _extract_btih_from_magnet(self, magnet_link: str) -> str | None:
        """从 magnet 链接提取 BTIH（返回 40 位 hex 字符串）。"""
        try:
            if not magnet_link or not str(magnet_link).startswith("magnet:"):
                return None

            import re
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(magnet_link).query)
            xt_list = qs.get("xt") or []
            xt = next((x for x in xt_list if x.startswith("urn:btih:")), "")
            if not xt:
                return None
            btih = xt.split("urn:btih:")[-1].strip()
            if not btih:
                return None

            # hex 40
            if re.fullmatch(r"[0-9a-fA-F]{40}", btih):
                return btih.lower()

            # base32 32 -> hex 40
            if re.fullmatch(r"[A-Z2-7]{32}", btih.upper()):
                import base64
                raw = base64.b32decode(btih.upper())
                return raw.hex()

            return None
        except Exception:
            return None

    def _save_torrent_from_magnet(self, magnet_link: str, title_text: str, download_dir: str | None = None) -> str | None:
        """尝试将 magnet 转成 .torrent 并保存到下载目录。

        说明：magnet 本身不包含种子文件内容，这里通过公共缓存源拉取对应 BTIH 的 .torrent。
        若缓存源没有该 BTIH，则返回 None。
        """
        try:
            btih = self._extract_btih_from_magnet(magnet_link)
            if not btih:
                return None

            if not download_dir:
                download_dir = get_default_torrent_dir()
            os.makedirs(download_dir, exist_ok=True)

            # 简单的文件名清洗（兼容 Windows）
            safe_title = (title_text or "download").strip()
            for ch in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
                safe_title = safe_title.replace(ch, '_')
            if not safe_title:
                safe_title = "download"

            out_path = os.path.join(download_dir, f"{safe_title}.torrent")

            session = requests.Session()
            session.trust_env = False
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/x-bittorrent,application/octet-stream,*/*",
            })

            candidate_urls = [
                f"https://itorrents.org/torrent/{btih.upper()}.torrent",
                f"https://torrage.info/torrent.php?h={btih}",
            ]

            torrent_bytes = None
            for u in candidate_urls:
                try:
                    r = session.get(u, timeout=20, allow_redirects=True)
                    if r.status_code != 200:
                        continue
                    content = r.content or b""
                    # .torrent 是 bencode 字典，通常以 'd' 开头
                    if len(content) < 50 or not content.startswith(b"d"):
                        continue
                    torrent_bytes = content
                    logging.info(f"已从缓存源获取种子文件: {u}")
                    break
                except Exception:
                    continue

            if not torrent_bytes:
                return None

            with open(out_path, "wb") as f:
                f.write(torrent_bytes)

            logging.info(f"已保存种子文件: {out_path}")
            # 与其他站点行为保持一致：保存后自动推送到下载器
            try:
                run_task_adder(out_path)
            except Exception:
                pass
            return out_path
        except Exception as e:
            logging.warning(f"magnet 转种子失败: {e}")
            return None

    def _download_torrent_via_http(self, url: str, title_text: str, referer: str | None = None, download_dir: str | None = None) -> str | None:
        """直接通过 HTTP 下载 .torrent 文件并保存。

        适用于论坛附件直链（如 1lou 的 attach-download-xxxx.htm）。
        成功保存后会自动调用 run_task_adder。
        """
        try:
            if not download_dir:
                download_dir = get_default_torrent_dir()
            os.makedirs(download_dir, exist_ok=True)

            safe_title = (title_text or "download").strip()
            for ch in ['<', '>', ':', '"', '/', '\\', '|', '?', '*']:
                safe_title = safe_title.replace(ch, '_')
            if not safe_title:
                safe_title = "download"

            out_path = os.path.join(download_dir, f"{safe_title}.torrent")

            session = requests.Session()
            session.trust_env = False
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/x-bittorrent,application/octet-stream,*/*",
            }
            if referer:
                headers["Referer"] = referer

            r = session.get(url, headers=headers, timeout=30, allow_redirects=True)
            r.raise_for_status()

            content = r.content or b""
            # .torrent 通常是 bencode dict，以 'd' 开头
            if len(content) < 50 or not content.startswith(b"d"):
                snippet = (r.text or "").strip().replace("\n", " ")[:200]
                logging.warning(f"下载到的内容不像种子文件，可能需要登录/防盗链: url={url} final={getattr(r,'url',url)} snippet={snippet}")
                return None

            with open(out_path, "wb") as f:
                f.write(content)

            logging.info(f"已保存种子文件: {out_path}")
            try:
                run_task_adder(out_path)
            except Exception:
                pass
            return out_path
        except Exception as e:
            logging.warning(f"HTTP 下载种子失败: {e}")
            return None

    def _1lou_extract_first_attachment(self, thread_url: str) -> str | None:
        try:
            session = requests.Session()
            session.trust_env = False
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            })
            r = session.get(thread_url, headers={"Referer": "https://www.1lou.me/"}, timeout=30, allow_redirects=True)
            r.raise_for_status()

            html = r.text or ""
            m = re.search(r"href=\"(https?://www\\.1lou\\.me/attach-download-[^\"\s]+)\"", html)
            if m:
                return m.group(1)
            m2 = re.search(r"href=\"(/attach-download-[^\"\s]+)\"", html)
            if m2:
                return urljoin("https://www.1lou.me/", m2.group(1).lstrip("/"))
            m3 = re.search(r"href=\"(attach-download-[^\"\s]+)\"", html)
            if m3:
                return urljoin("https://www.1lou.me/", m3.group(1).lstrip("/"))
            return None
        except Exception:
            return None

    def onelou_download_torrent(self, result, title_text, **_kwargs):
        """1LOU：优先下载附件 .torrent；若提供 magnet 则尝试 magnet->torrent。"""
        link = (result.get("link") or "").strip()
        if not link:
            raise Exception("缺少下载链接")

        # 兼容索引器/前端传入相对路径
        if not link.startswith("http"):
            if link.startswith("attach-download-") or link.startswith("/attach-download-"):
                link = urljoin("https://www.1lou.me/", link.lstrip("/"))
            elif link.startswith("thread-") or link.startswith("/thread-"):
                link = urljoin("https://www.1lou.me/", link.lstrip("/"))

        referer = ((result.get("subject_url") or result.get("referer") or _kwargs.get("referer") or "").strip()) or None

        if link.startswith("magnet:"):
            torrent_path = self._save_torrent_from_magnet(link, title_text)
            if torrent_path:
                return
            ok = self.add_magnet_task(link, title_text)
            if not ok:
                raise Exception("磁力已解析，但无法保存种子且添加磁力任务失败")
            return

        # 附件下载页
        if "attach-download-" in link or link.lower().endswith(".torrent"):
            torrent_path = self._download_torrent_via_http(link, title_text, referer=referer)
            if torrent_path:
                return
            raise Exception("1LOU 附件下载失败（可能需要登录/防盗链）")

        # 若传入的是 thread 详情页，则尝试从页面提取附件直链
        if "/thread-" in link:
            attachment = self._1lou_extract_first_attachment(link)
            if attachment:
                torrent_path = self._download_torrent_via_http(attachment, title_text, referer=link)
                if torrent_path:
                    return
            raise Exception("1LOU 详情页未找到附件下载链接")

        raise Exception("1LOU 下载链接不支持")

    def _seedhub_resolve_magnet_via_selenium(self, url: str) -> str | None:
        """用 Selenium 打开 SeedHub 的 link_start 页面并提取 magnet。"""
        if not self.driver:
            seedhub_headful = (os.environ.get("SEEDHUB_HEADFUL") or "").strip().lower() in {"1", "true", "yes", "on"}
            self.setup_webdriver(headless=not seedhub_headful)
        if not self.driver:
            return None

        try:
            # 可能触发 Cloudflare，复用现有验证码处理
            self.site_captcha(url)

            # 等待 JS 生成 magnet 链接（SeedHub 常见“5 秒后加载”+ Cloudflare，保守等待更稳）
            deadline = time.time() + 60
            magnet = None
            while time.time() < deadline and not magnet:
                try:
                    links = self.driver.find_elements(By.CSS_SELECTOR, "a[href^='magnet:']")
                    for a in links:
                        href = (a.get_attribute("href") or "").strip()
                        if href.startswith("magnet:"):
                            magnet = href
                            break
                except Exception:
                    pass

                if not magnet:
                    try:
                        # 有些页面把 magnet 放在 data-clipboard-text
                        elems = self.driver.find_elements(By.CSS_SELECTOR, "[data-clipboard-text]")
                        for el in elems:
                            val = (el.get_attribute("data-clipboard-text") or "").strip()
                            if val.startswith("magnet:"):
                                magnet = val
                                break
                    except Exception:
                        pass

                if not magnet:
                    try:
                        # 或者放在 input/textarea 的 value/text
                        fields = self.driver.find_elements(By.CSS_SELECTOR, "input, textarea")
                        for f in fields:
                            val = (f.get_attribute("value") or "").strip()
                            if val.startswith("magnet:"):
                                magnet = val
                                break
                            txt = (f.text or "").strip()
                            if txt.startswith("magnet:"):
                                magnet = txt
                                break
                    except Exception:
                        pass

                if not magnet:
                    try:
                        html = self.driver.page_source or ""
                        m = re.search(r"(magnet:\?xt=urn:btih:[A-Za-z0-9]+[^\"'<>\s]*)", html, flags=re.IGNORECASE)
                        if m:
                            magnet = _html.unescape(m.group(1))
                    except Exception:
                        pass

                if not magnet:
                    try:
                        # SeedHub 常见做法：在脚本里用 base64 存 magnet：const data = "bWF..."; window.atob(data)
                        html = self.driver.page_source or ""
                        m = re.search(r"\bconst\s+data\s*=\s*\"([A-Za-z0-9+/=]{20,})\"\s*;", html)
                        if m:
                            decoded = base64.b64decode(m.group(1)).decode("utf-8", errors="ignore").strip()
                            if decoded.startswith("magnet:"):
                                magnet = decoded
                    except Exception:
                        pass

                if not magnet:
                    time.sleep(1)

            if magnet and magnet.startswith("magnet:"):
                return magnet

            # 失败时落盘 HTML，便于定位站点结构/脚本变更（写入工作区，方便直接查看）
            try:
                ts = time.strftime("%Y%m%d_%H%M%S")
                dump_dir = os.path.join(os.getcwd(), "debug_dumps")
                os.makedirs(dump_dir, exist_ok=True)
                dump_path = os.path.join(dump_dir, f"seedhub_linkstart_failed_{ts}.html")
                with open(dump_path, "w", encoding="utf-8") as f:
                    f.write(self.driver.page_source or "")
                logging.warning(f"SeedHub magnet 解析失败，已保存页面 HTML: {dump_path}")
            except Exception:
                pass

            return None
        except Exception:
            return None

    def seedhub_download_torrent(self, result, title_text, **_kwargs):
        """SeedHub：索引器通常给 link_start 链接；此处解析为 magnet 再添加下载任务。"""
        link = (result.get("link") or "").strip()
        if not link:
            raise Exception("缺少下载链接")

        # 兼容相对路径
        if not link.startswith("http"):
            link = urljoin("https://www.seedhub.cc/", link.lstrip("/"))

        if link.startswith("magnet:"):
            torrent_path = self._save_torrent_from_magnet(link, title_text)
            if torrent_path:
                return
            ok = self.add_magnet_task(link, title_text)
            if not ok:
                raise Exception("磁力添加失败")
            return

        # link_start 页面 -> magnet
        magnet = self._seedhub_resolve_magnet_via_selenium(link)
        if not magnet:
            raise Exception("SeedHub 下载链接无法解析为 magnet（可能是 Cloudflare/站点结构变更）")

        try:
            logging.info(f"SeedHub 已解析 magnet: {magnet[:80]}")
        except Exception:
            pass

        torrent_path = self._save_torrent_from_magnet(magnet, title_text)
        if torrent_path:
            return
        ok = self.add_magnet_task(magnet, title_text)
        if not ok:
            raise Exception("已解析出磁力，但添加磁力任务失败")
        return

    def _btsj6_resolve_magnet(self, url: str, referer: str | None = None) -> str | None:
        """从 BTSJ6 的 down.php 页面解析出 magnet（无需 Selenium）。"""
        try:
            import re

            def _extract_magnet_from_html(page_html: str) -> str | None:
                if not page_html:
                    return None
                # 常见：href="magnet:?xt=urn:btih:...&amp;dn=..."
                m = re.search(r"(magnet:\?xt=urn:btih:[A-Za-z0-9]+[^\"'<>\s]*)", page_html, flags=re.IGNORECASE)
                if not m:
                    m = re.search(r"(magnet:\?[^\"'<>\s]+)", page_html, flags=re.IGNORECASE)
                if not m:
                    return None
                magnet_raw = m.group(1)
                magnet = _html.unescape(magnet_raw)
                return magnet if magnet.startswith("magnet:") else None

            # 有些环境下代理/系统设置会导致连接异常，禁用 trust_env
            session = requests.Session()
            session.trust_env = False
            session.headers.update({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            })

            base_url = (self.config.get("btsj6_base_url") or "https://www.btsj6.com/").strip() or "https://www.btsj6.com/"
            if not base_url.endswith('/'):
                base_url += '/'

            # 尽量模拟站点期望的导航路径：优先用 subject 作为 Referer（很多情况下不带 referer 会被跳回 subject）
            inferred_subject: str | None = None
            if not referer:
                try:
                    # 先用 base_url 作为 referer 访问一次 down.php，观察是否被跳转到 subject
                    r0 = session.get(url, headers={"Referer": base_url}, timeout=30, allow_redirects=True)
                    if r0.status_code == 200:
                        # 如果页面直接给了 magnet，这里直接返回
                        magnet0 = _extract_magnet_from_html(r0.text)
                        if magnet0:
                            return magnet0
                        final0 = (getattr(r0, "url", "") or "").strip()
                        if final0 and ("/subject/" in final0) and ("down.php" not in final0):
                            inferred_subject = final0
                except Exception:
                    inferred_subject = None

            referer_candidates: list[str] = []
            if referer:
                referer_candidates.append(referer)
            if inferred_subject and inferred_subject not in referer_candidates:
                referer_candidates.append(inferred_subject)
            if base_url not in referer_candidates:
                referer_candidates.append(base_url)

            r = None
            html = ""
            for ref in referer_candidates:
                try:
                    # 建会话：先访问 referer 页面（subject/base）
                    try:
                        session.get(ref, headers={"Referer": base_url}, timeout=20, allow_redirects=True)
                    except Exception:
                        pass

                    r_try = session.get(url, headers={"Referer": ref}, timeout=30, allow_redirects=True)
                    r_try.raise_for_status()
                    html_try = r_try.text or ""

                    # 0) 页面直接给出 magnet（或 href 含 &amp;），直接返回
                    magnet = _extract_magnet_from_html(html_try)
                    if magnet:
                        return magnet

                    # 若仍被跳回 subject，则从最终页再解析一次 magnet
                    final_url = (getattr(r_try, "url", "") or "").strip()
                    if final_url and ("/subject/" in final_url) and ("down.php" not in final_url):
                        try:
                            r_final = session.get(final_url, headers={"Referer": base_url}, timeout=30, allow_redirects=True)
                            if r_final.status_code == 200:
                                magnet2 = _extract_magnet_from_html(r_final.text)
                                if magnet2:
                                    return magnet2
                        except Exception:
                            pass

                    # 保留最后一次请求结果，供后续 file_id/fc 解析与 Selenium 兜底使用
                    r = r_try
                    html = html_try
                except Exception:
                    continue

            if not r:
                return None

            # 0.1) 仍可兜底：如果 down.php 最终页等于 referer，则从 referer 再解析一次
            if referer and getattr(r, "url", "") and r.url.rstrip("/") == referer.rstrip("/"):
                try:
                    r_ref = session.get(referer, headers={"Referer": base_url}, timeout=30, allow_redirects=True)
                    if r_ref.status_code == 200:
                        magnet2 = _extract_magnet_from_html(r_ref.text)
                        if magnet2:
                            return magnet2
                except Exception:
                    pass

            # 1) 解析 file_id / fc（兼容 : 或 =，单/双引号）
            file_id_match = (
                re.search(r"file_id\s*[:=]\s*\"(\d+)\"", html)
                or re.search(r"file_id\s*[:=]\s*'(\d+)'", html)
                or re.search(r"file_id\s*[:=]\s*(\d+)", html)
            )
            fc_match = (
                re.search(r"fc\s*[:=]\s*\"([^\"\\]+)\"", html)
                or re.search(r"fc\s*[:=]\s*'([^'\\]+)'", html)
                or re.search(r"fc\s*[:=]\s*([^,\s}]+)", html)
            )
            if not file_id_match or not fc_match:
                logging.warning(
                    f"BTSJ6 下载页未找到 file_id/fc（status={getattr(r,'status_code',None)} url={getattr(r,'url',url)}），可能需要 Referer/登录/站点结构变更"
                )
                # 尝试用 base_url 作为 Referer 再请求一次（部分站点会做防盗链）
                try:
                    r_retry = session.get(url, headers={"Referer": base_url}, timeout=30, allow_redirects=True)
                    r_retry.raise_for_status()
                    html2 = r_retry.text
                    magnet_match2 = re.search(r"(magnet:\?[^\s\"']+)", html2)
                    if magnet_match2:
                        magnet = _html.unescape(magnet_match2.group(1))
                        if magnet.startswith("magnet:"):
                            return magnet

                    file_id_match = (
                        re.search(r"file_id\s*[:=]\s*\"(\d+)\"", html2)
                        or re.search(r"file_id\s*[:=]\s*'(\d+)'", html2)
                        or re.search(r"file_id\s*[:=]\s*(\d+)", html2)
                    )
                    fc_match = (
                        re.search(r"fc\s*[:=]\s*\"([^\"\\]+)\"", html2)
                        or re.search(r"fc\s*[:=]\s*'([^'\\]+)'", html2)
                        or re.search(r"fc\s*[:=]\s*([^,\s}]+)", html2)
                    )
                    if not file_id_match or not fc_match:
                        # 最后一层兜底：用 Selenium 打开页面执行 JS，再抓 magnet
                        try:
                            self.load_config()
                        except Exception:
                            pass
                        try:
                            # 站点可能对 headless 返回不同内容/强制跳转，这里使用有界面模式更贴近真实浏览器
                            self.setup_webdriver(instance_id=12, headless=False)
                            if referer:
                                try:
                                    self.driver.get(referer)
                                except Exception:
                                    pass

                            # 关键点：一定要直接打开 down.php（不要用 requests 跳转后的 subject_url）
                            self.driver.get(url)
                            try:
                                logging.info(f"BTSJ6(Selenium) current_url={self.driver.current_url} title={self.driver.title}")
                            except Exception:
                                pass

                            # 如果站点仍然跳回了 subject，则再尝试打开最终页一次（兜底）
                            try:
                                final_url = getattr(r_retry, "url", "") or ""
                                if final_url and self.driver.current_url.rstrip('/') != url.rstrip('/'):
                                    if "subject/" in self.driver.current_url and "down.php" not in self.driver.current_url:
                                        # 有些情况下 down.php 会被拦截跳转，尝试直接访问最终页再扫一次
                                        self.driver.get(final_url)
                            except Exception:
                                pass

                            # 先从 page_source 直接扫 magnet（有时元素被隐藏/动态插入，选择器抓不到）
                            try:
                                src = self.driver.page_source or ""
                                magnet_src = _extract_magnet_from_html(src)
                                if magnet_src:
                                    return magnet_src
                            except Exception:
                                pass

                            try:
                                WebDriverWait(self.driver, 10).until(
                                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href^='magnet:'],#down_a1"))
                                )
                            except Exception:
                                pass

                            elements = self.driver.find_elements(By.CSS_SELECTOR, "a[href^='magnet:']")
                            if not elements:
                                el = None
                                try:
                                    el = self.driver.find_element(By.ID, "down_a1")
                                except Exception:
                                    el = None
                                if el is not None:
                                    elements = [el]

                            for el in elements:
                                href = (el.get_attribute("href") or "").strip()
                                href = _html.unescape(href)
                                if href.startswith("magnet:"):
                                    return href

                            # 如果 #down_a1 初始不是 magnet，尝试点击触发 JS 设置 href/复制内容
                            try:
                                btn = None
                                try:
                                    btn = self.driver.find_element(By.ID, "down_a1")
                                except Exception:
                                    btn = None

                                if btn is not None:
                                    try:
                                        btn.click()
                                    except Exception:
                                        try:
                                            self.driver.execute_script("arguments[0].click();", btn)
                                        except Exception:
                                            pass

                                    time.sleep(1.5)
                                    href2 = _html.unescape((btn.get_attribute("href") or "").strip())
                                    if href2.startswith("magnet:"):
                                        return href2

                                    # 有的站点把磁力放在 data-clipboard-text / data-href
                                    for attr in ["data-clipboard-text", "data-href", "data-url"]:
                                        v = _html.unescape((btn.get_attribute(attr) or "").strip())
                                        if v.startswith("magnet:"):
                                            return v

                                    # 再扫一次 page_source
                                    try:
                                        src2 = self.driver.page_source or ""
                                        magnet_src2 = _extract_magnet_from_html(src2)
                                        if magnet_src2:
                                            return magnet_src2
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                        except Exception:
                            return None
                        return None
                    html = html2
                    r = r_retry
                except Exception:
                    return None

            file_id = file_id_match.group(1)
            fc = fc_match.group(1)
            api_url = urljoin(base_url, "callfile/callfile.php")

            r2 = session.post(
                api_url,
                data={"file_id": file_id, "fc": fc},
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "Referer": r.url,
                },
                timeout=30,
            )
            r2.raise_for_status()
            try:
                data = r2.json()
            except Exception:
                logging.warning("BTSJ6 callfile 返回非 JSON，无法解析 magnet")
                return None
            if isinstance(data, dict) and data.get("error") == 0 and data.get("down"):
                down = str(data.get("down"))
                if down.startswith("magnet:"):
                    return down
            return None
        except Exception as e:
            logging.warning(f"BTSJ6 解析 magnet 失败: {e}")
            return None

    def btsj6_download_torrent(self, result, title_text, **_kwargs):
        """BT世界网：优先解析磁力并直接添加下载任务（不依赖 Selenium 点击下载）。"""
        link = (result.get("link") or "").strip()
        if not link:
            raise Exception("缺少下载链接")

        referer = ((result.get("subject_url") or result.get("referer") or _kwargs.get("referer") or "").strip()) or None

        # 1) 索引器若已给出 magnet，直接添加
        if link.startswith("magnet:"):
            torrent_path = self._save_torrent_from_magnet(link, title_text)
            if torrent_path:
                return
            ok = self.add_magnet_task(link, title_text)
            if not ok:
                raise Exception("磁力已解析，但无法保存种子且添加磁力任务失败")
            return

        # 2) 传入的是 down.php / 下载页 URL，则尝试无头解析出 magnet 再添加
        magnet = self._btsj6_resolve_magnet(link, referer=referer)
        if magnet:
            torrent_path = self._save_torrent_from_magnet(magnet, title_text)
            if torrent_path:
                return
            ok = self.add_magnet_task(magnet, title_text)
            if not ok:
                raise Exception("已解析出磁力，但无法保存种子且添加磁力任务失败")
            return

        # 3) 兜底：提示用户该链接不是磁力且无法解析
        raise Exception("BTSJ6 下载链接无法解析为磁力（可能站点结构变更/网络拦截）。")

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

    def close_popup_if_exists(self):
        """
        关闭观影站点可能出现的提示框
        处理多种类型的弹窗，包括：
        1. 带有"14天内不再提醒"按钮的弹窗
        2. 带有右上角关闭按钮的弹窗
        """
        try:
            # 首先尝试查找并点击"14天内不再提醒"按钮
            popup_footer_button = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".popup-footer button"))
            )
            popup_footer_button.click()
            logging.info("成功点击'14天内不再提醒'按钮关闭弹窗")
            
            # 等待弹窗消失
            WebDriverWait(self.driver, 5).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, "popup-wrapper"))
            )
            return
        except TimeoutException:
            pass  # 继续尝试其他关闭方式
        except Exception as e:
            logging.warning(f"尝试点击'14天内不再提醒'按钮时出错: {e}")
        
        try:
            # 如果上面的方法失败，尝试点击右上角的关闭按钮
            popup_close_button = WebDriverWait(self.driver, 3).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, ".popup-close"))
            )
            popup_close_button.click()
            logging.info("成功点击右上角关闭按钮关闭弹窗")
            
            # 等待弹窗消失
            WebDriverWait(self.driver, 5).until(
                EC.invisibility_of_element_located((By.CLASS_NAME, "popup-wrapper"))
            )
            return
        except TimeoutException:
            pass  # 继续尝试其他关闭方式
        except Exception as e:
            logging.warning(f"尝试点击右上角关闭按钮时出错: {e}")
        
        try:
            # 如果上面的方法都失败，使用JavaScript隐藏弹窗
            popup_wrapper = WebDriverWait(self.driver, 3).until(
                EC.presence_of_element_located((By.CLASS_NAME, "popup-wrapper"))
            )
            self.driver.execute_script("""
                var popupWrapper = document.querySelector('.popup-wrapper');
                if (popupWrapper) {
                    popupWrapper.style.display = 'none';
                }
            """)
            logging.info("使用JavaScript隐藏弹窗")
            return
        except TimeoutException:
            logging.info("未检测到提示框，无需操作")
        except Exception as e:
            logging.warning(f"尝试使用JavaScript隐藏弹窗时出错: {e}")

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

    def is_logged_in_gy(self):
        try:
            # 检查页面中是否存在特定的提示文本
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_element_located((By.XPATH, "//*[contains(text(), '登录成功')]"))
            )
            return True
        except TimeoutException:
            try:
                # 访问用户账户页面检查是否已登录
                self.driver.get(self.gy_user_info_url)
                # 检查是否存在账户设置相关的元素
                WebDriverWait(self.driver, 5).until(
                    EC.presence_of_element_located((By.XPATH, "//h2[contains(text(), '账户设置')]"))
                )
                # 检查用户名输入框是否存在且被禁用（表明已登录）
                username_input = self.driver.find_element(By.NAME, "username")
                if username_input.get_attribute("disabled") == "true":
                    logging.info("通过账户设置页面确认用户已登录")
                    # 关闭可能存在的提示框
                    self.close_popup_if_exists()
                    return True
            except TimeoutException:
                pass
            except Exception as e:
                logging.warning(f"检查登录状态时发生错误: {e}")
            return False

    def login_bthd_site(self, username, password):
        login_url = self.movie_login_url
        self.site_captcha(login_url)  # 使用新的统一验证码处理方法
        self.driver.get(login_url)
        try:
            # 检查是否已经自动登录
            if self.is_logged_in():
                logging.info("自动登录成功，无需再次登录")
                return
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            logging.info("电影站点登录页面加载完成")
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
            logging.info("电影站点登录成功！")
        except TimeoutException:
            logging.error("电影站点登录失败或页面未正确加载，未找到预期元素！")
            self.close_driver()
            raise

    def login_hdtv_site(self, username, password):
        login_url = self.tv_login_url
        self.site_captcha(login_url)  # 使用新的统一验证码处理方法
        self.driver.get(login_url)
        try:
            # 检查是否已经自动登录
            if self.is_logged_in():
                logging.info("自动登录成功，无需再次登录")
                return
            
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.NAME, "username"))
            )
            logging.info("电视剧站点登录页面加载完成")
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
            logging.info("电视剧站点登录成功！")
        except TimeoutException:
            logging.error("电视剧站点登录失败或页面未正确加载，未找到预期元素！")
            self.close_driver()
            raise

    def login_gy_site(self, username, password):
        login_url = self.gy_login_url
        user_info_url = self.gy_user_info_url
        self.site_captcha(login_url)  # 使用新的统一验证码处理方法
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
            # 在输入用户名和密码之前，先关闭可能存在的弹窗
            self.close_popup_if_exists()
            username_input = self.driver.find_element(By.NAME, 'username')
            password_input = self.driver.find_element(By.NAME, 'password')
            username_input.send_keys(username)
            password_input.send_keys(password)
            # 勾选自动登录选项
            auto_login_checkbox = self.driver.find_element(By.NAME, 'cookietime')
            if not auto_login_checkbox.is_selected():
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
            # 关闭可能存在的提示框
            self.close_popup_if_exists()
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
        """从数据库读取订阅电视节目信息和缺失的集数信息"""
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
        
        logging.debug("读取缺失的电视节目信息和缺失的集数信息完成")
        return all_tv_info

    def bthd_download_torrent(self, result, title_text, year=None, resolution=None, title=None):
        """高清影视之家解析并下载种子文件"""
        try:
            self.login_bthd_site(self.config["bt_login_username"], self.config["bt_login_password"])
            # 检查页面是否有验证码
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "cl")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                links = self.driver.find_elements(By.TAG_NAME, "a")
                for link in links:
                    link_text = link.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not title:
                            title = title_text
                        new_name = f"{title} ({year})-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise
    
    def hdtv_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """高清剧集网解析并下载种子文件"""
        try:
            self.login_hdtv_site(self.config["bt_login_username"], self.config["bt_login_password"])
            # 检查页面是否有验证码
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "plc")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.TAG_NAME, "a"))
                )
                links = self.driver.find_elements(By.TAG_NAME, "a")
                for link in links:
                    link_text = link.text.strip().lower()
                    if "torrent" in link_text:
                        attachment_url = link.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not season:
                            season = ""
                        if not episode_range:
                            episode_range = "未知集数"
                        if not title:
                            title = title_text
                        new_name = f"{title} ({year})-S{season}-[{episode_range}]-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise

    def btys_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """BT影视解析并下载种子文件"""
        try:
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载页面按钮...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "video-info-main")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "btn-aux"))
                )
                links = self.driver.find_elements(By.CLASS_NAME, "btn-aux")
                for link in links:
                    link_text = link.text.strip()
                    if "下载种子" in link_text:
                        attachment_url = link.get_attribute('href')
                        # 点击“下载种子文件”按钮
                        self.driver.execute_script("arguments[0].click();", link)
                        self.driver.switch_to.window(self.driver.window_handles[-1])
                        logging.info("已点击“下载种子文件”按钮，进入下载页面")
                        break

                if not attachment_url:
                    self.driver.close()  # 关闭新标签页
                    self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                    logging.warning(f"没有找到种子文件下载页面按钮，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                # 等待“点击下载”按钮可点击，并用 ActionChains 模拟真实鼠标点击
                try:
                    # 最长等待15秒，直到按钮可点击
                    download_btn = WebDriverWait(self.driver, 15).until(
                        EC.element_to_be_clickable((By.ID, "link"))
                    )
                    # 再加一点点延迟，确保倒计时动画和事件都绑定完毕
                    time.sleep(0.5)
                    actions = ActionChains(self.driver)
                    actions.move_to_element(download_btn).click().perform()
                    logging.info("已点击“点击下载”按钮，开始下载种子文件...")
                    self.driver.close()  # 关闭新标签页
                    self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                except TimeoutException:
                    logging.error("未找到“点击下载”按钮，无法下载种子文件")
                    self.driver.close()  # 关闭新标签页
                    self.driver.switch_to.window(self.driver.window_handles[0])  # 切回原标签页
                    raise Exception("未找到“点击下载”按钮，无法下载种子文件")

                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not title:
                            title = title_text
                        # 判断是否为电视剧命名
                        if season and episode_range:
                            new_name = f"{title} ({year})-S{season}-[{episode_range}]-{resolution}.torrent"
                        else:
                            new_name = f"{title} ({year})-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise

    def bt0_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """不太灵影视解析并下载种子文件"""
        try:
            self.site_captcha(result['link'])  # 使用新的统一验证码处理方法
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "tr-actions")))
            logging.info("页面加载完成")

            attachment_url = None
            max_retries = 5
            retries = 0

            while not attachment_url and retries < max_retries:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CLASS_NAME, "download-link"))
                )
                links = self.driver.find_elements(By.CLASS_NAME, "download-link")
                for link in links:
                    link_text = link.text.strip()
                    if "下载种子" in link_text:
                        attachment_url = link.get_attribute('href')
                        break

                if not attachment_url:
                    logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                    time.sleep(2)
                    retries += 1

            if attachment_url:
                logging.info(f"找到种子文件下载链接: {attachment_url}")
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title参数与title_text相同来判断）
                    if title and title == title_text:
                        # 手动模式下只使用标题命名
                        new_name = f"{title}.torrent"
                    else:
                        # 自动模式下使用完整命名
                        if not resolution:
                            resolution = "未知分辨率"
                        if not year:
                            year = ""
                        if not title:
                            title = title_text
                        # 判断是否为电视剧命名
                        if season and episode_range:
                            new_name = f"{title} ({year})-S{season}-[{episode_range}]-{resolution}.torrent"
                        else:
                            new_name = f"{title} ({year})-{resolution}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
            else:
                raise Exception("经过多次重试后仍未找到种子文件下载链接")

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise
    
    def gy_download_torrent(self, result, title_text, year=None, season=None, episode_range=None, resolution=None, title=None):
        """观影解析并下载种子文件"""
        try:
            self.login_gy_site(self.config["gy_login_username"], self.config["gy_login_password"])
            self.driver.get(result['link'])
            logging.info(f"进入：{title_text} 详情页面...")
            # 关闭可能存在的提示框
            self.close_popup_if_exists()
            logging.info(f"开始查找种子文件下载链接...")

            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.CLASS_NAME, "alert-info0")))
            logging.info("页面加载完成")

            # 检查是哪种形式：普通 torrent 形式还是 folder 形式
            is_folder_form = False
            try:
                self.driver.find_element(By.CSS_SELECTOR, ".down321")
                is_folder_form = True
                logging.info("检测到 folder 形式页面")
            except:
                logging.info("检测到普通 torrent 形式页面")

            if is_folder_form:
                # 处理 folder 形式
                self._handle_gy_folder_form(title_text, year, season, episode_range, resolution)  # 传递 title_text
            else:
                # 处理普通 torrent 形式，传递title参数用于命名
                self._handle_gy_normal_form(title_text, year, season, episode_range, resolution)  # 传递 title_text

        except TimeoutException:
            logging.error("种子文件下载链接加载超时")
            raise
        except Exception as e:
            logging.error(f"下载种子文件过程中出错: {e}")
            raise

    def _handle_gy_folder_form(self, title_text, year, season, episode_range, resolution):
        """处理观影网站的 folder 形式下载"""
        gy_base_url = self.config.get("gy_base_url", "")
        
        # 获取所有子资源项
        folder_items = WebDriverWait(self.driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".down321 li"))
        )
        
        logging.info(f"找到 {len(folder_items)} 个子资源项")
        
        # 预先收集所有子资源的信息，避免在循环中因页面跳转导致元素失效
        items_info = []
        for idx in range(len(folder_items)):
            try:
                # 重新获取所有子资源项以避免 stale element reference
                folder_items = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".down321 li"))
                )
                item = folder_items[idx]
                
                # 提取资源标题
                title_element = item.find_element(By.CSS_SELECTOR, "div:first-child")
                folder_title = title_element.text.strip()
                
                # 查找包含"种子下载"文本的链接
                seed_links = item.find_elements(By.CSS_SELECTOR, ".right span a")
                attachment_url = None
                
                for link in seed_links:
                    link_text = link.text.strip()
                    if "种子下载" in link_text:
                        attachment_url = link.get_attribute('href')
                        break
                
                if attachment_url:
                    # 如果是相对路径，需要拼接完整URL
                    if attachment_url.startswith('/'):
                        attachment_url = gy_base_url + attachment_url
                    
                    items_info.append({
                        'title': folder_title,
                        'url': attachment_url
                    })
                    logging.info(f"收集到种子文件下载链接: {attachment_url}")
                else:
                    logging.warning(f"未找到种子下载链接 for item {idx}")
            except Exception as e:
                logging.error(f"收集子资源信息时出错: {e}")
                continue
        
        # 处理每个子资源的下载
        for item_info in items_info:
            try:
                folder_title = item_info['title']
                attachment_url = item_info['url']
                
                logging.info(f"开始下载种子文件: {folder_title}")
                self.driver.get(attachment_url)
                time.sleep(10)
                
                # 重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    new_name = f"{folder_title}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    logging.error(f"未能找到下载的种子文件: {folder_title}")
            except Exception as e:
                logging.error(f"处理子资源时出错: {e}")
                continue

    def _handle_gy_normal_form(self, title_text, year, season, episode_range, resolution):
        """处理观影网站的普通 torrent 形式下载"""
        attachment_urls = []
        max_retries = 5
        retries = 0

        while not attachment_urls and retries < max_retries:
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".down123 li a"))
                )
                # 查找所有下载链接
                links = self.driver.find_elements(By.CSS_SELECTOR, ".down123 li a")
                
                for link in links:
                    link_text = link.text.strip()
                    # 精确查找包含"种子下载"文本的链接
                    if "种子下载" in link_text:
                        attachment_url = link.get_attribute('href')
                        if attachment_url:
                            attachment_urls.append(attachment_url)
                            logging.info(f"找到种子下载链接: {attachment_url}")
            except TimeoutException:
                pass

            if not attachment_urls:
                logging.warning(f"没有找到种子文件下载链接，重试中... ({retries + 1}/{max_retries})")
                time.sleep(2)
                retries += 1

        if attachment_urls:
            gy_base_url = self.config.get("gy_base_url", "")
            for attachment_url in attachment_urls:
                # 如果是相对路径，需要拼接完整URL
                if attachment_url.startswith('/'):
                    attachment_url = gy_base_url + attachment_url
                self.driver.get(attachment_url)
                logging.info("开始下载种子文件...")
                time.sleep(10)
                # 新增：重命名种子文件
                latest_torrent = get_latest_torrent_file()
                if latest_torrent:
                    # 判断是否为手动模式（通过title_text参数与传入的title_text相同来判断）
                    # 在普通下载中，使用title_text作为文件名
                    new_name = f"{title_text}.torrent"
                    rename_torrent_file(latest_torrent, new_name)
                else:
                    raise Exception("未能找到下载的种子文件")
        else:
            raise Exception("经过多次重试后仍未找到种子文件下载链接")
        
    def process_movie_downloads(self):
        """处理电影下载任务"""
        # 读取订阅的电影信息
        all_movie_info = self.extract_movie_info()

        # 定义来源优先级
        sources_priority = ["BTHD", "BT0", "BTYS", "GY", "SEEDHUB", "BTSJ6", "1LOU"]
        if str(self.config.get("jackett_enabled", "False")).strip().lower() == "true":
            # Jackett 作为聚合入口：默认放在 GY 后面、SEEDHUB 前面
            if "JACKETT" not in sources_priority:
                sources_priority.insert(4, "JACKETT")
        
        # 获取优先关键词配置
        prefer_keywords = self.config.get("resources_prefer_keywords", "")
        prefer_keywords_list = [kw.strip() for kw in prefer_keywords.split(",") if kw.strip()]

        # 遍历每部电影信息
        for movie in all_movie_info:
            title = movie["标题"]
            year = movie["年份"]
            
            # 遍历来源优先级
            download_success = False
            for source in sources_priority:
                index_file_name = f"{title}-{year}-{source}.json"
                index_file_path = os.path.join("/tmp/index", index_file_name)

                # 检查索引文件是否存在
                if not os.path.exists(index_file_path):
                    logging.warning(f"索引文件不存在: {index_file_path}，尝试下一个来源")
                    continue

                # 读取索引文件内容
                try:
                    with open(index_file_path, 'r', encoding='utf-8') as f:
                        index_data = json.load(f)
                except Exception as e:
                    logging.error(f"读取索引文件时出错: {index_file_path}, 错误: {e}")
                    continue

                # 根据优先关键词和热度对下载结果进行排序的函数
                def sort_key(result):
                    # 关键词匹配优先级
                    keyword_score = 0
                    if prefer_keywords_list:
                        title_text = result.get("title", "").lower()
                        keyword_score = sum(1 for kw in prefer_keywords_list if kw.lower() in title_text)
                    
                    # 热度值优先级（如果存在）
                    popularity = result.get("popularity", 0)
                    
                    # 排序规则：首先按关键词匹配数降序，然后按热度降序
                    return (-keyword_score, -popularity)

                # 按分辨率优先级选择资源
                selected_result = None
                selected_resolution_type = None
                resolution_priorities = ["首选分辨率", "备选分辨率", "其他分辨率"]

                # 按优先级顺序检查各分辨率类型
                for resolution_type in resolution_priorities:
                    if index_data.get(resolution_type):
                        # 对当前分辨率类型中的资源按热度和关键词排序
                        sorted_results = sorted(index_data[resolution_type], key=sort_key)
                        if sorted_results:
                            selected_result = sorted_results[0]
                            selected_resolution_type = resolution_type
                            break

                # 如果找到了合适的资源，则进行下载
                if selected_result:
                    download_title = selected_result.get("title")
                    logging.info(f"在来源 {source} 中找到匹配结果: {download_title} (分辨率类型: {selected_resolution_type})")
                    
                    # 获取下载链接和标题
                    link = selected_result.get("link")
                    resolution = selected_result.get("resolution")

                    if not link:
                        logging.warning(f"未找到种子下载链接: {title} ({year})，尝试下一个结果")
                        continue

                    # 根据来源调用相应的下载方法，重命名时传递title参数
                    logging.info(f"开始下载: {download_title} ({resolution}) 来源: {source}")
                    try:
                        if source == "BTHD":
                            self.bthd_download_torrent(selected_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "BTYS":
                            self.btys_download_torrent(selected_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "BT0":
                            self.bt0_download_torrent(selected_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "GY":
                            self.gy_download_torrent(selected_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "SEEDHUB":
                            self.seedhub_download_torrent(selected_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "JACKETT":
                            self.jackett_download_torrent(selected_result, download_title, referer=selected_result.get("referer") or selected_result.get("subject_url"))
                        elif source == "BTSJ6":
                            self.btsj6_download_torrent(selected_result, download_title, year=year, resolution=resolution, title=title)
                        elif source == "1LOU":
                            self.onelou_download_torrent(selected_result, download_title, year=year, resolution=resolution, title=title)
                        
                        # 下载成功
                        download_success = True
                        logging.info(f"电影下载成功: {title} ({year})")
                        
                        # 下载成功后，更新数据库，标记该电影已完成订阅
                        try:
                            with sqlite3.connect(self.db_path) as conn:
                                cursor = conn.cursor()
                                cursor.execute(
                                    "DELETE FROM MISS_MOVIES WHERE title=? AND year=?",
                                    (title, year)
                                )
                                conn.commit()
                            logging.info(f"已更新订阅数据库，移除已完成的电影订阅: {title} ({year})")
                        except Exception as e:
                            logging.error(f"更新订阅数据库时出错: {e}")
                        
                        # 只有下载成功时才发送通知
                        self.send_notification(movie, download_title, resolution)
                        break  # 不再尝试当前来源的其他结果
                        
                    except Exception as e:
                        logging.error(f"下载过程中发生错误: {e}，尝试当前来源的下一个结果")
                        continue  # 继续尝试当前来源的其他结果
                else:
                    logging.warning(f"在来源 {source} 中未找到任何匹配结果")
                
                # 如果当前来源中有成功下载的，就不再尝试其他来源
                if download_success:
                    break
            
            if not download_success:
                logging.error(f"所有来源都尝试失败，未能下载电影: {title} ({year})")

    def process_tvshow_downloads(self):
        """处理电视节目下载任务"""
        # 读取订阅的电视节目信息
        all_tv_info = self.extract_tv_info()

        # 定义来源优先级
        sources_priority = ["HDTV", "BT0", "BTYS", "GY", "SEEDHUB", "BTSJ6", "1LOU"]
        if str(self.config.get("jackett_enabled", "False")).strip().lower() == "true":
            if "JACKETT" not in sources_priority:
                sources_priority.insert(4, "JACKETT")
        
        # 获取优先关键词配置
        prefer_keywords = self.config.get("resources_prefer_keywords", "")
        prefer_keywords_list = [kw.strip() for kw in prefer_keywords.split(",") if kw.strip()]

        # 遍历每个电视节目信息
        for tvshow in all_tv_info:
            title = tvshow["剧集"]
            year = tvshow["年份"]
            season = tvshow["季"]
            missing_episodes = sorted(map(int, tvshow["缺失集数"]))  # 转换为整数集合并排序
            logging.debug(f"缺失集数: {missing_episodes}")
            
            original_missing_episodes = missing_episodes[:]  # 保存原始缺失集数列表
            successfully_downloaded_episodes = []  # 记录成功下载的集数

            # 创建一个集合来跟踪已经处理过的资源，避免重复下载
            processed_resources = set()
            
            # 添加标志位，用于标识是否已经下载了全集
            full_season_downloaded = False

            # 按集数分组处理，确保每集都能尝试不同来源
            for episode in missing_episodes[:]:  # 使用副本以避免在迭代时修改列表
                # 如果这一集已经被下载过了（在多集资源中），则跳过
                if episode in successfully_downloaded_episodes or full_season_downloaded:
                    continue
                    
                episode_downloaded = False
                
                # 遍历来源优先级
                for source in sources_priority:
                    index_file_name = f"{title}-S{season}-{year}-{source}.json"
                    index_file_path = os.path.join("/tmp/index", index_file_name)

                    # 检查索引文件是否存在
                    if not os.path.exists(index_file_path):
                        logging.warning(f"索引文件不存在: {index_file_path}，尝试下一个来源")
                        continue

                    # 读取索引文件内容
                    try:
                        with open(index_file_path, 'r', encoding='utf-8') as f:
                            index_data = json.load(f)
                    except Exception as e:
                        logging.error(f"读取索引文件时出错: {index_file_path}, 错误: {e}")
                        continue

                    # 定义分辨率优先级
                    resolution_priorities = ["首选分辨率", "备选分辨率", "其他分辨率"]
                    
                    # 定义资源类型的优先级映射
                    item_type_priority = {
                        "全集": 0,
                        "集数范围": 1,
                        "单集": 2
                    }
                    
                    # 根据优先关键词和热度对下载结果进行排序的函数
                    def sort_key(result):
                        # 关键词匹配优先级
                        keyword_score = 0
                        if prefer_keywords_list:
                            title_text = result.get("title", "").lower()
                            keyword_score = sum(1 for kw in prefer_keywords_list if kw.lower() in title_text)
                        
                        # 热度值优先级（如果存在）
                        popularity = result.get("popularity", 0)
                        
                        # 排序规则：首先按关键词匹配数降序，然后按热度降序
                        return (-keyword_score, -popularity)

                    # 按资源类型和分辨率优先级选择资源
                    selected_result = None
                    selected_resolution_type = None
                    selected_item_type = None

                    # 按资源类型优先级遍历
                    for item_type in ["全集", "集数范围", "单集"]:
                        if selected_result:
                            break
                        
                        # 在同一资源类型内按分辨率优先级遍历
                        for resolution_type in resolution_priorities:
                            if selected_result:
                                break
                                
                            # 收集当前类型和分辨率下的资源
                            current_options = []
                            
                            # 根据类型收集相应资源
                            if item_type == "全集":
                                for item in index_data.get(resolution_type, {}).get("全集", []):
                                    if (item.get("start_episode") is not None and item.get("end_episode") is not None and
                                        int(item["start_episode"]) <= episode <= int(item["end_episode"])):
                                        current_options.append(item)
                            elif item_type == "集数范围":
                                for item in index_data.get(resolution_type, {}).get("集数范围", []):
                                    if (item.get("start_episode") is not None and item.get("end_episode") is not None and
                                        int(item["start_episode"]) <= episode <= int(item["end_episode"])):
                                        current_options.append(item)
                            elif item_type == "单集":
                                for item in index_data.get(resolution_type, {}).get("单集", []):
                                    if item.get("start_episode") is not None and int(item["start_episode"]) == episode:
                                        current_options.append(item)
                            
                            # 如果当前类型和分辨率下有资源，则按关键词和热度排序，选择最佳资源
                            if current_options:
                                # 对当前选项排序并选择最佳资源
                                sorted_options = sorted(current_options, key=sort_key)
                                selected_result = sorted_options[0]
                                selected_resolution_type = resolution_type
                                selected_item_type = item_type
                                break

                    # 如果找到了合适的资源，则进行下载
                    if selected_result:
                        # 创建资源唯一标识符
                        resource_identifier = (source, selected_result.get("title"), 
                                            selected_result.get("link"), 
                                            selected_result.get("start_episode"), 
                                            selected_result.get("end_episode"))
                        
                        # 如果这个资源已经被处理过，跳过
                        if resource_identifier in processed_resources:
                            continue
                        
                        # 找到匹配结果，尝试下载
                        logging.info(f"在来源 {source} 中找到匹配结果: {selected_result['title']} (类型: {selected_item_type}, 分辨率优先级: {selected_resolution_type})")
                        
                        # 处理集数范围命名
                        start_ep = selected_result.get("start_episode")
                        end_ep = selected_result.get("end_episode")
                        
                        # 计算本次下载包含的集数
                        if start_ep and end_ep:
                            episode_nums = list(range(int(start_ep), int(end_ep) + 1))
                        elif start_ep:
                            episode_nums = [int(start_ep)]
                        else:
                            episode_nums = []
                        
                        # 检查这些集数是否都已经下载过了
                        already_downloaded = any(ep in successfully_downloaded_episodes for ep in episode_nums)
                        if already_downloaded:
                            # 标记这个资源已处理，避免重复尝试
                            processed_resources.add(resource_identifier)
                            continue
                        
                        # 处理集数范围
                        if start_ep and end_ep:
                            if int(start_ep) == int(end_ep):
                                episode_range = f"{start_ep}集"
                            elif int(start_ep) == 1 and int(end_ep) > 1 and selected_result.get("is_full_season", False):
                                episode_range = f"全{end_ep}集"
                            else:
                                episode_range = f"{start_ep}-{end_ep}集"
                        elif start_ep:
                            episode_range = f"{start_ep}集"
                        else:
                            episode_range = "未知集数"
                            
                        resolution = selected_result.get("resolution")
                        
                        # 尝试下载
                        try:
                            if source == "HDTV":
                                self.hdtv_download_torrent(selected_result, selected_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif source == "BTYS":
                                self.btys_download_torrent(selected_result, selected_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif source == "BT0":
                                self.bt0_download_torrent(selected_result, selected_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif source == "GY":
                                self.gy_download_torrent(selected_result, selected_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif source == "SEEDHUB":
                                self.seedhub_download_torrent(selected_result, selected_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif source == "JACKETT":
                                self.jackett_download_torrent(selected_result, selected_result["title"], referer=selected_result.get("referer") or selected_result.get("subject_url"))
                            elif source == "BTSJ6":
                                self.btsj6_download_torrent(selected_result, selected_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            elif source == "1LOU":
                                self.onelou_download_torrent(selected_result, selected_result["title"], year=year, season=season, episode_range=episode_range, resolution=resolution, title=title)
                            
                            # 下载成功
                            successfully_downloaded_episodes.extend(episode_nums)
                            logging.info(f"成功下载集数: {episode_nums}")
                            self.send_notification(tvshow, selected_result["title"], resolution)
                            
                            # 标记这个资源已处理
                            processed_resources.add(resource_identifier)
                            
                            # 如果下载的是全集，则标记全集已下载
                            if selected_item_type == "全集":
                                full_season_downloaded = True
                                logging.info(f"全集已下载，跳过该季其余集数的处理")
                                # 标记该全集包含的所有集数为已下载
                                if start_ep and end_ep:
                                    all_episodes_in_full = list(range(int(start_ep), int(end_ep) + 1))
                                    for ep in all_episodes_in_full:
                                        if ep not in successfully_downloaded_episodes:
                                            successfully_downloaded_episodes.append(ep)
                            
                            episode_downloaded = True
                            break  # 不再尝试当前来源的其他结果
                            
                        except Exception as e:
                            logging.error(f"下载失败: {selected_result['title']}, 错误: {e}，尝试当前来源的下一个结果")
                            # 标记这个资源已处理，避免重复尝试
                            processed_resources.add(resource_identifier)
                            # 继续尝试当前来源的其他结果
                    else:
                        logging.warning(f"在来源 {source} 中未找到任何匹配结果")
                    
                    # 如果当前来源中有成功下载的，就不再尝试其他来源
                    if episode_downloaded:
                        break
                
                if not episode_downloaded:
                    logging.warning(f"集数 {episode} 下载失败，所有来源均已尝试")
                
                # 如果已经下载了全集，则跳出集数循环
                if full_season_downloaded:
                    break

            # 只对实际下载成功的集数更新数据库
            if successfully_downloaded_episodes:
                try:
                    with sqlite3.connect(self.db_path) as conn:
                        cursor = conn.cursor()
                        # 查询当前缺失集数
                        cursor.execute(
                            "SELECT missing_episodes FROM MISS_TVS WHERE title=? AND year=? AND season=?",
                            (title, year, season)
                        )
                        row = cursor.fetchone()
                        if row:
                            current_missing = [ep.strip() for ep in row[0].split(',') if ep.strip()]
                            # 计算剩余缺失集（从原始缺失集中移除成功下载的集数）
                            updated_missing = [ep for ep in current_missing if ep and int(ep) not in successfully_downloaded_episodes]
                            if updated_missing:
                                # 还有未下载的缺失集，更新数据库
                                cursor.execute(
                                    "UPDATE MISS_TVS SET missing_episodes=? WHERE title=? AND year=? AND season=?",
                                    (",".join(updated_missing), title, year, season)
                                )
                                logging.info(f"部分集数已下载，剩余缺失集数已更新: {title} S{season} ({year})，剩余缺失集: {updated_missing}")
                            else:
                                # 所有缺失集已下载，删除订阅
                                cursor.execute(
                                    "DELETE FROM MISS_TVS WHERE title=? AND year=? AND season=?",
                                    (title, year, season)
                                )
                                logging.info(f"所有缺失集已下载，已完成订阅并移除: {title} S{season} ({year})")
                            conn.commit()
                except Exception as e:
                    logging.error(f"更新订阅数据库时出错: {e}")

            # 计算仍然未找到匹配的集数
            still_missing = [ep for ep in original_missing_episodes if ep not in successfully_downloaded_episodes]
            if still_missing:
                logging.warning(f"未找到匹配的下载结果或下载失败: {title} S{season} ({year}) 缺失集数: {still_missing}")

    def close_driver(self):
        if self.driver:
            self.driver.quit()
            logging.info("WebDriver关闭完成")
            self.driver = None  # 重置 driver 变量

    def run(self):
        """运行程序的主逻辑"""
        try:
            # 加载配置文件
            self.load_config()
            # 初始化WebDriver
            self.setup_webdriver()
            # 获取基础 URL
            bt_movie_base_url = self.config.get("bt_movie_base_url", "")
            self.movie_login_url = f"{bt_movie_base_url}/member.php?mod=logging&action=login"
            bt_tv_base_url = self.config.get("bt_tv_base_url", "")
            self.tv_login_url = f"{bt_tv_base_url}/member.php?mod=logging&action=login"
            gy_base_url = self.config.get("gy_base_url", "")
            self.gy_login_url = f"{gy_base_url}/user/login"
            self.gy_user_info_url = f"{gy_base_url}/user/account"
    
            # 获取订阅电影信息
            all_movie_info = self.extract_movie_info()
    
            # 获取订阅电视节目信息
            all_tv_info = self.extract_tv_info()
    
            # 检查订阅信息并运行对应任务
            if not all_movie_info and not all_tv_info:
                logging.info("数据库中没有有效订阅，无需执行后续操作")
                return  # 退出程序
    
            if all_movie_info:
                logging.info("检测到有效的电影订阅信息，开始处理电影下载任务")
                self.process_movie_downloads()
            else:
                logging.info("没有检测到有效的电影订阅信息，跳过电影下载任务")
    
            if all_tv_info:
                logging.info("检测到有效的电视节目订阅信息，开始处理电视节目下载任务")
                self.process_tvshow_downloads()
            else:
                logging.info("没有检测到有效的电视节目订阅信息，跳过电视节目下载任务")
    
        except Exception as e:
            logging.error(f"程序运行时发生错误: {e}")
        finally:
            # 确保程序结束时关闭 WebDriver
            self.close_driver()

if __name__ == "__main__":
    # 创建命令行参数解析器
    parser = argparse.ArgumentParser(description="Media Downloader")
    parser.add_argument("--site", type=str, help="下载站点名称，例如 BT0、BTHD、GY 等")
    parser.add_argument("--title", type=str, help="下载的标题")
    parser.add_argument("--link", type=str, help="下载链接")
    parser.add_argument("--referer", type=str, default="", help="可选：下载链接的 Referer/来源页面（用于防盗链/解析）")
    args = parser.parse_args()

    downloader = MediaDownloader()

    if args.site and args.title and args.link:
        # 如果提供了命令行参数，则手动运行下载功能
        try:
            downloader.load_config()
            site_upper = args.site.upper()
            selenium_sites = {"BTHD", "BTYS", "BT0", "GY", "HDTV", "SEEDHUB"}
            if site_upper in selenium_sites:
                if site_upper == "SEEDHUB":
                    seedhub_headful = (os.environ.get("SEEDHUB_HEADFUL") or "").strip().lower() in {"1", "true", "yes", "on"}
                    downloader.setup_webdriver(headless=not seedhub_headful)
                else:
                    downloader.setup_webdriver()

            # 初始化相关 URL 属性
                bt_movie_base_url = downloader.config.get("bt_movie_base_url", "")
                downloader.movie_login_url = f"{bt_movie_base_url}/member.php?mod=logging&action=login"
                bt_tv_base_url = downloader.config.get("bt_tv_base_url", "")
                downloader.tv_login_url = f"{bt_tv_base_url}/member.php?mod=logging&action=login"
                gy_base_url = downloader.config.get("gy_base_url", "")
                downloader.gy_login_url = f"{gy_base_url}/user/login"
                downloader.gy_user_info_url = f"{gy_base_url}/user/account"

            logging.info(f"手动运行下载功能，站点: {args.site}, 标题: {args.title}, 链接: {args.link}")
            # 修改各下载函数调用，添加is_manual参数
            if site_upper == "BTHD":
                downloader.bthd_download_torrent({"link": args.link}, args.title, title=args.title)
            elif site_upper == "BTYS":
                downloader.btys_download_torrent({"link": args.link}, args.title, title=args.title)
            elif site_upper == "BT0":
                downloader.bt0_download_torrent({"link": args.link}, args.title, title=args.title)
            elif site_upper == "GY":
                downloader.gy_download_torrent({"link": args.link}, args.title, title=args.title)
            elif site_upper == "HDTV":
                downloader.hdtv_download_torrent({"link": args.link}, args.title, title=args.title)
            elif site_upper == "BTSJ6":
                downloader.btsj6_download_torrent({"link": args.link, "referer": args.referer}, args.title, title=args.title, referer=args.referer)
            elif site_upper == "1LOU":
                downloader.onelou_download_torrent({"link": args.link, "referer": args.referer}, args.title, title=args.title, referer=args.referer)
            elif site_upper == "SEEDHUB":
                downloader.seedhub_download_torrent({"link": args.link, "referer": args.referer}, args.title, title=args.title, referer=args.referer)
            elif site_upper == "JACKETT":
                downloader.jackett_download_torrent({"link": args.link, "referer": args.referer}, args.title, referer=args.referer, title=args.title)
            else:
                logging.error(f"未知的站点名称: {args.site}")
        except Exception as e:
            logging.error(f"手动运行下载功能时发生错误: {e}")
        finally:
            downloader.close_driver()
    else:
        # 如果未提供命令行参数，则运行默认逻辑
        downloader.run()