# -*- coding: utf-8 -*-
"""
キーマンレター: URL 判定ルール（キーワード辞書）

エンジニア以外でもチューニングできるよう、判定ロジックとは分離している。
weight は加点（正）／減点（負）。パターンは Python の正規表現で、
アンカーテキスト（リンク文字列）と URL の両方に対して評価する。

チューニングの原則
------------------
* 「専用ページ」を指す語は高く（役員一覧=10）、「上位階層」を指す語は低く（会社情報=4）。
  上位階層は HUB_CATEGORIES 側で拾う。
* 誤検知しやすい語は必ず negative に入れる（例: 役員報酬、採用、グループ会社役員）。
* 迷ったら weight を動かすのではなく、まず eval_set.csv に正解を1件追加すること。
"""

# ---------------------------------------------------------------------------
# 収集対象カテゴリ（最終的に出力したい4種）
# ---------------------------------------------------------------------------
TARGET_CATEGORIES = {
    "company_profile": {
        "label": "会社概要",
        "verify": "profile",
        "watch": "hash",          # ページ全文ハッシュで更新検知
        "prefetch_threshold": 6,
        "accept_threshold": 12,
        "hub_bonus_from": ["hub_company"],
        "anchor": [
            (r"^会社概要$", 12),
            (r"^企業概要$", 12),
            (r"^会社案内$", 10),
            (r"^会社データ$", 10),
            (r"^(会社|企業)(概要|基本)?情報$", 5),
            (r"会社概要|企業概要|会社案内|会社データ|企業データ", 9),
            (r"^(company\s*)?(profile|outline|overview|data)$", 9),
            (r"corporate\s*(profile|data|outline)", 9),
            (r"^about\s*(us)?$", 6),
            (r"^概要$", 5),
        ],
        "path": [
            (r"/(aboutus|about-us|about|profile|outline|overview|companyprofile|"
             r"company_profile|corporate_?data|gaiyou|gaiyo|kaisha|summary)(/|\.|$)", 9),
            (r"/(company|corporate|information|kigyo|about)/", 2),
        ],
        "negative": [
            (r"(採用|求人|新卒|中途|キャリア|recruit|career|entry)", -14),
            (r"(グループ会社|関係会社|子会社|group\s*compan)", -8),
            (r"(沿革|歴史|history)", -4),
            (r"(サステナ|sustainab|csr|esg)", -6),
            (r"\.pdf$", -6),
        ],
    },
    "officers": {
        "label": "役員一覧",
        "verify": "officers",
        "watch": "hash",
        "prefetch_threshold": 6,
        "accept_threshold": 12,
        "hub_bonus_from": ["hub_company", "hub_ir"],
        "anchor": [
            (r"^役員(一覧|紹介|構成|体制|名簿)$", 12),
            (r"^(取締役|経営陣|経営体制|経営メンバー)(一覧|紹介)?$", 10),
            (r"役員(一覧|紹介|構成|体制|名簿)", 9),
            (r"取締役(会)?(一覧|名簿|の構成)", 8),
            (r"(経営陣|経営メンバー|マネジメントチーム|マネジメント体制)", 7),
            (r"^(役員|取締役|執行役員)", 6),
            (r"^(management|officers?|executives?|directors?|"
             r"board\s*of\s*directors|leadership|management\s*team)$", 10),
            (r"(management|officers|executives|board\s*members|leadership)", 6),
        ],
        "path": [
            (r"/(officer|officers|yakuin|executive|executives|management|"
             r"board|directors?|leadership|keiei)(/|\.|_|$)", 9),
            (r"/(company|corporate|information|about|ir)/", 2),
        ],
        "negative": [
            (r"(役員報酬|報酬(制度|方針)|remuneration|compensation)", -12),
            (r"(採用|求人|新卒|中途|recruit|career)", -14),
            (r"(グループ会社|関係会社|子会社|group\s*compan)", -10),
            (r"(株主総会|議決権|招集通知)", -8),
            (r"(コーポレートガバナンス|governance)", -3),  # 役員一覧が中に埋まる場合の保険で軽め
            (r"(スキルマトリ|skill\s*matrix)", -4),
            (r"\.pdf$", -5),
        ],
    },
    "news_public": {
        "label": "ニュース（一般向け）",
        "verify": "newslist",
        "watch": "entries",       # 一覧の記事差分で更新検知
        "prefetch_threshold": 6,
        "accept_threshold": 12,
        "hub_bonus_from": ["hub_news", "hub_company"],
        # グローバルナビにある「タブ」を、その配下の一覧より優先する。
        # 例: ニュースルーム(タブ) を ニュースリリース(配下の全件一覧) より優先。
        "prefer_nav_tab": True,
        "anchor": [
            (r"^(ニュース|ニュースリリース|ニュースルーム|プレスリリース)$", 12),
            (r"^(お知らせ|新着情報|新着ニュース|トピックス|最新情報|報道発表)$", 11),
            (r"(ニュースリリース|ニュースルーム|プレスリリース|報道発表)", 9),
            (r"(ニュース|お知らせ|新着情報|トピックス)", 6),
            (r"^(news|news\s*releases?|newsroom|press\s*releases?|topics|"
             r"what'?s\s*new|media)$", 10),
            (r"(news|press\s*release|newsroom)", 5),
        ],
        "path": [
            (r"/(news|newsroom|newsrelease|news_release|press|pressrelease|"
             r"release|topics|whatsnew|oshirase|info|information)(/|$)", 9),
            (r"/(news|press|topics)/(list|index)?/?$", 3),
        ],
        "negative": [
            (r"(^|[^a-z])ir([^a-z]|$)|投資家|株主|適時開示|決算", -8),
            (r"(採用|求人|recruit|career)", -12),
            (r"(rss|atom|feed|\.xml$)", -10),
            (r"/20\d{2}[/\-_]?(0[1-9]|1[0-2])", -7),   # 個別記事URLらしい
            (r"/(20\d{2})/?$", -4),                     # 年別アーカイブ
            (r"(archive|backnumber|バックナンバー|過去の)", -5),
            (r"\.pdf$", -12),
            (r"(page|p)=\d+|/page/\d+", -6),
        ],
    },
    "news_ir": {
        "label": "ニュース（IR向け）",
        "verify": "newslist",
        "watch": "entries",
        "prefetch_threshold": 6,
        "accept_threshold": 11,
        "hub_bonus_from": ["hub_ir"],
        "prefer_nav_tab": True,
        "anchor": [
            (r"^(ir|IR)(ニュース|情報|お知らせ|リリース|トピックス)$", 12),
            (r"^(適時開示|適時開示情報|開示資料|プレスリリース\s*\(IR\))$", 11),
            (r"(IRニュース|IRリリース|IRお知らせ|IRトピックス|適時開示)", 10),
            (r"(投資家|株主).{0,8}(ニュース|お知らせ|情報)", 8),
            (r"^(ir\s*)?(news|releases?|topics|library|information)$", 7),
            (r"^(株主・投資家|投資家情報|IR情報|株主・投資家の皆様へ)", 5),
        ],
        "path": [
            (r"/ir/(news|release|releases|topics|whatsnew|disclosure|tekiji|"
             r"library|information|info)(/|$)", 10),
            (r"/(ir|investor|investors|kabu)(/|$)", 5),
            (r"/(irnews|ir_news|ir-news)(/|$)", 10),
        ],
        "negative": [
            (r"(採用|求人|recruit)", -12),
            (r"(rss|atom|feed|\.xml$)", -10),
            (r"/20\d{2}[/\-_]?(0[1-9]|1[0-2])", -7),
            (r"(archive|backnumber|過去の)", -5),
            (r"\.pdf$", -12),
            (r"(page|p)=\d+|/page/\d+", -6),
            (r"(faq|calendar|カレンダー|よくあるご質問|メール配信|アラート)", -8),
        ],
    },
}

