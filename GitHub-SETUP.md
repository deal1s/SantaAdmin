# 🤖 Santa Admin Bot - GitHub Setup Guide

Покрокова інструкція для запуску бота на будь-якому сервері з Python.

## 📋 Передумови

- Python 3.9+
- pip (менеджер пакетів Python)
- Telegram Bot Token (від @BotFather)
- Доступ до серверу/Replit/VPS

## 🚀 Етап 1: Клонування та Підготовка

### 1.1 Клонуй репозиторій
```bash
git clone https://github.com/YOUR_USERNAME/santa-admin-bot.git
cd santa-admin-bot
```

### 1.2 Створи віртуальне середовище (опціонально, але рекомендується)
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# або
venv\Scripts\activate  # Windows
```

### 1.3 Встанови залежності
```bash
pip install -r requirements.txt
```

## 🔑 Етап 2: Налаштування

### 2.1 Створи .env файл
```bash
cp .env.example .env
```

### 2.2 Заповни .env своїм Bot Token
```
BOT_TOKEN=1234567890:ABCDEFGHijklmnopqrstuvwxyz
```

**Як отримати Bot Token:**
1. Напиши @BotFather в Telegram
2. Натисни /newbot
3. Дай боту ім'я та username
4. Скопіюй отриманий токен у .env

### 2.3 Налаштуй config.json
```json
{
  "ADMIN_CHAT_ID": -1002496348691,
  "USER_CHAT_ID": -1002646171857,
  "LOG_CHANNEL_ID": -1002863334815,
  "NOTES_CHANNEL_ID": -1002477496414,
  "TEST_CHANNEL_ID": -1002863334815,
  "OWNER_IDS": [7247114478],
  "MESSAGE_DELETE_TIMER": 5
}
```

**Як знайти Chat/Channel ID:**
- Додай бота в групу/канал
- Напиши `/adminchat`, `/userchat` тощо - бот виведе ID
- Або використай @userinfobot для пошуку ID

## 📝 Етап 3: Перше Запущення

### 3.1 Запусти бота
```bash
python bot.py
```

### 3.2 Перевір логи
```
🤖 Бот запущено!
```

Якщо бачиш цей текст - все ОК! ✅

### 3.3 Тестування в Telegram
1. Напиши `@YOUR_BOT_USERNAME /start`
2. Скопіюй команду `/adminchat`, `/userchat` для отримання ID чатів
3. Оновлюй config.json з отриманими ID

## 🌐 Етап 4: Розгортування на Сервері

### 4.1 Replit (EASIEST)
```bash
# 1. На Replit просто завантаж файли
# 2. Натисни "Run" 
# 3. Боті інакше розпочниться!
```

### 4.2 VPS / Dedicated Server

**Linux (Ubuntu/Debian):**
```bash
# Оновлюємо систему
sudo apt update && sudo apt upgrade -y
sudo apt install python3-pip python3-venv -y

# Клонуємо
git clone https://github.com/YOUR_USERNAME/santa-admin-bot.git
cd santa-admin-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Створюємо systemd сервіс для автозапуску
sudo nano /etc/systemd/system/bot.service
```

**Вміст bot.service:**
```ini
[Unit]
Description=Santa Admin Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/santa-admin-bot
Environment="BOT_TOKEN=your_token_here"
ExecStart=/home/ubuntu/santa-admin-bot/venv/bin/python bot.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Запуск:**
```bash
sudo systemctl daemon-reload
sudo systemctl enable bot
sudo systemctl start bot
sudo systemctl status bot
```

### 4.3 Docker (якщо потрібен контейнер)
```bash
# Створи Dockerfile
docker build -t santa-bot .
docker run -d --env-file .env santa-bot
```

## 📊 База Даних

Перший запуск автоматично створює `bot_database.db` з усіма таблицями.

**Резервна копія БД:**
```bash
cp bot_database.db bot_database.db.backup
```

## 🛠️ Команди Власника

```
/restart - перезапустити бота
/adminchat <ID> - встановити адмін-чат
/userchat <ID> - встановити користувацький чат
/logchannel <ID> - встановити канал логування
/deltimer <1-60> - встановити таймер видалення відповідей
```

## 🐛 Troubleshooting

### Помилка: "Chat not found"
- Додай бота в потрібну групу/канал
- Переконайся що ID в config.json правильний
- Попроси будь-кого кинути посилання на групу

### Помилка: "Unauthorized"
- Перевір BOT_TOKEN в .env
- Токен не повинен мати пробільні або додаткові символи

### Бот не вітає днями народження
- Перевір часовий пояс: за замовчуванням Київськ (Europe/Kyiv)
- Перевір що в БД додані дні народження (`/addb`)
- Перевір логи: `/previewb` для тесту

### База даних повільна
- Це нормально для SQLite при 1000+ користувачах
- Для великих ботів розглянь PostgreSQL

## 📚 Документація

- [Реджим команд](/replit.md)
- [API Telegram](https://core.telegram.org/api)
- [python-telegram-bot](https://python-telegram-bot.readthedocs.io/)

## 🤝 Дякуємо!

Якщо тобі подобається бот - залиш ⭐ на GitHub!

## 📞 Контакти

- **Власник**: @dont_luck (ID: 7247114478)
- **GitHub**: [посилання на репо]

---

**Версія**: 1.0  
**Оновлено**: Листопад 2025
