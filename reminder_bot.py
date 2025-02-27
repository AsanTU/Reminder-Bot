import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import logging
import asyncio
import pytz

logging.basicConfig(level=logging.INFO)

API_TOKEN = "7561419022:AAEcftzg_YrAHkJMMbxAuZagCJqH_AAXd9s"

TIMEZONES = list(pytz.all_timezones)

COUNTRY_TIMEZONES = {
    "Россия": "Europe/Moscow",
    "США": "America/New_York",
    "Казахстан": "Asia/Almaty",
    "Киргизия": "Asia/Bishkek",
    "Германия": "Europe/Berlin"
}

# Создаем бота и диспетчер
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# --- Работа с БД ---
class Database:
    def __init__(self, db_name="reminders.db"):
        self.db_name = db_name
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        """Создаем таблицу для хранения напоминаний."""
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                remind_time TEXT,
                text TEXT,
                status TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT DEFAULT 'UTC'
            )
        ''')
        self.conn.commit()

        self.cursor.execute("PRAGMA table_info(users)")
        columns = [column[1] for column in self.cursor.fetchall()]
        if "timezone" not in columns:
            self.cursor.execute("ALTER TABLE users ADD COLUMN timezone TEXT DEFAULT 'UTC'")
            self.conn.commit()

    def add_reminder(self, chat_id, remind_time, text):
        """Добавляем новое напоминание в базу данных."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO reminders (chat_id, remind_time, text, status) VALUES (?, ?, ?, ?)",
                           (chat_id, remind_time, text, "pending"))
            conn.commit()

    def get_pending_reminders(self, chat_id=None):
        """Получаем все активные напоминания из базы данных."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            if chat_id:
                cursor.execute("SELECT * FROM reminders WHERE chat_id = ? AND status = 'pending'", (chat_id,))
            else:
                cursor.execute("SELECT * FROM reminders WHERE status = 'pending'")
            return cursor.fetchall()

    def update_reminder_status(self, reminder_id, status):
        """Обновляем статус напоминания (например, 'completed')."""
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
            conn.commit()

    def update_user_timezone(self, user_id, timezone):
        """Обновляет часовой пояс пользователя в базе данных"""
        query = "UPDATE users SET timezone = ? WHERE id = ?"
        self.cursor.execute(query, (timezone, user_id))
        self.connection.commit()

db = Database()

# --- FSM для пошагового создания напоминания ---
class ReminderStates(StatesGroup):
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_text = State()

# --- Отправка напоминания ---
async def send_reminder(chat_id, text, reminder_id=None):
    """Отправляем напоминание пользователю."""
    try:
        await bot.send_message(chat_id, f"🔔 Напоминание: {text}")
        if reminder_id:
            db.update_reminder_status(reminder_id, "completed")
    except Exception as e:
        logging.error(f"Ошибка отправки напоминания: {e}")

# --- Планировщик напоминаний ---
def schedule_reminders():
    """Добавляем все активные напоминания в планировщик."""
    reminders = db.get_pending_reminders()
    for reminder in reminders:
        try: 
            remind_time = datetime.strptime(reminder[2], "%Y-%m-%d %H:%M")
            scheduler.add_job(
                send_reminder, 
                DateTrigger(run_date=remind_time),
                args=[reminder[1], reminder[3], reminder[0]],
                misfire_grace_time=3600
            )
        except Exception as e:
            logging.error(f"Ошибка планирования напоминания: {e}")

def convert_to_utc(user_time: str, user_timezone: str) -> str:
    user_tz = pytz.timezone(user_timezone)
    local_time = datetime.strptime(user_time, "%Y-%m-%d %H:%M")
    local_time = user_tz.localize(local_time)
    utc_time = local_time.astimezone(pytz.utc)
    return utc_time.strftime("%Y-%m-%d %H:%M")

def convert_to_user_timezone(utc_time: str, user_timezone: str) -> str:
    user_tz = pytz.timezone(user_timezone)
    utc_dt = datetime.strptime(utc_time, "%Y-%m-%d %H:%M").replace(tzinfo=pytz.utc)
    local_dt = utc_dt.astimezone(user_tz)
    return local_dt.strftime("%Y-%m-%d %H:%M")

# --- Команды бота ---
@dp.message(Command(commands=["start"]))
async def start(message: Message):
    """Отображаем кнопку 'Добавить напоминание' и 'Удалить напоминание'."""
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Добавить напоминание"), 
                KeyboardButton(text="Удалить напоминание"), 
                KeyboardButton(text="Мои напоминания")
            ],
            [KeyboardButton(text="Выбрать часовой пояс")]
        ],
        resize_keyboard=True
    )
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=keyboard)

# --- Удаление напоминаний ---
@dp.message(lambda message: message.text == "Удалить напоминание")
async def show_reminders(message: Message):
    """Отображаем список напоминаний с кнопками для удаления."""
    reminders = db.get_pending_reminders()
    if not reminders:
        await message.answer("У вас нет активных напоминаний.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{rem[3]} ({rem[2]})", callback_data=f"delete_{rem[0]}")]
        for rem in reminders
    ])
    
    await message.answer("Выберите напоминание для удаления:", reply_markup=keyboard)

@dp.callback_query(lambda call: call.data.startswith("delete_"))
async def delete_reminder(call: CallbackQuery):
    """Удаляем выбранное напоминание."""
    reminder_id = int(call.data.split("_")[1])
    with sqlite3.connect(db.db_name) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()

    await call.answer("Напоминание удалено!")
    await call.message.edit_text("Напоминание успешно удалено.")

@dp.message(lambda message: message.text == "Добавить напоминание")
async def start_reminder(message: Message, state: FSMContext):
    """Начинаем процесс создания напоминания."""
    await message.answer("Введите дату в формате YYYY-MM-DD:")
    await state.set_state(ReminderStates.waiting_for_date)

@dp.message(lambda message: message.text == "Выбрать часовой пояс")
async def set_timezone(message: types.Message):
    countries = list(COUNTRY_TIMEZONES.keys())

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=country)] for country in countries],
        resize_keyboard=True
    )

    await message.answer("Выберите вашу страну:", reply_markup=keyboard)

@dp.message()
async def handle_timezone_selection(message: types.Message):
    country = message.text.strip()

    if country in COUNTRY_TIMEZONES:
        timezone = COUNTRY_TIMEZONES[country]
        await message.answer(f"Ваш часовой пояс установлен: {timezone}")

        db.update_reminder_status(message.chat.id, timezone)

    else:
        await message.answer("Некорректный выбор. Выберите страну из списка.")

@dp.message()
async def save_timezone(message: types.Message):
    user_timezone = message.text.strip()

    if user_timezone in TIMEZONES:
        db.update_reminder_status(message.chat.id, user_timezone)
        await message.answer(f"Ваш часовой пояс установлен на {user_timezone}.")
    else:
        await message.answer("Некорректный часовой пояс. Попробуйте ещё раз.")

# --- Получение активных напоминаний пользователя ---
@dp.message(lambda message: message.text == "Мои напоминания")
async def my_reminders(message: Message):
    """Выводит список всех активных напоминаний пользователя."""
    user_reminders = db.get_pending_reminders(message.chat.id)
    reminders = db.get_pending_reminders(message.chat.id)

    if not user_reminders:
        await message.answer("У вас нет активных напоминаний.")
        return
    
    reminders_text = "📌 Ваши активные напоминания:\n\n"
    for reminder in reminders:
        local_time = convert_to_user_timezone(r[2], user_reminders)
        reminders_text += f"🕒 {reminder[2]} - {local_time}\n"

    await message.answer(reminders_text)

@dp.message(ReminderStates.waiting_for_date)
async def input_date(message: Message, state: FSMContext):
    """Сохраняем дату напоминания."""
    try:
        date = datetime.strptime(message.text, "%Y-%m-%d")
        if date.date() < datetime.today().date():
            await message.answer("Дата не может быть в прошлом. Попробуйте снова.")
            return
        await state.update_data(remind_date=date.strftime("%Y-%m-%d"))
        await message.answer("Введите время в формате HH:MM:")
        await state.set_state(ReminderStates.waiting_for_time)
    except ValueError:
        await message.answer("Некорректный формат даты. Попробуйте снова.")

@dp.message(ReminderStates.waiting_for_time)
async def input_time(message: Message, state: FSMContext):
    """Сохраняем время напоминания."""
    try:
        time = datetime.strptime(message.text, "%H:%M").time()
        await state.update_data(remind_time=time.strftime("%H:%M"))
        await message.answer("Введите текст напоминания:")
        await state.set_state(ReminderStates.waiting_for_text)
    except ValueError:
        await message.answer("Некорректный формат времени. Попробуйте снова.")

@dp.message(ReminderStates.waiting_for_text)
async def input_text(message: Message, state: FSMContext):
    """Сохраняем текст напоминания и планируем его."""
    user_data = await state.get_data()
    remind_datetime = f"{user_data['remind_date']} {user_data['remind_time']}"

    try:
        remind_text_dt = datetime.strptime(remind_datetime, "%Y-%m-%d %H:%M")
        if remind_text_dt < datetime.now():
            await message.answer("Время не может быть в прошлом. Попробуйте снова.")
            return
    except ValueError:
        await message.answer("Ошибка обработки даты. Попробуйте снова.")
        return
    
    db.add_reminder(message.chat.id, remind_datetime, message.text)
    scheduler.add_job(send_reminder, DateTrigger(run_date=remind_text_dt), args=[message.chat.id, message.text])

    await message.answer(f"✅ Напоминание добавлено: {message.text} в {remind_datetime}.")
    await state.clear()

# Запуск бота
async def main():
    schedule_reminders()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
