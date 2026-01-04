"""
DATA MODELS - Signal data classes, settings, and enums
"""

import os
import json
import logging
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ==================== SETTINGS MANAGEMENT ====================
class BotSettings:
    """Manages bot settings with persistence - ADJUSTABLE values for TP/SL/Lot"""
    
    def __init__(self, settings_file: str = "bot_settings.json"):
        self.settings_file = settings_file
        self.settings = self.load_settings()
    
    def load_settings(self) -> Dict:
        """Load settings from file or create default"""
        # DEFAULT VALUES - adjustable via commands
        default_settings = {
            'lot_size': 0.01,  # Default: 0.01 lot (adjustable)
            'stop_loss_pips': 5.0,  # Default: 5 pips (adjustable)
            'take_profit_pips': 15.0,  # Default: 15 pips (adjustable)
            'xau_take_profit_pips': 20.0,  # Special TP for XAU/USD (adjustable)
            'manual_confidence_threshold': 50.0,  # Confidence threshold for manual signals (adjustable)
            'last_updated': datetime.now().isoformat(),
            'settings_history': []
        }
        
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    loaded_settings = json.load(f)
                    # Update with defaults for any missing keys
                    for key, value in default_settings.items():
                        if key not in loaded_settings:
                            loaded_settings[key] = value
                    return loaded_settings
            else:
                # Create default settings file
                self.save_settings(default_settings)
                return default_settings
        except Exception as e:
            logger.error(f"Error loading settings: {e}")
            return default_settings
    
    def save_settings(self, settings: Dict = None):
        """Save settings to file"""
        try:
            if settings:
                self.settings = settings
            
            self.settings['last_updated'] = datetime.now().isoformat()
            
            with open(self.settings_file, 'w') as f:
                json.dump(self.settings, f, indent=2, default=str)
            
            logger.info(f"Settings saved: {self.settings_file}")
            return True
        except Exception as e:
            logger.error(f"Error saving settings: {e}")
            return False
    
    def get(self, key: str, default=None):
        """Get a setting value"""
        return self.settings.get(key, default)
    
    def set(self, key: str, value, note: str = ""):
        """Set a setting value and save"""
        old_value = self.settings.get(key)
        self.settings[key] = value
        
        # Add to history
        history_entry = {
            'timestamp': datetime.now().isoformat(),
            'setting': key,
            'old_value': old_value,
            'new_value': value,
            'note': note
        }
        
        if 'settings_history' not in self.settings:
            self.settings['settings_history'] = []
        
        self.settings['settings_history'].append(history_entry)
        
        # Keep only last 50 history entries
        if len(self.settings['settings_history']) > 50:
            self.settings['settings_history'] = self.settings['settings_history'][-50:]
        
        return self.save_settings()

class SignalType(Enum):
    AUTO = "AUTO"
    MANUAL_DETAILED = "MANUAL_DETAILED"
    MANUAL_FAST = "MANUAL_FAST"
    MANUAL_FORCE = "MANUAL_FORCE"  # NEW: For manual override signals

@dataclass
class Signal:
    pair: str
    signal_type: SignalType
    direction: str
    entry_price: float
    confidence: float
    expiry_minutes: int
    entry_time: datetime
    stop_loss: float
    take_profit: float
    stop_loss_pips: float
    take_profit_pips: float
    lot_size: float
    martingale_levels: List[datetime] = field(default_factory=list)
    technicals: Dict = field(default_factory=dict)
    ai_analysis: Dict = field(default_factory=dict)
    score: float = 0.0
    is_forced: bool = False  # NEW: Flag for forced/manual override signals
