import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse, sys, random, re
from datetime import datetime, timezone, timedelta
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from zoneinfo import ZoneInfo

# ============ НАСТРОЙКИ ============
BOT_TOKEN  = os.environ.get("BOT_TOKEN")                   # GitHub Secrets
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@usdtdollarm")  # по умолчанию твой канал
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Zurich")

MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))
LOOKBACK_MINUTES  = int(os.environ.get("LOOKBACK_MINUTES", "90"))

# RSS-источники: Россия/мир/крипта (можно добавлять/убирать)
RSS_FEEDS = [
    # Россия/СНГ — экономика/финансы
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",             # РБК (общая лента)
    "https://www.kommersant.ru/RSS/news.xml",                        # Коммерсант
    "https://lenta.ru/rss/news",                                     # Lenta
    "https://tass.ru/rss/v2.xml",                                    # ТАСС

    # Мировые рынки (общеизвестные публичные ленты/зеркала)
    "https://feeds.reuters.com/reuters/businessNews",                # Reuters Business
    "https://www.bloomberg.com/feeds/podcasts/etf_report.xml",       # Bloomberg (доступный feed; заголовки/описания)
    "https://www.ft.com/?format=rss",                                # FT общий RSS (через агрегатор FT)

    # Крипта
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://cointelegraph.com/rss",                                 # Cointelegraph
    "https://forklog.com/news/feed",                                 # Forklog (RU)
]

TAGS = "#новости #рынки #акции #облигации #валюта #crypto #usdt #доллар"

DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

UA  = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"}
UA_IMG = {"User-Agent":"Mozilla/5.0"}

# ============ УТИЛИТЫ ============
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
    return s if len(s) <= n else s[:n-1] + "…"

# ============ ФОРМИРОВАНИЕ CAPTION (единый стиль) ============
def summarize_two_level(feed_summary, article_text):
    """
    Возвращает (short, details):
    - short: 1–2 предложения (кратко)
    - details: 3–5 предложений (детали)
    """
    base = (article_text or "").strip() or (feed_summary or "").strip()
    base = re.sub(r"\s+", " ", base).strip()
    if not base:
        return ("", "")

    sents = re.split(r"(?<=[.!?])\s+", base)
    short = " ".join(sents[:2]).strip()
    details = " ".join(sents[2:7]).strip()
    return (clamp(short, 280), clamp(details, 650))

def build_caption(title, short, details, link):
    title = clamp(title, 200)
    short = short or ""
    details = details or ""
    chunks = [f"🔹 Коротко: {short}"] if short else []
    if details:
        chunks += [f"🔸 Детали: {details}"]
    chunks += ["", f"Источник: {link}", TAGS]
    cap = f"{title}\n\n" + "\n".join(chunks)
    # лимит подписи Telegram ~1024
    if len(cap) > 1024:
        extra = len(cap) - 1024 + 3
        # режем сначала details
        if details and extra < len(details):
            details = clamp(details[:-extra], 600)
        else:
            details = clamp(details, 600)
        chunks = [f"🔹 Коротко: {short}"] if short else []
        if details:
            chunks += [f"🔸 Детали: {details}"]
        chunks += ["", f"Источник: {link}", TAGS]
        cap = f"{title}\n\n" + "\n".join(chunks)
    return cap

# ============ ФОН (персона/предмет) ============
COMPANY_HINTS = [
    "Apple","Microsoft","Tesla","Meta","Google","Alphabet","Amazon","Nvidia","Samsung","Intel","Huawei",
    "Газпром","Сбербанк","Яндекс","Роснефть","Лукойл","Норникель","Татнефть","Новатэк","ВТБ","Сургутнефтегаз"
]
TICKER_PAT = re.compile(r"\b[A-Z]{2,6}\b")

def extract_entities(title, summary):
    text = f"{title} {summary}".strip()
    names = re.findall(r"(?:[A-ZА-ЯЁ][a-zа-яё]+(?:\s+[A-ZА-ЯЁ][a-zа-яё]+){0,2})", text)
    tickers = [m for m in TICKER_PAT.findall(text) if m not in ("NEWS","HTTP","HTTPS","HTML")]
    companies = [c for c in COMPANY_HINTS if c.lower() in text.lower()]
    stop = {"The","This","Президент","Правительство","Россия","США","Луна"}
    names = [x for x in names if x not in stop and len(x) > 2]
    out = []
    out += names[:3]; out += companies[:3]; out += tickers[:3]
    return out or ["finance","market"]

def build_photo_query(entities):
    ent = entities[0] if entities else ""
    if ent and len(ent.split()) >= 2 and all(w and w[0].isupper() for w in ent.split()):
        return f"portrait,{ent}"
    return ",".join(entities[:3])

def fetch_unsplash_image(query, w=1080, h=540, retries=3):
    for i in range(retries):
        try:
            seed = random.randint(0, 10_000_000)
            url = f"https://source.unsplash.com/{w}x{h}/?{urllib.parse.quote(query)}&sig={seed}"
            r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=25, allow_redirects=True)
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
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=20, allow_redirects=True)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None

