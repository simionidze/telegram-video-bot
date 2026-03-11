import os
import asyncio
import logging
import threading
import traceback
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient, errors
from telethon.tl.types import MessageMediaDocument
from datetime import datetime, timedelta
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация из переменных окружения
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
API_ID = int(os.environ.get('API_ID', 0))
API_HASH = os.environ.get('API_HASH')
CHANNEL_USERNAME = 'practika_video'  # Жестко задаем имя канала
TARGET_CHAT_ID = os.environ.get('TARGET_CHAT_ID')  # ID чата куда отправлять отчеты

# Flask приложение для поддержания активности
app = Flask(__name__)

# Глобальные переменные
telethon_client = None
analyzer_bot = None
application_bot = None
scheduler = None  # Планировщик

class VideoAnalyzerBot:
    def __init__(self, client):
        self.client = client
        self.last_analysis = None
        self.last_daily_report = None
        logger.info("🤖 Бот инициализирован")
    
    async def analyze_last_5_days(self):
        """Анализ записей за последние 5 дней с детальной обработкой ошибок"""
        try:
            # Получаем текущее время и время 5 дней назад
            now = datetime.now(pytz.UTC)
            five_days_ago = now - timedelta(days=5)
            
            logger.info(f"🔍 Начинаю анализ с {five_days_ago.strftime('%d.%m.%y')} по {now.strftime('%d.%m.%y')}")
            
            # Проверяем подключение Telethon
            if not self.client.is_connected():
                logger.info("🔄 Telethon не подключен, подключаюсь...")
                await self.client.connect()
                logger.info("✅ Telethon подключен")
            
            # Получаем канал
            try:
                channel = await self.client.get_entity('@' + CHANNEL_USERNAME)
                logger.info(f"📢 Канал найден: {channel.title} (ID: {channel.id})")
            except errors.rpcerrorlist.UsernameNotOccupiedError:
                logger.error(f"❌ Канал @{CHANNEL_USERNAME} не существует")
                return {'error': f'Канал @{CHANNEL_USERNAME} не найден'}
            except errors.rpcerrorlist.ChannelPrivateError:
                logger.error(f"❌ Нет доступа к каналу @{CHANNEL_USERNAME} (приватный)")
                return {'error': f'Нет доступа к каналу @{CHANNEL_USERNAME}. Бот должен быть администратором.'}
            except Exception as e:
                logger.error(f"❌ Ошибка при получении канала: {e}")
                return {'error': f'Ошибка доступа к каналу: {str(e)}'}
            
            # Получаем все сообщения за последние 5 дней
            messages = []
            try:
                async for message in self.client.iter_messages(channel, offset_date=now, reverse=False):
                    if message.date < five_days_ago:
                        break
                    if message.date >= five_days_ago:
                        messages.append(message)
                        logger.info(f"📄 Найден пост от {message.date.strftime('%d.%m.%y')}: {message.id}")
            except Exception as e:
                logger.error(f"❌ Ошибка при получении сообщений: {e}")
                return {'error': f'Ошибка при получении сообщений: {str(e)}'}
            
            logger.info(f"📊 Найдено записей за период: {len(messages)}")
            
            results = {
                'analyzed_at': now.strftime('%d.%m.%Y %H:%M:%S'),
                'channel_title': channel.title,
                'channel_id': channel.id,
                'period_start': five_days_ago.strftime('%d.%m.%y'),
                'period_end': now.strftime('%d.%m.%y'),
                'posts': []
            }
            
            # Анализируем каждое сообщение (пост)
            for message in messages:
                post_info = {
                    'date': message.date.strftime('%d.%m.%y'),
                    'post_title': self.extract_post_title(message),
                    'message_id': message.id,
                    'video_count': 0,
                    'videos': [],
                    'error': None
                }
                
                try:
                    logger.info(f"🔍 Анализирую пост {message.id} от {post_info['date']}")
                    
                    # Получаем комментарии к посту
                    try:
                        comments = await self.client.get_messages(
                            channel,
                            limit=500,
                            reply_to=message.id
                        )
                        logger.info(f"💬 Найдено комментариев: {len(comments) if comments else 0}")
                    except errors.rpcerrorlist.MsgIdInvalidError:
                        logger.warning(f"⚠️ Пост {message.id} не имеет комментариев или они недоступны")
                        comments = []
                    except Exception as e:
                        logger.error(f"❌ Ошибка при получении комментариев к посту {message.id}: {e}")
                        post_info['error'] = f"Ошибка комментариев: {str(e)}"
                        comments = []
                    
                    if comments:
                        video_count = 0
                        for comment in comments:
                            try:
                                if comment.media and isinstance(comment.media, MessageMediaDocument):
                                    if comment.document and comment.document.mime_type:
                                        if comment.document.mime_type.startswith('video/'):
                                            video_count += 1
                                            post_info['videos'].append({
                                                'comment_id': comment.id,
                                                'size': comment.document.size / (1024*1024) if comment.document.size else 0
                                            })
                            except Exception as e:
                                logger.error(f"⚠️ Ошибка при анализе комментария {comment.id}: {e}")
                                continue
                        
                        post_info['video_count'] = video_count
                        logger.info(f"🎥 В посте {message.id} найдено видео: {video_count}")
                    
                    results['posts'].append(post_info)
                    
                except Exception as e:
                    logger.error(f"❌ Критическая ошибка при анализе поста {message.id}: {e}")
                    logger.error(traceback.format_exc())
                    post_info['error'] = str(e)
                    results['posts'].append(post_info)
            
            # Сортируем посты по дате (от новых к старым)
            results['posts'].sort(
                key=lambda x: datetime.strptime(x['date'], '%d.%m.%y') if x['date'] else datetime.min, 
                reverse=True
            )
            self.last_analysis = results
            
            # Считаем статистику
            total_videos = sum(p['video_count'] for p in results['posts'])
            posts_with_errors = sum(1 for p in results['posts'] if p['error'])
            
            logger.info(f"✅ Анализ завершен. Постов: {len(results['posts'])}, "
                       f"Видео: {total_videos}, Ошибок: {posts_with_errors}")
            
            return results
            
        except Exception as e:
            logger.error(f"❌ Глобальная ошибка анализа: {e}")
            logger.error(traceback.format_exc())
            return {'error': f'Критическая ошибка: {str(e)}'}
    
    def extract_post_title(self, message):
        """Извлекает название поста из текста сообщения"""
        try:
            if message.text:
                # Берем первую строку или первые 50 символов
                first_line = message.text.split('\n')[0]
                if len(first_line) > 50:
                    return first_line[:50] + "..."
                return first_line if first_line else "Без названия"
            elif message.message:
                # Некоторые сообщения могут иметь поле message вместо text
                first_line = message.message.split('\n')[0]
                if len(first_line) > 50:
                    return first_line[:50] + "..."
                return first_line if first_line else "Без названия"
        except Exception as e:
            logger.error(f"Ошибка при извлечении заголовка: {e}")
        
        return "Без названия"
    
    def check_yesterday_posts(self, results):
        """Проверяет были ли посты вчера"""
        if 'error' in results:
            return False, f"❌ Ошибка при анализе: {results['error']}"
        
        if not results.get('posts'):
            return False, "📊 За указанный период постов не найдено."
        
        # Получаем вчерашнюю дату
        yesterday = (datetime.now(pytz.UTC) - timedelta(days=1)).strftime('%d.%m.%y')
        
        # Проверяем есть ли посты за вчера
        yesterday_posts = [post for post in results['posts'] if post['date'] == yesterday]
        
        if yesterday_posts:
            # Если есть посты за вчера
            text = f"📅 **Вчера ({yesterday})**\n\n"
            for post in yesterday_posts:
                text += f"📝 **{post['post_title']}**\n"
                text += f"🎥 **{post['video_count']} видео файлов**\n"
                if post.get('error'):
                    text += f"⚠️ *Ошибка: {post['error']}*\n"
                text += "\n"
            return True, text
        else:
            # Если нет постов за вчера
            return False, f"😴 **Вчера ({yesterday}) не было тренировки**"
    
    def format_results(self, results):
        """Форматирование результатов для отправки"""
        if 'error' in results:
            return f"❌ **Ошибка анализа:**\n{results['error']}"
        
        if not results.get('posts'):
            return f"📊 За период {results.get('period_start', '?')} - {results.get('period_end', '?')} постов не найдено."
        
        text = f"📊 **Анализ канала {results['channel_title']}**\n"
        text += f"📅 Период: {results['period_start']} - {results['period_end']}\n"
        text += f"🕐 Анализ выполнен: {results['analyzed_at']}\n\n"
        
        text += "📋 **Найденные записи:**\n"
        text += "─" * 40 + "\n"
        
        for post in results['posts']:
            text += f"\n📅 **{post['date']}**"
            text += f"\n📝 **{post['post_title']}**"
            text += f"\n🎥 **{post['video_count']} видео файлов**\n"
            if post.get('error'):
                text += f"⚠️ *Ошибка: {post['error']}*\n"
            text += "─" * 40 + "\n"
        
        # Добавляем итоговую статистику
        total_videos = sum(post['video_count'] for post in results['posts'])
        posts_with_errors = sum(1 for post in results['posts'] if post.get('error'))
        
        text += f"\n📊 **ИТОГО:** {total_videos} видео в {len(results['posts'])} постах"
        if posts_with_errors:
            text += f"\n⚠️ **Ошибок при анализе:** {posts_with_errors}"
        
        return text

