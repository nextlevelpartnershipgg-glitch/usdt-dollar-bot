# -*- coding: utf-8 -*-
"""
USDT=Dollar — автопостинг новостей (RU only) с перефразировкой и бренд-шапкой.

Требует переменные окружения:
  BOT_TOKEN   — токен Telegram бота
  CHANNEL_ID  — @username канала или числовой id (бот — админ канала)

Python deps:
  feedparser, requests, pillow, python-dateutil

Автор: для телеграм-канала USDT=Dollar
"""

import os
import re
import io
import sys
import html
import time
import json
import math
import random
import logging
import textwrap
import datetime as dt

import requests
import feedparser
from dateutil import tz
from urllib.parse import urlparse

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

# --------------- ЛОГИ -----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("poster")

# --------------- НАСТРОЙКИ ------------
BOT_TOKEN   = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID  = os.getenv("CHANNEL_ID", "").strip()  # например: @usdtdollarm
TIMEZONE    = tz.gettz("Europe/Moscow")

# Минимальная длина текста (после перефраза) для публикации
MIN_TEXT_LEN = 400

# Сколько новых постов пытаться сделать за один прогон
MAX_POSTS_PER_RUN = 3

# За какой период брать «свежие» новости (минут)
FRESH_WINDOW_MIN = 120

STATE_DIR = "data"
STATE_FILE = os.path.join(STATE_DIR, "state.json")
os.makedirs(STATE_DIR, exist_ok=True)

# --------------- ИСТОЧНИКИ (RU ONLY) -------------
# Только крупные/умеренные русскоязычные медиа (новости). RSS.
RSS_FEEDS = [
    # Общие
    "https://lenta.ru/rss/news",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://tass.ru/rss/v2.xml",
    "https://www.rbc.ru/rss/news.rss",
    "https://rg.ru/xml/index.xml",
    "https://1prime.ru/export/rss2/index.xml",
    "https://www.interfax.ru/rss.asp",
    "https://iz.ru/xml/rss/all.xml",
    "https://www.vedomosti.ru/rss/rubric/finance",
    "https://www.gazeta.ru/export/rss/lenta.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://www.fontanka.ru/frontpage.rss",
    "https://www.kommersant.ru/RSS/economics.xml",
    "https://www.kommersant.ru/RSS/politics.xml",
    "https://www.forbes.ru/newapi/get-rss",
    "https://www.bfm.ru/rss/main.xml",
    "https://www.dp.ru/rss",
    "https://argumenti.ru/rss.xml",
    "https://www.kp.ru/rss/online.xml",
    "https://www.mk.ru/rss/index.xml",
    "https://www.rbc.ru/economics/?rss",
    "https://russian.rt.com/rss",
    "https://www.ng.ru/rss/",
    "https://www.vedomosti.ru/rss/rubric/politics",
    "https://www.vedomosti.ru/rss/rubric/business",
    "https://www.vedomosti.ru/rss/rubric/society",
    "https://www.kommersant.ru/RSS/money.xml",
    "https://www.kommersant.ru/RSS/finance.xml",
    "https://www.kommersant.ru/RSS/technology.xml",
    "https://www.kommersant.ru/RSS/incidents.xml",
    "https://tass.ru/rss/economy",
    "https://tass.ru/rss/politika",
    "https://tass.ru/rss/obschestvo",
    "https://www.rbc.ru/politics/?rss",
    "https://www.rbc.ru/society/?rss",
    "https://www.rbc.ru/technology_and_media/?rss",
    "https://www.gazeta.ru/politics/news/rss.shtml",
    "https://www.gazeta.ru/business/news/rss.shtml",
    "https://1prime.ru/Finance/export/rss2/index.xml",
    "https://1prime.ru/Politics/export/rss2/index.xml",
    "https://1prime.ru/Business/export/rss2/index.xml",
    "https://iz.ru/rss",
    "https://www.interfax.ru/rss.asp?region=moscow",
    "https://www.interfax.ru/rss.asp?section=ekonomika",
    "https://www.interfax.ru/rss.asp?section=politics",
    "https://rg.ru/tema/ekonomika.xml",
    "https://rg.ru/tema/politika.xml",
    "https://rg.ru/tema/obschestvo.xml",
]

