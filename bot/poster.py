# -*- coding: utf-8 -*-
import os, io, json, random, time, re, math
from html import unescape
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
import feedparser
from PIL import Image, ImageDraw, ImageFont

# ================== НАСТРОЙКИ ==================

# Русскоязычные источники
RSS_FEEDS = [
    "https://rssexport.rbc.ru/rbcnews/news/20/full.rss",
    "https://rssexport.rbc.ru/rbcnews/economics/20/full.rss",
    "https://lenta.ru/rss/news",
    "https://www.kommersant.ru/RSS/news.xml",
    "https://tass.ru/rss/v2.xml",
    "https://www.gazeta.ru/export/rss/lenta.xml",
    "https://www.vedomosti.ru/rss/news",
    "https://www.interfax.ru/rss.asp",
    "https://1prime.ru/export/rss2/index.xml",
]

# Сколько новостей публиковать за один прогон
MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "1"))

# «Свежесть» новости
FRESH_WINDOW_MIN = int(os.getenv("FRESH_WINDOW_MIN", "25"))   # берём только свежие
FALLBACK_ON_NO_FRESH = int(os.getenv("FALLBACK_ON_NO_FRESH", "1"))
FALLBACK_WINDOW_MIN = int(os.getenv("FALLBACK_WINDOW_MIN", "360"))

# Хранилище состояния (анти-дубликаты)
STATE_DIR = "data"
STATE_PATH = os.path.join(STATE_DIR, "state.json")

# Таймзона
TIMEZONE = os.getenv("TIMEZONE", "Europe/Moscow")
TZ = ZoneInfo(TIMEZONE)

# Секреты (НЕ хардкодить токен в код)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()

# Временные «фолбэки» (можешь руками подставить для локального теста,
# но в проде держи в Secrets):
if not CHANNEL_ID:
    CHANNEL_ID = "@usdtdollarm"   # ок публично
# if not BOT_TOKEN: BOT_TOKEN = "ВСТАВЬ_СЮДА_ТОКЕН_Только_для_локального_теста"

# Шрифты
FONT_BOLD = os.path.join("data", "DejaVuSans-Bold.ttf")
FONT_REG  = os.path.join("data", "DejaVuSans.ttf")

# ================== УТИЛИТЫ ТЕКСТА ==================

def now_local():
    return datetime.now(TZ)

def ensure_state():
    os.makedirs(STATE_DIR, exist_ok=True)
    if not os.path.exists(STATE_PATH):
        with open(STATE_PATH, "w", encoding="utf-8") as f:
            json.dump({"posted": []}, f, ensure_ascii=False)

def load_state():
    ensure_state()
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def save_state(st):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)

def html_escape(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def sanitize_source_text(x: str) -> str:
    """Снять HTML-сущности, убрать теги, почистить пробелы и «кривые» кавычки/тире."""
    x = unescape(x or "")
    x = re.sub(r"<[^>]+>", " ", x)
    x = x.replace("\xa0", " ").replace("&nbsp;", " ")
    x = (x.replace("&laquo;", "«").replace("&raquo;", "»")
           .replace("&mdash;", "—").replace("&ndash;", "–")
           .replace("&quot;", "«").replace("&apos;", "’")
           .replace("&amp;", "&"))
    x = re.sub(r"\s+([,.;:!?])", r"\1", x)
    x = re.sub(r"\s{2,}", " ", x).strip()
    return x

def split_sentences(text: str):
    parts = re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z0-9])", text or "")
    return [p.strip() for p in parts if p.strip()]

