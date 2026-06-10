import argparse
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin
import tempfile
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import SessionNotCreatedException

from captcha_handler import CaptchaHandler


os.makedirs("/tmp/log", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/movie_tvshow_seedhub.log", mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)


@dataclass
class SearchTarget:
    title: str
    year: str
    season: str | None = None
    missing_episodes: list[int] | None = None


class SeedHubIndexer:
    def __init__(self, db_path: str | None = None, instance_id: str | None = None, headless: bool = True):
        self.instance_id = instance_id
        self.headless = headless
        self.db_path = db_path or os.environ.get("DB_PATH") or os.environ.get("DATABASE") or "/config/data.db"
        self.driver: webdriver.Chrome | None = None
        self.config: dict[str, str] = {}

        if instance_id:
            logging.getLogger().handlers.clear()
            logging.basicConfig(
                level=logging.INFO,
                format=f"%(asctime)s - %(levelname)s - SEEDHUB - INST - {instance_id} - %(message)s",
                handlers=[
                    logging.FileHandler(f"/tmp/log/movie_tvshow_seedhub_inst_{instance_id}.log", mode="w", encoding="utf-8"),
                    logging.StreamHandler(),
                ],
            )

    def load_config(self) -> None:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT OPTION, VALUE FROM CONFIG")
                rows = cursor.fetchall()
            self.config = {k: v for k, v in rows}
        except Exception as e:
            logging.error(f"加载配置失败: {e}")
            self.config = {}

    def _is_enabled(self) -> bool:
        enabled = self.config.get("seedhub_enabled", "True")
        if isinstance(enabled, str):
            return enabled.strip().lower() == "true"
        return bool(enabled)

    def setup_webdriver(self) -> None:
        if self.driver is not None:
            return

        configured_driver_path = (self.config.get("chromedriver_path") or "").strip()
        driver_path = os.environ.get("CHROMEDRIVER_PATH") or configured_driver_path or "/usr/lib/chromium/chromedriver"
        service = Service(executable_path=driver_path) if driver_path and os.path.exists(driver_path) else None

        # Windows 并发启动多个 Chrome 时偶发 DevToolsActivePort 崩溃；做两次尝试，第二次用全新临时 profile 目录。
        last_err: Exception | None = None
        for attempt in (1, 2):
            options = Options()
            if self.headless:
                options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920x1080")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--lang=zh-CN")
            options.page_load_strategy = "eager"

            # 降低被识别为自动化的概率（也有助于部分 Cloudflare 场景）
            try:
                options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.6943.90 Safari/537.36')
                options.add_experimental_option("excludeSwitches", ["enable-automation"])
                options.add_experimental_option("useAutomationExtension", False)
            except Exception:
                pass
            try:
                options.add_argument("--disable-blink-features=AutomationControlled")
            except Exception:
                pass

            # profile/cache 目录
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

            profile_id = self.instance_id or f"seedhub-{os.getpid()}"
            if attempt == 2:
                cache_base_dir = tempfile.mkdtemp(prefix="mediamaster_seedhub_chrome_")
                profile_id = f"{profile_id}-{int(time.time())}"

            user_data_dir = os.path.join(cache_base_dir, f"user-data-dir-inst-{profile_id}")
            disk_cache_dir = os.path.join(cache_base_dir, f"disk-cache-dir-inst-{profile_id}")
            try:
                os.makedirs(user_data_dir, exist_ok=True)
                os.makedirs(disk_cache_dir, exist_ok=True)
            except Exception:
                pass
            options.add_argument(f"--user-data-dir={user_data_dir}")
            options.add_argument(f"--disk-cache-dir={disk_cache_dir}")

            prefs = {
                "download.default_directory": os.path.abspath("Torrent"),
                "download.prompt_for_download": False,
                "download.directory_upgrade": True,
                "safebrowsing.enabled": True,
                "intl.accept_languages": "zh-CN",
                "profile.managed_default_content_settings.images": 1,
            }
            options.add_experimental_option("prefs", prefs)

            try:
                if service is not None:
                    self.driver = webdriver.Chrome(service=service, options=options)
                else:
                    self.driver = webdriver.Chrome(options=options)
                return
            except SessionNotCreatedException as e:
                last_err = e
                logging.warning(f"Chrome 启动失败(尝试 {attempt}/2): {e}")
                time.sleep(1)
            except Exception as e:
                last_err = e
                logging.warning(f"WebDriver 初始化异常(尝试 {attempt}/2): {e}")
                time.sleep(1)

        raise last_err or RuntimeError("WebDriver 初始化失败")

    def site_captcha(self, url: str) -> None:
        if not self.driver:
            raise RuntimeError("WebDriver 未初始化")
        ocr_api_key = self.config.get("ocr_api_key", "")
        CaptchaHandler(self.driver, ocr_api_key).handle_captcha(url)

    def extract_movie_targets(self) -> list[SearchTarget]:
        targets: list[SearchTarget] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT title, year FROM MISS_MOVIES")
                rows = cursor.fetchall()
            for title, year in rows:
                targets.append(SearchTarget(title=str(title), year=str(year)))
        except Exception as e:
            logging.error(f"读取电影订阅失败: {e}")
        return targets

    def extract_tv_targets(self) -> list[SearchTarget]:
        targets: list[SearchTarget] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT title, year, season, missing_episodes FROM MISS_TVS")
                rows = cursor.fetchall()
            for title, year, season, missing in rows:
                missing_list = []
                if missing:
                    try:
                        missing_list = [int(x.strip()) for x in str(missing).split(",") if x.strip().isdigit()]
                    except Exception:
                        missing_list = []
                targets.append(
                    SearchTarget(
                        title=str(title),
                        year=str(year),
                        season=str(season),
                        missing_episodes=sorted(missing_list),
                    )
                )
        except Exception as e:
            logging.error(f"读取剧集订阅失败: {e}")
        return targets

    @staticmethod
    def _normalize_title_for_matching(title: str) -> str:
        title = (title or "").strip()
        title = re.sub(r"\s+", " ", title)
        return title

    def _is_title_match(self, wanted: str, candidate: str) -> bool:
        a = self._normalize_title_for_matching(wanted)
        b = self._normalize_title_for_matching(candidate)
        if not a or not b:
            return False
        if a == b:
            return True
        a2 = a.replace(" ", "")
        b2 = b.replace(" ", "")
        return b.startswith(a) or a.startswith(b) or b2.startswith(a2) or a2.startswith(b2)

    @staticmethod
    def _clean_tv_title(title: str) -> str:
        t = title or ""
        t = re.sub(r"第[一二三四五六七八九十\d]+季", "", t)
        t = re.sub(r"\s*(Season\s*\d+|S\d+)\s*", " ", t, flags=re.IGNORECASE)
        t = re.sub(r"\s+", " ", t).strip()
        return t

    def _seedhub_base_url(self) -> str:
        base = (self.config.get("seedhub_base_url") or "").strip()
        if not base:
            raise ValueError("未在配置中找到 seedhub_base_url，请在数据库 CONFIG 表中设置该选项")
        if not base.startswith("http"):
            raise ValueError(f"配置的 seedhub_base_url '{base}' 不包含有效的协议前缀 (http/https)")
        return base.rstrip("/")

    def _search_url(self, keyword: str) -> str:
        base = self._seedhub_base_url()
        # SeedHub 搜索主要走 path 形式：/s/<keyword>/
        # 注意 keyword 需要 URL 编码（中文/特殊字符）
        encoded = quote((keyword or "").strip())
        return f"{base}/s/{encoded}/"

    def _search_url_fallback(self, keyword: str) -> str:
        # 兼容旧的 querystring 搜索（极少数情况下站点可能仍支持）
        base = self._seedhub_base_url()
        return f"{base}/?s={quote((keyword or '').strip())}"

    def _find_detail_url(self, target: SearchTarget, media_type: str) -> str | None:
        if not self.driver:
            raise RuntimeError("WebDriver 未初始化")

        search_url = self._search_url(target.title)
        # 先尝试 /s/<keyword>/ ；如果加载不到结果，再降级到 /?s=
        try:
            self.site_captcha(search_url)
        except Exception:
            fallback_url = self._search_url_fallback(target.title)
            self.site_captcha(fallback_url)

        try:
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/movies/']"))
            )
        except Exception:
            # 再尝试一次 fallback 搜索方式
            try:
                fallback_url = self._search_url_fallback(target.title)
                self.site_captcha(fallback_url)
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/movies/']"))
                )
            except Exception:
                try:
                    dump_dir = Path(os.getcwd()) / "debug_dumps"
                    dump_dir.mkdir(parents=True, exist_ok=True)
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    dump_path = dump_dir / f"seedhub_search_timeout_{ts}.html"
                    dump_path.write_text(self.driver.page_source or "", encoding="utf-8")
                    logging.warning(f"SeedHub 搜索页超时，已保存 HTML: {dump_path}")
                except Exception:
                    pass
                logging.info(f"SeedHub 搜索无结果或加载超时: {target.title}")
                return None

        anchors = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/movies/']")
        candidates: list[tuple[str, str]] = []  # (title_text, href)

        def _extract_anchor_title(a_el) -> str:
            # SeedHub 搜索结果卡片里，a.text 可能为空（只有图片/图标），尽量从属性/邻近元素取标题
            try:
                t = (a_el.text or "").strip()
                if t:
                    return t
            except Exception:
                pass
            for attr in ("title", "aria-label"):
                try:
                    t = (a_el.get_attribute(attr) or "").strip()
                    if t:
                        return t
                except Exception:
                    pass
            try:
                img = a_el.find_element(By.CSS_SELECTOR, "img")
                alt = (img.get_attribute("alt") or "").strip()
                if alt:
                    return alt
            except Exception:
                pass
            # 最后从父级卡片里找常见标题节点
            try:
                parent = a_el.find_element(By.XPATH, "./ancestor-or-self::*[self::article or self::div][1]")
                for sel in ("h1", "h2", "h3", ".title", ".post-title", ".entry-title"):
                    try:
                        t = (parent.find_element(By.CSS_SELECTOR, sel).text or "").strip()
                        if t:
                            return t
                    except Exception:
                        continue
            except Exception:
                pass
            return ""

        for a in anchors:
            try:
                href = (a.get_attribute("href") or "").strip()
                if not href or "/movies/" not in href:
                    continue
                text = _extract_anchor_title(a)
                # 允许标题为空（先留着候选，后面匹配失败会自然淘汰）
                candidates.append((text, href))
            except Exception:
                continue

        # 去重 href
        seen: set[str] = set()
        dedup: list[tuple[str, str]] = []
        for t, h in candidates:
            if h in seen:
                continue
            seen.add(h)
            dedup.append((t, h))

        if not dedup:
            try:
                dump_dir = Path(os.getcwd()) / "debug_dumps"
                dump_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                dump_path = dump_dir / f"seedhub_search_no_candidates_{ts}.html"
                dump_path.write_text(self.driver.page_source or "", encoding="utf-8")
                logging.warning(f"SeedHub 搜索页未提取到候选条目，已保存 HTML: {dump_path}")
            except Exception:
                pass
            return None

        wanted_title = target.title
        wanted_year = str(target.year or "")

        # 只按“名称”匹配：不强依赖年份（SeedHub 搜索卡片未必含年份，且年份可能不一致）
        title_matched: list[tuple[str, str]] = []
        year_preferred: list[tuple[str, str]] = []
        for title_text, href in dedup:
            t = self._clean_tv_title(title_text) if media_type == "tv" else title_text
            if not self._is_title_match(wanted_title, t):
                continue
            title_matched.append((title_text, href))

            # 如果页面块里刚好有年份，则作为“优先命中”而非过滤条件
            if wanted_year and wanted_year.isdigit():
                try:
                    block_text = (
                        self.driver.find_element(
                            By.XPATH,
                            f"//a[@href='{href}']/ancestor-or-self::*[self::article or self::div][1]",
                        ).text
                        or ""
                    )
                except Exception:
                    block_text = ""
                if wanted_year in block_text:
                    year_preferred.append((title_text, href))

        if year_preferred:
            return year_preferred[0][1]
        if title_matched:
            return title_matched[0][1]

        return None

    @staticmethod
    def _extract_resolution(text: str) -> str:
        t = (text or "").lower()
        if re.search(r"(2160p|hd2160|uhd|4k)", t):
            return "2160p"
        if re.search(r"(1080p|hd1080)", t) or ("蓝光" in text):
            return "1080p"
        if re.search(r"(720p|hd720)", t):
            return "720p"
        if re.search(r"(480p)", t):
            return "480p"
        return "未知分辨率"

    @staticmethod
    def _parse_size(text: str) -> str | None:
        if not text:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*([GMK])", text, flags=re.IGNORECASE)
        if not m:
            return None
        val, unit = m.group(1), m.group(2).upper()
        return f"{val}{unit}"

    @staticmethod
    def _parse_tv_episode_range(title_text: str, fallback_end: int | None = None) -> tuple[str, int | None, int | None, bool]:
        """Returns (item_type, start, end, is_full_season). item_type in 单集/集数范围/全集."""
        text = title_text or ""
        m = re.search(r"(?:EP|E|第)\s*(\d{1,3})(?:\s*[-~—]\s*(?:EP|E|第)?\s*(\d{1,3}))?", text, flags=re.IGNORECASE)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else start
            return ("单集" if start == end else "集数范围", start, end, False)

        m2 = re.search(r"全\s*(\d{1,3})\s*集", text)
        if m2:
            end = int(m2.group(1))
            return ("全集", 1, end, True)

        if ("全集" in text) or ("全季" in text) or ("全" in text and "集" in text):
            if fallback_end is not None and fallback_end > 0:
                return ("全集", 1, int(fallback_end), True)

        return ("未知集数", None, None, False)

    def _scrape_download_items(self, subject_url: str, media_type: str, target: SearchTarget) -> dict[str, Any]:
        if not self.driver:
            raise RuntimeError("WebDriver 未初始化")

        self.site_captcha(subject_url)

        # 等待下载链接出现（磁力 tab 中的 link_start）
        try:
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "a[href*='/link_start/?seed_id=']"))
            )
        except Exception:
            logging.info(f"未找到 SeedHub 下载资源: {subject_url}")

        anchors = self.driver.find_elements(By.CSS_SELECTOR, "a[href*='/link_start/?seed_id=']")

        preferred_resolution = (self.config.get("preferred_resolution") or "").strip().lower()
        fallback_resolution = (self.config.get("fallback_resolution") or "").strip().lower()
        exclude_keywords = [kw.strip() for kw in (self.config.get("resources_exclude_keywords") or "").split(",") if kw.strip()]

        if media_type == "movie":
            categorized: dict[str, list[dict[str, Any]]] = {
                "首选分辨率": [],
                "备选分辨率": [],
                "其他分辨率": [],
            }
        else:
            categorized = {
                "首选分辨率": {"单集": [], "集数范围": [], "全集": []},
                "备选分辨率": {"单集": [], "集数范围": [], "全集": []},
                "其他分辨率": {"单集": [], "集数范围": [], "全集": []},
            }

        max_missing = None
        if target.missing_episodes:
            max_missing = max(target.missing_episodes) if target.missing_episodes else None

        for a in anchors:
            try:
                link = (a.get_attribute("href") or "").strip()
                title_text = (a.text or "").strip()
                if not link or not title_text:
                    continue

                if any(kw in title_text for kw in exclude_keywords):
                    continue

                parent_text = ""
                try:
                    parent_text = (a.find_element(By.XPATH, "./ancestor-or-self::*[self::li or self::p or self::div][1]").text or "")
                except Exception:
                    parent_text = title_text

                resolution = self._extract_resolution(title_text + " " + parent_text)

                bucket = "其他分辨率"
                if preferred_resolution and resolution.lower() == preferred_resolution:
                    bucket = "首选分辨率"
                elif fallback_resolution and resolution.lower() == fallback_resolution:
                    bucket = "备选分辨率"

                item: dict[str, Any] = {
                    "title": title_text,
                    "link": link,
                    "resolution": resolution,
                    "subject_url": subject_url,
                    "referer": subject_url,
                }

                size = self._parse_size(parent_text)
                if size:
                    item["size"] = size

                if media_type == "movie":
                    categorized[bucket].append(item)
                else:
                    item_type, start_ep, end_ep, is_full_season = self._parse_tv_episode_range(title_text + " " + parent_text, fallback_end=max_missing)
                    if item_type == "未知集数" or start_ep is None:
                        continue
                    item["start_episode"] = start_ep
                    item["end_episode"] = end_ep
                    if is_full_season:
                        item["is_full_season"] = True
                    categorized[bucket][item_type].append(item)

            except Exception:
                continue

        return categorized

    def _save_results(self, target: SearchTarget, categorized_results: dict[str, Any]) -> None:
        if target.season:
            file_name = f"{target.title}-S{target.season}-{target.year}-SEEDHUB.json"
        else:
            file_name = f"{target.title}-{target.year}-SEEDHUB.json"

        out_path = os.path.join("/tmp/index", file_name)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(categorized_results, f, ensure_ascii=False, indent=4)
        logging.info(f"SeedHub 索引已保存: {out_path}")

    def run_auto(self) -> None:
        return self.run_auto_with_options(warmup=True)

    def run_auto_with_options(self, warmup: bool = True) -> None:
        self.load_config()
        if not self._is_enabled():
            logging.info("SeedHub 站点已禁用，退出")
            return

        movie_targets = self.extract_movie_targets()
        tv_targets = self.extract_tv_targets()

        if not movie_targets and not tv_targets:
            logging.info("没有订阅任务，退出")
            return

        try:
            self.setup_webdriver()
        except Exception as e:
            logging.error(f"SeedHub WebDriver 初始化失败，跳过: {e}")
            return
        assert self.driver is not None

        base = self._seedhub_base_url()
        if warmup:
            try:
                self.site_captcha(base)
            except Exception as e:
                logging.error(f"SeedHub 预热访问失败（将继续尝试具体条目）: {e}")

        for t in movie_targets:
            try:
                logging.info(f"SeedHub 搜索电影: {t.title} ({t.year})")
                subject_url = self._find_detail_url(t, media_type="movie")
                if not subject_url:
                    continue
                categorized = self._scrape_download_items(subject_url, media_type="movie", target=t)
                self._save_results(t, categorized)
            except Exception as e:
                logging.error(f"SeedHub 电影索引失败: {t.title} ({t.year}) err={e}")

        for t in tv_targets:
            try:
                logging.info(f"SeedHub 搜索剧集: {t.title} S{t.season} ({t.year})")
                subject_url = self._find_detail_url(t, media_type="tv")
                if not subject_url:
                    continue
                categorized = self._scrape_download_items(subject_url, media_type="tv", target=t)
                self._save_results(t, categorized)
            except Exception as e:
                logging.error(f"SeedHub 剧集索引失败: {t.title} S{t.season} ({t.year}) err={e}")

        try:
            self.driver.quit()
        except Exception:
            pass

    def run_manual(
        self,
        media_type: str,
        title: str,
        year: int | None,
        season: int | None = None,
        episodes: str | None = None,
        subject_url: str | None = None,
        warmup: bool = True,
    ) -> None:
        self.load_config()
        if not self._is_enabled():
            logging.info("SeedHub 站点已禁用，退出")
            return

        y = str(year) if year is not None else ""
        missing_eps: list[int] = []
        if episodes:
            try:
                missing_eps = [int(x.strip()) for x in episodes.split(",") if x.strip().isdigit()]
            except Exception:
                missing_eps = []

        target = SearchTarget(title=title, year=y, season=str(season) if season is not None else None, missing_episodes=sorted(missing_eps))

        try:
            self.setup_webdriver()
        except Exception as e:
            logging.error(f"SeedHub WebDriver 初始化失败: {e}")
            return
        assert self.driver is not None

        base = self._seedhub_base_url()
        # 直接给 subject_url 时，warmup 通常是多余的，且会额外触发 Cloudflare
        if warmup and not subject_url:
            try:
                self.site_captcha(base)
            except Exception as e:
                logging.error(f"SeedHub 预热访问失败: {e}")
                return

        if subject_url:
            subject_url = subject_url.strip()
        if not subject_url:
            subject_url = self._find_detail_url(target, media_type=media_type)
            if not subject_url:
                logging.info("未找到匹配条目")
                return

        categorized = self._scrape_download_items(subject_url, media_type=media_type, target=target)
        self._save_results(target, categorized)

        try:
            self.driver.quit()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="SeedHub 媒体索引器")
    parser.add_argument("--manual", action="store_true", help="手动搜索模式")
    parser.add_argument("--type", type=str, choices=["movie", "tv"], help="搜索类型")
    parser.add_argument("--title", type=str, help="媒体标题")
    parser.add_argument("--year", type=int, help="媒体年份")
    parser.add_argument("--season", type=int, help="季（仅 tv）")
    parser.add_argument("--episodes", type=str, help="缺失集数（仅 tv，可选），格式：1,2,3")
    parser.add_argument("--subject-url", type=str, help="直接指定 SeedHub 详情页 URL（跳过搜索，仅用于调试/手动模式）")
    parser.add_argument("--instance-id", type=str, help="实例唯一标识符")
    parser.add_argument("--headful", action="store_true", help="使用有头浏览器（遇到 Cloudflare/人机验证时更稳定）")
    parser.add_argument("--no-warmup", action="store_true", help="跳过 base_url 预热访问（减少触发 Cloudflare 的次数）")
    args = parser.parse_args()

    indexer = SeedHubIndexer(instance_id=args.instance_id, headless=not args.headful)

    try:
        if args.manual:
            if not args.type or not args.title:
                logging.error("手动模式必须提供 --type 和 --title")
                return
            indexer.run_manual(
                args.type,
                args.title,
                args.year,
                season=args.season,
                episodes=args.episodes,
                subject_url=args.subject_url,
                warmup=not args.no_warmup,
            )
        else:
            indexer.run_auto_with_options(warmup=not args.no_warmup)
    except Exception as e:
        # 手动搜索由 app.py 并行调用：这里不要抛出导致子进程非 0 退出，否则会被标记“站点搜索失败”
        logging.error(f"SeedHub 脚本异常（将返回空结果）: {e}")
        return


if __name__ == "__main__":
    main()
