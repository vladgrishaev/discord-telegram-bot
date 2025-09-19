import discord
from discord.ext import commands
from telethon import TelegramClient, events
import asyncio
import os
import re
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from dotenv import load_dotenv

# ------------------ ЗАГРУЗКА .ENV ------------------
load_dotenv()

# ------------------ НАСТРОЙКИ ------------------

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ROLE_ID = int(os.getenv("ROLE_ID", "0"))
DISCORD_ID_RAIN = int(os.getenv("DISCORD_ID_RAIN", "0"))
DISCORD_NEXT_RAIN = int(os.getenv("DISCORD_NEXT_RAIN", "0"))
BANDIT_CHANNEL_ID = int(os.getenv("BANDIT_CHANNEL_ID", "0"))

# Bandit Monitor
CHECK_WORD = os.getenv("CHECK_WORD", "Burmalda69")
MIN_SCRAP = float(os.getenv("MIN_SCRAP", "100"))
SITE_URL = os.getenv("SITE_URL", "https://bandit.camp/")
CHROME_DRIVER_PATH = os.getenv("CHROME_DRIVER_PATH", "/usr/local/bin/chromedriver")

# Telegram
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

CHANNEL_MAPPING = {
    "Rustmagici": int(os.getenv("CHANNEL_Rustmagici", "0")),
    "Banditcampi": int(os.getenv("CHANNEL_Banditcampi", "0")),
    "Upgraderi": int(os.getenv("CHANNEL_Upgraderi", "0"))
}

MESSAGE_IDS = {}
NOTIFICATION_SENT = {}

# ------------------ ЛОГИ ------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger("combined_bot")

# ------------------ DISCORD ------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ TELEGRAM ------------------
telegram_client = None

# ------------------ BANDIT MONITOR ------------------
class BanditMonitor:
    def __init__(self):
        self.last_message_id = None
        self.driver = None
        self.setup_driver()

    def setup_driver(self):
        try:
            # Проверяем существование драйвера
            if not os.path.exists(CHROME_DRIVER_PATH):
                logger.error(f"ChromeDriver не найден по пути: {CHROME_DRIVER_PATH}")
                return

            service = Service(CHROME_DRIVER_PATH)
            options = Options()
            
            # Настройки для headless режима на Linux
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-plugins")
            options.add_argument("--disable-images")
            options.add_argument("--log-level=3")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])
            options.add_experimental_option('useAutomationExtension', False)

            logger.info(f"Инициализация Chrome с драйвером: {CHROME_DRIVER_PATH}")
            self.driver = webdriver.Chrome(service=service, options=options)
            logger.info("Chrome драйвер успешно создан")
            
            # Устанавливаем таймауты
            self.driver.set_page_load_timeout(30)
            self.driver.implicitly_wait(10)
            
            logger.info(f"Загружаю сайт: {SITE_URL}")
            self.driver.get(SITE_URL)

            # Ждем загрузки чата
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CLASS_NAME, "chat-messages"))
            )
            logger.info("Bandit чат успешно загружен")
            
        except Exception as e:
            logger.error(f"Ошибка при настройке драйвера: {e}")
            self.driver = None

    def check_last_message(self):
        if not self.driver:
            return None
            
        try:
            # Проверяем наличие Rakeback Rain
            rakeback_blocks = self.driver.find_elements(By.CSS_SELECTOR, "div.chat-rain")
            if rakeback_blocks:
                logger.debug("Rakeback Rain обнаружен → пропускаем")
                return None

            # Находим контейнер чата
            chat_container = self.driver.find_element(By.CLASS_NAME, "chat-messages")
            messages = chat_container.find_elements(By.CSS_SELECTOR, 'p.message-content')
            
            if not messages:
                logger.debug("Сообщения в чате не найдены")
                return None

            last_msg = messages[-1]
            msg_id = last_msg.get_attribute('id') or hash(last_msg.text[:50])

            # Проверяем, не обработали ли уже это сообщение
            if msg_id == self.last_message_id:
                return None

            self.last_message_id = msg_id
            msg_text = last_msg.text.strip()

            # Проверка кодового слова
            if CHECK_WORD.lower() in msg_text.lower():
                logger.info(f"Найдено кодовое слово: {CHECK_WORD}")
                return {'type': 'word', 'text': msg_text, 'msg_id': msg_id}

            # Проверка rain
            try:
                parent_element = last_msg.find_element(By.XPATH, '..')
                parent_html = parent_element.get_attribute('innerHTML')
                
                if 'tipped' in parent_html and 'into the rain' in parent_html:
                    scrap_pattern = r'<span[^>]*class="[^"]*font-weight-bold[^"]*">([0-9,\.]+)</span>'
                    scrap_match = re.search(scrap_pattern, parent_html)
                    
                    if scrap_match:
                        amount_str = scrap_match.group(1).replace(',', '.')
                        try:
                            amount = float(amount_str)
                            if amount >= MIN_SCRAP:
                                logger.info(f"Найден rain: {amount} scrap")
                                return {'type': 'rain', 'amount': amount, 'text': msg_text, 'msg_id': msg_id}
                        except ValueError as ve:
                            logger.warning(f"Не удалось преобразовать сумму: {amount_str}")
            except Exception as pe:
                logger.debug(f"Ошибка при разборе родительского элемента: {pe}")

        except Exception as e:
            logger.error(f"Ошибка при проверке сообщения Bandit: {e}")
            
        return None

    def close(self):
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Chrome драйвер закрыт")
            except Exception as e:
                logger.error(f"Ошибка при закрытии драйвера: {e}")

