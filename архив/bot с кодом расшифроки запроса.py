import asyncio
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

import aiohttp
import aiosqlite
import whisper
import logging

TELEGRAM_TOKEN = "7238748055:AAHbVcV0lXL-odepWAb2QE6PB1Mi9g6eT1w"
OPENROUTER_API_KEY = "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"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

model = whisper.load_model("base")
DB_PATH = "stats.db"
user_histories = {}
MAX_HISTORY = 3
ANTI_SPAM_SECONDS = 10
user_last_message = {}

logging.basicConfig(level=logging.INFO)

def get_reply_button():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Задать вопрос снова", callback_data="repeat")]
    ])
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
            ON CONFLICT(user_id) DO UPDATE SET
                messages = messages + 1,
                last_seen = datetime('now')
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

import logging
import aiohttp

async def ask_gpt(prompt, user_id):
    history = user_histories.get(user_id, [])
    history.append({"role": "user", "content": prompt})
    if len(history) > MAX_HISTORY * 2:
        history = history[-MAX_HISTORY*2:]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": "openai/gpt-3.5-turbo",
                    "messages": history
                },
                headers={
                    "Authorization": "Bearer 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"
                }
            ) as resp:
                data = await resp.json()
                logging.info(f"API response: {data}")
                
                if "choices" not in data:
                    # Вернуть полный ответ как текст — чтобы увидеть ошибку
                    return f"⚠️ Ошибка API:\n{data}"
                
                reply = data["choices"][0]["message"]["content"]
                history.append({"role": "assistant", "content": reply})
                user_histories[user_id] = history
                return reply
    except Exception as e:
        logging.error(f"GPT connection error: {e}")
        return "⚠️ Ошибка соединения с GPT."

@dp.message(Command(commands=["start"]))
async def start_handler(message: types.Message):
    await message.answer(
        "Привет! Отправь мне текст или голосовое сообщение, я отвечу через GPT."
    )

@dp.message(lambda message: message.text is not None)
async def text_handler(message: types.Message):
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

@dp.message(lambda message: message.content_type == "voice")
async def voice_handler(message: types.Message):
    uid = message.from_user.id
    name = message.from_user.username or "без_имени"
    file_id = message.voice.file_id
    file = await bot.get_file(file_id)
    voice_path = f"voice_{uid}.ogg"
    await bot.download_file(file.file_path, voice_path)

    await update_stats(uid, name)
    if is_spamming(uid):
        await message.reply("⏳ Подожди немного...")
        try:
            os.remove(voice_path)
        except Exception:
            pass
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
    except Exception as e:
        logging.error(f"Whisper error: {e}")
        await message.answer("⚠️ Не удалось распознать речь.")
        try:
            os.remove(voice_path)
        except Exception:
            pass

@dp.callback_query(lambda c: c.data == "repeat")
async def repeat_handler(callback: types.CallbackQuery):
    await callback.answer()
    await bot.send_message(callback.from_user.id, "✍️ Введи вопрос:")

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
