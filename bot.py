import logging
import json
import os
import re
import time as time_module
import asyncio
import hashlib
import base64
import io
import random
import string
from datetime import datetime, timedelta, time
from typing import Optional, Any
import pytz
import qrcode
from PIL import Image
from telegram import Update, ChatPermissions, ChatMember, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, ChatMemberHandler
from telegram.ext import JobQueue
from database import Database

# Для розпізнавання QR кодів
try:
    from pyzbar import pyzbar
    HAS_PYZBAR = True
except:
    HAS_PYZBAR = False

# Для розпізнавання тексту з картинок
try:
    import pytesseract
    HAS_PYTESSERACT = True
except:
    HAS_PYTESSERACT = False

# Глобальний флаг для перезапуску
RESTART_BOT = False

# Кешування timezone для швидшого завантаження
KYIV_TZ = pytz.timezone('Europe/Kyiv')

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

with open('config.json', 'r', encoding='utf-8') as f:
    config = json.load(f)

BOT_TOKEN = os.getenv('BOT_TOKEN', config.get('TOKEN', ''))
ADMIN_CHAT_ID = config.get('ADMIN_CHAT_ID')
USER_CHAT_ID = config.get('USER_CHAT_ID')
LOG_CHANNEL_ID = config.get('LOG_CHANNEL_ID')
NOTES_CHANNEL_ID = config.get('NOTES_CHANNEL_ID')
TEST_CHANNEL_ID = config.get('TEST_CHANNEL_ID')
MAIN_OWNER_ID = config.get('MAIN_OWNER_ID')
OWNER_IDS = config.get('OWNER_IDS', [])
MESSAGE_DELETE_TIMER = config.get('MESSAGE_DELETE_TIMER', 5)

db = Database()

# Словарь всех команд що будуть зареєстровані пізніше
# (заповнюється в main() перед запуском бота)
COMMAND_HANDLERS = {}

# Словник для зберігання статусів функцій (toggle buttons)
FEATURE_STATUS = {
    # Модерація команди
    "mod_ban_s": True,
    "mod_ban_t": True,
    "mod_unban_s": True,
    "mod_unban_t": True,
    "mod_mute_s": True,
    "mod_mute_t": True,
    "mod_unmute_s": True,
    "mod_unmute_t": True,
    "mod_kick": True,
    # Відправлення команди
    "msg_sayon": True,
    "msg_sayson": True,
    "msg_sayoffall": True,
}

def format_kyiv_time(iso_string: str) -> str:
    """Форматує ISO дату в формат: 2025-10-24 о 13:24 (Київ)"""
    try:
        dt = datetime.fromisoformat(iso_string)
        if dt.tzinfo is None:
            dt = pytz.UTC.localize(dt)
        tz = pytz.timezone('Europe/Kyiv')
        dt_kyiv = dt.astimezone(tz)
        return dt_kyiv.strftime('%Y-%m-%d о %H:%M')
    except:
        return iso_string

def load_config():
    """Завантажує конфігурацію з файлу"""
    try:
        with open('config.json', 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"❌ Помилка завантаження config.json: {e}")
        return {}

def save_config():
    with open('config.json', 'w', encoding='utf-8') as f:
        json.dump({
            "ADMIN_CHAT_ID": ADMIN_CHAT_ID,
            "USER_CHAT_ID": USER_CHAT_ID,
            "LOG_CHANNEL_ID": LOG_CHANNEL_ID,
            "NOTES_CHANNEL_ID": NOTES_CHANNEL_ID,
            "TEST_CHANNEL_ID": TEST_CHANNEL_ID,
            "MAIN_OWNER_ID": MAIN_OWNER_ID,
            "OWNER_IDS": OWNER_IDS,
            "MESSAGE_DELETE_TIMER": MESSAGE_DELETE_TIMER,
            "SECONDARY_CHAT_IDS": SECONDARY_CHAT_IDS
        }, f, indent=2, ensure_ascii=False)

SECONDARY_CHAT_IDS = config.get('SECONDARY_CHAT_IDS', [])

def is_allowed_chat(chat_id: int) -> bool:
    return chat_id == USER_CHAT_ID or chat_id in SECONDARY_CHAT_IDS

def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS

def is_main_owner(user_id: int) -> bool:
    return user_id == MAIN_OWNER_ID

def is_head_admin(user_id: int) -> bool:
    return db.get_role(user_id) == "head_admin"

def is_gnome(user_id: int) -> bool:
    return db.get_role(user_id) == "gnome"

def can_use_bot(user_id: int) -> bool:
    return is_owner(user_id) or is_head_admin(user_id) or is_gnome(user_id)

def parse_telegram_link(link: str):
    """Парсить посилання на Telegram повідомлення: https://t.me/c/2646171857/770828"""
    match = re.search(r't\.me/c/(\d+)/(\d+)', link)
    if match:
        # Для приватних каналів Telegram: chat_id = -1000000000000 - ID
        channel_id = int(match.group(1))
        chat_id = -1000000000000 - channel_id
        message_id = int(match.group(2))
        logger.info(f"📎 Парсено посилання: channel_id={channel_id}, chat_id={chat_id}, message_id={message_id}")
        return chat_id, message_id
    return None, None

def can_manage_gnomes(user_id: int) -> bool:
    return is_owner(user_id) or is_head_admin(user_id)

def can_ban_mute(user_id: int) -> bool:
    return is_owner(user_id) or is_head_admin(user_id)

def get_unmute_time_str(seconds: int) -> str:
    """Розраховує час розмута в форматі 'ГГ:МВ' за київським часом"""
    from datetime import datetime, timedelta
    import pytz
    kyiv_tz = pytz.timezone('Europe/Kyiv')
    unmute_time = datetime.now(kyiv_tz) + timedelta(seconds=seconds)
    return unmute_time.strftime("%H:%M")

def get_display_name(user_id: int, default_name: str = "Невідомий") -> str:
    """Отримати кастомне імʼя користувача або стандартне"""
    custom_name = db.get_custom_name(user_id)
    if custom_name:
        return custom_name
    return default_name or "Невідомий"

def safe_send_message(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    text = re.sub(r'[<>&]', '', text)
    text = re.sub(r'[@#]', '', text)
    text = re.sub(r'[\[\]]', '', text)
    return text.strip()

def sanitize_message_text(text: str) -> str:
    """Очистити текст повідомлення: видалити HTML-теги але залишити @username"""
    if not text:
        return ""
    text = str(text)
    text = re.sub(r'[<>&]', '', text)
    text = re.sub(r'[\[\]]', '', text)
    return text.strip()

async def delete_message_after_delay(message, delay: int = 5):
    """Видаляє повідомлення через delay секунд"""
    try:
        await asyncio.sleep(delay)
        await message.delete()
    except Exception as e:
        logger.debug(f"⚠️ Не вдалось видалити повідомлення: {e}")

async def reply_and_delete(update: Update, text: str, delay: Optional[int] = None, parse_mode: Optional[str] = None):
    """Надсилає відповідь та видаляє її через delay секунд"""
    global MESSAGE_DELETE_TIMER
    try:
        if not update.message:
            return None
        msg = await update.message.reply_text(text, parse_mode=parse_mode)
        if delay is None:
            delay = MESSAGE_DELETE_TIMER
        final_delay: int = int(delay) if delay is not None else MESSAGE_DELETE_TIMER
        asyncio.create_task(delete_message_after_delay(msg, final_delay))
        return msg
    except Exception as e:
        logger.error(f"Помилка при надсиланні повідомлення: {e}")
        return None

async def log_to_channel(context: ContextTypes.DEFAULT_TYPE, message: str, parse_mode: Optional[str] = "HTML"):
    if LOG_CHANNEL_ID:
        try:
            await context.bot.send_message(
                chat_id=LOG_CHANNEL_ID,
                text=message,
                parse_mode=parse_mode
            )
        except Exception as e:
            logger.error(f"Помилка логування в канал: {e}")

async def get_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE, identifier: str) -> Optional[dict]:
    try:
        if identifier.startswith('@'):
            # Видаляємо @ і пробуємо знайти через обидва способи
            username = identifier.lstrip('@')
            logger.debug(f"🔍 Пошук користувача @{username}")
            
            # Спроба 1: Пошук в базі даних (ПЕРШИЙ ВАРІАНТ)
            logger.info(f"🔍 Спроба 1: Пошук в БД за username '@{username}'")
            user_data = db.get_user_by_username(username)
            if user_data:
                logger.info(f"✅ ЗНАЙДЕНО в БД! user_id={user_data['user_id']}, username={user_data.get('username')}, full_name={user_data.get('full_name')}")
                return {
                    "user_id": user_data["user_id"],
                    "username": user_data.get("username", ""),
                    "full_name": user_data.get("full_name", "")
                }
            logger.info(f"⚠️ Не знайдено в БД по запиту '{username}'")
            
            # Спроба 2: Використовуємо get_chat з @username (API Telegram)
            logger.debug(f"🔍 Спроба 2: Пошук через Telegram API")
            try:
                chat = await context.bot.get_chat(f"@{username}")
                logger.debug(f"✅ Знайдено через API: {chat}")
                return {
                    "user_id": chat.id,
                    "username": chat.username or username,
                    "full_name": chat.full_name or chat.first_name or ""
                }
            except Exception as e:
                logger.debug(f"⚠️ API Telegram не знайшов: {e}")
            
            # Спроба 3: Пошук через get_chat_member в обох чатах
            logger.debug(f"🔍 Спроба 3: Пошук через get_chat_member в чатах")
            all_user_ids = db.get_all_users()
            for user_id in all_user_ids:
                try:
                    chat_member = await context.bot.get_chat_member(USER_CHAT_ID, user_id)
                    if chat_member.user.username and chat_member.user.username.lower() == username.lower():
                        logger.debug(f"✅ Знайдено в USER_CHAT: {chat_member.user}")
                        return {
                            "user_id": user_id,
                            "username": chat_member.user.username,
                            "full_name": chat_member.user.full_name or ""
                        }
                except:
                    pass
            
            logger.warning(f"❌ Користувача @{username} не знайдено")
            # Покращена помилка для користувача
            logger.info(f"⚠️ Можливі причини:")
            logger.info(f"   1. Користувач @{username} ніколи не писав повідомлення у бот/групу")
            logger.info(f"   2. Акаунт приватний або був видалений")
            logger.info(f"   3. Невірно введене ім'я користувача")
            return None
        else:
            # Пошук по ID
            user_id = int(identifier)
            try:
                chat_member = await context.bot.get_chat_member(USER_CHAT_ID, user_id)
                user = chat_member.user
            except:
                try:
                    if ADMIN_CHAT_ID:
                        chat_member = await context.bot.get_chat_member(ADMIN_CHAT_ID, user_id)
                        user = chat_member.user
                    else:
                        logger.error(f"Не вдалося знайти користувача з ID {user_id}")
                        return None
                except Exception as e:
                    logger.error(f"Не вдалося знайти користувача з ID {user_id}: {e}")
                    return None
            
            return {
                "user_id": user.id,
                "username": user.username or "",
                "full_name": user.full_name or ""
            }
    except Exception as e:
        logger.error(f"Помилка отримання інформації про користувача {identifier}: {e}")
        return None

def save_user_from_update(update: Update):
    """Сохранить пользователя в БД з інформацією з Update"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    username = update.effective_user.username or ""
    full_name = update.effective_user.full_name or ""
    
    db.add_or_update_user(user_id, username=username, full_name=full_name)
    logger.debug(f"💾 Збережено користувача: {user_id} (@{username}) {full_name}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.message:
        return
    
    # Сохраняем пользователя в БД
    save_user_from_update(update)
    
    help_text = """🎄 SANTA ADMIN BOT

Ласкаво просимо! 👋

/help - показати команди для користувачів"""
    
    await reply_and_delete(update, help_text, delay=60)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Команди для звичайних користувачів"""
    if not update.message:
        return
    
    help_text = (
        "📚 <b>КОМАНДИ ДЛЯ КОРИСТУВАЧІВ</b>\n\n"
        "👤 <b>ПЕРСОНАЛЬНІ НАЛАШТУВАННЯ:</b>\n"
        "/profile_set - налаштування профілю\n"
        "/myname - кастомне імʼя\n"
        "/del_myname - видалити імʼя\n"
        "/mym - встановити аватар (reply)\n"
        "/del_mym - видалити аватар\n"
        "/mymt - опис профілю\n"
        "/del_mymt - видалити опис\n"
        "/profile - свій профіль\n\n"
        
        "💍 <b>ШЛЮБ:</b>\n"
        "/marry @user - запропонувати шлюб\n"
        "/unmarry - розлучитися\n"
        "/marriages - всі шлюби\n"
        "<b>Мій шлюб</b> - карта шлюбу\n"
        "<b>Фото шлюбу</b> - фото карти (reply)\n\n"

        "📝 <b>НОТАТКИ ТА НАГАДУВАННЯ:</b>\n"
        "/note - зберегти нотатку\n"
        "/notes - ваші нотатки\n"
        "/delnote - видалити нотатку\n"
        "/reminder - нагадування собі\n"
        "/reminde - нагадування іншому\n\n"

        "🎂 <b>ДНІ НАРОДЖЕННЯ:</b>\n"
        "/birthdays - дні народження\n"
        "/addb - додати ДН\n"
        "/delb - видалити свій ДН\n\n"

        "👥 <b>ІНФОРМАЦІЯ:</b>\n"
        "/profile @user - чужий профіль\n"
        "/hto - інформація про юзера\n"
        "/alarm - виклик адмінів\n"
        "/online_list - адміни онлайн\n"
        "/help - ця справка"
    )
    
    await update.message.reply_text(help_text, parse_mode="HTML")

async def help_g_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Команди для гномів"""
    if not update.message or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    if not is_gnome(user_id) and not is_head_admin(user_id) and not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для гномів, головних адмінів і власника!")
        return
    
    help_text = """🧙 КОМАНДИ ДЛЯ ГНОМІВ

👤 ПЕРСОНАЛЬНІ НАЛАШТУВАННЯ:
/profile_set - показати всі команди налаштування профілю
/myname - встановити кастомне імʼя (видиме скрізь)
/del_myname - видалити кастомне імʼя
/mym - встановити профіль-гіфку/фото (reply на медіа)
/del_mym - видалити профіль-гіфку
/mymt - встановити опис профілю (до 300 символів)
/del_mymt - видалити опис профілю
/hto - переглянути свій профіль (без аргумента)
/custom_main - встановити кастомну посаду для адміна (reply)

🗣️ ВІДПРАВЛЕННЯ ПОВІДОМЛЕНЬ:
/say - надіслати повідомлення з підписом
/says - надіслати анонімне повідомлення
/sayon - увімкнути режим авто-відповіді з підписом
/sayson - увімкнути режим анонімних авто-відповідей
/sayoff - вимкнути режим авто-відповіді
/saypin - надіслати і закріпити повідомлення
/save_s - зберегти повідомлення в адмін-чат
/sayb - заблокувати можливість використання /say користувачу
/sayu - розблокувати можливість використання /say користувачу

📢 РОЗСИЛКА:
/broadcast - розсилка повідомлення всім

🚫 МОДЕРАЦІЯ:
/ban_s - тихий бан користувача
/ban_t - публічний бан користувача (reply на повідомлення)
/unban_s - тихе розблокування користувача
/unban_t - публічне розблокування користувача (reply)
/mute_s - тихий мут користувача
/mute_t - публічний мут (reply). Формат: /mute_t [час] [причина]
  Приклад: /mute_t 1m спам (мут на 1 хвилину)
  Час: 30s (секунди), 5m (хвилини), 2h (години)
/unmute_s - тихе розмут користувача
/unmute_t - публічне розмут (reply)
/kick - вигнати учасника з чату (reply)

📝 НОТАТКИ ТА НАГАДУВАННЯ (доступно ДЛЯ ВСІХ):
/note - зберегти нотатку
  Приклад: /note Привіт
/notes - показати свої нотатки
  Приклад: /notes (ваші) або /notes @username (чужі - тільки для власника)
/delnote - видалити нотатку за номером
  Приклад: /delnote 1
/reminder - створити нагадування для себе
/reminde - створити нагадування для іншого користувача

🎂 ДНІ НАРОДЖЕННЯ:
/birthdays - показати список днів народження
/addb - додати день народження користувачу
/delb - видалити свій день народження
/profile - переглянути профіль користувача

👥 ІНФОРМАЦІЯ:
/alarm - виклик адміністрації
/hto - інформація про користувача
/online_list - показати список адмінів онлайн
/help - команди для звичайних користувачів
/helpg - показати цю справку
/helpm - команди для головних адмінів (якщо у вас є права)"""
    
    await reply_and_delete(update, help_text, delay=60)

async def help_m_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Команди для головних адмінів"""
    if not update.message or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    if not is_head_admin(user_id) and not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для головних адмінів і власника!")
        return
    
    help_text = """👑 УНІКАЛЬНІ КОМАНДИ ДЛЯ ГОЛОВНИХ АДМІНІВ

🔑 УПРАВЛІННЯ ПРАВАМИ:
/giveperm - дати ВСІ права адміністратора
/giveperm_simple - дати звичайні права
/removeperm - забрати всі права
/admin_list - показати список всіх адмінів

🔧 УПРАВЛІННЯ ГНОМАМИ:
/add_gnome - додати гнома
/remove_gnome - видалити гнома

🚫 МОДЕРАЦІЯ:
/ban_s - тихий бан користувача (reply)
/ban_t - публічний бан користувача (reply). Формат: /ban_t [причина]
/unban_s - тихе розблокування користувача
/unban_t - публічне розблокування користувача (reply)
/mute_s - тихий мут користувача (reply)
/mute_t - публічний мут (reply). Формат: /mute_t [час] [причина]
  Приклад: /mute_t 1m спам (мут на 1 хвилину)
  Час: 30s (секунди), 5m (хвилини), 2h (години)
  Автоматичний розмут через вказаний час! ⏱️
/unmute_s - тихе розмут користувача (reply)
/unmute_t - публічне розмут (reply)
/kick - вигнати учасника з чату (reply)
/nah - додати користувача в чорний список

🗣️ ВІДПРАВЛЕННЯ ПОВІДОМЛЕНЬ:
/say - надіслати повідомлення з підписом
/says - надіслати анонімне повідомлення
/sayon - увімкнути режим авто-відповіді з підписом
/sayson - увімкнути режим анонімних авто-відповідей
/sayoff - вимкнути режим авто-відповіді
/sayoffall - вимкнути режим для ВСІХ користувачів
/saypin - надіслати і закріпити повідомлення
/save_s - зберегти повідомлення в адмін-чат
/sayb - заблокувати можливість використання /say користувачу
/sayu - розблокувати можливість використання /say користувачу

📢 РОЗСИЛКА:
/broadcast - розсилка повідомлення всім

👤 АДМІНІСТРАТОРСЬКА ПОСАДА:
/custom_main - встановити кастомну посаду для адміна (reply)

📚 ВСІ ІНШІ КОМАНДИ:
Використовуйте /help для користувацьких команд
Використовуйте /helpg для команд гномів"""
    
    await reply_and_delete(update, help_text, delay=60)

async def allcmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Всі команди для власника"""
    if not update.message or not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для власника!")
        return
    
    # Основна частина для всіх власників
    help_text = """🌟 ВСІ КОМАНДИ ВЛАСНИКА (65+)

👑 УПРАВЛІННЯ АДМІНАМИ:"""
    
    # Тільки для 7247114478 та 7516733683
    if user_id in [7247114478, 7516733683]:
        help_text += """
/add_owner - додати ще одного власника
/remove_owner - видалити власника"""
    
    help_text += """
/giveperm - дати адміністратора (ВСІ права)
/giveperm_simple - дати звичайну адміну (тільки відправка)
/removeperm - забрати права адміністратора
/add_main_admin - додати головного адміна
/remove_main_admin - видалити головного адміна
/add_gnome - додати гнома
/remove_gnome - видалити гнома
/admin_list - список всіх адмінів
/custom_main - встановити посаду для адміна

🎭 МОДЕРАЦІЯ:
/ban_s - тихий бан
/ban_t - публічний бан з причиною
/unban_s - тихе розблокування
/unban_t - публічне розблокування
/mute_s - тихий мут
/mute_t - публічний мут (⏱️ з авто-розмутом)
/unmute_s - тихе розмут
/unmute_t - публічне розмут
/kick - вигнати з групи

🚫 ЧОРНИЙ СПИСОК (ГЛОБАЛЬНИЙ):
/nah - додати користувача в чорний список
/unnah - видалити користувача з чорного списку
/nahlist - показати весь чорний список
/export_nah - експортувати чорний список у файл
/import_nah - імпортувати чорний список з файлу

🗣️ ВІДПРАВЛЕННЯ ПОВІДОМЛЕНЬ:
/say - відправити повідомлення з підписом
/says - тихе (анонімне) відправлення
/sayon - режим авто-відповіді з підписом
/sayson - анонімний режим авто-відповіді
/sayoff - вимкнути режим авто-відповіді
/sayoffall - вимкнути для ВСІХ
/saypin - закріпити останнє повідомлення
/save_s - зберегти повідомлення приватом
/sayb - заблокувати /say користувачу
/sayu - розблокувати /say користувачу
/santas - тихо зберегти в канал Санти

⚙️ ТЕКСТОВІ КОМАНДИ І МЕДІА:
/set_cmd - створити текстовий алiас команди
/del_cmd - видалити текстовий алiас
/set_personal - створити персональну команду з медіа
/set_cmdm - додати медіа до персональної команди
/del_cmdm - видалити медіа з команди
/del_personal - видалити персональну команду
/list_cmdm - список всіх персональних команд
/set_adminm - додати медіа (стікер/гіф) до адмін-команди
/del_adminm - видалити медіа з адмін-команди

🎂 ДЕНІ НАРОДЖЕННЯ:
/birthdays - показати список днів народження
/addb - додати день народження користувачу
/delb - видалити день народження
/setbtext - встановити текст привітань
/setbgif - встановити GIF для привітань
/previewb - попередній перегляд привітань

📢 РОЗСИЛКА:
/broadcast - розіслати повідомлення всім

📝 НОТАТКИ:
/note - зберегти приватну нотатку
/notes - показати нотатки
/delnote - видалити нотатку

⏰ НАГАДУВАННЯ:
/reminder - встановити нагадування
/reminde - нагадування для іншого користувача
/deltimer - таймер видалення відповідей (1-60 сек)

👤 ПРОФІЛЬ І ПЕРСОНАЛЬНІ ДАНІ:
/profile - переглянути профіль користувача
/profile_set - меню налаштування профілю
/myname - встановити кастомне ім'я (видиме скрізь)
/del_myname - видалити кастомне ім'я
/mym - встановити фото профілю
/del_mym - видалити фото профілю
/mymt - встановити опис профілю (до 300 символів)
/del_mymt - видалити опис профілю

👤 ІНФОРМАЦІЯ КОРИСТУВАЧІВ:
/hto - розширена інформація про користувача
/online_list - список адмінів у режимі авто-відповіді
/alarm - виклик адміністрації

👥 КОМАНДИ ТА РОЛІ:
/role_cmd - список персональних команд користувача
/personal - показати персональні команди

⚡ СПЕЦІАЛЬНІ ФУНКЦІЇ:
/doubler - дублювання повідомлення

💾 РЕЗЕРВНІ КОПІЇ:
/rezerv - зберегти резервну копію в канал
/import - восстановити дані з резервної копії

⚡️ КОНФІГУРАЦІЯ (тільки 7247114478 та 7516733683):
/adminchat - встановити ID адмін-чату
/userchat - встановити ID чату користувачів
/logchannel - встановити ID каналу логування
/testchannel - встановити ID тестового каналу
/restart - перезапустити бота
/set_timer - встановити таймер видалення повідомлень (сек)
/add_secondary - додати додатковий чат (ID)
/remove_secondary - видалити додатковий чат (ID)
/secondary_list - список додаткових чатів

📚 ІНШІ КОМАНДИ:
Використовуйте /helpm для команд Head Admin
Використовуйте /helpg для команд гномів
Використовуйте /help для команд звичайних користувачів"""
    
    await reply_and_delete(update, help_text, delay=120)

async def add_gnome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_manage_gnomes(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    db.add_role(target_user["user_id"], "gnome", user_id, target_user["full_name"], target_user["username"])
    
    admin_name = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
    
    target_name = get_display_name(target_user["user_id"], target_user["full_name"])
    target_username = f"@{target_user['username']}" if target_user["username"] else ""
    clickable_target = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
    
    role_text = "Власник" if is_owner(user_id) else "Головний адмін"
    
    message = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
➕ Призначив гномом
{clickable_target} {target_username} [{target_user['user_id']}]"""
    
    await reply_and_delete(update, f"✅ {clickable_target} призначений гномом!", delay=60, parse_mode="HTML")
    
    await log_to_channel(context, message + "\n#add_gnome")
    db.log_action("add_gnome", user_id, target_user["user_id"], message)

async def remove_gnome_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_manage_gnomes(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    db.remove_role(target_user["user_id"])
    
    admin_name = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
    
    target_name = get_display_name(target_user["user_id"], target_user["full_name"])
    target_username = f"@{target_user['username']}" if target_user["username"] else ""
    clickable_target = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
    
    role_text = "Власник" if is_owner(user_id) else "Головний адмін"
    
    message = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
➖ Видалив гнома
{clickable_target} {target_username} [{target_user['user_id']}]"""
    
    await reply_and_delete(update, f"✅ {clickable_target} видалений з гномів!", delay=60, parse_mode="HTML")
    
    await log_to_channel(context, message + "\n#remove_gnome")
    db.log_action("remove_gnome", user_id, target_user["user_id"], message)

async def add_main_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може додавати головних адмінів!")
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    db.add_role(target_user["user_id"], "head_admin", user_id, target_user["full_name"], target_user["username"])
    
    admin_name = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
    
    target_name = get_display_name(target_user["user_id"], target_user["full_name"])
    target_username = f"@{target_user['username']}" if target_user["username"] else ""
    clickable_target = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
    
    message = f"""Власник
{clickable_admin} {admin_username} [{user_id}]
➕ Призначив Головним адміном
{clickable_target} {target_username} [{target_user['user_id']}]"""
    
    await reply_and_delete(update, f"✅ {clickable_target} призначений головним адміном!", delay=60, parse_mode="HTML")
    
    await log_to_channel(context, message + "\n#add_main_admin")
    db.log_action("add_main_admin", user_id, target_user["user_id"], message)

async def remove_main_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може видаляти головних адмінів!")
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    db.remove_role(target_user["user_id"])
    
    admin_name = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
    
    target_name = get_display_name(target_user["user_id"], target_user["full_name"])
    target_username = f"@{target_user['username']}" if target_user["username"] else ""
    clickable_target = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
    
    message = f"""Власник
{clickable_admin} {admin_username} [{user_id}]
➖ Видалив Головного адміна
{clickable_target} {target_username} [{target_user['user_id']}]"""
    
    await reply_and_delete(update, f"✅ {clickable_target} видалений з головних адмінів!", delay=60, parse_mode="HTML")
    
    await log_to_channel(context, message + "\n#remove_main_admin")
    db.log_action("remove_main_admin", user_id, target_user["user_id"], message)

async def add_owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Тільки 7247114478 та 7516733683 можуть додавати власників
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Тільки власники 7247114478 та 7516733683 можуть додавати нових власників!")
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи не власник вже
    if target_user["user_id"] in OWNER_IDS:
        await reply_and_delete(update, f"❌ {target_user['full_name']} вже є власником!", delay=60, parse_mode="HTML")
        return
    
    # Додаємо власника
    OWNER_IDS.append(target_user["user_id"])
    save_config()
    
    target_name = get_display_name(target_user["user_id"], target_user["full_name"])
    target_username = f"@{target_user['username']}" if target_user["username"] else ""
    clickable_target = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
    
    message = f"""👑 Новий Власник
{clickable_target} {target_username} [{target_user['user_id']}]
Додано як власник бота!"""
    
    await reply_and_delete(update, f"✅ {clickable_target} призначений власником!", delay=60, parse_mode="HTML")
    
    await log_to_channel(context, message + "\n#add_owner")
    db.log_action("add_owner", user_id, target_user["user_id"], message)

async def remove_owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Тільки 7247114478 та 7516733683 можуть видаляти власників
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Тільки власники 7247114478 та 7516733683 можуть видаляти власників!")
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи не власник
    if target_user["user_id"] not in OWNER_IDS:
        await reply_and_delete(update, f"❌ {target_user['full_name']} не є власником!", delay=60, parse_mode="HTML")
        return
    
    # Видаляємо власника
    OWNER_IDS.remove(target_user["user_id"])
    save_config()
    
    target_name = get_display_name(target_user["user_id"], target_user["full_name"])
    target_username = f"@{target_user['username']}" if target_user["username"] else ""
    clickable_target = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
    
    message = f"""👑 Видалено Власника
{clickable_target} {target_username} [{target_user['user_id']}]
Більше не є власником бота."""
    
    await reply_and_delete(update, f"✅ {clickable_target} видалений з власників!", delay=60, parse_mode="HTML")
    
    await log_to_channel(context, message + "\n#remove_owner")
    db.log_action("remove_owner", user_id, target_user["user_id"], message)


async def ban_s_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи це адмін (тільки власник може блокувати адміністраторів)
    if can_access_admin_commands(target_user["user_id"]) and not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки Власник може блокувати адміністраторів!", delay=60)
        return
    
    try:
        await context.bot.ban_chat_member(USER_CHAT_ID, target_user["user_id"])
        db.add_ban(target_user["user_id"], user_id, "Тихий бан", 
                   update.effective_user.full_name or "", update.effective_user.username or "")
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        msg_text = f"🚫 {target_mention} заблокований.\nАдмін: {admin_mention}"
        
        if USER_CHAT_ID:
            await context.bot.send_message(
                chat_id=USER_CHAT_ID,
                text=msg_text,
                parse_mode="HTML"
            )
        
        admin_name = safe_send_message(get_display_name(user_id, update.effective_user.full_name or "Невідомий"))
        admin_username = update.effective_user.username or ""
        target_name = safe_send_message(get_display_name(target_user["user_id"], target_user["full_name"]))
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
        
        log_message = f"""🚷 #BAN
• Хто: {admin_mention} ({admin_username}) [{user_id}]
• Кому: {target_mention} [{target_user['user_id']}]
• Група: {USER_CHAT_ID}
#id{target_user['user_id']}"""
        
        await log_to_channel(context, log_message, parse_mode="HTML")
        await reply_and_delete(update, "✅ Користувача заблоковано (тихо)")
        db.log_action("ban_s", user_id, target_user["user_id"], log_message)
    except Exception as e:
        logger.error(f"Помилка бану: {e}")
        await reply_and_delete(update, f"❌ Боту потрібні права або помилка: {e}", delay=60)

async def ban_t_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    reason = ""
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
        reason = " ".join(context.args) if context.args else ""
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи це адмін (тільки власник може блокувати адміністраторів)
    if can_access_admin_commands(target_user["user_id"]) and not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки Власник може блокувати адміністраторів!", delay=60)
        return
    
    try:
        await context.bot.ban_chat_member(USER_CHAT_ID, target_user["user_id"])
        db.add_ban(target_user["user_id"], user_id, reason if reason else "Порушення правил", 
                   update.effective_user.full_name or "", update.effective_user.username or "")
        
        target_display = get_display_name(target_user["user_id"], target_user['full_name'])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        # Динамічно збираємо повідомлення
        msg_parts = [f"🚫 {target_mention} заблокований.", "До: ∞"]
        if reason:
            msg_parts.append(f"Причина: {reason}")
        msg_parts.append(f"Адмін: {admin_mention}")
        msg_text = "\n".join(msg_parts)
        
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text=msg_text,
            parse_mode="HTML"
        )
        
        try:
            await context.bot.send_message(
                chat_id=target_user["user_id"],
                text=f"Ви були заблоковані. Причина: {reason}",
                parse_mode=None
            )
        except:
            pass
        
        admin_name = safe_send_message(get_display_name(user_id, update.effective_user.full_name or "Невідомий"))
        admin_username = update.effective_user.username or ""
        target_name = safe_send_message(get_display_name(target_user["user_id"], target_user["full_name"]))
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
        
        log_message = f"""🚷 #BAN
• Хто: {admin_mention} ({admin_username}) [{user_id}]
• Кому: {target_mention} [{target_user['user_id']}]
• Причина: {reason}
• Група: {USER_CHAT_ID}
#id{target_user['user_id']}"""
        
        await log_to_channel(context, log_message, parse_mode="HTML")
        await reply_and_delete(update, "✅ Користувача заблоковано публічно", delay=60)
        db.log_action("ban_t", user_id, target_user["user_id"], log_message)
    except Exception as e:
        logger.error(f"Помилка бану: {e}")
        await reply_and_delete(update, f"❌ Боту потрібні права або помилка: {e}", delay=60)

async def unban_s_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи користувач заблокований
    if not db.is_banned(target_user["user_id"]):
        await reply_and_delete(update, "❌ Користувач не був заблокований!", delay=60)
        return
    
    # Перевіряємо чи це адмін
    if can_access_admin_commands(target_user["user_id"]):
        await reply_and_delete(update, "❌ Неможливо розблокувати адміністратора!", delay=60)
        return
    
    try:
        await context.bot.unban_chat_member(USER_CHAT_ID, target_user["user_id"])
        db.remove_ban(target_user["user_id"])
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        msg_text = f"✅ {target_mention} розблокований.\nАдмін: {admin_mention}"
        
        if USER_CHAT_ID:
            await context.bot.send_message(
                chat_id=USER_CHAT_ID,
                text=msg_text,
                parse_mode="HTML"
            )
        
        await reply_and_delete(update, "✅ Користувача розблоковано (тихо)")
        db.log_action("unban_s", user_id, target_user["user_id"])
    except Exception as e:
        logger.error(f"Помилка команди: {e}")
        try:
            await reply_and_delete(update, f"❌ Помилка: {e}", delay=60)
        except:
            pass

async def unban_t_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи користувач заблокований
    if not db.is_banned(target_user["user_id"]):
        await reply_and_delete(update, "❌ Користувач не був заблокований!", delay=60)
        return
    
    # Перевіряємо чи це адмін
    if can_access_admin_commands(target_user["user_id"]):
        await reply_and_delete(update, "❌ Неможливо розблокувати адміністратора!", delay=60)
        return
    
    try:
        await context.bot.unban_chat_member(USER_CHAT_ID, target_user["user_id"])
        db.remove_ban(target_user["user_id"])
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        logger.info(f"🔍 unban_t: target_user={repr(target_user)}, target_display={repr(target_display)}, target_mention={repr(target_mention)}")
        
        msg_text = f"✅ {target_mention} розблокований.\nАдмін: {admin_mention}"
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text=msg_text,
            parse_mode="HTML"
        )
        
        admin_name = safe_send_message(get_display_name(user_id, update.effective_user.full_name or "Невідомий"))
        admin_username = update.effective_user.username or ""
        target_name = safe_send_message(get_display_name(target_user["user_id"], target_user["full_name"]))
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
        
        log_message = f"""✅ #UNBAN
• Хто: {admin_mention} ({admin_username}) [{user_id}]
• Кого: {target_mention} [{target_user['user_id']}]
• Група: {USER_CHAT_ID}
#id{target_user['user_id']}"""
        
        await log_to_channel(context, log_message, parse_mode="HTML")
        await reply_and_delete(update, "✅ Користувача розблоковано публічно", delay=60)
        db.log_action("unban_t", user_id, target_user["user_id"], log_message)
    except Exception as e:
        logger.error(f"Помилка команди: {e}")
        try:
            await reply_and_delete(update, f"❌ Помилка: {e}", delay=60)
        except:
            pass

