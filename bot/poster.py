import os
import requests
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import textwrap
from datetime import datetime

BOT_TOKEN = "8304198834:AAFmxWDHpFMQebf_Ns0TQi3B8nRldqgbxJg"
CHANNEL_ID = "@usdtdollarm"

# ===== Универсальная функция для измерения текста =====
def measure_text(draw, text, font):
    try:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]
    except Exception:
        return font.getsize(text)

# ===== Рисуем красивую картинку для заголовка =====
def draw_header_image(title, source, event_time):
    width, height = 800, 400
    img = Image.new("RGB", (width, height), (30, 30, 30))
    draw = ImageDraw.Draw(img)

    # Градиент
    for i in range(height):
        r = 40 + int(i * 100 / height)
        g = 30 + int(i * 60 / height)
        b = 90 + int(i * 40 / height)
        draw.line([(0, i), (width, i)], fill=(r, g, b))

    # Шрифты
    font_title = ImageFont.truetype("arial.ttf", 40)
    font_small = ImageFont.truetype("arial.ttf", 24)

    # Заголовок
    margin, offset = 40, 120
    for line in textwrap.wrap(title, width=30):
        draw.text((margin, offset), line, font=font_title, fill="white")
        _, th = measure_text(draw, line, font_title)
        offset += th + 10

    # Нижняя строка
    footer_text = f"source: {source}   •   событие: {event_time}"
    tw, th = measure_text(draw, footer_text, font_small)
    draw.text((width - tw - 20, height - th - 20), footer_text, font=font_small, fill="white")

    # Логотип
    logo_size = 60
    logo = Image.new("RGB", (logo_size, logo_size), (200, 200, 200))
    logo_draw = ImageDraw.Draw(logo)
    logo_draw.ellipse((0, 0, logo_size, logo_size), fill=(120, 120, 120))
    img.paste(logo, (20, 20))

    output = BytesIO()
    img.save(output, format="PNG")
    output.seek(0)
    return output

# ===== Формируем подпись =====
def build_full_caption(title, lead, details, link, hidden_tags):
    caption = f"<b>{title}</b>\n\n"
    caption += f"📰 {lead}\n\n"
    if details:
        caption += f"<b>Подробности:</b>\n{details}\n\n"
    caption += f"<b>Источник:</b> <a href='{link}'>{link}</a>\n\n"
    if hidden_tags:
        caption += "".join([f"#{tag} " for tag in hidden_tags])
    return caption

# ===== Отправка поста =====
def send_post(title, lead, details, source, link, hidden_tags):
    event_time = datetime.now().strftime("%d.%m %H:%M")
    header_img = draw_header_image(title, source, event_time)
    caption = build_full_caption(title, lead, details, link, hidden_tags)

    files = {"photo": header_img}
    data = {
        "chat_id": CHANNEL_ID,
        "caption": caption,
        "parse_mode": "HTML"
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    response = requests.post(url, data=data, files=files)

    if response.status_code != 200:
        print("Ошибка публикации:", response.text)
    else:
        print("Успешно опубликовано:", title)

# ======= Пример запуска =======
if __name__ == "__main__":
    send_post(
        title="Тестовый заголовок новости",
        lead="Краткое описание для проверки работы.",
        details="Расширенные подробности новости. Здесь должно быть больше текста.",
        source="rbc.ru",
        link="https://rbc.ru/test",
        hidden_tags=["новости", "тест"]
    )
