import os, io, json, time, textwrap, pathlib, hashlib, urllib.parse, random, re
from datetime import datetime, timezone, timedelta
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from zoneinfo import ZoneInfo

# ========= НАСТРОЙКИ =========
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@usdtdollarm")
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Zurich")

CHANNEL_NAME   = os.environ.get("CHANNEL_NAME", "USDT=Dollar")
CHANNEL_HANDLE = os.environ.get("CHANNEL_HANDLE", "@usdtdollarm")
CHANNEL_LINK   = os.environ.get("CHANNEL_LINK", f"https://t.me/{CHANNEL_HANDLE.lstrip('@')}")

MAX_POSTS_PER_RUN = int(os.environ.get("MAX_POSTS_PER_RUN", "5"))
LOOKBACK_MINUTES  = int(os.environ.get("LOOKBACK_MINUTES", "90"))

# ========= ИСТОЧНИКИ =========
RSS_FEEDS_RU = [
    # РБК
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://rssexport.rbc.ru/rbcnews/economics/30/full.rss",
    "https://rssexport.rbc.ru/rbcnews/finance/30/full.rss",
    "https://rssexport.rbc.ru/rbcnews/politics/30/full.rss",
    # Lenta
    "https://lenta.ru/rss/news",
    "https://lenta.ru/rss/economics",
    "https://lenta.ru/rss/russia",
    "https://lenta.ru/rss/world",
    # Коммерсантъ
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.kommersant.ru/RSS/economics.xml",
    "https://www.kommersant.ru/RSS/finance.xml",
    # Газета.ru
    "https://www.gazeta.ru/export/rss/first.xml",
    "https://www.gazeta.ru/export/rss/business.xml",
    "https://www.gazeta.ru/export/rss/politics.xml",
    # ТАСС / Ведомости / Интерфакс / РИА / Известия / Финмаркет
    "https://tass.ru/rss/v2.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.interfax.ru/rss.asp",
    "https://ria.ru/export/rss2/archive/index.xml",
    "https://iz.ru/xml/rss/all.xml",
    "https://www.finmarket.ru/rss/news.asp",
    # Профильные
    "https://banki.ru/xml/news.rss",
    "https://www.kommersant.ru/RSS/regions.xml",
    "https://www.kommersant.ru/RSS/tech.xml",
]

RSS_FEEDS_WORLD = [
    # Reuters
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.reuters.com/Reuters/worldNews",
    "https://feeds.reuters.com/reuters/marketsNews",
    # Bloomberg / FT / WSJ / BBC / CNN / CNBC / Guardian / NYT / MW
    "https://feeds.bloomberg.com/politics/news.rss",
    "https://www.bloomberg.com/feeds/podcasts/etf_report.xml",
    "https://www.ft.com/?format=rss",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    "http://feeds.bbci.co.uk/news/world/rss.xml",
    "http://feeds.bbci.co.uk/news/business/rss.xml",
    "http://rss.cnn.com/rss/edition_world.rss",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://www.theguardian.com/world/rss",
    "https://www.theguardian.com/uk/business/rss",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
    "https://www.marketwatch.com/rss/topstories",
    "https://www.aljazeera.com/xml/rss/all.xml",
    "https://asia.nikkei.com/rss",
    "https://www.scmp.com/rss/91/feed",
    # Крипта (международные)
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://cointelegraph.com/rss",
]

RSS_FEEDS = RSS_FEEDS_RU + RSS_FEEDS_WORLD

DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

UA  = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"}
UA_IMG = {"User-Agent":"Mozilla/5.0"}

# ========= PYMORPHY2 (для лемматизации тегов) =========
try:
    import pymorphy2
    MORPH = pymorphy2.MorphAnalyzer()
except Exception:
    MORPH = None  # будет фолбэк

# ========= ВСПОМОГАТЕЛЬНЫЕ =========
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

