"""
BOT HANDLERS - Telegram bot command handlers and main bot class
"""

import os
import asyncio
import threading
import socket
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

# Try to import telegram components
try:
    from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application,
        CommandHandler,
        CallbackQueryHandler,
        ContextTypes,
        MessageHandler,
        filters
    )
    TELEGRAM_AVAILABLE = True
except ImportError as e:
    print(f"Warning: telegram package not installed: {e}")
    TELEGRAM_AVAILABLE = False

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=10)

# Import from our modules
from data_models import Signal, SignalType, BotSettings
from time_utils import get_nigeria_time, format_nigeria_time, format_full_nigeria_datetime, get_timezone_info
from time_utils import get_active_pairs, is_pair_in_trading_hours, get_pair_trading_info
from analysis_engine import TechnicalAnalyzer, AIAnalyzer, calculate_stop_loss_take_profit
from instance_manager import EnhancedInstanceManager

class RobustForexSignalBot:
    """Main Forex Signal Bot with robust instance management - ADJUSTABLE TP/SL/LOT"""
    
    def __init__(self, telegram_token: str, admin_id: int, groq_api_key: str):
        self.token = telegram_token
        self.admin_id = admin_id
        
        # Enhanced instance manager
        self.instance_manager = EnhancedInstanceManager(telegram_token)
        self.is_primary_instance = False
        
        # Settings management (ADJUSTABLE)
        self.settings = BotSettings()
        
        # Technical analyzer
        self.tech_analyzer = TechnicalAnalyzer()
        
        # Initialize AI analyzer only if API key is provided
        if groq_api_key:
            try:
                self.ai_analyzer = AIAnalyzer(groq_api_key)
            except Exception as e:
                logger.error(f"Failed to initialize AI analyzer: {e}")
                self.ai_analyzer = None
        else:
            self.ai_analyzer = None
            
        self.app = None
        self.bot = None
        self.running = False
        
        # Forex Pairs with symbols
        self.forex_pairs = {
            'EUR/USD': {'symbol': 'EURUSD=X', 'base': '🇪🇺', 'quote': '🇺🇸'},
            'USD/JPY': {'symbol': 'JPY=X', 'base': '🇺🇸', 'quote': '🇯🇵'},
            'GBP/USD': {'symbol': 'GBPUSD=X', 'base': '🇬🇧', 'quote': '🇺🇸'},
            'AUD/USD': {'symbol': 'AUDUSD=X', 'base': '🇦🇺', 'quote': '🇺🇸'},
            'USD/CAD': {'symbol': 'CAD=X', 'base': '🇺🇸', 'quote': '🇨🇦'},
            'USD/CHF': {'symbol': 'CHF=X', 'base': '🇺🇸', 'quote': '🇨🇭'},
            'XAU/USD': {'symbol': 'GC=F', 'base': '🏳️', 'quote': '🇺🇸'},  # Gold
            'NZD/USD': {'symbol': 'NZDUSD=X', 'base': '🇳🇿', 'quote': '🇺🇸'},
            'EUR/JPY': {'symbol': 'EURJPY=X', 'base': '🇪🇺', 'quote': '🇯🇵'},
            'GBP/JPY': {'symbol': 'GBPJPY=X', 'base': '🇬🇧', 'quote': '🇯🇵'},
        }
        
        # Current active signal
        self.current_signal = None
        self.signal_lock = threading.Lock()
        
        # Statistics
        self.stats = {
            'total_signals': 0,
            'auto_signals': 0,
            'manual_signals': 0,
            'forced_signals': 0,  # NEW: Track forced manual signals
            'last_auto_scan': None,
            'signals_today': 0,
            'instance_id': self.instance_manager.instance_id,
            'is_primary': False,
            'skipped_out_of_hours': 0,
            'manual_override_count': 0  # NEW: Track manual overrides
        }
        
        # Auto scan settings
        self.auto_scan_interval = 300  # 5 minutes
        self.min_confidence = 70  # For auto signals
        
        # Get timezone info
        self.timezone_info = get_timezone_info()
        
        logger.info(f"Robust Forex Signal Bot initialized (Instance: {self.instance_manager.instance_id})")
        logger.info(f"📊 Analyzing {len(self.forex_pairs)} forex pairs")
        logger.info(f"🤖 AI Analysis: {'Available' if self.ai_analyzer else 'Not available'}")
        logger.info(f"📈 Technical Analysis: {'Available' if TechnicalAnalyzer else 'Fallback mode'}")
        logger.info(f"⏰ Timezone: {self.timezone_info}")
        logger.info(f"📰 News Analysis: ❌ DISABLED")
        logger.info(f"💰 ADJUSTABLE Lot Size: {self.settings.get('lot_size', 0.01)}")
        logger.info(f"🛑 ADJUSTABLE SL: {self.settings.get('stop_loss_pips', 5.0)} pips")
        logger.info(f"🎯 ADJUSTABLE TP: {self.settings.get('take_profit_pips', 15.0)} pips")
        logger.info(f"🥇 Special XAU TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips")
        logger.info(f"⚡ MANUAL OVERRIDE: Enabled (threshold: {self.settings.get('manual_confidence_threshold', 50.0)}%)")
        logger.info("🕐 TIME-BASED FILTERING: ENABLED (Nigeria WAT time, 12-hour format)")
        logger.info("⚡ ALL SIGNALS USE ADJUSTABLE PARAMETERS")
    
    # ==================== SETUP AND POLLING ====================
    
    async def setup_polling(self):
        """Set up Telegram bot with polling and robust instance management"""
        try:
            # Try to acquire lock with retry mechanism
            logger.info("🔒 Attempting to acquire instance lock...")
            
            for attempt in range(15):  # 15 attempts over 30 seconds
                if self.instance_manager.acquire_lock(timeout=300):
                    self.is_primary_instance = True
                    self.stats['is_primary'] = True
                    logger.info(f"🎯 Instance {self.instance_manager.instance_id} is now PRIMARY")
                    
                    # Start lock renewer task
                    asyncio.create_task(self.lock_maintainer())
                    break
                else:
                    if attempt == 0:
                        # Show who holds the lock
                        lock_status = self.instance_manager.check_lock_status()
                        if lock_status.get('locked'):
                            holder = lock_status.get('instance_id', 'Unknown')
                            since = lock_status.get('since', get_nigeria_time())
                            if isinstance(since, datetime):
                                since_str = format_nigeria_time(since, include_timezone=False)
                            else:
                                since_str = str(since)
                            logger.info(f"⏳ Lock held by instance {holder} (since {since_str})")
                        else:
                            logger.info("⏳ Waiting for lock...")
                    
                    wait_time = 2 + (attempt * 0.5)  # Progressive backoff
                    await asyncio.sleep(wait_time)
            
            if not self.is_primary_instance:
                logger.warning("⚠️ Running as SECONDARY instance (read-only mode)")
                self.auto_scan_interval = 0  # Disable auto scanning
            
            # Create application
            self.app = Application.builder().token(self.token).build()
            self.bot = self.app.bot
            
            # Set command handlers
            self.setup_handlers()
            
            # Get current Nigeria time and active pairs
            nigeria_time = get_nigeria_time()
            active_pairs = get_active_pairs(nigeria_time)
            
            # Send welcome message to admin
            if self.is_primary_instance:
                welcome_msg = f"""🤖 *FOREX SIGNAL BOT STARTED (TIME-BASED FILTERING)*

✅ {len(self.forex_pairs)} forex pairs (time-filtered)
✅ Instance ID: `{self.instance_manager.instance_id}`
✅ Role: {'PRIMARY 🟢' if self.is_primary_instance else 'SECONDARY 🟡'}
✅ Time: {format_nigeria_time(nigeria_time, include_timezone=True)}

💰 *ADJUSTABLE PARAMETERS:*
• Lot Size: {self.settings.get('lot_size', 0.01)} (default 0.01)
• Stop Loss: {self.settings.get('stop_loss_pips', 5.0)} pips (default 5)
• Take Profit: {self.settings.get('take_profit_pips', 15.0)} pips (default 15)
• XAU/USD TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips (default 20)

⚡ *MANUAL OVERRIDE:*
• Threshold: {self.settings.get('manual_confidence_threshold', 50.0)}% confidence
• Command: /manual - Force signal even if below 70%

🕐 *CURRENT TRADING SESSION (Nigeria WAT):*
• Current Time: {format_nigeria_time(nigeria_time, include_timezone=True)}
• Active Pairs: {len(active_pairs)}/{len(self.forex_pairs)}
• Next Auto Scan: {self.auto_scan_interval // 60} minutes

📊 *Active Pairs Right Now:*
"""
                
                for pair in active_pairs[:5]:  # Show first 5 active pairs
                    is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
                    if is_active:
                        welcome_msg += f"• {pair}: ✅ {time_range}\n"
                
                if len(active_pairs) > 5:
                    welcome_msg += f"• ... and {len(active_pairs) - 5} more\n"
                
                welcome_msg += "\n⚡ Use /auto for best signal (within trading hours)"
                welcome_msg += "\n🔄 Use /manual to force signal (bypass threshold)"
                welcome_msg += "\n⚙️ Use /settings to adjust parameters"
                welcome_msg += "\n📅 Use /tradinghours to see all pair schedules"
                
                await self.send_admin(welcome_msg)
            
            # Start auto scanner (only for primary instance)
            if self.is_primary_instance:
                asyncio.create_task(self.auto_scanner())
            
            self.running = True
            logger.info("✅ Forex Signal Bot polling setup complete")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup polling: {e}", exc_info=True)
            if self.is_primary_instance:
                self.instance_manager.release_lock()
            raise
    
    async def lock_maintainer(self):
        """Maintain the lock by periodically renewing it"""
        while self.running and self.is_primary_instance:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                
                # Check if we still hold the lock
                if not self.instance_manager.is_primary():
                    logger.warning("⚠️ Lost primary status, attempting to reacquire lock...")
                    self.is_primary_instance = False
                    
                    # Try to reacquire lock
                    if self.instance_manager.acquire_lock(timeout=300):
                        self.is_primary_instance = True
                        logger.info(f"🔁 Regained primary status: {self.instance_manager.instance_id}")
                    else:
                        logger.warning("❌ Failed to regain primary status")
                        break
                else:
                    # Renew the lock
                    self.instance_manager.renew_lock(timeout=300)
                    
            except Exception as e:
                logger.error(f"Lock maintainer error: {e}")
                await asyncio.sleep(60)
    
    def setup_handlers(self):
        """Setup all command handlers"""
        handlers = [
            CommandHandler("start", self.cmd_start),
            CommandHandler("stop", self.cmd_stop),
            CommandHandler("auto", self.cmd_auto),
            CommandHandler("manual", self.cmd_manual),  # NEW: Manual override command
            CommandHandler("analyze", self.cmd_analyze),
            CommandHandler("fast", self.cmd_fast),
            CommandHandler("scan", self.cmd_scan),
            CommandHandler("status", self.cmd_status),
            CommandHandler("clear", self.cmd_clear),
            CommandHandler("pairs", self.cmd_pairs),
            CommandHandler("stats", self.cmd_stats),
            CommandHandler("help", self.cmd_help),
            CommandHandler("instance", self.cmd_instance),
            CommandHandler("lockstatus", self.cmd_lockstatus),
            CommandHandler("timezone", self.cmd_timezone),
            CommandHandler("tradinghours", self.cmd_tradinghours),
            CommandHandler("activepairs", self.cmd_activepairs),
            # ADJUSTABLE PARAMETERS COMMANDS
            CommandHandler("settings", self.cmd_settings),
            CommandHandler("setlot", self.cmd_setlot),
            CommandHandler("setsl", self.cmd_setsl),
            CommandHandler("settp", self.cmd_settp),
            CommandHandler("setxautp", self.cmd_setxautp),
            CommandHandler("setmanualthreshold", self.cmd_setmanualthreshold),  # NEW: Set manual threshold
            CommandHandler("resetparams", self.cmd_resetparams),
            CallbackQueryHandler(self.handle_callback),
        ]
        
        for handler in handlers:
            self.app.add_handler(handler)
    
    async def start_polling(self):
        """Start polling for updates"""
        try:
            logger.info("📡 Starting polling...")
            
            # Start the application
            await self.app.initialize()
            
            # Create updater for polling
            await self.app.updater.start_polling(
                poll_interval=2.0,
                timeout=30,
                bootstrap_retries=-1,
                read_timeout=30,
                write_timeout=30,
                connect_timeout=30,
                pool_timeout=30
            )
            
            # Start the bot
            await self.app.start()
            
            logger.info("✅ Bot is now running and polling for updates...")
            
            # Keep running until interrupted
            while self.running:
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("Received interrupt, shutting down...")
        except Exception as e:
            logger.error(f"Polling error: {e}", exc_info=True)
        finally:
            await self.stop()
    
    async def stop(self):
        """Clean shutdown"""
        logger.info("🛑 Shutting down bot...")
        self.running = False
        
        # Stop the application properly
        if self.app:
            try:
                await self.app.stop()
            except Exception as e:
                logger.error(f"Error stopping app: {e}")
        
        # Release instance lock
        if self.is_primary_instance:
            self.instance_manager.release_lock()
        
        logger.info("✅ Bot shutdown complete")
    
    # ==================== COMMAND HANDLERS ====================
    
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start command"""
        nigeria_time = get_nigeria_time()
        active_pairs = get_active_pairs(nigeria_time)
        
        await update.message.reply_text(
            f"""🤖 *FOREX SIGNAL BOT (TIME-BASED FILTERING)*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}
