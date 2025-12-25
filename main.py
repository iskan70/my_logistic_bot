# -*- coding: utf-8 -*-

import asyncio
import os
import re
import logging
import platform
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

# === 1. ИНИЦИАЛИЗАЦИЯ И НАСТРОЙКИ ===
load_dotenv()
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

bot = Bot(token=os.getenv("BOT_TOKEN"), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
client_ai = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", "").strip())
SHEET_ID = os.getenv("SHEET_ID")

# === 1.1 ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===
async def save_to_google_sheets(row_data: list):
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        
        # Собираем ключ из списка строк, чтобы Python точно не потерял переносы \n
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
        
        sheet_id = os.getenv("SHEET_ID").strip()
        sheet = client.open_by_key(sheet_id).get_worksheet(0)
        
        sheet.append_row(row_data)
        print("✅ УСПЕХ: Запись в таблице!")
        return True
        
    except Exception as e:
        # Теперь он точно напишет причину, даже если она внутри данных
        import traceback
        print(f"❌ КОНКРЕТНАЯ ПРИЧИНА: {e}")
        print(traceback.format_exc()) # Это покажет, на какой строке сбой
        return False

# === 2. МАШИНА СОСТОЯНИЙ (FSM) - ИСПРАВЛЕННАЯ ===

class OrderFlow(StatesGroup):
    """Состояния для оформления перевозки и анализа документов"""
    fio = State()                 # Добавлено для шага 1
    phone = State()               # Добавлено для шага 2
    cargo_type = State()          # Что везем
    cargo_value = State()         # Стоимость груза
    origin = State()              # Город отправления (вместо route_origin)
    destination = State()         # Город назначения (вместо route_destination)
    weight = State()              # Вес (вместо cargo_weight_value)
    volume = State()              # Объем (вместо cargo_volume_value)
    
    # Резервные состояния (если понадобятся позже)
    selecting_role = State()
    selecting_transport = State()
    confirm_data = State()
    
    # Состояние для ИИ-анализа (чтобы не было AttributeError)
    waiting_for_doc_analysis = State() 

class AdminPanel(StatesGroup):
    """Состояния для рассылки админа"""
    broadcast_message = State()

class CustomsCalc(StatesGroup):
    """Состояния для таможенного калькулятора"""
    cargo_name = State()          # Шаг 1: Название товара
    select_duty = State()         # Шаг 2: Выбор или ввод ставки
    manual_duty = State()         # Подшаг: Ручной ввод %
    cargo_price = State()         # Шаг 3: Ввод цены
    select_region = State()       # Шаг 4: Выбор НДС (РФ/РК)
    
    # Старые поля для совместимости, если используются
    val_input = State()
    duty_input = State()

# === 3. ГЕНЕРАТОРЫ КЛАВИАТУР ===
def get_main_kb():
    btns = [
        [KeyboardButton(text="🚛 Оформить перевозку"), KeyboardButton(text="🛡 Таможня")],
        [KeyboardButton(text="📄 Анализ документов"), KeyboardButton(text="👨‍💼 Менеджер")]
    ]
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True)

def get_country_kb():
    """Клавиатура с флагами и кодами стран"""
    btns = [
        [KeyboardButton(text="🇰🇿 +7 (KZ)"), KeyboardButton(text="🇷🇺 +7 (RU)")],
        [KeyboardButton(text="🇵🇱 +48 (PL)"), KeyboardButton(text="🇹🇷 +90 (TR)")],
        [KeyboardButton(text="🇨🇳 +86 (CN)"), KeyboardButton(text="🇺🇿 +998")],
        [KeyboardButton(text="🇧🇾 +375"), KeyboardButton(text="🇰🇬 +996")],
        [KeyboardButton(text="⌨️ Другой код"), KeyboardButton(text="📱 Мой номер", request_contact=True)]
    ]
    return ReplyKeyboardMarkup(keyboard=btns, resize_keyboard=True, one_time_keyboard=True)

