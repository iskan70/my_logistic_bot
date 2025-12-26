# -*- coding: utf-8 -*-
import asyncio
import os
import re
import logging
import platform
import sqlite3  # Чтобы не было ошибки NameError: sqlite3
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

def get_country_kb():
    """Создает кнопки выбора страны с плюсами для всех направлений"""
    builder = InlineKeyboardBuilder()
    
    # Полный список стран для вашей логистики
    countries = [
        ("🇨🇳 Китай", "+86"),
        ("🇰🇿 Казахстан", "+7"),
        ("🇷🇺 Россия", "+7"),
        ("🇧🇾 Беларусь", "+375"),
        ("🇺🇿 Узбекистан", "+998"),
        ("🇰🇬 Киргизия", "+996"),
        ("🇹🇯 Таджикистан", "+992"), # Добавил для полноты региона
        ("🇩🇪 Германия", "+49"),
        ("🇵🇱 Польша", "+48"),
        ("🇪🇺 Европа", "+") 
    ]
    
    for name, code in countries:
        # Текст кнопки: "🇨🇳 Китай +86", данные: "country_+86"
        builder.button(text=f"{name} {code}", callback_data=f"country_{code}")
    
    builder.adjust(2) # Кнопки по две в ряд
    return builder.as_markup()

import json
from oauth2client.service_account import ServiceAccountCredentials

async def save_to_google_sheets(row):
    try:
        # 1. Получаем ключи из переменной Render (переименуй creds.json в GOOGLE_CREDS_JSON на Render!)
        creds_json = os.getenv("GOOGLE_CREDS_JSON")
        if not creds_json:
            print(">>> ОШИБКА: На Render не настроена переменная GOOGLE_CREDS_JSON")
            return False

        # 2. Авторизация
        info = json.loads(creds_json)
        scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
        creds = ServiceAccountCredentials.from_json_keyfile_dict(info, scope)
        client = gspread.authorize(creds)

        # 3. Открываем таблицу по ID из настроек Render
        sheet_id = os.getenv("SHEET_ID")
        sheet = client.open_by_key(sheet_id).get_worksheet(0)

        # 4. Запись данных
        sheet.append_row(row)
        print(">>> УСПЕХ: Данные в таблице!")
        return True
    except Exception as e:
        print(f">>> ОШИБКА GOOGLE: {e}")
        return False
# === 1. ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ ===
load_dotenv()

if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=os.getenv("BOT_TOKEN"), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
client_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
SHEET_ID = os.getenv("SHEET_ID")
ADMIN_IDS = [494255577]

# --- 1.1 ФУНКЦИЯ ДЛЯ GOOGLE ТАБЛИЦ ---
async def save_to_google_sheets(row_data: list):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        key_lines = [
            "-----BEGIN PRIVATE KEY-----",
            "MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQCod+3adi2TAque",
            "y1SCyV6iQ/m7/NIhZWKjw+DWqVt5ktr1p0ldtxS/plFZkURAY9vi5+s5JDJ2QfJn",
            "TmM0IONnBLS7y0/R7BNDW/yUNJ7dMNoG1JBs9TcZN52jk/Ljsc85R/eEPas7EAiC",
            "KVzRX+WJKQCuXzXw5BmEL24JWLolenOOwBRS4B+p9DipSTn8pWQCNqeYaKBKX8Eh",
            "NZQANhfHdHCvvDN0+9+HYNivTY378aTrDtMh8LQ7SnmqFcCt0dO8xAUBciou5KwI",
            "6otF2NtLdzg7btUspeCj2ZSon+VG6yNG7d9uG/2HdyZY3KLzgrsHHHaqNnXHvQuS",
            "IwxcOPItAgMBAAECggEAEducn/K1BAddb9i33aFA4cx41X+IOrgHi7qAw+Bx7OIv",
            "Sajw8vksPuB/cRIf/P9Y2KWi3ozCuJxm+KJri6QM1ue9zMZRcLwokpRWotMtH99E",
            "zUKNCK+5pnepwyQ0tAQuJjFFwIPU+c7KSBngV+VlbHOnOdSn4CAdwFBSxrTcDorO",
            "9aMLNif23c3buFuD0QZGKc1kQHY6Eow8P+a/GUTom8h0Cmdt4cgJEUgaFc16uF31",
            "i8fABxlcLGHnrd4hD/guDlcBZaVkzwHjKEyO7Up9psRnVWf31dC/XlM/CS2uSMoY",
            "iUvSE8/eONv+7PuzUZC/MKkydIjjLGWvbToYQvZ2AQKBgQDtmeUAH9NBtxvFXWZ9",
            "EAeZzHhXZb5iedZBRVR9nzFeiIUtfrjIx6CVtoLKMoF7o+8nxE7PJhY9i/HVD2NU",
            "XUud2VoB62vdOoOJs495eGdLozMpItgs7fwVK+Kbhjee84laE2ME1Sc/oL2e51OK",
            "UPbEIUreWFiXmQfxO+aHlv7NgQKBgQC1g5PQ1FaRKSxW6eRHf7hQthKy44eLpqnm",
            "ANcjRquM0MlQIZexPO3Pro2sGa9+SM1j/fuJPZSMXMxjUE2Q50U+A1x19jGL1/Et",
            "KRvc2p7jqrIqE9xLrzhJ5liTofUGvcrCxmDUpCka4o12wEMITOdmPqafg/h8E3rs",
            "Kp+EjBUSrQKBgQDKyiGQlJkbKmxSbCAwN4E1PDWt6lGu/Ovn84NkYH2jgIOiS9js",
            "zKz7erVwW+D1pPpWh4738DrlNs8lmKefdq02QS84GjWKsQlZet7GvwPyo4zj3DCD",
            "UG9ppnYXZVuNl7AwKAHIOyDvhoKw4CEGGYoz5XJgCSk74knME+Ly8OXygQKBgQCy",
            "42sxm6N5Sre9LKPjZ1dyjA6fqSg0FNxKprdgt8xoaniM9Z53edHyJVjQrTvM3Nk3",
            "W9+j4UHel7KDimf3kEYomM1uIGWyKe8yD9q67ec7/0W5vHsXSCfUhST00uAWdcQ3",
            "86UIzIUKTw8WYuNtccV4efRjL4AcYGJ8EIHH8vrtvQKBgFM6e3qWhokNRvekC43V",
            "UjeO6upUlAynHRXWutsgeYsmqdPKhzQxtEhhZ3gYDctXax1Jj7waH9j4OuKnfTT6",
            "Zn7jj36YZFy4vC8N5PadsvUe9k2StW5chzIcZ9OFJxL7FtVhfpVc9tyWYikoD+uW",
            "2T9nyXdgK5AaJOwH0DBPNoYO",
            "-----END PRIVATE KEY-----"
        ]
        formatted_key = "\n".join(key_lines)
        service_account_info = {
            "type": "service_account",
            "project_id": "telegram-bots-482313",
            "private_key_id": "e1a4584e90d891fcd020d4ce2216b96a00ed8a8a",
            "private_key": formatted_key,
            "client_email": "logistic-bot-manager@telegram-bots-482313.iam.gserviceaccount.com",
            "client_id": "108953038561525298418",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/logistic-bot-manager%40telegram-bots-482313.iam.gserviceaccount.com"
        }
        creds = ServiceAccountCredentials.from_json_keyfile_dict(service_account_info, scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SHEET_ID.strip()).get_worksheet(0)
        sheet.append_row(row_data)
        print("✅ УСПЕХ: Запись в таблице!")
        return True
    except Exception as e:
        import traceback
        print(f"❌ ОШИБКА ТАБЛИЦЫ: {e}")
        print(traceback.format_exc())
        return False

