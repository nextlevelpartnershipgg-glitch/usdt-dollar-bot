# bot/poster.py
import os, io, re, random, time
from datetime import datetime, timezone
import requests, feedparser
from PIL import Image, ImageDraw, ImageFont

# =========================
# Конфигурация из переменных среды (Secrets)
# =========================
BOT_TOKEN   = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID  = os.getenv("CHANNEL_ID", "@usdtdollarm").strip()  # ВАЖНО: @имя_канала
TIMEZONE    = os.getenv("TIMEZONE", "Europe/Moscow")

MAX_POSTS_PER_RUN = int(os.getenv("MAX_POSTS_PER_RUN", "1"))   # сколько постим за один запуск
HTTP_TIMEOUT      = 12                                         # сек, таймауты сетевых запросов

# =========================
# Русскоязычные RSS-источники (без РИА)
# =========================
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

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/125 Safari/537.36"}

# =========================
# Утилиты текста
# =========================
def detect_lang(text: str) -> str:
    return "ru" if re.search(r"[А-Яа-яЁё]", text or "") else "non-ru"

def split_sentences(text: str):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
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
    if bal > 0:
        out.append(close_ch * bal)
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

# =========================
# Короткий аналитический вывод
# =========================
def _sentiment_hint(text: str) -> str:
    t = (text or "").lower()
    neg = any(k in t for k in ["паден", "снижен", "сокращ", "штраф", "санкц", "убыт", "дефиц", "отзыв", "кризис"])
    pos = any(k in t for k in ["рост", "увелич", "расшир", "рекорд", "одобрен", "прибыл", "улучшен", "повышен"])
    if pos and not neg: return "нейтрально-позитивная"
    if neg and not pos: return "нейтрально-негативная"
    return "нейтральная"

def generate_brief_analysis(title_ru: str, p1: str, p2: str, p3: str) -> str:
    body = " ".join([p1 or "", p2 or "", p3 or ""])
    mood = _sentiment_hint(body)
    topic = "рынок"
    tl = (title_ru + " " + body).lower()
    if any(w in tl for w in ["ставк", "цб", "фрс", "инфляц"]): topic = "денежно-кредитная политика"
    elif any(w in tl for w in ["нефть", "газ", "opec", "брент", "энерги", "lng"]): topic = "энергетика"
    elif any(w in tl for w in ["акци", "бирж", "индекс", "nasdaq", "moex", "s&p", "облигац"]): topic = "финансовые рынки"
    elif any(w in tl for w in ["крипт", "биткоин", "bitcoin", "eth", "стейбл"]): topic = "крипторынок"
    elif any(w in tl for w in ["бюджет", "налог", "минфин"]): topic = "госфинансы"
    elif any(w in tl for w in ["ввп", "безработ", "производств", "экспорт", "импорт"]): topic = "макроэкономика"
    a1 = f"Итог ({topic}, {mood}): сообщение фиксирует факт и указывает на стадию процесса без смены ключевого тренда."
    a2 = "Дальнейшие выводы зависят от последующих официальных данных и комментариев."
    return a1 + " " + a2

# =========================
# Теги (3–5, существительные/ключевые слова)
# =========================
RU_STOP = set("это этот эта эти такой такой-то как по при про для на из от или либо ещё уже если когда куда где чем что чтобы и в во а но же же-то тот та то те к с о об".split())
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
    while len(tags) < min_tags and "рынки" not in tags:
        tags.append("рынки")
    return "||" + " ".join("#"+t for t in tags[:max_tags]) + "||"

# =========================
# Градиентная карточка с заголовком
# =========================
PALETTES = [((32,44,80),(12,16,28)), ((16,64,88),(8,20,36)), ((82,30,64),(14,12,24)),
            ((20,88,72),(8,24,22)), ((90,60,22),(20,16,12)), ((44,22,90),(16,12,32)),
            ((24,26,32),(12,14,18))]
def _boost(c, k=1.3): return tuple(max(0, min(255, int(v*k))) for v in c)

def generate_gradient(size=(1080, 540)):
    W,H = size
    top, bottom = random.choice(PALETTES)
    top, bottom = _boost(top,1.3), _boost(bottom,1.3)
    img = Image.new("RGB", (W,H))
    dr = ImageDraw.Draw(img)
    for y in range(H):
        t = y/(H-1)
        r = int(top[0]*(1-t) + bottom[0]*t)
        g = int(top[1]*(1-t) + bottom[1]*t)
        b = int(top[2]*(1-t) + bottom[2]*t)
        dr.line([(0,y),(W,y)], fill=(r,g,b))
    return img

def _font(path: str, size: int):
    try:
        return ImageFont.truetype(path, size)
    except Exception:
        # дефаултный шрифт без кириллицы худше, но не упадём
        return ImageFont.load_default()

def draw_card(title: str, source_domain: str, post_stamp: str) -> io.BytesIO:
    W,H = 1080, 540
    img = generate_gradient((W,H)).convert("RGBA")
    overlay = Image.new("RGBA",(W,H),(0,0,0,0))
    ImageDraw.Draw(overlay).rounded_rectangle([40,110,W-40,H-90], radius=28, fill=(0,0,0,118))
    img = Image.alpha_composite(img, overlay).convert("RGB")
    d = ImageDraw.Draw(img)

    font_bold = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 34)
    font_reg  = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)

    # Верх
    d.text((48, 26), "USDT=Dollar", font=font_bold, fill=(255,255,255))
    right = f"пост: {post_stamp}"
    d.text((W-48-d.textlength(right,font=font_reg), 28), right, font=font_reg, fill=(255,255,255))

    # Заголовок
    title = (title or "").strip()
    box_x, box_y = 72, 150
    box_w, box_h = W-2*box_x, H-box_y-120
    # подгон шрифта
    size = 64
    lines = []
    while size >= 28:
        f = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size)
        lines = wrap_by_width(d, title, f, box_w, max_lines=5)
        line_h = f.getbbox("Ag")[3]
        total_h = len(lines)*line_h + (len(lines)-1)*8
        if lines and total_h <= box_h: break
        size -= 2
    y = box_y
    for ln in lines:
        d.text((box_x, y), ln, font=f, fill=(255,255,255))
        y += f.getbbox("Ag")[3] + 8

    # Низ
    small = _font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
    d.text((72, H-64), f"source: {source_domain}", font=small, fill=(230,230,230))

    bio = io.BytesIO()
    img.save(bio, format="PNG", optimize=True)
    bio.seek(0)
    return bio

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

