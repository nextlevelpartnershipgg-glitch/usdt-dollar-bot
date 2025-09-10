# -*- coding: utf-8 -*-
"""
USDT=Dollar — авто-постинг новостей (RU-источники) с перефразом и шапкой.

Требуется:
- requests
- feedparser
- beautifulsoup4
- Pillow

Файл состояния: data/state.json
"""

import os
import io
import re
import json
import time
import random
import logging
import datetime as dt
import textwrap
from urllib.parse import urlparse

import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops

# ----------------------- НАСТРОЙКИ -----------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "")  # !!! ИСПОЛЬЗУЙТЕ Secrets в CI
CHANNEL_ID = os.getenv("CHANNEL_ID", "@usdtdollarm")  # постим в канал по username

STATE_PATH = os.getenv("STATE_PATH", "data/state.json")
MIN_BODY_CHARS = 400                 # минимум символов в тексте новости
CAPTION_LIMIT = 1024                 # лимит на подпись к фото в Telegram

# 50 русскоязычных RSS-источников
RSS_FEEDS = [
    "https://www.rbc.ru/rss/?rss=news",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://tass.ru/rss/v2.xml",
    "https://lenta.ru/rss",
    "https://iz.ru/xml/rss/all.xml",
    "https://rg.ru/xml/index.xml",
    "https://1prime.ru/export/rss2/index.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.interfax.ru/rss.asp",
    "https://www.fontanka.ru/rss.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://vz.ru/rss.xml",
    "https://www.ng.ru/rss/",
    "https://www.gazeta.ru/export/rss/first.xml",
    "https://www.mk.ru/rss/index.xml",
    "https://www.kp.ru/rss/allsections.xml",
    "https://news.mail.ru/rss/90/",
    "https://profile.ru/feed/",
    "https://www.rosbalt.ru/rss/",
    "https://expert.ru/export/all_news.xml",
    "https://www.bfm.ru/rss/news.xml",
    "https://www.forbes.ru/newrss.xml",
    "https://banki.ru/xml/news.rss",
    "https://russian.rt.com/rss",
    "https://www.stopcoronavirus.rf/news/rss",  # может быть пустым — пропустим
    "https://www.akinvest.ru/rss.xml",
    "https://www.ixbt.com/export/news.rss",
    "https://3dnews.ru/news/rss",
    "https://www.ferra.ru/exports/rss/all.xml",
    "https://www.cnews.ru/inc/rss/news.xml",
    "https://www.tvc.ru/feed/rss",
    "https://www.spb.kp.ru/rss/online.xml",
    "https://www.5-tv.ru/export/rss/",
    "https://77.ru/export/rss.xml",
    "https://ufa1.ru/text/rss/",
    "https://66.ru/news/rss/",
    "https://www.fontanka.ru/fontanka.rss",
    "https://ria.ru/services/rss/",
    "https://overclockers.ru/rss/all.rss",
    "https://sportrbc.ru/rss/newsline",
    "https://rsport.ria.ru/export/rss2/index.xml",
    "https://www.championat.com/xml/rss_news.xml",
    "https://www.autonews.ru/autonews.rss",
    "https://motor.ru/rss/all/",
    "https://news.drom.ru/rss/all.xml",
    "https://rg.ru/auto/rss.xml",
    "https://ria.ru/politics/rss/",
    "https://rbc.ru/economics/?rss=economics",
    "https://lenta.ru/rubrics/economics/rss",
    "https://tass.ru/ekonomika/rss"
]

# Брендовые мягкие палитры для фона
_BRAND_PALETTES = [
    ((18, 32, 47), (34, 39, 46)),
    ((28, 32, 66), (58, 36, 73)),
    ((9, 43, 54), (33, 37, 41)),
    ((24, 24, 26), (44, 36, 39)),
    ((12, 38, 32), (35, 40, 38)),
]
# ---------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


