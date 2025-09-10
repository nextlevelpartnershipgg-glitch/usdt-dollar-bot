# -*- coding: utf-8 -*-
"""
USDT=Dollar — авто-постинг новостей.
Сохранены все договорённости: перефраз (без искажения фактов), красивая шапка, антидубли,
жёсткий лимит подписи (всегда показываем источник и ссылку на канал), 1 пост за запуск.
ВОЗВРАЩЁН и РАСШИРЕН список источников (50+), только русскоязычные.

ENV:
  BOT_TOKEN   — токен бота
  CHANNEL_ID  — @usdtdollarm или числовой id канала
  TIMEZONE    — Europe/Moscow (по умолчанию)

Файлы состояния:
  data/posted.json — список уже опубликованных id (link/guid)
"""

import os, io, re, json, random, hashlib, textwrap
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
import feedparser
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ------------------------- НАСТРОЙКИ -------------------------
TZ = os.getenv("TIMEZONE", "Europe/Moscow")
TZINFO = ZoneInfo(TZ)

# ====== ВОЗВРАЩЁН И РАСШИРЕННЫЙ СПИСОК РУССКОЯЗЫЧНЫХ ИСТОЧНИКОВ (50+) ======
# Если какой-то RSS недоступен — парсер просто пропустит его, падения не будет.
FEEDS = [
    # РБК
    "https://www.rbc.ru/rss/politics.ru.xml",
    "https://www.rbc.ru/rss/economics.ru.xml",
    "https://www.rbc.ru/rss/technology_and_media.xml",
    "https://www.rbc.ru/rss/society.xml",
    "https://www.rbc.ru/rss/business.xml",
    # Lenta
    "https://lenta.ru/rss/news",
    # ТАСС
    "https://tass.ru/rss/v2.xml",
    # Ведомости
    "https://www.vedomosti.ru/rss/news",
    # Коммерсантъ
    "https://www.kommersant.ru/RSS/news.xml",
    # РИА/Прайм
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://1prime.ru/export/rss2/index.xml",
    # Интерфакс
    "https://www.interfax.ru/rss.asp",
    # РБК Спорт/Происшествия (доп каналы)
    "https://sportrbc.ru/xml/index.xml",
    # Газета.ру
    "https://www.gazeta.ru/export/rss/first.xml",
    # Известия
    "https://iz.ru/xml/rss/all.xml",
    # Российская газета
    "https://rg.ru/xml/index.xml",
    # Forbes Russia
    "https://www.forbes.ru/newrss.xml",
    # Взгляд
    "https://vz.ru/rss.xml",
    # Ридус
    "https://www.ridus.ru/export/yandex",
    # Банки.ру
    "https://www.banki.ru/xml/news.rss",
    # Комсомольская правда
    "https://www.kp.ru/rss/all.xml",
    # Московский комсомолец
    "https://www.mk.ru/rss/index.xml",
    # BFM
    "https://www.bfm.ru/rss",
    # URA.RU
    "https://ura.news/rss",
    # Фонтанка
    "https://www.fontanka.ru/feed/",
    # РБК Недвижимость/Авто (ещё два)
    "https://realty.rbc.ru/xml/index.xml",
    "https://www.autonews.ru/export/rss.xml",
    # Хабр (рус. техновости)
    "https://habr.com/ru/rss/all/all/",
    # РБК Крипто
    "https://quote.rbc.ru/crypto/news/rss.xml",
    # Профиль
    "https://profile.ru/feed/",
    # Телеканал 360
    "https://360tv.ru/rss/",
    # РБК-Стиль
    "https://style.rbc.ru/rss.xml",
    # РБК Наука
    "https://trends.rbc.ru/trends/science.rss",
    # Коммерсант Деньги
    "https://www.kommersant.ru/RSS/money.xml",
    # Секрет фирмы
    "https://secretmag.ru/export/rss.xml",
    # Ремедиум (фарма, рус.)
    "https://remedium.ru/rss/",
    # Ferra.ru (техно, рус.)
    "https://www.ferra.ru/exports/rss.xml",
    # TJournal (рус.)
    "https://tjournal.ru/rss",
    # Vc.ru (рус.)
    "https://vc.ru/rss/all",
    # CNews (рус.)
    "https://www.cnews.ru/inc/rss/newsline.xml",
    # Техномаркет (пример регионального)
    "https://www.rostov.su/rss",  # может не работать — пропустится
    # Коммерсант Наука
    "https://www.kommersant.ru/RSS/science.xml",
    # РБК Инвестиции
    "https://quote.rbc.ru/news/rss.xml",
    # РБК Недвижимость (дублирование на случай падения основного фида)
    "https://realty.rbc.ru/story/realty.rss",
    # Коммерсант Спорт
    "https://www.kommersant.ru/RSS/sport.xml",
    # РБК Авто (добавочный)
    "https://www.autonews.ru/autonews_rss.xml",
    # Финмаркет
    "https://www.finmarket.ru/rss/main.asp",
    # PRIME Энергетика
    "https://1prime.ru/export/rss2/energy/index.xml",
    # TAdviser
    "https://www.tadviser.ru/index.php?title=Проект:Rss&feed=atom",
    # Хайтек Mail.ru
    "https://hi-tech.mail.ru/rss/all/"
]