async def send_daily_report(chat_id=None):
    """Отправка ежедневного отчета"""
    global analyzer_bot, application_bot
    
    logger.info("📊 Запуск отчета")
    
    try:
        # Выполняем анализ за последние 5 дней
        results = await analyzer_bot.analyze_last_5_days()
        
        # Проверяем были ли посты вчера
        has_posts, report_text = analyzer_bot.check_yesterday_posts(results)
        
        # Создаем клавиатуру с кнопкой выхода
        keyboard = [[InlineKeyboardButton("🚪 Выход", callback_data='exit')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Определяем куда отправлять
        target_chat = chat_id if chat_id else TARGET_CHAT_ID
        
        # Отправляем отчет
        if target_chat:
            await application_bot.bot.send_message(
                chat_id=target_chat,
                text=report_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            logger.info(f"✅ Отчет отправлен в чат {target_chat}")
        else:
            logger.warning("⚠️ TARGET_CHAT_ID не указан, отчет не отправлен")
            return "❌ Чат для отчетов не настроен. Используйте /setchat"
            
    except Exception as e:
        logger.error(f"❌ Ошибка при отправке отчета: {e}")
        logger.error(traceback.format_exc())
        return f"❌ Ошибка: {e}"
    
    return report_text

# Flask маршруты
@app.route('/')
def home():
    return "Bot is running! 🤖"

@app.route('/health')
def health():
    return "OK", 200

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    logger.info(f"✅ ПОЛУЧЕНА КОМАНДА /start от пользователя {update.effective_user.id}")
    
    keyboard = [
        [InlineKeyboardButton("📊 Анализ за 5 дней", callback_data='analyze_5days')],
        [InlineKeyboardButton("📅 Отчет за вчера", callback_data='run_daily_report')],
        [InlineKeyboardButton("🔄 Обновить данные", callback_data='refresh')],
        [InlineKeyboardButton("🚪 Выход", callback_data='exit')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Привет! Я бот для анализа видео в канале @{CHANNEL_USERNAME}.\n"
        f"📅 Каждый день в 9 утра отправляю отчет о вчерашних тренировках.\n\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    logger.info(f"🔘 Нажата кнопка: {query.data}")
    
    if query.data == 'analyze_5days':
        await query.edit_message_text("🔍 Анализирую записи за последние 5 дней... Подождите немного...")
        try:
            results = await analyzer_bot.analyze_last_5_days()
            await query.edit_message_text(
                analyzer_bot.format_results(results),
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"❌ Ошибка в analyze_5days: {e}")
            await query.edit_message_text(f"❌ Произошла ошибка при анализе: {str(e)}")
    
    elif query.data == 'run_daily_report':
        await query.edit_message_text("📅 Формирую отчет за вчера... Подождите немного...")
        
        try:
            # Отправляем отчет в тот же чат, откуда пришел запрос
            await send_daily_report(chat_id=update.effective_chat.id)
            # Удаляем сообщение "формирую отчет" и оставляем только результат
            await query.delete_message()
        except Exception as e:
            logger.error(f"❌ Ошибка в run_daily_report: {e}")
            await query.edit_message_text(f"❌ Ошибка при формировании отчета: {str(e)}")
    
    elif query.data == 'refresh':
        if analyzer_bot.last_analysis:
            await query.edit_message_text(
                analyzer_bot.format_results(analyzer_bot.last_analysis),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Нет сохраненных данных. Сначала выполните анализ.")
    
    elif query.data == 'exit':
        await query.edit_message_text("👋 До свидания! Хорошего дня!")

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /analyze"""
    logger.info(f"Команда /analyze от пользователя {update.effective_user.id}")
    
    try:
        await update.message.reply_text("🔍 Анализирую записи за последние 5 дней...")
        results = await analyzer_bot.analyze_last_5_days()
        await update.message.reply_text(
            analyzer_bot.format_results(results),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка в команде analyze: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /report для ручного запуска отчета"""
    logger.info(f"Команда /report от пользователя {update.effective_user.id}")
    
    await update.message.reply_text("📅 Формирую отчет за вчера... Подождите немного...")
    
    try:
        # Отправляем отчет в тот же чат
        await send_daily_report(chat_id=update.effective_chat.id)
    except Exception as e:
        logger.error(f"Ошибка в команде report: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def set_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Устанавливает ID текущего чата для отправки отчетов"""
    global TARGET_CHAT_ID
    
    chat_id = str(update.effective_chat.id)
    TARGET_CHAT_ID = chat_id
    
    await update.message.reply_text(
        f"✅ ID чата установлен: {chat_id}\n"
        f"Теперь ежедневные отчеты будут приходить сюда."
    )

def setup_scheduler():
    """Настройка планировщика для ежедневных отчетов"""
    global scheduler
    scheduler = BackgroundScheduler(timezone='Europe/Moscow')
    
    # Запуск каждый день в 9:00
    scheduler.add_job(
        func=lambda: asyncio.create_task(send_daily_report()),
        trigger=CronTrigger(hour=9, minute=0, timezone='Europe/Moscow'),
        id='daily_report',
        name='Ежедневный отчет в 9:00',
        replace_existing=True
    )
    
    scheduler.start()
    logger.info("⏰ Планировщик запущен: ежедневный отчет в 9:00 (МСК)")

def shutdown_scheduler():
    """Остановка планировщика при завершении"""
    global scheduler
    if scheduler:
        scheduler.shutdown(wait=False)
        logger.info("⏰ Планировщик остановлен")

def main():
    """Главная функция"""
    global telethon_client, analyzer_bot, application_bot
    
    logger.info("🚀 ЗАПУСК БОТА...")
    
    # Проверяем токен
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN не найден!")
        return
    
    logger.info(f"✅ Токен загружен: {TELEGRAM_BOT_TOKEN[:10]}...")
    
    # Создаем event loop для главного потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Создаем Telethon клиент
    telethon_client = TelegramClient('bot_session', API_ID, API_HASH)
    analyzer_bot = VideoAnalyzerBot(telethon_client)
    
    # Запускаем Telethon КАК БОТА
    async def start_telethon():
        try:
            await telethon_client.start(bot_token=TELEGRAM_BOT_TOKEN)
            logger.info("✅ Telethon клиент подключен как бот")
        except Exception as e:
            logger.error(f"❌ Ошибка подключения Telethon: {e}")
            raise
    
    loop.run_until_complete(start_telethon())
    
    # Создаем приложение бота
    application_bot = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    application_bot.add_handler(CommandHandler("start", start))
    application_bot.add_handler(CommandHandler("analyze", analyze_command))
    application_bot.add_handler(CommandHandler("report", report_command))
    application_bot.add_handler(CommandHandler("setchat", set_chat_command))
    application_bot.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("✅ Обработчики команд добавлены")
    
    # Запускаем планировщик для ежедневных отчетов
    setup_scheduler()
    
    logger.info("🚀 Бот запущен и готов к работе")
    
    # Запускаем Flask в отдельном потоке
    def run_flask():
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info(f"🌐 Flask сервер запущен на порту {os.environ.get('PORT', 5000)}")
    
    # Запускаем бота (этот вызов блокирует поток)
    try:
        logger.info("🔄 Запускаю polling...")
        application_bot.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске polling: {e}")
        logger.error(traceback.format_exc())
    finally:
        shutdown_scheduler()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
        shutdown_scheduler()
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        logger.error(traceback.format_exc())