# ----------------------- УТИЛИТЫ СТЕЙТА -----------------------

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"posted": []}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def mark_posted(state, key):
    if key not in state["posted"]:
        state["posted"].append(key)
        # ограничим список, чтобы файл не разрастался бесконечно
        if len(state["posted"]) > 5000:
            state["posted"] = state["posted"][-3000:]
        save_state(state)


# ----------------------- ТЕКСТ/HTML -----------------------

_HTML_ESCAPE = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;"
}
def esc(s: str) -> str:
    return "".join(_HTML_ESCAPE.get(c, c) for c in s)

def strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    # вырезаем скрипты/стили
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # нормализуем пробелы
    text = re.sub(r"\s+", " ", text)
    return text

def conservative_paraphrase(text: str) -> str:
    """
    Консервативный рерайт без «выдумывания»: замены синонимов + перетасовка вводных.
    Сохраняем цифры, кавычки, имена как есть.
    """
    if not text:
        return ""

    # Бережно — не трогаем числа/единицы
    # Лёгкие замены частотных слов/фраз
    repl = [
        (r"\bсообщает\b", "уточняет"),
        (r"\bсообщили\b", "уточнили"),
        (r"\bзаявил(а|и)?\b", "отметил\\1"),
        (r"\bпо данным\b", "согласно сведениям"),
        (r"\bв частности\b", "в том числе"),
        (r"\bранее\b", "прежде"),
        (r"\bтакже\b", "кроме того"),
        (r"\bоднако\b", "впрочем"),
        (r"\bпри этом\b", "одновременно"),
        (r"\bв результате\b", "в итоге"),
        (r"\bмежду тем\b", "тем временем"),
        (r"\bсегодня\b", "сегодня же"),
        (r"\bвчера\b", "накануне"),
        (r"\bсогласно\b", "по информации"),
        (r"\bотмечается\b", "подчёркивается"),
        (r"\bподчеркнул(а|и)?\b", "добавил\\1"),
    ]
    out = text
    for pat, rep in repl:
        out = re.sub(pat, rep, out, flags=re.IGNORECASE)

    # Небольшая перестановка вводных конструкций в пределах предложений
    # Разбиваем по точкам/вопрос/восклиц, рерайтим отдельные предложения
    sentences = re.split(r"(?<=[\.\!\?])\s+", out)
    out_s = []
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        # перенос вводных, если есть запятая
        if "," in s and len(s) > 70:
            parts = [p.strip() for p in s.split(",")]
            if len(parts) >= 2 and len(parts[0]) < 35:
                # перенесём вводную часть в конец
                core = ", ".join(parts[1:])
                s = f"{core}, {parts[0]}"
        out_s.append(s)

    out = " ".join(out_s)
    # финальная чистка
    out = re.sub(r"\s+,", ",", out)
    out = re.sub(r"\s+\.", ".", out)
    out = re.sub(r"\s{2,}", " ", out)
    return out.strip()


# ----------------------- ИЗОБРАЖЕНИЕ ШАПКИ -----------------------