# --------------- УТИЛИТЫ СОСТОЯНИЯ ----------------
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"posted_links": [], "last_run": None}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"posted_links": [], "last_run": None}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

STATE = load_state()

def memorized(link: str) -> bool:
    return link in STATE.get("posted_links", [])

def remember(link: str):
    arr = STATE.get("posted_links", [])
    arr.insert(0, link)
    # ограничим историю 1000 ссылок
    STATE["posted_links"] = arr[:1000]
    save_state(STATE)

# --------------- ЧИСТКА/НОРМАЛИЗАЦИЯ ТЕКСТА ----------------
RE_SPACES = re.compile(r"[ \t\u00A0]+")
RE_MULTI_NL = re.compile(r"\n{3,}")
RE_HTMLTAG = re.compile(r"<[^>]+>")
RE_WS_AROUND_PUNCT = re.compile(r"\s+([,.:;!?])")
RE_FIX_QUOTES = re.compile(r"[«»“”]+")

def clean_html_text(s: str) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = s.replace("&nbsp;", " ").replace("&mdash;", "—").replace("&ndash;", "–").replace("&laquo;","«").replace("&raquo;","»")
    s = RE_HTMLTAG.sub("", s)
    s = RE_SPACES.sub(" ", s)
    s = RE_WS_AROUND_PUNCT.sub(r"\1", s)
    s = s.replace(" ,", ",").replace(" .", ".")
    s = RE_FIX_QUOTES.sub('"', s)
    s = s.strip()
    s = RE_MULTI_NL.sub("\n\n", s)
    return s

# --------------- ДЕТЕКТ КАТЕГОРИИ --------------------------
CAT_RULES = [
    ("экономика", re.compile(r"\b(эконом|инфляц|рынк|акци|облигац|бирж|нефть|газ|рубл|доллар|бюджет|ввп|налог)\w*", re.I)),
    ("технологии", re.compile(r"\b(технол|it|айти|компьютер|смартфон|искусственн|ИИ|робот|космос|ракет|спутник)\w*", re.I)),
    ("политика", re.compile(r"\b(президент|премьер|парламент|мин|власть|санкц|выбор|партия|кабинет|совбез)\w*", re.I)),
    ("происшествия", re.compile(r"\b(пожар|дтп|авар|взрыв|шторм|ураган|затоплен|следств|задержан|суд|штраф)\w*", re.I)),
]

def detect_category(text: str) -> str:
    for name, rx in CAT_RULES:
        if rx.search(text or ""):
            return name
    return "экономика"

# --------------- КОНСЕРВАТИВНЫЙ РЕРАЙТ ---------------------
# Не меняем числа, проценты, даты, валюты, имена собственные (приблизительно).
SYNONYMS = {
    "сообщил": ["заявил", "уточнил", "подтвердил"],
    "сообщила": ["заявила", "уточнила", "подтвердила"],
    "сообщили": ["заявили", "уточнили", "подтвердили"],
    "рассказал": ["отметил", "подчеркнул"],
    "рассказала": ["отметила", "подчеркнула"],
    "заявил": ["сообщил", "подчеркнул"],
    "заявила": ["сообщила", "подчеркнула"],
    "заявили": ["сообщили", "подчеркнули"],
    "отметил": ["добавил", "подчеркнул"],
    "отметили": ["добавили", "подчеркнули"],
    "в связи с": ["из-за", "на фоне"],
    "согласно": ["по данным", "как следует из"],
    "также": ["кроме того", "при этом"],
    "при этом": ["вдобавок", "кроме того"],
    "раннее": ["ранее"],
    "ранее": ["прежде", "до этого"],
    "изложено": ["указано", "отмечено"],
    "в частности": ["в том числе", "например"],
    "сегодня": ["сегодня", "в понедельник", "сегодня днем"],  # дата всё равно есть рядом
    "завтра": ["на следующий день", "в ближайшие сутки"],
}

