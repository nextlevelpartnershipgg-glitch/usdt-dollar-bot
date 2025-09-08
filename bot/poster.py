import os, io, json, time, pathlib, hashlib, urllib.parse, random, re, math
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

MAX_POSTS_PER_RUN  = int(os.environ.get("MAX_POSTS_PER_RUN", "6"))
LOOKBACK_MINUTES   = int(os.environ.get("LOOKBACK_MINUTES", "30"))   # безопасность против пропусков
FRESH_WINDOW_MIN   = int(os.environ.get("FRESH_WINDOW_MIN", "20"))   # окно «самых свежих»
MIN_EVENT_YEAR     = int(os.environ.get("MIN_EVENT_YEAR", "2023"))   # отсечём старые «вечнозелёные» ленты

DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "state.json"

# ========= ИСТОЧНИКИ (БЕЗ РИА) =========
RSS_FEEDS_RU = [
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://lenta.ru/rss/news",
    "https://tass.ru/rss/v2.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.interfax.ru/rss.asp",
]
RSS_FEEDS_WORLD = [
    "https://feeds.reuters.com/reuters/marketsNews",
    "https://feeds.reuters.com/Reuters/worldNews",
    "https://www.ft.com/?format=rss",
    "http://feeds.bbci.co.uk/news/business/rss.xml",
    "https://www.marketwatch.com/rss/topstories",
    "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
]
RSS_FEEDS = RSS_FEEDS_RU + RSS_FEEDS_WORLD

UA  = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"}

# ========= PYMORPHY2 (для тегов) =========
try:
    import pymorphy2
    MORPH = pymorphy2.MorphAnalyzer()
except Exception:
    MORPH = None  # мягкий фолбэк

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
        if len(parts) > 2: dom = ".".join(parts[-2:])
        return dom
    except Exception:
        return "источник"

def clean_html(html):
    if not html: return ""
    soup = BeautifulSoup(html, "html.parser")
    return " ".join(soup.get_text(separator=" ").split())

def clamp(s, n):
    s = (s or "").strip()
    return s if len(s) <= n else s[:n-1] + "…"

# ========= ПЕРЕВОД EN→RU (жёсткий) =========
def detect_lang(text: str) -> str:
    if re.search(r"[А-Яа-яЁё]", text): return "ru"
    en_hits = len(re.findall(r"\b(the|and|of|to|in|for|on|with|from|by|as|at|is|are|this|that|it|was|be)\b", text.lower()))
    ru_hits = len(re.findall(r"\b(и|в|на|по|для|из|от|как|это|что|бы|не|к|с|о|об)\b", text.lower()))
    return "en" if en_hits > ru_hits else "ru"

LT_ENDPOINTS = [
    "https://libretranslate.de/translate",
    "https://translate.argosopentech.com/translate",
]
LOCAL_EN_RU = {
    "china":"Китай","beijing":"Пекин","central bank":"центральный банк","central banks":"центральные банки",
    "dollar":"доллар","us dollar":"доллар США","reserve":"резерв","reserves":"резервы","safe haven":"тихая гавань",
    "gold":"золото","gold futures":"фьючерсы на золото","comex":"Comex","ounce":"унция","billion":"млрд",
    "percent":"%","percentage":"%","share":"доля","holdings":"запасы","treasuries":"казначейские облигации",
    "alternative":"альтернатива","geopolitical":"геополитический","risk":"риск","risки":"риски",
    "inflation":"инфляция","stability":"стабильность","assets":"активы","backed":"обеспеченный",
    "increase":"рост","rose":"вырос","rise":"рост","jump":"скачок","month":"месяц","monthly":"ежемесячный",
}
def translate_hard_ru(text: str, timeout=14) -> str:
    text = (text or "").strip()
    if not text: return text
    for ep in LT_ENDPOINTS:
        try:
            r = requests.post(ep, data={"q": text, "source":"en", "target":"ru", "format":"text"},
                              headers={"Accept":"application/json"}, timeout=timeout)
            if r.status_code == 200:
                out = (r.json() or {}).get("translatedText", "")
                if out and detect_lang(out) == "ru": return out.strip()
        except Exception:
            continue
    s = text
    for k in sorted(LOCAL_EN_RU.keys(), key=lambda x: -len(x)):
        s = re.sub(rf"\b{re.escape(k)}\b", LOCAL_EN_RU[k], s, flags=re.IGNORECASE)
    if detect_lang(s) == "en":
        s = "Перевод (упрощённый): " + s
    return s
