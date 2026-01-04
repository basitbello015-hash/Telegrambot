"""
TIME UTILITIES - Nigeria WAT timezone and trading hours
"""

import socket
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

# ==================== TIMEZONE HANDLING ====================
# Nigeria timezone (WAT - West Africa Time, UTC+1)
NIGERIA_TZ = ZoneInfo("Africa/Lagos")

def get_nigeria_time() -> datetime:
    """Get current time in Nigeria (WAT, UTC+1)"""
    return datetime.now(NIGERIA_TZ)

def format_nigeria_time(dt: datetime, include_timezone: bool = False) -> str:
    """Format datetime to Nigeria time in 12-hour format"""
    # Convert to Nigeria time if not already
    if dt.tzinfo is not None:
        nigeria_dt = dt.astimezone(NIGERIA_TZ)
    else:
        nigeria_dt = dt
    
    # Format in 12-hour format (1 PM, 2:30 AM, etc.)
    time_str = nigeria_dt.strftime('%I:%M %p').lstrip('0')
    
    if include_timezone:
        return f"{time_str} (WAT)"
    return time_str

def format_full_nigeria_datetime(dt: datetime) -> str:
    """Format datetime to full Nigeria date and time"""
    if dt.tzinfo is not None:
        nigeria_dt = dt.astimezone(NIGERIA_TZ)
    else:
        nigeria_dt = dt
    
    return nigeria_dt.strftime('%Y-%m-%d %I:%M:%S %p WAT')

def get_timezone_info() -> str:
    """Get Nigeria timezone information"""
    try:
        current_time = datetime.now(NIGERIA_TZ)
        return f"WAT (UTC+1) - Nigeria Time"
    except:
        return "Africa/Lagos (WAT)"

# ==================== TIME RANGE CHECKING (12-HOUR FORMAT) ====================
def parse_12h_time(time_str: str) -> Tuple[int, int]:
    """
    Parse 12-hour format time string (e.g., "1 PM", "2:30 AM", "11 PM")
    Returns (hour_24h, minute)
    """
    try:
        # Remove spaces and convert to uppercase
        time_str = time_str.strip().upper()
        
        # Split time and AM/PM
        if " " in time_str:
            time_part, ampm = time_str.split()
        elif "PM" in time_str:
            time_part = time_str.replace("PM", "").strip()
            ampm = "PM"
        elif "AM" in time_str:
            time_part = time_str.replace("AM", "").strip()
            ampm = "AM"
        else:
            raise ValueError(f"Invalid time format: {time_str}")
        
        # Parse hour and minute
        if ":" in time_part:
            hour_str, minute_str = time_part.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
        else:
            hour = int(time_part)
            minute = 0
        
        # Convert to 24-hour format
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        
        return hour, minute
        
    except Exception as e:
        logger.error(f"Error parsing time '{time_str}': {e}")
        return 0, 0

def is_time_in_range_12h(current_hour: int, current_minute: int, 
                         start_time_str: str, end_time_str: str) -> bool:
    """
    Check if current time is within a range specified in 12-hour format
    Handles ranges that cross midnight (e.g., 11 PM – 7 AM)
    """
    # Parse start and end times
    start_hour, start_minute = parse_12h_time(start_time_str)
    end_hour, end_minute = parse_12h_time(end_time_str)
    
    # Convert current time to minutes since midnight
    current_total_minutes = current_hour * 60 + current_minute
    start_total_minutes = start_hour * 60 + start_minute
    end_total_minutes = end_hour * 60 + end_minute
    
    # Check if range crosses midnight
    if start_total_minutes <= end_total_minutes:
        # Normal range (doesn't cross midnight)
        return start_total_minutes <= current_total_minutes <= end_total_minutes
    else:
        # Range crosses midnight (e.g., 11 PM to 7 AM)
        return (current_total_minutes >= start_total_minutes or 
                current_total_minutes <= end_total_minutes)