def gradient_fallback(w=1080, h=540):
    top=(24,26,28); bottom=(10,12,14)
    img=Image.new("RGB",(w,h)); d=ImageDraw.Draw(img)
    for y in range(h):
        a=y/(h-1); r=int(top[0]*(1-a)+bottom[0]*a); g=int(top[1]*(1-a)+bottom[1]*a); b=int(top[2]*(1-a)+bottom[2]*a)
        d.line([(0,y),(w,y)], fill=(r,g,b))
    return img

def get_background(title, summary, w=1080, h=540):
    entities = extract_entities(title, summary)
    query = build_photo_query(entities)
    img = fetch_unsplash_image(query, w, h) or fetch_picsum_image(w, h) or gradient_fallback(w, h)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    img = ImageEnhance.Brightness(img).enhance(0.9)
    return img

# ============ РИСУЕМ КАРТОЧКУ: ТОЛЬКО ЗАГОЛОВОК ============
def fit_title_in_box(draw, text, font_path, box_w, box_h, max_size=64, min_size=34, line_width=28, line_gap=8):
    for size in range(max_size, min_size-1, -2):
        font = ImageFont.truetype(font_path, size)
        lines = textwrap.wrap(text, width=line_width)
        height = 0
        clipped = []
        for i, ln in enumerate(lines):
            if i >= 4: break
            w = draw.textlength(ln, font=font)
            if w > box_w:
                ln = clamp(ln, int(len(ln) * box_w / (w+1)))
            clipped.append(ln)
            height += font.getbbox("Ag")[3] + line_gap
        if height <= box_h:
            return font, clipped
    font = ImageFont.truetype(font_path, min_size)
    lines = textwrap.wrap(text, width=line_width)[:4]
    if len(lines) == 4:
        lines[-1] = clamp(lines[-1], max(6, len(lines[-1])-3))
    return font, lines

def draw_title_card(title_text, src_domain, tzname):
    W, H = 1080, 540
    bg = get_background(title_text, "", W, H)
    overlay = Image.new("RGBA", (W, H), (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([40, 110, W-40, H-90], radius=28, fill=(0,0,0,110))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(bg)

    path_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    path_reg  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    font_brand  = ImageFont.truetype(path_bold, 34)
    font_time   = ImageFont.truetype(path_reg, 26)
    font_small  = ImageFont.truetype(path_reg, 22)

    brand = "USDT=Dollar"
    d.text((48, 26), brand, fill=(255,255,255), font=font_brand)
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d.%m %H:%M")
    d.text((W - 48 - d.textlength(now_str, font=font_time), 26), now_str, fill=(255,255,255), font=font_time)

    box_x, box_y = 72, 150
    box_w, box_h = W - 2*box_x, H - box_y - 110
    font_title, lines = fit_title_in_box(d, (title_text or "").strip(), path_bold, box_w, box_h)

    y = box_y
    for ln in lines:
        d.text((box_x, y), ln, font=font_title, fill=(255,255,255))
        y += font_title.getbbox("Ag")[3] + 8

    src = f"source: {src_domain}"
    d.text((72, H - 58), src, font=font_small, fill=(225,225,225))

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

# ============ ТЕКСТ СО СТРАНИЦЫ ============
def fetch_article_text(url, max_chars=2000):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200:
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        # простая эвристика: нормальные абзацы
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

# ============ СБОР И ПУБЛИКАЦИЯ ============
def collect_entries():
    items = []
    for feed_url in RSS_FEEDS:
        try:
            fp = feedparser.parse(feed_url)
        except Exception:
            continue
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
                "feed": feed_url, "link": link, "title": title or "(no title)",
                "summary": summary, "ts": ts, "dt": dt, "uid": uid,
            })
    return items

def process_item(link, title, feed_summary):
    article_text = fetch_article_text(link, max_chars=2200)
    short, details = summarize_two_level(feed_summary, article_text)
    caption = build_caption(title, short, details, link or "")
    card = draw_title_card(title, domain(link or ""), TIMEZONE)
    resp = send_photo(card, caption)
    print("Posted:", (title or "")[:80], "→", resp.get("ok", True))

def trim_posted(posted_set, keep_last=600):
    if len(posted_set) <= keep_last: return posted_set
    return set(list(posted_set)[-keep_last:])

def main():
    state = load_state()
    posted = set(state.get("posted_uids", []))

    items = collect_entries()
    if not items:
        print("No entries found."); return

    now = datetime.now(timezone.utc)
    lookback_dt = now - timedelta(minutes=LOOKBACK_MINUTES)
    fresh = [it for it in items if it["dt"] >= lookback_dt and it["uid"] not in posted]

    fresh.sort(key=lambda x: x["dt"], reverse=True)
    to_post = fresh[:MAX_POSTS_PER_RUN]
    if not to_post:
        print("Nothing new to post within lookback window."); return

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
