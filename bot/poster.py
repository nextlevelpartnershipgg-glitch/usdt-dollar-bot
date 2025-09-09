# bot/poster.py
import os, io, re, random, time, json, hashlib, urllib.parse
from datetime import datetime
import requests, feedparser
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ========= Конфиг из Secrets / Env =========
BOT_TOKEN   = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID  = os.getenv("CHANNEL_ID", "").strip()          # @имя_канала
MAX_POSTS_PER_RUN   = int(os.getenv("MAX_POSTS_PER_RUN", "1"))
HTTP_TIMEOUT        = 12
LOW_QUALITY_MIN_LEN = int(os.getenv("LOW_QUALITY_MIN_LEN", "200"))
ALLOW_BACKLOG       = os.getenv("ALLOW_BACKLOG", "1") == "1"     # брать из запаса, если свежих нет
LOGO_PATH = os.getenv("LOGO_PATH", "bot/logo.png")               # опционально: bot/logo.png

# ========= Источники (русские, без РИА) =========
RSS_FEEDS = [
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

# ========= Состояние (анти-дубликаты) =========
STATE_PATH = "data/state.json"

def _norm_url(u: str) -> str:
    if not u: return ""
    p = urllib.parse.urlsplit(u)
    q = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
    q = [(k, v) for (k, v) in q if not k.lower().startswith(("utm_", "yclid", "gclid", "fbclid"))]
    return urllib.parse.urlunsplit((p.scheme, p.netloc.lower(), p.path, urllib.parse.urlencode(q), ""))

def _uid_for(link: str, title: str) -> str:
    key = _norm_url(link) or (title or "").strip().lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

def _load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"posted": []}

def _save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

# ========= Текстовые утилиты =========
def detect_lang(text: str) -> str:
    return "ru" if re.search(r"[А-Яа-яЁё]", text or "") else "non-ru"

def split_sentences(text: str):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text: return []
    return re.split(r"(?<=[.!?])\s+", text)

def _smart_capitalize(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    if not s: return s
    m = re.search(r"[A-Za-zА-Яа-яЁё]", s)
    if not m: return s
    i = m.start()
    return s[:i] + s[i].upper() + s[i+1:]

def _remove_unmatched(s: str, open_ch: str, close_ch: str) -> str:
    bal, out = 0, []
    for ch in s:
        if ch == open_ch:
            bal += 1; out.append(ch)
        elif ch == close_ch:
            if bal == 0: continue
            bal -= 1; out.append(ch)
        else:
            out.append(ch)
    if bal > 0: out.append(close_ch * bal)
    return "".join(out)

def _balance_brackets_and_quotes(s: str) -> str:
    s = _remove_unmatched(s, "(", ")")
    s = _remove_unmatched(s, "[", "]")
    opens = s.count("«"); closes = s.count("»")
    if closes > opens:
        need = opens; buf=[]; seen=0
        for ch in s:
            if ch == "»":
                if seen >= need: continue
                seen += 1
            buf.append(ch)
        s = "".join(buf)
    elif opens > closes:
        s += "»" * (opens - closes)
    return s

def tidy_paragraph(p: str) -> str:
    p = (p or "").strip()
    if not p: return p
    p = _balance_brackets_and_quotes(p)
    p = _smart_capitalize(p)
    return p

RU_STOP = set("это этот эта эти такой такая такое такие как по при про для на из от или либо ещё уже если когда куда где чем что чтобы и в во а но же тот та то те к с о об".split())

def extract_tags_source(text, min_tags=3, max_tags=5):
    words = re.findall(r"[A-Za-zА-Яа-яЁё]{3,}", (text or "").lower())
    words = [re.sub(r"[^a-zа-яё]", "", w) for w in words]
    freq = {}
    for w in words:
        if w and w not in RU_STOP:
            freq[w] = freq.get(w, 0) + 1
    top = [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])]
    tags = []
    for w in top:
        if len(tags) >= max_tags: break
        if w not in tags: tags.append(w)
    while len(tags) < min_tags and "рынки" not in tags: tags.append("рынки")
    return "||" + " ".join("#"+t for t in tags[:max_tags]) + "||"

