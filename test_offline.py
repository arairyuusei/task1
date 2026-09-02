# -*- coding: utf-8 -*-
"""
オフラインテスト。実在サイト（住友林業）の構造を模した架空サイトで、
探索・採択・人事記事抽出・更新検知が正しく動くか確認する。

  python test_offline.py
"""
from __future__ import annotations

import json

from keyman_finder import (FetchResult, canonicalize, check_updates, discover,
                           url_key)

BASE = "https://example-forestry.jp"

HOME = f"""<html><head><title>木と生きる幸福 サンプル林業</title></head><body>
<nav class="gnav">
  <a href="/ie/">注文住宅</a><a href="/bunjyo/">分譲住宅・土地</a>
  <a href="/treecycle/">森林・木材・再エネ</a>
  <a href="/information/">企業・IR・ESG・採用</a>
  <a href="/information/company/">会社情報</a>
  <a href="/information/ir/">株主・投資家の皆様へ(IR情報)</a>
  <a href="/information/sustainability/">サステナビリティ</a>
  <a href="/information/saiyou/employment/">採用情報</a>
  <a href="/information/newsroom/">ニュースルーム</a>
</nav>
<main><a href="/catalog/">カタログ請求</a></main>
<footer><a href="/sitemap/">サイトマップ</a><a href="/hogo/">個人情報のお取り扱い</a>
<a href="https://www.instagram.com/example/">Instagram</a></footer>
</body></html>"""

COMPANY_HUB = f"""<html><head><title>会社情報｜サンプル林業</title></head><body>
<nav class="gnav"><a href="/information/company/">会社情報</a>
<a href="/information/ir/">株主・投資家の皆様へ(IR情報)</a></nav>
<div class="breadcrumb"><a href="/">ホーム</a><a href="/information/">企業・IR・ESG・採用</a></div>
<main><h1>会社情報</h1>
<ul class="local-nav">
  <li><a href="/information/company/message/">トップメッセージ</a></li>
  <li><a href="/information/company/aboutus/index.html">会社概要</a></li>
  <li><a href="/information/company/officer/index.html">役員一覧</a></li>
  <li><a href="/information/company/corporate_governance.html">コーポレートガバナンス</a></li>
  <li><a href="/information/company/history/">歴史・沿革</a></li>
  <li><a href="/information/company/cooperation/">グループ会社一覧</a></li>
  <li><a href="/information/ir/stockholder/yakuin_hoshu.html">役員報酬について</a></li>
  <li><a href="/information/saiyou/message/">経営陣からのメッセージ（採用情報）</a></li>
</ul></main></body></html>"""

IR_HUB = """<html><head><title>株主・投資家の皆様へ｜サンプル林業</title></head><body>
<nav class="gnav"><a href="/information/ir/">株主・投資家の皆様へ(IR情報)</a></nav>
<main><h1>株主・投資家の皆様へ(IR情報)</h1>
<ul class="local-nav">
  <li><a href="/information/ir/news/">IRニュース</a></li>
  <li><a href="/information/ir/library/">IRライブラリ</a></li>
  <li><a href="/information/ir/calendar/">IRカレンダー</a></li>
  <li><a href="/information/ir/faq/">よくあるご質問</a></li>
</ul></main></body></html>"""

ABOUTUS = """<html><head><title>会社概要 | サンプル林業</title></head><body>
<main><h1>会社概要</h1>
<table>
<tr><th>商号</th><td>サンプル林業株式会社</td></tr>
<tr><th>設立</th><td>1948年2月20日</td></tr>
<tr><th>本社所在地</th><td>東京都千代田区丸の内1-1-1</td></tr>
<tr><th>資本金</th><td>50,000百万円</td></tr>
<tr><th>従業員数</th><td>5,432名</td></tr>
<tr><th>代表者</th><td>代表取締役 執行役員社長 山田 太郎</td></tr>
<tr><th>事業内容</th><td>木材建材事業、住宅事業</td></tr>
<tr><th>証券コード</th><td>9999（東証プライム）</td></tr>
</table></main></body></html>"""

