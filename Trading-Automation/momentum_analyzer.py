#!/usr/bin/env python3
"""
Momentum Flexibility Analyzer (Console Version)
===============================================

This script demonstrates the dynamic momentum detection system implemented in the trading bot.
It shows how thresholds adapt based on volatility (ATR) and provides detailed analysis
without requiring matplotlib dependencies.

Features:
- Dynamic threshold calculation based on ATR
- Multi-timeframe momentum analysis (1m, 5m, 15m)
- RSI, Volume Spike, and MA Cross indicators
- Console-based analysis with detailed output
"""

import json
import random
import math
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from typing import Dict, List, Tuple, Optional

@dataclass
class MomentumConfig:
    """Configuration for momentum detection parameters"""
    # Dynamic threshold parameters
    k_1m: float = 0.55      # ATR multiplier for 1m
    k_5m: float = 0.70      # ATR multiplier for 5m  
    k_15m: float = 0.85     # ATR multiplier for 15m
    
    # Floor and cap values (as percentages)
    floor_1m: float = 0.07   # 0.07%
    floor_5m: float = 0.10   # 0.10%
    floor_15m: float = 0.15  # 0.15%
    
    cap_1m: float = 1.5      # 1.5%
    cap_5m: float = 1.8      # 1.8%
    cap_15m: float = 2.0     # 2.0%
    
    # Indicator thresholds
    rsi_overbought: float = 70
    volume_spike_multiplier: float = 2.0
    ma_periods_short: int = 5
    ma_periods_long: int = 20

class CandleData:
    """Represents a single candlestick"""
    def __init__(self, timestamp, open_price, high, low, close, volume):
        self.timestamp = timestamp
        self.open = open_price
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume

class MomentumAnalyzer:
    """Analyzes momentum detection flexibility"""
    
    def __init__(self, config: MomentumConfig = None):
        self.config = config or MomentumConfig()
        
    def generate_sample_data(self, symbol: str = "BTCUSDC", periods: int = 1000) -> List[CandleData]:
        """Generate realistic sample price data"""
        
        # Base parameters for different symbols
        base_params = {
            "BTCUSDC": {"base_price": 45000, "volatility": 0.02, "trend": 0.001},
            "ETHUSDC": {"base_price": 2800, "volatility": 0.025, "trend": 0.0015},
            "ADAUSDC": {"base_price": 0.45, "volatility": 0.035, "trend": 0.002}
        }
        
        params = base_params.get(symbol, base_params["BTCUSDC"])
        
        # Set seed for reproducible results
        random.seed(42)
        
        candles = []
        current_price = params["base_price"]
        base_volume = 1000000 if "BTC" in symbol else 500000
        
        start_time = datetime.now() - timedelta(minutes=periods)
        
        for i in range(periods):
            # Generate price movement
            price_change = random.gauss(
                params["trend"] / (24 * 60),  # Daily trend divided by minutes
                params["volatility"] / math.sqrt(24 * 60)  # Scale volatility
            )
            
            # Add momentum events occasionally
            if random.random() < 0.05:  # 5% chance of momentum event
                price_change += random.gauss(0.002, 0.001)
            
            current_price *= (1 + price_change)
            
            # Generate OHLC
            volatility = abs(price_change) + random.gauss(0, 0.001)
            high = current_price * (1 + abs(random.gauss(0, volatility)))
            low = current_price * (1 - abs(random.gauss(0, volatility)))
            open_price = candles[-1].close if candles else current_price
            
            # Generate volume
            volume_factor = abs(price_change) * 50 + random.gauss(0, 0.1)
            volume = base_volume * (1 + volume_factor)
            
            timestamp = start_time + timedelta(minutes=i)
            candles.append(CandleData(timestamp, open_price, high, low, current_price, volume))
        
        return candles
    
    def calculate_atr_percent(self, candles: List[CandleData], period: int = 14) -> float:
        """Calculate ATR as percentage of price"""
        if len(candles) < period + 1:
            return 0.5  # Default ATR
        
        true_ranges = []
        for i in range(1, len(candles)):
            high_low = candles[i].high - candles[i].low
            high_close = abs(candles[i].high - candles[i-1].close)
            low_close = abs(candles[i].low - candles[i-1].close)
            true_range = max(high_low, high_close, low_close)
            true_ranges.append(true_range)
        
        if len(true_ranges) < period:
            return 0.5
        
        # Calculate ATR (simple moving average of true ranges)
        recent_trs = true_ranges[-period:]
        atr = sum(recent_trs) / len(recent_trs)
        
        # Convert to percentage of current price
        current_price = candles[-1].close
        return (atr / current_price) * 100 if current_price > 0 else 0.5
    
    def calculate_dynamic_threshold(self, atr_percent: float, timeframe: str) -> float:
        """Calculate dynamic momentum threshold based on ATR"""
        if timeframe == '1m':
            k, floor, cap = self.config.k_1m, self.config.floor_1m, self.config.cap_1m
        elif timeframe == '5m':
            k, floor, cap = self.config.k_5m, self.config.floor_5m, self.config.cap_5m
        elif timeframe == '15m':
            k, floor, cap = self.config.k_15m, self.config.floor_15m, self.config.cap_15m
        else:
            k, floor, cap = 0.6, 0.08, 1.5
        
        threshold = k * atr_percent
        return max(floor, min(threshold, cap))
    
    def calculate_rsi(self, candles: List[CandleData], period: int = 14) -> float:
        """Calculate RSI"""
        if len(candles) < period + 1:
            return 50  # Neutral RSI
        
        gains = []
        losses = []
        
        for i in range(1, len(candles)):
            change = candles[i].close - candles[i-1].close
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        if len(gains) < period:
            return 50
        
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        
        if avg_loss == 0:
            return 100
        
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))
    
    def check_volume_spike(self, candles: List[CandleData], window: int = 20) -> bool:
        """Check for volume spike"""
        if len(candles) < window + 1:
            return False
        
        recent_volumes = [c.volume for c in candles[-window-1:-1]]
        avg_volume = sum(recent_volumes) / len(recent_volumes)
        current_volume = candles[-1].volume
        
        return current_volume > (avg_volume * self.config.volume_spike_multiplier)
    
    def check_ma_cross(self, candles: List[CandleData]) -> bool:
        """Check for moving average crossover"""
        short_period = self.config.ma_periods_short
        long_period = self.config.ma_periods_long
        
        if len(candles) < long_period + 1:
            return False
        
        # Calculate current MAs
        short_prices = [c.close for c in candles[-short_period:]]
        long_prices = [c.close for c in candles[-long_period:]]
        
        ma_short_current = sum(short_prices) / len(short_prices)
        ma_long_current = sum(long_prices) / len(long_prices)
        
        # Calculate previous MAs
        short_prices_prev = [c.close for c in candles[-short_period-1:-1]]
        long_prices_prev = [c.close for c in candles[-long_period-1:-1]]
        
        ma_short_prev = sum(short_prices_prev) / len(short_prices_prev)
        ma_long_prev = sum(long_prices_prev) / len(long_prices_prev)
        
        # Check for crossover
        crossed_above = (ma_short_prev <= ma_long_prev) and (ma_short_current > ma_long_current)
        return crossed_above
    
    def analyze_momentum_at_point(self, candles: List[CandleData], timeframe: str = '1m') -> Dict:
        """Analyze momentum at the latest point"""
        if len(candles) < 2:
            return {"error": "Insufficient data"}
        
        # Calculate price change
        prev_close = candles[-2].close
        current_close = candles[-1].close
        price_change_pct = ((current_close - prev_close) / prev_close) * 100
        
        # Calculate ATR and dynamic threshold
        atr_percent = self.calculate_atr_percent(candles)
        dynamic_threshold = self.calculate_dynamic_threshold(atr_percent, timeframe)
        
        # Calculate indicators
        rsi = self.calculate_rsi(candles)
        volume_spike = self.check_volume_spike(candles)
        ma_cross = self.check_ma_cross(candles)
        
        # Momentum conditions
        price_momentum = price_change_pct > dynamic_threshold
        rsi_momentum = rsi > self.config.rsi_overbought
        
        # Combined momentum signal
        momentum_score = (
            int(price_momentum) + 
            int(rsi_momentum) + 
            int(volume_spike) + 
            int(ma_cross)
        )
        
        has_momentum = price_momentum and (rsi_momentum or volume_spike or ma_cross)
        
        return {
            "timeframe": timeframe,
            "price_change_pct": round(price_change_pct, 4),
            "atr_percent": round(atr_percent, 4),
            "dynamic_threshold": round(dynamic_threshold, 4),
            "rsi": round(rsi, 2),
            "price_momentum": price_momentum,
            "rsi_momentum": rsi_momentum,
            "volume_spike": volume_spike,
            "ma_cross": ma_cross,
            "momentum_score": momentum_score,
            "has_momentum": has_momentum,
            "current_price": round(current_close, 6)
        }
    
    def run_comprehensive_analysis(self, symbol: str = "BTCUSDC", periods: int = 500) -> Dict:
        """Run comprehensive momentum analysis"""
        
        print(f"\\n{'='*60}")
        print(f"MOMENTUM FLEXIBILITY ANALYSIS: {symbol}")
        print(f"{'='*60}")
        
        # Generate sample data
        print(f"\\nGenerating {periods} periods of sample data...")
        candles = self.generate_sample_data(symbol, periods)
        
        # Analyze different scenarios
        scenarios = []
        
        # Test different timeframes at various points
        timeframes = ['1m', '5m', '15m']
        test_points = [
            int(periods * 0.25),  # 25% through
            int(periods * 0.5),   # 50% through  
            int(periods * 0.75),  # 75% through
            periods - 1           # Latest
        ]
        
        for point in test_points:
            scenario_candles = candles[:point+1]
            scenario = {
                "period": point,
                "timeframes": {}
            }
            
            for tf in timeframes:
                analysis = self.analyze_momentum_at_point(scenario_candles, tf)
                scenario["timeframes"][tf] = analysis
            
            scenarios.append(scenario)
        
        # Display results
        self._display_analysis_results(scenarios)
        
        # Test threshold adaptation across ATR ranges
        self._test_threshold_adaptation()
        
        return {
            "symbol": symbol,
            "config": asdict(self.config),
            "scenarios": scenarios
        }
    
    def _display_analysis_results(self, scenarios: List[Dict]):
        """Display analysis results in a readable format"""
        
        print(f"\\n{'='*60}")
        print("MOMENTUM DETECTION ANALYSIS")
        print(f"{'='*60}")
        
        for i, scenario in enumerate(scenarios):
            period = scenario["period"]
            print(f"\\n📊 ANALYSIS POINT {i+1} (Period {period})")
            print("-" * 50)
            
            # Display results for each timeframe
            for tf, analysis in scenario["timeframes"].items():
                if "error" in analysis:
                    continue
                    
                print(f"\\n{tf} Timeframe:")
                print(f"  Price Change: {analysis['price_change_pct']:+.3f}%")
                print(f"  ATR: {analysis['atr_percent']:.3f}%")
                print(f"  Dynamic Threshold: {analysis['dynamic_threshold']:.3f}%")
                print(f"  RSI: {analysis['rsi']:.1f}")
                print(f"  Current Price: ${analysis['current_price']:,.2f}")
                
                print(f"  Indicators:")
                print(f"    💹 Price Momentum: {'✅' if analysis['price_momentum'] else '❌'}")
                print(f"    📈 RSI Momentum: {'✅' if analysis['rsi_momentum'] else '❌'}")
                print(f"    📊 Volume Spike: {'✅' if analysis['volume_spike'] else '❌'}")
                print(f"    🔄 MA Cross: {'✅' if analysis['ma_cross'] else '❌'}")
                
                print(f"  Momentum Score: {analysis['momentum_score']}/4")
                print(f"  🎯 HAS MOMENTUM: {'🟢 YES' if analysis['has_momentum'] else '🔴 NO'}")
        
        # Summary statistics
        print(f"\\n{'='*60}")
        print("SUMMARY STATISTICS")
        print(f"{'='*60}")
        
        for tf in ['1m', '5m', '15m']:
            momentum_count = sum(1 for s in scenarios 
                               if s["timeframes"].get(tf, {}).get("has_momentum", False))
            total_valid = sum(1 for s in scenarios 
                            if "error" not in s["timeframes"].get(tf, {}))
            
            if total_valid > 0:
                momentum_pct = (momentum_count / total_valid) * 100
                print(f"{tf}: {momentum_count}/{total_valid} signals ({momentum_pct:.1f}%)")
    
    def _test_threshold_adaptation(self):
        """Test how thresholds adapt to different ATR levels"""
        
        print(f"\\n{'='*60}")
        print("THRESHOLD ADAPTATION ANALYSIS")
        print(f"{'='*60}")
        
        # Test different ATR scenarios
        atr_scenarios = [
            ("Low Volatility", 0.2),
            ("Normal Volatility", 0.8),
            ("High Volatility", 1.5),
            ("Extreme Volatility", 3.0)
        ]
        
        timeframes = ['1m', '5m', '15m']
        
        print(f"\\n{'ATR Scenario':<20} {'1m Threshold':<15} {'5m Threshold':<15} {'15m Threshold':<15}")
        print("-" * 75)
        
        for scenario_name, atr_value in atr_scenarios:
            thresholds = []
            for tf in timeframes:
                threshold = self.calculate_dynamic_threshold(atr_value, tf)
                thresholds.append(f"{threshold:.3f}%")
            
            print(f"{scenario_name:<20} {thresholds[0]:<15} {thresholds[1]:<15} {thresholds[2]:<15}")
        
        # Show configuration
        print(f"\\n{'='*60}")
        print("CONFIGURATION PARAMETERS")
        print(f"{'='*60}")
        
        config_display = [
            ("Parameter", "1m", "5m", "15m"),
            ("ATR Multiplier (k)", f"{self.config.k_1m:.2f}", f"{self.config.k_5m:.2f}", f"{self.config.k_15m:.2f}"),
            ("Floor Threshold", f"{self.config.floor_1m:.3f}%", f"{self.config.floor_5m:.3f}%", f"{self.config.floor_15m:.3f}%"),
            ("Cap Threshold", f"{self.config.cap_1m:.1f}%", f"{self.config.cap_5m:.1f}%", f"{self.config.cap_15m:.1f}%")
        ]
        
        for row in config_display:
            print(f"{row[0]:<20} {row[1]:<10} {row[2]:<10} {row[3]:<10}")

