import html
import re
from datetime import datetime
from typing import Optional, Union
from telegram import Bot, InputFile
from telegram.constants import ParseMode
from config import LOG_CHANNEL_ID

def escape_markdown(text: str, version: int = 2) -> str:
    """
    Escape special Markdown characters in text.
    
    Args:
        text: The text to escape
        version: Markdown version (1 or 2)
    
    Returns:
        Escaped text string
    """
    if version == 1:
        special_chars = ['_', '*', '`', '[']
    else:  # version 2
        special_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    
    return re.sub('|'.join(map(re.escape, special_chars)), lambda m: '\\' + m.group(), text)

def get_correct_username(user) -> str:
    """
    Get the best available username representation from a Telegram user object.
    
    Args:
        user: Telegram user object
    
    Returns:
        Formatted username string
    """
    if not user:
        return "Unknown User"
        
    if user.username:
        return f"@{user.username}"
    
    name_parts = []
    if user.first_name:
        name_parts.append(user.first_name)
    if user.last_name:
        name_parts.append(user.last_name)
    
    name = ' '.join(name_parts).strip()
    return f"{name} (ID: {user.id})" if name else f"User (ID: {user.id})"

async def log_to_channel(
    bot: Bot,
    user,
    action: str,
    filename: Optional[str] = None,
    file_content: Union[str, bytes, InputFile, None] = None,
    extra_info: Optional[str] = None
) -> bool:
    """
    Log actions to the configured Telegram channel.
    
    Args:
        bot: Telegram Bot instance
        user: Telegram user object
        action: Description of the action being logged
        filename: Name of the file being processed (optional)
        file_content: File content to send (optional)
        extra_info: Additional information to include in log (optional)
    
    Returns:
        bool: True if logging succeeded, False otherwise
    """
    try:
        username = get_correct_username(user)
        time_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        log_lines = [
            f"📄 {action}",
            f"👤 User: {escape_markdown(user.full_name)}",
            f"🆔 ID: {user.id}",
            f"📌 Username: {escape_markdown(username)}",
            f"🕒 Time: {time_str}"
        ]
        
        if filename:
            log_lines.append(f"📁 File: {escape_markdown(filename)}")
        if extra_info:
            log_lines.append(f"ℹ️ Info: {escape_markdown(extra_info)}")
        
        log_message = "\n\n".join(log_lines)
        
        if file_content:
            if isinstance(file_content, (str, bytes)):
                # Handle file paths or raw bytes
                with open(file_content, 'rb') as f if isinstance(file_content, str) else file_content:
                    await bot.send_document(
                        chat_id=LOG_CHANNEL_ID,
                        document=f,
                        caption=log_message,
                        parse_mode=ParseMode.MARKDOWN_V2
                    )
            else:
                # Handle Telegram InputFile objects
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
        return True
        
    except Exception as e:
        print(f"⚠️ Failed to log to channel: {e}")
        return False

def transform_media_links(content: str, pattern: str, replacement: str) -> str:
    """
    Transform media links in content based on regex pattern.
    
    Args:
        content: Text content containing links
        pattern: Regex pattern to match links
        replacement: Replacement pattern for matched links
    
    Returns:
        Transformed content string
    """
    try:
        return re.sub(pattern, replacement, content)
    except re.error as e:
        print(f"⚠️ Regex error in link transformation: {e}")
        return content