def ensure_russian(text: str) -> str:
    return translate_hard_ru(text) if detect_lang(text) == "en" else text

# ========= СУЩНОСТИ =========
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
    out += names[:5]; out += companies[:5]; out += tickers[:5]
    seen=set(); uniq=[]
    for x in out:
        if x not in seen:
            seen.add(x); uniq.append(x)
    return uniq or ["рынки","экономика"]

# ========= ГРАДИЕНТ (каждый пост — новый; +30% яркости/контраста) =========
PALETTES = [
    ((32, 44, 80), (12, 16, 28)),
    ((16, 64, 88), (8, 20, 36)),
    ((82, 30, 64), (14, 12, 24)),
    ((20, 88, 72), (8, 24, 22)),
    ((90, 60, 22), (20, 16, 12)),
    ((44, 22, 90), (16, 12, 32)),
    ((24, 26, 32), (12, 14, 18)),
]
def _boost(c, factor=1.3):
    return tuple(max(0, min(255, int(v*factor))) for v in c)
def random_gradient(w=1080, h=540):
    top, bottom = random.choice(PALETTES)
    top, bottom = _boost(top, 1.3), _boost(bottom, 1.3)  # ярче на ~30%
    angle = random.choice([0, 15, 30, 45, 60, 75, 90, 120, 135])
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    steps = max(w, h)
    for i in range(steps):
        t = i / (steps - 1)
        r = int(top[0]*(1-t) + bottom[0]*t)
        g = int(top[1]*(1-t) + bottom[1]*t)
        b = int(top[2]*(1-t) + bottom[2]*t)
        d.line([(i*w//steps,0),(i*w//steps,h)], fill=(r,g,b))
    if angle not in (90, 270):
        img = img.rotate(angle, expand=False, resample=Image.BICUBIC)
    # чуть больше контраста/насыщенности
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Brightness(img).enhance(1.05)
    # мягкая виньетка
    mask = Image.new("L",(w,h),0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-w*0.2,-h*0.4,w*1.2,h*1.4], fill=210)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=80))
    img = Image.composite(img, Image.new("RGB",(w,h),(0,0,0)), mask)
    return img

# ========= ТЕКСТ/РЕРАЙТ =========
def fetch_article_text(url, max_chars=2600):
    try:
        r = requests.get(url, headers=UA, timeout=20)
        if r.status_code != 200: return ""
        soup = BeautifulSoup(r.text, "html.parser")
        ps = soup.find_all("p")
        chunks = []
        for p in ps:
            t = p.get_text(" ", strip=True)
            if not t or len(t) < 60: continue
            if any(x in t.lower() for x in ["javascript","cookie","подпишитесь","реклама","cookies"]): continue
            chunks.append(t)
            if sum(len(c) for c in chunks) > max_chars: break
        return re.sub(r"\s+", " ", " ".join(chunks)).strip()
    except Exception:
        return ""

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
    return re.sub(r"\s+", " ", out).strip()

def split_sentences(text):
    text = re.sub(r"\s+", " ", text).strip()
    if not text: return []
    return re.split(r"(?<=[.!?])\s+", text)

def paraphrase_sentence_ru_or_en(s):
    if detect_lang(s) == "en":
        s = translate_hard_ru(s)
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
    base_raw = (article_text or "").strip() or (feed_summary or "").strip()
    base_ru = ensure_russian(base_raw)
    sents = [s for s in split_sentences(base_ru) if s]

    p1_src = sents[:2] or sents[:1]
    p2_src = sents[2:5] or sents[:1]
    p3_src = sents[5:8] or sents[1:3] or sents[:1]

    p1 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p1_src)
    p2 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p2_src)
    p3 = " ".join(paraphrase_sentence_ru_or_en(s) for s in p3_src)

    emoji = one_context_emoji(f"{title} {base_ru}")
    return f"{emoji} {clamp(p1, 320)}", clamp(p2, 360), clamp(p3, 360)