SAFE_TOKEN = re.compile(r"(^[\d\W]+$)|(\d)|(%|₽|\$|€)|([A-ZА-Я][A-Za-zА-Яа-я\-]{2,})")

def _swap_words(sent: str) -> str:
    words = sent.split()
    for i, w in enumerate(words):
        base = w.strip(",.?!:;()«»\"'").lower()
        if base in SYNONYMS and not SAFE_TOKEN.search(w):
            repl = random.choice(SYNONYMS[base])
            words[i] = w.replace(base, repl, 1)
    # лёгкий разворот вводных конструкций
    s = " ".join(words)
    s = s.replace("В связи с", random.choice(["На фоне", "Из-за"]))
    s = s.replace("в связи с", random.choice(["на фоне", "из-за"]))
    return s

def paraphrase_ru(text: str) -> str:
    """
    Осторожный рерайт: замены синонимов + лёгкая перестановка вводных.
    Числа/даты/имена не трогаем.
    """
    if not text:
        return ""
    text = clean_html_text(text)
    # Разбиваем на предложения
    sents = re.split(r"(?<=[.!?])\s+", text)
    out = []
    for s in sents:
        s = s.strip()
        if not s:
            continue
        # коротышей не трогаем
        if len(s) < 60:
            out.append(s)
            continue
        out.append(_swap_words(s))
    # Склейка + простая де-дубликация соседних предложений
    res = []
    prev = ""
    for s in out:
        if s != prev:
            res.append(s)
        prev = s
    text = " ".join(res)
    text = RE_MULTI_NL.sub("\n\n", text).strip()
    return text

# --------------- ГЕНЕРАЦИЯ ШАПКИ ---------------------------
def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont):
    # совместимость PIL 8/10
    try:
        return draw.textbbox((0, 0), text, font=font)
    except Exception:
        w, h = draw.textsize(text, font=font)
        return (0, 0, w, h)

