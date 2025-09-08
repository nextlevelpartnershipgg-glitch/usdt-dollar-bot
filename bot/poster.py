# bot/poster.py
import os, io, json, time, pathlib, hashlib, urllib.parse, random, re
from datetime import datetime, timezone, timedelta
from dateutil import parser as dtparse
import feedparser, requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
from zoneinfo import ZoneInfo

# ========= НАСТРОЙКИ =========
BOT_TOKEN  = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@usdtdollarm")
TIMEZONE   = os.environ.get("TIMEZONE", "Europe/Moscow")

CHANNEL_NAME   = os.environ.get("CHANNEL_NAME", "USDT=Dollar")
CHANNEL_HANDLE = os.environ.get("CHANNEL_HANDLE", "@usdtdollarm")
CHANNEL_LINK   = os.environ.get("CHANNEL_LINK", f"https://t.me/{CHANNEL_HANDLE.lstrip('@')}")

MAX_POSTS_PER_RUN  = int(os.environ.get("MAX_POSTS_PER_RUN", "1"))
LOOKBACK_MINUTES   = int(os.environ.get("LOOKBACK_MINUTES", "30"))
FRESH_WINDOW_MIN   = int(os.environ.get("FRESH_WINDOW_MIN", "25"))
MIN_EVENT_YEAR     = int(os.environ.get("MIN_EVENT_YEAR", "2023"))

FALLBACK_ON_NO_FRESH = os.environ.get("FALLBACK_ON_NO_FRESH", "1") == "1"
FALLBACK_WINDOW_MIN  = int(os.environ.get("FALLBACK_WINDOW_MIN", "360"))  # 6 часов
ALWAYS_POST          = os.environ.get("ALWAYS_POST", "1") == "1"

DATA_DIR = pathlib.Path("data"); DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE   = DATA_DIR / "state.json"
HISTORY_FILE = DATA_DIR / "history.json"

UA = {"User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"}

# ========= ИСТОЧНИКИ (РФ + мир; без РИА) =========
# ===== Текст: капс первой буквы + баланс скобок/кавычек =====
def _smart_capitalize(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s:
        return s
    m = re.search(r"[A-Za-zА-Яа-яЁё]", s)
    if not m:
        return s
    i = m.start()
    return s[:i] + s[i].upper() + s[i+1:]

def _remove_unmatched(s: str, open_ch: str, close_ch: str) -> str:
    bal = 0
    out = []
    for ch in s:
        if ch == open_ch:
            bal += 1
            out.append(ch)
        elif ch == close_ch:
            if bal == 0:
                # лишняя закрывающая — выкидываем
                continue
            bal -= 1
            out.append(ch)
        else:
            out.append(ch)
    # если остались незакрытые — добавим закрывающие в конец
    if bal > 0:
        out.append(close_ch * bal)
    return "".join(out)

def _balance_brackets_and_quotes(s: str) -> str:
    s = _remove_unmatched(s, "(", ")")
    s = _remove_unmatched(s, "[", "]")
    # русские «ёлочки»
    opens = s.count("«"); closes = s.count("»")
    if closes > opens:
        # выкинуть лишние закрывающие «»
        need = opens
        buf = []
        seen = 0
        for ch in s:
            if ch == "»":
                if seen >= need:
                    continue
                seen += 1
            buf.append(ch)
        s = "".join(buf)
    elif opens > closes:
        s += "»" * (opens - closes)
    return s

def tidy_paragraph(p: str) -> str:
    p = (p or "").strip()
    if not p:
        return p
    p = _balance_brackets_and_quotes(p)
    p = _smart_capitalize(p)
    return p

# ========= ИСТОЧНИКИ (только РФ, все на русском) =========
RSS_FEEDS_RU = [
    "https://rssexport.rbc.ru/rbcnews/news/30/full.rss",
    "https://rssexport.rbc.ru/rbcnews/economics/30/full.rss",
    "https://rssexport.rbc.ru/rbcnews/finance/30/full.rss",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://www.kommersant.ru/RSS/economics.xml",
    "https://www.kommersant.ru/RSS/finance.xml",
    "https://lenta.ru/rss/news",
    "https://lenta.ru/rss/economics",
    "https://lenta.ru/rss/russia",
    "https://lenta.ru/rss/world",
    "https://tass.ru/rss/v2.xml",
    "https://tass.ru/economy/rss",
    "https://tass.ru/politika/rss",
    "https://www.vedomosti.ru/rss/news",
    "https://www.interfax.ru/rss.asp",
    "https://www.gazeta.ru/export/rss/first.xml",
    "https://www.gazeta.ru/export/rss/business.xml",
    "https://iz.ru/xml/rss/all.xml",
    "https://www.finmarket.ru/rss/news.asp",
    "https://www.banki.ru/xml/news.rss",
    "https://1prime.ru/export/rss2/index.xml",
    "https://rg.ru/tema/ekonomika/rss.xml",
    "https://www.forbes.ru/newrss.xml",
    "https://www.mskagency.ru/rss/all",
    "https://www.ng.ru/rss/",
    "https://www.mk.ru/rss/finance/index.xml",
    "https://www.kommersant.ru/RSS/regions.xml",
    "https://www.kommersant.ru/RSS/tech.xml",
    "https://www.fontanka.ru/fontanka.rss",
    "https://minfin.gov.ru/ru/press-center/?rss=Y",
    "https://cbr.ru/StaticHtml/Rss/Press",
    "https://www.moex.com/Export/MRSS/News",
]

# Мировые источники убираем:
RSS_FEEDS_WORLD = []

# Используем только RU:
RSS_FEEDS = RSS_FEEDS_RU

# ========= Pymorphy (опционально) =========
try:
    import pymorphy2
    MORPH = pymorphy2.MorphAnalyzer()
except Exception:
    MORPH = None

# ========= Утилиты =========
def load_state():
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}

