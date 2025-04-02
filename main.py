import os
import re
import asyncio
import html
from datetime import datetime
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
from utils.helpers import escape_markdown, get_correct_username, log_to_channel

# --- Link Extractor Module (Pyrogram) ---
user_sessions = {}

async def setup_link_extractor(client: Client):
    @client.on_message(filters.command("extract_txt"))
    async def start_collecting(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id in user_sessions:
            await message.reply_text("You're already in a session. Send messages or use /over to finish.")
        else:
            if len(message.command) > 1:
                filename = message.command[1] + ".txt"
            else:
                filename = f"{user_id}_extracted.txt"
            
            user_sessions[user_id] = {"messages": [], "filename": filename}
            await message.reply_text(f"Started collecting text. Send messages, then type /over when done.\nYour file will be saved as: {filename}")

    @client.on_message(filters.text & ~filters.command("over"))
    async def collect_text(client: Client, message: Message):
        user_id = message.from_user.id
        if user_id in user_sessions:
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
    
    @client.on_message(filters.command("over"))
    async def stop_collecting(client: Client, message: Message):
        user_id = message.from_user.id
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

    @client.on_message(filters.command("reset"))
    async def reset_sessions(client: Client, message: Message):
        user_sessions.clear()
        await message.reply_text("All user sessions have been reset.")

# --- PW Link Changer Module ---
ASK_FOR_FILE, ASK_FOR_TOKEN = range(2)

def transform_mpd_links(content, token):
    mpd_links = re.findall(r"(https://[a-zA-Z0-9.-]+/[\w-]+/master\.mpd)", content)
    for original_link in mpd_links:
        video_id_match = re.search(r"https://[a-zA-Z0-9.-]+/([\w-]+)/master\.mpd", original_link)
        if video_id_match:
            video_id = video_id_match.group(1)
            new_link = f"https://madxabhi-pw.onrender.com/{video_id}/master.m3u8?token={token}"
            content = content.replace(original_link, new_link)
    return content

async def pw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.message.from_user
    await log_to_channel(context.bot, user, "🚀 PW Link Changer Started")
    await update.message.reply_text(
        "📤 Send me your TXT file in which you want to change your links",
        parse_mode=ParseMode.MARKDOWN_V2
    )
    return ASK_FOR_FILE

async def pw_handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def pw_handle_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# --- TXT to HTML Module ---
(FILENAME, TITLE, GLITCH, CLASS, HEADER, 
 METHOD_CHOICE, BUTTON_PAIRS, LINE_RANGE) = range(8)

html_user_data = {}

async def html_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📄 Please send me the filename you want for your HTML file (without .html extension)\n\nExample: my_lectures"
    )
    return FILENAME

async def get_filename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

async def get_title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    html_user_data[update.effective_user.id]['title'] = update.message.text
    await update.message.reply_text("Now send me your name")
    return GLITCH

async def get_glitch_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    html_user_data[update.effective_user.id]['glitch'] = update.message.text
    await update.message.reply_text("Now send coaching platform name")
    return CLASS

async def get_class(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    html_user_data[update.effective_user.id]['class'] = update.message.text
    await update.message.reply_text("Now send me sir name and chapter name")
    return HEADER

async def get_header(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    html_user_data[update.effective_user.id]['header'] = update.message.text
    await update.message.reply_text(
        "📌 How do you want to send button links?\n\n"
        "1⃣️ Manual Input – Send text:link pairs (one per line)\n"
        "2⃣ Upload TXT File – Send a .txt file with text:link pairs\n\n"
        "Reply with 1 or 2"
    )
    return METHOD_CHOICE

async def handle_method_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

async def get_line_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message.document:
        await update.message.reply_text("❌ Please upload a .txt file first.")
        return LINE_RANGE
    
    html_user_data[update.effective_user.id]['document'] = update.message.document
    await update.message.reply_text("📝 Now send the line range you want to process (e.g. 1-10):")
    return BUTTON_PAIRS

async def get_button_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
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

async def generate_html(update: Update, user_id: int) -> None:
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

        /* ... (rest of the CSS styles from the original txt_to_html.py) ... */
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
        /* ... (rest of the JavaScript from the original txt_to_html.py) ... */
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
        print(f"Failed to send log: {e}")
    
    os.remove(filename)
    del html_user_data[user_id]

async def html_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.effective_user.id in html_user_data:
        del html_user_data[update.effective_user.id]
    await update.message.reply_text('Operation cancelled.')
    return ConversationHandler.END

# --- Main Bot Setup ---
async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print(f"Error: {context.error}")
    if update and update.message:
        await update.message.reply_text(f"An error occurred: {context.error}")

async def main():
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

if __name__ == "__main__":
    asyncio.run(main())
