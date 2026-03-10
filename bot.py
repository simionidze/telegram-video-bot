import os
import asyncio
import logging
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument
from datetime import datetime

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

# Глобальные переменные
telethon_client = None
analyzer_bot = None

class VideoAnalyzerBot:
    def __init__(self, client):
        self.client = client
        self.stats_cache = {}
        self.last_analysis = None
        logger.info("🤖 Бот инициализирован")
    
    async def analyze_channel(self, posts_count=5):
        """Анализ канала"""
        try:
            logger.info(f"🔍 Начинаю анализ {posts_count} последних записей...")
            
            # Проверяем подключение Telethon
            if not self.client.is_connected():
                await self.client.connect()
            
            channel = await self.client.get_entity(CHANNEL_USERNAME)
            logger.info(f"📢 Канал найден: {channel.title}")
            
            # Получаем последние сообщения
            messages = []
            async for message in self.client.iter_messages(channel, limit=posts_count):
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
                    comments = await self.client.get_messages(
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

# Flask маршруты
@app.route('/')
def home():
    return "Bot is running! 🤖"

@app.route('/health')
def health():
    return "OK", 200

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
        if context.args and context.args[0].isdigit():
            count = min(int(context.args[0]), 20)
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

def main():
    """Главная функция"""
    global telethon_client, analyzer_bot
    
    # Создаем event loop для главного потока
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Создаем Telethon клиент ПОСЛЕ создания event loop
    telethon_client = TelegramClient('bot_session', API_ID, API_HASH)
    analyzer_bot = VideoAnalyzerBot(telethon_client)
    
    # Запускаем Telethon КАК БОТА
    async def start_telethon():
        # ВАЖНО: Используем бота, а не пользователя!
        await telethon_client.start(bot_token=TELEGRAM_BOT_TOKEN)
        logger.info("✅ Telethon клиент подключен как бот")
    
    loop.run_until_complete(start_telethon())
    
    # Создаем приложение бота в том же loop
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    
    # Добавляем обработчики
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("analyze", analyze_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    
    logger.info("🚀 Бот запущен и готов к работе")
    
    # Запускаем Flask в отдельном потоке
    def run_flask():
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port, use_reloader=False)
    
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Запускаем бота (это блокирующий вызов)
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
