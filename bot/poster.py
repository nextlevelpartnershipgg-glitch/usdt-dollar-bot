# -*- coding: utf-8 -*-
"""
Автопостер новостей в Telegram-канал с аккуратными обложками.
Только RU-источники, без дубликатов, с фильтрацией мусора
и минимальным безопасным перефразированием (без изменения фактов).
"""

import os
import json
import random
import textwrap
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
import feedparser
from readability import Document
from bs4 import BeautifulSoup
from dateutil import tz

from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ----------- НАСТРОЙКИ -------------

# 1) Токен и канал (username канала или отрицательный ID)
BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID", "@your_channel_username")

# 2) Таймзона отображения
LOCAL_TZ = tz.gettz(os.getenv("TZ", "Europe/Moscow"))

# 3) Минимальная длина текста для публикации
MIN_BODY_LEN = 400

# 4) Где хранить состояние (дубликаты)
STATE_DIR = "data"
POSTED_FILE = os.path.join(STATE_DIR, "posted.json")

# 5) Источники (RSS/ленты) — только RU
FEEDS = [
    # Агентства
    "https://tass.ru/rss/v2.xml",
    "https://rssexport.rbc.ru/rbcnews/news/20/full.rss",
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://lenta.ru/rss/news",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://www.interfax.ru/rss.asp",
    "https://www.xn--b1aew.xn--p1ai/export/rss2.xml",  # rg.ru
    # Деловые/тех
    "https://www.vedomosti.ru/rss/news",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://habr.com/ru/rss/news/?fl=ru",
    # Регион/общие
    "https://www.fontanka.ru/rss.xml",
    "https://www.kp.ru/rss/allsections.xml",
    "https://iz.ru/xml/rss/all.xml",
    # Запасные:
    "https://www.ng.ru/rss/",
    "https://www.gazeta.ru/export/rss/lastnews.xml",
]

# ----------- УТИЛИТЫ -------------

def ensure_state():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump({"links": []}, f, ensure_ascii=False)


def was_posted(link: str) -> bool:
    ensure_state()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return link in data.get("links", [])


