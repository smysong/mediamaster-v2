import argparse
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin

import requests
from bs4 import BeautifulSoup


DEFAULT_BASE_URL = "https://www.btsj6.com/"


def _setup_logging(instance_id: Optional[str] = None) -> None:
    log_path = "/tmp/log/movie_tvshow_btsj6.log"
    fmt = "%(asctime)s - %(levelname)s - %(message)s"
    if instance_id:
        log_path = f"/tmp/log/movie_tvshow_btsj6_inst_{instance_id}.log"
        fmt = f"%(asctime)s - %(levelname)s - INST - {instance_id} - %(message)s"

    log_dir = os.path.dirname(log_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    logging.getLogger().handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        handlers=[logging.FileHandler(log_path, mode="w"), logging.StreamHandler()],
    )


@dataclass
class SearchHit:
    title: str
    url: str
    year: Optional[str]


class BTSJ6Indexer:
    def __init__(self, db_path: str = "/config/data.db", instance_id: Optional[str] = None):
        _setup_logging(instance_id)
        self.db_path = db_path
        self.config: Dict[str, str] = {}
        self.base_url = DEFAULT_BASE_URL

        self.session = requests.Session()
        # Avoid inheriting potentially broken system/env proxy settings (common on Windows).
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
                "Connection": "keep-alive",
            }
        )

    def load_config(self) -> Dict[str, str]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT OPTION, VALUE FROM CONFIG")
                rows = cursor.fetchall()
                self.config = {k: v for k, v in rows}

            self.base_url = self.config.get("btsj6_base_url", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL
            if not self.base_url.endswith("/"):
                self.base_url += "/"

            return self.config
        except sqlite3.Error as e:
            logging.error(f"数据库加载配置错误: {e}")
            self.config = {}
            self.base_url = DEFAULT_BASE_URL
            return {}

    def extract_movie_info(self) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT title, year FROM MISS_MOVIES")
                for title, year in cursor.fetchall():
                    items.append({"标题": str(title), "年份": str(year) if year is not None else ""})
        except Exception as e:
            logging.error(f"提取电影信息时发生错误: {e}")
        return items

    def extract_tv_info(self) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT title, year, season, missing_episodes FROM MISS_TVS")
                for title, year, season, missing_episodes in cursor.fetchall():
                    year_str = str(year) if year is not None else ""
                    season_str = str(season) if season is not None else ""
                    missing_list = (
                        [ep.strip() for ep in str(missing_episodes).split(",") if ep.strip()]
                        if missing_episodes
                        else []
                    )
                    items.append({"剧集": str(title), "年份": year_str, "季": season_str, "缺失集数": missing_list})
        except Exception as e:
            logging.error(f"提取电视节目信息时发生错误: {e}")
        return items

    def _get(self, url: str, *, referer: Optional[str] = None, timeout: int = 30) -> requests.Response:
        headers = {}
        if referer:
            headers["Referer"] = referer
        r = self.session.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r

    def _post(self, url: str, data: Dict[str, str], *, referer: Optional[str] = None, timeout: int = 30) -> requests.Response:
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
        }
        if referer:
            headers["Referer"] = referer
        r = self.session.post(url, data=data, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r

    def search(self, keyword: str) -> Tuple[List[SearchHit], Optional[BeautifulSoup], str]:
        search_url = urljoin(self.base_url, f"?s={quote_plus(keyword)}")
        r = self._get(search_url)
        html = r.text
        soup = BeautifulSoup(html, "html.parser")

        hits: List[SearchHit] = []
        post_grid = soup.select_one("div.post-grid")
        if post_grid:
            for post in post_grid.select("div.post"):
                a = post.select_one("h3.entry-title a[rel='bookmark']")
                if not a or not a.get("href"):
                    continue

                title = a.get("title") or a.get_text(" ", strip=True)
                url = a.get("href")

                year = None
                for span in post.select("span.date"):
                    txt = span.get_text(" ", strip=True)
                    m = re.search(r"上映年份[:：]\s*(\d{4})", txt)
                    if m:
                        year = m.group(1)
                        break

                hits.append(SearchHit(title=title.strip(), url=url.strip(), year=year))

        return hits, soup, r.url

    def _normalize_title(self, s: str) -> str:
        s = re.sub(r"\s+", " ", s or "").strip()
        return s

    def choose_best_hit(self, hits: List[SearchHit], title: str, year: str) -> Optional[SearchHit]:
        if not hits:
            return None

        norm_title = self._normalize_title(title)

        # 先匹配年份
        year_matched = [h for h in hits if h.year and year and h.year == str(year)]
        candidates = year_matched if year_matched else hits

        # 再匹配标题包含
        for h in candidates:
            if norm_title and norm_title in self._normalize_title(h.title):
                return h

        return candidates[0]

    def resolve_magnet(self, down_url: str, subject_url: str) -> Optional[str]:
        try:
            # 先访问 subject 页（有时站点会根据会话/上下文放行）
            try:
                self._get(subject_url)
            except Exception:
                pass

            r = self._get(down_url, referer=subject_url)
            html = r.text

            # 有些页面会直接露出 magnet
            m = re.search(r"(magnet:\?xt=urn:btih:[A-Za-z0-9]+[^\s\"'<>]*)", html)
            if m:
                return m.group(1)

            # 兼容多种写法：file_id: "123" / file_id = '123' / var file_id = 123
            file_id_match = (
                re.search(r"\bfile_id\b\s*[:=]\s*\"(\d+)\"", html)
                or re.search(r"\bfile_id\b\s*[:=]\s*'(\d+)'", html)
                or re.search(r"\bfile_id\b\s*[:=]\s*(\d+)", html)
            )
            fc_match = (
                re.search(r"\bfc\b\s*[:=]\s*\"([^\"\\]+)\"", html)
                or re.search(r"\bfc\b\s*[:=]\s*'([^'\\]+)'", html)
                or re.search(r"\bfc\b\s*[:=]\s*([^,\s}]+)", html)
            )

            if not file_id_match or not fc_match:
                title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
                title_text = (title_match.group(1).strip() if title_match else "")
                snippet = re.sub(r"\s+", " ", html or "").strip()[:240]
                logging.warning(
                    "未能从下载页提取 file_id/fc，回退为 down_url: %s | status=%s | final_url=%s | title=%s | snippet=%s",
                    down_url,
                    getattr(r, "status_code", "?"),
                    getattr(r, "url", ""),
                    title_text,
                    snippet,
                )
                return None

            file_id = file_id_match.group(1)
            fc = fc_match.group(1)

            api_url = urljoin(self.base_url, "callfile/callfile.php")
            r2 = self._post(api_url, {"file_id": file_id, "fc": fc}, referer=r.url)

            try:
                data = r2.json()
            except Exception:
                logging.warning("callfile 返回非 JSON，回退")
                return None

            if isinstance(data, dict) and data.get("error") == 0 and data.get("down"):
                return str(data.get("down"))

            logging.warning(f"callfile 返回异常: {data}")
            return None
        except Exception as e:
            logging.warning(f"解析 magnet 失败: {e}")
            return None

    def _extract_resolution(self, text: str) -> str:
        t = text or ""
        if re.search(r"2160p|4k|uhd", t, re.IGNORECASE):
            return "2160p"
        m = re.search(r"(\d{3,4}p)", t, re.IGNORECASE)
        if m:
            return m.group(1).lower()
        if "1080" in t:
            return "1080p"
        if "720" in t:
            return "720p"
        return "未知分辨率"

    def _extract_size(self, text: str) -> str:
        t = text or ""
        m = re.search(r"([\d.]+)\s*(TB|GB|MB)", t, re.IGNORECASE)
        if m:
            return f"{m.group(1)}{m.group(2).upper()}"
        return "未知大小"

    def _extract_subtitles_audio(self, text: str) -> Tuple[List[str], List[str]]:
        t = text or ""
        audio: List[str] = []
        subs: List[str] = []

        if "国语" in t:
            audio.append("国语配音")
        if "粤语" in t:
            audio.append("粤语配音")

        if "中文字幕" in t or "中字" in t:
            subs.append("中文字幕")
        if "双语" in t:
            subs.append("双语字幕")

        return audio, subs

    def parse_subject_resources(self, subject_url: str) -> List[Dict[str, Any]]:
        r = self._get(subject_url)
        soup = BeautifulSoup(r.text, "html.parser")

        # 下载入口通常是 #zdownload a.download-link
        anchors = soup.select("#zdownload a.download-link")
        if not anchors:
            # 有些页面可能结构略有差异，兜底
            anchors = soup.select("a.download-link")

        resources: List[Dict[str, Any]] = []
        for a in anchors:
            href = a.get("href")
            if not href:
                continue

            down_url = urljoin(self.base_url, href)
            text = a.get_text(" ", strip=True) or a.get("title") or ""

            resolution = self._extract_resolution(text)
            size = self._extract_size(text)
            audio_tracks, subtitles = self._extract_subtitles_audio(text)

            magnet = self.resolve_magnet(down_url, subject_url)
            link = magnet or down_url

            resources.append(
                {
                    "title": text,
                    "link": link,
                    "subject_url": subject_url,
                    "resolution": resolution,
                    "audio_tracks": audio_tracks,
                    "subtitles": subtitles,
                    "size": size,
                    "popularity": 0,
                }
            )

        return resources

    def _episode_type(self, title: str) -> str:
        t = title or ""

        if re.search(r"全集|全\d+集", t):
            return "全集"
        if re.search(r"E\d{1,3}\s*[-~]\s*E\d{1,3}", t, re.IGNORECASE) or re.search(r"\d+\s*[-~]\s*\d+集", t):
            return "集数范围"
        if re.search(r"S\d{1,2}E\d{1,3}", t, re.IGNORECASE) or re.search(r"\bE\d{1,3}\b", t, re.IGNORECASE) or re.search(
            r"第\d+集", t
        ):
            return "单集"

        return "全集"

    def _filter_exclude_keywords(self, resources: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        exclude_str = self.config.get("resources_exclude_keywords", "")
        exclude_keywords = [k.strip() for k in exclude_str.split(",") if k.strip()]
        if not exclude_keywords:
            return resources

        filtered: List[Dict[str, Any]] = []
        for r in resources:
            title = r.get("title", "") or ""
            if any(k in title for k in exclude_keywords):
                continue
            filtered.append(r)
        return filtered

    def _categorize_movie(self, resources: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        preferred = self.config.get("preferred_resolution", "未知分辨率")
        fallback = self.config.get("fallback_resolution", "未知分辨率")

        categorized: Dict[str, List[Dict[str, Any]]] = {"首选分辨率": [], "备选分辨率": [], "其他分辨率": []}
        for r in resources:
            res = r.get("resolution", "未知分辨率")
            if res == preferred:
                categorized["首选分辨率"].append(r)
            elif res == fallback:
                categorized["备选分辨率"].append(r)
            else:
                categorized["其他分辨率"].append(r)

        return categorized

    def _categorize_tv(self, resources: List[Dict[str, Any]]) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
        preferred = self.config.get("preferred_resolution", "未知分辨率")
        fallback = self.config.get("fallback_resolution", "未知分辨率")

        def blank_group() -> Dict[str, List[Dict[str, Any]]]:
            return {"单集": [], "集数范围": [], "全集": []}

        categorized: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
            "首选分辨率": blank_group(),
            "备选分辨率": blank_group(),
            "其他分辨率": blank_group(),
        }

        for r in resources:
            res = r.get("resolution", "未知分辨率")
            bucket = "其他分辨率"
            if res == preferred:
                bucket = "首选分辨率"
            elif res == fallback:
                bucket = "备选分辨率"

            et = self._episode_type(str(r.get("title", "")))
            categorized[bucket][et].append(r)

        return categorized

    def save_results_to_json(self, title: str, year: str, site_suffix: str, data: Any, season: Optional[str] = None) -> str:
        if season:
            file_name = f"{title}-S{season}-{year}-{site_suffix}.json"
        else:
            file_name = f"{title}-{year}-{site_suffix}.json"

        file_path = os.path.join("/tmp/index", file_name)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        return file_path

    def index_movie(self, title: str, year: str) -> None:
        empty = {"首选分辨率": [], "备选分辨率": [], "其他分辨率": []}
        try:
            hits, _soup, final_url = self.search(title)

            subject_url: Optional[str] = None
            if hits:
                hit = self.choose_best_hit(hits, title, year)
                subject_url = hit.url if hit else None
            else:
                # 有时搜索会直接返回详情页（不是列表），此时就用当前响应 URL
                # 如果 final_url 不是 /subject/，也仍然可能是可解析的详情页
                subject_url = final_url

            if not subject_url:
                logging.info(f"未找到匹配结果: {title} ({year})")
                self.save_results_to_json(title, year, "BTSJ6", empty)
                return

            logging.info(f"BTSJ6 电影匹配: {title} ({year}) => {subject_url}")
            resources = self.parse_subject_resources(subject_url)
            resources = self._filter_exclude_keywords(resources)
            categorized = self._categorize_movie(resources)
            path = self.save_results_to_json(title, year, "BTSJ6", categorized)
            logging.info(f"已写入索引: {path}")
        except Exception as e:
            logging.error(f"BTSJ6 索引电影失败: {title} ({year})，错误: {e}")
            path = self.save_results_to_json(title, year, "BTSJ6", empty)
            logging.info(f"已写入空索引: {path}")

    def index_tv(self, title: str, year: str, season: Optional[str] = None) -> None:
        empty = {
            "首选分辨率": {"单集": [], "集数范围": [], "全集": []},
            "备选分辨率": {"单集": [], "集数范围": [], "全集": []},
            "其他分辨率": {"单集": [], "集数范围": [], "全集": []},
        }
        try:
            hits, _soup, final_url = self.search(title)
            subject_url: Optional[str] = None
            if hits:
                hit = self.choose_best_hit(hits, title, year)
                subject_url = hit.url if hit else None
            else:
                subject_url = final_url

            if not subject_url:
                logging.info(f"未找到匹配结果: {title} ({year})")
                self.save_results_to_json(title, year, "BTSJ6", empty, season=season)
                return

            logging.info(f"BTSJ6 剧集匹配: {title} ({year}) => {subject_url}")
            resources = self.parse_subject_resources(subject_url)
            resources = self._filter_exclude_keywords(resources)
            categorized = self._categorize_tv(resources)
            path = self.save_results_to_json(title, year, "BTSJ6", categorized, season=season)
            logging.info(f"已写入索引: {path}")
        except Exception as e:
            logging.error(f"BTSJ6 索引剧集失败: {title} ({year})，错误: {e}")
            path = self.save_results_to_json(title, year, "BTSJ6", empty, season=season)
            logging.info(f"已写入空索引: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="BTSJ6 indexer")
    parser.add_argument("--instance-id", dest="instance_id", default=None)
    parser.add_argument("--manual", action="store_true", help="Manual search mode")
    parser.add_argument("--type", choices=["movie", "tv"], default="movie")
    parser.add_argument("--title", default=None)
    parser.add_argument("--year", default=None)
    parser.add_argument("--season", default=None)
    parser.add_argument("--db-path", default="/config/data.db")
    args = parser.parse_args()

    indexer = BTSJ6Indexer(db_path=args.db_path, instance_id=args.instance_id)
    indexer.load_config()

    # 如果有站点开关，且关闭，则直接退出
    enabled = indexer.config.get("btsj6_enabled", "True").lower() == "true"
    if not enabled:
        logging.info("btsj6_enabled=False，跳过索引")
        return

    if args.manual:
        if not args.title or not args.year:
            raise SystemExit("--manual 模式需要 --title 和 --year")

        if args.type == "movie":
            indexer.index_movie(args.title, str(args.year))
        else:
            indexer.index_tv(args.title, str(args.year), season=str(args.season) if args.season else None)
        return

    # 自动模式：读取订阅缺失表
    movies = indexer.extract_movie_info()
    for m in movies:
        try:
            indexer.index_movie(m["标题"], m["年份"])
            time.sleep(1)
        except Exception as e:
            logging.error(f"索引电影失败 {m}: {e}")

    tvs = indexer.extract_tv_info()
    for t in tvs:
        try:
            indexer.index_tv(t["剧集"], t["年份"], season=t.get("季") or None)
            time.sleep(1)
        except Exception as e:
            logging.error(f"索引剧集失败 {t}: {e}")


if __name__ == "__main__":
    main()