# ========= Тематические палитры и визуал =========
PALETTES_GENERAL = [((28,42,74),(12,18,30)), ((18,64,96),(8,24,36)), ((84,32,68),(18,12,28))]
PALETTES_ECON    = [((6,86,70),(4,40,36)), ((16,112,84),(8,36,28))]
PALETTES_CRYPTO  = [((36,44,84),(16,18,40)), ((32,110,92),(14,28,32))]
PALETTES_POLIT   = [((98,36,36),(24,12,14)), ((52,22,90),(16,12,34))]
PALETTES_ENERGY  = [((124,72,16),(22,16,10)), ((88,46,18),(16,12,10))]
PALETTES_TRAGIC  = [((40,40,40),(8,8,10)), ((54,54,64),(14,14,20))]

def pick_palette(title_summary: str):
    t = (title_summary or "").lower()
    if any(k in t for k in ["взрыв","катастроф","авар","погиб","бомб","теракт","шторм","урага"]):
        base = PALETTES_TRAGIC
    elif any(k in t for k in ["нефть","газ","opec","брент","энерги","уголь","электро"]):
        base = PALETTES_ENERGY
    elif any(k in t for k in ["ставк","цб","инфляц","cpi","ppi","налог","бюджет","ввп"]):
        base = PALETTES_ECON
    elif any(k in t for k in ["крипт","биткоин","bitcoin","eth","стейбл","тезер","usdt"]):
        base = PALETTES_CRYPTO
    elif any(k in t for k in ["выборы","санкц","парламент","правительств","президент","мид"]):
        base = PALETTES_POLIT
    else:
        base = PALETTES_GENERAL
    return random.choice(base)

def _boost(c, k=1.3): return tuple(max(0, min(255, int(v*k))) for v in c)

def generate_gradient(size=(1080, 540), title_summary: str = ""):
    W,H = size
    top, bottom = pick_palette(title_summary)
    top, bottom = _boost(top,1.3), _boost(bottom,1.3)
    img = Image.new("RGB", (W,H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y/(H-1)
        r = int(top[0]*(1-t) + bottom[0]*t)
        g = int(top[1]*(1-t) + bottom[1]*t)
        b = int(top[2]*(1-t) + bottom[2]*t)
        d.line([(0,y),(W,y)], fill=(r,g,b))
    # лёгкая диагональная текстура
    overlay = Image.new("RGBA",(W,H),(0,0,0,0))
    od = ImageDraw.Draw(overlay)
    step = 20
    for x in range(-H, W, step):
        od.line([(x,0),(x+H,H)], fill=(255,255,255,18), width=1)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    return img

def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        return ImageFont.load_default()

def wrap_by_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int, max_lines=5):
    words = (text or "").split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
                if len(lines) >= max_lines: return lines
            cur = w
    if cur and len(lines) < max_lines: lines.append(cur)
    return lines

def _safe_open_logo():
    if not os.path.exists(LOGO_PATH):
        return None
    try:
        img = Image.open(LOGO_PATH).convert("RGBA")
        size = min(img.size)
        img = ImageOps.fit(img, (size, size), method=Image.LANCZOS)
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0,0,size,size), fill=255)
        img.putalpha(mask)
        return img
    except Exception:
        return None

def _draw_fallback_coin(size=72):
    r = size//2
    img = Image.new("RGBA", (size,size), (0,0,0,0))
    d = ImageDraw.Draw(img)
    d.ellipse((0,0,size,size), fill=(210,210,210,255))
    d.ellipse((6,6,size-6,size-6), fill=(235,235,235,255))
    font = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size//2)
    w = d.textlength("$", font=font)
    d.text(((size-w)/2, size*0.24), "$", font=font, fill=(60,60,60,255))
    return img

