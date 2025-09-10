# -*- coding: utf-8 -*-
"""
USDT=Dollar — авто-постер новостей (RU)
- RU-источники RSS
- парсинг статьи, мягкий рерайт без искажения фактов
- отрисовка шапки (градиент + лого + футер)
- HTML-капшен с тайтлом, лидом, подробностями, источником и тегами (в спойлере)
- антидубль, фильтры качества, кириллица, длинна
"""

import os, re, json, time, html, math, hashlib, random, textwrap, datetime
from urllib.parse import urlparse
import requests
import feedparser

from bs4 import BeautifulSoup  # bs4 указан в requirements
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# -------------------- Конфигурация --------------------

# Источники (только русскоязычные)
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

# Секреты
BOT_TOKEN  = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # пример: @usdtdollarm

# Лимиты и фильтры
MIN_BODY_CHARS     = 400        # минимальная длина текста для поста
CYR_RATIO_MIN      = 0.5        # доля кириллицы в тексте
FRESH_MINUTES      = 90         # окно свежести новости
POSTED_PATH        = "data/posted.json"

# Шрифты
FONT_REGULAR_PATH  = "data/DejaVuSans.ttf"
FONT_BOLD_PATH     = "data/DejaVuSans-Bold.ttf"

# Канальный брендинг
CHANNEL_NAME_SHORT = "USDT=Dollar"   # подпись в шапке
LOGO_EMOJI         = "💠"            # кружок-логотип (рисуем в шапке)
IMG_W, IMG_H       = 1024, 512       # шапка

# -------------------- Утилиты --------------------

def now_msk():
    tz = datetime.timezone(datetime.timedelta(hours=3))
    return datetime.datetime.now(tz)

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

def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.replace("www.","")
    except:
        return ""