# prefer_nav_tab が有効なカテゴリで、首位とこの点差以内の候補を
# 「タブらしさ」で並べ替える。大きすぎると弱い候補を拾うので注意。
NAV_TAB_MARGIN = 12.0

# ---------------------------------------------------------------------------
# ハブ（中間階層）カテゴリ: ここから1階層掘って候補を増やすためだけに使う
# ---------------------------------------------------------------------------
HUB_CATEGORIES = {
    "hub_company": {
        "label": "会社情報トップ",
        "anchor": [
            (r"^(会社|企業)情報(トップ)?$", 12),
            (r"^(会社|企業)案内$", 8),
            (r"(会社情報|企業情報|企業・IR|会社案内|コーポレート)", 7),
            (r"^(company|corporate|about\s*us|about)$", 9),
        ],
        "path": [(r"/(company|corporate|information|about|aboutus|kigyo|profile)(/|$)", 8)],
        "negative": [(r"(採用|recruit|career)", -12), (r"\.pdf$", -10)],
    },
    "hub_ir": {
        "label": "IRトップ",
        "anchor": [
            (r"^(IR情報|IR|株主・投資家|投資家情報|株主・投資家の皆様へ.*)$", 12),
            (r"(IR情報|投資家|株主)", 7),
            (r"^(investor\s*relations?|ir|investors?)$", 10),
        ],
        "path": [(r"/(ir|investor|investors|irinfo|kabu)(/|$)", 9)],
        "negative": [(r"\.pdf$", -10)],
    },
    "hub_news": {
        "label": "ニューストップ",
        "anchor": [
            (r"^(ニュース|ニュースルーム|ニュースリリース|お知らせ|新着情報|トピックス)$", 11),
            (r"(ニュース|お知らせ|新着|トピックス|プレスリリース)", 6),
            (r"^(news|newsroom|press|topics|media)$", 10),
        ],
        "path": [(r"/(news|newsroom|press|topics|information|info|whatsnew)(/|$)", 8)],
        "negative": [(r"\.pdf$", -10), (r"/20\d{2}[/\-_]", -6)],
    },
    "hub_sitemap": {
        "label": "サイトマップ",
        "anchor": [(r"^(サイトマップ|site\s*map)$", 12), (r"(サイトマップ|sitemap)", 6)],
        "path": [(r"/(sitemap|site_map|site-map)(/|\.html?|$)", 9)],
        "negative": [(r"\.xml$", -20)],
    },
}