# ========= ТЕГИ (скрытые; 3–5; существительные И.п.) =========
COUNTRY_PROPER = {
    "россия":"Россия","сша":"США","китай":"Китай","япония":"Япония","германия":"Германия","франция":"Франция",
    "великобритания":"Великобритания","индия":"Индия","европа":"Европа","украина":"Украина","турция":"Турция",
}
RU_STOP = set("это тот эта которые который которой которых также чтобы при про для на из от по как уже еще или либо чем если когда где куда весь все вся его ее их наш ваш мой твой один одна одно".split())

def lemma_noun(word):
    w = word.lower()
    if MORPH:
        p = MORPH.parse(w)[0]
        if 'NOUN' in p.tag:
            nf = p.normal_form
            if nf in COUNTRY_PROPER: return COUNTRY_PROPER[nf]
            return nf
    return w

def extract_candidate_nouns(text, entities, limit=12):
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text)
    candidates = []
    for w in words:
        wl = w.lower()
        if wl in RU_STOP: continue
        candidates.append(wl)
    for e in entities:
        if re.fullmatch(r"[A-Z]{2,6}", e): candidates.append(e)
        else: candidates += e.split()
    lemmas = []
    for c in candidates:
        if re.fullmatch(r"[A-Z]{2,6}", c): lemmas.append(c)
        else:
            l = lemma_noun(c)
            if l and len(l) >= 3: lemmas.append(l)
    freq = {}
    for l in lemmas: freq[l] = freq.get(l, 0) + 1
    out = [k for k,_ in sorted(freq.items(), key=lambda x: -x[1])]
    out = [re.sub(r"[^A-Za-zА-Яа-яЁё0-9]", "", x) for x in out]
    out = [x for x in out if x and x.lower() not in RU_STOP]
    return out[:limit]

def gen_hidden_tags(title, body, entities, min_tags=3, max_tags=5):
    text_l = (title + " " + body).lower()
    thematic = []
    def tadd(x):
        if x not in thematic: thematic.append(x)
    if any(k in text_l for k in ["биткоин","bitcoin","btc","крипт","ethereum","eth","stablecoin"]): tadd("крипта")
    if any(k in text_l for k in ["доллар","usd","евро","eur","рубл","rub","юань","cny","курс","форекс"]): tadd("валюта")
    if any(k in text_l for k in ["акци","рынок","бирж","индекс","nasdaq","nyse","s&p","sp500","dow"]): tadd("рынки")
    if any(k in text_l for k in ["ставк","фрс","цб","инфляц","cpi","ppi","qe","qt"]): tadd("ставки")
    if any(k in text_l for k in ["нефть","брент","wti","opec","газ","энерги","lng"]): tadd("энергетика")
    if any(k in text_l for k in ["санкц","эмбарго","пошлин","геополит","переговор","президент"]): tadd("геополитика")

    nouns = extract_candidate_nouns(title + " " + body, entities, limit=12)
    result = []
    def add(s):
        if s and s not in result: result.append(s)
    for t in thematic: add(t)
    for n in nouns: add(COUNTRY_PROPER.get(n.lower(), n))

    tags=[]
    for t in result:
        if re.fullmatch(r"[A-Z]{2,6}", t): tags.append("#"+t)
        else:
            tags.append("#"+(t if t in COUNTRY_PROPER.values() else t.lower()))
        if len(tags) >= max_tags: break
    if len(tags) < min_tags:
        for extra in ["#рынки","#валюта","#крипта","#ставки","#энергетика","#геополитика"]:
            if extra not in tags: tags.append(extra)
            if len(tags) >= min_tags: break
    return "||" + " ".join(tags[:max_tags]) + "||"