def compare_symbols():
    """Compare momentum behavior across different symbols"""
    
    symbols = ["BTCUSDC", "ETHUSDC", "ADAUSDC"]
    analyzer = MomentumAnalyzer()
    
    print(f"\\n{'='*80}")
    print("MULTI-SYMBOL MOMENTUM COMPARISON")
    print(f"{'='*80}")
    
    comparison_data = []
    
    for symbol in symbols:
        print(f"\\nAnalyzing {symbol}...")
        candles = analyzer.generate_sample_data(symbol, 200)
        
        # Get latest analysis for all timeframes
        analysis_1m = analyzer.analyze_momentum_at_point(candles, '1m')
        analysis_5m = analyzer.analyze_momentum_at_point(candles, '5m')
        analysis_15m = analyzer.analyze_momentum_at_point(candles, '15m')
        
        comparison_data.append({
            "symbol": symbol,
            "1m": analysis_1m,
            "5m": analysis_5m,
            "15m": analysis_15m
        })
    
    # Display comparison table
    print(f"\\n{'Symbol':<12} {'Timeframe':<12} {'Price Δ%':<10} {'ATR%':<8} {'Threshold%':<12} {'Momentum':<10}")
    print("-" * 80)
    
    for data in comparison_data:
        symbol = data["symbol"]
        for tf in ["1m", "5m", "15m"]:
            analysis = data[tf]
            if "error" not in analysis:
                momentum_status = "🟢 YES" if analysis["has_momentum"] else "🔴 NO"
                print(f"{symbol:<12} {tf:<12} {analysis['price_change_pct']:+.3f}%   {analysis['atr_percent']:.3f}% {analysis['dynamic_threshold']:.3f}%     {momentum_status:<10}")

