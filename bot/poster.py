# -*- coding: utf-8 -*-
"""
USDT=Dollar — авто-постер для канала (RU only).
— Читает 50 русскоязычных RSS (data/sources_ru.txt)
— Для свежих новостей вытягивает текст, чистит, частично перефразирует (~50%), без изменения фактов
— Фильтры: только кириллица, минимум 400 символов, удаление повторов
— Рисует мягкую обложку (градиент + тёмный блок) с заголовком
— Публикует ОТ ИМЕНИ КАНАЛА (бот должен быть админом канала)
— Подпись: Заголовок → Подробности → Источник (кликабельно) → Имя канала (кликабельно)
"""

import os, re, time, json, html, hashlib, random, datetime
from io import BytesIO
from urllib.parse import urlparse

import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ---------- Параметры ----------
BOT_TOKEN  = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()   # @usdtdollarm
BRAND      = os.getenv("BRAND", "USDT=Dollar")

DATA_DIR        = "data"
SOURCES_FILE    = os.path.join(DATA_DIR, "sources_ru.txt")
POSTED_FILE     = os.path.join(DATA_DIR, "posted.json")

FRESH_MINUTES   = 120
MIN_CHARS       = 400
CYR_RATIO_MIN   = 0.5
MAX_POSTS_PER_RUN = 1   # один пост за прогона — без спама

IMG_W, IMG_H    = 1024, 512
LOGO_EMOJI      = "💠"

FONT_REGULAR = os.path.join(DATA_DIR, "DejaVuSans.ttf")
FONT_BOLD    = os.path.join(DATA_DIR, "DejaVuSans-Bold.ttf")

# ---------- Вспомогалки ----------

def tz_msk():
    return datetime.timezone(datetime.timedelta(hours=3))

def now_msk():
    return datetime.datetime.now(tz_msk())

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.","")
    except:
        return ""

def ensure_data():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(POSTED_FILE):
        with open(POSTED_FILE, "w", encoding="utf-8") as f:
            json.dump([], f)

def load_posted():
    ensure_data()
    try:
        with open(POSTED_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except:
        return set()

def save_posted(s: set):
    ensure_data()
    with open(POSTED_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False)

def read_sources():
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]

def clean_html(txt: str) -> str:
    if not txt: return ""
    txt = html.unescape(txt)
    txt = re.sub(r"<\s*br\s*/?>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"\s+([,.;:!?])", r"\1", txt)
    # разлипание слепленных слов
    txt = re.sub(r"([а-яё])([А-ЯЁ])", r"\1 \2", txt)
    return txt.strip()

def cyr_ratio(text: str) -> float:
    if not text: return 0.0
    letters = [ch for ch in text if ch.isalpha()]
    if not letters: return 0.0
    return len([ch for ch in letters if re.match(r"[А-ЯЁа-яё]", ch)]) / len(letters)

def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]

def drop_noise(text: str) -> str:
    lines = [l.strip() for l in text.splitlines()]
    out = []
    for l in lines:
        if not l:
            out.append(l); continue
        if re.search(r"https?://\S+", l):     # голые ссылки вычищаем
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}(\s*\d{2}\.\d{2}\.\d{4})?", l):
            continue
        if len(l) <= 18 and not l.endswith("."):
            continue
        out.append(l)
    cleaned = " ".join(out)
    # убрать повторяющиеся предложения
    uniq = []
    seen = set()
    for s in split_sentences(cleaned):
        k = s.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return " ".join(uniq)

# ---------- Мягкий рерайт (правила) ----------

REWRITE_RULES = [
    # глаголы-«сообщил/сообщает»
    (r"\bсообщил(а|и)?\b", "заявил\\1"),
    (r"\bсообщает\b", "сообщает, что"),
    (r"\bсообщили\b", "заявили"),
    (r"\bпо данным\b", "по информации"),
    (r"\bсогласно\b", "как следует из"),
    (r"\bотметил(а|и)?\b", "подчеркнул\\1"),
    (r"\bзаявил(а|и)?\b", "заявил\\1"),
    (r"\bуточнил(а|и)?\b", "дополнительно отметил\\1"),
    (r"\bподчеркнул(а|и)?\b", "отдельно отметил\\1"),
    # вводные
    (r"\bв частности\b", "например"),
    (r"\bв то же время\b", "одновременно"),
    (r"\bнаряду с этим\b", "при этом"),
    (r"\bмежду тем\b", "тем временем"),
    # устойчивые
    (r"\bв ближайшее время\b", "в ближайшей перспективе"),
    (r"\bв настоящее время\b", "сейчас"),
    (r"\bв связи с\b", "из-за"),
    (r"\bтаким образом\b", "итогом стало то, что"),
    # синтаксис: чуть мягче
    (r"\s-\s", " — "),
]

