# bot/poster.py
# -*- coding: utf-8 -*-
import os, io, json, time, random, re, html
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# =========================
# Конфиг
# =========================
BOT_TOKEN   = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID  = os.getenv("CHANNEL_ID", "").strip()      # например: @usdtdollarm
MIN_LEN     = 400                                      # не постим короткие новости
STATE_DIR   = "data"
STATE_FILE  = os.path.join(STATE_DIR, "posted.json")
IMG_W, IMG_H = 1280, 640

# Брендовая палитра (спокойные, премиальные оттенки)
BRAND_GRADIENTS = [
    # (start, end, accent)
    ((18, 32, 56), (63, 81, 181), (124, 77, 255)),        # графит → индиго (+фиолет)
    ((22, 30, 40), (45, 64, 89),  (0, 173, 181)),         # тёмно-синий → стальной (+бирюза)
    ((30, 27, 38), (88, 28, 135), (255, 145, 77)),        # уголь → фиолет (+оранж. акцент)
    ((20, 24, 28), (50, 63, 72),  (214, 175, 98)),        # графит → сланец (+золото)
    ((16, 19, 23), (36, 44, 52),  (82, 113, 255)),        # чёрный-синий → сталь (+синий акцент)
]

CATEGORY_COLORS = {
    "Экономика": (66, 133, 244),
    "Политика":  (234, 67, 53),
    "Технологии":(52, 168, 83),
    "Происшествия": (251, 188, 5),
    "Общество": (156, 39, 176),
}

RSS_SOURCES = [
    # 100% русскоязычные ленты
    "https://lenta.ru/rss/news",
    "https://tass.ru/rss/v2.xml",
    "https://static.feed.rbc.ru/rbc/logical/footer/news.rss",
    "https://www.interfax.ru/rss.asp",
    "https://www.vedomosti.ru/rss/news",
    "https://iz.ru/rss",  # Известия
    "https://www.kommersant.ru/RSS/news.xml",
    "https://fontanka.ru/rss.xml",
    "https://www.kp.ru/rss/politics.xml",
    "https://www.gazeta.ru/export/rss/first.xml",
]

# =========================
# Утилиты
# =========================
def ensure_state():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(STATE_FILE):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"ids": []}, f, ensure_ascii=False)

def is_posted(uid: str) -> bool:
    ensure_state()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return uid in data.get("ids", [])

def mark_posted(uid: str):
    ensure_state()
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    ids = set(data.get("ids", []))
    ids.add(uid)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"ids": list(ids)}, f, ensure_ascii=False)

def domain_of(url: str) -> str:
    try:
        return urlparse(url).hostname.replace("www.", "")
    except:
        return ""

def safe_font(size=40, bold=False):
    # DejaVuSans есть практически везде в GitHub Actions/Pillow.
    # Фолбэк — встроенный bitmap-шрифт.
    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
         if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ("/usr/local/share/fonts/DejaVuSans-Bold.ttf"
         if bold else "/usr/local/share/fonts/DejaVuSans.ttf")
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except:
                pass
    return ImageFont.load_default()

def text_wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int):
    words = text.split()
    lines, cur = [], []
    for w in words:
        cur.append(w)
        bbox = draw.textbbox((0,0), " ".join(cur), font=font)
        if bbox[2] - bbox[0] > max_w:
            cur.pop()
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines

# =========================
# Перефразирование (бережное)
# =========================
REWRITE_MAP = [
    (r"\bсообщил\(а\)\b", "заявил(а)"),
    (r"\bсообщает\b", "уточняет"),
    (r"\bсообщили\b", "подтвердили"),
    (r"\bпо его словам\b", "как отметил"),
    (r"\bпо её словам\b", "как отметила"),
    (r"\bпо их словам\b", "как указали"),
    (r"\bранее\b", "до этого"),
    (r"\bсогласно\b", "по данным"),
    (r"\bотметил\b", "подчеркнул"),
    (r"\bотметила\b", "подчеркнула"),
    (r"\bтакже\b", "кроме того"),
    (r"\bв том числе\b", "в частности"),
]

def clean_text(t: str) -> str:
    t = re.sub(r"\s+", " ", t).strip()
    # Убираем HTML-сущности типа &laquo;
    t = BeautifulSoup(t, "html.parser").get_text(" ")
    return t

def rewrite_soft(text: str) -> str:
    # Консервативный рерайт: только стилистика, без изменения фактов.
    out = text
    for patt, repl in REWRITE_MAP:
        out = re.sub(patt, repl, out, flags=re.IGNORECASE)
    # Сокращаем дубли предложений (иногда в RSS дублируют лид)
    parts = [p.strip() for p in re.split(r"(?<=[\.\!\?])\s+", out) if p.strip()]
    uniq = []
    seen = set()
    for p in parts:
        key = re.sub(r"\W+", "", p.lower())
        if key not in seen:
            uniq.append(p)
            seen.add(key)
    return " ".join(uniq)