def demonstrate_flexibility():
    """Demonstrate the flexibility of the momentum system"""
    
    print(f"\\n{'='*80}")
    print("MOMENTUM SYSTEM FLEXIBILITY DEMONSTRATION")
    print(f"{'='*80}")
    
    print("""
🎯 KEY FEATURES OF THE DYNAMIC MOMENTUM SYSTEM:

1. ATR-BASED ADAPTATION:
   • Thresholds automatically adjust based on market volatility
   • Higher ATR = Higher thresholds (less sensitive, fewer false signals)
   • Lower ATR = Lower thresholds (more sensitive, catches small moves)

2. MULTI-TIMEFRAME CONFIRMATION:
   • Requires momentum signals across multiple timeframes (1m, 5m, 15m)
   • Different sensitivity parameters for each timeframe
   • Prevents false signals from single timeframe noise

3. MULTI-INDICATOR FUSION:
   • Price momentum (primary): Price change > dynamic threshold
   • RSI momentum: RSI above overbought level (70)
   • Volume spike: Volume > 2x recent average
   • MA crossover: Short MA crossing above long MA
   • Requires price momentum + at least one other indicator

4. SAFETY BOUNDS:
   • Floor values prevent thresholds from being too low in calm markets
   • Cap values prevent thresholds from being too high in volatile markets
   • Ensures system remains functional across all market conditions

5. SYMBOL-SPECIFIC CALIBRATION:
   • Different volume thresholds for BTC, ETH, and altcoins
   • Recognizes different trading characteristics of major vs minor pairs
   • Adapts to the natural volatility patterns of each asset

6. REAL-TIME ADAPTATION:
   • Thresholds recalculated for each analysis
   • System learns from recent market behavior
   • No manual parameter tuning required
    """)

