import os
import asyncio
import logging
import threading
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from telethon import TelegramClient
from telethon.tl.types import MessageMediaDocument
from datetime import datetime, timedelta
import pytz

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

# Flask приложение для поддержания активности
app = Flask(__name__)

# Глобальные переменные
telethon_client = None
analyzer_bot = None

class VideoAnalyzerBot:
    def __init__(self, client):
        self.client = client
        self.last_analysis = None
        logger.info("🤖 Бот инициализирован")
    
    async def analyze_last_5_days(self):
        """Анализ записей за последние 5 дней"""
        try:
            # Получаем текущее время и время 5 дней назад
            now = datetime.now(pytz.UTC)
            five_days_ago = now - timedelta(days=5)
            
            logger.info(f"🔍 Анализирую записи с {five_days_ago.strftime('%d.%m.%y')} по {now.strftime('%d.%m.%y')}")
            
            # Проверяем подключение Telethon
            if not self.client.is_connected():
                await self.client.connect()
            
            # Получаем канал
            channel = await self.client.get_entity('@' + CHANNEL_USERNAME)
            logger.info(f"📢 Канал найден: {channel.title}")
            
            # Получаем все сообщения за последние 5 дней
            messages = []
            async for message in self.client.iter_messages(channel, offset_date=now, reverse=False):
                if message.date < five_days_ago:
                    break
                if message.date >= five_days_ago:
                    messages.append(message)
            
            logger.info(f"📊 Найдено записей за период: {len(messages)}")
            
            results = {
                'analyzed_at': now.strftime('%d.%m.%Y %H:%M:%S'),
                'channel_title': channel.title,
                'period_start': five_days_ago.strftime('%d.%m.%y'),
                'period_end': now.strftime('%d.%m.%y'),
                'posts': []
            }
            
            # Анализируем каждое сообщение (пост)
            for message in messages:
                post_info = {
                    'date': message.date.strftime('%d.%m.%y'),
                    'post_title': self.extract_post_title(message),
                    'video_count': 0,
                    'videos': []
                }
                
                try:
                    # Получаем комментарии к посту
                    comments = await self.client.get_messages(
                        channel,
                        limit=500,  # Увеличиваем лимит для видео
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
                    
                    results['posts'].append(post_info)
                    logger.info(f"📝 Пост от {post_info['date']}: '{post_info['post_title']}' - {post_info['video_count']} видео")
                    
                except Exception as e:
                    logger.error(f"Ошибка при анализе комментариев к посту {message.id}: {e}")
                    post_info['video_count'] = 0
                    results['posts'].append(post_info)
            
            # Сортируем посты по дате (от новых к старым)
            results['posts'].sort(key=lambda x: x['date'], reverse=True)
            self.last_analysis = results
            
            logger.info(f"✅ Анализ завершен. Всего постов: {len(results['posts'])}")
            return results
            
        except Exception as e:
            logger.error(f"❌ Ошибка анализа: {e}")
            return {'error': str(e)}
    
    def extract_post_title(self, message):
        """Извлекает название поста из текста сообщения"""
        if message.text:
            # Берем первую строку или первые 50 символов
            first_line = message.text.split('\n')[0]
            if len(first_line) > 50:
                return first_line[:50] + "..."
            return first_line if first_line else "Без названия"
        return "Без названия"
    
    def format_results(self, results):
        """Форматирование результатов для отправки"""
        if 'error' in results:
            return f"❌ Ошибка: {results['error']}"
        
        if not results['posts']:
            return f"📊 За период {results['period_start']} - {results['period_end']} постов не найдено."
        
        text = f"📊 **Анализ канала {results['channel_title']}**\n"
        text += f"📅 Период: {results['period_start']} - {results['period_end']}\n"
        text += f"🕐 Анализ выполнен: {results['analyzed_at']}\n\n"
        
        text += "📋 **Найденные записи:**\n"
        text += "─" * 40 + "\n"
        
        for post in results['posts']:
            text += f"\n📅 **{post['date']}**"
            text += f"\n📝 **{post['post_title']}**"
            text += f"\n🎥 **{post['video_count']} видео файлов**\n"
            text += "─" * 40 + "\n"
        
        # Добавляем итоговую статистику
        total_videos = sum(post['video_count'] for post in results['posts'])
        text += f"\n📊 **ИТОГО:** {total_videos} видео в {len(results['posts'])} постах"
        
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
        [InlineKeyboardButton("📊 Анализ за 5 дней", callback_data='analyze_5days')],
        [InlineKeyboardButton("🔄 Обновить данные", callback_data='refresh')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"👋 Привет! Я бот для анализа видео в канале @{CHANNEL_USERNAME}.\n"
        f"Анализирую записи за последние 5 дней.\n"
        f"Выберите действие:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    
    if query.data == 'analyze_5days':
        await query.edit_message_text("🔍 Анализирую записи за последние 5 дней... Подождите немного...")
        results = await analyzer_bot.analyze_last_5_days()
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
        await update.message.reply_text("🔍 Анализирую записи за последние 5 дней...")
        results = await analyzer_bot.analyze_last_5_days()
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
