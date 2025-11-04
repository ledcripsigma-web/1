import logging
import random
from datetime import datetime
import pytz
from telegram import (
    Update, 
    InlineKeyboardButton, 
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters,
    ContextTypes
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Временное хранилище данных
users_data = {}
game_requests = {}
active_games = {}
pending_games = {}

# Эмодзи для оформления
EMOJI = {
    "dice": "🎲",
    "star": "⭐",
    "money": "💰",
    "gift": "🎁",
    "stats": "📊",
    "support": "🆘",
    "add": "➕",
    "withdraw": "💸",
    "win": "🏆",
    "lose": "❌",
    "accept": "✅",
    "decline": "❌",
    "fire": "🔥",
    "trophy": "🏆",
    "diamond": "💎",
    "ring": "💍",
    "cake": "🎂",
    "rocket": "🚀",
    "flower": "💐",
    "rose": "🌹",
    "teddy": "🧸",
    "heart": "💝",
    "slot": "🎰",
    "dart": "🎯",
    "football": "⚽",
    "basketball": "🏀",
    "bowling": "🎳"
}

# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "Игрок"
    
    # Инициализация данных пользователя
    if user_id not in users_data:
        users_data[user_id] = {
            "balance": 0,
            "wins": 0,
            "games_played": 0,
            "stars_won": 0,
            "username": username
        }
    
    # Создаем клавиатуру меню
    keyboard = [
        [InlineKeyboardButton(f"Играть{EMOJI['dice']}", callback_data="play")],
        [
            InlineKeyboardButton(f"Статистика{EMOJI['stats']}", callback_data="stats"),
            InlineKeyboardButton(f"Пополнить{EMOJI['add']}", callback_data="deposit")
        ],
        [
            InlineKeyboardButton(f"Вывод{EMOJI['withdraw']}", callback_data="withdraw"),
            InlineKeyboardButton(f"Поддержка{EMOJI['support']}", callback_data="support")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = f"""
{EMOJI['fire']} *Привет, добро пожаловать в RuletsGame!* {EMOJI['fire']}

🎮 *Это телеграм игра, где можно:*
• Весело провести время с друзьями {EMOJI['dice']}
• Играть на реальные Stars {EMOJI['star']}

{EMOJI['trophy']} _Испытай удачу и стань чемпионом!_ {EMOJI['trophy']}
    """
    
    if update.message:
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
    else:
        await update.callback_query.edit_message_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

# Команда /stop для отмены игры
async def stop_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    # Ищем активную ожидающую игру
    game_found = False
    for game_id, game_data in pending_games.items():
        if game_data['player1'] == user_id or game_data['player2'] == user_id:
            # Возвращаем Stars если игра на Stars
            if game_data['game_mode'] == 'stars':
                bet_amount = game_data['bet_amount']
                users_data[user_id]['balance'] += bet_amount
                
                # Уведомляем второго игрока
                opponent_id = game_data['player2'] if game_data['player1'] == user_id else game_data['player1']
                try:
                    await context.bot.send_message(
                        opponent_id,
                        f"❌ Игра отменена противником. Ваши {bet_amount} Stars возвращены на баланс."
                    )
                except:
                    pass
                
                text = f"""
❌ *Игра отменена*

{EMOJI['money']} Ваши {bet_amount} Stars возвращены на баланс
{EMOJI['star']} Текущий баланс: {users_data[user_id]['balance']} Stars
                """
            else:
                text = "❌ Обычная игра отменена"
            
            del pending_games[game_id]
            game_found = True
            break
    
    if not game_found:
        text = "❌ У вас нет активных ожидающих игр"
    
    await update.message.reply_text(text, parse_mode='Markdown')

# Обработчик кнопок
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    data = query.data
    
    if data == "play":
        await show_game_modes(query)
    elif data == "stats":
        await show_stats(query, user_id)
    elif data == "deposit":
        await show_deposit(query, user_id)
    elif data == "withdraw":
        await show_withdraw(query, user_id)
    elif data == "support":
        await show_support(query)
    elif data == "normal_game":
        await show_normal_games(query)
    elif data == "stars_game":
        await show_stars_games(query, user_id)
    elif data.startswith("game_"):
        game_type = data.split("_")[1]
        await request_opponent(query, context, game_type, "normal")
    elif data.startswith("stars_game_"):
        game_type = data.split("_")[2]
        await request_opponent(query, context, game_type, "stars")
    elif data == "select_gift":
        await select_gift(query, user_id)
    elif data.startswith("gift_"):
        gift_type = data.split("_")[1]
        await process_gift_selection(query, user_id, gift_type)
    elif data == "back_menu":
        await start_from_callback(query, context)
    elif data.startswith("pay_"):
        amount = int(data.split("_")[1])
        await process_payment(query, user_id, amount)
    elif data.startswith("accept_"):
        await accept_game(query, context)
    elif data.startswith("decline_"):
        await decline_game(query)
    elif data.startswith("pay_bet_"):
        await process_bet_payment(query, context)

# Запуск из callback
async def start_from_callback(query, context):
    user_id = query.from_user.id
    username = query.from_user.username or "Игрок"
    
    if user_id not in users_data:
        users_data[user_id] = {
            "balance": 0,
            "wins": 0,
            "games_played": 0,
            "stars_won": 0,
            "username": username
        }
    
    keyboard = [
        [InlineKeyboardButton(f"Играть{EMOJI['dice']}", callback_data="play")],
        [
            InlineKeyboardButton(f"Статистика{EMOJI['stats']}", callback_data="stats"),
            InlineKeyboardButton(f"Пополнить{EMOJI['add']}", callback_data="deposit")
        ],
        [
            InlineKeyboardButton(f"Вывод{EMOJI['withdraw']}", callback_data="withdraw"),
            InlineKeyboardButton(f"Поддержка{EMOJI['support']}", callback_data="support")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    welcome_text = f"""
{EMOJI['fire']} *Привет, добро пожаловать в RuletsGame!* {EMOJI['fire']}

🎮 *Это телеграм игра, где можно:*
• Весело провести время с друзьями {EMOJI['dice']}
• Играть на реальные Stars {EMOJI['star']}

{EMOJI['trophy']} _Испытай удачу и стань чемпионом!_ {EMOJI['trophy']}
    """
    
    await query.edit_message_text(
        welcome_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Показать режимы игры
async def show_game_modes(query):
    keyboard = [
        [InlineKeyboardButton(f"Обычная игра{EMOJI['dice']}", callback_data="normal_game")],
        [InlineKeyboardButton(f"Игра на Stars{EMOJI['star']}", callback_data="stars_game")],
        [InlineKeyboardButton("◀️ Назад", callback_data="back_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"""
🎮 *Выбери режим игры:*

{EMOJI['dice']} *Обычная игра* - играй для развлечения
{EMOJI['star']} *Игра на Stars* - играй на виртуальные звезды
    """
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Показать обычные игры
async def show_normal_games(query):
    keyboard = [
        [
            InlineKeyboardButton(f"{EMOJI['slot']}", callback_data="game_slot"),
            InlineKeyboardButton(f"{EMOJI['dice']}", callback_data="game_dice"),
            InlineKeyboardButton(f"{EMOJI['dart']}", callback_data="game_dart")
        ],
        [
            InlineKeyboardButton(f"{EMOJI['football']}", callback_data="game_football"),
            InlineKeyboardButton(f"{EMOJI['basketball']}", callback_data="game_basketball"),
            InlineKeyboardButton(f"{EMOJI['bowling']}", callback_data="game_bowling")
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data="play")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"""
🎲 *Выбери игру:*

{EMOJI['slot']} Слоты
{EMOJI['dice']} Кубики  
{EMOJI['dart']} Дартс
{EMOJI['football']} Футбол
{EMOJI['basketball']} Баскетбол
{EMOJI['bowling']} Боулинг
    """
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Показать игры на Stars
async def show_stars_games(query, user_id):
    user_data = users_data.get(user_id, {})
    balance = user_data.get("balance", 0)
    
    if balance <= 0:
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="play")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = f"""
{EMOJI['money']} *Недостаточно Stars!*

Твой баланс: *{balance}* {EMOJI['star']}

Пополни баланс чтобы играть на Stars!
        """
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        return
    
    keyboard = [
        [
            InlineKeyboardButton(f"{EMOJI['slot']}", callback_data="stars_game_slot"),
            InlineKeyboardButton(f"{EMOJI['dice']}", callback_data="stars_game_dice"),
            InlineKeyboardButton(f"{EMOJI['dart']}", callback_data="stars_game_dart")
        ],
        [
            InlineKeyboardButton(f"{EMOJI['football']}", callback_data="stars_game_football"),
            InlineKeyboardButton(f"{EMOJI['basketball']}", callback_data="stars_game_basketball"),
            InlineKeyboardButton(f"{EMOJI['bowling']}", callback_data="stars_game_bowling")
        ],
        [InlineKeyboardButton("◀️ Назад", callback_data="play")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"""
🎲 *Выбери игру на Stars:*

Твой баланс: *{balance}* {EMOJI['star']}

{EMOJI['slot']} Слоты
{EMOJI['dice']} Кубики  
{EMOJI['dart']} Дартс
{EMOJI['football']} Футбол
{EMOJI['basketball']} Баскетбол
{EMOJI['bowling']} Боулинг
    """
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Запрос противника
async def request_opponent(query, context, game_type, game_mode):
    user_id = query.from_user.id
    
    context.user_data['waiting_for_opponent'] = True
    context.user_data['game_type'] = game_type
    context.user_data['game_mode'] = game_mode
    
    game_names = {
        "slot": f"Слоты {EMOJI['slot']}",
        "dice": f"Кубики {EMOJI['dice']}",
        "dart": f"Дартс {EMOJI['dart']}",
        "football": f"Футбол {EMOJI['football']}",
        "basketball": f"Баскетбол {EMOJI['basketball']}",
        "bowling": f"Боулинг {EMOJI['bowling']}"
    }
    
    game_name = game_names.get(game_type, "Игра")
    
    text = f"""
🎮 *Поиск противника*

Игра: *{game_name}*
Режим: *{'Обычная игра' if game_mode == 'normal' else 'Игра на Stars'}*

📝 *Напиши username противника:*
(например: @username)
    """
    
    await query.edit_message_text(text, parse_mode='Markdown')

# Обработчик текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.user_data.get('waiting_for_opponent'):
        opponent_username = update.message.text.strip()
        
        if not opponent_username.startswith('@'):
            await update.message.reply_text("❌ Пожалуйста, введите username начинающийся с @")
            return
        
        game_type = context.user_data.get('game_type')
        game_mode = context.user_data.get('game_mode')
        
        # Для игры на Stars запрашиваем ставку
        if game_mode == 'stars':
            context.user_data['opponent_username'] = opponent_username
            context.user_data['waiting_for_bet'] = True
            context.user_data['waiting_for_opponent'] = False
            
            user_balance = users_data.get(user_id, {}).get('balance', 0)
            
            await update.message.reply_text(
                f"💰 *Введите ставку в Stars:*\n"
                f"Ваш баланс: {user_balance} Stars\n"
                f"Максимальная ставка: {user_balance} Stars",
                parse_mode='Markdown'
            )
            return
        
        # Для обычной игры создаем запрос
        request_id = f"{user_id}_{datetime.now().timestamp()}"
        game_requests[request_id] = {
            "from_user": user_id,
            "from_username": update.effective_user.username or "Игрок",
            "to_username": opponent_username,
            "game_type": game_type,
            "game_mode": game_mode,
            "timestamp": datetime.now()
        }
        
        context.user_data.clear()
        
        await update.message.reply_text(
            f"✅ Запрос на игру отправлен пользователю {opponent_username}"
        )
        
        await start(update, context)
    
    elif context.user_data.get('waiting_for_bet'):
        try:
            bet_amount = int(update.message.text)
            user_balance = users_data.get(user_id, {}).get('balance', 0)
            
            if bet_amount <= 0:
                await update.message.reply_text("❌ Ставка должна быть положительной")
                return
            
            if bet_amount > user_balance:
                await update.message.reply_text(f"❌ Недостаточно Stars. Ваш баланс: {user_balance}")
                return
            
            opponent_username = context.user_data['opponent_username']
            game_type = context.user_data['game_type']
            
            # Создаем запрос на игру с ставкой
            request_id = f"{user_id}_{datetime.now().timestamp()}"
            game_requests[request_id] = {
                "from_user": user_id,
                "from_username": update.effective_user.username or "Игрок",
                "to_username": opponent_username,
                "game_type": game_type,
                "game_mode": "stars",
                "bet_amount": bet_amount,
                "timestamp": datetime.now()
            }
            
            # Резервируем Stars
            users_data[user_id]['balance'] -= bet_amount
            
            context.user_data.clear()
            
            await update.message.reply_text(
                f"✅ Запрос на игру отправлен пользователю {opponent_username}\n"
                f"💰 Ставка: {bet_amount} Stars\n"
                f"💎 Ваш баланс: {users_data[user_id]['balance']} Stars\n\n"
                f"⚡ Ожидайте подтверждения от противника"
            )
            
        except ValueError:
            await update.message.reply_text("❌ Пожалуйста, введите корректную сумму ставки")
    
    elif context.user_data.get('waiting_deposit'):
        try:
            amount = int(update.message.text)
            if amount <= 0:
                await update.message.reply_text("❌ Сумма должна быть положительной")
                return
            
            keyboard = [[InlineKeyboardButton("💳 Оплатить", callback_data=f"pay_{amount}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"💰 *Счёт на {amount} Stars* {EMOJI['star']}\n\n"
                f"Для пополнения нажмите кнопку ниже:",
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            context.user_data['deposit_amount'] = amount
            
        except ValueError:
            await update.message.reply_text("❌ Пожалуйста, введите корректную сумму")

# Принять игру
async def accept_game(query, context):
    request_id = query.data.split("_")[1]
    user_id = query.from_user.id
    
    if request_id not in game_requests:
        await query.edit_message_text("❌ Запрос на игру устарел")
        return
    
    game_request = game_requests[request_id]
    
    # Для игры на Stars проверяем баланс и создаем ожидающую игру
    if game_request['game_mode'] == 'stars':
        user_balance = users_data.get(user_id, {}).get('balance', 0)
        bet_amount = game_request['bet_amount']
        
        if user_balance < bet_amount:
            await query.edit_message_text(
                f"❌ Недостаточно Stars для игры\n"
                f"💰 Нужно: {bet_amount} Stars\n"
                f"💎 Ваш баланс: {user_balance} Stars\n\n"
                f"Пополните баланс чтобы принять игру"
            )
            return
        
        # Резервируем Stars у второго игрока
        users_data[user_id]['balance'] -= bet_amount
        
        # Создаем ожидающую игру
        game_id = f"game_{request_id}"
        pending_games[game_id] = {
            'player1': game_request['from_user'],
            'player2': user_id,
            'game_type': game_request['game_type'],
            'game_mode': 'stars',
            'bet_amount': bet_amount,
            'player1_paid': True,
            'player2_paid': False
        }
        
        # Отправляем ссылку на оплату второму игроку
        keyboard = [[InlineKeyboardButton("💳 Оплатить ставку", callback_data=f"pay_bet_{game_id}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            f"✅ Вы приняли игру!\n"
            f"💰 Ставка: {bet_amount} Stars\n"
            f"🎮 Игра: {game_request['game_type']}\n\n"
            f"💳 *Для начала игры необходимо оплатить ставку*",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        
        # Уведомляем первого игрока
        try:
            await context.bot.send_message(
                game_request['from_user'],
                f"✅ Противник принял вашу игру!\n"
                f"💰 Ставка: {bet_amount} Stars\n"
                f"⏳ Ожидайте оплаты ставки противником\n\n"
                f"⚡ Используйте /stop для отмены игры"
            )
        except:
            pass
        
    else:
        # Для обычной игры сразу начинаем
        await start_normal_game(query, context, game_request, user_id)
    
    del game_requests[request_id]

# Отклонить игру
async def decline_game(query, context):
    request_id = query.data.split("_")[1]
    
    if request_id in game_requests:
        game_request = game_requests[request_id]
        
        # Возвращаем Stars если игра на Stars
        if game_request['game_mode'] == 'stars':
            users_data[game_request['from_user']]['balance'] += game_request['bet_amount']
            
            # Уведомляем первого игрока
            try:
                await context.bot.send_message(
                    game_request['from_user'],
                    f"❌ Противник отклонил вашу игру\n"
                    f"💰 {game_request['bet_amount']} Stars возвращены на ваш баланс"
                )
            except:
                pass
        
        del game_requests[request_id]
    
    await query.edit_message_text("❌ Игра отклонена")

# Оплата ставки для игры на Stars
async def process_bet_payment(query, context):
    game_id = query.data.split("_")[2]
    user_id = query.from_user.id
    
    if game_id not in pending_games:
        await query.edit_message_text("❌ Игра не найдена")
        return
    
    game_data = pending_games[game_id]
    
    if user_id != game_data['player2']:
        await query.edit_message_text("❌ Это не ваша игра")
        return
    
    # Помечаем что второй игрок оплатил
    game_data['player2_paid'] = True
    
    # Начинаем игру
    await start_stars_game(query, context, game_data)
    
    del pending_games[game_id]

# Запуск игры на Stars
async def start_stars_game(query, context, game_data):
    player1 = game_data['player1']
    player2 = game_data['player2']
    game_type = game_data['game_type']
    bet_amount = game_data['bet_amount']
    
    # Игровая логика
    if game_type == "dice":
        # Бросок кубиков
        roll1 = random.randint(1, 6)
        roll2 = random.randint(1, 6)
        
        # Определяем победителя
        if roll1 > roll2:
            winner_id = player1
            loser_id = player2
            winner_roll = roll1
            loser_roll = roll2
        elif roll2 > roll1:
            winner_id = player2
            loser_id = player1
            winner_roll = roll2
            loser_roll = roll1
        else:
            # Ничья - случайный победитель 50/50
            if random.choice([True, False]):
                winner_id = player1
                loser_id = player2
                winner_roll = roll1
                loser_roll = roll2
            else:
                winner_id = player2
                loser_id = player1
                winner_roll = roll2
                loser_roll = roll1
        
        # Начисляем выигрыш
        total_prize = bet_amount * 2
        users_data[winner_id]['balance'] += total_prize
        users_data[winner_id]['wins'] += 1
        users_data[winner_id]['stars_won'] += bet_amount
        users_data[winner_id]['games_played'] += 1
        users_data[loser_id]['games_played'] += 1
        
        # Отправляем результаты
        result_text = f"""
🎲 *Поединок начался!* 🎲

🎯 Игроки бросили кубики:

{EMOJI['dice']} {users_data[player1]['username']}: {roll1}
{EMOJI['dice']} {users_data[player2]['username']}: {roll2}

🏆 *Победитель: {users_data[winner_id]['username']}*

{winner_roll}️⃣ Победа: @{users_data[winner_id]['username']} - Выиграл {bet_amount} Stars⭐
{loser_roll}️⃣ Поражение: @{users_data[loser_id]['username']}

💰 Общий выигрыш: {total_prize} Stars
        """
        
    # Отправляем результаты обоим игрокам
    try:
        await context.bot.send_message(player1, result_text, parse_mode='Markdown')
        await context.bot.send_message(player2, result_text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка отправки результатов: {e}")

# Показать статистику
async def show_stats(query, user_id):
    user_data = users_data.get(user_id, {})
    
    wins = user_data.get("wins", 0)
    games_played = user_data.get("games_played", 0)
    stars_won = user_data.get("stars_won", 0)
    balance = user_data.get("balance", 0)
    
    win_rate = (wins / games_played * 100) if games_played > 0 else 0
    
    text = f"""
{EMOJI['stats']} *Твоя статистика* {EMOJI['stats']}

{EMOJI['trophy']} *Победы:* {wins}
{EMOJI['dice']} *Сыграно игр:* {games_played}
{EMOJI['star']} *Выиграно Stars:* {stars_won}
{EMOJI['money']} *Текущий баланс:* {balance} Stars
{EMOJI['fire']} *Процент побед:* {win_rate:.1f}%

{EMOJI['rocket']} _Продолжай в том же духе!_ {EMOJI['rocket']}
    """
    
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Показать пополнение
async def show_deposit(query, user_id):
    user_data = users_data.get(user_id, {})
    balance = user_data.get("balance", 0)
    
    text = f"""
💳 *Пополнение баланса* 💳

{EMOJI['star']} *Ваш баланс:* **{balance} Stars** {EMOJI['star']}

{EMOJI['money']} *Напишите сумму для пополнения:*
(Минимум: 1 Star)
    """
    
    await query.edit_message_text(text, parse_mode='Markdown')
    
    # Устанавливаем флаг ожидания суммы пополнения
    if 'user_data' not in context:
        context.user_data = {}
    context.user_data[user_id] = {'waiting_deposit': True}

# Обработка платежа
async def process_payment(query, user_id, amount):
    user_data = users_data.get(user_id, {})
    current_balance = user_data.get("balance", 0)
    new_balance = current_balance + amount
    
    users_data[user_id]["balance"] = new_balance
    
    text = f"""
🎉 *Пополнение успешно!* 🎉

{EMOJI['star']} *Баланс пополнен на:* **{amount} Stars**
{EMOJI['money']} *Текущий баланс:* **{new_balance} Stars**

{EMOJI['fire']} _Теперь ты можешь играть на Stars!_ {EMOJI['fire']}
    """
    
    keyboard = [[InlineKeyboardButton("◀️ В меню", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Показать вывод
async def show_withdraw(query, user_id):
    user_data = users_data.get(user_id, {})
    balance = user_data.get("balance", 0)
    
    text = f"""
🎁 *Вывод Stars* 🎁

{EMOJI['star']} *Ваш баланс:* **{balance} Stars** {EMOJI['star']}

{EMOJI['gift']} *Вывести Stars в реальные подарки:*
Обменивай свои виртуальные Stars на крутые подарки!

✨ _Чем больше Stars - тем лучше подарки!_ ✨
    """
    
    if balance >= 1:
        keyboard = [[InlineKeyboardButton(f"Вывести{EMOJI['money']}", callback_data="select_gift")]]
    else:
        keyboard = [[InlineKeyboardButton("Пополнить баланс", callback_data="deposit")]]
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="back_menu")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Показать поддержку
async def show_support(query):
    text = f"""
{EMOJI['support']} *Поддержка* {EMOJI['support']}

🆘 *Есть вопросы?*
Напиши нам: @rilyglrletukdetuluft

⏰ *Время работы поддержки:*
18:00 - 20:00 (МСК)

{EMOJI['fire']} _Мы всегда рады помочь!_ {EMOJI['fire']}
    """
    
    keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="back_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Выбор подарка
async def select_gift(query, user_id):
    user_data = users_data.get(user_id, {})
    balance = user_data.get("balance", 0)
    
    text = f"""
🎁 *Выбрать подарок* 🎁

{EMOJI['star']} *Ваш баланс:* **{balance} Stars** {EMOJI['star']}

Выберите подарок который хотите получить:
    """
    
    keyboard = []
    
    # Кнопки в зависимости от баланса (пониженные цены)
    if balance >= 1:
        if balance >= 15:
            keyboard.append([InlineKeyboardButton(f"{EMOJI['teddy']} Плюшевый мишка (15 Stars)", callback_data="gift_teddy")])
            keyboard.append([InlineKeyboardButton(f"{EMOJI['heart']} Сердце (15 Stars)", callback_data="gift_heart")])
        if balance >= 25:
            keyboard.append([InlineKeyboardButton(f"{EMOJI['gift']} Подарочная коробка (25 Stars)", callback_data="gift_box")])
            keyboard.append([InlineKeyboardButton(f"{EMOJI['rose']} Букет роз (25 Stars)", callback_data="gift_rose")])
        if balance >= 50:
            keyboard.append([InlineKeyboardButton(f"{EMOJI['cake']} Торт (50 Stars)", callback_data="gift_cake")])
            keyboard.append([InlineKeyboardButton(f"{EMOJI['flower']} Цветы (50 Stars)", callback_data="gift_flowers")])
            keyboard.append([InlineKeyboardButton(f"{EMOJI['rocket']} Ракета (50 Stars)", callback_data="gift_rocket")])
        if balance >= 100:
            keyboard.append([InlineKeyboardButton(f"{EMOJI['ring']} Кольцо (100 Stars)", callback_data="gift_ring")])
            keyboard.append([InlineKeyboardButton(f"{EMOJI['diamond']} Алмаз (100 Stars)", callback_data="gift_diamond")])
            keyboard.append([InlineKeyboardButton(f"{EMOJI['trophy']} Кубок (100 Stars)", callback_data="gift_trophy")])
    
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="withdraw")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Обработка выбора подарка
async def process_gift_selection(query, user_id, gift_type):
    user_data = users_data.get(user_id, {})
    balance = user_data.get("balance", 0)
    
    # Пониженные цены на подарки
    gift_prices = {
        "teddy": 15, "heart": 15,
        "box": 25, "rose": 25,
        "cake": 50, "flowers": 50, "rocket": 50,
        "ring": 100, "diamond": 100, "trophy": 100
    }
    
    price = gift_prices.get(gift_type, 0)
    
    if balance >= price:
        # Списание Stars
        users_data[user_id]["balance"] = balance - price
        
        gift_names = {
            "teddy": "Плюшевый мишка 🧸",
            "heart": "Сердце 💝", 
            "box": "Подарочная коробка 🎁",
            "rose": "Букет роз 🌹",
            "cake": "Торт 🎂",
            "flowers": "Цветы 💐",
            "rocket": "Ракета 🚀",
            "ring": "Кольцо 💍",
            "diamond": "Алмаз 💎",
            "trophy": "Кубок 🏆"
        }
        
        gift_name = gift_names.get(gift_type, 'Подарок')
        
        # Сообщение об успешном выводе
        success_text = f"""
🎉 *Поздравляем!* 🎉

{EMOJI['gift']} Вы успешно обменяли *{price} Stars* на подарок:
*{gift_name}*

{EMOJI['star']} *Новый баланс:* **{balance - price} Stars**

✨ _Спасибо за игру! Ваш подарок будет доставлен._ ✨
        """
        
        keyboard = [[InlineKeyboardButton("◀️ В меню", callback_data="back_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(success_text, reply_markup=reply_markup, parse_mode='Markdown')
        
        # Отправляем сам подарок
        gift_emoji = gift_names[gift_type].split()[-1]  # Берем эмодзи из названия
        
        # Отправляем сообщение с подарком
        gift_message = f"""
🎁 *Вам доставлен подарок!* 🎁

*{gift_name}*

{gift_emoji} {gift_emoji} {gift_emoji}
{gift_emoji} {gift_emoji} {gift_emoji}  
{gift_emoji} {gift_emoji} {gift_emoji}

🎉 _Наслаждайтесь вашим подарком!_
        """
        
        await query.message.reply_text(gift_message, parse_mode='Markdown')
        
    else:
        text = f"""
❌ *Недостаточно Stars*

Для этого подарка нужно {price} Stars
Ваш баланс: {balance} Stars

Пополните баланс чтобы получить этот подарок!
        """
        
        keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="select_gift")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

# Главная функция
def main():
    # Ваш токен бота
    TOKEN = "7611839139:AAEtf4j8itdKLjfo9YGRLhIOqPorpqtg2LY"
    
    # Создаем Application
    application = Application.builder().token(TOKEN).build()
    
    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop_game))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск бота
    print("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
