import sys
import os
import asyncio
import signal
import logging
from typing import Optional
from pyrogram import Client, filters
from pyrogram.types import Message, MessageEntity
from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters as tg_filters,
    ConversationHandler,
    ContextTypes,
)
from telegram.constants import ParseMode
from config import API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL_ID
from utils.helpers import (
    escape_markdown,
    get_correct_username,
    log_to_channel,
    transform_mpd_links,
    get_safe_user_id
)

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- Signal Handlers ---
async def shutdown(signal, loop):
    """Cleanup tasks tied to the service's shutdown."""
    logger.info(f"Received exit signal {signal.name}...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    [task.cancel() for task in tasks]
    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

def add_signal_handlers():
    try:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig,
                lambda s=sig: asyncio.create_task(shutdown(s, loop)))
    except RuntimeError:
        logger.warning("Could not add signal handlers - not running in main thread")

# --- Link Extractor Module (Pyrogram) ---
user_sessions = {}

async def setup_link_extractor(client: Client):
    @client.on_message(filters.command("extract_txt"))
    async def start_collecting(client: Client, message: Message):
        try:
            user_id = get_safe_user_id(message)
            if user_id is None:
                return

            if user_id in user_sessions:
                await message.reply_text("You're already in a session. Send messages or use /over to finish.")
            else:
                filename = f"{user_id}_extracted.txt" if len(message.command) <= 1 else f"{message.command[1]}.txt"
                user_sessions[user_id] = {"messages": [], "filename": filename}
                await message.reply_text(f"Started collecting text. Send messages, then type /over when done.\nYour file will be saved as: {filename}")
        except Exception as e:
            logger.error(f"Error in start_collecting: {e}")

    @client.on_message(filters.text & ~filters.command("over"))
    async def collect_text(client: Client, message: Message):
        try:
            user_id = get_safe_user_id(message)
            if user_id is None or user_id not in user_sessions:
                return

            text = message.text
            entities = message.entities if message.entities else []
            formatted_entries = []
            
            if entities:
                for entity in entities:
                    if entity.type.name.lower() == "text_link":
                        start = entity.offset
                        end = start + entity.length
                        link_text = text[start:end]
                        url = entity.url
                        formatted_entries.append(f"{link_text} : {url}")
            
            if formatted_entries:
                user_sessions[user_id]["messages"].extend(formatted_entries)
            else:
                user_sessions[user_id]["messages"].append(text)
        except Exception as e:
            logger.error(f"Error in collect_text: {e}")

    @client.on_message(filters.command("over"))
    async def stop_collecting(client: Client, message: Message):
        try:
            user_id = get_safe_user_id(message)
            if user_id is None or user_id not in user_sessions:
                await message.reply_text("You're not in a session. Use /extract_txt to start.")
                return
            
            session_data = user_sessions.pop(user_id)
            filename = session_data["filename"]
            file_content = "\n".join(session_data["messages"])
            
            if not file_content.strip():
                await message.reply_text("No content collected. File not generated.")
                return
            
            with open(filename, "w", encoding="utf-8") as file:
                file.write(file_content)
            
            await message.reply_document(filename)
            os.remove(filename)
        except Exception as e:
            logger.error(f"Error in stop_collecting: {e}")

    @client.on_message(filters.command("reset"))
    async def reset_sessions(client: Client, message: Message):
        user_sessions.clear()
        await message.reply_text("All user sessions have been reset.")

# --- PW Link Changer Module ---
ASK_FOR_FILE, ASK_FOR_TOKEN = range(2)