# =========================
# Сборка подписи к фото (HTML)
# =========================
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

    # формируем тело поста
    body_plain = smart_join_and_trim([p1, p2, p3], max_len=1024-350)
    body_html  = html_escape(body_plain)

    # футер (источник и канал)
    footer = [
        f'Источник: <a href="{html_escape(link)}">{html_escape(dom)}</a>',
        f'🪙 <a href="https://t.me/{CHANNEL_ID.lstrip("@")}">USDT=Dollar</a>'
    ]
    caption = f"{title_html}\n\n{body_html}\n\n" + "\n".join(footer)

    # скрытые теги
    if hidden_tags:
        inner = hidden_tags.strip("|")
        spoiler = f'\n\n<span class="tg-spoiler">{html_escape(inner)}</span>'
        if len(caption + spoiler) <= 1024:
            return caption + spoiler

    # если текст длинный — урезаем
    if len(caption) > 1024:
        main = smart_join_and_trim([body_plain], max_len=1024 - 100 - len("\n".join(footer)))
        caption = f"{title_html}\n\n{html_escape(main)}\n\n" + "\n".join(footer)
    return caption

# =========================
# Отправка фото (от имени канала)
# =========================
def send_photo_with_caption(photo_bytes: io.BytesIO, caption: str):
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN не задан")
    if not CHANNEL_ID: raise RuntimeError("CHANNEL_ID не задан")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("cover.png", photo_bytes, "image/png")}
    data  = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}
    r = requests.post(url, files=files, data=data, timeout=HTTP_TIMEOUT)
    print("Telegram sendPhoto:", r.status_code, r.text[:180])
    r.raise_for_status()
    return r.json()

# =========================
# Построение 3 абзацев из summary
# =========================
def build_three_paragraphs_scientific(title, summary_text):
    # summary уже русский, разобьём на предложения
    sents = [s for s in split_sentences(summary_text) if s]
    if not sents:
        sents = [title]  # подстрахуемся

    p1_src = sents[:2] or sents[:1]
    p2_src = sents[2:5] or sents[:1]
    p3_src = sents[5:8] or sents[1:3] or sents[:1]

    p1 = " ".join(p1_src)
    p2 = " ".join(p2_src)
    p3 = " ".join(p3_src)

    # Эмодзи по контексту первого абзаца
    emoji = "📰"
    t = (title + " " + summary_text).lower()
    if any(k in t for k in ["акци", "индекс", "рынок", "бирж", "nasdaq", "moex", "s&p"]): emoji = "📈"
    elif any(k in t for k in ["доллар", "рубл", "валют", "курс", "евро", "юань"]):        emoji = "💵"
    elif any(k in t for k in ["нефть","газ","opec","брент","энерги","lng"]):               emoji = "🛢️"
    elif any(k in t for k in ["крипт","биткоин","bitcoin","eth","стейбл"]):                emoji = "🪙"
    elif any(k in t for k in ["ставк","цб","фрс","инфляц","cpi","ppi"]):                   emoji = "🏦"

    p1 = tidy_paragraph(f"{emoji} {p1}".strip()); p2 = tidy_paragraph(p2); p3 = tidy_paragraph(p3)
    return p1, p2, p3

# =========================
# Основной цикл
# =========================
def main():
    posted = 0
    now = datetime.now().strftime("%d.%m %H:%M")

    for feed_url in RSS_FEEDS:
        try:
            fp = feedparser.parse(feed_url)
        except Exception as e:
            print("Feed error:", feed_url, e); continue

        for e in fp.entries:
            if posted >= MAX_POSTS_PER_RUN: break

            title  = (getattr(e, "title", "") or "").strip()
            summary = (getattr(e, "summary", getattr(e, "description", "")) or "").strip()
            link   = (getattr(e, "link", "") or "").strip()

            # только русские элементы
            if detect_lang(title + " " + summary) != "ru":
                continue

            # заголовок и текст
            title_ru = tidy_paragraph(title)
            p1, p2, p3 = build_three_paragraphs_scientific(title_ru, summary)

            # отфильтруем «пустые»
            body_len = len((p1 + " " + p2 + " " + p3).strip())
            if body_len < 250:
                print("Skip low-quality item:", title_ru[:90])
                continue

            # карточка
            domain = (re.sub(r"^www\.", "", link.split("/")[2]) if link else "source")
            card = draw_card(title_ru, domain, now)

            # скрытые теги
            hidden = extract_tags_source(title_ru + " " + summary, 3, 5)

            # подпись
            caption = build_full_caption(title_ru, p1, p2, p3, link, hidden)

            try:
                send_photo_with_caption(card, caption)
                posted += 1
                time.sleep(1.0)
            except Exception as ex:
                print("Error sending:", ex)

        if posted >= MAX_POSTS_PER_RUN:
            break

    if posted == 0:
        print("Nothing to post.")

if __name__ == "__main__":
    main()
