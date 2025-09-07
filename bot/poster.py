import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse, sys, random, re
from datetime import datetime, timezone, timedelta
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from zoneinfo import ZoneInfo

# ============ НАСТРОЙКИ ============
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@usdtdollarm")
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Zurich")

CHANNEL_NAME   = os.environ.get("CHANNEL_NAME", "USDT=Dollar")
CHANNEL_HANDLE = os.environ.get("CHANNEL_HANDLE", "@usdtdollarm")
CHANNEL_LINK   = os.environ.get("CHANNEL_LINK", f"https://t.me/{CHANNEL_HANDLE.lstrip('@')}")

MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))
LOOKBACK_MINUTES  = int(os.environ.get("LOOKBACK_MINUTES", "90"))

# RSS-источники
RSS_FEEDS = [
    # Россия/СНГ
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://lenta.ru/rss/news",
    "https://tass.ru/rss/v2.xml",
    # Мир
    "https://feeds.reuters.com/reuters/businessNews",
    "https://www.bloomberg.com/feeds/podcasts/etf_report.xml",
    "https://www.ft.com/?format=rss",
    # Крипта
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://cointelegraph.com/rss",
    "https://forklog.com/news/feed",
]

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

def root_domain(url):
    try:
        dom = urllib.parse.urlparse(url).netloc.replace("www.","")
        parts = dom.split(".")
        if len(parts) > 2:
            dom = ".".join(parts[-2:])
        return dom
    except Exception:
        return "источник"

def clean_html(html):
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text(separator=" ").split())

def clamp(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n-1] + "…"

def norm(s):
    return re.sub(r"\s+", " ", (s or "").strip().lower())

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

# ============ КАРТОЧКА: ТОЛЬКО ЗАГОЛОВОК ============
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

    d.text((48, 26), CHANNEL_NAME, fill=(255,255,255), font=font_brand)
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

# ============ ТЕКСТ СО СТРАНИЦЫ ============
def fetch_article_text(url, max_chars=2400):
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

# ============ ПАРАФРАЗ И 3 АБЗАЦА (анти-дубликат) ============
SYN_REPLACE = [
    (r"\bсообщает\b", "передаёт"),
    (r"\bсообщили\b", "уточнили"),
    (r"\bзаявил(а|и)?\b", "отметил\\1"),
    (r"\bговорится\b", "отмечается"),
    (r"\bпрошёл\b", "состоялся"),
    (r"\bожидается\b", "предполагается"),
    (r"\bпо данным\b", "согласно данным"),
]

def split_sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    if not text: return []
    return re.split(r"(?<=[.!?])\s+", text)

def paraphrase_sentence(s):
    out = s
    for pat, repl in SYN_REPLACE:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    return out

def pick_context_emoji_triplet(context):
    t = (context or "").lower()
    # Каждая тема возвращает ТРИ РАЗНЫЕ пары для (что случилось / детали / влияние)
    THEMES = [
        (["биткоин","crypto","btc","ethereum","крипт","stablecoin","defi","nft"], [("🪙","📰"),("🔗","📊"),("🚀","📉")]),
        (["акци","индекс","рынок","бирж","nasdaq","nyse","s&p","sp500","dow"],   [("📈","📰"),("📊","🏦"),("📉","🧭")]),
        (["доллар","рубл","валют","курс","евро","юань","usd","eur","cny","fx"],  [("💵","📰"),("💱","📊"),("🧭","🏦")]),
        (["ставк","фрс","цб","центробанк","инфляц","cpi","ppi","qe","qt"],      [("🏦","📰"),("📊","💬"),("📉","🧭")]),
        (["нефть","брент","wti","opec","газ","lng","энерги"],                    [("🛢️","📰"),("⚡","📊"),("🚚","🧭")]),
        (["золото","xau","металл","серебро","commodit"],                          [("🥇","📰"),("📊","🔎"),("🏦","🧭")]),
        (["банк","кредит","отчёт","earnings","profit","убыток"],                 [("🏦","📰"),("📊","🧾"),("🧭","💬")]),
        (["ai","искусств","нейросет","chip","полупровод","nvidia","intel"],      [("🤖","📰"),("🧠","📊"),("⚙️","🧭")]),
        (["санкц","эмбарго","пошлин","геополит","переговор","президент"],        [("🏛️","📰"),("🤝","📊"),("🧭","🌍")]),
        (["ипотек","недвижим","real estate","housing"],                           [("🏠","📰"),("📊","🔑"),("🧭","💼")]),
    ]
    for keys, triplet in THEMES:
        if any(k in t for k in keys):
            return triplet
    return [("🗞️","📰"),("📊","🔎"),("🧭","🧠")]  # дефолт