# ========= РЕНДЕР КАРТОЧКИ (показываем время события и время поста) =========
def wrap_text_by_width(draw, text, font, max_width, max_lines=5):
    words = (text or "").split()
    lines, current = [], ""
    for w in words:
        test = (current + " " + w).strip()
        if draw.textlength(test, font=font) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
                if len(lines) >= max_lines: return lines
            current = w
    if current and len(lines) < max_lines: lines.append(current)
    return lines

def fit_title_in_box(draw, text, font_path, box_w, box_h, start_size=66, min_size=28, line_gap=8, max_lines=5):
    from PIL import ImageFont
    for size in range(start_size, min_size-1, -2):
        font = ImageFont.truetype(font_path, size)
        lines = wrap_text_by_width(draw, text, font, box_w, max_lines=max_lines)
        h_line = font.getbbox("Ag")[3]
        total_h = len(lines)*h_line + (len(lines)-1)*line_gap
        if lines and total_h <= box_h: return font, lines
    font = ImageFont.truetype(font_path, min_size)
    lines = wrap_text_by_width(draw, text, font, box_w, max_lines=max_lines)
    return font, lines

def draw_title_card(title_text, src_domain, tzname, event_dt_utc, post_dt_utc):
    W, H = 1080, 540
    bg = random_gradient(W,H)
    overlay = Image.new("RGBA", (W, H), (0,0,0,0))
    ImageDraw.Draw(overlay).rounded_rectangle([40, 110, W-40, H-90], radius=28, fill=(0,0,0,118))
    bg = Image.alpha_composite(bg.convert("RGBA"), overlay).convert("RGB")
    d = ImageDraw.Draw(bg)

    path_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    path_reg  = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    from PIL import ImageFont
    font_brand  = ImageFont.truetype(path_bold, 34)
    font_time   = ImageFont.truetype(path_reg, 22)
    font_small  = ImageFont.truetype(path_reg, 20)

    # Заголовок + шапка
    d.text((48, 26), CHANNEL_NAME, fill=(255,255,255), font=font_brand)

    try: tz = ZoneInfo(tzname)
    except Exception: tz = ZoneInfo("UTC")
    event_loc = event_dt_utc.astimezone(tz)
    post_loc  = post_dt_utc.astimezone(tz)
    event_str = event_loc.strftime("%d.%m %H:%M")
    post_str  = post_loc.strftime("%d.%m %H:%M")

    # Справа вверху — когда запостили
    right_text = f"пост: {post_str}"
    d.text((W - 48 - d.textlength(right_text, font=font_time), 28), right_text, fill=(255,255,255), font=font_time)

    # Блок заголовка
    box_x, box_y = 72, 150
    box_w, box_h = W - 2*box_x, H - box_y - 120
    font_title, lines = fit_title_in_box(d, (title_text or "").strip(), path_bold, box_w, box_h, start_size=66, min_size=30, max_lines=5)

    y = box_y
    for ln in lines:
        d.text((box_x, y), ln, font=font_title, fill=(255,255,255))
        y += font_title.getbbox("Ag")[3] + 8

    # Низы: источник, время события
    d.text((72, H - 64), f"source: {src_domain}  •  событие: {event_str}", font=font_small, fill=(230,230,230))

    bio = io.BytesIO(); bg.save(bio, format="PNG", optimize=True); bio.seek(0)
    return bio