OFFICER = """<html><head><title>役員一覧 | サンプル林業</title></head><body>
<main><h1>役員一覧</h1>
<table>
<tr><td>代表取締役 会長</td><td>佐藤 一郎</td></tr>
<tr><td>代表取締役 執行役員社長</td><td>山田 太郎</td></tr>
<tr><td>取締役 専務執行役員</td><td>鈴木 次郎</td></tr>
<tr><td>取締役 常務執行役員</td><td>高橋 三郎</td></tr>
<tr><td>社外取締役</td><td>田中 花子</td></tr>
<tr><td>社外取締役</td><td>伊藤 明</td></tr>
<tr><td>監査役</td><td>渡辺 四郎</td></tr>
<tr><td>社外監査役</td><td>山本 五郎</td></tr>
<tr><td>執行役員</td><td>中村 六郎</td></tr>
</table>
<p>2026年3月25日現在の執行役員は次のとおりであります。</p>
</main></body></html>"""

LEGACY_OFFICER = """<html><head><title>役員一覧 | サンプル林業（旧サイト）</title></head><body>
<main><h1>役員一覧</h1>
<table>
<tr><td>代表取締役社長</td><td>旧 社長</td></tr>
<tr><td>取締役</td><td>旧 取締役</td></tr>
<tr><td>監査役</td><td>旧 監査役</td></tr>
<tr><td>執行役員</td><td>旧 執行役員</td></tr>
<tr><td>社外取締役</td><td>旧 社外</td></tr>
</table></main></body></html>"""

NEWSROOM = """<html><head><title>ニュースルーム｜サンプル林業</title>
<link rel="alternate" type="application/rss+xml" href="/information/newsroom/rss.xml">
</head><body>
<main><h1>ニュースルーム</h1>
<div class="category-nav">
  <a href="/information/newsroom/?category=all">すべて</a>
  <a href="/information/newsroom/?category=jinji">人事・組織</a>
  <a href="/information/newsroom/?category=product">製品</a>
</div>
<ul class="news-list">
  <li><time datetime="2026-08-31">2026.08.31</time>
      <a href="/information/newsroom/2026/0831_01.html">人事異動のお知らせ</a></li>
  <li><time datetime="2026-08-20">2026.08.20</time>
      <a href="/information/newsroom/2026/0820_01.html">新製品「サンプル床材」を発売</a></li>
  <li><time datetime="2026-07-15">2026.07.15</time>
      <a href="/information/newsroom/2026/0715_01.html">人事制度改革について</a></li>
  <li><time datetime="2026-06-30">2026.06.30</time>
      <a href="/information/newsroom/2026/0630_01.html">組織改編および人事異動について</a></li>
  <li><time datetime="2026-05-11">2026.05.11</time>
      <a href="/information/newsroom/2026/0511_01.html">人材育成プログラムを開始</a></li>
  <li><time datetime="2026-04-02">2026.04.02</time>
      <a href="/information/newsroom/2026/0402_01.html">研究所を新設しました</a></li>
</ul>
<a href="/information/newsroom/?page=2">次のページ</a>
</main></body></html>"""

IR_NEWS = """<html><head><title>IRニュース｜サンプル林業</title></head><body>
<main><h1>IRニュース</h1>
<ul class="news-list">
  <li><time datetime="2026-02-13">2026.02.13</time>
      <a href="/information/ir/news/2026/0213_01.html">代表取締役の異動に関するお知らせ</a></li>
  <li><time datetime="2026-02-13">2026.02.13</time>
      <a href="/information/ir/news/2026/0213_02.html">2025年12月期 決算短信</a></li>
  <li><time datetime="2026-01-30">2026.01.30</time>
      <a href="/information/ir/news/2026/0130_01.html">中期経営計画の進捗について</a></li>
  <li><time datetime="2025-12-10">2025.12.10</time>
      <a href="/information/ir/news/2025/1210_01.html">執行役員の異動について</a></li>
  <li><time datetime="2025-11-05">2025.11.05</time>
      <a href="/information/ir/news/2025/1105_01.html">自己株式取得に関するお知らせ</a></li>
</ul></main></body></html>"""

