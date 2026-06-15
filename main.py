#!/usr/bin/env python3
"""
大大寬頻最新消息 -> Telegram 推送

原理：
  網站沒有 RSS，公告是用 JavaScript 動態載入。
  本腳本直接呼叫官網的 JSON API 取得最新公告，
  比對已推送紀錄，把新公告透過 Telegram Bot 發給你。

用法：
  python3 dada_news_to_telegram.py            # 正式跑：只推送上次之後的新公告
  python3 dada_news_to_telegram.py --dry-run  # 只抓取並印出，不發送、不需 Token
  python3 dada_news_to_telegram.py --test     # 立刻把「最新一則」發到 Telegram（測試用）

環境變數（正式跑與 --test 需要）：
  TG_BOT_TOKEN  你的 Telegram Bot Token（找 @BotFather 申請）
  TG_CHAT_ID    要接收訊息的 chat id（你自己或群組）
  STATE_FILE    已推送紀錄檔路徑，預設 seen_news.json
"""
import os
import re
import sys
import json
import html
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

API_URL = "https://www.dadabroadband.com/api/Cs/GetNewsList"
DETAIL_URL = "https://www.dadabroadband.com/about/news_detail/{nid}"
STATE_FILE = Path(os.environ.get("STATE_FILE", "seen_news.json"))
PROBE_SIZE = 50          # 初次探測用的頁面大小，用來讀出總筆數
TG_LIMIT = 4096

def fetch_page(page, page_size):
    body = json.dumps({"Page": page, "PageSize": page_size}).encode("utf-8")
    req = Request(
        API_URL,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (news-monitor)",
        },
        method="POST",
    )
    with urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if payload.get("status") != "Success":
        raise RuntimeError(f"API 回傳非成功狀態：{payload.get('status')}")
    return payload.get("data") or []

def fetch_all_news():
    # 重要：此 API 是依「發布日(StartDate)」遞減排序，不是依 NID。
    # 若只抓第一頁，補登舊日期的新公告(NID 最大但日期舊)會排到後面而被漏掉。
    # 因此每次都抓完整清單，再用 NID 對整份去重，排序怎麼變都不影響。
    items = fetch_page(1, PROBE_SIZE)
    if not items:
        return []
    total = items[0].get("TOTAL_ROW_COUNT", len(items))
    if total > len(items):
        items = fetch_page(1, total + 10)   # 一次補齊全部
    return items

def strip_html(raw):
    text = re.sub(r"<br\s*/?>", "\n", raw, flags=re.I)
    text = re.sub(r"</(div|p)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def build_message(item):
    nid = item["NID"]
    title = item.get("Title", "").strip()
    date = item.get("StartDate", "")
    content = strip_html(item.get("NContent") or item.get("Summary") or "")
    url = DETAIL_URL.format(nid=nid)
    if len(content) > 3000:
        content = content[:3000].rstrip() + "…\n（內容過長，詳見連結）"
    msg = (
        f"【大大寬頻公告】{date}\n"
        f"<b>{html.escape(title)}</b>\n\n"
        f"{html.escape(content)}\n\n"
        f"{url}"
    )
    return msg[:TG_LIMIT]

def send_telegram(text):
    token = os.environ["TG_BOT_TOKEN"]
    chat_id = os.environ["TG_CHAT_ID"]
    api = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }).encode("utf-8")
    req = Request(api, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(req, timeout=30) as r:
        res = json.load(r)
    if not res.get("ok"):
        raise RuntimeError(f"Telegram 發送失敗：{res}")
    return res

def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()

def save_seen(seen):
    STATE_FILE.write_text(json.dumps(sorted(seen)), encoding="utf-8")

def main():
    args = set(sys.argv[1:])
    items = fetch_all_news()
    if not items:
        print("沒有抓到任何公告。", file=sys.stderr)
        return 0

    if "--dry-run" in args:
        for item in sorted(items, key=lambda i: i["NID"], reverse=True):
            print("=" * 60)
            print(build_message(item))
        print("=" * 60)
        print(f"（dry-run）共 {len(items)} 則，未發送。")
        return 0

    if "--test" in args:
        latest = max(items, key=lambda i: i["NID"])
        send_telegram(build_message(latest))
        print(f"已發送測試訊息：NID {latest['NID']}")
        return 0

    # 正式模式
    seen = load_seen()
    first_run = not STATE_FILE.exists()
    new_items = sorted((i for i in items if i["NID"] not in seen), key=lambda i: i["NID"])

    if first_run:
        # 首次執行只記錄現況，不洗版
        for item in items:
            seen.add(item["NID"])
        save_seen(seen)
        print(f"首次執行：已記錄目前 {len(items)} 則公告為基準，未發送。之後只推送新公告。")
        return 0

    sent = 0
    for item in new_items:
        send_telegram(build_message(item))
        seen.add(item["NID"])
        sent += 1
    save_seen(seen)
    print(f"完成：本次推送 {sent} 則新公告。")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except (HTTPError, URLError) as e:
        print(f"網路錯誤：{e}", file=sys.stderr)
        sys.exit(1)
    except KeyError as e:
        print(f"缺少環境變數：{e}（請設定 TG_BOT_TOKEN 與 TG_CHAT_ID）", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"執行失敗：{e}", file=sys.stderr)
        sys.exit(1)