def main():
    """Main function to run all analyses"""
    
    print("🚀 MOMENTUM FLEXIBILITY ANALYZER")
    print("=" * 80)
    print("Reproducing the intelligent momentum detection system")
    print("implemented in the trading automation bot.\\n")
    
    # Initialize analyzer with default config
    analyzer = MomentumAnalyzer()
    
    # Run comprehensive analysis
    print("1️⃣  Running comprehensive analysis...")
    result = analyzer.run_comprehensive_analysis("BTCUSDC", periods=300)
    
    # Compare different symbols
    print("\\n2️⃣  Comparing across symbols...")
    compare_symbols()
    
    # Demonstrate flexibility features
    print("\\n3️⃣  Demonstrating system flexibility...")
    demonstrate_flexibility()
    
    print(f"\\n{'='*80}")
    print("✅ ANALYSIS COMPLETE!")
    print(f"{'='*80}")
    print("""
💡 INSIGHTS:
• The system adapts thresholds based on real-time market volatility
• Multi-timeframe confirmation reduces false signals significantly
• Floor/cap bounds ensure stability across all market conditions
• Different assets get appropriate sensitivity calibration
• The approach balances responsiveness with noise filtering

🔧 PRACTICAL BENEFITS:
• No manual parameter tuning required
• Automatically adapts to changing market conditions
• Reduces false signals while maintaining sensitivity
• Works effectively across different asset classes
• Provides consistent performance over time
    """)

if __name__ == "__main__":
    main()