# --- 1.2 ЛОКАЛЬНАЯ БАЗА ДАННЫХ ---
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


# === 2. МАШИНА СОСТОЯНИЙ (FSM) - ИСПРАВЛЕННАЯ ===

class OrderFlow(StatesGroup):
    """Состояния для оформления перевозки и анализа документов"""
    fio = State()                 # Шаг 1
    phone = State()               # Шаг 2
    cargo_type = State()          # Что везем
    cargo_value = State()         # Стоимость груза
    origin = State()              # Город отправления
    destination = State()         # Город назначения
    weight = State()              # Вес
    volume = State()              # Объем
    waiting_for_weight = State()
    waiting_for_doc_analysis = State() # ТЕПЕРЬ СТРОКА 521 БУДЕТ РАБОТАТЬ
    confirm_data = State()        # Подтверждение данных

class Broadcast(StatesGroup):
    """Состояния для рассылки водителям"""
    waiting_for_text = State()

class AdminPanel(StatesGroup):
    """Состояния для админ-панели"""
    broadcast_message = State()

class CustomsCalc(StatesGroup):
    """Состояния для таможенного калькулятора"""
    cargo_name = State()          # Шаг 1: Название товара
    select_duty = State()         # Шаг 2: Выбор или ввод ставки
    manual_duty = State()         # Подшаг: Ручной ввод %
    cargo_price = State()         # Шаг 3: Ввод цены
    select_region = State()       # Шаг 4: Выбор НДС (РФ/РК)
    val_input = State()
    duty_input = State()

# Вспомогательные состояния для ролей
class RoleSelection(StatesGroup):
    selecting_role = State()
    selecting_transport = State()

# === 3. ГЕНЕРАТОРЫ КЛАВИАТУР ===
def get_main_kb(user_id: int):
    # Проверяем роль в базе данных
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    
    # СТРАХОВКА: Создаем таблицу, если Render её удалил после деплоя
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, role TEXT DEFAULT 'Клиент')")
    
    cursor.execute("SELECT role FROM users WHERE user_id=?", (user_id,))
    row = cursor.fetchone()
    role = row[0] if row else "Клиент"
    conn.close()

    btns = [
        [KeyboardButton(text="🚛 Оформить перевозку"), KeyboardButton(text="🛡 Таможня")],
        [KeyboardButton(text="📄 Анализ документов"), KeyboardButton(text="👨‍💼 Менеджер")]
    ]
    
    # Кнопку видят админы и Водители
    if user_id in ADMIN_IDS or role == "Водитель":
        # Важно: request_location=True позволяет одним нажатием отправить координаты
        btns.append([KeyboardButton(text="🚀 Начать рейс (Включить GPS)", request_location=True)])
        
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

# === 4. ОБРАБОТКА КОМАНДЫ /START ===

@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    """
    Запуск бота. 
    """
    await state.clear()
    
    # Регистрация пользователя в базе
    update_user_db(m.from_user.id, m.from_user.username, status="В главном меню")
    
    # Текст приветствия
    welcome_text = (
        f"🤝 Здравствуйте, {m.from_user.first_name}!\n\n"
        f"Вас приветствует логист компании Logistics Manager.\n\n"
        f"Я помогу вам:\n"
        f"• Оформить заказ\n"
        f"• Рассчитать стоимость международной доставки\n"
        f"• Проверить коммерческие документы (AI-анализ)\n"
        f"• Оценить таможенные пошлины и налоги\n\n"
        f"Воспользуйтесь меню ниже для начала работы 👇"
    )
    
    # Важно: передаем m.from_user.id в генератор клавиатуры!
    await m.answer(welcome_text, reply_markup=get_main_kb(m.from_user.id))

