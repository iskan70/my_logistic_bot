import os, asyncio, logging, sqlite3, json, re
from datetime import datetime
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.enums import ChatAction
from openai import AsyncOpenAI
import gspread
from google.oauth2.service_account import Credentials

# --- КОНФИГУРАЦИЯ ---
logging.basicConfig(level=logging.INFO)
bot = Bot(token=os.getenv("BOT_TOKEN"))
dp = Dispatcher(storage=MemoryStorage())
client_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ADMIN_IDS = [494255577] 

VAT_RATES = {"RF": 20, "KZ": 12}
DUTY_PRESETS = {"electronics": 5, "clothes": 10, "parts": 7}

# --- СОСТОЯНИЯ ---
class CustomsCalc(StatesGroup):
    cargo_name = State()
    select_duty = State()
    manual_duty = State()
    cargo_price = State()
    select_region = State()

class OrderFlow(StatesGroup):
    waiting_for_doc_analysis = State()

class DriverReg(StatesGroup):
    car_number = State()
    route = State()

# --- БАЗА ДАННЫХ (ОБНОВЛЕННАЯ) ---
def init_db():
    conn = sqlite3.connect('logistics.db')
    # Добавлены колонки для статистики: количество документов и общая сумма расчетов
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY, username TEXT, role TEXT, 
        car_number TEXT, route TEXT, last_geo TEXT, last_seen TEXT,
        docs_analyzed INTEGER DEFAULT 0, total_calculated REAL DEFAULT 0)""")
    conn.commit()
    conn.close()

def update_user(user_id, username, **kwargs):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, role) VALUES (?, ?, 'Клиент')", (user_id, username))
    if kwargs:
        if 'docs_analyzed' in kwargs: # Инкремент для документов
            cursor.execute("UPDATE users SET docs_analyzed = docs_analyzed + 1 WHERE user_id = ?", (user_id,))
            kwargs.pop('docs_analyzed')
        if 'add_calc_sum' in kwargs: # Добавление суммы к общему счету
            cursor.execute("UPDATE users SET total_calculated = total_calculated + ? WHERE user_id = ?", (kwargs['add_calc_sum'], user_id))
            kwargs.pop('add_calc_sum')
        
        if kwargs:
            cols = ", ".join([f"{k} = ?" for k in kwargs.keys()])
            cursor.execute(f"UPDATE users SET {cols}, last_seen = ? WHERE user_id = ?", (*kwargs.values(), now, user_id))
    else:
        cursor.execute("UPDATE users SET last_seen = ? WHERE user_id = ?", (now, user_id))
    conn.commit()
    conn.close()

async def write_gs(row):
    try:
        info = json.loads(os.getenv("GOOGLE_CREDS_JSON"))
        creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(os.getenv("SHEET_ID")).sheet1
        sh.append_row(row)
    except Exception as e: logging.error(f"GS Error: {e}")

# --- КЛАВИАТУРЫ ---
def main_kb(user_id):
    kb = ReplyKeyboardBuilder()
    kb.row(KeyboardButton(text="🚛 Оформить перевозку"))
    kb.row(KeyboardButton(text="🛡 Таможня"), KeyboardButton(text="📄 Анализ документов"))
    kb.row(KeyboardButton(text="👨‍💼 Менеджер"))
    if user_id in ADMIN_IDS: kb.row(KeyboardButton(text="🛠 Админ-панель"))
    return kb.as_markup(resize_keyboard=True)

# --- ПРИВЕТСТВИЕ ---
@dp.message(Command("start"))
async def start_cmd(m: Message):
    update_user(m.from_user.id, m.from_user.username)
    text = (f"🤝 Здравствуйте, {m.from_user.first_name}!\n\n"
            f"Вас приветствует логист компании <b>Logistics Manager</b>.\n\n"
            f"Мы доставляем из Китая в Европу за <b>18 дней</b> и у нас самые <b>низкие расценки</b>.\n\n"
            f"Я помогу вам:\n"
            f"• Оформить заказ\n"
            f"• Рассчитать стоимость международной доставки\n"
            f"• Проверить коммерческие документы (AI-анализ)\n"
            f"• Оценить таможенные пошлины и налоги\n\n"
            f"Воспользуйтесь меню ниже 👇")
    await m.answer(text, reply_markup=main_kb(m.from_user.id), parse_mode="HTML")

# --- ТАМОЖНЯ (С ЗАПИСЬЮ СУММЫ) ---
@dp.message(F.text == "🛡 Таможня")
async def cust_1(m: Message, state: FSMContext):
    await state.set_state(CustomsCalc.cargo_name)
    await m.answer("🔍 Введите название товара:")

@dp.message(CustomsCalc.cargo_name)
async def cust_2(m: Message, state: FSMContext):
    await state.update_data(c_name=m.text)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Электроника (5%)", callback_data="d_5"), InlineKeyboardButton(text="Одежда (10%)", callback_data="d_10")],
        [InlineKeyboardButton(text="✏️ Свой %", callback_data="d_manual")]
    ])
    await m.answer(f"📦 Товар: {m.text}\nВыберите ставку пошлины:", reply_markup=kb)
    await state.set_state(CustomsCalc.select_duty)

@dp.callback_query(F.data.startswith("d_"), CustomsCalc.select_duty)
async def cust_3(cb: CallbackQuery, state: FSMContext):
    if cb.data == "d_manual":
        await cb.message.answer("Введите % пошлины:")
        await state.set_state(CustomsCalc.manual_duty)
    else:
        await state.update_data(duty=float(cb.data.split("_")[1]))
        await cb.message.answer("💰 Введите стоимость груза ($):")
        await state.set_state(CustomsCalc.cargo_price)
    await cb.answer()

@dp.message(CustomsCalc.manual_duty)
async def cust_m(m: Message, state: FSMContext):
    await state.update_data(duty=float(m.text.replace(",", ".")))
    await m.answer("💰 Введите стоимость ($):")
    await state.set_state(CustomsCalc.cargo_price)

@dp.message(CustomsCalc.cargo_price)
async def cust_4(m: Message, state: FSMContext):
    price = float(m.text.replace(",", "."))
    await state.update_data(price=price)
    # Собираем статистику по сумме расчетов
    update_user(m.from_user.id, m.from_user.username, add_calc_sum=price)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🇷🇺 РФ ({VAT_RATES['RF']}%)", callback_data="v_RF"),
         InlineKeyboardButton(text=f"🇰🇿 РК ({VAT_RATES['KZ']}%)", callback_data="v_KZ")]
    ])
    await m.answer("🌍 Регион назначения:", reply_markup=kb)
    await state.set_state(CustomsCalc.select_region)

@dp.callback_query(F.data.startswith("v_"), CustomsCalc.select_region)
async def cust_final(cb: CallbackQuery, state: FSMContext):
    v_rate = VAT_RATES[cb.data.split("_")[1]]
    d = await state.get_data()
    duty_a = d['price'] * (d['duty'] / 100)
    vat_a = (d['price'] + duty_a) * (v_rate / 100)
    res_text = f"📊 <b>ИТОГ: ${(duty_a + vat_a):,.2f}</b>\n\nПошлина: ${duty_a:,.2f}\nНДС: ${vat_a:,.2f}"
    await cb.message.edit_text(res_text, parse_mode="HTML")
    await write_gs(["РАСЧЕТ", datetime.now().strftime("%d.%m.%Y"), cb.from_user.full_name, d['c_name'], d['price'], d['duty'], v_rate, duty_a, vat_a, (duty_a+vat_a), "Успешно"])
    await state.clear(); await cb.answer()

# --- АНАЛИЗ ДОКУМЕНТОВ (СО СЧЕТЧИКОМ) ---
@dp.message(F.text == "📄 Анализ документов")
async def doc_init(m: Message, state: FSMContext):
    await state.set_state(OrderFlow.waiting_for_doc_analysis)
    await m.answer("📂 Отправьте фото документов.")

@dp.message(OrderFlow.waiting_for_doc_analysis, F.photo | F.document)
async def doc_proc(m: Message, state: FSMContext):
    update_user(m.from_user.id, m.from_user.username, docs_analyzed=1)
    # Логика AI анализа (сокращена для краткости, она идентична прошлой версии)
    await m.answer("📊 Документ принят в работу. AI анализирует данные...")
    await state.clear()

# --- АДМИНКА С ВОРОНКОЙ И ФИНАНСАМИ ---
@dp.message(F.text == "🛠 Админ-панель")
async def adm_menu(m: Message):
    if m.from_user.id not in ADMIN_IDS: return
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text="📈 Воронка и Финансы", callback_data="a_stats_full"))
    kb.row(InlineKeyboardButton(text="👥 Юзеры", callback_data="a_u"), InlineKeyboardButton(text="🚛 Карта", callback_data="a_g"))
    await m.answer("🛠 <b>Панель управления</b>", reply_markup=kb.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data == "a_stats_full")
async def adm_stats_full(cb: CallbackQuery):
    conn = sqlite3.connect('logistics.db')
    cursor = conn.cursor()
    
    # Считаем общие показатели
    total_users = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_docs = cursor.execute("SELECT SUM(docs_analyzed) FROM users").fetchone()[0] or 0
    total_money = cursor.execute("SELECT SUM(total_calculated) FROM users").fetchone()[0] or 0
    active_drivers = cursor.execute("SELECT COUNT(*) FROM users WHERE role='Водитель'").fetchone()[0]
    
    res = (f"📊 <b>ОТЧЕТ ПО ВОРОНКЕ:</b>\n\n"
           f"👥 Всего пользователей: <b>{total_users}</b>\n"
           f"📑 Проверено документов (AI): <b>{total_docs}</b>\n"
           f"💰 Общая сумма расчетов: <b>${total_money:,.0f}</b>\n"
           f"🚛 Водителей в штате: <b>{active_drivers}</b>\n\n"
           f"<i>Средний чек запроса: ${ (total_money/total_users if total_users>0 else 0):,.0f}</i>")
    
    await cb.message.answer(res, parse_mode="HTML")
    await cb.answer()

# --- ОСТАЛЬНОЕ (МЕНЕДЖЕР, ОФОРМЛЕНИЕ, AI ЧАТ) ---
@dp.message(F.text == "👨‍💼 Менеджер")
async def manager_call(m: Message):
    await m.answer("👨‍💼 Связь: @logistics_manager_pro\nДоставка за 18 дней.")

@dp.message(F.text == "🚛 Оформить перевозку")
async def order_link(m: Message):
    await m.answer("📝 Для оформления заявки, пожалуйста, напишите нашему менеджеру @logistics_manager_pro детали вашего груза.")

# [Код для GPS и регистрации водителей остается прежним]

async def main():
    init_db(); await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
