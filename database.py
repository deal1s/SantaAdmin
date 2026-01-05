import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any
import pytz

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path: str = "bot_database.db"):
        self.db_path = db_path
        self.init_database()
    
    def get_connection(self):
        return sqlite3.connect(self.db_path)
    
    def init_database(self):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS roles (
                user_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL,
                added_by INTEGER,
                added_at TEXT NOT NULL,
                full_name TEXT,
                username TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS message_mapping (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_message_id INTEGER NOT NULL,
                user_message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS online_modes (
                user_id INTEGER PRIMARY KEY,
                mode TEXT NOT NULL,
                started_at TEXT NOT NULL,
                last_activity TEXT NOT NULL,
                source_chat_id INTEGER,
                target_chat_id INTEGER
            )
        ''')
        
        # Міграція: додаємо target_chat_id колонку якщо вона не існує
        try:
            cursor.execute('PRAGMA table_info(online_modes)')
            columns = [column[1] for column in cursor.fetchall()]
            if 'target_chat_id' not in columns:
                cursor.execute('ALTER TABLE online_modes ADD COLUMN target_chat_id INTEGER')
                logger.info("✅ Додана колонка target_chat_id до таблиці online_modes")
        except Exception as e:
            logger.warning(f"⚠️ Помилка при міграції: {e}")
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY,
                banned_by INTEGER NOT NULL,
                reason TEXT,
                banned_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                banned_by_name TEXT,
                banned_by_username TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS mutes (
                user_id INTEGER PRIMARY KEY,
                muted_by INTEGER NOT NULL,
                reason TEXT,
                muted_at TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                muted_by_name TEXT,
                muted_by_username TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS blacklist (
                user_id INTEGER PRIMARY KEY,
                added_by INTEGER NOT NULL,
                added_at TEXT NOT NULL,
                reason TEXT,
                added_by_name TEXT,
                added_by_username TEXT,
                user_full_name TEXT,
                user_username TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS forwarding_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                forwarded_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                note_text TEXT NOT NULL,
                created_by_id INTEGER NOT NULL,
                created_by_name TEXT,
                created_by_username TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                target_user_id INTEGER,
                reminder_text TEXT NOT NULL,
                remind_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                is_sent INTEGER DEFAULT 0,
                chat_id INTEGER
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS birthdays (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                birth_date TEXT NOT NULL,
                added_by INTEGER NOT NULL,
                added_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS birthday_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                gif_file_id TEXT,
                greeting_text TEXT DEFAULT 'З Днем Народження!'
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                first_message_at TEXT,
                joined_at TEXT,
                left_at TEXT,
                invited_by INTEGER,
                invited_by_name TEXT,
                invited_by_username TEXT,
                birth_date TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS say_blocks (
                user_id INTEGER PRIMARY KEY,
                blocked_by INTEGER NOT NULL,
                blocked_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS action_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                user_id INTEGER,
                target_user_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS custom_names (
                user_id INTEGER PRIMARY KEY,
                custom_name TEXT NOT NULL,
                set_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profile_pictures (
                user_id INTEGER PRIMARY KEY,
                media_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                set_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS profile_descriptions (
                user_id INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                set_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS custom_positions (
                user_id INTEGER PRIMARY KEY,
                position_title TEXT NOT NULL,
                set_at TEXT NOT NULL
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS command_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                alias_name TEXT NOT NULL,
                target_command TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, alias_name)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS personal_commands (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                template_text TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, command_name)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS personal_command_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                command_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(command_id) REFERENCES personal_commands(id) ON DELETE CASCADE
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admin_command_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                command_name TEXT NOT NULL,
                media_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, command_name, file_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS marriages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                user1_name TEXT,
                user2_name TEXT,
                married_at TEXT NOT NULL,
                status TEXT DEFAULT 'married',
                divorced_at TEXT,
                UNIQUE(user1_id, user2_id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS marriage_assets (
                user_id INTEGER PRIMARY KEY,
                media_type TEXT NOT NULL,
                file_id TEXT NOT NULL,
                set_at TEXT NOT NULL
            )
        ''')
        
        conn.commit()
        conn.close()
    
    def set_marriage_asset(self, user_id: int, media_type: str, file_id: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO marriage_assets (user_id, media_type, file_id, set_at)
            VALUES (?, ?, ?, ?)
        ''', (user_id, media_type, file_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def get_marriage_asset(self, user_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT media_type, file_id FROM marriage_assets WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return {"media_type": result[0], "file_id": result[1]} if result else None

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO roles (user_id, role, added_by, added_at, full_name, username)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, role, added_by, datetime.now().isoformat(), full_name, username))
        conn.commit()
        conn.close()
    
    def remove_role(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM roles WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def get_role(self, user_id: int) -> Optional[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT role FROM roles WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def get_all_with_role(self, role: str) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, full_name, username FROM roles WHERE role = ?', (role,))
        results = cursor.fetchall()
        conn.close()
        return [{"user_id": r[0], "full_name": r[1], "username": r[2]} for r in results]
    
    def set_online_mode(self, user_id: int, mode: str, source_chat_id: int = None, target_chat_id: int = None):
        conn = self.get_connection()
        cursor = conn.cursor()
        now = datetime.now().isoformat()
        cursor.execute('''
            INSERT OR REPLACE INTO online_modes (user_id, mode, started_at, last_activity, source_chat_id, target_chat_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, mode, now, now, source_chat_id, target_chat_id))
        conn.commit()
        conn.close()
    
    def update_online_activity(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE online_modes SET last_activity = ? WHERE user_id = ?
        ''', (datetime.now().isoformat(), user_id))
        conn.commit()
        conn.close()
    
    def get_online_mode(self, user_id: int) -> Optional[str]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT mode FROM online_modes WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def get_online_mode_source(self, user_id: int) -> Optional[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT source_chat_id FROM online_modes WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def get_online_mode_target(self, user_id: int) -> Optional[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT target_chat_id FROM online_modes WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def remove_online_mode(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM online_modes WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def get_all_online_modes(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT om.user_id, om.mode, om.started_at, om.last_activity, r.full_name, r.username, om.source_chat_id
            FROM online_modes om
            LEFT JOIN roles r ON om.user_id = r.user_id
        ''')
        results = cursor.fetchall()
        conn.close()
        return [{
            "user_id": r[0],
            "mode": r[1],
            "started_at": r[2],
            "last_activity": r[3],
            "full_name": r[4],
            "username": r[5],
            "source_chat_id": r[6]
        } for r in results]
    
    def get_all_online_modes_with_targets(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT om.user_id, om.mode, om.started_at, om.last_activity, r.full_name, r.username, om.source_chat_id, om.target_chat_id
            FROM online_modes om
            LEFT JOIN roles r ON om.user_id = r.user_id
        ''')
        results = cursor.fetchall()
        conn.close()
        return [{
            "user_id": r[0],
            "mode": r[1],
            "started_at": r[2],
            "last_activity": r[3],
            "full_name": r[4],
            "username": r[5],
            "source_chat_id": r[6],
            "target_chat_id": r[7]
        } for r in results]
    
    def clear_all_online_modes(self):
        """Очищує всі активні режими при перезапуску бота"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM online_modes')
        conn.commit()
        conn.close()
    
    def add_ban(self, user_id: int, banned_by: int, reason: str = "", banned_by_name: str = "", banned_by_username: str = ""):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO bans (user_id, banned_by, reason, banned_at, is_active, banned_by_name, banned_by_username)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        ''', (user_id, banned_by, reason, datetime.now().isoformat(), banned_by_name, banned_by_username))
        conn.commit()
        conn.close()
    
    def remove_ban(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE bans SET is_active = 0 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def is_banned(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT is_active FROM bans WHERE user_id = ? AND is_active = 1', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return bool(result)
    
    def add_mute(self, user_id: int, muted_by: int, reason: str = "", muted_by_name: str = "", muted_by_username: str = ""):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO mutes (user_id, muted_by, reason, muted_at, is_active, muted_by_name, muted_by_username)
            VALUES (?, ?, ?, ?, 1, ?, ?)
        ''', (user_id, muted_by, reason, datetime.now().isoformat(), muted_by_name, muted_by_username))
        conn.commit()
        conn.close()
    
    def remove_mute(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE mutes SET is_active = 0 WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def add_to_blacklist(self, user_id: int, added_by: int, reason: str = "", added_by_name: str = "", added_by_username: str = "", user_full_name: str = "", user_username: str = ""):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO blacklist (user_id, added_by, added_at, reason, added_by_name, added_by_username, user_full_name, user_username)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, added_by, datetime.now().isoformat(), reason, added_by_name, added_by_username, user_full_name, user_username))
        conn.commit()
        conn.close()
    
    def is_blacklisted(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM blacklist WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return bool(result)
    
    def get_all_blacklist(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id, added_by, added_at, reason, user_full_name, user_username FROM blacklist ORDER BY added_at DESC')
        results = cursor.fetchall()
        conn.close()
        logger.info(f"📋 [get_all_blacklist] Знайдено {len(results)} записів у чорному списку")
        return [{"user_id": r[0], "added_by": r[1], "added_at": r[2], "reason": r[3], "user_full_name": r[4], "user_username": r[5]} for r in results]
    
    def remove_from_blacklist(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM blacklist WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def add_note(self, user_id: int, note_text: str, created_by_id: int = None, username: str = "", full_name: str = ""):
        conn = self.get_connection()
        cursor = conn.cursor()
        tz = pytz.timezone('Europe/Kyiv')
        now = datetime.now(tz)
        # created_by_id за замовчуванням = user_id якщо не передано
        if created_by_id is None:
            created_by_id = user_id
        cursor.execute('''
            INSERT INTO notes (user_id, note_text, created_by_id, created_by_name, created_by_username, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, note_text, created_by_id, full_name, username, now.isoformat()))
        conn.commit()
        conn.close()
    
    def get_notes(self, user_id: int) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT n.id, n.note_text, n.created_at, n.created_by_id, n.created_by_name, n.created_by_username
            FROM notes n
            WHERE n.user_id = ? 
            ORDER BY n.created_at DESC
        ''', (user_id,))
        results = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "text": r[1], "created_at": r[2], "created_by_id": r[3], "created_by_name": r[4], "created_by_username": r[5]} for r in results]
    
    def delete_note(self, note_id: int) -> bool:
        """Видалити нотатку по ID. Повертає True якщо успішно, False якщо нотатки не існує"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM notes WHERE id = ?', (note_id,))
        deleted = cursor.rowcount > 0
        conn.commit()
        conn.close()
        return deleted
    
    def add_reminder(self, user_id: int, target_user_id: Optional[int], reminder_text: str, remind_at: str, chat_id: Optional[int] = None):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO reminders (user_id, target_user_id, reminder_text, remind_at, created_at, is_sent, chat_id)
            VALUES (?, ?, ?, ?, ?, 0, ?)
        ''', (user_id, target_user_id, reminder_text, remind_at, datetime.now().isoformat(), chat_id))
        conn.commit()
        conn.close()
    
    def get_pending_reminders(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Отримуємо поточний час в Київській timezone
        kyiv_tz = pytz.timezone('Europe/Kyiv')
        now_kyiv = datetime.now(kyiv_tz).isoformat()
        
        logger.info(f"📝 [get_pending_reminders] Перевіряємо нагадування. Поточний час (Київ): {now_kyiv}")
        
        cursor.execute('''
            SELECT id, user_id, target_user_id, reminder_text, remind_at, chat_id
            FROM reminders
            WHERE is_sent = 0 AND remind_at <= ?
        ''', (now_kyiv,))
        results = cursor.fetchall()
        conn.close()
        
        reminders_list = [{
            "id": r[0],
            "user_id": r[1],
            "target_user_id": r[2],
            "text": r[3],
            "remind_at": r[4],
            "chat_id": r[5]
        } for r in results]
        
        logger.info(f"✅ [get_pending_reminders] Знайдено {len(reminders_list)} нагадувань для відправки")
        
        return reminders_list
    
    def mark_reminder_sent(self, reminder_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE reminders SET is_sent = 1 WHERE id = ?', (reminder_id,))
        conn.commit()
        conn.close()
    
    def add_birthday(self, user_id: int, birth_date: str, added_by: int, username: str = "", full_name: str = ""):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO birthdays (user_id, username, full_name, birth_date, added_by, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, username, full_name, birth_date, added_by, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def get_all_birthdays(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT user_id, username, full_name, birth_date FROM birthdays ORDER BY birth_date
        ''')
        results = cursor.fetchall()
        conn.close()
        return [{"user_id": r[0], "username": r[1], "full_name": r[2], "birth_date": r[3]} for r in results]
    
    def get_todays_birthdays(self) -> List[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        today = datetime.now().strftime("%d.%m")
        cursor.execute('''
            SELECT user_id, username, full_name, birth_date
            FROM birthdays
            WHERE substr(birth_date, 1, 5) = ?
        ''', (today,))
        results = cursor.fetchall()
        conn.close()
        return [{"user_id": r[0], "username": r[1], "full_name": r[2], "birth_date": r[3]} for r in results]
    
    def get_birthday(self, user_id: int) -> Optional[str]:
        """Отримати дату народження користувача"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT birth_date FROM birthdays WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def delete_birthday(self, user_id: int) -> bool:
        """Видалити день народження користувача"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM birthdays WHERE user_id = ?', (user_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        conn.close()
        return deleted
    
    def set_birthday_gif(self, gif_file_id: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO birthday_settings (id, gif_file_id, greeting_text)
            VALUES (1, ?, (SELECT COALESCE(greeting_text, 'З Днем Народження!') FROM birthday_settings WHERE id = 1))
        ''', (gif_file_id,))
        conn.commit()
        conn.close()
    
    def set_birthday_text(self, greeting_text: str):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO birthday_settings (id, gif_file_id, greeting_text)
            VALUES (1, (SELECT gif_file_id FROM birthday_settings WHERE id = 1), ?)
        ''', (greeting_text,))
        conn.commit()
        conn.close()
    
    def get_birthday_settings(self) -> Dict:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT gif_file_id, greeting_text FROM birthday_settings WHERE id = 1')
        result = cursor.fetchone()
        conn.close()
        if result:
            return {"gif_file_id": result[0], "greeting_text": result[1]}
        return {"gif_file_id": None, "greeting_text": "З Днем Народження!"}
    
    def add_or_update_user(self, user_id: int, username: str = "", full_name: str = "", **kwargs):
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('SELECT user_id FROM users WHERE user_id = ?', (user_id,))
        exists = cursor.fetchone()
        
        if exists:
            update_fields = []
            update_values = []
            if username:
                update_fields.append('username = ?')
                update_values.append(username)
            if full_name:
                update_fields.append('full_name = ?')
                update_values.append(full_name)
            for key, value in kwargs.items():
                if value is not None:
                    update_fields.append(f'{key} = ?')
                    update_values.append(value)
            
            if update_fields:
                update_values.append(user_id)
                cursor.execute(f'UPDATE users SET {", ".join(update_fields)} WHERE user_id = ?', update_values)
        else:
            cursor.execute('''
                INSERT INTO users (user_id, username, full_name, joined_at)
                VALUES (?, ?, ?, ?)
            ''', (user_id, username, full_name, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
    
    def get_user(self, user_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        if result:
            return {
                "user_id": result[0],
                "username": result[1],
                "full_name": result[2],
                "first_message_at": result[3],
                "joined_at": result[4],
                "left_at": result[5],
                "invited_by": result[6],
                "invited_by_name": result[7],
                "invited_by_username": result[8],
                "birth_date": result[9]
            }
        return None
    
    def get_all_users(self) -> List[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT user_id FROM users WHERE user_id > 0')
        results = cursor.fetchall()
        conn.close()
        return [r[0] for r in results]
    
    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Отримати користувача за username (без @)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Спроба 1: Точний пошук
        cursor.execute('SELECT * FROM users WHERE LOWER(username) = LOWER(?)', (username,))
        result = cursor.fetchone()
        
        # Спроба 2: Якщо не знайдено - пошук з @ символом
        if not result:
            cursor.execute('SELECT * FROM users WHERE LOWER(username) = LOWER(?)', (f'@{username}',))
            result = cursor.fetchone()
        
        # Спроба 3: Якщо не знайдено - пошук частково (містить)
        if not result:
            cursor.execute('SELECT * FROM users WHERE LOWER(username) LIKE LOWER(?)', (f'%{username}%',))
            result = cursor.fetchone()
        
        conn.close()
        if result:
            return {
                "user_id": result[0],
                "username": result[1],
                "full_name": result[2],
                "first_message_at": result[3],
                "joined_at": result[4],
                "left_at": result[5],
                "invited_by": result[6],
                "invited_by_name": result[7],
                "invited_by_username": result[8],
                "birth_date": result[9]
            }
        return None
    
    def block_say_command(self, user_id: int, blocked_by: int, blocked_by_name: str = "", blocked_by_username: str = ""):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO say_blocks (user_id, blocked_by, blocked_at, blocked_by_name, blocked_by_username)
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, blocked_by, datetime.now().isoformat(), blocked_by_name, blocked_by_username))
        conn.commit()
        conn.close()
    
    def unblock_say_command(self, user_id: int):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM say_blocks WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
    
    def is_say_blocked(self, user_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1 FROM say_blocks WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return bool(result)
    
    def log_action(self, action_type: str, user_id: Optional[int] = None, target_user_id: Optional[int] = None, details: str = ""):
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO action_logs (action_type, user_id, target_user_id, details, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (action_type, user_id, target_user_id, details, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    
    def set_custom_name(self, user_id: int, custom_name: str) -> bool:
        """Встановити кастомне імʼя для користувача"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO custom_names (user_id, custom_name, set_at)
                VALUES (?, ?, ?)
            ''', (user_id, custom_name, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            return False
    
    def get_custom_name(self, user_id: int) -> Optional[str]:
        """Отримати кастомне імʼя користувача"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT custom_name FROM custom_names WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def delete_custom_name(self, user_id: int) -> bool:
        """Видалити кастомне імʼя"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM custom_names WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True
    
    def set_profile_picture(self, user_id: int, media_type: str, file_id: str) -> bool:
        """Встановити профіль-фото/гіфку"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO profile_pictures (user_id, media_type, file_id, set_at)
                VALUES (?, ?, ?, ?)
            ''', (user_id, media_type, file_id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            return False
    
    def get_profile_picture(self, user_id: int) -> Optional[Dict]:
        """Отримати профіль-фото/гіфку користувача"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT media_type, file_id FROM profile_pictures WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return {"media_type": result[0], "file_id": result[1]} if result else None
    
    def set_profile_description(self, user_id: int, description: str) -> bool:
        """Встановити опис профілю"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO profile_descriptions (user_id, description, set_at)
                VALUES (?, ?, ?)
            ''', (user_id, description, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            return False
    
    def get_profile_description(self, user_id: int) -> Optional[str]:
        """Отримати опис профілю"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT description FROM profile_descriptions WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def set_custom_position(self, user_id: int, position_title: str) -> bool:
        """Встановити кастомну посаду"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO custom_positions (user_id, position_title, set_at)
                VALUES (?, ?, ?)
            ''', (user_id, position_title, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            return False
    
    def get_custom_position(self, user_id: int) -> Optional[str]:
        """Отримати кастомну посаду"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT position_title FROM custom_positions WHERE user_id = ?', (user_id,))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def delete_custom_position(self, user_id: int) -> bool:
        """Видалити кастомну посаду"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM custom_positions WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True
    
    def delete_profile_picture(self, user_id: int) -> bool:
        """Видалити профіль-фото/гіфку"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM profile_pictures WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True
    
    def delete_profile_description(self, user_id: int) -> bool:
        """Видалити опис профілю"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM profile_descriptions WHERE user_id = ?', (user_id,))
        conn.commit()
        conn.close()
        return True
    
    # ===== CUSTOM COMMANDS =====
    
    def add_command_alias(self, chat_id: int, alias_name: str, target_command: str, created_by: int) -> bool:
        """Додати текстовий дублер команди"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO command_aliases (chat_id, alias_name, target_command, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (chat_id, alias_name.lower(), target_command, created_by, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except:
            return False
    
    def get_command_alias(self, chat_id: int, alias_name: str) -> Optional[str]:
        """Отримати команду за алайсом"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT target_command FROM command_aliases WHERE chat_id = ? AND alias_name = ?
        ''', (chat_id, alias_name.lower()))
        result = cursor.fetchone()
        conn.close()
        return result[0] if result else None
    
    def delete_command_alias(self, chat_id: int, alias_name: str) -> bool:
        """Видалити текстовий дублер команди за іменем дублера"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM command_aliases WHERE chat_id = ? AND alias_name = ?
        ''', (chat_id, alias_name.lower()))
        conn.commit()
        conn.close()
        return True
    
    def get_all_command_aliases(self, chat_id: int) -> list:
        """Отримати всі дублери команд для чату"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT alias_name, target_command FROM command_aliases WHERE chat_id = ?
            ORDER BY alias_name ASC
        ''', (chat_id,))
        results = cursor.fetchall()
        conn.close()
        return [{"alias": row[0], "command": row[1]} for row in results]
    
    def add_personal_command(self, chat_id: int, command_name: str, template_text: str, created_by: int) -> int:
        """Додати персональну команду, повертає command_id"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO personal_commands (chat_id, command_name, template_text, created_by, created_at)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, command_name.lower(), template_text, created_by, datetime.now().isoformat()))
        conn.commit()
        
        # Отримаємо ID команди
        cursor.execute('''
            SELECT id FROM personal_commands WHERE chat_id = ? AND command_name = ?
        ''', (chat_id, command_name.lower()))
        result = cursor.fetchone()
        command_id = result[0] if result else None
        conn.close()
        return command_id
    
    def get_personal_command(self, chat_id: int, command_name: str) -> Optional[Dict]:
        """Отримати персональну команду"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, template_text FROM personal_commands WHERE chat_id = ? AND command_name = ?
        ''', (chat_id, command_name.lower()))
        result = cursor.fetchone()
        conn.close()
        return {"id": result[0], "template": result[1]} if result else None
    
    def delete_personal_command(self, chat_id: int, command_name: str) -> bool:
        """Видалити персональну команду"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM personal_commands WHERE chat_id = ? AND command_name = ?
        ''', (chat_id, command_name.lower()))
        conn.commit()
        conn.close()
        return True
    
    def get_all_personal_commands(self, chat_id: int) -> List[Dict]:
        """Отримати всі персональні команди для чату"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT command_name, template_text, id FROM personal_commands WHERE chat_id = ? ORDER BY command_name
        ''', (chat_id,))
        results = cursor.fetchall()
        conn.close()
        return [{"name": r[0], "template": r[1], "id": r[2]} for r in results]
    
    def add_personal_command_media(self, command_id: int, media_type: str, file_id: str) -> bool:
        """Додати медіа до персональної команди (можна кілька)"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            # Просто додаємо нове медіа, без видалення старих!
            cursor.execute('''
                INSERT INTO personal_command_media (command_id, media_type, file_id, created_at)
                VALUES (?, ?, ?, ?)
            ''', (command_id, media_type, file_id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except:
            return False
    
    def get_personal_command_media(self, command_id: int) -> Optional[list]:
        """Отримати ВСІ медіа персональної команди (список)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, media_type, file_id FROM personal_command_media WHERE command_id = ? ORDER BY created_at
        ''', (command_id,))
        results = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "type": r[1], "file_id": r[2]} for r in results] if results else None
    
    def delete_personal_command_media(self, media_id: int) -> bool:
        """Видалити одне медіа з персональної команди"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM personal_command_media WHERE id = ?', (media_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False
    
    def add_admin_command_media(self, chat_id: int, command_name: str, media_type: str, file_id: str) -> bool:
        """Додати стікер/гіф до команди адміна"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO admin_command_media (chat_id, command_name, media_type, file_id, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (chat_id, command_name.lower(), media_type, file_id, datetime.now().isoformat()))
            conn.commit()
            conn.close()
            return True
        except:
            return False
    
    def get_admin_command_media(self, chat_id: int, command_name: str) -> Optional[list]:
        """Отримати ВСІ медіа команди адміна (стікери/гіф)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, media_type, file_id FROM admin_command_media 
            WHERE chat_id = ? AND command_name = ? ORDER BY created_at
        ''', (chat_id, command_name.lower()))
        results = cursor.fetchall()
        conn.close()
        return [{"id": r[0], "type": r[1], "file_id": r[2]} for r in results] if results else None
    
    def get_admin_command_by_file_id(self, chat_id: int, file_id: str) -> Optional[Dict]:
        """Знайти команду адміна за file_id стікера/гіф"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT command_name, media_type, id FROM admin_command_media 
            WHERE chat_id = ? AND file_id = ? LIMIT 1
        ''', (chat_id, file_id))
        result = cursor.fetchone()
        conn.close()
        return {"command": result[0], "type": result[1], "id": result[2]} if result else None
    
    def delete_admin_command_media(self, media_id: int) -> bool:
        """Видалити медіа з команди адміна"""
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM admin_command_media WHERE id = ?', (media_id,))
            conn.commit()
            conn.close()
            return True
        except:
            return False
    
    def export_all_backup(self) -> Dict[str, Any]:
        """Експортує ВСІ налаштування в словник для бекапу"""
        conn = self.get_connection()
        cursor = conn.cursor()
        backup = {}
        
        # Таблиці для експорту
        tables = [
            'roles', 'bans', 'mutes', 'blacklist', 'notes', 'reminders',
            'birthdays', 'birthday_settings', 'users', 'say_blocks',
            'custom_names', 'profile_pictures', 'profile_descriptions',
            'custom_positions', 'command_aliases', 'personal_commands',
            'personal_command_media', 'admin_command_media'
        ]
        
        for table in tables:
            try:
                cursor.execute(f'SELECT * FROM {table}')
                columns = [description[0] for description in cursor.description]
                rows = cursor.fetchall()
                backup[table] = {
                    'columns': columns,
                    'rows': [dict(zip(columns, row)) for row in rows]
                }
            except Exception as e:
                backup[table] = {'error': str(e)}
        
        conn.close()
        return backup
    
    def marry_users(self, user1_id: int, user2_id: int, user1_name: str = "", user2_name: str = ""):
        """Оформити шлюб між двома користувачами"""
        conn = self.get_connection()
        cursor = conn.cursor()
        # Убеждаемся, что user1_id < user2_id для уникальности
        if user1_id > user2_id:
            user1_id, user2_id = user2_id, user1_id
            user1_name, user2_name = user2_name, user1_name
        try:
            cursor.execute('''
                INSERT OR REPLACE INTO marriages (user1_id, user2_id, user1_name, user2_name, married_at, status)
                VALUES (?, ?, ?, ?, ?, 'married')
            ''', (user1_id, user2_id, user1_name, user2_name, datetime.now().isoformat()))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Помилка при оформленні шлюбу: {e}")
            return False
        finally:
            conn.close()
    
    def get_spouse(self, user_id: int) -> Optional[Dict]:
        """Отримати інформацію про чоловіка/дружину користувача"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Шукаємо шлюб де цей користувач або user1 або user2
        cursor.execute('''
            SELECT id, user1_id, user2_id, user1_name, user2_name, married_at, status
            FROM marriages
            WHERE (user1_id = ? OR user2_id = ?) AND status = 'married'
            LIMIT 1
        ''', (user_id, user_id))
        
        result = cursor.fetchone()
        conn.close()
        
        if result:
            user1_id, user2_id = result[1], result[2]
            spouse_id = user2_id if user1_id == user_id else user1_id
            spouse_name = result[4] if user1_id == user_id else result[3]
            return {
                "id": result[0],
                "spouse_id": spouse_id,
                "spouse_name": spouse_name,
                "married_at": result[5],
                "status": result[6]
            }
        return None
    
    def get_all_marriages(self) -> List[Dict]:
        """Отримати список всіх активних шлюбів"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT id, user1_id, user2_id, user1_name, user2_name, married_at
            FROM marriages
            WHERE status = 'married'
            ORDER BY married_at DESC
        ''')
        results = cursor.fetchall()
        conn.close()
        return [{
            "id": r[0],
            "user1_id": r[1],
            "user2_id": r[2],
            "user1_name": r[3],
            "user2_name": r[4],
            "married_at": r[5]
        } for r in results]
    
    def divorce_users(self, user1_id: int, user2_id: int):
        """Розірвати шлюб між користувачами"""
        conn = self.get_connection()
        cursor = conn.cursor()
        # Убеждаемся правильный порядок
        if user1_id > user2_id:
            user1_id, user2_id = user2_id, user1_id
        try:
            cursor.execute('''
                UPDATE marriages
                SET status = 'divorced', divorced_at = ?
                WHERE (user1_id = ? AND user2_id = ?) OR (user1_id = ? AND user2_id = ?)
            ''', (datetime.now().isoformat(), user1_id, user2_id, user2_id, user1_id))
            conn.commit()
            return cursor.rowcount > 0
        except Exception as e:
            logger.error(f"Помилка при розірванні шлюбу: {e}")
            return False
        finally:
            conn.close()

    def import_all_backup(self, backup_data: Dict[str, Any]) -> dict:
        """Імпортує налаштування з бекапу, повертає статистику"""
        stats = {
            'success': False,
            'tables': {},
            'total_records': 0,
            'error': None
        }
        
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            # Очищуємо всі таблиці
            tables = list(backup_data.keys())
            for table in tables:
                if table != 'sqlite_sequence':
                    cursor.execute(f'DELETE FROM {table}')
            
            # Імпортуємо дані
            for table, data in backup_data.items():
                if table == 'sqlite_sequence' or 'error' in data:
                    continue
                
                if not data.get('rows'):
                    stats['tables'][table] = 0
                    continue
                
                columns = data.get('columns', [])
                rows = data.get('rows', [])
                record_count = 0
                
                for row_dict in rows:
                    cols = list(row_dict.keys())
                    vals = list(row_dict.values())
                    placeholders = ', '.join(['?' for _ in cols])
                    col_names = ', '.join(cols)
                    cursor.execute(f'INSERT INTO {table} ({col_names}) VALUES ({placeholders})', vals)
                    record_count += 1
                
                stats['tables'][table] = record_count
                stats['total_records'] += record_count
            
            conn.commit()
            conn.close()
            stats['success'] = True
            return stats
        except Exception as e:
            stats['success'] = False
            stats['error'] = str(e)
            return stats
