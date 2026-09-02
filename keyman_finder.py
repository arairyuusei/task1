#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
キーマンレター: 企業サイトから「人事情報が載っているページ」のURLを特定する。

出力する4カテゴリ（および準ずるもの）
  company_profile : 会社概要 / 企業概要 / 会社案内
  officers        : 役員一覧 / 役員紹介 / 経営体制
  news_public     : 一般向けニュース一覧（ニュースリリース / ニュースルーム / お知らせ）
  news_ir         : IR向けニュース一覧（IRニュース / 適時開示）

出力するのは上記の「タブ／一覧ページのURL」までである。
個別記事（例: 2026.08.31 人事異動のお知らせ）の特定は、この一覧URLを入力と
する後段のプログラムが担当する。本プログラムは併せて更新検知用の状態
（ETag / Last-Modified / 本文ハッシュ / 既知記事URL）を保存し、更新があった
ときに後段へ渡すURLを返す。

使い方
  python keyman_finder.py discover https://sfc.jp/ --company 住友林業 --out out/sfc.json
  python keyman_finder.py check out/sfc.json            # 更新検知（差分だけ返す）
  python keyman_finder.py batch companies.csv --out-dir out/
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import json
import logging
import re
import sys
import time
import unicodedata
import urllib.parse as up
import urllib.robotparser as urobot
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import warnings

import requests
from bs4 import BeautifulSoup

try:
    from bs4 import XMLParsedAsHTMLWarning
    warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
except ImportError:
    pass

try:  # パッケージとして import された場合
    from .rules import (
        ARTICLE_URL_HINTS, CLIENT_RENDER_HINTS, DATE_PATTERNS, GLOBAL_NEGATIVE,
        HUB_CATEGORIES, NAV_TAB_MARGIN, TARGET_CATEGORIES, VERIFY_RULES,
    )
except ImportError:  # スクリプトとして実行された場合
    from rules import (
        ARTICLE_URL_HINTS, CLIENT_RENDER_HINTS, DATE_PATTERNS, GLOBAL_NEGATIVE,
        HUB_CATEGORIES, NAV_TAB_MARGIN, TARGET_CATEGORIES, VERIFY_RULES,
    )

LOG = logging.getLogger("keyman")
SCHEMA_VERSION = "1.1"
DEFAULT_UA = ("KeymanLetterBot/0.1 (+https://example.com/about-bot; "
              "contact@example.com)")

# ===========================================================================
# URL 正規化
# ===========================================================================
_INDEX_FILES = ("index.html", "index.htm", "index.php", "index.shtml",
                "index.asp", "index.aspx", "index.jsp", "default.html",
                "default.htm", "default.aspx", "top.html")
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|yclid|mc_cid|mc_eid|_ga|ref|"
                       r"cmp|campaign)", re.I)
_MULTI_SLASH = re.compile(r"(?<!:)//+")
_JP_SUFFIXES = ("co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp", "gr.jp", "ed.jp",
                "lg.jp", "co.uk", "com.cn", "com.tw", "com.au", "co.kr")