# ========= КАПШЕН (включаем времена) =========
def build_caption(title, para1, para2, para3, link, hidden_tags, event_dt_utc, post_dt_utc):
    title = clamp(title, 200)
    dom = root_domain(link) if link else None
    body = f"{para1}\n\n{para2}\n\n{para3}"

    tz = ZoneInfo(TIMEZONE)
    ev = event_dt_utc.astimezone(tz).strftime("%d.%m %H:%M")
    po = post_dt_utc.astimezone(tz).strftime("%d.%m %H:%M")

    parts = [title, "", body]
    parts += ["", f"Время события: {ev}  •  Время поста: {po}"]
    parts += ["", f"Источник: [{dom}]({link})" if dom else "Источник: неизвестно"]
    parts += ["", f"🪙 [{CHANNEL_NAME}]({CHANNEL_LINK})"]
    if hidden_tags: parts += ["", hidden_tags]

    cap = "\n".join(parts)
    if len(cap) > 1024:
        over = len(cap) - 1024 + 3
        p3 = clamp(para3[:-min(over, len(para3))], 300)
        parts = [title, "", f"{para1}\n\n{para2}\n\n{p3}",
                 "", f"Время события: {ev}  •  Время поста: {po}",
                 "", f"Источник: [{dom}]({link})" if dom else "Источник: неизвестно",
                 "", f"🪙 [{CHANNEL_NAME}]({CHANNEL_LINK})", "", hidden_tags]
        cap = "\n".join(parts)
    return cap

# ========= ТЕЛЕГРАМ =========
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

# ========= СБОР ФИДОВ =========
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
                else:
                    dt = dt.astimezone(timezone.utc)
            except Exception:
                # если нет времени публикации — считаем сильно старым
                dt = datetime(1970,1,1, tzinfo=timezone.utc)
            if dt.year < MIN_EVENT_YEAR:
                continue
            uid = hashlib.sha256((link + "|" + title + "|" + ts).encode("utf-8")).hexdigest()
            items.append({"feed": feed_url, "link": link, "title": title or "(no title)",
                          "summary": summary, "ts": ts, "dt": dt, "uid": uid})
    return items

# ========= ОБРАБОТКА ОДНОЙ НОВОСТИ =========
def process_item(item, now_utc):
    link, title, feed_summary, event_dt = item["link"], item["title"], item["summary"], item["dt"]
    # Перевод заголовка
    title_ru = ensure_russian(title)
    # Текст статьи
    article_text = fetch_article_text(link, max_chars=2600)
    # Абзацы
    p1, p2, p3 = build_three_paragraphs_scientific(title_ru, article_text, ensure_russian(feed_summary))
    # Теги
    entities_for_tags = extract_entities(title_ru, f"{p1} {p2} {p3}")
    hidden_tags = gen_hidden_tags(title_ru, f"{p1} {p2} {p3}", entities_for_tags, min_tags=3, max_tags=5)
    # Карточка с указанием времени события и поста
    card = draw_title_card(title_ru, domain(link or ""), TIMEZONE, event_dt, now_utc)
    caption = build_caption(title_ru, p1, p2, p3, link or "", hidden_tags, event_dt, now_utc)
    resp = send_photo(card, caption)
    print(f"Posted: {title_ru[:80]} | event={event_dt.isoformat()} | tags={hidden_tags}")
    return resp

# ========= MAIN =========
def trim_posted(posted_set, keep_last=1000):
    if len(posted_set) <= keep_last: return posted_set
    return set(list(posted_set)[-keep_last:])

def main():
    state = load_state()
    posted = set(state.get("posted_uids", []))

    items = collect_entries()
    if not items:
        print("No entries found."); return

    now_utc = datetime.now(timezone.utc)
    lookback_dt  = now_utc - timedelta(minutes=LOOKBACK_MINUTES)
    fresh_cutoff = now_utc - timedelta(minutes=FRESH_WINDOW_MIN)

    # Только свежие + не постили ранее
    fresh = [it for it in items
             if it["dt"] >= fresh_cutoff and it["dt"] >= lookback_dt and it["uid"] not in posted]

    # Сортировка по времени публикации (самые новые сверху)
    fresh.sort(key=lambda x: x["dt"], reverse=True)

    # Ограничим пачку на прогон
    to_post = fresh[:MAX_POSTS_PER_RUN]
    if not to_post:
        print("Nothing new in the fresh window."); return

    for it in to_post:
        try:
            process_item(it, now_utc)
            posted.add(it["uid"])
            time.sleep(1.0)
        except Exception as e:
            print("Error sending:", e)

    state["posted_uids"] = list(trim_posted(posted))
    save_state(state)

if __name__ == "__main__":
    main()