async def mute_s_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    import re
    mute_duration = None
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
        # Перевіряємо чи це адмін (тільки власник може мутити адміністраторів)
        if can_access_admin_commands(target_user["user_id"]) and not is_owner(user_id):
            await reply_and_delete(update, "❌ Тільки Власник може мутити адміністраторів!", delay=60)
            return
        if context.args:
            first_arg = context.args[0]
            time_match = re.match(r'^(\d+)([smh])$', first_arg.lower())
            if time_match:
                value = int(time_match.group(1))
                unit = time_match.group(2)
                if unit == 's':
                    mute_duration = value
                elif unit == 'm':
                    mute_duration = value * 60
                elif unit == 'h':
                    mute_duration = value * 3600
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
        if context.args and len(context.args) > 1:
            first_arg = context.args[1]
            time_match = re.match(r'^(\d+)([smh])$', first_arg.lower())
            if time_match:
                value = int(time_match.group(1))
                unit = time_match.group(2)
                if unit == 's':
                    mute_duration = value
                elif unit == 'm':
                    mute_duration = value * 60
                elif unit == 'h':
                    mute_duration = value * 3600
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    try:
        permissions = ChatPermissions(can_send_messages=False)
        await context.bot.restrict_chat_member(USER_CHAT_ID, target_user["user_id"], permissions)
        db.add_mute(target_user["user_id"], user_id, "Тихий мут", 
                    update.effective_user.full_name or "", update.effective_user.username or "")
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        until_time = get_unmute_time_str(mute_duration) if mute_duration and mute_duration > 0 else "∞"
        msg_text = f"🔇 {target_mention} замучений.\nДо: {until_time}\nАдмін: {admin_mention}"
        
        if USER_CHAT_ID:
            await context.bot.send_message(
                chat_id=USER_CHAT_ID,
                text=msg_text,
                parse_mode="HTML"
            )
        
        if mute_duration and mute_duration > 0:
            async def auto_unmute(bot, user_id_to_unmute, duration):
                await asyncio.sleep(duration)
                try:
                    permissions = ChatPermissions(
                        can_send_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True
                    )
                    await bot.restrict_chat_member(USER_CHAT_ID, user_id_to_unmute, permissions)
                    db.remove_mute(user_id_to_unmute)
                except Exception as e:
                    logger.error(f"❌ Помилка при автоматичному анмуті: {e}")
            asyncio.create_task(auto_unmute(context.bot, target_user["user_id"], mute_duration))
        
        await reply_and_delete(update, "✅ Користувача замучено (тихо)")
        db.log_action("mute_s", user_id, target_user["user_id"])
    except Exception as e:
        await reply_and_delete(update, f"❌ Боту потрібні права або помилка: {e}", delay=60)

async def mute_t_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    # Парсим час з першого аргументу, якщо він в форматі часу (1m, 2h, 30s)
    import re
    mute_duration = None
    reason = ""
    
    if context.args:
        first_arg = context.args[0]
        # Перевіримо, чи перший аргумент це час (1m, 2h, 30s і т.д.)
        time_match = re.match(r'^(\d+)([smh])$', first_arg.lower())
        if time_match:
            value = int(time_match.group(1))
            unit = time_match.group(2)
            # Конвертуємо в секунди
            if unit == 's':
                mute_duration = value
            elif unit == 'm':
                mute_duration = value * 60
            elif unit == 'h':
                mute_duration = value * 3600
            # Причина - якщо є аргументи після часу, беремо їх
            reason_parts = context.args[1:] if len(context.args) > 1 else []
            reason = " ".join(reason_parts) if reason_parts else ""
        else:
            # Якщо це не час - весь текст це причина
            reason = " ".join(context.args)
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    
    if not target_user:
        await reply_and_delete(update, "❌ Відповідьте на повідомлення користувача!", delay=60)
        return
    
    try:
        permissions = ChatPermissions(can_send_messages=False)
        await context.bot.restrict_chat_member(USER_CHAT_ID, target_user["user_id"], permissions)
        db.add_mute(target_user["user_id"], user_id, reason, 
                    update.effective_user.full_name or "", update.effective_user.username or "")
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        until_time = get_unmute_time_str(mute_duration) if mute_duration and mute_duration > 0 else "∞"
        
        # Збираємо повідомлення динамічно
        msg_parts = [f"🔇 {target_mention} замучений.", f"До: {until_time}"]
        if reason:
            msg_parts.append(f"Причина: {reason}")
        msg_parts.append(f"Адмін: {admin_mention}")
        msg_text = "\n".join(msg_parts)
        
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text=msg_text,
            parse_mode="HTML"
        )
        
        # Якщо вказано час - запланувати автоматичний анмут
        if mute_duration and mute_duration > 0:
            async def auto_unmute(bot, user_id_to_unmute, duration):
                logger.info(f"⏱️ Запланований анмут на {duration} секунд для {user_id_to_unmute}")
                await asyncio.sleep(duration)
                try:
                    permissions = ChatPermissions(
                        can_send_messages=True,
                        can_send_polls=True,
                        can_send_other_messages=True,
                        can_add_web_page_previews=True
                    )
                    await bot.restrict_chat_member(USER_CHAT_ID, user_id_to_unmute, permissions)
                    db.remove_mute(user_id_to_unmute)
                    logger.info(f"✅ Автоматичний анмут виконано для {user_id_to_unmute}")
                except Exception as e:
                    logger.error(f"❌ Помилка при автоматичному анмуті: {e}")
            asyncio.create_task(auto_unmute(context.bot, target_user["user_id"], mute_duration))
        
        db.log_action("mute_t", user_id, target_user["user_id"], reason)
    except Exception as e:
        await reply_and_delete(update, f"❌ Боту потрібні права або помилка: {e}", delay=60)

async def unmute_s_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи користувач е мучений
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT is_active FROM mutes WHERE user_id = ? AND is_active = 1', (target_user["user_id"],))
    is_muted_result = cursor.fetchone()
    conn.close()
    if not is_muted_result:
        await reply_and_delete(update, "❌ Користувач не є мучений!", delay=60)
        return
    
    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        await context.bot.restrict_chat_member(USER_CHAT_ID, target_user["user_id"], permissions)
        db.remove_mute(target_user["user_id"])
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        msg_text = f"🔊 {target_mention} розмучений.\nАдмін: {admin_mention}"
        
        logger.info(f"🔊 [unmute_s] USER_CHAT_ID: {USER_CHAT_ID}, текст: {msg_text[:50]}...")
        
        if USER_CHAT_ID:
            try:
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=msg_text,
                    parse_mode="HTML"
                )
                logger.info(f"✅ [unmute_s] Повідомлення відправлено в чат {USER_CHAT_ID}")
            except Exception as send_err:
                logger.error(f"❌ [unmute_s] Помилка при відправці повідомлення: {send_err}")
        else:
            logger.warning(f"⚠️ [unmute_s] USER_CHAT_ID не встановлено!")
        
        await reply_and_delete(update, "✅ Користувача розмучено (тихо)")
        db.log_action("unmute_s", user_id, target_user["user_id"])
    except Exception as e:
        logger.error(f"Помилка команди: {e}")
        try:
            await reply_and_delete(update, f"❌ Помилка: {e}", delay=60)
        except:
            pass

async def unmute_t_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
    else:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    if not target_user:
        await reply_and_delete(update, "❌ Користувача не знайдено!", delay=60)
        return
    
    # Перевіряємо чи користувач е мучений
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT is_active FROM mutes WHERE user_id = ? AND is_active = 1', (target_user["user_id"],))
    is_muted_result = cursor.fetchone()
    conn.close()
    if not is_muted_result:
        await reply_and_delete(update, "❌ Користувач не є мучений!", delay=60)
        return
    
    try:
        permissions = ChatPermissions(
            can_send_messages=True,
            can_send_polls=True,
            can_send_other_messages=True,
            can_add_web_page_previews=True
        )
        await context.bot.restrict_chat_member(USER_CHAT_ID, target_user["user_id"], permissions)
        db.remove_mute(target_user["user_id"])
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        msg_text = f"🔊 {target_mention} розмучений.\nАдмін: {admin_mention}"
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text=msg_text,
            parse_mode="HTML"
        )
        db.log_action("unmute_t", user_id, target_user["user_id"])
    except Exception as e:
        logger.error(f"Помилка команди: {e}")
        try:
            await reply_and_delete(update, f"❌ Помилка: {e}", delay=60)
        except:
            pass

async def kick_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!", delay=60)
        return
    
    reason = ""
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
        reason = " ".join(context.args) if context.args else ""
    elif context.args:
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
        reason = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    else:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!", delay=60)
        return
    
    # Перевіряємо чи це адмін (тільки власник може кікувати адміністраторів)
    if can_access_admin_commands(target_user["user_id"]) and not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки Власник може кікувати адміністраторів!", delay=60)
        return
    
    try:
        await context.bot.ban_chat_member(USER_CHAT_ID, target_user["user_id"])
        await context.bot.unban_chat_member(USER_CHAT_ID, target_user["user_id"])
        
        target_display = get_display_name(target_user["user_id"], target_user["full_name"])
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
        admin_display = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_display}</a>"
        
        msg_parts = [f"👟 {target_mention} вигнаний."]
        if reason:
            msg_parts.append(f"Причина: {reason}")
        msg_parts.append(f"Адмін: {admin_mention}")
        msg_text = "\n".join(msg_parts)
        
        if USER_CHAT_ID:
            await context.bot.send_message(
                chat_id=USER_CHAT_ID,
                text=msg_text,
                parse_mode="HTML"
            )
        
        admin_name = safe_send_message(get_display_name(user_id, update.effective_user.full_name or "Невідомий"))
        admin_username = update.effective_user.username or ""
        target_name = safe_send_message(get_display_name(target_user["user_id"], target_user["full_name"]))
        admin_mention = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
        target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
        
        log_message = f"""👟 #KICK
• Хто: {admin_mention} ({admin_username}) [{user_id}]
• Кого: {target_mention} [{target_user['user_id']}]
• Група: {USER_CHAT_ID}
#id{target_user['user_id']}"""
        
        await log_to_channel(context, log_message, parse_mode="HTML")
        await reply_and_delete(update, "✅ Користувача вигнано з чату", delay=60)
        db.log_action("kick", user_id, target_user["user_id"], log_message)
    except Exception as e:
        await reply_and_delete(update, f"❌ Боту потрібні права або помилка: {e}", delay=60)

async def nah_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може додавати в чорний список!")
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        
        # Якщо це ID (цифри) - спробувати витягнути інформацію з Telegram
        if identifier.isdigit():
            user_id_to_add = int(identifier)
            try:
                user_info = await context.bot.get_chat(user_id_to_add)
                target_user = {
                    "user_id": user_id_to_add,
                    "username": user_info.username or "",
                    "full_name": user_info.full_name or ""
                }
            except:
                # Якщо не можна витягнути - використовувати як є
                target_user = {
                    "user_id": user_id_to_add,
                    "username": "",
                    "full_name": ""
                }
        else:
            # Якщо це @username - спробувати знайти в БД
            target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!")
        return
    
    db.add_to_blacklist(target_user["user_id"], user_id, "Чорний список", 
                        update.effective_user.full_name or "", update.effective_user.username or "",
                        target_user.get("full_name", ""), target_user.get("username", ""))
    
    try:
        await context.bot.ban_chat_member(USER_CHAT_ID, target_user["user_id"])
    except:
        pass
    
    admin_name = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
    admin_username = update.effective_user.username or ""
    target_name = get_display_name(target_user["user_id"], target_user["full_name"])
    target_username = target_user.get("username", "") or ""
    
    admin_mention = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
    target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
    
    admin_username_str = f"@{admin_username}" if admin_username else ""
    target_username_str = f"@{target_username}" if target_username else ""
    
    log_message = f"""🚫 #BLACKLIST
• Хто: {admin_mention} ({admin_username_str}) [{user_id}]
• Кого: {target_mention} ({target_username_str}) [{target_user['user_id']}]
• Причина: Чорний список
#id{target_user['user_id']}"""
    
    await reply_and_delete(update, f"✅ {target_mention} додано в чорний список!", parse_mode="HTML", delay=60)
    await log_to_channel(context, log_message, parse_mode="HTML")
    db.log_action("blacklist", user_id, target_user["user_id"], log_message)

async def unnah_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може видаляти з чорного списку!")
        return
    
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    elif context.args:
        identifier = context.args[0]
        
        # Якщо це ID (цифри) - спробувати витягнути інформацію з Telegram
        if identifier.isdigit():
            user_id_to_add = int(identifier)
            try:
                user_info = await context.bot.get_chat(user_id_to_add)
                target_user = {
                    "user_id": user_id_to_add,
                    "username": user_info.username or "",
                    "full_name": user_info.full_name or ""
                }
            except:
                # Якщо не можна витягнути - використовувати як є
                target_user = {
                    "user_id": user_id_to_add,
                    "username": "",
                    "full_name": ""
                }
        else:
            # Якщо це @username - спробувати знайти в БД
            target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Вкажіть ID, @username або відповідьте на повідомлення користувача!")
        return
    
    db.remove_from_blacklist(target_user["user_id"])
    target_display = get_display_name(target_user["user_id"], target_user["full_name"])
    target_mention = f"<a href='tg://user?id={target_user['user_id']}'>{target_display}</a>"
    
    await reply_and_delete(update, f"✅ {target_mention} видалено з чорного списку!", parse_mode="HTML", delay=60)
    db.log_action("remove_blacklist", user_id, target_user["user_id"])

async def nahlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може переглядати чорний список!")
        return
    
    blacklist = db.get_all_blacklist()
    
    if not blacklist:
        await reply_and_delete(update, "✅ Чорний список порожній!", delay=60)
        return
    
    msg = "🚫 ЧОРНИЙ СПИСОК\n\n"
    for idx, bl in enumerate(blacklist, 1):
        uid = bl.get("user_id")
        name = bl.get("user_full_name") or "Невідомий"
        username = bl.get("user_username", "")
        
        user_line = f"{idx}. <a href='tg://user?id={uid}'>{name}</a>"
        if username:
            user_line += f" @{username}"
        msg += f"{user_line}\nID: <code>{uid}</code>\n\n"
    
    await reply_and_delete(update, msg, parse_mode="HTML", delay=120)

async def export_nah_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може експортувати чорний список!")
        return
    
    blacklist = db.get_all_blacklist()
    
    if not blacklist:
        await reply_and_delete(update, "❌ Чорний список порожній!", delay=60)
        return
    
    content = ""
    for bl in blacklist:
        uid = bl.get("user_id")
        name = bl.get("user_full_name") or "Невідомий"
        content += f"{uid}|{name}\n"
    
    doc_file = io.BytesIO(content.encode('utf-8'))
    doc_file.seek(0)
    
    # Надсилаємо файл в приватні повідомлення
    await context.bot.send_document(
        chat_id=user_id,
        document=doc_file,
        filename="blacklist_export.txt"
    )
    
    # Надсилаємо файл в лог канал
    doc_file.seek(0)
    if LOG_CHANNEL_ID:
        try:
            await context.bot.send_document(
                chat_id=LOG_CHANNEL_ID,
                document=doc_file,
                filename="blacklist_export.txt",
                caption="📤 Експорт чорного списку"
            )
            logger.info(f"📤 [export_nah] Файл експортовано в лог канал")
        except Exception as e:
            logger.warning(f"⚠️ [export_nah] Помилка надіслання в лог канал: {e}")
    
    # Повідомлення в приватний чат
    await context.bot.send_message(
        chat_id=user_id,
        text="✅ Чорний список експортовано в приватні повідомлення та лог канал!"
    )
    logger.info(f"✅ [export_nah] Експорт завершено власником {user_id}")

async def process_blacklist_import(update: Update, context: ContextTypes.DEFAULT_TYPE, file: Any) -> tuple:
    """Спільна функція обробки імпорту чорного списку"""
    added = 0
    failed = 0
    
    try:
        content = await file.download_as_bytearray()
        lines = content.decode('utf-8').strip().split('\n')
        
        for idx, line in enumerate(lines, 1):
            if not line.strip():
                continue
            parts = line.split('|')
            if len(parts) >= 1:
                try:
                    uid = int(parts[0].strip())
                    if uid > 0:
                        # Витягуємо ім'я з Telegram API
                        user_full_name = ""
                        user_username = ""
                        try:
                            user_info = await context.bot.get_chat(uid)
                            user_full_name = user_info.full_name or ""
                            user_username = user_info.username or ""
                        except:
                            pass
                        
                        user_id = update.effective_user.id if update.effective_user else 0
                        db.add_to_blacklist(uid, user_id, "Імпорт", "", "", user_full_name, user_username)
                        added += 1
                        logger.info(f"✅ [import_nah] Додано ID {uid}, ім'я: {user_full_name}")
                except Exception as parse_err:
                    failed += 1
                    logger.warning(f"⚠️ [import_nah] Помилка парсингу лінії {idx}: {line} - {parse_err}")
    except Exception as e:
        logger.error(f"❌ [import_nah] Помилка обробки файлу: {e}")
        return 0, 1
    
    return added, failed

async def import_nah_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може імпортувати чорний список!")
        return
    
    # Шукаємо документ у 2 місцях: у самому повідомленні ЧИ у reply
    document = None
    if update.message.document:
        document = update.message.document
        logger.info(f"📥 [import_nah] Документ у повідомленні команди: {document.file_name}")
    elif update.message.reply_to_message and update.message.reply_to_message.document:
        document = update.message.reply_to_message.document
        logger.info(f"📥 [import_nah] Документ у reply: {document.file_name}")
    
    if not document:
        await reply_and_delete(update, "❌ Додайте текстовий файл для імпорту або відповідьте на файл командою /import_nah!")
        return
    
    try:
        file = await context.bot.get_file(document.file_id)
        added, failed = await process_blacklist_import(update, context, file)
        
        msg = f"✅ Імпортовано {added} користувачів!"
        if failed > 0:
            msg += f"\n⚠️ Пропущено {failed} рядків"
        
        # Надсилаємо результат в приватні повідомлення
        await context.bot.send_message(chat_id=user_id, text=msg)
        
        # Надсилаємо результат в лог канал
        if LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=f"📥 Імпорт чорного списку\n{msg}"
                )
                logger.info(f"📥 [import_nah] Результат надіслано в лог канал")
            except Exception as e:
                logger.warning(f"⚠️ [import_nah] Помилка надіслання в лог канал: {e}")
        
        logger.info(f"✅ [import_nah] Імпорт завершено: {added} успішно, {failed} помилок")
    except Exception as e:
        logger.error(f"❌ [import_nah] Помилка імпорту: {e}")
        msg_error = f"❌ Помилка імпорту: {e}"
        await context.bot.send_message(chat_id=user_id, text=msg_error)

async def handle_document_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє завантажені документи - якщо це blacklist_export.txt, робить імпорт"""
    if not update.message or not update.message.document or not update.effective_user:
        return
    
    filename = update.message.document.file_name or "unknown"
    user_id = update.effective_user.id
    
    logger.info(f"📄 [handle_document_import] Отримано документ: '{filename}' від {user_id}")
    
    # Перевіряємо чи це файл чорного списку
    if filename.lower() == "blacklist_export.txt" or filename.lower().endswith("blacklist_export.txt"):
        if not is_owner(user_id):
            await reply_and_delete(update, "❌ Тільки власник може імпортувати чорний список!")
            return
        
        logger.info(f"📥 [handle_document_import] Знайдено файл {filename} від {user_id}")
        
        try:
            file = await context.bot.get_file(update.message.document.file_id)
            added, failed = await process_blacklist_import(update, context, file)
            
            msg = f"✅ Імпортовано {added} користувачів!"
            if failed > 0:
                msg += f"\n⚠️ Пропущено {failed} рядків"
            
            # Надсилаємо результат в приватні повідомлення
            await context.bot.send_message(chat_id=user_id, text=msg)
            
            # Надсилаємо результат в лог канал
            if LOG_CHANNEL_ID:
                try:
                    await context.bot.send_message(
                        chat_id=LOG_CHANNEL_ID,
                        text=f"📥 Імпорт чорного списку (автоматичний)\n{msg}"
                    )
                    logger.info(f"📥 [handle_document_import] Результат надіслано в лог канал")
                except Exception as e:
                    logger.warning(f"⚠️ [handle_document_import] Помилка надіслання в лог канал: {e}")
            
            # Видаляємо оригінальне повідомлення з файлом
            try:
                await context.bot.delete_message(chat_id=update.effective_chat.id, message_id=update.message.message_id)
            except:
                pass
            
            logger.info(f"✅ [handle_document_import] Імпорт завершено: {added} успішно, {failed} помилок")
        except Exception as e:
            logger.error(f"❌ [handle_document_import] Помилка імпорту: {e}")
            msg_error = f"❌ Помилка імпорту: {e}"
            await context.bot.send_message(chat_id=user_id, text=msg_error)


async def say_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    if db.is_say_blocked(user_id):
        await reply_and_delete(update, "❌ Вашу можливість використання /say заблоковано!")
        return
    
    # Парсимо параметри команди для отримання ID чату
    target_chat_id = None
    if context.args and len(context.args) > 0:
        try:
            # Спробуємо парсити перший параметр як chat ID
            test_id = int(context.args[0])
            # Видаляємо перший параметр з args для обробки команди
            context.args = context.args[1:]
            target_chat_id = test_id
            logger.info(f"📤 /say: Target chat ID: {target_chat_id}")
        except ValueError:
            # Перший параметр не число, залишаємо його в args
            pass
    
    forward_to = target_chat_id if target_chat_id else USER_CHAT_ID
    
    if not forward_to:
        await reply_and_delete(update, "❌ Не налаштовано чат користувачів!")
        return
    
    author_name = safe_send_message(update.effective_user.full_name or "Невідомий")
    username = f"@{safe_send_message(update.effective_user.username)}" if update.effective_user.username else ""
    signature = f"— {author_name} {username}"
    
    try:
        if update.message.reply_to_message:
            replied_message = update.message.reply_to_message
            
            # Якщо вказаний текст після /say - відправити як reply в forward_to
            if context.args:
                message_text = ' '.join(context.args)
                clean_message = sanitize_message_text(message_text)
                final_message = f"{clean_message}\n\n{signature}"
                
                await context.bot.send_message(
                    chat_id=forward_to,
                    text=final_message,
                    reply_to_message_id=replied_message.message_id,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
                logger.info(f"📤 /say: текст від {user_id} як reply на {replied_message.message_id} відправлено в {forward_to}")
            else:
                # Без тексту - пересилати саме повідомлення в forward_to
                if replied_message.text:
                    clean_message = sanitize_message_text(replied_message.text)
                    final_message = f"{clean_message}\n\n{signature}"
                    await context.bot.send_message(
                        chat_id=forward_to,
                        text=final_message,
                        parse_mode=None,
                        disable_web_page_preview=True
                    )
                elif replied_message.caption:
                    clean_caption = sanitize_message_text(replied_message.caption)
                    final_message = f"{clean_caption}\n\n{signature}"
                    await context.bot.send_message(
                        chat_id=forward_to,
                        text=final_message,
                        parse_mode=None,
                        disable_web_page_preview=True
                    )
                else:
                    if update.effective_chat:
                        await context.bot.forward_message(
                            chat_id=forward_to,
                            from_chat_id=update.effective_chat.id,
                            message_id=replied_message.message_id
                        )
                    await context.bot.send_message(
                        chat_id=forward_to,
                        text=signature,
                        parse_mode=None,
                        disable_web_page_preview=True
                    )
                logger.info(f"📤 /say: повідомлення від {user_id} пересилано в {forward_to}")
        elif context.args:
            message_text = ' '.join(context.args)
            
            # Перевіримо чи це посилання на Telegram повідомлення
            reply_to_id = None
            reply_target_id = forward_to
            
            # Шукаємо посилання у тексту
            link_match = re.search(r'https?://t\.me/c/\d+/\d+', message_text)
            if link_match:
                link = link_match.group()
                parsed_chat_id, parsed_message_id = parse_telegram_link(link)
                
                if parsed_chat_id and parsed_message_id:
                    # Видаляємо посилання з тексту
                    text_without_link = message_text.replace(link, '').strip()
                    clean_message = sanitize_message_text(text_without_link)
                    reply_target_id = parsed_chat_id
                    reply_to_id = parsed_message_id
                    final_message = f"{clean_message}\n\n{signature}"
                    logger.info(f"📤 /say: текст в чат {reply_target_id} reply на {reply_to_id}")
                else:
                    clean_message = sanitize_message_text(message_text)
                    final_message = f"{clean_message}\n\n{signature}"
                    logger.info(f"📤 /say: невірне посилання в тексті")
            else:
                clean_message = sanitize_message_text(message_text)
                final_message = f"{clean_message}\n\n{signature}"
            
            await context.bot.send_message(
                chat_id=reply_target_id,
                text=final_message,
                reply_to_message_id=reply_to_id,
                parse_mode=None,
                disable_web_page_preview=True
            )
            logger.info(f"📤 /say: текст від {user_id} відправлено")
            db.log_action("say", user_id, details=f"Message sent to user chat")
        else:
            await reply_and_delete(update, "❌ Вкажіть повідомлення після команди або відповідьте на повідомлення!")
            return
        
    except Exception as e:
        logger.error(f"Помилка відправки: {e}")
        try:
            await reply_and_delete(update, f"❌ Помилка відправки: {e}")
        except:
            pass

async def says_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    if db.is_say_blocked(user_id):
        await reply_and_delete(update, "❌ Вашу можливість використання /says заблоковано!")
        return
    
    # Парсимо параметри команди для отримання ID чату
    target_chat_id = None
    if context.args and len(context.args) > 0:
        try:
            # Спробуємо парсити перший параметр як chat ID
            test_id = int(context.args[0])
            # Видаляємо перший параметр з args для обробки команди
            context.args = context.args[1:]
            target_chat_id = test_id
            logger.info(f"📤 /says: Target chat ID: {target_chat_id}")
        except ValueError:
            # Перший параметр не число, залишаємо його в args
            pass
    
    forward_to = target_chat_id if target_chat_id else USER_CHAT_ID
    
    if not forward_to:
        await reply_and_delete(update, "❌ Не налаштовано чат користувачів!")
        return
    
    try:
        if update.message.reply_to_message:
            replied_message = update.message.reply_to_message
            
            # Якщо вказаний текст після /says - відправити як reply в forward_to (анонімно)
            if context.args:
                message_text = ' '.join(context.args)
                clean_message = sanitize_message_text(message_text)
                
                await context.bot.send_message(
                    chat_id=forward_to,
                    text=clean_message,
                    reply_to_message_id=replied_message.message_id,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
                logger.info(f"📤 /says: анонімний текст від {user_id} як reply на {replied_message.message_id} відправлено в USER_CHAT_ID")
            else:
                # Без тексту - пересилати саме повідомлення в forward_to
                if replied_message.text:
                    clean_message = sanitize_message_text(replied_message.text)
                    await context.bot.send_message(
                        chat_id=forward_to,
                        text=clean_message,
                        parse_mode=None,
                        disable_web_page_preview=True
                    )
                elif replied_message.caption:
                    clean_caption = sanitize_message_text(replied_message.caption)
                    await context.bot.send_message(
                        chat_id=forward_to,
                        text=clean_caption,
                        parse_mode=None,
                        disable_web_page_preview=True
                    )
                else:
                    if update.effective_chat:
                        await context.bot.forward_message(
                            chat_id=forward_to,
                            from_chat_id=update.effective_chat.id,
                            message_id=replied_message.message_id
                        )
                logger.info(f"📤 /says: повідомлення від {user_id} пересилано в {forward_to}")
        elif context.args:
            message_text = ' '.join(context.args)
            
            # Перевіримо чи це посилання на Telegram повідомлення
            reply_to_id = None
            reply_target_id = forward_to
            
            # Шукаємо посилання у тексту
            link_match = re.search(r'https?://t\.me/c/\d+/\d+', message_text)
            if link_match:
                link = link_match.group()
                parsed_chat_id, parsed_message_id = parse_telegram_link(link)
                
                if parsed_chat_id and parsed_message_id:
                    # Видаляємо посилання з тексту
                    text_without_link = message_text.replace(link, '').strip()
                    clean_message = sanitize_message_text(text_without_link)
                    reply_target_id = parsed_chat_id
                    reply_to_id = parsed_message_id
                    logger.info(f"📤 /says: текст в чат {reply_target_id} reply на {reply_to_id}")
                else:
                    clean_message = sanitize_message_text(message_text)
                    logger.info(f"📤 /says: невірне посилання в тексті")
            else:
                clean_message = sanitize_message_text(message_text)
            
            await context.bot.send_message(
                chat_id=reply_target_id,
                text=clean_message,
                reply_to_message_id=reply_to_id,
                parse_mode=None,
                disable_web_page_preview=True
            )
            logger.info(f"📤 /says: текст від {user_id} відправлено")
            db.log_action("says", user_id, details="Anonymous message sent to user chat")
        else:
            await reply_and_delete(update, "❌ Вкажіть повідомлення після команди або відповідьте на повідомлення!")
            return
        
    except Exception as e:
        logger.error(f"Помилка відправки: {e}")
        try:
            await reply_and_delete(update, f"❌ Помилка відправки: {e}")
        except:
            pass

async def sayon_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    logger.info(f"🟡 [sayon_command] START - user_id: {update.effective_user.id if update.effective_user else None}")
    
    if not update.effective_user or not update.message:
        logger.warning("🟡 [sayon_command] No user or message")
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        logger.warning(f"🟡 [sayon_command] User {user_id} cannot use bot")
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    if db.is_say_blocked(user_id):
        logger.warning(f"🟡 [sayon_command] User {user_id} is say_blocked")
        await reply_and_delete(update, "❌ Вашу можливість використання sayon заблоковано!")
        return
    
    # Парсимо параметри команди для отримання ID чату
    target_chat_id = None
    if context.args and len(context.args) > 0:
        try:
            target_chat_id = int(context.args[0])
            logger.info(f"🟡 [sayon_command] Target chat ID: {target_chat_id}")
        except ValueError:
            await reply_and_delete(update, "❌ Невірний ID чату! Використовуйте: /sayon або /sayon -1003163238506")
            return
    
    try:
        current_mode = db.get_online_mode(user_id)
        logger.info(f"🟡 [sayon_command] current_mode: {current_mode}")
    except Exception as e:
        logger.error(f"❌ [sayon_command] Помилка отримання режиму: {e}")
        await reply_and_delete(update, f"❌ Помилка бази даних: {e}")
        return
    
    if current_mode == "sayon":
        try:
            db.remove_online_mode(user_id)
            logger.info(f"✅ [sayon_command] Режим sayon вимкнено для {user_id}")
            await reply_and_delete(update, "✅ Режим sayon вимкнено")
        except Exception as e:
            logger.error(f"❌ [sayon_command] Помилка видалення режиму: {e}")
            await reply_and_delete(update, f"❌ Помилка видалення режиму: {e}")
            return
        
        admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
        admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
        clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
        
        log_message = f"""Власник/Адмін
{clickable_admin} {admin_username} [{user_id}]
Автоматичне пересилання з підписом вимкнено
#sayoff #id{user_id}"""
        
        await log_to_channel(context, log_message, parse_mode="HTML")
    else:
        try:
            source_chat_id = update.effective_chat.id if update.effective_chat else 0
            db.set_online_mode(user_id, "sayon", source_chat_id, target_chat_id)
            logger.info(f"✅ [sayon_command] Режим sayon увімкнено для {user_id}, target: {target_chat_id}")
            
            msg = "✅ Режим sayon увімкнено! Ваші повідомлення будуть автоматично пересилатися з підписом.\nРежим вимкнеться автоматично через 5 хвилин неактивності."
            if target_chat_id:
                try:
                    chat = await context.bot.get_chat(target_chat_id)
                    chat_name = chat.title or chat.full_name or "Невідомий чат"
                    try:
                        invite_link = await context.bot.create_chat_invite_link(chat_id=target_chat_id)
                        msg += f"\n📍 Чат для пересилання: {chat_name}\n🔗 {invite_link.invite_link}"
                        logger.info(f"✅ [sayon_command] Запрошувальне посилання створено: {invite_link.invite_link}")
                    except Exception as invite_err:
                        logger.warning(f"⚠️ [sayon_command] Не можу створити запрошувальне посилання: {invite_err}")
                        channel_id = str(abs(target_chat_id))[4:] if target_chat_id < 0 else str(abs(target_chat_id))
                        chat_link = f"https://t.me/c/{channel_id}"
                        msg += f"\n📍 Чат для пересилання: {chat_name}\n🔗 {chat_link}"
                except Exception as e:
                    logger.warning(f"⚠️ [sayon_command] Не можу отримати інформацію про чат {target_chat_id}: {e}")
                    msg += f"\n📍 Чат для пересилання: [{target_chat_id}]"
            await reply_and_delete(update, msg)
            
            admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
            admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
            clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
            
            role_text = "Власник" if is_owner(user_id) else ("Головний адмін" if is_head_admin(user_id) else "Гном")
            
            log_message = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
