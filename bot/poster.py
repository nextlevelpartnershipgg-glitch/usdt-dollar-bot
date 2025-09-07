import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse, sys, random, re
from datetime import datetime, timezone, timedelta
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from zoneinfo import ZoneInfo

# ============ НАСТРОЙКИ ============
BOT_TOKEN  = os.environ.get("BOT_TOKEN")                   # ОБЯЗАТЕЛЬНО: GitHub Secrets
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@usdtdollarm")  # твой канал по умолчанию
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Zurich")   # твой часовой пояс

# Сколько постить за запуск (анти-спам)
MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))
# С какой давности брать новости (минут)
LOOKBACK_MINUTES  = int(os.environ.get("LOOKBACK_MINUTES", "90"))

# Источники
RSS_FEEDS = [
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",  # РБК
    "https://lenta.ru/rss/news",                          # Lenta.ru
    "https://www.gazeta.ru/export/rss/lenta.xml",         # Газета.ru
    "https://tass.ru/rss/v2.xml",                         # ТАСС
    "https://www.kommersant.ru/RSS/news.xml",             # Коммерсантъ
]

# Хэштеги
TAGS = "#новости #рынки #экономика #акции #usdt #доллар"

# Файл состояния (защита от повторов)
DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

UA  = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"}
UA_IMG = {"User-Agent":"Mozilla/5.0"}

# ===== УТИЛИТЫ =====
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def domain(url):
    return urllib.parse.urlparse(url).netloc.replace("www.", "") or "source"

def clean_html(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text(separator=" ").split())

def clamp(s, n):
    s = (s or "").strip()
    return (s if len(s) <= n else s[:n-1] + "…")

def make_caption(title, long_text, link, ctx_lines=None):
    title = clamp(title, 200)
    summary = clamp(long_text, 850)  # развёрнутая выжимка
    lines = [f"💵 {title}", f"{summary}"]
    if ctx_lines:
        lines += ["", "🧭 Контекст:"] + ctx_lines
    lines += ["", f"🔗 Источник: {link}", TAGS]
    cap = "\n".join(lines)
    # лимит Telegram ~1024
    if len(cap) > 1024:
        over = len(cap) - 1024 + 3
        summary2 = clamp(summary[:-over] if over < len(summary) else summary, 820)
        lines[1] = summary2
        cap = "\n".join(lines)
    return cap

# ---------- ФОН: Unsplash → Picsum → градиент ----------
def fetch_unsplash_image(query, w=1080, h=540, retries=3):
    for i in range(retries):
        try:
            seed = random.randint(0, 10_000_000)
            url = f"https://source.unsplash.com/{w}x{h}/?{urllib.parse.quote(query)}&sig={seed}"
            r = requests.get(url, headers=UA_IMG, timeout=25, allow_redirects=True)
            if r.status_code == 200:
                return Image.open(io.BytesIO(r.content)).convert("RGB")
            time.sleep(0.8 * (i+1))
        except Exception:
            time.sleep(0.8 * (i+1))
    return None

def fetch_picsum_image(w=1080, h=540):
    try:
        seed = random.randint(1, 10_000_000)
        url = f"https://picsum.photos/{w}/{h}?random={seed}"
        r = requests.get(url, headers=UA_IMG, timeout=20, allow_redirects=True)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None

def gradient_fallback(w=1080, h=540):
    top = (24, 26, 28); bottom = (10, 12, 14)
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        a = y/(h-1)
        r = int(top[0]*(1-a) + bottom[0]*a)
        g = int(top[1]*(1-a) + bottom[1]*a)
        b = int(top[2]*(1-a) + bottom[2]*a)
        draw.line([(0,y),(w,y)], fill=(r,g,b))
    return img

# ---------- ВЫДЕЛЕНИЕ КЛЮЧЕВОЙ ПЕРСОНЫ/ПРЕДМЕТА ДЛЯ ФОНА ----------
COMPANY_HINTS = [
    "Apple","Microsoft","Tesla","Meta","Google","Alphabet","Amazon","Nvidia","Samsung","Intel","Huawei",
    "Газпром","Сбербанк","Яндекс","Роснефть","Лукойл","Норникель","Татнефть","Новатэк","ВТБ"
]
TICKER_PAT = re.compile(r"\b[A-Z]{2,5}\b")  # USD, EUR, BTC, AAPL…