# ---------------------------------------------------------------------------
# 全カテゴリ共通の減点
# ---------------------------------------------------------------------------
GLOBAL_NEGATIVE = [
    # 日本語サイトを対象にする前提。target_lang を変える場合はここを調整。
    (r"/(en|eng|english|global|us|cn|zh|tw|ko|kr|th|vn|id|fr|de|es)(/|$)", -10),
    (r"[?&](lang|hl)=(en|zh|ko)", -10),
    (r"/(archive|archives|old|backup|bak|test|stg|staging|dev)(/|$)", -8),
    (r"[?&](print|preview)=", -6),
    (r"/(login|mypage|member|entry_form|form)(/|$)", -6),
    (r"^https?://[^/]*(facebook|twitter|x|instagram|youtube|line|linkedin)\.", -30),
]

# ---------------------------------------------------------------------------
# ページ内容による検証（そのURLが本当に目的のページか確かめる）
# ---------------------------------------------------------------------------
VERIFY_RULES = {
    "officers": {
        "patterns": [
            r"代表取締役", r"取締役", r"執行役員", r"監査役", r"社外取締役",
            r"執行役", r"会長", r"社長", r"専務", r"常務", r"監査等委員",
            r"CEO", r"COO", r"CFO",
        ],
        "min_hits": 4,            # 異なる役職語が4種以上
        "min_total": 8,           # 役職語の総出現が8回以上（一覧である証拠）
        "bonus": 8,
        "penalty": -10,
    },
    "profile": {
        "patterns": [
            r"(商号|会社名|名称)", r"(設立|創立|創業)", r"(本社|本店|所在地)",
            r"資本金", r"(従業員数|社員数|従業員)", r"(代表者|代表取締役)",
            r"事業内容", r"(上場|証券コード)", r"決算期", r"URL",
        ],
        "min_hits": 4,
        "min_total": 4,
        "bonus": 8,
        "penalty": -10,
    },
    "newslist": {
        # 日付付きリンクが並んでいるか
        "min_dated_entries": 4,
        "bonus": 8,
        "penalty": -10,
    },
}

# ---------------------------------------------------------------------------
# ニュース一覧ページの解析用
#
# 本プログラムの成果物は「一覧ページのURL」までである。
# 個別記事（例: 2026.08.31 人事異動のお知らせ）の特定は、この一覧URLを
# 入力とする後段のプログラムが担当する。ここでの記事抽出は
#   (a) そのURLが本当に一覧ページかの検証
#   (b) 一覧に更新があったかの検知（更新があれば後段を起動する）
# の2つの目的にのみ使う。
# ---------------------------------------------------------------------------

# 日付として認識するパターン
DATE_PATTERNS = [
    r"(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?",
    r"(20\d{2})(\d{2})(\d{2})",
]

# 一覧がJavaScriptで描画されていることの手がかり。
# これに当たる場合、記事が取れなくても「一覧ページではない」とは判定しない。
CLIENT_RENDER_HINTS = [
    r"JavaScript[^。<]{0,60}(オン|有効|ON)",
    r"(スクリプト|JavaScript)を有効",
    r"enable\s+JavaScript",
    r"__NUXT__|__NEXT_DATA__|window\.__INITIAL",
    r"data-reactroot|ng-app|v-cloak",
]

# 個別記事URLらしさ（一覧ページと区別するため）
ARTICLE_URL_HINTS = [
    r"/20\d{2}[/\-_]", r"\d{6,8}\.html?$", r"[?&](id|newsid|article)=\d+",
    r"/(detail|article|topics_detail)/", r"/\d{4,}/?$",
]