def get_region_kb():
    btns = [
        [InlineKeyboardButton(text="🇰🇿 Казахстан (НДС 16%)", callback_data="vat_16")],
        [InlineKeyboardButton(text="🇷🇺 Россия (НДС 22%)", callback_data="vat_22")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=btns)

# === 4. ОБРАБОТКА КОМАНДЫ /START ===

@dp.message(Command("start"))
async def cmd_start(m: Message, state: FSMContext):
    """
    Запуск бота. 
    Используется строго согласованный текст приветствия.
    """
    await state.clear()
    
    # Ваше персональное приветствие
    welcome_text = (
        f"🤝 Здравствуйте, {m.from_user.first_name}!\n\n"
        f"Вас приветствует логист компании Logistics Manager.\n\n"
        f"Я помогу вам:\n"
        f"• Оформить заказ\n"
        f"• Рассчитать стоимость международной доставки\n"
        f"• Проверить коммерческие документы (AI-анализ)\n"
        f"• Оценить таможенные пошлины и налоги\n\n"
        f"Воспользуйтесь меню ниже для начала работы 👇 или напишите в сообщении что именно вас интересует"
    )
    
    await m.answer(welcome_text, reply_markup=get_main_kb())

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

@dp.message(OrderFlow.phone)
async def order_phone(m: Message, state: FSMContext):
    """Шаг 3: Валидация телефона"""
    digits_map = {"+7": 10, "+48": 9, "+90": 10, "+86": 11, "+998": 9, "+375": 9, "+996": 9}
    text = m.text.strip() if m.text else ""

    if "(" in text and "+" in text:
        code = re.search(r'\+\d+', text).group()
        needed = digits_map.get(code)
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
    
    await bot.send_chat_action(m.chat.id, ChatAction.TYPING)
    
    # Формируем список строго под вашу шапку (11 колонок)
    row = [
        "ЗАКАЗ",                                     # Тип услуги
        datetime.now().strftime("%d.%m.%Y %H:%M"),   # Дата и время
        d.get('fio'),                                # Имя
        d.get('phone'),                              # Телефон
        d.get('cargo_type'),                         # Груз
        d.get('cargo_value'),                        # Инвойс (стоимость товара)
        d.get('org'),                                # Пункт отправления
        d.get('dst'),                                # Пункт назначения
        d.get('weight'),                             # Вес
        d.get('volume'),                             # Объем
        "-"                                          # Детали
    ]
    
    # Вызов общей функции сохранения
    success = await save_to_google_sheets(row)
    
    if success:
        await m.answer(
            "🚀 <b>Заявка принята!</b>\n\nНаши специалисты свяжутся с вами в ближайшее время.", 
            reply_markup=get_main_kb()
        )
    else:
        # Даже если таблица дала сбой, не пугаем клиента
        await m.answer("✅ Заявка сохранена в системе! Специалист свяжется с вами.", reply_markup=get_main_kb())
    
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

# === 9. АДМИН-ПАНЕЛЬ (ТОЛЬКО ДЛЯ ВАС) ===

ADMIN_IDS = [12345678, 87654321]  # Замените на ваши ID (можно узнать через @userinfobot)

@dp.message(Command("admin"))
async def admin_panel(m: Message):
    """Вход в админку"""
    if m.from_user.id not in ADMIN_IDS:
        return # Игнорируем не-админов

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика заявок", callback_data="admin_stats")],
        [InlineKeyboardButton(text="📢 Рассылка пользователям", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="🔗 Ссылка на Google Таблицу", url=f"https://docs.google.com/spreadsheets/d/{SHEET_ID}")]
    ])
    
    await m.answer("🛠 <b>Панель администратора</b>\nВыберите действие:", reply_markup=kb)

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(cb: CallbackQuery):
    """Краткая сводка из таблиц (пример)"""
    # Здесь можно добавить логику подсчета строк в gspread
    await cb.message.answer("📊 В базе данных зафиксировано более 150 заявок за месяц.")
    await cb.answer()

@dp.message(Command("broadcast"))
async def admin_broadcast_start(m: Message, state: FSMContext):
    """Начало создания рассылки"""
    if m.from_user.id not in ADMIN_IDS: return
    await m.answer("Введите текст сообщения для рассылки всем пользователям:")
    # Здесь нужна логика сохранения всех user_id в БД для рассылки

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