def is_pair_in_trading_hours(pair: str, nigeria_time: datetime) -> Tuple[bool, str]:
    """
    Check if a pair is in its recommended trading hours
    Returns (is_in_hours, time_range_string)
    """
    # Trading hours based on your table (Nigeria WAT time, 12-hour format)
    trading_hours = {
        'EUR/USD': ('1 PM', '5 PM'),
        'USD/JPY': ('2 AM', '4 AM'),
        'GBP/USD': ('1 PM', '5 PM'),
        'AUD/USD': ('11 PM', '7 AM'),
        'USD/CAD': ('2:30 PM', '6 PM'),
        'USD/CHF': ('1 PM', '5 PM'),
        'XAU/USD': ('1 PM', '5 PM'),
        'NZD/USD': ('11 PM', '7 AM'),
        'EUR/JPY': ('2 AM', '4 AM'),
        'GBP/JPY': ('2 AM', '4 AM'),
    }
    
    if pair not in trading_hours:
        return True, "No time restriction"  # Default to allow if not in table
    
    start_time, end_time = trading_hours[pair]
    current_hour = nigeria_time.hour
    current_minute = nigeria_time.minute
    
    is_in_range = is_time_in_range_12h(current_hour, current_minute, start_time, end_time)
    time_range_str = f"{start_time} – {end_time} WAT"
    
    return is_in_range, time_range_str

def get_active_pairs(current_time: datetime) -> List[str]:
    """Get list of pairs that are currently in their trading hours"""
    all_pairs = [
        'EUR/USD', 'USD/JPY', 'GBP/USD', 'AUD/USD', 'USD/CAD',
        'USD/CHF', 'XAU/USD', 'NZD/USD', 'EUR/JPY', 'GBP/JPY'
    ]
    
    active_pairs = []
    for pair in all_pairs:
        is_active, _ = is_pair_in_trading_hours(pair, current_time)
        if is_active:
            active_pairs.append(pair)
    
    return active_pairs

def get_pair_trading_info(pair: str) -> Dict:
    """Get trading information for a pair based on the table"""
    trading_info = {
        'EUR/USD': {
            'time': '1 PM – 5 PM',
            'move': '5–10 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'London/NY overlap → safest for $10 account',
            'lot_recommendation': '0.005 lot (recommended)',
            'volatility': 'Medium'
        },
        'USD/JPY': {
            'time': '2 AM – 4 AM',
            'move': '3–8 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'Tokyo/London overlap, moderate volatility',
            'lot_recommendation': 'Smaller lot',
            'volatility': 'Low-Medium'
        },
        'GBP/USD': {
            'time': '1 PM – 5 PM',
            'move': '7–15 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'High RR, monitor spreads',
            'lot_recommendation': 'Reduced lot size',
            'volatility': 'High'
        },
        'AUD/USD': {
            'time': '11 PM – 7 AM',
            'move': '5–10 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'Quiet, safe for micro accounts',
            'lot_recommendation': 'Very small lot',
            'volatility': 'Low'
        },
        'USD/CAD': {
            'time': '2:30 PM – 6 PM',
            'move': '5–10 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'Oil-sensitive, moderate volatility',
            'lot_recommendation': 'Reduced lot size',
            'volatility': 'Medium'
        },
        'USD/CHF': {
            'time': '1 PM – 5 PM',
            'move': '5–10 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'Stable, low spreads',
            'lot_recommendation': 'Micro-lot only',
            'volatility': 'Low'
        },
        'XAU/USD': {
            'time': '1 PM – 5 PM',
            'move': '20–50 pips',
            'tp_sl': 'TP 20 / SL 5',
            'notes': 'Very volatile → risky for $10',
            'lot_recommendation': 'Avoid unless experienced',
            'volatility': 'Very High'
        },
        'NZD/USD': {
            'time': '11 PM – 7 AM',
            'move': '4–8 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'Quiet, slow-moving pair',
            'lot_recommendation': 'Smaller lot recommended',
            'volatility': 'Low'
        },
        'EUR/JPY': {
            'time': '2 AM – 4 AM',
            'move': '6–12 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'Moderate volatility',
            'lot_recommendation': 'Smaller lot',
            'volatility': 'Medium'
        },
        'GBP/JPY': {
            'time': '2 AM – 4 AM',
            'move': '8–15 pips',
            'tp_sl': 'TP 10–15 / SL 5',
            'notes': 'High volatility; only for disciplined trading',
            'lot_recommendation': 'Reduce lot; high-risk',
            'volatility': 'Very High'
        }
    }
    
    return trading_info.get(pair, {})