def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []

def append_history(entry):
    hist = load_history()
    hist.append(entry)
    HISTORY_FILE.write_text(json.dumps(hist, ensure_ascii=False, indent=2), encoding="utf-8")

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

# ========= Перевод EN→RU =========
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
    "alternative":"альтернатива","geopolitical":"геополитический","risk":"риск","risks":"риски",
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


# ===== Короткий аналитический вывод (1–2 фразы, без домыслов) =====
def _sentiment_hint(text: str) -> str:
    t = (text or "").lower()
    neg = any(k in t for k in [
        "паден", "снижен", "сокращ", "штраф", "санкц", "убыт", "дефиц",
        "отзыв", "кризис", "неустойчив", "замедлен"
    ])
    pos = any(k in t for k in [
        "рост", "увелич", "расшир", "рекорд", "одобрен", "прибыл",
        "планирует", "ускорен", "улучшен", "повышен"
    ])
    if pos and not neg:
        return "нейтрально-позитивная"
    if neg and not pos:
        return "нейтрально-негативная"
    return "нейтральная"

def generate_brief_analysis(title_ru: str, p1: str, p2: str, p3: str) -> str:
    """
    Делает короткий вывод по фактам из текста (без новых сведений).
    Тон: деловой/нейтральный. 1–2 предложения.
    """
    body = " ".join([p1 or "", p2 or "", p3 or ""])
    mood = _sentiment_hint(body)

    # базовая тема по ключам
    topic = "рынок"
    tl = (title_ru + " " + body).lower()
    if any(w in tl for w in ["ставк", "цб", "фрс", "инфляц"]):
        topic = "денежно-кредитная политика"
    elif any(w in tl for w in ["нефть", "газ", "opec", "брент", "энерги", "lng"]):
        topic = "энергетика"
    elif any(w in tl for w in ["акци", "бирж", "индекс", "nasdaq", "moex", "s&p", "облигац"]):
        topic = "финансовые рынки"
    elif any(w in tl for w in ["крипт", "биткоин", "bitcoin", "eth", "стейбл"]):
        topic = "крипторынок"
    elif any(w in tl for w in ["бюджет", "налог", "минфин", "расход", "доход"]):
        topic = "госфинансы"
    elif any(w in tl for w in ["ввп", "безработ", "делов", "производств", "экспорт", "импорт"]):
        topic = "макроэкономика"

    a1 = f"Итог ({topic}, {mood}): формально описанное событие отражает текущую динамику без добавления новых рисков."
    a2 = "Критично наблюдать за следующими релизами и официальными комментариями для подтверждения тренда."
    return a1 + " " + a2