def _normalize_for_cmp(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()

def dedupe_sentences(sents, title):
    seen = set([_normalize_for_cmp(title)])
    out = []
    for s in sents:
        k = _normalize_for_cmp(s)
        if k and k not in seen:
            seen.add(k)
            out.append(s)
    return out

def smart_join_and_trim(paragraphs, max_len=1000):
    txt = "\n\n".join([p for p in paragraphs if p])
    if len(txt) <= max_len:
        return txt
    res, total = [], 0
    for p in paragraphs:
        if not p: 
            continue
        if total + len(p) + 2 <= max_len:
            res.append(p); total += len(p) + 2
        else:
            tail = p[: max(0, max_len - total - 1)].rstrip()
            if tail: res.append(tail + "…")
            break
    return "\n\n".join(res).strip()

# ================== БЕЗОПАСНЫЙ ПЕРЕФРАЗ ==================

PHRASES = [
    (r"\bсообщается\b", "уточнили"),
    (r"\bпо его словам\b", "как подчеркнули"),
    (r"\bпояснил\(а\)?\b", "отметил"),
]

SYN_MAP = {
    "сказал": ["заявил", "отметил", "подчеркнул"],
    "сообщил": ["указал", "сообщили", "заявил"],
    "заявил": ["сообщил", "подчеркнул", "уточнил"],
    "отметил": ["подчеркнул", "уточнил"],
    "в связи": ["из-за", "на фоне"],
    "рассказал": ["сообщил", "пояснил"],
    "сообщило": ["заявили", "подтвердили"],
}

SENSITIVE_TERMS = [
    "приговор", "приговорили", "осудили", "суд", "колони", "арест",
    "задержан", "следствие", "уголовн", "штраф", "исков", "прокуратур",
]
DONT_INVENT_TERMS = ["колони", "лет", "месяц", "суток", "штраф", "миллион", "тыс", "приговор"]

_NUM_RE = re.compile(r"\b\d+[ \u00A0]?(?:[.,]\d+)?\b")
_DATE_RE = re.compile(r"\b(?:\d{1,2}\.\d{1,2}\.\d{2,4}|\d{1,2}\s*[а-я]+\s*\d{4}|\d{4})\b", re.IGNORECASE)
_CURRENCY_RE = re.compile(r"\b(?:₽|руб(?:\.|лей)?|USD|доллар(?:ов)?|EUR|евро)\b", re.IGNORECASE)
_QUOTED_RE = re.compile(r"[\"«][^\"»]{2,}[\"»]")

def _tokens_guard(text: str):
    nums = set(_NUM_RE.findall(text))
    dates = set(_DATE_RE.findall(text))
    curs = set(m.group(0) for m in _CURRENCY_RE.finditer(text))
    quotes = set(m.group(0) for m in _QUOTED_RE.finditer(text))
    names = set(re.findall(r"\b[А-ЯЁ][а-яё]+(?:ов|ев|ин|кин|ская|ский|ян|дзе|швили|енко)\b", text))
    return {"nums":nums, "dates":dates, "curs":curs, "quotes":quotes, "names":names}

def _violates_guard(before: str, after: str) -> bool:
    b, a = _tokens_guard(before), _tokens_guard(after)
    if not b["nums"].issubset(a["nums"]): return True
    if not b["curs"].issubset(a["curs"]): return True
    if not b["dates"].issubset(a["dates"]): return True
    if not b["quotes"].issubset(a["quotes"]): return True
    if not b["names"].issubset(a["names"]): return True
    low_b, low_a = before.lower(), after.lower()
    for t in DONT_INVENT_TERMS:
        if t not in low_b and t in low_a:
            return True
    return False

def _keep_case(src: str, repl: str) -> str:
    return repl.capitalize() if src[:1].isupper() else repl

def _swap_clauses(s: str) -> str:
    parts = re.split(r"(,|\—|-)", s, 1)
    if len(parts) == 3 and len(parts[0]) > 12 and len(parts[2]) > 12:
        return (parts[2].strip() + " — " + parts[0].strip()).strip()
    return s

def _is_sensitive_topic(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in SENSITIVE_TERMS)

def paraphrase_ru_safe(text: str, target_ratio: float, conservative: bool) -> str:
    if not text: return text
    original = text

    local = original
    for pat, repl in PHRASES:
        local = re.sub(pat, repl, local, flags=re.IGNORECASE)

    words = local.split()
    idxs = [i for i,w in enumerate(words) if w.lower().strip(".,!?;:") in SYN_MAP]
    random.shuffle(idxs)
    limit = max(1, int(len(idxs) * (0.35 if conservative else target_ratio)))

    done = 0
    puncts = ".,!?;:"
    for i in idxs:
        if done >= limit: break
        raw = words[i]
        core = raw.rstrip(puncts); tail = raw[len(core):]
        key = core.lower()

        if _NUM_RE.search(core) or _CURRENCY_RE.search(core) or _QUOTED_RE.search(core):
            continue
        if re.match(r"^[А-ЯЁ][а-яё-]{2,}$", core):
            continue

        cand = SYN_MAP.get(key)
        if not cand: continue
        repl = _keep_case(core, random.choice(cand))
        words[i] = repl + tail
        done += 1

    res = " ".join(words)

    if not conservative:
        sents = split_sentences(res)
        out = []
        for s in sents:
            s = s.strip()
            if not s: continue
            if random.random() < 0.35:
                s = _swap_clauses(s)
            out.append(s)
        res = " ".join(out)

    res = re.sub(r"\s+([,.:;!?])", r"\1", res)
    res = re.sub(r"\s+", " ", res).strip()

    if _violates_guard(original, res): return original
    if len(res) < len(original) * 0.6: return original
    return res

def tidy_paragraph(p: str) -> str:
    if not p: return p
    p = p.strip()
    if p and p[0].islower():
        p = p[0].upper() + p[1:]
    p = re.sub(r"\s+([,.:;!?])", r"\1", p)
    p = re.sub(r"\s{2,}", " ", p)
    return p

def pick_emoji(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["рынок", "акци", "бирж", "эконом", "инвест"]): return "📊"
    if any(k in t for k in ["правитель", "закон", "госдума", "кабинет"]): return "🏛️"
    if any(k in t for k in ["технолог", "платформ", "ит", "сервис"]): return "💻"
    if any(k in t for k in ["спорт", "матч", "игра"]): return "🏟️"
    if any(k in t for k in ["погода", "шторм", "природ"]): return "🌧️"
    return "📰"

def build_sections_marketing(title: str, summary_text: str):
    sents_raw = split_sentences(summary_text)
    sents = dedupe_sentences(sents_raw, title)
    if not sents:
        sents = split_sentences(summary_text)

    lead_sents = sents[:2] if sents else [title]
    details_sents = sents[2:10] if len(sents) > 2 else []

    lead_norm = {_normalize_for_cmp(x) for x in lead_sents}
    details_sents = [s for s in details_sents if _normalize_for_cmp(s) not in lead_norm]

    lead_src    = " ".join(lead_sents) or title
    details_src = (" ".join(details_sents)).strip()

    sensitive = _is_sensitive_topic(title + " " + summary_text)

    title_p = tidy_paragraph(paraphrase_ru_safe(title, target_ratio=0.55, conservative=sensitive))
    emoji   = pick_emoji(title + " " + summary_text)

    lead    = tidy_paragraph(f"{emoji} {paraphrase_ru_safe(lead_src, target_ratio=0.4 if sensitive else 0.92, conservative=sensitive)}")
    details = tidy_paragraph(paraphrase_ru_safe(details_src, target_ratio=0.5 if sensitive else 0.95, conservative=sensitive)) if details_src else ""

    if _normalize_for_cmp(details) == _normalize_for_cmp(lead):
        details = tidy_paragraph(paraphrase_ru_safe(details_src, target_ratio=0.6 if sensitive else 0.97, conservative=sensitive))

    conclusion = ""  # блок убран
    return title_p, lead, details, conclusion

# ================== КАРТИНКА (градиент + брендинг) ==================

def _rand_grad_colors():
    palettes = [
        [(32, 40, 99), (10, 140, 200)],
        [(29, 12, 40), (160, 40, 150)],
        [(10, 50, 60), (10, 160, 120)],
        [(40, 10, 10), (220, 140, 20)],
        [(28, 30, 34), (75, 85, 100)],
    ]
    return random.choice(palettes)

def _draw_linear_gradient(img, c1, c2):
    w, h = img.size
    base = Image.new("RGB", (w, h), c1)
    top = Image.new("RGB", (w, h), c2)
    # создаём простую линейную маску сами (совместимо со старым Pillow)
    mask = Image.new("L", (w, h))
    md = ImageDraw.Draw(mask)
    for y in range(h):
        md.line([(0, y), (w, y)], fill=int(255 * y / (h - 1)))
    return Image.composite(top, base, mask)

def draw_header_image(title: str, source_host: str, post_dt: datetime):
    W, H = 1280, 720
    im = Image.new("RGB", (W, H), (18, 23, 28))
    c1, c2 = _rand_grad_colors()
    im = _draw_linear_gradient(im, c1, c2)
    draw = ImageDraw.Draw(im)

    # верхняя плашка
    header_h = 86
    draw.rectangle([0, 0, W, header_h], fill=(20, 35, 55))

    # логотип
    font_logo = ImageFont.truetype(FONT_BOLD, 38)
    draw.text((20, 22), "USDT=Dollar", font=font_logo, fill=(235, 242, 255))

    # время поста
    font_small = ImageFont.truetype(FONT_REG, 28)
    ts = post_dt.strftime("пост: %d.%m %H:%M")
    tw, _ = draw.textsize(ts, font=font_small)
    draw.text((W - tw - 24, 24), ts, font=font_small, fill=(220, 225, 235))

    # тёмная панель для заголовка
    panel = Image.new("RGBA", (W - 120, H - 240), (0, 0, 0, 90))
    im.paste(panel, (60, 140), panel)

    # заголовок
    font_title = ImageFont.truetype(FONT_BOLD, 66)
    x, y = 90, 170
    max_w = W - 180
    words = title.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        ww, _ = draw.textsize(test, font=font_title)
        if ww <= max_w:
            cur = test
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    for line in lines[:5]:
        draw.text((x, y), line, font=font_title, fill=(250, 250, 255))
        y += 80

    # источник
    draw.text((60, H - 60), f"source: {source_host}", font=font_small, fill=(220, 220, 230))

    out = io.BytesIO()
    im.save(out, format="JPEG", quality=92)
    out.seek(0)
    return out

# ================== ПОДПИСЬ (без «что это значит») ==================

def build_full_caption(title, lead, details, conclusion, link, hidden_tags):
    dom = (re.sub(r"^www\.", "", (link or "").split("/")[2]) if link else "источник")
    title_html = f"<b>{html_escape(title)}</b>"

    body = [html_escape(lead)]
    if details:
        body.append(f"<b>Подробности:</b>\n{html_escape(details)}")

    body_text = smart_join_and_trim(body, max_len=1024 - 220)

    footer = [
        f'Источник: <a href="{html_escape(link)}">{html_escape(dom)}</a>',
        f'🪙 <a href="https://t.me/{CHANNEL_ID.lstrip("@")}">USDT=Dollar</a>'
    ]
    caption = f"{title_html}\n\n{body_text}\n\n" + "\n".join(footer)

    if hidden_tags:
        inner = hidden_tags.strip("|")
        spoiler = f'\n\n<span class="tg-spoiler">{html_escape(inner)}</span>'
        if len(caption + spoiler) <= 1024:
            return caption + spoiler
    return caption[:1024]

# ================== РАЗБОР ЛЕНТЫ ==================

def fetch_entries():
    items = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:30]:
                title   = sanitize_source_text((getattr(e, "title", "") or "").strip())
                summary = sanitize_source_text((getattr(e, "summary", getattr(e, "description", "")) or "").strip())
                link    = (getattr(e, "link", "") or "").strip()

                # дата
                if getattr(e, "published_parsed", None):
                    dt = datetime(*e.published_parsed[:6], tzinfo=timezone.utc).astimezone(TZ)
                elif getattr(e, "updated_parsed", None):
                    dt = datetime(*e.updated_parsed[:6], tzinfo=timezone.utc).astimezone(TZ)
                else:
                    dt = now_local()

                items.append({
                    "id": (getattr(e, "id", link) or link),
                    "title": title,
                    "summary": summary,
                    "link": link,
                    "published": dt,
                    "host": (link.split("/")[2] if ("//" in link) else "news")
                })
        except Exception as ex:
            print("feed error:", url, ex)
            continue
    items.sort(key=lambda x: x["published"], reverse=True)
    return items