# ========= ЯЗЫК / ПЕРЕВОД =========
def detect_lang(text: str) -> str:
    if re.search(r"[А-Яа-яЁё]", text):
        return "ru"
    en_hits = len(re.findall(r"\b(the|and|of|to|in|for|on|with|from|by|as|at|is|are)\b", text.lower()))
    ru_hits = len(re.findall(r"\b(и|в|на|по|для|из|от|как|это|что|бы|не|к)\b", text.lower()))
    return "en" if en_hits > ru_hits else "ru"

LT_ENDPOINTS = [
    "https://libretranslate.de/translate",
    "https://translate.argosopentech.com/translate",
]

LOCAL_EN_RU = {
    "fed": "ФРС", "ecb":"ЕЦБ", "bank of england":"Банк Англии", "bank of japan":"Банк Японии",
    "inflation":"инфляция", "cpi":"индекс CPI", "ppi":"индекс PPI",
    "rate":"ставка", "rates":"ставки", "hike":"повышение", "cut":"снижение",
    "recession":"рецессия", "growth":"рост", "gdp":"ВВП",
    "oil":"нефть", "gas":"газ", "brent":"Brent", "wti":"WTI",
    "stocks":"акции", "bonds":"облигации", "equities":"акции", "yields":"доходности",
    "dollar":"доллар", "euro":"евро", "ruble":"рубль", "yuan":"юань",
    "bitcoin":"биткоин", "ethereum":"эфириум", "crypto":"криптовалюта",
}