# ========= Сущности/теги =========
COMPANY_HINTS = ["Apple","Microsoft","Tesla","Meta","Google","Alphabet","Amazon","Nvidia","Samsung","Intel","Huawei",
                 "Газпром","Сбербанк","Яндекс","Роснефть","Лукойл","Норникель","Татнефть","Новатэк","ВТБ","Сургутнефтегаз"]
TICKER_PAT = re.compile(r"\b[A-Z]{2,6}\b")

RU_STOP=set("это тот эта которые который которой которых также чтобы при про для на из от по как уже еще или либо чем если когда где куда весь все вся его ее их наш ваш мой твой один одна одно".split())
COUNTRY_PROPER={"россия":"Россия","сша":"США","китай":"Китай","япония":"Япония","германия":"Германия","франция":"Франция",
                "великобритания":"Великобритания","индия":"Индия","европа":"Европа","украина":"Украина","турция":"Турция"}

def extract_entities(title, summary):
    text = f"{title} {summary}".strip()
    names = re.findall(r"(?:[A-ZА-ЯЁ][a-zа-яё]+(?:\s+[A-ZА-ЯЁ][a-zа-яё]+){0,2})", text)
    tickers = [m for m in TICKER_PAT.findall(text) if m not in ("NEWS","HTTP","HTTPS","HTML")]
    companies = [c for c in COMPANY_HINTS if c.lower() in text.lower()]
    stop = {"The","This"}
    names = [x for x in names if x not in stop and len(x) > 2]
    out = []; out += names[:5]; out += companies[:5]; out += tickers[:5]
    seen=set(); uniq=[]
    for x in out:
        if x not in seen: seen.add(x); uniq.append(x)
    return uniq or ["рынки","экономика"]

def lemma_noun(word):
    w=word.lower()
    try:
        if MORPH:
            p=MORPH.parse(w)[0]
            if "NOUN" in p.tag:
                nf=p.normal_form
                return COUNTRY_PROPER.get(nf, nf)
    except Exception:
        pass
    return w

def extract_candidate_nouns(text, entities, limit=12):
    words=re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", text)
    candidates=[w.lower() for w in words if w.lower() not in RU_STOP]
    for e in entities:
        if re.fullmatch(r"[A-Z]{2,6}", e): candidates.append(e)
        else: candidates += e.split()
    lemmas=[]
    for c in candidates:
        if re.fullmatch(r"[A-Z]{2,6}", c): lemmas.append(c)
        else:
            l=lemma_noun(c)
            if l and len(l)>=3: lemmas.append(l)
    freq={}
    for l in lemmas: freq[l]=freq.get(l,0)+1
    out=[k for k,_ in sorted(freq.items(), key=lambda x: -x[1])]
    out=[re.sub(r"[^A-Za-zА-Яа-яЁё0-9]","",x) for x in out]
    out=[x for x in out if x and x.lower() not in RU_STOP]
    return out[:limit]