MIN_BODY_LEN = 400        # минимальная длина текста для публикации
CAPTION_LIMIT = 1024      # лимит Telegram для подписи к фото
POSTED_PATH = "data/posted.json"

CHANNEL_ID = os.getenv("CHANNEL_ID")  # @usdtdollarm
BOT_TOKEN  = os.getenv("BOT_TOKEN")

CHANNEL_LINK = f"https://t.me/{CHANNEL_ID.lstrip('@')}" if CHANNEL_ID else ""
BRAND_NAME   = "USDT=Dollar"

# Палитры градиентов
PALETTES = [
    ((18, 32, 47), (64, 87, 118)),
    ((23, 28, 38), (86, 66, 105)),
    ((13, 29, 51), (36, 84, 138)),
    ((30, 36, 40), (65, 105, 80)),
    ((28, 26, 34), (95, 76, 102)),
]
ACCENT_COLORS = [
    (220, 182, 75), (90, 200, 200), (105, 176, 255), (255, 170, 120), (190, 230, 140)
]

# Локальные шрифты
FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")
FONT_BOLD = os.path.join(FONT_DIR, "NotoSans-Bold.ttf")
FONT_REG  = os.path.join(FONT_DIR, "NotoSans-Regular.ttf")

# -------------------------------------------------------------

def ensure_dirs():
    os.makedirs(os.path.dirname(POSTED_PATH), exist_ok=True)
    os.makedirs(FONT_DIR, exist_ok=True)