def translate_en_to_ru(text: str, timeout=12) -> str:
    text = text.strip()
    if not text:
        return text
    for ep in LT_ENDPOINTS:
        try:
            r = requests.post(ep, data={"q": text, "source":"en", "target":"ru", "format":"text"},
                              headers={"Accept":"application/json"}, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                out = data.get("translatedText") or ""
                if out.strip():
                    return out.strip()
        except Exception:
            continue
    s = text
    for k in sorted(LOCAL_EN_RU.keys(), key=lambda x: -len(x)):
        s = re.sub(rf"\b{re.escape(k)}\b", LOCAL_EN_RU[k], s, flags=re.IGNORECASE)
    return s

# ========= ФОН (персона/предмет) =========
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
    stop = {"The","This"}
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
            time.sleep(0.8*(i+1))
        except Exception:
            time.sleep(0.8*(i+1))
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

# ========= КАРТОЧКА: перенос по словам =========
def wrap_text_by_width(draw, text, font, max_width, max_lines=5):
    words = text.split()
    lines, current = [], ""
    for w in words:
        test = (current + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
                if len(lines) >= max_lines:
                    return lines
            current = w
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines

def fit_title_in_box(draw, text, font_path, box_w, box_h, start_size=64, min_size=28, line_gap=8, max_lines=5):
    for size in range(start_size, min_size-1, -2):
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text_by_width(draw, text, font, box_w, max_lines=max_lines)
        h_line = font.getbbox("Ag")[3]
        total_h = len(lines)*h_line + (len(lines)-1)*line_gap
        if lines and total_h <= box_h:
            return font, lines
    font = ImageFont.truetype(font_path, min_size)
    lines = wrap_text_by_width(draw, text, font, box_w, max_lines=max_lines)
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
    try: tz = ZoneInfo(tzname)
    except Exception: tz = ZoneInfo("UTC")
    now_str = datetime.now(tz).strftime("%d.%m %H:%M")
    d.text((W - 48 - d.textlength(now_str, font=font_time), 26), now_str, fill=(255,255,255), font=font_time)

    box_x, box_y = 72, 150
    box_w, box_h = W - 2*box_x, H - box_y - 110
    font_title, lines = fit_title_in_box(d, (title_text or "").strip(), path_bold, box_w, box_h, start_size=64, min_size=30, max_lines=5)

    y = box_y
    for ln in lines:
        d.text((box_x, y), ln, font=font_title, fill=(255,255,255))
        y += font_title.getbbox("Ag")[3] + 8

    d.text((72, H - 58), f"source: {src_domain}", font=font_small, fill=(225,225,225))
    bio = io.BytesIO(); bg.save(bio, format="PNG", optimize=True); bio.seek(0)
    return bio

# ========= СТАТЬЯ =========
def fetch_article_text(url, max_chars=2600):
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

# ========= «Научный» стиль RU =========
RU_TONE_REWRITE = [
    (r"\bсказал(а|и)?\b", "сообщил\\1"),
    (r"\bзаявил(а|и)?\b", "отметил\\1"),
    (r"\bпо словам\b", "по данным"),
    (r"\bпо мнению\b", "согласно оценкам"),
    (r"\bпримерно\b", "порядка"),
    (r"\bочень\b", "существенно"),
    (r"\bсильно\b", "значительно"),
]

def ru_scientific_paraphrase(s):
    out = s
    for pat, repl in RU_TONE_REWRITE:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    out = re.sub(r"\s+%", "%", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out

def split_sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    if not text: return []
    return re.split(r"(?<=[.!?])\s+", text)

def paraphrase_sentence_ru_or_en(s):
    lang = detect_lang(s)
    if lang == "en":
        s = translate_en_to_ru(s)
    return ru_scientific_paraphrase(s)

def one_context_emoji(context):
    t = (context or "").lower()
    if any(k in t for k in ["биткоин","crypto","btc","ethereum","крипт"]): return "🪙"
    if any(k in t for k in ["акци","индекс","рынок","бирж","nasdaq","nyse","s&p"]): return "📈"
    if any(k in t for k in ["доллар","рубл","валют","курс","евро","юань","usd","eur","cny"]): return "💵"
    if any(k in t for k in ["ставк","фрс","цб","центробанк","инфляц","cpi","ppi"]): return "🏦"
    if any(k in t for k in ["нефть","брент","wti","opec","газ","lng","энерги"]): return "🛢️"
    if any(k in t for k in ["золото","xau","металл","серебро"]): return "🥇"
    if any(k in t for k in ["санкц","эмбарго","пошлин","геополит","переговор","президент"]): return "🏛️"
    return "📰"

def build_three_paragraphs_scientific(title, article_text, feed_summary):
    base = (article_text or "").strip() or (feed_summary or "").strip()
    if detect_lang(base) == "en":
        base = translate_en_to_ru(base)
    sents = [s for s in split_sentences(base) if s]

    p1_src = sents[:2] or sents[:1]
    p2_src = sents[2:5] or sents[:1]
    p3_src = sents[5:8] or sents[1:3] or sents[:1]

    p1 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p1_src)
    p2 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p2_src)
    p3 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p3_src)

    emoji = one_context_emoji(f"{title} {base}")
    p1 = f"{emoji} {clamp(p1, 320)}"
    p2 = clamp(p2, 360)
    p3 = clamp(p3, 360)
    return p1, p2, p3

# ========= ТЕГИ (существительные, И.п.) =========
RU_STOP = set("""
это тот эта которые который которой которых таком таком-то также поэтому таким чтобы при про для на из от по как уже еще или либо либо-же чем чем-то если когда где куда всего весь все вся того той его ее их наш ваш свой мой твой один две три четыре пять шесть семь восемь девять ноль
""".split())

COUNTRY_PROPER = {
    "россия":"Россия","сша":"США","китай":"Китай","япония":"Япония","германия":"Германия","франция":"Франция",
    "великобритания":"Великобритания","индия":"Индия","россий":"Россия","европа":"Европа"
}

def lemma_noun(word):
    w = word.lower()
    if MORPH:
        p = MORPH.parse(w)[0]
        if 'NOUN' in p.tag:
            nf = p.normal_form
            # собственные имена/страны — с заглавной
            if nf in COUNTRY_PROPER:
                return COUNTRY_PROPER[nf]
            # иначе маленькими (в тегах принято)
            return nf
    # фолбэк: вернем как есть
    return w