Автоматичне пересилання з підписом увімкнено"""
            if target_chat_id:
                try:
                    chat = await context.bot.get_chat(target_chat_id)
                    chat_name = chat.title or chat.full_name or "Невідомий чат"
                    log_message += f"\n📍 Чат: {chat_name} [{target_chat_id}]"
                except:
                    log_message += f"\n📍 Чат: [{target_chat_id}]"
            log_message += f"\n#sayon #id{user_id}"
            
            await log_to_channel(context, log_message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"❌ [sayon_command] Помилка активації режиму: {e}")
            await reply_and_delete(update, f"❌ Помилка активації режиму: {e}")
            return

async def sayson_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    logger.info(f"🔵 [sayson_command] START - user_id: {update.effective_user.id if update.effective_user else None}")
    
    if not update.effective_user or not update.message:
        logger.warning("🔵 [sayson_command] No user or message")
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        logger.warning(f"🔵 [sayson_command] User {user_id} cannot use bot")
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    if db.is_say_blocked(user_id):
        logger.warning(f"🔵 [sayson_command] User {user_id} is say_blocked")
        await reply_and_delete(update, "❌ Вашу можливість використання sayson заблоковано!")
        return
    
    # Парсимо параметри команди для отримання ID чату
    target_chat_id = None
    if context.args and len(context.args) > 0:
        try:
            target_chat_id = int(context.args[0])
            logger.info(f"🔵 [sayson_command] Target chat ID: {target_chat_id}")
        except ValueError:
            await reply_and_delete(update, "❌ Невірний ID чату! Використовуйте: /sayson або /sayson -1003163238506")
            return
    
    try:
        current_mode = db.get_online_mode(user_id)
        logger.info(f"🔵 [sayson_command] current_mode: {current_mode}")
    except Exception as e:
        logger.error(f"❌ [sayson_command] Помилка отримання режиму: {e}")
        await reply_and_delete(update, f"❌ Помилка бази даних: {e}")
        return
    
    if current_mode == "sayson":
        logger.info(f"🔵 [sayson_command] Removing sayson mode")
        try:
            db.remove_online_mode(user_id)
            logger.info(f"✅ [sayson_command] Режим sayson вимкнено для {user_id}")
            await reply_and_delete(update, "✅ Режим sayson вимкнено")
        except Exception as e:
            logger.error(f"❌ [sayson_command] Помилка видалення режиму: {e}")
            await reply_and_delete(update, f"❌ Помилка видалення режиму: {e}")
            return
        
        admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
        admin_username = f"(@{update.effective_user.username})" if update.effective_user.username else ""
        
        log_message = f"""Власник/Адмін
{admin_name} {admin_username} [{user_id}]
Автоматичне пересилання без підпису вимкнено
#saysoff #id{user_id}"""
        
        await log_to_channel(context, log_message)
    else:
        logger.info(f"🔵 [sayson_command] Setting sayson mode")
        try:
            source_chat_id = update.effective_chat.id if update.effective_chat else 0
            logger.info(f"🔵 [sayson_command] source_chat_id: {source_chat_id}")
            
            db.set_online_mode(user_id, "sayson", source_chat_id, target_chat_id)
            logger.info(f"✅ [sayson_command] Режим sayson увімкнено для {user_id}, target: {target_chat_id}")
            
            msg = "✅ Режим sayson увімкнено! Ваші повідомлення будуть автоматично пересилатися анонімно.\nРежим вимкнеться автоматично через 5 хвилин неактивності."
            if target_chat_id:
                try:
                    chat = await context.bot.get_chat(target_chat_id)
                    chat_name = chat.title or chat.full_name or "Невідомий чат"
                    try:
                        invite_link = await context.bot.create_chat_invite_link(chat_id=target_chat_id)
                        msg += f"\n📍 Чат для пересилання: {chat_name}\n🔗 {invite_link.invite_link}"
                        logger.info(f"✅ [sayson_command] Запрошувальне посилання створено: {invite_link.invite_link}")
                    except Exception as invite_err:
                        logger.warning(f"⚠️ [sayson_command] Не можу створити запрошувальне посилання: {invite_err}")
                        channel_id = str(abs(target_chat_id))[4:] if target_chat_id < 0 else str(abs(target_chat_id))
                        chat_link = f"https://t.me/c/{channel_id}"
                        msg += f"\n📍 Чат для пересилання: {chat_name}\n🔗 {chat_link}"
                except Exception as e:
                    logger.warning(f"⚠️ [sayson_command] Не можу отримати інформацію про чат {target_chat_id}: {e}")
                    msg += f"\n📍 Чат для пересилання: [{target_chat_id}]"
            await reply_and_delete(update, msg)
            
            admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
            admin_username = f"(@{update.effective_user.username})" if update.effective_user.username else ""
            
            role_text = "Власник" if is_owner(user_id) else ("Головний адмін" if is_head_admin(user_id) else "Гном")
            
            log_message = f"""{role_text}
{admin_name} {admin_username} [{user_id}]
Автоматичне пересилання без підпису увімкнено"""
            if target_chat_id:
                try:
                    chat = await context.bot.get_chat(target_chat_id)
                    chat_name = chat.title or chat.full_name or "Невідомий чат"
                    log_message += f"\n📍 Чат: {chat_name} [{target_chat_id}]"
                except:
                    log_message += f"\n📍 Чат: [{target_chat_id}]"
            log_message += f"\n#sayson #id{user_id}"
            
            await log_to_channel(context, log_message)
            logger.info(f"🔵 [sayson_command] SUCCESS - mode activated")
        except Exception as e:
            logger.error(f"❌ [sayson_command] Помилка активації режиму: {e}")
            await reply_and_delete(update, f"❌ Помилка активації режиму: {e}")
            return

async def sayoff_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    current_mode = db.get_online_mode(user_id)
    
    if not current_mode:
        await reply_and_delete(update, "❌ Режим не вмикнено!")
        return
    
    db.remove_online_mode(user_id)
    await reply_and_delete(update, "✅ Режим вимкнено")
    
    admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
    admin_username = f"(@{update.effective_user.username})" if update.effective_user.username else ""
    
    mode_text = "з підписом" if current_mode == "sayon" else "анонімно"
    clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
    log_message = f"""Власник/Адмін
{clickable_admin} {admin_username} [{user_id}]
Автоматичне пересилання {mode_text} вимкнено
#sayoff #id{user_id}"""
    
    await log_to_channel(context, log_message, parse_mode="HTML")

async def sayoffall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ Тільки власник і головні адміни мають доступ до цієї команди!")
        return
    
    all_modes = db.get_all_online_modes()
    
    if not all_modes:
        await reply_and_delete(update, "❌ Немає активних режимів!")
        return
    
    count = len(all_modes)
    db.clear_all_online_modes()
    await reply_and_delete(update, f"✅ Вимкнено режим для {count} користувачів")
    
    admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
    role_text = "Власник" if is_owner(user_id) else "Головний адмін"
    
    # Створюємо клікабельні імена для кожного режиму
    modes_list_items = []
    for m in all_modes:
        mode_user_id = m['user_id']
        mode_user_name = m['full_name'] or "Невідомий"
        clickable_mode_user = f"<a href='tg://user?id={mode_user_id}'>{mode_user_name}</a>"
        modes_list_items.append(f"• {clickable_mode_user} ({m['mode']})")
    
    modes_list = "\n".join(modes_list_items)
    
    log_message = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
