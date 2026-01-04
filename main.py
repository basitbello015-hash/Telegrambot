"""
MAIN ENTRY POINT - COMPLETE FOREX SIGNAL BOT
POLLING VERSION WITH TIME-BASED PAIR FILTERING
"""

import os
import asyncio
import logging
from datetime import datetime
import signal

from dotenv import load_dotenv

# Import from our modules
from time_utils import get_nigeria_time, format_full_nigeria_datetime, get_active_pairs, get_timezone_info
from bot_handlers import RobustForexSignalBot

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('forex_signal_bot_polling.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

async def main():
    """Main entry point"""
    load_dotenv()
    
    # Get configuration
    TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
    ADMIN_CHAT_ID = os.getenv('ADMIN_CHAT_ID')
    GROQ_API_KEY = os.getenv('GROQ_API_KEY')
    
    # Validate
    if not TELEGRAM_TOKEN:
        logger.error("❌ Missing TELEGRAM_TOKEN")
        return
    if not ADMIN_CHAT_ID:
        logger.error("❌ Missing ADMIN_CHAT_ID")
        return
    
    try:
        ADMIN_CHAT_ID = int(ADMIN_CHAT_ID)
    except ValueError:
        logger.error("❌ ADMIN_CHAT_ID must be an integer")
        return
    
    # Display Nigeria timezone info
    nigeria_time = get_nigeria_time()
    logger.info(f"🚀 Starting Robust Forex Signal Bot (Time-Based Filtering)...")
    logger.info(f"⏰ Nigeria Timezone: {get_timezone_info()}")
    logger.info(f"⏰ Current Nigeria Time: {format_full_nigeria_datetime(nigeria_time)}")
    logger.info("🔒 Using Portalocker for robust instance management")
    logger.info("📰 News Analysis: ❌ DISABLED")
    logger.info("🕐 Time-Based Filtering: ✅ ENABLED (12-hour format)")
    
    # Show active pairs at startup
    ALL_PAIRS = [
        'EUR/USD', 'USD/JPY', 'GBP/USD', 'AUD/USD', 'USD/CAD',
        'USD/CHF', 'XAU/USD', 'NZD/USD', 'EUR/JPY', 'GBP/JPY'
    ]

    active_pairs = get_active_pairs(nigeria_time)

    logger.info(
        f"📊 Active Pairs Now: {len(active_pairs)}/{len(ALL_PAIRS)}"
    )
    
    if active_pairs:
         logger.info("✅ Active pairs: " + ", ".join(active_pairs))
    else:
         logger.info("⏸️ No pairs currently in trading hours")
    
    # Create bot instance
    bot = RobustForexSignalBot(
        telegram_token=TELEGRAM_TOKEN,
        admin_id=ADMIN_CHAT_ID,
        groq_api_key=GROQ_API_KEY
    )
    
    # Setup and start polling
    try:
        await bot.setup_polling()
        await bot.start_polling()
    except KeyboardInterrupt:
        logger.info("Received interrupt, shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        await bot.stop()

# Run the bot
if __name__ == "__main__":
    def signal_handler(sig, frame):
        print("\n🛑 Received shutdown signal")
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n✅ Bot stopped by user")