# === 4.1 СЕКРЕТНАЯ РЕГИСТРАЦИЯ ВОДИТЕЛЯ ===
@dp.message(Command("driver_2025")) # Команда, которую вы дадите водителю
async def cmd_driver_reg(m: Message):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    # Устанавливаем роль 'Водитель'
    cursor.execute("UPDATE users SET role='Водитель' WHERE user_id=?", (m.from_user.id,))
    conn.commit()
    conn.close()
    
    await m.answer(
        "✅ <b>Доступ водителя активирован!</b>\n"
        "Теперь в вашем меню появилась кнопка включения GPS.",
        reply_markup=get_main_kb(m.from_user.id), # Сразу обновляем меню
        parse_mode="HTML"
    )

# === 5. ЛОГИКА ОФОРМЛЕНИЯ ПЕРЕВОЗКИ ===

@dp.message(F.text == "🚛 Оформить перевозку")
async def order_init(m: Message, state: FSMContext):
    """Шаг 1: ФИО"""
    await state.clear()
    await state.set_state(OrderFlow.fio)
    await m.answer("👤 Введите ваше <b>ФИО:</b>", reply_markup=ReplyKeyboardRemove())

@dp.message(OrderFlow.fio)
async def order_fio(m: Message, state: FSMContext):
    """Шаг 2: Телефон (выбор страны)"""
    await state.update_data(fio=m.text)
    await state.set_state(OrderFlow.phone)
    await m.answer("📱 Выберите код страны или введите номер полностью (+...):", reply_markup=get_country_kb())
    
@dp.callback_query(F.data.startswith("country_"))
async def cb_country_select(cb: CallbackQuery, state: FSMContext):
    """Обработка выбора страны из инлайн-кнопок"""
    country_code = cb.data.split("_")[1]
    digits_map = {"+86": 11, "+7": 10, "+375": 9, "+998": 9, "+996": 9, "+49": 11, "+48": 9}
    needed = digits_map.get(country_code, 10)
    
    await state.update_data(temp_code=country_code, needed_digits=needed)
    await cb.answer()
    await cb.message.answer(
        f"✅ Выбрана страна с кодом <b>{country_code}</b>\n"
        f"Введите оставшиеся <b>{needed}</b> цифр номера (без кода страны):",
        parse_mode="HTML"
    )

@dp.message(OrderFlow.phone)
async def order_phone(m: Message, state: FSMContext):
    """Шаг 3: Валидация телефона"""
    digits_map = {"+7": 10, "+48": 9, "+90": 10, "+86": 11, "+998": 9, "+375": 9, "+996": 9}
    text = m.text.strip() if m.text else ""

    # Если ввели руками с кодом в скобках (на всякий случай)
    if "(" in text and "+" in text:
        code = re.search(r'\+\d+', text).group()
        needed = digits_map.get(code, 10)
        await state.update_data(temp_code=code, needed_digits=needed)
        return await m.answer(f"Вы выбрали {code}. Введите ровно <b>{needed}</b> цифр номера:")

    data = await state.get_data()
    temp_code = data.get("temp_code")
    needed_digits = data.get("needed_digits")

    if temp_code and needed_digits:
        clean_input = re.sub(r'\D', '', text)
        if len(clean_input) == needed_digits:
            phone = temp_code + clean_input
            await state.update_data(phone=phone, temp_code=None, needed_digits=None)
        else:
            return await m.answer(f"⚠️ Нужно ровно <b>{needed_digits}</b> цифр. Вы ввели {len(clean_input)}. Попробуйте снова:")
    elif text.startswith('+'):
        if re.match(r'^\+\d{10,15}$', text):
            await state.update_data(phone=text)
        else:
            return await m.answer("⚠️ Введите корректный номер (+ и 10-15 цифр):")
    else:
        return await m.answer("Выберите страну или введите номер через +", reply_markup=get_country_kb())

    await state.set_state(OrderFlow.cargo_type)
    await m.answer("📦 <b>Что везем?</b> (Наименование груза):", reply_markup=ReplyKeyboardRemove())

@dp.message(OrderFlow.cargo_type)
async def order_type(m: Message, state: FSMContext):
    """Шаг 4: Тип груза"""
    await state.update_data(cargo_type=m.text)
    await state.set_state(OrderFlow.cargo_value)
    await m.answer("💰 Укажите <b>инвойсную стоимость</b> груза (USD):")

@dp.message(OrderFlow.cargo_value)
async def order_value(m: Message, state: FSMContext):
    """Шаг 5: Стоимость"""
    await state.update_data(cargo_value=m.text)
    await state.set_state(OrderFlow.origin)
    await m.answer("📍 <b>Пункт отправления:</b>")

@dp.message(OrderFlow.origin)
async def order_org(m: Message, state: FSMContext):
    """Шаг 6: Откуда"""
    await state.update_data(org=m.text)
    await state.set_state(OrderFlow.destination)
    await m.answer("🏁 <b>Пункт назначения:</b>")

@dp.message(OrderFlow.destination)
async def order_dst(m: Message, state: FSMContext):
    """Шаг 7: Куда"""
    await state.update_data(dst=m.text)
    await state.set_state(OrderFlow.weight)
    await m.answer("⚖️ Общий <b>вес</b> (кг):")

@dp.message(OrderFlow.weight)
async def order_weight(m: Message, state: FSMContext):
    """Шаг 8: Вес"""
    await state.update_data(weight=m.text)
    await state.set_state(OrderFlow.volume)
    await m.answer("📐 Общий <b>объем</b> (куб. метры):")

