import os
import json
import random
import string
import time
import sqlite3
import threading
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from telebot import TeleBot, types
from functools import wraps
import logging
from logging.handlers import RotatingFileHandler
from contextlib import contextmanager
import shutil
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'super_secret_key_123'
bot = TeleBot('5001581220:AAFMdu68XwJ1sk_HNxt0aps5rlp8cKhoRk4/test')

handler = RotatingFileHandler('app.log', maxBytes=10000, backupCount=3)
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)

pending_transfers = {}
CHANNEL_ID = -1002200380076

@contextmanager
def get_db_connection():
    conn = sqlite3.connect('database.db', timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT is_blocked FROM users WHERE id = ?", (session['user_id'],))
            user = c.fetchone()
            
            if user and user['is_blocked']:
                return redirect(url_for('blocked'))
        
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def init_db():
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("ALTER TABLE users ADD COLUMN is_blocked BOOLEAN DEFAULT FALSE")
        except sqlite3.OperationalError:
            pass
        
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            username TEXT NOT NULL,
            balance INTEGER DEFAULT 10000000,
            is_blocked BOOLEAN DEFAULT FALSE
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS gifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            improved_image TEXT,
            improved INTEGER DEFAULT 0,
            collection_number INTEGER DEFAULT NULL,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS market (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gift_id INTEGER UNIQUE NOT NULL,
            price INTEGER NOT NULL,
            seller_id INTEGER NOT NULL,
            FOREIGN KEY (gift_id) REFERENCES gifts (id),
            FOREIGN KEY (seller_id) REFERENCES users (id)
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS login_codes (
            code TEXT PRIMARY KEY,
            tg_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS product_stats (
            product_id INTEGER PRIMARY KEY,
            bought INTEGER DEFAULT 0
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS price_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            min_price INTEGER DEFAULT 0,
            max_price INTEGER DEFAULT 1000000000
        )''')
        
        c.execute("INSERT OR IGNORE INTO price_settings (min_price, max_price) VALUES (0, 1000000000)")
        
        c.execute('''CREATE TABLE IF NOT EXISTS auctions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gift_id INTEGER UNIQUE NOT NULL,
            seller_id INTEGER NOT NULL,
            start_price INTEGER NOT NULL,
            current_price INTEGER NOT NULL,
            current_bidder_id INTEGER DEFAULT NULL,
            step_price INTEGER DEFAULT 100,
            end_time DATETIME NOT NULL,
            status TEXT DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (gift_id) REFERENCES gifts (id),
            FOREIGN KEY (seller_id) REFERENCES users (id),
            FOREIGN KEY (current_bidder_id) REFERENCES users (id)
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS auction_bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            auction_id INTEGER NOT NULL,
            bidder_id INTEGER NOT NULL,
            bid_amount INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (auction_id) REFERENCES auctions (id),
            FOREIGN KEY (bidder_id) REFERENCES users (id)
        )''')
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_users_tg_id ON users(tg_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_gifts_user_id ON gifts(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_market_seller_id ON market(seller_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_market_gift_id ON market(gift_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_login_codes_created ON login_codes(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_auctions_status ON auctions(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_auctions_end_time ON auctions(end_time)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_auction_bids_auction_id ON auction_bids(auction_id)")
        
        conn.commit()

init_db()

products_cache = []
products_cache_time = 0

def load_products_cached():
    global products_cache, products_cache_time
    
    if not products_cache or time.time() - products_cache_time > 60:
        try:
            with open('products.json', 'r', encoding='utf-8') as f:
                products_cache = json.load(f)
                for p in products_cache:
                    if 'limit' in p:
                        p['remaining'] = p['limit']
                    
                    if 'improve_folder' in p and p['improve_folder']:
                        improve_path = os.path.join(app.static_folder, 'images', p['improve_folder'])
                        if not os.path.exists(improve_path):
                            os.makedirs(improve_path)
                            app.logger.info(f"Создана папка для улучшений: {improve_path}")
                products_cache_time = time.time()
        except Exception as e:
            app.logger.error(f"Ошибка загрузки products.json: {e}")
            return []
    
    return products_cache

def save_products(products):
    try:
        with open('products.json', 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=4)
        global products_cache_time
        products_cache_time = 0
        return True
    except Exception as e:
        app.logger.error(f"Ошибка сохранения products.json: {e}")
        return False

def generate_code():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def init_product_stats():
    with get_db_connection() as conn:
        c = conn.cursor()
        products = load_products_cached()
        for p in products:
            c.execute("INSERT OR IGNORE INTO product_stats (product_id) VALUES (?)", (p['id'],))
        conn.commit()

init_product_stats()

def send_telegram_message(tg_id, message):
    try:
        bot.send_message(tg_id, message, parse_mode='HTML')
        return True
    except Exception as e:
        app.logger.error(f"Ошибка отправки сообщения пользователю {tg_id}: {e}")
        return False

def send_channel_notification(product, improvement_count=None):
    try:
        if improvement_count is None:
            message = f"""🎁 <b>Новый подарок:</b>

💼 <b>Название:</b> {product['name']}
🤝 <b>Стоимость:</b> {product['price']}⭐
🏦 <b>Количество:</b> {product.get('limit', 'Безлимитно')}

🎳 <a href="https://t.me/IFragmentBot?start=start">Забрать</a>"""
        else:
            message = f"""🔥 <b>Новое улучшение для подарка:</b>

💼 <b>Название:</b> {product['name']}
🎁 <b>Количество улучшений:</b> {improvement_count}

🎳 <a href="https://t.me/IFragmentBot?start=start">Забрать</a>"""
        
        bot.send_message(CHANNEL_ID, message, parse_mode='HTML')
        return True
    except Exception as e:
        app.logger.error(f"Ошибка отправки уведомления в канал: {e}")
        return False

@bot.message_handler(commands=['start'])
def start_bot(message):
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("SELECT * FROM users WHERE tg_id = ?", (message.chat.id,))
            user = c.fetchone()
            
            if not user:
                username = message.from_user.username or f"User{message.chat.id}"
                c.execute("INSERT INTO users (tg_id, username) VALUES (?, ?)", 
                         (message.chat.id, username))
                conn.commit()
                app.logger.info(f"Создан новый пользователь: {username} ({message.chat.id})")
            
            code = generate_code()
            c.execute("INSERT INTO login_codes (code, tg_id) VALUES (?, ?)", (code, message.chat.id))
            conn.commit()
            
            bot.send_message(
                message.chat.id,
                f"🔑 <b>Ваш код для входа на сайт:\n\n"
                f"[<code>{code}</code>]\n\n"
                "Просто нажмите на код, чтобы скопировать его.\n"
                "Используйте код на сайте в течение 10 минут.</b>",
                parse_mode='HTML'
            )
        except Exception as e:
            app.logger.error(f"Ошибка в start_bot: {e}")
            bot.reply_to(message, "Произошла ошибка, попробуйте снова")

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.data.startswith('transfer_confirm_'):
        transfer_key = call.data.replace('transfer_confirm_', '')
        confirm_transfer(call, transfer_key)
    elif call.data.startswith('transfer_cancel_'):
        transfer_key = call.data.replace('transfer_cancel_', '')
        cancel_transfer(call, transfer_key)

def confirm_transfer(call, transfer_key):
    if transfer_key not in pending_transfers:
        bot.answer_callback_query(call.id, "Запрос на передачу устарел или не найден.")
        return
    
    transfer_data = pending_transfers[transfer_key]
    
    with get_db_connection() as conn:
        c = conn.cursor()
        try:
            c.execute("SELECT id FROM users WHERE tg_id = ?", (transfer_data['recipient_id'],))
            recipient = c.fetchone()
            
            if not recipient:
                bot.answer_callback_query(call.id, "Получатель не найден.")
                return
            
            c.execute("UPDATE gifts SET user_id = ? WHERE id = ?", 
                     (recipient['id'], transfer_data['gift_id']))
            
            recipient_message = f"🎁 Вы получили подарок <b>{transfer_data['product_name']}</b> от пользователя <b>{transfer_data['sender_username']}</b>!"
            send_telegram_message(transfer_data['recipient_id'], recipient_message)
            
            confirm_success_message = f"✅ Подарок <b>{transfer_data['product_name']}</b> успешно передан пользователю <b>{transfer_data['recipient_username']}</b>!"
            bot.send_message(transfer_data['sender_tg_id'], confirm_success_message, parse_mode='HTML')
            
            conn.commit()
            
            del pending_transfers[transfer_key]
            
            bot.answer_callback_query(call.id, "Подарок успешно передан!")
        except Exception as e:
            app.logger.error(f"Ошибка при подтверждении передачи: {e}")
            bot.answer_callback_query(call.id, "Произошла ошибка при передаче подарка.")

def cancel_transfer(call, transfer_key):
    if transfer_key in pending_transfers:
        transfer_data = pending_transfers[transfer_key]
        bot.send_message(transfer_data['sender_tg_id'], "❌ Передача подарка отменена.", parse_mode='HTML')
        del pending_transfers[transfer_key]
    bot.answer_callback_query(call.id, "Передача отменена.")

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('home'))
    
    if request.method == 'POST':
        code_input = request.form.get('code', '').strip()
        
        if code_input == "kolosart78":
            session['admin'] = True
            return redirect(url_for('admin_panel'))
        
        code = code_input.upper()
        
        with get_db_connection() as conn:
            c = conn.cursor()
            try:
                c.execute("SELECT tg_id FROM login_codes WHERE code = ? AND created_at > datetime('now', '-10 minutes')", (code,))
                result = c.fetchone()
                
                if result:
                    tg_id = result['tg_id']
                    c.execute("SELECT * FROM users WHERE tg_id = ?", (tg_id,))
                    user = c.fetchone()
                    
                    if user:
                        if user['is_blocked']:
                            return render_template('login.html', error="Ваш аккаунт заблокирован. Обратитесь к администрации.")
                        
                        session['user_id'] = user['id']
                        session['username'] = user['username']
                        session['balance'] = user['balance']
                        return redirect(url_for('home'))
                
                return render_template('login.html', error="Неверный или просроченный код")
            except Exception as e:
                app.logger.error(f"Ошибка в login: {e}")
                return render_template('login.html', error="Ошибка сервера")
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/blocked')
def blocked():
    return render_template('blocked.html')

@app.route('/home')
@login_required
def home():
    products = load_products_cached()
    products_dict = {p['id']: p for p in products}
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT id, product_id, improved, improved_image, collection_number
                FROM gifts 
                WHERE user_id = ? AND id NOT IN (SELECT gift_id FROM market)
            """, (session['user_id'],))
            
            gifts = []
            for row in c.fetchall():
                gift_id, product_id, improved, improved_image, collection_number = row
                product = products_dict.get(product_id)
                if product:
                    if improved and improved_image:
                        image = f"images/{improved_image}"
                    else:
                        image = f"images/{product['image']}"
                    
                    can_improve = False
                    if 'improve_folder' in product and product['improve_folder']:
                        improve_path = os.path.join(app.static_folder, 'images', product['improve_folder'])
                        if os.path.exists(improve_path):
                            images = [f for f in os.listdir(improve_path) 
                                     if os.path.isfile(os.path.join(improve_path, f))]
                            can_improve = len(images) > 0
                    
                    gifts.append({
                        'id': gift_id,
                        'name': product['name'],
                        'image': image,
                        'improved': improved,
                        'collection_number': collection_number,
                        'can_improve': can_improve
                    })
                else:
                    app.logger.warning(f"Подарок {gift_id} ссылается на несуществующий продукт {product_id}")
            
            return render_template('home.html', 
                                 username=session['username'],
                                 balance=session['balance'],
                                 gifts=gifts)
        except Exception as e:
            app.logger.error(f"Ошибка в home: {e}")
            return "Ошибка загрузки данных", 500

@app.route('/shop')
@login_required
def shop():
    try:
        products = load_products_cached()
        with get_db_connection() as conn:
            c = conn.cursor()
            for p in products:
                c.execute("SELECT bought FROM product_stats WHERE product_id = ?", (p['id'],))
                result = c.fetchone()
                bought = result['bought'] if result else 0
                
                if 'limit' in p:
                    p['remaining'] = p['limit'] - bought
                    p['bought'] = bought
        
        return render_template('shop.html', products=products)
    except Exception as e:
        app.logger.error(f"Ошибка загрузки магазина: {e}")
        return "Ошибка загрузки магазина", 500

@app.route('/trading')
@login_required
def trading():
    products = load_products_cached()
    products_dict = {p['id']: p for p in products}
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT market.id, gifts.product_id, gifts.improved, gifts.improved_image, gifts.collection_number, market.price, users.username
                FROM market
                JOIN gifts ON market.gift_id = gifts.id
                JOIN users ON market.seller_id = users.id
                WHERE market.seller_id != ?
            """, (session['user_id'],))
            
            all_items = []
            for item in c.fetchall():
                item_id, product_id, improved, improved_image, collection_number, price, seller = item
                product = products_dict.get(product_id)
                if product:
                    if improved and improved_image:
                        image = f"images/{improved_image}"
                    else:
                        image = f"images/{product['image']}"
                    all_items.append({
                        'id': item_id,
                        'name': product['name'],
                        'image': image,
                        'improved': improved,
                        'collection_number': collection_number,
                        'price': price,
                        'seller': seller,
                        'is_own': False
                    })
                else:
                    app.logger.warning(f"Товар {item_id} ссылается на несуществующий продукт {product_id}")
            
            c.execute("""
                SELECT market.id, gifts.product_id, gifts.improved, gifts.improved_image, gifts.collection_number, market.price
                FROM market
                JOIN gifts ON market.gift_id = gifts.id
                WHERE market.seller_id = ?
            """, (session['user_id'],))
            
            own_items = []
            for item in c.fetchall():
                item_id, product_id, improved, improved_image, collection_number, price = item
                product = products_dict.get(product_id)
                if product:
                    if improved and improved_image:
                        image = f"images/{improved_image}"
                    else:
                        image = f"images/{product['image']}"
                    own_items.append({
                        'id': item_id,
                        'name': product['name'],
                        'image': image,
                        'improved': improved,
                        'collection_number': collection_number,
                        'price': price,
                        'seller': session['username'],
                        'is_own': True
                    })
            
            items = all_items + own_items
            return render_template('trading.html', items=items)
        except Exception as e:
            app.logger.error(f"Ошибка в trading: {e}")
            return "Ошибка загрузки торговой площадки", 500

@app.route('/buy_product', methods=['POST'])
@login_required
def buy_product():
    data = request.get_json()
    product_id = data.get('product_id')
    
    products = load_products_cached()
    product = next((p for p in products if p['id'] == product_id), None)
    if not product:
        return jsonify({'success': False, 'error': 'Product not found'}), 404
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("SELECT id FROM users WHERE id = ?", (session['user_id'],))
            user_exists = c.fetchone()
            if not user_exists:
                session.clear()
                return jsonify({
                    'success': False, 
                    'error': 'User session expired. Please login again.'
                }), 404
                
            c.execute("SELECT balance FROM users WHERE id = ?", (session['user_id'],))
            result = c.fetchone()
            if not result:
                return jsonify({'success': False, 'error': 'User not found'}), 404
                
            balance = result['balance']
            
            if balance < product['price']:
                return jsonify({'success': False, 'error': 'Insufficient funds'}), 400
            
            if 'limit' in product:
                c.execute("SELECT bought FROM product_stats WHERE product_id = ?", (product_id,))
                result = c.fetchone()
                current_bought = result['bought'] if result else 0
                
                remaining = product['limit'] - current_bought
                if remaining <= 0:
                    return jsonify({'success': False, 'error': 'Product limit reached'}), 400
            
            new_balance = balance - product['price']
            c.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, session['user_id']))
            
            c.execute("INSERT INTO gifts (user_id, product_id) VALUES (?, ?)", 
                     (session['user_id'], product_id))
            
            if 'limit' in product:
                c.execute("INSERT OR IGNORE INTO product_stats (product_id, bought) VALUES (?, 0)", (product_id,))
                c.execute("UPDATE product_stats SET bought = bought + 1 WHERE product_id = ?", (product_id,))
            
            conn.commit()
            session['balance'] = new_balance
            
            updated_remaining = None
            if 'limit' in product:
                c.execute("SELECT bought FROM product_stats WHERE product_id = ?", (product_id,))
                result = c.fetchone()
                updated_bought = result['bought'] if result else 0
                updated_remaining = product['limit'] - updated_bought
            
            return jsonify({
                'success': True, 
                'new_balance': new_balance,
                'remaining': updated_remaining
            })
        except Exception as e:
            app.logger.error(f"Ошибка в buy_product: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/put_on_market', methods=['POST'])
@login_required
def put_on_market():
    data = request.get_json()
    gift_id = data.get('gift_id')
    price = data.get('price')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("SELECT min_price, max_price FROM price_settings WHERE id = 1")
            min_price, max_price = c.fetchone()
            
            if price < min_price or price > max_price:
                return jsonify({
                    'success': False,
                    'error': f'Цена должна быть между {min_price} и {max_price}'
                }), 400
            
            c.execute("SELECT user_id FROM gifts WHERE id = ?", (gift_id,))
            result = c.fetchone()
            
            if not result or result['user_id'] != session['user_id']:
                return jsonify({'success': False, 'error': 'Invalid gift'}), 400
            
            c.execute("INSERT INTO market (gift_id, price, seller_id) VALUES (?, ?, ?)", 
                     (gift_id, price, session['user_id']))
            
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в put_on_market: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/buy_from_market', methods=['POST'])
@login_required
def buy_from_market():
    data = request.get_json()
    item_id = data.get('item_id')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT market.price, market.seller_id, market.gift_id, gifts.product_id
                FROM market 
                JOIN gifts ON market.gift_id = gifts.id
                WHERE market.id = ?
            """, (item_id,))
            item = c.fetchone()
            
            if not item:
                return jsonify({'success': False, 'error': 'Item not found'}), 404
            
            price, seller_id, gift_id, product_id = item
            
            c.execute("SELECT balance FROM users WHERE id = ?", (session['user_id'],))
            result = c.fetchone()
            if not result:
                return jsonify({'success': False, 'error': 'User not found'}), 404
                
            buyer_balance = result['balance']
            
            if buyer_balance < price:
                return jsonify({'success': False, 'error': 'Insufficient funds'}), 400
            
            products = load_products_cached()
            product = next((p for p in products if p['id'] == product_id), None)
            product_name = product['name'] if product else "Неизвестный продукт"
            
            c.execute("SELECT tg_id, username FROM users WHERE id = ?", (seller_id,))
            seller_info = c.fetchone()
            seller_tg_id = seller_info['tg_id'] if seller_info else None
            seller_username = seller_info['username'] if seller_info else "Неизвестный продавец"
            
            c.execute("UPDATE users SET balance = balance - ? WHERE id = ?", (price, session['user_id']))
            c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", (price, seller_id))
            
            c.execute("UPDATE gifts SET user_id = ? WHERE id = ?", (session['user_id'], gift_id))
            
            c.execute("DELETE FROM market WHERE id = ?", (item_id,))
            
            conn.commit()
            
            c.execute("SELECT balance FROM users WHERE id = ?", (session['user_id'],))
            result = c.fetchone()
            if result:
                session['balance'] = result['balance']
            
            if seller_tg_id:
                message = f"🎉 Ваш товар <b>{product_name}</b> был куплен пользователем <b>{session['username']}</b> за <b>{price}</b> звезд!"
                send_telegram_message(seller_tg_id, message)
            
            return jsonify({'success': True, 'new_balance': session['balance']})
        except Exception as e:
            app.logger.error(f"Ошибка в buy_from_market: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/improve_gift', methods=['POST'])
@login_required
def improve_gift():
    data = request.get_json()
    gift_id = data.get('gift_id')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("SELECT user_id, improved, product_id FROM gifts WHERE id = ?", (gift_id,))
            result = c.fetchone()
            
            if not result or result['user_id'] != session['user_id']:
                return jsonify({'success': False, 'error': 'Invalid gift'}), 400
            
            if result['improved'] == 1:
                return jsonify({'success': False, 'error': 'Gift already improved'}), 400
            
            c.execute("SELECT balance FROM users WHERE id = ?", (session['user_id'],))
            balance_result = c.fetchone()
            if not balance_result or balance_result['balance'] < 500:
                return jsonify({'success': False, 'error': 'Insufficient funds'}), 400
            
            products = load_products_cached()
            product = next((p for p in products if p['id'] == result['product_id']), None)
            
            if not product or 'improve_folder' not in product or not product['improve_folder']:
                return jsonify({'success': False, 'error': 'Product cannot be improved'}), 400
            
            improve_folder = os.path.join(app.static_folder, 'images', product['improve_folder'])
            
            if not os.path.exists(improve_folder) or not os.path.isdir(improve_folder):
                return jsonify({'success': False, 'error': 'Improvement folder not found'}), 400
            
            images = [f for f in os.listdir(improve_folder) 
                     if os.path.isfile(os.path.join(improve_folder, f))]
            
            if not images:
                return jsonify({'success': False, 'error': 'No improvement images available'}), 400
            
            new_image = random.choice(images)
            new_image_path = f"{product['improve_folder']}/{new_image}"
            
            image_file = os.path.join(improve_folder, new_image)
            if not os.path.isfile(image_file):
                return jsonify({'success': False, 'error': 'Image file not found'}), 400
            
            c.execute("SELECT MAX(collection_number) FROM gifts WHERE product_id = ? AND improved = 1", 
                     (result['product_id'],))
            max_number = c.fetchone()[0] or 0
            
            new_number = max_number + 1
            
            new_balance = balance_result['balance'] - 500
            c.execute("UPDATE gifts SET improved = 1, improved_image = ?, collection_number = ? WHERE id = ?", 
                     (new_image_path, new_number, gift_id))
            c.execute("UPDATE users SET balance = ? WHERE id = ?", (new_balance, session['user_id']))
            
            conn.commit()
            session['balance'] = new_balance
            return jsonify({
                'success': True, 
                'new_image': f"images/{new_image_path}",
                'new_balance': new_balance
            })
        except Exception as e:
            app.logger.error(f"Ошибка в improve_gift: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/transfer_gift', methods=['POST'])
@login_required
def transfer_gift():
    data = request.get_json()
    gift_id = data.get('gift_id')
    recipient_id = data.get('recipient_id')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("SELECT user_id, product_id FROM gifts WHERE id = ?", (gift_id,))
            result = c.fetchone()
            
            if not result or result['user_id'] != session['user_id']:
                return jsonify({'success': False, 'error': 'Invalid gift'}), 400
            
            c.execute("SELECT tg_id, username FROM users WHERE id = ?", (session['user_id'],))
            sender_info = c.fetchone()
            sender_tg_id = sender_info['tg_id'] if sender_info else None
            sender_username = sender_info['username'] if sender_info else "Неизвестный отправитель"
            
            c.execute("SELECT id, tg_id, username FROM users WHERE tg_id = ?", (recipient_id,))
            recipient = c.fetchone()
            
            if not recipient:
                return jsonify({'success': False, 'error': 'Recipient not found'}), 404
            
            products = load_products_cached()
            product = next((p for p in products if p['id'] == result['product_id']), None)
            product_name = product['name'] if product else "Неизвестный продукт"
            
            transfer_key = f"{sender_tg_id}_{gift_id}"
            pending_transfers[transfer_key] = {
                'gift_id': gift_id,
                'sender_tg_id': sender_tg_id,
                'sender_username': sender_username,
                'recipient_id': recipient_id,
                'recipient_username': recipient['username'],
                'product_name': product_name
            }
            
            markup = types.InlineKeyboardMarkup()
            confirm_btn = types.InlineKeyboardButton('✅ Да', callback_data=f'transfer_confirm_{transfer_key}')
            cancel_btn = types.InlineKeyboardButton('❌ Нет', callback_data=f'transfer_cancel_{transfer_key}')
            markup.add(confirm_btn, cancel_btn)
            
            bot.send_message(
                sender_tg_id,
                f"❓ Вы уверены, что хотите передать подарок <b>{product_name}</b> пользователю <b>{recipient['username']}</b>?",
                parse_mode='HTML',
                reply_markup=markup
            )
            
            return jsonify({'success': True, 'message': 'Запрос на передачу отправлен. Подтвердите в Telegram.'})
        except Exception as e:
            app.logger.error(f"Ошибка в transfer_gift: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/remove_from_market', methods=['POST'])
@login_required
def remove_from_market():
    data = request.get_json()
    item_id = data.get('item_id')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT seller_id, gift_id FROM market WHERE id = ?
            """, (item_id,))
            result = c.fetchone()
            
            if not result or result['seller_id'] != session['user_id']:
                return jsonify({'success': False, 'error': 'Invalid item'}), 400
            
            gift_id = result['gift_id']
            
            c.execute("DELETE FROM market WHERE id = ?", (item_id,))
            
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в remove_from_market: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/admin')
@admin_required
def admin_panel():
    return render_template('adminkaPanelkaPipes.html')

@app.route('/admin/give', methods=['POST'])
@admin_required
def admin_give():
    data = request.get_json()
    user_id = data.get('user_id')
    stars = data.get('stars')
    product_id = data.get('product_id')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            if stars and stars > 0:
                c.execute("UPDATE users SET balance = balance + ? WHERE tg_id = ?", 
                         (stars, user_id))
            
            if product_id:
                c.execute("INSERT INTO gifts (user_id, product_id) VALUES ((SELECT id FROM users WHERE tg_id = ?), ?)", 
                         (user_id, product_id))
            
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в admin_give: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/admin/remove_market_item', methods=['POST'])
@admin_required
def admin_remove_market_item():
    data = request.get_json()
    item_id = data.get('item_id')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("DELETE FROM market WHERE id = ?", (item_id,))
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в admin_remove_market_item: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/admin/update_price_limits', methods=['POST'])
@admin_required
def admin_update_price_limits():
    data = request.get_json()
    min_price = data.get('min_price')
    max_price = data.get('max_price')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("UPDATE price_settings SET min_price = ?, max_price = ? WHERE id = 1", 
                     (min_price, max_price))
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в admin_update_price_limits: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/admin/add_product', methods=['POST'])
@admin_required
def admin_add_product():
    try:
        name = request.form.get('name')
        price = int(request.form.get('price'))
        limit = request.form.get('limit')
        improve_folder = request.form.get('improve_folder')
        
        if not name or not price:
            return jsonify({'success': False, 'error': 'Missing required fields'}), 400
        
        products = load_products_cached()
        
        new_id = max(p['id'] for p in products) + 1 if products else 1
        
        image_file = request.files.get('image')
        if not image_file:
            return jsonify({'success': False, 'error': 'Image is required'}), 400
        
        image_filename = f"product_{new_id}{os.path.splitext(image_file.filename)[1]}"
        image_path = os.path.join(app.static_folder, 'images', image_filename)
        image_file.save(image_path)
        
        new_product = {
            'id': new_id,
            'name': name,
            'price': price,
            'image': image_filename
        }
        
        if limit and limit.isdigit():
            new_product['limit'] = int(limit)
        
        if improve_folder:
            new_product['improve_folder'] = improve_folder
            improve_path = os.path.join(app.static_folder, 'images', improve_folder)
            if not os.path.exists(improve_path):
                os.makedirs(improve_path)
        
        products.append(new_product)
        with open('products.json', 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=4)
        
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO product_stats (product_id) VALUES (?)", (new_id,))
            conn.commit()
        
        send_channel_notification(new_product)
        
        return jsonify({'success': True, 'product_id': new_id})
    except Exception as e:
        app.logger.error(f"Ошибка в admin_add_product: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/upload_improvements', methods=['POST'])
@admin_required
def admin_upload_improvements():
    try:
        product_id = int(request.form.get('product_id'))
        files = request.files.getlist('images')
        
        if not files:
            return jsonify({'success': False, 'error': 'No files uploaded'}), 400
        
        products = load_products_cached()
        product = next((p for p in products if p['id'] == product_id), None)
        
        if not product:
            return jsonify({'success': False, 'error': 'Product not found'}), 404
        
        if 'improve_folder' not in product or not product['improve_folder']:
            return jsonify({'success': False, 'error': 'Product has no improvement folder'}), 400
        
        improve_path = os.path.join(app.static_folder, 'images', product['improve_folder'])
        
        uploaded = 0
        for file in files:
            if file.filename:
                file.save(os.path.join(improve_path, file.filename))
                uploaded += 1
        
        send_channel_notification(product, uploaded)
        
        return jsonify({
            'success': True,
            'uploaded': uploaded,
            'folder': product['improve_folder']
        })
    except Exception as e:
        app.logger.error(f"Ошибка в admin_upload_improvements: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/delete_product', methods=['POST'])
@admin_required
def admin_delete_product():
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        
        if not product_id:
            return jsonify({'success': False, 'error': 'Product ID is required'}), 400
        
        products = load_products_cached()
        
        product_to_delete = next((p for p in products if p['id'] == product_id), None)
        if not product_to_delete:
            return jsonify({'success': False, 'error': 'Product not found'}), 404
        
        image_path = os.path.join(app.static_folder, 'images', product_to_delete['image'])
        if os.path.exists(image_path):
            os.remove(image_path)
        
        if 'improve_folder' in product_to_delete and product_to_delete['improve_folder']:
            improve_path = os.path.join(app.static_folder, 'images', product_to_delete['improve_folder'])
            if os.path.exists(improve_path):
                for file in os.listdir(improve_path):
                    os.remove(os.path.join(improve_path, file))
                os.rmdir(improve_path)
        
        products = [p for p in products if p['id'] != product_id]
        
        with open('products.json', 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=4)
        
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f"Ошибка в admin_delete_product: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/toggle_user_block', methods=['POST'])
@admin_required
def admin_toggle_user_block():
    data = request.get_json()
    user_id = data.get('user_id')
    is_blocked = data.get('is_blocked')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        try:
            c.execute("UPDATE users SET is_blocked = ? WHERE tg_id = ?", (is_blocked, user_id))
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в admin_toggle_user_block: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/admin/update_product', methods=['POST'])
@admin_required
def admin_update_product():
    try:
        product_id = int(request.form.get('product_id'))
        name = request.form.get('name')
        price = request.form.get('price')
        limit = request.form.get('limit')
        improve_folder = request.form.get('improve_folder')
        
        products = load_products_cached()
        product_index = next((i for i, p in enumerate(products) if p['id'] == product_id), None)
        
        if product_index is None:
            return jsonify({'success': False, 'error': 'Product not found'}), 404
        
        if name:
            products[product_index]['name'] = name
        if price:
            products[product_index]['price'] = int(price)
        if limit:
            if limit == 'none':
                products[product_index].pop('limit', None)
            else:
                products[product_index]['limit'] = int(limit)
        if improve_folder:
            old_improve_folder = products[product_index].get('improve_folder')
            if old_improve_folder and old_improve_folder != improve_folder:
                old_path = os.path.join(app.static_folder, 'images', old_improve_folder)
                new_path = os.path.join(app.static_folder, 'images', improve_folder)
                if os.path.exists(old_path):
                    os.rename(old_path, new_path)
            products[product_index]['improve_folder'] = improve_folder
        
        image_file = request.files.get('image')
        if image_file and image_file.filename:
            old_image = products[product_index].get('image')
            if old_image:
                old_path = os.path.join(app.static_folder, 'images', old_image)
                if os.path.exists(old_path):
                    os.remove(old_path)
            
            image_filename = f"product_{product_id}{os.path.splitext(image_file.filename)[1]}"
            image_path = os.path.join(app.static_folder, 'images', image_filename)
            image_file.save(image_path)
            products[product_index]['image'] = image_filename
        
        if save_products(products):
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Failed to save products'}), 500
            
    except Exception as e:
        app.logger.error(f"Ошибка в admin_update_product: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/get_product_info', methods=['GET'])
@admin_required
def admin_get_product_info():
    try:
        product_id = int(request.args.get('product_id'))
        products = load_products_cached()
        product = next((p for p in products if p['id'] == product_id), None)
        
        if not product:
            return jsonify({'success': False, 'error': 'Product not found'}), 404
        
        improvements = []
        if 'improve_folder' in product and product['improve_folder']:
            improve_path = os.path.join(app.static_folder, 'images', product['improve_folder'])
            if os.path.exists(improve_path):
                improvements = [f for f in os.listdir(improve_path) 
                              if os.path.isfile(os.path.join(improve_path, f))]
        
        with get_db_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT bought FROM product_stats WHERE product_id = ?", (product_id,))
            result = c.fetchone()
            bought = result['bought'] if result else 0
        
        product_info = {
            'id': product['id'],
            'name': product['name'],
            'price': product['price'],
            'limit': product.get('limit', 'none'),
            'improve_folder': product.get('improve_folder', ''),
            'bought': bought,
            'improvements': improvements
        }
        
        return jsonify({'success': True, 'product': product_info})
    except Exception as e:
        app.logger.error(f"Ошибка в admin_get_product_info: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/delete_improvement', methods=['POST'])
@admin_required
def admin_delete_improvement():
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        image_name = data.get('image_name')
        
        products = load_products_cached()
        product = next((p for p in products if p['id'] == product_id), None)
        
        if not product or 'improve_folder' not in product:
            return jsonify({'success': False, 'error': 'Product or improvement folder not found'}), 404
        
        improve_path = os.path.join(app.static_folder, 'images', product['improve_folder'], image_name)
        if os.path.exists(improve_path):
            os.remove(improve_path)
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Improvement image not found'}), 404
    except Exception as e:
        app.logger.error(f"Ошибка в admin_delete_improvement: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/update_improvements', methods=['POST'])
@admin_required
def admin_update_improvements():
    try:
        product_id = int(request.form.get('product_id'))
        files = request.files.getlist('improvements')
        
        products = load_products_cached()
        product = next((p for p in products if p['id'] == product_id), None)
        
        if not product or 'improve_folder' not in product:
            return jsonify({'success': False, 'error': 'Product or improvement folder not found'}), 404
        
        improve_path = os.path.join(app.static_folder, 'images', product['improve_folder'])
        if not os.path.exists(improve_path):
            os.makedirs(improve_path)
        
        uploaded = 0
        for file in files:
            if file.filename:
                file.save(os.path.join(improve_path, file.filename))
                uploaded += 1
        
        send_channel_notification(product, uploaded)
        
        return jsonify({
            'success': True,
            'uploaded': uploaded,
            'message': f'Успешно загружено {uploaded} улучшений'
        })
    except Exception as e:
        app.logger.error(f"Ошибка в admin_update_improvements: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin/get_data', methods=['GET'])
@admin_required
def admin_get_data():
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("SELECT min_price, max_price FROM price_settings WHERE id = 1")
            price_settings = c.fetchone()
            
            c.execute("SELECT tg_id, username, balance, is_blocked FROM users")
            users = [{'tg_id': row['tg_id'], 'username': row['username'], 'balance': row['balance'], 'is_blocked': bool(row['is_blocked'])} for row in c.fetchall()]
            
            c.execute("""
                SELECT market.id, gifts.product_id, market.price, users.username
                FROM market
                JOIN gifts ON market.gift_id = gifts.id
                JOIN users ON market.seller_id = users.id
            """)
            market_items_db = c.fetchall()
            
            products = load_products_cached()
            
            market_items = []
            for item in market_items_db:
                item_id, product_id, price, seller = item
                product = next((p for p in products if p['id'] == product_id), None)
                product_name = product['name'] if product else f"Unknown Product ({product_id})"
                market_items.append({
                    'id': item_id,
                    'name': product_name,
                    'price': price,
                    'seller': seller
                })
            
            return jsonify({
                'success': True,
                'price_settings': {
                    'min_price': price_settings['min_price'],
                    'max_price': price_settings['max_price']
                },
                'users': users,
                'market_items': market_items,
                'products': products
            })
        except Exception as e:
            app.logger.error(f"Ошибка в admin_get_data: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/auctions')
@login_required
def auctions():
    products = load_products_cached()
    products_dict = {p['id']: p for p in products}
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT a.*, g.product_id, g.improved, g.improved_image, g.collection_number, 
                       u.username as seller_username, 
                       b.username as current_bidder_username
                FROM auctions a
                JOIN gifts g ON a.gift_id = g.id
                JOIN users u ON a.seller_id = u.id
                LEFT JOIN users b ON a.current_bidder_id = b.id
                WHERE a.status = 'active' AND a.end_time > datetime('now')
            """)
            
            auctions_list = []
            for auction in c.fetchall():
                product_id = auction['product_id']
                product = products_dict.get(product_id)
                
                if product:
                    if auction['improved'] and auction['improved_image']:
                        image = f"images/{auction['improved_image']}"
                    else:
                        image = f"images/{product['image']}"
                    
                    time_left = datetime.strptime(auction['end_time'], '%Y-%m-%d %H:%M:%S') - datetime.now()
                    hours_left = time_left.total_seconds() // 3600
                    minutes_left = (time_left.total_seconds() % 3600) // 60
                    
                    auctions_list.append({
                        'id': auction['id'],
                        'name': product['name'],
                        'image': image,
                        'improved': auction['improved'],
                        'collection_number': auction['collection_number'],
                        'start_price': auction['start_price'],
                        'current_price': auction['current_price'],
                        'step_price': auction['step_price'],
                        'seller': auction['seller_username'],
                        'current_bidder': auction['current_bidder_username'],
                        'end_time': auction['end_time'],
                        'time_left': f"{int(hours_left)}ч {int(minutes_left)}м",
                        'is_own': auction['seller_id'] == session['user_id']
                    })
            
            return render_template('auctions.html', auctions=auctions_list)
        except Exception as e:
            app.logger.error(f"Ошибка в auctions: {e}")
            return "Ошибка загрузки аукционов", 500

@app.route('/create_auction', methods=['POST'])
@login_required
def create_auction():
    data = request.get_json()
    gift_id = data.get('gift_id')
    start_price = data.get('start_price')
    end_time_hours = data.get('end_time_hours', 24)
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("SELECT user_id FROM gifts WHERE id = ?", (gift_id,))
            result = c.fetchone()
            
            if not result or result['user_id'] != session['user_id']:
                return jsonify({'success': False, 'error': 'Invalid gift'}), 400
            
            c.execute("SELECT id FROM auctions WHERE gift_id = ? AND status = 'active'", (gift_id,))
            existing_auction = c.fetchone()
            
            if existing_auction:
                return jsonify({'success': False, 'error': 'Gift is already on auction'}), 400
            
            end_time = datetime.now() + timedelta(hours=end_time_hours)
            end_time_str = end_time.strftime('%Y-%m-%d %H:%M:%S')
            
            c.execute("""
                INSERT INTO auctions (gift_id, seller_id, start_price, current_price, step_price, end_time)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (gift_id, session['user_id'], start_price, start_price, 100, end_time_str))
            
            conn.commit()
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в create_auction: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/place_bid', methods=['POST'])
@login_required
def place_bid():
    data = request.get_json()
    auction_id = data.get('auction_id')
    bid_amount = data.get('bid_amount')
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT a.*, g.product_id, u.tg_id as seller_tg_id
                FROM auctions a
                JOIN gifts g ON a.gift_id = g.id
                JOIN users u ON a.seller_id = u.id
                WHERE a.id = ? AND a.status = 'active' AND a.end_time > datetime('now')
            """, (auction_id,))
            
            auction = c.fetchone()
            
            if not auction:
                return jsonify({'success': False, 'error': 'Auction not found or expired'}), 404
            
            if bid_amount <= auction['current_price']:
                return jsonify({'success': False, 'error': 'Bid must be higher than current price'}), 400
            
            if auction['seller_id'] == session['user_id']:
                return jsonify({'success': False, 'error': 'You cannot bid on your own auction'}), 400
            
            c.execute("SELECT balance FROM users WHERE id = ?", (session['user_id'],))
            user_balance = c.fetchone()['balance']
            
            if user_balance < bid_amount:
                return jsonify({'success': False, 'error': 'Insufficient funds'}), 400
            
            if auction['current_bidder_id']:
                c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", 
                         (auction['current_price'], auction['current_bidder_id']))
            
            c.execute("UPDATE auctions SET current_price = ?, current_bidder_id = ? WHERE id = ?", 
                     (bid_amount, session['user_id'], auction_id))
            
            c.execute("UPDATE users SET balance = balance - ? WHERE id = ?", 
                     (bid_amount, session['user_id']))
            
            c.execute("INSERT INTO auction_bids (auction_id, bidder_id, bid_amount) VALUES (?, ?, ?)",
                     (auction_id, session['user_id'], bid_amount))
            
            conn.commit()
            
            products = load_products_cached()
            product = next((p for p in products if p['id'] == auction['product_id']), None)
            product_name = product['name'] if product else "Неизвестный продукт"
            
            seller_message = f"🎯 На ваш аукцион для товара <b>{product_name}</b> поступила новая ставка: <b>{bid_amount}</b> звезд!"
            send_telegram_message(auction['seller_tg_id'], seller_message)
            
            if auction['current_bidder_id']:
                c.execute("SELECT tg_id FROM users WHERE id = ?", (auction['current_bidder_id'],))
                previous_bidder = c.fetchone()
                if previous_bidder:
                    outbid_message = f"😔 Ваша ставка на товар <b>{product_name}</b> перебита. Текущая цена: <b>{bid_amount}</b> звезд."
                    send_telegram_message(previous_bidder['tg_id'], outbid_message)
            
            return jsonify({'success': True})
        except Exception as e:
            app.logger.error(f"Ошибка в place_bid: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/complete_auctions')
def complete_auctions():
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT a.*, g.product_id, u.tg_id as seller_tg_id, 
                       b.tg_id as winner_tg_id, b.username as winner_username
                FROM auctions a
                JOIN gifts g ON a.gift_id = g.id
                JOIN users u ON a.seller_id = u.id
                LEFT JOIN users b ON a.current_bidder_id = b.id
                WHERE a.status = 'active' AND a.end_time <= datetime('now')
            """)
            
            completed_auctions = c.fetchall()
            
            products = load_products_cached()
            
            for auction in completed_auctions:
                product = next((p for p in products if p['id'] == auction['product_id']), None)
                product_name = product['name'] if product else "Неизвестный продукт"
                
                if auction['current_bidder_id']:
                    c.execute("UPDATE gifts SET user_id = ? WHERE id = ?", 
                             (auction['current_bidder_id'], auction['gift_id']))
                    
                    winner_message = f"🎉 Поздравляем! Вы выиграли аукцион на товар <b>{product_name}</b> за <b>{auction['current_price']}</b> звезд!"
                    send_telegram_message(auction['winner_tg_id'], winner_message)
                    
                    seller_message = f"✅ Ваш аукцион на товар <b>{product_name}</b> завершен. Победитель: <b>{auction['winner_username']}</b> с ценой <b>{auction['current_price']}</b> звезд."
                    send_telegram_message(auction['seller_tg_id'], seller_message)
                    
                    c.execute("UPDATE users SET balance = balance + ? WHERE id = ?", 
                             (auction['current_price'], auction['seller_id']))
                else:
                    seller_message = f"ℹ️ Ваш аукцион на товар <b>{product_name}</b> завершен. Ставок не поступило."
                    send_telegram_message(auction['seller_tg_id'], seller_message)
                
                c.execute("UPDATE auctions SET status = 'completed' WHERE id = ?", (auction['id'],))
            
            conn.commit()
            return jsonify({'success': True, 'completed': len(completed_auctions)})
        except Exception as e:
            app.logger.error(f"Ошибка в complete_auctions: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

@app.route('/get_live_auctions', methods=['GET'])
@login_required
def get_live_auctions():
    products = load_products_cached()
    products_dict = {p['id']: p for p in products}
    
    with get_db_connection() as conn:
        c = conn.cursor()
        
        try:
            c.execute("""
                SELECT a.*, g.product_id, g.improved, g.improved_image, g.collection_number, 
                       u.username as seller_username, 
                       b.username as current_bidder_username,
                       a.seller_id
                FROM auctions a
                JOIN gifts g ON a.gift_id = g.id
                JOIN users u ON a.seller_id = u.id
                LEFT JOIN users b ON a.current_bidder_id = b.id
                WHERE a.status = 'active' AND a.end_time > datetime('now')
            """)
            
            auctions_list = []
            for auction in c.fetchall():
                product_id = auction['product_id']
                product = products_dict.get(product_id)
                
                if product:
                    if auction['improved'] and auction['improved_image']:
                        image = f"images/{auction['improved_image']}"
                    else:
                        image = f"images/{product['image']}"
                    
                    try:
                        end_time = datetime.strptime(auction['end_time'], '%Y-%m-%d %H:%M:%S')
                        time_left = end_time - datetime.now()
                        hours_left = int(time_left.total_seconds() // 3600)
                        minutes_left = int((time_left.total_seconds() % 3600) // 60)
                        seconds_left = int(time_left.total_seconds() % 60)
                        
                        time_left_str = f"{hours_left:02d}:{minutes_left:02d}:{seconds_left:02d}"
                    except:
                        time_left_str = "00:00:00"
                    
                    progress_percentage = calculate_auction_progress(auction['created_at'], auction['end_time'])
                    
                    auctions_list.append({
                        'id': auction['id'],
                        'name': product['name'],
                        'image': image,
                        'improved': auction['improved'],
                        'collection_number': auction['collection_number'],
                        'start_price': auction['start_price'],
                        'current_price': auction['current_price'],
                        'step_price': auction['step_price'],
                        'seller_username': auction['seller_username'],
                        'current_bidder_username': auction['current_bidder_username'],
                        'end_time': auction['end_time'],
                        'time_left': time_left_str,
                        'progress_percentage': progress_percentage,
                        'is_own': auction['seller_id'] == session['user_id']
                    })
            
            return jsonify({'success': True, 'auctions': auctions_list})
        except Exception as e:
            app.logger.error(f"Ошибка в get_live_auctions: {e}")
            return jsonify({'success': False, 'error': 'Server error'}), 500

def calculate_auction_progress(start_time, end_time):
    try:
        start = datetime.strptime(start_time, '%Y-%m-%d %H:%M:%S')
        end = datetime.strptime(end_time, '%Y-%m-%d %H:%M:%S')
        now = datetime.now()
        
        total_duration = (end - start).total_seconds()
        elapsed = (now - start).total_seconds()
        
        if total_duration <= 0:
            return 100
        
        progress = min(100, max(0, (elapsed / total_duration) * 100))
        return int(progress)
    except Exception as e:
        app.logger.error(f"Ошибка расчета прогресса аукциона: {e}")
        return 0

def run_bot():
    app.logger.info("Запуск бота...")
    while True:
        try:
            bot.infinity_polling(skip_pending=True)
        except Exception as e:
            app.logger.error(f"Ошибка в боте: {e}")
            
            try:
                bot.stop_polling()
            except:
                pass
            
            wait_time = 60 if "409" in str(e) else 20
            app.logger.info(f"Перезапуск бота через {wait_time} секунд...")
            time.sleep(wait_time)

def run_auction_checker():
    while True:
        try:
            with app.app_context():
                complete_auctions()
            time.sleep(60)
        except Exception as e:
            app.logger.error(f"Ошибка в auction_checker: {e}")
            time.sleep(60)

if __name__ == '__main__':
    init_db()
    init_product_stats()
    
    t_bot = threading.Thread(target=run_bot)
    t_bot.daemon = True
    t_bot.start()
    
    t_auction = threading.Thread(target=run_auction_checker)
    t_auction.daemon = True
    t_auction.start()
    
    from waitress import serve
    serve(app, host='0.0.0.0', port=1513, threads=10)