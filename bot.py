import asyncio
import logging
import os
import sys
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import yt_dlp
from faster_whisper import WhisperModel

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

# Initialize lightweight Whisper model for fast CPU transcription on Render
whisper_model = WhisperModel("tiny", device="cpu", compute_type="int8")

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
        [InlineKeyboardButton(text="🎬 Clip & Auto-Caption", callback_data="pay_per_clip")],
        [InlineKeyboardButton(text="ℹ️ How It Works", callback_data="help")]
    ])

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    user_name = message.from_user.full_name
    welcome_text = (
        f"Hello, {html.bold(user_name)}! 👋\n\n"
        "I am your automated **AI Video & Auto-Captioning Bot**.\n"
        "Send me a **YouTube link** OR upload a **video file**, and I'll slice it into vertical shorts with **burned-in subtitles**!"
    )
    await message.answer(welcome_text, reply_markup=get_main_menu(), parse_mode=ParseMode.MARKDOWN)

@dp.callback_query(F.data == "help")
async def process_help(callback: CallbackQuery):
    help_text = (
        "📖 **How to use this bot:**\n\n"
        "1. Send a YouTube link OR upload any video file.\n"
        "2. The bot cuts 4-6 vertical clips, transcribes the speech, and burns auto-captions onto them!"
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
    prices = [LabeledPrice(label="AI Multi-Clip + Auto-Captions", amount=50)]
    await callback.message.answer_invoice(
        title="AI Clipper & Captions Pass",
        description="Generates vertical shorts with automated burned-in subtitles.",
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

def format_time(seconds):
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

def generate_srt_and_burn(input_video, output_video):
    # 1. Extract audio temporary file for Whisper
    audio_path = "temp_audio.wav"
    subprocess.run(['ffmpeg', '-y', '-i', input_video, '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1', audio_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 2. Transcribe with faster-whisper
    segments, _ = whisper_model.transcribe(audio_path, beam_size=1)
    
    srt_path = "subtitles.srt"
    with open(srt_path, "w", encoding="utf-8") as srt_file:
        for i, segment in enumerate(segments, start=1):
            start_str = format_time(segment.start)
            end_str = format_time(segment.end)
            text = segment.text.strip()
            srt_file.write(f"{i}\n{start_str} --> {end_str}\n{text}\n\n")

    if os.path.exists(audio_path):
        os.remove(audio_path)

    # 3. FFmpeg crop to 9:16 vertical AND burn subtitles with styling
    # Note: On Linux/Render, subtitle path escaping for FFmpeg: escape colons if any
    safe_srt = srt_path.replace(":", "\\:")
    sub_filter = f"subtitles={safe_srt}:force_style='FontName=Arial,FontSize=20,PrimaryColour=&H00FFFF&,Outline=2,Shadow=1,Alignment=2'"
    crop_filter = "crop=ih*9/16:ih"

    ffmpeg_cmd = [
        'ffmpeg', '-y', '-i', input_video,
        '-vf', f"{crop_filter},{sub_filter}",
        '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '28',
        '-c:a', 'aac',
        output_video
    ]
    subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if os.path.exists(srt_path):
        os.remove(srt_path)

# --- PROCESSING PIPELINE FOR BOTH UPLOADS & YOUTUBE ---
async def process_and_send_clips(message, source_path, title_prefix):
    try:
        probe_cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1', source_path]
        result = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        duration = float(result.stdout.strip()) if result.stdout.strip() else 60.0

        if duration < 10:
            await message.answer("❌ Video is too short to clip.")
            return

        interval = duration / 6.0
        start_times = [int(interval * 1), int(interval * 2.5), int(interval * 4), int(interval * 5)]

        status_msg = await message.answer(f"⚡ Slicing & generating AI auto-captions for *{title_prefix}*...")

        generated_files = []
        for idx, start_sec in enumerate(start_times, start=1):
            raw_chunk = f"raw_chunk_{idx}.mp4"
            final_clip = f"final_clip_{idx}.mp4"

            # Fast chunk cut
            cut_cmd = ['ffmpeg', '-y', '-ss', str(start_sec), '-i', source_path, '-t', '25', '-c', 'copy', raw_chunk]
            subprocess.run(cut_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if os.path.exists(raw_chunk) and os.path.getsize(raw_chunk) > 0:
                # Transcribe & burn captions + 9:16 crop
                generate_srt_and_burn(raw_chunk, final_clip)
                if os.path.exists(final_clip) and os.path.getsize(final_clip) > 0:
                    generated_files.append(final_clip)
                if os.path.exists(raw_chunk):
                    os.remove(raw_chunk)

        if os.path.exists(source_path):
            os.remove(source_path)

        if generated_files:
            await status_msg.edit_text(f"✅ Generated **{len(generated_files)} vertical shorts** with auto-captions!")
            for fpath in generated_files:
                await message.answer_video(FSInputFile(fpath), caption=f"🎬 {title_prefix[:25]} (Auto-Captioned)")
                await asyncio.sleep(1)
                try:
                    os.remove(fpath)
                except:
                    pass
        else:
            await status_msg.edit_text("❌ Failed to process video clips.")

    except Exception as e:
        await message.answer(f"❌ Processing error: `{str(e)}`")

# --- HANDLER 1: DIRECT VIDEO UPLOADS ---
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

    if user_id != DEV_ID:
        paid_users.remove(user_id)

    await status_msg.delete()
    await process_and_send_clips(message, local_path, "Uploaded Video")

# --- HANDLER 2: YOUTUBE LINKS ---
@dp.message(F.text.contains("youtube.com") | F.text.contains("youtu.be"))
async def handle_youtube_link(message: Message):
    user_id = message.from_user.id
    if user_id != DEV_ID and user_id not in paid_users:
        await message.answer("⚠️ Please complete payment or use developer access first via `/start`!")
        return

    url = message.text.strip()
    status_msg = await message.answer("📥 Downloading YouTube video via embedded client...")

    try:
        ydl_opts = {
            'format': 'best[height<=720]',
            'outtmpl': 'yt_source.mp4',
            'quiet': True,
            'extractor_args': {'youtube': {'player_client': ['web_embedded', 'android']}}
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get('title', 'YouTube Video')

        local_path = "yt_source.mp4"
        if not os.path.exists(local_path):
            raise Exception("YouTube download failed.")

        if user_id != DEV_ID:
            paid_users.remove(user_id)

        await status_msg.delete()
        await process_and_send_clips(message, local_path, title)

    except Exception as e:
        await status_msg.edit_text(f"❌ YouTube Error: `{str(e)}`")

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
