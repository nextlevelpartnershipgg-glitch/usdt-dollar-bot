import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse, sys, random
from datetime import datetime, timezone
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from zoneinfo import ZoneInfo

# ============ НАСТРОЙКИ ============
BOT_TOKEN  = os.environ.get("BOT_TOKEN")  # ОБЯЗАТЕЛЬНО через GitHub Secrets
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@usdtdollarm")  # твой канал по умолчанию
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Zurich")   # твой часовой пояс

# Русские источники (можно менять/добавлять)
RSS_FEEDS = [
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",  # РБК
    "https://lenta.ru/rss/news",                          # Lenta.ru
    "https://www.gazeta.ru/export/rss/lenta.xml",         # Газета.ru
    "https://tass.ru/rss/v2.xml",                         # ТАСС
    "https://www.kommersant.ru/RSS/news.xml",             # Коммерсантъ
]

# Хэштеги под постом
TAGS = "#новости #рынки #экономика #акции #usdt #доллар"

# Файл состояния (защита от повторов)
DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"


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

def make_caption(title, summary, link):
    title = (title or "").strip()
    summary = (summary or "").strip()
    if len(summary) > 300:
        summary = summary[:297] + "…"
    caption = f"💵 {title}\n— {summary}\n\n🔗 Источник: {link}\n{TAGS}"
    if len(caption) > 1020:  # лимит подписи Telegram
        extra = len(caption) - 1020
        summary = summary[:-extra-1] + "…"
        caption = f"💵 {title}\n— {summary}\n\n🔗 Источник: {link}\n{TAGS}"
    return caption

# Подбор ключевых слов для фоновой картинки
KEYMAP = [
    (["фрс","ставк","инфляц","cpi","ppi","процент"], "interest rates,economy,bank"),
    (["нефть","брент","wti","oil","опек"], "oil,barrels,energy,refinery"),
    (["газ","lng","газопровод"], "natural gas,energy,pipeline"),
    (["рубл","ruble","руб"], "ruble,currency,money"),
    (["доллар","usd","dxy","usdt"], "dollar,currency,finance,wall street"),
    (["биткоин","bitcoin","btc","крипт","crypto","ether","eth"], "crypto,blockchain,bitcoin,ethereum"),
    (["акци","индекс","s&p","nasdaq","рынок","биржа"], "stocks,stock market,ticker,wall street"),
    (["евро","eur"], "euro,currency,finance"),
    (["золото","gold","xau"], "gold,precious metal,ingots"),
]

def pick_photo_query(title, summary):
    text = f"{title} {summary}".lower()
    for keys, q in KEYMAP:
        if any(k in text for k in keys):
            return q
    return "finance,markets,city night,news"

def fetch_unsplash_image(query, w=1080, h=540):
    # Публичный источник случайных фото (без API-ключа)
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
        return Image.new("RGB", (w, h), (24, 26, 28))
    img = img.resize((w, h))
    img = img.filter(ImageFilter.GaussianBlur(radius=0.6))
    img = ImageEnhance.Brightness(img).enhance(0.85)
    return img

# Карточка 1080x540: цитата поверх картинки
def draw_card_quote(title_text, summary_text, src_domain, tzname):
    W, H = 1080, 540
    query = pick_photo_query(title_text, summary_text)
    bg = ensure_bg(fetch_unsplash_image(query, W, H), W, H)
    d = ImageDraw.Draw(bg)

    # Полупрозрачная плашка для читаемости
    overlay = Image.new("RGBA", (W, H), (0,0,0,0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([40, 90, W-40, H-70], radius=28, fill=(0,0,0,120))
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
    d.text((48, 26), brand, fill=(255,255,255), font=font_brand)
    try:
        tz = ZoneInfo(tzname)
    except Exception:
        tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d.%m %H:%M")
    d.text((W - 48 - d.textlength(now_str, font=font_time), 26), now_str, fill=(255,255,255), font=font_time)

    # Тело: кавычки, заголовок и короткий текст
    margin_x = 72
    y = 120

    # Открывающая кавычка
    d.text((margin_x - 20, y - 20), "“", fill=(255,255,255), font=font_quote_mark)

    # Заголовок (жирный)
    for line in textwrap.wrap((title_text or "").strip(), width=28)[:3]:
        d.text((margin_x + 50, y), line, font=font_title, fill=(255,255,255))
        y += 58

    # Краткий текст
    short = (summary_text or "").strip().replace("\n", " ")
    if len(short) > 260:
        short = short[:257] + "…"
    if short:
        y += 12
        for ln in textwrap.wrap(short, width=40):
            if y + 42 > H - 100:
                break
            d.text((margin_x + 50, y), ln, font=font_quote, fill=(230,230,230))
            y += 42

    # Закрывающая кавычка
    d.text((W - 110, H - 140), "”", fill=(255,255,255), font=font_quote_mark)

    # Низ: источник
    src = f"source: {src_domain}"
    d.text((72, H - 56), src, font=font_small, fill=(220,220,220))

    # В память
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
    # лог ответа для отладки
    print("Telegram status:", r.status_code, r.text[:300])
    r.raise_for_status()
    return r.json()


# ============ ЛОГИКА ============
def choose_freshest_entry():
    """Выбираем САМУЮ свежую запись среди всех фидов."""
    freshest = None
    freshest_dt = datetime(1970,1,1, tzinfo=timezone.utc)

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

        # самая свежая в этом фиде
        e = sorted(fp.entries, key=parse_dt, reverse=True)[0]
        edt = parse_dt(e)
        if edt > freshest_dt:
            freshest_dt = edt
            freshest = (feed_url, e)

    return freshest  # (feed_url, entry) или None

def process_item(link, title, summary):
    cap  = make_caption(title, summary, link or "")
    card = draw_card_quote(title, summary, domain(link or ""), TIMEZONE)
    resp = send_photo(card, cap)
    print("Posted:", (title or "")[:80], "→", resp.get("ok", True))

def main():
    state = load_state()
    last_uid = state.get("last_uid", "")

    chosen = choose_freshest_entry()
    if not chosen:
        print("No entries found in feeds.")
        return

    feed_url, entry = chosen
    link    = getattr(entry, "link", "") or ""
    title   = (getattr(entry, "title", "") or "").strip() or "(no title)"
    summary = clean_html(getattr(entry, "summary", getattr(entry, "description", "")))

    # UID для защиты от повтора: хеш по ссылке/тайтлу/времени
    ts = getattr(entry, "published", getattr(entry, "updated", "")) or ""
    entry_uid = hashlib.sha256((link + "|" + title + "|" + ts).encode("utf-8")).hexdigest()

    if entry_uid == last_uid:
        print("Freshest item already posted, skip.")
        return

    try:
        process_item(link, title, summary)
        state["last_uid"] = entry_uid
        save_state(state)
    except Exception as e:
        print("Error sending:", e)

if __name__ == "__main__":
    main()