Вимкнено режими для {count} користувачів:
{modes_list}
#sayoffall"""
    
    await log_to_channel(context, log_message, parse_mode="HTML")

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    # Сохраняем всех пользователей в БД при первом сообщении
    save_user_from_update(update)
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not can_use_bot(user_id):
        return
    
    # Обробка команд видалення профілю простим текстом (з дефісом на початку)
    if update.message.text and update.message.text.startswith('-'):
        text = update.message.text.strip()
        
        # -myname - видалити кастомне імʼя
        if text == '-myname':
            await del_myname_command(update, context)
            return
        
        # -mym - видалити профіль-фото
        elif text == '-mym':
            await del_mym_command(update, context)
            return
        
        # -mymt - видалити опис профілю
        elif text == '-mymt':
            await del_mymt_command(update, context)
            return
    
    if not USER_CHAT_ID:
        logger.error("❌ USER_CHAT_ID не встановлено!")
        return
    
    mode = db.get_online_mode(user_id)
    source_chat_id = db.get_online_mode_source(user_id)
    
    # Для власника - дозволити режим з будь-якого чату (PM або адмін-чат)
    # Для адмінів - тільки з адмін-чату
    if not mode:
        return
    
    is_owner_user = is_owner(user_id)
    if not is_owner_user and source_chat_id != chat_id:
        return
    
    logger.info(f"📨 Пересилаємо ({mode}): user={user_id}, from_chat={chat_id}, to_chat={USER_CHAT_ID}")
    
    db.update_online_activity(user_id)
    
    try:
        if mode == "sayon":
            author_name = safe_send_message(update.effective_user.full_name or "Невідомий")
            username = f"@{safe_send_message(update.effective_user.username)}" if update.effective_user.username else ""
            signature = f"\n\n— {author_name} {username}"
            
            if update.message.text:
                clean_message = sanitize_message_text(update.message.text)
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=f"{clean_message}{signature}",
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            elif update.message.caption:
                clean_caption = sanitize_message_text(update.message.caption)
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=f"{clean_caption}{signature}",
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            else:
                await context.bot.forward_message(
                    chat_id=USER_CHAT_ID,
                    from_chat_id=chat_id,
                    message_id=update.message.message_id
                )
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=signature.strip(),
                    parse_mode=None
                )
        
        elif mode == "sayson":
            if update.message.text:
                clean_message = sanitize_message_text(update.message.text)
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=clean_message,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            elif update.message.caption:
                clean_caption = sanitize_message_text(update.message.caption)
                await context.bot.send_message(
                    chat_id=USER_CHAT_ID,
                    text=clean_caption,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            else:
                await context.bot.forward_message(
                    chat_id=USER_CHAT_ID,
                    from_chat_id=chat_id,
                    message_id=update.message.message_id
                )
        
        logger.info(f"✅ Повідомлення успішно пересилано")
    except Exception as e:
        logger.error(f"❌ Помилка автопересилання: {e}")


async def saypin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    # Визначаємо ID чату для відправлення
    target_chat_id = USER_CHAT_ID
    message_text = None
    
    # Перевіряємо, чи перший аргумент - це ID чату
    if context.args and context.args[0].lstrip('-').isdigit():
        try:
            target_chat_id = int(context.args[0])
            message_args = context.args[1:]
        except (ValueError, IndexError):
            message_args = context.args
    else:
        message_args = context.args
    
    if not target_chat_id:
        await reply_and_delete(update, "❌ Не налаштовано чат призначення!")
        return
    
    try:
        sent_message = None
        
        if update.message.reply_to_message:
            replied_message = update.message.reply_to_message
            
            if replied_message.text:
                clean_message = sanitize_message_text(replied_message.text)
                sent_message = await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=clean_message,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            elif replied_message.caption:
                clean_caption = sanitize_message_text(replied_message.caption)
                sent_message = await context.bot.send_message(
                    chat_id=target_chat_id,
                    text=clean_caption,
                    parse_mode=None,
                    disable_web_page_preview=True
                )
            else:
                sent_message = await context.bot.forward_message(
                    chat_id=target_chat_id,
                    from_chat_id=update.effective_chat.id,
                    message_id=replied_message.message_id
                )
        elif message_args:
            message_text = ' '.join(message_args)
            clean_message = sanitize_message_text(message_text)
            sent_message = await context.bot.send_message(
                chat_id=target_chat_id,
                text=clean_message,
                parse_mode=None,
                disable_web_page_preview=True
            )
        else:
            await reply_and_delete(update, "❌ Вкажіть повідомлення після команди або відповідьте на повідомлення!")
            return
        
        if sent_message:
            await context.bot.pin_chat_message(target_chat_id, sent_message.message_id)
        
        await reply_and_delete(update, "✅ Повідомлення відправлено і закріплено!")
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await reply_and_delete(update, f"❌ Помилка: {e}")

async def save_s_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    if not update.message.reply_to_message:
        await reply_and_delete(update, "❌ Відповідьте на повідомлення яке потрібно зберегти!")
        return
    
    try:
        if not ADMIN_CHAT_ID:
            await reply_and_delete(update, "❌ Адмін-чат не налаштовано!")
            return
        
        replied_msg = update.message.reply_to_message
        
        # Спочатку спробуємо скопіювати (працює з bot messages і захищеним контентом)
        try:
            await context.bot.copy_message(
                chat_id=ADMIN_CHAT_ID,
                from_chat_id=update.effective_chat.id,
                message_id=replied_msg.message_id
            )
            logger.info(f"✅ Повідомлення скопійовано")
        except Exception as copy_error:
            logger.warning(f"⚠️ Помилка копіювання: {copy_error}, спробую альтернативний метод...")
            
            # Визначаємо тип медіа для логування
            media_type = "невідомо"
            if replied_msg.sticker:
                media_type = "стікер 📌"
            elif replied_msg.photo:
                media_type = "фото 🖼️"
            elif replied_msg.video:
                media_type = "відео 🎬"
            elif replied_msg.animation:
                media_type = "гіфка 🎞️"
            elif replied_msg.document:
                media_type = "документ 📎"
            elif replied_msg.audio:
                media_type = "аудіо 🎵"
            elif replied_msg.text:
                media_type = "текст 📝"
            
            logger.info(f"📤 Тип контенту: {media_type}")
            
            # Якщо копіювання не спрацює, пересилаємо
            try:
                await context.bot.forward_message(
                    chat_id=ADMIN_CHAT_ID,
                    from_chat_id=update.effective_chat.id,
                    message_id=replied_msg.message_id
                )
                logger.info(f"✅ Повідомлення пересилано ({media_type})")
            except Exception as forward_error:
                logger.warning(f"⚠️ Помилка пересилання: {forward_error}, копіюю вміст...")
                
                # Останній варіант - копіюємо вміст (перевіряємо МЕДІА перед ТЕКСТОМ)
                if replied_msg.sticker:
                    logger.info("📌 Копіюю стікер")
                    await context.bot.send_sticker(
                        chat_id=ADMIN_CHAT_ID,
                        sticker=replied_msg.sticker.file_id
                    )
                elif replied_msg.photo:
                    logger.info("🖼️ Копіюю фото")
                    await context.bot.send_photo(
                        chat_id=ADMIN_CHAT_ID,
                        photo=replied_msg.photo[-1].file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.video:
                    logger.info("🎬 Копіюю відео")
                    await context.bot.send_video(
                        chat_id=ADMIN_CHAT_ID,
                        video=replied_msg.video.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.animation:
                    logger.info("🎞️ Копіюю гіфку")
                    await context.bot.send_animation(
                        chat_id=ADMIN_CHAT_ID,
                        animation=replied_msg.animation.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.document:
                    logger.info("📎 Копіюю документ")
                    await context.bot.send_document(
                        chat_id=ADMIN_CHAT_ID,
                        document=replied_msg.document.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.audio:
                    logger.info("🎵 Копіюю аудіо")
                    await context.bot.send_audio(
                        chat_id=ADMIN_CHAT_ID,
                        audio=replied_msg.audio.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.text:
                    logger.info("📝 Копіюю текст")
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text=replied_msg.text,
                        parse_mode=None
                    )
                else:
                    logger.warning("❓ Невідомий тип повідомлення")
                    await context.bot.send_message(
                        chat_id=ADMIN_CHAT_ID,
                        text="[Повідомлення без тексту]"
                    )
        
        # Тихе збереження - без повідомлення користувачеві
        try:
            await update.message.delete()
        except:
            pass
        
    except Exception as e:
        logger.error(f"Помилка збереження: {e}")
        await reply_and_delete(update, f"❌ Помилка при збереженні: {e}")

async def online_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    online_modes = db.get_all_online_modes()
    
    if not online_modes:
        await reply_and_delete(update, "📵 Немає адмінів в онлайн-режимі")
        return
    
    message = "📱 Адміни в онлайн-режимі:\n\n"
    
    for mode_data in online_modes:
        name = mode_data.get("full_name", "Невідомий")
        user_id = mode_data.get("user_id")
        clickable_name = f"<a href='tg://user?id={user_id}'>{name}</a>" if user_id else name
        username = f"(@{mode_data.get('username')})" if mode_data.get("username") else ""
        mode = "sayon (з підписом)" if mode_data["mode"] == "sayon" else "sayson (анонімно)"
        message += f"• {clickable_name} {username}\n  Режим: {mode}\n\n"
    
    await reply_and_delete(update, message, parse_mode="HTML")

async def sayb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_manage_gnomes(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть ID користувача!")
        return
    
    try:
        target_id = int(context.args[0])
        
        if is_owner(target_id) and not is_owner(user_id):
            await reply_and_delete(update, "❌ Не можна блокувати власника!")
            return
        
        if is_head_admin(target_id) and not is_owner(user_id):
            await reply_and_delete(update, "❌ Тільки власник може блокувати головних адмінів!")
            return
        
        db.block_say_command(target_id, user_id, 
                             update.effective_user.full_name or "", update.effective_user.username or "")
        await reply_and_delete(update, f"✅ Користувач {target_id} заблокований від використання /say та /says")
        db.log_action("sayb", user_id, target_id)
        
    except ValueError:
        await reply_and_delete(update, "❌ Невірний ID!")

async def sayu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_manage_gnomes(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть ID користувача!")
        return
    
    try:
        target_id = int(context.args[0])
        db.unblock_say_command(target_id)
        await reply_and_delete(update, f"✅ Користувач {target_id} розблокований для використання /say та /says")
        db.log_action("sayu", user_id, target_id)
        
    except ValueError:
        await reply_and_delete(update, "❌ Невірний ID!")


async def alarm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name or "Невідомий"
    username = update.effective_user.username or ""
    clickable_user = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
    
    alarm_text = " ".join(context.args) if context.args else "Виклик адміністрації"
    
    message_link = ""
    if update.message.reply_to_message:
        chat_id = str(USER_CHAT_ID).replace("-100", "")
        message_link = f"\n👀 Дивитись повідомлення: http://t.me/c/{chat_id}/{update.message.reply_to_message.message_id}"
    
    alarm_message = f"""🚨 #ALARM
Користувач: {clickable_user} (@{username}) [{user_id}]
Текст: {alarm_text}{message_link}
#id{user_id}"""
    
    try:
        sent_msg = await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=alarm_message,
            parse_mode=None
        )
        
        try:
            await context.bot.pin_chat_message(ADMIN_CHAT_ID, sent_msg.message_id)
        except:
            pass
        
        await reply_and_delete(update, "✅ Передано на перегляд адміністрації, очікуйте.")
        await log_to_channel(context, alarm_message, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Помилка alarm: {e}")

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть текст для розсилки!")
        return
    
    message_text = " ".join(context.args)
    clean_message = sanitize_message_text(message_text)
    
    await reply_and_delete(update, f"📢 Розпочато розсилку повідомлення всім користувачам...")
    
    all_users = db.get_all_users()
    sent_count = 0
    failed_count = 0
    
    logger.info(f"🔊 Розсилка розпочата: {len(all_users)} користувачів")
    
    for target_user_id in all_users:
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=clean_message,
                parse_mode=None
            )
            sent_count += 1
        except Exception as e:
            failed_count += 1
            logger.warning(f"⚠️ Не вдалось отправити користувачу {target_user_id}: {e}")
    
    admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
    admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
    
    result_message = f"""✅ Розсилка завершена!
📤 Отправлено: {sent_count}
❌ Помилок: {failed_count}
👤 Адмін: {admin_name} {admin_username}
📝 Текст: {clean_message}"""
    
    await reply_and_delete(update, result_message)
    
    logger.info(f"✅ Розсилка завершена: {sent_count} успішно, {failed_count} помилок")

async def hto_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Розширена інформація про користувача з профіль-системою"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    target_user_id = user_id
    target_user_name = update.effective_user.full_name or "Невідомий"
    target_username = update.effective_user.username or ""
    
    # Перевіряємо чи це адмін (гном, головний адмін або власник)
    is_admin = is_gnome(user_id) or is_head_admin(user_id) or is_owner(user_id)
    
    # Якщо є аргумент (@username або ID) - адміни можуть переглядати чужих
    if context.args:
        if not is_admin:
            await reply_and_delete(update, "❌ Тільки адміни можуть переглядати інших користувачів!", delay=60)
            return
        
        identifier = context.args[0]
        target_user = await get_user_info(update, context, identifier)
        if target_user:
            target_user_id = target_user["user_id"]
            target_user_name = target_user["full_name"]
            target_username = target_user["username"]
        else:
            await reply_and_delete(update, "❌ Користувача не знайдено!", delay=60)
            return
    # Без аргументів і без reply - показуємо інформацію про себе
    elif update.message.reply_to_message and update.message.reply_to_message.from_user:
        # Якщо є reply - адміни можуть переглядати чужих
        if is_admin or user_id == update.message.reply_to_message.from_user.id:
            target_user_id = update.message.reply_to_message.from_user.id
            target_user_name = update.message.reply_to_message.from_user.full_name or "Невідомий"
            target_username = update.message.reply_to_message.from_user.username or ""
        else:
            await reply_and_delete(update, "❌ Ви можете переглядати тільки свій профіль!", delay=60)
            return
    
    user_data = db.get_user(target_user_id)
    custom_name = db.get_custom_name(target_user_id)
    profile_desc = db.get_profile_description(target_user_id)
    custom_position = db.get_custom_position(target_user_id)
    
    # Визначаємо посаду - перевіряємо через функції
    if is_owner(target_user_id):
        position_display = "👑 Власник"
    elif is_head_admin(target_user_id):
        position_display = "🔒 Головний Адмін"
    elif is_gnome(target_user_id):
        position_display = "🧙 Гном"
    else:
        position_display = "👤 Користувач"
    
    # Якщо є кастомна посада - додаємо
    if custom_position:
        position_display += f" ({custom_position})"
    
    info_message = f"""👤 ПРОФІЛЬ КОРИСТУВАЧА

"""
    
    # Кастомне імʼя (якщо є) - з клікабельним посиланням
    clickable_name = f"<a href='tg://user?id={target_user_id}'>{target_user_name}</a>"
    if custom_name:
        info_message += f"📝 Імʼя: {custom_name}\n"
    else:
        info_message += f"📝 Імʼя: {clickable_name}\n"
    
    # Опис профілю (якщо є)
    if profile_desc:
        info_message += f"📄 Про себе: {profile_desc}\n"
    
    info_message += f"""
@{target_username if target_username else 'не вказано'}
ID: <code>{target_user_id}</code>
{position_display}
"""
    
    if user_data and user_data.get("joined_at"):
        # Форматуємо дату: день.місяць.рік - години:хвилини
        try:
            from datetime import datetime
            joined_dt = datetime.fromisoformat(user_data['joined_at'])
            formatted_date = joined_dt.strftime("%d.%m.%Y - %H:%M")
            info_message += f"📅 Дата вступу: {formatted_date}\n"
        except:
            info_message += f"📅 Дата вступу: {user_data['joined_at']}\n"
    
    # Дата народження (якщо є)
    birth_date = db.get_birthday(target_user_id)
    if birth_date:
        info_message += f"🎂 День народження: {birth_date}\n"
    
    # Отримуємо профіль-фото якщо є
    profile_pic = db.get_profile_picture(target_user_id)
    if profile_pic:
        try:
            # Якщо є фото/гіфка - надсилаємо її з описом
            if profile_pic["media_type"] == "photo":
                sent_msg = await context.bot.send_photo(
                    chat_id=update.message.chat_id,
                    photo=profile_pic["file_id"],
                    caption=info_message,
                    parse_mode="HTML"
                )  # Клікабельні імена через HTML посилання
                # Видаляємо через 60 секунд (1 хвилина)
                asyncio.create_task(delete_message_after_delay(sent_msg, 60))
            elif profile_pic["media_type"] == "gif":
                sent_msg = await context.bot.send_animation(
                    chat_id=update.message.chat_id,
                    animation=profile_pic["file_id"],
                    caption=info_message,
                    parse_mode="HTML"
                )
                # Видаляємо через 60 секунд (1 хвилина)
                asyncio.create_task(delete_message_after_delay(sent_msg, 60))
        except Exception as e:
            logger.warning(f"⚠️ Не вдалось надіслати профіль-фото з описом: {e}")
            # Якщо помилка - просто надіслемо текст
            await reply_and_delete(update, info_message, delay=60, parse_mode="HTML")
    else:
        # Якщо немає фото - просто надіслемо текст
        await reply_and_delete(update, info_message, delay=60, parse_mode="HTML")

async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Зберегти нотатку - доступно для всіх користувачів"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть текст нотатки!\nПриклад: /note важливе завдання на завтра")
        return
    
    note_text = " ".join(context.args)
    db.add_note(user_id, note_text, 
                created_by_id=user_id,
                username=update.effective_user.username or "", 
                full_name=update.effective_user.full_name or "")
    
    try:
        if NOTES_CHANNEL_ID:
            user_name = update.effective_user.full_name or "Невідомий"
            username = f"@{update.effective_user.username}" if update.effective_user.username else ""
            clickable_name = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
            
            note_message = f"""📝 Нотатка від {clickable_name} {username} [{user_id}]

{note_text}

#id{user_id}"""
            
            await context.bot.send_message(
                chat_id=NOTES_CHANNEL_ID,
                text=note_message,
                parse_mode="HTML"
            )
        
        await reply_and_delete(update, "✅ Нотатку збережено!")
        
    except Exception as e:
        logger.error(f"Помилка збереження нотатки: {e}")
        await reply_and_delete(update, f"❌ Помилка: {e}")

async def notes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Показати нотатки - кожен користувач видит тільки свої (вінні власник може видіти чужі)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    target_id = user_id
    
    # Тільки власник може переглядати нотатки інших користувачів
    if context.args and is_owner(user_id):
        try:
            target_id = int(context.args[0])
        except:
            identifier = context.args[0]
            target_user = await get_user_info(update, context, identifier)
            if target_user:
                target_id = target_user["user_id"]
    
    notes = db.get_notes(target_id)
    
    if not notes:
        await reply_and_delete(update, "📝 Нотаток не знайдено")
        return
    
    # Отримуємо ім'я користувача для заголовка
    user_info = db.get_user(target_id)
    user_name = user_info.get("full_name", "Невідомий") if user_info else "Невідомий"
    
    # Клікабельне ім'я та копіювальний ID
    clickable_user_name = f"<a href='tg://user?id={target_id}'>{user_name}</a>"
    message = f"📝 Нотатки користувача {clickable_user_name}\nID <code>[{target_id}]</code>:\n\n"
    
    for idx, note in enumerate(notes, 1):
        formatted_time = format_kyiv_time(note['created_at'])
        message += f"{idx}. {note['text']}\n   ({formatted_time})\n\n"
    
    await reply_and_delete(update, message, parse_mode="HTML")

async def delnote_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Видалити нотатку за номером - доступно для всіх користувачів (тільки свої)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть номер нотатки для видалення!\nПриклад: /delnote 1")
        return
    
    try:
        note_number = int(context.args[0])
    except ValueError:
        await reply_and_delete(update, "❌ Вкажіть число! Приклад: /delnote 1")
        return
    
    # Отримуємо всі нотатки користувача
    notes = db.get_notes(user_id)
    
    if not notes:
        await reply_and_delete(update, "📝 У вас немає нотаток!")
        return
    
    if note_number < 1 or note_number > len(notes):
        await reply_and_delete(update, f"❌ Нотатка номер {note_number} не знайдена! У вас {len(notes)} нотаток.")
        return
    
    # Видаляємо нотатку (нотатки у db.get_notes() впорядковані від нових до старих)
    note_to_delete = notes[note_number - 1]
    note_id = note_to_delete['id']
    note_text = note_to_delete['text']
    
    if db.delete_note(note_id):
        await reply_and_delete(update, f"✅ Нотатка видалена!\n📝 Текст: {note_text[:50]}...")
        logger.info(f"🗑️ Нотатка #{note_id} видалена користувачем {user_id}")
    else:
        await reply_and_delete(update, "❌ Помилка при видаленні нотатки!")

async def deltimer_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Встановити таймер автоматичного видалення відповідей (1-60 секунд)"""
    global MESSAGE_DELETE_TIMER
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для власника!")
        return
    
    if not context.args:
        await reply_and_delete(update, f"⏱️ Поточний таймер видалення: {MESSAGE_DELETE_TIMER} секунд\n\nЯк змінити: /deltimer [1-60]\nПриклад: /deltimer 10", delay=60)
        return
    
    try:
        delay = int(context.args[0])
        logger.debug(f"🔍 /deltimer: користувач спробував встановити таймер на {delay} сек")
        
        if delay < 1 or delay > 60:
            await reply_and_delete(update, "❌ Таймер має бути від 1 до 60 секунд!\nПриклад: /deltimer 5", delay=60)
            logger.debug(f"🔍 /deltimer: значення {delay} поза діапазоном 1-60")
            return
        
        MESSAGE_DELETE_TIMER = delay
        save_config()
        logger.info(f"✅ /deltimer: встановлено таймер на {delay} сек")
        
        await reply_and_delete(update, f"✅ Таймер встановлено на {delay} сек!\n⏱️ Усі повідомлення бота видаляються через {delay} сек.", delay=60)
        logger.info(f"⏱️ Власник {user_id} встановив таймер видалення на {delay} секунд")
        
        if LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=f"⏱️ Таймер видалення змінено на {delay} секунд\nВласник: {update.effective_user.full_name}"
                )
            except:
                pass
    except ValueError:
        await reply_and_delete(update, "❌ Вкажіть число від 1 до 60!\nПриклад: /deltimer 5", delay=60)
        logger.debug(f"🔍 /deltimer: помилка при розборі значення '{context.args[0]}'")

async def quit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для власника!")
        return
    
    if not USER_CHAT_ID:
        await reply_and_delete(update, "❌ Основний чат (USER_CHAT_ID) не налаштований!")
        return

    try:
        # Відправляємо повідомлення в основний чат
        await context.bot.send_message(
            chat_id=USER_CHAT_ID,
            text="Ви образили мого власника гниди, я йду від вас!\nПока лузери!"
        )
        logger.info(f"👋 Бот виходить з основного чату {USER_CHAT_ID} за командою власника {user_id}")
        
        # Виходимо з чату
        await context.bot.leave_chat(chat_id=USER_CHAT_ID)
        
        # Відповідаємо власнику
        if update.effective_chat and update.effective_chat.type == "private":
            await update.message.reply_text(f"✅ Виконано! Бот вийшов з чату {USER_CHAT_ID}")
            
    except Exception as e:
        logger.error(f"❌ Помилка при виконанні команди /quit: {e}")
        await reply_and_delete(update, f"❌ Помилка при спробі вийти з чату: {e}")

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global RESTART_BOT
    save_user_from_update(update)
    """Перезапустити бота (тільки для власника)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для власника!")
        return
    
    await reply_and_delete(update, "✅ Бот успішно перезавантажено! ⚡", delay=3)
    logger.info(f"🔄 Бот перезавантажено власником {user_id}")
    
    # Встановлюємо флаг перезапуску
    RESTART_BOT = True
    # Даємо час на відправку повідомлення
    await asyncio.sleep(0.5)
    # Зупиняємо додаток
    await context.application.stop()

async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Отримати файл логів (тільки для власника)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Ця команда доступна тільки для основного власника!")
        return
    
    try:
        log_file_path = "bot.log"
        if os.path.exists(log_file_path):
            await update.message.reply_document(
                document=open(log_file_path, 'rb'),
                caption="📋 Файл логів бота"
            )
            logger.info(f"📋 Логи відправлені власнику {user_id}")
        else:
            await reply_and_delete(update, "❌ Файл логів не знайдено!", delay=30)
            logger.warning(f"⚠️ Файл логів не існує при запиті від {user_id}")
    except Exception as e:
        await reply_and_delete(update, f"❌ Помилка при надіслані логів: {e}", delay=30)
        logger.error(f"❌ Помилка отримання логів: {e}")

async def get_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Отримати поточний config.json (тільки для основного власника)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Ця команда доступна тільки для основного власника!")
        return
    
    try:
        if os.path.exists('config.json'):
            await update.message.reply_document(
                document=open('config.json', 'rb'),
                caption="⚙️ Поточний config.json"
            )
            logger.info(f"⚙️ Config.json відправлений власнику {user_id}")
        else:
            await reply_and_delete(update, "❌ Файл config.json не знайдено!", delay=30)
    except Exception as e:
        await reply_and_delete(update, f"❌ Помилка при надіслані config: {e}", delay=30)
        logger.error(f"❌ Помилка отримання config: {e}")

async def update_config_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Оновити config.json з відправленого файлу (тільки для основного власника)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Ця команда доступна тільки для основного власника!")
        return
    
    try:
        if not update.message.document:
            await reply_and_delete(update, "❌ Будь ласка, відправте файл як документ (reply: /update_config)!", delay=30)
            return
        
        # Завантажуємо файл
        file = await context.bot.get_file(update.message.document.file_id)
        file_data = await file.download_as_bytearray()
        
        # Парсимо JSON
        try:
            new_config = json.loads(file_data.decode('utf-8'))
        except json.JSONDecodeError as e:
            await reply_and_delete(update, f"❌ Помилка парсингу JSON: {e}", delay=30)
            logger.error(f"❌ Помилка парсингу JSON: {e}")
            return
        
        # Зберігаємо резервну копію
        if os.path.exists('config.json'):
            import shutil
            shutil.copy('config.json', f'config.json.backup_{int(time_module.time())}')
        
        # Записуємо новий config
        with open('config.json', 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=2, ensure_ascii=False)
        
        await reply_and_delete(update, "✅ Config.json успішно оновлено! Перезапустіть бота для застосування змін.", delay=30)
        logger.info(f"✅ Config.json оновлено власником {user_id}")
        
        # Логуємо в канал
        if LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=f"⚙️ Config.json оновлено власником {user_id}\nНалаштування: {json.dumps(new_config, indent=2, ensure_ascii=False)[:1000]}..."
                )
            except:
                pass
        
    except Exception as e:
        await reply_and_delete(update, f"❌ Помилка при оновленні config: {e}", delay=30)
        logger.error(f"❌ Помилка оновлення config: {e}")

async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Показати меню управління командами"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    logger.info(f"📋 [Menu] Користувач {user_id} відкрив меню")
    
    menu_text = """🎛️ <b>МЕНЮ УПРАВЛІННЯ КОМАНДАМИ</b>

Виберіть категорію для налаштування:"""
    
    keyboard = [
        [InlineKeyboardButton("👑 УПРАВЛІННЯ АДМІНАМИ", callback_data="menu_admins")],
        [InlineKeyboardButton("🎭 МОДЕРАЦІЯ", callback_data="menu_moderation")],
        [InlineKeyboardButton("🚫 ЧОРНИЙ СПИСОК", callback_data="menu_blacklist")],
        [InlineKeyboardButton("🗣️ ВІДПРАВЛЕННЯ", callback_data="menu_messages")],
        [InlineKeyboardButton("⚙️ ТЕКСТОВІ КОМАНДИ", callback_data="menu_text_commands")],
        [InlineKeyboardButton("🎂 ДНІ НАРОДЖЕННЯ", callback_data="menu_birthdays")],
        [InlineKeyboardButton("📢 РОЗСИЛКА", callback_data="menu_broadcast")],
        [InlineKeyboardButton("📝 НОТАТКИ", callback_data="menu_notes")],
        [InlineKeyboardButton("⏰ НАГАДУВАННЯ", callback_data="menu_reminders")],
        [InlineKeyboardButton("👤 ПРОФІЛЬ", callback_data="menu_profile")],
        [InlineKeyboardButton("👥 ІНФОРМАЦІЯ", callback_data="menu_info")],
        [InlineKeyboardButton("💾 РЕЗЕРВНІ КОПІЇ", callback_data="menu_backup")],
        [InlineKeyboardButton("⚡️ КОНФІГУРАЦІЯ", callback_data="menu_config")],
        [InlineKeyboardButton("❌ Закрити", callback_data="menu_close")],
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(menu_text, parse_mode="HTML", reply_markup=reply_markup)
    logger.info(f"✅ [Menu] Меню показано користувачу {user_id}")

async def menu_moderation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню модерації з toggle кнопками для кожної команди"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not can_use_bot(user_id):
        await query.answer("❌ Немає доступу", show_alert=True)
        return
    
    logger.info(f"📋 [MenuMod] Користувач {user_id} відкрив меню модерації")
    
    menu_text = """🎭 <b>МОДЕРАЦІЯ - УПРАВЛІННЯ КОМАНДАМИ</b>

Натискайте на кнопку щоб ввімкнути/вимкнути команду:"""
    
    # Створюємо кнопки для кожної команди
    keyboard = []
    
    # Ban команди (2 в ряд)
    ban_s_status = FEATURE_STATUS.get("mod_ban_s", True)
    ban_t_status = FEATURE_STATUS.get("mod_ban_t", True)
    ban_s_text = "✅ /ban_s" if ban_s_status else "❌ /ban_s"
    ban_t_text = "✅ /ban_t" if ban_t_status else "❌ /ban_t"
    keyboard.append([
        InlineKeyboardButton(ban_s_text, callback_data="mod_toggle_ban_s"),
        InlineKeyboardButton(ban_t_text, callback_data="mod_toggle_ban_t"),
    ])
    
    # Unban команди (2 в ряд)
    unban_s_status = FEATURE_STATUS.get("mod_unban_s", True)
    unban_t_status = FEATURE_STATUS.get("mod_unban_t", True)
    unban_s_text = "✅ /unban_s" if unban_s_status else "❌ /unban_s"
    unban_t_text = "✅ /unban_t" if unban_t_status else "❌ /unban_t"
    keyboard.append([
        InlineKeyboardButton(unban_s_text, callback_data="mod_toggle_unban_s"),
        InlineKeyboardButton(unban_t_text, callback_data="mod_toggle_unban_t"),
    ])
    
    # Mute команди (2 в ряд)
    mute_s_status = FEATURE_STATUS.get("mod_mute_s", True)
    mute_t_status = FEATURE_STATUS.get("mod_mute_t", True)
    mute_s_text = "✅ /mute_s" if mute_s_status else "❌ /mute_s"
    mute_t_text = "✅ /mute_t" if mute_t_status else "❌ /mute_t"
    keyboard.append([
        InlineKeyboardButton(mute_s_text, callback_data="mod_toggle_mute_s"),
        InlineKeyboardButton(mute_t_text, callback_data="mod_toggle_mute_t"),
    ])
    
    # Unmute команди (2 в ряд)
    unmute_s_status = FEATURE_STATUS.get("mod_unmute_s", True)
    unmute_t_status = FEATURE_STATUS.get("mod_unmute_t", True)
    unmute_s_text = "✅ /unmute_s" if unmute_s_status else "❌ /unmute_s"
    unmute_t_text = "✅ /unmute_t" if unmute_t_status else "❌ /unmute_t"
    keyboard.append([
        InlineKeyboardButton(unmute_s_text, callback_data="mod_toggle_unmute_s"),
        InlineKeyboardButton(unmute_t_text, callback_data="mod_toggle_unmute_t"),
    ])
    
    # Kick команда (одна)
    kick_status = FEATURE_STATUS.get("mod_kick", True)
    kick_text = "✅ /kick" if kick_status else "❌ /kick"
    keyboard.append([InlineKeyboardButton(kick_text, callback_data="mod_toggle_kick")])
    
    # Кнопка повернення
    keyboard.append([InlineKeyboardButton("◀️ Повернутись", callback_data="menu_back")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(menu_text, parse_mode="HTML", reply_markup=reply_markup)
    logger.info(f"✅ [MenuMod] Меню модерації показано {user_id}")

async def toggle_mod_command(update: Update, context: ContextTypes.DEFAULT_TYPE, command_key: str):
    """Універсальна функція для toggle команд модерації"""
    query = update.callback_query
    user_id = query.from_user.id
    
    current_status = FEATURE_STATUS.get(command_key, True)
    new_status = not current_status
    FEATURE_STATUS[command_key] = new_status
    
    command_name = command_key.replace("mod_", "").replace("_", "_")
    
    if new_status:
        logger.info(f"🟢 [MenuMod] Користувач {user_id} ввімкнув /{command_name}")
        await query.answer(f"✅ {command_name} ввімкнена!", show_alert=False)
    else:
        logger.info(f"🔴 [MenuMod] Користувач {user_id} вимкнув /{command_name}")
        await query.answer(f"❌ {command_name} вимкнена!", show_alert=False)
    
    # Оновлюємо меню з новим статусом
    await menu_moderation_callback(update, context)

async def menu_messages_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню ВІДПРАВЛЕННЯ - управління say командами"""
    query = update.callback_query
    user_id = query.from_user.id
    
    if not can_use_bot(user_id):
        await query.answer("❌ Немає доступу", show_alert=True)
        return
    
    logger.info(f"📋 [MenuMsg] Користувач {user_id} відкрив меню відправлення")
    
    menu_text = """🗣️ <b>ВІДПРАВЛЕННЯ - УПРАВЛІННЯ КОМАНДАМИ</b>

Натискайте на кнопку щоб ввімкнути/вимкнути команду:"""
    
    # Створюємо кнопки для say команд
    keyboard = []
    
    # Sayon та Sayson команди (2 в ряд)
    keyboard.append([
        InlineKeyboardButton("🗣️ З автором", callback_data="msg_cmd_sayon"),
        InlineKeyboardButton("🗨️ Без підпису", callback_data="msg_cmd_sayson"),
    ])
    
    # Sayoffall команда (одна - виключити всім)
    keyboard.append([InlineKeyboardButton("⛔ Вимкнути всім", callback_data="msg_cmd_sayoffall")])
    
    # Кнопка повернення
    keyboard.append([InlineKeyboardButton("◀️ Повернутись", callback_data="menu_back")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(menu_text, parse_mode="HTML", reply_markup=reply_markup)
    logger.info(f"✅ [MenuMsg] Меню відправлення показано {user_id}")

async def msg_cmd_sayon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Виконує команду /sayon при натисканні кнопки (для callback)"""
    query = update.callback_query
    user_id = query.from_user.id
    logger.info(f"🗣️ [MenuMsg] Користувач {user_id} натиснув кнопку 'З автором'")
    
    await query.answer()
    
    if not can_use_bot(user_id):
        await query.edit_message_text("❌ У вас немає доступу до цієї команди!")
        return
    
    if db.is_say_blocked(user_id):
        await query.edit_message_text("❌ Вашу можливість використання sayon заблоковано!")
        return
    
    try:
        current_mode = db.get_online_mode(user_id)
        
        if current_mode == "sayon":
            db.remove_online_mode(user_id)
            await query.edit_message_text("✅ Режим sayon вимкнено")
            logger.info(f"✅ [MenuMsg] Режим sayon вимкнено для {user_id}")
        else:
            source_chat_id = query.message.chat_id if query.message else 0
            db.set_online_mode(user_id, "sayon", source_chat_id, None)
            await query.edit_message_text("✅ Режим sayon увімкнено! Ваші повідомлення будуть автоматично пересилатися з підписом.")
            logger.info(f"✅ [MenuMsg] Режим sayon увімкнено для {user_id}")
    except Exception as e:
        logger.error(f"❌ [MenuMsg] Помилка: {e}")
        await query.edit_message_text(f"❌ Помилка: {e}")

async def msg_cmd_sayson_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Виконує команду /sayson при натисканні кнопки (для callback)"""
    query = update.callback_query
    user_id = query.from_user.id
    logger.info(f"🗨️ [MenuMsg] Користувач {user_id} натиснув кнопку 'Без підпису'")
    
    await query.answer()
    
    if not can_use_bot(user_id):
        await query.edit_message_text("❌ У вас немає доступу до цієї команди!")
        return
    
    if db.is_say_blocked(user_id):
        await query.edit_message_text("❌ Вашу можливість використання sayson заблоковано!")
        return
    
    try:
        current_mode = db.get_online_mode(user_id)
        
        if current_mode == "sayson":
            db.remove_online_mode(user_id)
            await query.edit_message_text("✅ Режим sayson вимкнено")
            logger.info(f"✅ [MenuMsg] Режим sayson вимкнено для {user_id}")
        else:
            source_chat_id = query.message.chat_id if query.message else 0
            db.set_online_mode(user_id, "sayson", source_chat_id, None)
            await query.edit_message_text("✅ Режим sayson увімкнено! Ваші повідомлення будуть автоматично пересилатися анонімно.")
            logger.info(f"✅ [MenuMsg] Режим sayson увімкнено для {user_id}")
    except Exception as e:
        logger.error(f"❌ [MenuMsg] Помилка: {e}")
        await query.edit_message_text(f"❌ Помилка: {e}")

async def msg_cmd_sayoffall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Виконує команду /sayoffall при натисканні кнопки (для callback)"""
    query = update.callback_query
    user_id = query.from_user.id
    logger.info(f"⛔ [MenuMsg] Користувач {user_id} натиснув кнопку 'Вимкнути всім'")
    
    await query.answer()
    
    if not can_ban_mute(user_id):
        await query.edit_message_text("❌ Тільки власник і головні адміни мають доступ!")
        return
    
    try:
        all_modes = db.get_all_online_modes()
        
        if not all_modes:
            await query.edit_message_text("❌ Немає активних режимів!")
            return
        
        count = len(all_modes)
        db.clear_all_online_modes()
        await query.edit_message_text(f"✅ Вимкнено режим для {count} користувачів")
        logger.info(f"✅ [MenuMsg] Вимкнено режим для {count} користувачів")
    except Exception as e:
        logger.error(f"❌ [MenuMsg] Помилка: {e}")
        await query.edit_message_text(f"❌ Помилка: {e}")

async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє натиски кнопок меню"""
    query = update.callback_query
    user_id = query.from_user.id
    
    logger.debug(f"📍 [MenuCallback] Користувач {user_id} натиснув {query.data}")
    
    try:
        if query.data == "menu_close":
            # Видалення повідомлення меню
            await query.delete_message()
            logger.info(f"🗑️ [MenuClose] Меню закрито користувачем {user_id}")
            return
            
        elif query.data == "menu_back":
            # Повернення до головного меню
            menu_text = """🎛️ <b>МЕНЮ УПРАВЛІННЯ КОМАНДАМИ</b>

Виберіть категорію для налаштування:"""
            
            keyboard = [
                [InlineKeyboardButton("👑 УПРАВЛІННЯ АДМІНАМИ", callback_data="menu_admins")],
                [InlineKeyboardButton("🎭 МОДЕРАЦІЯ", callback_data="menu_moderation")],
                [InlineKeyboardButton("🚫 ЧОРНИЙ СПИСОК", callback_data="menu_blacklist")],
                [InlineKeyboardButton("🗣️ ВІДПРАВЛЕННЯ", callback_data="menu_messages")],
                [InlineKeyboardButton("⚙️ ТЕКСТОВІ КОМАНДИ", callback_data="menu_text_commands")],
                [InlineKeyboardButton("🎂 ДНІ НАРОДЖЕННЯ", callback_data="menu_birthdays")],
                [InlineKeyboardButton("📢 РОЗСИЛКА", callback_data="menu_broadcast")],
                [InlineKeyboardButton("📝 НОТАТКИ", callback_data="menu_notes")],
                [InlineKeyboardButton("⏰ НАГАДУВАННЯ", callback_data="menu_reminders")],
                [InlineKeyboardButton("👤 ПРОФІЛЬ", callback_data="menu_profile")],
                [InlineKeyboardButton("👥 ІНФОРМАЦІЯ", callback_data="menu_info")],
                [InlineKeyboardButton("💾 РЕЗЕРВНІ КОПІЇ", callback_data="menu_backup")],
                [InlineKeyboardButton("⚡️ КОНФІГУРАЦІЯ", callback_data="menu_config")],
                [InlineKeyboardButton("❌ Закрити", callback_data="menu_close")],
            ]
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(menu_text, parse_mode="HTML", reply_markup=reply_markup)
            logger.info(f"✅ [MenuBack] Повернено до головного меню для {user_id}")
            
        elif query.data == "menu_moderation":
            await menu_moderation_callback(update, context)
        elif query.data.startswith("mod_toggle_"):
            # Обробляємо toggle команди модерації
            command_key = query.data.replace("mod_toggle_", "mod_")
            await toggle_mod_command(update, context, command_key)
        elif query.data == "menu_messages":
            await menu_messages_callback(update, context)
        elif query.data == "msg_cmd_sayon":
            await msg_cmd_sayon_callback(update, context)
        elif query.data == "msg_cmd_sayson":
            await msg_cmd_sayson_callback(update, context)
        elif query.data == "msg_cmd_sayoffall":
            await msg_cmd_sayoffall_callback(update, context)
        elif query.data in ["menu_admins", "menu_blacklist", "menu_text_commands", 
                            "menu_birthdays", "menu_broadcast", "menu_notes", "menu_reminders",
                            "menu_profile", "menu_info", "menu_backup", "menu_config"]:
            # Скелет для інших меню
            await query.answer("⏳ Ця категорія ще у розробці...", show_alert=True)
            logger.info(f"⏳ [Menu] Категорія {query.data} ще у розробці")
        else:
            await query.answer("❓ Невідома команда", show_alert=True)
    except Exception as e:
        logger.error(f"❌ [MenuCallback] Помилка: {e}")
        await query.answer(f"❌ Помилка: {e}", show_alert=True)

async def profile_set_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Показати всі команди налаштування профілю"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    profile_text = """👤 НАЛАШТУВАННЯ ПРОФІЛЮ

📝 ІМ'Я:
/myname - встановити кастомне імʼя (видиме скрізь)
  Приклад: /myname 🎮 Геймер Pro
/del_myname - видалити кастомне імʼя

📸 ФОТО/GIF ПРОФІЛЮ:
/mym - встановити фото/GIF (reply на медіа)
  Приклад: (reply на фото) /mym
/del_mym - видалити фото/GIF профілю

📄 ОПИС ПРОФІЛЮ:
/mymt - встановити опис про себе (до 300 символів)
  Приклад: /mymt Люблю програмування і кіно
/del_mymt - видалити опис

👁️ ПЕРЕГЛЯНУТИ:
/hto - переглянути свій профіль
/profile - переглянути профіль (з датою народження)

🎖️ ПОСАДА (для адмінів):
/custom_main - встановити кастомну посаду (reply)
  Приклад: (reply) /custom_main 🔴 Головний Адмін"""
    
    await reply_and_delete(update, profile_text, delay=60)

async def myname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Встановити кастомне імʼя (видиме скрізь в команді)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    if not context.args:
        current_name = db.get_custom_name(user_id)
        if current_name:
            await reply_and_delete(update, f"📝 Ваше кастомне імʼя: {current_name}\n\nЯк використовувати: /myname [нове імʼя]\nЩоб видалити: /myname - або /myname clear", delay=60)
        else:
            await reply_and_delete(update, "❌ Вкажіть імʼя!\nПриклад: /myname Мій Нік\nЩоб видалити: /myname - або /myname clear", delay=60)
        return
    
    custom_name = ' '.join(context.args)
    
    # Видалення кастомного імʼя
    if custom_name in ['-', 'clear']:
        old_name = db.get_custom_name(user_id)
        if db.delete_custom_name(user_id):
            old_name_text = f" ({old_name})" if old_name else ""
            await reply_and_delete(update, f"✅ Кастомне імʼя{old_name_text} видалено! Тепер видиме стандартне імʼя.", delay=60)
            logger.info(f"🗑️ Видалено кастомне імʼя '{old_name}' користувачем {user_id}")
        else:
            await reply_and_delete(update, "❌ Помилка при видаленні кастомного імʼя!", delay=60)
        return
    
    # Встановлення нового імʼя
    if len(custom_name) > 100:
        await reply_and_delete(update, "❌ Імʼя занадто довге (максимум 100 символів)!", delay=60)
        return
    
    if db.set_custom_name(user_id, custom_name):
        await reply_and_delete(update, f"✅ Кастомне імʼя встановлено!\n📝 Ваше нове імʼя: {custom_name}\n\nТепер воно буде видиме скрізь!", delay=60)
        logger.info(f"✏️ Користувач {user_id} встановив кастомне імʼя: {custom_name}")
    else:
        await reply_and_delete(update, "❌ Помилка при встановленні кастомного імʼя!", delay=60)

async def mym_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Встановити профіль-гіфку або фото, або видалити (-) """
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Видалення фото/гіфки
    if context.args and context.args[0] == '-':
        pic = db.get_profile_picture(user_id)
        old_pic_text = f" ({pic['media_type']})" if pic else ""
        if db.delete_profile_picture(user_id):
            await reply_and_delete(update, f"✅ Профіль-фото{old_pic_text} видалено! Тепер видиме стандартне.", delay=60)
            logger.info(f"🗑️ Видалено профіль-фото користувачем {user_id}")
        else:
            await reply_and_delete(update, "❌ Помилка при видаленні фото!", delay=60)
        return
    
    # Перевіряємо чи це reply на медіа
    if not update.message.reply_to_message:
        await reply_and_delete(update, "❌ Відповідьте на гіфку або фото!\nЩоб видалити: /mym -", delay=60)
        return
    
    reply = update.message.reply_to_message
    
    if reply.animation:
        # Це гіфка
        file_id = reply.animation.file_id
        media_type = "gif"
        emoji = "🎬"
    elif reply.photo:
        # Це фото
        file_id = reply.photo[-1].file_id
        media_type = "photo"
        emoji = "🖼️"
    else:
        await reply_and_delete(update, "❌ Це не гіфка і не фото!", delay=60)
        return
    
    if db.set_profile_picture(user_id, media_type, file_id):
        await reply_and_delete(update, f"✅ Профіль-{emoji} встановлено!", delay=60)
        logger.info(f"🖼️ Користувач {user_id} встановив профіль-{media_type}")
        
        # Логування в канал
        if LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=f"🖼️ Користувач {update.effective_user.full_name} [{user_id}] встановив профіль-{media_type}"
                )
            except:
                pass
    else:
        await reply_and_delete(update, "❌ Помилка при встановленні фото!", delay=60)

async def mymt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Встановити опис профілю або видалити (-)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not context.args:
        current_desc = db.get_profile_description(user_id)
        if current_desc:
            await reply_and_delete(update, f"📄 Ваш опис: {current_desc}\n\nЯк використовувати: /mymt [новий опис]\nЩоб видалити: /mymt - або /mymt clear", delay=60)
        else:
            await reply_and_delete(update, "❌ Вкажіть опис!\nПриклад: /mymt Я люблю програмування\nЩоб видалити: /mymt - або /mymt clear", delay=60)
        return
    
    description = " ".join(context.args)
    
    # Видалення опису
    if description in ['-', 'clear']:
        old_desc = db.get_profile_description(user_id)
        if db.delete_profile_description(user_id):
            old_desc_text = f" ({old_desc})" if old_desc else ""
            await reply_and_delete(update, f"✅ Опис профілю{old_desc_text} видалено! Тепер видиме стандартне.", delay=60)
            logger.info(f"🗑️ Видалено опис профілю '{old_desc}' користувачем {user_id}")
        else:
            await reply_and_delete(update, "❌ Помилка при видаленні опису!", delay=60)
        return
    
    # Встановлення нового опису
    if len(description) > 300:
        await reply_and_delete(update, "❌ Опис занадто довгий (максимум 300 символів)!", delay=60)
        return
    
    if db.set_profile_description(user_id, description):
        await reply_and_delete(update, f"✅ Опис профілю встановлено!\n📄 {description}", delay=60)
        logger.info(f"📝 Користувач {user_id} встановив опис: {description}")
        
        # Логування в канал
        if LOG_CHANNEL_ID:
            try:
                await context.bot.send_message(
                    chat_id=LOG_CHANNEL_ID,
                    text=f"📝 Користувач {update.effective_user.full_name} [{user_id}] встановив опис профілю"
                )
            except:
                pass
    else:
        await reply_and_delete(update, "❌ Помилка при встановленні опису!", delay=60)

def parse_time_to_seconds(time_str: str) -> int:
    match = re.match(r'(\d+)([dmh])', time_str)
    if not match:
        return 0
    
    value, unit = match.groups()
    value = int(value)
    
    if unit == 'm':
        return value * 60
    elif unit == 'h':
        return value * 3600
    elif unit == 'd':
        return value * 86400
    
    return 0

def parse_reminder_time(time_arg1: str, time_arg2: Optional[str] = None) -> Optional[datetime]:
    """
    Парсить час нагадування:
    1. Якщо 2 аргументи: ДАТА ЧАС (25.11.2025 18:50)
    2. Якщо 1 аргумент: ЧАС на сьогодні (18:50)
    Повертає datetime або None якщо помилка
    """
    try:
        if time_arg2:
            # Формат: 25.11.2025 18:50
            date_str = time_arg1
            time_str = time_arg2
            dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
        else:
            # Формат: 18:50 на сьогодні
            time_str = time_arg1
            today = datetime.now().date()
            dt = datetime.strptime(f"{today.strftime('%d.%m.%Y')} {time_str}", "%d.%m.%Y %H:%M")
        
        # Якщо час вже пройшов - ставимо на завтра (для часу без дати)
        if not time_arg2 and dt < datetime.now():
            dt = dt + timedelta(days=1)
        
        # Конвертуємо в Київський час
        kyiv_tz = pytz.timezone('Europe/Kyiv')
        dt = kyiv_tz.localize(dt)
        
        return dt
    except:
        return None

async def reminder_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    if not context.args or len(context.args) < 2:
        await reply_and_delete(update, "❌ Використання: /reminder [час: 1m/1h/1d] [текст]\nПриклад: /reminder 1h важливо запам'ятати")
        return
    
    time_str = context.args[0]
    reminder_text = " ".join(context.args[1:])
    
    seconds = parse_time_to_seconds(time_str)
    
    if seconds == 0:
        await reply_and_delete(update, "❌ Невірний формат часу! Використовуйте: 1m, 1h, 1d")
        return
    
    remind_at = (datetime.now() + timedelta(seconds=seconds)).isoformat()
    
    db.add_reminder(user_id, None, reminder_text, remind_at, update.effective_chat.id if update.effective_chat else None)
    
    # Клікабельне ім'я користувача
    clickable_name = f"<a href='tg://user?id={user_id}'>{update.effective_user.full_name}</a>"
    await reply_and_delete(update, f"⏰ Нагадування для {clickable_name} встановлено на {time_str}!", parse_mode="HTML")

async def reminde_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    logger.info(f"📝 [reminde_command] ВХІД з args: {context.args}")
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    # Мінімум 3 аргументи: @user ЧАС [текст]
    if not context.args or len(context.args) < 3:
        await reply_and_delete(update, "❌ Використання:\n/reminde @user ЧАС [текст]\n\nПриклади:\n/reminde @john 18:50 зайти в варзону\n/reminde @john 25.11.2025 18:50 зайти в варзону")
        return
    
    identifier = context.args[0]
    
    target_user = await get_user_info(update, context, identifier)
    
    if not target_user:
        await reply_and_delete(update, "❌ Користувача не знайдено!")
        return
    
    # Парсимо час - може бути 2 або 3 аргументи після @user
    # /reminde @user 18:50 текст текст
    # /reminde @user 25.11.2025 18:50 текст текст
    
    if len(context.args) >= 4 and re.match(r'\d{1,2}\.\d{1,2}\.\d{4}', context.args[1]):
        # Формат: /reminde @user ДАТА ЧАС текст
        date_str = context.args[1]
        time_str = context.args[2]
        reminder_text = " ".join(context.args[3:])
        remind_dt = parse_reminder_time(date_str, time_str)
    else:
        # Формат: /reminde @user ЧАС текст
        time_str = context.args[1]
        reminder_text = " ".join(context.args[2:])
        remind_dt = parse_reminder_time(time_str)
    
    if not remind_dt:
        await reply_and_delete(update, "❌ Невірний формат часу!\nВикористовуйте:\n• ЧАС: 18:50\n• ДАТА та ЧАС: 25.11.2025 18:50")
        return
    
    remind_at = remind_dt.isoformat()
    
    # Визначаємо chat_id для відправки:
    # Якщо команда в приватні, то нагадування по замовчуванню йде в основний чат USER_CHAT_ID
    # Якщо команда в групі, то в ту групу де писали
    target_chat_id = update.effective_chat.id if update.effective_chat else USER_CHAT_ID
    if update.effective_chat and update.effective_chat.type == "private":
        target_chat_id = USER_CHAT_ID

    db.add_reminder(user_id, target_user["user_id"], reminder_text, remind_at, target_chat_id)
    
    # Клікабельне ім'я користувача
    clickable_name = f"<a href='tg://user?id={target_user['user_id']}'>{target_user['full_name']}</a>"
    display_time = remind_dt.strftime("%d.%m.%Y %H:%M") if remind_dt else time_str
    await reply_and_delete(update, f"⏰ Нагадування для {clickable_name} встановлено на {display_time}!", parse_mode="HTML")
    logger.info(f"⏰ [reminde_command] Нагадування створено для {target_user['full_name']} на {display_time}")

async def birthdays_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    birthdays = db.get_all_birthdays()
    
    if not birthdays:
        await reply_and_delete(update, "🎂 Днів народження не знайдено")
        return
    
    today = datetime.now()
    birthday_list = []
    
    for bd in birthdays:
        try:
            birth_date = datetime.strptime(bd["birth_date"], "%d.%m.%Y")
            next_birthday = birth_date.replace(year=today.year)
            
            if next_birthday < today:
                next_birthday = next_birthday.replace(year=today.year + 1)
            
            days_until = (next_birthday - today).days
            
            # Отримуємо ID користувача для клікабельного лінку
            user_info = db.get_user_by_username(bd['username']) if bd['username'] else None
            user_id = user_info['user_id'] if user_info else None
            
            # Отримуємо ПОТОЧНЕ ім'я користувача (може змінитись після додання дня народження)
            if user_id:
                current_user = db.get_user(user_id)
                current_full_name = current_user['full_name'] if current_user else bd['full_name']
            else:
                current_full_name = bd['full_name']
            
            # Створюємо клікабельне ім'я з HTML-лінком
            if user_id:
                clickable_name = f"<a href='tg://user?id={user_id}'>{current_full_name}</a>"
            else:
                clickable_name = current_full_name
            
            username_str = f"(@{bd['username']})" if bd['username'] else ""
            
            birthday_list.append({
                "name": clickable_name,
                "username_str": username_str,
                "date": bd["birth_date"],
                "days": days_until
            })
        except:
            pass
    
    birthday_list.sort(key=lambda x: x["days"])
    
    message = "🎂 Дні народження:\n\n"
    
    for idx, bd in enumerate(birthday_list, 1):
        days = bd['days']
        if days % 10 == 1 and days % 100 != 11:
            day_word = "день"
        elif days % 10 in [2, 3, 4] and days % 100 not in [12, 13, 14]:
            day_word = "дні"
        else:
            day_word = "днів"
        message += f"{idx}. {bd['name']} {bd['username_str']} {bd['date']} [{days} {day_word}]\n"
    
    await reply_and_delete(update, message, parse_mode="HTML", delay=40)

# ===== КОРИСТУВАЦЬКІ КОМАНДИ =====

async def set_cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Встановити текстовий дублер команди /set_cmd бан giveperm"""
    save_user_from_update(update)
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not context.args or len(context.args) < 2:
        await reply_and_delete(update, "❌ Використання: /set_cmd <назва> <команда>\nПриклад: /set_cmd бан ban")
        return
    
    # ОСТАННЄ слово - команда, ВСЕ ІНШЕ - назва дублера
    target_cmd = context.args[-1].lower()  # Останнє слово
    alias_name = " ".join(context.args[:-1]).lower()  # Все крім останнього
    
    # Видаляємо / якщо є
    if target_cmd.startswith('/'):
        target_cmd = target_cmd.lstrip('/')
    
    # Перевіряємо що команда існує в COMMAND_HANDLERS
    if target_cmd not in COMMAND_HANDLERS:
        valid_commands = ", ".join(sorted(COMMAND_HANDLERS.keys())[:15])
        error_msg = f"""❌ Команда '<b>/{target_cmd}</b>' не знайдена!

✅ Коректні команди для дублерів:
<code>{valid_commands}... та інші</code>"""
        logger.warning(f"❌ [set_cmd] Команда '{target_cmd}' не існує в COMMAND_HANDLERS")
        await reply_and_delete(update, error_msg, parse_mode="HTML", delay=60)
        return
    
    logger.info(f"🔤 [set_cmd] Створення дублера: '{alias_name}' -> '/{target_cmd}' (перевіренo в COMMAND_HANDLERS)")
    
    try:
        # Зберігаємо дублер БЕЗ слеша - при виконанні бот додасть слеш
        db.add_command_alias(update.effective_chat.id, alias_name, target_cmd, user_id)
        logger.info(f"✅ [set_cmd] Дублер '{alias_name}' → '/{target_cmd}' збережено в БД")
        await reply_and_delete(update, f"""✅ Дублер створено!
<b>{alias_name}</b> → /{target_cmd}

📌 Тепер напишіть: <b>{alias_name}</b>
   і запуститься команда: /{target_cmd}""", parse_mode="HTML", delay=60)
    except Exception as e:
        logger.error(f"❌ [set_cmd] Помилка: {str(e)}")
        await reply_and_delete(update, f"❌ Помилка: {str(e)}")

async def del_cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видалити текстовий дублер команди"""
    save_user_from_update(update)
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not context.args or len(context.args) < 1:
        await reply_and_delete(update, "❌ Використання: /del_cmd <назва>\nПриклад: /del_cmd бан")
        return
    
    alias_name = context.args[0].lower()
    db.delete_command_alias(update.effective_chat.id, alias_name)
    await reply_and_delete(update, f"✅ Дублер '{alias_name}' видалено!")

async def doubler_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати список всіх текстових дублерів команд"""
    save_user_from_update(update)
    if not update.effective_user or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    if not can_ban_mute(user_id):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ У вас немає прав!"
        )
        return
    
    logger.info(f"📋 [Doubler] Команда від {user_id} в чаті {update.effective_chat.id}")
    
    aliases = db.get_all_command_aliases(update.effective_chat.id)
    
    if not aliases:
        logger.info(f"📋 [Doubler] Дублерів не знайдено")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Дублерів команд не знайдено!"
        )
        return
    
    text = "📋 СПИСОК ТЕКСТОВИХ ДУБЛЕРІВ:\n\n"
    for idx, alias in enumerate(aliases, 1):
        text += f"{idx}. <b>{alias['alias']}</b> → {alias['command']}\n"
    
    logger.info(f"✅ [Doubler] Показано {len(aliases)} дублерів")
    msg = await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        parse_mode="HTML"
    )
    
    # Видаляємо повідомлення через 60 секунд
    asyncio.create_task(delete_message_after_delay(msg, 60))

async def set_personal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Створити персональну команду /set_personal дати копня @s1 дав копня @s2"""
    save_user_from_update(update)
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not context.args or len(context.args) < 2:
        msg = "❌ Використання: /set_personal <назва команди> <шаблон з @s1/@s2/@t>\n"
        msg += "Шаблон: @s1 = відправник, @s2 = одержувач, @t = додатковий текст\n"
        msg += "Приклад: /set_personal дати копня @s1 дав копня @s2"
        await reply_and_delete(update, msg)
        return
    
    # Знаходимо перший плейсхолдер (@s1, @s2, @t)
    placeholder_idx = -1
    for i, arg in enumerate(context.args):
        if arg.lower() in ['@s1', '@s2', '@t']:
            placeholder_idx = i
            break
    
    if placeholder_idx == -1:
        await reply_and_delete(update, "❌ Шаблон має містити хоча б один плейсхолдер (@s1, @s2 або @t)!")
        return
    
    # Все до плейсхолдера - назва команди
    cmd_name = ' '.join(context.args[:placeholder_idx]).lower()
    # Все від плейсхолдера - шаблон
    template = ' '.join(context.args[placeholder_idx:])
    
    try:
        cmd_id = db.add_personal_command(update.effective_chat.id, cmd_name, template, user_id)
        context.chat_data['last_personal_cmd_id'] = cmd_id
        await reply_and_delete(update, f"✅ Персональну команду '{cmd_name}' створено!\n💬 Шаблон: {template}")
    except Exception as e:
        await reply_and_delete(update, f"❌ Помилка: {str(e)}")

async def set_cmdm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Додати медіа до персональної команди - reply на фото/гіф/відео"""
    save_user_from_update(update)
    logger.info(f"🎬 [set_cmdm] ВХІД в функцію")
    
    if not update.effective_user or not update.message or not update.effective_chat:
        logger.warning(f"🎬 [set_cmdm] Відсутні обов'язкові дані")
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    logger.info(f"🎬 [set_cmdm] user_id={user_id}, chat_id={chat_id}")
    
    if not can_ban_mute(user_id):
        logger.warning(f"🎬 [set_cmdm] Користувач {user_id} немає прав")
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not update.message.reply_to_message:
        logger.warning(f"🎬 [set_cmdm] Немає reply_to_message")
        await reply_and_delete(update, "❌ Відповідьте на фото/гіф/відео")
        return
    
    has_photo = bool(update.message.reply_to_message.photo)
    has_anim = bool(update.message.reply_to_message.animation)
    has_video = bool(update.message.reply_to_message.video)
    has_sticker = bool(update.message.reply_to_message.sticker)
    logger.info(f"🎬 [set_cmdm] Media check: photo={has_photo}, animation={has_anim}, video={has_video}, sticker={has_sticker}")
    
    if not (has_photo or has_anim or has_video or has_sticker):
        logger.warning(f"🎬 [set_cmdm] Немає медіа-файла")
        await reply_and_delete(update, "❌ Відповідьте на фото/гіф/відео/стікер")
        return
    
    if not context.args or len(context.args) < 1:
        logger.warning(f"🎬 [set_cmdm] Немає аргументів")
        await reply_and_delete(update, "❌ Використання: Reply на медіа (фото/гіф/відео/стікер) + /set_cmdm <назва_команди>")
        return
    
    cmd_name = ' '.join(context.args).lower()
    logger.info(f"🎬 [set_cmdm] Шукаємо команду: '{cmd_name}'")
    cmd_info = db.get_personal_command(chat_id, cmd_name)
    
    if not cmd_info:
        logger.warning(f"🎬 [set_cmdm] Команда '{cmd_name}' не знайдена в чаті {chat_id}!")
        await reply_and_delete(update, f"❌ Команда '{cmd_name}' не знайдена!")
        return
    
    logger.info(f"🎬 [set_cmdm] Знайдена команда: id={cmd_info['id']}")
    
    msg = update.message.reply_to_message
    media_type = None
    file_id = None
    
    if msg.photo:
        media_type = "photo"
        file_id = msg.photo[-1].file_id
    elif msg.animation:
        media_type = "animation"
        file_id = msg.animation.file_id
    elif msg.video:
        media_type = "video"
        file_id = msg.video.file_id
    elif msg.sticker:
        media_type = "sticker"
        file_id = msg.sticker.file_id
    
    logger.info(f"🎬 [set_cmdm] Додаємо медіа: type={media_type}, file_id={file_id[:20]}...")
    
    if db.add_personal_command_media(cmd_info['id'], media_type, file_id):
        # Рахуємо скільки всього медіа тепер в команді
        all_media = db.get_personal_command_media(cmd_info['id'])
        count = len(all_media) if all_media else 0
        logger.info(f"✅ [set_cmdm] Медіа успішно додано! Всього медіа: {count}")
        await reply_and_delete(update, f"✅ Медіа до команди '{cmd_name}' додано!\n📊 Всього медіа: {count}")
    else:
        logger.error(f"❌ [set_cmdm] Помилка при додаванні медіа до команди '{cmd_name}'")
        await reply_and_delete(update, "❌ Помилка при додаванні медіа")

async def list_cmdm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати список медіа в персональній команді"""
    save_user_from_update(update)
    logger.info(f"📋 [list_cmdm] ВХІД в функцію")
    
    if not update.effective_user or not update.message or not update.effective_chat:
        logger.warning(f"📋 [list_cmdm] Відсутні обов'язкові дані")
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not can_ban_mute(user_id):
        logger.warning(f"📋 [list_cmdm] Користувач {user_id} немає прав")
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not context.args or len(context.args) < 1:
        logger.warning(f"📋 [list_cmdm] Немає аргументів")
        await reply_and_delete(update, "❌ Використання: /list_cmdm <назва_команди>")
        return
    
    cmd_name = ' '.join(context.args).lower()
    logger.info(f"📋 [list_cmdm] Шукаємо команду: '{cmd_name}'")
    cmd_info = db.get_personal_command(chat_id, cmd_name)
    
    if not cmd_info:
        logger.warning(f"📋 [list_cmdm] Команда '{cmd_name}' не знайдена в чаті {chat_id}!")
        await reply_and_delete(update, f"❌ Команда '{cmd_name}' не знайдена!")
        return
    
    media_list = db.get_personal_command_media(cmd_info['id'])
    
    if not media_list:
        await reply_and_delete(update, f"❌ У команди '{cmd_name}' немає медіа!")
        return
    
    msg = f"📊 МЕДІА У КОМАНДИ '{cmd_name}':\n\n"
    for i, media in enumerate(media_list, 1):
        msg += f"{i}️⃣ {media['type'].upper()}\n"
    
    msg += f"\n💡 Усього: {len(media_list)} медіа\n"
    msg += f"🎲 При виконанні команди буде відправлена випадкова!"
    
    await reply_and_delete(update, msg, delay=60)

async def del_cmdm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видалити медіа з персональної команди - reply на гіф/фото/відео"""
    save_user_from_update(update)
    logger.info(f"🗑️ [del_cmdm] ВХІД в функцію")
    
    if not update.effective_user or not update.message or not update.effective_chat:
        logger.warning(f"🗑️ [del_cmdm] Відсутні обов'язкові дані")
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not can_ban_mute(user_id):
        logger.warning(f"🗑️ [del_cmdm] Користувач {user_id} немає прав")
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    # Обов'язково потрібна reply на медіа
    if not update.message.reply_to_message:
        logger.warning(f"🗑️ [del_cmdm] Немає reply_to_message")
        await reply_and_delete(update, "❌ Відповідьте на фото/гіф/відео/стікер для видалення!")
        return
    
    # Перевіряємо що це медіа
    msg = update.message.reply_to_message
    file_id = None
    media_type = None
    
    if msg.photo:
        file_id = msg.photo[-1].file_id
        media_type = "photo"
    elif msg.animation:
        file_id = msg.animation.file_id
        media_type = "animation"
    elif msg.video:
        file_id = msg.video.file_id
        media_type = "video"
    elif msg.sticker:
        file_id = msg.sticker.file_id
        media_type = "sticker"
    
    if not file_id:
        logger.warning(f"🗑️ [del_cmdm] У reply немає медіа")
        await reply_and_delete(update, "❌ Відповідьте саме на медіа-файл (фото/гіф/відео/стікер)!")
        return
    
    logger.info(f"🗑️ [del_cmdm] Витягнуто file_id: {file_id[:20]}... (тип: {media_type})")
    logger.info(f"🗑️ [del_cmdm] context.args отримані: {context.args}")
    logger.info(f"🗑️ [del_cmdm] Текст команди: '{update.message.text}'")
    
    # Потрібна назва команди в аргументах
    if not context.args or len(context.args) < 1:
        logger.warning(f"🗑️ [del_cmdm] Немає назви команди в args")
        msg = "❌ Використання: Reply на медіа + napишіть:\n/del_cmdm <назва_команди>\n\n📌 Приклад:\n/del_cmdm дати в рот"
        await reply_and_delete(update, msg)
        return
    
    cmd_name = ' '.join(context.args).lower()
    logger.info(f"🗑️ [del_cmdm] Шукаємо команду: '{cmd_name}'")
    cmd_info = db.get_personal_command(chat_id, cmd_name)
    
    if not cmd_info:
        logger.warning(f"🗑️ [del_cmdm] Команда '{cmd_name}' не знайдена в чаті {chat_id}!")
        await reply_and_delete(update, f"❌ Команда '{cmd_name}' не знайдена!")
        return
    
    media_list = db.get_personal_command_media(cmd_info['id'])
    
    if not media_list:
        logger.warning(f"🗑️ [del_cmdm] У команди '{cmd_name}' немає медіа")
        await reply_and_delete(update, f"❌ У команди '{cmd_name}' немає медіа!")
        return
    
    # Шукаємо медіа з цим file_id
    found_media = None
    for media in media_list:
        if media['file_id'] == file_id:
            found_media = media
            break
    
    if not found_media:
        logger.warning(f"🗑️ [del_cmdm] Медіа з цим file_id не знайдена в команді '{cmd_name}'")
        await reply_and_delete(update, f"❌ Ця медіа не знайдена у команді '{cmd_name}'!")
        return
    
    # Видаляємо медіа
    if db.delete_personal_command_media(found_media['id']):
        logger.info(f"✅ [del_cmdm] Медіа {media_type} видалено з команди '{cmd_name}'")
        
        remaining = len(media_list) - 1
        if remaining > 0:
            await reply_and_delete(update, f"✅ {media_type.upper()} видалено!\n📊 Залишилось: {remaining} медіа")
        else:
            await reply_and_delete(update, f"✅ Медіа видалено!\n⚠️ Тепер команда '{cmd_name}' буде відправлятись БЕЗ медіа (тільки текст з описом)!")
    else:
        logger.error(f"❌ [del_cmdm] Помилка при видаленні медіа")
        await reply_and_delete(update, "❌ Помилка при видаленні медіа")

async def del_personal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видалити персональну команду"""
    save_user_from_update(update)
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not context.args or len(context.args) < 1:
        await reply_and_delete(update, "❌ Використання: /del_personal <назва>")
        return
    
    cmd_name = ' '.join(context.args).lower()
    db.delete_personal_command(update.effective_chat.id, cmd_name)
    await reply_and_delete(update, f"✅ Команда '{cmd_name}' видалена!")

async def set_adminm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Додати стікер/гіф до команди адміна"""
    save_user_from_update(update)
    logger.info(f"🎬 [set_adminm] ВХІД в функцію")
    
    if not update.effective_user or not update.message or not update.effective_chat:
        logger.warning(f"🎬 [set_adminm] Відсутні обов'язкові дані")
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not can_ban_mute(user_id):
        logger.warning(f"🎬 [set_adminm] Користувач {user_id} немає прав")
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not update.message.reply_to_message:
        logger.warning(f"🎬 [set_adminm] Немає reply_to_message")
        await reply_and_delete(update, "❌ Відповідьте на стікер/гіф")
        return
    
    has_anim = bool(update.message.reply_to_message.animation)
    has_sticker = bool(update.message.reply_to_message.sticker)
    logger.info(f"🎬 [set_adminm] Media check: animation={has_anim}, sticker={has_sticker}")
    
    if not (has_anim or has_sticker):
        logger.warning(f"🎬 [set_adminm] Немає медіа-файла")
        await reply_and_delete(update, "❌ Відповідьте на стікер/гіф")
        return
    
    if not context.args or len(context.args) < 1:
        logger.warning(f"🎬 [set_adminm] Немає аргументів")
        await reply_and_delete(update, "❌ Використання: /set_adminm <назва_команди>")
        return
    
    cmd_name = ' '.join(context.args).lower()
    msg = update.message.reply_to_message
    media_type = None
    file_id = None
    
    if msg.animation:
        media_type = "animation"
        file_id = msg.animation.file_id
    elif msg.sticker:
        media_type = "sticker"
        file_id = msg.sticker.file_id
    
    logger.info(f"🎬 [set_adminm] Додаємо медіа: type={media_type}, file_id={file_id[:20]}...")
    
    if db.add_admin_command_media(chat_id, cmd_name, media_type, file_id):
        logger.info(f"✅ [set_adminm] Медіа успішно додано до команди '{cmd_name}'")
        await reply_and_delete(update, f"✅ {media_type.upper()} до команди '{cmd_name}' додано!\n💬 Коли кидати цю {media_type} в чат - виконаєтьсяся команда!")
    else:
        logger.error(f"❌ [set_adminm] Помилка при додаванні медіа")
        await reply_and_delete(update, "❌ Помилка при додаванні медіа")

async def del_adminm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видалити стікер/гіф з команди адміна"""
    save_user_from_update(update)
    logger.info(f"🗑️ [del_adminm] ВХІД в функцію")
    
    if not update.effective_user or not update.message or not update.effective_chat:
        logger.warning(f"🗑️ [del_adminm] Відсутні обов'язкові дані")
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    if not can_ban_mute(user_id):
        logger.warning(f"🗑️ [del_adminm] Користувач {user_id} немає прав")
        await reply_and_delete(update, "❌ У вас немає прав!")
        return
    
    if not update.message.reply_to_message:
        logger.warning(f"🗑️ [del_adminm] Немає reply_to_message")
        await reply_and_delete(update, "❌ Відповідьте на стікер/гіф для видалення!")
        return
    
    msg = update.message.reply_to_message
    file_id = None
    media_type = None
    
    if msg.animation:
        file_id = msg.animation.file_id
        media_type = "animation"
    elif msg.sticker:
        file_id = msg.sticker.file_id
        media_type = "sticker"
    
    if not file_id:
        logger.warning(f"🗑️ [del_adminm] У reply немає медіа")
        await reply_and_delete(update, "❌ Відповідьте саме на стікер/гіф!")
        return
    
    logger.info(f"🗑️ [del_adminm] Витягнуто file_id: {file_id[:20]}... (тип: {media_type})")
    logger.info(f"🗑️ [del_adminm] context.args отримані: {context.args}")
    
    if not context.args or len(context.args) < 1:
        logger.warning(f"🗑️ [del_adminm] Немає назви команди в args")
        await reply_and_delete(update, "❌ Використання: Reply на стікер/гіф + /del_adminm <назва_команди>")
        return
    
    cmd_name = ' '.join(context.args).lower()
    logger.info(f"🗑️ [del_adminm] Шукаємо медіа команди '{cmd_name}'")
    
    media_data = db.get_admin_command_by_file_id(chat_id, file_id)
    
    if not media_data:
        logger.warning(f"🗑️ [del_adminm] Медіа з цим file_id не знайдена")
        await reply_and_delete(update, "❌ Ця медіа не пов'язана з жодною командою!")
        return
    
    if media_data['command'] != cmd_name:
        logger.warning(f"🗑️ [del_adminm] Медіа пов'язана з командою '{media_data['command']}', а не '{cmd_name}'")
        await reply_and_delete(update, f"❌ Ця {media_type} пов'язана з командою '{media_data['command']}', а не '{cmd_name}'!")
        return
    
    if db.delete_admin_command_media(media_data['id']):
        logger.info(f"✅ [del_adminm] {media_type} видалено з команди '{cmd_name}'")
        await reply_and_delete(update, f"✅ {media_type.upper()} видалено з команди '{cmd_name}'!")
    else:
        logger.error(f"❌ [del_adminm] Помилка при видаленні медіа")
        await reply_and_delete(update, "❌ Помилка при видаленні медіа")

async def role_cmd_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати список всіх рольових команд (для всіх)"""
    save_user_from_update(update)
    if not update.effective_user or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    
    commands = db.get_all_personal_commands(update.effective_chat.id)
    
    if not commands:
        await reply_and_delete(update, "❌ Персональних команд не знайдено!")
        return
    
    msg = "📋 ПЕРСОНАЛЬНІ КОМАНДИ:\n"
    for cmd in commands:
        msg += f"🔹 {cmd['name']}\n"
    
    await reply_and_delete(update, msg, delay=60)

async def addb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    target_user = None
    birth_date = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        if not context.args or len(context.args) < 1:
            await reply_and_delete(update, "❌ Вкажіть дату народження у форматі ДД.ММ.РРРР\nПриклад: /addb 25.12.1990")
            return
        
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
        birth_date = context.args[0]
    
    elif context.args and len(context.args) >= 2:
        identifier = context.args[0]
        birth_date = context.args[1]
        target_user = await get_user_info(update, context, identifier)
        
        if not target_user and identifier.startswith('@'):
            target_user = {
                "user_id": 0,
                "username": identifier.lstrip('@'),
                "full_name": identifier.lstrip('@')
            }
    
    if not target_user or not birth_date:
        await reply_and_delete(update, "❌ Використання:\n1️⃣ /addb @username ДД.ММ.РРРР\n2️⃣ Відповісти на повідомлення з /addb ДД.ММ.РРРР\n\nПриклад: /addb @john 01.05.1990")
        return
    
    try:
        birth_obj = datetime.strptime(birth_date, "%d.%m.%Y")
        if birth_obj > datetime.now():
            await reply_and_delete(update, "❌ День народження не може бути в майбутньому!")
            return
    except ValueError as e:
        await reply_and_delete(update, "❌ Невірна дата! Перевірте:\n• День: 01-31\n• Місяць: 01-12\n• Рік: РРРР\n\nПриклад: /addb @john 13.06.1990")
        return
    
    db.add_birthday(target_user["user_id"], birth_date, user_id, target_user["username"], target_user["full_name"])
    
    await reply_and_delete(update, f"✅ День народження {target_user['full_name']} ({birth_date}) збережено!")

async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати профіль користувача з датою народження"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.effective_chat:
        return
    
    # Визначаємо кого профіль показувати
    target_user_id = update.effective_user.id
    
    # Якщо є аргумент - показуємо профіль цього користувача
    if context.args:
        identifier = context.args[0]
        user_info = await get_user_info(update, context, identifier)
        if user_info:
            target_user_id = user_info["user_id"]
        else:
            await reply_and_delete(update, f"❌ Користувач {identifier} не знайдений!")
            return
    
    # Отримуємо інформацію про користувача
    user = db.get_user(target_user_id)
    if not user:
        await reply_and_delete(update, "❌ Користувача не знайдено!")
        return
    
    # Формуємо профіль
    profile_text = f"👤 <b>Профіль {safe_send_message(user['full_name'])}</b>\n\n"
    
    if user['username']:
        profile_text += f"📱 Username: @{safe_send_message(user['username'])}\n"
    
    profile_text += f"🆔 ID: <code>{user['user_id']}</code>\n"
    
    # Дата народження
    birth_date = db.get_birthday(target_user_id)
    if birth_date:
        profile_text += f"🎂 День народження: {birth_date}\n"
    
    # Опис профілю
    description = db.get_profile_description(target_user_id)
    if description:
        profile_text += f"\n💬 <b>Про себе:</b>\n{safe_send_message(description)}\n"
    
    # Дата входження
    if user['joined_at']:
        try:
            joined_dt = datetime.fromisoformat(user['joined_at'])
            profile_text += f"\n📅 Приєднався: {joined_dt.strftime('%d.%m.%Y о %H:%M')}\n"
        except:
            pass
    
    await reply_and_delete(update, profile_text, parse_mode="HTML")

async def delb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Видалити день народження користувача"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    # Якщо немає аргумента - видаляємо свій день народження
    if not context.args:
        if db.delete_birthday(user_id):
            await reply_and_delete(update, "✅ Ваш день народження видалено!")
        else:
            await reply_and_delete(update, "❌ У вас не встановлено день народження!")
        return
    
    # Якщо є аргумент - видаляємо за порядком з списку
    try:
        position = int(context.args[0])
        if position < 1:
            await reply_and_delete(update, "❌ Порядок має бути більше 0!")
            return
        
        birthdays = db.get_all_birthdays()
        if position > len(birthdays):
            await reply_and_delete(update, f"❌ Порядок {position} не існує! В списку всього {len(birthdays)} днів народження")
            return
        
        target_user_id = birthdays[position - 1]["user_id"]
        target_name = birthdays[position - 1]["full_name"]
        
        if db.delete_birthday(target_user_id):
            await reply_and_delete(update, f"✅ День народження {target_name} (позиція {position}) видалено!")
        else:
            await reply_and_delete(update, "❌ Помилка при видаленні!")
    
    except ValueError:
        await reply_and_delete(update, "❌ Вкажіть число (позицію з списку)\nПриклад: /delb 1 або /delb 2")

async def setbgif_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    if not update.message.reply_to_message or not update.message.reply_to_message.animation:
        await reply_and_delete(update, "❌ Відповідьте на повідомлення з GIF!")
        return
    
    gif_file_id = update.message.reply_to_message.animation.file_id
    db.set_birthday_gif(gif_file_id)
    
    if update.message.reply_to_message.caption:
        db.set_birthday_text(update.message.reply_to_message.caption)
    
    await reply_and_delete(update, "✅ GIF для привітань встановлено!")

async def setbtext_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_ban_mute(user_id):
        await reply_and_delete(update, "❌ У вас немає прав для цієї команди!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть текст привітання!")
        return
    
    greeting_text = " ".join(context.args)
    db.set_birthday_text(greeting_text)
    
    await reply_and_delete(update, "✅ Текст привітань встановлено!")

async def previewb_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Показати попередній перегляд привітань - текст і GIF (як буде виглядати при привітанні)"""
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для власника!")
        return
    
    settings = db.get_birthday_settings()
    gif_file_id = settings.get("gif_file_id")
    greeting_text = settings.get("greeting_text", "З Днем Народження!")
    
    # Формуємо тег з клікабельним посиланням на користувача
    username = update.effective_user.username
    user_name = update.effective_user.full_name or update.effective_user.first_name or "Користувачу"
    clickable_tag = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
    congratulation_text = f"Давайте привітаємо {clickable_tag}"
    
    if gif_file_id:
        try:
            sent_msg = await context.bot.send_animation(
                chat_id=update.effective_chat.id,
                animation=gif_file_id,
                caption=f"{greeting_text}\n\n{congratulation_text}",
                parse_mode="HTML"
            )
            # Закріплюємо повідомлення
            await context.bot.pin_chat_message(
                chat_id=update.effective_chat.id,
                message_id=sent_msg.message_id
            )
            logger.info(f"🎉 Попередження закріплено для {tag}")
        except Exception as e:
            logger.error(f"Помилка при відправці попередження GIF: {e}")
            await reply_and_delete(update, f"{greeting_text}\n\n{congratulation_text}")
    else:
        try:
            sent_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"{greeting_text}\n\n{congratulation_text}",
                parse_mode="HTML"
            )
            # Закріплюємо повідомлення
            await context.bot.pin_chat_message(
                chat_id=update.effective_chat.id,
                message_id=sent_msg.message_id
            )
            logger.info(f"🎉 Попередження закріплено для {tag}")
        except Exception as e:
            logger.error(f"Помилка при закріпленні попередження: {e}")
            await reply_and_delete(update, f"{greeting_text}\n\n{congratulation_text}")

async def adminchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    global ADMIN_CHAT_ID
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Тільки 7247114478 та 7516733683 можуть змінювати налаштування
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Тільки власники 7247114478 та 7516733683 можуть змінювати налаштування!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть ID адмін-чату!")
        return
    
    try:
        ADMIN_CHAT_ID = int(context.args[0])
        save_config()
        await reply_and_delete(update, f"✅ Адмін-чат змінено на {ADMIN_CHAT_ID}")
    except:
        await reply_and_delete(update, "❌ Невірний ID!")

async def userchat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    global USER_CHAT_ID
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Тільки 7247114478 та 7516733683 можуть змінювати налаштування
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Тільки власники 7247114478 та 7516733683 можуть змінювати налаштування!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть ID чату користувачів!")
        return
    
    try:
        USER_CHAT_ID = int(context.args[0])
        save_config()
        await reply_and_delete(update, f"✅ Чат користувачів змінено на {USER_CHAT_ID}")
    except:
        await reply_and_delete(update, "❌ Невірний ID!")

async def logchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    global LOG_CHANNEL_ID
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Тільки 7247114478 та 7516733683 можуть змінювати налаштування
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Тільки власники 7247114478 та 7516733683 можуть змінювати налаштування!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть ID каналу логування!")
        return
    
    try:
        LOG_CHANNEL_ID = int(context.args[0])
        save_config()
        await reply_and_delete(update, f"✅ Канал логування змінено на {LOG_CHANNEL_ID}")
    except:
        await reply_and_delete(update, "❌ Невірний ID!")

async def testchannel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    global TEST_CHANNEL_ID
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Тільки 7247114478 та 7516733683 можуть змінювати налаштування
    if user_id not in [7247114478, 7516733683]:
        await reply_and_delete(update, "❌ Тільки власники 7247114478 та 7516733683 можуть змінювати налаштування!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть ID тестового каналу!")
        return
    
    try:
        TEST_CHANNEL_ID = int(context.args[0])
        save_config()
        await reply_and_delete(update, f"✅ Тестовий канал змінено на {TEST_CHANNEL_ID}")
    except:
        await reply_and_delete(update, "❌ Невірний ID!")

async def santas_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not is_owner(user_id):
        return
    
    if not TEST_CHANNEL_ID:
        return
    
    if not update.message.reply_to_message:
        return
    
    try:
        replied_msg = update.message.reply_to_message
        
        # Спочатку спробуємо скопіювати (працює з bot messages і захищеним контентом)
        try:
            await context.bot.copy_message(
                chat_id=TEST_CHANNEL_ID,
                from_chat_id=update.effective_chat.id if update.effective_chat else USER_CHAT_ID,
                message_id=replied_msg.message_id
            )
            logger.info(f"🎅 /santas: Повідомлення скопійовано")
        except Exception as copy_error:
            logger.warning(f"⚠️ /santas: Помилка копіювання: {copy_error}, спробую альтернативний метод...")
            
            # Визначаємо тип медіа для логування
            media_type = "невідомо"
            if replied_msg.sticker:
                media_type = "стікер 📌"
            elif replied_msg.photo:
                media_type = "фото 🖼️"
            elif replied_msg.video:
                media_type = "відео 🎬"
            elif replied_msg.animation:
                media_type = "гіфка 🎞️"
            elif replied_msg.document:
                media_type = "документ 📎"
            elif replied_msg.audio:
                media_type = "аудіо 🎵"
            elif replied_msg.text:
                media_type = "текст 📝"
            
            logger.info(f"📤 /santas: Тип контенту: {media_type}")
            
            # Якщо копіювання не спрацює, пересилаємо
            try:
                await context.bot.forward_message(
                    chat_id=TEST_CHANNEL_ID,
                    from_chat_id=update.effective_chat.id if update.effective_chat else USER_CHAT_ID,
                    message_id=replied_msg.message_id
                )
                logger.info(f"✅ /santas: Повідомлення пересилано ({media_type})")
            except Exception as forward_error:
                logger.warning(f"⚠️ /santas: Помилка пересилання: {forward_error}, копіюю вміст...")
                
                # Останній варіант - копіюємо вміст (перевіряємо МЕДІА перед ТЕКСТОМ)
                if replied_msg.sticker:
                    logger.info("📌 /santas: Копіюю стікер")
                    await context.bot.send_sticker(
                        chat_id=TEST_CHANNEL_ID,
                        sticker=replied_msg.sticker.file_id
                    )
                elif replied_msg.photo:
                    logger.info("🖼️ /santas: Копіюю фото")
                    await context.bot.send_photo(
                        chat_id=TEST_CHANNEL_ID,
                        photo=replied_msg.photo[-1].file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.video:
                    logger.info("🎬 /santas: Копіюю відео")
                    await context.bot.send_video(
                        chat_id=TEST_CHANNEL_ID,
                        video=replied_msg.video.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.animation:
                    logger.info("🎞️ /santas: Копіюю гіфку")
                    await context.bot.send_animation(
                        chat_id=TEST_CHANNEL_ID,
                        animation=replied_msg.animation.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.document:
                    logger.info("📎 /santas: Копіюю документ")
                    await context.bot.send_document(
                        chat_id=TEST_CHANNEL_ID,
                        document=replied_msg.document.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.audio:
                    logger.info("🎵 /santas: Копіюю аудіо")
                    await context.bot.send_audio(
                        chat_id=TEST_CHANNEL_ID,
                        audio=replied_msg.audio.file_id,
                        caption=replied_msg.caption or ""
                    )
                elif replied_msg.text:
                    logger.info("📝 /santas: Копіюю текст")
                    await context.bot.send_message(
                        chat_id=TEST_CHANNEL_ID,
                        text=replied_msg.text,
                        parse_mode=None
                    )
                else:
                    logger.warning("❓ /santas: Невідомий тип повідомлення")
                    await context.bot.send_message(
                        chat_id=TEST_CHANNEL_ID,
                        text="[Повідомлення без тексту]"
                    )
        
        # Тихе збереження - без повідомлення користувачеві
        try:
            await update.message.delete()
        except:
            pass
        
    except Exception as e:
        logger.error(f"Помилка /santas: {e}")

async def check_and_send_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Перевіряє та надсилає нагадування користувачам - в приватні повідомлення І в чат"""
    try:
        reminders = db.get_pending_reminders()
        
        if not reminders:
            return
        
        for reminder in reminders:
            try:
                target_user_id = reminder['target_user_id']
                text = reminder['text']
                chat_id = reminder['chat_id']  # Чат звідки було встановлено нагадування
                
                # 🔍 Отримуємо інформацію про користувача щоб додати тег
                target_user = db.get_user(target_user_id) if target_user_id else None
                user_mention = ""
                
                if target_user:
                    # Формуємо HTML-лінку на користувача з його ім'ям
                    full_name = target_user.get('full_name', 'Користувач')
                    user_mention = f"<a href='tg://user?id={target_user_id}'>{full_name}</a>"
                    message_text = f"⏰ <b>НАГАДУВАННЯ:</b> {user_mention}\n\n{text}"
                else:
                    message_text = f"⏰ <b>НАГАДУВАННЯ:</b>\n\n{text}"
                
                # 1️⃣ Надсилаємо в приватні повідомлення користувачу
                try:
                    await context.bot.send_message(
                        chat_id=target_user_id,
                        text=message_text,
                        parse_mode="HTML"
                    )
                    logger.info(f"✅ [Reminders] Нагадування надіслано в приватні повідомлення {target_user_id}: {text[:50]}")
                except Exception as e:
                    logger.warning(f"⚠️ [Reminders] Не вдалось надіслати приватне повідомлення {target_user_id}: {e}")
                
                # 2️⃣ Надсилаємо в чат (групу)
                if chat_id:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=message_text,
                            parse_mode="HTML"
                        )
                        logger.info(f"✅ [Reminders] Нагадування надіслано в чат {chat_id}: {text[:50]}")
                    except Exception as e:
                        logger.warning(f"⚠️ [Reminders] Не вдалось надіслати в чат {chat_id}: {e}")
                
                # Помічаємо як надіслане
                db.mark_reminder_sent(reminder['id'])
            except Exception as e:
                logger.warning(f"⚠️ [Reminders] Помилка з нагаданням {reminder['id']}: {e}")
    except Exception as e:
        logger.error(f"❌ [Reminders] Помилка при перевірці нагадувань: {e}")

async def send_birthday_greetings(context: ContextTypes.DEFAULT_TYPE):
    """Відправляє привітання на дні народження о 08:00 Київського часу"""
    try:
        tz_kyiv = pytz.timezone('Europe/Kyiv')
        today = datetime.now(tz_kyiv).strftime("%d.%m")
        
        todays_birthdays = db.get_todays_birthdays()
        
        if not todays_birthdays:
            logger.info("🎂 Сьогодні немає днів народження")
            return
        
        settings = db.get_birthday_settings()
        gif_file_id = settings.get("gif_file_id")
        greeting_text = settings.get("greeting_text", "З Днем Народження!")
        
        for birthday_person in todays_birthdays:
            username = birthday_person.get("username")
            full_name = birthday_person.get("full_name", "Користувачу")
            
            # Формуємо тег з @username або ім'ям
            tag = f"@{username}" if username else full_name
            congratulation_text = f"Давайте привітаємо {tag}"
            message = f"{greeting_text}\n\n{congratulation_text}"
            
            try:
                if gif_file_id:
                    sent_msg = await context.bot.send_animation(
                        chat_id=USER_CHAT_ID,
                        animation=gif_file_id,
                        caption=message,
                        parse_mode=None
                    )
                    logger.info(f"🎉 Привітання з GIF надіслано {tag}")
                else:
                    sent_msg = await context.bot.send_message(
                        chat_id=USER_CHAT_ID,
                        text=message,
                        parse_mode=None
                    )
                    logger.info(f"🎉 Привітання надіслано {tag}")
                
                # Закріплюємо привітання
                try:
                    await context.bot.pin_chat_message(
                        chat_id=USER_CHAT_ID,
                        message_id=sent_msg.message_id
                    )
                    logger.info(f"📌 Привітання закріплено для {tag}")
                except Exception as e:
                    logger.warning(f"⚠️ Не вдалося закріпити привітання для {tag}: {e}")
            except Exception as e:
                logger.error(f"Помилка при відправці привітання {tag}: {e}")
    
    except Exception as e:
        logger.error(f"🎂 Помилка у send_birthday_greetings: {e}")

# ============ КОМАНДИ ДЛЯ ВИДАЛЕННЯ ПРОФІЛЮ ============

async def del_myname_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Видалити кастомне імʼя (-myname)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    old_name = db.get_custom_name(user_id)
    if not old_name:
        await reply_and_delete(update, "❌ У вас немає кастомного імʼя для видалення!")
        return
    
    if db.delete_custom_name(user_id):
        await reply_and_delete(update, f"✅ Кастомне імʼя видалено! ❌ ({old_name})\n→ Повернулось стандартне імʼя")
        logger.info(f"🗑️ Видалено кастомне імʼя '{old_name}' користувачем {user_id}")
    else:
        await reply_and_delete(update, "❌ Помилка при видаленні кастомного імʼя!")

async def del_mym_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Видалити профіль-фото (-mym)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    pic = db.get_profile_picture(user_id)
    if not pic:
        await reply_and_delete(update, "❌ У вас немає профіль-фото для видалення!")
        return
    
    pic_type = pic.get('media_type', 'невідомо')
    emoji = "🎬" if pic_type == "gif" else "🖼️"
    
    if db.delete_profile_picture(user_id):
        await reply_and_delete(update, f"✅ Профіль-фото видалено! ❌ ({pic_type})\n→ Повернулось стандартне {emoji}")
        logger.info(f"🗑️ Видалено профіль-{pic_type} користувачем {user_id}")
    else:
        await reply_and_delete(update, "❌ Помилка при видаленні фото!")

async def del_mymt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    """Видалити опис профілю (-mymt)"""
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    if not can_use_bot(user_id):
        await reply_and_delete(update, "❌ У вас немає доступу до цієї команди!")
        return
    
    old_desc = db.get_profile_description(user_id)
    if not old_desc:
        await reply_and_delete(update, "❌ У вас немає опису для видалення!")
        return
    
    if db.delete_profile_description(user_id):
        desc_preview = old_desc[:50] + "..." if len(old_desc) > 50 else old_desc
        await reply_and_delete(update, f"✅ Опис видалено! ❌ ({desc_preview})\n→ Повернулось стандартне")
        logger.info(f"🗑️ Видалено опис профілю користувачем {user_id}")
    else:
        await reply_and_delete(update, "❌ Помилка при видаленні опису!")

# ============ 13 НОВИХ КОМАНД ============

async def giveperm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Надати права адміністратора - власник/головні адміни 
    (просто: собі, reply: іншому користувачу)"""
    save_user_from_update(update)
    
    logger.info("🔐 [giveperm_command] ✅ Початок виконання команди")
    
    if not update.effective_user or not update.message or not update.effective_chat:
        logger.warning("🔐 [giveperm_command] ❌ Не вдалось отримати дані (user/message/chat)")
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    logger.info(f"🔐 [giveperm_command] ✅ User ID: {user_id}, Chat ID: {chat_id}")
    
    # ПЕРЕВІРИМО ЧИ КОРИСТУВАЧ ВЛАСНИК АБО ГОЛОВНИЙ АДМІН
    role = db.get_role(user_id)
    owner_check = is_owner(user_id)
    logger.info(f"🔐 [giveperm_command] ✅ Роль користувача: {role}, is_owner: {owner_check}, head_admin check: {role == 'head_admin'}")
    
    if not owner_check and role != "head_admin":
        logger.warning(f"🔐 [giveperm_command] ❌ Користувач {user_id} не має прав (не власник та не head_admin)")
        await reply_and_delete(update, "❌ Тільки власник та головні адміни можуть надавати права адміністратора!", delay=60)
        return
    
    # ОТРИМУЄМО ЦІЛЬОВОГО КОРИСТУВАЧА
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        # REPLY НА ПОВІДОМЛЕННЯ - ДАЄМО ПРАВА ІНШОМУ КОРИСТУВАЧУ
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    else:
        # БЕЗ REPLY - ДАЄМО ПРАВА САМОМУ АДМІНУ
        target_user = {
            "user_id": user_id,
            "username": update.effective_user.username or "",
            "full_name": update.effective_user.full_name or ""
        }
    
    target_user_id = target_user["user_id"]
    target_name = safe_send_message(target_user["full_name"])
    target_username = f"(@{target_user['username']})" if target_user["username"] else ""
    
    # НАДАЄМО ПРАВА АДМІНІСТРАТОРА З ПОСАДОЮ "ᅠ" (всі права)
    try:
        logger.info(f"🔐 [giveperm_command] Даємо права адміну користувачу {target_user_id} в чаті {chat_id}")
        
        # Спочатку видалимо права (якщо вони були) щоб переконатися, що задамо САМЕ ті права
        try:
            logger.debug(f"🔐 [giveperm_command] Спроба скидання прав користувача...")
            await context.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=target_user_id,
                is_anonymous=False
            )
            logger.debug(f"🔐 [giveperm_command] Права скинуті")
        except Exception as reset_error:
            logger.debug(f"🔐 [giveperm_command] Не вдалось скинути права (це нормально): {reset_error}")
            pass  # Можливо він не був адміном
        
        # Тепер даємо ВСІ права ОКРІМ редагування історій
        logger.info(f"🔐 [giveperm_command] Надання ВСІХ прав адміністратора (окрім редагування історій)...")
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target_user_id,
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True,
            can_restrict_members=True,
            can_promote_members=True,
            can_change_info=True,
            can_invite_users=True,
            can_pin_messages=True,
            can_manage_video_chats=True,
            can_edit_stories=False
        )
        logger.info(f"🔐 [giveperm_command] ✅ ПРАВА НАДАНІ УСПІШНО користувачу {target_user_id}")
        
        # Встановлюємо посаду "ᅠ"
        try:
            await context.bot.set_chat_administrator_custom_title(
                chat_id=chat_id,
                user_id=target_user_id,
                custom_title="ᅠ"
            )
        except Exception as title_error:
            logger.warning(f"⚠️ Не вдалось встановити посаду: {title_error}")
        
        logger.info(f"✅ Надані права адміністратора користувачу {target_user_id}")
        
        # Повідомлення в чат
        clickable_target_msg = f"<a href='tg://user?id={target_user_id}'>{target_name}</a>"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ {clickable_target_msg} {target_username} отримав адмінку зі всіма правами!",
            parse_mode="HTML"
        )
        
        # ЛОГУЄМО В КАНАЛ
        if LOG_CHANNEL_ID:
            try:
                admin_name = update.effective_user.full_name or "Невідомий"
                admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
                clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
                clickable_target = f"<a href='tg://user?id={target_user_id}'>{target_name}</a>"
                role_text = "Власник" if is_owner(user_id) else "Головний адмін"
                
                log_text = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
✅ Надав права адміністратора
{clickable_target} {target_username} [{target_user_id}]
• Посада: ᅠ
• Чат: {chat_id}"""
                
                await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"⚠️ Помилка при логуванні в канал: {e}")
    
    except Exception as e:
        logger.error(f"❌ Помилка при наданні прав адміністратора: {e}")
        await reply_and_delete(update, f"❌ Помилка при наданні прав: {str(e)[:100]}", delay=60)

async def giveperm_simple_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Надати звичайні права адміністратора - власник/головні адміни
    (просто: собі, reply: іншому користувачу)"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # ПЕРЕВІРИМО ЧИ КОРИСТУВАЧ ВЛАСНИК АБО ГОЛОВНИЙ АДМІН
    role = db.get_role(user_id)
    if not is_owner(user_id) and role != "head_admin":
        await reply_and_delete(update, "❌ Тільки власник та головні адміни можуть надавати права адміністратора!", delay=60)
        return

    # Обмеження на основний чат
    if chat_id != -1002646171857:
        await reply_and_delete(update, "❌ Надання прав адміністратора доступне тільки в основному чаті!", delay=60)
        return
    
    # ОТРИМУЄМО ЦІЛЬОВОГО КОРИСТУВАЧА
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        # REPLY НА ПОВІДОМЛЕННЯ - ДАЄМО ПРАВА ІНШОМУ КОРИСТУВАЧУ
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    else:
        # БЕЗ REPLY - ДАЄМО ПРАВА САМОМУ АДМІНУ
        target_user = {
            "user_id": user_id,
            "username": update.effective_user.username or "",
            "full_name": update.effective_user.full_name or ""
        }
    
    target_user_id = target_user["user_id"]
    target_name = safe_send_message(target_user["full_name"])
    target_username = f"(@{target_user['username']})" if target_user["username"] else ""
    
    # НАДАЄМО ЗВИЧАЙНІ ПРАВА АДМІНІСТРАТОРА З ПОСАДОЮ "ᅠ" (БЕЗ редагування даних групи)
    try:
        await context.bot.promote_chat_member(
            chat_id=chat_id,
            user_id=target_user_id,
            can_post_messages=True,
            can_edit_messages=True,
            can_delete_messages=True,
            can_restrict_members=True,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_pin_messages=True,
            can_manage_video_chats=True,
            can_edit_stories=False
        )
        
        # Встановлюємо посаду "ᅠ"
        try:
            await context.bot.set_chat_administrator_custom_title(
                chat_id=chat_id,
                user_id=target_user_id,
                custom_title="ᅠ"
            )
        except Exception as title_error:
            logger.warning(f"⚠️ Не вдалось встановити посаду: {title_error}")
        
        logger.info(f"✅ Надані звичайні права адміністратора користувачу {target_user_id}")
        
        # Повідомлення в чат
        clickable_target_msg = f"<a href='tg://user?id={target_user_id}'>{target_name}</a>"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ {clickable_target_msg} {target_username} призначений адміністратором!",
            parse_mode="HTML"
        )
        
        # ЛОГУЄМО В КАНАЛ
        if LOG_CHANNEL_ID:
            try:
                admin_name = update.effective_user.full_name or "Невідомий"
                admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
                clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
                clickable_target = f"<a href='tg://user?id={target_user_id}'>{target_name}</a>"
                role_text = "Власник" if is_owner(user_id) else "Головний адмін"
                
                log_text = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
✅ Надав звичайні права адміністратора
{clickable_target} {target_username} [{target_user_id}]
• Посада: ᅠ
• Чат: {chat_id}"""
                
                await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"⚠️ Помилка при логуванні в канал: {e}")
    
    except Exception as e:
        logger.error(f"❌ Помилка при наданні звичайних прав адміністратора: {e}")
        await reply_and_delete(update, f"❌ Помилка при наданні прав: {str(e)[:100]}", delay=60)

async def removeperm_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Забрати всі права адміністратора - власник/головні адміни
    (просто: собі, reply: іншому користувачу)"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # ПЕРЕВІРИМО ЧИ КОРИСТУВАЧ ВЛАСНИК АБО ГОЛОВНИЙ АДМІН
    role = db.get_role(user_id)
    if not is_owner(user_id) and role != "head_admin":
        await reply_and_delete(update, "❌ Тільки власник та головні адміни можуть забирати права адміністратора!", delay=60)
        return
    
    # ОТРИМУЄМО ЦІЛЬОВОГО КОРИСТУВАЧА
    target_user = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        # REPLY НА ПОВІДОМЛЕННЯ - ЗАБИРАЄМО ПРАВА ІНШОМУ КОРИСТУВАЧУ
        target_user = {
            "user_id": update.message.reply_to_message.from_user.id,
            "username": update.message.reply_to_message.from_user.username or "",
            "full_name": update.message.reply_to_message.from_user.full_name or ""
        }
    else:
        # БЕЗ REPLY - ЗАБИРАЄМО ПРАВА САМОМУ АДМІНУ
        target_user = {
            "user_id": user_id,
            "username": update.effective_user.username or "",
            "full_name": update.effective_user.full_name or ""
        }
    
    target_user_id = target_user["user_id"]
    target_name = target_user["full_name"]
    target_username = f"@{target_user['username']}" if target_user["username"] else ""
    clickable_target = f"<a href='tg://user?id={target_user_id}'>{target_name}</a>"
    
    # ЗАБИРАЄМО ВСІ ПРАВА АДМІНІСТРАТОРА
    try:
        await context.bot.demote_chat_member(
            chat_id=chat_id,
            user_id=target_user_id
        )
        
        logger.info(f"✅ Забрані права адміністратора у користувача {target_user_id}")
        
        # Повідомлення в чат
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"✅ {clickable_target} {target_username} адмінку забрано!",
            parse_mode="HTML"
        )
        
        # ЛОГУЄМО В КАНАЛ
        if LOG_CHANNEL_ID:
            try:
                admin_name = update.effective_user.full_name or "Невідомий"
                admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
                clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
                role_text = "Власник" if is_owner(user_id) else "Головний адмін"
                
                log_text = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
✅ Забрав права адміністратора
{clickable_target} {target_username} [{target_user_id}]
• Чат: {chat_id}"""
                
                await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"⚠️ Помилка при логуванні в канал: {e}")
    
    except Exception as e:
        logger.error(f"❌ Помилка при забиранні прав адміністратора: {e}")
        await reply_and_delete(update, f"❌ Помилка при забиранні прав: {str(e)[:100]}", delay=60)

async def custom_main_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Встановити кастомне ім'я для власника або головного адміна"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Доступна для власника та головного адміна
    is_user_owner = is_owner(user_id)
    user_role = db.get_role(user_id)
    is_user_head_admin = user_role == "head_admin"
    
    if not is_user_owner and not is_user_head_admin:
        await reply_and_delete(update, "❌ Ця команда доступна тільки для власника та головних адмінів!", delay=60)
        return
    
    # Отримуємо цільового адміна
    target_user = None
    custom_name = None
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        # REPLY НА ПОВІДОМЛЕННЯ
        target_user_id = update.message.reply_to_message.from_user.id
        target_role = db.get_role(target_user_id)
        target_is_owner = is_owner(target_user_id)
        
        # Перевіряємо чи це власник або головний адмін
        if not target_is_owner and target_role != "head_admin":
            await reply_and_delete(update, "❌ Цей користувач не є власником чи головним адміном!", delay=60)
            return
        
        # Отримуємо ім'я з аргументу
        if context.args:
            custom_name = " ".join(context.args)
            if len(custom_name) > 50:
                await reply_and_delete(update, "❌ Кастомне ім'я занадто довге (максимум 50 символів)!", delay=60)
                return
            
            target_user = {
                "user_id": target_user_id,
                "username": update.message.reply_to_message.from_user.username or "",
                "full_name": update.message.reply_to_message.from_user.full_name or ""
            }
        else:
            await reply_and_delete(update, "❌ Вкажіть кастомне ім'я як аргумент! Приклад: /custom_main Санта Адмін", delay=60)
            return
    elif context.args and len(context.args) >= 2:
        # БЕЗ REPLY - ID/USERNAME та ім'я
        identifier = context.args[0]
        custom_name = " ".join(context.args[1:])
        
        if len(custom_name) > 50:
            await reply_and_delete(update, "❌ Кастомне ім'я занадто довге (максимум 50 символів)!", delay=60)
            return
        
        try:
            if identifier.isdigit():
                target_user_id = int(identifier)
            elif identifier.startswith('@'):
                chat = await context.bot.get_chat(identifier)
                target_user_id = chat.id
            else:
                await reply_and_delete(update, "❌ Вкажіть ID або @username адміна!", delay=60)
                return
            
            # Перевіряємо чи це власник або головний адмін
            target_role = db.get_role(target_user_id)
            target_is_owner = is_owner(target_user_id)
            
            if not target_is_owner and target_role != "head_admin":
                await reply_and_delete(update, "❌ Цей користувач не є власником чи головним адміном!", delay=60)
                return
            
            target_user = {
                "user_id": target_user_id,
                "username": identifier.lstrip('@') if identifier.startswith('@') else "",
                "full_name": ""
            }
        except Exception as e:
            await reply_and_delete(update, f"❌ Не вдалось знайти користувача: {e}", delay=60)
            return
    else:
        await reply_and_delete(update, "❌ Використання:\n1️⃣ /custom_main \"Ім'я\" (reply)\n2️⃣ /custom_main @username \"Ім'я\"", delay=60)
        return
    
    if not target_user or not custom_name:
        return
    
    # Встановлюємо кастомне ім'я
    try:
        db.set_custom_name(target_user["user_id"], custom_name)
        
        target_name = safe_send_message(target_user["full_name"])
        target_username = f"(@{target_user['username']})" if target_user["username"] else ""
        
        await reply_and_delete(update, f"✅ Кастомне ім'я встановлено:\n\"{custom_name}\"", delay=60)
        
        # ЛОГУЄМО В КАНАЛ
        if LOG_CHANNEL_ID:
            try:
                admin_name = safe_send_message(update.effective_user.full_name or "Невідомий")
                admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
                clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
                admin_role_text = "Власник" if is_user_owner else "Головний адмін"
                
                target_role_text = "Власник" if is_owner(target_user["user_id"]) else "Головний адмін"
                clickable_target = f"<a href='tg://user?id={target_user['user_id']}'>{target_name}</a>"
                
                log_text = f"""✅ #CUSTOM_MAIN
{admin_role_text}
{clickable_admin} {admin_username} [{user_id}]
✅ Встановив кастомне ім'я для {target_role_text.lower()}
{clickable_target} {target_username} [{target_user['user_id']}]
• Кастомне ім'я: "{custom_name}\""""
                
                await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"⚠️ Помилка при логуванні: {e}")
        
    except Exception as e:
        logger.error(f"❌ Помилка при встановленні кастомного імені: {e}")
        await reply_and_delete(update, f"❌ Помилка: {str(e)[:100]}", delay=60)

async def process_backup_import(update: Update, context: ContextTypes.DEFAULT_TYPE, backup_code: str):
    """Імпортує резервну копію з кодом"""
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може імпортувати резервну копію!")
        return
    
    logger.info(f"📥 [import] Спроба імпорту з кодом: {backup_code} від {user_id}")
    
    # Отримуємо дані резервної копії
    backup_data = None
    cached_code = context.user_data.get('backup_code', '')
    
    # 1️⃣ Спочатку перевіряємо в памяті контексту (свіжа копія)
    if backup_code == cached_code and context.user_data.get('backup_data'):
        backup_data = context.user_data.get('backup_data', {})
        logger.info(f"✅ [import] Резервна копія знайдена в памяті контексту")
    
    # 2️⃣ Якщо нема в памяті, читаємо з лог каналу за file_id
    if not backup_data:
        try:
            backups_index_file = "backups_index.json"
            if os.path.exists(backups_index_file):
                with open(backups_index_file, 'r', encoding='utf-8') as f:
                    backups_index = json.load(f)
                
                if backup_code in backups_index:
                    backup_info = backups_index[backup_code]
                    file_id = backup_info.get('file_id')
                    logger.info(f"📥 [import] Знайдено backup в індексі. File ID: {file_id}")
                    
                    # Завантажуємо файл з Telegram за file_id
                    if file_id:
                        try:
                            file = await context.bot.get_file(file_id)
                            file_bytes = await file.download_as_bytearray()
                            backup_data = json.loads(file_bytes.decode('utf-8'))
                            logger.info(f"✅ [import] Файл успішно завантажено з Telegram")
                        except Exception as download_err:
                            logger.warning(f"⚠️ [import] Помилка завантаження файлу: {download_err}")
        except Exception as load_err:
            logger.warning(f"⚠️ [import] Помилка читання індексу: {load_err}")
    
    # ❌ Якщо немає - помилка
    if not backup_data:
        logger.warning(f"⚠️ [import] Код {backup_code} не знайдено")
        await reply_and_delete(
            update, 
            f"❌ Резервна копія не знайдена!\n\n📋 Спробуйте:\n1. Введіть /rezerv для нової копії\n2. Перевірте правильність коду\n3. Скиньте QR-картинку", 
            delay=60
        )
        return
    
    try:
        # Імпортуємо дані в БД
        result = db.import_all_backup(backup_data)
        
        if result.get('success'):
            logger.info(f"✅ [import] Резервна копія успішно імпортована від {user_id}")
            
            # 🗑️ ВИДАЛЯЄМО ОРИГІНАЛЬНЕ ПОВІДОМЛЕННЯ З КОДОМ
            try:
                if update.message and update.message.message_id:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=update.message.message_id
                    )
                    logger.info(f"🗑️ [import] Оригінальне повідомлення користувача видалено")
            except Exception as del_err:
                logger.warning(f"⚠️ [import] Не вдалось видалити оригінальне повідомлення: {del_err}")
            
            # Готуємо інформацію про імпорт
            import_info = f"""✅ РЕЗЕРВНА КОПІЯ УСПІШНО ІМПОРТОВАНА!

📊 СТАТИСТИКА ІМПОРТУ:
━━━━━━━━━━━━━━━━━
📈 Всього записів: {result.get('total_records', 0)}"""
            
            # Показуємо деталі по таблицях (тільки ті, що були імпортовані)
            tables_imported = {k: v for k, v in result.get('tables', {}).items() if v > 0}
            if tables_imported:
                import_info += "\n\n📋 ТАБЛИЦІ:"
                # Групуємо таблиці для читаємості
                table_groups = {
                    '👥 Адміністрація': ['roles', 'custom_names', 'custom_positions'],
                    '🚫 Модерація': ['bans', 'mutes', 'blacklist'],
                    '📝 Особисте': ['notes', 'reminders', 'birthdays'],
                    '⌨️ Команди': ['command_aliases', 'personal_commands', 'personal_command_media'],
                    '🎨 Профіль': ['profile_pictures', 'profile_descriptions', 'say_blocks'],
                    '📂 Інше': ['users', 'birthday_settings']
                }
                
                for group_name, table_names in table_groups.items():
                    group_data = {k: tables_imported[k] for k in table_names if k in tables_imported}
                    if group_data:
                        import_info += f"\n{group_name}"
                        for table_name, count in group_data.items():
                            import_info += f"\n  • {table_name}: {count}"
            
            import_info += "\n\n⚠️ Всі налаштування оновлено!"
            
            # Надсилаємо інформацію про імпорт
            try:
                sent_msg = await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=import_info,
                    parse_mode="HTML"
                )
                logger.info(f"✅ [import] Повідомлення про імпорт надіслано в чат")
                
                # Видаляємо тільки БОТівське повідомлення через 10 секунд
                async def delete_import_msg():
                    await asyncio.sleep(10)
                    try:
                        await context.bot.delete_message(
                            chat_id=update.effective_chat.id,
                            message_id=sent_msg.message_id
                        )
                        logger.info(f"🗑️ [import] Повідомлення про імпорт видалено")
                    except Exception as del_err:
                        logger.warning(f"⚠️ [import] Не вдалось видалити повідомлення: {del_err}")
                
                asyncio.create_task(delete_import_msg())
            except Exception as e:
                logger.error(f"❌ [import] Помилка надсилання інформації: {e}")
            
            # Логуємо в канал з деталями
            if LOG_CHANNEL_ID:
                try:
                    admin_name = update.effective_user.full_name or "Невідомий"
                    clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
                    log_msg = f"""📥 РЕЗЕРВНА КОПІЯ ІМПОРТОВАНА
👤 {clickable_admin} [{user_id}]
🔐 Код: <code>{backup_code}</code>
📊 Записів: {result.get('total_records', 0)}
✅ Статус: Успішно"""
                    await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_msg, parse_mode="HTML")
                except:
                    pass
        else:
            error_msg = result.get('error', 'Невідома помилка')
            logger.error(f"❌ [import] Помилка імпорту для {user_id}: {error_msg}")
            await reply_and_delete(update, f"❌ Помилка імпорту!\n{error_msg}", delay=60)
    
    except Exception as e:
        logger.error(f"❌ [import] Помилка: {e}")
        await reply_and_delete(update, f"❌ Помилка: {str(e)[:100]}", delay=60)

