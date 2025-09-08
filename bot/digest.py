import os, json, pathlib
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import requests

BOT_TOKEN  = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@usdtdollarm")
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Moscow")

DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
HISTORY_FILE = DATA_DIR / "history.json"
STATE_FILE   = DATA_DIR / "digest_state.json"

def load_json(p, default):
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default

def save_json(p, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def send_message(text):
    url=f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r=requests.post(url, data={"chat_id": CHANNEL_ID, "text": text, "parse_mode":"Markdown"})
    print("Telegram:", r.status_code, r.text[:120]); r.raise_for_status()

def main():
    hist = load_json(HISTORY_FILE, [])
    state = load_json(STATE_FILE, {"last_digest_utc": None})
    now_utc = datetime.now(timezone.utc)
    tz = ZoneInfo(TIMEZONE)

    window_start = now_utc - timedelta(hours=8)

    last = state.get("last_digest_utc")
    if last:
        last_dt = datetime.fromisoformat(last)
        if last_dt >= window_start:
            print("Digest already done for this window.")
            return

    items = []
    for it in hist:
        try:
            posted = datetime.fromisoformat(it["posted_utc"])
            if posted >= window_start:
                items.append(it)
        except Exception:
            continue

    if not items:
        print("No items for digest.")
        state["last_digest_utc"]=now_utc.isoformat(); save_json(STATE_FILE, state)
        return

    items.sort(key=lambda x: x["posted_utc"], reverse=True)

    lines = ["*Дайджест за 8 часов*"]
    for it in items[:18]:
        ev = datetime.fromisoformat(it["event_utc"]).astimezone(tz).strftime("%d.%m %H:%M")
        title = it["title"]
        link = it["link"]
        lines.append(f"• {title}  — [{ev}]({link})")

    lines.append("")
    lines.append("🪙 [USDT=Dollar](https://t.me/usdtdollarm)")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:3996] + "…"

    send_message(text)
    state["last_digest_utc"] = now_utc.isoformat()
    save_json(STATE_FILE, state)

if __name__ == "__main__":
    main()