def draw_card(title: str, source_domain: str, post_stamp: str, themed_hint: str = "") -> io.BytesIO:
    W,H = 1080, 540
    base = generate_gradient((W,H), title_summary=themed_hint).convert("RGBA")

    # верхняя плашка
    header = Image.new("RGBA", (W, 84), (0,0,0,80))
    base.alpha_composite(header, (0,0))

    d = ImageDraw.Draw(base)
    font_bold = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 36)
    font_reg  = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)

    # логотип
    logo = _safe_open_logo() or _draw_fallback_coin(72)
    base.alpha_composite(logo, (36, 6))

    # название канала и время поста
    d.text((120, 22), "USDT=Dollar", font=font_bold, fill=(255,255,255,255))
    right = f"пост: {post_stamp}"
    d.text((W-36-d.textlength(right,font=font_reg), 28), right, font=font_reg, fill=(255,255,255,230))

    # подложка под заголовок
    pad = Image.new("RGBA", (W-72, H-84-86), (0,0,0,110))
    base.alpha_composite(pad, (36, 100))

    # заголовок
    title = (title or "").strip()
    box_x, box_y = 64, 124
    box_w, box_h = W-2*box_x, H- box_y - 132
    size = 64; lines = []
    while size >= 28:
        f = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        lines = wrap_by_width(d, title, f, box_w, max_lines=4)
        line_h = f.getbbox("Ag")[3]
        total_h = len(lines)*line_h + (len(lines)-1)*8
        if lines and total_h <= box_h: break
        size -= 2
    y = box_y
    for ln in lines:
        d.text((box_x+2, y+2), ln, font=f, fill=(0,0,0,120))
        d.text((box_x, y), ln, font=f, fill=(255,255,255,255))
        y += f.getbbox("Ag")[3] + 8

    # нижний футер
    footer_h = 70
    footer = Image.new("RGBA", (W, footer_h), (0,0,0,84))
    base.alpha_composite(footer, (0, H-footer_h))
    d = ImageDraw.Draw(base)
    d.text((36, H-48), f"source: {source_domain}", font=font_reg, fill=(230,230,230,230))

    bio = io.BytesIO()
    base.convert("RGB").save(bio, format="PNG", optimize=True)
    bio.seek(0)
    return bio

# ========= Парафраз (rule-based, безопасный) =========
# Словарь синонимов: можно расширять.
SYN_MAP = {
    "сообщил": ["заявил", "уточнил", "проинформировал"],
    "сообщила": ["заявила", "уточнила", "проинформировала"],
    "сообщили": ["заявили", "уточнили", "проинформировали"],
    "заявил": ["сообщил", "отметил", "подчеркнул"],
    "заявила": ["сообщила", "отметила", "подчеркнула"],
    "заявили": ["сообщили", "отметили", "подчеркнули"],
    "сегодня": ["сегодня", "в этот день"],
    "вчера": ["накануне"],
    "ранее": ["до этого", "прежде"],
    "также": ["кроме того", "вдобавок"],
    "кроме": ["помимо"],
    "однако": ["при этом", "вместе с тем"],
    "из-за": ["вследствие", "по причине"],
    "в связи с": ["из-за", "по причине"],
    "в результате": ["итогом стало", "в итоге"],
    "в том числе": ["включая"],
    "при этом": ["однако", "вместе с тем"],
    "по данным": ["согласно данным", "как следует из данных"],
    "по словам": ["как заявил", "как отметили"],
    "согласно": ["по", "в соответствии с"],
    "в ходе": ["во время"],
    "например": ["к примеру"],
    "в частности": ["в том числе"],
    "ситуация": ["обстановка", "положение"],
    "меры": ["шаги"],
    "планируется": ["намечается"],
    "ожидается": ["предполагается"],
    "привел": ["вызвал", "стал причиной"],
    "привела": ["вызвала", "стала причиной"],
    "привели": ["вызвали", "стали причиной"],
    "повышение": ["рост"],
    "снижение": ["падение", "сокращение"],
    "рост": ["увеличение"],
    "падение": ["снижение", "просадка"],
    "продолжится": ["сохранится", "может продолжиться"],
    "начался": ["стартовал", "запустился"],
    "началась": ["стартовала", "запустилась"],
    "началось": ["стартовало", "запустилось"],
}

PHRASES = [
    (r"\bв\s+настоящее\s+время\b", "сейчас"),
    (r"\bна\s+данный\s+момент\b", "сейчас"),
    (r"\bв\s+ближайшее\s+время\b", "скоро"),
    (r"\bв\s+том\s+числе\b", "включая"),
    (r"\bпо\s+данным\b", "согласно данным"),
]

PROTECT_RE = re.compile(
    r"(\d[\d\s\.,:%]*|\b[А-ЯЁ][а-яё]+(?:\s[А-ЯЁ][а-яё]+){0,2}\b|https?://\S+|«[^»]+»|\"[^\"]+\")"
)

def _keep_case(src: str, repl: str) -> str:
    if src.isupper():
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl

def paraphrase_ru(text: str, target_ratio: float = 0.5) -> str:
    """
    Осторожный перефраз:
    - не трогаем числа/даты/проценты/валюты/URL/кавычки/имена (PROTECT_RE)
    - заменяем до ~50% доступных слов по словарю
    - чутка шлифуем фразы
    """
    if not text: return text
    original = text

    # фразы-замены (регулярками)
    for pat, repl in PHRASES:
        text = re.sub(pat, repl, text, flags=re.IGNORECASE)

    tokens = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
    protected_spans = []
    for m in PROTECT_RE.finditer(text):
        protected_spans.append((m.start(), m.end()))

    # функция «защищено ли это токеном»
    def is_protected(idx_in_text_start: int, idx_in_text_end: int) -> bool:
        for a,b in protected_spans:
            if idx_in_text_start >= a and idx_in_text_end <= b:
                return True
        return False

    # восстанавливаем позиции токенов в исходной строке
    pos = 0
    pieces = []
    replaced = 0
    candidates = 0
    for tok in tokens:
        start = text.find(tok, pos)
        if start == -1:
            start = pos
        end = start + len(tok)

        if tok.isalpha() and not is_protected(start, end):
            low = tok.lower()
            if low in SYN_MAP:
                candidates += 1
                pieces.append(("word", tok, start, end))
            else:
                pieces.append(("plain", tok, start, end))
        else:
            pieces.append(("plain", tok, start, end))
        pos = end

    max_replace = max(1, int(candidates * target_ratio))
    out = []
    for kind, tok, s, e in pieces:
        if kind == "word":
            low = tok.lower()
            opts = SYN_MAP.get(low, [])
            do = (replaced < max_replace) and (random.random() < 0.8) and opts
            if do:
                repl = random.choice(opts)
                out.append(_keep_case(tok, repl))
                replaced += 1
            else:
                out.append(tok)
        else:
            out.append(tok)

    res = "".join(out)

    # небольшой разброс синтаксиса: делим длинные предложения
    res = re.sub(r",\s+которые", ". Которые", res)
    res = re.sub(r",\s+что", ". Что", res)

    # подчистим пробелы/скобки
    res = re.sub(r"\s+([,.:;!?])", r"\1", res)
    res = re.sub(r"\(\s+", "(", res)
    res = re.sub(r"\s+\)", ")", res)
    res = re.sub(r"\s+", " ", res).strip()

    # не допускаем слишком сильных изменений: если укатали <60% исходной длины — вернём исходник
    if len(res) < len(original)*0.6:
        return original
    return res

# ========= Подпись/HTML =========
def html_escape(s: str) -> str:
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

def smart_join_and_trim(paragraphs, max_len=1024):
    raw = "\n\n".join([p for p in paragraphs if p])
    if len(raw) <= max_len: return raw
    cut = raw[:max_len]
    for sep in [". ", "! ", "? ", "… ", ".\n", "!\n", "?\n", "…\n"]:
        pos = cut.rfind(sep)
        if pos != -1: return cut[:pos+1].rstrip()
    return cut[:-1].rstrip() + "…"

def build_full_caption(title, p1, p2, p3, link, hidden_tags):
    dom = (re.sub(r"^www\.", "", (link or "").split("/")[2]) if link else "источник")
    title_html = f"<b>{html_escape(title)}</b>"
    body_plain = smart_join_and_trim([p1, p2, p3], max_len=1024-220)
    body_html  = html_escape(body_plain)
    footer = [
        f'Источник: <a href="{html_escape(link)}">{html_escape(dom)}</a>',
        f'🪙 <a href="https://t.me/{CHANNEL_ID.lstrip("@")}">USDT=Dollar</a>'
    ]
    caption = f"{title_html}\n\n{body_html}\n\n" + "\n".join(footer)
    if hidden_tags:
        inner = hidden_tags.strip("|")
        spoiler = f'\n\n<span class="tg-spoiler">{html_escape(inner)}</span>'
        if len(caption + spoiler) <= 1024:
            return caption + spoiler
    if len(caption) > 1024:
        main = smart_join_and_trim([body_plain], max_len=1024 - 100 - len("\n".join(footer)))
        caption = f"{title_html}\n\n{html_escape(main)}\n\n" + "\n".join(footer)
    return caption

# ========= Отправка =========
def send_photo_with_caption(photo_bytes: io.BytesIO, caption: str):
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN не задан")
    if not CHANNEL_ID or not CHANNEL_ID.startswith("@"):
        raise RuntimeError("CHANNEL_ID должен быть в формате @имя_канала")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data  = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
    r = requests.post(url, files=files, data=data, timeout=HTTP_TIMEOUT)
    print("Telegram sendPhoto:", r.status_code, r.text[:180])
    r.raise_for_status()

