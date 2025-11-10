import requests
import json
import telebot
from telebot import types
import io
import os
import threading
import time
from flask import Flask, request
import sqlite3
from datetime import datetime
import base64
import zipfile

# Конфигурация
API_KEY = "AIzaSyARZYE8kSTBVlGF_A1jxFdEQdVi5-9MN38"
SELECTED_MODEL = "gemini-2.5-flash"
CHANNEL_USERNAME = "@GeniAi"
ADMIN_ID = 2202291197
BOT_TOKEN = "2201851225:AAEruvQjAyxiYIcsVCwa-JoIcWaXMx4kqE8/test"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            subscribed INTEGER DEFAULT 0,
            requests_balance INTEGER DEFAULT 5,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            user_id INTEGER,
            action_type TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS requests_history (
            user_id INTEGER,
            requests_change INTEGER,
            reason TEXT,
            admin_id INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

user_states = {}

def keep_alive():
    while True:
        try:
            response = requests.get("https://one2-1-04er.onrender.com/", timeout=10)
            print(f"Keep-alive запрос отправлен: {response.status_code}")
        except Exception as e:
            print(f"Ошибка keep-alive: {e}")
        time.sleep(240)

def add_user(user_id, username, first_name, last_name):
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, last_name, requests_balance)
        VALUES (?, ?, ?, ?, 5)
    ''', (user_id, username, first_name, last_name))
    conn.commit()
    conn.close()

def update_subscription(user_id, subscribed):
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users SET subscribed = ? WHERE user_id = ?
    ''', (subscribed, user_id))
    conn.commit()
    conn.close()

def get_user_balance(user_id):
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('SELECT requests_balance FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else 0

def update_user_balance(user_id, new_balance):
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE users SET requests_balance = ? WHERE user_id = ?
    ''', (new_balance, user_id))
    conn.commit()
    conn.close()

def add_requests(user_id, amount, reason, admin_id=None):
    current_balance = get_user_balance(user_id)
    new_balance = current_balance + amount
    
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET requests_balance = ? WHERE user_id = ?', (new_balance, user_id))
    cursor.execute('''
        INSERT INTO requests_history (user_id, requests_change, reason, admin_id)
        VALUES (?, ?, ?, ?)
    ''', (user_id, amount, reason, admin_id))
    conn.commit()
    conn.close()
    return new_balance

def use_request(user_id):
    current_balance = get_user_balance(user_id)
    if current_balance > 0:
        new_balance = current_balance - 1
        update_user_balance(user_id, new_balance)
        return True, new_balance
    return False, current_balance

def add_stat(user_id, action_type):
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO stats (user_id, action_type) VALUES (?, ?)
    ''', (user_id, action_type))
    conn.commit()
    conn.close()

def check_subscription(user_id):
    try:
        chat_member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return chat_member.status in ['member', 'administrator', 'creator']
    except:
        return False