def mark_posted(link: str):
    ensure_state()
    with open(POSTED_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    if link not in data["links"]:
        data["links"].append(link)
    # обрезаем журнал, чтобы не рос бесконечно
    if len(data["links"]) > 5000:
        data["links"] = data["links"][-3000:]
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def domain_of(url: str) -> str:
    try:
        d = urlparse(url).netloc
        return d.replace("www.", "")
    except Exception:
        return "source"


def get(url: str, timeout=12) -> requests.Response:
    headers = {
        "User-Agent": "Mozilla/5.0 (bot; news-poster) Safari/537.36"
    }
    return requests.get(url, headers=headers, timeout=timeout)


# ----------- ИЗВЛЕЧЕНИЕ ТЕКСТА -------------

def extract_article(link: str) -> tuple[str, str, str]:
    """
    Возвращает: title, body, category
    Текст очищается от меню/навигации; безопасно нормализуется.
    """
    html = get(link).text
    doc = Document(html)
    title = doc.short_title() or ""
    article_html = doc.summary(html_partial=True)

    soup = BeautifulSoup(article_html, "lxml")

    # Удаляем возможные таблицы, списки тегов, меню, скрипты
    for bad in soup(["script", "style", "header", "footer", "nav", "form", "aside", "noscript"]):
        bad.decompose()

    # Собираем текст параграфов
    paragraphs = []
    for p in soup.find_all(["p", "li"]):
        t = " ".join(p.get_text(separator=" ", strip=True).split())
        # отбрасываем строки-«облака тегов» и рубрикаторы
        if not t:
            continue
        tokens = t.split()
        # если слишком много однословной «мешанины» — выбросить
        if sum(1 for tok in tokens if tok.istitle()) > 12 and len(tokens) > 30:
            continue
        if "подписывайтесь" in t.lower() or "телеграм" in t.lower() and "канал" in t.lower():
            continue
        # часто медиа вставляют «Читайте также» — убираем
        if t.lower().startswith(("читайте также", "см. также", "по теме")):
            continue
        paragraphs.append(t)

    body = []
    seen = set()
    for t in paragraphs:
        if t in seen:
            continue
        seen.add(t)
        # мягкая «перефраза» без искажения фактов: склейка коротких предложений
        t = t.replace(" ,", ",").replace(" .", ".")
        body.append(t)

    body_text = "\n\n".join(body)
    body_text = normalize_spaces(body_text)

    # Категорию пробуем вытащить по метатегам/заголовкам
    category = guess_category(html, title, body_text)

    return clean_title(title), body_text, category


def normalize_spaces(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = " ".join(s.split())
    # возвращаем абзацы
    s = s.replace(". ", ".§").replace("! ", "!§").replace("? ", "?§")
    s = s.replace("§", " ")
    s = s.replace("\n ", "\n")
    # лёгкая пунктуация
    s = s.replace(" ,", ",").replace(" .", ".")
    return s


def clean_title(t: str) -> str:
    t = t.replace("\xa0", " ").strip()
    t = " ".join(t.split())
    # убираем хвосты сайта в тайтле
    for tail in ("— РБК", "— РИА Новости", "— Коммерсантъ"):
        if t.endswith(tail):
            t = t[: -len(tail)].rstrip()
    return t


def guess_category(html: str, title: str, body: str) -> str:
    meta = BeautifulSoup(html, "lxml")
    for tag in meta.find_all("meta"):
        n = (tag.get("name") or tag.get("property") or "").lower()
        if "section" in n or "category" in n:
            v = tag.get("content")
            if v:
                return simplify_category(v)
    # fallback — по ключевым словам
    low = (title + " " + body).lower()
    if any(w in low for w in ["акция", "рынок", "инфляц", "бюджет", "эконом"]):
        return "Экономика"
    if any(w in low for w in ["технолог", "стартап", "it", "программист", "искусств", "нейросет"]):
        return "Технологии"
    if any(w in low for w in ["суд", "следователь", "силов", "мвд", "мчс", "происшеств"]):
        return "Происшествия"
    if any(w in low for w in ["полит", "парламент", "правительств", "санкц"]):
        return "Политика"
    return "Общество"


def simplify_category(s: str) -> str:
    s = s.strip().capitalize()
    mapping = {
        "economy": "Экономика", "business": "Экономика",
        "tech": "Технологии", "science": "Технологии",
        "politics": "Политика", "world": "Политика",
        "incidents": "Происшествия",
        "society": "Общество", "culture": "Культура", "sport": "Спорт",
        "финансы": "Экономика", "технологии": "Технологии",
        "политика": "Политика", "происшествия": "Происшествия",
        "общество": "Общество", "экономика": "Экономика",
    }
    for k, v in mapping.items():
        if k.lower() in s.lower():
            return v
    # нормализация русских вариантов
    ru = {"Экономика","Технологии","Политика","Происшествия","Общество","Культура","Спорт"}
    if s in ru: return s
    return "Общество"


# ----------- РИСОВАНИЕ ОБЛОЖКИ -------------

def try_font(size: int, bold=False) -> ImageFont.FreeTypeFont:
    # используем системные DejaVu — они есть в GHA runner
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf" if bold else "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size=size)
    return ImageFont.load_default()


def draw_badge(draw: ImageDraw.ImageDraw, xy, text, font, pad=(14, 8), fill=(40, 40, 40), fg=(230, 230, 230)):
    x, y = xy
    w, h = text_size(draw, text, font)
    rw, rh = w + pad[0]*2, h + pad[1]*2
    radius = rh // 2
    rect = [x, y, x+rw, y+rh]
    rounded(draw, rect, radius, fill)
    draw.text((x+pad[0], y+pad[1]), text, font=font, fill=fg)
    return rw, rh


def rounded(draw, rect, r, color):
    (x1, y1, x2, y2) = rect
    draw.rounded_rectangle(rect, radius=r, fill=color)


def text_size(draw, text, font):
    # совместимость разных версий Pillow
    try:
        bbox = draw.textbbox((0,0), text, font=font)
        return (bbox[2]-bbox[0], bbox[3]-bbox[1])
    except Exception:
        return draw.textsize(text, font=font)


def draw_multiline_fit(draw, text, font, box, fill=(255,255,255), line_spacing=6, max_lines=4):
    """
    Впишем текст в прямоугольник: уменьшаем кегль, переносим строки.
    """
    x, y, w, h = box
    size = font.size
    while size >= 20:
        f = try_font(size, bold=True)
        lines = []
        # оценочно подбираем ширину
        words = text.split()
        line = []
        for word in words:
            test = " ".join(line+[word])
            tw, th = text_size(draw, test, f)
            if tw <= w:
                line.append(word)
            else:
                if not line:
                    # слово длиннее строки — жесткий перенос
                    line = [word]
                lines.append(" ".join(line))
                line = [word]
        if line:
            lines.append(" ".join(line))

        # урежем по числу строк
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            # добавим многоточие к последней
            if not lines[-1].endswith("…"):
                lines[-1] = lines[-1].rstrip(".,;: ") + "…"

        # высота блока
        th_total = 0
        for ln in lines:
            th_total += text_size(draw, ln, f)[1] + line_spacing
        th_total -= line_spacing

        if th_total <= h:
            # рисуем
            cy = y + (h - th_total) // 2
            for ln in lines:
                tw, th = text_size(draw, ln, f)
                draw.text((x, cy), ln, font=f, fill=fill)
                cy += th + line_spacing
            return
        size -= 2

    # если не вписался — рисуем мелко
    f = try_font(20, bold=True)
    draw.text((x, y), text[:80] + "…", font=f, fill=fill)


def make_background(size=(1280, 640)) -> Image:
    """Спокойный градиент с мягкими пятнами."""
    w, h = size
    img = Image.new("RGB", size, (18, 20, 24))
    draw = ImageDraw.Draw(img)

    # фирменная палитра
    palettes = [
        ((18,22,27), (34,43,54)),   # графит -> стальной
        ((20,24,30), (52,31,69)),   # графит -> фиолетово-синий
        ((17,24,21), (20,55,43)),   # графит -> изумруд
        ((24,24,24), (62,62,62)),   # тёмный моно
    ]
    c1, c2 = random.choice(palettes)

    # вертикальный мягкий градиент
    for y in range(h):
        t = y / max(1, h-1)
        r = int(c1[0] * (1-t) + c2[0] * t)
        g = int(c1[1] * (1-t) + c2[1] * t)
        b = int(c1[2] * (1-t) + c2[2] * t)
        draw.line([(0, y), (w, y)], fill=(r,g,b))

    # soft-spot акценты
    spots = random.randint(2, 3)
    for _ in range(spots):
        sx = random.randint(int(0.2*w), int(0.8*w))
        sy = random.randint(int(0.2*h), int(0.8*h))
        sr = random.randint(int(0.12*h), int(0.22*h))
        overlay = Image.new("L", (w, h), 0)
        odraw = ImageDraw.Draw(overlay)
        odraw.ellipse((sx-sr, sy-sr, sx+sr, sy+sr), fill=random.randint(60, 110))
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=int(0.07*h)))
        # осветление
        img = Image.composite(Image.new("RGB", size, (240,240,240)), img, overlay.point(lambda p: int(p*0.35)))
    # лёгкий шум
    noise = Image.effect_noise(size, 4).convert("L").point(lambda p: int(p*0.07))
    img = Image.composite(img, Image.new("RGB", size, (0,0,0)), noise)
    return img