# ========= Формирование 3 абзацев =========
def build_three_paragraphs_scientific(title, summary_text):
    sents = [s.strip() for s in split_sentences(summary_text) if s.strip()]
    uniq = []
    for s in sents:
        if not uniq or s.lower() != uniq[-1].lower():
            uniq.append(s)

    p1 = " ".join(uniq[:2]) if uniq else title
    p2 = " ".join(uniq[2:4]) if len(uniq) > 2 else ""
    p3 = " ".join(uniq[4:6]) if len(uniq) > 4 else ""

    emoji = "📰"
    t = (title + " " + summary_text).lower()
    if any(k in t for k in ["акци","индекс","рынок","бирж","nasdaq","moex","s&p"]): emoji = "📈"
    elif any(k in t for k in ["доллар","рубл","валют","курс","евро","юань"]):      emoji = "💵"
    elif any(k in t for k in ["нефть","газ","opec","брент","энерги","lng"]):       emoji = "🛢️"
    elif any(k in t for k in ["крипт","биткоин","bitcoin","eth","стейбл"]):        emoji = "🪙"
    elif any(k in t for k in ["ставк","цб","фрс","инфляц","cpi","ppi"]):           emoji = "🏦"

    p1 = tidy_paragraph(f"{emoji} {p1}".strip())
    p2 = tidy_paragraph(p2) if p2 else ""
    p3 = tidy_paragraph(p3) if p3 else ""
    return p1, p2, p3

# ========= Обработка ленты =========
def _process_entries(entries, state, posted_uids, batch_seen, now, posted):
    for e in entries:
        if posted[0] >= MAX_POSTS_PER_RUN:
            break

        title   = (getattr(e, "title", "") or "").strip()
        summary = (getattr(e, "summary", getattr(e, "description", "")) or "").strip()
        link    = (getattr(e, "link", "") or "").strip()

        if detect_lang(title + " " + summary) != "ru":
            continue

        uid = _uid_for(link, title)
        if uid in posted_uids or uid in batch_seen:
            continue

        # ЛЁГКИЙ ПАРАФРАЗ заголовка и текста (с факт-стоперами)
        title_ru   = tidy_paragraph(paraphrase_ru(title, target_ratio=0.35))
        summary_ru = paraphrase_ru(summary, target_ratio=0.5)

        p1, p2, p3 = build_three_paragraphs_scientific(title_ru, summary_ru)
        body_len = len((p1 + " " + p2 + " " + p3).strip())
        if body_len < LOW_QUALITY_MIN_LEN:
            print("Skip low-quality item:", title_ru[:90]); continue

        domain = re.sub(r"^www\.", "", link.split("/")[2]) if link else "source"
        themed_hint = (title_ru + " " + summary_ru)
        card   = draw_card(title_ru, domain, now, themed_hint=themed_hint)
        hidden = extract_tags_source(title_ru + " " + summary_ru, 3, 5)
        caption = build_full_caption(title_ru, p1, p2, p3, link, hidden)

        try:
            send_photo_with_caption(card, caption)
            posted[0] += 1
            batch_seen.add(uid)
            posted_uids.add(uid)
            state["posted"] = list(posted_uids)[-5000:]
            _save_state(state)
            time.sleep(1.0)
        except Exception as ex:
            print("Error sending:", ex)

def main():
    state = _load_state()
    posted_uids = set(state.get("posted", []))
    batch_seen  = set()
    posted = [0]
    now = datetime.now().strftime("%d.%m %H:%M")

    # 1) основной проход
    for feed_url in RSS_FEEDS:
        try:
            fp = feedparser.parse(feed_url)
        except Exception as e:
            print("Feed error:", feed_url, e); continue
        _process_entries(fp.entries, state, posted_uids, batch_seen, now, posted)
        if posted[0] >= MAX_POSTS_PER_RUN:
            break

    # 2) бэкап-режим
    if posted[0] == 0 and ALLOW_BACKLOG:
        print("No fresh posts. Trying backlog mode...")
        for feed_url in RSS_FEEDS:
            try:
                fp = feedparser.parse(feed_url)
            except Exception as e:
                print("Feed error:", feed_url, e); continue
            _process_entries(fp.entries, state, posted_uids, batch_seen, now, posted)
            if posted[0] >= MAX_POSTS_PER_RUN:
                break

    if posted[0] == 0:
        print("Nothing to post.")

if __name__ == "__main__":
    main()
