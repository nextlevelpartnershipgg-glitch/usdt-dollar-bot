\
import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse
from datetime import datetime, timezone
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont

# ====== Настройка ======
BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # пример: @USDT_Dollar

# Белый список RSS-источников (можешь менять/добавлять)
RSS_FEEDS = [
    "http://feeds.reuters.com/reuters/worldNews",
    "http://feeds.reuters.com/reuters/businessNews",
    "https://apnews.com/apf-topnews?output=rss",
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://www.theblock.co/rss"
]

# Хэштеги по умолчанию
TAGS = "#мировыеновости #доллар #usdt #рынки #крипта"

# Папки/файлы состояния
DATA_DIR = pathlib.Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

# ====== Утилиты ======
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def domain(url):
    return urllib.parse.urlparse(url).netloc.replace("www.", "") or "source"

def clean_html(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text(separator=" ").split())

def make_caption(title, summary, link):
    title = title.strip()
    summary = (summary or "").strip()
    if len(summary) > 300:
        summary = summary[:297] + "…"
    caption = f"💵 {title}\n— {summary}\n\n🔗 Источник: {link}\n{TAGS}"
    # Telegram caption лимит ~1024 символа
    if len(caption) > 1020:
        extra = len(caption) - 1020
        summary = summary[:-extra-1] + "…"
        caption = f"💵 {title}\n— {summary}\n\n🔗 Источник: {link}\n{TAGS}"
    return caption

def draw_card(title_text, src_domain):
    W, H = 1080, 1080
    bg = (18, 20, 22)       # тёмный фон
    green = (16, 185, 129)  # акцент USDT-зелёный
    gray = (160, 160, 160)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # Шрифты по умолчанию в GitHub Actions (DejaVu)
    title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
    small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    brand_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)

    # Верхняя плашка
    d.rectangle([(0,0),(W,140)], fill=green)
    d.text((40, 45), "USDT=Dollar", fill=(0,0,0), font=brand_font)

    # Дата/время UTC
    now = datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")
    d.text((W-40 - d.textlength(now, font=small_font), 50), now, fill=(0,0,0), font=small_font)

    # Заголовок
    margin = 80
    wrapped = textwrap.wrap(title_text, width=20)  # грубая обёртка
    y = 220
    for line in wrapped[:8]:
        d.text((margin, y), line, font=title_font, fill=(235,235,235))
        y += 80

    # Источник (низ)
    src = f"source: {src_domain}"
    d.text((margin, H-80), src, font=small_font, fill=gray)

    # Картинка в память
    bio = io.BytesIO()
    img.save(bio, format="PNG", optimize=True)
    bio.seek(0)
    return bio

def send_photo(photo_bytes, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption}
    r = requests.post(url, files=files, data=data, timeout=30)
    r.raise_for_status()
    return r.json()

# ====== Основной цикл ======
def main():
    state = load_state()

    for feed_url in RSS_FEEDS:
        fp = feedparser.parse(feed_url)
        if not fp.entries:
            continue

        # Берём самую свежую запись
        def parse_dt(e):
            ts = getattr(e, "published", getattr(e, "updated", "")) or ""
            try:
                return dtparse.parse(ts)
            except Exception:
                return datetime(1970,1,1, tzinfo=timezone.utc)

        entry = sorted(fp.entries, key=parse_dt, reverse=True)[0]

        link = getattr(entry, "link", "")
        title = getattr(entry, "title", "").strip() or "(no title)"
        summary = clean_html(getattr(entry, "summary", getattr(entry, "description", "")))

        # ID записи для дедупликации (по ссылке/тайтлу)
        entry_uid = hashlib.sha256((link or title).encode("utf-8")).hexdigest()
        last_uid = state.get(feed_url, "")

        if entry_uid == last_uid:
            # уже постили свежую запись этого фида
            continue

        # Подпись и карточка
        cap = make_caption(title, summary, link or feed_url)
        card = draw_card(title, domain(link or feed_url))

        # Отправка
        try:
            send_photo(card, cap)
            state[feed_url] = entry_uid  # обновить состояние
        except Exception as e:
            print("Error sending:", e)

    save_state(state)

if __name__ == "__main__":
    main()