# =========================
# Категория по ключевым словам
# =========================
KW = {
    "Экономика": ["биржа","рынок","акции","инфляц","рубл","доллар","эконом","банк","бюджет","ВВП"],
    "Политика": ["президент","премьер","правительств","санкц","выбор","парламент","госдум","кабинет"],
    "Технологии": ["техно","ИИ","искусственн","робот","стартап","софт","платформ","смартфон","микросх"],
    "Происшествия": ["пожар","ДТП","авар","взрыв","шторм","штраф","обвал","задержан","суд","дело"],
    "Общество": ["школ","университет","экология","культура","спорт","образован","здравоохран","соц"],
}
def guess_category(title: str, body: str) -> str:
    text = f"{title} {body}".lower()
    score = {k:0 for k in CATEGORY_COLORS}
    for cat, keys in KW.items():
        for k in keys:
            if k in text: score[cat]+=1
    cat = max(score, key=lambda c: score[c])
    # если все нули — экономия по умолчанию
    return cat if score[cat]>0 else "Экономика"

# =========================
# Фон: градиент + grain + spotlights + лейбл категории
# =========================
def linear_gradient(size, start_rgb, end_rgb, angle_deg=0):
    w, h = size
    base = Image.new("RGB", size, start_rgb)
    top  = Image.new("RGB", size, end_rgb)
    # создаём маску
    mask = Image.linear_gradient("L")  # горизонтальный
    if angle_deg % 360 != 0:
        mask = mask.rotate(angle_deg, expand=False, resample=Image.BICUBIC)
    mask = mask.resize(size)
    return Image.composite(top, base, mask)

def add_grain(img: Image.Image, opacity=0.07):
    noise = Image.effect_noise(img.size, 8.0)
    noise = noise.convert("L").point(lambda p: int(p*255/255))
    noise = Image.merge("RGBA", (noise, noise, noise, noise))
    blend = Image.new("RGBA", img.size, (0,0,0,0))
    blend = Image.alpha_composite(Image.alpha_composite(Image.new("RGBA", img.size), img.convert("RGBA")), Image.new("RGBA", img.size))
    out = Image.alpha_composite(img.convert("RGBA"), Image.new("RGBA", img.size))
    # накладываем шум как overlay через прозрачность
    noise.putalpha(int(255*opacity))
    return Image.alpha_composite(img.convert("RGBA"), noise).convert("RGB")

def add_spotlights(img: Image.Image, accents, count=2):
    rng = random.Random()
    over = Image.new("RGBA", img.size, (0,0,0,0))
    draw = ImageDraw.Draw(over, "RGBA")
    for _ in range(count):
        color = random.choice(accents)
        x = rng.randint(int(0.1*IMG_W), int(0.9*IMG_W))
        y = rng.randint(int(0.1*IMG_H), int(0.9*IMG_H))
        r = rng.randint(int(IMG_H*0.18), int(IMG_H*0.28))
        for rad in range(r, 0, -4):
            alpha = int(120 * (rad/r) * 0.25)
            draw.ellipse((x-rad,y-rad,x+rad,y+rad), fill=(color[0], color[1], color[2], alpha))
    merged = Image.alpha_composite(img.convert("RGBA"), over)
    return merged.convert("RGB")

def draw_category_badge(draw, text, x, y, font, fill):
    pad_x, pad_y = 18, 8
    bbox = draw.textbbox((0,0), text, font=font)
    bw = (bbox[2]-bbox[0]) + pad_x*2
    bh = (bbox[3]-bbox[1]) + pad_y*2
    radius = 14
    rect = Image.new("RGBA", (bw, bh), (0,0,0,0))
    rdraw = ImageDraw.Draw(rect)
    rdraw.rounded_rectangle((0,0,bw,bh), radius, fill=fill+(255,))
    # тень
    rdraw.rounded_rectangle((2,2,bw, bh), radius, outline=None, fill=(0,0,0,50))
    draw.bitmap((x,y), rect, fill=None)
    draw.text((x+pad_x, y+pad_y), text, font=font, fill=(255,255,255))