def extract_candidate_nouns(text, entities, limit=10):
    # базовый сбор: слова кириллицей/латиницей + сущности/тикеры
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text)
    candidates = []
    for w in words:
        wl = w.lower()
        if wl in RU_STOP: 
            continue
        # отфильтруем служебные
        if re.fullmatch(r"[A-Za-z]{3,}", wl) and wl in {"the","and","for","with","from","that"}:
            continue
        candidates.append(wl)

    # плюс сущности
    for e in entities:
        if re.fullmatch(r"[A-Z]{2,6}", e):
            candidates.append(e)  # тикер
        else:
            candidates += e.split()

    # к леммам
    lemmas = []
    for c in candidates:
        if re.fullmatch(r"[A-Z]{2,6}", c):
            lemmas.append(c)  # тикер без изменений
        else:
            l = lemma_noun(c)
            if l and len(l) >= 3:
                lemmas.append(l)
    # частотная сортировка
    freq = {}
    for l in lemmas:
        freq[l] = freq.get(l, 0) + 1
    out = [k for k,_ in sorted(freq.items(), key=lambda x: -x[1])]
    # финальный фильтр: только буквы/цифры
    out = [re.sub(r"[^A-Za-zА-Яа-яЁё0-9]", "", x) for x in out]
    out = [x for x in out if x and x.lower() not in RU_STOP]
    return out[:limit]

def gen_tags_nominative(title, body, entities, max_tags=6):
    # базовые тематики (в виде нормальной формы)
    thematic = []
    text_l = (title + " " + body).lower()
    def tadd(x):
        if x not in thematic:
            thematic.append(x)

    if any(k in text_l for k in ["биткоин","bitcoin","btc","крипт","ethereum","eth","stablecoin"]): tadd("крипта")
    if any(k in text_l for k in ["доллар","usd","евро","eur","рубл","rub","юань","cny","курс","форекс"]): tadd("валюта")
    if any(k in text_l for k in ["акци","рынок","бирж","индекс","nasdaq","nyse","s&p","sp500","dow"]): tadd("рынки")
    if any(k in text_l for k in ["ставк","фрс","цб","инфляц","cpi","ppi","qe","qt"]): tadd("ставки")
    if any(k in text_l for k in ["нефть","брент","wti","opec","газ","энерги","lng"]): tadd("энергетика")
    if any(k in text_l for k in ["санкц","эмбарго","пошлин","геополит","переговор","президент"]): tadd("геополитика")

    # кандидаты-существительные
    nouns = extract_candidate_nouns(title + " " + body, entities, limit=12)

    # собираем финальные (сначала тематические, потом частотные)
    result = []
    def add_tag(s):
        if s and s not in result and len(result) < max_tags:
            result.append(s)

    for t in thematic:
        add_tag(t)

    for n in nouns:
        # нормализуем заглавные для стран
        cap = COUNTRY_PROPER.get(n.lower(), n)
        add_tag(cap)

    # тикеры (#BTC) уже могут попасть как кандидаты — оставим их как есть
    tags = []
    for t in result[:max_tags]:
        if re.fullmatch(r"[A-Z]{2,6}", t):
            tags.append("#" + t)
        else:
            # делаем хэштег в именительном падеже
            # страны и имена — с заглавной, прочие — строчные
            if t in COUNTRY_PROPER.values():
                tags.append("#" + t)
            else:
                tags.append("#" + t.lower())
    return " ".join(tags[:max_tags])

