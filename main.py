#!/usr/bin/env python3
"""
SANTA ADMIN BOT - Main Entry Point

Запуск: python3 main.py або python main.py

Вимоги:
- BOT_TOKEN в .env файлі
- config/config.json налаштований
"""
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from bot import main

if __name__ == '__main__':
    main()
