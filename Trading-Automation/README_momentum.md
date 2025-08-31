# Momentum Flexibility Visualization Scripts

This directory contains two scripts that reproduce and visualize the intelligent momentum detection system implemented in the trading automation bot.

## 📊 Scripts Overview

### 1. `momentum_visualizer.py` (Graphical Version)
- **Requirements:** matplotlib, seaborn, numpy, pandas
- **Features:** Interactive charts and graphs
- **Output:** Visual plots showing momentum analysis
- **Install dependencies:** `pip install -r requirements_viz.txt`

### 2. `momentum_analyzer.py` (Console Version) ✅
- **Requirements:** Built-in Python libraries only
- **Features:** Comprehensive console-based analysis
- **Output:** Detailed text reports and statistics
- **Ready to run:** No additional dependencies needed

## 🚀 Quick Start

Run the console analyzer (recommended):
```bash
python momentum_analyzer.py
```

Or install dependencies and run the graphical version:
```bash
pip install -r requirements_viz.txt
python momentum_visualizer.py
```

## 🎯 What These Scripts Demonstrate

### Dynamic Momentum System Features

1. **ATR-Based Threshold Adaptation**
   - Automatically adjusts based on market volatility
   - Higher ATR → Higher thresholds (fewer false signals)
   - Lower ATR → Lower thresholds (catches small moves)

2. **Multi-Timeframe Confirmation**
   - Analyzes 1m, 5m, and 15m timeframes
   - Different sensitivity for each timeframe
   - Prevents single-timeframe noise

3. **Multi-Indicator Fusion**
   - Price momentum (primary signal)
   - RSI momentum (overbought detection)
   - Volume spikes (unusual activity)
   - Moving average crossovers (trend changes)

4. **Safety Bounds**
   - Floor values prevent overly low thresholds
   - Cap values prevent overly high thresholds
   - Ensures functionality across all market conditions

5. **Symbol-Specific Calibration**
   - Different volume thresholds for BTC, ETH, altcoins
   - Adapts to natural volatility patterns
   - Recognizes trading characteristics of different pairs

## 📈 Sample Output Analysis

The console analyzer shows:

```
🎯 HAS MOMENTUM: 🟢 YES/🔴 NO
💹 Price Momentum: ✅/❌
📈 RSI Momentum: ✅/❌  
📊 Volume Spike: ✅/❌
🔄 MA Cross: ✅/❌
```

### Threshold Adaptation Example:
```
ATR Scenario         1m Threshold    5m Threshold    15m Threshold
Low Volatility       0.110%          0.140%          0.170%
Normal Volatility    0.440%          0.560%          0.680%
High Volatility      0.825%          1.050%          1.275%
Extreme Volatility   1.500%          1.800%          2.000%
```

## 🔧 Configuration Parameters

The system uses these key parameters:

```python
# ATR multipliers (sensitivity)
k_1m: 0.55   # More sensitive for 1m
k_5m: 0.70   # Medium sensitivity for 5m  
k_15m: 0.85  # Less sensitive for 15m

# Safety bounds
floor_1m: 0.07%   # Minimum threshold
cap_1m: 1.5%      # Maximum threshold

# Indicator thresholds
rsi_overbought: 70
volume_spike_multiplier: 2.0
```

## 💡 Key Insights

1. **Adaptive Intelligence**: The system learns from recent market behavior and automatically adjusts sensitivity.

2. **Noise Filtering**: Multi-timeframe and multi-indicator confirmation significantly reduces false signals.

3. **Market Condition Awareness**: Different volatility environments get appropriate threshold calibration.

4. **No Manual Tuning**: Parameters self-adjust based on real-time market data.

5. **Cross-Asset Compatibility**: Works effectively across different cryptocurrency pairs.

## 🏗️ Technical Implementation

The scripts reproduce the exact logic from `trading_automation.py`:

- `calculate_atr_percent()` - ATR calculation as percentage of price
- `dynamic_momentum_threshold()` - ATR-based threshold calculation  
- `check_advanced_momentum()` - Multi-indicator momentum detection
- `has_recent_momentum()` - Multi-timeframe confirmation logic

## 📋 Use Cases

1. **System Understanding**: Visualize how the momentum detection works
2. **Parameter Tuning**: Test different configuration values
3. **Market Analysis**: Understand momentum patterns across timeframes
4. **Strategy Validation**: Verify momentum logic before live trading
5. **Educational**: Learn about adaptive trading algorithms

## 🎨 Customization

Both scripts can be easily modified to:
- Test different symbols (BTC, ETH, altcoins)
- Adjust timeframe parameters
- Modify indicator thresholds
- Add new momentum indicators
- Change analysis periods

Run the scripts to see the momentum flexibility in action! 🚀
