from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackContext,
    MessageHandler,
    filters,
    JobQueue
)
from mcstatus import JavaServer
import sqlite3
import logging
from os import remove, path

# логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

DATABASE_NAME = 'minecraft_tracker.db'

#  ОСНОВНЫЕ ФУНКЦИИ ПРОВЕРКИ СЕРВЕРА 

def validate_server_address(address):
    """Проверка правильности формата адреса сервера"""
    if ':' not in address:
        return False
    ip, port = address.split(':', 1)
    if not port.isdigit() or not (1 <= int(port) <= 65535):
        return False
    return True

async def check_server_connection(server_address, player_name):
    """Проверка соединения с сервером"""
    try:
        server = JavaServer.lookup(server_address)
        server.ping()  # доступен ли айпишник сервера
        return True
    except Exception as e:
        logger.error(f"Ошибка соединения с сервера {server_address}: {e}")
        return False

async def is_player_online(server_address, player_name):
    """Проверка онлайн-статуса игрока"""
    try:
        if not await check_server_connection(server_address, player_name):
            return None
            
        server = JavaServer.lookup(server_address)
        status = server.status()
        if status.players.sample:
            return any(player.name == player_name for player in status.players.sample)
        return False
    except Exception as e:
        logger.error(f"Ошибка при проверке игрока {player_name} на сервере {server_address}: {e}")
        return None

#          ФУНКЦИИ РАБОТЫ С БАЗОЙ ДАННЫХ

def init_db():
    """Инициализация базы данных"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            server_address TEXT,
            player_name TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user_data(user_id):
    """Получение данных пользователя из БД"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    cursor.execute('SELECT server_address, player_name FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (None, None)

def update_user_data(user_id, server_address=None, player_name=None):
    """Обновление данных пользователя в БД"""
    conn = sqlite3.connect(DATABASE_NAME)
    cursor = conn.cursor()
    
    cursor.execute('SELECT 1 FROM users WHERE user_id = ?', (user_id,))
    exists = cursor.fetchone()
    
    if exists:
        if server_address is not None and player_name is not None:
            cursor.execute('UPDATE users SET server_address = ?, player_name = ? WHERE user_id = ?',
                         (server_address, player_name, user_id))
        elif server_address is not None:
            cursor.execute('UPDATE users SET server_address = ? WHERE user_id = ?',
                         (server_address, user_id))
        elif player_name is not None:
            cursor.execute('UPDATE users SET player_name = ? WHERE user_id = ?',
                         (player_name, user_id))
        else:
            # сброс значений(обоих)
            cursor.execute('UPDATE users SET server_address = NULL, player_name = NULL WHERE user_id = ?',
                         (user_id,))
    else:
        if server_address is not None and player_name is not None:
            cursor.execute('INSERT INTO users (user_id, server_address, player_name) VALUES (?, ?, ?)',
                      (user_id, server_address, player_name))
    
    conn.commit()
    conn.close()

async def reset_db(update: Update, context: CallbackContext):
    """Сброс базы данных"""
    try:
        if path.exists(DATABASE_NAME):
            remove(DATABASE_NAME)
            init_db()
            await update.message.reply_text("✅ База данных успешно сброшена!")
        else:
            await update.message.reply_text("ℹ️ База данных не найдена")
    except Exception as e:
        logger.error(f"Ошибка при сбросе базы данных: {e}")
        await update.message.reply_text("❌ Произошла ошибка при сбросе базы данных")

async def reset_settings(update: Update, context: CallbackContext):
    """Сброс настроек сервера и ника"""
    user_id = update.effective_user.id
    
    # кнопки подтверждения сброса
    confirm_keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("Да, сбросить")],
        [KeyboardButton("Нет, отменить")]
    ], resize_keyboard=True)
    
    # подтверждаем сброс
    await update.message.reply_text(
        "⚠️ Вы уверены, что хотите сбросить настройки сервера и ника?\n"
        "Это действие нельзя отменить.",
        reply_markup=confirm_keyboard
    )
    context.user_data['awaiting_reset_confirm'] = True

#          ФУНКЦИИ ИНТЕРФЕЙСА 

def get_main_keyboard(user_id=None):
    """Главное меню кнопок"""
    server_address, player_name = get_user_data(user_id) if user_id else (None, None)
    
    buttons = [
        [KeyboardButton("Проверить статус")],
        [KeyboardButton("Мониторинг ON"), KeyboardButton("Мониторинг OFF")],
        [KeyboardButton("Настройки"), KeyboardButton("Помощь")]
    ]
    
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_settings_keyboard():
    """Меню настроек"""
    return ReplyKeyboardMarkup([
        [KeyboardButton("Изменить сервер"), KeyboardButton("Изменить ник")],
        [KeyboardButton("Сбросить настройки")],
        [KeyboardButton("Назад")]
    ], resize_keyboard=True)

#           ОСНОВНЫЕ КОМАНДЫ БОТА