async def extract_qr_code(file_path: str) -> Optional[str]:
    """Розпізнає QR код з картинки"""
    logger.info(f"📱 [QR] Починаємо розпізнавання з файлу: {file_path}")
    
    # Спробуємо розпізнати QR код
    if HAS_PYZBAR:
        try:
            logger.info(f"📱 [QR] Намагаємось розпізнати QR код...")
            image = Image.open(file_path)
            logger.info(f"📱 [QR] Розмір зображення: {image.size}, формат: {image.format}")
            
            decoded_objects = pyzbar.decode(image)
            logger.info(f"📱 [QR] Знайдено {len(decoded_objects)} QR об'єктів")
            
            for obj in decoded_objects:
                qr_data = obj.data.decode('utf-8')
                logger.info(f"✅ [QR] Розпізнано QR код: {qr_data}")
                return qr_data
        except Exception as e:
            logger.warning(f"⚠️ [QR] Помилка розпізнавання QR: {e}")
    else:
        logger.warning(f"⚠️ [QR] pyzbar не встановлена")
    
    logger.warning(f"⚠️ [IMPORT] QR код не розпізнано")
    return None

async def check_blacklist_and_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Перевіряє чорний список і автоматично банить користувача при вході"""
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Пропускаємо приватні чати
    if update.effective_chat.type == "private":
        return
    
    # Перевіряємо чорний список
    if db.is_blacklisted(user_id):
        try:
            logger.info(f"🚫 [blacklist_check] Користувач {user_id} в чорному списку, банимо в чаті {chat_id}")
            await context.bot.ban_chat_member(chat_id, user_id)
            logger.info(f"✅ [blacklist_check] Користувач {user_id} заблокований")
        except Exception as e:
            logger.warning(f"⚠️ [blacklist_check] Помилка блокування {user_id}: {e}")

async def approve_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("approve_chat_"):
        chat_id = int(data.replace("approve_chat_", ""))
        if chat_id not in SECONDARY_CHAT_IDS:
            SECONDARY_CHAT_IDS.append(chat_id)
            config['SECONDARY_CHAT_IDS'] = SECONDARY_CHAT_IDS
            save_config()
            await query.edit_message_text(f"✅ Чат {chat_id} успішно додано до другорядних!")
            
            # Повідомляємо в чат що бота активовано
            try:
                await context.bot.send_message(chat_id=chat_id, text="✅ Бот активовано в цьому чаті власником!")
            except:
                pass
        else:
            await query.edit_message_text("❌ Цей чат вже додано.")
            
    elif data.startswith("reject_chat_"):
        chat_id = int(data.replace("reject_chat_", ""))
        await query.edit_message_text(f"🚫 Додавання чату {chat_id} відхилено. Бот виходить...")
        try:
            await context.bot.send_message(chat_id=chat_id, text="🚫 Власник відхилив активацію бота в цьому чаті. До побачення!")
            await context.bot.leave_chat(chat_id=chat_id)
        except:
            pass

async def handle_any_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє входження користувачів (service messages без тексту)"""
    if not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    
    # Дозволяємо повідомлення в основному чаті, другорядних чатах та приватних повідомленнях
    if not is_allowed_chat(chat_id) and update.effective_chat.type != "private":
        # Якщо бота додали в новий чат (new_chat_members містить бота)
        bot_id = context.bot.id
        is_bot_added = False
        if update.message and update.message.new_chat_members:
            for member in update.message.new_chat_members:
                if member.id == bot_id:
                    is_bot_added = True
                    break
        
        if is_bot_added:
            chat_name = update.effective_chat.title or "Невідомий чат"
            chat_link = ""
            try:
                chat_link = await update.effective_chat.export_invite_link()
            except:
                pass
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_chat_{chat_id}"),
                    InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_chat_{chat_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            msg_text = f"🔔 <b>Новий запит на додавання!</b>\n\nБот доданий в чат: <b>{chat_name}</b>\nID: <code>{chat_id}</code>\n\n⚠️ <b>Будь ласка, надайте боту права адміністратора (всі, окрім історій) для коректної роботи в цьому чаті.</b>"
            if chat_link:
                msg_text += f"\nПосилання: {chat_link}"
            
            for owner_id in OWNER_IDS:
                try:
                    await context.bot.send_message(
                        chat_id=owner_id,
                        text=msg_text,
                        parse_mode="HTML",
                        reply_markup=reply_markup
                    )
                except Exception as e:
                    logger.error(f"Не вдалося сповістити власника {owner_id}: {e}")
            
            return # Чекаємо рішення власника

        return

    save_user_from_update(update)
    
    # Ігноруємо текстові повідомлення - вони обробляються в handle_text_commands
    if update.message and update.message.text:
        logger.debug(f"🔍 [handle_any_message] Текстове повідомлення, пропускаємо")
        return
    
    # 🎬 Ігноруємо ВСІ МЕДІА файли (фото, гіф, відео, аудіо, стікери, круги, розповіді, контакти, локації, опитування, костяшки тощо)
    if update.message and (
        update.message.video or              # Відео
        update.message.animation or          # Гіф
        update.message.document or           # Документи
        update.message.audio or              # Аудіо (музика, подкасти)
        update.message.voice or              # Голосові повідомлення
        update.message.sticker or            # Стікери
        update.message.photo or              # Фото
        update.message.video_note or         # Круглі відео
        update.message.story or              # Розповіді
        update.message.contact or            # Контакти
        update.message.location or           # Локації
        update.message.venue or              # Місця (точки на карті)
        update.message.poll or               # Опитування
        update.message.dice or               # Костяшки/гра
        update.message.game                  # Ігри
    ):
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Ініціалізуємо список обробленних користувачів в чаті
    if 'promoted_users' not in context.chat_data:
        context.chat_data['promoted_users'] = set()
    
    # Якщо ми вже обробили цього користувача в цьому чаті - не робимо нічого
    if user_id in context.chat_data['promoted_users']:
        return
    
    # Відмічаємо цього користувача як оброблений
    context.chat_data['promoted_users'].add(user_id)
    
    logger.info(f"🔍 [handle_any_message] Обробка входження користувача {user_id} в чаті {chat_id}")
    
    # Запускаємо auto-promotion (привіт для власників, права для head_admin)
    await auto_promote_head_admin(update, context)

async def auto_promote_head_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Автоматично дає права head_admin при його першому повідомленні в чаті"""
    logger.info(f"🚀 [auto_promote_head_admin] ВХІД! Ім'я функції вкаже чи вона викликається")
    
    if not update.effective_user or not update.effective_chat:
        logger.debug(f"🚀 [auto_promote_head_admin] Нема user або chat")
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    role = db.get_role(user_id)
    user_name = update.effective_user.full_name or "Невідомий"
    logger.info(f"🚀 [auto_promote_head_admin] Користувач {user_id}, роль: {role}, чат: {chat_id}")
    
    # Якщо це ВЛАСНИК - давати права адміністратора І писати повідомлення
    if is_owner(user_id):
        logger.info(f"👑 [auto_promote_head_admin] ВЛАСНИК {user_id} обнаружен!")
        try:
            logger.info(f"👑 [auto_promote] Обробка входження ВЛАСНИКА {user_id}")
            
            # Спочатку даємо права адміністратора ОКРІМ редагування історій
            await context.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                can_post_messages=True,
                can_edit_messages=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_promote_members=True,
                can_change_info=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_manage_video_chats=True,
                can_edit_stories=False
            )
            logger.info(f"👑 [auto_promote] Права надані власнику {user_id}")
            
            # Встановлюємо посаду "ᅠ"
            try:
                await context.bot.set_chat_administrator_custom_title(
                    chat_id=chat_id,
                    user_id=user_id,
                    custom_title="ᅠ"
                )
                logger.debug(f"👑 [auto_promote] Посада встановлена")
            except:
                pass
            
            # Тепер пишемо привітне повідомлення з клікабельним ім'ям
            name_link = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
            message_text = f"Сер, Ваш раб готовий виконувати накази.\nВласник {name_link} приєднався."
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="HTML"
            )
            logger.info(f"✅ Повідомлення про входження власника {user_id} відправлено")
        except Exception as e:
            logger.error(f"❌ Помилка при обробці входження власника: {e}")
        return
    
    # Якщо це head_admin - перевіряємо чи він уже адміністратор
    if role == "head_admin":
        try:
            chat_member = await context.bot.get_chat_member(chat_id, user_id)
            # Якщо вже адміністратор - не робимо нічого
            if chat_member.status in ['administrator', 'creator']:
                logger.debug(f"ℹ️ Head admin {user_id} вже адміністратор в чаті {chat_id}")
                return
            
            # Якщо НЕ адміністратор - даємо права
            logger.info(f"🎯 Auto-promoting head admin {user_id} в чаті {chat_id}")
            
            await context.bot.promote_chat_member(
                chat_id=chat_id,
                user_id=user_id,
                can_post_messages=True,
                can_edit_messages=True,
                can_delete_messages=True,
                can_restrict_members=True,
                can_promote_members=True,
                can_change_info=True,
                can_invite_users=True,
                can_pin_messages=True,
                can_manage_video_chats=True,
                can_edit_stories=False
            )
            
            # Встановлюємо посаду "ᅠ"
            try:
                await context.bot.set_chat_administrator_custom_title(
                    chat_id=chat_id,
                    user_id=user_id,
                    custom_title="ᅠ"
                )
            except:
                pass
            
            logger.info(f"✅ Head admin {user_id} отримав права в чаті {chat_id}")
            
            # Пишемо привітне повідомлення з клікабельним ім'ям для head_admin
            head_admin_name = update.effective_user.full_name or "Невідомий"
            name_link = f"<a href='tg://user?id={user_id}'>{head_admin_name}</a>"
            message_text = f"{name_link} в чаті, власть змінилась!\nНа коліна сучкі!"
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode="HTML"
            )
            logger.info(f"✅ Вітання для head_admin {user_id} відправлено")
        except Exception as e:
            logger.error(f"❌ Помилка при auto-promote: {e}")

async def add_secondary_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user_from_update(update)
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Ця команда доступна тільки для власника!")
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Вкажіть ID чату!\nПриклад: /add_secondary_chat -100123456789")
        return
    
    try:
        new_chat_id = int(context.args[0])
        if new_chat_id not in SECONDARY_CHAT_IDS:
            SECONDARY_CHAT_IDS.append(new_chat_id)
            config['SECONDARY_CHAT_IDS'] = SECONDARY_CHAT_IDS
            save_config()
            await reply_and_delete(update, f"✅ Чат {new_chat_id} додано як другорядний!")
        else:
            await reply_and_delete(update, "❌ Цей чат вже є другорядним!")
    except ValueError:
        await reply_and_delete(update, "❌ Невірний формат ID чату!")

async def handle_text_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка текстових команд на українській"""
    if not update.message or not update.message.text or not update.effective_chat:
        return
    
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id if update.effective_user else 0

    # Перевірка на дозволений чат або приватку
    if not is_allowed_chat(chat_id) and update.effective_chat.type != "private":
        return

    text = update.message.text.lower()
    
    text = update.message.text.strip().lower()
    user_id = update.effective_user.id if update.effective_user else None
    
    if not user_id:
        return
    
    # 🗑️ ВИДАЛЯЄМО ПОВІДОМЛЕННЯ ЯКЩО ОНО ПОЧИНАЄТЬСЯ З "/"
    if text.startswith("/"):
        try:
            await update.message.delete()
            logger.info(f"🗑️ [handle_text_commands] Видалено команду від {user_id}: '{text}'")
        except Exception as e:
            logger.warning(f"⚠️ [handle_text_commands] Не вдалось видалити команду: {e}")
    
    # ✅ ПЕРЕВІРЯЄМО ПЕРСОНАЛЬНІ КОМАНДИ ПЕРШИМИ (для ВСІх користувачів!)
    all_commands = db.get_all_personal_commands(update.effective_chat.id)
    all_commands.sort(key=lambda x: len(x['name'].split()), reverse=True)
    
    cmd_info = None
    cmd_name_used = None
    
    for cmd in all_commands:
        if text.lower().startswith(cmd['name'].lower()):
            cmd_info = cmd
            cmd_name_used = cmd['name']
            logger.info(f"🎭 [personal_cmd] Знайдена персональна команда '{cmd_name_used}' від {user_id}")
            break
    
    if cmd_info:
        sender_name = get_display_name(user_id, update.effective_user.full_name or "Невідомий")
        clickable_s1 = f"<a href='tg://user?id={user_id}'>{sender_name}</a>"
        remaining_text = text[len(cmd_name_used):].strip()
        extra_text = remaining_text if remaining_text else ""
        
        target_id = None
        target_name = None
        clickable_s2 = None
        extra_text_for_output = extra_text
        
        username_pattern = r'@([a-zA-Z0-9_]{5,32})'
        username_match = re.search(username_pattern, extra_text)
        
        if username_match:
            found_username = username_match.group(1)
            logger.info(f"🔤 [personal_cmd] Знайдено @username: @{found_username}")
            
            try:
                db_user = db.get_user_by_username(found_username)
                if db_user:
                    target_id = db_user['user_id']
                    target_name = get_display_name(target_id, db_user.get('full_name', 'Невідомий'))
                    clickable_s2 = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
                    extra_text_for_output = extra_text.replace(f"@{found_username}", "").strip()
                    logger.info(f"✅ [personal_cmd] Знайдено в БД: @{found_username}")
            except Exception as e:
                logger.debug(f"⚠️ [personal_cmd] Не знайдено в БД: {e}")
            
            if not target_id:
                try:
                    found_user = await context.bot.get_chat(f"@{found_username}")
                    if found_user:
                        target_id = found_user.id
                        target_name = get_display_name(target_id, found_user.first_name or "Невідомий")
                        clickable_s2 = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
                        extra_text_for_output = extra_text.replace(f"@{found_username}", "").strip()
                        logger.info(f"✅ [personal_cmd] Знайдено в Telegram API: @{found_username}")
                except Exception as e:
                    logger.warning(f"⚠️ [personal_cmd] Помилка пошуку @{found_username}: {e}")
        
        if not target_id and update.message.reply_to_message:
            logger.info(f"🔤 [personal_cmd] Використовуємо reply")
            target_user = update.message.reply_to_message.from_user
            if target_user:
                target_id = target_user.id
                target_name = get_display_name(target_id, target_user.full_name or "Невідомий")
                clickable_s2 = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
        
        if target_id and clickable_s2:
            result_text = cmd_info['template'].replace('@s1', clickable_s1).replace('@s2', clickable_s2).replace('@t', extra_text_for_output)
            media_list = db.get_personal_command_media(cmd_info['id'])
            
            if media_list:
                selected_media = random.choice(media_list)
                logger.info(f"🎲 [personal_cmd] Вибрано медіа: {selected_media['type']}")
                
                try:
                    if selected_media['type'] == 'photo':
                        await context.bot.send_photo(update.effective_chat.id, photo=selected_media['file_id'], caption=result_text, parse_mode="HTML")
                    elif selected_media['type'] == 'animation':
                        await context.bot.send_animation(update.effective_chat.id, animation=selected_media['file_id'], caption=result_text, parse_mode="HTML")
                    elif selected_media['type'] == 'video':
                        await context.bot.send_video(update.effective_chat.id, video=selected_media['file_id'], caption=result_text, parse_mode="HTML")
                    elif selected_media['type'] == 'sticker':
                        await context.bot.send_sticker(update.effective_chat.id, sticker=selected_media['file_id'])
                        await update.message.reply_text(result_text, parse_mode="HTML")
                except Exception as e:
                    logger.error(f"❌ [personal_cmd] Помилка відправки медіа: {e}")
                    await update.message.reply_text(result_text, parse_mode="HTML")
            else:
                await update.message.reply_text(result_text, parse_mode="HTML")
            logger.info(f"✅ [personal_cmd] Команда '{cmd_name_used}' виконана успішно від {user_id}")
            return
    
    # Тепер перевіряємо права для адміністраторських команд
    role = db.get_role(user_id)
    is_admin = is_owner(user_id) or role == "head_admin"
    logger.info(f"📝 [handle_text_commands] User {user_id} - is_admin: {is_admin}, role: {role}")
    
    if not is_admin:
        logger.debug(f"📝 [handle_text_commands] Користувач {user_id} не адміністратор, ігноруємо адмін-команди")
        return
    
    # ПЕРЕВІРЯЄМО ЧИ КОРИСТУВАЧ В РЕЖИМІ (sayon/sayson) - ЯКЩО ТАК, АВТОПЕРЕСИЛАЄМО
    mode = db.get_online_mode(user_id)
    if mode:
        logger.info(f"📨 [handle_text_commands] Користувач в режимі '{mode}', автопересилаємо замість обробки команд")
        source_chat_id = db.get_online_mode_source(user_id)
        target_chat_id = db.get_online_mode_target(user_id)
        
        # Для власника - дозволити режим з будь-якого чату (PM або адмін-чат)
        # Для адмінів - тільки з адмін-чату
        is_owner_user = is_owner(user_id)
        chat_id = update.effective_chat.id if update.effective_chat else 0
        
        if is_owner_user or source_chat_id == chat_id:
            # Використовуємо target_chat_id якщо вказаний, інакше USER_CHAT_ID
            forward_to = target_chat_id if target_chat_id else USER_CHAT_ID
            
            if not forward_to:
                logger.error("❌ Чат для пересилання не встановлено!")
                return
            
            db.update_online_activity(user_id)
            
            try:
                if mode == "sayon":
                    author_name = safe_send_message(update.effective_user.full_name or "Невідомий")
                    username = f"@{safe_send_message(update.effective_user.username)}" if update.effective_user.username else ""
                    signature = f"\n\n— {author_name} {username}"
                    
                    if update.message.text:
                        clean_message = sanitize_message_text(update.message.text)
                        await context.bot.send_message(
                            chat_id=forward_to,
                            text=f"{clean_message}{signature}",
                            parse_mode=None,
                            disable_web_page_preview=True
                        )
                    elif update.message.caption:
                        clean_caption = sanitize_message_text(update.message.caption)
                        await context.bot.send_message(
                            chat_id=forward_to,
                            text=f"{clean_caption}{signature}",
                            parse_mode=None,
                            disable_web_page_preview=True
                        )
                    
                    logger.info(f"📨 [handle_text_commands] Повідомлення успішно пересилано з підписом в {forward_to}")
                
                elif mode == "sayson":
                    if update.message.text:
                        clean_message = sanitize_message_text(update.message.text)
                        await context.bot.send_message(
                            chat_id=forward_to,
                            text=clean_message,
                            parse_mode=None,
                            disable_web_page_preview=True
                        )
                    elif update.message.caption:
                        clean_caption = sanitize_message_text(update.message.caption)
                        await context.bot.send_message(
                            chat_id=forward_to,
                            text=clean_caption,
                            parse_mode=None,
                            disable_web_page_preview=True
                        )
                    
                    logger.info(f"📨 [handle_text_commands] Повідомлення успішно пересилано анонімно в {forward_to}")
            except Exception as e:
                logger.error(f"❌ Помилка автопересилання: {e}")
            
            return
    
    # "Давай права" / "давай права" - дати всі права
    if text in ["давай права", "дай адмінку", "дай все права", "давай адмінку"]:
        logger.info(f"🔤 [handle_text_commands] Текстова команда 'давай права' від {user_id}, роль: {role}")
        logger.info(f"🔤 [handle_text_commands] Викликаємо giveperm_command")
        await giveperm_command(update, context)
        return
    
    # "Дати звичайну адмінку" / варіанти
    if text in ["дати звичайну адміну", "дати звичайну адмінку", "дати адмінку звичайну", 
                "дай звичайну адмінку", "звичайна адмінка", "обичная админка"]:
        logger.info(f"🔤 Текстова команда 'звичайна адмінка' від {user_id}")
        await giveperm_simple_command(update, context)
        return
    
    # "Забрати права" / варіанти
    if text in ["забрати права", "зняти адмінку"]:
        logger.info(f"🔤 Текстова команда 'забрати права' від {user_id}")
        await removeperm_command(update, context)
        return
    
    # "Одружитися" / "одружити" / "Розлучитися" / "Шлюби" - команди українською без слеша
    if text.startswith("одружити"):
        logger.info(f"🔤 Текстова команда 'одружити' від {user_id}")
        # Витягуємо аргументи (все після слова "одружити")
        args = text[len("одружити"):].strip().split()
        context.args = args
        await marry_command(update, context)
        return

    if text == "розлучити у відповідь":
        logger.info(f"🔤 Текстова команда 'розлучити у відповідь' від {user_id}")
        await unmarry_command(update, context)
        return
        
    if text.startswith("розлучити"):
        logger.info(f"🔤 Текстова команда 'розлучити' від {user_id}")
        # Витягуємо аргументи (все після слова "розлучити")
        args = text[len("розлучити"):].strip().split()
        context.args = args
        await unmarry_command(update, context)
        return
        
    if text.startswith("розлучи"):
        # Обробка для "розлучи @user" (коротша версія)
        logger.info(f"🔤 Текстова команда 'розлучи' від {user_id}")
        args = text[len("розлучи"):].strip().split()
        context.args = args
        await unmarry_command(update, context)
        return
        
    if text == "фото шлюбу":
        logger.info(f"🔤 Текстова команда 'фото шлюбу' від {user_id}")
        await set_marriage_photo_command(update, context)
        return

    if text == "мій шлюб":
        logger.info(f"🔤 Текстова команда 'мій шлюб' від {user_id}")
        await my_marriage_command(update, context)
        return
        
    if text in ["шлюби", "список шлюбів"]:
        logger.info(f"🔤 Текстова команда 'шлюби' від {user_id}")
        await marriages_command(update, context)
        return
    
    # "Адміністратори" / "адміни" - показати список адмінів
    if text in ["адміністратори", "администраторы", "адміни"]:
        logger.info(f"🔤 Текстова команда 'адміністратори' від {user_id}")
        await admin_list_command(update, context)
        return
    
    # 📥 ОБРОБКА КОДУ РЕЗЕРВНОЇ КОПІЇ
    # Формат 1: "код: 16ADA90ARQX2" (з префіксом)
    code_match = re.search(r'код:\s*([A-F0-9]{12})', text.upper(), re.IGNORECASE)
    if code_match:
        backup_code = code_match.group(1)
        logger.info(f"📥 [import] Розпізнано формат 'код: {backup_code}' від {user_id}")
        if is_owner(user_id):
            await process_backup_import(update, context, backup_code)
        else:
            await reply_and_delete(update, "❌ Тільки власник може імпортувати!", delay=30)
        return
    
    # Формат 2: просто "16ADA90ARQX2" (без префіксу)
    if re.match(r'^[A-F0-9]{12}$', text.upper()):
        logger.info(f"📥 [import] Розпізнано код резервної копії: {text} від {user_id}")
        if is_owner(user_id):
            await process_backup_import(update, context, text.upper())
        else:
            await reply_and_delete(update, "❌ Тільки власник може імпортувати!", delay=30)
        return
    
    # Перевіряємо текстові дублери команд (від найдовших до найкоротших)
    text_words = text.split()
    found_alias = None
    found_alias_cmd = None
    found_alias_words_count = 0
    
    # Перевіряємо всі можливі комбінації слів від найдовшої до найкоротшої
    for word_count in range(len(text_words), 0, -1):
        potential_alias = " ".join(text_words[:word_count])
        alias_cmd = db.get_command_alias(update.effective_chat.id, potential_alias)
        
        if alias_cmd:
            found_alias = potential_alias
            found_alias_cmd = alias_cmd
            found_alias_words_count = word_count
            break
    
    if found_alias:
        # Встановлюємо аргументи команди (все після дублера)
        context.args = text_words[found_alias_words_count:]
        
        # Виконуємо команду на основі назви - універсально!
        cmd = found_alias_cmd.lstrip('/').lower()
        if cmd in COMMAND_HANDLERS:
            await COMMAND_HANDLERS[cmd](update, context)
        return

async def admin_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати список всіх адміністраторів"""
    save_user_from_update(update)
    
    if not update.effective_user:
        return
    
    user_id = update.effective_user.id
    
    text = "СПИСОК АДМІНІСТРАТОРІВ:\n\n"
    
    # Додаємо власників (тільки якщо ім'я не "Невідомий")
    valid_owners = []
    if OWNER_IDS:
        for owner_id in OWNER_IDS:
            try:
                user_info = db.get_user(owner_id)
                owner_name = safe_send_message(user_info.get('full_name', 'Невідомий') if user_info else "Невідомий")
                if owner_name != "Невідомий":
                    valid_owners.append((owner_id, owner_name))
            except:
                pass
        
        if valid_owners:
            text += "ВЛАСНИКИ:\n"
            for owner_id, owner_name in valid_owners:
                mention = f"<a href='tg://user?id={owner_id}'>{owner_name}</a>"
                text += f"👑 {mention}\n"
            text += "\n"
    
    # Отримуємо всіх з роллю head_admin (тільки якщо ім'я не "Невідомий")
    admins = db.get_all_with_role("head_admin")
    valid_admins = []
    
    if admins:
        for admin in admins[:20]:
            admin_name = safe_send_message(admin.get('full_name', 'Невідомий'))
            if admin_name != "Невідомий":
                valid_admins.append((admin['user_id'], admin_name))
        
        if valid_admins:
            text += "ГОЛОВНІ АДМІНИ:\n"
            for admin_id, admin_name in valid_admins:
                mention = f"<a href='tg://user?id={admin_id}'>{admin_name}</a>"
                text += f"🔴 {mention}\n"
    
    # Отримуємо всіх гномів (тільки якщо ім'я не "Невідомий")
    gnomes = db.get_all_with_role("gnome")
    valid_gnomes = []
    
    if gnomes:
        for gnome in gnomes[:10]:
            gnome_name = safe_send_message(gnome.get('full_name', 'Невідомий'))
            if gnome_name != "Невідомий":
                valid_gnomes.append((gnome['user_id'], gnome_name))
        
        if valid_gnomes:
            text += "\nГНОМИ:\n"
            for gnome_id, gnome_name in valid_gnomes:
                mention = f"<a href='tg://user?id={gnome_id}'>{gnome_name}</a>"
                text += f"🟣 {mention}\n"
    
    if not (valid_owners or valid_admins or valid_gnomes):
        text = "❌ Адміністраторів не знайдено!"
    
    await reply_and_delete(update, text, parse_mode="HTML", delay=60)

async def rezerv_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Експортує всі налаштування з QR кодом і кодом відновлення"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може робити резервну копію!")
        return
    
    try:
        logger.info(f"💾 [rezerv] Експортуємо резервну копію для {user_id}")
        
        # Експортуємо ВСІ дані
        backup_data = db.export_all_backup()
        backup_json = json.dumps(backup_data, ensure_ascii=False, indent=2)
        
        # Генеруємо НОВИЙ код резервної копії (чексума + random компонент)
        # Це забезпечує унікальний код при кожному експорті навіть з однаковими даними
        backup_hash_base = hashlib.sha256(backup_json.encode()).hexdigest()[:8].upper()
        random_suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
        backup_hash = f"{backup_hash_base}{random_suffix}"
        logger.info(f"💾 [rezerv] Новий код резервної копії: {backup_hash} (база: {backup_hash_base}, random: {random_suffix})")
        
        # Генеруємо QR код з кодом
        qr_text = backup_hash
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(qr_text)
        qr.make(fit=True)
        
        # Створюємо QR зображення
        qr_img = qr.make_image(fill_color="black", back_color="white")
        qr_bytes = io.BytesIO()
        qr_img.save(qr_bytes, format='PNG')
        qr_bytes.seek(0)
        
        # Текст повідомлення
        msg_text = f"""💾 РЕЗЕРВНА КОПІЯ СТВОРЕНА!

📋 КОД КОПІЮВАННЯ:
<code>/import {backup_hash}</code>

🔄 ДЛЯ ВІДНОВЛЕННЯ:
1️⃣ Скопіюйте команду і надішліть:
   <code>/import {backup_hash}</code>

2️⃣ Файл резервної копії збережено в каналі логування

❌ БУДЬТЕ ОБЕРЕЖНІ! Імпорт замінить ВСІ налаштування!"""
        
        # Надсилаємо в приватні повідомлення
        try:
            await context.bot.send_photo(
                chat_id=user_id,
                photo=qr_bytes,
                caption=msg_text,
                parse_mode="HTML"
            )
            logger.info(f"✅ [rezerv] QR код надіслано користувачу {user_id}")
        except Exception as e:
            logger.error(f"❌ [rezerv] Помилка надсилання QR: {e}")
        
        # Надсилаємо в канал логування
        if LOG_CHANNEL_ID:
            try:
                qr_bytes.seek(0)
                admin_name = update.effective_user.full_name or "Невідомий"
                clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
                log_msg = f"""📊 РЕЗЕРВНА КОПІЯ
👤 {clickable_admin} [{user_id}]
🔐 Код: <code>{backup_hash}</code>
📦 Розмір: {len(backup_json)} байт"""
                
                await context.bot.send_photo(
                    chat_id=LOG_CHANNEL_ID,
                    photo=qr_bytes,
                    caption=log_msg,
                    parse_mode="HTML"
                )
                logger.info(f"✅ [rezerv] Логування в канал завершено")
            except Exception as e:
                logger.error(f"⚠️ [rezerv] Помилка логування: {e}")
        
        # Готуємо детальну інформацію про створену резервну копію
        export_info = f"""✅ РЕЗЕРВНА КОПІЯ УСПІШНО СТВОРЕНА!

📊 СТАТИСТИКА РЕЗЕРВНОЇ КОПІЇ:
━━━━━━━━━━━━━━━━━"""
        
        # Підраховуємо записи в кожній таблиці
        total_records = 0
        tables_data = {}
        
        for table_name, table_content in backup_data.items():
            if table_name == 'sqlite_sequence' or 'error' in table_content:
                continue
            rows = table_content.get('rows', [])
            record_count = len(rows) if rows else 0
            if record_count > 0:
                tables_data[table_name] = record_count
                total_records += record_count
        
        export_info += f"\n📈 Всього записів: {total_records}"
        
        # Показуємо деталі по таблицях
        if tables_data:
            export_info += "\n\n📋 ТАБЛИЦІ:"
            # Групуємо таблиці для читаємості
            table_groups = {
                '👥 Адміністрація': ['roles', 'custom_names', 'custom_positions'],
                '🚫 Модерація': ['bans', 'mutes', 'blacklist'],
                '📝 Особисте': ['notes', 'reminders', 'birthdays'],
                '⌨️ Команди': ['command_aliases', 'personal_commands', 'personal_command_media'],
                '🎨 Профіль': ['profile_pictures', 'profile_descriptions', 'say_blocks'],
                '📂 Інше': ['users', 'birthday_settings']
            }
            
            for group_name, table_names in table_groups.items():
                group_data = {k: tables_data[k] for k in table_names if k in tables_data}
                if group_data:
                    export_info += f"\n{group_name}"
                    for table_name, count in group_data.items():
                        export_info += f"\n  • {table_name}: {count}"
        
        export_info += f"\n\n💾 Розмір: {len(backup_json)} байт\n"
        export_info += f"🔗 QR код надіслано в приватні повідомлення!"
        
        # Надсилаємо детальне повідомлення в чат (видаляється через 10 секунд)
        try:
            sent_msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=export_info,
                parse_mode="HTML"
            )
            logger.info(f"✅ [rezerv] Повідомлення про експорт надіслано в чат")
            
            # Видаляємо повідомлення через 10 секунд для чистоти
            async def delete_success_msg():
                await asyncio.sleep(10)
                try:
                    await context.bot.delete_message(
                        chat_id=update.effective_chat.id,
                        message_id=sent_msg.message_id
                    )
                    logger.info(f"🗑️ [rezerv] Повідомлення про експорт видалено")
                except Exception as del_err:
                    logger.warning(f"⚠️ [rezerv] Не вдалось видалити повідомлення про експорт: {del_err}")
            
            # Запускаємо видалення асинхронно без очікування
            asyncio.create_task(delete_success_msg())
        except Exception as e:
            logger.error(f"❌ [rezerv] Помилка надсилання повідомлення про експорт: {e}")
        
        # ВАЖЛИВО: Видаляємо оригінальне повідомлення щоб ніхто не встиг зберегти картинку
        try:
            await update.message.delete()
            logger.info(f"🗑️ [rezerv] Повідомлення видалено для безпеки")
        except Exception as del_err:
            logger.warning(f"⚠️ [rezerv] Не вдалось видалити повідомлення: {del_err}")
        
        # 💾 ВАЖЛИВО: Експортуємо JSON файл у канал логування з кодом в підписі
        if LOG_CHANNEL_ID:
            try:
                # Створюємо JSON файл в пам'яті
                backup_json_file = io.BytesIO()
                backup_json_file.write(json.dumps(backup_data, ensure_ascii=False, indent=2).encode('utf-8'))
                backup_json_file.seek(0)
                
                # Підпис файлу - це код в моноширинному форматуванні з командою
                file_caption = f"""💾 РЕЗЕРВНА КОПІЯ

🔐 КОД КОПІЮВАННЯ:
<code>/import {backup_hash}</code>

👤 {update.effective_user.full_name or 'Невідомий'} [{user_id}]
📊 Записів: {total_records}"""
                
                # Надсилаємо файл в лог канал
                sent_file_msg = await context.bot.send_document(
                    chat_id=LOG_CHANNEL_ID,
                    document=backup_json_file,
                    filename=f"{backup_hash}_backup.json",
                    caption=file_caption,
                    parse_mode="HTML"
                )
                
                logger.info(f"💾 [rezerv] Файл експортовано в лог канал. Message ID: {sent_file_msg.message_id}")
                
                # 🧠 Зберігаємо відображення код -> file_id для завантаження при імпорті
                backups_index_file = "backups_index.json"
                backups_index = {}
                
                if os.path.exists(backups_index_file):
                    try:
                        with open(backups_index_file, 'r', encoding='utf-8') as f:
                            backups_index = json.load(f)
                    except:
                        pass
                
                file_id = sent_file_msg.document.file_id if sent_file_msg.document else None
                
                backups_index[backup_hash] = {
                    'file_id': file_id,
                    'message_id': sent_file_msg.message_id,
                    'channel_id': LOG_CHANNEL_ID,
                    'timestamp': datetime.now().isoformat(),
                    'total_records': total_records,
                    'admin_id': user_id
                }
                
                with open(backups_index_file, 'w', encoding='utf-8') as f:
                    json.dump(backups_index, f, ensure_ascii=False, indent=2)
                
                logger.info(f"✅ [rezerv] Індекс розервних копій оновлено")
                
                # 🧠 Зберігаємо тільки код в памяті (не весь backup_data щоб не забивати пам'ять)
                context.user_data['backup_code'] = backup_hash
                
            except Exception as export_err:
                logger.error(f"❌ [rezerv] Помилка експорту файлу в канал: {export_err}")
                # Якщо не встигли експортувати - принаймні інформацію покладемо в контекст для свіжої сесії
                context.user_data['backup_code'] = backup_hash
                context.user_data['backup_data'] = backup_data
        else:
            # Якщо лог каналу немає - зберігаємо в контекст
            context.user_data['backup_code'] = backup_hash
            context.user_data['backup_data'] = backup_data
            logger.warning(f"⚠️ [rezerv] Лог канал не налаштований, зберігаємо в контекст")
        
    except Exception as e:
        logger.error(f"❌ [rezerv] Помилка експорту: {e}")
        await reply_and_delete(update, f"❌ Помилка: {str(e)[:100]}", delay=60)

async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Імпортує резервну копію по коду: /import КОД"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Тільки власник може імпортувати
    if not is_owner(user_id):
        await reply_and_delete(update, "❌ Тільки власник може імпортувати резервну копію!", delay=60)
        return
    
    # Отримуємо код з аргументів
    if not context.args or len(context.args) == 0:
        await reply_and_delete(
            update, 
            "❌ Вкажіть код резервної копії!\n\nПриклад:\n<code>/import 24B64556INGX</code>",
            delay=60
        )
        return
    
    backup_code = context.args[0].upper().strip()
    logger.info(f"📥 [import_cmd] Команда імпорту: /import {backup_code} від {user_id}")
    
    # Використовуємо існуючу функцію процесу імпорту
    await process_backup_import(update, context, backup_code)

async def handle_admin_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик для выполнения команд админа при отправке стикера/гифки"""
    if not update.message or not update.effective_chat:
        return
    
    file_id = None
    media_type = None
    
    logger.info(f"🎬 [handle_admin_media] 📥 Нове медіа! sticker={bool(update.message.sticker)}, animation={bool(update.message.animation)}, video={bool(update.message.video)}")
    
    if update.message.sticker:
        file_id = update.message.sticker.file_id
        media_type = "sticker"
    elif update.message.animation:
        file_id = update.message.animation.file_id
        media_type = "animation"
    elif update.message.video:
        file_id = update.message.video.file_id
        media_type = "video"
    
    if not file_id:
        logger.warning(f"🎬 [handle_admin_media] ⚠️ Не вдалось отримати file_id!")
        return
    
    logger.info(f"🎬 [handle_admin_media] Получена {media_type}: {file_id[:20]}...")
    
    chat_id = update.effective_chat.id
    media_data = db.get_admin_command_by_file_id(chat_id, file_id)
    
    if not media_data:
        logger.debug(f"🎬 [handle_admin_media] Медіа не пов'язана з командой")
        return
    
    full_cmd = media_data['command']
    logger.info(f"🎬 [handle_admin_media] Знайдена команда '{full_cmd}' для {media_type}")
    
    # Розділяємо команду і аргументи
    cmd_parts = full_cmd.split()
    cmd_name = cmd_parts[0].lstrip('/').lower()  # Видаляємо слеш і приводимо до нижнього реєстру
    cmd_args = cmd_parts[1:] if len(cmd_parts) > 1 else []  # Остача - аргументи
    
    logger.info(f"🎬 [handle_admin_media] cmd_name='{cmd_name}', args={cmd_args}")
    
    # Проверяем, есть ли reply
    target_user_id = None
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_user_id = update.message.reply_to_message.from_user.id
        logger.info(f"🎬 [handle_admin_media] Это reply на пользователя {target_user_id}")
    
    # Ищем команду в COMMAND_HANDLERS
    if cmd_name in COMMAND_HANDLERS:
        logger.info(f"🎬 [handle_admin_media] Выполняем команду '{cmd_name}' з аргументами {cmd_args}")
        
        # Если есть target_user - создаем fake reply
        if target_user_id:
            try:
                target_user = await context.bot.get_chat(target_user_id)
                from telegram import User as TgUser
                fake_user = TgUser(
                    id=target_user_id,
                    is_bot=False,
                    first_name=target_user.first_name or "",
                    last_name=target_user.last_name or "",
                    username=target_user.username
                )
                from telegram import Message
                fake_msg = Message(
                    message_id=0,
                    date=datetime.now(),
                    chat=update.effective_chat,
                    from_user=fake_user
                )
                update.message.reply_to_message = fake_msg
                logger.info(f"🎬 [handle_admin_media] Создан fake reply для {target_user_id}")
            except Exception as e:
                logger.warning(f"⚠️ Не удалось создать fake reply: {e}")
        
        # Передаємо аргументи в context
        context.args = cmd_args
        logger.info(f"🎬 [handle_admin_media] context.args встановлено: {context.args}")
        
        # Выполняем команду
        try:
            await COMMAND_HANDLERS[cmd_name](update, context)
            logger.info(f"✅ [handle_admin_media] Команда '{cmd_name}' успішно виконана!")
        except Exception as e:
            logger.error(f"❌ Помилка при выполнении команди '{cmd_name}': {e}")
    else:
        logger.warning(f"⚠️ Команда '{cmd_name}' не найдена в COMMAND_HANDLERS")

async def posada_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Змінити посаду адміністратора - власник/головні адміни
    /posada <посада> - змінити собі
    /posada <посада> (reply) - змінити тому, кому replied"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message or not update.effective_chat:
        await reply_and_delete(update, "❌ Помилка при отриманні даних", delay=30)
        return
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    
    # Перевіряємо чи це власник або head_admin
    role = db.get_role(user_id)
    if not is_owner(user_id) and role != "head_admin":
        await reply_and_delete(update, "❌ Тільки власник та головні адміни можуть змінювати посади!", delay=60)
        return
    
    # Отримуємо посаду з аргументів
    if not context.args or len(context.args) == 0:
        await reply_and_delete(update, "❌ Вкажіть посаду: /posada <посада>", delay=30)
        return
    
    new_posada = " ".join(context.args)
    
    # Визначаємо цільового користувача
    target_user_id = user_id
    target_user_name = update.effective_user.full_name or "Невідомий"
    
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        # Reply на повідомлення - змінюємо посаду тому, кому replied
        target_user_id = update.message.reply_to_message.from_user.id
        target_user_name = update.message.reply_to_message.from_user.full_name or "Невідомий"
    
    try:
        logger.info(f"🏷️ [posada_command] Змінюємо посаду користувачу {target_user_id} на '{new_posada}'")
        
        # Встановлюємо нову посаду
        await context.bot.set_chat_administrator_custom_title(
            chat_id=chat_id,
            user_id=target_user_id,
            custom_title=new_posada
        )
        
        logger.info(f"✅ [posada_command] Посада встановлена")
        
        # Повідомлення в чат
        clickable_target = f"<a href='tg://user?id={target_user_id}'>{target_user_name}</a>"
        admin_name = update.effective_user.full_name or "Невідомий"
        clickable_admin = f"<a href='tg://user?id={user_id}'>{admin_name}</a>"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🏷️ {clickable_admin} встановив посаду для {clickable_target}\n• Нова посада: {new_posada}",
            parse_mode="HTML"
        )
        
        # Логуємо в канал
        if LOG_CHANNEL_ID:
            try:
                role_text = "Власник" if is_owner(user_id) else "Головний адмін"
                admin_username = f"@{update.effective_user.username}" if update.effective_user.username else ""
                target_username = f"@{update.message.reply_to_message.from_user.username}" if (update.message.reply_to_message and update.message.reply_to_message.from_user.username) else ""
                
                log_text = f"""{role_text}
{clickable_admin} {admin_username} [{user_id}]
🏷️ Встановив посаду
{clickable_target} {target_username} [{target_user_id}]
• Нова посада: {new_posada}
• Чат: {chat_id}"""
                
                await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode="HTML")
            except Exception as e:
                logger.warning(f"⚠️ Помилка при логуванні: {e}")
    
    except Exception as e:
        logger.error(f"❌ Помилка при зміні посади: {e}")
        await reply_and_delete(update, f"❌ Помилка: {str(e)[:100]}", delay=60)


