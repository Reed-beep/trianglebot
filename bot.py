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

# === НАСТРОЙКИ ===
TELEGRAM_TOKEN = "7238748055:AAHbVcV0lXL-odepWAb2QE6PB1Mi9g6eT1w"
OPENROUTER_API_KEY = "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"

# === Глобальные переменные ===
model = None
DB_PATH = "stats.db"
user_histories = {}
user_settings = {}  # user_id -> dict: model, history_depth, style
MAX_HISTORY = 2
ANTI_SPAM_SECONDS = 10
user_last_message = {}

logging.basicConfig(level=logging.INFO)

# === КНОПКИ ===
def get_reply_button():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="↩️ Задать вопрос снова", callback_data="repeat")]
    ])
    return kb

# === ЛОГГИРОВАНИЕ ===
def log_message(user_id, username, text):
    with open("chat_log.txt", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now()} | {user_id} | {username} | {text}\n")

# === ОБНОВЛЕНИЕ СТАТИСТИКИ ===
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

# === АНТИСПАМ ===
def is_spamming(user_id):
    now = datetime.now()
    last = user_last_message.get(user_id)
    if last and (now - last).total_seconds() < ANTI_SPAM_SECONDS:
        return True
    user_last_message[user_id] = now
    return False

# === GPT-запрос через OpenRouter ===
async def ask_gpt(prompt, user_id):
    settings = user_settings.get(user_id, {
        "model": "anthropic/claude-3-haiku",
        "history_depth": 2,
        "style": "default"
    })

    history = user_histories.get(user_id, [])
    history.append({"role": "user", "content": prompt})
    if len(history) > settings["history_depth"] * 2:
        history = history[-settings["history_depth"] * 2:]

    if settings["style"] == "short":
        prompt += "\n\nОтветь кратко."
    elif settings["style"] == "detailed":
        prompt += "\n\nОтветь развернуто с пояснением."

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json={
                    "model": settings["model"],
                    "messages": history
                },
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "HTTP-Referer": "https://t.me/Trianglejonbot",
                    "X-Title": "TriangleGPTBot"
                }
            ) as resp:
                data = await resp.json()
                if "choices" not in data:
                    logging.error(f"OpenRouter error response: {data}")
                    return "⚠️ GPT вернул ошибку. Проверь модель, ключ или регион."

                reply = data["choices"][0]["message"]["content"]
                history.append({"role": "assistant", "content": reply})
                user_histories[user_id] = history
                return reply
    except Exception as e:
        logging.error(f"GPT connection error: {e}")
        return "⚠️ Ошибка соединения с GPT."

async def main():
    global model

    # Создаём и устанавливаем event loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Инициализация бота и диспетчера внутри main
    bot = Bot(token=TELEGRAM_TOKEN)
    dp = Dispatcher()

    # Загружаем модель Whisper
    model = whisper.load_model("base")

    # Инициализируем базу данных
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

    # === Регистрируем хэндлеры ===
    @dp.message(Command(commands=["start"]))
    async def start_handler(message: types.Message):
        await message.answer("Привет! Отправь мне текст или голосовое сообщение, я отвечу через GPT.")

    @dp.message(Command(commands=["settings"]))
    async def settings_handler(message: types.Message):
        uid = message.from_user.id
        settings = user_settings.get(uid, {
            "model": "anthropic/claude-3-haiku",
            "history_depth": 2,
            "style": "default"
        })

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🧠 Модель", callback_data="set_model"),
                InlineKeyboardButton(text="🔁 История", callback_data="set_history"),
                InlineKeyboardButton(text="✍️ Стиль", callback_data="set_style"),
            ]
        ])
        text = (
            f"⚙️ Текущие настройки:\n"
            f"Модель: `{settings['model']}`\n"
            f"Глубина истории: `{settings['history_depth']}`\n"
            f"Тип ответа: `{settings['style']}`"
        )
        await message.answer(text, reply_markup=kb, parse_mode="Markdown")

    @dp.callback_query(lambda c: c.data == "repeat")
    async def repeat_handler(callback: types.CallbackQuery):
        await callback.answer()
        await bot.send_message(callback.from_user.id, "✍️ Введи вопрос:")

    @dp.callback_query(lambda c: c.data.startswith("set_"))
    async def settings_callback(callback: types.CallbackQuery):
        uid = callback.from_user.id
        user_settings.setdefault(uid, {
            "model": "anthropic/claude-3-haiku",
            "history_depth": 2,
            "style": "default"
        })

        action = callback.data
        if action == "set_model":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Claude-3-Haiku", callback_data="model_claude")],
                [InlineKeyboardButton(text="GPT-4", callback_data="model_gpt")],
                [InlineKeyboardButton(text="Mistral", callback_data="model_mistral")]
            ])
            await callback.message.answer("Выберите модель:", reply_markup=kb)
        elif action == "set_history":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="2", callback_data="hist_2"),
                 InlineKeyboardButton(text="5", callback_data="hist_5"),
                 InlineKeyboardButton(text="10", callback_data="hist_10")]
            ])
            await callback.message.answer("Выберите глубину истории:", reply_markup=kb)
        elif action == "set_style":
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="По умолчанию", callback_data="style_default")],
                [InlineKeyboardButton(text="Кратко", callback_data="style_short")],
                [InlineKeyboardButton(text="Развернуто", callback_data="style_detailed")]
            ])
            await callback.message.answer("Выберите стиль ответа:", reply_markup=kb)
        await callback.answer()

    @dp.callback_query(lambda c: c.data.startswith(("model_", "hist_", "style_")))
    async def apply_setting(callback: types.CallbackQuery):
        uid = callback.from_user.id
        settings = user_settings.setdefault(uid, {
            "model": "anthropic/claude-3-haiku",
            "history_depth": 2,
            "style": "default"
        })

        data = callback.data
        if data.startswith("model_"):
            model_key = data.split("_", 1)[1]
            model_map = {
                "claude": "anthropic/claude-3-haiku",
                "gpt": "openai/gpt-4",
                "mistral": "mistralai/mistral-7b-instruct"
            }
            settings["model"] = model_map.get(model_key, settings["model"])
            await callback.message.answer(f"✅ Модель установлена: `{settings['model']}`", parse_mode="Markdown")
        elif data.startswith("hist_"):
            settings["history_depth"] = int(data.split("_")[1])
            await callback.message.answer(f"✅ Глубина истории установлена: `{settings['history_depth']}`", parse_mode="Markdown")
        elif data.startswith("style_"):
            settings["style"] = data.split("_")[1]
            await callback.message.answer(f"✅ Стиль ответа установлен: `{settings['style']}`", parse_mode="Markdown")

        await callback.answer()

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

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