# ------------------ DISCORD СОБЫТИЯ ------------------
@bot.event
async def on_ready():
    logger.info(f'Discord бот {bot.user} успешно запущен!')
    # Запускаем мониторинг Bandit чата
    bot.loop.create_task(monitor_bandit_chat())

@bot.event
async def on_error(event, *args, **kwargs):
    logger.error(f"Ошибка в Discord событии {event}: {args}")

# ------------------ УТИЛИТЫ ------------------
def check_for_large_numbers(content):
    if not content:
        return False
        
    patterns = [
        (r'\b\d+(?:\.\d+)?\b', lambda x: float(x)),
        (r'\b\d+,\d+\b', lambda x: float(x.replace(',', '.'))),
        (r'\b\d+(?:\s+\d+)+\b', lambda x: float(x.replace(' ', '')))
    ]
    
    for pattern, parser in patterns:
        for num_str in re.findall(pattern, content):
            try:
                if parser(num_str) > 100:
                    logger.debug(f"Найдено число больше 100: {num_str}")
                    return True
            except ValueError:
                continue
    return False

async def forward_to_discord(message, discord_channel_id):
    try:
        channel = bot.get_channel(discord_channel_id)
        if not channel:
            logger.error(f"Канал Discord с ID {discord_channel_id} не найден")
            return

        content = message.message if message.message else "Медиа без текста"
        should_tag_role = check_for_large_numbers(content)
        
        if message.media:
            try:
                file_path = await telegram_client.download_media(message.media, file="temp_media")
                discord_file = discord.File(file_path)
                discord_message = await channel.send(content=content, file=discord_file)
                MESSAGE_IDS[(message.chat_id, message.id)] = (discord_channel_id, discord_message.id)
                os.remove(file_path)
                
                if should_tag_role and (message.chat_id, message.id) not in NOTIFICATION_SENT:
                    await channel.send(f"<@&{ROLE_ID}> рейн нормальный")
                    NOTIFICATION_SENT[(message.chat_id, message.id)] = True
                return
            except Exception as e:
                logger.error(f"Ошибка при обработке медиа: {e}")
                
        discord_message = await channel.send(content)
        MESSAGE_IDS[(message.chat_id, message.id)] = (discord_channel_id, discord_message.id)
        
        if should_tag_role and (message.chat_id, message.id) not in NOTIFICATION_SENT:
            await channel.send(f"<@&{ROLE_ID}> нормальный рейн")
            NOTIFICATION_SENT[(message.chat_id, message.id)] = True
            
    except Exception as e:
        logger.error(f"Ошибка при пересылке в Discord: {e}")

async def update_discord_message(message):
    try:
        if (message.chat_id, message.id) in MESSAGE_IDS:
            discord_channel_id, discord_message_id = MESSAGE_IDS[(message.chat_id, message.id)]
            channel = bot.get_channel(discord_channel_id)
            
            if channel:
                discord_message = await channel.fetch_message(discord_message_id)
                content = message.message if message.message else "Медиа без текста"
                should_tag_role = check_for_large_numbers(content)
                
                await discord_message.edit(content=content)
                
                if should_tag_role and (message.chat_id, message.id) not in NOTIFICATION_SENT:
                    await channel.send(f"<@&{ROLE_ID}> нормальный рейн")
                    NOTIFICATION_SENT[(message.chat_id, message.id)] = True
                    
    except Exception as e:
        logger.error(f"Ошибка при обновлении сообщения: {e}")