def soft_rewrite_sentence(s: str) -> str:
    orig = s
    # не трогаем числа, даты, котировки — они останутся как есть
    # заменяем только связки/вводные
    for pat, rep in REWRITE_RULES:
        s = re.sub(pat, rep, s, flags=re.IGNORECASE)
    # лёгкая перестановка при: «X, заявил(а) Y.» → «Как заявил(а) Y, X.»
    m = re.search(r"(?P<body>.+?),\s*(заявил|заявила|заявили)\s+(?P<who>[^.]+)\.$", s, flags=re.I)
    if m and len(m.group("body")) > 40:
        s = f"Как {m.group(0).split(',')[1].strip()}, {m.group('body')}."
        s = re.sub(r",\s*заявил.*", "", s)
    # с заглавной
    if s:
        s = s[:1].upper() + s[1:]
    return s if s.strip() else orig

def rewrite_text(text: str, title: str) -> str:
    sents = split_sentences(text)
    if not sents:
        return ""
    # избегаем повторения заголовка
    title_norm = title.lower().strip().rstrip(".")
    out = []
    seen = set()
    for i, s in enumerate(sents):
        sn = s.lower().strip()
        if sn.rstrip(".") == title_norm:
            continue
        r = soft_rewrite_sentence(s)
        key = r.lower()
        if key in seen: 
            continue
        seen.add(key)
        out.append(r)
        if len(out) >= 10:
            break
    return " ".join(out).strip()

# ---------- Парсинг статей ----------

def fetch_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []
        selectors = [
            "article", ".article__content", "[itemprop='articleBody']",
            ".article", ".news-item__content", ".layout-article",
            ".lenta__text", ".content__body", ".b-material-wrapper"
        ]
        for sel in selectors:
            for el in soup.select(sel):
                t = clean_html(el.get_text("\n"))
                if len(t) > 200:
                    candidates.append(t)
        base = ""
        if candidates:
            base = sorted(candidates, key=len, reverse=True)[0][:8000]
        else:
            base = clean_html(soup.get_text("\n"))[:6000]
        return drop_noise(base)
    except:
        return ""

# ---------- Рендер обложки ----------

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

def hsv_to_rgb(h,s,v):
    import colorsys
    r,g,b = colorsys.hsv_to_rgb(h/360.0, s, v)
    return (int(r*255), int(g*255), int(b*255))

def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, line = [], ""
    for w in words:
        t = (line+" "+w).strip()
        bbox = draw.textbbox((0,0), t, font=font)
        if bbox[2]-bbox[0] <= max_width:
            line = t
        else:
            if line: lines.append(line)
            line = w
    if line: lines.append(line)
    return lines

def draw_header(title: str, source: str) -> bytes:
    seed = int(hashlib.sha1(title.encode("utf-8")).hexdigest(), 16)
    random.seed(seed)
    base_h = random.randint(190, 330)      # спокойные фиолет/син в диапазоне
    c1 = hsv_to_rgb(base_h, 0.28, 0.95)
    c2 = hsv_to_rgb((base_h+20)%360, 0.25, 0.78)

    img = Image.new("RGB", (IMG_W, IMG_H), c1)
    grad = Image.new("RGB", (IMG_W, IMG_H), c2)
    mask = Image.linear_gradient("L").resize((IMG_W, IMG_H)).filter(ImageFilter.GaussianBlur(1.5))
    img = Image.composite(grad, img, mask)

    d = ImageDraw.Draw(img)
    # диагональный лёгкий паттерн
    for x in range(-IMG_H, IMG_W, 24):
        d.line([(x,0),(x+IMG_H,IMG_H)], fill=(255,255,255,15), width=1)

    # затемнённый блок под текст
    block = Image.new("RGBA", (IMG_W-80, IMG_H-140), (0,0,0,150))
    img.paste(block, (40,110), block)

    # шрифты
    font_title = load_font(FONT_BOLD, 60)
    font_brand = load_font(FONT_BOLD, 32)
    font_tiny  = load_font(FONT_REGULAR, 22)

    # верхняя строка: лого + бренд
    d.ellipse((28,18,68,58), fill=(245,245,245))
    d.text((36,24), "$", font=load_font(FONT_BOLD, 24), fill=(20,20,20))
    d.text((78,22), BRAND, font=font_brand, fill=(245,245,245))

    # заголовок
    max_w = IMG_W - 140
    y = 150
    for line in wrap_text(d, title, font_title, max_w)[:3]:
        d.text((70,y), line, font=font_title, fill=(245,245,248))
        y += 68

    # источник
    d.text((36, IMG_H-40), f"source: {source}", font=font_tiny, fill=(230,230,235))

    out = BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()