def filter_fresh(items):
    now = now_local()
    fresh = [it for it in items if (now - it["published"]) <= timedelta(minutes=FRESH_WINDOW_MIN)]
    if fresh: return fresh
    if FALLBACK_ON_NO_FRESH:
        return [it for it in items if (now - it["published"]) <= timedelta(minutes=FALLBACK_WINDOW_MIN)]
    return []

def make_hidden_tags(item):
    words = re.findall(r"\b[А-ЯЁа-яё]{4,}\b", item["title"] + " " + item["summary"])
    uniq = []
    for w in words:
        lw = w.lower()
        if lw.endswith(("ами","ями","ах","ях","ой","ей","ом","ем","ою","ею","у","ю","а","я","ы","и")):
            lw = re.sub(r"(ами|ями|ах|ях|ой|ей|ом|ем|ою|ею|у|ю|а|я|ы|и)$", "", lw)
        if lw and lw not in uniq:
            uniq.append(lw)
        if len(uniq) >= 5: break
    if not uniq: return ""
    return "||#" + " #".join(uniq[:5]) + "||"

def build_post_payload(item):
    t, l, d, c = build_sections_marketing(item["title"], item["summary"])
    caption = build_full_caption(t, l, d, c, item["link"], hidden_tags=make_hidden_tags(item))
    img = draw_header_image(t, item["host"], now_local())
    return img, caption

