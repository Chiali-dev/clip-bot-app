import asyncio
import logging
import os
import sys
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import yt_dlp

from aiogram import Bot, Dispatcher, F, html
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, 
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    CallbackQuery,
    LabeledPrice,
    PreCheckoutQuery,
    FSInputFile
)

TOKEN = os.getenv("BOT_TOKEN")
DEV_ID = 1190758162

dp = Dispatcher()
paid_users = set()

# --- Tiny dummy web server to keep Render's port checker happy ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive and polling!")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()
# -----------------------------------------------------------------

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Clip a Video", callback_data="pay_per_clip")],
        [InlineKeyboardButton(text="ℹ️ How It Works", callback_data="help")]
    ])

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    user_name = message.from_user.full_name
    welcome_text = (
        f"Hello, {html.bold(user_name)}! 👋\n\n"
        "I am your automated **AI Video Clipping Bot**.\n"
        "Send me a **YouTube link** OR upload a **video file**, and I'll slice it into 4-6 vertical shorts instantly!"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "help")
async def process_help(callback: CallbackQuery):
    help_text = (
        "📖 **How to use this bot:**\n\n"
        "1. Select 'Clip a Video'.\n"
        "2. Send a YouTube link OR upload any video file.\n"
        "3. Receive your 4-6 vertical 9:16 shorts at blazing speed!"
    )
    await callback.message.edit_text(help_text, reply_markup=get_main_menu(), parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "pay_per_clip")
async def process_pay_per_clip(callback: CallbackQuery):
    user_id = callback.from_user.id
    if user_id == DEV_ID:
        paid_users.add(user_id)
        await callback.message.edit_text(
            "👑 **Developer Access Recognized!**\n\n"
            "Bypassed automatically. **Send a YouTube link or upload a video file** now!",
            parse_mode=ParseMode.MARKDOWN
        )
        await callback.answer()
        return

    await callback.message.edit_text(
        "⚡ **Pay-Per-Clip Selected**\n\n"
        "Cost: 50 Telegram Stars.\n"
        "Click below to generate your invoice:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pay 50 Stars", callback_data="send_invoice")],
            [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="main_menu")]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(F.data == "send_invoice")
async def send_demo_invoice(callback: CallbackQuery):
    prices = [LabeledPrice(label="AI Multi-Clip Generation", amount=50)]
    await callback.message.answer_invoice(
        title="AI Video Clipper Pass",
        description="Generates multiple 9:16 vertical short clips.",
        payload="clip_token_payload",
        currency="XTR",
        prices=prices,
    )
    await callback.answer()

@dp.pre_checkout_query()
async def pre_checkout_query_handler(pre_checkout_query: PreCheckoutQuery):
    await pre_checkout_query.answer(ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: Message):
    paid_users.add(message.from_user.id)
    await message.answer("🎉 **Payment Successful!** Send your YouTube link or upload a video file now.")

# --- HANDLER 1: DIRECT VIDEO FILE UPLOADS (OPTIMIZED FOR SPEED) ---
@dp.message(F.video | F.document)
async def handle_uploaded_video(message: Message):
    user_id = message.from_user.id
    if user_id != DEV_ID and user_id not in paid_users:
        await message.answer("⚠️ Please tap `/start` and select clip generation first!")
        return

    status_msg = await message.answer("📥 Downloading your uploaded video...")
    
    file_id = message.video.file_id if message.video else message.document.file_id
    file_info = await message.bot.get_file(file_id)
    local_path = "uploaded_source.mp4"
    await message.bot.download_file(file_info.file_path, local_path)

    await status_msg.edit_text("⚡ Fast-cutting uploaded video into vertical shorts...")

    try:
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', local_path]
        result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 60.0

        interval = duration / 6.0
        start_times = [int(interval * 1), int(interval * 2.5), int(interval * 4), int(interval * 5)]
        
        generated_files = []
        for idx, start_sec in enumerate(start_times, start=1):
            out_name = f"uploaded_clip_{idx}.mp4"
            # Fast input seeking (-ss before -i) for instant cutting
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-ss', str(start_sec), '-i', local_path, '-t', '30',
                '-vf', 'crop=ih*9/16:ih',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30',
                '-c:a', 'copy', out_name
            ]
            subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out_name) and os.path.getsize(out_name) > 0:
                generated_files.append(out_name)

        if user_id != DEV_ID:
            paid_users.remove(user_id)

        if os.path.exists(local_path):
            os.remove(local_path)

        if generated_files:
            await status_msg.edit_text(f"✅ Generated **{len(generated_files)} vertical clips** at lightning speed!")
            for fpath in generated_files:
                await message.answer_video(FSInputFile(fpath), caption="🎬 Vertical Cut")
                await asyncio.sleep(1)
                try:
                    os.remove(fpath)
                except:
                    pass
        else:
            await status_msg.edit_text("❌ Failed to slice the uploaded video.")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing file: `{str(e)}`")

# --- HANDLER 2: YOUTUBE LINKS ---
@dp.message(F.text.contains("youtube.com") | F.text.contains("youtu.be"))
async def handle_youtube_link(message: Message):
    user_id = message.from_user.id
    if user_id != DEV_ID and user_id not in paid_users:
        await message.answer("⚠️ Please complete payment or use developer access first via `/start`!")
        return

    url = message.text.strip()
    status_msg = await message.answer("📥 Connecting to YouTube via embedded client...")

    try:
        ydl_opts = {
            'format': 'best[height<=720]',
            'outtmpl': 'yt_source.mp4',
            'quiet': True,
            'extractor_args': {'youtube': {'player_client': ['web_embedded', 'android']}}
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'Video')
            duration = info.get('duration', 60)

        local_path = "yt_source.mp4"
        if not os.path.exists(local_path):
            raise Exception("Download payload missing.")

        interval = duration / 6.0
        start_times = [int(interval * 1), int(interval * 2.5), int(interval * 4), int(interval * 5)]

        await status_msg.edit_text(f"⚡ Fast-slicing *{title}* into vertical shorts...")

        generated_files = []
        for idx, start_sec in enumerate(start_times, start=1):
            out_name = f"yt_clip_{idx}.mp4"
            # Fast input seeking (-ss before -i)
            ffmpeg_cmd = [
                'ffmpeg', '-y', '-ss', str(start_sec), '-i', local_path, '-t', '30',
                '-vf', 'crop=ih*9/16:ih',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30',
                '-c:a', 'copy',
                out_name
            ]
            subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if os.path.exists(out_name) and os.path.getsize(out_name) > 0:
                generated_files.append(out_name)

        if user_id != DEV_ID:
            paid_users.remove(user_id)

        if os.path.exists(local_path):
            os.remove(local_path)

        if generated_files:
            await status_msg.edit_text(f"✅ Generated **{len(generated_files)} vertical clips** from YouTube!")
            for fpath in generated_files:
                await message.answer_video(FSInputFile(fpath), caption=f"🎬 {title[:25]}...")
                await asyncio.sleep(1)
                try:
                    os.remove(fpath)
                except:
                    pass
        else:
            await status_msg.edit_text("❌ Failed to process video clips.")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error: `{str(e)}`")

@dp.callback_query(F.data == "main_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text("Main Menu:", reply_markup=get_main_menu())
    await callback.answer()

async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
