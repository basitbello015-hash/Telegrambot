"""
ANALYSIS ENGINE - Technical analysis, AI analysis, and signal generation
"""

import pandas as pd
import numpy as np
import yfinance as yf
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional

# Try to import technical analysis
try:
    import ta
    TA_AVAILABLE = True
except ImportError:
    print("Warning: ta package not installed. Install with: pip install ta")
    ta = None
    TA_AVAILABLE = False

# Try to import Groq AI
try:
    from groq import Groq
    GROQ_AVAILABLE = True
except ImportError:
    print("Warning: groq package not installed. Install with: pip install groq")
    Groq = None
    GROQ_AVAILABLE = False

logger = logging.getLogger(__name__)
executor = ThreadPoolExecutor(max_workers=10)

# Import from our modules
from data_models import Signal, SignalType
from time_utils import get_nigeria_time, is_pair_in_trading_hours, get_pair_trading_info

class TechnicalAnalyzer:
    """Performs technical analysis on forex pairs"""
    
    def analyze(self, data: pd.DataFrame) -> Dict:
        if not TA_AVAILABLE:
            logger.warning("Technical Analysis (ta) package not available")
            return self.fallback_analysis(data)
        
        if len(data) < 20:
            logger.warning(f"Insufficient data for analysis: {len(data)} rows")
            return self.fallback_analysis(data)
        
        try:
            close_series = data['Close']
            high_series = data['High']
            low_series = data['Low']
            
            if close_series.empty:
                return {}
            
            close_values = close_series.values.flatten() if hasattr(close_series.values, 'flatten') else close_series.values
            high_values = high_series.values.flatten() if hasattr(high_series.values, 'flatten') else high_series.values
            low_values = low_series.values.flatten() if hasattr(low_series.values, 'flatten') else low_series.values
            
            close = np.array(close_values, dtype=np.float64)
            high = np.array(high_values, dtype=np.float64)
            low = np.array(low_values, dtype=np.float64)
            
            if close.ndim > 1:
                close = close.flatten()
            if high.ndim > 1:
                high = high.flatten()
            if low.ndim > 1:
                low = low.flatten()
            
            close_clean = pd.Series(close)
            high_clean = pd.Series(high)
            low_clean = pd.Series(low)
            
            # Trend indicators
            sma_20_indicator = ta.trend.SMAIndicator(close_clean, window=20)
            sma_50_indicator = ta.trend.SMAIndicator(close_clean, window=50)
            ema_12_indicator = ta.trend.EMAIndicator(close_clean, window=12)
            ema_26_indicator = ta.trend.EMAIndicator(close_clean, window=26)
            
            sma_20 = sma_20_indicator.sma_indicator()
            sma_50 = sma_50_indicator.sma_indicator()
            ema_12 = ema_12_indicator.ema_indicator()
            ema_26 = ema_26_indicator.ema_indicator()
            
            # Momentum indicators
            rsi_indicator = ta.momentum.RSIIndicator(close_clean, window=14)
            rsi = rsi_indicator.rsi()
            
            macd_indicator = ta.trend.MACD(close_clean)
            macd_line = macd_indicator.macd()
            macd_signal = macd_indicator.macd_signal()
            macd_diff = macd_indicator.macd_diff()
            
            stoch_indicator = ta.momentum.StochasticOscillator(
                high_clean, low_clean, close_clean, window=14, smooth_window=3
            )
            stoch_k = stoch_indicator.stoch()
            stoch_d = stoch_indicator.stoch_signal()
            
            # Volatility indicators
            bb_indicator = ta.volatility.BollingerBands(close_clean, window=20, window_dev=2)
            bb_upper = bb_indicator.bollinger_hband()
            bb_lower = bb_indicator.bollinger_lband()
            bb_middle = bb_indicator.bollinger_mavg()
            
            atr_indicator = ta.volatility.AverageTrueRange(high_clean, low_clean, close_clean, window=14)
            atr = atr_indicator.average_true_range()
            
            # Get last values safely
            try:
                current_price = float(close_clean.iloc[-1])
                current_rsi = float(rsi.iloc[-1]) if not rsi.empty else 50.0
                current_macd = float(macd_diff.iloc[-1]) if not macd_diff.empty else 0.0
                current_stoch_k = float(stoch_k.iloc[-1]) if not stoch_k.empty else 50.0
                current_stoch_d = float(stoch_d.iloc[-1]) if not stoch_d.empty else 50.0
                current_sma_20 = float(sma_20.iloc[-1]) if not sma_20.empty else current_price
                current_sma_50 = float(sma_50.iloc[-1]) if not sma_50.empty else current_price
                current_ema_12 = float(ema_12.iloc[-1]) if not ema_12.empty else current_price
                current_ema_26 = float(ema_26.iloc[-1]) if not ema_26.empty else current_price
                current_bb_upper = float(bb_upper.iloc[-1]) if not bb_upper.empty else current_price
                current_bb_lower = float(bb_lower.iloc[-1]) if not bb_lower.empty else current_price
                current_bb_middle = float(bb_middle.iloc[-1]) if not bb_middle.empty else current_price
                current_atr = float(atr.iloc[-1]) if not atr.empty else 0.0
            except Exception as e:
                logger.error(f"Error extracting indicator values: {e}")
                return self.fallback_analysis(data)
            
            # Calculate Bollinger Band width
            if current_bb_middle != 0:
                bb_width = (current_bb_upper - current_bb_lower) / current_bb_middle
            else:
                bb_width = 0
            
            # Calculate ATR percentage
            if current_price > 0:
                atr_percent = (current_atr / current_price) * 100
            else:
                atr_percent = 0
            
            # Determine signal strength
            buy_signals = 0
            sell_signals = 0
            
            # RSI signals
            if current_rsi < 30:
                buy_signals += 2
            elif current_rsi > 70:
                sell_signals += 2
            elif current_rsi < 45:
                buy_signals += 1
            elif current_rsi > 55:
                sell_signals += 1
            
            # MACD signals
            if current_macd > 0:
                buy_signals += 1
            elif current_macd < 0:
                sell_signals += 1
            
            # Stochastic signals
            if current_stoch_k < 20:
                buy_signals += 2
            elif current_stoch_k > 80:
                sell_signals += 2
            elif current_stoch_k < 50:
                buy_signals += 1
            elif current_stoch_k > 50:
                sell_signals += 1
            
            # Price vs moving averages
            if current_price > current_sma_20:
                buy_signals += 1
            else:
                sell_signals += 1
            
            if current_price > current_sma_50:
                buy_signals += 1
            else:
                sell_signals += 1
            
            # Bollinger Bands signals
            if current_price < current_bb_lower:
                buy_signals += 2
            elif current_price > current_bb_upper:
                sell_signals += 2
            
            # EMA crossover signals
            if current_ema_12 > current_ema_26:
                buy_signals += 1
            else:
                sell_signals += 1
            
            # Determine direction
            if buy_signals >= 7 and sell_signals <= 2:
                direction = "STRONG_BUY"
                confidence = min(95, 50 + (buy_signals - sell_signals) * 5)
            elif buy_signals > sell_signals + 2:
                direction = "BUY"
                confidence = min(85, 50 + (buy_signals - sell_signals) * 4)
            elif sell_signals >= 7 and buy_signals <= 2:
                direction = "STRONG_SELL"
                confidence = min(95, 50 + (sell_signals - buy_signals) * 5)
            elif sell_signals > buy_signals + 2:
                direction = "SELL"
                confidence = min(85, 50 + (sell_signals - buy_signals) * 4)
            else:
                direction = "NEUTRAL"
                confidence = 50
            
            # Calculate support and resistance
            support = float(close_clean.tail(20).min())
            resistance = float(close_clean.tail(20).max())
            
            return {
                'direction': direction,
                'confidence': float(confidence),
                'price': current_price,
                'rsi': current_rsi,
                'macd_diff': current_macd,
                'stochastic_k': current_stoch_k,
                'stochastic_d': current_stoch_d,
                'sma_20': current_sma_20,
                'sma_50': current_sma_50,
                'ema_12': current_ema_12,
                'ema_26': current_ema_26,
                'bb_upper': current_bb_upper,
                'bb_lower': current_bb_lower,
                'bb_middle': current_bb_middle,
                'bb_width': float(bb_width),
                'atr': current_atr,
                'atr_percent': float(atr_percent),
                'buy_signals': buy_signals,
                'sell_signals': sell_signals,
                'support': support,
                'resistance': resistance
            }
            
        except Exception as e:
            logger.error(f"Technical analysis error: {e}", exc_info=True)
            return self.fallback_analysis(data)
    
    def fallback_analysis(self, data: pd.DataFrame) -> Dict:
        """Simple fallback analysis when TA library is not available"""
        try:
            if data.empty or len(data) < 5:
                return {}
            
            close = data['Close']
            current_price = float(close.iloc[-1])
            
            # Simple moving averages
            if len(close) >= 5:
                sma_5 = close.tail(5).mean()
            else:
                sma_5 = current_price
            
            if len(close) >= 10:
                sma_10 = close.tail(10).mean()
            else:
                sma_10 = current_price
            
            # Simple trend detection
            price_change = (current_price - float(close.iloc[-5])) / float(close.iloc[-5]) * 100 if len(close) >= 5 else 0
            
            if price_change > 0.5:
                direction = "BUY"
                confidence = 60
            elif price_change < -0.5:
                direction = "SELL"
                confidence = 60
            else:
                direction = "NEUTRAL"
                confidence = 50
            
            return {
                'direction': direction,
                'confidence': float(confidence),
                'price': current_price,
                'sma_5': float(sma_5),
                'sma_10': float(sma_10),
                'price_change': float(price_change),
                'support': float(close.tail(20).min()) if len(close) >= 20 else current_price * 0.99,
                'resistance': float(close.tail(20).max()) if len(close) >= 20 else current_price * 1.01,
                'buy_signals': 1 if direction == "BUY" else 0,
                'sell_signals': 1 if direction == "SELL" else 0
            }
        except Exception as e:
            logger.error(f"Fallback analysis error: {e}")
            return {}

