import os
import logging
import requests
import tempfile
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
GEMINI_API_KEY     = os.environ.get("GEMINI_API_KEY")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")


# ==================== টেক্সট অনুবাদ ====================
def translate_text(text):
    try:
        prompt = (
            "নিচের টেক্সটটি বাংলায় অনুবাদ করো। "
            "শুধু অনুবাদ দাও, কোনো ব্যাখ্যা লিখবে না। "
            "যদি ইতিমধ্যে বাংলায় থাকে তাহলে হুবহু ফেরত দাও।\n\n"
            f"{text}"
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"টেক্সট অনুবাদ ত্রুটি: {e}")
        return None


# ==================== অডিও/ভিডিও প্রসেস ====================
def download_file(file_url, suffix):
    """Telegram থেকে ফাইল ডাউনলোড করে"""
    response = requests.get(file_url, timeout=60)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(response.content)
    tmp.close()
    return tmp.name


def transcribe_and_translate(file_path, mime_type):
    """Gemini দিয়ে অডিও/ভিডিও থেকে বাংলা টেক্সট বের করে"""
    try:
        with open(file_path, "rb") as f:
            file_data = f.read()

        prompt = (
            "এই অডিও/ভিডিওতে যা বলা হয়েছে তা সম্পূর্ণ বাংলায় অনুবাদ করো। "
            "প্রথমে মূল ভাষায় কী বলা হয়েছে তা সংক্ষেপে লেখো, "
            "তারপর সম্পূর্ণ বাংলা অনুবাদ দাও। "
            "ফরম্যাট:\n"
            "মূল ভাষা: [ভাষার নাম]\n\n"
            "বাংলা অনুবাদ:\n[অনুবাদ এখানে]"
        )

        response = model.generate_content([
            {"mime_type": mime_type, "data": file_data},
            prompt
        ])
        return response.text.strip()
    except Exception as e:
        logger.error(f"অডিও/ভিডিও প্রসেস ত্রুটি: {e}")
        return None
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def translate_subtitle(text):
    """সাবটাইটেল টেক্সট বাংলায় অনুবাদ করে"""
    try:
        prompt = (
            "এটি একটি ভিডিওর সাবটাইটেল বা ক্যাপশন। "
            "বাংলায় অনুবাদ করো, শুধু অনুবাদ দাও।\n\n"
            f"{text}"
        )
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"সাবটাইটেল অনুবাদ ত্রুটি: {e}")
        return None


# ==================== হ্যান্ডলার ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "অনুবাদ বট চালু আছে!\n\n"
        "যা পাঠাতে পারবেন:\n"
        "টেক্সট — যেকোনো ভাষা থেকে বাংলা\n"
        "অডিও — স্পিচ থেকে বাংলা টেক্সট\n"
        "ভয়েস মেসেজ — বাংলায় অনুবাদ\n"
        "ভিডিও — অডিও থেকে বাংলা টেক্সট\n"
        "ভিডিও নোট — বাংলায় অনুবাদ\n"
        "ক্যাপশন সহ মিডিয়া — ক্যাপশন অনুবাদ\n\n"
        "Gemini AI দ্বারা চালিত"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message:
        return
    text = message.text or message.caption
    if not text or not text.strip():
        return

    status_msg = await message.reply_text("অনুবাদ করছি...")
    translated = translate_text(text)
    if translated:
        await status_msg.edit_text(f"বাংলা অনুবাদ:\n\n{translated}")
    else:
        await status_msg.edit_text("অনুবাদ করতে সমস্যা হয়েছে।")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message:
        return

    # অডিও বা ভয়েস মেসেজ
    audio = message.audio or message.voice
    if not audio:
        return

    status_msg = await message.reply_text("অডিও প্রসেস করছি...")

    try:
        file = await context.bot.get_file(audio.file_id)
        suffix = ".mp3" if message.audio else ".ogg"
        mime = "audio/mp3" if message.audio else "audio/ogg"
        file_path = download_file(file.file_path, suffix)
        result = transcribe_and_translate(file_path, mime)

        if result:
            await status_msg.edit_text(f"অডিও অনুবাদ:\n\n{result}")
        else:
            await status_msg.edit_text("অডিও প্রসেস করতে সমস্যা হয়েছে।")
    except Exception as e:
        logger.error(f"অডিও হ্যান্ডলার ত্রুটি: {e}")
        await status_msg.edit_text("অডিও প্রসেস করতে সমস্যা হয়েছে।")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message:
        return

    video = message.video or message.video_note
    if not video:
        return

    # ভিডিওর ক্যাপশন থাকলে সেটা অনুবাদ করো
    if message.caption:
        status_msg = await message.reply_text("ক্যাপশন অনুবাদ করছি...")
        translated = translate_subtitle(message.caption)
        if translated:
            await status_msg.edit_text(f"ক্যাপশন অনুবাদ:\n\n{translated}")
        else:
            await status_msg.edit_text("ক্যাপশন অনুবাদ করতে সমস্যা হয়েছে।")
        return

    # ভিডিওর অডিও অনুবাদ
    status_msg = await message.reply_text("ভিডিও প্রসেস করছি... (একটু সময় লাগবে)")

    try:
        file = await context.bot.get_file(video.file_id)
        file_path = download_file(file.file_path, ".mp4")
        result = transcribe_and_translate(file_path, "video/mp4")

        if result:
            await status_msg.edit_text(f"ভিডিও অনুবাদ:\n\n{result}")
        else:
            await status_msg.edit_text("ভিডিও প্রসেস করতে সমস্যা হয়েছে।")
    except Exception as e:
        logger.error(f"ভিডিও হ্যান্ডলার ত্রুটি: {e}")
        await status_msg.edit_text("ভিডিও প্রসেস করতে সমস্যা হয়েছে।")


# ==================== মেইন ====================
def main():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))

    # টেক্সট ও ক্যাপশন
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # অডিও ও ভয়েস
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE, handle_audio))

    # ভিডিও ও ভিডিও নোট
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))

    # ক্যাপশন সহ মিডিয়া (ছবি, ডকুমেন্ট)
    app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND, handle_text))

    logger.info("বট চালু হচ্ছে... (Gemini AI - Audio/Video Support)")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