async def handle_user_join_proper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробляє приєднання користувачів через StatusUpdate.NEW_CHAT_MEMBERS"""
    if not update.message or not update.message.new_chat_members:
        return
    
    chat_id = update.effective_chat.id
    bot_id = context.bot.id
    
    # Перевіряємо, чи був доданий саме бот
    is_bot_added = any(member.id == bot_id for member in update.message.new_chat_members)
    
    # Якщо бот доданий у невідомий чат - запитуємо підтвердження у власника
    if is_bot_added and not is_allowed_chat(chat_id) and update.effective_chat.type != "private":
        chat_name = update.effective_chat.title or "Невідомий чат"
        chat_link = ""
        try:
            chat_link = await update.effective_chat.export_invite_link()
        except:
            pass
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Підтвердити", callback_data=f"approve_chat_{chat_id}"),
                InlineKeyboardButton("❌ Відхилити", callback_data=f"reject_chat_{chat_id}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        msg_text = f"🔔 <b>Новий запит на додавання!</b>\n\nБот доданий в чат: <b>{chat_name}</b>\nID: <code>{chat_id}</code>\n\n⚠️ <b>Будь ласка, надайте боту права адміністратора (всі, окрім історій) для коректної роботи в цьому чаті.</b>"
        if chat_link:
            msg_text += f"\nПосилання: {chat_link}"
        
        # Повідомляємо в чат про необхідність прав
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text="👋 Вітаю! Я Santa Admin Bot. Для моєї повноцінної роботи в цьому чаті, будь ласка, <b>зробіть мене адміністратором</b> з усіма правами (окрім історій).",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"⚠️ Не вдалося відправити привітання в чат {chat_id}: {e}")

        for owner_id in OWNER_IDS:
            try:
                await context.bot.send_message(
                    chat_id=owner_id,
                    text=msg_text,
                    parse_mode="HTML",
                    reply_markup=reply_markup
                )
                logger.info(f"📨 Сповіщення про новий чат {chat_id} надіслано власнику {owner_id}")
            except Exception as e:
                logger.error(f"❌ Не вдалося сповістити власника {owner_id}: {e}")
        return

    logger.info(f"👥 [handle_user_join_proper] Обробка приєднання в чаті {chat_id}, USER_CHAT_ID: {USER_CHAT_ID}")
    
    # Якщо це НЕ основний чат - пропускаємо (для звичайних користувачів)
    if chat_id != USER_CHAT_ID and chat_id not in SECONDARY_CHAT_IDS:
        logger.info(f"👥 [handle_user_join_proper] Чат {chat_id} не є дозволеним, пропускаємо авто-адмінку")
        return
    
    for new_member in update.message.new_chat_members:
        user_id = new_member.id
        logger.info(f"👥 [handle_user_join_proper] Обробка користувача {user_id}")
        
        # Пропускаємо ботів
        if new_member.is_bot:
            logger.info(f"🤖 [handle_user_join_proper] Бот {user_id}, пропускаємо")
            continue
        
        # Перевіряємо роль
        role = db.get_role(user_id)
        is_owner_user = is_owner(user_id)
        logger.info(f"👥 [handle_user_join_proper] Роль: '{role}' (type: {type(role)}), is_owner: {is_owner_user}")
        logger.info(f"👥 [handle_user_join_proper] Перевірка: role == 'head_admin' → {role == 'head_admin'}, is_owner → {is_owner_user}")
        
        if is_owner_user or role == "head_admin":
            logger.info(f"👥 [handle_user_join_proper] ✅ УМОВА ВИКОНАНА! Даємо права адміна користувачу {user_id}")
        else:
            logger.info(f"👥 [handle_user_join_proper] ❌ УМОВА НЕ ВИКОНАНА (role={role}, owner={is_owner_user}), права не даємо")
            
        if is_owner_user or role == "head_admin":
            logger.info(f"👥 [handle_user_join_proper] Даємо права адміна користувачу {user_id}")
            
            try:
                # Даємо права адміністратора
                await context.bot.promote_chat_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    can_post_messages=True,
                    can_edit_messages=True,
                    can_delete_messages=True,
                    can_restrict_members=True,
                    can_promote_members=True,
                    can_change_info=True,
                    can_invite_users=True,
                    can_pin_messages=True,
                    can_manage_video_chats=True,
                    can_edit_stories=False
                )
                logger.info(f"✅ [handle_user_join_proper] Права адміна надані користувачу {user_id}")
                
                # Встановлюємо посаду "ᅠ" одразу
                try:
                    await context.bot.set_chat_administrator_custom_title(
                        chat_id=chat_id,
                        user_id=user_id,
                        custom_title="ᅠ"
                    )
                    logger.info(f"✅ [handle_user_join_proper] Посада встановлена для {user_id}")
                except Exception as e:
                    logger.warning(f"⚠️ [handle_user_join_proper] Помилка при встановленні посади: {e}")
                
            except Exception as e:
                logger.error(f"❌ [handle_user_join_proper] Помилка при наданні прав: {e}")




async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Логує отримання прав адміністратора в окремий канал"""
    if not update.my_chat_member:
        return
    
    old_status = update.my_chat_member.old_chat_member.status
    new_status = update.my_chat_member.new_chat_member.status
    
    # Якщо статус змінився на адміністратора
    if new_status == 'administrator' and old_status != 'administrator':
        chat = update.effective_chat
        chat_id = chat.id
        chat_name = chat.title or "Приватний чат"
        
        # Спроба отримати посилання
        chat_link = ""
        try:
            chat_link = await chat.export_invite_link()
        except:
            if chat.username:
                chat_link = f"https://t.me/{chat.username}"
        
        log_channel_new = -1002863334815
        msg_text = f"#add_group\n\n✅ <b>Бот отримав права адміністратора!</b>\n\nГрупа: <b>{chat_name}</b>\nID: <code>{chat_id}</code>"
        if chat_link:
            msg_text += f"\nПосилання: {chat_link}"
            
        try:
            await context.bot.send_message(chat_id=log_channel_new, text=msg_text, parse_mode="HTML")
            logger.info(f"📊 Лог про отримання прав у чаті {chat_id} відправлено")
        except Exception as e:
            logger.error(f"❌ Помилка відправки логу про права: {e}")