class GeminiChat:
    def __init__(self, model=SELECTED_MODEL):
        self.url = f"https://generativelanguage.googleapis.com/v1/models/{model}:generateContent?key={API_KEY}"
        self.headers = {'Content-Type': 'application/json'}
    
    def send_message(self, message, is_code_request=True, is_plugin_request=False, is_project_request=False, image_data=None):
        if is_plugin_request:
            prompt = f"""
            Создай Python плагин для exteragram. Запрос: {message}

            Формат плагина:
            __id__ = "уникальный_ид"
            __name__ = "Название плагина" 
            __description__ = "Описание плагина"
            __author__ = "@автор"
            __version__ = "1.0.0"
            __min_version__ = "11.12.0"

            from base_plugin import BasePlugin, MethodHook
            from hook_utils import find_class
            from java.lang import Long as JavaLong, Boolean as JavaBoolean

            class MyPlugin(BasePlugin):
                def on_plugin_load(self):
                    # код загрузки плагина

                def create_settings(self):
                    # настройки плагина
                    return []

            Создай полноценный рабочий плагин с комментариями.
            """
        elif is_project_request:
            prompt = f"""
            Создай полноценный Python проект для: {message}
            
            Требования:
            1. Создай несколько файлов с понятной структурой проекта
            2. Включи основной файл (main.py, app.py или аналогичный)
            3. Добавь README.md с инструкцией по установке и запуску
            4. Добавь requirements.txt если нужны внешние зависимости
            5. Создай логичную структуру папок если нужно
            6. Каждый файл должен быть полностью рабочим и содержать комментарии
            
            Формат ответа - каждый файл должен быть в отдельном блоке с указанием имени файла:
            
            ФАЙЛ: main.py
            ```python
            # код main.py
            ```
            
            ФАЙЛ: config.py
            ```python
            # код config.py
            ```
            
            ФАЙЛ: README.md
            ```markdown
            # описание проекта
            ```
            
            И так для всех файлов проекта.
            """
        elif is_code_request:
            prompt = f"Создай Python код для: {message}. Добавь комментарии и описание."
        else:
            prompt = f"Улучши код: {message['code']}. Запрос: {message['request']}. Сохрани функциональность."
        
        # Подготовка содержимого с изображением если есть
        contents = {"contents": [{"parts": [{"text": prompt}]}]}
        
        if image_data:
            contents["contents"][0]["parts"].insert(0, {
                "inline_data": {
                    "mime_type": "image/jpeg",
                    "data": image_data
                }
            })
        
        try:
            response = requests.post(self.url, headers=self.headers, json=contents, timeout=120)
            
            if response.status_code == 200:
                result = response.json()
                if 'candidates' in result and result['candidates']:
                    return result['candidates'][0]['content']['parts'][0]['text']
                return "❌ Ошибка: Пустой ответ от API"
            else:
                error_data = response.json()
                error_msg = error_data.get('error', {}).get('message', 'Неизвестная ошибка')
                return f"❌ Ошибка API ({response.status_code}): {error_msg}"
                
        except requests.exceptions.Timeout:
            return "❌ Время ожидания истекло. Попробуйте еще раз."
        except Exception as e:
            return f"❌ Ошибка соединения: {str(e)}"

def parse_code_response(response):
    try:
        # Пытаемся найти код между ```python и ```
        if '```python' in response:
            parts = response.split('```python')
            if len(parts) >= 2:
                code_part = parts[1].split('```')[0]
                description = parts[0].strip() if parts[0].strip() else "Сгенерированный код"
                return description, code_part.strip()
        
        # Пытаемся найти код между ``` и ```
        if '```' in response:
            parts = response.split('```')
            if len(parts) >= 3:
                code = parts[1].strip()
                description = parts[0].strip() if parts[0].strip() else "Сгенерированный код"
                return description, code
        
        # Если нет блоков кода, возвращаем весь ответ как код
        return "Сгенерированный код", response
        
    except Exception as e:
        return "Ошибка при разборе ответа", response

