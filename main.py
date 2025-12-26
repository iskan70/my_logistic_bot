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
# 2. СОСТОЯНИЯ (FSM) — КАРКАС ДИАЛОГОВ
# =========================================================
class OrderFlow(StatesGroup):
    fio = State()           # ФИО
    phone = State()         # Номер
    cargo_type = State()    # Тип груза
    cargo_value = State()   # Стоимость $
    origin = State()        # Откуда
    destination = State()   # Куда
    weight = State()        # Вес
    volume = State()        # Объем
    waiting_for_doc_analysis = State()

class CustomsCalc(StatesGroup):
    cargo_name = State()    # Название для AI
    select_duty = State()   # Выбор %
    manual_duty = State()   # Свой %
    cargo_price = State()   # Цена товара

class Broadcast(StatesGroup):
    waiting_for_text = State()

# =========================================================
# 3. РАБОТА С ДАННЫМИ (DB & GOOGLE)
# =========================================================
def init_db():
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
        (user_id INTEGER PRIMARY KEY, username TEXT, role TEXT DEFAULT 'Клиент', 
        status TEXT, last_seen TEXT, last_geo TEXT, car_number TEXT, route TEXT, last_google_update TEXT)''')
    conn.commit()
    conn.close()

async def get_gs_client():
    creds_json = os.getenv("GOOGLE_CREDS_JSON")
    if not creds_json: return None
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
    row = conn.execute("SELECT role FROM users WHERE user_id=?", (user_id,)).fetchone()
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
    countries = [("🇨🇳 +86", "+86"), ("🇰🇿 +7", "+7"), ("🇷🇺 +7", "+7"), ("🇧🇾 +375", "+375"), ("🇺🇿 +998", "+998")]
    for n, c in countries: builder.button(text=n, callback_data=f"country_{c}")
    return builder.adjust(2).as_markup()

# =========================================================
# 5. КОМАНДЫ (START, ADMIN, DRIVER, DEMO)
# =========================================================

@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    
    # Регистрация или обновление пользователя в БД
    conn = sqlite3.connect('logistics.db')
    # Используем INSERT OR IGNORE, чтобы не было ошибок, если колонки status еще нет
    conn.execute(
        "INSERT OR IGNORE INTO users (user_id, username, last_seen) VALUES (?, ?, ?)",
        (m.from_user.id, m.from_user.username, datetime.now().strftime("%d.%m.%Y %H:%M"))
    )
    conn.execute(
        "UPDATE users SET last_seen=?, username=? WHERE user_id=?",
        (datetime.now().strftime("%d.%m.%Y %H:%M"), m.from_user.username, m.from_user.id)
    )
    conn.commit()
    conn.close()
    
    welcome_text = (
        f"🤝 Здравствуйте, {m.from_user.first_name}!\n\n"
        f"Вас приветствует логист компании <b>Logistics Manager</b>.\n\n"
        f"Я помогу вам:\n"
        f"• Оформить заказ\n"
        f"• Рассчитать стоимость международной доставки\n"
        f"• Проверить коммерческие документы (AI-анализ)\n"
        f"• Оценить таможенные пошлины и налоги\n\n"
        f"Воспользуйтесь меню ниже для начала работы 👇 или напишите в сообщении свой вопрос"
    )
    
    await m.answer(welcome_text, reply_markup=get_main_kb(m.from_user.id))

@dp.message(Command("admin"))
async def cmd_admin(m: Message):
    """Команда вызова панели управления (только для ADMIN_IDS)"""
    if m.from_user.id not in ADMIN_IDS:
        # Если не админ — просто игнорируем или пускаем в AI-чат
        return 

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📈 Статистика базы", callback_data="stats_users")],
        [InlineKeyboardButton(text="📋 Тест системы (/demo)", callback_data="run_demo_fast")]
    ])
    await m.answer("🛠 <b>Панель администратора Logistics Manager</b>", reply_markup=kb)

@dp.message(Command("demo"))
async def cmd_demo(m: Message):
    """Глубокая диагностика 2-х инструментов: Заявки + GPS Мониторинг"""
    if m.from_user.id not in ADMIN_IDS: return

    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    status_msg = await m.answer("⚙️ <b>Запуск комплексной диагностики...</b>")
    
    # 1. Проверка таблицы заявок (11 колонок)
    order_payload = [
        "🤖 ТЕСТ_ЗАЯВКА", now, m.from_user.full_name, "+7(999)000-00-00", 
        "Запчасти", "5000 USD", "Шанхай", "Мюнхен", "50 кг", "0.3 м³", "Проверка связи"
    ]
    
    # 2. Проверка таблицы мониторинга (7 колонок)
    geo_payload = [
        f"Тест-Водитель ({m.from_user.first_name})", "TEST-777", "Пекин -> Варшава", 
        now, "39.9042,116.4074", "http://maps.google.com/?q=39.9042,116.4074", "🛠 ДИАГНОСТИКА"
    ]

    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)

    success_order = await save_to_google_sheets(order_payload)
    success_geo = await save_to_google_sheets(geo_payload, "мониторинг водителей")

    res = [
        "✅ <b>РЕЗУЛЬТАТЫ ПРОВЕРКИ:</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"1️⃣ <b>Таблица заявок:</b> {'ОК (11 колонок)' if success_order else '❌ ОШИБКА'}",
        f"2️⃣ <b>Мониторинг GPS:</b> {'ОК (Запись в лог)' if success_geo else '❌ ОШИБКА'}",
        "━━━━━━━━━━━━━━━━━━",
        f"🕒 Время теста: {now}",
        "\n<i>Если GPS '❌', создайте лист 'мониторинг водителей'</i>"
    ]
    await status_msg.edit_text("\n".join(res))

@dp.message(Command("driver_2025"))
async def cmd_driver(m: Message):
    conn = sqlite3.connect('logistics.db')
    conn.execute("UPDATE users SET role='Водитель' WHERE user_id=?", (m.from_user.id,))
    conn.commit()
    conn.close()
    await m.answer("✅ <b>Роль водителя активирована!</b>\nВам доступна кнопка отправки GPS.", reply_markup=get_main_kb(m.from_user.id))

# =========================================================
# 6. АНКЕТА (11 КОЛОНОК)
# =========================================================
@dp.message(F.text == "🚛 Оформить перевозку")
async def ord_1(m: Message, state: FSMContext):
    await state.set_state(OrderFlow.fio)
    await m.answer("👤 Введите ФИО:", reply_markup=ReplyKeyboardRemove())

@dp.message(OrderFlow.fio)
async def ord_2(m: Message, state: FSMContext):
    await state.update_data(fio=m.text)
    await state.set_state(OrderFlow.phone)
    await m.answer("📱 Выберите код:", reply_markup=get_country_kb())

@dp.callback_query(F.data.startswith("country_"), OrderFlow.phone)
async def ord_3(cb: CallbackQuery, state: FSMContext):
    await state.update_data(p_code=cb.data.split("_")[1])
    await cb.message.answer("Введите номер (без кода):")
    await cb.answer()

@dp.message(OrderFlow.phone)
async def ord_4(m: Message, state: FSMContext):
    d = await state.get_data()
    await state.update_data(phone=d['p_code'] + m.text)
    await state.set_state(OrderFlow.cargo_type); await m.answer("📦 Что везем?")

@dp.message(OrderFlow.cargo_type)
async def ord_5(m: Message, state: FSMContext):
    await state.update_data(cargo=m.text); await state.set_state(OrderFlow.cargo_value)
    await m.answer("💰 Стоимость ($):")

@dp.message(OrderFlow.cargo_value)
async def ord_6(m: Message, state: FSMContext):
    await state.update_data(val=m.text); await state.set_state(OrderFlow.origin)
    await m.answer("📍 Откуда?")

@dp.message(OrderFlow.origin)
async def ord_7(m: Message, state: FSMContext):
    await state.update_data(org=m.text); await state.set_state(OrderFlow.destination)
    await m.answer("🏁 Куда?")

@dp.message(OrderFlow.destination)
async def ord_8(m: Message, state: FSMContext):
    await state.update_data(dst=m.text); await state.set_state(OrderFlow.weight)
    await m.answer("⚖️ Вес (кг):")

@dp.message(OrderFlow.weight)
async def ord_9(m: Message, state: FSMContext):
    await state.update_data(w=m.text); await state.set_state(OrderFlow.volume)
    await m.answer("📐 Объем (м³):")

@dp.message(OrderFlow.volume)
async def ord_10(m: Message, state: FSMContext):
    d = await state.get_data()
    row = ["ЗАКАЗ", datetime.now().strftime("%d.%m.%Y %H:%M"), d['fio'], d['phone'], d['cargo'], d['val'], d['org'], d['dst'], d['w'], m.text, "Срок 18д"]
    await save_to_google_sheets(row)
    await m.answer("🚀 Заявка в таблице! Менеджер свяжется.", reply_markup=get_main_kb(m.from_user.id))
    await state.clear()

# =========================================================
# 7. VISION AI (Base64)
# =========================================================
@dp.message(F.text == "📄 Анализ документов")
async def vis_1(m: Message, state: FSMContext):
    await state.set_state(OrderFlow.waiting_for_doc_analysis)
    await m.answer("📸 Пришлите фото документа:")

@dp.message(OrderFlow.waiting_for_doc_analysis, F.photo)
async def vis_2(m: Message, state: FSMContext):
    await m.answer("⌛ Анализирую...")
    file = await bot.get_file(m.photo[-1].file_id)
    p_bytes = await bot.download_file(file.file_path)
    b64 = base64.b64encode(p_bytes.getvalue()).decode()
    
    res = await client_ai.chat.completions.create(model="gpt-4o", messages=[{"role": "user", "content": [{"type": "text", "text": "Выпиши Отправителя, Товар и Вес."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}]}] )
    await m.answer(f"📊 AI Резюме:\n{res.choices[0].message.content}", reply_markup=get_main_kb(m.from_user.id))
    await state.clear()

# =========================================================
# 8. ТАМОЖЕННЫЙ КАЛЬКУЛЯТОР (ПОШЛИНЫ И ТН ВЭД)
# =========================================================

@dp.message(F.text == "🛡 Таможня")
async def cust_init(m: Message, state: FSMContext):
    await state.set_state(CustomsCalc.cargo_name)
    await m.answer("🔍 Введите название товара (например: 'Литиевые аккумуляторы'):")

@dp.message(CustomsCalc.cargo_name)
async def cust_ai_tip(m: Message, state: FSMContext):
    await state.update_data(c_name=m.text)
    # Быстрая подсказка от AI по коду ТН ВЭД
    res = await client_ai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "system", "content": "Назови только вероятный код ТН ВЭД и ставку пошлины %."}, {"role": "user", "content": m.text}]
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5%", callback_data="setduty_5"), InlineKeyboardButton(text="10%", callback_data="setduty_10")],
        [InlineKeyboardButton(text="15%", callback_data="setduty_15"), InlineKeyboardButton(text="Свой %", callback_data="setduty_manual")]
    ])
    await m.answer(f"💡 <b>Справка AI:</b> {res.choices[0].message.content}\n\nВыберите или введите ставку пошлины для расчета:", reply_markup=kb)
    await state.set_state(CustomsCalc.select_duty)

@dp.callback_query(F.data.startswith("setduty_"), CustomsCalc.select_duty)
async def cust_set_duty_choice(cb: CallbackQuery, state: FSMContext):
    """Обработка выбора процента пошлины"""
    action = cb.data.split("_")[1]
    
    if action == "manual":
        await cb.message.answer("Введите число процентов пошлины (только цифры):")
        await state.set_state(CustomsCalc.manual_duty)
    else:
        await state.update_data(duty=float(action))
        await cb.message.answer("💰 Введите инвойсную стоимость товара ($):")
        await state.set_state(CustomsCalc.cargo_price)
    await cb.answer()

@dp.message(CustomsCalc.manual_duty)
async def cust_manual_duty_val(m: Message, state: FSMContext):
    """Прием ручного ввода процента"""
    try:
        val = float(m.text.replace(",", "."))
        await state.update_data(duty=val)
        await m.answer("💰 Теперь введите инвойсную стоимость товара ($):")
        await state.set_state(CustomsCalc.cargo_price)
    except:
        await m.answer("⚠️ Пожалуйста, введите число.")

@dp.message(CustomsCalc.cargo_price)
async def cust_final_calc(m: Message, state: FSMContext):
    data = await state.get_data()
    try:
        price = float(m.text.replace(",", "."))
        duty_p = data['duty']
        
        # Формула: Пошлина + НДС (начисляется на сумму цены и пошлины)
        duty_v = price * (duty_p / 100)
        vat_v = (price + duty_v) * 0.20 # Стандарт НДС 20%
        total_taxes = duty_v + vat_v
        
        res = (f"📊 <b>ПРЕДВАРИТЕЛЬНЫЙ РАСЧЕТ:</b>\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"📦 Товар: {data.get('c_name', 'Не указан')}\n"
               f"💵 Стоимость: ${price:,.2f}\n"
               f"⚖️ Пошлина ({duty_p}%): ${duty_v:,.2f}\n"
               f"🏦 НДС (20%): ${vat_v:,.2f}\n"
               f"━━━━━━━━━━━━━━━━━━\n"
               f"💰 <b>ИТОГО ТАМОЖНЯ: ${total_taxes:,.2f}</b>\n\n"
               f"<i>*Расчет носит ознакомительный характер.</i>")
        
        await m.answer(res, reply_markup=get_main_kb(m.from_user.id))
    except Exception as e:
        logging.error(f"Calc error: {e}")
        await m.answer("⚠️ Ошибка: Введите только число (цену).")
    await state.clear()

# =========================================================
# 9. GPS МОНИТОРИНГ (Edited & Manual)
# =========================================================
@dp.message(F.location)
async def handle_manual_geo(m: Message):
    lat, lon = m.location.latitude, m.location.longitude
    geo = f"{lat},{lon}"
    # Генерируем прямую ссылку на точку
    map_url = f"https://www.google.com/maps?q={geo}"
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    
    conn = sqlite3.connect('logistics.db')
    # Добавляем IFNULL, чтобы код не падал, если данных нет
    u = conn.execute("SELECT username, IFNULL(car_number, '-'), IFNULL(route, '-') FROM users WHERE user_id=?", (m.from_user.id,)).fetchone()
    conn.execute("UPDATE users SET last_geo=?, last_seen=? WHERE user_id=?", (geo, now, m.from_user.id))
    conn.commit()
    conn.close()

    row = [u[0] or m.from_user.full_name, u[1], u[2], now, geo, map_url, "🚀 Начал рейс"]
    # Пишем в отдельный лист Google
    await save_to_google_sheets(row, "мониторинг водителей")
    await m.answer("✅ <b>Рейс запущен!</b>\nВаш GPS-сигнал транслируется в таблицу.")

@dp.edited_message(F.location)
async def handle_live_geo(m: Message):
    user_id = m.from_user.id
    geo = f"{m.location.latitude},{m.location.longitude}"
    now = datetime.now()
    
    conn = sqlite3.connect('logistics.db')
    u = conn.execute("SELECT username, car_number, route, last_google_update FROM users WHERE user_id=?", (user_id,)).fetchone()
    
    # Ограничение 3 часа для записи в Google Sheets (чтобы не спамить API)
    should_update_gs = True
    if u and u[3]:
        try:
            last_dt = datetime.strptime(u[3], "%d.%m.%Y %H:%M")
            if (now - last_dt).total_seconds() < 10800:
                should_update_gs = False
        except:
            should_update_gs = True

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
    if cb.from_user.id not in ADMIN_IDS: return
    
    conn = sqlite3.connect('logistics.db')
    # Более мощный запрос: общее кол-во и список последних 5 имен
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    recent = conn.execute("SELECT username FROM users ORDER BY last_seen DESC LIMIT 5").fetchall()
    conn.close()
    
    names = ", ".join([f"@{r[0]}" for r in recent if r[0]])
    res = (f"📊 <b>СТАТИСТИКА БАЗЫ</b>\n"
           f"━━━━━━━━━━━━━━━━━━\n"
           f"👥 Всего пользователей: <b>{total}</b>\n"
           f"🕒 Последние в сети: <i>{names}</i>")
    
    await cb.message.answer(res)
    await cb.answer()

@dp.message(F.text & ~F.state())
async def ai_consultant(m: Message):
    # Если это кнопка меню — не отвечаем как AI
    if m.text in ["🚛 Оформить перевозку", "🛡 Таможня", "📄 Анализ документов", "👨‍💼 Менеджер"]: 
        return
        
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    # Используем твой фирменный промпт
    res = await client_ai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "Ты эксперт Logistics Manager. Доставка из Китая в Европу 18 дней, низкие цены. Предлагай нажать 'Оформить перевозку'."}, 
            {"role": "user", "content": m.text}
        ]
    )
    await m.answer(f"🏢 <b>Logistics Manager:</b>\n\n{res.choices[0].message.content}")

# =========================================================
# ЗАПУСК БОТА
# =========================================================
async def main():
    init_db()
    print("✅ База данных готова")
    print("🚀 Бот Logistics Manager запущен и ожидает сообщений...")
    
    # Сброс вебхуков и запуск пуллинга
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        print("🛑 Бот остановлен")
