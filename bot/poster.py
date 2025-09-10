# -*- coding: utf-8 -*-
"""
USDT=Dollar — авто-постинг новостей (фикс длины подписи, дедупликация, 1 пост за запуск).
Требует ENV:
  BOT_TOKEN   — токен бота
  CHANNEL_ID  — @usdtdollarm  (или числовой id с минусом для канала)
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

# Только русскоязычные источники (можно дополнять)
FEEDS = [
    "https://www.rbc.ru/rss/politics.ru.xml",
    "https://www.rbc.ru/rss/economics.ru.xml",
    "https://www.rbc.ru/rss/technology_and_media.xml",
    "https://lenta.ru/rss/news",
    "https://tass.ru/rss/v2.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://1prime.ru/export/rss2/index.xml",
    "https://www.fontanka.ru/feed/",
]

MIN_BODY_LEN = 400        # минимальная длина текста для публикации
CAPTION_LIMIT = 1024      # лимит Telegram для подписи к фото
POSTED_PATH = "data/posted.json"

CHANNEL_ID = os.getenv("CHANNEL_ID")  # например: @usdtdollarm
BOT_TOKEN  = os.getenv("BOT_TOKEN")

CHANNEL_LINK = f"https://t.me/{CHANNEL_ID.lstrip('@')}" if CHANNEL_ID else ""
BRAND_NAME   = "USDT=Dollar"

# Палитры градиентов (брендово-спокойные)
PALETTES = [
    ((18, 32, 47), (64, 87, 118)),     # тёмно-синий -> графит
    ((23, 28, 38), (86, 66, 105)),     # угольный -> фиолетово-серый
    ((13, 29, 51), (36, 84, 138)),     # сине-стальной
    ((30, 36, 40), (65, 105, 80)),     # графит -> изумрудный
    ((28, 26, 34), (95, 76, 102)),     # фиолетовый мягкий
]

ACCENT_COLORS = [
    (220, 182, 75),   # золотистый
    (90, 200, 200),   # циан
    (105, 176, 255),  # голубой
    (255, 170, 120),  # персиковый
    (190, 230, 140),  # лайм
]

# Локальные шрифты (без системных)
# В репозитории положи папку bot/fonts/ с NotoSans*.ttf (или поменяй пути ниже)
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
    """Убираем html-шум, приводим пробелы, убираем дубли."""
    if not txt:
        return ""
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"&[a-z]+;", " ", txt)
    txt = txt.replace("\xa0", " ")
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt

def pick_first_fresh(entries, posted_ids):
    """Берём единственную свежую новость (первая подходящая)."""
    for e in entries:
        link = getattr(e, "link", "") or ""
        guid = getattr(e, "id", "") or link
        uid = hashlib.md5((guid or link).encode("utf-8")).hexdigest()
        if uid in posted_ids:
            continue
        title = clean_text(getattr(e, "title", ""))
        summary = clean_text(getattr(e, "summary", ""))
        # Если нет summary, пробуем содержимое
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
    """
    Осторожный рерайт без «придумываний»: сохраняем факты, меняем формулировки.
    Не трогаем цифры/даты/имена (кроме регистровых исправлений).
    """
    # Защита чисел/дат — обрамим плейсхолдерами (чтобы не трогать при заменах)
    numbers = re.findall(r"\d+[.,]?\d*", text)
    placeholders = {}
    for i, n in enumerate(numbers):
        key = f"__NUM{i}__"
        placeholders[key] = n
        text = text.replace(n, key, 1)

    # Мягкие синонимы (рус.)
    repl = {
        "сообщил": "заявил",
        "сообщила": "заявила",
        "сообщили": "заявили",
        "рассказал": "уточнил",
        "рассказала": "уточнила",
        "рассказали": "уточнили",
        "заявил": "подтвердил",
        "заявила": "подтвердила",
        "заявили": "подтвердили",
        "в связи с": "из-за",
        "в рамках": "в пределах",
        "в ближайшее время": "в скором времени",
        "ранее": "прежде",
        "также": "кроме того",
        "однако": "при этом",
    }
    # По словам ... → согласно словам ...
    text = re.sub(r"\bПо словам\b", "Согласно словам", text, flags=re.I)

    # Заменяем по словарю на границах слов
    def repl_word(m):
        w = m.group(0)
        low = w.lower()
        if low in repl:
            new = repl[low]
            return new.capitalize() if w[0].isupper() else new
        return w

    text = re.sub(r"\b[А-Яа-яёЁ\-]+\b", repl_word, text)

    # Возвращаем числа/даты
    for k, v in placeholders.items():
        text = text.replace(k, v)

    # Укрепляем читаемость: разбиваем на предложения и убираем дубли
    sents = re.split(r"(?<=[.!?])\s+", text)
    out = []
    seen = set()
    for s in sents:
        s = s.strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    text = " ".join(out)
    return text

def make_caption(title: str, body: str, source_link: str, channel_link: str) -> str:
    """Формируем подпись (<= 1024), обрезаем тело, но сохраняем «Источник» и ссылку на канал."""
    title_html = f"<b>{escape_html(title)}</b>"
    src_domain = domain_of(source_link)
    footer = f"\n\nИсточник: <a href=\"{escape_html(source_link)}\">{escape_html(src_domain)}</a>\n" \
             f"<a href=\"{escape_html(channel_link)}\">{escape_html(BRAND_NAME)}</a>"

    # Основной текст — абзацем
    body_html = escape_html(body)

    base = f"{title_html}\n\n{body_html}{footer}"
    if len(base) <= CAPTION_LIMIT:
        return base

    # Подпись длинная — обрезаем body так, чтобы уместился footer.
    max_body_len = CAPTION_LIMIT - len(title_html) - len(footer) - 2  # запас на \n\n
    trunc = body_html[:max(0, max_body_len)]
    # режем до ближайшей точки/границы слова
    cut = re.sub(r"[^.?!]*$", "", trunc).strip()
    if not cut:
        cut = trunc.rstrip()
    if not cut.endswith(("!", "?", ".")):
        cut = cut.rstrip(" ,;:") + "…"
    caption = f"{title_html}\n\n{cut}{footer}"
    return caption[:CAPTION_LIMIT]

def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

# --------------------- КАРТИНКА-ШАПКА -----------------------

def load_font(path: str, size: int, fallback: str) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        # запасной встроенный
        return ImageFont.truetype(fallback, size) if os.path.exists(fallback) else ImageFont.load_default()

def draw_header(title: str, src_domain: str, dt: datetime) -> bytes:
    W, H = 1024, 512
    img = Image.new("RGB", (W, H), (20, 26, 34))
    draw = ImageDraw.Draw(img)

    # Градиент
    c1, c2 = random.choice(PALETTES)
    for y in range(H):
        t = y / (H - 1)
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # мягкие световые пятна для глубины
    for _ in range(3):
        rad = random.randint(120, 220)
        cx  = random.randint(int(0.1*W), int(0.9*W))
        cy  = random.randint(int(0.1*H), int(0.9*H))
        spot = Image.new("RGBA", (rad*2, rad*2), (0, 0, 0, 0))
        sd = ImageDraw.Draw(spot)
        ac = random.choice(ACCENT_COLORS)
        sd.ellipse([0, 0, rad*2, rad*2], fill=(ac[0], ac[1], ac[2], 60))
        spot = spot.filter(ImageFilter.GaussianBlur(25))
        img.paste(spot, (cx - rad, cy - rad), spot)

    # полупрозрачная «плашка» под текст (для читаемости)
    overlay = Image.new("RGBA", (W-72, H-180), (0, 0, 0, 130))
    img.paste(overlay, (36, 100), overlay)

    # Лого + бренд сверху слева
    font_bold  = load_font(FONT_BOLD, 44, os.path.join(FONT_DIR, "NotoSans-Bold.ttf"))
    font_small = load_font(FONT_REG, 22, os.path.join(FONT_DIR, "NotoSans-Regular.ttf"))

    # кружок-логотип $ (простая форма)
    logo_r = 28
    logo = Image.new("RGBA", (logo_r*2, logo_r*2), (0,0,0,0))
    ld = ImageDraw.Draw(logo)
    ld.ellipse([0,0,logo_r*2,logo_r*2], fill=(240,240,240,255))
    ld.text((logo_r-10, logo_r-17), "$", font=font_bold, fill=(60,60,60), anchor="mm")
    img.paste(logo, (32, 24), logo)
    draw = ImageDraw.Draw(img)
    draw.text((32 + logo_r*2 + 14, 24+logo_r), BRAND_NAME, font=font_bold, fill=(240,240,240), anchor="lm")

    # время поста справа
    ts = dt.strftime("пост: %d.%m %H:%M")
    draw.text((W-36, 36), ts, font=font_small, fill=(220,220,220), anchor="ra")

    # Заголовок — крупно, автоматический перенос
    title_font = load_font(FONT_BOLD, 56, os.path.join(FONT_DIR, "NotoSans-Bold.ttf"))
    max_w = W - 72 - 64
    lines = wrap_text_for_width(title, title_font, max_w)
    y = 140
    for line in lines[:3]:  # не более 3 строк
        draw.text((64, y), line, font=title_font, fill=(250,250,250))
        y += title_font.size + 8

    # подвал: источник и время события
    src_font = font_small
    footer = f"source: {src_domain}   •   событие: {dt.strftime('%d.%m %H:%M')}"
    draw.text((64, H-36), footer, font=src_font, fill=(220,220,220))

    # в байты
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

def wrap_text_for_width(text: str, font: ImageFont.FreeTypeFont, max_width: int):
    words = text.split()
    lines = []
    cur = ""
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
    # отсортируем по времени (если есть)
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

    # Выбираем ОДНУ свежую подходящую новость
    e, uid, title_raw, body_raw, link = pick_first_fresh(entries, posted)
    if not e:
        print("Свежих подходящих новостей нет.")
        return

    # Перефразируем сбережно
    body = paraphrase(body_raw)
    # ещё чутка приводим формат (абзацы)
    body = prettify_paragraphs(body)

    if len(body) < MIN_BODY_LEN:
        print(f"Пропуск (мало текста): {link}")
        posted.add(uid)
        save_posted(posted)
        return

    # время события
    dt_event = extract_datetime(e) or datetime.now(TZINFO)

    # Картинка-шапка
    header_png = draw_header(title_raw, domain_of(link), dt_event)

    # Подпись (<= 1024), с обязательным «Источник» и ссылкой на канал
    caption = make_caption(title_raw, body, link, CHANNEL_LINK)

    # Отправка (от имени канала)
    tg_send_photo(BOT_TOKEN, CHANNEL_ID, header_png, caption)

    # Сохраняем ID — чтобы не дублировать
    posted.add(uid)
    save_posted(posted)
    print(f"Опубликовано: {title_raw}")

def extract_datetime(e) -> datetime | None:
    for field in ("published_parsed", "updated_parsed"):
        t = getattr(e, field, None)
        if t:
            # struct_time -> datetime с TZ
            return datetime(*t[:6], tzinfo=TZINFO)
    return None

def prettify_paragraphs(text: str) -> str:
    # Крупный первый абзац + 1–2 дополнительных, удаляем повторы
    text = text.strip()
    # Исправить склеенные слова (если у источника были «вырезанные» пробелы)
    text = re.sub(r"([А-Яа-яёЁ]),([А-Яа-яёЁ])", r"\1, \2", text)
    text = re.sub(r"([а-яё])([А-ЯЁ])", r"\1 \2", text)

    # Разбиваем на 2–3 абзаца по смысловым точкам
    sents = re.split(r"(?<=[.!?])\s+", text)
    if len(sents) > 7:
        a = " ".join(sents[:4])
        b = " ".join(sents[4:7])
        c = " ".join(sents[7:])
        parts = [a, b, c]
    elif len(sents) > 3:
        a = " ".join(sents[:2])
        b = " ".join(sents[2:])
        parts = [a, b]
    else:
        parts = [text]

    # Финальное склеивание с пустыми строками между абзацами
    return "\n\n".join(p.strip() for p in parts if p.strip())

# ------------------------------------------------------------

if __name__ == "__main__":
    main()