def parse_project_response(response):
    """Парсинг ответа с несколькими файлами проекта"""
    files = {}
    current_file = None
    current_content = []
    
    lines = response.split('\n')
    for line in lines:
        if line.startswith('ФАЙЛ:') or line.startswith('FILE:'):
            # Сохраняем предыдущий файл если есть
            if current_file and current_content:
                files[current_file] = '\n'.join(current_content).strip()
            
            # Начинаем новый файл
            current_file = line.split(':', 1)[1].strip()
            current_content = []
        elif line.startswith('```'):
            continue  # Пропускаем разделители кода
        elif current_file is not None:
            current_content.append(line)
    
    # Добавляем последний файл
    if current_file and current_content:
        files[current_file] = '\n'.join(current_content).strip()
    
    # Если не нашли структуру с ФАЙЛ:, пробуем найти блоки кода
    if not files:
        parts = response.split('```')
        for i in range(0, len(parts)-1, 2):
            if i+1 < len(parts):
                code_block = parts[i+1].strip()
                # Пытаемся определить имя файла из предыдущего текста
                prev_text = parts[i].strip()
                filename = "file_{}.py".format(i//2 + 1)
                if 'main' in prev_text.lower():
                    filename = "main.py"
                elif 'readme' in prev_text.lower():
                    filename = "README.md"
                elif 'requirement' in prev_text.lower():
                    filename = "requirements.txt"
                elif 'config' in prev_text.lower():
                    filename = "config.py"
                files[filename] = code_block
    
    return files

def create_zip_from_files(files):
    """Создает ZIP архив из словаря файлов"""
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zip_file:
        for filename, content in files.items():
            zip_file.writestr(filename, content)
    zip_buffer.seek(0)
    return zip_buffer

def process_image_message(message):
    """Обработка сообщения с изображением и текстом"""
    if message.text:
        return message.text, None
    
    caption = message.caption if message.caption else ""
    
    # Если это просто текстовое сообщение
    if not (message.photo or (message.document and message.document.mime_type.startswith('image/'))):
        return "", None
    
    image_file = None
    if message.photo:
        # Берем самое большое фото
        file_id = message.photo[-1].file_id
    elif message.document and message.document.mime_type.startswith('image/'):
        file_id = message.document.file_id
    else:
        return caption, None
    
    try:
        file_info = bot.get_file(file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        image_data = base64.b64encode(downloaded_file).decode('utf-8')
        return caption, image_data
    except Exception as e:
        print(f"Ошибка обработки изображения: {e}")
        return caption, None

@app.route('/')
def home():
    return "GeniAi Bot is running!"

@app.route('/health')
def health():
    return "OK"

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if update:
        bot.process_new_updates([telebot.types.Update.de_json(update)])
    return 'OK'

@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    add_user(user_id, username, first_name, last_name)
    if check_subscription(user_id):
        update_subscription(user_id, 1)
        show_main_menu(message)
    else:
        update_subscription(user_id, 0)
        show_subscription_request(message)

def show_subscription_request(message):
    markup = types.InlineKeyboardMarkup()
    subscribe_btn = types.InlineKeyboardButton('📢 Подписаться', url='https://t.me/GeniAi')
    check_btn = types.InlineKeyboardButton('✅ Проверить подписку', callback_data='check_subscription')
    markup.add(subscribe_btn)
    markup.add(check_btn)
    text = "📢 Подпишитесь на канал чтобы продолжить:\n\nhttps://t.me/GeniAi\n\nПосле подписки нажмите ✅ Проверить подписку"
    bot.send_message(message.chat.id, text, reply_markup=markup)

def show_main_menu(message):
    user_id = message.from_user.id
    balance = get_user_balance(user_id)
    markup = types.InlineKeyboardMarkup(row_width=1)
    btn1 = types.InlineKeyboardButton('💻 Написать код', callback_data='write_code')
    btn2 = types.InlineKeyboardButton('🚀 Собрать проект', callback_data='write_project')
    btn3 = types.InlineKeyboardButton('🔌 Написать плагин', callback_data='write_plugin')
    btn4 = types.InlineKeyboardButton('⚡ Изменить готовый', callback_data='modify_code')
    btn5 = types.InlineKeyboardButton('📊 Статистика', callback_data='stats')
    btn6 = types.InlineKeyboardButton('💎 Подписка', callback_data='subscription')
    btn7 = types.InlineKeyboardButton('👤 Автор бота', callback_data='author')
    if message.from_user.id == ADMIN_ID:
        btn8 = types.InlineKeyboardButton('👑 Админ панель', callback_data='admin_panel')
        markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8)
    else:
        markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7)
    welcome_text = f"""🤖 Привет, я GeniAi!

💰 Баланс: {balance} запросов

Выберите действие:"""
    bot.send_message(message.chat.id, welcome_text, reply_markup=markup)
    user_states[message.chat.id] = 'main_menu'

def show_subscription_info(message):
    user_id = message.from_user.id
    balance = get_user_balance(user_id)
    text = f"""💎 Информация о подписке

💰 У вас {balance} запросов

🛒 Купить запросы: @xostcodingkrytoy

📋 Для покупки отправьте админу:
- Ваш ID: {user_id}
- Количество запросов
- Скриншот оплаты

💳 1 запрос = 2 торта"""
    markup = types.InlineKeyboardMarkup()
    buy_btn = types.InlineKeyboardButton('🛒 Купить запросы', url='https://t.me/xostcodingkrytoy')
    back_btn = types.InlineKeyboardButton('🔙 Назад', callback_data='back_to_menu')
    markup.add(buy_btn)
    markup.add(back_btn)
    bot.send_message(message.chat.id, text, reply_markup=markup)

