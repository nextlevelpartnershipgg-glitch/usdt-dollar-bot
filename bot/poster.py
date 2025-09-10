# bot/poster.py
import os
import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import textwrap
from datetime import datetime

# ------- секреты из окружения (НЕ хардкодим) -------
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()  # пример: @usdtdollarm
if not BOT_TOKEN or not CHANNEL_ID:
    raise SystemExit("BOT_TOKEN / CHANNEL_ID не заданы в Secrets/ENV")

# ------- универсальная функция измерения текста -------
def measure_text(draw, text, font):
    try:
        # новый способ (Pillow 10+)
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    except Exception:
        # fallback для старых версий
        return font.getsize(text)

# ------- надёжная загрузка шрифта -------
def load_font(size=32, bold=False):
    candidates = []

    # 1) файлы в репозитории (рекомендуется положить их в data/)
    if bold:
        candidates.append(os.path.join("data", "DejaVuSans-Bold.ttf"))
    else:
        candidates.append(os.path.join("data", "DejaVuSans.ttf"))

    # 2) стандартные пути Linux (GH Actions)
    if bold:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        ]
    else:
        candidates += [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]

    # 3) macOS
    candidates += [
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]

    # 4) Windows (если вдруг локальный запуск)
    candidates += [
        "C:\\Windows\\Fonts\\arial.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
    ]

    for path in candidates:
        try:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        except Exception:
            continue

    # самый последний шанс
    return ImageFont.load_default()

# ------- рисуем шапку с градиентом -------
def draw_header_image(title: str, source: str, event_time: str):
    width, height = 800, 400
    img = Image.new("RGB", (width, height), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    # градиент (ручной, совместимый со старыми версиями Pillow)
    for i in range(height):
        r = 40 + int(i * 100 / height)
        g = 30 + int(i * 60 / height)
        b = 90 + int(i * 40 / height)
        draw.line([(0, i), (width, i)], fill=(r, g, b))

    font_title = load_font(40, bold=True)
    font_small = load_font(22, bold=False)

    # заголовок
    margin, offset = 40, 120
    for line in textwrap.wrap(title, width=30):
        draw.text((margin, offset), line, font=font_title, fill="white")
        _, th = measure_text(draw, line, font_title)
        offset += th + 10

    # нижний футер
    footer_text = f"source: {source}   •   событие: {event_time}"
    tw, th = measure_text(draw, footer_text, font_small)
    draw.text((width - tw - 20, height - th - 20), footer_text, font=font_small, fill="white")

    # простая «плашка-лого»
    logo_size = 60
    logo = Image.new("RGB", (logo_size, logo_size), (200, 200, 200))
    logo_draw = ImageDraw.Draw(logo)
    logo_draw.ellipse((0, 0, logo_size, logo_size), fill=(120, 120, 120))
    img.paste(logo, (20, 20))

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

# ------- подпись к посту -------
def build_full_caption(title, lead, details, link, hidden_tags):
    def esc(s: str) -> str:
        return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    caption = f"<b>{esc(title)}</b>\n\n"
    if lead:
        caption += f"📰 {esc(lead)}\n\n"
    if details:
        caption += f"<b>Подробности:</b>\n{esc(details)}\n\n"
    if link:
        caption += f"<b>Источник:</b> <a href='{esc(link)}'>{esc(link)}</a>\n\n"
    if hidden_tags:
        caption += "".join([f"#{esc(tag)} " for tag in hidden_tags])
    # лимит подписи Telegram = 1024 символа
    return caption[:1024]

# ------- отправка в Telegram -------
def send_post(title, lead, details, source, link, hidden_tags):
    event_time = datetime.now().strftime("%d.%m %H:%M")
    header_img = draw_header_image(title, source, event_time)
    caption = build_full_caption(title, lead, details, link, hidden_tags)

    files = {"photo": ("header.png", header_img.getvalue(), "image/png")}
    data = {"chat_id": CHANNEL_ID, "caption": caption, "parse_mode": "HTML"}

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    r = requests.post(url, data=data, files=files, timeout=30)
    if r.status_code != 200:
        print("Ошибка публикации:", r.status_code, r.text)
        r.raise_for_status()
    else:
        print("Успешно опубликовано:", title)

# ======= пример запуска (для проверки) =======
if __name__ == "__main__":
    # Этот блок можно удалить — он просто проверяет, что всё рисуется и отправляется.
    send_post(
        title="Тестовый заголовок новости",
        lead="Краткое описание для проверки работы фикса шрифтов.",
        details="Расширенные подробности новости. Здесь может быть длинный текст на несколько абзацев — подпись всё равно обрежется по лимиту Telegram.",
        source="rbc.ru",
        link="https://rbc.ru/test",
        hidden_tags=["новости", "проверка"]
    )
