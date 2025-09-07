import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse, sys
from datetime import datetime, timezone
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
from zoneinfo import ZoneInfo

# ====== Настройка окружения ======
BOT_TOKEN  = os.environ["BOT_TOKEN"]
CHANNEL_ID = os.environ["CHANNEL_ID"]              # @USDT_Dollar или -100xxxxxxxxx
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Moscow")

# Русские источники (можешь менять)
RSS_FEEDS = [
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",  # РБК
    "https://lenta.ru/rss/news",                          # Lenta
    "https://www.gazeta.ru/export/rss/lenta.xml",         # Газета.ru
    "https://tass.ru/rss/v2.xml",                         # ТАСС
    "https://www.kommersant.ru/RSS/news.xml",             # Коммерсантъ
]

# Хэштеги
TAGS = "#новости #рынки #акции #экономика #usdt #доллар"

# Состояние (дедупликация)
DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
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
    if len(caption) > 1020:
        extra = len(caption) - 1020
        summary = summary[:-extra-1] + "…"
        caption = f"💵 {title}\n— {summary}\n\n🔗 Источник: {link}\n{TAGS}"
    return caption

# --- простая эвристика тональности (RU ключевые слова) ---
POS_WORDS = ["рост", "выше", "подорожал", "укрепил", "поддержал", "рекорд", "вырос", "повысил", "улучшил", "прибыль", "оптимизм"]
NEG_WORDS = ["падени", "ниже", "подешев", "ослаб", "обвал", "кризис", "снижен", "ухудшен", "убыток", "страх", "паник"]

def sentiment(text):
    t = (text or "").lower()
    pos = any(w in t for w in POS_WORDS)
    neg = any(w in t for w in NEG_WORDS)
    if pos and not neg: return "pos"
    if neg and not pos: return "neg"
    return "neutral"

# --- рисуем карточку 1080x540, фон по тональности ---
def draw_card(title_text, src_domain, summary_text=""):
    BRAND = "USDT=Dollar"
    W, H = 1080, 540

    tone = sentiment(f"{title_text} {summary_text}")
    if tone == "pos":
        bg = (8, 94, 60)      # зелёный тёмный
        accent = (16, 185, 129)
        arrow = "↑"
    elif tone == "neg":
        bg = (120, 22, 34)    # красный тёмный
        accent = (239, 68, 68)
        arrow = "↓"
    else:
        bg = (18, 20, 22)     # нейтральный тёмный
        accent = (100, 116, 139)
        arrow = "→"

    text_main  = (240, 240, 240)
    text_muted = (200, 200, 200)
    black = (0, 0, 0)

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)

    # Шрифты
    font_brand   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    font_time    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
    font_title   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56)
    font_summary = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 34)
    font_small   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    font_arrow   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)

    # Верхняя плашка (тонкая)
    d.rectangle([(0,0),(W,90)], fill=accent)
    d.text((28, 26), f"{BRAND}", fill=black, font=font_brand)

    # Локальное время
    try:
        tz = ZoneInfo(TIMEZONE)
    except Exception:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d.%m %H:%M")
    d.text((W - 28 - d.textlength(now_str, font=font_time), 26), now_str, fill=black, font=font_time)

    # Индикатор тренда (стрелка)
    d.text((W - 90, 100), arrow, fill=accent, font=font_arrow)

    # Заголовок
    margin_x = 40
    y = 110
    for line in textwrap.wrap(title_text, width=28)[:3]:
        d.text((margin_x, y), line, font=font_title, fill=text_main)
        y += 66

    # Короткий текст
    if summary_text:
        short = summary_text.strip().replace("\n", " ")
        if len(short) > 220:
            short = short[:217] + "…"
        y_sum = y + 8
        for ln in textwrap.wrap(short, width=40):
            if y_sum + 40 > H - 70:
                break
            d.text((margin_x, y_sum