def extract_entities(title, summary):
    text = f"{title} {summary}".strip()
    # 1) Пары/тройки заглавных слов (имена/фамилии/бренды)
    cap_names = re.findall(r"(?:[A-ZА-ЯЁ][a-zа-яё]+(?:\s+[A-ZА-ЯЁ][a-zа-яё]+){0,2})", text)
    # 2) Тикеры/коды валют
    tickers = [m for m in TICKER_PAT.findall(text) if m not in ("NEWS","HTTP","HTTPS","HTML")]
    # 3) Явные компании
    companies = [c for c in COMPANY_HINTS if c.lower() in text.lower()]
    # Удалим совсем общие слова
    stop = {"The","This","That","Economy","Market","Index","Президент","Правительство","Россия","США"}
    cap_names = [x for x in cap_names if x not in stop and len(x) > 2]
    # Соберём приоритет: имена → компании → тикеры
    out = []
    out += cap_names[:3]
    out += companies[:3]
    out += tickers[:3]
    # fallback
    if not out:
        out = ["finance", "market"]
    return out

def build_photo_query(entities):
    # Если похоже на персону (две заглавные части) — портрет
    if entities:
        ent = entities[0]
        if len(ent.split()) >= 2 and all(w and w[0].isupper() for w in ent.split()):
            return f"portrait,{ent}"
    # Иначе предмет/бренд
    return ",".join(entities[:3])

def get_background(title, summary, w=1080, h=540):
    entities = extract_entities(title, summary)
    query = build_photo_query(entities)
    img = fetch_unsplash_image(query, w, h) or fetch_picsum_image(w, h) or gradient_fallback(w, h)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    img = ImageEnhance.Brightness(img).enhance(0.9)
    return img, query

# ---------- КАРТОЧКА 1080x540 (ТОЛЬКО ЗАГОЛОВОК) ----------
def draw_card_title_only(title_text, src_domain, tzname, query_used):
    W, H = 1080, 540
    # для читаемости подложим лёгкую тень-плашку
    bg, _ = get_background(title_text, "", W, H)
    d = ImageDraw.Draw(bg)

    # затемняем широкую центральную область
    overlay = Image.new("RGBA", (W, H), (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([40, 110, W-40, H-90], radius=28, fill=(0,0,0,110))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(bg)

    # Шрифты
    font_brand  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    font_time   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    font_title  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 58)
    font_small  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)

    # Верх: бренд + время
    brand = "USDT=Dollar"
    d.text((48, 26), brand, fill=(255,255,255), font=font_brand)
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d.%m %H:%M")
    d.text((W - 48 - d.textlength(now_str, font=font_time), 26), now_str, fill=(255,255,255), font=font_time)

    # Заголовок (только он)
    margin_x = 72
    y = 150
    for line in textwrap.wrap((title_text or "").strip(), width=28)[:4]:
        d.text((margin_x, y), line, font=font_title, fill=(255,255,255))
        y += 66

    # Низ: источник + подсказка запроса (чтобы понять, что за фон подобрался)
    src = f"source: {src_domain}"
    d.text((72, H - 58), src, font=font_small, fill=(225,225,225))
    # можно тихо вывести query_used (закомментируй, если не нужно)
    # d.text((W - 72 - d.textlength(query_used, font=font_small), H - 58), query_used, font=font_small, fill=(200,200,200))

    bio = io.BytesIO()
    bg.save(bio, format="PNG", optimize=True)
    bio.seek(0)
    return bio

def send_photo(photo_bytes, caption):
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN. Добавь секрет в GitHub: Settings → Secrets → Actions → BOT_TOKEN")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption}
    r = requests.post(url, files=files, data=data, timeout=30)
    print("Telegram status:", r.status_code, r.text[:200])
    r.raise_for_status()
    return r.json()

# ---------- ТЕКСТ СО СТРАНИЦЫ ----------
def fetch_article_text(url, max_chars=2000):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        ps = soup.find_all("p")
        chunks = []
        for p in ps:
            t = p.get_text(" ", strip=True)
            if not t: continue
            if len(t) < 60: continue
            if any(x in t.lower() for x in ["javascript","cookie","подпишитесь","реклама","cookies"]):
                continue
            chunks.append(t)
            if sum(len(c) for c in chunks) > max_chars:
                break
        text = " ".join(chunks)
        text = re.sub(r"\s+", " ", text).strip()
        return text
    except Exception:
        return ""

