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

# ------------------ ЗАГРУЗКА .env ------------------
load_dotenv()

# ------------------ НАСТРОЙКИ ------------------

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
ROLE_ID = int(os.getenv("ROLE_ID", "0"))
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
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("combined_bot")

# ------------------ DISCORD ------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ TELEGRAM ------------------
telegram_client = TelegramClient('session_name', TELEGRAM_API_ID, TELEGRAM_API_HASH)

# ------------------ BANDIT MONITOR ------------------
class BanditMonitor:
    def __init__(self):
        self.last_message_id = None
        self.driver = None
        self.setup_driver()
        self.print_last_messages(5)

    def setup_driver(self):
        try:
            service = Service(CHROME_DRIVER_PATH)
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--log-level=3")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])

            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.get(SITE_URL)

            # Ждём блок с чатом
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CLASS_NAME, "chat-messages"))
            )
            logger.info("Bandit чат загружен")
        except Exception as e:
            logger.error(f"Ошибка при настройке драйвера: {e}")
            self.driver = None

    def check_last_message(self):
        if not self.driver:
            return None
        try:
            chat_container = self.driver.find_element(By.CLASS_NAME, "chat-messages")
            messages = chat_container.find_elements(By.CSS_SELECTOR, 'p.message-content')
            if not messages:
                return None

            last_msg = messages[-1]
            msg_id = last_msg.id if hasattr(last_msg, 'id') else last_msg.text[:50]

            if msg_id == self.last_message_id:
                return None

            self.last_message_id = msg_id
            msg_text = last_msg.text.strip()

            # Проверка кодового слова
            if CHECK_WORD.lower() in msg_text.lower():
                return {'type': 'word', 'text': msg_text}

            # Проверка rain
            parent_html = last_msg.find_element(By.XPATH, '..').get_attribute('innerHTML')
            if 'tipped' in parent_html and 'into the rain' in parent_html:
                scrap_pattern = r'<span[^>]*class="[^"]*font-weight-bold[^"]*">([0-9,\.]+)</span>'
                scrap_match = re.search(scrap_pattern, parent_html)
                if scrap_match:
                    amount_str = scrap_match.group(1).replace(',', '.')
                    try:
                        amount = float(amount_str)
                        if amount >= MIN_SCRAP:
                            return {'type': 'rain', 'amount': amount, 'text': msg_text}
                    except ValueError:
                        pass
        except Exception as e:
            logger.error(f"Ошибка при проверке сообщения: {e}")
        return None

    def print_last_messages(self, count=5):
        """Вывод последних N сообщений для проверки"""
        if not self.driver:
            logger.warning("Драйвер не инициализирован")
            return
        try:
            chat_container = self.driver.find_element(By.CLASS_NAME, "chat-messages")
            messages = chat_container.find_elements(By.CSS_SELECTOR, 'p.message-content')
            logger.info(f"Последние {count} сообщений в чате Bandit:")
            for msg in messages[-count:]:
                print(msg.text.strip())
        except Exception as e:
            logger.error(f"Ошибка при выводе последних сообщений: {e}")

    def close(self):
        if self.driver:
            self.driver.quit()

# ------------------ MONITORING TASK ------------------
bandit_monitor = BanditMonitor()

@bot.event
async def on_ready():
    logger.info(f"Discord бот {bot.user} успешно запущен!")
    bot.loop.create_task(monitor_bandit_chat())

async def monitor_bandit_chat():
    await bot.wait_until_ready()
    bandit_channel = bot.get_channel(BANDIT_CHANNEL_ID)
    if not bandit_channel:
        logger.error(f"Не найден канал Discord для Bandit с ID {BANDIT_CHANNEL_ID}")
        return
    logger.info(f"Bandit канал найден: {bandit_channel.name}")

    while not bot.is_closed():
        try:
            result = bandit_monitor.check_last_message()
            if result and bandit_channel:
                role_mention = f"<@&{ROLE_ID}>"
                if result['type'] == 'word':
                    await bandit_channel.send(f"{role_mention} найдено кодовое слово: {CHECK_WORD}")
                elif result['type'] == 'rain':
                    await bandit_channel.send(f"{role_mention} Rain на {result['amount']} scrap!")
        except Exception as e:
            logger.error(f"Ошибка в мониторинге Bandit чата: {e}")
        await asyncio.sleep(1)

# ------------------ TELEGRAM HANDLERS ------------------
for tg_channel, discord_channel_id in CHANNEL_MAPPING.items():
    @telegram_client.on(events.NewMessage(chats=tg_channel))
    async def telegram_new_handler(event, discord_channel_id=discord_channel_id):
        content = event.message.message if event.message.message else "Медиа без текста"
        print(f"Новое сообщение из {tg_channel}: {content[:50]}...")

# ------------------ MAIN ------------------
async def main():
    await telegram_client.start()
    logger.info(f"Telegram клиент запущен! Каналы: {', '.join(CHANNEL_MAPPING.keys())}")
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен вручную")
    finally:
        if bandit_monitor:
            bandit_monitor.close()
        if telegram_client.is_connected():
            asyncio.run(telegram_client.disconnect())
