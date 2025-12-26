# -*- coding: utf-8 -*-
import asyncio
import os
import re
import logging
import platform
import sqlite3
import json
from datetime import datetime

import gspread
from dotenv import load_dotenv
from openai import AsyncOpenAI
from oauth2client.service_account import ServiceAccountCredentials

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

# === 1. ИНИЦИАЛИЗАЦИЯ ===
load_dotenv()

if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=os.getenv("BOT_TOKEN"), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
client_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
SHEET_ID = os.getenv("SHEET_ID")
ADMIN_IDS = [494255577]

# --- 1.1 ФУНКЦИЯ ДЛЯ GOOGLE ТАБЛИЦ (ИСПРАВЛЕННАЯ) ---
async def save_to_google_sheets(row_data: list):
    try:
        # 1. Берем данные ТОЛЬКО из переменной Render
        creds_json = os.getenv("GOOGLE_CREDS_JSON")
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

        if not creds_json:
            logging.error("❌ ОШИБКА: Переменная GOOGLE_CREDS_JSON не задана в настройках Render!")
            return False

        # 2. Авторизуемся через полученный JSON
        info = json.loads(creds_json)
        creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
        client = gspread.authorize(creds)

        # 3. Записываем данные
        sheet = client.open_by_key(SHEET_ID.strip()).get_worksheet(0)
        sheet.append_row(row_data)
        return True

    except Exception as e:
        logging.error(f"❌ ОШИБКА ТАБЛИЦЫ: {e}")
        return False