# ================== ОТПРАВКА В ТГ ==================

def tg_send_photo(img_bytes: io.BytesIO, caption: str):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("post.jpg", img_bytes.getvalue(), "image/jpeg")}
    data = {
        "chat_id": CHANNEL_ID,
        "caption": caption,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = requests.post(url, data=data, files=files, timeout=30)
    if r.status_code != 200:
        print("Telegram sendPhoto:", r.status_code, r.text)
        r.raise_for_status()
    else:
        print("Telegram sendPhoto OK")
    return r.json()

# ================== MAIN ==================

def main():
    assert BOT_TOKEN and CHANNEL_ID, "BOT_TOKEN / CHANNEL_ID не заданы (положи в Secrets)."

    st = load_state()
    posted = set(st.get("posted", []))

    items = fetch_entries()
    items = [it for it in items if it["id"] not in posted]
    items = filter_fresh(items)
    if not items:
        print("Нет подходящих новостей (старые или уже опубликованы).")
        return

    published_count = 0
    for it in items:
        if published_count >= MAX_POSTS_PER_RUN:
            break
        try:
            img, caption = build_post_payload(it)
            tg_send_photo(img, caption)
            posted.add(it["id"])
            published_count += 1
            print("Posted:", it["title"])
            time.sleep(1.0)
        except Exception as ex:
            print("Ошибка публикации:", ex)
            continue

    st["posted"] = list(posted)[-5000:]
    save_state(st)

if __name__ == "__main__":
    main()
