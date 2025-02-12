import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger
import asyncio

# Создаем бота и диспетчер
API_TOKEN = "7561419022:AAEcftzg_YrAHkJMMbxAuZagCJqH_AAXd9s"
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
scheduler = AsyncIOScheduler()

# Подключение к базе данных SQLite
def init_db():
    conn = sqlite3.connect("reminders.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER,
                        remind_time TEXT,
                        text TEXT,
                        status TEXT)''')
    conn.commit()
    conn.close()

# Функция добавления напоминания в базу данных
def add_reminder(chat_id, remind_time, text):
    conn = sqlite3.connect("reminders.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO reminders (chat_id, remind_time, text, status) VALUES (?, ?, ?, ?)",
                   (chat_id, remind_time, text, "pending"))
    conn.commit()
    conn.close()

# Отправка напоминания
async def send_reminder(chat_id, text):
    await bot.send_message(chat_id, f"Напоминание: {text}")

# Обработчик команды /remind
@dp.message(Command(commands=["remind"]))
async def remind(message: Message):
    try:
        # Пример: /remind 2025-02-06 14:00 Позвонить маме
        command_parts = message.text.split(maxsplit=3)
        if len(command_parts) < 4:
            await message.reply("Неверный формат. Используйте: /remind YYYY-MM-DD HH:MM Текст напоминания.")
            return

        date_str = command_parts[1]
        time_str = command_parts[2]
        remind_text = command_parts[3]
        remind_time = f"{date_str} {time_str}"

        # Проверка формата даты и времени
        remind_time_dt = datetime.strptime(remind_time, "%Y-%m-%d %H:%M")

        # Сохраняем напоминание в базу данных
        add_reminder(message.chat.id, remind_time, remind_text)

        # Добавляем задачу в планировщик
        scheduler.add_job(send_reminder, DateTrigger(run_date=remind_time_dt), args=[message.chat.id, remind_text])

        await message.reply(f"Напоминание добавлено: {remind_text} в {remind_time_dt}.")
    except ValueError:
        await message.reply("Ошибка: некорректный формат даты или времени. Используйте: YYYY-MM-DD HH:MM.")
    except Exception as e:
        await message.reply(f"Ошибка: {e}")

# Запуск бота
async def main():
    init_db()
    scheduler.start()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())