async def quit_target_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Дозволяє власнику змусити бота покинути конкретний чат за його ID"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Використання: /quit [chat_id]")
        return
    
    try:
        target_chat_id = int(context.args[0])
        chat = await context.bot.get_chat(target_chat_id)
        
        await update.message.reply_text(f"👋 Бот покидає чат <b>{chat.title}</b> ({target_chat_id}) за наказом власника.", parse_mode="HTML")
        
        try:
            await context.bot.send_message(chat_id=target_chat_id, text="👋 До побачення! Власник наказав мені покинути цей чат.")
        except:
            pass
            
        await context.bot.leave_chat(chat_id=target_chat_id)
        
        # Якщо чат був у другорядних - видаляємо
        if target_chat_id in SECONDARY_CHAT_IDS:
            SECONDARY_CHAT_IDS.remove(target_chat_id)
            config['SECONDARY_CHAT_IDS'] = SECONDARY_CHAT_IDS
            save_config()
            
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {str(e)}")

async def link_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Дозволяє власнику отримати посилання на чат за його ID"""
    user_id = update.effective_user.id
    if not is_owner(user_id):
        return
    
    if not context.args:
        await reply_and_delete(update, "❌ Використання: /link [chat_id]")
        return
    
    try:
        target_chat_id = int(context.args[0])
        chat = await context.bot.get_chat(target_chat_id)
        
        chat_link = ""
        # Спроба отримати існуюче посилання або створити нове
        try:
            chat_link = await chat.export_invite_link()
        except:
            if chat.username:
                chat_link = f"https://t.me/{chat.username}"
        
        if chat_link:
            await update.message.reply_text(f"🔗 Посилання на чат <b>{chat.title}</b>:\n{chat_link}", parse_mode="HTML")
        else:
            await update.message.reply_text(f"❌ Не вдалося отримати посилання для чату <b>{chat.title}</b> (можливо, бот не адмін або посилання заборонені).", parse_mode="HTML")
            
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {str(e)}")

async def marry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Оформити віртуальний шлюб (з підтвердженням)"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    is_head = is_head_admin(user_id)
    is_owner_user = is_owner(user_id)
    
    # Випадок 1: Reply на повідомлення - може будь-який користувач
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        try:
            spouse_user = update.message.reply_to_message.from_user
            spouse_id = spouse_user.id
            
            if user_id == spouse_id:
                await update.message.reply_text("❌ Не можна одружитися самому з собою! 💔")
                return
            
            user_name = get_display_name(user_id, update.effective_user.full_name or "")
            spouse_name = get_display_name(spouse_id, spouse_user.full_name or "")
            
            # Перевірка на вже існуючий шлюб
            if db.get_spouse(user_id):
                await update.message.reply_text("❌ Ви вже одружені!")
                return
            if db.get_spouse(spouse_id):
                await update.message.reply_text(f"❌ {spouse_name} вже у шлюбі!")
                return

            keyboard = [
                [
                    InlineKeyboardButton("✅ Так, я згоден/згодна", callback_data=f"marry|yes|{user_id}|{spouse_id}"),
                    InlineKeyboardButton("❌ Ні, відхилити", callback_data=f"marry|no|{user_id}|{spouse_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Create clickable mentions
            user_mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
            spouse_mention = f"<a href='tg://user?id={spouse_id}'>{spouse_name}</a>"
            
            logger.info(f"💍 [marry_proposal] Creating marriage proposal from {user_id} ({user_name}) to {spouse_id} ({spouse_name})")
            
            await update.message.reply_text(
                f"💍 {user_mention} пропонує руку і серце {spouse_mention}!\n\n{spouse_mention}, ви згодні?",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            logger.info(f"✅ [marry_proposal] Message sent successfully")
        except Exception as e:
            logger.error(f"Помилка: {e}")
            await update.message.reply_text(f"❌ Помилка: {e}")
        return
    
    # Випадок 2: Одружувати інших - тільки власник чи головний адмін
    if not (is_owner_user or is_head):
        await update.message.reply_text("❌ Для пропозиції шлюбу потрібна відповідь на повідомлення обранця/обраниці!")
        return
    
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❌ Використання: /marry @user1 @user2")
        return
    
    try:
        user1_info = await get_user_info(update, context, context.args[0])
        user2_info = await get_user_info(update, context, context.args[1])
        
        if not user1_info or not user2_info:
            missing_user = context.args[1] if user1_info else context.args[0]
            await update.message.reply_text(
                f"❌ Користувач {missing_user} не знайдено в системі.\n\n"
                f"💭 Можливо причина:\n"
                f"• Він не писав в чат після оновлення бота\n"
                f"• Невірно введено username\n\n"
                f"✍️ Попросіть його написати щось в чат, щоб його додали до системи, і спробуйте ще раз!"
            )
            return
        
        u1_id, u2_id = user1_info["user_id"], user2_info["user_id"]
        if u1_id == u2_id:
            await update.message.reply_text("❌ Користувач не може одружитися сам з собою! 💔")
            return
        
        u1_name = get_display_name(u1_id, user1_info.get("full_name", ""))
        u2_name = get_display_name(u2_id, user2_info.get("full_name", ""))
        
        success = db.marry_users(u1_id, u2_id, u1_name, u2_name)
        
        if success:
            u1_mention = f"<a href=\"tg://user?id={u1_id}\">{u1_name}</a>"
            u2_mention = f"<a href=\"tg://user?id={u2_id}\">{u2_name}</a>"
            
            # Get owner/admin mention
            admin_id = update.effective_user.id
            admin_name = get_display_name(admin_id, update.effective_user.full_name or "Santa")
            admin_mention = f"<a href=\"tg://user?id={admin_id}\">{admin_name}</a>"
            
            await update.message.reply_text(
                f"💍 {u1_mention} та {u2_mention} 💕\n🎉 {admin_mention} оголосив вас подружжям!", 
                parse_mode="HTML"
            )
            if LOG_CHANNEL_ID:
                await log_to_channel(context, f"💍 {u1_name} ({u1_id}) і {u2_name} ({u2_id}) одружені адміном {admin_name}! 💕")
        else:
            await update.message.reply_text("❌ Помилка при оформленні шлюбу!")
            
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")

async def marriage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка кнопок підтвердження шлюбу"""
    query = update.callback_query
    if not query or not query.data:
        return

    logger.debug(f"📝 [marriage_callback] Processing callback data: {query.data}")
    
    # Parse callback data with pipe separator format
    if not query.data.startswith("marry|"):
        logger.warning(f"⚠️ [marriage_callback] Invalid callback data format: {query.data}")
        return
    
    try:
        data = query.data.split('|')
        if len(data) != 4 or data[0] != "marry":
            logger.warning(f"⚠️ [marriage_callback] Incorrect data parts: {len(data)} parts, first: {data[0]}")
            return

        action = data[1]  # yes or no
        proposer_id = int(data[2])
        target_id = int(data[3])
        
        logger.info(f"💍 [marriage_callback] Processing {action} response from {query.from_user.id} for proposal {proposer_id} -> {target_id}")
        
        if query.from_user.id != target_id:
            logger.warning(f"⚠️ [marriage_callback] User {query.from_user.id} cannot respond to proposal (target: {target_id})")
            await query.answer("❌ Це повідомлення не для вас!", show_alert=True)
            return

        # Get proposer's info to get their full name for display
        proposer_info = db.get_user(proposer_id)
        proposer_name = get_display_name(proposer_id, proposer_info.get("full_name", "") if proposer_info else "")
        target_name = get_display_name(target_id, query.from_user.full_name or "")

        if action == "yes":
            success = db.marry_users(proposer_id, target_id, proposer_name, target_name)
            if success:
                logger.info(f"✅ [marriage_callback] Marriage successful: {proposer_name} ({proposer_id}) + {target_name} ({target_id})")
                proposer_mention = f"<a href=\"tg://user?id={proposer_id}\">{proposer_name}</a>"
                target_mention = f"<a href=\"tg://user?id={target_id}\">{target_name}</a>"
                await query.edit_message_text(f"💍 {proposer_mention} та {target_mention} 💕\n🎉 Вітаємо з шлюбом! Кохання та злагоди! ❤️", parse_mode="HTML")
                if LOG_CHANNEL_ID:
                    await log_to_channel(context, f"💍 {proposer_name} ({proposer_id}) і {target_name} ({target_id}) одружилися! 💕")
            else:
                logger.error(f"❌ [marriage_callback] Marriage failed for {proposer_id} + {target_id}")
                await query.edit_message_text("❌ Сталася помилка при оформленні шлюбу.")
        else:
            logger.info(f"💔 [marriage_callback] Proposal rejected: {target_name} rejected {proposer_name}")
            proposer_mention = f"<a href='tg://user?id={proposer_id}'>{proposer_name}</a>"
            target_mention = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
            await query.edit_message_text(f"💔 {target_mention} відхилив(ла) пропозицію від {proposer_mention}...", parse_mode="HTML")
    
    except Exception as e:
        logger.error(f"❌ [marriage_callback] Error processing callback: {e}")
        try:
            await query.answer(f"❌ Помилка: {e}", show_alert=True)
        except:
            pass