📊 *Active Pairs:* {len(active_pairs)}/{len(self.forex_pairs)} in trading hours

⚙️ **Current Settings:**
• Lot Size: {self.settings.get('lot_size', 0.01)}
• Stop Loss: {self.settings.get('stop_loss_pips', 5.0)} pips
• Take Profit: {self.settings.get('take_profit_pips', 15.0)} pips
• XAU/USD TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips
• Manual Threshold: {self.settings.get('manual_confidence_threshold', 50.0)}%

⚡ **Commands:**
• /auto - Auto signal (best active pair, ≥70% confidence)
• /manual - FORCE signal (best active pair, ≥{self.settings.get('manual_confidence_threshold', 50.0)}% confidence) ⚡
• /analyze [pair] - Detailed analysis (if in hours)
• /fast [pair] - Quick analysis (if in hours)
• /scan - Scan active pairs
• /tradinghours - View all pair schedules
• /activepairs - Current active pairs
• /status - Current signal status
• /pairs - Available pairs
• /stats - Bot statistics
• /settings - Adjust parameters
• /help - Show help

⚙️ **Adjust Parameters:**
• /setlot [size] - Set lot size (e.g., /setlot 0.01)
• /setsl [pips] - Set stop loss pips (e.g., /setsl 5)
• /settp [pips] - Set take profit pips (e.g., /settp 15)
• /setxautp [pips] - Set XAU/USD TP (e.g., /setxautp 20)
• /setmanualthreshold [%] - Set manual threshold (e.g., /setmanualthreshold 50)
• /resetparams - Reset to defaults

