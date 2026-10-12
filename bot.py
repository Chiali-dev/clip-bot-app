import asyncio
import logging
import os
import sys
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
    PreCheckoutQuery
)

TOKEN = os.getenv("BOT_TOKEN")
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

# Start the web server in a background thread
threading.Thread(target=run_web_server, daemon=True).start()
# -----------------------------------------------------------------

def get_main_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Clip a Video (Pay-Per-Use)", callback_data="pay_per_clip")],
        [InlineKeyboardButton(text="ℹ️ How It Works", callback_data="help")]
    ])

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    user_name = message.from_user.full_name
    welcome_text = (
        f"Hello, {html.bold(user_name)}! 👋\n\n"
        "I am your automated **AI Video Clipping Bot**. "
        "Send me a long YouTube link, and I'll slice it into viral vertical clips with captions.\n\n"
        "Choose an option below to get started:"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "help")
async def process_help(callback: CallbackQuery):
    help_text = (
        "📖 **How to use this bot:**\n\n"
        "1. Select 'Clip a Video'.\n"
        "2. Click 'Pay Now' to open the Telegram Stars test invoice.\n"
        "3. Confirm the test payment (zero real cost!).\n"
        "4. Paste your YouTube link and get your shorts!"
    )
    await callback.message.edit_text(help_text, reply_markup=get_main_menu(), parse_mode=ParseMode.MARKDOWN)
    await callback.answer()

@dp.callback_query(F.data == "pay_per_clip")
async def process_pay_per_clip(callback: CallbackQuery):
    await callback.message.edit_text(
        "⚡ **Pay-Per-Clip Selected**\n\n"
        "Cost: 50 Telegram Stars (Test Mode).\n"
        "Click below to generate your test invoice:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pay 50 Stars", callback_data="send_invoice")],
            [InlineKeyboardButton(text="🔙 Back to Menu", callback_data="main_menu")]
        ]),
        parse_mode=ParseMode.MARKDOWN
    )
    await callback.answer()

@dp.callback_query(F.data == "send_invoice")
async def send_demo_invoice(callback: CallbackQuery):
    prices = [LabeledPrice(label="AI Video Clip Generation", amount=50)]
    await callback.message.answer_invoice(
        title="AI Video Clipper Pass",
        description="Generates 1x vertical 9:16 short clip with custom captions from a YouTube link.",
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
        "Your credit has been unlocked. Now, please **paste your YouTube link** below so I can fetch and process your video!",
        parse_mode=ParseMode.MARKDOWN
    )

@dp.message(F.text.contains("youtube.com") | F.text.contains("youtu.be"))
async def handle_youtube_link(message: Message):
    user_id = message.from_user.id
    if user_id not in paid_users:
        await message.answer("⚠️ Please complete the 50 Stars payment first by tapping /start before sending a link!")
        return

    url = message.text.strip()
    status_msg = await message.answer("📥 Fetching video details with `yt-dlp`...")

    try:
        ydl_opts = {'format': 'best', 'quiet': True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Unknown Title')
            duration = info.get('duration', 0)

        paid_users.remove(user_id)

        await status_msg.edit_text(
            f"✅ **Video Received & Verified!**\n\n"
            f"📌 **Title:** {title}\n"
            f"⏱️ **Duration:** {duration} seconds\n\n"
            "🚀 *Next step:* Slicing into vertical shorts & generating captions..."
        )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error processing link: {str(e)}")

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