def clean_html(txt: str) -> str:
    if not txt: return ""
    # HTML -> текст
    txt = html.unescape(txt)
    # Убираем теги
    txt = re.sub(r"<\s*br\s*/?>", "\n", txt, flags=re.I)
    txt = re.sub(r"<[^>]+>", " ", txt)
    # Многопробелы/переносы
    txt = re.sub(r"[ \t]+", " ", txt)
    txt = re.sub(r"\s*\n\s*", "\n", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    # Пробел перед пунктуацией
    txt = re.sub(r"\s+([,.;:!?])", r"\1", txt)
    # Нормализация тире, кавычек
    txt = txt.replace(" - ", " — ")
    txt = txt.replace("\"", "«").replace("««", "«").replace("»»", "»").replace("»«", "» «")
    # Слитности типа «участникиопроса»
    txt = re.sub(r"([а-яё])([А-ЯЁ])", r"\1 \2", txt)
    return txt.strip()

def cyr_ratio(txt: str) -> float:
    if not txt: return 0.0
    total = len([ch for ch in txt if ch.isalpha()])
    if total == 0: return 0.0
    cyr = len(re.findall(r"[а-яёА-ЯЁ]", txt))
    return cyr / total

def soft_rewrite(sent: str) -> str:
    """Очень мягкий рерайт (замены канцеляризмов, вводных, порядок частей).
       Никаких новых фактов/чисел/имен!"""
    if not sent: return ""
    s = sent

    # Вводные
    repls = {
        "сообщил ": "заявил ",
        "сообщила ": "заявила ",
        "сообщили ": "заявили ",
        "сообщается, что": "уточняется, что",
        "отметил ": "подчеркнул ",
        "отметила ": "подчеркнула ",
        "отметили ": "подчеркнули ",
        "по данным ": "по информации ",
        "в своем сообщении": "в публикации",
        "в своем заявлении": "в заявлении",
    }
    for k,v in repls.items():
        s = re.sub(r"\b"+re.escape(k)+r"\b", v, s, flags=re.IGNORECASE)

    # Лёгкая перестановка: «X, сообщил Y.» → «Как заявил Y, X.»
    m = re.search(r"(?P<body>.+?),\s*(сообщил|сообщила|сообщили|заявил|заявила|заявили)\s+(?P<who>[^.]+)\.$", s, flags=re.I)
    if m and len(m.group("body"))>40:
        verb = "Как " + ("заявил" if re.search(r"(заявил|сообщил)", m.group(0), re.I) else "заявили")
        s = f"{verb} {m.group('who')}, {m.group('body')}."

    # Капитализация первого символа
    s = s[:1].upper() + s[1:] if s else s
    return s

def split_sentences(text: str) -> list:
    # простая разрезка (без тяжелого nltk)
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    parts = [p.strip() for p in parts if p.strip()]
    return parts

def pick_lead_and_body(full: str) -> tuple[str, str]:
    sents = split_sentences(full)
    if not sents:
        return "", ""
    lead = sents[0]
    body = " ".join(sents[1:]) if len(sents) > 1 else ""
    # мягкий рерайт
    lead = soft_rewrite(lead)
    body_sents = [soft_rewrite(x) for x in split_sentences(body)]
    # убрать прямые дубли
    body_sents = [x for x in body_sents if x.lower() != lead.lower()]
    body = " ".join(body_sents)
    return lead, body

def keywords_to_tags(title: str, body: str, k: int = 5) -> list:
    text = (title + " " + body).lower()
    # только кириллица + дефис
    words = re.findall(r"[а-яё\-]{4,}", text)
    stop = set("это такой также также-то может чтобы после перед между через всего более около тогда очень быть были было было бы либо типа вроде лишь уже ещё еще или при без для над под про как чем чем-то чем-либо что чтобы кого чего куда когда где какая какие каких каких-то каких-либо почему зато зато-то либо-то".split())
    freq = {}
    for w in words:
        if w in stop: continue
        freq[w] = freq.get(w, 0) + 1
    # топ по частотности и длине
    cand = sorted(freq.items(), key=lambda x: (x[1], len(x[0])), reverse=True)
    tags = []
    for w,_ in cand:
        if len(tags) >= k: break
        if all(w not in t and t not in w for t in tags):
            tags.append("#"+w.replace("—","-").replace("–","-"))
    return tags

def draw_header(title: str, source_domain: str, event_dt: datetime.datetime) -> bytes:
    """Шапка: градиент + скошенная маска + логотип + футер + заголовок с переносами"""
    # Градиент по хэшу заголовка
    h = int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16)
    random.seed(h)
    def rnd(): return random.randint(90, 200)
    c1 = (rnd(), rnd(), 255 - rnd()//2)
    c2 = (255 - rnd()//3, rnd(), rnd())

    img = Image.new("RGB", (IMG_W, IMG_H), c1)
    grad = Image.new("RGB", (IMG_W, IMG_H), c2)
    mask = Image.linear_gradient("L").resize((IMG_W, IMG_H)).filter(ImageFilter.GaussianBlur(2))
    img = Image.composite(grad, img, mask)

    # Скошенная плашка
    d = ImageDraw.Draw(img)
    angle_h = IMG_H//5
    d.polygon([(0,0),(IMG_W,0),(IMG_W,angle_h),(0,angle_h+40)], fill=(0,0,0,70))

    # Шрифты
    def load_font(path, size):
        try:
            return ImageFont.truetype(path, size)
        except:
            return ImageFont.load_default()
    font_b = load_font(FONT_BOLD_PATH, 46)
    font_r = load_font(FONT_REGULAR_PATH, 24)
    font_m = load_font(FONT_BOLD_PATH, 28)

    # Логотип
    d.ellipse((24,24,72,72), fill=(255,255,255,220))
    d.text((34,30), LOGO_EMOJI, font=load_font(FONT_REGULAR_PATH, 28), fill=(30,30,30))
    d.text((90,38), CHANNEL_NAME_SHORT, font=font_m, fill=(240,240,245))

    # Заголовок — переносы
    max_w = IMG_W - 120
    lines = []
    for para in title.split("\n"):
        acc = ""
        for w in para.split():
            t = (acc + " " + w).strip()
            bbox = d.textbbox((0,0), t, font=font_b)
            if bbox[2] - bbox[0] <= max_w:
                acc = t
            else:
                if acc: lines.append(acc)
                acc = w
        if acc: lines.append(acc)
    # Рисуем заголовок
    y = 170
    for line in lines[:4]:
        d.text((60,y), line, font=font_b, fill=(245,245,248))
        y += 56

    # Футер
    foot = f"source: {source_domain}  •  событие: {event_dt.strftime('%d.%m %H:%M')}"
    d.text((60, IMG_H-42), foot, font=font_r, fill=(230,230,235))

    # В байты
    out = requests.compat.BytesIO()
    img.save(out, format="JPEG", quality=92)
    return out.getvalue()

def fetch_article(url: str) -> str:
    """Тянем страницу и пробуем достать основной текст."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        # простые эвристики по блокам
        cand = []
        for sel in ["article","div[itemprop='articleBody']",".article__content",".layout-article",".news-body",".article","main",".lenta__text","[class*=content]"]:
            for el in soup.select(sel):
                txt = clean_html(el.get_text("\n"))
                if len(txt) > 200:
                    cand.append(txt)
        if not cand:
            txt = clean_html(soup.get_text("\n"))
            return txt[:4000]
        # самая длинная версия
        return sorted(cand, key=len, reverse=True)[0][:6000]
    except:
        return ""

def html_escape(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def build_caption(title: str, lead: str, body: str, source_url: str, tags: list) -> str:
    # Заголовок (жирный), лид и детали (каждый с большой буквы)
    def cap(s): 
        s = s.strip()
        return s[:1].upper()+s[1:] if s else s
    title_h = f"<b>{html_escape(cap(title))}</b>"
    lead_h  = f"📰 {html_escape(cap(lead))}"
    body_h  = html_escape(cap(body))

    # Источник — прямая ссылка
    src_h   = f"<b>Источник:</b> <a href=\"{html_escape(source_url)}\">{html_escape(domain_of(source_url))}</a>"

    # Теги — спойлеры (кликабельность появится после раскрытия)
    if tags:
        tags_sp = " ".join([f"<span class=\"tg-spoiler\">{html_escape(t)}</span>" for t in tags[:5]])
        tags_h = f"\n\n{tags_sp}"
    else:
        tags_h = ""

    # Финальный капшен
    parts = [
        title_h, "",  # пустая строка после заголовка
        lead_h,
        "", "<b>Подробности:</b>",
        body_h,
        "", src_h,
        tags_h
    ]
    res = "\n".join([p for p in parts if p is not None])
    # Ограничение Telegram ~1024 символа для caption фото — стремимся уложиться
    if len(res) > 1024:
        # сокращаем body
        cut = 1024 - (len(res) - len(body_h)) - 20
        body_h = html_escape(body_h[:max(0,cut)].rsplit(" ",1)[0]) + "…"
        parts[5] = body_h
        res = "\n".join([p for p in parts if p is not None])
    return res

def send_photo(token: str, chat_id: str, photo_bytes: bytes, caption_html: str):
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("header.jpg", photo_bytes, "image/jpeg")}
    data = {"chat_id": chat_id, "caption": caption_html, "parse_mode": "HTML", "disable_web_page_preview": True}
    r = requests.post(url, data=data, files=files, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendPhoto: {r.status_code} {r.text}")

# -------------------- Главная логика --------------------

def pick_best_item():
    """Собираем ленты, выбираем первую подходящую новость."""
    items = []
    for feed in FEEDS:
        try:
            fp = feedparser.parse(feed)
            for e in fp.entries[:12]:
                link = e.get("link") or e.get("id") or ""
                title = clean_html(e.get("title",""))
                if not link or not title: 
                    continue
                # время
                published_parsed = e.get("published_parsed") or e.get("updated_parsed")
                if published_parsed:
                    dt = datetime.datetime.fromtimestamp(time.mktime(published_parsed), datetime.timezone.utc).astimezone(datetime.timezone(datetime.timedelta(hours=3)))
                else:
                    dt = now_msk()
                items.append((dt, title, link))
        except:
            continue
    # Свежее сначала
    items.sort(key=lambda x: x[0], reverse=True)
    return items

def main():
    assert BOT_TOKEN and CHANNEL_ID, "Не заданы BOT_TOKEN / CHANNEL_ID (Secrets в GitHub)"
    posted = load_posted()

    for dt, title, link in pick_best_item():
        if link in posted:
            continue
        # окно свежести
        if (now_msk() - dt).total_seconds() > FRESH_MINUTES*60:
            continue

        # Текст статьи
        raw = fetch_article(link)
        text = clean_html(raw)
        # Быстрая защита от «пустых»/очень коротких новостей
        if len(text) < MIN_BODY_CHARS or cyr_ratio(text) < CYR_RATIO_MIN:
            continue

        # Заголовок тоже чистим
        title = clean_html(title)

        # Лид + подробности
        lead, body = pick_lead_and_body(text)
        if not lead or not body or (lead.lower() in body.lower()):
            # если всё равно «сухо» — пробуем взять 2–3 первых предложения в body
            sents = split_sentences(text)
            if len(sents) >= 3:
                lead = soft_rewrite(sents[0])
                body = " ".join([soft_rewrite(x) for x in sents[1:4]])
            else:
                continue

        # Теги
        tags = keywords_to_tags(title, body, k=5)

        # Шапка
        photo = draw_header(title, domain_of(link), dt)

        # Капшен
        caption = build_caption(title, lead, body, link, tags)

        # Отправка
        try:
            send_photo(BOT_TOKEN, CHANNEL_ID, photo, caption)
            posted.add(link)
            save_posted(posted)
            print(f"Posted: {title}")
            return  # публикуем одну новость за запуск
        except Exception as ex:
            print("Ошибка публикации:", ex)
            # пробуем следующую
            continue

    print("Подходящих новостей нет — пропуск.")

if __name__ == "__main__":
    main()