SITEMAP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example-forestry.jp/</loc></url>
<url><loc>https://example-forestry.jp/information/company/</loc></url>
<url><loc>https://example-forestry.jp/information/company/aboutus/</loc></url>
<url><loc>https://example-forestry.jp/information/company/officer/</loc></url>
<url><loc>https://example-forestry.jp/information/newsroom/</loc></url>
<url><loc>https://example-forestry.jp/information/ir/news/</loc></url>
<url><loc>https://example-forestry.jp/japanese/corporate/officer.html</loc></url>
<url><loc>https://example-forestry.jp/english/company/officer/</loc></url>
</urlset>"""


# ---------------------------------------------------------------------------
# 第2シナリオ: sfc.jp（住友林業）の実構造を再現
#   - グローバルナビに「ニュースルーム」がある（=タブ）
#   - その配下に全件一覧「ニュースリリース」があるが、どちらもJS描画で
#     静的HTMLには記事が入っていない
#   - トップページだけが最新リリースを静的HTMLで持っている
# ---------------------------------------------------------------------------
SFC_BASE = "https://sfc-example.jp"

SFC_HOME = """<html><head><title>木と生きる幸福 サンプル林業</title></head><body>
<nav class="gnav">
  <a href="/ie/">注文住宅</a>
  <a href="/information/">企業・IR・ESG・採用</a>
  <a href="/information/company/">会社情報</a>
  <a href="/information/ir/">株主・投資家の皆様へ(IR情報)</a>
  <a href="/information/sustainability/">サステナビリティ</a>
  <a href="/information/saiyou/employment/">採用情報</a>
  <a href="/information/newsroom/">ニュースルーム</a>
</nav>
<main>
  <ul class="tab"><li><a href="#tab1">ニュースリリース</a></li>
    <li><a href="#tab2">TOPICS</a></li><li><a href="#tab3">ストーリー</a></li></ul>
  <div class="release-list">
    <div class="item">2026.09.01
      <a href="/information/news/2026/2026-09-01.html">2026年 オリコン顧客満足度ランキング 6年連続総合1位を受賞</a>
      カテゴリ：建築</div>
    <div class="item">2026.08.31
      <a href="/information/news/pdf/2026-08-31.pdf">劣後特約付シンジケートローンの契約締結に関するお知らせ</a>
      カテゴリ：その他</div>
    <div class="item">2026.08.31
      <a href="/information/news/pdf/2026-08-31_02.pdf">人事異動のお知らせ</a>
      カテゴリ：その他</div>
    <div class="item">2026.07.27
      <a href="/information/news/2026/2026-07-27.html">マンション緑化の効果を検証する共同研究を開始</a>
      カテゴリ：建築</div>
    <div class="item">2026.06.30
      <a href="/information/news/2026/2026-06-30.html">第1回日本物流大賞「奨励賞」を受賞</a>
      カテゴリ：木材</div>
  </div>
  <a href="/information/newsroom/">ニュースルーム一覧へ</a>
</main></body></html>"""

SFC_NEWSROOM = """<html><head><title>ニュースルーム｜サンプル林業</title></head><body>
<nav class="gnav"><a href="/information/newsroom/">ニュースルーム</a></nav>
<div class="breadcrumb"><a href="/">ホーム</a><a href="/information/">企業・IR・ESG・採用</a></div>
<main><h1>ニュースルーム</h1>
<p>JavaScriptの設定がオンになっていないため、一部ご利用いただけない機能があります。</p>
<ul class="local-nav">
  <li><a href="/information/news/">ニュースリリース</a></li>
  <li><a href="/information/newstopics/">TOPICS</a></li>
  <li><a href="/information/newsroom/story/">ストーリー</a></li>