def draw_header(title: str, source_domain: str, dtime: dt.datetime, category: str) -> str:
    """
    Крупная шапка с бренд-градиентом, шумом, спотлайтом, лого и меткой категории.
    Возвращает путь к JPEG.
    """
    W, H = 1080, 540
    img = Image.new("RGB", (W, H), (10, 10, 10))
    draw = ImageDraw.Draw(img)

    # Палитры бренда (строгие, без «кислоты»)
    palettes = [
        ((18, 32, 64), (60, 46, 110)),     # тёмно-синий → фиолет
        ((24, 36, 40), (32, 84, 66)),      # графит → изумруд
        ((34, 34, 34), (70, 70, 70)),       # графитовый
        ((28, 24, 48), (92, 72, 144)),      # глубокий фиолет
    ]
    c1, c2 = random.choice(palettes)

    # Градиент по вертикали
    for y in range(H):
        r = int(c1[0] + (c2[0]-c1[0]) * y / H)
        g = int(c1[1] + (c2[1]-c1[1]) * y / H)
        b = int(c1[2] + (c2[2]-c1[2]) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Лёгкий шум/текстура
    noise = Image.effect_noise((W, H), 18).convert("L")
    noise = noise.point(lambda p: int(p * 0.08))  # приглушить
    img = ImageCh = Image.merge("RGB", (noise, noise, noise))
    img = Image.blend(img, ImageCh, 0.0)  # только для совместимости строк, не меняем фон
    img = Image.blend(Image.merge("RGB", (noise, noise, noise)), img, 0.1)

    # Spotlight (глубина)
    spot = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(spot)
    sx, sy = random.randint(200, 880), random.randint(120, 420)
    sd.ellipse((sx-260, sy-260, sx+260, sy+260), fill=160)
    spot = spot.filter(ImageFilter.GaussianBlur(120))
    img = Image.composite(Image.new("RGB", (W, H), (255, 255, 255)), img, spot)

    draw = ImageDraw.Draw(img)

    # Метка категории (цвет)
    cat_colors = {
        "экономика": (70, 130, 180),
        "технологии": (60, 179, 113),
        "политика": (178, 34, 34),
        "происшествия": (218, 165, 32),
    }
    cat_color = cat_colors.get(category.lower(), (105, 105, 105))
    draw.rectangle([W-220, 0, W, 56], fill=cat_color)

    # Шрифты (DejaVu есть в образах GH Actions)
    font_logo  = ImageFont.truetype("DejaVuSans-Bold.ttf", 40)
    font_small = ImageFont.truetype("DejaVuSans.ttf", 24)
    font_title = ImageFont.truetype("DejaVuSans-Bold.ttf", 58)

    # Верхний левый логотип-надпись
    # (маленький «жетон» слева)
    draw.ellipse((26, 22, 82, 78), fill=(235, 235, 235))
    draw.text((46, 35), "$", font=ImageFont.truetype("DejaVuSans-Bold.ttf", 34), fill=(40, 40, 40))
    draw.text((100, 32), "USDT=Dollar", font=font_logo, fill=(245, 245, 245))

    # Метка категории текстом
    draw.text((W-210, 12), category.capitalize(), font=ImageFont.truetype("DejaVuSans-Bold.ttf", 26), fill="white")

    # Нижняя служебная полоса
    draw.text((40, H-40), f"source: {source_domain}", font=font_small, fill=(230, 230, 230))
    draw.text((W-250, H-40), f"событие: {dtime.strftime('%d.%m %H:%M')}", font=font_small, fill=(230, 230, 230))

    # Полупрозрачная подложка под заголовок
    pad = 38
    box = [pad, 110, W - pad, H - 100]
    draw.rounded_rectangle(box, radius=24, fill=(0, 0, 0, 140))

    # Разбивка заголовка на 2–3 строки
    max_width = (W - 2*pad) - 60
    lines = []
    current = ""
    for word in title.split():
        trial = (current + " " + word).strip()
        l, t, r, b = _text_bbox(draw, trial, font_title)
        if (r - l) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    lines = lines[:3]

    # Рисуем заголовок
    y = box[1] + 28
    for i, line in enumerate(lines):
        draw.text((box[0] + 30, y), line, font=font_title, fill="#FFFFFF")
        y += 70

    out = "/tmp/header.jpg"
    img.save(out, "JPEG", quality=92, subsampling=0)
    return out

# --------------- ТЕЛЕГРАМ: ОТПРАВКА -------------------------
TG_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def tg_send_photo(chat_id: str, photo_path: str, caption_html: str) -> bool:
    with open(photo_path, "rb") as f:
        files = {"photo": f}
        data = {
            "chat_id": chat_id,
            "caption": caption_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        r = requests.post(f"{TG_API}/sendPhoto", data=data, files=files, timeout=30)
    if not r.ok:
        log.error("Telegram sendPhoto: %s %s", r.status_code, r.text)
        return False
    return True

# --------------- ФОРМИРОВАНИЕ КАПШЕНА -----------------------
def build_caption(title: str, body: str, source_link: str, channel_link: str) -> str:
    """
    Структура:
    <b>Заголовок</b>

    Подробности: (перефраз)
    ...
    Источник: <a href="...">rbc.ru</a>
    🪙 <a href="https://t.me/usdtdollarm">USDT=Dollar</a>
    """
    domain = urlparse(source_link).netloc.replace("www.", "")
    lead = f"<b>{html.escape(title)}</b>"
    # компактный, но читабельный блок "Подробности"
    details = clean_html_text(body)
    details = textwrap.fill(details, width=100)
    details = html.escape(details)
    details = details.replace("\n", "\n")

    src = f'Источник: <a href="{html.escape(source_link)}">{html.escape(domain)}</a>'
    ch  = f'🪙 <a href="https://t.me/{channel_link.lstrip("@")}">USDT=Dollar</a>'

    caption = f"{lead}\n\n{details}\n\n{src}\n{ch}"
    # Telegram ограничивает ~1024 символов в подписи к фото
    return caption[:1023]

# --------------- ВЫБОР НОВОСТЕЙ -----------------------------
def pick_items():
    """Собираем свежие новости из RSS, снимаем дубли по ссылке и времени."""
    items = []
    now = dt.datetime.now(tz=TIMEZONE)
    fresh_after = now - dt.timedelta(minutes=FRESH_WINDOW_MIN)

    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            log.warning("rss error %s: %s", url, e)
            continue

        for e in feed.entries[:10]:
            link = getattr(e, "link", "") or ""
            if not link or memorized(link):
                continue

            published = None
            if hasattr(e, "published_parsed") and e.published_parsed:
                published = dt.datetime.fromtimestamp(time.mktime(e.published_parsed), tz=tz.UTC).astimezone(TIMEZONE)
            elif hasattr(e, "updated_parsed") and e.updated_parsed:
                published = dt.datetime.fromtimestamp(time.mktime(e.updated_parsed), tz=tz.UTC).astimezone(TIMEZONE)
            else:
                published = now

            if published < fresh_after:
                continue

            title = clean_html_text(getattr(e, "title", ""))
            summary = clean_html_text(getattr(e, "summary", ""))

            # иногда summary пуст, попробуем content
            if not summary and hasattr(e, "content") and e.content:
                summary = clean_html_text(e.content[0].value or "")

            # отбрасываем совсем короткие заготовки
            if len(title) < 15 or len(summary) < 80:
                continue

            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "published": published,
                "source": urlparse(link).netloc.replace("www.", "") or urlparse(url).netloc.replace("www.", ""),
            })
    # первичная сортировка по времени
    items.sort(key=lambda x: x["published"], reverse=True)
    return items