@dp.message(OrderFlow.volume)
async def order_finish(m: Message, state: FSMContext):
    """Шаг 9: Финальная запись в таблицу (11 колонок)"""
    await state.update_data(volume=m.text)
    d = await state.get_data()
    
    # Визуальный отклик в боте
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    await m.answer("⏳ Сохраняю вашу заявку...")
    
    # Формируем список строго под вашу шапку (11 колонок)
    row = [
        "ЗАКАЗ",                                     # Тип услуги
        datetime.now().strftime("%d.%m.%Y %H:%M"),   # Дата и время
        d.get('fio', '-'),                           # Имя
        d.get('phone', '-'),                         # Телефон
        d.get('cargo_type', '-'),                   # Груз
        d.get('cargo_value', '-'),                  # Инвойс
        d.get('org', '-'),                           # Откуда
        d.get('dst', '-'),                           # Куда
        d.get('weight', '-'),                        # Вес
        d.get('volume', '-'),                        # Объем
        "-"                                          # Детали
    ]
    
    # Пытаемся сохранить (если функция save_to_google_sheets асинхронная)
    try:
        success = await save_to_google_sheets(row)
    except Exception:
        success = False
    
    if success:
        await m.answer(
            "🚀 <b>Заявка принята!</b>\n\nНаши специалисты свяжутся с вами в ближайшее время.", 
            reply_markup=get_main_kb(m.from_user.id)
        )
    else:
        # План Б: если таблица недоступна
        await m.answer(
            "✅ Заявка сохранена в системе! Специалист свяжется с вами.", 
            reply_markup=get_main_kb(m.from_user.id)
        )
    
    await state.clear()

# === 6. ЛОГИКА ТАМОЖЕННОГО КАЛЬКУЛЯТОРА ===

@dp.message(F.text == "🛡 Таможня")
async def cust_init(m: Message, state: FSMContext):
    """Шаг 1: Запрос названия товара"""
    await state.clear()
    await state.set_state(CustomsCalc.cargo_name)
    await m.answer("🔍 Введите <b>наименование товара</b> для предварительного анализа:")

@dp.message(CustomsCalc.cargo_name)
async def cust_cargo_ai(m: Message, state: FSMContext):
    """Шаг 2: AI-подсказка кода и выбор категории ставки"""
    await state.update_data(c_name=m.text)
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    # AI помогает определить примерный код ТН ВЭД
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

    # Клавиатура с фиксированными ставками по категориям
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💻 Электроника (5%)", callback_data="setduty_5")],
        [InlineKeyboardButton(text="🚗 Автозапчасти (8%)", callback_data="setduty_8")],
        [InlineKeyboardButton(text="👕 Одежда (12%)", callback_data="setduty_12")],
        [InlineKeyboardButton(text="⚙️ Оборудование (3%)", callback_data="setduty_3")],
        [InlineKeyboardButton(text="✏️ Ввести свой %", callback_data="setduty_manual")]
    ])
    
    await m.answer(
        f"📋 <b>Анализ товара:</b>\n{ai_tip}\n\nВыберите <b>ставку пошлины</b> или введите свою:", 
        reply_markup=kb
    )
    await state.set_state(CustomsCalc.select_duty)

@dp.callback_query(F.data == "setduty_manual", CustomsCalc.select_duty)
async def cust_manual(cb: CallbackQuery, state: FSMContext):
    """Ручной ввод процента пошлины"""
    await state.set_state(CustomsCalc.manual_duty)
    await cb.message.answer("📝 Введите % пошлины (только число):")
    await cb.answer()

@dp.callback_query(F.data.startswith("setduty_"), CustomsCalc.select_duty)
async def cust_set_preset(cb: CallbackQuery, state: FSMContext):
    """Выбор готовой ставки"""
    rate = float(cb.data.split("_")[1])
    await state.update_data(duty=rate)
    await cb.message.answer(f"✅ Ставка {rate}% выбрана.\n💰 Введите <b>стоимость груза (USD):</b>")
    await state.set_state(CustomsCalc.cargo_price)
    await cb.answer()

@dp.message(CustomsCalc.manual_duty)
async def cust_manual_val(m: Message, state: FSMContext):
    """Валидация ручного ввода процента"""
    val = m.text.replace(",", ".").strip()
    if not re.match(r'^\d+(\.\d+)?$', val):
        return await m.answer("⚠️ Введите корректное число!")
    await state.update_data(duty=float(val))
    await m.answer(f"✅ Ставка {val}% принята.\n💰 Введите <b>стоимость груза (USD):</b>")
    await state.set_state(CustomsCalc.cargo_price)

@dp.message(CustomsCalc.cargo_price)
async def cust_price(m: Message, state: FSMContext):
    """Шаг 3: Ввод стоимости и выбор региона НДС"""
    val = m.text.replace(",", ".").strip()
    if not re.match(r'^\d+(\.\d+)?$', val):
        return await m.answer("⚠️ Введите корректное число стоимости!")
    
    await state.update_data(price=float(val))
    await m.answer("🌍 Выберите <b>регион назначения</b> для расчета НДС:", reply_markup=get_region_kb())
    await state.set_state(CustomsCalc.select_region)

@dp.callback_query(F.data.startswith("vat_"), CustomsCalc.select_region)
async def cust_final_res(cb: CallbackQuery, state: FSMContext):
    """Шаг 4: Финальный расчет и вывод"""
    vat_rate = float(cb.data.split("_")[1])
    data = await state.get_data()
    
    price = data['price']
    duty_percent = data['duty']
    
    # Формула: Пошлина = Стоимость * %; НДС = (Стоимость + Пошлина) * %
    duty_amount = price * (duty_percent / 100)
    vat_amount = (price + duty_amount) * (vat_rate / 100)
    total_taxes = duty_amount + vat_amount
    
    res_text = (
        f"📊 <b>Результат предварительного расчета:</b>\n\n"
        f"📦 Товар: {data['c_name']}\n"
        f"💵 Стоимость: ${price:,.2f}\n"
        f"🧾 Пошлина ({duty_percent}%): ${duty_amount:,.2f}\n"
        f"📉 НДС ({vat_rate}%): ${vat_amount:,.2f}\n"
        f"---"
        f"🏁 <b>ИТОГО К УПЛАТЕ: ${total_taxes:,.2f}</b>\n\n"
        f"<i>*Расчет является предварительным. Для точных условий с вами свяжется наш специалист.</i>"
    )
    
    await cb.message.edit_text(res_text)
    # Запись расчета в таблицу (опционально, для истории)
    await state.clear()
    await cb.answer()

