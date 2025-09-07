import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse, sys, random
from datetime import datetime, timezone
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from zoneinfo import ZoneInfo

# ====== ОКРУЖЕНИЕ ======
BOT_TOKEN  = os.environ["8304198834:AAFmxWDHpFMQebf_Ns0TQi3B8nRldqgbxJg"]
CHANNEL_ID = os.environ["usdtdollarm"]              # @USDT_Dollar или -100xxxxxxxxx
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Moscow")

# Русские источники (можно менять)
RSS_FEEDS = [
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",  # РБК
    "https://lenta.ru/rss/news",                          # Lenta.ru
    "https://www.gazeta.ru/export/rss/lenta.xml",         # Газета.ru
    "https://tass.ru/rss/v2.xml",                         # ТАСС
    "https://www.kommersant.ru/RSS/news.xml",             # Коммерсантъ
]

# Хэштеги
TAGS = "#новости #рынки #экономика #акции #usdt #доллар"

# Состояние (дедупликация по каждому фиду)
DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

# ====== УТИЛИТЫ ======
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

def make_caption(title, summary, link):
    title = title.strip()
    summary = (summary or "").strip()
    if len(summary) > 300:
        summary = summary[:297] + "…"
    caption = f"💵 {title}\n— {summary}\n\n🔗 Источник: {link}\n{TAGS}"
    if len(caption) > 1020:  # лимит подписи Telegram
        extra = len(caption) - 1020
        summary = summary[:-extra-1] + "…"
        caption = f"💵 {title}\n— {summary}\n\n🔗 Источник: {link}\n{TAGS}"
    return caption

# --- подбор ключевых слов для фоновой картинки ---
KEYMAP = [
    (["ФРС","ставк","инфляц","CPI","PPI","процент"], "interest rates,economy,bank"),
    (["нефть","брент","wti","oil","ОПЕК"], "oil,barrels,energy,refinery"),
    (["газ","газпр","lng","газопровод"], "natural gas,energy,pipeline"),
    (["рубл","ruble","руб"], "ruble,currency,money"),
    (["доллар","usd","dxy","usdt"], "dollar,currency,finance"),
    (["биткоин","bitcoin","btc","крипт","crypto","ether","eth"], "crypto,blockchain,bitcoin,ethereum"),
    (["акци","индекс","s&p","nasdaq","рынок","биржа"], "stocks,stock market,ticker,wall street"),
    (["евро","eur"], "euro,currency,finance"),
    (["золото","gold","xau"], "gold,precious metal,ingots"),
]

def pick_photo_query(title, summary):
    text = f"{title} {summary}".lower()
    for keys, q in KEYMAP:
        if any(k.lower() in text for k in keys):
            return q
    # fallback — общая финансовая тема
    return "finance,markets,city night,news"

def fetch_unsplash_image(query, w=1080, h=540):
    # Без API-ключа: используем публичный источник случайных фото
    # Пример: https://source.unsplash.com/1080x540/?finance,stocks
    seed = random.randint(0, 10_000_000)
    url = f"https://source.unsplash.com/{w}x{h}/?{urllib.parse.quote(query)}&sig={seed}"
    r = requests.get(url, timeout=25)
    r.raise_for_status()
    try:
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        return img
    except Exception:
        return None

def ensure_bg(img, w=1080, h=540):
    if img is None:
        # запасной вариант — градиент
        bg = Image.new("RGB", (w, h), (24, 26, 28))
        return bg
    # подровняем размер/кадрирование
    img = img.resize((w, h))
    # немного размытия + затемнение, чтобы текст читался
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    img = ImageEnhance.Brightness(img).enhance(0.8)
    return img

