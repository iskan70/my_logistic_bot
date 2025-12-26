# -*- coding: utf-8 -*-
import asyncio
import os
import re
import logging
import platform
import sqlite3
import json
import base64
import gspread
from datetime import datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI
from google.oauth2.service_account import Credentials

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

# =========================================================
# 1. КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# =========================================================
load_dotenv()

# Настройка для Windows (локальная разработка)
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Инициализация Bot и AI
bot = Bot(token=os.getenv("BOT_TOKEN"), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
client_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())

# Константы
ADMIN_IDS = [494255577]
SHEET_ID = os.getenv("SHEET_ID")

# =========================================================
# 2. СОСТОЯНИЯ (FSM)
# =========================================================
class OrderFlow(StatesGroup):
    fio = State()
    phone = State()
    cargo_type = State()
    cargo_value = State()
    origin = State()
    destination = State()
    weight = State()
    volume = State()
    waiting_for_doc_analysis = State()

class CustomsCalc(StatesGroup):
    cargo_name = State()
    select_duty = State()
    manual_duty = State()
    cargo_price = State()
    select_region = State()

class Broadcast(StatesGroup):
    waiting_for_text = State()
    waiting_for_retry = State()

# =========================================================
# 3. РАБОТА С ДАННЫМИ (DB & GOOGLE)
# =========================================================
def init_db():
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
        (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT DEFAULT 'Клиент', 
        status TEXT, last_seen TEXT, last_geo TEXT, car_number TEXT, route TEXT, last_google_update TEXT)''')
    
    # Принудительное обновление колонок для старых баз
    cols = [column[1] for column in cursor.execute("PRAGMA table_info(users)").fetchall()]
    for col in ["car_number", "route", "last_google_update"]:
        if col not in cols:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()

async def get_gs_client():
    """Авторизация в Google Sheets через JSON из переменной окружения"""
    creds_json = os.getenv("GOOGLE_CREDS_JSON")
    if not creds_json:
        logging.error("GOOGLE_CREDS_JSON не найден в переменных окружения!")
        return None
    info = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    return gspread.authorize(creds)

async def save_to_google_sheets(row_data: list, sheet_name=None):
    try:
        client = await get_gs_client()
        if not client: return False
        ss = client.open_by_key(SHEET_ID.strip())
        sheet = ss.worksheet(sheet_name) if sheet_name else ss.get_worksheet(0)
        sheet.append_row(row_data)
        return True
    except Exception as e:
        logging.error(f"GS Error: {e}")
        return False

# =========================================================
# 4. КЛАВИАТУРЫ
# =========================================================
def get_main_kb(user_id: int):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    role = row[0] if row else "Клиент"
    conn.close()

    btns = [
        [KeyboardButton(text="🚛 Оформить перевозку"), KeyboardButton(text="🛡 Таможня")],
        [KeyboardButton(text="📄 Анализ документов"), KeyboardButton(text="👨‍💼 Менеджер")]
    ]
    if user_id in ADMIN_IDS or role == "Водитель":
        btns.append([KeyboardButton(text="🚀 Начать рейс (Включить GPS)", request_location=True)])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_country_kb():
    builder = InlineKeyboardBuilder()
    countries = [("🇨🇳 Китай +86", "+86"), ("🇰🇿 Каз +7", "+7"), ("🇷🇺 Рос +7", "+7"), 
                 ("🇧🇾 Бел +375", "+375"), ("🇺🇿 Узб +998", "+998"), ("🇪🇺 Европа +", "+")]
    for name, code in countries:
        builder.button(text=name, callback_data=f"country_{code}")
    return builder.adjust(2).as_markup()

def get_region_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Россия (НДС 20%)", callback_data="vat_20")],
        [InlineKeyboardButton(text="🇰🇿 Казахстан (НДС 12%)", callback_data="vat_12")]
    ])

# =========================================================
# 5. КОМАНДЫ (START, ADMIN, DRIVER)
# =========================================================
@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    conn = sqlite3.connect('logistics.db')
    conn.execute("INSERT INTO users (user_id, username, last_seen, status) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET last_seen=excluded.last_seen, status=excluded.status",
                 (m.from_user.id, m.from_user.username, datetime.now().strftime("%d.%m.%Y %H:%M"), "В главном меню"))
    conn.commit()
    conn.close()
    
    welcome = (f"🤝 Здравствуйте, {m.from_user.first_name}!\n\n"
               f"Вас приветствует <b>Logistics Manager</b>.\n"
               f"Мы доставляем из Китая в Европу за 18 дней по лучшим ценам.\n\n"
               f"Выберите нужное действие в меню 👇")
    await m.answer(welcome, reply_markup=get_main_kb(m.from_user.id))

@dp.message(Command("driver_2025"))
async def cmd_driver_reg(m: Message):
    conn = sqlite3.connect('logistics.db')
    conn.execute("UPDATE users SET role='Водитель' WHERE user_id=?", (m.from_user.id,))
    conn.commit()
    conn.close()
    await m.answer("✅ <b>Доступ водителя активирован!</b>\nТеперь вам доступна кнопка GPS.", reply_markup=get_main_kb(m.from_user.id))

@dp.message(Command("admin"))
async def cmd_admin(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Статистика", callback_data="stats_users")],
        [InlineKeyboardButton(text="🚛 Мониторинг", callback_data="stats_drivers")],
        [InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📂 Скачать базу", callback_data="download_base")]
    ])
    await m.answer("🛠 <b>Панель администратора:</b>", reply_markup=kb)

# =========================================================
# 6. АНКЕТА ПЕРЕВОЗКИ (11 КОЛОНОК)
# =========================================================
@dp.message(F.text == "🚛 Оформить перевозку")
async def order_init(m: Message, state: FSMContext):
    await state.set_state(OrderFlow.fio)
    await m.answer("👤 Введите ваше <b>ФИО:</b>", reply_markup=ReplyKeyboardRemove())

@dp.message(OrderFlow.fio)
async def order_fio(m: Message, state: FSMContext):
    await state.update_data(fio=m.text)
    await state.set_state(OrderFlow.phone)
    await m.answer("📱 Выберите код страны:", reply_markup=get_country_kb())

@dp.callback_query(F.data.startswith("country_"), OrderFlow.phone)
async def cb_phone_code(cb: CallbackQuery, state: FSMContext):
    code = cb.data.split("_")[1]
    digits = {"+86": 11, "+7": 10, "+375": 9, "+998": 9}.get(code, 10)
    await state.update_data(temp_code=code, needed=digits)
    await cb.message.answer(f"Введите оставшиеся <b>{digits}</b> цифр номера:")
    await cb.answer()

@dp.message(OrderFlow.phone)
async def order_phone_val(m: Message, state: FSMContext):
    data = await state.get_data()
    code, needed = data.get("temp_code"), data.get("needed")
    clean = re.sub(r'\D', '', m.text)
    
    if code and len(clean) == needed:
        await state.update_data(phone=code + clean)
        await state.set_state(OrderFlow.cargo_type)
        await m.answer("📦 <b>Что везем?</b> (Наименование):")
    else:
        await m.answer(f"⚠️ Нужно {needed} цифр. Попробуйте еще раз.")

@dp.message(OrderFlow.cargo_type)
async def order_type(m: Message, state: FSMContext):
    await state.update_data(cargo_type=m.text)
    await state.set_state(OrderFlow.cargo_value)
    await m.answer("💰 <b>Инвойсная стоимость</b> (USD):")

@dp.message(OrderFlow.cargo_value)
async def order_val(m: Message, state: FSMContext):
    await state.update_data(cargo_value=m.text)
    await state.set_state(OrderFlow.origin)
    await m.answer("📍 <b>Пункт отправления:</b>")

@dp.message(OrderFlow.origin)
async def order_org(m: Message, state: FSMContext):
    await state.update_data(org=m.text)
    await state.set_state(OrderFlow.destination)
    await m.answer("🏁 <b>Пункт назначения:</b>")

@dp.message(OrderFlow.destination)
async def order_dst(m: Message, state: FSMContext):
    await state.update_data(dst=m.text)
    await state.set_state(OrderFlow.weight)
    await m.answer("⚖️ Общий <b>вес</b> (кг):")

@dp.message(OrderFlow.weight)
async def order_w(m: Message, state: FSMContext):
    await state.update_data(weight=m.text)
    await state.set_state(OrderFlow.volume)
    await m.answer("📐 Общий <b>объем</b> (м³):")

@dp.message(OrderFlow.volume)
async def order_finish(m: Message, state: FSMContext):
    await state.update_data(volume=m.text)
    d = await state.get_data()
    row = [
        "ЗАКАЗ", datetime.now().strftime("%d.%m.%Y %H:%M"),
        d.get('fio'), d.get('phone'), d.get('cargo_type'), d.get('cargo_value'),
        d.get('org'), d.get('dst'), d.get('weight'), d.get('volume'), "-"
    ]
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    success = await save_to_google_sheets(row)
    text = "🚀 <b>Заявка принята!</b> Менеджер скоро свяжется с вами." if success else "✅ Заявка сохранена!"
    await m.answer(text, reply_markup=get_main_kb(m.from_user.id))
    await state.clear()

# =========================================================
# 7. VISION AI: АНАЛИЗ ДОКУМЕНТОВ (Base64)
# =========================================================
@dp.message(F.text == "📄 Анализ документов")
async def doc_init(m: Message, state: FSMContext):
    await state.set_state(OrderFlow.waiting_for_doc_analysis)
    await m.answer("📂 Пришлите ФОТО документа. Я проанализирую данные через AI.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))

@dp.message(OrderFlow.waiting_for_doc_analysis, F.photo)
async def handle_doc_ai(m: Message, state: FSMContext):
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    # Загрузка и конвертация в Base64 для надежности
    file_info = await bot.get_file(m.photo[-1].file_id)
    photo_bytes = await bot.download_file(file_info.file_path)
    base64_image = base64.b64encode(photo_bytes.getvalue()).decode('utf-8')

    prompt = "Ты эксперт Logistics Manager. Проанализируй фото документа и выдай: 1. Отправитель, 2. Получатель, 3. Товар, 4. Вес, 5. Цена. На русском."
    
    try:
        response = await client_ai.chat.completions.create(
            model="gpt-4o",
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]
            }]
        )
        report = response.choices[0].message.content
        await m.answer(f"📊 <b>РЕЗЮМЕ AI:</b>\n\n{report}", reply_markup=get_main_kb(m.from_user.id))
        
        row = ["AI_АНАЛИЗ", datetime.now().strftime("%d.%m.%Y %H:%M"), m.from_user.full_name, "-", "-", "-", "-", "-", "-", "-", report]
        await save_to_google_sheets(row)
    except Exception as e:
        logging.error(f"Vision Error: {e}")
        await m.answer("⚠️ Ошибка анализа. Попробуйте еще раз.")
    await state.clear()

# =========================================================
# 8. ТАМОЖЕННЫЙ КАЛЬКУЛЯТОР
# =========================================================
@dp.message(F.text == "🛡 Таможня")
async def cust_init(m: Message, state: FSMContext):
    await state.set_state(CustomsCalc.cargo_name)
    await m.answer("🔍 Введите название товара:")

@dp.message(CustomsCalc.cargo_name)
async def cust_ai_tip(m: Message, state: FSMContext):
    await state.update_data(c_name=m.text)
    res = await client_ai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "Назови только код ТН ВЭД и ставку %."}, {"role": "user", "content": m.text}]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5%", callback_data="setduty_5"), InlineKeyboardButton(text="10%", callback_data="setduty_10")],
        [InlineKeyboardButton(text="Свой %", callback_data="setduty_manual")]
    ])
    await m.answer(f"💡 Подсказка AI: {res.choices[0].message.content}\nВыберите ставку:", reply_markup=kb)
    await state.set_state(CustomsCalc.select_duty)

@dp.callback_query(F.data.startswith("setduty_"), CustomsCalc.select_duty)
async def cust_set(cb: CallbackQuery, state: FSMContext):
    if cb.data == "setduty_manual":
        await cb.message.answer("Введите число %:")
        await state.set_state(CustomsCalc.manual_duty)
    else:
        await state.update_data(duty=float(cb.data.split("_")[1]))
        await cb.message.answer("💰 Стоимость товара (USD):")
        await state.set_state(CustomsCalc.cargo_price)
    await cb.answer()

@dp.message(CustomsCalc.cargo_price)
async def cust_final(m: Message, state: FSMContext):
    data = await state.get_data()
    price = float(m.text.replace(",", "."))
    duty_p = data['duty']
    duty_v = price * (duty_p / 100)
    total = duty_v + (price + duty_v) * 0.2  # Пример НДС 20%
    await m.answer(f"📊 <b>Расчет:</b>\nПошлина: ${duty_v:.2f}\nИтого с НДС (ориентир): ${total:.2f}")
    await state.clear()

# =========================================================
# 9. GPS МОНИТОРИНГ (Edited & Manual)
# =========================================================
@dp.message(F.location)
async def handle_manual_geo(m: Message):
    lat, lon = m.location.latitude, m.location.longitude
    geo = f"{lat},{lon}"
    map_url = f"https://www.google.com/maps?q={geo}"
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    conn = sqlite3.connect('logistics.db')
    u = conn.execute("SELECT username, car_number, route FROM users WHERE user_id=?", (m.from_user.id,)).fetchone()
    conn.execute("UPDATE users SET last_geo=?, last_seen=? WHERE user_id=?", (geo, now, m.from_user.id))
    conn.commit()
    conn.close()

    row = [u[0] or m.from_user.full_name, u[1] or "-", u[2] or "-", now, geo, map_url, "🚀 Начал рейс"]
    await save_to_google_sheets(row, "мониторинг водителей")
    await m.answer("✅ <b>Рейс запущен!</b> GPS транслируется.")

@dp.edited_message(F.location)
async def handle_live_geo(m: Message):
    user_id = m.from_user.id
    geo = f"{m.location.latitude},{m.location.longitude}"
    now = datetime.now()
    
    conn = sqlite3.connect('logistics.db')
    u = conn.execute("SELECT username, car_number, route, last_google_update FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    # Ограничение 3 часа для записи в Google Sheets
    should_update_gs = True
    if u and u[3]:
        last_dt = datetime.strptime(u[3], "%d.%m.%Y %H:%M")
        if (now - last_dt).total_seconds() < 10800:
            should_update_gs = False

    if should_update_gs:
        map_url = f"https://www.google.com/maps?q={geo}"
        row = [u[0] or "Водитель", u[1] or "-", u[2] or "-", now.strftime("%d.%m.%Y %H:%M"), geo, map_url, "🚚 В пути"]
        await save_to_google_sheets(row, "мониторинг водителей")
        conn.execute("UPDATE users SET last_google_update=? WHERE user_id=?", (now.strftime("%d.%m.%Y %H:%M"), user_id))
    
    conn.execute("UPDATE users SET last_geo=?, last_seen=? WHERE user_id=?", (geo, now.strftime("%d.%m.%Y %H:%M"), user_id))
    conn.commit()
    conn.close()

# =========================================================
# 10. АДМИНКА, РАССЫЛКА И AI-КОНСУЛЬТАНТ
# =========================================================
@dp.callback_query(F.data == "stats_users")
async def cb_stats(cb: CallbackQuery):
    conn = sqlite3.connect('logistics.db')
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    conn.close()
    await cb.message.answer(f"📊 Всего пользователей: <b>{total}</b>")
    await cb.answer()

@dp.message(F.text & ~F.state())
async def ai_consultant(m: Message):
    if m.text in ["🚛 Оформить перевозку", "🛡 Таможня", "📄 Анализ документов", "👨‍💼 Менеджер"]: return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    res = await client_ai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "Ты эксперт Logistics Manager. Доставка из Китая в Европу 18 дней, низкие цены. Предлагай нажать 'Оформить перевозку'."}, 
                  {"role": "user", "content": m.text}]
    )
    await m.answer(f"🏢 <b>Logistics Manager:</b>\n\n{res.choices[0].message.content}")

# =========================================================
# ЗАПУСК
# =========================================================
async def main():
    init_db()
    print("🚀 Бот Logistics Manager запущен...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