# === 7. ЛОГИКА AI-АНАЛИЗА ДОКУМЕНТОВ (ЕДИНОЕ РЕЗЮМЕ) ===

@dp.message(F.text == "📄 Анализ документов")
async def doc_analysis_init(m: Message, state: FSMContext):
    """Вход в режим анализа документов"""
    await state.clear()
    await state.set_state(OrderFlow.waiting_for_doc_analysis)
    
    kb = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🔙 Назад")]], 
        resize_keyboard=True
    )
    
    await m.answer(
        "📂 <b>Режим пакетного анализа документов</b>\n\n"
        "Пришлите одно или несколько фото (инвойс, CMR, упаковочный).\n"
        "Бот подождет несколько секунд, пока вы загрузите все страницы, и выдаст <b>единое резюме</b>.\n\n"
        "<i>Я проверю: отправителя, получателя, общую стоимость и веса.</i>", 
        reply_markup=kb
    )

@dp.message(OrderFlow.waiting_for_doc_analysis, F.photo | F.document)
async def handle_document_ai(m: Message, state: FSMContext):
    """Сбор файлов и финальный анализ через Vision AI"""
    data = await state.get_data()
    file_list = data.get("temp_files", [])

    # Получаем ссылку на файл для OpenAI (используем API токен напрямую)
    file_id = m.photo[-1].file_id if m.photo else m.document.file_id
    file_info = await bot.get_file(file_id)
    bot_token = os.getenv("BOT_TOKEN")
    file_url = f"https://api.telegram.org/file/bot{bot_token}/{file_info.file_path}"
    
    # Формируем структуру для Vision API
    file_list.append({"type": "image_url", "image_url": {"url": file_url}})
    await state.update_data(temp_files=file_list)

    # Умная пауза 6 секунд: ждем, пока пользователь отправит все фото
    await asyncio.sleep(6)
    
    # Проверка: если за время паузы добавились новые файлы, текущий запуск прерывается
    current_data = await state.get_data()
    if len(current_data.get("temp_files", [])) > len(file_list):
        return 

    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    prompt = (
        "Ты — эксперт компании Logistics Manager. "
        "Проанализируй эти документы и дай единое краткое резюме: "
        "1. Отправитель, 2. Получатель, 3. Общая стоимость (с валютой), 4. Вес. "
        "Если данные противоречат друг другу, укажи на это. Отвечай на русском языке."
    )

    try:
        response = await client_ai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}] + file_list
                }
            ],
            max_tokens=1000
        )

        final_report = response.choices[0].message.content
        
        await m.answer(
            f"📊 <b>ЗАКЛЮЧЕНИЕ ЭКСПЕРТА:</b>\n\n{final_report}\n\n"
            f"<i>Данные сохранены в таблицу для менеджера.</i>",
            reply_markup=get_main_kb()
        )

        # ФОРМИРУЕМ СТРОКУ (11 колонок под вашу шапку)
        # В этой версии заполняем Тип, Дату, Имя и Детали (отчет ИИ)
        row = [
            "AI_АНАЛИЗ",                                # Тип услуги
            datetime.now().strftime("%d.%m.%Y %H:%M"),  # Дата и время
            m.from_user.full_name,                      # Имя
            "-",                                        # Телефон (необязательно)
            "-",                                        # Груз
            "-",                                        # Инвойс
            "-",                                        # Пункт отправления
            "-",                                        # Пункт назначения
            "-",                                        # Вес
            "-",                                        # Объем
            final_report                                # ДЕТАЛИ (Отчет ИИ здесь)
        ]
        
        await save_to_google_sheets(row)

    except Exception as e:
        logging.error(f"AI Error: {e}")
        await m.answer("⚠️ Не удалось проанализировать документы. Попробуйте сделать более четкое фото.")
    
    await state.clear()

# === 8. МЕНЕДЖЕР И ГЕОГРАФИЯ ===

@dp.message(F.text == "👨‍💼 Менеджер")
async def contact_manager(m: Message):
    """Прямая связь с оператором"""
    text = (
        "👨‍💼 <b>Связь с менеджером</b>\n\n"
        "Наши специалисты готовы ответить на ваши вопросы прямо сейчас.\n\n"
        "• <b>Telegram:</b> @logistics_manager_pro\n"
        "• <b>WhatsApp:</b> +7XXXXXXXXXX\n"
        "• <b>График:</b> Пн-Пт, 09:00 - 18:00\n\n"
        "Напишите менеджеру напрямую или дождитесь звонка по вашей заявке."
    )
    await m.answer(text, reply_markup=get_main_kb())