# ---------- Телеграм ----------

def html_escape(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def build_caption(title, body, link, channel):
    title_h = f"<b>{html_escape(title.strip())}</b>"
    body = body.strip()
    # аккуратные абзацы по 2–4 предложения
    sents = split_sentences(body)
    paras, cur, lim = [], [], 220
    for s in sents[:10]:
        if len(" ".join(cur+[s])) <= lim:
            cur.append(s)
        else:
            paras.append(" ".join(cur)); cur=[s]
    if cur: paras.append(" ".join(cur))
    details = "\n\n".join(html_escape(p) for p in paras[:3])

    src = domain_of(link)
    source_h = f"<b>Источник:</b> <a href=\"{html_escape(link)}\">{html_escape(src)}</a>"
    channel_h = f"<a href=\"https://t.me/{channel.lstrip('@')}\">{html_escape(channel.lstrip('@'))}</a>"

    parts = [title_h, "", details, "", source_h, channel_h]
    cap = "\n".join([p for p in parts if p is not None]).strip()
    if len(cap) > 1024:
        # мягко укорачиваем детали
        keep = 1024 - (len(cap) - len(details)) - 10
        details = html_escape(details[:keep].rsplit(" ",1)[0]) + "…"
        parts[2] = details
        cap = "\n".join(parts).strip()
    return cap

def send_photo(token, chat_id, photo_bytes, caption_html):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("header.jpg", photo_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption_html, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, data=data, files=files, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"sendPhoto {r.status_code}: {r.text}")

# ---------- Основной цикл ----------

def gather_items():
    items = []
    for feed in read_sources():
        try:
            fp = feedparser.parse(feed)
            for e in fp.entries[:12]:
                link = e.get("link") or e.get("id") or ""
                title = clean_html(e.get("title",""))
                if not link or not title: 
                    continue
                pp = e.get("published_parsed") or e.get("updated_parsed")
                if pp:
                    dt = datetime.datetime.fromtimestamp(
                        time.mktime(pp),
                        datetime.timezone.utc
                    ).astimezone(tz_msk())
                else:
                    dt = now_msk()
                items.append((dt, title, link))
        except Exception:
            continue
    items.sort(key=lambda x: x[0], reverse=True)
    return items

def main():
    assert BOT_TOKEN and CHANNEL_ID, "Задай BOT_TOKEN и CHANNEL_ID в Secrets."
    posted = load_posted()
    posted_changed = False
    posted_count = 0

    for dt, title, link in gather_items():
        if posted_count >= MAX_POSTS_PER_RUN:
            break
        if link in posted:
            continue
        if (now_msk()-dt).total_seconds() > FRESH_MINUTES*60:
            continue

        raw = fetch_article(link)
        text = clean_html(raw)
        text = drop_noise(text)

        if len(text) < MIN_CHARS or cyr_ratio(text) < CYR_RATIO_MIN:
            continue

        # рерайт ≈50%
        body = rewrite_text(text, title)
        if len(body) < MIN_CHARS:
            # если сильно ужалось — берём часть оригинала + рерайт оставшейся
            body = rewrite_text(text[:2000], title)

        # финальная страховка от повторов заголовка
        title_norm = title.lower().strip().rstrip(".")
        body_sents = [s for s in split_sentences(body) if s.lower().strip().rstrip(".") != title_norm]
        body = " ".join(body_sents).strip()

        if len(body) < MIN_CHARS:
            continue

        # картинка + подпись
        photo = draw_header(title, domain_of(link))
        caption = build_caption(title, body, link, CHANNEL_ID)

        try:
            send_photo(BOT_TOKEN, CHANNEL_ID, photo, caption)
            posted.add(link); posted_changed = True; posted_count += 1
            print("Posted:", title)
        except Exception as ex:
            print("Ошибка отправки:", ex)
            continue

    if posted_changed:
        save_posted(posted)
    if posted_count == 0:
        print("Подходящих свежих новостей не найдено.")

if __name__ == "__main__":
    main()
