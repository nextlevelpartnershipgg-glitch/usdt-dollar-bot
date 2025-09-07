import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse
from datetime import datetime, timezone
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo  # для локального времени

# ====== Настройка ======
BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]  # пример: @USDT_Dollar

# Список русских источников новостей (RSS)
RSS_FEEDS = [
    # РБК
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",

    # Lenta.ru
    "https://lenta.ru/rss/news",

    # Газета.ru
    "https://www.gazeta.ru/export/rss/lenta.xml",

    # ТАСС
    "https://tass.ru/rss/v2.xml",

    # Коммерсантъ
    "https://www.kommersant.ru/RSS/news.xml",
]

# Хэштеги по умолчанию
TAGS = "#новости #экономика #Россия #финансы #usdt #доллар"

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

def draw_card(title_text, src_domain, summary_text=""):
    BRAND = "USDT=Dollar"
    TZ = os.environ.get("TIMEZONE", "Europe/Moscow")  # локальный часовой пояс
    W, H = 1080, 1080

    # Цвета/стили
    bg = (18, 20, 22)         # фон
    green = (16, 185, 129)    # акцент
    text_main = (235, 235, 235)
    text_muted = (165, 165, 165)
    black = (0, 0, 0)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # Шрифты
    font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
    font_brand = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)
    font_summary = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)

    # Верхняя бренд-плашка
    d.rectangle([(0, 0), (W, 140)], fill=green)
    d.text((40, 45), BRAND, fill=black, font=font_brand)

    # Время
    try:
        tz = ZoneInfo(TZ)
    except Exception:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d.%m %H:%M")
    d.text((W - 40 - d.textlength(now_str, font=font_small), 50), now_str, fill=black, font=font_small)

    # Заголовок
    margin_x = 80
    y = 180
    for line in textwrap.wrap(title_text, width=22)[:6]:
        d.text((margin_x, y), line, font=font_title, fill=text_main)
        y += 80

    # Summary (короткий текст)
    if summary_text:
        short = summary_text.strip().replace("\n", " ")
        if len(short) > 320:
            short = short[:317] + "…"
        y_sum = y + 20
        for ln in textwrap.wrap(short, width=32):
            if y_sum + 50 > H - 120:
                break
            d.text((margin_x, y_sum), ln, font=font_summary, fill=text_main)
            y_sum += 54

    # Низ: источник
    src = f"source: {src_domain}"
    d.text((margin_x, H - 70), src, font=font_small, fill=text_muted)

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

        entry_uid = hashlib.sha256((link or title).encode("utf-8")).hexdigest()
        last_uid = state.get(feed_url, "")

        if entry_uid == last_uid:
            continue

        cap = make_caption(title, summary, link or feed_url)
        card = draw_card(title, domain(link or feed_url), summary)

        try:
            send_photo(card, cap)
            state[feed_url] = entry_uid
            time.sleep(2)
        except Exception as e:
            print("Error sending:", e)

    save_state(state)

if __name__ == "__main__":
    main()