# Можно добавить обработку локаций, если пользователь спрашивает про офисы
@dp.message(F.text.lower().contains("где") | F.text.lower().contains("офис"))
async def company_geography(m: Message):
    """Информация о географии присутствия"""
    text = (
        "🌍 <b>География Logistics Manager</b>\n\n"
        "Мы обеспечиваем логистику по направлениям:\n"
        "• 🇨🇳 <b>Китай:</b> Склады в Гуанчжоу и Иу\n"
        "• 🇰🇿 <b>Казахстан:</b> Алматы, Астана\n"
        "• 🇷🇺 <b>Россия:</b> Москва, Екатеринбург\n"
        "• 🇪🇺 <b>Европа:</b> Склад консолидации в Польше (Варшава)\n"
        "• 🇹🇷 <b>Турция:</b> Стамбул\n\n"
        "Срок доставки из Китая — 18 дней!"
    )
    await m.answer(text, reply_markup=get_main_kb())

# === 9. ОБНОВЛЕННАЯ АДМИН-ПАНЕЛЬ (СТАТИСТИКА, ВОДИТЕЛИ, РАССЫЛКА) ===

ADMIN_IDS = [494255577]  # Ваш ID

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    """Вход в админку"""
    if m.from_user.id not in ADMIN_IDS:
        return 

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Детальная статистика", callback_data="stats_users")],
        [InlineKeyboardButton(text="📂 Скачать базу клиентов", callback_data="download_base")], # ДОБАВИЛИ ЭТУ СТРОКУ
        [InlineKeyboardButton(text="🚛 Мониторинг водителей", callback_data="stats_drivers")],
        [InlineKeyboardButton(text="📢 Рассылка водителям", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔗 Открыть Google Таблицу", url=f"https://docs.google.com/spreadsheets/d/{os.getenv('SHEET_ID')}")]
    ])
    
    await m.answer("🛠 <b>Панель администратора</b>\nВыберите нужный раздел:", reply_markup=kb, parse_mode="HTML")

# --- 1. ОБРАБОТКА ДЕТАЛЬНОЙ СТАТИСТИКИ ---
@dp.callback_query(F.data == "stats_users")
async def cb_admin_stats(cb: CallbackQuery):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    
    # Считаем общее количество уникальных пользователей
    cursor.execute("SELECT COUNT(*) FROM users")
    total = cursor.fetchone()[0]
    
    # Берем 10 последних активных пользователей (даже без /start, если они попали в базу при заказе)
    cursor.execute('''
        SELECT username, role, status, last_seen 
        FROM users 
        ORDER BY last_seen DESC 
        LIMIT 10
    ''')
    recent_users = cursor.fetchall()
    conn.close()

    res = f"📊 <b>ОТЧЕТ ПО ПОЛЬЗОВАТЕЛЯМ</b>\n"
    res += f"Всего в базе: <b>{total}</b> чел.\n"
    res += f"__________________________\n\n"
    res += f"🕒 <b>Последняя активность:</b>\n"
    
    if recent_users:
        for u in recent_users:
            uname = f"@{u[0]}" if u[0] else "ID (скрыт)"
            role = u[1] if u[1] else "Клиент"
            status = u[2] if u[2] else "В процессе"
            # Форматируем время для читаемости
            time = u[3].split('.')[0] if u[3] else "Неизвестно"
            
            res += f"👤 <b>{uname}</b> (<i>{role}</i>)\n"
            res += f"└ 📍 Статус: {status}\n"
            res += f"└ 🕒 {time}\n\n"
    else:
        res += "База данных пока пуста."
    
    await cb.message.answer(res, parse_mode="HTML")
    await cb.answer()

# --- 2. МОНИТОРИНГ ВОДИТЕЛЕЙ ---

# Обработчик нажатия кнопки "СТАТИСТИКА ВОДИТЕЛЕЙ" в админ-панели
@dp.callback_query(F.data == "stats_drivers")
async def cb_admin_drivers(cb: CallbackQuery):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    # Проверяем и создаем колонки, если их нет
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
                res += f"👤 <b>{username}</b> | {car}\n{route}\n"
                res += f"📍 <a href='{map_url}'>Посмотреть на карте</a>\n"
                res += f"🕒 Обновлено: {d[2]}\n\n"
            else:
                res += f"👤 <b>{username}</b> | {car}\n📍 GPS выключен\n\n"
    
    await cb.message.answer(res, parse_mode="HTML", disable_web_page_preview=True)
    await cb.answer()