def expanded_summary(feed_summary, article_text, limit=900):
    base = (article_text or "").strip() or (feed_summary or "").strip()
    sents = re.split(r"(?<=[.!?])\s+", base)
    out = " ".join(sents[:5]).strip()
    return clamp(out, limit)

# ---------- СТРАНА + ГОС.ЛИЦА ----------
COUNTRIES = [
    ("Россия", ["россия","рф","москва","рубл","пути","россий"], "Q159"),
    ("США", ["сша","соединенные шт","washington","байден","доллар","фрс","белый дом"], "Q30"),
    ("Китай", ["китай","кнр","пекин","си цзиньпин","шаньхай","yuan","cny"], "Q148"),
    ("Украина", ["украин","киев","kyiv","зеленск","гривн","uah"], "Q212"),
    ("Германия", ["герман","берлин","scholz","евро","bundes"], "Q183"),
    ("Франция", ["франц","париж","макрон","euro","elysee"], "Q142"),
    ("Великобритания", ["британи","британ","лондон","великобрит","uk","king charles","премьер"], "Q145"),
    ("Италия", ["итал","рим","meloni","euro","итальян"], "Q38"),
    ("Испания", ["испан","мадрид","sanchez","euro","ибери"], "Q29"),
    ("Япония", ["япони","токио","yen","jpy","kishida"], "Q17"),
    ("Индия", ["индия","нью-дели","rupee","modi","inr"], "Q668"),
    ("Турция", ["турци","анкара","эрдоган","lira","try"], "Q43"),
    ("Польша", ["польш","варшава","zl","pln","tusk"], "Q36"),
    ("Беларусь", ["беларус","минск","лукашенк","byn","белорус"], "Q184"),
    ("Казахстан", ["казахстан","астан","тенге","kzt","токаев"], "Q232"),
    ("Иран", ["иран","тегеран","rial","irn","раиси","хамене"], "Q794"),
    ("Израиль", ["израил","тель-авив","нетаньяху","шекел","ils"], "Q801"),
    ("ОАЭ", ["оаэ","эмират","абу-даби","дубай","aed","dirham"], "Q878"),
    ("Саудовская Аравия", ["сауд","риад","sar","saudi","мбс"], "Q851"),
    ("Канада", ["канада","оттава","cad","trudeau"], "Q16"),
    ("Бразилия", ["бразил","рио","реал","lula","sao paulo"], "Q155"),
    ("Мексика", ["мексик","песо","mxn","обрадор","lopez obrador"], "Q96"),
]

def detect_country(text):
    t = (text or "").lower()
    for name, keys, qid in COUNTRIES:
        if any(k in t for k in keys):
            return {"name": name, "qid": qid}
    return None

def wikidata_officials(qid):
    """Возвращает (head_of_state, head_of_gov). Без падения при ошибках."""
    try:
        query = f"""
        SELECT ?hosLabel ?hogLabel WHERE {{
          OPTIONAL {{ wd:{qid} wdt:P35 ?hos. }}
          OPTIONAL {{ wd:{qid} wdt:P6  ?hog. }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru,en". }}
        }}
        """
        r = requests.get(
            "https://query.wikidata.org/sparql",
            params={"query": query, "format": "json"},
            headers={"Accept":"application/sparql-results+json","User-Agent":"usdtdollar-bot/1.0"},
            timeout=15
        )
        if r.status_code != 200:
            return (None, None)
        data = r.json()
        hos, hog = None, None
        for b in data.get("results", {}).get("bindings", []):
            if "hosLabel" in b and not hos:
                hos = b["hosLabel"]["value"]
            if "hogLabel" in b and not hog:
                hog = b["hogLabel"]["value"]
        return (hos, hog)
    except Exception:
        return (None, None)

# ---------- СБОР И ПУБЛИКАЦИЯ ----------
def collect_entries():
    items = []
    for feed_url in RSS_FEEDS:
        fp = feedparser.parse(feed_url)
        for e in fp.entries or []:
            link = getattr(e, "link", "") or ""
            title = (getattr(e, "title", "") or "").strip()
            summary = clean_html(getattr(e, "summary", getattr(e, "description", "")))
            ts = getattr(e, "published", getattr(e, "updated", "")) or ""
            try:
                dt = dtparse.parse(ts)
                if not dt.tzinfo:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                dt = datetime(1970,1,1, tzinfo=timezone.utc)
            uid = hashlib.sha256((link + "|" + title + "|" + ts).encode("utf-8")).hexdigest()
            items.append({
                "feed": feed_url,
                "link": link,
                "title": title or "(no title)",
                "summary": summary,
                "ts": ts,
                "dt": dt,
                "uid": uid,
            })
    return items

