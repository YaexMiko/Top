import sys
import os
import asyncio
import html
import re
import logging
from datetime import datetime
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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Link Extractor Module (Pyrogram) ---
user_sessions = {}

async def setup_link_extractor(client: Client):
    @client.on_message(filters.command("extract_txt"))
    async def start_collecting(client: Client, message: Message):
        try:
            user_id = get_safe_user_id(message)
            if user_id is None:
                await message.reply_text("Error: Could not identify sender")
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
            if user_id is None:
                await message.reply_text("Error: Could not identify sender")
                return

            if user_id not in user_sessions:
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
        token = update.message.text
        original_filename = context.user_data["original_filename"]

        with open(original_filename, "r") as file:
            content = file.read()

        transformed_content = transform_mpd_links(content, token)

        new_filename = f"_ @ItsNomis _{original_filename}"
        with open(new_filename, "w") as file:
            file.write(transformed_content)

        with open(new_filename, "rb") as file:
            await update.message.reply_document(
                document=file,
                caption="📄 Here is the final TXT file\n\n👨‍💻 Done by -- @Pwlinkcangerbot",
                parse_mode=ParseMode.MARKDOWN_V2
            )

        await log_to_channel(context.bot, update.message.from_user, "📄 Transformed File Sent", new_filename)
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=f"User's Token:\n```\n{token}\n```",
            parse_mode=ParseMode.MARKDOWN_V2
        )

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

# ... [Rest of your TXT to HTML module functions remain the same, just add similar try-catch blocks]

async def main():
    try:
        # Initialize Pyrogram client
        pyro_client = Client(
            "my_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN
        )
        
        # Initialize python-telegram-bot application
        ptb_application = Application.builder().token(BOT_TOKEN).build()
        
        # Set up all modules
        await setup_link_extractor(pyro_client)
        
        # PW Link Changer
        pw_conv_handler = ConversationHandler(
            entry_points=[CommandHandler("pw", pw_start)],
            states={
                ASK_FOR_FILE: [MessageHandler(tg_filters.Document.FileExtension("txt"), pw_handle_file)],
                ASK_FOR_TOKEN: [MessageHandler(tg_filters.TEXT & ~tg_filters.COMMAND, pw_handle_token)],
            },
            fallbacks=[CommandHandler("cancel", html_cancel)],
        )
        ptb_application.add_handler(pw_conv_handler)
        
        # TXT to HTML
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
        
        # Start command
        ptb_application.add_handler(CommandHandler("start", start_bot))
        
        # Error handler
        ptb_application.add_error_handler(error_handler)
        
        # Start both clients
        await pyro_client.start()
        await ptb_application.initialize()
        await ptb_application.start()
        
        # Run forever
        await asyncio.Event().wait()
    except Exception as e:
        logger.critical(f"Fatal error in main: {e}")
    finally:
        await pyro_client.stop()
        await ptb_application.stop()

if __name__ == "__main__":
    asyncio.run(main())
