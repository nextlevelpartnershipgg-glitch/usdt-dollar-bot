# bot/poster.py
import os
import re
import json
import time
import html
import textwrap
from io import BytesIO
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import feedparser
from PIL import Image, ImageDraw, ImageFont

# ================== НАСТРОЙКИ ==================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # пример: @usdtdollarm
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")

if not BOT_TOKEN or not CHANNEL_ID:
    raise SystemExit("BOT_TOKEN / CHANNEL_ID не заданы. Зайди в Settings → Secrets → Actions и добавь их.")

# Русскоязычные источники
RSS_SOURCES = [
    "https://www.rbc.ru/rss/latest/?utm_source=rss&utm_medium=main",
    "https://lenta.ru/rss/news",
    "https://www.gazeta.ru/export/rss/lenta.xml",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.vedomosti.ru/rss/news",            # может иногда отдавать 403 — ок, пропустим
    "https://www.interfax.ru/rss.asp",
    "https://iz.ru/xml/rss/all.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
]

STATE_PATH = os.path.join("data", "posted.json")   # файл со списком уже опубликованных ссылок
MAX_CAPTION = 1024

# ================== УТИЛИТЫ ==================
def ensure_dirs():
    os.makedirs("data", exist_ok=True)

def load_state():
    ensure_dirs()
    if not os.path.exists(STATE_PATH):
        return {"posted_links": []}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"posted_links": []}

def save_state(state):
    ensure_dirs()
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def clean_html(text: str) -> str:
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    # нормализуем пробелы и тире/кавычки
    text = text.replace("\xa0", " ").replace("&mdash;", "—").replace("&ndash;", "–")
    text = text.replace("&laquo;", "«").replace("&raquo;", "»").replace("&quot;", "«").replace("&amp;", "&")
    text = re.sub(r" +", " ", text).strip()
    # ставим пробелы после знаков препинания, если потерялись
    text = re.sub(r"([,.!?;:])([^\s])", r"\1 \2", text)
    return text

def split_lead_details(text: str):
    """Лид — первое полноценное предложение, остальное — подробности."""
    text = clean_html(text)
    # жёсткий разрез по точке/вопрос/воскл/двоеточию
    parts = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)
    lead = parts[0].strip() if parts else ""
    details = parts[1].strip() if len(parts) > 1 else ""
    return lead, details

def extract_tags(title: str, count: int = 5):
    """Простые теги из ключевых слов заголовка (существительные не гарантируем, но избегаем предлогов)."""
    stop = set("и в во на с со о об от из для по при как что это к у до над под про без".split())
    words = re.findall(r"[А-Яа-яA-Za-z\-]{3,}", title.lower())
    words = [w for w in words if w not in stop]
    uniq = []
    for w in words:
        if w not in uniq:
            uniq.append(w)
        if len(uniq) >= count:
            break
    return uniq

# ------- универсальная функция измерения текста -------
def measure_text(draw, text, font):
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    except Exception:
        return font.getsize(text)

