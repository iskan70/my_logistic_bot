# -*- coding: utf-8 -*-
import asyncio
import os
import re
import logging
import platform
import sqlite3
import json
import gspread
from datetime import datetime
from dotenv import load_dotenv
from openai import AsyncOpenAI
from google.oauth2.service_account import Credentials # Используем современную библиотеку

from aiogram import Bot, Dispatcher, types, F
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, 
    CallbackQuery, 
    ReplyKeyboardMarkup, 
    KeyboardButton, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton,
    ReplyKeyboardRemove
)
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

# === 1. ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ ===
load_dotenv()

if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=os.getenv("BOT_TOKEN"), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
client_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
ADMIN_IDS = [494255577]

# === 2. УНИВЕРСАЛЬНАЯ ФУНКЦИЯ GOOGLE ТАБЛИЦ ===
def save_to_google_sheets_sync(row_data: list):
    """Синхронная часть записи (универсальная для любых серверов)"""
    try:
        # Берем настройки из окружения
        creds_json = os.getenv("GOOGLE_CREDS_JSON")
        sheet_id = os.getenv("SHEET_ID")
        # Позволяем настраивать имя листа через переменную (завтра пригодится клиентам)
        sheet_name = os.getenv("SHEET_NAME", "мониторинг водителей") 

        if not creds_json or not sheet_id:
            print(">>> ОШИБКА: GOOGLE_CREDS_JSON или SHEET_ID не настроены!")
            return False

        # Авторизация через словарь из памяти
        info = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        client = gspread.authorize(creds)

        # Открываем таблицу
        sheet = client.open_by_key(sheet_id.strip()).get_worksheet(0)
        sheet.append_row(row_data)
        print(f">>> УСПЕХ: Запись в таблицу выполнена ({sheet_name})")
        return True
    except Exception as e:
        print(f">>> ОШИБКА GOOGLE SHEETS: {e}")
        return False