async def divorce_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробка підтвердження розлучення"""
    query = update.callback_query
    if not query or not query.data:
        return

    logger.debug(f"📝 [divorce_callback] Processing callback data: {query.data}")
    
    # Parse callback data with pipe separator format
    if not query.data.startswith("divorce|"):
        logger.warning(f"⚠️ [divorce_callback] Invalid callback data format: {query.data}")
        return
    
    try:
        data = query.data.split('|')
        if len(data) != 4 or data[0] != "divorce":
            logger.warning(f"⚠️ [divorce_callback] Incorrect data parts: {len(data)} parts, first: {data[0]}")
            return

        action = data[1]  # confirm
        user_id = int(data[2])
        spouse_id = int(data[3])
        
        logger.info(f"💔 [divorce_callback] Processing divorce confirmation from {query.from_user.id}")
        
        # Verify the user clicking the button is the one who initiated the divorce
        if query.from_user.id != user_id and not (is_owner(query.from_user.id) or is_head_admin(query.from_user.id)):
            logger.warning(f"⚠️ [divorce_callback] User {query.from_user.id} cannot confirm divorce for {user_id}")
            await query.answer("❌ Це повідомлення не для вас!", show_alert=True)
            return

        if action == "confirm":
            # Get names before divorce
            user_name = get_display_name(user_id, query.from_user.full_name or "Користувач")
            
            # Fetch spouse info from DB before deleting the marriage
            spouse_info = db.get_user(spouse_id)
            spouse_name = get_display_name(spouse_id, spouse_info.get('full_name', 'Невідомий') if spouse_info else "Невідомий")
            
            # Якщо адмін розлучає кабанів
            if query.from_user.id != user_id and (is_owner(query.from_user.id) or is_head_admin(query.from_user.id)):
                logger.info(f"💔 [divorce_callback] Admin {query.from_user.id} divorcing {user_id} and {spouse_id}")
                db.divorce_users(user_id, spouse_id)
                admin_name = get_display_name(query.from_user.id, query.from_user.full_name or "Адмін")
                user_mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
                spouse_mention = f"<a href='tg://user?id={spouse_id}'>{spouse_name}</a>"
                await query.edit_message_text(f"💔 Адмін {admin_name} розлучив {user_mention} і {spouse_mention}! 😢", parse_mode="HTML")
                if LOG_CHANNEL_ID:
                    await log_to_channel(context, f"💔 Адмін {admin_name} ({query.from_user.id}) розлучив {user_name} ({user_id}) і {spouse_name} ({spouse_id})! 😢")
                return

            logger.info(f"💔 [divorce_callback] Confirmed: {user_id} ({user_name}) divorcing {spouse_id} ({spouse_name})")
            db.divorce_users(user_id, spouse_id)
            
            user_mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
            spouse_mention = f"<a href='tg://user?id={spouse_id}'>{spouse_name}</a>"
            await query.edit_message_text(f"💔 {user_mention} і {spouse_mention} розлучилися! 😢", parse_mode="HTML")
            if LOG_CHANNEL_ID:
                await log_to_channel(context, f"💔 {user_name} ({user_id}) і {spouse_name} ({spouse_id}) розлучилися! 😢")
    
    except Exception as e:
        logger.error(f"❌ [divorce_callback] Error processing callback: {e}")
        try:
            await query.answer(f"❌ Помилка: {e}", show_alert=True)
        except:
            pass

async def unmarry_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Розірвати шлюб (автоматично для своєї половини або через адміна для інших)"""
    save_user_from_update(update)
    
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    is_head = is_head_admin(user_id)
    
    # Випадок 1: Reply на повідомлення супруга - верифікація та запит підтвердження
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        try:
            target_user = update.message.reply_to_message.from_user
            target_id = target_user.id
            
            # Якщо це адмін/власник хоче розлучити когось у відповідь
            if is_owner(user_id) or is_head_admin(user_id):
                spouse_info = db.get_spouse(target_id)
                if not spouse_info:
                    await reply_and_delete(update, "❌ Цей користувач не одружений!")
                    return
                
                spouse_id = spouse_info["spouse_id"]
                spouse_name = spouse_info["spouse_name"]
                target_name = get_display_name(target_id, target_user.full_name or "")
                
                target_mention = f"<a href='tg://user?id={target_id}'>{target_name}</a>"
                spouse_mention = f"<a href='tg://user?id={spouse_id}'>{spouse_name}</a>"
                
                keyboard = [[InlineKeyboardButton("Да, розведи кабанів", callback_data=f"divorce|confirm|{user_id}|{target_id}")]]
                reply_markup = InlineKeyboardMarkup(keyboard)
                
                await update.message.reply_text(
                    f"❓ Ви впевнені, що бажаєте розлучити {target_mention} та {spouse_mention}?",
                    reply_markup=reply_markup,
                    parse_mode="HTML"
                )
                return

            # Звичайна логіка для користувача (розлучення зі своїм супругом)
            spouse_info = db.get_spouse(user_id)
            if not spouse_info or spouse_info["spouse_id"] != target_id:
                await reply_and_delete(update, "❌ Це не твій супруг/супруга! 💔")
                return
            
            user_name = get_display_name(user_id, update.effective_user.full_name or "")
            spouse_name = get_display_name(target_id, target_user.full_name or "")
            
            user_mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
            spouse_mention = f"<a href='tg://user?id={target_id}'>{spouse_name}</a>"
            
            logger.info(f"💔 [unmarry] Confirmation requested: {user_id} for divorce from {target_id}")
            
            # Запит підтвердження перед розлученням
            keyboard = [
                [
                    InlineKeyboardButton("100% впевнений, пока", callback_data=f"divorce|confirm|{user_id}|{target_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"💔 {user_mention}, ви впевнені, що хочете розлучитися з {spouse_mention}?\n\nЦю дію неможливо відмінити!",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"❌ [unmarry] Error in reply case: {e}")
            await reply_and_delete(update, f"❌ Помилка: {e}")
        return
    
    # Випадок 2: Без reply - розлучити свою половину (будь-який користувач)
    try:
        spouse_info = db.get_spouse(user_id)
        
        if spouse_info:
            # Користувач одружений, запит підтвердження
            spouse_id = spouse_info["spouse_id"]
            spouse_name = spouse_info["spouse_name"]
            
            user_name = get_display_name(user_id, update.effective_user.full_name or "")
            
            user_mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
            spouse_mention = f"<a href='tg://user?id={spouse_id}'>{spouse_name}</a>"
            
            logger.info(f"💔 [unmarry] Confirmation requested: {user_id} for auto-divorce from {spouse_id}")
            
            # Запит підтвердження перед розлученням
            keyboard = [
                [
                    InlineKeyboardButton("100% впевнений, пока", callback_data=f"divorce|confirm|{user_id}|{spouse_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"💔 {user_mention}, ви впевнені, що хочете розлучитися з {spouse_mention}?\n\nЦю дію неможливо відмінити!",
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
            return
    except Exception as e:
        logger.error(f"❌ [unmarry] Error checking spouse: {e}")
        await reply_and_delete(update, f"❌ Помилка: {e}")
        return
    
    # Випадок 3: Розлучити іншого користувача - тільки власник або головний адмін
    if not (is_head or is_owner(user_id)):
        logger.info(f"🚫 [unmarry] Відмовлено: {user_id} не має прав")
        await reply_and_delete(update, "❌ Ви не одружені! Для розлучення іншої людини потрібні права головного адміна або власника.")
        return
    
    # Get identifier from args or text
    identifier = None
    if context.args:
        identifier = context.args[0]
    elif update.message and update.message.text:
        # Check if it was called as a text command "розлучити @user"
        msg_text = update.message.text.strip()
        if msg_text.startswith("розлучити"):
            parts = msg_text.split()
            if len(parts) > 1:
                identifier = parts[1]
            
    if not identifier:
        await reply_and_delete(update, "❌ Використання (адмін): /unmarry @user або розлучити @user")
        return
    
    try:
        user_info = await get_user_info(update, context, identifier)
        
        if not user_info:
            await reply_and_delete(update, "❌ Користувача не знайдено!")
            return
        
        spouse = db.get_spouse(user_info["user_id"])
        
        if not spouse:
            u_name = get_display_name(user_info["user_id"], user_info.get("full_name", ""))
            u_mention = f"<a href=\"tg://user?id={user_info['user_id']}\">{u_name}</a>"
            await reply_and_delete(update, f"❌ {u_mention} не одружений/а!", parse_mode="HTML")
            return
        
        logger.info(f"💔 [unmarry] Admin divorce: {user_id} divorcing {user_info['user_id']} and {spouse['spouse_id']}")
        db.divorce_users(user_info["user_id"], spouse["spouse_id"])
        
        user_mention = f"<a href=\"tg://user?id={user_info['user_id']}\">{user_name}</a>"
        spouse_mention = f"<a href=\"tg://user?id={spouse['spouse_id']}\">{spouse_name}</a>"
        
        await reply_and_delete(update, f"💔 Розлучення оформлено!\n{user_mention} і {spouse_mention} більше не разом...", delay=10, parse_mode="HTML")
        if LOG_CHANNEL_ID:
            await log_to_channel(context, f"💔 Адмін {get_display_name(user_id)} розлучив {user_name} ({user_info['user_id']}) і {spouse_name}! 😢")
        
    except Exception as e:
        logger.error(f"❌ [unmarry] Error in admin case: {e}")
        await reply_and_delete(update, f"❌ Помилка: {e}")

async def set_marriage_photo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Встановити фото або гіф для карти шлюбу"""
    save_user_from_update(update)
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    
    # Перевіряємо, чи користувач одружений
    spouse_info = db.get_spouse(user_id)
    if not spouse_info:
        await update.message.reply_text("💔 Ви не можете встановити фото шлюбу, бо ви не одружені.")
        return

    # Перевіряємо реплай на медіа
    target_msg = update.message.reply_to_message
    file_id = None
    media_type = None

    if target_msg:
        if target_msg.photo:
            file_id = target_msg.photo[-1].file_id
            media_type = "photo"
        elif target_msg.animation:
            file_id = target_msg.animation.file_id
            media_type = "animation"
        elif target_msg.video:
            file_id = target_msg.video.file_id
            media_type = "video"

    if not file_id:
        await update.message.reply_text("🖼 Будь ласка, використайте цю команду у відповідь на фото, гіф або відео, яке хочете встановити для вашої карти шлюбу.")
        return

    try:
        db.set_marriage_asset(user_id, media_type, file_id)
        # Також встановлюємо для супруга, щоб карта була однакова
        db.set_marriage_asset(spouse_info["spouse_id"], media_type, file_id)
        
        await update.message.reply_text("✅ Фото для вашої карти шлюбу успішно встановлено!")
    except Exception as e:
        logger.error(f"Error in set_marriage_photo: {e}")
        await update.message.reply_text(f"❌ Сталася помилка: {e}")

async def my_marriage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати карту шлюбу користувача"""
    save_user_from_update(update)
    if not update.effective_user or not update.message:
        return
    
    user_id = update.effective_user.id
    try:
        spouse_info = db.get_spouse(user_id)
        if not spouse_info:
            await update.message.reply_text("💔 Ви наразі не перебуваєте у шлюбі.")
            return
        
        spouse_id = spouse_info["spouse_id"]
        spouse_name = spouse_info["spouse_name"]
        married_at = spouse_info["married_at"]
        
        # Отримуємо дату в гарному форматі
        marriage_date = format_kyiv_time(married_at)
        
        # Вираховуємо тривалість
        try:
            married_dt = datetime.fromisoformat(married_at)
            now = datetime.now()
            delta = now - married_dt
            days = delta.days
            if days == 0:
                duration = "сьогодні"
            else:
                duration = f"{days} днів"
        except:
            duration = "невідомо"

        user_name = get_display_name(user_id, update.effective_user.full_name or "")
        user_mention = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
        spouse_mention = f"<a href='tg://user?id={spouse_id}'>{spouse_name}</a>"
        
        text = (
            "💍 <b>КАРТА ВАШОГО ШЛЮБУ</b> 💍\n\n"
            f"👤 {user_mention} та {spouse_mention}\n\n"
            f"📅 <b>Дата весілля:</b> {marriage_date}\n"
            f"⏳ <b>Ви разом уже:</b> {duration}\n\n"
            "💕 Бажаємо вам щастя та вічного кохання! 💕"
        )
        
        # Перевіряємо наявність встановленого фото/гіф
        asset = db.get_marriage_asset(user_id)
        if asset:
            try:
                if asset["media_type"] == "photo":
                    await update.message.reply_photo(asset["file_id"], caption=text, parse_mode="HTML")
                elif asset["media_type"] == "animation":
                    await update.message.reply_animation(asset["file_id"], caption=text, parse_mode="HTML")
                elif asset["media_type"] == "video":
                    await update.message.reply_video(asset["file_id"], caption=text, parse_mode="HTML")
                return
            except Exception as e:
                logger.error(f"Error sending marriage asset: {e}")
                # Якщо медіа не відправилось, шлемо просто текст
        
        await update.message.reply_text(text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Помилка в my_marriage_command: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")

async def marriages_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показати список всіх шлюбів"""
    save_user_from_update(update)
    
    if not update.message:
        return
    
    try:
        marriages = db.get_all_marriages()
        
        if not marriages:
            await update.message.reply_text("💔 Наразі немає ніяких шлюбів!")
            return
        
        text = "💍 <b>СПИСОК ВІРТУАЛЬНИХ ШЛЮБІВ</b>\n\n"
        for i, marriage in enumerate(marriages, 1):
            marriage_date = format_kyiv_time(marriage["married_at"])
            # Get fresh user data from database and use display name
            u1_info = db.get_user(marriage['user1_id'])
            u2_info = db.get_user(marriage['user2_id'])
            u1_name = get_display_name(marriage['user1_id'], u1_info.get('full_name', '') if u1_info else marriage['user1_name'] or "")
            u2_name = get_display_name(marriage['user2_id'], u2_info.get('full_name', '') if u2_info else marriage['user2_name'] or "")
            u1_mention = f"<a href=\"tg://user?id={marriage['user1_id']}\">{u1_name}</a>"
            u2_mention = f"<a href=\"tg://user?id={marriage['user2_id']}\">{u2_name}</a>"
            text += f"{i}. 💍 {u1_mention} та {u2_mention} 💕\n   📅 {marriage_date}\n\n"
        
        await update.message.reply_text(text, parse_mode="HTML")
        
    except Exception as e:
        logger.error(f"Помилка: {e}")
        await update.message.reply_text(f"❌ Помилка: {e}")

def setup_handlers(application):
    """Налаштовує всі хендлери (винесено з main для швидшого завантаження)"""
    # ✅ КРИТИЧНО: Обробка приєднання користувачів МУСИТЬ БУТИ ДО інших обробників!
    application.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_user_join_proper))
    application.add_handler(ChatMemberHandler(handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    
    application.add_handler(CommandHandler("posada", posada_command))
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("helpg", help_g_command))
    application.add_handler(CommandHandler("helpm", help_m_command))
    application.add_handler(CommandHandler("allcmd", allcmd_command))
    application.add_handler(CommandHandler("cmdall", allcmd_command))
    
    application.add_handler(CommandHandler("giveperm", giveperm_command))
    application.add_handler(CommandHandler("giveperm_simple", giveperm_simple_command))
    application.add_handler(CommandHandler("removeperm", removeperm_command))
    application.add_handler(CommandHandler("custom_main", custom_main_command))
    application.add_handler(CommandHandler("set_cmd", set_cmd_command))
    application.add_handler(CommandHandler("del_cmd", del_cmd_command))
    application.add_handler(CommandHandler("doubler", doubler_command))
    application.add_handler(CommandHandler("set_personal", set_personal_command))
    application.add_handler(CommandHandler("set_cmdm", set_cmdm_command))
    application.add_handler(CommandHandler("list_cmdm", list_cmdm_command))
    application.add_handler(CommandHandler("del_cmdm", del_cmdm_command))
    application.add_handler(CommandHandler("set_adminm", set_adminm_command))
    application.add_handler(CommandHandler("del_adminm", del_adminm_command))
    application.add_handler(CommandHandler("del_personal", del_personal_command))
    application.add_handler(CommandHandler("role_cmd", role_cmd_command))
    application.add_handler(CommandHandler("personal", role_cmd_command))
    application.add_handler(CommandHandler("admin_list", admin_list_command))
    
    application.add_handler(CommandHandler("add_gnome", add_gnome_command))
    application.add_handler(CommandHandler("remove_gnome", remove_gnome_command))
    application.add_handler(CommandHandler("add_main_admin", add_main_admin_command))
    application.add_handler(CommandHandler("remove_main_admin", remove_main_admin_command))
    application.add_handler(CommandHandler("add_owner", add_owner_command))
    application.add_handler(CommandHandler("remove_owner", remove_owner_command))
    
    application.add_handler(CommandHandler("ban_s", ban_s_command))
    application.add_handler(CommandHandler("ban_t", ban_t_command))
    application.add_handler(CommandHandler("unban_s", unban_s_command))
    application.add_handler(CommandHandler("unban_t", unban_t_command))
    application.add_handler(CommandHandler("mute_s", mute_s_command))
    application.add_handler(CommandHandler("mute_t", mute_t_command))
    application.add_handler(CommandHandler("unmute_s", unmute_s_command))
    application.add_handler(CommandHandler("unmute_t", unmute_t_command))
    application.add_handler(CommandHandler("kick", kick_command))
    application.add_handler(CommandHandler("nah", nah_command))
    application.add_handler(CommandHandler("unnah", unnah_command))
    application.add_handler(CommandHandler("nahlist", nahlist_command))
    application.add_handler(CommandHandler("export_nah", export_nah_command))
    application.add_handler(CommandHandler("import_nah", import_nah_command))
    
    application.add_handler(CommandHandler("say", say_command))
    application.add_handler(CommandHandler("link", link_command))
    application.add_handler(CommandHandler("quit", quit_target_command))
    application.add_handler(CommandHandler("marry", marry_command))
    application.add_handler(CommandHandler("unmarry", unmarry_command))
    application.add_handler(CommandHandler("marriages", marriages_command))
    application.add_handler(CommandHandler("my_marriage", my_marriage_command))
    application.add_handler(CommandHandler("marriage_photo", set_marriage_photo_command))
    application.add_handler(CommandHandler("says", says_command))
    application.add_handler(CommandHandler("sayon", sayon_command))
    application.add_handler(CommandHandler("sayson", sayson_command))
    application.add_handler(CommandHandler("sayoff", sayoff_command))
    application.add_handler(CommandHandler("sayoffall", sayoffall_command))
    application.add_handler(CommandHandler("saypin", saypin_command))
    application.add_handler(CommandHandler("save_s", save_s_command))
    application.add_handler(CommandHandler("online_list", online_list_command))
    application.add_handler(CommandHandler("sayb", sayb_command))
    application.add_handler(CommandHandler("sayu", sayu_command))
    
    application.add_handler(CommandHandler("alarm", alarm_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("hto", hto_command))
    
    application.add_handler(CommandHandler("note", note_command))
    application.add_handler(CommandHandler("notes", notes_command))
    application.add_handler(CommandHandler("delnote", delnote_command))
    application.add_handler(CommandHandler("reminder", reminder_command))
    application.add_handler(CommandHandler("reminde", reminde_command))
    
    application.add_handler(CommandHandler("birthdays", birthdays_command))
    application.add_handler(CommandHandler("addb", addb_command))
    application.add_handler(CommandHandler("delb", delb_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("setbgif", setbgif_command))
    application.add_handler(CommandHandler("setbtext", setbtext_command))
    application.add_handler(CommandHandler("previewb", previewb_command))
    
    application.add_handler(CommandHandler("adminchat", adminchat_command))
    application.add_handler(CommandHandler("userchat", userchat_command))
    application.add_handler(CommandHandler("logchannel", logchannel_command))
    application.add_handler(CommandHandler("testchannel", testchannel_command))
    application.add_handler(CommandHandler("santas", santas_command))
    application.add_handler(CommandHandler("deltimer", deltimer_command))
    application.add_handler(CommandHandler("restart", restart_command))
    application.add_handler(CommandHandler("quit", quit_command))
    application.add_handler(CommandHandler("add_secondary_chat", add_secondary_chat_command))
    application.add_handler(CommandHandler("menu", menu_command))
    
    application.add_handler(ChatMemberHandler(on_chat_member_updated, ChatMemberHandler.CHAT_MEMBER))
    
    # Callback queries for chat approval
    application.add_handler(CallbackQueryHandler(approve_chat_callback, pattern="^chat_"))
    application.add_handler(CallbackQueryHandler(marriage_callback, pattern="^marry\\|"))
    application.add_handler(CallbackQueryHandler(divorce_confirmation_callback, pattern="^divorce\\|"))
    application.add_handler(CallbackQueryHandler(menu_callback_handler, pattern="^menu_"))
    
    application.add_handler(CommandHandler("profile_set", profile_set_command))
    application.add_handler(CommandHandler("myname", myname_command))
    application.add_handler(CommandHandler("mym", mym_command))
    application.add_handler(CommandHandler("mymt", mymt_command))
    
    # Команди для видалення профілю
    application.add_handler(CommandHandler("del_myname", del_myname_command))
    application.add_handler(CommandHandler("del_mym", del_mym_command))
    application.add_handler(CommandHandler("del_mymt", del_mymt_command))
    
    # Команди для резервної копії
    application.add_handler(CommandHandler("rezerv", rezerv_command))
    application.add_handler(CommandHandler("import", import_command))
    
    # ВАЖЛИВО: Обробка текстових команд МУСИТЬ БУТИ ДО handle_any_message!
    # Якщо handle_any_message з filters.ALL буде першим - вона перехопить ВСІ повідомлення
    # Обробка текстових команд на українській
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_commands))
    
    # Обработчик для стикеров/гифок/видео (выполнение команд админа)
    # КРИТИЧНО: ПЕРЕД handle_document_import щоб перехопити гіфи ДО документів!
    # Створюємо фільтр для всіх типів медіа: стікери, гіфи (animation), відео
    application.add_handler(MessageHandler(filters.Sticker.ALL | filters.ANIMATION | filters.VIDEO, handle_admin_media))
    
    # Обробка документів (автоматичний імпорт чорного списку за назвою файлу)
    # ВАЖЛИВО: ПІСЛЯ обробника медіа, щоб гіфи спочатку перевірилися як команди
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document_import))
    
    # КРИТИЧНО: Перевірка чорного списку - ПІСЛЯ більш специфічних обробників!
    application.add_handler(MessageHandler(filters.ALL, check_blacklist_and_ban))
    
    
    # Обробка входження користувачів - запускається для НЕ-текстових повідомлень
    application.add_handler(MessageHandler(filters.ALL, handle_any_message))
    
    # Ініціалізуємо COMMAND_HANDLERS для алiасів ДИНАМІЧНО через globals()
    # Це дозволяє уникнути проблем з порядком визначення функцій
    global COMMAND_HANDLERS
    command_names = [
        "start", "help", "help_g", "help_m", "allcmd", "add_gnome", "remove_gnome",
        "add_main_admin", "remove_main_admin", "ban_s", "ban_t", "unban_s", "unban_t",
        "mute_s", "mute_t", "unmute_s", "unmute_t", "kick", "nah", "say", "says",
        "sayon", "sayson", "sayoff", "sayoffall", "saypin", "save_s", "online_list",
        "sayb", "sayu", "alarm", "broadcast", "hto", "note", "notes", "delnote", "marry", "unmarry", "marriages",
        "reminder", "reminde", "birthdays", "addb", "delb", "setbgif", "setbtext", "previewb",
        "adminchat", "userchat", "logchannel", "testchannel", "santas", "deltimer",
        "restart", "quit", "add_secondary_chat", "profile", "profile_set", "myname", "mym", "mymt", "del_myname", "del_mym",
        "del_mymt", "giveperm", "giveperm_simple", "removeperm", "custom_main", "set_cmd", 
        "del_cmd", "doubler", "set_personal", "set_cmdm", "del_personal", "set_adminm", "del_adminm", "role_cmd", "admin_list", "rezerv", "posada",
        "logs", "get_config", "update_config", "menu",
    ]
    
    COMMAND_HANDLERS = {}
    for cmd_name in command_names:
        func_name = f"{cmd_name}_command"
        if func_name in globals():
            COMMAND_HANDLERS[cmd_name] = globals()[func_name]
    
    logger.info(f"✅ COMMAND_HANDLERS ініціалізовано з {len(COMMAND_HANDLERS)} командами!")

async def on_chat_member_updated(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вихід бота з чату, якщо власник виходить і немає інших адмінів/власників"""
    chat_member = update.chat_member
    if not chat_member:
        return

    # Перевіряємо, чи це вихід учасника (left або kicked)
    if chat_member.new_chat_member.status in ["left", "kicked"]:
        user_id = chat_member.from_user.id
        chat_id = update.effective_chat.id
        
        logger.info(f"📍 [ChatMember] Користувач {user_id} покинув/був кікнут з чату {chat_id}")
        
        # Перевіряємо, чи це саме головний власник 7247114478
        if user_id == 7247114478:
            logger.info(f"👑 [ChatMember] Основний власник {user_id} покинув чат {chat_id}")
            
            # Перевіряємо, чи це не основний чат (адмін-чат або користувацький)
            config_data = load_config()
            main_chats = [config_data.get("ADMIN_CHAT_ID"), config_data.get("USER_CHAT_ID")]
            
            if chat_id not in main_chats:
                logger.info(f"📍 [ChatMember] Чат {chat_id} - це другорядний чат, перевіряю адмінів/власників")
                
                # Отримуємо всіх головних адмінів з бази даних
                head_admins = db.get_all_with_role("head_admin")
                head_admin_ids = [admin["user_id"] for admin in head_admins]
                
                # Додаємо власників з конфігурації
                config_owner_ids = config_data.get("OWNER_IDS", [])
                all_admins_and_owners = head_admin_ids + config_owner_ids
                
                logger.info(f"📋 [ChatMember] Список адмінів/власників: {all_admins_and_owners}")
                
                # Перевіряємо, чи є хоча б один адмін/власник в чаті
                has_admin_or_owner = False
                remaining_admins = []
                
                for admin_id in all_admins_and_owners:
                    try:
                        chat_member_status = await context.bot.get_chat_member(chat_id, admin_id)
                        # Перевіряємо, чи адмін/власник в чаті і не покинув його
                        if chat_member_status.status not in ["left", "kicked"]:
                            has_admin_or_owner = True
                            remaining_admins.append(admin_id)
                            logger.info(f"✅ [ChatMember] Адмін/власник {admin_id} залишається в чаті {chat_id}")
                    except Exception as e:
                        logger.debug(f"⚠️ [ChatMember] Не вдалось перевірити статус {admin_id}: {e}")
                
                if has_admin_or_owner:
                    logger.info(f"✅ [ChatMember] У чаті {chat_id} залишаються адмін/власник(и): {remaining_admins}. Бот залишається в чаті.")
                else:
                    logger.info(f"❌ [ChatMember] У чаті {chat_id} немає адмінів/власників. Бот виходить з чату.")
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text="⚠️ <b>Основний власник покинув чат.</b>\nНемає інших адмінів/власників.\nБот припиняє роботу в даному чаті та виходить.",
                            parse_mode="HTML"
                        )
                        await context.bot.leave_chat(chat_id)
                        logger.info(f"🚪 [ChatMember] Бот успішно вийшов з чату {chat_id}")
                    except Exception as e:
                        logger.error(f"❌ [ChatMember] Помилка при виході з чату {chat_id}: {e}")

def main():
    if not BOT_TOKEN:
        logger.error("Не вказано BOT_TOKEN!")
        return
    
    restart_count = 0
    
    while True:
        try:
            # Очищуємо старий event loop (для Replit)
            try:
                old_loop = asyncio.get_event_loop()
                if old_loop.is_closed():
                    asyncio.set_event_loop(asyncio.new_event_loop())
            except:
                asyncio.set_event_loop(asyncio.new_event_loop())
            
            # Очищуємо активні режими асинхронно (не блокуємо запуск)
            try:
                db.clear_all_online_modes()
            except:
                pass  # Ігноруємо помилки при очищенні
            
            application = Application.builder().token(BOT_TOKEN).build()
            
            # Налаштування job_queue для автоматичних днів народження та нагадувань
            if application.job_queue:
                # Дні народження о 8:00 Київського часу
                birthday_time = time(hour=8, minute=0, tzinfo=KYIV_TZ)
                application.job_queue.run_daily(
                    send_birthday_greetings,
                    time=birthday_time,
                    days=(0, 1, 2, 3, 4, 5, 6)  # Кожен день
                )
                
                # Перевірка нагадувань кожну хвилину
                application.job_queue.run_repeating(
                    check_and_send_reminders,
                    interval=60,  # Кожні 60 секунд
                    first=10  # Перший запуск через 10 секунд
                )
            
            # Налаштовуємо всі хендлери
            setup_handlers(application)
            
            logger.info("🤖 Бот запущено!")
            restart_count = 0
            
            application.run_polling(allowed_updates=Update.ALL_TYPES)
            
            # Якщо RESTART_BOT = True, вихідимо з exception обробки і перезапускаємо
            if RESTART_BOT:
                logger.info("🔄 Перезапуск бота за запитом...")
                continue
            
        except Exception as e:
            restart_count += 1
            logger.error(f"🔴 ПОМИЛКА БОТА: {e}")
            logger.error(f"🔄 Перезапуск #{restart_count} через 5 секунд...")
            time_module.sleep(5)
            
        except KeyboardInterrupt:
            logger.info("🛑 Бот зупинено користувачем")
            break

if __name__ == '__main__':
    main()