def build_header_image(title, source_domain, category):
    # 1) градиент
    start, end, accent = random.choice(BRAND_GRADIENTS)
    grad = linear_gradient((IMG_W, IMG_H), start, end, angle_deg=random.choice([0,45,60,120,180]))
    # 2) лёгкий grain
    grad = add_grain(grad, opacity=0.06)
    # 3) мягкие световые пятна
    grad = add_spotlights(grad, accents=[accent,(255,255,255)], count=2)

    # Рисуем контент
    draw = ImageDraw.Draw(grad)
    f_brand   = safe_font(40, bold=True)
    f_time    = safe_font(26, bold=False)
    f_title   = safe_font(64, bold=True)
    f_source  = safe_font(28, bold=False)
    f_badge   = safe_font(28, bold=True)

    # Шапка: логотип и имя канала
    logo_r = 26
    cx, cy = 62, 62
    draw.ellipse((cx-logo_r, cy-logo_r, cx+logo_r, cy+logo_r), fill=(242,242,242))
    draw.text((cx-9, cy-15), "$", fill=(60,60,60), font=safe_font(42, bold=True))
    draw.text((cx+logo_r+16, cy-22), "USDT=Dollar", fill=(240,240,240), font=f_brand)

    # Время поста
    dt = datetime.now(timezone.utc).astimezone()
    draw.text((IMG_W-350, 22), f"пост: {dt:%d.%m %H:%M}", fill=(230,230,230), font=f_time)

    # Плашка под заголовок (полупрозрачная)
    pad = 28
    block = Image.new("RGBA", (IMG_W - pad*2, int(IMG_H*0.58)), (0,0,0,0))
    bdraw = ImageDraw.Draw(block)
    bdraw.rounded_rectangle((0,0,block.size[0],block.size[1]), 28, fill=(0,0,0,140))
    grad.paste(block, (pad, int(IMG_H*0.18)), block)

    # Заголовок
    title_area_w = IMG_W - pad*4
    title_x = pad*2
    title_y = int(IMG_H*0.22)
    lines = text_wrap(draw, title, f_title, title_area_w)
    # оставим 2-3 строки максимум
    lines = lines[:3]
    cur_y = title_y
    for ln in lines:
        draw.text((title_x, cur_y), ln, font=f_title, fill=(255,255,255))
        cur_y += safe_font(64, bold=True).getbbox("Ag")[3] + 10

    # Источник и событие
    draw.text((pad, IMG_H-46), f"source: {source_domain}", fill=(235,235,235), font=f_source)

    # Бейдж категории
    badge_col = CATEGORY_COLORS.get(category, (66,133,244))
    draw_category_badge(draw, category, IMG_W- pad - 260, pad+8, f_badge, badge_col)

    return grad

# =========================
# Получение новости
# =========================
def fetch_fresh():
    for url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:8]:
                link = e.get("link") or ""
                title = clean_text(e.get("title",""))
                # тянем страницу и парсим абзацы
                if not link or not title: 
                    continue
                html_doc = requests.get(link, timeout=10).text
                soup = BeautifulSoup(html_doc, "html.parser")
                # собираем текст новости
                candidates = soup.find_all(["p","article","div"])
                text_chunks = []
                for c in candidates:
                    t = c.get_text(" ").strip()
                    # отбрасываем мусор, оставляем осмысленные абзацы
                    if 60 <= len(t) <= 1500 and not re.search(r"cookie|javascript|enable", t, re.I):
                        text_chunks.append(t)
                    if len(" ".join(text_chunks)) > 1200:  # достаточно
                        break
                body = clean_text(" ".join(text_chunks))
                if not body or len(body) < MIN_LEN:
                    continue
                uid = f"{domain_of(link)}::{hash(title) ^ hash(body[:80])}"
                if is_posted(uid):
                    continue
                category = guess_category(title, body)
                return {
                    "uid": uid,
                    "title": title,
                    "body": rewrite_soft(body),
                    "link": link,
                    "source": domain_of(link),
                    "category": category
                }
        except Exception:
            continue
    return None

# =========================
# Телеграм
# =========================
def send_post(title, body, link, source, category):
    # картинка-заголовок
    header_img = build_header_image(title, source, category)
    bio = io.BytesIO()
    header_img.save(bio, format="JPEG", quality=90)
    bio.seek(0)

    # подпись
    title_html = f"<b>{html.escape(title)}</b>"
    body_html  = html.escape(body)
    footer = (
        f'\n\nИсточник: <a href="{html.escape(link)}">{html.escape(source)}</a>\n'
        f'<a href="https://t.me/{CHANNEL_ID.lstrip("@")}">USDT=Dollar</a>'
    )
    caption = f"{title_html}\n\n{body_html}{footer}"
    # Telegram ограничение: 1024 символа в подписи фото (HTML)
    if len(caption) > 1024:
        # сокращаем тело, сохраняя аккуратное окончание
        trim = 1024 - len(footer) - len(title_html) - 10
        body_short = (body_html[:trim].rsplit(" ",1)[0] + "…") if trim>0 else ""
        caption = f"{title_html}\n\n{body_short}{footer}"

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    resp = requests.post(
        url,
        data={
            "chat_id": CHANNEL_ID,
            "caption": caption,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        files={"photo": ("header.jpg", bio.getvalue(), "image/jpeg")},
        timeout=20
    )
    try:
        j = resp.json()
    except Exception:
        j = {}
    ok = j.get("ok") is True
    return ok, j

# =========================
# main
# =========================
def main():
    if not BOT_TOKEN or not CHANNEL_ID:
        raise SystemExit("Set BOT_TOKEN and CHANNEL_ID env vars")

    news = fetch_fresh()
    if not news:
        print("Нет подходящих свежих новостей.")
        return

    # публикация
    ok, info = send_post(
        title=news["title"],
        body=news["body"],
        link=news["link"],
        source=news["source"],
        category=news["category"]
    )
    if ok:
        mark_posted(news["uid"])
        print("Опубликовано:", news["title"])
    else:
        print("Ошибка публикации:", info)

if __name__ == "__main__":
    main()