def ensure_unique_paragraphs(p1, p2, p3, sents):
    # Если абзацы совпадают по смыслу — добираем/заменяем предложениями
    def uniq(a, b):
        return norm(a) != norm(b) and a.strip() and b.strip()
    if not uniq(p2, p1):
        extra = " ".join(paraphrase_sentence(s) for s in sents[3:6]) or p2
        p2 = clamp(extra, max(120, len(p1)))  # другой материал
    if not uniq(p3, p2) or not uniq(p3, p1):
        extra2 = " ".join(paraphrase_sentence(s) for s in sents[6:9]) or p3
        p3 = clamp(extra2, max(120, len(p2)))
    # финальная гарантия уникальности (обрежем/изменим форму)
    if not uniq(p2, p1):
        p2 = clamp(p2 + " Подробности уточняются.", len(p2)+40)
    if not uniq(p3, p2) or not uniq(p3, p1):
        p3 = clamp("Влияние: " + p3, len(p3)+20)
    return p1, p2, p3

def build_three_paragraphs(title, article_text, feed_summary):
    base = (article_text or "").strip() or (feed_summary or "").strip()
    sents = [s for s in split_sentences(base) if len(s) > 0]

    # 1: суть (1–2 предложения)
    p1 = " ".join(paraphrase_sentence(s) for s in sents[:2]) or clamp(feed_summary, 250)
    # 2: детали (2–3)
    p2 = " ".join(paraphrase_sentence(s) for s in sents[2:5]) or clamp(base, 300)
    # 3: влияние / что дальше (до 3)
    p3_src = sents[5:8] or sents[:1]
    p3 = " ".join(paraphrase_sentence(s) for s in p3_src)

    # анти-дубликат
    p1, p2, p3 = ensure_unique_paragraphs(p1, p2, p3, sents)

    # эмодзи-триплет по контексту
    trip = pick_context_emoji_triplet(f"{title} {base}")
    p1 = f"{trip[0][0]}{trip[0][1]} {clamp(p1, 320)}"
    p2 = f"{trip[1][0]}{trip[1][1]} {clamp(p2, 360)}"
    p3 = f"{trip[2][0]}{trip[2][1]} {clamp(p3, 360)}"
    return p1, p2, p3

# ============ УМНЫЕ ТЕГИ ============
def gen_smart_tags(title, text, entities, max_tags=6):
    t = f"{title} {text}".lower()
    buckets = []
    def add(tag): 
        if tag not in buckets:
            buckets.append(tag)

    if any(k in t for k in ["биткоин","bitcoin","btc","эфириум","ethereum","eth","крипт","stablecoin","usdt","usdc","bnb","solana","sol"]):
        add("#крипта"); 
        if "btc" in t or "биткоин" in t: add("#BTC")
        if "eth" in t or "эфириум" in t: add("#ETH")

    if any(k in t for k in ["доллар","usd","евро","eur","рубл","rub","юань","cny","курс","форекс","fx"]):
        add("#валюта")
        if any(k in t for k in ["usd","доллар"]): add("#USD")
        if any(k in t for k in ["eur","евро"]): add("#EUR")
        if any(k in t for k in ["рубл","rub"]): add("#RUB")
        if any(k in t for k in ["cny","юань","yuan"]): add("#CNY")

    if any(k in t for k in ["акци","рынок","бирж","индекс","насдак","s&p","dow","мосбирж","nasdaq","nyse","sp500"]):
        add("#акции"); add("#рынки")

    if any(k in t for k in ["ставк","фрс","цб","центробанк","инфляц","cpi","ppi","qe","qt"]):
        add("#ставки"); add("#инфляция")

    if any(k in t for k in ["нефть","брент","wti","opec","газ","энерги","lng"]):
        add("#энергетика")
        if any(k in t for k in ["брент","brent"]): add("#Brent")
        if any(k in t for k in ["wti"]): add("#WTI")
        if "газ" in t: add("#газ")

    if any(k in t for k in ["санкц","эмбарго","пошлин","геополит","переговор","президент"]):
        add("#геополитика")

    # Компании/тикеры
    for e in entities[:3]:
        if re.fullmatch(r"[A-Z]{2,6}", e):
            add(f"#{e}")
        else:
            name = re.sub(r"[^A-Za-zА-Яа-я0-9]+", "", e)
            if 2 < len(name) <= 20:
                add(f"#{name}")

    # Итог
    return " ".join(buckets[:max_tags])