def gen_hidden_tags(title, body, entities, min_tags=3, max_tags=5):
    text_l = (title + " " + body).lower()
    thematic=[]
    def tadd(x):
        if x not in thematic: thematic.append(x)
    if any(k in text_l for k in ["биткоин","bitcoin","btc","крипт","ethereum","eth","stablecoin"]): tadd("крипта")
    if any(k in text_l for k in ["доллар","usd","евро","eur","рубл","rub","юань","cny","курс","форекс"]): tadd("валюта")
    if any(k in text_l for k in ["акци","рынок","бирж","индекс","nasdaq","nyse","s&p","sp500","dow"]): tadd("рынки")
    if any(k in text_l for k in ["ставк","фрс","цб","инфляц","cpi","ppi","qe","qt"]): tadd("ставки")
    if any(k in text_l for k in ["нефть","брент","wti","opec","газ","энерги","lng"]): tadd("энергетика")
    if any(k in text_l for k in ["санкц","эмбарго","пошлин","геополит","переговор","президент"]): tadd("геополитика")
    nouns=extract_candidate_nouns(title+" "+body, entities, limit=12)
    result=[]
    def add(s):
        if s and s not in result: result.append(s)
    for t in thematic: add(t)
    for n in nouns: add(COUNTRY_PROPER.get(n.lower(), n))
    tags=[]
    for t in result:
        if re.fullmatch(r"[A-Z]{2,6}", t): tags.append("#"+t)
        else: tags.append("#"+(t if t in COUNTRY_PROPER.values() else t.lower()))
        if len(tags)>=max_tags: break
    if len(tags)<min_tags:
        for extra in ["#рынки","#валюта","#крипта","#ставки","#энергетика","#геополитика"]:
            if extra not in tags: tags.append(extra)
            if len(tags)>=min_tags: break
    return "||"+" ".join(tags[:max_tags])+"||"

# ========= Градиент (ярче ~30%) =========
PALETTES = [((32,44,80),(12,16,28)),((16,64,88),(8,20,36)),((82,30,64),(14,12,24)),
            ((20,88,72),(8,24,22)),((90,60,22),(20,16,12)),((44,22,90),(16,12,32)),((24,26,32),(12,14,18))]