</ul>
<div id="newsList"></div></main></body></html>"""

SFC_NEWS = """<html><head><title>ニュースリリース｜サンプル林業</title></head><body>
<nav class="gnav"><a href="/information/newsroom/">ニュースルーム</a></nav>
<div class="breadcrumb"><a href="/">ホーム</a>
<a href="/information/newsroom/">ニュースルーム</a></div>
<main><h1>ニュースリリース</h1>
<p>JavaScriptの設定がオンになっていないため、一部ご利用いただけない機能があります。</p>
<div class="filter">年から選ぶ 2026年 2025年 2024年 カテゴリーから選ぶ すべて 決算 その他</div>
<div id="newsList"></div></main></body></html>"""

SFC_PAGES = {
    "sfc-example.jp/": SFC_HOME,
    "sfc-example.jp/information/newsroom": SFC_NEWSROOM,
    "sfc-example.jp/information/news": SFC_NEWS,
    "sfc-example.jp/information/company": COMPANY_HUB.replace(
        "example-forestry.jp", "sfc-example.jp"),
    "sfc-example.jp/information/company/aboutus": ABOUTUS,
    "sfc-example.jp/information/company/officer": OFFICER,
    "sfc-example.jp/information/ir": IR_HUB,
    "sfc-example.jp/information/ir/news": SFC_NEWS.replace(
        "ニュースリリース", "IRニュース"),
}

PAGES = {
    "example-forestry.jp/": HOME,
    "example-forestry.jp/information/company": COMPANY_HUB,
    "example-forestry.jp/information/ir": IR_HUB,
    "example-forestry.jp/information/company/aboutus": ABOUTUS,
    "example-forestry.jp/information/company/officer": OFFICER,
    "example-forestry.jp/japanese/corporate/officer.html": LEGACY_OFFICER,
    "example-forestry.jp/english/company/officer": OFFICER,
    "example-forestry.jp/information/newsroom": NEWSROOM,
    "example-forestry.jp/information/ir/news": IR_NEWS,
    "example-forestry.jp/sitemap.xml": SITEMAP_XML,
    "example-forestry.jp/information/company/corporate_governance.html":
        "<html><title>コーポレートガバナンス</title><body><main>"
        "<h1>コーポレートガバナンス</h1><p>取締役会の実効性評価について</p>"
        "</main></body></html>",
    "example-forestry.jp/information/ir/stockholder/yakuin_hoshu.html":
        "<html><title>役員報酬について</title><body><main><h1>役員報酬</h1>"
        "<p>報酬の決定方針</p></main></body></html>",
    "example-forestry.jp/information/ir/library":
        "<html><title>IRライブラリ</title><body><main><h1>IRライブラリ</h1>"
        "<a href='/pdf/a.pdf'>統合報告書2026</a></main></body></html>",
}


class FakeFetcher:
    """ネットワークを使わない Fetcher 互換オブジェクト。"""

    def __init__(self, pages: dict):
        self.pages = dict(pages)
        self.fetch_count = 0
        self.log: list = []

    def allowed(self, url: str) -> bool:
        return True

    def sitemaps(self, url: str) -> list:
        return []

    def get(self, url, etag=None, last_modified=None) -> FetchResult:
        self.fetch_count += 1
        self.log.append(url)
        key = url_key(url)
        html = self.pages.get(key)
        if html is None:
            return FetchResult(url, 404, error="not found (fake)")
        return FetchResult(canonicalize(url) or url, 200, text=html,
                           headers={"Content-Type": "text/html; charset=utf-8"})


def check(label, cond):
    print(("  OK   " if cond else "  FAIL ") + label)
    return bool(cond)


def main() -> int:
    ok = True

    print("[1] URL 正規化")
    ok &= check("index.html を除去",
                canonicalize("/information/company/officer/index.html", BASE + "/")
                == BASE + "/information/company/officer/")
    ok &= check("トラッキングパラメータを除去",
                canonicalize(BASE + "/a/?utm_source=x&id=3") == BASE + "/a/?id=3")
    ok &= check("www有無と末尾スラッシュを吸収",
                url_key("https://www.example.jp/a/") == url_key("http://example.jp/a"))

    print("[2] 探索")
    fetcher = FakeFetcher(PAGES)
    result = discover(BASE + "/", fetcher, company="サンプル林業")
    pages = result["pages"]
    expect = {
        "company_profile": BASE + "/information/company/aboutus/",
        "officers": BASE + "/information/company/officer/",
        "news_public": BASE + "/information/newsroom/",
        "news_ir": BASE + "/information/ir/news/",
    }
    for cat, want in expect.items():
        got = (pages.get(cat) or {}).get("url")
        ok &= check(f"{cat}: {got}", got == want)
        ok &= check(f"{cat}: status={pages[cat]['status']}",
                    pages[cat]["status"] in ("confirmed", "likely"))

    print("[3] 一覧ページの指紋（更新検知の下準備）")
    np = pages["news_public"]
    ok &= check("一覧の最新日付を把握 " + str(np.get("latest_entry_date")),
                np.get("latest_entry_date") == "2026-08-31")
    ok &= check(f"既知記事URLを記録 ({np.get('entry_count')}件)",
                (np.get("entry_count") or 0) >= 6 and
                any("0831_01.html" in u for u in np.get("known_entry_urls") or []))
    ok &= check("IR一覧も同様", (pages["news_ir"].get("entry_count") or 0) >= 5)
    ok &= check("会社概要・役員一覧は本文ハッシュを保持",
                pages["company_profile"].get("content_hash") and
                pages["officers"].get("content_hash"))
    ok &= check("RSSフィードを検出",
                any("rss" in f for f in result.get("feeds") or []))
    ok &= check("記事の中身は出力しない（後段プログラムの担当）",
                "hr_articles" not in result)

    print("[4] 更新検知")
    state = json.loads(json.dumps(result))          # 保存→再読込を模す
    updated = dict(PAGES)
    updated["example-forestry.jp/information/newsroom"] = NEWSROOM.replace(
        '<ul class="news-list">',
        '<ul class="news-list">\n  <li><time datetime="2026-09-01">2026.09.01</time>'
        '<a href="/information/newsroom/2026/0901_01.html">'
        '役員人事および組織改編について</a></li>')
    updated["example-forestry.jp/information/company/officer"] = OFFICER.replace(
        "山田 太郎", "松本 七郎")
    out = check_updates(state, FakeFetcher(updated))
    kinds = {(e["category"], e["type"]) for e in out["events"]}
    ok &= check("ニュース一覧の新着を検知", ("news_public", "new_entries") in kinds)
    ok &= check("役員一覧の内容変更を検知", ("officers", "content_changed") in kinds)
    ok &= check("変更のなかった会社概要はイベントを出さない",
                ("company_profile", "content_changed") not in kinds)
    ev = next((e for e in out["events"] if e["type"] == "new_entries"), None)
    ok &= check("イベントには後段に渡す一覧URLが入っている",
                ev and ev.get("list_url") == BASE + "/information/newsroom/")
    ok &= check("新着件数は1件（既存記事は再通知しない）",
                ev and ev.get("new_count") == 1)

    print("[5] sfc.jp型サイト（JS描画の一覧＋ナビ上のタブ）")
    f2 = FakeFetcher(SFC_PAGES)
    r2 = discover(SFC_BASE + "/", f2, company="サンプル林業")
    p2 = r2["pages"]
    ok &= check(f"会社概要: {p2['company_profile']['url']}",
                p2["company_profile"]["url"] == SFC_BASE + "/information/company/aboutus/")
    ok &= check(f"役員一覧: {p2['officers']['url']}",
                p2["officers"]["url"] == SFC_BASE + "/information/company/officer/")
    ok &= check(f"ニュースタブ: {p2['news_public']['url']}",
                p2["news_public"]["url"] == SFC_BASE + "/information/newsroom/")
    ok &= check("配下の全件一覧ではなくタブが選ばれる",
                p2["news_public"]["url"] != SFC_BASE + "/information/news/")
    ok &= check("JS描画のため未検証だが not_found にはしない",
                p2["news_public"]["status"] == "likely" and
                p2["news_public"]["evidence"].get("render") == "javascript")
    ok &= check("needs_rendering に入る",
                "news_public" in (r2.get("needs_rendering") or []))
    ok &= check("needs_review には入らない",
                "news_public" not in (r2.get("needs_review") or []))
    ok &= check(f"更新検知はトップページの新着で代替: {p2['news_public'].get('entry_source')}",
                p2["news_public"].get("entry_source") == SFC_BASE + "/")
    ok &= check("人事異動リリースを既知記事として把握",
                any("2026-08-31_02.pdf" in u
                    for u in p2["news_public"].get("known_entry_urls") or []))

    print(f"\n取得回数: {fetcher.fetch_count} 回 / {f2.fetch_count} 回")
    print("\n=== " + ("全テスト成功" if ok else "失敗あり") + " ===")
    if not ok:
        print(json.dumps(result.get("debug_top_candidates"),
                         ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