class AIAnalyzer:
    """Uses Groq AI for market analysis (without news)"""
    
    def __init__(self, groq_api_key: str):
        if not GROQ_AVAILABLE:
            raise ImportError("groq package not installed. Install with: pip install groq")
        self.client = Groq(api_key=groq_api_key) if groq_api_key else None
    
    async def analyze_pair(self, pair: str, technicals: Dict, analysis_type: str = "detailed") -> Dict:
        """Analyze pair using Groq AI (without news)"""
        if not self.client:
            return {
                "signal": "NEUTRAL",
                "confidence": 50,
                "model_used": "No API Key",
                "analysis_type": analysis_type,
                "reason": "Groq API key not configured"
            }
        
        try:
            # Select model based on analysis type
            if analysis_type == "fast":
                model = "llama-3.1-8b-instant"
                max_tokens = 300
            else:
                model = "llama-3.3-70b-versatile"
                max_tokens = 500
            
            # Get trading info for the pair
            trading_info = get_pair_trading_info(pair)
            
            # Prepare analysis context (no news)
            context = f"""
            Forex Pair: {pair}
            
            Trading Session Information (Nigeria WAT Time):
            - Recommended Time: {trading_info.get('time', 'N/A')}
            - Typical 5-min Move: {trading_info.get('move', 'N/A')}
            - Volatility: {trading_info.get('volatility', 'N/A')}
            - Notes: {trading_info.get('notes', 'N/A')}
            
            Technical Analysis:
            - Current Price: {technicals.get('price', 0):.5f}
            - RSI: {technicals.get('rsi', 50):.1f} ({'Oversold' if technicals.get('rsi', 50) < 30 else 'Overbought' if technicals.get('rsi', 50) > 70 else 'Neutral'})
            - MACD: {technicals.get('macd_diff', 0):.4f} ({'Bullish' if technicals.get('macd_diff', 0) > 0 else 'Bearish'})
            - Stochastic K: {technicals.get('stochastic_k', 50):.1f}
            - Stochastic D: {technicals.get('stochastic_d', 50):.1f}
            - Price vs SMA20: {'Above' if technicals.get('price', 0) > technicals.get('sma_20', 0) else 'Below'}
            - Price vs SMA50: {'Above' if technicals.get('price', 0) > technicals.get('sma_50', 0) else 'Below'}
            - Price vs EMA12: {'Above' if technicals.get('price', 0) > technicals.get('ema_12', 0) else 'Below'}
            - Price vs EMA26: {'Above' if technicals.get('price', 0) > technicals.get('ema_26', 0) else 'Below'}
            - Bollinger Position: {'Upper Band' if technicals.get('price', 0) > technicals.get('bb_upper', 0) else 'Lower Band' if technicals.get('price', 0) < technicals.get('bb_lower', 0) else 'Middle Band'}
            - Bollinger Width: {technicals.get('bb_width', 0):.3f}
            - ATR: {technicals.get('atr', 0):.5f} ({technicals.get('atr_percent', 0):.2f}%)
            - Support: {technicals.get('support', 0):.5f}
            - Resistance: {technicals.get('resistance', 0):.5f}
            - Buy Signals: {technicals.get('buy_signals', 0)}
            - Sell Signals: {technicals.get('sell_signals', 0)}
            
            Trading Context:
            - 5-minute binary options
            - Martingale strategy (3 levels: 5, 10, 15 minutes)
            - Trading during recommended Nigeria WAT hours: {trading_info.get('time', 'N/A')}
            - Pure technical analysis only (no news sentiment considered)
            
            Provide a clear trading signal based on technical analysis only in JSON format:
            {{
                "signal": "STRONG_BUY|BUY|NEUTRAL|SELL|STRONG_SELL",
                "confidence": 0-100,
                "reason": "brief technical analysis reason",
                "timeframe": "5m",
                "risk_level": "LOW|MEDIUM|HIGH",
                "analysis_type": "PURE_TECHNICAL"
            }}
            """
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                executor,
                lambda: self.client.chat.completions.create(
                    messages=[
                        {
                            "role": "system",
                            "content": f"You are an expert forex trading analyst. {pair} is currently in its recommended trading hours ({trading_info.get('time', 'N/A')} Nigeria WAT time). Provide clear, actionable trading signals based on technical analysis only. Do not consider news or fundamental analysis."
                        },
                        {
                            "role": "user",
                            "content": context
                        }
                    ],
                    model=model,
                    temperature=0.3,
                    max_tokens=max_tokens
                )
            )
            
            # Parse response
            content = response.choices[0].message.content
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            
            if json_match:
                try:
                    analysis = json.loads(json_match.group())
                except json.JSONDecodeError:
                    analysis = {"signal": "NEUTRAL", "confidence": 50}
            else:
                analysis = {"signal": "NEUTRAL", "confidence": 50}
            
            analysis['model_used'] = model
            analysis['analysis_type'] = analysis_type
            
            return analysis
            
        except Exception as e:
            logger.error(f"AI analysis error for {pair}: {e}")
            return {
                "signal": "NEUTRAL",
                "confidence": 50,
                "model_used": "error",
                "analysis_type": analysis_type
            }

def calculate_stop_loss_take_profit(entry_price: float, direction: str, sl_pips: float, tp_pips: float, pair: str) -> tuple:
    """Calculate stop loss and take profit prices based on pair type"""
    # Calculate pip value based on pair
    if 'JPY' in pair:
        pip_multiplier = 100  # JPY pairs: 151.20 → 0.01 per pip
    elif pair == 'XAU/USD':
        pip_multiplier = 100  # Gold: 2165.25 → 0.01 per pip
    else:
        pip_multiplier = 10000  # Other Forex: 1.08500 → 0.0001 per pip
    
    # Calculate SL and TP prices
    if "BUY" in direction:
        stop_loss_price = entry_price - (sl_pips / pip_multiplier)
        take_profit_price = entry_price + (tp_pips / pip_multiplier)
    elif "SELL" in direction:
        stop_loss_price = entry_price + (sl_pips / pip_multiplier)
        take_profit_price = entry_price - (tp_pips / pip_multiplier)
    else:
        stop_loss_price = entry_price * 0.995
        take_profit_price = entry_price * 1.005
    
    return round(stop_loss_price, 5), round(take_profit_price, 5)