# ------- надёжная загрузка шрифта -------
def load_font(size=32, bold=False):
    candidates = []
    if bold:
        candidates.append(os.path.join("data", "DejaVuSans-Bold.ttf"))
    else:
        candidates.append(os.path.join("data", "DejaVuSans.ttf"))

    if bold:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]

    candidates += [
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ------- рисуем шапку с градиентом -------
def draw_header_image(title: str, source_host: str, event_time: str):
    width, height = 900, 470
    img = Image.new("RGB", (width, height), (24, 24, 28))
    draw = ImageDraw.Draw(img)

    # более насыщенный градиент
    for i in range(height):
        r = 50 + int(i * 120 / height)
        g = 40 + int(i * 80 / height)
        b = 100 + int(i * 70 / height)
        draw.line([(0, i), (width, i)], fill=(r, g, b))

    # диагональные плашки
    for offset in (0, 60, 120):
        draw.polygon(
            [(0, 60 + offset), (width * 0.75, 0 + offset), (width, 0 + offset), (width, 80 + offset), (0, 140 + offset)],
            fill=(0, 0, 0, 40),
        )

    font_title = load_font(46, bold=True)
    font_small = load_font(22)

    # заголовок (wrap)
    margin, offset = 60, 160
    for line in textwrap.wrap(title, width=24):
        draw.text((margin, offset), line, font=font_title, fill="white")
        _, th = measure_text(draw, line, font_title)
        offset += th + 10

    # нижний футер (источник + время события)
    footer_text = f"source: {source_host}  •  событие: {event_time}"
    tw, th = measure_text(draw, footer_text, font_small)
    draw.text((width - tw - 24, height - th - 20), footer_text, font=font_small, fill=(230, 230, 235))

    # круг-«логотип» влево сверху
    logo_size = 64
    logo = Image.new("RGB", (logo_size, logo_size), (220, 220, 230))
    logo_draw = ImageDraw.Draw(logo)
    logo_draw.ellipse((0, 0, logo_size, logo_size), fill=(180, 180, 190))
    img.paste(logo, (24, 20))

    out = BytesIO()
    img.save(out, format="PNG")
    out.seek(0)
    return out

# ------- подпись к посту -------
def build_caption(title, lead, details, link, tags):
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    cap = f"<b>{esc(title)}</b>\n\n"
    if lead:
        cap += f"📰 {esc(lead)}\n\n"
    if details:
        cap += f"<b>Подробности:</b>\n{esc(details)}\n\n"
    if link:
        cap += f"<b>Источник:</b> <a href='{esc(link)}'>{esc(link)}</a>\n\n"
    if tags:
        cap += "".join(f"#{esc(t)} " for t in tags)
    return cap[:MAX_CAPTION]

# ------- отправка в Telegram -------
def tg_send_photo(image_io, caption_html):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("header.png", image_io.getvalue(), "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption_html, "parse_mode": "HTML"}
    r = requests.post(url, data=data, files=files, timeout=30)
    if r.status_code != 200:
        print("Ошибка публикации:", r.status_code, r.text)
        r.raise_for_status()

# ------- получение новостей -------
def fetch_latest_item():
    """Возвращает первый свежий элемент (title, summary, link, source_host, published_local_str)."""
    items = []
    for rss in RSS_SOURCES:
        try:
            fp = feedparser.parse(rss)
        except Exception as e:
            print("RSS fail:", rss, e)
            continue
        for e in fp.entries[:10]:
            title = clean_html(getattr(e, "title", ""))
            summary = clean_html(getattr(e, "summary", "") or getattr(e, "description", ""))
            link = getattr(e, "link", "")
            if not (title and link):
                continue
            # published
            published = None
            if getattr(e, "published_parsed", None):
                published = datetime.fromtimestamp(time.mktime(e.published_parsed), tz=timezone.utc)
            elif getattr(e, "updated_parsed", None):
                published = datetime.fromtimestamp(time.mktime(e.updated_parsed), tz=timezone.utc)
            items.append((published, title, summary, link))
    if not items:
        return None

    # сортировка по времени (None в конец)
    items.sort(key=lambda x: (x[0] is None, x[0]), reverse=True)
    pub, title, summary, link = items[0]
    host = urlparse(link).hostname or ""
    # локальное время для футера
    event_time = datetime.now().strftime("%d.%m %H:%M") if pub is None else pub.astimezone().strftime("%d.%m %H:%M")
    return {
        "title": title,
        "summary": summary,
        "link": link,
        "host": host,
        "event_time": event_time,
    }

# ================== ОСНОВНОЙ ХОД ==================
def main():
    state = load_state()
    posted = set(state.get("posted_links", []))

    item = fetch_latest_item()
    if not item:
        print("Нет доступных новостей из RSS.")
        return

    if item["link"] in posted:
        print("Свежих непубликованных новостей не нашлось (top уже в posted.json).")
        return

    # формируем текст
    lead, details = split_lead_details(item["summary"] or item["title"])
    tags = extract_tags(item["title"], count=5)

    # картинка
    header_img = draw_header_image(item["title"], item["host"], item["event_time"])
    # подпись
    caption = build_caption(item["title"], lead, details, item["link"], tags)

    # публикация
    tg_send_photo(header_img, caption)
    print("Опубликовано:", item["title"])

    # сохраняем состояние
    state["posted_links"] = ([item["link"]] + list(posted))[:500]
    save_state(state)

if __name__ == "__main__":
    # Никаких тестовых постов тут нет. Скрипт работает только в связке с твоим workflow.
    main()