def show_admin_panel(message):
    stats = get_stats()
    text = f"""👑 Админ панель

📊 Статистика:
👥 Пользователей: {stats['total_users']}
💻 Кодов создано: {stats['codes_generated']}
🚀 Проектов создано: {stats['projects_generated']}
🔌 Плагинов создано: {stats['plugins_generated']}
⚡ Кодов изменено: {stats['codes_modified']}
📈 Всего запросов: {stats['total_requests']}

⚙️ Команды:
/request [id] [количество] - выдать запросы
/users - список пользователей"""
    bot.send_message(message.chat.id, text)

def get_stats():
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM stats WHERE action_type = "code_generated"')
    codes_generated = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM stats WHERE action_type = "plugin_generated"')
    plugins_generated = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM stats WHERE action_type = "code_modified"')
    codes_modified = cursor.fetchone()[0]
    cursor.execute('SELECT COUNT(*) FROM stats WHERE action_type = "project_generated"')
    projects_generated = cursor.fetchone()[0]
    cursor.execute('SELECT SUM(requests_balance) FROM users')
    total_requests = cursor.fetchone()[0] or 0
    conn.close()
    return {
        'total_users': total_users,
        'codes_generated': codes_generated,
        'plugins_generated': plugins_generated,
        'codes_modified': codes_modified,
        'projects_generated': projects_generated,
        'total_requests': total_requests
    }

@bot.message_handler(commands=['request'])
def handle_request_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    try:
        _, user_id, amount = message.text.split()
        user_id = int(user_id)
        amount = int(amount)
        new_balance = add_requests(user_id, amount, "Выдача админом", ADMIN_ID)
        try:
            user_message = f"""🎉 Спасибо за покупку!
📦 Вам выдано {amount} запросов
💰 Текущий баланс: {new_balance} запросов"""
            bot.send_message(user_id, user_message)
        except: pass
        bot.send_message(message.chat.id, f"✅ Пользователю {user_id} выдано {amount} запросов. Новый баланс: {new_balance}")
    except ValueError:
        bot.send_message(message.chat.id, "❌ Неправильный формат. Используйте: /request [id] [количество]")

