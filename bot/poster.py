# -*- coding: utf-8 -*-
"""
USDT=Dollar — авто-постер
• Источники: RU-RSS (новости)
• Парсинг статьи + мягкий рерайт без искажения фактов
• Красивый заголовок-баннер: мягкий градиент + лёгкий акцент, без «вырвиглаз»
• Подпись в стиле СМИ: Заголовок → Лид → Подробности → Источник
• Без хэштегов вовсе. Без блока «что это значит».
• Защита от дублей и «мусора» (временные метки, голые URL, повторные предложения)
"""

import os, re, json, time, html, hashlib, random, datetime
from io import BytesIO
from urllib.parse import urlparse

import requests
import feedparser
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# -------------------- Конфиг --------------------

FEEDS = [
    "https://www.rbc.ru/rss/?rss=news",
    "https://lenta.ru/rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.gazeta.ru/export/rss/first.xml",
    "https://www.interfax.ru/rss.asp",
    "https://iz.ru/xml/rss/all.xml",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://www.vedomosti.ru/rss/news",
]

BOT_TOKEN  = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # например: @usdtdollarm

POSTED_PATH      = "data/posted.json"
FRESH_MINUTES    = 90           # окно свежести
MIN_BODY_CHARS   = 400          # минимум символов статьи после чистки
CYR_RATIO_MIN    = 0.5          # доля кириллицы
IMG_W, IMG_H     = 1024, 512

BRAND            = "USDT=Dollar"
LOGO_EMOJI       = "🪙"

FONT_REGULAR_PATH = "data/DejaVuSans.ttf"
FONT_BOLD_PATH    = "data/DejaVuSans-Bold.ttf"

# -------------------- Утилиты --------------------

def now_msk():
    tz = datetime.timezone(datetime.timedelta(hours=3))
    return datetime.datetime.now(tz)

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.","")
    except:
        return ""

def load_posted():
    if not os.path.exists(POSTED_PATH):
        return set()
    try:
        with open(POSTED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data if isinstance(data, list) else [])
    except:
        return set()

def save_posted(s: set):
    os.makedirs(os.path.dirname(POSTED_PATH), exist_ok=True)
    with open(POSTED_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted(list(s)), f, ensure_ascii=False, indent=2)

def clean_html(txt: str) -> str:
    if not txt: return ""
    txt = html.unescape(txt)
    txt = re.sub(r"<\s*br\s*/?>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    txt = re.sub(r"\s+([,.;:!?])", r"\1", txt)
    txt = txt.replace(" - ", " — ")
    # разлипание "СправочникаВрача" → "Справочника Врача"
    txt = re.sub(r"([а-яё])([А-ЯЁ])", r"\1 \2", txt)
    return txt.strip()

def cyr_ratio(txt: str) -> float:
    if not txt: return 0.0
    total = len([ch for ch in txt if ch.isalpha()])
    if total == 0: return 0.0
    cyr = len(re.findall(r"[а-яёА-ЯЁ]", txt))
    return cyr / total

def split_sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]

def drop_noise_lines(text: str) -> str:
    """Вычистить явный мусор: голые URL, штампы времени/дат, повтор заголовков."""
    lines = [l.strip() for l in text.splitlines()]
    out = []
    for l in lines:
        if not l: 
            out.append(l)
            continue
        if re.search(r"https?://\S+", l):         # голые ссылки
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}(\s*\d{2}\.\d{2}\.\d{4})?", l):
            continue
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T?\d{2}:\d{2}:\d{2}\s*\+\d{2}:\d{2}", l):
            continue
        # короткие списки-тэги из одной колонки
        if len(l) <= 20 and len(l.split()) <= 3 and not l.endswith("."):
            # пропускаем такие крошки в «подробностях»
            continue
        out.append(l)
    out_s = "\n".join(out)
    # убирать троекратные повторы одна за одной
    parts = split_sentences(out_s)
    seen = set()
    uniq = []
    for s in parts:
        k = s.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(s)
    return " ".join(uniq)

def soft_rewrite(sent: str) -> str:
    """Мягкий рерайт без изменения фактов/чисел/имен."""
    if not sent: return ""
    s = sent

    replacements = {
        "сообщил ": "заявил ",
        "сообщила ": "заявила ",
        "сообщили ": "заявили ",
        "сообщается, что": "уточняется, что",
        "отметил ": "подчеркнул ",
        "отметила ": "подчеркнула ",
        "отметили ": "подчеркнули ",
        "по данным ": "по информации ",
        "согласно ": "как следует из ",
    }
    for k, v in replacements.items():
        s = re.sub(r"\b"+re.escape(k)+r"\b", v, s, flags=re.IGNORECASE)

    # Возможная перестановка: «X, заявили в Y.» → «Как заявили в Y, X.»
    m = re.search(r"(?P<body>.+?),\s*(заявил|заявила|заявили)\s+(?P<who>[^.]+)\.$", s, flags=re.I)
    if m and len(m.group("body")) > 40:
        s = f"Как {m.group(0).split(',')[1].strip()}, {m.group('body')}."
        s = re.sub(r",\s*заявил.*", "", s)  # на случай избыточного хвоста

    s = s[:1].upper() + s[1:] if s else s
    return s

