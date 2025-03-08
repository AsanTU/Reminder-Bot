import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types, Router, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.markdown import hbold
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import logging
import asyncio
import pytz
import os

logging.basicConfig(level=logging.INFO)

API_TOKEN = "7561419022:AAEcftzg_YrAHkJMMbxAuZagCJqH_AAXd9s"

TIMEZONES = list (map(str.capitalize, pytz.all_timezones))

COUNTRY_TIMEZONES = {
    "Россия": ["Europe/Moscow", "Asia/Yekaterinburg", "Asia/Krasnoyarsk"],
    "США": ["America/New_York", "America/Chicago", "America/Los_Angeles"],
    "Казахстан": ["Asia/Almaty", "Asia/Aqtobe"],
    "Кыргызстан": ["Asia/Bishkek"],
}

VOICE_DIR = "voices"
os.makedirs(VOICE_DIR, exist_ok=True)

user_messages = {}

bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

scheduler = AsyncIOScheduler()

class Database:
    def __init__(self, db_name="reminders.db"):
        self.db_name = db_name
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                remind_time TEXT,
                text TEXT DEFAULT NULL,
                status TEXT,
                voice_file_id TEXT
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                timezone TEXT DEFAULT 'UTC'
            )
        ''')
        self.conn.commit()

        self.cursor.execute("PRAGMA table_info(reminders)")
        columns = [column[1] for column in self.cursor.fetchall()]
        if "voice_file_id" not in columns:
            self.cursor.execute("ALTER TABLE reminders ADD COLUMN voice_file_id TEXT")
            self.conn.commit()


    def add_reminder(self, chat_id, remind_datetime, text=None, voice_file_id=None):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO reminders (chat_id, remind_datetime, text, status, voice_file_id)
                VALUES (?, ?, ?, ?, ?)
            """, (chat_id, remind_datetime, text if text else None, "pending", voice_file_id))
            conn.commit()

    def get_pending_reminders(self, chat_id=None):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            if chat_id:
                cursor.execute("SELECT * FROM reminders WHERE chat_id = ? AND status = 'pending'", (chat_id,))
            else:
                cursor.execute("SELECT * FROM reminders WHERE status = 'pending'")
            return cursor.fetchall()

    def update_reminder_status(self, reminder_id, status):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
            conn.commit()

    def update_reminder_text(self, reminder_id, new_text):
        with sqlite3.connect(self.db_name) as conn:
            cursor = conn.cursor()
            if new_text:
                cursor.execute("UPDATE reminders SET text = ?, voice_file_id = NULL WHERE id = ?", (new_text, reminder_id))
            conn.commit()

    def update_user_timezone(self, user_id, timezone):
        query = "UPDATE users SET timezone = ? WHERE user_id = ?"
        self.cursor.execute(query, (timezone, user_id))
        self.conn.commit()
        self.conn.close()

    def get_reminder_by_id(self, reminder_id):
        self.cursor.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return self.cursor.fetchone()

    def delete_reminder(self, reminder_id):
        self.cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        self.conn.commit()

    def update_reminder_text(self, reminder_id, new_text):
        if new_text:
            self.cursor.execute("UPDATE reminders SET text = ?, voice_file_id = NULL WHERE id = ?", (new_text, reminder_id))
        self.conn.commit()

    def update_reminder_voice(self, reminder_id, voice_file_id):
        self.cursor.execute("UPDATE reminders SET voice_file_id = ?, text = NULL WHERE id = ?", (voice_file_id, reminder_id))
        self.conn.commit()

    def get_expired_reminders(self, now):
        self.cursor.execute("SELECT user_id, text, time FROM reminders WHERE time < ? AND sent = 0", (now,))
        return self.cursor.fetchall()

    def mark_reminder_as_sent(self, reminder_id):
        self.cursor.execute("UPDATE reminders SET sent = 1 WHERE id = ?", (reminder_id,))
        self.conn.commit()

db = Database()

class ReminderStates(StatesGroup):
    waiting_for_country = State()
    waiting_for_timezone = State()
    waiting_for_date = State()
    waiting_for_time = State()
    waiting_for_text = State()
    waiting_for_note = State()

class EditReminderState(StatesGroup):
    waiting_for_new_text = State()
    waiting_for_new_voice = State()

user_timezones = {}

async def send_reminder(chat_id, text=None, voice_file_id=None, reminder_id=None):
    if voice_file_id:
        await bot.send_voice(chat_id, voice=voice_file_id, caption="🔔 Напоминание!")
    elif text:
        await bot.send_message(chat_id, f"🔔 Напоминание: {text}")
    else:
        await bot.send_message(chat_id, "🔔 Напоминание без содержимого.")

def schedule_reminders():
    reminders = db.get_pending_reminders()
    for reminder in reminders:
        try: 
            remind_time = datetime.strptime(reminder[2], "%Y-%m-%d %H:%M")
            scheduler.add_job(
                lambda: asyncio.create_task(send_reminder(reminder[1], reminder[3], reminder[0])), 
                DateTrigger(run_date=remind_time),
                misfire_grace_time=3600
            )
        except Exception as e:
            logging.error(f"Ошибка планирования напоминания: {e}")

def convert_to_utc(user_time: str, user_timezone: str) -> str:
    try:
        user_tz = pytz.timezone(user_timezone)
        local_time = datetime.strptime(user_time, "%Y-%m-%d %H:%M")
        local_time = user_tz.localize(local_time)
        utc_time = local_time.astimezone(pytz.utc)
        return utc_time.strftime("%Y-%m-%d %H:%M")
    except Exception as e:
        print(f"Ошибка в convert_to_utc: {e}")
        return user_time

def convert_to_user_timezone(utc_time, user_timezone):
    if not isinstance(user_timezone, str):
        print(f"Ошибка: user_timezone должен быть строкой, а не {type(user_timezone)}")
        return utc_time
    
    try:
        user_tz = pytz.timezone(user_timezone)

        if isinstance(utc_time, str):
            utc_time = datetime.strptime(utc_time, "%Y-%m-%d %H:%M")
            utc_time = pytz.utc.localize(utc_time)

        return utc_time.astimezone(user_tz)
    except Exception as e:
        print(f"Ошибка в convert_to_user_timezone: {e}")
        return utc_time

@dp.message(Command(commands=["start"]))
async def start(message: types.Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="Добавить напоминание"),
                KeyboardButton(text="Мои напоминания")
            ],
            [KeyboardButton(text="Выбрать часовой пояс")]
        ],
        resize_keyboard=True
    )
    await message.answer("Добро пожаловать! Выберите действие:", reply_markup=keyboard)
    

@router.message(F.text == "Мои напоминания")
async def show_reminders(message: Message):
    user_reminders = db.get_pending_reminders(message.from_user.id)

    if not user_reminders:
        await message.answer("У вас нет активных напоминаний.")
        return
    
    for reminder in user_reminders:

        reminder_id, _, remind_time, text, _, voice_file_id = reminder

        inline_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Отметить выполненным", callback_data=f"done_{reminder_id}"),
                    InlineKeyboardButton(text="✏️ Изменить", callback_data=f"edit_{reminder_id}"),
                    InlineKeyboardButton(text="❌ Удалить", callback_data=f"delete_{reminder_id}")
                ]
            ]
        )

        if text and text != "Без текста":
            await message.answer(f"📌 {text}\n⏰ {remind_time}", reply_markup=inline_keyboard)
        elif voice_file_id:
            await message.answer_voice(voice_file_id, caption=f"⏰ {remind_time}", reply_markup=inline_keyboard)

@router.callback_query(F.data.startswith(("done_", "edit_", "delete_")))
async def reminder_action(callback_query: types.CallbackQuery, state: FSMContext):
    action, reminder_id = callback_query.data.split("_")
    reminder_id = int(reminder_id)

    reminder = db.get_reminder_by_id(reminder_id)
    if not reminder:
        await callback_query.answer("Ошибка! Напоминание не найдено.", show_alert=True)
        return
    
    _, _, remind_time, text, _, voice_file_id = reminder

    if action == "done":
        if text and text != "Без текста":
            await callback_query.message.edit_text("✅ Напоминание выполнено!")
        elif voice_file_id:
            await callback_query.message.delete()
            await callback_query.message.answer("✅ Голосовое напоминание выполнено!")

    elif action == "edit":
        await state.update_data(reminder_id=reminder_id)
        await state.set_state(EditReminderState.waiting_for_new_text)

        if text and text != "Без текста":
            await callback_query.message.edit_text("✏️ Введите новое напоминание:")

        elif voice_file_id:
            await callback_query.message.delete()
            await callback_query.message.answer("✏️ Отправьте новое голосовое сообщение или текст.")

    elif action == "delete":
        db.delete_reminder(reminder_id)
        await callback_query.message.delete()
        await callback_query.message.edit_text("❌ Напоминание удалено!")

    await callback_query.answer()

@dp.callback_query(lambda call: call.data.startswith("delete_"))
async def delete_reminder(call: CallbackQuery):
    reminder_id = int(call.data.split("_")[1])
    with sqlite3.connect(db.db_name) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()

    await call.answer("Напоминание удалено!")

    if call.message.text:
        await call.message.edit_text("Напоминание успешно удалено.")
    elif call.message.voice:
        await call.message.delete()
        await call.message.answer("Напоминание успешно удалено.")

@router.message(EditReminderState.waiting_for_new_text)
async def process_new_text(message: Message, state: FSMContext):
    user_data = await state.get_data()
    reminder_id = user_data.get("reminder_id")

    if reminder_id is None:
        await message.answer("⚠ Ошибка: не найдено напоминание для редактирования.")
        return
    
    if message.text:
        db.update_reminder_text(reminder_id, message.text)
        await message.answer("✅ Текст напоминания обновлен!")
        
    elif message.voice:
        voice_file_id = message.voice.file_id
        db.update_reminder_voice(reminder_id, voice_file_id)
        await message.answer("✅ Голосовое напоминание обновлено!")
    else:
        await message.answer("❌ Ошибка! Отправьте текстовое или голосовое сообщение.")
    
    db.update_reminder_text(reminder_id, message.text)
    await state.clear()

dp.include_router(router)

@dp.message(lambda message: message.text == "Добавить напоминание")
async def start_reminder(message: Message, state: FSMContext):
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

@dp.message(lambda message: message.text in COUNTRY_TIMEZONES)
async def choose_timezone(message: types.Message, state: FSMContext):
    country = message.text
    timezones = COUNTRY_TIMEZONES[country]

    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=tz)] for tz in timezones],
        resize_keyboard=True
    )

    await message.answer("Выберите ваш часовой пояс:", reply_markup=keyboard)

@dp.message(lambda message: message.text in pytz.all_timezones)
async def save_timezone(message: types.Message, state: FSMContext):
    user_timezone = message.text.strip()

    await state.update_data(user_timezone=user_timezone)
    await message.answer(f"Ваш часовой пояс установлен на {user_timezone}.")
    await state.set_state(ReminderStates.waiting_for_date)
    await start(message)

logging.basicConfig(level=logging.DEBUG)

@dp.message(ReminderStates.waiting_for_date)
async def input_date(message: Message, state: FSMContext):
    try:
        user_data = await state.get_data()
        user_timezone = user_data.get("user_timezone", "UTC")
        timezone = pytz.timezone(user_timezone)

        date = datetime.strptime(message.text, "%Y-%m-%d")
        date = timezone.localize(date)

        now = datetime.now(timezone)
        if date.date() < now.date():
            await message.answer("❌ Дата не может быть в прошлом. Попробуйте снова.")
            return
        
        await state.update_data(remind_date=date.strftime("%Y-%m-%d"))
        await message.answer("📅 Дата сохранена! Теперь введите время в формате HH:MM:")
        await state.set_state(ReminderStates.waiting_for_time)
    except ValueError:
        await message.answer("❌ Некорректный формат даты. Попробуйте снова.")

@dp.message(ReminderStates.waiting_for_time)
async def input_time(message: types.Message, state: FSMContext):
    try:
        user_data = await state.get_data()
        user_timezone = user_data.get("user_timezone", "UTC")
        timezone = pytz.timezone(user_timezone)

        time = datetime.strptime(message.text, "%H:%M").time()

        remind_date = user_data.get("remind_date")
        if not remind_date:
            await message.answer("❌ Сначала введите дату напоминания.")
            return
        
        remind_datetime = datetime.strptime(remind_date, "%Y-%m-%d").replace(
            hour=time.hour, minute=time.minute
        )

        remind_datetime = timezone.localize(remind_datetime)

        now = datetime.now(timezone)
        if remind_datetime < now:
            await message.answer("❌ Время не может быть в прошлом. Попробуйте снова.")
            return
        
        await state.update_data(remind_time=message.text, remind_datetime=remind_datetime.strftime("%Y-%m-%d %H:%M"))

        await message.answer("⏳ Время сохранено! Введите текст напоминания:")
        await state.set_state(ReminderStates.waiting_for_text)
    except ValueError:
        await message.answer("❌ Некорректный формат времени. Попробуйте снова.")

@dp.message(ReminderStates.waiting_for_text)
async def input_message(message: types.Message, state: FSMContext):
    if message.voice:
        voice_file_id = message.voice.file_id
        reminder_text = None
    elif message.text:
        reminder_text = message.text.strip()
        voice_file_id = None
    else:
        await message.asnwer("❌ Ошибка! Отправьте текстовое или голосовое сообщение.")
        return
    
    data = await state.get_data()
    remind_datetime_str = data.get("remind_datetime")

    if not remind_datetime_str:
        await message.answer("❌ Ошибка! Сначала введите дату и время в формате 'YYYY-MM-DD HH:MM'.")
        return

    try:
        remind_datetime = datetime.strptime(remind_datetime_str, "%Y-%m-%d %H:%M")

        logging.debug(f"✅ Запланированное напоминание: {reminder_text or 'Голосовое'} в {remind_datetime}")

        db.add_reminder(message.chat.id, remind_datetime, reminder_text, voice_file_id)

        scheduler.add_job(send_reminder, DateTrigger(run_date=remind_datetime), args=[message.chat.id, reminder_text, voice_file_id])

        await message.answer(f"✅ Напоминание добавлено!")
        await state.clear()

    except ValueError:
        await message.answer("❌ Ошибка! Не удалось обработать дату напоминания.")

async def restore_pending_reminders():
    now = datetime.now()
    expired_reminders = db.get_pending_reminders(now)

    for reminder in expired_reminders:
        user_id, text, reminder_time = reminder
        local_time = convert_to_user_timezone(reminder_time, "Europe/Moscow")

        message_text = (
            f"⏳ {hbold('Пропущенное напоминание!')}\n\n"
            f"{hbold('Текст:')} {text}\n"
            f"{hbold('Ожидалось в:')} {local_time.strftime('%Y-%m-%d %H:%M')}\n\n"
            "⚠️ Бот был отключен, поэтому напоминание не сработало вовремя."
        )

        try:
            await bot.send_message(user_id, message_text)
            db.mark_reminder_as_sent(reminder[0])
        except Exception as e:
            print(f"Ошибка при отправке напоминания {reminder[0]}: {e}")

    print("✅ Восстановление напоминаний завершено.")

async def on_startup():
    print("🔄 Проверка пропущенных напоминаний...")
    await restore_pending_reminders()

# Запуск бота
async def main():
    await on_startup()
    schedule_reminders()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
    dp.run_polling(bot)