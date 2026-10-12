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
        "I am your automated **AI Video Clipping Bot**. "
        "Send me a long YouTube link, and I'll slice it into **4-6 viral vertical clips** with cropping!\n\n"
        "Choose an option below to get started:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "help")
async def process_help(callback: CallbackQuery):
    help_text = (
        "📖 **How to use this bot:**\n\n"
        "1. Select 'Clip a Video'.\n"
        "2. Bypass/Pay the test invoice.\n"
        "3. Paste your YouTube link and receive 4-6 vertical shorts!"
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
            "Payment bypassed automatically. Please **paste any YouTube link** below to generate 4-6 shorts!",
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
        title="AI Video Clipper Pass (4-6 Clips)",
        description="Generates multiple 9:16 vertical short clips from a YouTube link.",
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
    await message.answer(
        "🎉 **Payment Successful!**\n\n"
        "Credits unlocked! Now, please **paste your YouTube link** below to generate your clips.",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(F.text.contains("youtube.com") | F.text.contains("youtu.be"))
async def handle_youtube_link(message: Message):
    user_id = message.from_user.id
    
    if user_id != DEV_ID and user_id not in paid_users:
        await message.answer("⚠️ Please complete the payment or use developer access by tapping `/start` first!")
        return

    url = message.text.strip()
    status_msg = await message.answer("📥 Analyzing video with Android client `yt-dlp`...")

    try:
        ydl_opts = {
            'format': 'bestvideo[height<=720]+bestaudio/best[height<=720]',
            'quiet': True,
            'extractor_args': {'youtube': {'player_client': ['android']}}
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Video')
            duration = info.get('duration', 0)
            webpage_url = info.get('webpage_url', url)

        if duration < 60:
            await status_msg.edit_text("❌ This video is too short! Please provide a video longer than 3 minutes.")
            return

        # Calculate 4 distinct start timestamps across the video
        interval = duration / 6.0
        start_times = [
            int(interval * 1),
            int(interval * 2.5),
            int(interval * 4),
            int(interval * 5)
        ]

        await status_msg.edit_text(f"✂️ Found video: *{title}*.\nGenerating 4 vertical clips via FFmpeg...")

        generated_files = []

        for idx, start_sec in enumerate(start_times, start=1):
            output_filename = f"clip_{idx}.mp4"
            
            clip_ydl_opts = {
                'format': 'best[height<=720]',
                'outtmpl': f'raw_clip_{idx}.mp4',
                'download_ranges': yt_dlp.utils.download_range_func(None, [(start_sec, start_sec + 45)]),
                'quiet': True,
                'extractor_args': {'youtube': {'player_client': ['android']}}
            }

            try:
                with yt_dlp.YoutubeDL(clip_ydl_opts) as ydl:
                    ydl.download([webpage_url])

                raw_file = f"raw_clip_{idx}.mp4"
                if os.path.exists(raw_file):
                    ffmpeg_cmd = [
                        'ffmpeg', '-y', '-i', raw_file,
                        '-vf', 'crop=ih*9/16:ih',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
                        '-c:a', 'copy',
                        output_filename
                    ]
                    subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

                    if os.path.exists(output_filename):
                        generated_files.append(output_filename)
                    
                    if os.path.exists(raw_file):
                        os.remove(raw_file)
            except Exception as sub_err:
                print(f"Error slicing clip {idx}: {sub_err}")

        if user_id != DEV_ID:
            paid_users.remove(user_id)

        if generated_files:
            await status_msg.edit_text(f"✅ Successfully generated **{len(generated_files)} vertical clips**! Sending them now...")
            for file_path in generated_files:
                video_file = FSInputFile(file_path)
                await message.answer_video(video_file, caption=f"🎬 Viral Vertical Clip from: {title[:30]}...")
                await asyncio.sleep(1)
                try:
                    os.remove(file_path)
                except:
                    pass
        else:
            await status_msg.edit_text("❌ Failed to process and crop video clips. FFmpeg or download failed.")

    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing link: `{str(e)}`")

@dp.callback_query(F.data == "main_menu")
async def back_to_menu(callback: CallbackQuery):
    await callback.message.edit_text("Welcome back to the main menu:", reply_markup=get_main_menu())
    await callback.answer()

async def main() -> None:
    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await dp.start_polling(bot)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    asyncio.run(main())
