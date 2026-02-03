import asyncio
import json
import sqlite3
import html
from typing import List, Dict, Any, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo,
    ReplyKeyboardMarkup, KeyboardButton
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.client.default import DefaultBotProperties

from config import BOT_TOKEN, CHANNEL_ID, MOD_GROUP_ID

# ---------- MODERATORS ----------
MODERATOR_IDS = {535860827, 688059959, 669987059, 464271249}

# ---------- CONSTANTS ----------
SOURCE_TAG = "@car_spot_ua"

# Хештеги (можеш редагувати як хочеш)
TAGS = [
    # КПП
    "#автомат", "#механіка", "#робот", "#варіатор",

    # Кузов
    "#седан", "#універсал", "#хетчбек",
    "#кросовер", "#позашляховик", "#мінівен", "#пікап",

    # Ціна
    "#до3к", "#до5к", "#до10к", "#до15к", "#до20к", "#20кплюс",

    # Тип пального
    "#бензин", "#дизель", "#електро", "#гібрид", "#газ",
]

# ---------- DB ----------
db = sqlite3.connect("autobazar.db")
cur = db.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS submissions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER,
  username TEXT,
  answers TEXT,
  photos TEXT,
  status TEXT DEFAULT 'pending'
)
""")
db.commit()

# додамо колонку tags (якщо ще нема)
try:
    cur.execute("ALTER TABLE submissions ADD COLUMN tags TEXT DEFAULT '[]'")
    db.commit()
except sqlite3.OperationalError:
    # колонка вже існує
    pass

# додамо колонку для збереження типу медіа (photo/video)
try:
    cur.execute("ALTER TABLE submissions ADD COLUMN media_types TEXT DEFAULT '[]'")
    db.commit()
except sqlite3.OperationalError:
    pass

# ---------- FSM ----------
class Form(StatesGroup):
    car_title = State()
    engine = State()
    gearbox = State()
    mileage = State()
    city = State()
    price = State()
    contacts = State()
    description = State()

    photo_main = State()     # 1) головне фото/відео
    photo_back = State()     # 2) ззаду
    photos_extra = State()   # 3) решта

# ---------- Keyboards ----------
def kb_done():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Готово ✅", callback_data="photos_done")]
    ])

def kb_send():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Надіслати на модерацію ✅", callback_data="send_mod")],
        [InlineKeyboardButton(text="Скасувати ❌", callback_data="cancel")]
    ])

def kb_mod(sub_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Approve", callback_data=f"approve:{sub_id}"),
            InlineKeyboardButton(text="❌ Deny", callback_data=f"deny:{sub_id}")
        ]
    ])

def main_menu_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🚗 Подати оголошення")],
            [KeyboardButton(text="ℹ️ Як це працює"), KeyboardButton(text="🔄 Почати заново")],
            [KeyboardButton(text="❌ Скасувати")]
        ],
        resize_keyboard=True
    )

def kb_approve_options(sub_id: int):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏷 Додати хештеги", callback_data=f"addtags:{sub_id}")],
        [InlineKeyboardButton(text="🚀 Постити без хештегів", callback_data=f"postnow:{sub_id}")]
    ])

def kb_tags_picker(sub_id: int, selected: List[str]):
    rows = []
    # по 2 теги в ряд
    for i in range(0, len(TAGS), 2):
        row = []
        for tag in TAGS[i:i+2]:
            mark = "✅ " if tag in selected else ""
            row.append(InlineKeyboardButton(text=f"{mark}{tag}", callback_data=f"tag:{sub_id}:{tag}"))
        rows.append(row)

    rows.append([
        InlineKeyboardButton(text="✅ Готово", callback_data=f"tags_done:{sub_id}"),
        InlineKeyboardButton(text="↩️ Скасувати", callback_data=f"tags_cancel:{sub_id}")
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# ---------- Helpers ----------
def esc(x: Any) -> str:
    return html.escape(str(x), quote=False).strip()

def render_post(data: Dict[str, Any], tags: List[str] | None = None) -> str:
    """Варіант 1: Мінімалістичний (без надписів)"""
    car_title = esc(data.get("car_title", ""))
    engine = esc(data.get("engine", ""))
    gearbox = esc(data.get("gearbox", ""))
    mileage = esc(data.get("mileage", ""))
    city = esc(data.get("city", ""))
    price = esc(data.get("price", ""))
    contacts = esc(data.get("contacts", ""))
    description = esc(data.get("description", ""))

    base = (
        f"🚗 <b>{car_title}</b>\n\n"
        f"⚡ {engine}\n"
        f"🔄 {gearbox}\n"
        f"📏 {mileage} км\n\n"
        f"📍 {city}\n"
        f"💰 {price}\n"
        f"📞 {contacts}\n\n"
        f"📝 {description}\n\n"
	f"━━━━━━━━━━━━━━━\n"
		f"Подати оголошення — @car_spot_ua_bot \n\n"
        f"Канал — {SOURCE_TAG}"
    )

    if tags:
        tags_line = " ".join(tags)
        return base + "\n\n" + tags_line
    return base

async def start_flow(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("1️⃣ Назва авто (наприклад: Audi A4 2013)", reply_markup=main_menu_kb())
    await state.set_state(Form.car_title)

async def publish_submission(sub_id: int, with_tags: List[str] | None = None):
    cur.execute("SELECT user_id, answers, photos, media_types, status FROM submissions WHERE id=?", (sub_id,))
    row = cur.fetchone()
    if not row:
        return None

    user_id, answers_json, photos_json, media_types_json, status = row
    if status != "pending":
        return None

    data = json.loads(answers_json)
    photos = json.loads(photos_json)
    media_types = json.loads(media_types_json) if media_types_json else ["photo"] * len(photos)

    post_text = render_post(data, with_tags or [])

    album: List[InputMediaPhoto | InputMediaVideo] = []
    for i, (media_id, media_type) in enumerate(zip(photos, media_types)):
        if i == 0:
            if media_type == "video":
                album.append(InputMediaVideo(media=media_id, caption=post_text))
            else:
                album.append(InputMediaPhoto(media=media_id, caption=post_text))
        else:
            if media_type == "video":
                album.append(InputMediaVideo(media=media_id))
            else:
                album.append(InputMediaPhoto(media=media_id))

    await bot.send_media_group(CHANNEL_ID, album)

    cur.execute("UPDATE submissions SET status='approved', tags=? WHERE id=?", (json.dumps(with_tags or [], ensure_ascii=False), sub_id))
    db.commit()

    await bot.send_message(user_id, "✅ Оголошення опубліковано", reply_markup=main_menu_kb())
    return True

# ---------- Bot ----------
bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

pending_denies: Dict[int, int] = {}
pending_tag_select: Dict[Tuple[int, int], List[str]] = {}  # (moder_id, sub_id) -> tags

# ---------- Commands / Menu ----------
@dp.message(F.text.in_({"/help", "help", "ℹ️ Як це працює"}))
async def help_cmd(m: Message):
    await m.answer(
        "Як подати оголошення:\n"
        "1) Натисни «🚗 Подати оголошення» або /start\n"
        "2) Відповідай на питання\n"
        "3) Додай фото/відео: 1) головне, 2) ззаду, 3) решта до 8\n"
        "4) Натисни «Надіслати на модерацію ✅»\n\n"
        "Команди:\n"
        "/start — почати\n"
        "/restart — почати заново\n"
        "/cancel — скасувати\n"
        "/help — інструкція",
        reply_markup=main_menu_kb()
    )

@dp.message(F.text.in_({"/cancel", "cancel", "❌ Скасувати"}))
async def cancel_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "Скасовано ✅\nНатисни «🚗 Подати оголошення» або /start, щоб почати знову.",
        reply_markup=main_menu_kb()
    )

@dp.message(F.text.in_({"/restart", "restart", "🔄 Почати заново"}))
async def restart_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Ок, починаємо заново ✅", reply_markup=main_menu_kb())
    await m.answer("1️⃣ Назва авто (наприклад: Audi A4 2013)")
    await state.set_state(Form.car_title)

@dp.message(F.text.in_({"/start", "🚗 Подати оголошення"}))
async def start(m: Message, state: FSMContext):
    await start_flow(m, state)

# ---------- User flow ----------
@dp.message(Form.car_title)
async def q_engine(m: Message, state: FSMContext):
    await state.update_data(car_title=m.text)
    await m.answer("2️⃣ Двигун (наприклад: 2.0 бензин / 2.0 дизель / електро)")
    await state.set_state(Form.engine)

@dp.message(Form.engine)
async def q_gearbox(m: Message, state: FSMContext):
    await state.update_data(engine=m.text)
    await m.answer("3️⃣ Коробка (автомат / механіка / робот / варіатор)")
    await state.set_state(Form.gearbox)

@dp.message(Form.gearbox)
async def q_mileage(m: Message, state: FSMContext):
    await state.update_data(gearbox=m.text)
    await m.answer("4️⃣ Пробіг (тільки число в км) — наприклад: 173383")
    await state.set_state(Form.mileage)

@dp.message(Form.mileage)
async def q_city(m: Message, state: FSMContext):
    await state.update_data(mileage=m.text)
    await m.answer("5️⃣ Місто")
    await state.set_state(Form.city)

@dp.message(Form.city)
async def q_price(m: Message, state: FSMContext):
    await state.update_data(city=m.text)
    await m.answer("6️⃣ Ціна")
    await state.set_state(Form.price)

@dp.message(Form.price)
async def q_contacts(m: Message, state: FSMContext):
    await state.update_data(price=m.text)
    await m.answer("7️⃣ Контакти (телефон або Telegram)")
    await state.set_state(Form.contacts)

@dp.message(Form.contacts)
async def q_description(m: Message, state: FSMContext):
    await state.update_data(contacts=m.text)
    await m.answer("8️⃣ Опис (стан, нюанси)")
    await state.set_state(Form.description)

# ---------- Photos/Videos funnel ----------
@dp.message(Form.description)
async def photo_main_step(m: Message, state: FSMContext):
    await state.update_data(description=m.text, photos=[], media_types=[])
    await m.answer("1️⃣ Надішли ГОЛОВНЕ фото або відео авто (спереду або збоку).\n⚠️ Одне медіа.")
    await state.set_state(Form.photo_main)

@dp.message(Form.photo_main, F.photo)
async def get_main_photo(m: Message, state: FSMContext):
    await state.update_data(photo_main=m.photo[-1].file_id, main_type="photo")
    await m.answer("2️⃣ Надішли фото або відео авто ЗЗАДУ.\n⚠️ Одне медіа.")
    await state.set_state(Form.photo_back)

@dp.message(Form.photo_main, F.video)
async def get_main_video(m: Message, state: FSMContext):
    await state.update_data(photo_main=m.video.file_id, main_type="video")
    await m.answer("2️⃣ Надішли фото або відео авто ЗЗАДУ.\n⚠️ Одне медіа.")
    await state.set_state(Form.photo_back)

@dp.message(Form.photo_main)
async def need_photo_main(m: Message):
    await m.answer("Надішли, будь ласка, ОДНЕ фото або відео (це буде головне).")

@dp.message(Form.photo_back, F.photo)
async def get_back_photo(m: Message, state: FSMContext):
    data = await state.get_data()
    photos = [data["photo_main"], m.photo[-1].file_id]
    media_types = [data["main_type"], "photo"]
    await state.update_data(photos=photos, media_types=media_types)

    await m.answer(
        "3️⃣ Тепер надішли ДОДАТКОВІ фото або відео (до 8 шт) — салон/деталі/нюанси.\n"
        "Коли закінчиш — натисни «Готово ✅».",
        reply_markup=kb_done()
    )
    await state.set_state(Form.photos_extra)

@dp.message(Form.photo_back, F.video)
async def get_back_video(m: Message, state: FSMContext):
    data = await state.get_data()
    photos = [data["photo_main"], m.video.file_id]
    media_types = [data["main_type"], "video"]
    await state.update_data(photos=photos, media_types=media_types)

    await m.answer(
        "3️⃣ Тепер надішли ДОДАТКОВІ фото або відео (до 8 шт) — салон/деталі/нюанси.\n"
        "Коли закінчиш — натисни «Готово ✅».",
        reply_markup=kb_done()
    )
    await state.set_state(Form.photos_extra)

@dp.message(Form.photo_back)
async def need_photo_back(m: Message):
    await m.answer("Надішли, будь ласка, ОДНЕ фото або відео ЗЗАДУ.")

@dp.message(Form.photos_extra, F.photo)
async def collect_extra_photos(m: Message, state: FSMContext):
    data = await state.get_data()
    photos: List[str] = data.get("photos", [])
    media_types: List[str] = data.get("media_types", [])

    if len(photos) >= 10:
        await m.answer("Максимум 10 медіа. Натисни «Готово ✅».", reply_markup=kb_done())
        return

    photos.append(m.photo[-1].file_id)
    media_types.append("photo")
    await state.update_data(photos=photos, media_types=media_types)

@dp.message(Form.photos_extra, F.video)
async def collect_extra_videos(m: Message, state: FSMContext):
    data = await state.get_data()
    photos: List[str] = data.get("photos", [])
    media_types: List[str] = data.get("media_types", [])

    if len(photos) >= 10:
        await m.answer("Максимум 10 медіа. Натисни «Готово ✅».", reply_markup=kb_done())
        return

    photos.append(m.video.file_id)
    media_types.append("video")
    await state.update_data(photos=photos, media_types=media_types)

@dp.callback_query(F.data == "photos_done")
async def photos_done(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])

    if len(photos) < 2:
        await cb.answer("Потрібно мінімум 2 медіа: головне + ззаду.", show_alert=True)
        return

    text = render_post(data)
    await cb.message.answer("Попередній перегляд:\n\n" + text)
    await cb.message.answer("Надіслати на модерацію?", reply_markup=kb_send())
    await cb.answer()

@dp.callback_query(F.data == "cancel")
async def cancel_inline(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Скасовано ✅\nНатисни «🚗 Подати оголошення» або /start.", reply_markup=main_menu_kb())
    await cb.answer()

# ---------- Send to moderation ----------
@dp.callback_query(F.data == "send_mod")
async def send_to_mod(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])
    media_types = data.get("media_types", [])

    if len(photos) < 2:
        await cb.answer("Мінімум 2 медіа: головне + ззаду.", show_alert=True)
        return

    text = render_post(data)

    cur.execute(
        "INSERT INTO submissions (user_id, username, answers, photos, media_types, tags) VALUES (?, ?, ?, ?, ?, ?)",
        (
            cb.from_user.id,
            cb.from_user.username or "",
            json.dumps(data, ensure_ascii=False),
            json.dumps(photos, ensure_ascii=False),
            json.dumps(media_types, ensure_ascii=False),
            json.dumps([], ensure_ascii=False)
        )
    )
    db.commit()
    sub_id = cur.lastrowid

    caption = f"🆕 <b>Заявка #{sub_id}</b>\n\n{text}"

    album: List[InputMediaPhoto | InputMediaVideo] = []
    for i, (media_id, media_type) in enumerate(zip(photos, media_types)):
        if i == 0:
            if media_type == "video":
                album.append(InputMediaVideo(media=media_id, caption=caption))
            else:
                album.append(InputMediaPhoto(media=media_id, caption=caption))
        else:
            if media_type == "video":
                album.append(InputMediaVideo(media=media_id))
            else:
                album.append(InputMediaPhoto(media=media_id))

    await bot.send_media_group(MOD_GROUP_ID, album)
    await bot.send_message(MOD_GROUP_ID, "Модерація", reply_markup=kb_mod(sub_id))

    await cb.message.answer("✅ Відправлено на модерацію", reply_markup=main_menu_kb())
    await state.clear()
    await cb.answer()

# ---------- Approve (now shows options) ----------
@dp.callback_query(F.data.startswith("approve:"))
async def approve(cb: CallbackQuery):
    if cb.from_user.id not in MODERATOR_IDS:
        await cb.answer("Немає доступу", show_alert=True)
        return

    sub_id = int(cb.data.split(":")[1])
    cur.execute("SELECT status FROM submissions WHERE id=?", (sub_id,))
    row = cur.fetchone()
    if not row or row[0] != "pending":
        await cb.answer("Заявка вже оброблена", show_alert=True)
        return

    await cb.message.answer(
        f"✅ Заявка #{sub_id} схвалена.\nДодати хештеги перед публікацією?",
        reply_markup=kb_approve_options(sub_id)
    )
    await cb.answer()

# ---------- Post without tags ----------
@dp.callback_query(F.data.startswith("postnow:"))
async def post_now(cb: CallbackQuery):
    if cb.from_user.id not in MODERATOR_IDS:
        await cb.answer("Немає доступу", show_alert=True)
        return

    sub_id = int(cb.data.split(":")[1])

    ok = await publish_submission(sub_id, with_tags=[])
    if not ok:
        await cb.answer("Не вдалося. Можливо вже опубліковано/оброблено.", show_alert=True)
        return

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Опубліковано ✅")

# ---------- Start picking tags ----------
@dp.callback_query(F.data.startswith("addtags:"))
async def add_tags(cb: CallbackQuery):
    if cb.from_user.id not in MODERATOR_IDS:
        await cb.answer("Немає доступу", show_alert=True)
        return

    sub_id = int(cb.data.split(":")[1])
    key = (cb.from_user.id, sub_id)
    if key not in pending_tag_select:
        pending_tag_select[key] = []

    await cb.message.answer(
        f"🏷 Обери хештеги для заявки #{sub_id} (можна декілька):",
        reply_markup=kb_tags_picker(sub_id, pending_tag_select[key])
    )
    await cb.answer()

# ---------- Toggle tag ----------
@dp.callback_query(F.data.startswith("tag:"))
async def toggle_tag(cb: CallbackQuery):
    if cb.from_user.id not in MODERATOR_IDS:
        await cb.answer("Немає доступу", show_alert=True)
        return

    _, sub_id_str, tag = cb.data.split(":", 2)
    sub_id = int(sub_id_str)
    key = (cb.from_user.id, sub_id)

    selected = pending_tag_select.get(key, [])
    if tag in selected:
        selected.remove(tag)
    else:
        selected.append(tag)

    pending_tag_select[key] = selected

    await cb.message.edit_reply_markup(reply_markup=kb_tags_picker(sub_id, selected))
    await cb.answer()

# ---------- Done selecting tags => publish ----------
@dp.callback_query(F.data.startswith("tags_done:"))
async def tags_done(cb: CallbackQuery):
    if cb.from_user.id not in MODERATOR_IDS:
        await cb.answer("Немає доступу", show_alert=True)
        return

    sub_id = int(cb.data.split(":")[1])
    key = (cb.from_user.id, sub_id)
    selected = pending_tag_select.pop(key, [])

    ok = await publish_submission(sub_id, with_tags=selected)
    if not ok:
        await cb.answer("Не вдалося. Можливо вже опубліковано/оброблено.", show_alert=True)
        return

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Опубліковано з тегами ✅")

# ---------- Cancel selecting tags ----------
@dp.callback_query(F.data.startswith("tags_cancel:"))
async def tags_cancel(cb: CallbackQuery):
    if cb.from_user.id not in MODERATOR_IDS:
        await cb.answer("Немає доступу", show_alert=True)
        return

    sub_id = int(cb.data.split(":")[1])
    key = (cb.from_user.id, sub_id)
    pending_tag_select.pop(key, None)

    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.answer("Скасувано")

# ---------- Deny ----------
@dp.callback_query(F.data.startswith("deny:"))
async def deny(cb: CallbackQuery):
    if cb.from_user.id not in MODERATOR_IDS:
        await cb.answer("Немає доступу", show_alert=True)
        return

    sub_id = int(cb.data.split(":")[1])
    pending_denies[cb.from_user.id] = sub_id
    await cb.message.answer("Напиши причину відмови одним повідомленням")
    await cb.answer()

@dp.message(F.text)
async def deny_reason(m: Message):
    if m.from_user.id not in pending_denies:
        return

    sub_id = pending_denies.pop(m.from_user.id)

    cur.execute("SELECT user_id, status FROM submissions WHERE id=?", (sub_id,))
    row = cur.fetchone()
    if not row:
        return
    user_id, status = row
    if status != "pending":
        return

    cur.execute("UPDATE submissions SET status='denied' WHERE id=?", (sub_id,))
    db.commit()

    await bot.send_message(
        user_id,
        f"❌ Оголошення відхилено.\nПричина: {m.text}\n\nНатисни «🚗 Подати оголошення» або /start — подати заново.",
        reply_markup=main_menu_kb()
    )
    await m.answer("Причину надіслано ✅")

# ---------- Run ----------
async def main():
    await dp.start_polling(bot)

# ---------- For Render Web Service ----------
from aiohttp import web
import os

async def health_check(request):
    return web.Response(text="Bot is running!")

async def start_bot_and_server():
    # Запускаємо бота в окремій задачі
    bot_task = asyncio.create_task(dp.start_polling(bot))
    
    # Запускаємо HTTP сервер для Render
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get('PORT', 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    print(f"HTTP server started on port {port}")
    
    # Чекаємо завершення бота
    await bot_task

if __name__ == "__main__":
    # Якщо є PORT (Render Web Service) - запускаємо з HTTP сервером
    if os.environ.get('PORT'):
        asyncio.run(start_bot_and_server())
    else:
        # Локально - просто бот
        asyncio.run(main())
