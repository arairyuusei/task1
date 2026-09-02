# -*- coding: utf-8 -*-
"""
LLM フォールバック。

ルールベースで決着しなかったカテゴリ（全体の数%想定）だけを LLM に渡す。
候補URLとアンカーテキストという「小さな構造化データ」を渡すだけなので、
HTML全文を投げるより桁違いに安く、再現性も高い。

環境変数
  ANTHROPIC_API_KEY : 必須
  ANTHROPIC_MODEL   : 任意（既定 claude-sonnet-5）
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import requests

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

SYSTEM_PROMPT = """あなたは日本企業のコーポレートサイトの構造に詳しいアナリストです。
与えられたリンク候補（URLとアンカーテキスト）から、指定されたカテゴリに最も適合する
1件を選びます。

カテゴリの定義:
- company_profile: 会社概要・企業概要・会社案内。設立年月日、資本金、代表者、
  従業員数などが表形式で載っているページ。
- officers: 役員一覧・役員紹介・経営体制。取締役や執行役員の氏名と役職が
  一覧で載っているページ。役員報酬の方針ページ、グループ会社の役員ページ、
  採用向けの経営者紹介は選ばない。
- news_public: 一般向けニュースの「一覧ページ」。個別記事のURLは選ばない。
- news_ir: 株主・投資家向けニュース（IRニュース、適時開示）の「一覧ページ」。

規則:
1. 候補にない URL を創作してはならない。
2. 個別記事・PDF・英語版・アーカイブは選ばない。
3. 適合する候補が無い場合は url を null にする。曖昧なまま選ぶより null が良い。
4. 出力は JSON のみ。前置きも Markdown のコードフェンスも付けない。"""

USER_TEMPLATE = """企業名: {company}
サイト: {base_url}

判定してほしいカテゴリ: {categories}

候補一覧:
{candidates}

次の形式の JSON だけを出力してください:
{{"picks": {{"<category>": {{"url": "<候補中のURL または null>",
  "confidence": <0.0-1.0>, "reason": "<20字程度の根拠>"}}}}}}"""


def _format_candidates(candidates: dict) -> str:
    lines = []
    for cat, rows in candidates.items():
        lines.append(f"[{cat}]")
        for r in rows:
            lines.append(f"  - url: {r['url']}\n    anchor: {r.get('anchor') or '(なし)'}"
                         f"\n    rule_score: {r.get('score')}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    text = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def make_adjudicator(model: Optional[str] = None, api_key: Optional[str] = None,
                     timeout: float = 60.0):
    """discover(llm=...) に渡す callable を返す。"""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY が設定されていません")
    model = model or DEFAULT_MODEL

    def adjudicate(company, base_url, categories, candidates) -> dict:
        allowed = {r["url"] for rows in candidates.values() for r in rows}
        body = {
            "model": model,
            "max_tokens": 1024,
            "temperature": 0,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": USER_TEMPLATE.format(
                company=company or "(不明)", base_url=base_url,
                categories=", ".join(categories),
                candidates=_format_candidates(candidates))}],
        }
        r = requests.post(API_URL, timeout=timeout, json=body, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01",
            "content-type": "application/json"})
        r.raise_for_status()
        text = "".join(b.get("text", "") for b in r.json().get("content", [])
                       if b.get("type") == "text")
        data = _extract_json(text)
        # ハルシネーション対策: 候補に無いURLは捨てる
        picks = {}
        for cat, pick in (data.get("picks") or {}).items():
            url = (pick or {}).get("url")
            if url and url in allowed:
                picks[cat] = pick
            elif url:
                picks[cat] = {"url": None, "confidence": 0.0,
                              "reason": "候補外URLのため破棄"}
        return {"model": model, "picks": picks, "raw": data}

    return adjudicate


# ---------------------------------------------------------------------------
# 参考: 後段の「人事情報抽出」で使うプロンプト雛形
# discover が特定した各URLの本文を渡して構造化データを得る。
# ---------------------------------------------------------------------------
EXTRACTION_PROMPT = """以下は「{company}」の{page_label}ページ（{url}、取得日 {fetched_at}）の本文です。
役員・幹部の人事情報を抽出し、JSON配列で出力してください。

抽出項目:
  name（氏名／原文表記）, name_kana（あれば）, title（役職の原文表記）,
  role_type（chairman/president/vice_president/executive_director/director/
  outside_director/auditor/executive_officer/other のいずれか）,
  is_representative（代表権の有無 true/false/null）,
  concurrent_posts（兼任・委嘱の原文）, effective_date（YYYY-MM-DD、発効日。不明ならnull）,
  change_type（appointed/retired/promoted/reassigned/unchanged/null）,
  source_quote（根拠となる原文を40字以内）

規則:
- 本文に書かれていないことは推測せず null にする。
- 氏名の表記（スペースの有無、旧字体）は原文どおりにする。
- 発効日が「〇年〇月〇日付」と書かれている場合は effective_date に入れる。
- 出力は JSON のみ。

本文:
---
{body}
---"""