def load_posted():
    if not os.path.exists(POSTED_PATH):
        return set()
    try:
        with open(POSTED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    except Exception:
        return set()

def save_posted(s):
    with open(POSTED_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False, indent=2)

def domain_of(url: str) -> str:
    try:
        return re.sub(r"^https?://(www\.)?([^/]+)/?.*$", r"\2", url, flags=re.I)
    except Exception:
        return "source"

def clean_text(txt: str) -> str:
    if not txt:
        return ""
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&[a-zA-Z#0-9]+;", " ", txt)
    txt = txt.replace("\xa0", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def pick_first_fresh(entries, posted_ids):
    for e in entries:
        link = getattr(e, "link", "") or ""
        guid = getattr(e, "id", "") or link
        uid = hashlib.md5((guid or link).encode("utf-8")).hexdigest()
        if uid in posted_ids:
            continue
        title = clean_text(getattr(e, "title", ""))
        summary = clean_text(getattr(e, "summary", ""))
        content = ""
        if hasattr(e, "content") and e.content:
            content = clean_text(" ".join([c.value for c in e.content if hasattr(c, "value")]))
        body = summary or content
        if not title or not body:
            continue
        if len(body) < MIN_BODY_LEN:
            continue
        return e, uid, title, body, link
    return None, None, None, None, None

def paraphrase(text: str) -> str:
    # Защита чисел/дат
    numbers = re.findall(r"\d+[.,]?\d*", text)
    placeholders = {}
    for i, n in enumerate(numbers):
        key = f"__NUM{i}__"
        placeholders[key] = n
        text = text.replace(n, key, 1)

    repl = {
        "сообщил": "заявил", "сообщила": "заявила", "сообщили": "заявили",
        "рассказал": "уточнил", "рассказала": "уточнила", "рассказали": "уточнили",
        "заявил": "подтвердил", "заявила": "подтвердила", "заявили": "подтвердили",
        "в связи с": "из-за", "в рамках": "в пределах", "однако": "при этом",
        "также": "кроме того", "ранее": "прежде"
    }
    text = re.sub(r"\bПо словам\b", "Согласно словам", text, flags=re.I)

    def repl_word(m):
        w = m.group(0)
        low = w.lower()
        if low in repl:
            new = repl[low]
            return new.capitalize() if w[0].isupper() else new
        return w

    text = re.sub(r"\b[А-Яа-яёЁ\-]+\b", repl_word, text)

    for k, v in placeholders.items():
        text = text.replace(k, v)

    sents = re.split(r"(?<=[.!?])\s+", text)
    out, seen = [], set()
    for s in sents:
        s = s.strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return " ".join(out)

def make_caption(title: str, body: str, source_link: str, channel_link: str) -> str:
    title_html = f"<b>{escape_html(title)}</b>"
    src_domain = domain_of(source_link)
    footer = f"\n\nИсточник: <a href=\"{escape_html(source_link)}\">{escape_html(src_domain)}</a>\n" \
             f"<a href=\"{escape_html(channel_link)}\">{escape_html(BRAND_NAME)}</a>"

    body_html = escape_html(body)
    base = f"{title_html}\n\n{body_html}{footer}"
    if len(base) <= CAPTION_LIMIT:
        return base

    max_body_len = CAPTION_LIMIT - len(title_html) - len(footer) - 2
    trunc = body_html[:max(0, max_body_len)]
    cut = re.sub(r"[^.?!]*$", "", trunc).strip()
    if not cut:
        cut = trunc.rstrip()
    if not cut.endswith(("!", "?", ".")):
        cut = cut.rstrip(" ,;:") + "…"
    return f"{title_html}\n\n{cut}{footer}"[:CAPTION_LIMIT]

def escape_html(s: str) -> str:
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))

# --------------------- КАРТИНКА-ШАПКА -----------------------

def load_font(path: str, size: int, fallback: str) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.truetype(fallback, size) if os.path.exists(fallback) else ImageFont.load_default()

def draw_header(title: str, src_domain: str, dt: datetime) -> bytes:
    W, H = 1024, 512
    img = Image.new("RGB", (W, H), (20, 26, 34))
    draw = ImageDraw.Draw(img)

    c1, c2 = random.choice(PALETTES)
    for y in range(H):
        t = y / (H - 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    for _ in range(3):
        rad = random.randint(120, 220)
        cx  = random.randint(int(0.1*W), int(0.9*W))
        cy  = random.randint(int(0.1*H), int(0.9*H))
        spot = Image.new("RGBA", (rad*2, rad*2), (0,0,0,0))
        sd = ImageDraw.Draw(spot)
        ac = random.choice(ACCENT_COLORS)
        sd.ellipse([0, 0, rad*2, rad*2], fill=(ac[0], ac[1], ac[2], 60))
        spot = spot.filter(ImageFilter.GaussianBlur(25))
        img.paste(spot, (cx - rad, cy - rad), spot)

    overlay = Image.new("RGBA", (W-72, H-180), (0, 0, 0, 130))
    img.paste(overlay, (36, 100), overlay)

    font_bold  = load_font(FONT_BOLD, 44, os.path.join(FONT_DIR, "NotoSans-Bold.ttf"))
    font_small = load_font(FONT_REG, 22, os.path.join(FONT_DIR, "NotoSans-Regular.ttf"))

    logo_r = 28
    logo = Image.new("RGBA", (logo_r*2, logo_r*2), (0,0,0,0))
    ld = ImageDraw.Draw(logo)
    ld.ellipse([0,0,logo_r*2,logo_r*2], fill=(240,240,240,255))
    ld.text((logo_r-10, logo_r-17), "$", font=font_bold, fill=(60,60,60), anchor="mm")
    img.paste(logo, (32, 24), logo)

    draw = ImageDraw.Draw(img)
    draw.text((32 + logo_r*2 + 14, 24+logo_r), BRAND_NAME, font=font_bold, fill=(240,240,240), anchor="lm")
    ts = dt.strftime("пост: %d.%m %H:%M")
    draw.text((W-36, 36), ts, font=font_small, fill=(220,220,220), anchor="ra")

    title_font = load_font(FONT_BOLD, 56, os.path.join(FONT_DIR, "NotoSans-Bold.ttf"))
    max_w = W - 72 - 64
    lines = wrap_text_for_width(title, title_font, max_w)
    y = 140
    for line in lines[:3]:
        draw.text((64, y), line, font=title_font, fill=(250,250,250))
        y += title_font.size + 8

    footer = f"source: {src_domain}   •   событие: {dt.strftime('%d.%m %H:%M')}"
    draw.text((64, H-36), footer, font=font_small, fill=(220,220,220))

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

def wrap_text_for_width(text: str, font: ImageFont.FreeTypeFont, max_width: int):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if font.getlength(test) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines

# --------------------- TELEGRAM -----------------------------

def tg_send_photo(token: str, chat_id: str, image_bytes: bytes, caption_html: str):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("header.png", image_bytes, "image/png")}
    data = {
        "chat_id": chat_id,
        "caption": caption_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, data=data, files=files, timeout=30)
    if not r.ok:
        raise RuntimeError(f"Telegram sendPhoto error: {r.status_code} {r.text}")
    return r.json()

# --------------------- ОСНОВНОЙ ПРОЦЕСС --------------------

def fetch_entries():
    entries = []
    for url in FEEDS:
        try:
            d = feedparser.parse(url)
            if d and d.entries:
                entries.extend(d.entries)
        except Exception:
            continue
    def key(e):
        dt = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        return dt or 0
    entries.sort(key=key, reverse=True)
    return entries

def main():
    ensure_dirs()
    if not BOT_TOKEN or not CHANNEL_ID:
        raise RuntimeError("ENV BOT_TOKEN/CHANNEL_ID не заданы.")

    posted = load_posted()
    entries = fetch_entries()

    e, uid, title_raw, body_raw, link = pick_first_fresh(entries, posted)
    if not e:
        print("Свежих подходящих новостей нет.")
        return

    body = paraphrase(body_raw)
    body = prettify_paragraphs(body)

    if len(body) < MIN_BODY_LEN:
        print(f"Пропуск (мало текста): {link}")
        posted.add(uid); save_posted(posted)
        return

    dt_event = extract_datetime(e) or datetime.now(TZINFO)
    header_png = draw_header(title_raw, domain_of(link), dt_event)
    caption = make_caption(title_raw, body, link, CHANNEL_LINK)

    tg_send_photo(BOT_TOKEN, CHANNEL_ID, header_png, caption)

    posted.add(uid); save_posted(posted)
    print(f"Опубликовано: {title_raw}")

def extract_datetime(e) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(e, field, None)
        if t:
            return datetime(*t[:6], tzinfo=TZINFO)
    return None

def prettify_paragraphs(text: str) -> str:
    text = text.strip()
    text = re.sub(r"([А-Яа-яёЁ]),([А-Яа-яёЁ])", r"\1, \2", text)
    text = re.sub(r"([а-яё])([А-ЯЁ])", r"\1 \2", text)
    sents = re.split(r"(?<=[.!?])\s+", text)
    if len(sents) > 7:
        a = " ".join(sents[:4]); b = " ".join(sents[4:7]); c = " ".join(sents[7:])
        parts = [a, b, c]
    elif len(sents) > 3:
        a = " ".join(sents[:2]); b = " ".join(sents[2:])
        parts = [a, b]
    else:
        parts = [text]
    return "\n\n".join(p.strip() for p in parts if p.strip())

# ------------------------------------------------------------

if __name__ == "__main__":
    main()
