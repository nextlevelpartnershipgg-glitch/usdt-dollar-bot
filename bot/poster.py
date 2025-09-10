import os
import io
import random
from datetime import datetime
from dateutil import tz

import requests
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# =========================
# Константы оформления
# =========================
W, H = 1280, 640                  # размер обложки
SAFE = 32                         # общий внутренний отступ
BRAND = os.getenv("BRAND", "USDT=Dollar")
CATEGORY = os.getenv("CATEGORY", "Экономика")  # можно менять через env

# Telegram
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # пример: @usdtdollarm


# =========================
# Служебные утилиты
# =========================
def load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Надежно находим системный шрифт. Без внешних файлов."""
    candidates = []
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    for p in candidates:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont):
    """Точное измерение текста для Pillow 10+."""
    x0, y0, x1, y1 = draw.textbbox((0, 0), text, font=font)
    return (x1 - x0, y1 - y0)


def normalize_xy(xy):
    x0, y0, x1, y1 = xy
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def rr(draw: ImageDraw.ImageDraw, xy, radius, fill=None, outline=None, width=1):
    """Безопасный rounded_rectangle."""
    draw.rounded_rectangle(normalize_xy(xy), radius=radius, fill=fill, outline=outline, width=width)


def clamp(v, a, b):
    return max(a, min(b, v))


def wrap_lines(draw, text, font, max_w, max_lines):
    """Перенос строк по ширине с ограничением по числу строк."""
    words = text.split()
    lines = []
    cur = []
    for w in words:
        probe = " ".join(cur + [w])
        wpx, _ = measure(draw, probe, font)
        if wpx <= max_w:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
            if len(lines) == max_lines:
                break
    if cur and len(lines) < max_lines:
        lines.append(" ".join(cur))
    if len(lines) > max_lines:
        lines = lines[:max_lines]
    # если последняя строка слишком длинная — подрежем и добавим «…»
    if lines:
        last = lines[-1]
        while True:
            wpx, _ = measure(draw, last + "…", font)
            if wpx <= max_w or len(last) <= 1:
                lines[-1] = last + "…"
                break
            last = last[:-1]
    return lines


# =========================
# Фон: градиент + спотлайты
# =========================
def gradient_bg(w, h):
    """Спокойный брендовый градиент (вертикальный)."""
    palettes = [
        ((18, 24, 32), (60, 75, 95)),   # холодный сине-графит
        ((23, 25, 31), (70, 58, 79)),   # фиолетово-графит
        ((18, 22, 24), (52, 68, 64)),   # глубокий зелёный
        ((22, 22, 26), (62, 62, 72)),   # нейтрально-серый
    ]
    c0, c1 = random.choice(palettes)
    base = Image.new("RGB", (w, h), c0)
    top = Image.new("RGB", (w, h), c1)
    mask = Image.linear_gradient("L").resize((w, h))
    grad = Image.composite(top, base, mask)
    return grad


def add_spotlights(canvas: Image.Image, spots=3):
    """Мягкие световые пятна (добавляют глубину)."""
    ov = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    for _ in range(spots):
        r = random.randint(140, 280)
        cx = random.randint(-r // 2, canvas.size[0] + r // 2)
        cy = random.randint(-r // 2, canvas.size[1] + r // 2)
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, random.randint(36, 64)))
    ov = ov.filter(ImageFilter.GaussianBlur(radius=36))
    canvas.alpha_composite(ov)


# =========================
# Рендер обложки
# =========================
def draw_header_image(title: str, source_domain: str, event_dt: datetime, category: str = CATEGORY) -> Image.Image:
    bg = gradient_bg(W, H).convert("RGBA")
    add_spotlights(bg, spots=3)
    draw = ImageDraw.Draw(bg)

    # Шрифты
    f_brand = load_font(44, bold=True)
    f_small = load_font(28, bold=False)
    f_small_b = load_font(28, bold=True)

    # -------- Верхняя строка (логотип + бренд) --------
    left = SAFE
    top = SAFE

    # кружок
    icon_r = 28
    cx = left + icon_r
    cy = top + icon_r
    rr(draw, (cx - icon_r, cy - icon_r, cx + icon_r, cy + icon_r), radius=icon_r, fill=(255, 255, 255, 36))

    # знак $
    dollar = "$"
    dw, dh = measure(draw, dollar, f_small_b)
    draw.text((cx - dw / 2, cy - dh / 2), dollar, font=f_small_b, fill=(255, 255, 255, 220))

    # бренд
    bw, bh = measure(draw, BRAND, f_brand)
    draw.text((cx + icon_r + 12, cy - bh / 2), BRAND, font=f_brand, fill=(255, 255, 255, 230))

    # -------- Пилюли справа (категория, время) --------
    pill_pad_x, pill_pad_y = 16, 8
    right = W - SAFE
    px = right
    py = SAFE

    def pill(text, top_y, bg_col=(255, 255, 255, 40), fg=(255, 255, 255, 230)):
        tw, th = measure(draw, text, f_small_b)
        x1 = px
        y1 = top_y
        x0 = x1 - tw - pill_pad_x * 2
        y0 = y1
        rr(draw, (x0, y0, x1, y1 + th + pill_pad_y * 2), radius=18, fill=bg_col)
        draw.text((x0 + pill_pad_x, y0 + pill_pad_y), text, font=f_small_b, fill=fg)
        return y0 + th + pill_pad_y * 2

    py = pill(category, py, bg_col=(74, 171, 255, 110))
    dt_str = event_dt.strftime("%d.%m %H:%M")
    py = pill(f"пост: {dt_str}", py + 8)

    # -------- Контейнер заголовка --------
    # высота под заголовок (адаптивная, но не меньше 240)
    block_top = int(max(cy + icon_r + 18, py + 18))
    block_bottom = H - SAFE * 2  # оставляем место для подписи «source:»
    block_bottom = clamp(block_bottom, block_top + 240, H - SAFE)

    rr(draw, (SAFE, block_top, W - SAFE, block_bottom), radius=24, fill=(0, 0, 0, 180))

    inner = 28
    text_max_w = (W - SAFE * 2) - inner * 2

    # подгон размера заголовка
    title = " ".join(title.split())
    for size in (64, 58, 52, 48, 44):
        f_title = load_font(size, bold=True)
        lines = wrap_lines(draw, title, f_title, text_max_w, max_lines=4)
        lh = measure(draw, "Ag", f_title)[1]
        need_h = inner * 2 + lh * len(lines) + (len(lines) - 1) * 6
        if need_h <= (block_bottom - block_top):
            break
    # рисуем строки
    y = block_top + inner
    for ln in lines:
        draw.text((SAFE + inner, y), ln, font=f_title, fill=(255, 255, 255, 240))
        y += measure(draw, ln, f_title)[1] + 6

    # нижняя подпись
    src = f"source: {source_domain}"
    sw, sh = measure(draw, src, f_small)
    draw.text((SAFE, H - SAFE - sh), src, font=f_small, fill=(255, 255, 255, 170))

    return bg


# =========================
# Телега
# =========================
def send_photo_with_caption(img: Image.Image, caption_html: str):
    if not BOT_TOKEN or not CHANNEL_ID:
        print("WARN: BOT_TOKEN/CHANNEL_ID не заданы — пропуск отправки.")
        return
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    data = {
        "chat_id": CHANNEL_ID,
        "caption": caption_html,
        "parse_mode": "HTML",
        "disable_notification": True,
    }
    files = {"photo": ("cover.jpg", buf, "image/jpeg")}
    r = requests.post(url, data=data, files=files, timeout=25)
    if r.status_code != 200 or not r.json().get("ok", False):
        print("Telegram sendPhoto error:", r.text)


def build_caption(title: str, body: str, link: str, brand_link: str):
    """Подпись: заголовок, текст, источник, ссылка на канал. Без повторов и портянок."""
    title = " ".join(title.split())
    body = " ".join(body.split())
    if len(body) > 1200:
        body = body[:1200].rsplit(" ", 1)[0] + "…"

    source_html = f'<a href="{link}">{link.split("/")[2]}</a>'
    brand_html = f'<a href="{brand_link}">{BRAND}</a>'

    return f"<b>{title}</b>\n\n{body}\n\nИсточник: {source_html}\n{brand_html}"


def now_msk():
    return datetime.now(tz=tz.gettz("Europe/Moscow"))


# =========================
# Точка входа
# =========================
def main():
    title = os.getenv("NEWS_TITLE", "").strip()
    body = os.getenv("NEWS_BODY", "").strip()
    link = os.getenv("NEWS_LINK", "").strip()

    if not title or not body or not link:
        print("Пропуск: нет NEWS_TITLE/NEWS_BODY/NEWS_LINK.")
        return

    if len(body) < 400:
        print(f"Пропуск: текст короткий ({len(body)} символов < 400).")
        return

    source_domain = link.split("/")[2] if "://" in link else "source"
    cover = draw_header_image(title, source_domain, now_msk(), category=CATEGORY)

    brand_link = f"https://t.me/{CHANNEL_ID.lstrip('@')}"
    caption = build_caption(title, body, link, brand_link)

    send_photo_with_caption(cover, caption)


if __name__ == "__main__":
    main()