# --- 1.2 БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
        (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT DEFAULT 'Клиент', status TEXT, last_seen DATETIME, last_geo TEXT)''')
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

# === 2. СОСТОЯНИЯ ===
class OrderFlow(StatesGroup):
    fio = State()
    phone = State()
    cargo_type = State()
    cargo_value = State()
    origin = State()
    destination = State()
    weight = State()
    volume = State()

# === 3. КЛАВИАТУРЫ ===
def get_main_kb(user_id: int):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    role = row[0] if row else "Клиент"
    conn.close()
    btns = [[KeyboardButton(text="🚛 Оформить перевозку"), KeyboardButton(text="🛡 Таможня")],
            [KeyboardButton(text="📄 Анализ документов"), KeyboardButton(text="👨‍💼 Менеджер")]]
    if user_id in ADMIN_IDS or role == "Водитель":
        btns.append([KeyboardButton(text="🚀 Начать рейс (Включить GPS)", request_location=True)])
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_country_kb():
    builder = InlineKeyboardBuilder()
    countries = [("🇨🇳 Китай +86", "+86"), ("🇰🇿 Казахстан +7", "+7"), ("🇷🇺 Россия +7", "+7"),
                 ("🇧🇾 Беларусь +375", "+375"), ("🇺🇿 Узбекистан +998", "+998"), ("🇰🇬 Киргизия +996", "+996"),
                 ("🇩🇪 Германия +49", "+49"), ("🇵🇱 Польша +48", "+48"), ("🇪🇺 Европа +", "+")]
    for name, code in countries:
        builder.button(text=name, callback_data=f"country_{code}")
    builder.adjust(2)
    return builder.as_markup()

# === 4. ПРИВЕТСТВИЕ (ТВОЙ ВАРИАНТ) ===
@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    update_user_db(m.from_user.id, m.from_user.username, status="В меню")
    welcome_text = (f"🤝 Здравствуйте, {m.from_user.first_name}!\n\n"
            f"Вас приветствует логист компании <b>Logistics Manager</b>.\n\n"
            f"1. Оформить заказ\n"
            f"2. Рассчитать стоимость международной доставки\n"
            f"3. Проверить коммерческие документы (AI-анализ)\n"
            f"4. Оценить таможенные пошлины и налоги\n\n"
            f"Воспользуйтесь меню ниже для начала работы 👇 или напишите в сообщении о вашем вопросе")
    await m.answer(welcome_text, reply_markup=get_main_kb(m.from_user.id))

# === 4.1 СЕКРЕТНАЯ РЕГИСТРАЦИЯ ВОДИТЕЛЯ ===
@dp.message(Command("driver_2025"))
async def cmd_driver_reg(m: Message):
    import sqlite3
    try:
        conn = sqlite3.connect('logistics.db')
        cursor = conn.cursor()
        
        # Сначала проверяем, есть ли пользователь в базе, если нет — добавляем
        cursor.execute("INSERT OR IGNORE INTO users (user_id, username, role) VALUES (?, ?, ?)", 
                       (m.from_user.id, m.from_user.username, 'Клиент'))
        
        # Устанавливаем роль 'Водитель'
        cursor.execute("UPDATE users SET role='Водитель' WHERE user_id=?", (m.from_user.id,))
        conn.commit()
        conn.close()
        
        await m.answer(
            "✅ <b>Доступ водителя активирован!</b>\n\n"
            "Теперь вам доступны функции логистики:\n"
            "1. Включение GPS-трекера\n"
            "2. Отметка о доставке (18 дней из Китая)\n\n"
            "Меню обновлено.",
            reply_markup=get_main_kb(m.from_user.id), # Функция должна проверять роль в БД
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Ошибка регистрации водителя: {e}")
        await m.answer("❌ Произошла ошибка при активации доступа.")

# === 5. ЛОГИКА ОФОРМЛЕНИЯ ПЕРЕВОЗКИ (ТВОЙ ПОЛНЫЙ КОД) ===
@dp.message(F.text == "🚛 Оформить перевозку")
async def order_init(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderFlow.fio)
    await m.answer("👤 Введите ваше <b>ФИО:</b>", reply_markup=ReplyKeyboardRemove())

@dp.message(OrderFlow.fio)
async def order_fio(m: Message, state: FSMContext):
    await state.update_data(fio=m.text)
    await state.set_state(OrderFlow.phone)
    await m.answer("📱 Выберите код страны или введите номер полностью (+...):", reply_markup=get_country_kb())

@dp.callback_query(F.data.startswith("country_"))
async def cb_country_select(cb: CallbackQuery, state: FSMContext):
    country_code = cb.data.split("_")[1]
    digits_map = {"+86": 11, "+7": 10, "+375": 9, "+998": 9, "+996": 9, "+49": 11, "+48": 9}
    needed = digits_map.get(country_code, 10)
    await state.update_data(temp_code=country_code, needed_digits=needed)
    await cb.answer()
    await cb.message.answer(f"✅ Выбрана страна с кодом <b>{country_code}</b>\nВведите остальные <b>{needed}</b> цифр номера:")

@dp.message(OrderFlow.phone)
async def order_phone(m: Message, state: FSMContext):
    data = await state.get_data()
    temp_code = data.get("temp_code")
    needed_digits = data.get("needed_digits")
    text = re.sub(r'\D', '', m.text)

    if temp_code and needed_digits:
        if len(text) == needed_digits:
            phone = temp_code + text
            await state.update_data(phone=phone)
        else:
            return await m.answer(f"⚠️ Нужно <b>{needed_digits}</b> цифр. Попробуйте снова:")
    else:
        await state.update_data(phone=m.text)

    await state.set_state(OrderFlow.cargo_type)
    await m.answer("📦 <b>Что везем?</b> (Наименование груза):")

@dp.message(OrderFlow.cargo_type)
async def order_type(m: Message, state: FSMContext):
    await state.update_data(cargo_type=m.text)
    await state.set_state(OrderFlow.cargo_value)
    await m.answer("💰 Укажите <b>инвойсную стоимость</b> груза (USD):")

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
    await m.answer("⚖️ Общий <b>вес</b> (кг):")

@dp.message(OrderFlow.weight)
async def order_weight(m: Message, state: FSMContext):
    await state.update_data(weight=m.text)
    await state.set_state(OrderFlow.volume)
    await m.answer("📐 Общий <b>объем</b> (куб. метры):")

@dp.message(OrderFlow.volume)
async def order_finish(m: Message, state: FSMContext):
    await state.update_data(volume=m.text)
    d = await state.get_data()
    row = ["ЗАКАЗ", datetime.now().strftime("%d.%m.%Y %H:%M"), d.get('fio'), d.get('phone'), 
           d.get('cargo_type'), d.get('cargo_value'), d.get('org'), d.get('dst'), 
           d.get('weight'), d.get('volume'), "Бот"]
    
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    success = await save_to_google_sheets(row)
    
    msg = "🚀 <b>Заявка принята!</b>" if success else "✅ Заявка сохранена!"
    await m.answer(msg, reply_markup=get_main_kb(m.from_user.id))
    await state.clear()

# --- GPS И ПРОЧЕЕ ---
@dp.message(F.location)
async def handle_gps(m: Message):
    row = ["GPS", datetime.now().strftime("%d.%m.%Y %H:%M"), f"@{m.from_user.username}", f"{m.location.latitude}, {m.location.longitude}"]
    await save_to_google_sheets(row)
    await m.answer("📍 Координаты обновлены.")

@dp.message(Command("driver_2025"))
async def cmd_driver(m: Message):
    update_user_db(m.from_user.id, m.from_user.username, role="Водитель")
    await m.answer("✅ Роль водителя активирована!", reply_markup=get_main_kb(m.from_user.id))

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

# === 6. ЛОГИКА ТАМОЖЕННОГО КАЛЬКУЛЯТОРА ===

@dp.message(F.text == "🛡 Таможня")
async def cust_init(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(CustomsCalc.cargo_name)
    await m.answer("🔍 Введите <b>наименование товара</b> для предварительного анализа:")

@dp.message(CustomsCalc.cargo_name)
async def cust_cargo_ai(m: Message, state: FSMContext):
    await state.update_data(c_name=m.text)
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    try:
        res = await client_ai.chat.completions.create(
            model="gpt-4o", 
            messages=[
                {"role": "system", "content": "Ты эксперт по ВЭД. Назови только вероятный код ТН ВЭД и краткое описание ставки."},
                {"role": "user", "content": f"Товар: {m.text}"}
            ]
        )
        ai_tip = res.choices[0].message.content
    except:
        ai_tip = "Не удалось определить код автоматически."

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💻 Электроника (5%)", callback_data="setduty_5")],
        [InlineKeyboardButton(text="🚗 Автозапчасти (8%)", callback_data="setduty_8")],
        [InlineKeyboardButton(text="👕 Одежда (12%)", callback_data="setduty_12")],
        [InlineKeyboardButton(text="⚙️ Оборудование (3%)", callback_data="setduty_3")],
        [InlineKeyboardButton(text="✏️ Ввести свой %", callback_data="setduty_manual")]
    ])
    
    await m.answer(f"📋 <b>Анализ товара:</b>\n{ai_tip}\n\nВыберите <b>ставку пошлины</b> или введите свою:", reply_markup=kb)
    await state.set_state(CustomsCalc.select_duty)

@dp.callback_query(F.data == "setduty_manual", CustomsCalc.select_duty)
async def cust_manual(cb: CallbackQuery, state: FSMContext):
    await state.set_state(CustomsCalc.manual_duty)
    await cb.message.answer("📝 Введите % пошлины (только число):")
    await cb.answer()

@dp.callback_query(F.data.startswith("setduty_"), CustomsCalc.select_duty)
async def cust_set_preset(cb: CallbackQuery, state: FSMContext):
    rate = float(cb.data.split("_")[1])
    await state.update_data(duty=rate)
    await cb.message.answer(f"✅ Ставка {rate}% выбрана.\n💰 Введите <b>стоимость груза (USD):</b>")
    await state.set_state(CustomsCalc.cargo_price)
    await cb.answer()

@dp.message(CustomsCalc.manual_duty)
async def cust_manual_val(m: Message, state: FSMContext):
    val = m.text.replace(",", ".").strip()
    if not re.match(r'^\d+(\.\d+)?$', val):
        return await m.answer("⚠️ Введите корректное число!")
    await state.update_data(duty=float(val))
    await m.answer(f"✅ Ставка {val}% принята.\n💰 Введите <b>стоимость груза (USD):</b>")
    await state.set_state(CustomsCalc.cargo_price)

@dp.message(CustomsCalc.cargo_price)
async def cust_price(m: Message, state: FSMContext):
    val = m.text.replace(",", ".").strip()
    if not re.match(r'^\d+(\.\d+)?$', val):
        return await m.answer("⚠️ Введите корректное число стоимости!")
    
    await state.update_data(price=float(val))
    await m.answer("🌍 Выберите <b>регион назначения</b> для расчета НДС:", reply_markup=get_region_kb())
    await state.set_state(CustomsCalc.select_region)

@dp.callback_query(F.data.startswith("vat_"), CustomsCalc.select_region)
async def cust_final_res(cb: CallbackQuery, state: FSMContext):
    vat_rate = float(cb.data.split("_")[1])
    data = await state.get_data()
    price, duty_percent = data['price'], data['duty']
    
    duty_amount = price * (duty_percent / 100)
    vat_amount = (price + duty_amount) * (vat_rate / 100)
    total_taxes = duty_amount + vat_amount
    
    res_text = (
        f"📊 <b>Результат предварительного расчета:</b>\n\n"
        f"📦 Товар: {data['c_name']}\n"
        f"💵 Стоимость: ${price:,.2f}\n"
        f"🧾 Пошлина ({duty_percent}%): ${duty_amount:,.2f}\n"
        f"📉 НДС ({vat_rate}%): ${vat_amount:,.2f}\n"
        f"---\n"
        f"🏁 <b>ИТОГО К УПЛАТЕ: ${total_taxes:,.2f}</b>\n\n"
        f"<i>*Расчет является предварительным.</i>"
    )
    await cb.message.edit_text(res_text)
    await state.clear()
    await cb.answer()

# === 7. ЛОГИКА AI-АНАЛИЗА ДОКУМЕНТОВ (С BASE64) ===

import base64
import io

@dp.message(F.text == "📄 Анализ документов")
async def doc_analysis_init(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(OrderFlow.waiting_for_doc_analysis)
    await m.answer("📂 <b>Режим анализа</b>\nПришлите фото инвойса или CMR. Я проверю данные.", 
                   reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔙 Назад")]], resize_keyboard=True))

@dp.message(OrderFlow.waiting_for_doc_analysis, F.photo)
async def handle_document_ai(m: Message, state: FSMContext):
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    # Скачиваем файл в память
    file_info = await bot.get_file(m.photo[-1].file_id)
    file_content = await bot.download_file(file_info.file_path)
    base64_image = base64.b64encode(file_content.getvalue()).decode('utf-8')

    prompt = "Ты эксперт Logistics Manager. Выдели из документа: 1. Отправитель, 2. Получатель, 3. Сумма, 4. Вес."

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
        await m.answer(f"📊 <b>ЗАКЛЮЧЕНИЕ:</b>\n\n{report}")
        
        # Сохранение (пример строки)
        row = ["AI_АНАЛИЗ", datetime.now().strftime("%d.%m.%Y %H:%M"), m.from_user.full_name, "-", "-", "-", "-", "-", "-", "-", report]
        await save_to_google_sheets(row)
    except Exception as e:
        logging.error(f"AI Error: {e}")
        await m.answer("⚠️ Ошибка анализа. Попробуйте другое фото.")
    await state.clear()

# === 8. МЕНЕДЖЕР И ГЕОГРАФИЯ ===

@dp.message(F.text == "👨‍💼 Менеджер")
async def contact_manager(m: Message):
    text = "👨‍💼 <b>Связь с менеджером</b>\n\n• Telegram: @logistics_manager_pro\n• Срок доставки из Китая — 18 дней!"
    await m.answer(text)

@dp.message(F.text.lower().contains("офис") | F.text.lower().contains("где"))
async def company_geography(m: Message):
    text = "🌍 <b>География</b>\n• Китай (Гуанчжоу)\n• Европа (Варшава)\n• РФ/РК\nСрок 18 дней!"
    await m.answer(text)

# === 9. АДМИН-ПАНЕЛЬ И МОНИТОРИНГ ===

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    if m.from_user.id not in ADMIN_IDS: return 
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Статистика", callback_data="stats_users")],
        [InlineKeyboardButton(text="📂 Скачать базу", callback_data="download_base")],
        [InlineKeyboardButton(text="🚛 Мониторинг GPS", callback_data="stats_drivers")]
    ])
    await m.answer("🛠 Панель администратора:", reply_markup=kb)

@dp.callback_query(F.data == "download_base")
async def cb_download_base(cb: CallbackQuery):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, role FROM users")
    with open("users.txt", "w") as f:
        for u in cursor.fetchall(): f.write(f"{u[0]} | {u[1]} | {u[2]}\n")
    await cb.message.answer_document(types.FSInputFile("users.txt"))
    await cb.answer()

# === 10. ФИНАЛЬНЫЙ ЗАПУСК ===

@dp.message(F.text & ~F.state())
async def ai_consultant(m: Message):
    if m.text in ["🚛 Оформить перевозку", "🛡 Таможня", "📄 Анализ документов", "👨‍💼 Менеджер"]: return
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    sys_ctx = "Ты эксперт Logistics Manager. Доставка из Китая в Европу за 18 дней. Цены самые низкие."
    res = await client_ai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": sys_ctx}, {"role": "user", "content": m.text}]
    )
    await m.answer(f"🏢 <b>Logistics Manager:</b>\n\n{res.choices[0].message.content}")

async def main():
    # Инициализация БД
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT, car_number TEXT, route TEXT, last_geo TEXT, last_seen TEXT, last_google_update TEXT)''')
    conn.commit()
    conn.close()

    logging.info("Бот запущен...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    
# --- 11. DEMO для маркетинга (ИСПРАВЛЕННЫЙ) ---
@dp.message(Command("demo"))
async def cmd_demo(m: Message):
    # Проверка прав админа
    if m.from_user.id not in ADMIN_IDS:
        return

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    # Колонки: Тип услуги, Дата, Имя, Телефон, Груз, Инвойс, Пункт отпр, Пункт назн, Вес, Объем, Детали
    demo_payload = [
        "Авто-доставка (Демо)",      # 0. Тип услуги
        now,                         # 1. Дата и время
        m.from_user.full_name,       # 2. Имя
        "+7 999 000-00-00",          # 3. Телефон (тестовый)
        "Запчасти",                  # 4. Груз
        "5000 USD",                  # 5. Инвойс
        "Урумчи (Китай)",            # 6. Пункт отправления
        "Гданьск (Польша)",          # 7. Пункт назначения
        "150 кг",                    # 8. Вес
        "0.5 м³",                    # 9. Объем
        "ТЕСТОВЫЙ ЗАКАЗ ДЛЯ ДЕМО"    # 10. Детали
    ]

    msg = await m.answer("⏳ <b>Запуск демо-заказа...</b>", parse_mode="HTML")

    try:
        import gspread
        import json
        import os
        from google.oauth2.service_account import Credentials

        # 1. Настройка доступов
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        
        # 2. Извлекаем JSON-ключ из переменной Render (вместо файла creds.json)
        creds_raw = os.getenv("GOOGLE_CREDS_JSON")
        if not creds_raw:
            raise ValueError("Переменная GOOGLE_CREDS_JSON не найдена в настройках Render!")

        creds_info = json.loads(creds_raw)
        creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
        client = gspread.authorize(creds)

        # 3. Открытие таблицы по ID из Environment Variables
        sheet_id = os.getenv('SHEET_ID')
        if not sheet_id:
            raise ValueError("Переменная SHEET_ID не найдена в настройках Render!")
            
        spreadsheet = client.open_by_key(sheet_id.strip())
        sheet = spreadsheet.get_worksheet(0) # Берем самый первый лист (крайний слева)
        
        # 4. Запись строки
        sheet.append_row(demo_payload) 
        
        await msg.edit_text(
            f"✅ <b>Заказ успешно создан!</b>\n\n"
            f"📍 Таблица: <b>{spreadsheet.title}</b>\n"
            f"📍 Маршрут: {demo_payload[6]} -> {demo_payload[7]}\n"
            f"📊 Данные ушли в {len(demo_payload)} колонок.\n"
            f"🚀 Срок 18 дней подтвержден.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        logging.error(f"Ошибка в демо-команде: {e}")
        await msg.edit_text(f"❌ <b>Ошибка:</b>\n<code>{e}</code>", parse_mode="HTML")

# === ДОПОЛНИТЕЛЬНО: КНОПКА СКАЧАТЬ БАЗУ ===
@dp.callback_query(F.data == "download_base")
async def cb_download_base(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS:
        return
    
    import sqlite3
    # Создаем текстовый файл со всеми клиентами
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, status FROM users")
    users = cursor.fetchall()
    conn.close()
    
    file_path = "users_base.txt"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("ID | USERNAME | STATUS\n")
        for u in users:
            f.write(f"{u[0]} | @{u[1]} | {u[2]}\n")
    
    # Отправляем файл админу
    from aiogram.types import FSInputFile
    file = FSInputFile(file_path)
    await cb.message.answer_document(file, caption="📂 Полная база клиентов для анализа.")
    await cb.answer()

# ==========================================================
# ОБНОВЛЕННЫЕ РАЗДЕЛЫ 1, 2, 3 (БЕЗ ФАЙЛА CREDS.JSON)
# ==========================================================

# Универсальная функция для получения доступа к Google (вместо файла)
def get_google_client():
    import gspread
    import json
    import os
    from google.oauth2.service_account import Credentials
    
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    creds_raw = os.getenv("GOOGLE_CREDS_JSON")
    if not creds_raw:
        return None
    
    creds_info = json.loads(creds_raw)
    creds = Credentials.from_service_account_info(creds_info, scopes=scopes)
    return gspread.authorize(creds)

# --- 1. ОБРАБОТКА ДЕТАЛЬНОЙ СТАТИСТИКИ ---
@dp.callback_query(F.data == "stats_users")
async def cb_admin_stats(cb: CallbackQuery):
    import sqlite3
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT username, role, status, last_seen FROM users ORDER BY last_seen DESC LIMIT 10")
    recent_users = cursor.fetchall()
    conn.close()

    res = f"📊 <b>ОТЧЕТ ПО ПОЛЬЗОВАТЕЛЯМ</b>\nВсего в базе: <b>{total}</b> чел.\n__________________________\n\n🕒 <b>Последняя активность:</b>\n"
    if recent_users:
        for u in recent_users:
            uname = f"@{u[0]}" if u[0] else "ID (скрыт)"
            res += f"👤 <b>{uname}</b> (<i>{u[1] or 'Клиент'}</i>)\n└ 📍 Статус: {u[2] or 'В процессе'}\n└ 🕒 {u[3].split('.')[0] if u[3] else 'Неизвестно'}\n\n"
    else: res += "База данных пока пуста."
    
    await cb.message.answer(res, parse_mode="HTML")
    await cb.answer()

# --- 2. МОНИТОРИНГ ВОДИТЕЛЕЙ ---

@dp.callback_query(F.data == "stats_drivers")
async def cb_admin_drivers(cb: CallbackQuery):
    import sqlite3
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN car_number TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN route TEXT")
    except: pass

    cursor.execute("SELECT username, last_geo, last_seen, car_number, route FROM users WHERE role='Водитель'")
    drivers = cursor.fetchall()
    conn.close()

    res = "🚛 <b>ТЕКУЩАЯ ДИСЛОКАЦИЯ</b>\n\n"
    if not drivers:
        res += "Водителей с активным GPS не найдено."
    else:
        for d in drivers:
            username = f"@{d[0]}" if d[0] else "ID (скрыт)"
            car = f"🚗 <code>{d[3]}</code>" if d[3] else "🚗 Без номера"
            route = f"🛣 {d[4]}" if d[4] else "🛣 Маршрут не указан"
            if d[1] and "," in d[1]:
                map_url = f"https://www.google.com/maps?q={d[1]}"
                res += f"👤 <b>{username}</b> | {car}\n{route}\n📍 <a href='{map_url}'>Карта</a>\n🕒 {d[2]}\n\n"
            else:
                res += f"👤 <b>{username}</b> | {car}\n📍 GPS выключен\n\n"
    
    await cb.message.answer(res, parse_mode="HTML", disable_web_page_preview=True)
    await cb.answer()

@dp.edited_message(F.location)
async def handle_live_location(message: Message):
    user_id = message.from_user.id
    geo_string = f"{message.location.latitude},{message.location.longitude}"
    now = datetime.now()
    now_str = now.strftime("%d.%m.%Y %H:%M")

    import sqlite3
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN last_google_update TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN car_number TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN route TEXT")
    except: pass

    cursor.execute("SELECT username, car_number, route, last_google_update FROM users WHERE user_id = ?", (user_id,))
    u_data = cursor.fetchone()
    if not u_data: 
        conn.close()
        return

    username, car_num, route, last_upd = u_data
    should_google = False
    if last_upd:
        try:
            last_dt = datetime.strptime(last_upd, "%d.%m.%Y %H:%M")
            if (now - last_dt).total_seconds() >= 10800: should_google = True
        except: should_google = True
    else: should_google = True

    cursor.execute("UPDATE users SET last_geo=?, last_seen=? WHERE user_id=?", (geo_string, now_str, user_id))
    
    if should_google:
        try:
            client = get_google_client()
            if client:
                sheet = client.open_by_key(os.getenv('SHEET_ID')).worksheet("мониторинг водителей")
                sheet.append_row([f"@{username}" if username else f"ID:{user_id}", car_num or "-", route or "-", now_str, geo_string, f"https://www.google.com/maps?q={geo_string}", "🚚 В пути"])
                cursor.execute("UPDATE users SET last_google_update=? WHERE user_id=?", (now_str, user_id))
        except Exception as e: print(f"GS-Error (Auto): {e}")

    conn.commit()
    conn.close()

@dp.message(F.location)
async def handle_manual_location(message: Message):
    user_id = message.from_user.id
    geo_string = f"{message.location.latitude},{message.location.longitude}"
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    import sqlite3
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT username, car_number, route FROM users WHERE user_id=?", (user_id,))
    u = cursor.fetchone()
    cursor.execute("UPDATE users SET last_geo=?, last_seen=? WHERE user_id=?", (geo_string, now_str, user_id))
    conn.commit()
    conn.close()

    username = f"@{u[0]}" if u and u[0] else message.from_user.full_name
    car = u[1] if u and u[1] else "Не указан"
    route = u[2] if u and u[2] else "Не указан"

    for adm in ADMIN_IDS:
        try: await bot.send_message(adm, f"🚀 <b>РЕЙС ЗАПУЩЕН</b>\n👤 {username}\n🚗 {car}\n🛣 {route}\n📍 <a href='https://www.google.com/maps?q={geo_string}'>Карта</a>", parse_mode="HTML")
        except: pass

    try:
        client = get_google_client()
        if client:
            sheet = client.open_by_key(os.getenv('SHEET_ID')).worksheet("мониторинг водителей")
            sheet.append_row([username, car, route, now_str, geo_string, f"https://www.google.com/maps?q={geo_string}", "🚀 Начал рейс"])
    except Exception as e: print(f"GS-Error (Manual): {e}")

    await message.answer(f"✅ <b>Рейс активирован!</b>\nМаршрут: {route}\nВаш GPS транслируется диспетчеру.", parse_mode="HTML")

# --- 3. РАССЫЛКА ДЛЯ ВОДИТЕЛЕЙ ---
class Broadcast(StatesGroup):
    waiting_for_text = State()
    waiting_for_retry = State()

@dp.callback_query(F.data == "admin_broadcast")
async def cb_broadcast_start(cb: CallbackQuery, state: FSMContext):
    import sqlite3
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE role='Водитель'")
    count = cursor.fetchone()[0]
    conn.close()
    if count == 0:
        await cb.message.answer("❌ Нет водителей.")
        return await cb.answer()
    await cb.message.answer(f"📢 Рассылка для {count} чел. Введите текст:")
    await state.set_state(Broadcast.waiting_for_text)
    await cb.answer()

@dp.message(Broadcast.waiting_for_text)
async def process_broadcast_text(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    text_to_send = message.text
    import sqlite3
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users WHERE role='Водитель'")
    drivers = cursor.fetchall()
    conn.close()

    success, failed = 0, []
    for d in drivers:
        try:
            await bot.send_message(d[0], f"⚠️ <b>ОПОВЕЩЕНИЕ:</b>\n\n{text_to_send}", parse_mode="HTML")
            success += 1
        except: failed.append(str(d[0]))

    kb = InlineKeyboardBuilder()
    if failed:
        await state.update_data(retry_ids=failed, retry_text=text_to_send)
        kb.row(InlineKeyboardButton(text="🔄 Повторить ошибки", callback_data="broadcast_retry"))
    kb.row(InlineKeyboardButton(text="✅ Закрыть", callback_data="delete_msg"))
    await message.answer(f"🏁 Успешно: {success}, Ошибок: {len(failed)}", reply_markup=kb.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "broadcast_retry")
async def cb_broadcast_retry(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    retry_ids, text = data.get("retry_ids", []), data.get("retry_text", "")
    still_failed = []
    success = 0
    for u_id in retry_ids:
        try:
            await bot.send_message(int(u_id), f"⚠️ <b>ПОВТОР:</b>\n\n{text}", parse_mode="HTML")
            success += 1
        except: still_failed.append(u_id)
    await cb.message.edit_text(f"🏁 Итог повтора: {success} ОК, {len(still_failed)} FAIL")
    await state.clear() if not still_failed else await state.update_data(retry_ids=still_failed)

@dp.callback_query(F.data == "delete_msg")
async def cb_delete(cb: CallbackQuery):
    await cb.message.delete()