def process_item(link, title, feed_summary):
    # развёрнутый текст из статьи
    article_text = fetch_article_text(link, max_chars=2000)
    long_summary = expanded_summary(feed_summary, article_text, limit=900)

    # Контекст: страна и лидеры
    country_info = detect_country(f"{title} {feed_summary} {article_text}")
    ctx_lines = []
    if country_info:
        hos, hog = wikidata_officials(country_info["qid"])
        ctx_lines.append(f"🗺️ Страна: {country_info['name']}")
        if hos: ctx_lines.append(f"👤 Глава государства: {hos}")
        if hog: ctx_lines.append(f"👤 Глава правительства: {hog}")

    # Подпись (подробно), картинка (только заголовок)
    cap  = make_caption(title, long_summary, link or "", ctx_lines=ctx_lines)

    # Для фона постараемся взять ключевую персону/предмет
    entities = extract_entities(title, long_summary)
    query = build_photo_query(entities)
    # перерисуем фон по query и наложим ТОЛЬКО заголовок
    # (используем draw_card_title_only, который внутри также затемняет)
    # Подменим фон: создадим вручную, чтобы точно учёлся новый query
    bg_img = fetch_unsplash_image(query, 1080, 540) or fetch_picsum_image(1080, 540) or gradient_fallback(1080, 540)
    bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=0.5))
    bg_img = ImageEnhance.Brightness(bg_img).enhance(0.9)

    # Нарисуем заголовок на этом фоне
    def finalize_card(image):
        W, H = 1080, 540
        img = image.resize((W,H))
        overlay = Image.new("RGBA", (W, H), (0,0,0,0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle([40, 110, W-40, H-90], radius=28, fill=(0,0,0,110))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        d = ImageDraw.Draw(img)
        font_brand  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
        font_time   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
        font_title  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 58)
        font_small  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        brand = "USDT=Dollar"
        d.text((48, 26), brand, fill=(255,255,255), font=font_brand)
        try:
            tz = ZoneInfo(TIMEZONE)
        except Exception:
            tz = ZoneInfo("UTC")
        now_str = datetime.now(tz).strftime("%d.%m %H:%M")
        d.text((W - 48 - d.textlength(now_str, font=font_time), 26), now_str, fill=(255,255,255), font=font_time)
        # Заголовок
        margin_x = 72
        y = 150
        for line in textwrap.wrap((title or "").strip(), width=28)[:4]:
            d.text((margin_x, y), line, font=font_title, fill=(255,255,255))
            y += 66
        # Низ: домен
        src = f"source: {domain(link or '')}"
        d.text((72, H - 58), src, font=font_small, fill=(225,225,225))
        bio = io.BytesIO()
        img.save(bio, format="PNG", optimize=True)
        bio.seek(0)
        return bio

    card = finalize_card(bg_img)
    resp = send_photo(card, cap)
    print("Posted:", (title or "")[:80], "→", resp.get("ok", True), "| query:", query)

def trim_posted(posted_set, keep_last=600):
    if len(posted_set) <= keep_last:
        return posted_set
    return set(list(posted_set)[-keep_last:])

def main():
    state = load_state()
    posted = set(state.get("posted_uids", []))

    items = collect_entries()
    if not items:
        print("No entries found.")
        return

    now = datetime.now(timezone.utc)
    lookback_dt = now - timedelta(minutes=LOOKBACK_MINUTES)
    fresh = [it for it in items if it["dt"] >= lookback_dt and it["uid"] not in posted]

    fresh.sort(key=lambda x: x["dt"], reverse=True)
    to_post = fresh[:MAX_POSTS_PER_RUN]

    if not to_post:
        print("Nothing new to post within lookback window.")
        return

    for it in to_post:
        try:
            process_item(it["link"], it["title"], it["summary"])
            posted.add(it["uid"])
            time.sleep(1.0)
        except Exception as e:
            print("Error sending:", e)

    state["posted_uids"] = list(trim_posted(posted))
    save_state(state)

if __name__ == "__main__":
    main()
