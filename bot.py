import os
import asyncio
import logging
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument
from datetime import datetime
import threading

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
CHANNEL_USERNAME = os.environ.get('CHANNEL_USERNAME')

# Flask приложение для поддержания активности
app = Flask(__name__)

# Telethon клиент
telethon_client = TelegramClient('bot_session', API_ID, API_HASH)

class VideoAnalyzerBot:
    def __init__(self):
        self.stats_cache = {}
        self.last_analysis = None
        logger.info("🤖 Бот инициализирован")
    
    async def analyze_channel(self, posts_count=5):
        """Анализ канала"""
        try:
            logger.info(f"🔍 Начинаю анализ {posts_count} последних записей...")
            
            # Проверяем подключение Telethon
            if not telethon_client.is_connected():
                await telethon_client.connect()
            
            channel = await telethon_client.get_entity(CHANNEL_USERNAME)
            logger.info(f"📢 Канал найден: {channel.title}")
            
            # Получаем последние сообщения
            messages = []
            async for message in telethon_client.iter_messages(channel, limit=posts_count):
                messages.append(message)
            
            logger.info(f"📊 Найдено записей: {len(messages)}")
            
            results = {
                'total_videos': 0,
                'posts': [],
                'analyzed_at': datetime.now().strftime('%d.%m.%Y %H:%M:%S'),
                'channel_title': channel.title
            }
            
            for message in messages:
                post_info = {
                    'post_id': message.id,
                    'post_date': message.date.strftime('%d.%m.%Y %H:%M'),
                    'post_text': message.text[:100] if message.text else "Без текста",
                    'video_count': 0,
                    'videos': []
                }
                
                try:
                    # Получаем комментарии к посту
                    comments = await telethon_client.get_messages(
                        channel,
                        limit=100,
                        reply_to=message.id
                    )
                    
                    if comments:
                        for comment in comments:
                            if comment.media and isinstance(comment.media, MessageMediaDocument):
                                if comment.document and comment.document.mime_type and comment.document.mime_type.startswith('video/'):
                                    post_info['video_count'] += 1
                                    post_info['videos'].append({
                                        'comment_id': comment.id,
                                        'size': comment.document.size / (1024*1024) if comment.document.size else 0
                                    })
                    
                    results['total_videos'] += post_info['video_count']
                    results['posts'].append(post_info)
                    logger.info(f"📝 Пост #{message.id}: найдено {post_info['video_count']} видео")
                    
                except Exception as e:
                    logger.error(f"Ошибка при анализе комментариев к посту {message.id}: {e}")
            
            # Сохраняем в кэш
            cache_key = f"analysis_{posts_count}"
            self.stats_cache[cache_key] = results
            self.last_analysis = results
            
            logger.info(f"✅ Анализ завершен. Всего видео: {results['total_videos']}")
            return results
            
        except Exception as e:
            logger.error(f"❌ Ошибка анализа: {e}")
            return {'error': str(e)}
    
    def format_results(self, results):
        """Форматирование результатов для отправки"""
        if 'error' in results:
            return f"❌ Ошибка: {results['error']}"
        
        text = f"📊 **Анализ канала {results['channel_title']}**\n"
        text += f"🕐 Выполнен: {results['analyzed_at']}\n"
        text += f"📹 **Всего видео: {results['total_videos']}**\n\n"
        
        text += "📋 **Последние записи:**\n"
        text += "─" * 30 + "\n"
        
        for i, post in enumerate(results['posts'], 1):
            text += f"\n{i}. **Пост #{post['post_id']}**\n"
            text += f"   📅 {post['post_date']}\n"
            text += f"   🎥 Видео: {post['video_count']}\n"
            
            if post['video_count'] > 0:
                total_size = sum(v['size'] for v in post['videos'])
                text += f"   💾 Объем: {total_size:.1f} МБ\n"
            
            if post['post_text'] and post['post_text'] != "Без текста":
                short_text = post['post_text'][:50] + "..." if len(post['post_text']) > 50 else post['post_text']
                text += f"   📝 {short_text}\n"
        
        return text

# Создаем экземпляр бота
analyzer_bot = VideoAnalyzerBot()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    logger.info(f"Команда /start от пользователя {update.effective_user.id}")
    
    keyboard = [
        [InlineKeyboardButton("📊 Анализ 5 последних записей", callback_data='analyze_5')],
        [InlineKeyboardButton("📊 Анализ 10 последних записей", callback_data='analyze_10')],
        [InlineKeyboardButton("🔄 Обновить данные", callback_data='refresh')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Привет! Я бот для анализа видео в комментариях канала.\n"
        "Выберите действие:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'analyze_5':
        await query.edit_message_text("🔍 Анализирую 5 последних записей... Подождите немного...")
        results = await analyzer_bot.analyze_channel(5)
        await query.edit_message_text(
            analyzer_bot.format_results(results),
            parse_mode='Markdown'
        )
    
    elif query.data == 'analyze_10':
        await query.edit_message_text("🔍 Анализирую 10 последних записей... Подождите немного...")
        results = await analyzer_bot.analyze_channel(10)
        await query.edit_message_text(
            analyzer_bot.format_results(results),
            parse_mode='Markdown'
        )
    
    elif query.data == 'refresh':
        if analyzer_bot.last_analysis:
            await query.edit_message_text(
                analyzer_bot.format_results(analyzer_bot.last_analysis),
                parse_mode='Markdown'
            )
        else:
            await query.edit_message_text("❌ Нет сохраненных данных. Сначала выполните анализ.")

async def analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /analyze"""
    logger.info(f"Команда /analyze от пользователя {update.effective_user.id}")
    
    try:
        # Проверяем, указано ли количество постов
        if context.args and context.args[0].isdigit():
            count = min(int(context.args[0]), 20)  # Максимум 20 постов
            await update.message.reply_text(f"🔍 Анализирую {count} последних записей...")
            results = await analyzer_bot.analyze_channel(count)
        else:
            await update.message.reply_text("🔍 Анализирую 5 последних записей...")
            results = await analyzer_bot.analyze_channel(5)
        
        await update.message.reply_text(
            analyzer_bot.format_results(results),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f"Ошибка в команде analyze: {e}")
        await update.message.reply_text(f"❌ Ошибка: {e}")

# Flask маршруты для проверки активности
@app.route('/')
def home():
    return "Bot is running! 🤖"

@app.route('/health')
def health():
    return "OK", 200

def run_bot():
    """Запуск бота в отдельном потоке"""
    try:
        # Создаем приложение бота
        application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Добавляем обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("analyze", analyze_command))
        application.add_handler(CallbackQueryHandler(button_handler))
        
        logger.info("🚀 Бот запущен и готов к работе")
        
        # Запускаем бота (polling)
        application.run_polling(allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

@app.before_first_request
def before_first_request():
    """Действия перед первым запросом"""
    logger.info("🔥 Flask приложение запущено, инициализирую бота...")
    
    # Запускаем бота в фоне
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    # Подключаем Telethon в главном потоке
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    async def init_telethon():
        await telethon_client.start()
        logger.info("✅ Telethon клиент подключен")
    
    loop.run_until_complete(init_telethon())

if __name__ == "__main__":
    # Получаем порт из переменных окружения
    port = int(os.environ.get('PORT', 5000))
    
    # Запускаем Flask
    app.run(host='0.0.0.0', port=port)