⚠️ *Signals use adjustable parameters*
⚠️ *Pairs are filtered by Nigeria trading hours*
⚡ *Use /manual to force signal when auto doesn't trigger*
""",
            parse_mode='Markdown'
        )
    
    async def cmd_manual(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """MANUAL OVERRIDE: Force signal from active pairs with lower confidence threshold"""
        if not self.is_primary_instance:
            await update.message.reply_text(
                "⚠️ *Secondary Instance*\n\n"
                "This instance is running in secondary mode.\n"
                "Only primary instance can generate signals.\n\n"
                "Use /analyze for manual analysis.",
                parse_mode='Markdown'
            )
            return
        
        if self.current_signal:
            expiry_time = self.current_signal.entry_time + timedelta(minutes=5)
            expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=True)
            
            await update.message.reply_text(
                f"⏳ *Signal Active*\n\n"
                f"Active signal for {self.current_signal.pair}\n"
                f"Expires: {expiry_nigeria}\n\n"
                f"Use /clear to remove or wait for expiry.",
                parse_mode='Markdown'
            )
            return
        
        nigeria_time = get_nigeria_time()
        active_pairs = get_active_pairs(nigeria_time)
        
        if not active_pairs:
            await update.message.reply_text(
                f"📭 *No Active Trading Pairs*\n\n"
                f"Current Nigeria Time: {format_nigeria_time(nigeria_time, include_timezone=True)}\n\n"
                f"No pairs are currently in their recommended trading hours.\n"
                f"Use `/tradinghours` to see pair schedules.\n"
                f"Use `/activepairs` to check current active pairs.\n\n"
                f"*Next Active Sessions:*\n"
                f"• 1 PM – 5 PM: EUR/USD, GBP/USD, USD/CHF, XAU/USD\n"
                f"• 2 AM – 4 AM: USD/JPY, EUR/JPY, GBP/JPY\n"
                f"• 11 PM – 7 AM: AUD/USD, NZD/USD\n"
                f"• 2:30 PM – 6 PM: USD/CAD",
                parse_mode='Markdown'
            )
            return
        
        manual_threshold = self.settings.get('manual_confidence_threshold', 50.0)
        
        await update.message.reply_text(
            f"⚡ *MANUAL OVERRIDE: Forcing signal from {len(active_pairs)} active pairs...*\n"
            f"Current Nigeria Time: {format_nigeria_time(nigeria_time, include_timezone=True)}\n"
            f"Minimum Confidence: {manual_threshold}% (lower threshold)\n"
            f"This may take 10-15 seconds...",
            parse_mode='Markdown'
        )
        
        try:
            # Use scan_active_pairs_manual which uses lower threshold
            signal = await self.scan_active_pairs_manual(SignalType.MANUAL_FORCE)
            
            if not signal:
                # Even with manual override, we still need SOME confidence
                await update.message.reply_text(
                    f"📭 *No signals found even with manual override*\n\n"
                    f"No active pairs meet even the manual threshold ({manual_threshold}%).\n"
                    f"Market conditions are extremely neutral.\n\n"
                    f"*Scanned {len(active_pairs)} active pairs*\n"
                    f"Try lowering the manual threshold with /setmanualthreshold command.",
                    parse_mode='Markdown'
                )
                return
            
            with self.signal_lock:
                self.current_signal = signal
            
            await self.send_forced_signal(update, signal)
            
            self.stats['forced_signals'] += 1
            self.stats['total_signals'] += 1
            self.stats['signals_today'] += 1
            self.stats['manual_override_count'] += 1
            
            # Log the forced signal
            logger.info(f"Manual override signal generated: {signal.pair} with {signal.confidence:.0f}% confidence")
            
        except Exception as e:
            logger.error(f"Manual override error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)[:200]}")
    
    async def cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show current settings"""
        nigeria_time = get_nigeria_time()
        
        message = f"""⚙️ *BOT SETTINGS*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}

💰 **Trade Parameters:**
• Lot Size: {self.settings.get('lot_size', 0.01)} (default: 0.01)
• Stop Loss: {self.settings.get('stop_loss_pips', 5.0)} pips (default: 5)
• Take Profit: {self.settings.get('take_profit_pips', 15.0)} pips (default: 15)
• XAU/USD TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips (default: 20)

⚡ **Manual Override Settings:**
• Manual Threshold: {self.settings.get('manual_confidence_threshold', 50.0)}% (default: 50)
• Auto Threshold: {self.min_confidence}% (fixed)

⚡ **Adjust Commands:**
• `/setlot [size]` - Set lot size (0.001 to 1.0)
• `/setsl [pips]` - Set stop loss (1 to 50 pips)
• `/settp [pips]` - Set take profit (5 to 100 pips)
• `/setxautp [pips]` - Set XAU/USD TP (10 to 100 pips)
• `/setmanualthreshold [%]` - Set manual threshold (20 to 100%)
• `/resetparams` - Reset all to defaults

📝 **Examples:**
• `/setlot 0.02` - Change lot to 0.02
• `/setsl 10` - Change SL to 10 pips
• `/settp 20` - Change TP to 20 pips
• `/setxautp 25` - Change XAU TP to 25 pips
• `/setmanualthreshold 40` - Change manual threshold to 40%

🔄 **Last Updated:** {self.settings.get('last_updated', 'Never')}
"""
        
        keyboard = [
            [
                InlineKeyboardButton("💰 SET LOT", callback_data="setlot_menu"),
                InlineKeyboardButton("🛑 SET SL", callback_data="setsl_menu")
            ],
            [
                InlineKeyboardButton("🎯 SET TP", callback_data="settp_menu"),
                InlineKeyboardButton("🥇 XAU TP", callback_data="setxautp_menu")
            ],
            [
                InlineKeyboardButton("⚡ MANUAL THRESHOLD", callback_data="setmanualthreshold_menu"),
                InlineKeyboardButton("🔄 RESET", callback_data="reset_settings")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def cmd_setmanualthreshold(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set manual confidence threshold"""
        if not context.args:
            await update.message.reply_text(
                "⚡ *Usage:* `/setmanualthreshold [percentage]`\n\n"
                "*Examples:*\n"
                "• `/setmanualthreshold 50` - Standard 50% threshold\n"
                "• `/setmanualthreshold 40` - Lower threshold (more signals)\n"
                "• `/setmanualthreshold 60` - Higher threshold (fewer but stronger signals)\n\n"
                "*Valid range:* 20 to 100%\n"
                "*Note:* This affects the /manual command threshold",
                parse_mode='Markdown'
            )
            return
        
        try:
            threshold = float(context.args[0])
            
            # Validate
            if threshold < 20 or threshold > 100:
                await update.message.reply_text(
                    "❌ *Invalid threshold*\n\n"
                    "Manual threshold must be between 20 and 100%\n"
                    "Example: `/setmanualthreshold 50`",
                    parse_mode='Markdown'
                )
                return
            
            old_threshold = self.settings.get('manual_confidence_threshold', 50.0)
            
            # Save setting
            if self.settings.set('manual_confidence_threshold', threshold, f"Changed via command by user {update.effective_user.id}"):
                await update.message.reply_text(
                    f"✅ *Manual Threshold Updated*\n\n"
                    f"Old: {old_threshold}%\n"
                    f"New: {threshold}%\n\n"
                    f"The /manual command will now require at least {threshold}% confidence.\n"
                    f"*Auto threshold remains at {self.min_confidence}%*",
                    parse_mode='Markdown'
                )
                
                # Log the change
                logger.info(f"Manual threshold changed: {old_threshold}% → {threshold}% by user {update.effective_user.id}")
            else:
                await update.message.reply_text("❌ Failed to save settings")
                
        except ValueError:
            await update.message.reply_text(
                "❌ *Invalid number*\n\n"
                "Please enter a valid number\n"
                "Example: `/setmanualthreshold 50`",
                parse_mode='Markdown'
            )
    
    async def cmd_setlot(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set lot size"""
        if not context.args:
            await update.message.reply_text(
                "❌ *Usage:* `/setlot [size]`\n\n"
                "*Examples:*\n"
                "• `/setlot 0.01` - Standard lot\n"
                "• `/setlot 0.005` - Small lot\n"
                "• `/setlot 0.1` - Large lot\n\n"
                "*Valid range:* 0.001 to 1.0",
                parse_mode='Markdown'
            )
            return
        
        try:
            lot_size = float(context.args[0])
            
            # Validate
            if lot_size < 0.001 or lot_size > 1.0:
                await update.message.reply_text(
                    "❌ *Invalid lot size*\n\n"
                    "Lot size must be between 0.001 and 1.0\n"
                    "Example: `/setlot 0.01`",
                    parse_mode='Markdown'
                )
                return
            
            old_lot = self.settings.get('lot_size', 0.01)
            
            # Save setting
            if self.settings.set('lot_size', lot_size, f"Changed via command by user {update.effective_user.id}"):
                await update.message.reply_text(
                    f"✅ *Lot Size Updated*\n\n"
                    f"Old: {old_lot}\n"
                    f"New: {lot_size}\n\n"
                    f"All future signals will use this lot size.",
                    parse_mode='Markdown'
                )
                
                # Log the change
                logger.info(f"Lot size changed: {old_lot} → {lot_size} by user {update.effective_user.id}")
            else:
                await update.message.reply_text("❌ Failed to save settings")
                
        except ValueError:
            await update.message.reply_text(
                "❌ *Invalid number*\n\n"
                "Please enter a valid number\n"
                "Example: `/setlot 0.01`",
                parse_mode='Markdown'
            )
    
    async def cmd_setsl(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set stop loss pips"""
        if not context.args:
            await update.message.reply_text(
                "❌ *Usage:* `/setsl [pips]`\n\n"
                "*Examples:*\n"
                "• `/setsl 5` - Standard 5 pip SL\n"
                "• `/setsl 10` - Wider 10 pip SL\n"
                "• `/setsl 3` - Tighter 3 pip SL\n\n"
                "*Valid range:* 1 to 50 pips",
                parse_mode='Markdown'
            )
            return
        
        try:
            sl_pips = float(context.args[0])
            
            # Validate
            if sl_pips < 1 or sl_pips > 50:
                await update.message.reply_text(
                    "❌ *Invalid stop loss*\n\n"
                    "Stop loss must be between 1 and 50 pips\n"
                    "Example: `/setsl 5`",
                    parse_mode='Markdown'
                )
                return
            
            old_sl = self.settings.get('stop_loss_pips', 5.0)
            
            # Save setting
            if self.settings.set('stop_loss_pips', sl_pips, f"Changed via command by user {update.effective_user.id}"):
                await update.message.reply_text(
                    f"✅ *Stop Loss Updated*\n\n"
                    f"Old: {old_sl} pips\n"
                    f"New: {sl_pips} pips\n\n"
                    f"All future signals will use this stop loss.",
                    parse_mode='Markdown'
                )
                
                # Log the change
                logger.info(f"Stop loss changed: {old_sl} → {sl_pips} by user {update.effective_user.id}")
            else:
                await update.message.reply_text("❌ Failed to save settings")
                
        except ValueError:
            await update.message.reply_text(
                "❌ *Invalid number*\n\n"
                "Please enter a valid number\n"
                "Example: `/setsl 5`",
                parse_mode='Markdown'
            )
    
    async def cmd_settp(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set take profit pips"""
        if not context.args:
            await update.message.reply_text(
                "❌ *Usage:* `/settp [pips]`\n\n"
                "*Examples:*\n"
                "• `/settp 15` - Standard 15 pip TP\n"
                "• `/settp 20` - Wider 20 pip TP\n"
                "• `/settp 10` - Tighter 10 pip TP\n\n"
                "*Valid range:* 5 to 100 pips",
                parse_mode='Markdown'
            )
            return
        
        try:
            tp_pips = float(context.args[0])
            
            # Validate
            if tp_pips < 5 or tp_pips > 100:
                await update.message.reply_text(
                    "❌ *Invalid take profit*\n\n"
                    "Take profit must be between 5 and 100 pips\n"
                    "Example: `/settp 15`",
                    parse_mode='Markdown'
                )
                return
            
            old_tp = self.settings.get('take_profit_pips', 15.0)
            
            # Save setting
            if self.settings.set('take_profit_pips', tp_pips, f"Changed via command by user {update.effective_user.id}"):
                await update.message.reply_text(
                    f"✅ *Take Profit Updated*\n\n"
                    f"Old: {old_tp} pips\n"
                    f"New: {tp_pips} pips\n\n"
                    f"All future signals will use this take profit.",
                    parse_mode='Markdown'
                )
                
                # Log the change
                logger.info(f"Take profit changed: {old_tp} → {tp_pips} by user {update.effective_user.id}")
            else:
                await update.message.reply_text("❌ Failed to save settings")
                
        except ValueError:
            await update.message.reply_text(
                "❌ *Invalid number*\n\n"
                "Please enter a valid number\n"
                "Example: `/settp 15`",
                parse_mode='Markdown'
            )
    
    async def cmd_setxautp(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Set XAU/USD take profit pips"""
        if not context.args:
            await update.message.reply_text(
                "❌ *Usage:* `/setxautp [pips]`\n\n"
                "*Examples:*\n"
                "• `/setxautp 20` - Standard 20 pip TP for XAU/USD\n"
                "• `/setxautp 25` - Wider 25 pip TP\n"
                "• `/setxautp 15` - Tighter 15 pip TP\n\n"
                "*Valid range:* 10 to 100 pips\n"
                "*Note:* This only affects XAU/USD (Gold)",
                parse_mode='Markdown'
            )
            return
        
        try:
            xau_tp_pips = float(context.args[0])
            
            # Validate
            if xau_tp_pips < 10 or xau_tp_pips > 100:
                await update.message.reply_text(
                    "❌ *Invalid XAU take profit*\n\n"
                    "XAU take profit must be between 10 and 100 pips\n"
                    "Example: `/setxautp 20`",
                    parse_mode='Markdown'
                )
                return
            
            old_xau_tp = self.settings.get('xau_take_profit_pips', 20.0)
            
            # Save setting
            if self.settings.set('xau_take_profit_pips', xau_tp_pips, f"Changed via command by user {update.effective_user.id}"):
                await update.message.reply_text(
                    f"✅ *XAU/USD Take Profit Updated*\n\n"
                    f"Old: {old_xau_tp} pips\n"
                    f"New: {xau_tp_pips} pips\n\n"
                    f"All future XAU/USD signals will use this take profit.",
                    parse_mode='Markdown'
                )
                
                # Log the change
                logger.info(f"XAU TP changed: {old_xau_tp} → {xau_tp_pips} by user {update.effective_user.id}")
            else:
                await update.message.reply_text("❌ Failed to save settings")
                
        except ValueError:
            await update.message.reply_text(
                "❌ *Invalid number*\n\n"
                "Please enter a valid number\n"
                "Example: `/setxautp 20`",
                parse_mode='Markdown'
            )
    
    async def cmd_resetparams(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Reset all parameters to defaults"""
        defaults = {
            'lot_size': 0.01,
            'stop_loss_pips': 5.0,
            'take_profit_pips': 15.0,
            'xau_take_profit_pips': 20.0,
            'manual_confidence_threshold': 50.0
        }
        
        old_settings = {
            'lot': self.settings.get('lot_size', 0.01),
            'sl': self.settings.get('stop_loss_pips', 5.0),
            'tp': self.settings.get('take_profit_pips', 15.0),
            'xau_tp': self.settings.get('xau_take_profit_pips', 20.0),
            'manual_threshold': self.settings.get('manual_confidence_threshold', 50.0)
        }
        
        # Reset all settings
        for key, value in defaults.items():
            self.settings.set(key, value, f"Reset to default by user {update.effective_user.id}")
        
        message = f"""🔄 *PARAMETERS RESET TO DEFAULTS*

📊 **Before Reset:**
• Lot Size: {old_settings['lot']}
• Stop Loss: {old_settings['sl']} pips
• Take Profit: {old_settings['tp']} pips
• XAU/USD TP: {old_settings['xau_tp']} pips
• Manual Threshold: {old_settings['manual_threshold']}%

✅ **After Reset:**
• Lot Size: {defaults['lot_size']}
• Stop Loss: {defaults['stop_loss_pips']} pips
• Take Profit: {defaults['take_profit_pips']} pips
• XAU/USD TP: {defaults['xau_take_profit_pips']} pips
• Manual Threshold: {defaults['manual_confidence_threshold']}%

All future signals will use these default parameters.
"""
        
        await update.message.reply_text(message, parse_mode='Markdown')
        logger.info(f"Parameters reset to defaults by user {update.effective_user.id}")
    
    async def cmd_tradinghours(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show trading hours for all pairs"""
        nigeria_time = get_nigeria_time()
        current_time_str = format_nigeria_time(nigeria_time, include_timezone=True)
        
        message = f"""🕐 *TRADING HOURS (Nigeria WAT Time)*

*Current Time:* {current_time_str}

| Pair | Trading Hours | Status |
|------|--------------|--------|
"""
        
        for pair in self.forex_pairs.keys():
            is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
            trading_info = get_pair_trading_info(pair)
            status = "✅ ACTIVE" if is_active else "⏸️ INACTIVE"
            move = trading_info.get('move', 'N/A')
            
            message += f"| {pair} | {time_range} | {status} |\n"
        
        message += f"\n*Note:* All times in Nigeria WAT (UTC+1) timezone\n"
        message += f"*Total Active Now:* {len(get_active_pairs(nigeria_time))}/{len(self.forex_pairs)}\n"
        message += f"*Use /activepairs* to see detailed info"
        
        # Split message if too long
        if len(message) > 4000:
            parts = [message[i:i+4000] for i in range(0, len(message), 4000)]
            for part in parts:
                await update.message.reply_text(part, parse_mode='Markdown')
        else:
            await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_activepairs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show currently active pairs with details"""
        nigeria_time = get_nigeria_time()
        active_pairs = get_active_pairs(nigeria_time)
        
        message = f"""📊 *ACTIVE TRADING PAIRS*

*Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}
*Active Pairs:* {len(active_pairs)}/{len(self.forex_pairs)}

"""
        
        if not active_pairs:
            message += "📭 *No pairs are currently in trading hours*\n\n"
            message += "Check back during these times:\n"
            message += "• 1 PM – 5 PM: EUR/USD, GBP/USD, USD/CHF, XAU/USD\n"
            message += "• 2 AM – 4 AM: USD/JPY, EUR/JPY, GBP/JPY\n"
            message += "• 11 PM – 7 AM: AUD/USD, NZD/USD\n"
            message += "• 2:30 PM – 6 PM: USD/CAD\n"
        else:
            for i, pair in enumerate(active_pairs, 1):
                is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
                trading_info = get_pair_trading_info(pair)
                
                message += f"{i}. *{pair}*\n"
                message += f"   🕐 {time_range}\n"
                message += f"   📈 Typical Move: {trading_info.get('move', 'N/A')}\n"
                message += f"   📝 {trading_info.get('notes', 'N/A')}\n"
                message += f"   💡 {trading_info.get('lot_recommendation', 'N/A')}\n\n"
        
        message += f"*Commands:*\n"
        message += f"• `/auto` - Scan active pairs for best signal (≥70%)\n"
        message += f"• `/manual` - Force signal from active pairs (≥{self.settings.get('manual_confidence_threshold', 50.0)}%)\n"
        message += f"• `/analyze [pair]` - Analyze specific active pair\n"
        message += f"• `/tradinghours` - View all pair schedules\n"
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Get auto signal from active pairs only"""
        if not self.is_primary_instance:
            await update.message.reply_text(
                "⚠️ *Secondary Instance*\n\n"
                "This instance is running in secondary mode.\n"
                "Only primary instance can generate auto signals.\n\n"
                "Use /analyze for manual analysis.",
                parse_mode='Markdown'
            )
            return
        
        if self.current_signal:
            expiry_time = self.current_signal.entry_time + timedelta(minutes=5)
            expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=True)
            
            await update.message.reply_text(
                f"⏳ *Signal Active*\n\n"
                f"Active signal for {self.current_signal.pair}\n"
                f"Expires: {expiry_nigeria}\n\n"
                f"Use /clear to remove or wait for expiry.",
                parse_mode='Markdown'
            )
            return
        
        nigeria_time = get_nigeria_time()
        active_pairs = get_active_pairs(nigeria_time)
        
        if not active_pairs:
            await update.message.reply_text(
                f"📭 *No Active Trading Pairs*\n\n"
                f"Current Nigeria Time: {format_nigeria_time(nigeria_time, include_timezone=True)}\n\n"
                f"No pairs are currently in their recommended trading hours.\n"
                f"Use `/tradinghours` to see pair schedules.\n"
                f"Use `/activepairs` to check current active pairs.\n\n"
                f"*Next Active Sessions:*\n"
                f"• 1 PM – 5 PM: EUR/USD, GBP/USD, USD/CHF, XAU/USD\n"
                f"• 2 AM – 4 AM: USD/JPY, EUR/JPY, GBP/JPY\n"
                f"• 11 PM – 7 AM: AUD/USD, NZD/USD\n"
                f"• 2:30 PM – 6 PM: USD/CAD",
                parse_mode='Markdown'
            )
            return
        
        await update.message.reply_text(
            f"🤖 *Scanning {len(active_pairs)} active pairs for best condition...*\n"
            f"Current Nigeria Time: {format_nigeria_time(nigeria_time, include_timezone=True)}\n"
            f"Minimum Confidence: {self.min_confidence}% (auto threshold)\n"
            f"This may take 10-15 seconds...",
            parse_mode='Markdown'
        )
        
        try:
            signal = await self.scan_active_pairs(SignalType.AUTO)
            
            if not signal:
                await update.message.reply_text(
                    f"📭 *No strong signals found*\n\n"
                    f"No active pairs meet the minimum confidence threshold ({self.min_confidence}%).\n"
                    f"Market may be consolidating.\n\n"
                    f"*Scanned {len(active_pairs)} active pairs*\n"
                    f"Try manual override with `/manual` (threshold: {self.settings.get('manual_confidence_threshold', 50.0)}%)",
                    parse_mode='Markdown'
                )
                return
            
            with self.signal_lock:
                self.current_signal = signal
            
            await self.send_signal(update, signal)
            
            self.stats['auto_signals'] += 1
            self.stats['total_signals'] += 1
            self.stats['signals_today'] += 1
            
        except Exception as e:
            logger.error(f"Auto signal error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)[:200]}")
    
    async def cmd_analyze(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Detailed analysis for specific pair (only if in trading hours)"""
        if self.current_signal:
            await update.message.reply_text(
                f"⏳ *Signal Active*\n\n"
                f"Cannot analyze while {self.current_signal.pair} is active.\n"
                f"Use /clear first or wait for expiry.",
                parse_mode='Markdown'
            )
            return
        
        args = context.args
        if not args:
            # Default to checking active pairs
            nigeria_time = get_nigeria_time()
            active_pairs = get_active_pairs(nigeria_time)
            
            if active_pairs:
                pair = active_pairs[0]  # Use first active pair
            else:
                await update.message.reply_text(
                    f"📭 *No Active Trading Pairs*\n\n"
                    f"Current Nigeria Time: {format_nigeria_time(nigeria_time, include_timezone=True)}\n\n"
                    f"No pairs are currently in trading hours.\n"
                    f"Use `/tradinghours` to see schedules.\n\n"
                    f"*Example:* `/analyze EUR/USD` (when in 1 PM – 5 PM WAT)",
                    parse_mode='Markdown'
                )
                return
        else:
            pair = self.parse_pair(args[0])
            if not pair:
                await update.message.reply_text(
                    f"❌ *Invalid pair: {args[0]}*\n\n"
                    "Use formats like: USDJPY, EUR/USD, XAUUSD\n"
                    "See /pairs for available pairs",
                    parse_mode='Markdown'
                )
                return
        
        # Check if pair is in trading hours
        nigeria_time = get_nigeria_time()
        is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
        
        if not is_active:
            trading_info = get_pair_trading_info(pair)
            await update.message.reply_text(
                f"⏸️ *Pair Not in Trading Hours*\n\n"
                f"*Pair:* {pair}\n"
                f"*Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}\n"
                f"*Trading Hours:* {time_range}\n\n"
                f"*Typical Move:* {trading_info.get('move', 'N/A')}\n"
                f"*Notes:* {trading_info.get('notes', 'N/A')}\n\n"
                f"⚠️ *Signal generation disabled outside trading hours*\n"
                f"Check back during: {time_range}\n\n"
                f"Use `/tradinghours` to see all pair schedules\n"
                f"Use `/activepairs` to see currently active pairs",
                parse_mode='Markdown'
            )
            self.stats['skipped_out_of_hours'] += 1
            return
        
        await update.message.reply_text(
            f"🔧 *Detailed analysis for {pair}...*\n"
            f"Trading Hours: {time_range}\n"
            "Getting market data and AI analysis...",
            parse_mode='Markdown'
        )
        
        try:
            signal = await self.analyze_single_pair(pair, SignalType.MANUAL_DETAILED)
            
            if not signal or signal.confidence < self.min_confidence:
                await update.message.reply_text(
                    f"📭 *No strong signal for {pair}*\n\n"
                    f"Confidence: {signal.confidence if signal else 0:.0f}% (min {self.min_confidence}% required)\n"
                    f"Trading Hours: {time_range}\n"
                    "Market conditions not favorable.\n\n"
                    "Try another pair or use /manual for lower threshold",
                    parse_mode='Markdown'
                )
                return
            
            with self.signal_lock:
                self.current_signal = signal
            
            await self.send_signal(update, signal)
            
            self.stats['manual_signals'] += 1
            self.stats['total_signals'] += 1
            self.stats['signals_today'] += 1
            
        except Exception as e:
            logger.error(f"Analyze error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)[:200]}")
    
    async def cmd_fast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Quick analysis for specific pair (only if in trading hours)"""
        if self.current_signal:
            await update.message.reply_text(
                f"⏳ *Signal Active*\n\n"
                f"Cannot analyze while {self.current_signal.pair} is active.",
                parse_mode='Markdown'
            )
            return
        
        args = context.args
        if not args:
            pair = "EUR/USD"
        else:
            pair = self.parse_pair(args[0])
            if not pair:
                await update.message.reply_text(
                    f"❌ *Invalid pair: {args[0]}*",
                    parse_mode='Markdown'
                )
                return
        
        # Check if pair is in trading hours
        nigeria_time = get_nigeria_time()
        is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
        
        if not is_active:
            await update.message.reply_text(
                f"⏸️ *{pair} not in trading hours*\n\n"
                f"Current: {format_nigeria_time(nigeria_time, include_timezone=True)}\n"
                f"Trading Hours: {time_range}\n\n"
                f"Try again during {time_range}",
                parse_mode='Markdown'
            )
            self.stats['skipped_out_of_hours'] += 1
            return
        
        await update.message.reply_text(
            f"⚡ *Quick analysis for {pair}...*\n"
            f"Trading Hours: {time_range}",
            parse_mode='Markdown'
        )
        
        try:
            signal = await self.analyze_single_pair(pair, SignalType.MANUAL_FAST)
            
            if not signal or signal.confidence < self.min_confidence:
                await update.message.reply_text(
                    f"📭 *No strong signal for {pair}*",
                    parse_mode='Markdown'
                )
                return
            
            with self.signal_lock:
                self.current_signal = signal
            
            await self.send_signal(update, signal)
            
            self.stats['manual_signals'] += 1
            self.stats['total_signals'] += 1
            self.stats['signals_today'] += 1
            
        except Exception as e:
            logger.error(f"Fast analysis error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)[:200]}")
    
    async def cmd_scan(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Scan active pairs and show results (no signal)"""
        nigeria_time = get_nigeria_time()
        active_pairs = get_active_pairs(nigeria_time)
        
        if not active_pairs:
            await update.message.reply_text(
                f"📭 *No Active Trading Pairs*\n\n"
                f"Current Nigeria Time: {format_nigeria_time(nigeria_time, include_timezone=True)}\n\n"
                f"No pairs are currently in trading hours.\n"
                f"Use `/tradinghours` to see schedules.",
                parse_mode='Markdown'
            )
            return
        
        await update.message.reply_text(
            f"🔍 *Scanning {len(active_pairs)} active pairs for conditions...*\n"
            f"This may take 15-20 seconds...",
            parse_mode='Markdown'
        )
        
        try:
            results = await self.scan_active_pairs_for_analysis()
            
            if not results:
                await update.message.reply_text(
                    "📭 *No analyzable pairs found*",
                    parse_mode='Markdown'
                )
                return
            
            current_time = format_nigeria_time(nigeria_time, include_timezone=True)
            
            message = f"📊 *ACTIVE PAIRS SCAN RESULTS*\n\n"
            message += f"*Current Nigeria Time:* {current_time}\n"
            message += f"*Active Pairs Scanned:* {len(results)}\n\n"
            
            for i, (pair, score, direction, confidence) in enumerate(results[:5], 1):
                emoji = "🟢" if "BUY" in direction else "🔴" if "SELL" in direction else "⚪"
                message += f"{i}. {emoji} *{pair}*\n"
                message += f"   📈 {direction} | ⚡ {confidence:.0f}% | 📊 {score:.1f}\n\n"
            
            if len(results) > 5:
                message += f"... and {len(results) - 5} more pairs\n\n"
            
            message += f"*Total Active Pairs:* {len(active_pairs)}\n\n"
            message += f"*Auto Threshold:* {self.min_confidence}%\n"
            message += f"*Manual Threshold:* {self.settings.get('manual_confidence_threshold', 50.0)}%"
            
            keyboard = []
            for pair, score, direction, confidence in results[:3]:
                if confidence >= self.settings.get('manual_confidence_threshold', 50.0):
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🚨 {pair} {direction[:4]}",
                            callback_data=f"analyze_{pair}"
                        )
                    ])
            
            keyboard.append([
                InlineKeyboardButton("🤖 AUTO", callback_data="auto_signal"),
                InlineKeyboardButton("⚡ MANUAL", callback_data="manual_signal"),
                InlineKeyboardButton("📊 STATUS", callback_data="status")
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"Scan error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)[:200]}")
    
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check current signal status"""
        nigeria_time = get_nigeria_time()
        
        if not self.current_signal:
            active_pairs = get_active_pairs(nigeria_time)
            
            message = f"""📭 *No Active Signal*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}
📊 *Active Pairs:* {len(active_pairs)}/{len(self.forex_pairs)}

You can generate a signal with:
• /auto - Auto signal (best active pair, ≥{self.min_confidence}%)
• /manual - FORCE signal (best active pair, ≥{self.settings.get('manual_confidence_threshold', 50.0)}%) ⚡
• /analyze [pair] - Detailed analysis
• /fast [pair] - Quick analysis

*Note:* Only pairs in trading hours will be analyzed.
"""
            
            if active_pairs:
                message += "\n*Currently Active Pairs:*\n"
                for pair in active_pairs[:5]:
                    is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
                    message += f"• {pair} ({time_range})\n"
                
                if len(active_pairs) > 5:
                    message += f"• ... and {len(active_pairs) - 5} more\n"
            
            await update.message.reply_text(message, parse_mode='Markdown')
            return
        
        signal = self.current_signal
        expiry_time = signal.entry_time + timedelta(minutes=5)
        time_remaining = expiry_time - datetime.now(timezone.utc)
        minutes = int(time_remaining.total_seconds() // 60)
        seconds = int(time_remaining.total_seconds() % 60)
        
        # Format times in Nigeria time
        entry_nigeria = format_nigeria_time(signal.entry_time, include_timezone=True)
        expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=True)
        
        # Calculate risk-reward ratio
        if signal.stop_loss_pips > 0:
            rr_ratio = signal.take_profit_pips / signal.stop_loss_pips
        else:
            rr_ratio = 0
        
        # Check if pair is still in trading hours
        is_active, time_range = is_pair_in_trading_hours(signal.pair, nigeria_time)
        status_emoji = "✅" if is_active else "⏸️"
        
        # Check if this is a forced signal
        signal_type_display = signal.signal_type.value
        if signal.is_forced:
            signal_type_display = "MANUAL FORCE ⚡"
        
        message = f"""📊 *ACTIVE SIGNAL STATUS*

📈 **Pair:** {signal.pair} {status_emoji}
🤖 **Type:** {signal_type_display}
🎯 **Direction:** {signal.direction}
⚡ **Confidence:** {signal.confidence:.0f}%

💰 **Trade Parameters (Current Settings):**
• Lot Size: {signal.lot_size} ⚡
• Stop Loss: {signal.stop_loss_pips} pips ⚡
• Take Profit: {signal.take_profit_pips} pips ⚡
• Risk-Reward: {rr_ratio:.2f}:1

⏰ **Timing:**
• Entry: {entry_nigeria}
• Expires: {expiry_nigeria}
• Remaining: {minutes}m {seconds}s
• Trading Hours: {time_range}
• Status: {'IN TRADING HOURS ✅' if is_active else 'OUTSIDE TRADING HOURS ⏸️'}

📊 **Analysis:**
• RSI: {signal.technicals.get('rsi', 0):.1f}
• MACD: {signal.technicals.get('macd_diff', 0):.4f}
• AI Model: {signal.ai_analysis.get('model_used', 'N/A') if signal.ai_analysis else 'N/A'}

⚠️ *One signal at a time*
"""
        
        keyboard = [
            [
                InlineKeyboardButton("🔄 VIEW SIGNAL", callback_data="view_signal"),
                InlineKeyboardButton("❌ CLEAR", callback_data="clear_signal")
            ],
            [
                InlineKeyboardButton("🤖 AUTO", callback_data="auto_signal"),
                InlineKeyboardButton("⚡ MANUAL", callback_data="manual_signal"),
                InlineKeyboardButton("📊 STATS", callback_data="stats")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def cmd_clear(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Clear current signal"""
        if not self.current_signal:
            await update.message.reply_text("✅ *No signal to clear*", parse_mode='Markdown')
            return
        
        pair = self.current_signal.pair
        with self.signal_lock:
            self.current_signal = None
        
        await update.message.reply_text(
            f"✅ *Signal cleared*\n\n"
            f"Cleared signal for {pair}\n"
            f"You can now generate a new signal.",
            parse_mode='Markdown'
        )
    
    async def cmd_pairs(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show all available pairs with trading hours"""
        nigeria_time = get_nigeria_time()
        active_pairs = get_active_pairs(nigeria_time)
        
        message = f"""💎 *AVAILABLE FOREX PAIRS*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}
📊 *Active Now:* {len(active_pairs)}/{len(self.forex_pairs)} pairs

"""
        
        for pair, info in self.forex_pairs.items():
            is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
            status = "🟢 ACTIVE" if is_active else "⚪ INACTIVE"
            message += f"• {pair} {info['base']}{info['quote']} - {time_range} ({status})\n"
        
        message += f"\n*Total:* {len(self.forex_pairs)} pairs\n\n"
        message += "*Usage:*\n"
        message += "/analyze [pair] - Detailed analysis (if in hours)\n"
        message += "/fast [pair] - Quick analysis (if in hours)\n"
        message += "/scan - Scan active pairs\n"
        message += "/auto - Bot picks best active pair (≥70%)\n"
        message += "/manual - Force signal (≥{self.settings.get('manual_confidence_threshold', 50.0)}%) ⚡\n"
        message += "/tradinghours - View all schedules\n"
        message += "/activepairs - Current active pairs\n\n"
        message += f"⚡ *Current Settings:*\n"
        message += f"• Lot Size: {self.settings.get('lot_size', 0.01)}\n"
        message += f"• Stop Loss: {self.settings.get('stop_loss_pips', 5.0)} pips\n"
        message += f"• Take Profit: {self.settings.get('take_profit_pips', 15.0)} pips\n"
        message += f"• XAU/USD TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips\n"
        message += f"• Manual Threshold: {self.settings.get('manual_confidence_threshold', 50.0)}%"
        
        keyboard = []
        for i in range(0, len(list(self.forex_pairs.keys())), 3):
            row_pairs = list(self.forex_pairs.keys())[i:i+3]
            buttons = []
            for pair in row_pairs:
                is_active, _ = is_pair_in_trading_hours(pair, nigeria_time)
                label = f"🟢 {pair}" if is_active else f"⚪ {pair}"
                buttons.append(InlineKeyboardButton(label, callback_data=f"analyze_{pair}"))
            keyboard.append(buttons)
        
        keyboard.append([
            InlineKeyboardButton("🤖 AUTO", callback_data="auto_signal"),
            InlineKeyboardButton("⚡ MANUAL", callback_data="manual_signal"),
            InlineKeyboardButton("🔍 SCAN", callback_data="scan_all"),
            InlineKeyboardButton("🕐 HOURS", callback_data="trading_hours")
        ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot statistics"""
        role = "PRIMARY" if self.is_primary_instance else "SECONDARY"
        nigeria_time = get_nigeria_time()
        active_pairs = get_active_pairs(nigeria_time)
        
        # Format last auto scan time
        last_scan = "Never"
        if self.stats['last_auto_scan']:
            last_scan = format_nigeria_time(self.stats['last_auto_scan'], include_timezone=True)
        
        message = f"""📊 *BOT STATISTICS*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}
📊 *Active Pairs:* {len(active_pairs)}/{len(self.forex_pairs)}

🤖 **Signal Count:**
• Total: {self.stats['total_signals']}
• Auto: {self.stats['auto_signals']} (≥{self.min_confidence}%)
• Manual: {self.stats['manual_signals']}
• Forced: {self.stats['forced_signals']} (≥{self.settings.get('manual_confidence_threshold', 50.0)}%) ⚡
• Today: {self.stats['signals_today']}
• Skipped (out of hours): {self.stats['skipped_out_of_hours']}
• Manual Overrides: {self.stats['manual_override_count']}

💰 **Current Trade Settings (Adjustable):**
• Lot Size: {self.settings.get('lot_size', 0.01)} (default: 0.01)
• Stop Loss: {self.settings.get('stop_loss_pips', 5.0)} pips (default: 5)
• Take Profit: {self.settings.get('take_profit_pips', 15.0)} pips (default: 15)
• XAU/USD TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips (default: 20)
• Manual Threshold: {self.settings.get('manual_confidence_threshold', 50.0)}% (default: 50)

🖥️ **Instance Info:**
• ID: {self.instance_manager.instance_id}
• Role: {role}
• Primary: {'✅ Yes' if self.is_primary_instance else '❌ No'}
• Host: {socket.gethostname()}
• Timezone: {self.timezone_info}

⚙️ **Configuration:**
• Pairs: {len(self.forex_pairs)}
• Auto Confidence: {self.min_confidence}% (fixed)
• Manual Confidence: {self.settings.get('manual_confidence_threshold', 50.0)}% (adjustable)
• Auto Interval: {self.auto_scan_interval // 60} minutes
• News Analysis: ❌ DISABLED
• Time Filtering: ✅ ENABLED

📈 **Current Status:**
• Active Signal: {'✅ Yes' if self.current_signal else '❌ No'}
• One at a time: ✅ Enforced
• Last Auto Scan: {last_scan}
"""
        
        keyboard = [
            [
                InlineKeyboardButton("🤖 AUTO NOW", callback_data="auto_signal"),
                InlineKeyboardButton("⚡ MANUAL", callback_data="manual_signal")
            ],
            [
                InlineKeyboardButton("📋 PAIRS", callback_data="pairs"),
                InlineKeyboardButton("⚙️ SETTINGS", callback_data="settings_menu")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def cmd_instance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show instance information"""
        try:
            import psutil
            pid = os.getpid()
            process = psutil.Process(pid)
            memory_mb = process.memory_info().rss / 1024 / 1024
            cpu_percent = process.cpu_percent()
        except ImportError:
            memory_mb = 0
            cpu_percent = 0
            psutil = None
        
        nigeria_time = get_nigeria_time()
        role = "PRIMARY 🟢" if self.is_primary_instance else "SECONDARY 🟡"
        status = "Active" if self.is_primary_instance else "Standby"
        
        message = f"""🖥️ *INSTANCE INFORMATION*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}

📊 **Instance Details:**
• ID: `{self.instance_manager.instance_id}`
• Role: {role}
• Status: {status}
• Host: {socket.gethostname()}
• PID: {os.getpid()}
• Memory: {memory_mb:.1f} MB
• CPU: {cpu_percent:.1f}%
• Timezone: {self.timezone_info}
• Running: {'✅ Yes' if self.running else '❌ No'}

⚙️ **Capabilities:**
• Auto Signals: {'✅ Enabled' if self.is_primary_instance else '❌ Disabled'}
• Manual Signals: ✅ Enabled
• Manual Override: ✅ Enabled (/manual command)
• News Analysis: ❌ Disabled
• AI Analysis: {'✅ Enabled' if self.ai_analyzer else '❌ Disabled'}
• Time Filtering: ✅ Enabled

💰 **Current Trade Settings:**
• Lot Size: {self.settings.get('lot_size', 0.01)} (default: 0.01)
• Stop Loss: {self.settings.get('stop_loss_pips', 5.0)} pips (default: 5)
• Take Profit: {self.settings.get('take_profit_pips', 15.0)} pips (default: 15)
• XAU/USD TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips (default: 20)
• Manual Threshold: {self.settings.get('manual_confidence_threshold', 50.0)}% (default: 50)
• *Parameters are adjustable via commands*

🔒 **Locking System:**
• Type: Portalocker (file-based)
• Lock File: {self.instance_manager.lock_file_path}
• Lock Held: {'✅ Yes' if self.instance_manager.lock_held else '❌ No'}

💡 **What does this mean?**
• *Primary*: Generates auto signals, holds lock
• *Secondary*: Manual commands only, no auto signals
• Multiple instances can run without conflicts
• Time filtering: Only analyzes pairs during Nigeria trading hours
• *Manual Override*: Use /manual to force signals when auto doesn't trigger
"""
        
        keyboard = [
            [
                InlineKeyboardButton("🔒 LOCK STATUS", callback_data="lock_status"),
                InlineKeyboardButton("🕐 TIMEZONE", callback_data="timezone_info")
            ],
            [
                InlineKeyboardButton("📊 STATS", callback_data="stats"),
                InlineKeyboardButton("⚙️ SETTINGS", callback_data="settings_menu")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def cmd_lockstatus(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show lock status"""
        lock_status = self.instance_manager.check_lock_status()
        nigeria_time = get_nigeria_time()
        
        if lock_status.get('locked'):
            holder = lock_status.get('instance_id', 'Unknown')
            host = lock_status.get('holder', 'Unknown')
            since = lock_status.get('since')
            expires = lock_status.get('expires')
            
            if isinstance(since, datetime):
                since_nigeria = format_nigeria_time(since, include_timezone=True)
                time_diff = (nigeria_time - since).total_seconds()
            else:
                since_nigeria = str(since)
                time_diff = 0
            
            if isinstance(expires, datetime):
                expires_nigeria = format_nigeria_time(expires, include_timezone=True)
            else:
                expires_nigeria = str(expires)
            
            is_current = holder == self.instance_manager.instance_id
            is_expired = lock_status.get('is_expired', False)
            
            message = f"""🔒 *LOCK STATUS*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}

📊 **Current Lock:**
• Holder: `{holder}` {'(THIS INSTANCE)' if is_current else ''}
• Host: {host}
• PID: {lock_status.get('pid', 'N/A')}
• Since: {since_nigeria} ({int(time_diff//60)}m {int(time_diff%60)}s ago)
• Expires: {expires_nigeria}
• Status: {'✅ VALID' if not is_expired else '⚠️ EXPIRED'}

🤖 **This Instance:**
• ID: `{self.instance_manager.instance_id}`
• Primary Role: {'✅ Yes' if self.is_primary_instance else '❌ No'}
• Lock Held: {'✅ Yes' if self.instance_manager.lock_held else '❌ No'}

💡 **Status Indicators:**
• 🟢 PRIMARY - This instance holds the lock
• 🟡 SECONDARY - Another instance holds lock
• 🔴 EXPIRED - Lock expired, can be taken
"""
            
            if is_expired and not is_current:
                keyboard = [
                    [
                        InlineKeyboardButton("🔓 TAKE LOCK", callback_data="take_lock"),
                        InlineKeyboardButton("🔄 REFRESH", callback_data="refresh_lock")
                    ]
                ]
            else:
                keyboard = [
                    [
                        InlineKeyboardButton("🔄 REFRESH", callback_data="refresh_lock"),
                        InlineKeyboardButton("🖥️ INSTANCE", callback_data="instance_info")
                    ]
                ]
        else:
            message = f"""🔓 *NO ACTIVE LOCK*

⏰ *Current Nigeria Time:* {format_nigeria_time(nigeria_time, include_timezone=True)}

📊 **Lock Status:**
• Locked: ❌ No
• Lock File: {self.instance_manager.lock_file_path}
• Available: ✅ Yes

🤖 **This Instance:**
• ID: `{self.instance_manager.instance_id}`
• Primary Role: {'✅ Yes' if self.is_primary_instance else '❌ No'}

💡 **You can take the lock to become primary!**
"""
            
            keyboard = [
                [
                    InlineKeyboardButton("🔓 TAKE LOCK", callback_data="take_lock"),
                    InlineKeyboardButton("🔄 REFRESH", callback_data="refresh_lock")
                ]
            ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def cmd_timezone(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show timezone information"""
        nigeria_time = get_nigeria_time()
        formatted_time = format_full_nigeria_datetime(nigeria_time)
        
        try:
            import pytz
            
            # Get current time in other major timezones
            major_timezones = {
                'Nigeria (WAT)': 'Africa/Lagos',
                'UTC': 'UTC',
                'New York (EST)': 'America/New_York',
                'London (GMT)': 'Europe/London',
                'Tokyo (JST)': 'Asia/Tokyo',
                'Sydney (AEST)': 'Australia/Sydney',
                'Dubai (GST)': 'Asia/Dubai',
                'Singapore (SGT)': 'Asia/Singapore',
                'Moscow (MSK)': 'Europe/Moscow'
            }
            
            other_times = ""
            for name, tz_name in major_timezones.items():
                try:
                    tz = pytz.timezone(tz_name)
                    other_time = nigeria_time.astimezone(tz)
                    other_times += f"• {name}: {other_time.strftime('%I:%M %p %Z')}\n"
                except:
                    pass
            
            message = f"""🕐 *TIMEZONE INFORMATION*

📍 **Bot Server Timezone:**
• Name: Africa/Lagos (WAT)
• Offset: UTC+1
• Current Nigeria Time: {formatted_time}

🌍 **Major Financial Centers:**
{other_times}
📝 *Note:* All trading hours are based on Nigeria WAT time (UTC+1).
"""
            
        except ImportError:
            message = f"""🕐 *TIMEZONE INFORMATION*

📍 **Bot Server Time:**
• Current Nigeria Time: {formatted_time}
• Using Nigeria WAT time (UTC+1)

📝 *Note:* All trading hours are based on Nigeria WAT time (UTC+1).
"""
        
        await update.message.reply_text(message, parse_mode='Markdown')
    
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show help"""
        help_text = f"""🆘 *HELP & INFORMATION*

⚡ *Quick Start:*
1. Check current active pairs with `/activepairs`
2. Use `/auto` for bot to find best condition from active pairs (≥{self.min_confidence}%)
3. If no auto signal, use `/manual` to force signal (≥{self.settings.get('manual_confidence_threshold', 50.0)}%)
4. Signal expires in 5 minutes
5. Wait for expiry before next signal

📋 *Available Commands:*
• `/start` - Welcome message
• `/stop` - Stop the bot gracefully
• `/auto` - Auto signal (best active pair, ≥{self.min_confidence}%)
• `/manual` - FORCE signal (best active pair, ≥{self.settings.get('manual_confidence_threshold', 50.0)}%) ⚡
• `/analyze [pair]` - Detailed analysis (if in hours)
• `/fast [pair]` - Quick analysis (if in hours)
• `/scan` - Scan active pairs
• `/status` - Current signal status
• `/clear` - Clear current signal
• `/pairs` - Available pairs with hours
• `/tradinghours` - View all pair schedules
• `/activepairs` - Current active pairs
• `/stats` - Bot statistics
• `/instance` - Instance information
• `/lockstatus` - Check lock status
• `/timezone` - Timezone information
• `/settings` - View and adjust parameters
• `/help` - This message

⚙️ *Adjustable Parameters Commands:*
• `/setlot [size]` - Set lot size (e.g., /setlot 0.01)
• `/setsl [pips]` - Set stop loss pips (e.g., /setsl 5)
• `/settp [pips]` - Set take profit pips (e.g., /settp 15)
• `/setxautp [pips]` - Set XAU/USD TP (e.g., /setxautp 20)
• `/setmanualthreshold [%]` - Set manual threshold (e.g., /setmanualthreshold 50)
• `/resetparams` - Reset all to defaults

🎯 *Signal Information:*
• Expiry: 5 minutes
• Martingale: 3 levels (5, 10, 15 minutes after)
• Analysis: Technical + AI only (no news)
• Time: All times shown in Nigeria WAT (UTC+1)
• Time Filtering: ✅ ENABLED (pairs only analyzed during recommended hours)

💰 *Current Trade Settings:*
• Lot Size: {self.settings.get('lot_size', 0.01)} (default: 0.01)
• Stop Loss: {self.settings.get('stop_loss_pips', 5.0)} pips (default: 5)
• Take Profit: {self.settings.get('take_profit_pips', 15.0)} pips (default: 15)
• XAU/USD TP: {self.settings.get('xau_take_profit_pips', 20.0)} pips (default: 20)
• Manual Threshold: {self.settings.get('manual_confidence_threshold', 50.0)}% (default: 50)

⚡ *Manual Override:*
• Use `/manual` when auto doesn't give signal
• Lower confidence threshold ({self.settings.get('manual_confidence_threshold', 50.0)}% vs {self.min_confidence}%)
• Adjust threshold with `/setmanualthreshold`
• Great for when markets are consolidating

⚠️ *Instance Management:*
• Only ONE primary instance at a time
• Portalocker ensures no conflicts
• Use /lockstatus to check lock
• Secondary instances can still use manual commands

🕐 *Trading Hours (Nigeria WAT):*
• 1 PM – 5 PM: EUR/USD, GBP/USD, USD/CHF, XAU/USD
• 2 AM – 4 AM: USD/JPY, EUR/JPY, GBP/JPY
• 11 PM – 7 AM: AUD/USD, NZD/USD
• 2:30 PM – 6 PM: USD/CAD
"""
        
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stop the bot gracefully"""
        await update.message.reply_text(
            "🛑 *Stopping bot...*\n\n"
            "The bot will shut down gracefully.\n"
            "This may take a few seconds...",
            parse_mode='Markdown'
        )
        
        logger.info("Stop command received, initiating shutdown...")
        self.running = False
        
        # Send confirmation
        await update.message.reply_text(
            "✅ *Bot shutdown initiated*\n\n"
            "Goodbye! 👋",
            parse_mode='Markdown'
        )
        
        # Stop the bot
        await self.stop()
    
    # ==================== CALLBACK HANDLER ====================
    
    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle inline button presses"""
        query = update.callback_query
        await query.answer()
        
        data = query.data
        
        if data == "auto_signal":
            context.args = []
            await self.cmd_auto(update, context)
        elif data == "manual_signal":
            context.args = []
            await self.cmd_manual(update, context)
        elif data.startswith("analyze_"):
            pair = data.split("_")[1]
            context.args = [pair]
            await self.cmd_analyze(update, context)
        elif data.startswith("fast_"):
            pair = data.split("_")[1]
            context.args = [pair]
            await self.cmd_fast(update, context)
        elif data == "scan_all":
            await self.cmd_scan(update, context)
        elif data == "status":
            await self.cmd_status(update, context)
        elif data == "view_signal":
            if self.current_signal:
                if self.current_signal.is_forced:
                    await self.send_forced_signal(update, self.current_signal)
                else:
                    await self.send_signal(update, self.current_signal)
            else:
                await query.edit_message_text("No active signal to view.")
        elif data == "view_signal_details":
            if self.current_signal:
                if self.current_signal.is_forced:
                    await self.send_forced_signal_detailed(update, self.current_signal)
                else:
                    await self.send_signal_detailed(update, self.current_signal)
            else:
                await query.edit_message_text("No active signal to view.")
        elif data == "clear_signal":
            await self.cmd_clear(update, context)
        elif data == "accept_signal":
            await query.edit_message_text("✅ Signal accepted! Good luck!")
        elif data == "instance_info":
            await self.cmd_instance(update, context)
        elif data == "stats":
            await self.cmd_stats(update, context)
        elif data == "lock_status":
            await self.cmd_lockstatus(update, context)
        elif data == "refresh_lock":
            await self.cmd_lockstatus(update, context)
        elif data == "take_lock":
            # Try to acquire lock
            if self.instance_manager.acquire_lock(timeout=300):
                self.is_primary_instance = True
                self.stats['is_primary'] = True
                logger.info(f"🔓 Lock acquired via callback: {self.instance_manager.instance_id}")
                
                # Start lock maintainer
                asyncio.create_task(self.lock_maintainer())
                
                await query.edit_message_text(
                    f"✅ *Lock Acquired!*\n\n"
                    f"Instance `{self.instance_manager.instance_id}` is now PRIMARY.\n"
                    f"Auto signals are now enabled.",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    "❌ *Failed to acquire lock*\n\n"
                    "Another instance holds the lock.\n"
                    "Use /lockstatus to check status.",
                    parse_mode='Markdown'
                )
        elif data == "timezone_info":
            await self.cmd_timezone(update, context)
        elif data == "trading_hours":
            await self.cmd_tradinghours(update, context)
        elif data == "pairs":
            await self.cmd_pairs(update, context)
        elif data == "active_pairs":
            await self.cmd_activepairs(update, context)
        elif data == "settings_menu":
            await self.cmd_settings(update, context)
        elif data == "setlot_menu":
            await query.edit_message_text(
                "💰 *SET LOT SIZE*\n\n"
                "Enter lot size in format:\n"
                "`/setlot 0.01`\n\n"
                "*Valid range:* 0.001 to 1.0\n"
                "*Examples:*\n"
                "• `/setlot 0.01` - Standard lot\n"
                "• `/setlot 0.005` - Small lot\n"
                "• `/setlot 0.1` - Large lot\n\n"
                "*Current lot:* " + str(self.settings.get('lot_size', 0.01)),
                parse_mode='Markdown'
            )
        elif data == "setsl_menu":
            await query.edit_message_text(
                "🛑 *SET STOP LOSS*\n\n"
                "Enter stop loss in pips:\n"
                "`/setsl 5`\n\n"
                "*Valid range:* 1 to 50 pips\n"
                "*Examples:*\n"
                "• `/setsl 5` - Standard 5 pip SL\n"
                "• `/setsl 10` - Wider 10 pip SL\n"
                "• `/setsl 3` - Tighter 3 pip SL\n\n"
                "*Current SL:* " + str(self.settings.get('stop_loss_pips', 5.0)) + " pips",
                parse_mode='Markdown'
            )
        elif data == "settp_menu":
            await query.edit_message_text(
                "🎯 *SET TAKE PROFIT*\n\n"
                "Enter take profit in pips:\n"
                "`/settp 15`\n\n"
                "*Valid range:* 5 to 100 pips\n"
                "*Examples:*\n"
                "• `/settp 15` - Standard 15 pip TP\n"
                "• `/settp 20` - Wider 20 pip TP\n"
                "• `/settp 10` - Tighter 10 pip TP\n\n"
                "*Current TP:* " + str(self.settings.get('take_profit_pips', 15.0)) + " pips",
                parse_mode='Markdown'
            )
        elif data == "setxautp_menu":
            await query.edit_message_text(
                "🥇 *SET XAU/USD TAKE PROFIT*\n\n"
                "Enter XAU/USD take profit in pips:\n"
                "`/setxautp 20`\n\n"
                "*Valid range:* 10 to 100 pips\n"
                "*Examples:*\n"
                "• `/setxautp 20` - Standard 20 pip TP\n"
                "• `/setxautp 25` - Wider 25 pip TP\n"
                "• `/setxautp 15` - Tighter 15 pip TP\n\n"
                "*Current XAU TP:* " + str(self.settings.get('xau_take_profit_pips', 20.0)) + " pips\n"
                "*Note:* This only affects XAU/USD (Gold)",
                parse_mode='Markdown'
            )
        elif data == "setmanualthreshold_menu":
            await query.edit_message_text(
                "⚡ *SET MANUAL THRESHOLD*\n\n"
                "Enter manual confidence threshold in %:\n"
                "`/setmanualthreshold 50`\n\n"
                "*Valid range:* 20 to 100%\n"
                "*Examples:*\n"
                "• `/setmanualthreshold 50` - Standard 50% threshold\n"
                "• `/setmanualthreshold 40` - Lower threshold (more signals)\n"
                "• `/setmanualthreshold 60` - Higher threshold (fewer but stronger signals)\n\n"
                "*Current Manual Threshold:* " + str(self.settings.get('manual_confidence_threshold', 50.0)) + "%\n"
                "*Auto Threshold:* " + str(self.min_confidence) + "% (fixed)\n"
                "*Note:* This affects the /manual command threshold",
                parse_mode='Markdown'
            )
        elif data == "reset_settings":
            await self.cmd_resetparams(update, context)
        elif data == "stop_bot":
            await self.cmd_stop(update, context)
        elif data == "home":
            context.args = []
            await self.cmd_start(update, context)
    
    # ==================== ANALYSIS METHODS ====================
    
    async def scan_active_pairs(self, signal_type: SignalType) -> Optional[Signal]:
        """Scan ONLY active pairs and return best signal with AUTO threshold"""
        try:
            nigeria_time = get_nigeria_time()
            pairs_to_scan = get_active_pairs(nigeria_time)
            
            if not pairs_to_scan:
                logger.info(f"No active pairs at {format_nigeria_time(nigeria_time)}")
                return None
            
            logger.info(f"Auto scan: Scanning {len(pairs_to_scan)} active pairs at {format_nigeria_time(nigeria_time)}")
            
            batch_size = 3  # Smaller batch size for better reliability
            all_signals = []
            
            for i in range(0, len(pairs_to_scan), batch_size):
                batch = pairs_to_scan[i:i+batch_size]
                
                tasks = []
                for pair in batch:
                    task = asyncio.create_task(self.analyze_single_pair(pair, signal_type))
                    tasks.append(task)
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Signal) and result.confidence >= self.min_confidence:
                        all_signals.append(result)
                    elif isinstance(result, Exception):
                        logger.error(f"Error in auto scan batch: {result}")
                
                await asyncio.sleep(1)  # Add delay between batches
            
            if not all_signals:
                logger.info(f"No signals met auto confidence threshold ({self.min_confidence}%)")
                return None
            
            all_signals.sort(key=lambda x: x.confidence, reverse=True)
            best_signal = all_signals[0]
            best_signal.is_forced = False  # Auto signal, not forced
            logger.info(f"Auto signal found: {best_signal.pair} with {best_signal.confidence:.0f}% confidence")
            return best_signal
            
        except Exception as e:
            logger.error(f"Auto scan error: {e}")
            return None
    
    async def scan_active_pairs_manual(self, signal_type: SignalType) -> Optional[Signal]:
        """Scan ONLY active pairs and return best signal with MANUAL threshold"""
        try:
            nigeria_time = get_nigeria_time()
            pairs_to_scan = get_active_pairs(nigeria_time)
            
            if not pairs_to_scan:
                logger.info(f"No active pairs at {format_nigeria_time(nigeria_time)}")
                return None
            
            logger.info(f"Manual override: Scanning {len(pairs_to_scan)} active pairs at {format_nigeria_time(nigeria_time)}")
            
            batch_size = 3  # Smaller batch size for better reliability
            all_signals = []
            
            for i in range(0, len(pairs_to_scan), batch_size):
                batch = pairs_to_scan[i:i+batch_size]
                
                tasks = []
                for pair in batch:
                    task = asyncio.create_task(self.analyze_single_pair_manual(pair, signal_type))
                    tasks.append(task)
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, Signal):
                        # Use manual threshold instead of auto threshold
                        manual_threshold = self.settings.get('manual_confidence_threshold', 50.0)
                        if result.confidence >= manual_threshold:
                            all_signals.append(result)
                    elif isinstance(result, Exception):
                        logger.error(f"Error in manual scan batch: {result}")
                
                await asyncio.sleep(1)  # Add delay between batches
            
            if not all_signals:
                logger.info(f"No signals met manual confidence threshold ({self.settings.get('manual_confidence_threshold', 50.0)}%)")
                return None
            
            all_signals.sort(key=lambda x: x.confidence, reverse=True)
            best_signal = all_signals[0]
            best_signal.is_forced = True  # Mark as forced signal
            logger.info(f"Manual override signal found: {best_signal.pair} with {best_signal.confidence:.0f}% confidence")
            return best_signal
            
        except Exception as e:
            logger.error(f"Manual scan error: {e}")
            return None
    
    async def analyze_single_pair(self, pair: str, signal_type: SignalType) -> Optional[Signal]:
        """Complete analysis for a single pair (only if in trading hours) - WITH ADJUSTABLE VALUES"""
        try:
            # First check if pair is in trading hours
            nigeria_time = get_nigeria_time()
            is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
            
            if not is_active:
                logger.warning(f"Pair {pair} not in trading hours at {format_nigeria_time(nigeria_time)}")
                return None
            
            logger.info(f"Analyzing pair: {pair} (in trading hours: {time_range})")
            
            data = await self.get_market_data(pair)
            if data.empty or len(data) < 5:
                logger.warning(f"Insufficient data for {pair}: {len(data)} rows")
                return None
            
            # Run technical analysis in thread pool
            loop = asyncio.get_event_loop()
            technicals = await loop.run_in_executor(
                executor,
                self.tech_analyzer.analyze,
                data
            )
            
            if not technicals or technicals.get('confidence', 0) < 30:
                logger.debug(f"No valid technicals for {pair}")
                return None
            
            # AI analysis (optional)
            ai_analysis = {}
            if self.ai_analyzer:
                analysis_type = "fast" if signal_type == SignalType.MANUAL_FAST else "detailed"
                try:
                    ai_analysis = await self.ai_analyzer.analyze_pair(pair, technicals, analysis_type)
                except Exception as e:
                    logger.error(f"AI analysis failed for {pair}: {e}")
                    ai_analysis = {"signal": "NEUTRAL", "confidence": 50, "model_used": "error"}
            
            tech_confidence = technicals['confidence']
            ai_confidence = ai_analysis.get('confidence', 50) if ai_analysis else 50
            
            # No news impact calculation
            final_confidence = (tech_confidence * 0.7 + ai_confidence * 0.3)
            final_confidence = min(100, max(0, final_confidence))
            
            if ai_analysis and ai_analysis.get('signal') and ai_analysis['signal'] != 'NEUTRAL':
                direction = ai_analysis['signal']
            else:
                direction = technicals['direction']
            
            entry_price = technicals['price']
            
            # ADJUSTABLE VALUES - from settings
            lot_size = self.settings.get('lot_size', 0.01)
            sl_pips = self.settings.get('stop_loss_pips', 5.0)
            tp_pips = self.settings.get('take_profit_pips', 15.0)
            
            # For XAU/USD (Gold), use special TP setting
            if pair == 'XAU/USD':
                tp_pips = self.settings.get('xau_take_profit_pips', 20.0)
            
            # Calculate SL and TP prices
            stop_loss_price, take_profit_price = calculate_stop_loss_take_profit(
                entry_price, direction, sl_pips, tp_pips, pair
            )
            
            entry_time = datetime.now(timezone.utc)
            martingale_levels = [
                entry_time + timedelta(minutes=5),
                entry_time + timedelta(minutes=10),
                entry_time + timedelta(minutes=15)
            ]
            
            score = (
                technicals['confidence'] * 0.5 +
                (ai_confidence if ai_analysis else 50) * 0.3 +
                (technicals.get('buy_signals', 0) - technicals.get('sell_signals', 0)) * 0.2
            )
            
            signal = Signal(
                pair=pair,
                signal_type=signal_type,
                direction=direction,
                entry_price=round(entry_price, 5),
                confidence=final_confidence,
                expiry_minutes=5,
                entry_time=entry_time,
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                stop_loss_pips=sl_pips,
                take_profit_pips=tp_pips,
                lot_size=lot_size,
                martingale_levels=martingale_levels,
                technicals=technicals,
                ai_analysis=ai_analysis,
                score=score,
                is_forced=False
            )
            
            logger.info(f"Analysis complete for {pair}: {direction} ({final_confidence:.0f}%)")
            logger.info(f"Using adjustable parameters - Lot: {lot_size}, SL: {sl_pips} pips, TP: {tp_pips} pips")
            logger.info(f"Calculated prices - Entry: {signal.entry_price}, SL: {signal.stop_loss}, TP: {signal.take_profit}")
            logger.info(f"Trading Hours: {time_range}")
            return signal
            
        except Exception as e:
            logger.error(f"Analyze single pair error for {pair}: {e}", exc_info=True)
            return None
    
    async def analyze_single_pair_manual(self, pair: str, signal_type: SignalType) -> Optional[Signal]:
        """Complete analysis for a single pair with MANUAL threshold - WITH ADJUSTABLE VALUES"""
        try:
            # First check if pair is in trading hours
            nigeria_time = get_nigeria_time()
            is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
            
            if not is_active:
                logger.warning(f"Pair {pair} not in trading hours at {format_nigeria_time(nigeria_time)}")
                return None
            
            logger.info(f"Manual analysis for pair: {pair} (in trading hours: {time_range})")
            
            data = await self.get_market_data(pair)
            if data.empty or len(data) < 5:
                logger.warning(f"Insufficient data for {pair}: {len(data)} rows")
                return None
            
            # Run technical analysis in thread pool
            loop = asyncio.get_event_loop()
            technicals = await loop.run_in_executor(
                executor,
                self.tech_analyzer.analyze,
                data
            )
            
            if not technicals or technicals.get('confidence', 0) < 30:
                logger.debug(f"No valid technicals for {pair}")
                return None
            
            # AI analysis (optional)
            ai_analysis = {}
            if self.ai_analyzer:
                analysis_type = "fast" if signal_type == SignalType.MANUAL_FAST else "detailed"
                try:
                    ai_analysis = await self.ai_analyzer.analyze_pair(pair, technicals, analysis_type)
                except Exception as e:
                    logger.error(f"AI analysis failed for {pair}: {e}")
                    ai_analysis = {"signal": "NEUTRAL", "confidence": 50, "model_used": "error"}
            
            tech_confidence = technicals['confidence']
            ai_confidence = ai_analysis.get('confidence', 50) if ai_analysis else 50
            
            # No news impact calculation
            final_confidence = (tech_confidence * 0.7 + ai_confidence * 0.3)
            final_confidence = min(100, max(0, final_confidence))
            
            if ai_analysis and ai_analysis.get('signal') and ai_analysis['signal'] != 'NEUTRAL':
                direction = ai_analysis['signal']
            else:
                direction = technicals['direction']
            
            entry_price = technicals['price']
            
            # ADJUSTABLE VALUES - from settings
            lot_size = self.settings.get('lot_size', 0.01)
            sl_pips = self.settings.get('stop_loss_pips', 5.0)
            tp_pips = self.settings.get('take_profit_pips', 15.0)
            
            # For XAU/USD (Gold), use special TP setting
            if pair == 'XAU/USD':
                tp_pips = self.settings.get('xau_take_profit_pips', 20.0)
            
            # Calculate SL and TP prices
            stop_loss_price, take_profit_price = calculate_stop_loss_take_profit(
                entry_price, direction, sl_pips, tp_pips, pair
            )
            
            entry_time = datetime.now(timezone.utc)
            martingale_levels = [
                entry_time + timedelta(minutes=5),
                entry_time + timedelta(minutes=10),
                entry_time + timedelta(minutes=15)
            ]
            
            score = (
                technicals['confidence'] * 0.5 +
                (ai_confidence if ai_analysis else 50) * 0.3 +
                (technicals.get('buy_signals', 0) - technicals.get('sell_signals', 0)) * 0.2
            )
            
            signal = Signal(
                pair=pair,
                signal_type=signal_type,
                direction=direction,
                entry_price=round(entry_price, 5),
                confidence=final_confidence,
                expiry_minutes=5,
                entry_time=entry_time,
                stop_loss=stop_loss_price,
                take_profit=take_profit_price,
                stop_loss_pips=sl_pips,
                take_profit_pips=tp_pips,
                lot_size=lot_size,
                martingale_levels=martingale_levels,
                technicals=technicals,
                ai_analysis=ai_analysis,
                score=score,
                is_forced=False  # Will be set to True in scan_active_pairs_manual
            )
            
            logger.info(f"Manual analysis complete for {pair}: {direction} ({final_confidence:.0f}%)")
            logger.info(f"Using adjustable parameters - Lot: {lot_size}, SL: {sl_pips} pips, TP: {tp_pips} pips")
            logger.info(f"Calculated prices - Entry: {signal.entry_price}, SL: {signal.stop_loss}, TP: {signal.take_profit}")
            logger.info(f"Trading Hours: {time_range}")
            return signal
            
        except Exception as e:
            logger.error(f"Manual analyze single pair error for {pair}: {e}", exc_info=True)
            return None
    
    async def scan_active_pairs_for_analysis(self) -> List[Tuple[str, float, str, float]]:
        """Quick scan active pairs for analysis (no signal generation)"""
        try:
            nigeria_time = get_nigeria_time()
            pairs_to_scan = get_active_pairs(nigeria_time)
            
            if not pairs_to_scan:
                return []
            
            results = []
            
            for pair in pairs_to_scan[:6]:  # Scan only 6 pairs for speed
                try:
                    # Check if still in trading hours (double-check)
                    is_active, time_range = is_pair_in_trading_hours(pair, nigeria_time)
                    if not is_active:
                        continue
                    
                    data = await self.get_market_data(pair)
                    if data.empty or len(data) < 5:
                        continue
                    
                    # Run technical analysis in thread pool
                    loop = asyncio.get_event_loop()
                    technicals = await loop.run_in_executor(
                        executor,
                        self.tech_analyzer.analyze,
                        data
                    )
                    
                    if not technicals:
                        continue
                    
                    score = technicals['confidence'] / 100.0
                    direction = technicals['direction']
                    
                    results.append((pair, score, direction, technicals['confidence']))
                    
                except Exception as e:
                    logger.warning(f"Error scanning {pair}: {e}")
                    continue
            
            results.sort(key=lambda x: x[1], reverse=True)
            return results
            
        except Exception as e:
            logger.error(f"Scan for analysis error: {e}")
            return []
    
    async def get_market_data(self, pair: str) -> pd.DataFrame:
        """Get market data for pair (only called if pair is in trading hours)"""
        import pandas as pd
        import yfinance as yf
        
        if pair not in self.forex_pairs:
            logger.warning(f"Pair {pair} not found in forex_pairs")
            return pd.DataFrame()
        
        try:
            symbol = self.forex_pairs[pair]['symbol']
            
            for attempt in range(3):
                try:
                    loop = asyncio.get_event_loop()
                    data = await loop.run_in_executor(
                        executor,
                        lambda: yf.download(
                            symbol,
                            period='2d',
                            interval='5m',
                            progress=False,
                            timeout=10,
                            threads=False
                        )
                    )
                    
                    if not data.empty and len(data) >= 5:
                        logger.debug(f"Data fetched for {pair}: {len(data)} rows")
                        return data
                    
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"Failed to fetch data for {pair}: {e}")
                    await asyncio.sleep(2)
            
            return pd.DataFrame()
            
        except Exception as e:
            logger.error(f"Market data error for {pair}: {e}")
            return pd.DataFrame()
    
    def parse_pair(self, input_str: str) -> Optional[str]:
        """Parse user input to valid pair"""
        clean = input_str.upper().replace('/', '').replace(' ', '')
        
        for pair in self.forex_pairs:
            if clean == pair.replace('/', ''):
                return pair
        
        variations = {
            'EURUSD': 'EUR/USD',
            'USDJPY': 'USD/JPY',
            'GBPUSD': 'GBP/USD',
            'AUDUSD': 'AUD/USD',
            'USDCAD': 'USD/CAD',
            'USDCHF': 'USD/CHF',
            'XAUUSD': 'XAU/USD',
            'NZDUSD': 'NZD/USD',
            'EURJPY': 'EUR/JPY',
            'GBPJPY': 'GBP/JPY',
        }
        
        return variations.get(clean)
    
    # ==================== SIGNAL SENDING METHODS ====================
    
    async def send_signal(self, update: Update, signal: Signal):
        """Send concise regular signal with essential info only"""
        pair_info = self.forex_pairs[signal.pair]
        expiry_time = signal.entry_time + timedelta(minutes=signal.expiry_minutes)
        expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=False)
        
        direction_emoji = "🟩" if "BUY" in signal.direction else "🟥"
        
        message = f"""🚨 *AUTO: {signal.pair} {signal.direction}* {direction_emoji}

Entry: {signal.entry_price:.5f}
TP: {signal.take_profit:.5f} (+{signal.take_profit_pips}p)
SL: {signal.stop_loss:.5f} (-{signal.stop_loss_pips}p)

Lot: {signal.lot_size} | Conf: {signal.confidence:.0f}%
Expires: {expiry_nigeria} (5min)

✅ Auto Signal | Above {self.min_confidence}% threshold
"""
        
        keyboard = [
            [
                InlineKeyboardButton("✅ ACCEPT", callback_data="accept_signal"),
                InlineKeyboardButton("📊 DETAILS", callback_data="view_signal_details")
            ],
            [
                InlineKeyboardButton("❌ CLEAR", callback_data="clear_signal"),
                InlineKeyboardButton("⏰ STATUS", callback_data="status")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def send_signal_detailed(self, update: Update, signal: Signal):
        """Send detailed regular signal (full info)"""
        pair_info = self.forex_pairs[signal.pair]
        entry_nigeria = format_nigeria_time(signal.entry_time, include_timezone=True)
        expiry_time = signal.entry_time + timedelta(minutes=signal.expiry_minutes)
        expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=True)
        
        # Get trading info
        nigeria_time = get_nigeria_time()
        is_active, time_range = is_pair_in_trading_hours(signal.pair, nigeria_time)
        
        direction_emoji = "🟩" if "BUY" in signal.direction else "🟥"
        
        martingale_text = ""
        for i, level_time in enumerate(signal.martingale_levels, 1):
            level_nigeria = format_nigeria_time(level_time, include_timezone=False)
            martingale_text += f"🔁 Level {i} → {level_nigeria}\n"
        
        # Calculate risk-reward ratio
        if signal.stop_loss_pips > 0:
            rr_ratio = signal.take_profit_pips / signal.stop_loss_pips
        else:
            rr_ratio = 0
        
        message = f"""🚨 *AUTO SIGNAL DETAILS*

📉{pair_info['base']} {signal.pair} {pair_info['quote']} (OTC)
⏰ Expiry: {signal.expiry_minutes} minutes
📍 Entry Time: {entry_nigeria}
📈 Direction: {signal.direction} {direction_emoji}
🕐 Trading Hours: {time_range} {'✅' if is_active else '⏸️'}

💰 **Trade Parameters:**
• Lot Size: {signal.lot_size}
• Stop Loss: {signal.stop_loss_pips} pips
• Take Profit: {signal.take_profit_pips} pips
• Risk-Reward: {rr_ratio:.1f}:1

🎯 **Price Levels:**
💰 Entry: {signal.entry_price:.5f}
🎯 TP: {signal.take_profit:.5f} (+{signal.take_profit_pips}p)
🛑 SL: {signal.stop_loss:.5f} (-{signal.stop_loss_pips}p)

🎯 **Martingale Levels:**
{martingale_text}
📊 **Technical Analysis:**
• Confidence: {signal.confidence:.0f}%
• RSI: {signal.technicals.get('rsi', 0):.1f}
• MACD: {signal.technicals.get('macd_diff', 0):.4f}
• Stochastic: {signal.technicals.get('stochastic_k', 0):.1f}/{signal.technicals.get('stochastic_d', 0):.1f}
• AI Model: {signal.ai_analysis.get('model_used', 'N/A') if signal.ai_analysis else 'N/A'}

⚠️ *Signal expires at: {expiry_nigeria}*
⚠️ One signal active - Wait for expiry before next signal
"""
        
        keyboard = [
            [
                InlineKeyboardButton("✅ ACCEPT", callback_data="accept_signal"),
                InlineKeyboardButton("📊 BACK", callback_data="view_signal")
            ],
            [
                InlineKeyboardButton("❌ CLEAR", callback_data="clear_signal"),
                InlineKeyboardButton("⏰ STATUS", callback_data="status")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def send_forced_signal(self, update: Update, signal: Signal):
        """Send concise FORCED signal with essential info only"""
        pair_info = self.forex_pairs[signal.pair]
        expiry_time = signal.entry_time + timedelta(minutes=signal.expiry_minutes)
        expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=False)
        
        direction_emoji = "🟩" if "BUY" in signal.direction else "🟥"
        
        message = f"""🚨⚡ *FORCE: {signal.pair} {signal.direction}* {direction_emoji}

Entry: {signal.entry_price:.5f}
TP: {signal.take_profit:.5f} (+{signal.take_profit_pips}p)
SL: {signal.stop_loss:.5f} (-{signal.stop_loss_pips}p)

Lot: {signal.lot_size} | Conf: {signal.confidence:.0f}%
Expires: {expiry_nigeria} (5min)

⚡ Manual Override | Below auto threshold
"""
        
        keyboard = [
            [
                InlineKeyboardButton("✅ ACCEPT", callback_data="accept_signal"),
                InlineKeyboardButton("📊 DETAILS", callback_data="view_signal_details")
            ],
            [
                InlineKeyboardButton("❌ CLEAR", callback_data="clear_signal"),
                InlineKeyboardButton("⏰ STATUS", callback_data="status")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    async def send_forced_signal_detailed(self, update: Update, signal: Signal):
        """Send detailed FORCED signal (full info)"""
        pair_info = self.forex_pairs[signal.pair]
        entry_nigeria = format_nigeria_time(signal.entry_time, include_timezone=True)
        expiry_time = signal.entry_time + timedelta(minutes=signal.expiry_minutes)
        expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=True)
        
        # Get trading info
        nigeria_time = get_nigeria_time()
        is_active, time_range = is_pair_in_trading_hours(signal.pair, nigeria_time)
        
        direction_emoji = "🟩" if "BUY" in signal.direction else "🟥"
        
        martingale_text = ""
        for i, level_time in enumerate(signal.martingale_levels, 1):
            level_nigeria = format_nigeria_time(level_time, include_timezone=False)
            martingale_text += f"🔁 Level {i} → {level_nigeria}\n"
        
        # Calculate risk-reward ratio
        if signal.stop_loss_pips > 0:
            rr_ratio = signal.take_profit_pips / signal.stop_loss_pips
        else:
            rr_ratio = 0
        
        message = f"""🚨⚡ *FORCED SIGNAL DETAILS*

📉{pair_info['base']} {signal.pair} {pair_info['quote']} (OTC)
⏰ Expiry: {signal.expiry_minutes} minutes
📍 Entry Time: {entry_nigeria}
📈 Direction: {signal.direction} {direction_emoji}
🕐 Trading Hours: {time_range} {'✅' if is_active else '⏸️'}

⚡ *MANUAL OVERRIDE SIGNAL*
• Auto threshold: {self.min_confidence}% ❌
• Manual threshold: {self.settings.get('manual_confidence_threshold', 50.0)}% ✅
• Confidence: {signal.confidence:.0f}%

💰 **Trade Parameters:**
• Lot Size: {signal.lot_size}
• Stop Loss: {signal.stop_loss_pips} pips
• Take Profit: {signal.take_profit_pips} pips
• Risk-Reward: {rr_ratio:.1f}:1

🎯 **Price Levels:**
💰 Entry: {signal.entry_price:.5f}
🎯 TP: {signal.take_profit:.5f} (+{signal.take_profit_pips}p)
🛑 SL: {signal.stop_loss:.5f} (-{signal.stop_loss_pips}p)

🎯 **Martingale Levels:**
{martingale_text}
📊 **Technical Analysis:**
• Confidence: {signal.confidence:.0f}%
• RSI: {signal.technicals.get('rsi', 0):.1f}
• MACD: {signal.technicals.get('macd_diff', 0):.4f}
• Stochastic: {signal.technicals.get('stochastic_k', 0):.1f}/{signal.technicals.get('stochastic_d', 0):.1f}
• AI Model: {signal.ai_analysis.get('model_used', 'N/A') if signal.ai_analysis else 'N/A'}

⚠️ *FORCED SIGNAL - Below auto threshold ({self.min_confidence}%)*
⚠️ *Signal expires at: {expiry_nigeria}*
⚠️ One signal active - Wait for expiry before next signal
"""
        
        keyboard = [
            [
                InlineKeyboardButton("✅ ACCEPT", callback_data="accept_signal"),
                InlineKeyboardButton("📊 BACK", callback_data="view_signal")
            ],
            [
                InlineKeyboardButton("❌ CLEAR", callback_data="clear_signal"),
                InlineKeyboardButton("⏰ STATUS", callback_data="status")
            ]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(message, parse_mode='Markdown', reply_markup=reply_markup)
    
    # ==================== AUTO SCANNER ====================
    
    async def auto_scanner(self):
        """Automatic signal scanner (only for primary instance) - ONLY SCANS ACTIVE PAIRS"""
        if not self.is_primary_instance:
            logger.info("Auto scanner disabled for secondary instance")
            return
        
        logger.info("Auto scanner started (time-based filtering enabled)")
        
        while self.running and self.is_primary_instance:
            try:
                # Get current Nigeria time
                nigeria_time = get_nigeria_time()
                
                # Reset daily counter at midnight Nigeria time
                if nigeria_time.hour == 0 and nigeria_time.minute < 5:
                    self.stats['signals_today'] = 0
                    logger.info("Daily signal counter reset")
                
                # Check if we should scan
                if not self.current_signal:
                    if not self.stats['last_auto_scan'] or \
                       (nigeria_time - self.stats['last_auto_scan']).total_seconds() >= self.auto_scan_interval:
                        
                        # Get active pairs
                        active_pairs = get_active_pairs(nigeria_time)
                        
                        if not active_pairs:
                            logger.info(f"No active pairs at {format_nigeria_time(nigeria_time)}, skipping scan")
                            self.stats['last_auto_scan'] = nigeria_time
                            await asyncio.sleep(60)
                            continue
                        
                        logger.info(f"Auto scanner: Scanning {len(active_pairs)} active pairs at {format_nigeria_time(nigeria_time)}...")
                        
                        signal = await self.scan_active_pairs(SignalType.AUTO)
                        
                        if signal and signal.confidence >= 75:
                            with self.signal_lock:
                                self.current_signal = signal
                            
                            self.stats['auto_signals'] += 1
                            self.stats['total_signals'] += 1
                            self.stats['signals_today'] += 1
                            self.stats['last_auto_scan'] = nigeria_time
                            
                            message = self.format_signal_for_admin(signal)
                            await self.send_admin(f"🤖 *AUTO SIGNAL DETECTED*\n\n{message}")
                            
                            logger.info(f"Auto signal sent for {signal.pair} at {format_nigeria_time(nigeria_time)}")
                        else:
                            logger.info("Auto scanner: No strong signals found in active pairs")
                            self.stats['last_auto_scan'] = nigeria_time
                
                await asyncio.sleep(60)
                
            except Exception as e:
                logger.error(f"Auto scanner error: {e}")
                await asyncio.sleep(300)
    
    def format_signal_for_admin(self, signal: Signal) -> str:
        """Format signal for admin notification"""
        pair_info = self.forex_pairs[signal.pair]
        entry_nigeria = format_nigeria_time(signal.entry_time, include_timezone=True)
        expiry_time = signal.entry_time + timedelta(minutes=5)
        expiry_nigeria = format_nigeria_time(expiry_time, include_timezone=True)
        
        # Get trading info
        nigeria_time = get_nigeria_time()
        is_active, time_range = is_pair_in_trading_hours(signal.pair, nigeria_time)
        trading_info = get_pair_trading_info(signal.pair)
        
        return f"""📉{pair_info['base']} {signal.pair} {pair_info['quote']}
⏰ Expiry: 5 minutes
📍 Entry: {entry_nigeria}
📈 Direction: {signal.direction}
⚡ Confidence: {signal.confidence:.0f}%
🕐 Trading Hours: {time_range} {'✅' if is_active else '⏸️'}

💰 ADJUSTABLE PARAMETERS:
• Lot Size: {signal.lot_size} (default: 0.01)
• Stop Loss: {signal.stop_loss_pips} pips (default: 5)
• Take Profit: {signal.take_profit_pips} pips (default: 15)

💎 Price Levels:
💰 Entry: {signal.entry_price:.5f}
🎯 TP: {signal.take_profit:.5f}
🛑 SL: {signal.stop_loss:.5f}

📊 Trading Info:
• Recommended: {trading_info.get('time', 'N/A')}
• Typical Move: {trading_info.get('move', 'N/A')}
• Notes: {trading_info.get('notes', 'N/A')}

📈 Indicators: RSI={signal.technicals.get('rsi', 0):.1f}, MACD={signal.technicals.get('macd_diff', 0):.4f}
⚠️ Expires: {expiry_nigeria}
"""
    
    async def send_admin(self, message: str):
        """Send message to admin"""
        try:
            await self.bot.send_message(
                chat_id=self.admin_id,
                text=message,
                parse_mode='Markdown'
            )
        except Exception as e:
            logger.error(f"Error sending to admin: {e}")