def draw_header_image(title: str, src_domain: str, category: str, post_dt: datetime) -> BytesIO:
    W, H = 1280, 640
    img = make_background((W, H))
    draw = ImageDraw.Draw(img)

    # Логотип
    # кружок
    circle_r = 30
    cx, cy = 64, 64
    draw.ellipse((cx-circle_r, cy-circle_r, cx+circle_r, cy+circle_r), fill=(230,230,230))
    # $ по центру
    sym_font = try_font(42, bold=True)
    dollar = "$"
    tw, th = text_size(draw, dollar, sym_font)
    draw.text((cx - tw//2, cy - th//2 + 1), dollar, font=sym_font, fill=(40,40,40))
    # название
    name_font = try_font(42, bold=True)
    draw.text((cx + circle_r + 18, cy - 22), "USDT=Dollar", font=name_font, fill=(240,240,240))

    # Бейджи справа — сначала «пост: дата», ниже — категория
    badge_font = try_font(26, bold=False)
    date_text = post_dt.strftime("пост: %d.%m %H:%M")
    b1w, b1h = draw_badge(draw, (W-10, 18), date_text, badge_font, fill=(72, 78, 84), fg=(240,240,240))
    # корректируем X, чтобы бейдж рисовался справа налево
    draw_badge(draw, (W-10-b1w, 18), date_text, badge_font, fill=(72, 78, 84), fg=(240,240,240))
    cat_text = category if category else "Новости"
    b2w, b2h = draw_badge(draw, (W-10-b1w, 18+b1h+12), cat_text, badge_font, fill=(62, 118, 164), fg=(255,255,255))

    # Подложка для заголовка
    pad = 26
    box = (26, 150, W-26, H-120)
    rounded(draw, (box[0], box[1], box[2], box[3]), 28, (0,0,0,))  # затемнение
    # слегка прозрачнее: поверх — чёрный с альфой
    overlay = Image.new("RGBA", (W, H), (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle((box[0], box[1], box[2], box[3]), radius=28, fill=(0,0,0,160))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")

    draw = ImageDraw.Draw(img)
    title_font = try_font(64, bold=True)
    draw_multiline_fit(
        draw,
        title,
        title_font,
        (box[0]+pad, box[1]+pad, box[2]-box[0]-pad*2, box[3]-box[1]-pad*2),
        fill=(255,255,255),
        max_lines=4
    )

    # Нижние подписи
    small = try_font(26)
    draw.text((32, H-44), f"source: {src_domain}", font=small, fill=(210,210,210))

    out = BytesIO()
    img.save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out


# ----------- ТЕЛЕГРАМ -------------

def tg_send_photo(buf: BytesIO, caption_html: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": CHANNEL_ID,
        "caption": caption_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    files = {"photo": ("cover.jpg", buf, "image/jpeg")}
    r = requests.post(url, data=data, files=files, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Telegram sendPhoto error {r.status_code}: {r.text}")


# ----------- СБОРКА ПОДПИСИ -------------

def build_caption(title: str, body: str, link: str, src_domain: str) -> str:
    # Чёткая структура: Заголовок → текст → источник → канал
    lead = f"<b>{escape_html(title)}</b>"
    details = escape_html(body)
    source = f'Источник: <a href="{link}">{escape_html(src_domain)}</a>'
    channel = f'<a href="https://t.me/usdtdollarm">USDT=Dollar</a>'
    return f"{lead}\n\n{details}\n\n{source}\n\n{channel}"


def escape_html(s: str) -> str:
    return (
        s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;")
    )


# ----------- ПОТОК -------------

def fetch_items():
    items = []
    for feed in FEEDS:
        try:
            parsed = feedparser.parse(feed)
            for e in parsed.entries[:5]:
                link = e.get("link") or ""
                title = (e.get("title") or "").strip()
                if not link or not title:
                    continue
                items.append((link, title))
        except Exception:
            continue
    # случайный порядок, чтобы не зацикливаться на одном источнике
    random.shuffle(items)
    return items


def main():
    ensure_state()
    items = fetch_items()
    for link, t in items:
        if was_posted(link):
            continue

        try:
            title, body, category = extract_article(link)
            # Если не смогли распарсить — пропускаем
            if not title or not body:
                continue

            # Минимум 400 символов — иначе пропускаем
            if len(body) < MIN_BODY_LEN:
                mark_posted(link)  # чтобы не зацикливаться на коротких
                continue

            # Обложка
            now_local = datetime.now(LOCAL_TZ)
            img = draw_header_image(title, domain_of(link), category, now_local)

            # Подпись
            caption = build_caption(title, body, link, domain_of(link))

            tg_send_photo(img, caption)
            mark_posted(link)
            # публикуем только один свежий пост за запуск
            break

        except Exception as e:
            # лог и продолжаем
            print("Error:", e)
            continue


# ------------------------------

if __name__ == "__main__":
    # среда в CI может быть перегружена DNS — легкая задержка
    time.sleep(1)
    main()