# --------------- ОСНОВНОЙ ПРОГОН ----------------------------
def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        log.error("ENV BOT_TOKEN/CHANNEL_ID not set")
        sys.exit(1)

    items = pick_items()
    if not items:
        log.info("Нет свежих новостей.")
        return

    posted = 0
    for it in items:
        if posted >= MAX_POSTS_PER_RUN:
            break

        title = it["title"]
        body  = it["summary"]
        link  = it["link"]
        pub   = it["published"]

        # Перефраз без изменения фактов
        para = paraphrase_ru(body)

        # Итоговая длина для фильтра
        pure_len = len(clean_html_text(para))
        if pure_len < MIN_TEXT_LEN:
            log.info("Мало текста (%s) — пропуск: %s", pure_len, title)
            remember(link)  # чтобы не пытаться снова
            continue

        # Категория
        category = detect_category(title + " " + para)

        # Рисуем шапку
        header_path = draw_header(title, it["source"], pub, category)

        # Подпись
        caption = build_caption(title, para, link, CHANNEL_ID)

        # Публикуем В КАНАЛ (бот как админ → пост от имени канала)
        ok = tg_send_photo(CHANNEL_ID, header_path, caption)
        if not ok:
            # не запоминаем — попробуем позже
            log.error("Не удалось отправить: %s", link)
            continue

        remember(link)
        posted += 1
        log.info("Опубликовано: %s", title)
        time.sleep(2)  # лёгкая пауза

    log.info("Готово. Новых постов: %s", posted)


# --------------- Точка входа -------------------------------
if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception("fatal: %s", e)
        sys.exit(1)
