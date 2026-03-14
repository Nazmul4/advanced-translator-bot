import os
import logging
import requests
import tempfile
import google.generativeai as genai
from telegram import Update
from telegram.ext import Application, MessageHandler, CommandHandler, filters, ContextTypes

# ==================== কনফিগারেশন ====================

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

# ✅ একাধিক Gemini API Key সাপোর্ট
GEMINI_KEYS = [
    k.strip()
    for k in [
        os.environ.get("GEMINI_API_KEY_1", ""),
        os.environ.get("GEMINI_API_KEY_2", ""),
        os.environ.get("GEMINI_API_KEY_3", ""),
        os.environ.get("GEMINI_API_KEY_4", ""),
        os.environ.get("GEMINI_API_KEY_5", ""),
    ]
    if k.strip()
]

if not GEMINI_KEYS:
    single_key = os.environ.get("GEMINI_API_KEY", "")
    if single_key:
        GEMINI_KEYS = [single_key]

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# মডেল নির্ধারণ
TEXT_MODEL  = "gemini-2.5-flash-lite"  # টেক্সট — 1000 RPD
MEDIA_MODEL = "gemini-2.5-flash"       # ইমেজ/অডিও/ভিডিও — 500 RPD


# ==================== KEY রোটেশন ম্যানেজার ====================

class GeminiKeyManager:
    def __init__(self, keys):
        self.keys = keys
        self.index = 0
        self.failed = set()

    def get_key(self):
        available = [k for i, k in enumerate(self.keys) if i not in self.failed]
        if not available:
            self.failed.clear()
            available = self.keys
        key = available[self.index % len(available)]
        self.index += 1
        return key

    def mark_failed(self, key):
        if key in self.keys:
            self.failed.add(self.keys.index(key))
            logger.warning(f"Key #{self.keys.index(key)+1} quota শেষ, পরের Key-তে যাচ্ছি")

key_manager = GeminiKeyManager(GEMINI_KEYS)


def call_gemini(model_name: str, content) -> str:
    """Quota শেষ হলে আপনাআপনি পরের Key-তে যায়"""
    max_attempts = max(len(GEMINI_KEYS) * 2, 3)
    last_error = None

    for attempt in range(max_attempts):
        api_key = key_manager.get_key()
        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(content)
            return response.text.strip()
        except Exception as e:
            last_error = e
            err = str(e)
            if "429" in err or "quota" in err.lower() or "limit" in err.lower():
                key_manager.mark_failed(api_key)
                continue
            raise

    raise Exception(f"সব Key ব্যর্থ! শেষ ত্রুটি: {last_error}")


# ==================== অনুবাদ ফাংশন ====================

def translate_text(text: str) -> str:
    """টেক্সট → বাংলা | gemini-2.5-flash-lite"""
    prompt = (
        "নিচের টেক্সটটি বাংলায় অনুবাদ করো। "
        "শুধু অনুবাদ দাও, কোনো ব্যাখ্যা লিখবে না। "
        "যদি ইতিমধ্যে বাংলায় থাকে তাহলে হুবহু ফেরত দাও।\n\n"
        f"{text}"
    )
    return call_gemini(TEXT_MODEL, prompt)


def translate_image(file_path: str, caption: str = "") -> str:
    """ইমেজের টেক্সট পড়ে বাংলায় অনুবাদ | gemini-2.5-flash"""
    try:
        with open(file_path, "rb") as f:
            image_data = f.read()

        # ইমেজের ধরন বোঝার জন্য extension চেক
        ext = file_path.split(".")[-1].lower()
        mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/jpeg")

        if caption:
            prompt = (
                f"এই ইমেজে ক্যাপশন আছে: '{caption}'\n\n"
                "১. ইমেজে যদি কোনো টেক্সট থাকে সেটা বাংলায় অনুবাদ করো।\n"
                "২. ক্যাপশনটিও বাংলায় অনুবাদ করো।\n"
                "৩. ইমেজটি সংক্ষেপে বর্ণনা করো।\n\n"
                "ফরম্যাট:\n"
                "📝 ইমেজের টেক্সট: [অনুবাদ বা 'কোনো টেক্সট নেই']\n"
                "📌 ক্যাপশন: [অনুবাদ]\n"
                "🖼️ বর্ণনা: [সংক্ষিপ্ত বর্ণনা]"
            )
        else:
            prompt = (
                "এই ইমেজটি বিশ্লেষণ করো:\n\n"
                "১. ইমেজে যদি কোনো টেক্সট/লেখা থাকে সেটা বাংলায় অনুবাদ করো।\n"
                "২. ইমেজটি সংক্ষেপে বাংলায় বর্ণনা করো।\n\n"
                "ফরম্যাট:\n"
                "📝 ইমেজের টেক্সট: [অনুবাদ বা 'কোনো টেক্সট নেই']\n"
                "🖼️ বর্ণনা: [সংক্ষিপ্ত বাংলা বর্ণনা]"
            )

        content = [{"mime_type": mime_type, "data": image_data}, prompt]
        return call_gemini(MEDIA_MODEL, content)
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def transcribe_and_translate(file_path: str, mime_type: str) -> str:
    """অডিও/ভিডিও → বাংলা | gemini-2.5-flash"""
    try:
        with open(file_path, "rb") as f:
            file_data = f.read()

        prompt = (
            "এই অডিও/ভিডিওতে যা বলা হয়েছে তা সম্পূর্ণ বাংলায় অনুবাদ করো।\n\n"
            "ফরম্যাট:\n"
            "মূল ভাষা: [ভাষার নাম]\n\n"
            "বাংলা অনুবাদ:\n[অনুবাদ এখানে]"
        )
        content = [{"mime_type": mime_type, "data": file_data}, prompt]
        return call_gemini(MEDIA_MODEL, content)
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