async def pw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.message.from_user
        await log_to_channel(context.bot, user, "🚀 PW Link Changer Started")
        await update.message.reply_text(
            "📤 Send me your TXT file in which you want to change your links",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ASK_FOR_FILE
    except Exception as e:
        logger.error(f"Error in pw_start: {e}")
        return ConversationHandler.END

async def pw_handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message.document:
            await update.message.reply_text("Please send a TXT file")
            return ASK_FOR_FILE

        file = await update.message.document.get_file()
        original_filename = update.message.document.file_name
        await file.download_to_drive(original_filename)
        context.user_data["original_filename"] = original_filename

        await log_to_channel(context.bot, update.message.from_user, "📂 File Uploaded", original_filename)
        
        await update.message.reply_text(
            "✅ Your TXT file is received\n\n🔑 Please send me your token",
            parse_mode=ParseMode.MARKDOWN_V2
        )
        return ASK_FOR_TOKEN
    except Exception as e:
        logger.error(f"Error in pw_handle_file: {e}")
        return ConversationHandler.END

async def pw_handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        token = update.message.text.strip()
        if not token:
            await update.message.reply_text("Please enter a valid token")
            return ASK_FOR_TOKEN

        original_filename = context.user_data.get("original_filename")
        if not original_filename or not os.path.exists(original_filename):
            await update.message.reply_text("File not found. Please start over.")
            return ConversationHandler.END

        with open(original_filename, "r") as file:
            content = file.read()

        transformed_content = transform_mpd_links(content, token)
        new_filename = f"transformed_{original_filename}"

        with open(new_filename, "w") as file:
            file.write(transformed_content)

        with open(new_filename, "rb") as file:
            await update.message.reply_document(
                document=file,
                caption="📄 Here is your transformed file",
                parse_mode=ParseMode.MARKDOWN_V2
            )

        await log_to_channel(context.bot, update.message.from_user, "📄 Transformed File Sent", new_filename)
        
        os.remove(original_filename)
        os.remove(new_filename)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in pw_handle_token: {e}")
        return ConversationHandler.END

# --- TXT to HTML Module ---
(FILENAME, TITLE, GLITCH, CLASS, HEADER, 
 METHOD_CHOICE, BUTTON_PAIRS, LINE_RANGE) = range(8)

html_user_data = {}

async def html_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        await update.message.reply_text(
            "📄 Please send me the filename you want for your HTML file (without .html extension)\n\nExample: my_lectures"
        )
        return FILENAME
    except Exception as e:
        logger.error(f"Error in html_start: {e}")
        return ConversationHandler.END

async def get_filename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        filename = update.message.text.strip()
        if not filename:
            await update.message.reply_text("❌ Invalid filename. Please try again.")
            return FILENAME
        
        html_user_data[update.effective_user.id] = {
            'filename': filename,
            'username': update.effective_user.username or 'No username',
            'first_name': update.effective_user.first_name or '',
            'last_name': update.effective_user.last_name or ''
        }
        await update.message.reply_text("Send me the text that you want to be page title")
        return TITLE
    except Exception as e:
        logger.error(f"Error in get_filename: {e}")
        return ConversationHandler.END

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        html_user_data[update.effective_user.id]['title'] = update.message.text
        await update.message.reply_text("Now send me your name")
        return GLITCH
    except Exception as e:
        logger.error(f"Error in get_title: {e}")
        return ConversationHandler.END

async def get_glitch_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        html_user_data[update.effective_user.id]['glitch'] = update.message.text
        await update.message.reply_text("Now send coaching platform name")
        return CLASS
    except Exception as e:
        logger.error(f"Error in get_glitch_text: {e}")
        return ConversationHandler.END

async def get_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        html_user_data[update.effective_user.id]['class'] = update.message.text
        await update.message.reply_text("Now send me sir name and chapter name")
        return HEADER
    except Exception as e:
        logger.error(f"Error in get_class: {e}")
        return ConversationHandler.END

async def get_header(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        html_user_data[update.effective_user.id]['header'] = update.message.text
        await update.message.reply_text(
            "📌 How do you want to send button links?\n\n"
            "1⃣️ Manual Input – Send text:link pairs (one per line)\n"
            "2⃣ Upload TXT File – Send a .txt file with text:link pairs\n\n"
            "Reply with 1 or 2"
        )
        return METHOD_CHOICE
    except Exception as e:
        logger.error(f"Error in get_header: {e}")
        return ConversationHandler.END

async def handle_method_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        choice = update.message.text.strip()

        if choice == "1":
            await update.message.reply_text(
                "📝 Send your button texts & links in this format (one per line):\n\n"
                "Example:\n"
                "Lecture 1:https://example.com/1\n"
                "Lecture 1:https://example.com/2"
            )
            return BUTTON_PAIRS

        elif choice == "2":
            await update.message.reply_text(
                "📤 Please upload a .txt file containing text:link pairs (one per line).\n\n"
                "Then send the line range you want to process in format:\n"
                "from-to\n\n"
                "Example: 1-10 (will process lines 1 to 10)"
            )
            return LINE_RANGE

        else:
            await update.message.reply_text("❌ Invalid choice. Please reply with 1 or 2.")
            return METHOD_CHOICE
    except Exception as e:
        logger.error(f"Error in handle_method_choice: {e}")
        return ConversationHandler.END

async def get_line_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        if not update.message.document:
            await update.message.reply_text("❌ Please upload a .txt file first.")
            return LINE_RANGE
        
        html_user_data[update.effective_user.id]['document'] = update.message.document
        await update.message.reply_text("📝 Now send the line range you want to process (e.g. 1-10):")
        return BUTTON_PAIRS
    except Exception as e:
        logger.error(f"Error in get_line_range: {e}")
        return ConversationHandler.END

async def get_button_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        user_id = update.effective_user.id
        data = html_user_data[user_id]

        if 'document' in data:
            if '-' not in update.message.text:
                await update.message.reply_text("❌ Invalid format. Please use format: from-to (e.g. 1-10)")
                return BUTTON_PAIRS
            
            try:
                from_line, to_line = map(int, update.message.text.split('-'))
                if from_line < 1 or to_line < from_line:
                    raise ValueError
            except:
                await update.message.reply_text("❌ Invalid line range. Please try again.")
                return BUTTON_PAIRS

            file = data['document'].get_file()
            downloaded_file = file.download()

            with open(downloaded_file, 'r', encoding='utf-8') as f:
                all_lines = [line.strip() for line in f.readlines() if line.strip()]
            
            os.remove(downloaded_file)
            from_idx = max(0, from_line - 1)
            to_idx = min(len(all_lines), to_line)
            lines = all_lines[from_idx:to_idx]
            del data['document']
        elif update.message.text:
            lines = [line.strip() for line in update.message.text.split('\n') if line.strip()]
        else:
            await update.message.reply_text("❌ No pairs found. Please try again.")
            return BUTTON_PAIRS

        button_texts = []
        button_links = []

        for line in lines:
            if ':' not in line:
                await update.message.reply_text(f"❌ Invalid format in line: '{line}'. Use `text:link` format.")
                return BUTTON_PAIRS

            text, link = line.split(':', 1)
            text = text.strip()
            link = link.strip()
            
            if 'media-cdn.classplusapp.com' in link:
                link = f"https://master-api-v3.vercel.app/nomis-player?url={link}"

            button_texts.append(text)
            button_links.append(link)

        if not button_texts:
            await update.message.reply_text("❌ No valid button pairs found. Try again.")
            return BUTTON_PAIRS

        data['button_texts'] = button_texts
        data['button_links'] = button_links
        await generate_html(update, user_id)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in get_button_pairs: {e}")
        return ConversationHandler.END

async def generate_html(update: Update, user_id: int) -> None:
    try:
        data = html_user_data[user_id]
        
        buttons_html = ""
        for text, link in zip(data['button_texts'], data['button_links']):
            buttons_html += f"""
               <li><a href="{html.escape(link)}" target="_blank"><button class="lecture-button">{html.escape(text)}</button></a></li>"""
        
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(data['title'])}</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700&family=Rajdhani:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        /* Cyberpunk Neon Theme */
        :root {{
          --neon-purple: #bc13fe;
          --neon-pink: #ff00ff;
          --neon-blue: #00ffff;
          --neon-green: #00ff41;
          --dark-purple: #8e44ad;
          --deep-black: #000000;
          --dark-gray: #121212;
          --light-gray: #bdc3c7;
          --matrix-green: #0f0;
        }}

        body {{
          margin: 0;
          padding: 0;
          font-family: 'Orbitron', 'Rajdhani', sans-serif;
          background-color: var(--deep-black);
          color: var(--light-gray);
          display: flex;
          justify-content: center;
          align-items: center;
          min-height: 100vh;
          flex-direction: column;
          overflow-x: hidden;
          position: relative;
        }}

        /* ... (rest of your CSS styles) ... */
    </style>
</head>
<body>
    <div class="kanji-rain" id="kanjiRain"></div>
    
    <div class="container">
        <h1 class="glitch" data-text="{html.escape(data['glitch'])}">{html.escape(data['glitch'])}</h1><br>
        <h2>{html.escape(data['class'])}</h2><br>
        <h3>{html.escape(data['header'])}</h3><br>
        <ul class="lecture-list">
           {buttons_html}
           <li><a href="https://youtu.be/Tba8arFqBFw?si=q01kKfamn4rKW_er" target="_blank"><button class="lecture-button">How To Process Links</button></a></li>
        </ul>
    </div>

    <div class="footer">
        <p>Leaked by <span>Nomis</span> | From <span>IIT School Institute ({html.escape(data['class'])})</span> | <br><br><br>
            <a href="https://t.me/ItsNomis" target="_blank" class="social-link">
                <img src="https://cdn-icons-png.flaticon.com/512/2111/2111646.png" alt="Telegram" width="16"> Telegram
            </a> | 
            <a href="http://www.aboutnomis.carrd.co" target="_blank" class="social-link">
                <img src="https://cdn-icons-png.flaticon.com/512/25/25231.png" alt="Website" width="16"> Website
            </a>
        </p>
    </div>

    <button class="back-to-top" onclick="scrollToTop()">⬆️</button>

    <script>
        // Back to Top Button
        const backToTopBtn = document.querySelector('.back-to-top');
        window.addEventListener('scroll', () => {{
            backToTopBtn.style.display = window.scrollY > 300 ? 'block' : 'none';
        }});
        function scrollToTop() {{
            window.scrollTo({{ top: 0, behavior: 'smooth' }});
        }}

        // Kanji Rain Animation
        const kanjiCharacters = ['侍', '忍', '龍', '鬼', '刀', '影', '闇', '光', '電', '夢', '愛', '戦', '死', '生', '風', '火', '水', '土', '空', '心'];
        const kanjiContainer = document.getElementById('kanjiRain');
        
        function createKanji() {{
            const kanji = document.createElement('div');
            kanji.className = 'kanji';
            kanji.textContent = kanjiCharacters[Math.floor(Math.random() * kanjiCharacters.length)];
            kanji.style.left = Math.random() * 100 + 'vw';
            kanji.style.animationDuration = (Math.random() * 5 + 3) + 's';
            kanji.style.opacity = Math.random() * 0.5 + 0.1;
            kanji.style.fontSize = (Math.random() * 10 + 16) + 'px';
            kanjiContainer.appendChild(kanji);
            
            setTimeout(() => {{ kanji.remove(); }}, 8000);
        }}

        // Initialize
        document.addEventListener('DOMContentLoaded', () => {{
            for (let i = 0; i < 50; i++) {{
                setTimeout(createKanji, i * 200);
            }}
            setInterval(createKanji, 300);
            backToTopBtn.style.display = 'none';
        }});
    </script>
</body>
</html>"""
        
        filename = f"{data['filename']}.html"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        caption = (f"📄 New HTML File Generated\n\n"
                f"👤 User: {data['first_name']} {data['last_name']}\n"
                f"🆔 ID: {user_id}\n"
                f"🔗 Username: @{data['username']}\n"
                f"📛 Title: {data['title']}\n"
                f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🔢 Buttons: {len(data['button_texts'])}")
        
        with open(filename, 'rb') as f:
            await update.message.reply_document(
                document=f,
                filename=filename,
                caption=caption
            )
        
        try:
            log_bot = Bot(token=BOT_TOKEN)
            log_message = (
                f"📄 New HTML File Generated\n\n"
                f"👤 User: {data['first_name']} {data['last_name']}\n"
                f"🆔 ID: {user_id}\n"
                f"🔗 Username: @{data['username']}\n"
                f"📛 Title: {data['title']}\n"
                f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"🔢 Buttons: {len(data['button_texts'])}"
            )
            
            with open(filename, 'rb') as f:
                await log_bot.send_document(
                    chat_id=LOG_CHANNEL_ID,
                    document=f,
                    filename=f"user_{user_id}_{filename}",
                    caption=log_message
                )
        except Exception as e:
            logger.error(f"Failed to send log: {e}")
        
        os.remove(filename)
        del html_user_data[user_id]
    except Exception as e:
        logger.error(f"Error in generate_html: {e}")

async def html_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        if update.effective_user.id in html_user_data:
            del html_user_data[update.effective_user.id]
        await update.message.reply_text('Operation cancelled.')
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Error in html_cancel: {e}")
        return ConversationHandler.END

# --- Main Bot Setup ---
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.message.from_user
        await log_to_channel(context.bot, user, "🚀 New User Started the Bot")
        
        await update.message.reply_photo(
            photo="https://envs.sh/BNN.jpg",
            caption=(
                "🤖 This Telegram Bot combines multiple functionalities:\n\n"
                "1️⃣ /extract_txt - Extract text and links from messages\n"
                "2️⃣ /pw - Convert PW DRM protected links\n"
                "3️⃣ /html - Generate HTML files with button links\n\n"
                "Bot made by @ItsNomis"
            ),
            parse_mode=ParseMode.MARKDOWN_V2
        )
    except Exception as e:
        logger.error(f"Error in start_bot: {e}")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update and update.message:
        try:
            await update.message.reply_text(f"An error occurred: {context.error}")
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

async def main():
    pyro_client = None
    ptb_application = None
    
    try:
        # Initialize PTB first
        ptb_application = Application.builder().token(BOT_TOKEN).build()
        
        # Set up PTB handlers
        pw_conv_handler = ConversationHandler(
            entry_points=[CommandHandler("pw", pw_start)],
            states={
                ASK_FOR_FILE: [MessageHandler(tg_filters.Document.FileExtension("txt"), pw_handle_file)],
                ASK_FOR_TOKEN: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, pw_handle_token)],
            },
            fallbacks=[CommandHandler("cancel", html_cancel)],
        )
        ptb_application.add_handler(pw_conv_handler)
        
        html_conv_handler = ConversationHandler(
            entry_points=[CommandHandler("html", html_start)],
            states={
                FILENAME: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, get_filename)],
                TITLE: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, get_title)],
                GLITCH: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, get_glitch_text)],
                CLASS: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, get_class)],
                HEADER: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, get_header)],
                METHOD_CHOICE: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, handle_method_choice)],
                LINE_RANGE: [MessageHandler(tg_filters.Document, get_line_range)],
                BUTTON_PAIRS: [
                    MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, get_button_pairs),
                    MessageHandler(tg_filters.Document, get_button_pairs)
                ],
            },
            fallbacks=[CommandHandler("cancel", html_cancel)],
        )
        ptb_application.add_handler(html_conv_handler)
        
        ptb_application.add_handler(CommandHandler("start", start_bot))
        ptb_application.add_error_handler(error_handler)
        
        # Initialize Pyrogram after PTB is set up
        pyro_client = Client(
            "my_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            in_memory=True
        )
        
        await setup_link_extractor(pyro_client)
        
        # Add signal handlers
        add_signal_handlers()
        
        # Start Pyrogram first
        await pyro_client.start()
        logger.info("Pyrogram client started")
        
        # Then start PTB with polling
        await ptb_application.initialize()
        await ptb_application.run_polling()
        logger.info("PTB application started polling")
        
    except asyncio.CancelledError:
        logger.info("Main task cancelled")
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}")
    finally:
        # Cleanup in reverse order
        try:
            if ptb_application:
                await ptb_application.stop()
                logger.info("PTB application stopped")
        except Exception as e:
            logger.error(f"Error stopping PTB: {e}")
            
        try:
            if pyro_client and await pyro_client.is_connected():
                await pyro_client.stop()
                logger.info("Pyrogram client stopped")
        except Exception as e:
            logger.error(f"Error stopping Pyrogram: {e}")

if __name__ == "__main__":
    asyncio.run(main())