def pick_lead_and_body(full: str, title: str) -> tuple[str, str]:
    sents = split_sentences(full)
    if not sents:
        return "", ""
    lead = soft_rewrite(sents[0])
    # если лид почти дублирует заголовок — возьмём следующую
    if lead.lower().strip().rstrip(".") == title.lower().strip().rstrip(".") and len(sents) > 1:
        lead = soft_rewrite(sents[1])
        sents = sents[1:]
    body_sents = []
    seen = set([lead.lower()])
    for s in sents[1:]:
        r = soft_rewrite(s)
        if not r: 
            continue
        key = r.lower()
        if key in seen:
            continue
        seen.add(key)
        body_sents.append(r)
        if len(body_sents) >= 6:   # компактные «подробности»
            break
    body = " ".join(body_sents)
    return lead, body

def fetch_article(url: str) -> str:
    try:
        r = requests.get(url, timeout=12, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        candidates = []
        selectors = [
            "article", "div[itemprop='articleBody']",
            ".article__content",".layout-article",".news-body",
            ".article","main",".lenta__text","[class*=content]"
        ]
        for sel in selectors:
            for el in soup.select(sel):
                txt = clean_html(el.get_text("\n"))
                if len(txt) > 200:
                    candidates.append(txt)
        base = ""
        if candidates:
            base = sorted(candidates, key=len, reverse=True)[0][:8000]
        else:
            base = clean_html(soup.get_text("\n"))[:5000]
        return drop_noise_lines(base)
    except:
        return ""

def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

def hsv_to_rgb(h,s,v):
    """h:0..360, s:0..1, v:0..1 → (r,g,b) 0..255"""
    import math
    h = h % 360
    c = v*s
    x = c*(1-abs((h/60)%2-1))
    m = v-c
    r,g,b = 0,0,0
    if   0<=h<60:   r,g,b = c,x,0
    elif 60<=h<120: r,g,b = x,c,0
    elif 120<=h<180:r,g,b = 0,c,x
    elif 180<=h<240:r,g,b = 0,x,c
    elif 240<=h<300:r,g,b = x,0,c
    else:           r,g,b = c,0,x
    return (int((r+m)*255),int((g+m)*255),int((b+m)*255))

def draw_header(title: str, source_domain: str, event_dt: datetime.datetime) -> bytes:
    # Нежный градиент: базовый тон + акцент в той же гамме
    seed = int(hashlib.sha1(title.encode("utf-8")).hexdigest(), 16)
    random.seed(seed)
    base_h = random.randint(200, 340)          # сиренево-сине-фиолетовый сектор
    c1 = hsv_to_rgb(base_h, 0.25, 0.95)
    c2 = hsv_to_rgb((base_h+25)%360, 0.35, 0.85)

    img = Image.new("RGB", (IMG_W, IMG_H), c1)
    grad = Image.new("RGB", (IMG_W, IMG_H), c2)
    mask = Image.linear_gradient("L").resize((IMG_W, IMG_H)).filter(ImageFilter.GaussianBlur(2))
    img = Image.composite(grad, img, mask)

    d = ImageDraw.Draw(img)
    # Мягкий акцент-наклон
    band_h = IMG_H//5
    d.polygon([(0, band_h), (IMG_W, band_h-20), (IMG_W, band_h+60), (0, band_h+80)], fill=(255,255,255,20))

    # Скругление верхних углов
    corner = Image.new("L", (40,40), 0)
    draw_c = ImageDraw.Draw(corner)
    draw_c.pieslice([0,0,80,80], 180, 270, fill=255)
    mask_r = Image.new("L", (IMG_W, IMG_H), 255)
    mask_r.paste(corner, (0,0))
    corner = corner.transpose(Image.FLIP_LEFT_RIGHT)
    mask_r.paste(corner, (IMG_W-40,0))
    img = Image.composite(img, Image.new("RGB",(IMG_W,IMG_H),(0,0,0)), mask_r)

    font_title = load_font(FONT_BOLD_PATH, 50)
    font_small = load_font(FONT_REGULAR_PATH, 24)
    font_brand = load_font(FONT_BOLD_PATH, 28)

    # Шапка слева: мини-лого + бренд
    d.ellipse((20,20,60,60), fill=(245,245,245))
    d.text((28,26), LOGO_EMOJI, font=load_font(FONT_REGULAR_PATH, 22), fill=(30,30,30))
    d.text((70,28), BRAND, font=font_brand, fill=(245,245,245))

    # Заголовок с переносами
    max_w = IMG_W - 120
    y = 140
    for line in wrap_text(d, title, font_title, max_w)[:4]:
        d.text((60, y), line, font=font_title, fill=(245,245,248))
        y += 56

    foot = f"source: {source_domain}  •  событие: {event_dt.strftime('%d.%m %H:%M')}"
    d.text((60, IMG_H-42), foot, font=font_small, fill=(235,235,240))

    out = BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()

def wrap_text(draw, text, font, max_width):
    words = text.split()
    line = ""
    lines = []
    for w in words:
        t = (line + " " + w).strip()
        bbox = draw.textbbox((0,0), t, font=font)
        if bbox[2]-bbox[0] <= max_width:
            line = t
        else:
            if line: lines.append(line)
            line = w
    if line: lines.append(line)
    return lines

def html_escape(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def build_caption(title: str, lead: str, body: str, source_url: str) -> str:
    def cap(s): 
        s = (s or "").strip()
        return s[:1].upper()+s[1:] if s else s

    title_h = f"<b>{html_escape(cap(title))}</b>"
    lead_h  = f"📰 {html_escape(cap(lead))}"
    # Подробности абзацами на 2–4 коротких блока
    body_sents = split_sentences(body)[:6]
    chunks = []
    chunk = []
    lim = 180
    for s in body_sents:
        if len(" ".join(chunk+[s])) <= lim:
            chunk.append(s)
        else:
            chunks.append(" ".join(chunk))
            chunk = [s]
    if chunk: chunks.append(" ".join(chunk))
    details = "\n\n".join(html_escape(cap(c)) for c in chunks[:3])

    src_h = f"<b>Источник:</b> <a href=\"{html_escape(source_url)}\">{html_escape(domain_of(source_url))}</a>"

    parts = [title_h, "", lead_h, "", "<b>Подробности:</b>", details, "", src_h]
    res = "\n".join([p for p in parts if p is not None])

    # лимит caption ~1024
    if len(res) > 1024:
        cut = 1024 - (len(res) - len(details)) - 20
        details = html_escape(details[:max(0,cut)].rsplit(" ",1)[0]) + "…"
        parts[5] = details
        res = "\n".join([p for p in parts if p is not None])
    return res

def send_photo(token: str, chat_id: str, photo_bytes: bytes, caption_html: str):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("header.jpg", photo_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption_html, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, data=data, files=files, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendPhoto: {r.status_code} {r.text}")

# -------------------- Основной поток --------------------

def gather_candidates():
    items = []
    for feed in FEEDS:
        try:
            fp = feedparser.parse(feed)
            for e in fp.entries[:12]:
                link = e.get("link") or e.get("id") or ""
                title = clean_html(e.get("title",""))
                if not link or not title:
                    continue
                published = e.get("published_parsed") or e.get("updated_parsed")
                if published:
                    dt = datetime.datetime.fromtimestamp(
                        time.mktime(published),
                        datetime.timezone.utc
                    ).astimezone(datetime.timezone(datetime.timedelta(hours=3)))
                else:
                    dt = now_msk()
                items.append((dt, title, link))
        except:
            continue
    items.sort(key=lambda x: x[0], reverse=True)
    return items

def main():
    assert BOT_TOKEN and CHANNEL_ID, "Укажи BOT_TOKEN и CHANNEL_ID в Secrets."
    posted = load_posted()

    for dt, title, link in gather_candidates():
        if link in posted:
            continue
        if (now_msk() - dt).total_seconds() > FRESH_MINUTES*60:
            continue

        raw = fetch_article(link)
        text = clean_html(raw)
        if len(text) < MIN_BODY_CHARS or cyr_ratio(text) < CYR_RATIO_MIN:
            continue

        title = clean_html(title)
        lead, body = pick_lead_and_body(text, title)
        if not lead or not body:
            continue

        photo = draw_header(title, domain_of(link), dt)
        caption = build_caption(title, lead, body, link)

        try:
            send_photo(BOT_TOKEN, CHANNEL_ID, photo, caption)
            posted.add(link)
            save_posted(posted)
            print("Posted:", title)
            return  # один качественный пост за запуск
        except Exception as ex:
            print("Ошибка публикации:", ex)
            continue

    print("Подходящих новостей нет — пропуск.")

if __name__ == "__main__":
    main()