async def start(update: Update, context: CallbackContext):
    """Обработка команды /start"""
    user_id = update.effective_user.id
    # удаление старых задач мониторинга
    jobs = context.job_queue.get_jobs_by_name(f"monitor_{user_id}")
    for job in jobs:
        job.schedule_removal()
    
    await update.message.reply_text(
        "Привет! Я бот для мониторинга игроков в Minecraft.\n"
        "Сначала настройте сервер и ник в разделе Настройки.",
        reply_markup=get_main_keyboard(user_id)
    )

async def settings(update: Update, context: CallbackContext):
    """Меню настроек"""
    user_id = update.effective_user.id
    server, player = get_user_data(user_id)
    
    status_msg = "Текущие настройки:\n"
    if server:
        status_msg += f"Сервер: {server}\n"
    else:
        status_msg += "Сервер: не задан\n"
    
    if player:
        status_msg += f"Игрок: {player}"
    else:
        status_msg += "Игрок: не задан"
    
    await update.message.reply_text(
        "⚙️ Настройки:\n" + status_msg,
        reply_markup=get_settings_keyboard()
    )

async def change_server(update: Update, context: CallbackContext):
    """Запрос на изменение сервера"""
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("Назад")]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        "Введите адрес сервера в формате IP:PORT (например, 123.45.67.89:25565):",
        reply_markup=keyboard
    )
    context.user_data['awaiting_server'] = True

async def change_player(update: Update, context: CallbackContext):
    """Запрос на изменение ника"""
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("Назад")]
    ], resize_keyboard=True)
    
    await update.message.reply_text(
        "Введите ваш ник в Minecraft (только латинские буквы и цифры, от 5 до 16 символов):",
        reply_markup=keyboard
    )
    context.user_data['awaiting_player'] = True