# АВТОМАТИЧЕСКОЕ ОБНОВЛЕНИЕ (когда водитель просто едет с включенным Live Location)
@dp.edited_message(F.location)
async def handle_live_location(message: Message):
    user_id = message.from_user.id
    lat, lon = message.location.latitude, message.location.longitude
    geo_string = f"{lat},{lon}"
    now = datetime.now()
    now_str = now.strftime("%d.%m.%Y %H:%M")

    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    # Авто-создание колонок
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
    
    # Проверка таймера: 3 часа (10800 сек)
    should_google = False
    if last_upd:
        try:
            last_dt = datetime.strptime(last_upd, "%d.%m.%Y %H:%M")
            if (now - last_dt).total_seconds() >= 10800: should_google = True
        except: should_google = True
    else: should_google = True

    # Обновляем SQLite (всегда сохраняем последнюю точку)
    cursor.execute("UPDATE users SET last_geo=?, last_seen=? WHERE user_id=?", (geo_string, now_str, user_id))
    
    # Пишем в Google Sheets (раз в 3 часа)
    if should_google:
        try:
            import gspread
            from google.oauth2.service_account import Credentials
            creds = Credentials.from_service_account_file("creds.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])
            client = gspread.authorize(creds)
            sheet = client.open_by_key(os.getenv('SHEET_ID')).worksheet("мониторинг водителей")
            
            map_url = f"https://www.google.com/maps?q={geo_string}"
            sheet.append_row([
                f"@{username}" if username else f"ID:{user_id}", 
                car_num or "-", 
                route or "-", 
                now_str, 
                geo_string, 
                map_url, 
                "🚚 В пути"
            ])
            
            cursor.execute("UPDATE users SET last_google_update=? WHERE user_id=?", (now_str, user_id))
        except Exception as e: 
            print(f"GS-Error (Auto): {e}")

    conn.commit()
    conn.close()

# РУЧНОЕ НАЧАЛО РЕЙСА (когда водитель отправляет геопозицию кнопкой)
@dp.message(F.location)
async def handle_manual_location(message: Message):
    user_id = message.from_user.id
    geo_string = f"{message.location.latitude},{message.location.longitude}"
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")
    map_url = f"https://www.google.com/maps?q={geo_string}"

    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    # Гарантируем наличие колонок в базе
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN car_number TEXT")
        cursor.execute("ALTER TABLE users ADD COLUMN route TEXT")
    except: pass

    cursor.execute("SELECT username, car_number, route FROM users WHERE user_id=?", (user_id,))
    u = cursor.fetchone()
    
    # Обновляем локальную базу
    cursor.execute("UPDATE users SET last_geo=?, last_seen=? WHERE user_id=?", (geo_string, now_str, user_id))
    conn.commit()
    conn.close()

    username = f"@{u[0]}" if u and u[0] else message.from_user.full_name
    car = u[1] if u and u[1] else "Не указан"
    route = u[2] if u and u[2] else "Не указан"

    # Уведомление всем администраторам
    for adm in ADMIN_IDS:
        try: 
            await bot.send_message(adm, f"🚀 <b>РЕЙС ЗАПУЩЕН</b>\n👤 {username}\n🚗 {car}\n🛣 {route}\n📍 <a href='{map_url}'>Посмотреть карту</a>", parse_mode="HTML")
        except: pass

    # Запись в Google Таблицу (статус "Начал рейс")
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        creds = Credentials.from_service_account_file("creds.json", scopes=["https://www.googleapis.com/auth/spreadsheets"])
        client = gspread.authorize(creds)
        sheet = client.open_by_key(os.getenv('SHEET_ID')).worksheet("мониторинг водителей")
        sheet.append_row([username, car, route, now_str, geo_string, map_url, "🚀 Начал рейс"])
    except Exception as e: 
        print(f"GS-Error (Manual): {e}")

    await message.answer(f"✅ <b>Рейс активирован!</b>\nМаршрут: {route}\nВаш GPS транслируется диспетчеру.\n\n(Для автоматического обновления не забудьте включить 'Транслировать мою геопозицию')", parse_mode="HTML")

# --- 3. РАССЫЛКА ДЛЯ ВОДИТЕЛЕЙ С ПОВТОРОМ ---

class Broadcast(StatesGroup):
    waiting_for_text = State()
    waiting_for_retry = State()

@dp.callback_query(F.data == "admin_broadcast")
async def cb_broadcast_start(cb: CallbackQuery, state: FSMContext):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE role='Водитель'")
    count = cursor.fetchone()[0]
    conn.close()

    if count == 0:
        await cb.message.answer("❌ <b>В базе нет водителей.</b>\nИспользуйте <code>/driver_2025</code> для регистрации.")
        return await cb.answer()

    await cb.message.answer(f"📢 <b>Рассылка для {count} водителей.</b>\nВведите текст сообщения:")
    await state.set_state(Broadcast.waiting_for_text)
    await cb.answer()

@dp.message(Broadcast.waiting_for_text)
async def process_broadcast_text(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS: return
    
    text_to_send = message.text
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username FROM users WHERE role='Водитель'")
    drivers = cursor.fetchall()
    conn.close()

    success = 0
    failed_ids = []

    status_msg = await message.answer("🚀 <i>Рассылаю...</i>")

    for d_id, d_name in drivers:
        try:
            full_text = f"⚠️ <b>ОПОВЕЩЕНИЕ ЛОГИСТА:</b>\n\n{text_to_send}"
            await bot.send_message(d_id, full_text, parse_mode="HTML")
            success += 1
        except Exception:
            failed_ids.append(str(d_id))

    kb = InlineKeyboardBuilder()
    if failed_ids:
        await state.update_data(retry_ids=failed_ids, retry_text=text_to_send)
        kb.row(InlineKeyboardButton(text="🔄 Повторить для недошедших", callback_data="broadcast_retry"))
    
    kb.row(InlineKeyboardButton(text="✅ Закрыть", callback_data="delete_msg"))

    res_text = (
        f"🏁 <b>Результаты рассылки:</b>\n"
        f"✅ Доставлено: <b>{success}</b>\n"
        f"❌ Ошибки (не в сети): <b>{len(failed_ids)}</b>"
    )
    
    await status_msg.edit_text(res_text, reply_markup=kb.as_markup(), parse_mode="HTML")
    if not failed_ids:
        await state.clear()

@dp.callback_query(F.data == "broadcast_retry")
async def cb_broadcast_retry(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    retry_ids = data.get("retry_ids", [])
    text = data.get("retry_text", "")

    if not retry_ids:
        return await cb.answer("Больше некому переотправлять.")

    await cb.message.edit_text(f"🔄 <i>Повторная попытка для {len(retry_ids)} чел...</i>")
    
    still_failed = []
    success = 0

    for u_id in retry_ids:
        try:
            await bot.send_message(int(u_id), f"⚠️ <b>ПОВТОРНОЕ ОПОВЕЩЕНИЕ:</b>\n\n{text}", parse_mode="HTML")
            success += 1
        except Exception:
            still_failed.append(u_id)

    if still_failed:
        await state.update_data(retry_ids=still_failed)
        kb = InlineKeyboardBuilder()
        kb.row(InlineKeyboardButton(text="🔄 Попробовать еще раз", callback_data="broadcast_retry"))
        kb.row(InlineKeyboardButton(text="❌ Отмена", callback_data="delete_msg"))
        await cb.message.edit_text(f"🏁 <b>Итог повтора:</b>\n✅ Успешно: {success}\n❌ Всё еще не в сети: {len(still_failed)}", 
                                   reply_markup=kb.as_markup())
    else:
        await cb.message.edit_text(f"✅ <b>Все сообщения успешно доставлены!</b>")
        await state.clear()
    await cb.answer()

@dp.callback_query(F.data == "delete_msg")
async def cb_delete(cb: CallbackQuery):
    await cb.message.delete()
    await cb.answer()

# --- 4. DEMO для маркетинга ---
@dp.message(Command("demo"))
async def cmd_demo(m: Message):
    if m.from_user.id not in ADMIN_IDS: return

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    # Колонки: Тип услуги, Дата, Имя, Телефон, Груз, Инвойс, Пункт отпр, Пункт назн, Вес, Объем, Детали
    demo_payload = [
        "Авто-доставка (Демо)", # Тип услуги
        now,                    # Дата и время
        m.from_user.full_name,  # Имя
        "+7 999 000-00-00",     # Телефон (тестовый)
        "Запчасти",             # Груз
        "5000 USD",             # Инвойс
        "Урумчи (Китай)",       # Пункт отправления
        "Гданьск (Польша)",     # Пункт назначения
        "150 кг",               # Вес
        "0.5 м³",               # Объем
        "ТЕСТОВЫЙ ЗАКАЗ ДЛЯ ДЕМО" # Детали
    ]

    msg = await m.answer("⏳ <b>Запуск демо-заказа...</b>")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        # Исправлено название файла на creds.json по вашему запросу
        creds = Credentials.from_service_account_file("creds.json", scopes=scopes)
        client = gspread.authorize(creds)

        sheet_id = os.getenv('SHEET_ID')
        sheet = client.open_by_key(sheet_id).sheet1 
        sheet.append_row(demo_payload) 
        
        await msg.edit_text(
            f"✅ <b>Заказ успешно создан!</b>\n\n"
            f"📍 Маршрут: {demo_payload[6]} -> {demo_payload[7]}\n"
            f"📊 Данные распределены по вашим {len(demo_payload)} колонкам.\n"
            f"🚀 Срок 18 дней подтвержден.",
            parse_mode="HTML"
        )
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

# === ДОПОЛНИТЕЛЬНО: КНОПКА СКАЧАТЬ БАЗУ ===
@dp.callback_query(F.data == "download_base")
async def cb_download_base(cb: CallbackQuery):
    if cb.from_user.id not in ADMIN_IDS: return
    
    # Создаем текстовый файл со всеми клиентами
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, status FROM users")
    users = cursor.fetchall()
    conn.close()
    
    with open("users_base.txt", "w", encoding="utf-8") as f:
        f.write("ID | USERNAME | STATUS\n")
        for u in users:
            f.write(f"{u[0]} | @{u[1]} | {u[2]}\n")
    
    # Отправляем файл админу
    from aiogram.types import FSInputFile
    file = FSInputFile("users_base.txt")
    await cb.message.answer_document(file, caption="📂 Полная база клиентов для продажи.")
    await cb.answer()

# === 10. ФИНАЛЬНЫЙ ЗАПУСК И AI-КОНСУЛЬТАНТ ===

@dp.message(F.text & ~F.state())
async def ai_consultant(m: Message):
    """
    Обработка любых текстовых сообщений вне сценариев.
    AI консультирует пользователя на базе ваших условий.
    """
    if m.text in ["🚛 Оформить перевозку", "🛡 Таможня", "📄 Анализ документов", "👨‍💼 Менеджер"]:
        return # Игнорируем нажатия кнопок, для них есть свои хендлеры

    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    # Контекст для нейросети (ваши условия)
    system_ctx = (
        "Ты эксперт компании Logistics Manager. "
        "Мы доставляем из Китая в Европу за 18 дней и у нас самые низкие расценки. "
        "Если пользователь спрашивает о цене или сроках — подтверждай эти данные. "
        "Всегда предлагай нажать кнопку 'Оформить перевозку' для точного расчета специалистом."
    )
    
    try:
        res = await client_ai.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_ctx},
                {"role": "user", "content": m.text}
            ]
        )
        await m.answer(f"🏢 <b>Logistics Manager:</b>\n\n{res.choices[0].message.content}")
    except Exception as e:
        logging.error(f"AI Error: {e}")
        await m.answer("Напишите нашему менеджеру @logistics_manager_pro для консультации.")

async def main():
    """Точка входа в программу"""
    logging.info("Бот Logistics Manager запущен...")
    
    # Удаляем вебхуки и запускаем чистый опрос
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Бот остановлен.")

# --- ФИНАЛЬНЫЙ БЛОК ЗАПУСКА (ВСТАВИТЬ В САМЫЙ КОНЕЦ ФАЙЛА) ---

async def main():
    # Эта часть создает нужные колонки в базе данных при запуске
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    
    # Создаем таблицу, если её нет
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            role TEXT DEFAULT 'Клиент',
            car_number TEXT,
            route TEXT,
            last_geo TEXT,
            last_seen TEXT,
            last_google_update TEXT
        )
    ''')
    
    # ПРИНУДИТЕЛЬНО добавляем недостающие колонки в уже существующую базу
    columns = [
        ("car_number", "TEXT"),
        ("route", "TEXT"),
        ("last_google_update", "TEXT")
    ]
    
    for col_name, col_type in columns:
        try:
            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")
        except:
            pass # Если колонка уже есть, код просто пойдет дальше
            
    conn.commit()
    conn.close()

    # Запуск самого бота
    print("🚀 Система готова. База данных проверена.")
    await dp.start_polling(bot)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
