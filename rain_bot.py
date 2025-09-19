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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

CHANNEL_MAPPING = {
    "Rustmagici": int(os.getenv("CHANNEL_Rustmagici", "0")),
    "Banditcampi": int(os.getenv("CHANNEL_Banditcampi", "0")),
    "Upgraderi": int(os.getenv("CHANNEL_Upgraderi", "0"))
}

MESSAGE_IDS = {}
NOTIFICATION_SENT = {}

# ------------------ ЛОГИ ------------------
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("combined_bot")

# ------------------ DISCORD ------------------
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# ------------------ TELEGRAM ------------------
telegram_client = TelegramClient('session_name', TELEGRAM_API_ID, TELEGRAM_API_HASH)
# Если используешь бот-токен:
if TELEGRAM_BOT_TOKEN:
    telegram_client.start(bot_token=TELEGRAM_BOT_TOKEN)

# ------------------ BANDIT MONITOR ------------------
class BanditMonitor:
    def __init__(self):
        self.last_message_id = None
        self.driver = None
        self.setup_driver()

    def setup_driver(self):
        try:
            service = Service(CHROME_DRIVER_PATH)
            options = Options()
            options.add_argument("--headless=new")
            options.add_argument("--disable-gpu")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--log-level=3")
            options.add_argument("--window-size=1920,1080")
            options.add_experimental_option("excludeSwitches", ["enable-logging"])

            self.driver = webdriver.Chrome(service=service, options=options)
            self.driver.get(SITE_URL)

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
            rakeback_blocks = self.driver.find_elements(By.CSS_SELECTOR, "div.chat-rain")
            if rakeback_blocks:
                logger.info("Rakeback Rain обнаружен → пропускаем")
                return None

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

            if CHECK_WORD.lower() in msg_text.lower():
                return {'type': 'word', 'text': msg_text, 'msg_id': msg_id}

            parent_html = last_msg.find_element(By.XPATH, '..').get_attribute('innerHTML')
            if 'tipped' in parent_html and 'into the rain' in parent_html:
                scrap_pattern = r'<span[^>]*class="[^"]*font-weight-bold[^"]*">([0-9,\.]+)</span>'
                scrap_match = re.search(scrap_pattern, parent_html)
                if scrap_match:
                    amount_str = scrap_match.group(1).replace(',', '.')
                    try:
                        amount = float(amount_str)
                        if amount >= MIN_SCRAP:
                            return {'type': 'rain', 'amount': amount, 'text': msg_text, 'msg_id': msg_id}
                    except ValueError:
                        pass
        except Exception as e:
            logger.error(f"Ошибка при проверке сообщения Bandit: {e}")
        return None

    def close(self):
        if self.driver:
            self.driver.quit()

bandit_monitor = BanditMonitor()

# ------------------ DISCORD СОБЫТИЯ ------------------
@bot.event
async def on_ready():
    print(f'Discord бот {bot.user} успешно запущен!')
    logger.info(f"Запустился как {bot.user}")
    bot.loop.create_task(monitor_bandit_chat())

# ------------------ УТИЛИТЫ ------------------
def check_for_large_numbers(content):
    patterns = [
        (r'\b\d+(?:\.\d+)?\b', lambda x: float(x)),
        (r'\b\d+,\d+\b', lambda x: float(x.replace(',', '.'))),
        (r'\b\d+(?:\s+\d+)+\b', lambda x: float(x.replace(' ', '')))
    ]
    for pattern, parser in patterns:
        for num_str in re.findall(pattern, content):
            try:
                if parser(num_str) > 100:
                    return True
            except ValueError:
                continue
    return False

async def forward_to_discord(message, discord_channel_id):
    channel = bot.get_channel(discord_channel_id)
    if channel:
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
                    await channel.send(f"<@&{ROLE_ID}> ")
                    NOTIFICATION_SENT[(message.chat_id, message.id)] = True
                return
            except Exception as e:
                print(f"Ошибка при обработке медиа: {e}")
        discord_message = await channel.send(content)
        MESSAGE_IDS[(message.chat_id, message.id)] = (discord_channel_id, discord_message.id)
        if should_tag_role and (message.chat_id, message.id) not in NOTIFICATION_SENT:
            await channel.send(f"<@&{ROLE_ID}> ")
            NOTIFICATION_SENT[(message.chat_id, message.id)] = True
    else:
        print(f"Не удалось найти канал Discord с ID {discord_channel_id}")

async def update_discord_message(message):
    if (message.chat_id, message.id) in MESSAGE_IDS:
        discord_channel_id, discord_message_id = MESSAGE_IDS[(message.chat_id, message.id)]
        channel = bot.get_channel(discord_channel_id)
        if channel:
            try:
                discord_message = await channel.fetch_message(discord_message_id)
                content = message.message if message.message else "Медиа без текста"
                should_tag_role = check_for_large_numbers(content)
                await discord_message.edit(content=content)
                if should_tag_role and (message.chat_id, message.id) not in NOTIFICATION_SENT:
                    await channel.send(f"<@&{ROLE_ID}>")
                    NOTIFICATION_SENT[(message.chat_id, message.id)] = True
            except Exception as e:
                print(f"Ошибка при обновлении сообщения: {e}")

# ------------------ BANDIT МОНИТОР ------------------
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
            if result:
                msg_key = f"bandit_{result['msg_id']}"
                if msg_key in NOTIFICATION_SENT:
                    await asyncio.sleep(1)
                    continue

                if result['type'] == 'word':
                    await bandit_channel.send(f"<@&{DISCORD_NEXT_RAIN}> найдено кодовое слово: {CHECK_WORD}")
                    NOTIFICATION_SENT[msg_key] = True
                elif result['type'] == 'rain':
                    await bandit_channel.send(f"<@&{DISCORD_ID_RAIN}> Next {result['amount']}!")
                    NOTIFICATION_SENT[msg_key] = True
        except Exception as e:
            logger.error(f"Ошибка в мониторинге Bandit чата: {e}")
        await asyncio.sleep(1)

# ------------------ TELEGRAM ОБРАБОТЧИКИ ------------------
for tg_channel, discord_channel_id in CHANNEL_MAPPING.items():
    @telegram_client.on(events.NewMessage(chats=tg_channel))
    async def telegram_new_handler(event, discord_channel_id=discord_channel_id):
        await forward_to_discord(event.message, discord_channel_id)

for tg_channel in CHANNEL_MAPPING.keys():
    @telegram_client.on(events.MessageEdited(chats=tg_channel))
    async def telegram_edit_handler(event):
        await update_discord_message(event.message)

# ------------------ MAIN ------------------
async def main():
    await telegram_client.start()
    print("Telegram клиент запущен!")
    print(f"Настроена пересылка из каналов: {', '.join(CHANNEL_MAPPING.keys())}")
    await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания")
    finally:
        if bandit_monitor:
            bandit_monitor.close()
        if telegram_client.is_connected():
            asyncio.run(telegram_client.disconnect())
        logger.info("Бот остановлен")