def download_file(file_url: str, suffix: str) -> str:
    r = requests.get(file_url, timeout=60)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(r.content)
    tmp.close()
    return tmp.name


# ==================== Telegram হ্যান্ডলার ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"✅ অনুবাদ বট চালু!\n"
        f"🔑 {len(GEMINI_KEYS)}টি API Key সক্রিয়\n\n"
        "যা পাঠাতে পারবেন:\n"
        "📝 টেক্সট → বাংলা অনুবাদ\n"
        "🖼️ ইমেজ → টেক্সট পড়ে বাংলায় অনুবাদ\n"
        "🎙️ ভয়েস/অডিও → বাংলা অনুবাদ\n"
        "🎬 ভিডিও → বাংলা অনুবাদ\n\n"
        "📌 টেক্সট: gemini-2.5-flash-lite\n"
        "📌 ইমেজ/মিডিয়া: gemini-2.5-flash"
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message:
        return
    text = message.text or message.caption
    if not text or not text.strip():
        return

    status_msg = await message.reply_text("⏳ অনুবাদ করছি...")
    try:
        result = translate_text(text)
        await status_msg.edit_text(f"🇧🇩 বাংলা:\n\n{result}")
    except Exception as e:
        await status_msg.edit_text(f"❌ ব্যর্থ: {str(e)[:150]}")


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ইমেজ হ্যান্ডলার"""
    message = update.message or update.channel_post
    if not message:
        return

    photo = message.photo
    if not photo:
        return

    status_msg = await message.reply_text("🖼️ ইমেজ বিশ্লেষণ করছি...")
    try:
        # সবচেয়ে বড় (ভালো মানের) ছবি নাও
        largest_photo = max(photo, key=lambda p: p.file_size or 0)
        file = await context.bot.get_file(largest_photo.file_id)
        path = download_file(file.file_path, ".jpg")

        caption = message.caption or ""
        result = translate_image(path, caption)
        await status_msg.edit_text(f"🖼️ ইমেজ অনুবাদ:\n\n{result}")
    except Exception as e:
        await status_msg.edit_text(f"❌ ব্যর্থ: {str(e)[:150]}")


async def handle_document_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Document হিসেবে পাঠানো ইমেজ হ্যান্ডলার"""
    message = update.message or update.channel_post
    if not message or not message.document:
        return

    doc = message.document
    mime = doc.mime_type or ""

    if not mime.startswith("image/"):
        return

    status_msg = await message.reply_text("🖼️ ইমেজ বিশ্লেষণ করছি...")
    try:
        file = await context.bot.get_file(doc.file_id)
        ext_map = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
        suffix = ext_map.get(mime, ".jpg")
        path = download_file(file.file_path, suffix)

        caption = message.caption or ""
        result = translate_image(path, caption)
        await status_msg.edit_text(f"🖼️ ইমেজ অনুবাদ:\n\n{result}")
    except Exception as e:
        await status_msg.edit_text(f"❌ ব্যর্থ: {str(e)[:150]}")


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message:
        return
    audio = message.audio or message.voice
    if not audio:
        return

    status_msg = await message.reply_text("🎙️ অডিও প্রসেস করছি...")
    try:
        file = await context.bot.get_file(audio.file_id)
        suffix = ".mp3" if message.audio else ".ogg"
        mime   = "audio/mp3" if message.audio else "audio/ogg"
        path   = download_file(file.file_path, suffix)
        result = transcribe_and_translate(path, mime)
        await status_msg.edit_text(f"🎙️ অডিও অনুবাদ:\n\n{result}")
    except Exception as e:
        await status_msg.edit_text(f"❌ ব্যর্থ: {str(e)[:150]}")


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message or update.channel_post
    if not message:
        return
    video = message.video or message.video_note
    if not video:
        return

    if message.caption:
        status_msg = await message.reply_text("⏳ ক্যাপশন অনুবাদ করছি...")
        try:
            result = translate_text(message.caption)
            await status_msg.edit_text(f"📝 ক্যাপশন:\n\n{result}")
        except Exception as e:
            await status_msg.edit_text(f"❌ ব্যর্থ: {str(e)[:150]}")
        return

    status_msg = await message.reply_text("🎬 ভিডিও প্রসেস করছি...")
    try:
        file = await context.bot.get_file(video.file_id)
        path = download_file(file.file_path, ".mp4")
        result = transcribe_and_translate(path, "video/mp4")
        await status_msg.edit_text(f"🎬 ভিডিও অনুবাদ:\n\n{result}")
    except Exception as e:
        await status_msg.edit_text(f"❌ ব্যর্থ: {str(e)[:150]}")


# ==================== মেইন ====================

def main():
    if not GEMINI_KEYS:
        logger.error("কোনো Gemini API Key পাওয়া যায়নি!")
        return

    logger.info(f"বট চালু | Keys: {len(GEMINI_KEYS)} | টেক্সট: {TEXT_MODEL} | মিডিয়া: {MEDIA_MODEL}")

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document_image))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE, handle_audio))
    app.add_handler(MessageHandler(filters.VIDEO | filters.VIDEO_NOTE, handle_video))
    app.add_handler(MessageHandler(filters.CAPTION & ~filters.COMMAND & ~filters.PHOTO, handle_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