def canonicalize(url: str, base: Optional[str] = None) -> Optional[str]:
    """相対URL解決・トラッキングパラメータ除去・index.html除去を行う。"""
    if not url:
        return None
    url = url.strip()
    if url.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
        return None
    if base:
        url = up.urljoin(base, url)
    try:
        p = up.urlsplit(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https"):
        return None
    host = (p.hostname or "").lower()
    if not host:
        return None
    if p.port and not ((p.scheme == "http" and p.port == 80) or
                       (p.scheme == "https" and p.port == 443)):
        host = f"{host}:{p.port}"

    path = _MULTI_SLASH.sub("/", p.path or "/") or "/"
    for idx in _INDEX_FILES:
        if path.endswith("/" + idx):
            path = path[: -len(idx)]
            break
    if not path.startswith("/"):
        path = "/" + path

    params = [(k, v) for k, v in up.parse_qsl(p.query, keep_blank_values=True)
              if not _TRACKING.match(k)]
    query = up.urlencode(sorted(params), doseq=True)
    return up.urlunsplit((p.scheme, host, path, query, ""))


def url_key(url: str) -> str:
    """重複判定用キー。www有無・末尾スラッシュ・スキームの差を吸収する。"""
    c = canonicalize(url) or url
    p = up.urlsplit(c)
    host = re.sub(r"^www\d?\.", "", p.netloc)
    path = p.path.rstrip("/") or "/"
    return f"{host}{path}" + (f"?{p.query}" if p.query else "")


def registrable_domain(host: str) -> str:
    host = re.sub(r":\d+$", "", (host or "").lower())
    labels = host.split(".")
    for suf in _JP_SUFFIXES:
        if host.endswith("." + suf) or host == suf:
            n = len(suf.split(".")) + 1
            return ".".join(labels[-n:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def same_site(url: str, root_domain: str) -> bool:
    host = (up.urlsplit(url).hostname or "").lower()
    return bool(host) and registrable_domain(host) == root_domain


def normalize_text(s: str) -> str:
    """アンカーテキストの正規化。全角/半角、装飾文字、注記を落とす。"""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[\u200b\u3000\s]+", " ", s)
    s = re.sub(r"[（(\[【]\s*(別(の)?ウィンドウ|新しいウィンドウ|外部サイト|"
               r"PDF[^)\]】]*|\d+(\.\d+)?\s*(KB|MB)|new)[^)\]】]*[)\]】】]", "",
               s, flags=re.I)
    s = re.sub(r"(別ウィンドウで開く|新しいウィンドウで開きます|外部サイトへ)", "", s)
    return s.strip(" -–—›»>|・\t")


# ===========================================================================
# 取得層
# ===========================================================================
@dataclasses.dataclass
class FetchResult:
    url: str
    status: int
    text: str = ""
    headers: dict = dataclasses.field(default_factory=dict)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.text)


def decode_bytes(content: bytes, header_encoding: Optional[str]) -> str:
    """日本語サイト向け。meta charset → HTTPヘッダ → 推定 の順で試す。"""
    head = content[:4096].decode("ascii", "ignore").lower()
    candidates: list[str] = []
    m = re.search(r'charset\s*=\s*["\']?\s*([\w\-]+)', head)
    if m:
        candidates.append(m.group(1))
    if header_encoding:
        candidates.append(header_encoding)
    candidates += ["utf-8", "cp932", "euc_jp", "iso2022_jp"]
    seen = set()
    for enc in candidates:
        enc = (enc or "").lower().replace("shift_jis", "cp932").replace("sjis", "cp932")
        if not enc or enc in seen:
            continue
        seen.add(enc)
        try:
            return content.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", "replace")


class Fetcher:
    """robots.txt 準拠・ホスト単位レート制限つきの HTTP クライアント。"""

    def __init__(self, user_agent: str = DEFAULT_UA, delay: float = 1.5,
                 timeout: float = 20.0, respect_robots: bool = True,
                 max_bytes: int = 4_000_000, max_fetches: int = 60):
        self.ua = user_agent
        self.delay = delay
        self.timeout = timeout
        self.respect_robots = respect_robots
        self.max_bytes = max_bytes
        self.max_fetches = max_fetches
        self.fetch_count = 0
        self._last: dict[str, float] = {}
        self._robots: dict[str, Any] = {}
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": user_agent,
            "Accept-Language": "ja,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    # -- robots -------------------------------------------------------------
    def _robots_for(self, url: str):
        p = up.urlsplit(url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = urobot.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            r = self._session.get(origin + "/robots.txt", timeout=self.timeout)
            if r.status_code == 200:
                rp.parse(decode_bytes(r.content, r.encoding).splitlines())
            else:
                rp.parse([])
        except requests.RequestException:
            rp.parse([])
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        try:
            return self._robots_for(url).can_fetch(self.ua, url)
        except Exception:  # robots が壊れている場合は許可扱い
            return True

    def sitemaps(self, url: str) -> list[str]:
        try:
            sm = self._robots_for(url).site_maps()
            return list(sm or [])
        except Exception:
            return []

    # -- fetch --------------------------------------------------------------
    def _wait(self, host: str) -> None:
        delay = self.delay
        last = self._last.get(host)
        if last is not None:
            gap = time.time() - last
            if gap < delay:
                time.sleep(delay - gap)
        self._last[host] = time.time()

    def get(self, url: str, etag: Optional[str] = None,
            last_modified: Optional[str] = None) -> FetchResult:
        if self.fetch_count >= self.max_fetches:
            return FetchResult(url, 0, error="fetch budget exceeded")
        if not self.allowed(url):
            LOG.info("robots.txt により取得しない: %s", url)
            return FetchResult(url, 0, error="disallowed by robots.txt")
        host = up.urlsplit(url).netloc
        self._wait(host)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        self.fetch_count += 1
        try:
            r = self._session.get(url, timeout=self.timeout, headers=headers,
                                  allow_redirects=True, stream=True)
            if r.status_code == 304:
                r.close()
                return FetchResult(url, 304, headers=dict(r.headers))
            ctype = (r.headers.get("Content-Type") or "").lower()
            body = r.raw.read(self.max_bytes, decode_content=True) or b""
            r.close()
            if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                return FetchResult(str(r.url), r.status_code,
                                   headers=dict(r.headers),
                                   error=f"unsupported content-type: {ctype}")
            return FetchResult(str(r.url), r.status_code,
                               text=decode_bytes(body, r.encoding),
                               headers=dict(r.headers))
        except requests.RequestException as e:
            return FetchResult(url, 0, error=f"{type(e).__name__}: {e}")


# ===========================================================================
# リンク抽出
# ===========================================================================
_NAV_RE = re.compile(r"(^|[-_ ])(g?nav|gnavi|navi|global|menu|megamenu|drawer|"
                     r"utility|headnav)", re.I)
_FOOTER_RE = re.compile(r"foot", re.I)
_BREAD_RE = re.compile(r"(bread|crumb|topicpath|pankuzu|^pan$)", re.I)
_SITEMAP_RE = re.compile(r"site_?-?map", re.I)


@dataclasses.dataclass
class Link:
    url: str
    key: str
    texts: set = dataclasses.field(default_factory=set)
    contexts: set = dataclasses.field(default_factory=set)
    found_on: set = dataclasses.field(default_factory=set)
    count: int = 0
    depth: int = 9

    @property
    def anchor(self) -> str:
        return " / ".join(sorted(self.texts))


def soup_of(html: str) -> BeautifulSoup:
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html, parser)
        except Exception:
            continue
    return BeautifulSoup(html, "html.parser")


def _contexts_of(a) -> set:
    ctx: set = set()
    node = a
    for _ in range(8):
        node = getattr(node, "parent", None)
        if node is None or not getattr(node, "name", None):
            break
        name = node.name.lower()
        cls = node.get("class") or []
        attrs = " ".join([node.get("id") or "", " ".join(cls), node.get("role") or ""])
        if name in ("nav", "header") or node.get("role") == "navigation" or _NAV_RE.search(attrs):
            ctx.add("nav")
        if name == "footer" or _FOOTER_RE.search(attrs):
            ctx.add("footer")
        if _BREAD_RE.search(attrs):
            ctx.add("breadcrumb")
        if _SITEMAP_RE.search(attrs):
            ctx.add("sitemap")
        if name == "main" or node.get("role") == "main":
            ctx.add("main")
    return ctx


def _anchor_text(a) -> str:
    t = normalize_text(a.get_text(" ", strip=True))
    if t:
        return t
    for img in a.find_all("img"):
        for attr in ("alt", "title"):
            v = normalize_text(img.get(attr) or "")
            if v:
                return v
    for attr in ("aria-label", "title"):
        v = normalize_text(a.get(attr) or "")
        if v:
            return v
    return ""


def extract_links(index: dict, html: str, base_url: str, depth: int,
                  root_domain: str, extra_context: Optional[set] = None) -> None:
    """html 内のリンクを index（key -> Link）にマージする。"""
    soup = soup_of(html)
    src_key = url_key(base_url)
    for a in soup.find_all("a", href=True):
        url = canonicalize(a["href"], base_url)
        if not url or not same_site(url, root_domain):
            continue
        key = url_key(url)
        link = index.get(key)
        if link is None:
            link = Link(url=url, key=key, depth=depth)
            index[key] = link
        text = _anchor_text(a)
        if text:
            link.texts.add(text[:120])
        link.contexts |= _contexts_of(a)
        if extra_context:
            link.contexts |= extra_context
        link.found_on.add(src_key)
        link.count += 1
        link.depth = min(link.depth, depth)


def page_signals(html: str, url: str) -> dict:
    """title / h1 / canonical / RSS フィードを取り出す。"""
    soup = soup_of(html)
    title = normalize_text(soup.title.get_text() if soup.title else "")
    h1 = normalize_text(soup.h1.get_text(" ", strip=True) if soup.h1 else "")
    canonical = None
    for link in soup.find_all("link", rel=True):
        rels = [r.lower() for r in (link.get("rel") or [])]
        if "canonical" in rels and link.get("href"):
            canonical = canonicalize(link["href"], url)
            break
    feeds = []
    for link in soup.find_all("link", href=True):
        t = (link.get("type") or "").lower()
        if "rss" in t or "atom" in t:
            f = canonicalize(link["href"], url)
            if f:
                feeds.append(f)
    return {"title": title, "h1": h1, "canonical": canonical, "feeds": feeds}


# ===========================================================================
# スコアリング
# ===========================================================================
def _apply(patterns: Iterable, haystack: str, reasons: list, tag: str) -> float:
    total = 0.0
    for pat, weight in patterns:
        if re.search(pat, haystack, re.I):
            total += weight
            reasons.append(f"{tag}:{pat}({weight:+g})")
    return total


def score_link(link: Link, cat: dict, hub_roles: dict) -> tuple[float, list]:
    reasons: list = []
    score = 0.0
    # 同じURLが複数の文言でリンクされることがある（「ニュースルーム」と
    # 「ニュースルーム一覧へ」など）。結合して評価すると ^...$ の完全一致
    # パターンが効かなくなるので、テキストごとに評価して最良を採る。
    best = -1.0
    for text in (sorted(link.texts) or [""]):
        r: list = []
        v = _apply(cat.get("anchor", []), text, r, "anchor")
        if v > best:
            best, reasons = v, r
    score += max(best, 0.0)
    score += _apply(cat.get("path", []), link.url, reasons, "path")
    if score <= 0:
        return score, reasons  # 手がかりゼロなら以降の加点はしない
    anchor = link.anchor
    score += _apply(cat.get("negative", []), anchor + " " + link.url, reasons, "neg")
    score += _apply(GLOBAL_NEGATIVE, anchor + " " + link.url, reasons, "global")

    if link.texts and min(len(t) for t in link.texts) <= 12:
        score += 2
        reasons.append("short-anchor(+2)")
    if "nav" in link.contexts:
        score += 3
        reasons.append("in-global-nav(+3)")
    if "breadcrumb" in link.contexts:
        score += 1
        reasons.append("in-breadcrumb(+1)")
    if "sitemap" in link.contexts:
        score += 1
        reasons.append("in-sitemap(+1)")
    if link.count >= 3:
        score += 1
        reasons.append("repeated-link(+1)")
    depth_bonus = max(0, 2 - link.depth)
    if depth_bonus:
        score += depth_bonus
        reasons.append(f"depth{link.depth}(+{depth_bonus})")

    roles: set = set()
    for src in link.found_on:
        roles |= hub_roles.get(src, set())
    for hub in cat.get("hub_bonus_from", []):
        if hub in roles:
            score += 3
            reasons.append(f"under-{hub}(+3)")
    return score, reasons


def rank_candidates(index: dict, categories: dict, hub_roles: dict) -> dict:
    ranked: dict[str, list] = {}
    for name, cat in categories.items():
        rows = []
        for link in index.values():
            s, reasons = score_link(link, cat, hub_roles)
            if s > 0:
                rows.append({"url": link.url, "key": link.key, "anchor": link.anchor,
                             "score": round(s, 2), "reasons": reasons,
                             "depth": link.depth,
                             "contexts": sorted(link.contexts)})
        rows.sort(key=lambda r: (-r["score"], len(r["url"])))
        ranked[name] = rows
    return ranked


def prefer_nav_tab(evaluated: list, margin: float = NAV_TAB_MARGIN) -> list:
    """首位と margin 点以内の候補のうち「グローバルナビ上のタブ」を採る。

    ニュース系では、タブ（例: ニュースルーム）とその配下の全件一覧
    （例: ニュースリリース）が同点近くで並ぶ。人事情報の監視対象としては
    トップのナビから直接辿れるタブのほうが安定するので、そちらを選ぶ。
    """
    if len(evaluated) < 2:
        return evaluated
    top = evaluated[0]["final_score"]
    pool = [c for c in evaluated if top - c["final_score"] <= margin]
    if len(pool) < 2:
        return evaluated

    def rank(c):
        in_home_nav = not (c.get("depth") == 0 and "nav" in (c.get("contexts") or []))
        path = up.urlsplit(c["url"]).path.strip("/")
        return (in_home_nav, len([p for p in path.split("/") if p]),
                -c["final_score"])

    pool.sort(key=rank)
    winner = pool[0]
    if winner is evaluated[0]:
        return evaluated
    winner.setdefault("reasons", []).append("prefer-nav-tab")
    return [winner] + [c for c in evaluated if c is not winner]


# ===========================================================================
# ページ内容による検証
# ===========================================================================
def visible_text(html: str) -> str:
    soup = soup_of(html)
    for tag in soup(["script", "style", "noscript", "template", "svg"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ", strip=True))


def main_text(html: str) -> str:
    """ハッシュ比較用。ナビ・フッタなど共通部分を落とす。"""
    soup = soup_of(html)
    for tag in soup(["script", "style", "noscript", "template", "svg", "nav",
                     "header", "footer", "aside", "form", "iframe"]):
        tag.decompose()
    for sel in soup.find_all(attrs={"class": True}):
        cls = " ".join(sel.get("class") or [])
        if _NAV_RE.search(cls) or _FOOTER_RE.search(cls) or _BREAD_RE.search(cls):
            sel.decompose()
    node = soup.find("main") or soup.find(attrs={"role": "main"}) or soup.body or soup
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True))


def content_hash(html: str) -> str:
    return hashlib.sha256(main_text(html).encode("utf-8")).hexdigest()


_RENDER_RES = [re.compile(p, re.I) for p in CLIENT_RENDER_HINTS]


def looks_client_rendered(html: str) -> bool:
    """一覧がJavaScriptで描画されているサイトかどうか。"""
    return any(rx.search(html) for rx in _RENDER_RES)


def verify_page(kind: Optional[str], html: str, url: str) -> tuple[float, dict]:
    if not kind:
        return 0.0, {}
    rule = VERIFY_RULES.get(kind)
    if not rule:
        return 0.0, {}
    if kind == "newslist":
        entries = extract_news_entries(html, url)
        n = len(entries)
        if n >= rule["min_dated_entries"]:
            return rule["bonus"], {"verified": True, "dated_entries": n}
        if looks_client_rendered(html):
            # 記事が取れないのは描画方式の問題であって、
            # 「一覧ページではない」ことの証拠にはならない。減点しない。
            return 0.0, {"verified": False, "dated_entries": n,
                         "render": "javascript",
                         "note": "一覧がJavaScriptで描画されている"}
        return rule["penalty"], {"verified": False, "dated_entries": n}
    text = visible_text(html)
    hits, total = 0, 0
    matched = []
    for pat in rule["patterns"]:
        c = len(re.findall(pat, text))
        if c:
            hits += 1
            total += c
            matched.append(pat)
    ok = hits >= rule["min_hits"] and total >= rule["min_total"]
    return (rule["bonus"] if ok else rule["penalty"]), {
        "verified": ok, "distinct_hits": hits, "total_hits": total,
        "matched": matched[:12]}


def title_bonus(cat: dict, signals: dict) -> tuple[float, list]:
    reasons: list = []
    hay = f"{signals.get('h1','')} {signals.get('title','')}"
    score = _apply([(p, min(w, 6) / 2) for p, w in cat.get("anchor", [])],
                   hay, reasons, "title")
    return score, reasons


# ===========================================================================
# ニュース一覧の解析
# ===========================================================================
_DATE_RES = [re.compile(p) for p in DATE_PATTERNS]
_ARTICLE_RES = [re.compile(p, re.I) for p in ARTICLE_URL_HINTS]


def _parse_date(text: str) -> Optional[str]:
    for rx in _DATE_RES:
        m = rx.search(text)
        if m:
            y, mo, d = (int(g) for g in m.groups()[:3])
            if 2000 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31:
                return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _date_near(a) -> Optional[str]:
    """<a> の近傍から日付を1つだけ取る。日付が複数ある広い親までは遡らない。"""
    node = a
    for _ in range(5):
        if node is None:
            break
        times = node.find_all("time") if hasattr(node, "find_all") else []
        if len(times) > 1:
            return None  # 複数記事を含む広い要素まで来た
        if len(times) == 1:
            got = _parse_date(times[0].get("datetime") or
                              times[0].get_text(" ", strip=True))
            if got:
                return got
        text = normalize_text(node.get_text(" ", strip=True))[:400]
        found = [d for rx in _DATE_RES for d in rx.findall(text)]
        if len(found) == 1:
            return _parse_date(text)
        if len(found) > 1:
            return None
        node = node.parent
    return None


def looks_like_article(url: str) -> bool:
    return any(rx.search(url) for rx in _ARTICLE_RES)


def extract_news_entries(html: str, base_url: str, limit: int = 120) -> list:
    """一覧ページから (date, title, url) を抽出する。"""
    soup = soup_of(html)
    seen: set = set()
    entries: list = []
    for a in soup.find_all("a", href=True):
        url = canonicalize(a["href"], base_url)
        if not url:
            continue
        # ページ送り・カテゴリ絞り込みは記事ではない
        if re.search(r"[?&](page|p|category|cat|tag|genre|type|year)=", url, re.I):
            continue
        key = url_key(url)
        if key in seen:
            continue
        title = _anchor_text(a)
        if len(title) < 4:
            continue
        date = _date_near(a)
        if not date:
            continue
        seen.add(key)
        entries.append({"date": date, "title": title[:200], "url": url})
        if len(entries) >= limit:
            break
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries



# ===========================================================================
# サイトマップ
# ===========================================================================
def collect_sitemap_urls(fetcher: Fetcher, home_url: str, root_domain: str,
                         max_urls: int = 4000, max_files: int = 6) -> list:
    p = up.urlsplit(home_url)
    origin = f"{p.scheme}://{p.netloc}"
    queue = list(dict.fromkeys(
        (fetcher.sitemaps(home_url) or []) +
        [origin + "/sitemap.xml", origin + "/sitemap_index.xml"]))
    urls: list = []
    files = 0
    while queue and files < max_files and len(urls) < max_urls:
        sm = queue.pop(0)
        files += 1
        res = fetcher.get(sm)
        if not res.ok or "<" not in res.text:
            continue
        soup = soup_of(res.text)
        if soup.find("sitemapindex"):
            for loc in soup.find_all("loc")[:max_files]:
                child = canonicalize(loc.get_text(strip=True))
                if child and same_site(child, root_domain) and child not in queue:
                    queue.append(child)
            continue
        for loc in soup.find_all("loc"):
            u = canonicalize(loc.get_text(strip=True))
            if u and same_site(u, root_domain):
                urls.append(u)
                if len(urls) >= max_urls:
                    break
    return urls


def _sitemap_prefilter(url: str, categories: dict) -> bool:
    """サイトマップは件数が多いので、path パターンに当たるものだけ候補にする。"""
    for cat in categories.values():
        for pat, w in cat.get("path", []):
            if w > 0 and re.search(pat, url, re.I):
                return True
    return False


# ===========================================================================
# 本体: 探索
# ===========================================================================
def discover(base_url: str, fetcher: Fetcher, company: Optional[str] = None,
             use_sitemap: bool = True, max_hub_fetch: int = 8,
             max_verify_fetch: int = 6, llm=None) -> dict:
    started = datetime.now(timezone.utc)
    home = fetcher.get(base_url)
    if not home.ok:
        return {"schema_version": SCHEMA_VERSION, "company": company,
                "input_url": base_url, "status": "error",
                "error": home.error or f"HTTP {home.status}",
                "generated_at": started.isoformat()}

    home_url = canonicalize(home.url) or base_url
    root_domain = registrable_domain(up.urlsplit(home_url).hostname or "")
    index: dict = {}
    hub_roles: dict = {url_key(home_url): {"home"}}
    pages: dict = {url_key(home_url): {"html": home.text, "url": home_url,
                                       "headers": home.headers}}

    # --- Phase A: トップページ -------------------------------------------
    extract_links(index, home.text, home_url, 0, root_domain)
    LOG.info("トップページから %d 件のリンクを抽出", len(index))

    # --- Phase B: ハブページを1階層だけ掘る ------------------------------
    hub_ranked = rank_candidates(index, HUB_CATEGORIES, hub_roles)
    hub_targets: dict = {}
    for hub_name, rows in hub_ranked.items():
        for row in rows[:2]:
            if row["score"] >= 8:
                hub_targets.setdefault(row["key"], {"url": row["url"], "roles": set()})
                hub_targets[row["key"]]["roles"].add(hub_name)
    for key, info in list(hub_targets.items())[:max_hub_fetch]:
        if key in pages:
            hub_roles.setdefault(key, set()).update(info["roles"])
            continue
        res = fetcher.get(info["url"])
        if not res.ok:
            continue
        final = canonicalize(res.url) or info["url"]
        pages[url_key(final)] = {"html": res.text, "url": final,
                                 "headers": res.headers}
        hub_roles.setdefault(url_key(final), set()).update(info["roles"])
        hub_roles.setdefault(key, set()).update(info["roles"])
        ctx = {"sitemap"} if "hub_sitemap" in info["roles"] else None
        extract_links(index, res.text, final, 1, root_domain, extra_context=ctx)
    LOG.info("ハブ展開後の候補リンク数: %d", len(index))

    # --- Phase C: sitemap.xml -------------------------------------------
    if use_sitemap:
        added = 0
        for u in collect_sitemap_urls(fetcher, home_url, root_domain):
            if not _sitemap_prefilter(u, TARGET_CATEGORIES):
                continue
            key = url_key(u)
            if key in index:
                index[key].contexts.add("sitemap_xml")
                continue
            index[key] = Link(url=u, key=key, depth=2,
                              contexts={"sitemap_xml"}, count=1)
            added += 1
        LOG.info("sitemap.xml から %d 件追加", added)

    # --- 候補のスコアリング ---------------------------------------------
    ranked = rank_candidates(index, TARGET_CATEGORIES, hub_roles)

    # --- 検証（上位候補だけ実際に取得して中身を確認）--------------------
    verified_cache: dict = {}
    per_cat_budget = max_verify_fetch

    def ensure_page(key: str, url: str) -> Optional[dict]:
        if key in pages:
            return pages[key]
        res = fetcher.get(url)
        if not res.ok:
            pages[key] = None
            return None
        final = canonicalize(res.url) or url
        page = {"html": res.text, "url": final, "headers": res.headers}
        pages[key] = page
        if url_key(final) != key:
            pages[url_key(final)] = page
        return page

    results: dict = {}
    for name, cat in TARGET_CATEGORIES.items():
        rows = [r for r in ranked[name] if r["score"] >= cat["prefetch_threshold"]]
        evaluated: list = []
        for row in rows[:per_cat_budget]:
            page = ensure_page(row["key"], row["url"])
            entry = dict(row)
            if page is None:
                entry["final_score"] = row["score"] - 6
                entry["evidence"] = {"fetch": "failed"}
                evaluated.append(entry)
                continue
            sig = page.setdefault("signals", page_signals(page["html"], page["url"]))
            # canonical が別URLを指す場合は正規URLに寄せる
            if sig.get("canonical") and same_site(sig["canonical"], root_domain):
                entry["url"] = sig["canonical"]
            cache_key = (row["key"], cat.get("verify"))
            if cache_key not in verified_cache:
                verified_cache[cache_key] = verify_page(
                    cat.get("verify"), page["html"], page["url"])
            vscore, vinfo = verified_cache[cache_key]
            tscore, treasons = title_bonus(cat, sig)
            entry["final_score"] = round(row["score"] + vscore + tscore, 2)
            entry["reasons"] = row["reasons"] + treasons + [
                f"verify:{cat.get('verify')}({vscore:+g})"]
            entry["evidence"] = {**vinfo, "title": sig.get("title"),
                                 "h1": sig.get("h1")}
            entry["page_key"] = row["key"]
            evaluated.append(entry)
        evaluated.sort(key=lambda r: -r["final_score"])
        if cat.get("prefer_nav_tab"):
            evaluated = prefer_nav_tab(evaluated)
        results[name] = {"label": cat["label"], "candidates": evaluated,
                         "fallback_candidates": rows[per_cat_budget:per_cat_budget + 5]}

    # --- 採択 ------------------------------------------------------------
    out_pages: dict = {}
    need_llm: list = []
    for name, cat in TARGET_CATEGORIES.items():
        cands = results[name]["candidates"]
        best = cands[0] if cands else None
        if best is None:
            out_pages[name] = {"label": cat["label"], "url": None,
                               "status": "not_found", "confidence": 0.0,
                               "alternates": [r["url"] for r in
                                              results[name]["fallback_candidates"][:3]]}
            need_llm.append(name)
            continue
        verified = bool(best.get("evidence", {}).get("verified"))
        if verified and best["final_score"] >= cat["accept_threshold"]:
            status = "confirmed"
        elif best["final_score"] >= cat["accept_threshold"]:
            status = "likely"
        else:
            status = "uncertain"
            need_llm.append(name)
        out_pages[name] = {
            "label": cat["label"],
            "url": best["url"],
            "status": status,
            "confidence": round(min(1.0, max(0.0, best["final_score"] / 28.0)), 2),
            "score": best["final_score"],
            "anchor": best["anchor"],
            "title": best.get("evidence", {}).get("title"),
            "evidence": best.get("evidence", {}),
            "reasons": best.get("reasons", [])[:14],
            "watch": cat["watch"],
            "alternates": [c["url"] for c in cands[1:3]],
        }

    # --- 更新検知用の指紋を記録（記事の中身には踏み込まない）--------------
    feeds: set = set()
    for name, info in out_pages.items():
        if not info.get("url"):
            continue
        page = pages.get(url_key(info["url"]))
        if not page:
            continue
        info["etag"] = page["headers"].get("ETag")
        info["last_modified"] = page["headers"].get("Last-Modified")
        if info.get("watch") == "entries":
            entries = extract_news_entries(page["html"], page["url"])
            entry_source = info["url"]
            if len(entries) < VERIFY_RULES["newslist"]["min_dated_entries"]:
                # 一覧がJS描画で読めない場合、トップページの新着ブロックが
                # 静的HTMLで最新数件を持っていることが多いので代替に使う。
                home_entries = extract_news_entries(home.text, home_url)
                if len(home_entries) > len(entries):
                    entries = home_entries
                    entry_source = home_url
            info["entry_source"] = entry_source
            info["latest_entry_date"] = entries[0]["date"] if entries else None
            info["entry_count"] = len(entries)
            info["known_entry_urls"] = [e["url"] for e in entries[:60]]
            for f in (page.get("signals") or {}).get("feeds", []):
                feeds.add(f)
        else:
            info["content_hash"] = content_hash(page["html"])

    # --- LLM フォールバック（曖昧なカテゴリだけ）------------------------
    llm_notes = None
    if llm and need_llm:
        try:
            llm_notes = llm(company=company, base_url=home_url,
                            categories=need_llm,
                            candidates={n: ranked[n][:20] for n in need_llm})
            for name, pick in (llm_notes.get("picks") or {}).items():
                if not pick.get("url"):
                    continue
                out_pages.setdefault(name, {"label": TARGET_CATEGORIES[name]["label"]})
                out_pages[name].update({
                    "url": pick["url"], "status": "llm_selected",
                    "confidence": float(pick.get("confidence", 0.5)),
                    "llm_reason": pick.get("reason"),
                    "watch": TARGET_CATEGORIES[name]["watch"]})
        except Exception as e:  # LLM 障害でパイプライン全体を止めない
            LOG.warning("LLM 判定に失敗: %s", e)
            llm_notes = {"error": str(e)}

    return {
        "schema_version": SCHEMA_VERSION,
        "company": company,
        "input_url": base_url,
        "site_url": home_url,
        "root_domain": root_domain,
        "status": "ok",
        "generated_at": started.isoformat(),
        "fetch_count": fetcher.fetch_count,
        "pages": out_pages,
        "feeds": sorted(feeds),
        "needs_review": [n for n, v in out_pages.items()
                         if v.get("status") in ("uncertain", "not_found")],
        # 静的HTMLでは記事一覧が読めないカテゴリ。URLは特定できているので、
        # 後段でヘッドレスレンダリングやJSON APIを使う必要がある。
        "needs_rendering": [n for n, v in out_pages.items()
                            if (v.get("evidence") or {}).get("render") == "javascript"],
        "llm": llm_notes,
        "debug_top_candidates": {n: [{"url": r["url"], "anchor": r["anchor"],
                                      "score": r["score"]}
                                     for r in ranked[n][:5]]
                                 for n in TARGET_CATEGORIES},
    }


# ===========================================================================
# 更新検知
# ===========================================================================
def check_updates(state: dict, fetcher: Fetcher) -> dict:
    """前回結果（discover の出力）と比較し、更新のあったページだけ返す。"""
    events: list = []
    pages = state.get("pages") or {}
    for name, info in pages.items():
        url = info.get("url")
        if not url:
            continue
        res = fetcher.get(url, etag=info.get("etag"),
                          last_modified=info.get("last_modified"))
        if res.status == 304:
            info["last_checked"] = datetime.now(timezone.utc).isoformat()
            continue
        if not res.ok:
            events.append({"category": name, "url": url, "type": "fetch_error",
                           "detail": res.error or f"HTTP {res.status}"})
            continue
        info["etag"] = res.headers.get("ETag")
        info["last_modified"] = res.headers.get("Last-Modified")
        info["last_checked"] = datetime.now(timezone.utc).isoformat()

        if info.get("watch") == "entries":
            # 一覧に新着があったことだけを検知する。どの記事が人事案件かの
            # 判定は、この一覧URLを入力とする後段のプログラムが行う。
            entries = extract_news_entries(res.text, res.url)
            known = set(url_key(u) for u in info.get("known_entry_urls") or [])
            new_entries = [e for e in entries if url_key(e["url"]) not in known]
            if known and new_entries:
                events.append({
                    "category": name, "type": "new_entries",
                    "list_url": url,          # ← 後段プログラムに渡すURL
                    "new_count": len(new_entries),
                    "new_entries": new_entries[:20],
                })
            info["known_entry_urls"] = [e["url"] for e in entries[:60]]
            info["latest_entry_date"] = entries[0]["date"] if entries else None
            info["entry_count"] = len(entries)
        else:
            new_hash = content_hash(res.text)
            if info.get("content_hash") and new_hash != info["content_hash"]:
                events.append({"category": name, "type": "content_changed",
                               "page_url": url})
            info["content_hash"] = new_hash
    state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
    return {"company": state.get("company"), "site_url": state.get("site_url"),
            "checked_at": state["last_checked_at"], "events": events,
            "state": state}


# ===========================================================================
# CLI
# ===========================================================================
def _make_fetcher(args) -> Fetcher:
    return Fetcher(user_agent=args.user_agent, delay=args.delay,
                   respect_robots=not args.ignore_robots,
                   max_fetches=args.max_fetches)


def _load_llm(args):
    if not args.llm:
        return None
    try:
        try:
            from .llm_adjudicate import make_adjudicator
        except ImportError:
            from llm_adjudicate import make_adjudicator
        return make_adjudicator(model=args.llm_model)
    except Exception as e:
        LOG.warning("LLM モジュールを読み込めません: %s", e)
        return None


def cmd_discover(args) -> int:
    fetcher = _make_fetcher(args)
    result = discover(args.url, fetcher, company=args.company,
                      use_sitemap=not args.no_sitemap, llm=_load_llm(args))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"書き出し: {args.out}", file=sys.stderr)
        _print_summary(result)
    else:
        print(text)
    return 0 if result.get("status") == "ok" else 1


def cmd_check(args) -> int:
    state = json.loads(Path(args.state).read_text(encoding="utf-8"))
    fetcher = _make_fetcher(args)
    out = check_updates(state, fetcher)
    Path(args.state).write_text(
        json.dumps(out["state"], ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "state"},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_batch(args) -> int:
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(args.csv, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    summary = []
    for row in rows:
        url = (row.get("url") or row.get("URL") or "").strip()
        company = (row.get("company") or row.get("企業名") or "").strip()
        if not url:
            continue
        LOG.info("=== %s (%s) ===", company or url, url)
        fetcher = _make_fetcher(args)
        try:
            result = discover(url, fetcher, company=company,
                              use_sitemap=not args.no_sitemap,
                              llm=_load_llm(args))
        except Exception as e:
            LOG.exception("失敗: %s", url)
            result = {"company": company, "input_url": url,
                      "status": "error", "error": str(e)}
        slug = re.sub(r"[^a-z0-9.\-]+", "_",
                      (up.urlsplit(url).hostname or "site").lower())
        (out_dir / f"{slug}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        summary.append({
            "company": company, "url": url, "status": result.get("status"),
            **{k: (result.get("pages", {}).get(k) or {}).get("url")
               for k in TARGET_CATEGORIES},
            "needs_review": ",".join(result.get("needs_review") or []),
        })
    csv_path = out_dir / "summary.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(summary[0].keys()) if summary else
                           ["company", "url", "status"])
        w.writeheader()
        w.writerows(summary)
    print(f"書き出し: {csv_path}", file=sys.stderr)
    return 0


def _print_summary(result: dict) -> None:
    print(f"\n■ {result.get('company') or result.get('site_url')}", file=sys.stderr)
    for name, info in (result.get("pages") or {}).items():
        print(f"  {info.get('label','?'):<20} {info.get('status','?'):<12} "
              f"{info.get('url') or '-'}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="企業サイトから人事情報ページのURLを特定")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--user-agent", default=DEFAULT_UA)
    p.add_argument("--delay", type=float, default=1.5, help="同一ホストへの待機秒数")
    p.add_argument("--max-fetches", type=int, default=60, help="1社あたりの取得上限")
    p.add_argument("--ignore-robots", action="store_true",
                   help="robots.txt を無視（原則使わない）")
    p.add_argument("--no-sitemap", action="store_true")
    p.add_argument("--llm", action="store_true", help="曖昧な場合にLLMで判定")
    p.add_argument("--llm-model", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="1社のURLを特定")
    d.add_argument("url")
    d.add_argument("--company")
    d.add_argument("--out")
    d.set_defaults(func=cmd_discover)

    c = sub.add_parser("check", help="保存済みJSONと比較して更新検知")
    c.add_argument("state")
    c.set_defaults(func=cmd_check)

    b = sub.add_parser("batch", help="CSV（company,url）を一括処理")
    b.add_argument("csv")
    b.add_argument("--out-dir", default="out")
    b.set_defaults(func=cmd_batch)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