# ========= КАПШЕН =========
def build_caption(title, para1, para2, para3, link, tags_str):
    title = clamp(title, 200)
    dom = root_domain(link) if link else None
    body = f"{para1}\n\n{para2}\n\n{para3}"

    parts = [title, "", body]
    if dom: parts += ["", f"Источник: [{dom}]({link})"]
    else:   parts += ["", "Источник: неизвестно"]

    parts += ["", f"[{CHANNEL_NAME}]({CHANNEL_LINK})"]
    if tags_str: parts += ["", tags_str]

    cap = "\n".join(parts)

    # лимит подписи ~1024 → поочерёдно укорачиваем 3→2→1 абзац
    if len(cap) > 1024:
        over = len(cap) - 1024 + 3
        p3 = clamp(para3[:-min(over, len(para3))], 300)
        parts = [title, "", f"{para1}\n\n{para2}\n\n{p3}"]
        parts += ["", f"Источник: [{dom}]({link})" if dom else "Источник: неизвестно",
                  "", f"[{CHANNEL_NAME}]({CHANNEL_LINK})", "", tags_str]
        cap = "\n".join(parts)
        if len(cap) > 1024:
            over = len(cap) - 1024 + 3
            p2 = clamp(para2[:-min(over, len(para2))], 300)
            parts = [title, "", f"{para1}\n\n{p2}\n\n{p3}"]
            parts += ["", f"Источник: [{dom}]({link})" if dom else "Источник: неизвестно",
                      "", f"[{CHANNEL_NAME}]({CHANNEL_LINK})", "", tags_str]
            cap = "\n".join(parts)
            if len(cap) > 1024:
                over = len(cap) - 1024 + 3
                p1 = clamp(para1[:-min(over, len(para1))], 280)
                parts = [title, "", f"{p1}\n\n{p2}\n\n{p3}"]
                parts += ["", f"Источник: [{dom}]({link})" if dom else "Источник: неизвестно",
                          "", f"[{CHANNEL_NAME}]({CHANNEL_LINK})", "", tags_str]
                cap = "\n".join(parts)
    return cap

# ========= ОТПРАВКА =========
def send_photo(photo_bytes, caption):
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN. Добавь секрет BOT_TOKEN в GitHub → Settings → Secrets → Actions")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "Markdown"}
    r = requests.post(url, files=files, data=data, timeout=30)
    print("Telegram status:", r.status_code, r.text[:200])
    r.raise_for_status()
    return r.json()

# ========= ФИДЫ =========
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
            items.append({"feed": feed_url, "link": link, "title": title or "(no title)",
                          "summary": summary, "ts": ts, "dt": dt, "uid": uid})
    return items

# ========= ОБРАБОТКА ОДНОЙ НОВОСТИ =========
def build_three_paragraphs_scientific(title, article_text, feed_summary):
    base = (article_text or "").strip() or (feed_summary or "").strip()
    if detect_lang(base) == "en":
        base = translate_en_to_ru(base)
    sents = [s for s in split_sentences(base) if s]

    p1_src = sents[:2] or sents[:1]
    p2_src = sents[2:5] or sents[:1]
    p3_src = sents[5:8] or sents[1:3] or sents[:1]

    p1 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p1_src)
    p2 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p2_src)
    p3 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p3_src)

    emoji = one_context_emoji(f"{title} {base}")
    p1 = f"{emoji} {clamp(p1, 320)}"
    p2 = clamp(p2, 360)
    p3 = clamp(p3, 360)
    return p1, p2, p3

def process_item(link, title, feed_summary):
    article_text = fetch_article_text(link, max_chars=2600)
    p1, p2, p3 = build_three_paragraphs_scientific(title, article_text, feed_summary)

    entities = extract_entities(title, f"{p1} {p2} {p3}")
    tags_str = gen_tags_nominative(title, f"{p1} {p2} {p3}", entities, max_tags=6) or "#новости"

    caption = build_caption(title, p1, p2, p3, link or "", tags_str)
    card = draw_title_card(title, domain(link or ""), TIMEZONE)
    resp = send_photo(card, caption)
    print("Posted:", (title or "")[:80], "→", resp.get("ok", True))

# ========= MAIN =========
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