# ============ КАПШЕН (теги в самом конце) ============
def build_caption(title, para1, para2, para3, link, tags_str):
    title = clamp(title, 200)
    dom = root_domain(link) if link else None
    body = f"{para1}\n\n{para2}\n\n{para3}"
    parts = [title, "", body]

    if dom:
        parts += ["", f"Источник: [{dom}]({link})"]
    else:
        parts += ["", "Источник: неизвестно"]

    # канал — перед тегами
    parts += ["", f"[{CHANNEL_NAME}]({CHANNEL_LINK})"]

    # в самом конце — теги, после пустой строки
    if tags_str:
        parts += ["", tags_str]

    cap = "\n".join(parts)

    # Лимит подписи ~1024: режем абзацы (3->2->1), сохраняя порядок концовки
    if len(cap) > 1024:
        over = len(cap) - 1024 + 3
        p3 = clamp(para3[:-min(over, len(para3))], 300)
        parts = [title, "", f"{para1}\n\n{para2}\n\n{p3}"]
        if dom: parts += ["", f"Источник: [{dom}]({link})"]
        else:   parts += ["", "Источник: неизвестно"]
        parts += ["", f"[{CHANNEL_NAME}]({CHANNEL_LINK})", "", tags_str]
        cap = "\n".join(parts)

        if len(cap) > 1024:
            over = len(cap) - 1024 + 3
            p2 = clamp(para2[:-min(over, len(para2))], 300)
            parts = [title, "", f"{para1}\n\n{p2}\n\n{p3}"]
            if dom: parts += ["", f"Источник: [{dom}]({link})"]
            else:   parts += ["", "Источник: неизвестно"]
            parts += ["", f"[{CHANNEL_NAME}]({CHANNEL_LINK})", "", tags_str]
            cap = "\n".join(parts)

            if len(cap) > 1024:
                over = len(cap) - 1024 + 3
                p1 = clamp(para1[:-min(over, len(para1))], 280)
                parts = [title, "", f"{p1}\n\n{p2}\n\n{p3}"]
                if dom: parts += ["", f"Источник: [{dom}]({link})"]
                else:   parts += ["", "Источник: неизвестно"]
                parts += ["", f"[{CHANNEL_NAME}]({CHANNEL_LINK})", "", tags_str]
                cap = "\n".join(parts)
    return cap

# ============ ОТПРАВКА ============
def send_photo(photo_bytes, caption):
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN. Добавь секрет в GitHub: Settings → Secrets → Actions → BOT_TOKEN")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(url, files=files, data=data, timeout=30)
    print("Telegram status:", r.status_code, r.text[:200])
    r.raise_for_status()
    return r.json()

# ============ СБОР ФИДОВ ============
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

# ============ ОБРАБОТКА ОДНОЙ НОВОСТИ ============
def process_item(link, title, feed_summary):
    article_text = fetch_article_text(link, max_chars=2400)
    p1, p2, p3 = build_three_paragraphs(title, article_text, feed_summary)

    entities = extract_entities(title, f"{p1} {p2} {p3}")
    tags_str = gen_smart_tags(title, f"{p1} {p2} {p3}", entities, max_tags=6)
    if not tags_str:
        tags_str = "#новости"

    caption = build_caption(title, p1, p2, p3, link or "", tags_str)
    card = draw_title_card(title, domain(link or ""), TIMEZONE)
    resp = send_photo(card, caption)
    print("Posted:", (title or "")[:80], "→", resp.get("ok", True))

# ============ ГЛАВНЫЙ ЦИКЛ ============
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