# --- карточка 1080x540, цитата на фоне картинки ---
def draw_card_quote(title_text, summary_text, src_domain, tzname):
    W, H = 1080, 540
    # подбираем фон
    query = pick_photo_query(title_text, summary_text)
    bg = ensure_bg(fetch_unsplash_image(query, W, H), W, H)
    d = ImageDraw.Draw(bg)

    # затемняем центр под текст (мягкая плашка)
    overlay = Image.new("RGBA", (W, H), (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    od.rectangle([40, 100, W-40, H-80], fill=(0,0,0,120), outline=None, width=0, radius=28)
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")

    d = ImageDraw.Draw(bg)

    # Шрифты
    font_brand   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    font_time    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 26)
    font_quote   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
    font_title   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 50)
    font_small   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
    font_quote_mark = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 80)

    # Верх: бренд + время
    brand = "USDT=Dollar"
    d.text((48, 30), brand, fill=(255,255,255), font=font_brand)

    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d.%m %H:%M")
    d.text((W - 48 - d.textlength(now_str, font=font_time), 30), now_str, fill=(255,255,255), font=font_time)

    # Основной текст как цитата
    margin_x = 72
    y = 120

    # Большая открывающая кавычка
    d.text((margin_x - 20, y - 20), "“", fill=(255,255,255), font=font_quote_mark)

    # заголовок (жирный)
    for line in textwrap.wrap(title_text.strip(), width=28)[:3]:
        d.text((margin_x + 50, y), line, font=font_title, fill=(255,255,255))
        y += 58

    # краткий текст (обычный шрифт)
    if summary_text:
        short = summary_text.strip().replace("\n", " ")
        if len(short) > 260:
            short = short[:257] + "…"
        y += 12
        for ln in textwrap.wrap(short, width=40):
            if y + 42 > H - 100:
                break
            d.text((margin_x + 50, y), ln, font=font_quote, fill=(230,230,230))
            y += 42

    # Закрывающая кавычка
    d.text((W - 110, H - 140), "”", fill=(255,255,255), font=font_quote_mark)

    # Низ: источник домен
    src = f"source: {src_domain}"
    d.text((72, H - 56), src, font=font_small, fill=(220,220,220))

    # Итог — в память
    bio = io.BytesIO()
    bg.save(bio, format="PNG", optimize=True)
    bio.seek(0)
    return bio

def send_photo(photo_bytes, caption):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption}
    r = requests.post(url, files=files, data=data, timeout=30)
    r.raise_for_status()
    return r.json()

# ====== ЛОГИКА ======
def process_item(link, title, summary):
    # Подпись
    cap  = make_caption(title, summary, link or "")
    # Карточка-цитата на фоне подходящей картинки
    card = draw_card_quote(title, summary, domain(link or ""), TIMEZONE)
    # Публикация
    send_photo(card, cap)

def run_cron_mode():
    state = load_state()
    for feed_url in RSS_FEEDS:
        fp = feedparser.parse(feed_url)
        if not fp.entries:
            continue

        def parse_dt(e):
            ts = getattr(e, "published", getattr(e, "updated", "")) or ""
            try:
                return dtparse.parse(ts)
            except Exception:
                return datetime(1970,1,1, tzinfo=timezone.utc)

        entry = sorted(fp.entries, key=parse_dt, reverse=True)[0]

        link    = getattr(entry, "link", "") or ""
        title   = (getattr(entry, "title", "") or "").strip() or "(no title)"
        summary = clean_html(getattr(entry, "summary", getattr(entry, "description", "")))

        entry_uid = hashlib.sha256((link or title).encode("utf-8")).hexdigest()
        last_uid  = state.get(feed_url, "")

        if entry_uid == last_uid:
            continue  # уже постили эту последнюю запись этого фида

        try:
            process_item(link, title, summary)
            state[feed_url] = entry_uid
            time.sleep(1.0)  # маленькая пауза между источниками
        except Exception as e:
            print("Error sending:", e)

    save_state(state)

def run_single_mode():
    title   = os.environ.get("USDT_TITLE", "").strip()
    link    = os.environ.get("USDT_LINK", "")
    summary = clean_html(os.environ.get("USDT_SUM", ""))
    if not title:
        print("No USDT_TITLE provided")
        return
    try:
        process_item(link, title, summary)
    except Exception as e:
        print("Error sending single:", e)

if __name__ == "__main__":
    if "--single" in sys.argv:
        run_single_mode()
    else:
        run_cron_mode()