@bot.message_handler(commands=['users'])
def handle_users_command(message):
    if message.from_user.id != ADMIN_ID:
        return
    conn = sqlite3.connect('bot_stats.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, first_name, requests_balance FROM users ORDER BY created_at DESC LIMIT 10')
    users = cursor.fetchall()
    conn.close()
    if not users:
        bot.send_message(message.chat.id, "❌ Пользователей нет")
        return
    text = "👥 Последние 10 пользователей:\n\n"
    for user in users:
        user_id, username, first_name, balance = user
        user_info = f"@{username}" if username else first_name
        text += f"🆔 {user_id} | 👤 {user_info} | 💰 {balance}\n"
    bot.send_message(message.chat.id, text)

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    if call.data == 'check_subscription':
        if check_subscription(user_id):
            update_subscription(user_id, 1)
            bot.answer_callback_query(call.id, "✅ Спасибо за подписку!")
            show_main_menu(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ Вы еще не подписались на канал!")
    elif check_subscription(user_id):
        if call.data == 'write_code':
            balance = get_user_balance(user_id)
            if balance <= 0:
                bot.answer_callback_query(call.id, "❌ У вас закончились запросы!")
                show_subscription_info(call.message)
            else:
                msg = bot.send_message(chat_id, "💻 Опишите какой код нужен:")
                user_states[chat_id] = 'waiting_code_request'
        elif call.data == 'write_project':
            balance = get_user_balance(user_id)
            if balance <= 0:
                bot.answer_callback_query(call.id, "❌ У вас закончились запросы!")
                show_subscription_info(call.message)
            else:
                msg = bot.send_message(chat_id, "🚀 Опишите какой проект нужен:")
                user_states[chat_id] = 'waiting_project_request'
        elif call.data == 'write_plugin':
            balance = get_user_balance(user_id)
            if balance <= 0:
                bot.answer_callback_query(call.id, "❌ У вас закончились запросы!")
                show_subscription_info(call.message)
            else:
                msg = bot.send_message(chat_id, "🔌 Опишите какой плагин нужен:")
                user_states[chat_id] = 'waiting_plugin_request'
        elif call.data == 'modify_code':
            balance = get_user_balance(user_id)
            if balance <= 0:
                bot.answer_callback_query(call.id, "❌ У вас закончились запросы!")
                show_subscription_info(call.message)
            else:
                msg = bot.send_message(chat_id, "⚡ Отправьте .py файл для изменения:")
                user_states[chat_id] = 'waiting_code_file'
        elif call.data == 'stats':
            stats = get_stats()
            user_balance = get_user_balance(user_id)
            stats_text = f"""📊 Статистика бота:

👥 Всего пользователей: {stats['total_users']}
💻 Создано кодов: {stats['codes_generated']}
🚀 Создано проектов: {stats['projects_generated']}
🔌 Создано плагинов: {stats['plugins_generated']}
⚡ Изменено кодов: {stats['codes_modified']}
💰 Ваш баланс: {user_balance} запросов"""
            bot.send_message(chat_id, stats_text)
        elif call.data == 'subscription':
            show_subscription_info(call.message)
        elif call.data == 'author':
            bot.send_message(chat_id, "👤 Автор бота: @xostcodingkrytoy")
        elif call.data == 'admin_panel':
            if user_id == ADMIN_ID:
                show_admin_panel(call.message)
        elif call.data == 'back_to_menu':
            show_main_menu(call.message)
    else:
        bot.answer_callback_query(call.id, "❌ Сначала подпишитесь на канал!")
        show_subscription_request(call.message)

@bot.message_handler(content_types=['photo', 'text'])
def handle_code_requests(message):
    if not check_subscription(message.from_user.id):
        show_subscription_request(message)
        return
        
    chat_id = message.chat.id
    state = user_states.get(chat_id)
    
    if state == 'waiting_code_request':
        process_code_request(message)
    elif state == 'waiting_project_request':
        process_project_request(message)
    elif state == 'waiting_plugin_request':
        process_plugin_request(message)
    elif state == 'waiting_modification_request':
        process_modification_request(message)

def process_code_request(message):
    if not check_subscription(message.from_user.id):
        show_subscription_request(message)
        return
        
    user_id = message.from_user.id
    success, new_balance = use_request(user_id)
    if not success:
        bot.send_message(message.chat.id, "❌ У вас закончились запросы! Нажмите на подписку чтобы купить новые")
        show_subscription_info(message)
        return
        
    chat_id = message.chat.id
    
    # Получаем текст и изображение
    user_request, image_data = process_image_message(message)
    
    # Проверяем есть ли текст
    if not user_request or user_request.strip() == "":
        bot.send_message(chat_id, "❌ Пожалуйста, добавьте описание к запросу")
        return
        
    if user_request.startswith('/'):
        show_main_menu(message)
        return
        
    processing_msg = bot.send_message(chat_id, "⏳ Код готовится...")
    
    try:
        gemini = GeminiChat()
        response = gemini.send_message(user_request, is_code_request=True, image_data=image_data)
        
        if response.startswith('❌'):
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_message(chat_id, response)
            add_requests(user_id, 1, "Возврат при ошибке")
        else:
            description, code = parse_code_response(response)
            file_buffer = io.BytesIO(code.encode('utf-8'))
            file_buffer.name = "generated_code.py"
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_document(chat_id, file_buffer, 
                             caption=f"✅ Готовый код\n\n📝 Описание:\n{description}\n\n💰 Осталось запросов: {new_balance}")
            user_states[chat_id] = 'main_menu'
            add_stat(user_id, "code_generated")
    except Exception as e:
        bot.delete_message(chat_id, processing_msg.message_id)
        bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
        add_requests(user_id, 1, "Возврат при ошибке")

def process_project_request(message):
    if not check_subscription(message.from_user.id):
        show_subscription_request(message)
        return
        
    user_id = message.from_user.id
    success, new_balance = use_request(user_id)
    if not success:
        bot.send_message(message.chat.id, "❌ У вас закончились запросы! Нажмите на подписку чтобы купить новые")
        show_subscription_info(message)
        return
        
    chat_id = message.chat.id
    
    # Получаем текст и изображение
    user_request, image_data = process_image_message(message)
    
    # Проверяем есть ли текст
    if not user_request or user_request.strip() == "":
        bot.send_message(chat_id, "❌ Пожалуйста, добавьте описание проекта")
        return
        
    if user_request.startswith('/'):
        show_main_menu(message)
        return
        
    processing_msg = bot.send_message(chat_id, "🚀 Собираю проект...")
    
    try:
        gemini = GeminiChat()
        response = gemini.send_message(user_request, is_project_request=True, image_data=image_data)
        
        if response.startswith('❌'):
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_message(chat_id, response)
            add_requests(user_id, 1, "Возврат при ошибке")
        else:
            files = parse_project_response(response)
            if not files:
                bot.delete_message(chat_id, processing_msg.message_id)
                bot.send_message(chat_id, "❌ Не удалось распознать структуру проекта. Попробуйте еще раз.")
                add_requests(user_id, 1, "Возврат при ошибке")
                return
            
            # Создаем ZIP архив
            zip_buffer = create_zip_from_files(files)
            zip_buffer.name = "project.zip"
            
            # Создаем описание файлов
            file_list = "\n".join([f"📄 {filename}" for filename in files.keys()])
            
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_document(chat_id, zip_buffer,
                             caption=f"🚀 Готовый проект!\n\n📁 Файлы в проекте:\n{file_list}\n\n💰 Осталось запросов: {new_balance}")
            user_states[chat_id] = 'main_menu'
            add_stat(user_id, "project_generated")
    except Exception as e:
        bot.delete_message(chat_id, processing_msg.message_id)
        bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
        add_requests(user_id, 1, "Возврат при ошибке")

def process_plugin_request(message):
    if not check_subscription(message.from_user.id):
        show_subscription_request(message)
        return
        
    user_id = message.from_user.id
    success, new_balance = use_request(user_id)
    if not success:
        bot.send_message(message.chat.id, "❌ У вас закончились запросы! Нажмите на подписку чтобы купить новые")
        show_subscription_info(message)
        return
        
    chat_id = message.chat.id
    
    # Получаем текст и изображение
    user_request, image_data = process_image_message(message)
    
    # Проверяем есть ли текст
    if not user_request or user_request.strip() == "":
        bot.send_message(chat_id, "❌ Пожалуйста, добавьте описание к запросу")
        return
        
    if user_request.startswith('/'):
        show_main_menu(message)
        return
        
    processing_msg = bot.send_message(chat_id, "⏳ Плагин готовится...")
    
    try:
        gemini = GeminiChat()
        response = gemini.send_message(user_request, is_code_request=False, is_plugin_request=True, image_data=image_data)
        
        if response.startswith('❌'):
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_message(chat_id, response)
            add_requests(user_id, 1, "Возврат при ошибке")
        else:
            description, code = parse_code_response(response)
            file_buffer = io.BytesIO(code.encode('utf-8'))
            file_buffer.name = "generated_plugin.plugin"
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_document(chat_id, file_buffer, 
                             caption=f"✅ Готовый плагин\n\n📝 Описание:\n{description}\n\n💰 Осталось запросов: {new_balance}")
            user_states[chat_id] = 'main_menu'
            add_stat(user_id, "plugin_generated")
    except Exception as e:
        bot.delete_message(chat_id, processing_msg.message_id)
        bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
        add_requests(user_id, 1, "Возврат при ошибке")

@bot.message_handler(content_types=['document'])
def handle_document(message):
    if not check_subscription(message.from_user.id):
        show_subscription_request(message)
        return
    chat_id = message.chat.id
    if user_states.get(chat_id) == 'waiting_code_file':
        if message.document.file_name and message.document.file_name.endswith('.py'):
            try:
                file_info = bot.get_file(message.document.file_id)
                downloaded_file = bot.download_file(file_info.file_path)
                code_content = downloaded_file.decode('utf-8')
                user_states[chat_id] = {'state': 'waiting_modification_request', 'code': code_content}
                
                # Если есть подпись к файлу, используем ее как запрос на изменение
                if message.caption:
                    process_modification_request(message)
                else:
                    bot.send_message(chat_id, "⚡ Что изменить в коде?")
            except Exception as e:
                bot.send_message(chat_id, f"❌ Ошибка при чтении файла: {str(e)}")
        else:
            bot.send_message(chat_id, "❌ Пожалуйста, отправьте именно Python файл (.py)")
    else:
        bot.send_message(chat_id, "❌ Сначала нажмите '⚡ Изменить готовый'")

def process_modification_request(message):
    if not check_subscription(message.from_user.id):
        show_subscription_request(message)
        return
        
    user_id = message.from_user.id
    success, new_balance = use_request(user_id)
    if not success:
        bot.send_message(message.chat.id, "❌ У вас закончились запросы! Нажмите на подписку чтобы купить новые")
        show_subscription_info(message)
        return
        
    chat_id = message.chat.id
    
    # Получаем текст и изображение
    modification_request, image_data = process_image_message(message)
    
    # Проверяем есть ли текст
    if not modification_request or modification_request.strip() == "":
        bot.send_message(chat_id, "❌ Пожалуйста, добавьте описание изменений")
        return
        
    user_data = user_states.get(chat_id, {})
    original_code = user_data.get('code', '')
    if not original_code:
        bot.send_message(chat_id, "❌ Не удалось найти исходный код. Попробуйте снова.")
        return
        
    processing_msg = bot.send_message(chat_id, "⏳ Вносятся изменения...")
    
    try:
        gemini = GeminiChat()
        request_data = {'code': original_code, 'request': modification_request}
        response = gemini.send_message(request_data, is_code_request=False, image_data=image_data)
        
        if response.startswith('❌'):
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_message(chat_id, response)
            add_requests(user_id, 1, "Возврат при ошибке")
        else:
            description, modified_code = parse_code_response(response)
            file_buffer = io.BytesIO(modified_code.encode('utf-8'))
            file_buffer.name = "modified_code.py"
            bot.delete_message(chat_id, processing_msg.message_id)
            bot.send_document(chat_id, file_buffer,
                             caption=f"✅ Измененный код\n\n📝 Что сделано:\n{description}\n\n💰 Осталось запросов: {new_balance}")
            user_states[chat_id] = 'main_menu'
            add_stat(user_id, "code_modified")
    except Exception as e:
        bot.delete_message(chat_id, processing_msg.message_id)
        bot.send_message(chat_id, f"❌ Ошибка: {str(e)}")
        add_requests(user_id, 1, "Возврат при ошибке")

@bot.message_handler(func=lambda message: True)
def handle_other_messages(message):
    if not check_subscription(message.from_user.id):
        show_subscription_request(message)
        return
    chat_id = message.chat.id
    if user_states.get(chat_id) not in ['waiting_code_request', 'waiting_project_request', 'waiting_plugin_request', 'waiting_code_file', 'waiting_modification_request']:
        show_main_menu(message)

def start_keep_alive():
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    print("Keep-alive запущен")

if __name__ == "__main__":
    start_keep_alive()
    bot.remove_webhook()
    port = int(os.environ.get('PORT', 10000))
    print(f"Bot starting on port {port}")
    try:
        WEBHOOK_URL = "https://one2-1-04er.onrender.com/webhook"
        bot.set_webhook(url=WEBHOOK_URL)
        print(f"Webhook установлен: {WEBHOOK_URL}")
        app.run(host='0.0.0.0', port=port, debug=False)
    except Exception as e:
        print(f"Используем поллинг... Ошибка: {e}")
        bot.infinity_polling()
