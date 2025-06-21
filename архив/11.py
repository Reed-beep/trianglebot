import asyncio
import os
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
import aiohttp
import aiosqlite
import whisper
import logging

TELEGRAM_TOKEN = "7238748055:AAHbVcV0lXL-odepWAb2QE6PB1Mi9g6eT1w"

logging.basicConfig(level=logging.INFO)

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

model = whisper.load_model("base")
DB_PATH = "stats.db"
user_histories = {}
MAX_HISTORY = 3
ANTI_SPAM_SECONDS = 10
user_last_message = {}

def get_reply_button():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("↩️ Задать вопрос снова", callback_data="repeat"))
    return kb

def log_message(user_id, username, text):
    with open("chat_log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} | {user_id} | {username} | {text}\n")

async def update_stats(user_id, username):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, messages, last_seen)
            VALUES (?, ?, 1, datetime('now'))
            ON CONFLICT(user_id)
            DO UPDATE SET messages = messages + 1, last_seen = datetime('now')
            """,
            (user_id, username)
        )
        await db.commit()

def is_spamming(user_id):
    now = datetime.now()
    last = user_last_message.get(user_id)
    if last and (now - last).total_seconds() < ANTI_SPAM_SECONDS:
        return True
    user_last_message[user_id] = now
    return False

async def ask_gpt(prompt, user_id):
    history = user_histories.get(user_id, [])
    history.append({"role": "user", "content": prompt})
    if len(history) > MAX_HISTORY:
        history = history[-MAX_HISTORY:]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post("https://chatgpt-api.shn.hk/v1/", json={"messages": history}) as resp:
                data = await resp.json()
                reply = data["choices"][0]["message"]["content"]
                history.append({"role": "assistant", "content": reply})
                user_histories[user_id] = history
                return reply
    except Exception:
        return "⚠️ Ошибка соединения с GPT."

# Обработчик текстовых сообщений
@dp.message()
async def handle_text(message: types.Message):
    if message.text is None:
        return  # Защита на случай, если текст отсутствует

    uid = message.from_user.id
    name = message.from_user.username or "без_имени"
    log_message(uid, name, message.text)
    await update_stats(uid, name)

    if is_spamming(uid):
        await message.reply("⏳ Подожди немного...")
        return

    await message.answer("💭 Думаю...")
    reply = await ask_gpt(message.text, uid)
    await message.reply(reply, reply_markup=get_reply_button())

# Обработчик голосовых сообщений
@dp.message(content_types=types.ContentType.VOICE)
async def handle_voice(message: types.Message):
    uid = message.from_user.id
    name = message.from_user.username or "без_имени"
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    voice_path = f"voice_{uid}.ogg"
    await bot.download_file(file.file_path, voice_path)
    await update_stats(uid, name)

    if is_spamming(uid):
        await message.reply("⏳ Подожди немного...")
        return

    await message.answer("🔊 Распознаю голос...")
    try:
        result = model.transcribe(voice_path)
        os.remove(voice_path)
        recognized_text = result['text']
        log_message(uid, name, f"[voice] {recognized_text}")
        await message.answer(f"📝 Ты сказал: {recognized_text}")
        await message.answer("💭 Думаю...")
        reply = await ask_gpt(recognized_text, uid)
        await message.reply(reply, reply_markup=get_reply_button())
    except Exception:
        await message.answer("⚠️ Не удалось распознать речь.")

# Обработчик нажатия кнопки "repeat"
@dp.callback_query(lambda c: c.data == "repeat")
async def process_repeat(callback_query: types.CallbackQuery):
    await callback_query.answer()
    await bot.send_message(callback_query.from_user.id, "✍️ Введи вопрос:")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                messages INTEGER,
                last_seen TEXT
            )
        """)
        await db.commit()

async def main():
    await init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())