async def check_status(update: Update, context: CallbackContext):
    """Проверка текущего статуса игрока"""
    user_id = update.effective_user.id
    server_address, player_name = get_user_data(user_id)
    
    if not server_address or not player_name:
        await update.message.reply_text(
            "❌ Сначала настройте сервер и ник в разделе Настройки.",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    status = await is_player_online(server_address, player_name)
    if status is None:
        await update.message.reply_text(
            "❌ Ошибка при проверке статуса. Проверьте правильность адреса сервера.",
            reply_markup=get_main_keyboard(user_id)
        )
    elif status:
        await update.message.reply_text(
            f"🎮 {player_name} сейчас в игре! (Сервер: {server_address})",
            reply_markup=get_main_keyboard(user_id)
        )
    else:
        await update.message.reply_text(
            f"💤 {player_name} сейчас не в игре. (Сервер: {server_address})",
            reply_markup=get_main_keyboard(user_id)
        )

async def start_monitoring(update: Update, context: CallbackContext):
    """Запуск мониторинга"""
    user_id = update.effective_user.id
    server, player = get_user_data(user_id)
    
    # активный мониторинг
    if context.job_queue.get_jobs_by_name(f"monitor_{user_id}"):
        await update.message.reply_text(
            "⚠️ Мониторинг уже запущен!",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    if not server or not player:
        await update.message.reply_text(
            "❌ Сначала настройте сервер и ник!",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    # проверка доступности сервера перед запуском мониторинга
    if not await check_server_connection(server, player):
        await update.message.reply_text(
            "❌ Не удалось подключиться к серверу. Проверьте адрес и попробуйте снова.",
            reply_markup=get_main_keyboard(user_id)
        )
        return

    context.job_queue.run_repeating(
        monitor_player,
        interval=30,
        first=5,
        chat_id=update.effective_chat.id,
        data={'server': server, 'player': player, 'last_status': None},
        name=f"monitor_{user_id}"
    )
    
    await update.message.reply_text(
        f"🔔 Мониторинг запущен для {player} на {server}\n"
        f"Уведомления будут приходить каждые 30 секунд.",
        reply_markup=get_main_keyboard(user_id)
    )

async def stop_monitoring(update: Update, context: CallbackContext):
    """Остановка мониторинга"""
    user_id = update.effective_user.id
    jobs = context.job_queue.get_jobs_by_name(f"monitor_{user_id}")
    
    if not jobs:
        await update.message.reply_text(
            "ℹ️ Мониторинг не был запущен",
            reply_markup=get_main_keyboard(user_id)
        )
        return
    
    for job in jobs:
        job.schedule_removal()
    
    await update.message.reply_text(
        "✅ Мониторинг успешно остановлен!",
        reply_markup=get_main_keyboard(user_id)
    )

async def monitor_player(context: CallbackContext):
    """Функция мониторинга игрока"""
    try:
        job = context.job
        if not job:
            return
            
        server = job.data['server']
        player = job.data['player']
        last_status = job.data.get('last_status')
        
        online = await is_player_online(server, player)
        
        # статус не изменился - не отправляем уведомление
        if online == last_status:
            return
            
        # ласт известный статус
        job.data['last_status'] = online
        
        if online is None:
            await context.bot.send_message(
                chat_id=job.chat_id,
                text=f"❌ Ошибка при проверке сервера {server}",
                reply_markup=get_main_keyboard(job.chat_id)
            )
        elif online:
            await context.bot.send_message(
                chat_id=job.chat_id,
                text=f"🎮 {player} сейчас в игре! (Сервер: {server})",
                reply_markup=get_main_keyboard(job.chat_id)
            )
        else:
            await context.bot.send_message(
                chat_id=job.chat_id,
                text=f"💤 {player} сейчас не в игре. (Сервер: {server})",
                reply_markup=get_main_keyboard(job.chat_id)
            )
    except Exception as e:
        logger.error(f"Ошибка в monitor_player: {e}")

async def help_command(update: Update, context: CallbackContext):
    """Показать справку"""
    if 'last_help_msg' in context.user_data:
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=context.user_data['last_help_msg']
            )
        except Exception as e:
            logger.error(f"Ошибка при удалении сообщения: {e}")
    
    help_text = (
        "🛠 <b>Доступные команды:</b>\n\n"
        "🔍 <b>Проверить статус</b> - Текущий онлайн игрока\n"
        "🔔 <b>Мониторинг ON</b> - Уведомления при входе\n"
        "🔕 <b>Мониторинг OFF</b> - Остановить уведомления\n"
        "⚙️ <b>Настройки</b> - Изменить сервер/ник\n"
        "🔄 <b>Сбросить настройки</b> - Удалить текущие настройки\n\n"
        "📩 <b>Поддержка:</b> @rozovoe_vino65"
    )
    
    msg = await update.message.reply_text(
        text=help_text,
        parse_mode='HTML',
        reply_markup=get_main_keyboard(update.effective_user.id)
    )
    context.user_data['last_help_msg'] = msg.message_id

async def handle_message(update: Update, context: CallbackContext):
    """Обработчик текстовых сообщений"""
    user_id = update.effective_user.id
    text = update.message.text

    # сброс настроек1
    if context.user_data.get('awaiting_reset_confirm'):
        if text == "Да, сбросить":
            update_user_data(user_id, server_address=None, player_name=None)
            await update.message.reply_text(
                "✅ Настройки успешно сброшены!",
                reply_markup=get_main_keyboard(user_id)
            )
        else:
            await update.message.reply_text(
                "❌ Сброс настроек отменен",
                reply_markup=get_main_keyboard(user_id)
            )
        context.user_data['awaiting_reset_confirm'] = False
        return

    # кнопка назад
    if text == "Назад" and (context.user_data.get('awaiting_server') or context.user_data.get('awaiting_player')):
        context.user_data['awaiting_server'] = False
        context.user_data['awaiting_player'] = False
        await settings(update, context)
        return

    # ввод айпишника сервера
    if context.user_data.get('awaiting_server'):
        server_address = text.strip()
        if ':' not in server_address:
            await update.message.reply_text(
                "❌ Неверный формат! Введите IP:PORT (например, 123.45.67.89:25565)",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Назад")]], resize_keyboard=True)
            )
            return
            
        update_user_data(user_id, server_address=server_address)
        context.user_data['awaiting_server'] = False
        await update.message.reply_text(
            f"✅ Сервер изменен на: {server_address}",
            reply_markup=get_settings_keyboard()
        )
        return

    # ввод никнейма
    elif context.user_data.get('awaiting_player'):
        player_name = text.strip()
        if not player_name.isascii() or len(player_name) < 5 or len(player_name) > 16:
            await update.message.reply_text(
                "❌ Ник должен содержать только латинские буквы/цифры (от 5 до 16 символов)",
                reply_markup=ReplyKeyboardMarkup([[KeyboardButton("Назад")]], resize_keyboard=True)
            )
            return
            
        update_user_data(user_id, player_name=player_name)
        context.user_data['awaiting_player'] = False
        await update.message.reply_text(
            f"✅ Ник изменен на: {player_name}",
            reply_markup=get_settings_keyboard()
        )
        return

    # кнопки главного меню
    elif text == "Проверить статус":
        await check_status(update, context)
    elif text == "Настройки":
        await settings(update, context)
    elif text == "Изменить сервер":
        await change_server(update, context)
    elif text == "Изменить ник":
        await change_player(update, context)
    elif text == "Сбросить настройки":
        await reset_settings(update, context)
    elif text == "Назад":
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=get_main_keyboard(user_id)
        )
    elif text == "Мониторинг ON":
        await start_monitoring(update, context)
    elif text == "Мониторинг OFF":
        await stop_monitoring(update, context)
    elif text == "Помощь":
        await help_command(update, context)
    else:
        await update.message.reply_text(
            "Используйте кнопки для взаимодействия с ботом.",
            reply_markup=get_main_keyboard(user_id)
        )

def main():
    """Запуск бота"""
    init_db()
    application = Application.builder().token('').build()

    # обработчик команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("check", check_status))
    application.add_handler(CommandHandler("monitor", start_monitoring))
    application.add_handler(CommandHandler("stop_monitor", stop_monitoring))
    application.add_handler(CommandHandler("settings", settings))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("reset_db", reset_db))
    application.add_handler(CommandHandler("reset_settings", reset_settings))
    
    # текстовые сообщения
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # запускаем ботека
    application.run_polling()

if __name__ == '__main__':
    main()