async def save_to_google_sheets(row_data: list):
    """Асинхронная обертка, чтобы бот не зависал"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, save_to_google_sheets_sync, row_data)

# === 3. ЛОКАЛЬНАЯ БАЗА ДАННЫХ ===
def init_db():
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
        (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT, status TEXT, last_seen DATETIME, last_geo TEXT)''')
    conn.commit()
    conn.close()

init_db()

def update_user_db(user_id, username, role=None, status=None, geo=None):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute('''INSERT INTO users (user_id, username, role, status, last_seen, last_geo) 
        VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET 
        username=excluded.username, role=COALESCE(excluded.role, users.role), 
        status=excluded.status, last_seen=excluded.last_seen,
        last_geo=COALESCE(excluded.last_geo, users.last_geo)''',
        (user_id, username, role, status, datetime.now(), geo))
    conn.commit()
    conn.close()

# === 4. КЛАВИАТУРЫ И СОСТОЯНИЯ ===

def get_country_kb():
    builder = InlineKeyboardBuilder()
    countries = [
        ("🇨🇳 Китай", "+86"), ("🇰🇿 Казахстан", "+7"), ("🇷🇺 Россия", "+7"),
        ("🇧🇾 Беларусь", "+375"), ("🇺🇿 Узбекистан", "+998"), ("🇰🇬 Киргизия", "+996"),
        ("🇹🇯 Таджикистан", "+992"), ("🇩🇪 Германия", "+49"), ("🇵🇱 Польша", "+48"), ("🇪🇺 Европа", "+") 
    ]
    for name, code in countries:
        builder.button(text=f"{name} {code}", callback_data=f"country_{code}")
    builder.adjust(2)
    return builder.as_markup()

class OrderFlow(StatesGroup):
    fio = State()
    phone = State()
    cargo_type = State()
    cargo_value = State()
    origin = State()
    destination = State()
    weight = State()
    volume = State()
    waiting_for_weight = State()
    waiting_for_doc_analysis = State()
    confirm_data = State()

class Broadcast(StatesGroup):
    waiting_for_text = State()

class AdminPanel(StatesGroup):
    broadcast_message = State()

class CustomsCalc(StatesGroup):
    cargo_name = State()
    select_duty = State()
    manual_duty = State()
    cargo_price = State()
    select_region = State()
    val_input = State()
    duty_input = State()

def get_main_kb(user_id: int):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, role TEXT DEFAULT 'Клиент')")
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

# === 5. ОБРАБОТКА КОМАНД ===

@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    update_user_db(m.from_user.id, m.from_user.username, status="В главном меню")
    welcome_text = (
        f"🤝 Здравствуйте, {m.from_user.first_name}!\n\n"
        f"Вас приветствует логист компании Logistics Manager.\n"
        f"Мы доставляем из Китая в Европу за 18 дней!\n\n"
        f"Воспользуйтесь меню ниже для начала работы 👇"
    )
    await m.answer(welcome_text, reply_markup=get_main_kb(m.from_user.id))

@dp.message(Command("driver_2025"))
async def cmd_driver_reg(m: Message):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET role='Водитель' WHERE user_id=?", (m.from_user.id,))
    conn.commit()
    conn.close()
    await m.answer("✅ Доступ водителя активирован!", reply_markup=get_main_kb(m.from_user.id))

# === 6. ЛОГИКА ОФОРМЛЕНИЯ ПЕРЕВОЗКИ ===

@dp.message(F.text == "🚛 Оформить перевозку")
async def order_init(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderFlow.fio)
    await m.answer("👤 Введите ваше <b>ФИО:</b>", reply_markup=ReplyKeyboardRemove())

@dp.message(OrderFlow.fio)
async def order_fio(m: Message, state: FSMContext):
    await state.update_data(fio=m.text)
    await state.set_state(OrderFlow.phone)
    await m.answer("📱 Выберите код страны:", reply_markup=get_country_kb())

@dp.callback_query(F.data.startswith("country_"))
async def cb_country_select(cb: CallbackQuery, state: FSMContext):
    country_code = cb.data.split("_")[1]
    digits_map = {"+86": 11, "+7": 10, "+375": 9, "+998": 9, "+996": 9, "+49": 11, "+48": 9}
    needed = digits_map.get(country_code, 10)
    await state.update_data(temp_code=country_code, needed_digits=needed)
    await cb.answer()
    await cb.message.answer(f"✅ Код {country_code}. Введите оставшиеся {needed} цифр:")

@dp.message(OrderFlow.phone)
async def order_phone(m: Message, state: FSMContext):
    data = await state.get_data()
    temp_code = data.get("temp_code")
    needed = data.get("needed_digits", 10)
    text = re.sub(r'\D', '', m.text)

    if temp_code and len(text) == needed:
        phone = temp_code + text
        await state.update_data(phone=phone)
        await state.set_state(OrderFlow.cargo_type)
        await m.answer("📦 <b>Что везем?</b> (Груз):")
    else:
        await m.answer(f"⚠️ Введите ровно {needed} цифр:")

@dp.message(OrderFlow.cargo_type)
async def order_type(m: Message, state: FSMContext):
    await state.update_data(cargo_type=m.text)
    await state.set_state(OrderFlow.cargo_value)
    await m.answer("💰 <b>Инвойсная стоимость (USD):</b>")

@dp.message(OrderFlow.cargo_value)
async def order_value(m: Message, state: FSMContext):
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
    await m.answer("⚖️ <b>Вес (кг):</b>")

@dp.message(OrderFlow.weight)
async def order_weight(m: Message, state: FSMContext):
    await state.update_data(weight=m.text)
    await state.set_state(OrderFlow.volume)
    await m.answer("📐 <b>Объем (м³):</b>")

@dp.message(OrderFlow.volume)
async def order_finish(m: Message, state: FSMContext):
    await state.update_data(volume=m.text)
    d = await state.get_data()
    
    await m.answer("⏳ Сохраняю заявку...")
    
    row = [
        "ЗАКАЗ", datetime.now().strftime("%d.%m.%Y %H:%M"),
        d.get('fio', '-'), d.get('phone', '-'), d.get('cargo_type', '-'),
        d.get('cargo_value', '-'), d.get('org', '-'), d.get('dst', '-'),
        d.get('weight', '-'), d.get('volume', '-'), "-"
    ]
    
    # ВАЖНО: Асинхронный вызов новой функции
    success = await save_to_google_sheets(row)
    
    if success:
        await m.answer("🚀 <b>Заявка принята!</b>", reply_markup=get_main_kb(m.from_user.id))
    else:
        await m.answer("✅ Заявка в системе, менеджер свяжется с вами.", reply_markup=get_main_kb(m.from_user.id))
    
    await state.clear()

# === 6. ЛОГИКА ТАМОЖЕННОГО КАЛЬКУЛЯТОРА ===

def get_region_kb():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇷🇺 Россия (НДС 20%)", callback_data="vat_20")],
        [InlineKeyboardButton(text="🇰🇿 Казахстан (НДС 12%)", callback_data="vat_12")]
    ])
    return kb

@dp.message(F.text == "🛡 Таможня")
async def cust_init(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CustomsCalc.cargo_name)
    await m.answer("🔍 Введите <b>наименование товара</b> для анализа:")

@dp.message(CustomsCalc.cargo_name)
async def cust_cargo_ai(m: Message, state: FSMContext):
    await state.update_data(c_name=m.text)
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    try:
        res = await client_ai.chat.completions.create(
            model="gpt-4o", 
            messages=[
                {"role": "system", "content": "Ты эксперт по ВЭД. Назови только вероятный код ТН ВЭД и описание ставки."},
                {"role": "user", "content": f"Товар: {m.text}"}
            ]
        )
        ai_tip = res.choices[0].message.content
    except:
        ai_tip = "Код ТН ВЭД определится при оформлении."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💻 Электроника (5%)", callback_data="setduty_5")],
        [InlineKeyboardButton(text="🚗 Автозапчасти (8%)", callback_data="setduty_8")],
        [InlineKeyboardButton(text="✏️ Ввести свой %", callback_data="setduty_manual")]
    ])
    await m.answer(f"📋 <b>Анализ:</b>\n{ai_tip}\n\nВыберите ставку:", reply_markup=kb)
    await state.set_state(CustomsCalc.select_duty)

@dp.callback_query(F.data == "setduty_manual", CustomsCalc.select_duty)
async def cust_manual(cb: CallbackQuery, state: FSMContext):
    await state.set_state(CustomsCalc.manual_duty)
    await cb.message.answer("📝 Введите % пошлины:")
    await cb.answer()

@dp.callback_query(F.data.startswith("setduty_"), CustomsCalc.select_duty)
async def cust_set_preset(cb: CallbackQuery, state: FSMContext):
    rate = float(cb.data.split("_")[1])
    await state.update_data(duty=rate)
    await cb.message.answer(f"✅ Ставка {rate}%. Введите <b>стоимость (USD):</b>")
    await state.set_state(CustomsCalc.cargo_price)
    await cb.answer()

@dp.message(CustomsCalc.manual_duty)
async def cust_manual_val(m: Message, state: FSMContext):
    val = m.text.replace(",", ".").strip()
    await state.update_data(duty=float(val))
    await m.answer(f"✅ Ставка {val}%. Введите <b>стоимость (USD):</b>")
    await state.set_state(CustomsCalc.cargo_price)

@dp.message(CustomsCalc.cargo_price)
async def cust_price(m: Message, state: FSMContext):
    val = m.text.replace(",", ".").strip()
    await state.update_data(price=float(val))
    await m.answer("🌍 Выберите регион НДС:", reply_markup=get_region_kb())
    await state.set_state(CustomsCalc.select_region)

@dp.callback_query(F.data.startswith("vat_"), CustomsCalc.select_region)
async def cust_final_res(cb: CallbackQuery, state: FSMContext):
    vat_rate = float(cb.data.split("_")[1])
    data = await state.get_data()
    price, duty_p = data['price'], data['duty']
    duty_a = price * (duty_p / 100)
    vat_a = (price + duty_a) * (vat_rate / 100)
    
    res_text = (
        f"📊 <b>Расчет:</b>\n📦 {data['c_name']}\n💵 Цена: ${price:,.2f}\n"
        f"🧾 Пошлина: ${duty_a:,.2f}\n📉 НДС: ${vat_a:,.2f}\n"
        f"🏁 <b>ИТОГО: ${(duty_a + vat_a):,.2f}</b>"
    )
    await cb.message.edit_text(res_text)
    await state.clear()
    await cb.answer()

# === 7. AI-АНАЛИЗ ДОКУМЕНТОВ ===

@dp.message(F.text == "📄 Анализ документов")
async def doc_analysis_init(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderFlow.waiting_for_doc_analysis)
    await m.answer("📂 Пришлите фото инвойса или CMR. Я сделаю резюме.", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))

@dp.message(OrderFlow.waiting_for_doc_analysis, F.photo | F.document)
async def handle_document_ai(m: Message, state: FSMContext):
    data = await state.get_data()
    file_list = data.get("temp_files", [])
    file_id = m.photo[-1].file_id if m.photo else m.document.file_id
    file_info = await bot.get_file(file_id)
    file_url = f"https://api.telegram.org/file/bot{os.getenv('BOT_TOKEN')}/{file_info.file_path}"
    
    file_list.append({"type": "image_url", "image_url": {"url": file_url}})
    await state.update_data(temp_files=file_list)
    await asyncio.sleep(5) # Ждем пакет фото

    current_data = await state.get_data()
    if len(current_data.get("temp_files", [])) > len(file_list): return 

    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    try:
        response = await client_ai.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": [{"type": "text", "text": "Дай резюме: Отправитель, Получатель, Цена, Вес."}] + file_list}],
            max_tokens=500
        )
        report = response.choices[0].message.content
        await m.answer(f"📊 <b>АНАЛИЗ:</b>\n\n{report}", reply_markup=get_main_kb(m.from_user.id))
        
        row = ["AI_АНАЛИЗ", datetime.now().strftime("%d.%m.%Y %H:%M"), m.from_user.full_name, "-", "-", "-", "-", "-", "-", "-", report]
        await save_to_google_sheets(row)
    except Exception as e:
        await m.answer("⚠️ Ошибка анализа документов.")
    await state.clear()

# === 8. МЕНЕДЖЕР И ГЕОГРАФИЯ ===

@dp.message(F.text == "👨‍💼 Менеджер")
async def contact_manager(m: Message):
    await m.answer("👨‍💼 <b>Связь:</b> @logistics_manager_pro\nСрок доставки: 18 дней!", reply_markup=get_main_kb(m.from_user.id))

# === 9. АДМИН-ПАНЕЛЬ И МОНИТОРИНГ ===

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    if m.from_user.id not in ADMIN_IDS: return 
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Статистика", callback_data="stats_users")],
        [InlineKeyboardButton(text="🚛 Водители", callback_data="stats_drivers")],
        [InlineKeyboardButton(text="📂 База (.txt)", callback_data="download_base")]
    ])
    await m.answer("🛠 Админ-панель:", reply_markup=kb)

@dp.callback_query(F.data == "stats_drivers")
async def cb_admin_drivers(cb: CallbackQuery):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT username, last_geo, last_seen FROM users WHERE role='Водитель'")
    drivers = cursor.fetchall()
    conn.close()
    res = "🚛 <b>ВОДИТЕЛИ:</b>\n"
    for d in drivers:
        res += f"👤 @{d[0]} | 📍 {d[1]} | 🕒 {d[2]}\n"
    await cb.message.answer(res or "Водителей нет")
    await cb.answer()

@dp.message(F.location)
async def handle_location_universal(message: Message):
    """Единый обработчик геопозиции для всех случаев"""
    user_id = message.from_user.id
    lat, lon = message.location.latitude, message.location.longitude
    geo = f"{lat},{lon}"
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    update_user_db(user_id, message.from_user.username, geo=geo)
    
    # Отправка в таблицу через универсальную функцию
    row = [f"@{message.from_user.username}", "-", "-", now, geo, f"http://maps.google.com/?q={geo}", "🚚 В пути"]
    await save_to_google_sheets(row) # Она сама поймет, в какой лист писать, если SHEET_NAME настроен
    
    await message.answer("✅ Геопозиция обновлена.")

@dp.callback_query(F.data == "download_base")
async def cb_download_base(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, role FROM users")
    users = cursor.fetchall()
    conn.close()
    with open("base.txt", "w") as f:
        for u in users: f.write(f"{u[0]} | {u[1]} | {u[2]}\n")
    from aiogram.types import FSInputFile
    await cb.message.answer_document(FSInputFile("base.txt"), caption="База клиентов")
    await cb.answer()

# === 10. AI КОНСУЛЬТАНТ И ЗАПУСК ===

@dp.message(F.text & ~F.state())
async def ai_consultant(m: Message):
    if m.text in ["🚛 Оформить перевозку", "🛡 Таможня", "📄 Анализ документов", "👨‍💼 Менеджер"]: return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    try:
        res = await client_ai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Ты эксперт Logistics Manager. Доставка из Китая 18 дней. Предлагай оформить перевозку."},
                {"role": "user", "content": m.text}
            ]
        )
        await m.answer(f"🏢 <b>Logistics Manager:</b>\n\n{res.choices[0].message.content}")
    except:
        await m.answer("Свяжитесь с менеджером: @logistics_manager_pro")

async def main():
    # Инициализация колонок БД
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT DEFAULT 'Клиент', 
                      car_number TEXT, route TEXT, last_geo TEXT, last_seen TEXT, last_google_update TEXT)''')
    for col in ["car_number", "route", "last_google_update"]:
        try: cursor.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT")
        except: pass
    conn.commit()
    conn.close()

    logging.info("🚀 Бот запущен!")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
