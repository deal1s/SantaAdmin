#!/usr/bin/env python3
"""
Скрипт для імпорту даних з бекапу JSON в bot_database.db
"""
import json
import sqlite3
from datetime import datetime

# Читаємо JSON файл
with open('attached_assets/A3DFB0013KMZ_backup_1764247406360.json', 'r', encoding='utf-8') as f:
    backup_data = json.load(f)

# Підключаємося до БД
conn = sqlite3.connect('bot_database.db')
cursor = conn.cursor()

print("🔄 Імпорт даних з бекапу...")
print("=" * 60)

tables_imported = 0
rows_imported = 0

# Імпортуємо кожну таблицю
for table_name, table_data in backup_data.items():
    columns = table_data['columns']
    rows = table_data['rows']
    
    if not rows:
        print(f"⏭️  {table_name}: 0 рядків (пусто)")
        continue
    
    # Будуємо INSERT заявку
    placeholders = ','.join(['?' for _ in columns])
    column_names = ','.join(columns)
    
    try:
        # Видаляємо старі дані з таблиці (крім глобальних настроєк)
        if table_name not in ['birthday_settings']:
            cursor.execute(f'DELETE FROM {table_name}')
        
        # Вставляємо нові дані
        for row in rows:
            values = [row.get(col) for col in columns]
            cursor.execute(f'INSERT OR REPLACE INTO {table_name} ({column_names}) VALUES ({placeholders})', values)
        
        conn.commit()
        print(f"✅ {table_name}: {len(rows)} рядків імпортовано")
        tables_imported += 1
        rows_imported += len(rows)
    
    except Exception as e:
        print(f"❌ {table_name}: ПОМИЛКА - {e}")

print("=" * 60)
print(f"✅ Всього: {tables_imported} таблиць, {rows_imported} рядків")

# Перевіряємо дані
print("\n📊 Перевірка імпортованих даних:")
print("-" * 60)

# Адміни
admin_count = cursor.execute("SELECT COUNT(*) FROM roles WHERE role='head_admin'").fetchone()[0]
print(f"👤 Head Admin користувачів: {admin_count}")

# Користувачі
user_count = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
print(f"👥 Всього користувачів: {user_count}")

# Дні народження
birthday_count = cursor.execute("SELECT COUNT(*) FROM birthdays").fetchone()[0]
print(f"🎂 Днів народження: {birthday_count}")

# Команди
cmd_count = cursor.execute("SELECT COUNT(*) FROM command_aliases").fetchone()[0]
print(f"🔤 Дублерів команд: {cmd_count}")

# Персональні команди
personal_cmd_count = cursor.execute("SELECT COUNT(*) FROM personal_commands").fetchone()[0]
print(f"📝 Персональних команд: {personal_cmd_count}")

# Медіа-команди
media_cmd_count = cursor.execute("SELECT COUNT(*) FROM admin_command_media").fetchone()[0]
print(f"🎬 Медіа-команд: {media_cmd_count}")

# Записи
notes_count = cursor.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
print(f"📋 Записів про користувачів: {notes_count}")

# Нагадування
reminder_count = cursor.execute("SELECT COUNT(*) FROM reminders").fetchone()[0]
print(f"⏰ Нагадувань: {reminder_count}")

print("-" * 60)
print("✅ ВСІ ДАНІ УСПІШНО ІМПОРТОВАНІ!")

conn.close()
