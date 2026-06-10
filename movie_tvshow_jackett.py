import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import requests


os.makedirs("/tmp/log", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("/tmp/log/movie_tvshow_jackett.log", mode="w", encoding="utf-8"),
        logging.StreamHandler(),
    ],
    force=True,
)


TORZNAB_NS = "http://torznab.com/schemas/2015/feed"


@dataclass
class SearchTarget:
    title: str
    year: str
    season: int | None = None
    missing_episodes: list[int] | None = None
    alt_titles: list[str] | None = None


class JackettIndexer:
    def __init__(self, db_path: str | None = None, instance_id: str | None = None):
        self.instance_id = instance_id
        self.db_path = db_path or os.environ.get("DB_PATH") or os.environ.get("DATABASE") or "/config/data.db"
        self.config: dict[str, str] = {}

        if instance_id:
            logging.basicConfig(
                level=logging.INFO,
                format=f"%(asctime)s - %(levelname)s - JACKETT - INST - {instance_id} - %(message)s",
                handlers=[
                    logging.FileHandler(
                        f"/tmp/log/movie_tvshow_jackett_inst_{instance_id}.log",
                        mode="w",
                        encoding="utf-8",
                    ),
                    logging.StreamHandler(),
                ],
                force=True,
            )

        logging.info(f"Jackett 脚本启动: argv={sys.argv}")

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
        enabled = self.config.get("jackett_enabled", "False")
        if isinstance(enabled, str):
            return enabled.strip().lower() == "true"
        return bool(enabled)

    def _jackett_base_url(self) -> str:
        base = (self.config.get("jackett_base_url") or "http://127.0.0.1:9117").strip()
        if not base.startswith("http"):
            base = "http://127.0.0.1:9117"
        return base.rstrip("/")

    def _jackett_api_key(self) -> str:
        return (self.config.get("jackett_api_key") or "").strip()

    def _jackett_timeout_seconds(self) -> int:
        """Jackett 请求超时（read timeout），默认 90 秒。

        说明：部分反代/远程部署的 Jackett 响应较慢，30 秒容易超时。
        """
        raw = (self.config.get("jackett_timeout_seconds") or "").strip()
        try:
            v = int(raw)
            return v if v > 0 else 90
        except Exception:
            return 90

    def _jackett_verify_ssl(self) -> bool:
        raw = (self.config.get("jackett_verify_ssl") or "True").strip()
        return raw.lower() == "true"

    def _jackett_retries(self) -> int:
        raw = (self.config.get("jackett_retries") or "").strip()
        try:
            v = int(raw)
            return v if v >= 0 else 2
        except Exception:
            return 2

    def _tmdb_api_key(self) -> str:
        return (self.config.get("tmdb_api_key") or "").strip()

    def _tmdb_base_url(self) -> str:
        base = (self.config.get("tmdb_base_url") or "https://api.themoviedb.org").strip()
        if not base.startswith("http"):
            base = "https://api.themoviedb.org"
        return base.rstrip("/")

    @staticmethod
    def _contains_cjk(text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

    def _tmdb_get_english_title(self, media_type: str, title: str, year: str | None = None) -> str | None:
        """用 TMDB 把中文标题映射到英文标题（或原始名）。

        仅在配置了 tmdb_api_key 且 title 含中文时尝试；失败则返回 None。
        """
        api_key = self._tmdb_api_key()
        if not api_key:
            return None
        if not title or not self._contains_cjk(title):
            return None

        try:
            s = self._session()
            base = f"{self._tmdb_base_url()}/3"

            if media_type == "movie":
                search_path = "/search/movie"
                details_path_tpl = "/movie/{id}"
                year_key = "year"
                name_key = "title"
                original_key = "original_title"
            else:
                search_path = "/search/tv"
                details_path_tpl = "/tv/{id}"
                year_key = "first_air_date_year"
                name_key = "name"
                original_key = "original_name"

            params: dict[str, Any] = {
                "api_key": api_key,
                "query": title,
                "language": "zh-CN",
                "include_adult": False,
            }
            if year and str(year).isdigit():
                params[year_key] = int(year)

            r = s.get(f"{base}{search_path}", params=params, timeout=(10, 15))
            r.raise_for_status()
            data = r.json() if r.content else {}
            results = data.get("results") or []
            if not results:
                return None

            tmdb_id = results[0].get("id")
            if not tmdb_id:
                return None

            # 取英文详情名（更贴近 Jackett 索引器的英文标题）
            r2 = s.get(
                f"{base}{details_path_tpl.format(id=tmdb_id)}",
                params={"api_key": api_key, "language": "en-US"},
                timeout=(10, 15),
            )
            r2.raise_for_status()
            details = r2.json() if r2.content else {}
            candidate = (details.get(name_key) or "").strip()
            if candidate and not self._contains_cjk(candidate):
                return candidate

            # 兜底：原始名 / 其它字段
            candidate = (details.get(original_key) or details.get(name_key) or "").strip()
            if candidate and not self._contains_cjk(candidate):
                return candidate
            return None
        except Exception as e:
            logging.info(f"TMDB 英文标题获取失败（将跳过英文兜底）：{e}")
            return None

    def _session(self) -> requests.Session:
        s = requests.Session()
        s.trust_env = False
        s.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
        )
        return s

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
                missing_list: list[int] = []
                if missing:
                    try:
                        missing_list = [int(x.strip()) for x in str(missing).split(",") if x.strip().isdigit()]
                    except Exception:
                        missing_list = []
                targets.append(
                    SearchTarget(
                        title=str(title),
                        year=str(year),
                        season=int(season) if season is not None and str(season).strip().isdigit() else None,
                        missing_episodes=sorted(missing_list),
                    )
                )
        except Exception as e:
            logging.error(f"读取剧集订阅失败: {e}")
        return targets

    @staticmethod
    def _normalize_title_for_matching(title: str) -> str:
        t = (title or "").strip()
        t = re.sub(r"\s+", " ", t)
        return t

    @staticmethod
    def _normalize_title_compact(title: str) -> str:
        """更强的归一化：移除空格与常见符号，保留字母数字与中文字符。"""
        t = (title or "").strip()
        t = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", t)
        return t.lower()

    def _is_title_match(self, wanted: str, candidate: str) -> bool:
        a = self._normalize_title_for_matching(wanted)
        b = self._normalize_title_for_matching(candidate)
        if not a or not b:
            return False
        if a.lower() == b.lower():
            return True
        a2 = self._normalize_title_compact(a)
        b2 = self._normalize_title_compact(b)
        return b.lower().startswith(a.lower()) or a.lower().startswith(b.lower()) or b2.startswith(a2) or a2.startswith(b2)

    def _is_title_match_any(self, wanted_titles: list[str], candidate: str) -> bool:
        for w in wanted_titles:
            if self._is_title_match(w, candidate):
                return True
        return False

    def _needs_english_fallback(self, wanted_titles: list[str], items: list[ET.Element]) -> bool:
        if not items:
            return True
        for item in items:
            try:
                item_title = self._first_text(item, "title")
                if item_title and self._is_title_match_any(wanted_titles, item_title):
                    return False
            except Exception:
                continue
        return True

    @staticmethod
    def _extract_resolution(text: str) -> str:
        t = (text or "").lower()
        if "2160p" in t or "4k" in t:
            return "2160p"
        if "1080p" in t:
            return "1080p"
        if "720p" in t:
            return "720p"
        if "480p" in t:
            return "480p"
        return "未知分辨率"

    @staticmethod
    def _bytes_to_size(num_bytes: int | None) -> str | None:
        try:
            if not num_bytes or num_bytes <= 0:
                return None
            size = float(num_bytes)
            for unit in ["B", "KB", "MB", "GB", "TB"]:
                if size < 1024 or unit == "TB":
                    if unit == "B":
                        return f"{int(size)}{unit}"
                    return f"{size:.2f}{unit}".replace(".00", "")
                size /= 1024
            return None
        except Exception:
            return None

    @staticmethod
    def _torznab_attrs(item: ET.Element) -> dict[str, str]:
        attrs: dict[str, str] = {}
        for el in item.findall(f".//{{{TORZNAB_NS}}}attr"):
            name = (el.attrib.get("name") or "").strip().lower()
            value = (el.attrib.get("value") or "").strip()
            if name:
                attrs[name] = value
        # 有些返回里 namespace 可能丢失，兜底扫描 tag endswith 'attr'
        if not attrs:
            for el in item.findall(".//attr"):
                name = (el.attrib.get("name") or "").strip().lower()
                value = (el.attrib.get("value") or "").strip()
                if name:
                    attrs[name] = value
        return attrs

    @staticmethod
    def _first_text(item: ET.Element, tag: str) -> str:
        el = item.find(tag)
        if el is not None and el.text:
            return el.text.strip()
        return ""

    @staticmethod
    def _pick_download_link(item: ET.Element, attrs: dict[str, str]) -> tuple[str, str | None]:
        """返回 (link, size_str_or_none)。优先 magneturl；否则 enclosure url；否则 link/guid。"""
        magnet = (attrs.get("magneturl") or "").strip()
        if magnet.startswith("magnet:"):
            size_str = None
            try:
                size_bytes = int(attrs.get("size") or 0)
                size_str = JackettIndexer._bytes_to_size(size_bytes)
            except Exception:
                size_str = None
            return magnet, size_str

        enclosure = item.find("enclosure")
        if enclosure is not None:
            url = (enclosure.attrib.get("url") or "").strip()
            length = enclosure.attrib.get("length")
            size_str = None
            if length and str(length).isdigit():
                size_str = JackettIndexer._bytes_to_size(int(length))
            if url:
                return url, size_str

        link = JackettIndexer._first_text(item, "link")
        if link:
            return link, None

        guid = JackettIndexer._first_text(item, "guid")
        if guid:
            return guid, None

        return "", None

    def _torznab_query(self, params: dict[str, Any]) -> str:
        api_key = self._jackett_api_key()
        if not api_key:
            raise RuntimeError("Jackett API Key 未配置")

        base = self._jackett_base_url()
        url = f"{base}/api/v2.0/indexers/all/results/torznab/api"

        qp = {"apikey": api_key}
        qp.update({k: v for k, v in params.items() if v is not None and v != ""})
        return f"{url}?{urlencode(qp, doseq=True)}"

    def _torznab_query_masked(self, params: dict[str, Any]) -> str:
        """用于日志：不包含 apikey，避免泄露。"""
        base = self._jackett_base_url()
        url = f"{base}/api/v2.0/indexers/all/results/torznab/api"
        qp = {k: v for k, v in params.items() if v is not None and v != ""}
        if qp:
            return f"{url}?{urlencode(qp, doseq=True)}"
        return url

    def _fetch_items(self, params: dict[str, Any]) -> list[ET.Element]:
        url = self._torznab_query(params)
        masked = self._torznab_query_masked(params)
        connect_timeout = 10
        read_timeout = self._jackett_timeout_seconds()
        retries = self._jackett_retries()
        verify_ssl = self._jackett_verify_ssl()

        def _redact(msg: str) -> str:
            try:
                return re.sub(r"(apikey=)[^&\s]+", r"\1***", msg or "")
            except Exception:
                return msg

        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                logging.info(
                    f"Jackett 请求: {masked} (connect={connect_timeout}s read={read_timeout}s attempt={attempt+1}/{retries+1})"
                )
                s = self._session()
                r = s.get(url, timeout=(connect_timeout, read_timeout), verify=verify_ssl)
                r.raise_for_status()
                content = r.content or b""
                try:
                    root = ET.fromstring(content)
                except Exception as e:
                    snippet = (r.text or "")[:300].replace("\n", " ")
                    logging.error(f"Jackett 响应不是有效 XML: {masked} snippet={snippet}")
                    raise
                items = root.findall(".//item")
                logging.info(f"Jackett 返回 items: {len(items)}")
                if not items:
                    try:
                        txt = (r.text or "")[:300].replace("\n", " ")
                        if txt:
                            logging.info(f"Jackett 空 items 响应预览: {txt}")
                    except Exception:
                        pass
                return items
            except requests.Timeout as e:
                last_err = e
                logging.error(
                    f"Jackett 请求超时: {masked} (connect={connect_timeout}s read={read_timeout}s attempt={attempt+1}/{retries+1})"
                )
            except Exception as e:
                last_err = e
                logging.error(
                    f"Jackett 请求失败: {masked} attempt={attempt+1}/{retries+1} err={type(e).__name__}: {_redact(str(e))[:240]}"
                )

            # 简单退避
            try:
                import time

                time.sleep(min(2 ** attempt, 8))
            except Exception:
                pass

        raise last_err or RuntimeError("Jackett 请求失败")

    def _empty_results(self, media_type: str) -> dict[str, Any]:
        if media_type == "movie":
            return {"首选分辨率": [], "备选分辨率": [], "其他分辨率": []}
        return {
            "首选分辨率": {"单集": [], "集数范围": [], "全集": []},
            "备选分辨率": {"单集": [], "集数范围": [], "全集": []},
            "其他分辨率": {"单集": [], "集数范围": [], "全集": []},
        }

    def _parse_tv_episode_range(self, title_text: str, season: int | None, fallback_end: int | None) -> tuple[str, int | None, int | None, bool]:
        t = (title_text or "")
        tl = t.lower()

        if season is not None:
            s_pat = f"{season:02d}"
            # S01 Complete / Season 1 Complete 等
            if re.search(rf"\bS{s_pat}\b", t, flags=re.IGNORECASE) and re.search(r"complete|full\s*season|season\s*pack|全集|全季", tl, flags=re.IGNORECASE):
                end = fallback_end if fallback_end and fallback_end > 0 else 999
                return ("全集", 1, int(end), True)

            # S01E01-E12 / S01E01-E02
            m = re.search(rf"\bS{s_pat}E(\d{{1,3}})\s*[-~]\s*E?(\d{{1,3}})\b", t, flags=re.IGNORECASE)
            if m:
                a = int(m.group(1))
                b = int(m.group(2))
                if b < a:
                    a, b = b, a
                return ("集数范围", a, b, False)

            # S01E01
            m = re.search(rf"\bS{s_pat}E(\d{{1,3}})\b", t, flags=re.IGNORECASE)
            if m:
                e = int(m.group(1))
                return ("单集", e, e, False)

        # 无 season 参数时，也尽可能解析：E01-E12 / E01
        m = re.search(r"\bE(\d{1,3})\s*[-~]\s*E?(\d{1,3})\b", t, flags=re.IGNORECASE)
        if m:
            a = int(m.group(1))
            b = int(m.group(2))
            if b < a:
                a, b = b, a
            return ("集数范围", a, b, False)

        m = re.search(r"\bE(\d{1,3})\b", t, flags=re.IGNORECASE)
        if m:
            e = int(m.group(1))
            return ("单集", e, e, False)

        return ("未知集数", None, None, False)

    def _categorize_and_save(self, target: SearchTarget, media_type: str, items: list[ET.Element]) -> None:
        preferred_resolution = (self.config.get("preferred_resolution") or "").strip().lower()
        fallback_resolution = (self.config.get("fallback_resolution") or "").strip().lower()
        exclude_keywords = [kw.strip() for kw in (self.config.get("resources_exclude_keywords") or "").split(",") if kw.strip()]

        results = self._empty_results(media_type)

        fallback_end = None
        if target.missing_episodes:
            try:
                fallback_end = max(target.missing_episodes)
            except Exception:
                fallback_end = None

        for item in items:
            try:
                item_title = self._first_text(item, "title")
                if not item_title:
                    continue

                wanted_titles = [target.title]
                if target.alt_titles:
                    wanted_titles.extend([t for t in target.alt_titles if t])
                if not self._is_title_match_any(wanted_titles, item_title):
                    continue

                if any(kw and kw in item_title for kw in exclude_keywords):
                    continue

                attrs = self._torznab_attrs(item)

                # 如果有 year 且条目里明确有 4 位年份，但不匹配时仅降低概率：这里先不硬过滤
                # 电视剧如果能取到 season，优先校验 season
                if media_type == "tv" and target.season is not None:
                    season_attr = (attrs.get("season") or "").strip()
                    if season_attr.isdigit() and int(season_attr) != int(target.season):
                        continue

                link, size_str = self._pick_download_link(item, attrs)
                if not link:
                    continue

                resolution = self._extract_resolution(item_title)

                bucket = "其他分辨率"
                if preferred_resolution and resolution.lower() == preferred_resolution:
                    bucket = "首选分辨率"
                elif fallback_resolution and resolution.lower() == fallback_resolution:
                    bucket = "备选分辨率"

                popularity = 0
                try:
                    popularity = int(attrs.get("seeders") or 0)
                except Exception:
                    popularity = 0

                entry: dict[str, Any] = {
                    "title": item_title,
                    "link": link,
                    "resolution": resolution,
                    "popularity": popularity,
                }
                if size_str:
                    entry["size"] = size_str

                if media_type == "movie":
                    results[bucket].append(entry)
                else:
                    item_type, start_ep, end_ep, is_full = self._parse_tv_episode_range(
                        item_title, season=target.season, fallback_end=fallback_end
                    )
                    if item_type == "未知集数" or start_ep is None or end_ep is None:
                        continue
                    entry["start_episode"] = start_ep
                    entry["end_episode"] = end_ep
                    if is_full:
                        entry["is_full_season"] = True
                    results[bucket][item_type].append(entry)
            except Exception:
                continue

        try:
            if media_type == "movie":
                total = sum(len(results.get(k, [])) for k in ["首选分辨率", "备选分辨率", "其他分辨率"])
                logging.info(
                    f"Jackett 分类结果(movie): title={target.title} year={target.year} total={total} "
                    f"pref={len(results['首选分辨率'])} fallback={len(results['备选分辨率'])} other={len(results['其他分辨率'])}"
                )
            else:
                def _count_bucket(bucket: dict[str, list[Any]]) -> int:
                    return sum(len(v) for v in bucket.values())

                total = _count_bucket(results["首选分辨率"]) + _count_bucket(results["备选分辨率"]) + _count_bucket(results["其他分辨率"])
                logging.info(
                    f"Jackett 分类结果(tv): title={target.title} S{target.season} year={target.year} total={total} "
                    f"pref={_count_bucket(results['首选分辨率'])} fallback={_count_bucket(results['备选分辨率'])} other={_count_bucket(results['其他分辨率'])}"
                )
        except Exception:
            pass

        # 保存 JSON
        if target.season is not None:
            file_name = f"{target.title}-S{target.season}-{target.year}-JACKETT.json"
        else:
            file_name = f"{target.title}-{target.year}-JACKETT.json"
        out_path = os.path.join("/tmp/index", file_name)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=4)

    def run_auto(self) -> None:
        self.load_config()
        if not self._is_enabled():
            logging.info("Jackett 站点已禁用，退出")
            return
        if not self._jackett_api_key():
            logging.error("Jackett API Key 未配置，退出")
            return

        os.makedirs("/tmp/index", exist_ok=True)

        movie_targets = self.extract_movie_targets()
        tv_targets = self.extract_tv_targets()

        for t in movie_targets:
            try:
                logging.info(f"Jackett 搜索电影: {t.title} ({t.year})")
                params = {"t": "movie", "q": t.title}
                if t.year and str(t.year).isdigit():
                    params["year"] = int(t.year)
                items = self._fetch_items(params)

                wanted_titles = [t.title]
                if self._needs_english_fallback(wanted_titles, items) and self._contains_cjk(t.title):
                    en_title = self._tmdb_get_english_title("movie", t.title, year=t.year)
                    if en_title and en_title.strip() and en_title.strip().lower() != t.title.strip().lower():
                        logging.info(f"Jackett 中文无匹配，改用英文标题兜底搜索: {t.title} -> {en_title}")
                        t.alt_titles = (t.alt_titles or []) + [en_title]
                        params2 = {"t": "movie", "q": en_title}
                        if t.year and str(t.year).isdigit():
                            params2["year"] = int(t.year)
                        try:
                            items2 = self._fetch_items(params2)
                            items = items + items2
                        except Exception as e:
                            logging.info(f"Jackett 英文兜底搜索失败（将使用中文结果）：{e}")
                self._categorize_and_save(t, "movie", items)
            except Exception as e:
                logging.error(f"Jackett 电影索引失败: {t.title} ({t.year}) err={e}")

        for t in tv_targets:
            try:
                if t.season is None:
                    continue
                logging.info(f"Jackett 搜索剧集: {t.title} S{t.season} ({t.year})")
                params = {"t": "tvsearch", "q": t.title, "season": int(t.season)}
                items = self._fetch_items(params)

                wanted_titles = [t.title]
                if self._needs_english_fallback(wanted_titles, items) and self._contains_cjk(t.title):
                    en_title = self._tmdb_get_english_title("tv", t.title, year=t.year)
                    if en_title and en_title.strip() and en_title.strip().lower() != t.title.strip().lower():
                        logging.info(f"Jackett 中文无匹配，改用英文标题兜底搜索: {t.title} -> {en_title}")
                        t.alt_titles = (t.alt_titles or []) + [en_title]
                        params2 = {"t": "tvsearch", "q": en_title, "season": int(t.season)}
                        try:
                            items2 = self._fetch_items(params2)
                            items = items + items2
                        except Exception as e:
                            logging.info(f"Jackett 英文兜底搜索失败（将使用中文结果）：{e}")
                self._categorize_and_save(t, "tv", items)
            except Exception as e:
                logging.error(f"Jackett 剧集索引失败: {t.title} S{t.season} ({t.year}) err={e}")

    def run_manual(self, media_type: str, title: str, year: int | None, season: int | None = None, episodes: str | None = None) -> None:
        self.load_config()
        if not self._is_enabled():
            logging.info("Jackett 站点已禁用，退出")
            return
        if not self._jackett_api_key():
            logging.error("Jackett API Key 未配置，退出")
            return

        y = str(year) if year is not None else ""
        missing_eps: list[int] = []
        if episodes:
            try:
                missing_eps = [int(x.strip()) for x in episodes.split(",") if x.strip().isdigit()]
            except Exception:
                missing_eps = []

        target = SearchTarget(title=title, year=y, season=season, missing_episodes=sorted(missing_eps))

        params: dict[str, Any]
        if media_type == "movie":
            params = {"t": "movie", "q": title}
            if year is not None:
                params["year"] = year
            items = self._fetch_items(params)

            if not items:
                logging.info(f"Jackett 电影中文查询无 item: title={title} year={year}")

            wanted_titles = [target.title]
            if self._needs_english_fallback(wanted_titles, items) and self._contains_cjk(target.title):
                en_title = self._tmdb_get_english_title("movie", target.title, year=target.year)
                if en_title and en_title.strip() and en_title.strip().lower() != target.title.strip().lower():
                    logging.info(f"Jackett 中文无匹配，改用英文标题兜底搜索: {target.title} -> {en_title}")
                    target.alt_titles = (target.alt_titles or []) + [en_title]
                    params2 = {"t": "movie", "q": en_title}
                    if year is not None:
                        params2["year"] = year
                    try:
                        items2 = self._fetch_items(params2)
                        items = items + items2
                    except Exception as e:
                        logging.info(f"Jackett 英文兜底搜索失败（将使用中文结果）：{e}")
            self._categorize_and_save(target, "movie", items)
            return

        params = {"t": "tvsearch", "q": title}
        if season is not None:
            params["season"] = season
        items = self._fetch_items(params)

        if not items:
            logging.info(f"Jackett 剧集中文查询无 item: title={title} season={season} year={year}")

        wanted_titles = [target.title]
        if self._needs_english_fallback(wanted_titles, items) and self._contains_cjk(target.title):
            en_title = self._tmdb_get_english_title("tv", target.title, year=target.year)
            if en_title and en_title.strip() and en_title.strip().lower() != target.title.strip().lower():
                logging.info(f"Jackett 中文无匹配，改用英文标题兜底搜索: {target.title} -> {en_title}")
                target.alt_titles = (target.alt_titles or []) + [en_title]
                params2 = {"t": "tvsearch", "q": en_title}
                if season is not None:
                    params2["season"] = season
                try:
                    items2 = self._fetch_items(params2)
                    items = items + items2
                except Exception as e:
                    logging.info(f"Jackett 英文兜底搜索失败（将使用中文结果）：{e}")
        self._categorize_and_save(target, "tv", items)


def main() -> None:
    parser = argparse.ArgumentParser(description="Jackett Torznab 媒体索引器")
    parser.add_argument("--manual", action="store_true", help="手动搜索模式")
    parser.add_argument("--type", type=str, choices=["movie", "tv"], help="搜索类型")
    parser.add_argument("--title", type=str, help="媒体标题")
    parser.add_argument("--year", type=int, help="媒体年份")
    parser.add_argument("--season", type=int, help="季（仅 tv，可选）")
    parser.add_argument("--episodes", type=str, help="缺失集数（仅 tv，可选），格式：1,2,3")
    parser.add_argument("--instance-id", type=str, help="实例唯一标识符")
    args = parser.parse_args()

    indexer = JackettIndexer(instance_id=args.instance_id)

    try:
        if args.manual:
            if not args.type or not args.title:
                logging.error("手动模式必须提供 --type 和 --title")
                return
            indexer.run_manual(args.type, args.title, args.year, season=args.season, episodes=args.episodes)
        else:
            indexer.run_auto()
    except Exception as e:
        # 手动搜索由 app.py 并行调用：这里不要抛出导致子进程非 0 退出
        logging.error(f"Jackett 脚本异常（将返回空结果）: {e}")
        return


if __name__ == "__main__":
    main()