# ------------------ BANDIT МОНИТОР ------------------
async def monitor_bandit_chat():
    await bot.wait_until_ready()
    
    # Проверяем настройки Bandit мониторинга
    if BANDIT_CHANNEL_ID == 0:
        logger.warning("BANDIT_CHANNEL_ID не настроен, мониторинг Bandit отключен")
        return
        
    bandit_channel = bot.get_channel(BANDIT_CHANNEL_ID)
    if not bandit_channel:
        logger.error(f"Канал Discord для Bandit с ID {BANDIT_CHANNEL_ID} не найден")
        return
        
    logger.info(f"Bandit канал найден: {bandit_channel.name}")

    while not bot.is_closed():
        try:
            if not bandit_monitor or not bandit_monitor.driver:
                logger.warning("Bandit monitor не инициализирован, пропускаем проверку")
                await asyncio.sleep(5)
                continue
                
            result = bandit_monitor.check_last_message()
            
            if result:
                msg_key = f"bandit_{result['msg_id']}"
                
                if msg_key not in NOTIFICATION_SENT:
                    if result['type'] == 'word':
                        await bandit_channel.send(f"<@&{DISCORD_NEXT_RAIN}> найдено кодовое слово: {CHECK_WORD}")
                        NOTIFICATION_SENT[msg_key] = True
                        logger.info("Отправлено уведомление о кодовом слове")
                        
                    elif result['type'] == 'rain':
                        await bandit_channel.send(f"<@&{DISCORD_ID_RAIN}> Next {result['amount']}!")
                        NOTIFICATION_SENT[msg_key] = True
                        logger.info(f"Отправлено уведомление о rain: {result['amount']}")
                        
        except Exception as e:
            logger.error(f"Ошибка в мониторинге Bandit чата: {e}")
            
        await asyncio.sleep(1)

# ------------------ TELEGRAM ОБРАБОТЧИКИ ------------------
async def setup_telegram_handlers():
    for tg_channel, discord_channel_id in CHANNEL_MAPPING.items():
        @telegram_client.on(events.NewMessage(chats=tg_channel))
        async def telegram_new_handler(event, dc_id=discord_channel_id, tg_ch=tg_channel):
            logger.info(f"Новое сообщение из {tg_ch}")
            await forward_to_discord(event.message, dc_id)
    
    for tg_channel in CHANNEL_MAPPING.keys():
        @telegram_client.on(events.MessageEdited(chats=tg_channel))
        async def telegram_edit_handler(event, tg_ch=tg_channel):
            logger.info(f"Отредактировано сообщение в {tg_ch}")
            await update_discord_message(event.message)

# ------------------ MAIN ------------------
async def main():
    global telegram_client, bandit_monitor
    
    try:
        # Создаем Bandit monitor
        bandit_monitor = BanditMonitor()
        
        # Создаем и запускаем Telegram клиент
        telegram_client = TelegramClient('session_name', TELEGRAM_API_ID, TELEGRAM_API_HASH)
        await telegram_client.start()
        logger.info("Telegram клиент запущен!")
        
        # Настраиваем обработчики Telegram
        await setup_telegram_handlers()
        logger.info(f"Настроена пересылка из каналов: {', '.join(CHANNEL_MAPPING.keys())}")
        
        # Запускаем Discord бота
        await bot.start(DISCORD_TOKEN)
        
    except Exception as e:
        logger.error(f"Критическая ошибка в main: {e}")
        raise

if __name__ == "__main__":
    bandit_monitor = None
    telegram_client = None
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
    finally:
        # Корректное закрытие ресурсов
        logger.info("Завершение работы бота...")
        
        if bandit_monitor:
            bandit_monitor.close()
            
        if telegram_client and telegram_client.is_connected():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(telegram_client.disconnect())
                loop.close()
            except Exception as e:
                logger.error(f"Ошибка при закрытии Telegram клиента: {e}")
        
        logger.info("Бот остановлен")