def _try_font(size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except Exception:
        return ImageFont.load_default()

def _v_gradient(size, c1, c2):
    w, h = size
    base = Image.new("RGB", size, c1)
    top = Image.new("RGB", size, c2)
    mask = Image.linear_gradient("L").resize((1, h)).resize((w, h))
    return Image.composite(top, base, mask)

def _add_subtle_noise(img: Image.Image, amount=0.04) -> Image.Image:
    noise = Image.effect_noise(img.size, 100).convert("L")
    noise = ImageChops.multiply(noise, Image.new("L", img.size, int(amount * 255)))
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    out = ImageChops.add_modulo(img, noise_rgb)
    return out.filter(ImageFilter.GaussianBlur(0.3))

def _vignette(img: Image.Image, strength=0.22) -> Image.Image:
    w, h = img.size
    vign = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(vign)
    ellipse_bbox = (-int(w*0.15), -int(h*0.2), int(w*1.15), int(h*1.2))
    draw.ellipse(ellipse_bbox, fill=int(255*strength))
    vign = vign.filter(ImageFilter.GaussianBlur(radius=min(w, h)//6))
    return Image.composite(img, Image.new("RGB", img.size, (0, 0, 0)), vign)

def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int):
    words = text.strip().split()
    lines, cur = [], []
    for w in words:
        test = (" ".join(cur+[w])).strip()
        tw = draw.textlength(test, font=font)
        if tw <= max_width or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines[:4]  # не более 4 строк на карточке

def draw_header_image(title: str, source_domain: str, dt_str: str,
                      size=(1024, 540)) -> bytes:
    W, H = size
    c1, c2 = random.choice(_BRAND_PALETTES)
    bg = _v_gradient((W, H), c1, c2)
    try:
        bg = _add_subtle_noise(bg, 0.04)
        bg = _vignette(bg, 0.22)
    except Exception:
        pass

    draw = ImageDraw.Draw(bg)
    font_ui = _try_font(36)
    font_small = _try_font(24)

    # Логотип-круг
    logo_r = 34
    logo_x, logo_y = 40, 34
    draw.ellipse((logo_x-logo_r, logo_y-logo_r, logo_x+logo_r, logo_y+logo_r),
                 fill=(240, 242, 245))
    draw.text((logo_x-16, logo_y-22), "$", font=_try_font(42), fill=(30, 33, 36))
    draw.text((logo_x+46, logo_y-14), "USDT=Dollar", font=font_ui, fill=(245, 246, 247))

    # Дата поста
    dt_box = draw.textbbox((0, 0), f"пост: {dt_str}", font=font_small)
    draw.text((W - dt_box[2] - 28, 26), f"пост: {dt_str}", font=font_small, fill=(235, 235, 238))

    # Карточка под заголовок
    pad = 38
    card_top = 120
    card_w, card_h = W - pad*2, H - card_top - pad
    card = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    cdraw = ImageDraw.Draw(card)
    radius = 28
    rect = (0, 0, card_w, card_h)
    overlay = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
    o_d = ImageDraw.Draw(overlay)
    o_d.rounded_rectangle(rect, radius=radius, fill=(0, 0, 0, 160))
    card = Image.alpha_composite(card, overlay)

    # Заголовок
    title_font = _try_font(64)
    inner_pad_x, inner_pad_y = 42, 32
    max_text_w = card_w - inner_pad_x*2
    lines = _wrap_text(cdraw, title, title_font, max_text_w)
    y = inner_pad_y
    for line in lines:
        cdraw.text((inner_pad_x+2, y+2), line, font=title_font, fill=(0, 0, 0, 96))
        cdraw.text((inner_pad_x, y), line, font=title_font, fill=(250, 250, 252))
        y += title_font.size + 6

    # Источник внизу карточки
    src_font = _try_font(22)
    cdraw.text((inner_pad_x, card_h - inner_pad_y - 4),
               f"source: {source_domain}", font=src_font, fill=(210, 214, 219))

    bg.paste(card, (pad, card_top), card)

    out = io.BytesIO()
    bg.save(out, format="JPEG", quality=92, optimize=True)
    out.seek(0)
    return out.read()


# ----------------------- ПАРСИНГ RSS -----------------------

def get_domain(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.", "")
    except Exception:
        return "source"

def fetch_items():
    """Идём по источникам по порядку и возвращаем (feed_url, entry) первым подходящим."""
    for rss in RSS_FEEDS:
        try:
            d = feedparser.parse(rss)
            # по убыванию даты, если есть
            entries = sorted(d.entries, key=lambda e: e.get("published_parsed", time.gmtime(0)), reverse=True)
            for e in entries:
                link = e.get("link") or e.get("id") or ""
                title = strip_html(e.get("title", ""))
                # контент берём из content/summary
                content = ""
                if "content" in e and e.content:
                    content = " ".join(strip_html(c.value) for c in e.content if hasattr(c, "value"))
                elif "summary" in e:
                    content = strip_html(e.summary)
                # отброс пустых/мусорных
                if len(title) < 8 or len(content) < 120:
                    continue
                yield rss, {
                    "id": e.get("id", link) or link,
                    "title": title,
                    "content": content,
                    "link": link,
                    "published": e.get("published", "") or e.get("updated", "")
                }
        except Exception as ex:
            logging.warning("RSS error %s: %s", rss, ex)
            continue


# ----------------------- TELEGRAM -----------------------

def send_photo_with_caption(image_bytes: bytes, caption_html: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("header.jpg", image_bytes, "image/jpeg")}
    data = {
        "chat_id": CHANNEL_ID,
        "caption": caption_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    r = requests.post(url, data=data, files=files, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendPhoto error {r.status_code}: {r.text}")
    return r.json()


# ----------------------- ГЛАВНАЯ ЛОГИКА -----------------------

def build_caption(title: str, body: str, link: str) -> str:
    # Заголовок, потом развёрнутый текст, затем кликабельные ссылки
    domain = get_domain(link)
    # ограничим подпись, чтобы не упереться в лимит Telegram
    body = body.strip()
    # собираем HTML
    parts = []
    parts.append(f"<b>{esc(title)}</b>")
    parts.append(esc(body))
    parts.append("")
    parts.append(f"Источник: <a href=\"{esc(link)}\">{esc(domain)}</a>")
    parts.append(f"🪙 <a href=\"https://t.me/usdtdollarm\">USDT=Dollar</a>")
    caption = "\n".join(parts)
    if len(caption) > CAPTION_LIMIT:
        # мягко укорачиваем основной текст, оставляя ссылки
        overshoot = len(caption) - CAPTION_LIMIT
        cut = max(0, len(body) - overshoot - 3)
        body = (body[:cut] + "…").rstrip()
        parts[1] = esc(body)
        caption = "\n".join(parts)
    return caption

def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан (ожидается в Secrets/GitHub Actions).")

    state = load_state()
    now = dt.datetime.now()
    dt_str = now.strftime("%d.%m %H:%M")

    for rss, item in fetch_items():
        uniq_key = f"{get_domain(item['link'])}|{item['id']}"
        if uniq_key in state["posted"]:
            continue

        # Перефраз + фильтры длины
        base_text = item["content"]
        base_text = re.sub(r"\s{2,}", " ", base_text).strip()
        base_text = re.sub(r"(?i)читать( далее| полностью).*", "", base_text)

        if len(base_text) < MIN_BODY_CHARS:
            # короткие — пропускаем
            continue

        body = conservative_paraphrase(base_text)
        # подстрахуемся от дублирования предложений
        # (уберём дословные повторы длиной > 30 символов)
        seen = set()
        cleaned_sentences = []
        for s in re.split(r"(?<=[\.\!\?])\s+", body):
            cs = s.strip()
            if len(cs) < 3:
                continue
            key = cs.lower()
            if key in seen:
                continue
            seen.add(key)
            cleaned_sentences.append(cs)
        body = " ".join(cleaned_sentences)

        if len(body) < MIN_BODY_CHARS:
            continue

        title = item["title"].strip()
        # Рисуем шапку
        img_bytes = draw_header_image(title, get_domain(item["link"]), dt_str)

        caption = build_caption(title, body, item["link"])
        try:
            send_photo_with_caption(img_bytes, caption)
            logging.info("Posted: %s", title)
            mark_posted(state, uniq_key)
            # Опубликовали одну — выходим (по порядку источников)
            return
        except Exception as ex:
            logging.error("Ошибка публикации: %s", ex)
            # не помечаем как опубликованную — попробуем позже
            continue

    logging.info("Подходящих новостей не найдено (длина/язык/дубли).")


if __name__ == "__main__":
    main()