def _boost(c, factor=1.3): return tuple(max(0, min(255, int(v*factor))) for v in c)
def random_gradient(w=1080, h=540):
    top,bottom = random.choice(PALETTES)
    top, bottom = _boost(top,1.3), _boost(bottom,1.3)
    angle = random.choice([0,15,30,45,60,75,90,120,135])
    img = Image.new("RGB",(w,h)); d=ImageDraw.Draw(img)
    steps = max(w,h)
    for i in range(steps):
        t = i/(steps-1)
        r = int(top[0]*(1-t) + bottom[0]*t)
        g = int(top[1]*(1-t) + bottom[1]*t)
        b = int(top[2]*(1-t) + bottom[2]*t)
        d.line([(i*w//steps,0),(i*w//steps,h)], fill=(r,g,b))
    if angle not in (90,270):
        img = img.rotate(angle, expand=False, resample=Image.BICUBIC)
    img = ImageEnhance.Contrast(img).enhance(1.15)
    img = ImageEnhance.Brightness(img).enhance(1.05)
    mask = Image.new("L",(w,h),0)
    md = ImageDraw.Draw(mask)
    md.ellipse([-w*0.2,-h*0.4,w*1.2,h*1.4], fill=210)
    mask = mask.filter(ImageFilter.GaussianBlur(radius=80))
    img = Image.composite(img, Image.new("RGB",(w,h),(0,0,0)), mask)
    return img

# ========= Рерайт/парсинг =========
RU_TONE_REWRITE=[(r"\bсказал(а|и)?\b","сообщил\\1"),(r"\bзаявил(а|и)?\b","отметил\\1"),
                 (r"\bпо словам\b","по данным"),(r"\bпо мнению\b","согласно оценкам"),
                 (r"\bпримерно\b","порядка"),(r"\bочень\b","существенно"),(r"\bсильно\b","значительно")]
def ru_scientific_paraphrase(s):
    out=s
    for pat,repl in RU_TONE_REWRITE: out=re.sub(pat,repl,out,flags=re.IGNORECASE)
    out=re.sub(r"\s+%","%",out)
    return re.sub(r"\s+"," ",out).strip()
def split_sentences(text):
    text=re.sub(r"\s+"," ",text or "").strip()
    return re.split(r"(?<=[.!?])\s+", text) if text else []
def paraphrase_sentence_ru_or_en(s):
    if detect_lang(s)=="en":
        s=translate_hard_ru(s)
    return ru_scientific_paraphrase(s)

def one_context_emoji(context):
    t=(context or "").lower()
    if any(k in t for k in ["биткоин","crypto","btc","ethereum","крипт"]): return "🪙"
    if any(k in t for k in ["акци","индекс","рынок","бирж","nasdaq","nyse","s&p"]): return "📈"
    if any(k in t for k in ["доллар","рубл","валют","курс","евро","юань","usd","eur","cny"]): return "💵"
    if any(k in t for k in ["ставк","фрс","цб","центробанк","инфляц","cpi","ppi"]): return "🏦"
    if any(k in t for k in ["нефть","брент","wti","opec","газ","lng","энерги"]): return "🛢️"
    if any(k in t for k in ["золото","xau","металл","серебро"]): return "🥇"
    if any(k in t for k in ["санкц","эмбарго","пошлин","геополит","переговор","президент"]): return "🏛️"
    return "📰"

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
            if not t or len(t) < 60:
                continue
            if any(x in t.lower() for x in ["javascript","cookie","подпишитесь","реклама","cookies"]):
                continue
            chunks.append(t)
            if sum(len(c) for c in chunks) > max_chars:
                break
        return re.sub(r"\s+", " ", " ".join(chunks)).strip()
    except Exception:
        return ""

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

    # Аккуратим абзацы: заглавная буква, баланс скобок/кавычек
    p1 = tidy_paragraph(f"{emoji} {p1}".strip())
    p2 = tidy_paragraph(p2.strip())
    p3 = tidy_paragraph(p3.strip())

    return p1, p2, p3

# ========= Рендер карточки =========
def wrap_text_by_width(draw, text, font, max_width, max_lines=5):
    words=(text or "").split(); lines=[]; cur=""
    for w in words:
        test=(cur+" "+w).strip()
        if draw.textlength(test,font=font)<=max_width: cur=test
        else:
            if cur:
                lines.append(cur)
                if len(lines)>=max_lines: return lines
            cur=w
    if cur and len(lines)<max_lines: lines.append(cur)
    return lines
def fit_title_in_box(draw, text, font_path, box_w, box_h, start=66, min_s=28, line_gap=8, max_lines=5):
    for size in range(start, min_s-1, -2):
        font=ImageFont.truetype(font_path,size)
        lines=wrap_text_by_width(draw,text,font,box_w,max_lines=max_lines)
        h=font.getbbox("Ag")[3]; total=len(lines)*h+(len(lines)-1)*line_gap
        if lines and total<=box_h: return font,lines
    font=ImageFont.truetype(font_path,min_s)
    lines=wrap_text_by_width(draw,text,font,box_w,max_lines=max_lines)
    return font,lines
def draw_title_card(title_text, src_domain, tzname, event_dt_utc, post_dt_utc):
    W,H=1080,540
    bg=random_gradient(W,H)
    overlay=Image.new("RGBA",(W,H),(0,0,0,0))
    ImageDraw.Draw(overlay).rounded_rectangle([40,110,W-40,H-90], radius=28, fill=(0,0,0,118))
    bg=Image.alpha_composite(bg.convert("RGBA"),overlay).convert("RGB")
    d=ImageDraw.Draw(bg)
    path_bold="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    path_reg ="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    f_brand=ImageFont.truetype(path_bold,34)
    f_time =ImageFont.truetype(path_reg,22)
    f_small=ImageFont.truetype(path_reg,20)
    d.text((48,26),CHANNEL_NAME,fill=(255,255,255),font=f_brand)
    try: tz=ZoneInfo(tzname)
    except Exception: tz=ZoneInfo("UTC")
    ev=event_dt_utc.astimezone(tz).strftime("%d.%m %H:%M")
    po=post_dt_utc.astimezone(tz).strftime("%d.%m %H:%M")
    right=f"пост: {po}"
    d.text((W-48-d.textlength(right,font=f_time),28),right,fill=(255,255,255),font=f_time)
    box_x,box_y=72,150
    box_w,box_h=W-2*box_x, H-box_y-120
    f_title,lines=fit_title_in_box(d,(title_text or "").strip(),path_bold,box_w,box_h, start=66,min_s=30,max_lines=5)
    y=box_y
    for ln in lines:
        d.text((box_x,y),ln,font=f_title,fill=(255,255,255))
        y+=f_title.getbbox("Ag")[3]+8
    d.text((72,H-64),f"source: {src_domain}  •  событие: {ev}",font=f_small,fill=(230,230,230))
    bio=io.BytesIO(); bg.save(bio,format="PNG",optimize=True); bio.seek(0); return bio

# ========= HTML caption: экранирование и умное сокращение =========
def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def smart_join_and_trim(paragraphs, max_len=1024):
    raw = "\n\n".join([p for p in paragraphs if p])
    if len(raw) <= max_len:
        return raw
    cut = raw[:max_len]
    for sep in [". ", "! ", "? ", "… ", ".\n", "!\n", "?\n", "…\n"]:
        pos = cut.rfind(sep)
        if pos != -1:
            return cut[:pos+1].rstrip()
    return cut[:-1].rstrip() + "…"

def build_full_caption(title, p1, p2, p3, link, hidden_tags):
    dom = root_domain(link) if link else "источник"

    # Заголовок жирным
    title_html = f"<b>{html_escape(title)}</b>"

    # Тело — без <br>, используем обычные переводы строк
    body_plain = smart_join_and_trim([p1, p2, p3], max_len=1024 - 220)
    body_html = html_escape(body_plain)  # переносы уже \n\n

    footer = [
    f'Источник: <a href="{html_escape(link)}">{html_escape(dom)}</a>',
    f'🪙 <a href="{html_escape(CHANNEL_LINK)}">{html_escape(CHANNEL_NAME)}</a>'
]


    caption_no_tags = f"{title_html}\n\n{body_html}\n\n" + "\n".join(footer)

    # скрытые теги как спойлер
    if hidden_tags:
        inner = hidden_tags.strip("|")  # "||#a #b||" -> "#a #b"
        spoiler_html = f'\n\n<span class="tg-spoiler">{html_escape(inner)}</span>'
        if len(caption_no_tags + spoiler_html) <= 1024:
            return caption_no_tags + spoiler_html

    return caption_no_tags


def send_photo_with_caption(photo_bytes, caption):
    if not BOT_TOKEN:
        raise RuntimeError("Нет BOT_TOKEN (добавь секреты в Settings → Secrets → Actions)")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
    r = requests.post(url, files=files, data=data, timeout=30)
    print("Telegram sendPhoto:", r.status_code, r.text[:200])
    r.raise_for_status()
    return r.json()

# ========= Сбор фидов =========
def collect_entries():
    items=[]
    for feed_url in RSS_FEEDS:
        try:
            fp=feedparser.parse(feed_url)
        except Exception:
            continue
        for e in fp.entries or []:
            link=getattr(e,"link","") or ""
            title=(getattr(e,"title","") or "").strip()
            summary=clean_html(getattr(e,"summary",getattr(e,"description","")))
            ts=getattr(e,"published",getattr(e,"updated","")) or ""
            try:
                dt=dtparse.parse(ts)
                if not dt.tzinfo: dt=dt.replace(tzinfo=timezone.utc)
                else: dt=dt.astimezone(timezone.utc)
            except Exception:
                dt=datetime(1970,1,1,tzinfo=timezone.utc)
            if dt.year<MIN_EVENT_YEAR: continue
            uid=hashlib.sha256((link+"|"+title+"|"+ts).encode("utf-8")).hexdigest()
            items.append({"feed":feed_url,"link":link,"title":title or "(no title)",
                          "summary":summary,"ts":ts,"dt":dt,"uid":uid})
    return items

# ========= Фильтр "пустых" новостей =========
def is_low_quality(title_ru, p1, p2, p3, min_total=280):
    text = (p1 + " " + p2 + " " + p3).strip()
    if len(text) < min_total:
        return True
    core = re.sub(r"[«»\"'”“]", "", title_ru).lower()
    dup_score = sum(1 for x in [p1,p2,p3] if core[:40] in x.lower())
    return dup_score >= 2

# ========= Процесс одной новости =========
def process_item(item, now_utc):
    link, title, feed_summary, event_dt = item["link"], item["title"], item["summary"], item["dt"]
    title_ru = ensure_russian(title)
    article_text = fetch_article_text(link, max_chars=2600)
    p1, p2, p3 = build_three_paragraphs_scientific(title_ru, article_text, ensure_russian(feed_summary))

    if is_low_quality(title_ru, p1, p2, p3):
        print("Skip low-quality item:", title_ru[:80])
        return None

    entities = extract_entities(title_ru, f"{p1} {p2} {p3}")
    hidden_tags = gen_hidden_tags(title_ru, f"{p1} {p2} {p3}", entities, min_tags=3, max_tags=5)

    card = draw_title_card(title_ru, domain(link or ""), TIMEZONE, event_dt, now_utc)
    caption = build_full_caption(title_ru, p1, p2, p3, link or "", hidden_tags)
    resp = send_photo_with_caption(card, caption)

    append_history({
        "uid": item["uid"], "title": title_ru, "link": link,
        "event_utc": event_dt.isoformat(), "posted_utc": now_utc.isoformat(),
        "tags": hidden_tags
    })
    print(f"Posted: {title_ru[:80]}")
    return resp

# ========= MAIN =========
def trim_posted(posted_set, keep_last=1500):
    if len(posted_set)<=keep_last: return posted_set
    return set(list(posted_set)[-keep_last:])

def main():
    state=load_state()
    posted=set(state.get("posted_uids", []))
    items=collect_entries()
    if not items:
        print("No entries."); return

    now_utc=datetime.now(timezone.utc)
    lookback_dt=now_utc - timedelta(minutes=LOOKBACK_MINUTES)
    fresh_cutoff=now_utc - timedelta(minutes=FRESH_WINDOW_MIN)
    fresh=[it for it in items if it["dt"]>=fresh_cutoff and it["dt"]>=lookback_dt and it["uid"] not in posted]
    fresh.sort(key=lambda x: x["dt"], reverse=True)

    to_post = fresh[:MAX_POSTS_PER_RUN]

    if not to_post and FALLBACK_ON_NO_FRESH:
        fallback_cutoff = now_utc - timedelta(minutes=FALLBACK_WINDOW_MIN)
        candidates = [it for it in items if it["uid"] not in posted and it["dt"] >= fallback_cutoff]
        candidates.sort(key=lambda x: x["dt"], reverse=True)
        to_post = candidates[:MAX_POSTS_PER_RUN]
        if to_post:
            print(f"Fallback used: took newest item(s) within {FALLBACK_WINDOW_MIN} min.")

    if not to_post and ALWAYS_POST:
        anyc = [it for it in items if it["uid"] not in posted]
        anyc.sort(key=lambda x: x["dt"], reverse=True)
        to_post = anyc[:MAX_POSTS_PER_RUN]
        if to_post:
            print("ALWAYS_POST used: took newest item.")

    if not to_post:
        print("Nothing to post."); return

    posted_any = False
    for it in to_post:
        try:
            resp = process_item(it, now_utc)
            if resp:
                posted.add(it["uid"])
                posted_any = True
            time.sleep(1.0)
        except Exception as e:
            print("Error sending:", e)

    if posted_any:
        state["posted_uids"]=list(trim_posted(posted))
        save_state(state)

if __name__=="__main__":
    main()
