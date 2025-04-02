import html
from datetime import datetime
from telegram import Bot
from telegram.constants import ParseMode

def escape_markdown(text):
    special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in special_chars:
        text = text.replace(char, f"\\{char}")
    return text

def get_correct_username(user):
    if user.username:
        return f"@{user.username}"
    else:
        first_name = user.first_name or ""
        last_name = user.last_name or ""
        name = f"{first_name} {last_name}".strip()
        if name:
            return f"{name} (ID: {user.id})"
        else:
            return f"User (ID: {user.id})"

async def log_to_channel(bot: Bot, user, action: str, filename: str = None, file_content=None):
    username = get_correct_username(user)
    log_message = escape_markdown(
        f"📄 {action}\n\n"
        f"👤 Name: {user.full_name}\n"
        f"📌 Username: {username}\n"
        f"🆔 User ID: {user.id}\n"
        f"🕒 Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    
    if filename:
        log_message += f"\n📄 File Name: {filename}"
    
    if file_content:
        await bot.send_document(
            chat_id=LOG_CHANNEL_ID,
            document=file_content,
            caption=log_message,
            parse_mode=ParseMode.MARKDOWN_V2
        )
    else:
        await bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=log_message,
            parse_mode=ParseMode.MARKDOWN_V2
        )
