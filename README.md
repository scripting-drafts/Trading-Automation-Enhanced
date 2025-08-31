# Trading Automation Enhanced

Advanced cryptocurrency trading bot with intelligent momentum detection and multi-timeframe analysis.

## 📁 Project Structure

### Main Trading System

| File/Directory             | Description                                  |
|----------------------------|----------------------------------------------|
| `Trading-Automation/`      | Core trading automation system              |
| ├─ `trading_automation.py` | Main trading bot with enhanced momentum detection |
| ├─ `update_symbols.py`     | Symbol fetcher and market data updater      |
| ├─ `secrets.py`            | API keys and configuration                   |
| `symbols.yaml`             | USDC trading pairs and market data          |
| `trading.service`          | Linux systemd service configuration         |
| `symbols_update.service`   | Service for automatic symbol updates        |

### Research & Analysis Tools

| File                       | Description                                  |
|----------------------------|----------------------------------------------|
| `Research/`                | Independent momentum analysis tools          |
| ├─ `momentum_analyzer.py`  | Console-based momentum analysis (no deps)   |
| ├─ `momentum_visualizer.py`| Graphical momentum analysis (matplotlib)    |
| ├─ `requirements_viz.txt`  | Dependencies for visualization tools        |
| └─ `README.md`             | Detailed research tools documentation       |

## 🚀 Quick Start

### Prerequisites

```bash
pip install python-telegram-bot==22.2
```

### Setup Process

1. **Generate symbols.yaml** - Update trading pairs and market data
2. **Configure API keys** - Set up Binance and Telegram credentials in `secrets.py`
3. **Run the bot** - Start the main trading automation
4. **Keep symbol updater running** - Maintain fresh market data

### Research Tools

```bash
cd Research
python momentum_analyzer.py              # Console analysis (no dependencies)
pip install -r requirements_viz.txt      # Install visualization dependencies
python momentum_visualizer.py            # Graphical analysis
```

## 🧠 Enhanced Momentum Detection System

The bot now features an intelligent, adaptive momentum detection system that automatically adjusts to market conditions:

### Key Features

- **ATR-Based Dynamic Thresholds**: Automatically adapts sensitivity based on market volatility
- **Multi-Timeframe Confirmation**: Requires signals across 1m, 5m, and 15m timeframes
- **Multi-Indicator Fusion**: Combines price momentum, RSI, volume spikes, and MA crossovers
- **Symbol-Specific Calibration**: Different thresholds for BTC, ETH, and altcoins
- **Safety Bounds**: Floor and cap values ensure stability across all market conditions

### Volume Thresholds by Asset

- **BTC**: 660 USDC minimum volume
- **ETH**: 500,000 USDC minimum volume  
- **Other pairs**: 300,000 USDC minimum volume

### Adaptive Threshold Parameters

| Timeframe | ATR Multiplier | Floor Threshold | Cap Threshold |
|-----------|----------------|-----------------|---------------|
| 1m        | 0.55           | 0.070%          | 1.5%          |
| 5m        | 0.70           | 0.100%          | 1.8%          |
| 15m       | 0.85           | 0.150%          | 2.0%          |

## 📈 Trading Strategy

### Entry Conditions

The bot considers buying only when ALL of the following conditions are met:

- **Multi-timeframe momentum**: Positive momentum detected across 1m, 5m, and 15m timeframes
- **Volume confirmation**: Trading volume exceeds minimum thresholds for the asset class
- **Multi-indicator confluence**: Price momentum plus at least one additional indicator (RSI, volume spike, or MA crossover)
- **Dynamic threshold validation**: Price changes exceed ATR-based adaptive thresholds

### Exit Strategy

The bot employs a sophisticated exit system:

- **Trailing stop loss**: Automatically adjusts stop levels as price moves favorably
- **Maximum hold time**: Prevents indefinite position holding in sideways markets
- **Rapid reversal protection**: Quick exit on sharp price reversals to minimize losses

### Risk Management

- **Position sizing**: Calculated based on account balance and risk parameters
- **Asset-specific limits**: Different volume requirements for major vs minor pairs
- **Volatility adaptation**: Threshold sensitivity adjusts to current market conditions

## 📊 Performance Characteristics

![Trading Example](https://i.imgur.com/7oMaPLM.jpeg)

The system is designed to:

- **Catch strong trends**: Enter during clear upward momentum phases
- **Avoid false signals**: Multi-timeframe confirmation reduces noise
- **Adapt to volatility**: Dynamic thresholds prevent over-trading in choppy markets
- **Exit efficiently**: Trailing stops and time limits protect profits

## ⚙️ Configuration & Tuning

### Micro-Scalping Optimization

For high-frequency trading scenarios:

**Threshold Multipliers (k)**:

- Start with k≈0.55–0.65 for 1m
- Use k≈0.7–0.8 for 5m  
- Set k≈0.85–1.0 for 15m

**Sensitivity Adjustments**:

- **Too many entries**: Increase k values or floor thresholds
- **Missing clean moves**: Decrease k slightly on 1m/5m timeframes
- **Volatile spikes**: Keep cap thresholds ≤ 2% to avoid waiting indefinitely

**Exit Optimization**:

- Consider reducing MIN_PROFIT to ~0.8% for faster fills
- Tighten TRAIL_STOP to ~0.5% if spreads are tight
- Monitor slippage on rapid reversals

## 🔬 Research & Analysis

The `Research/` directory contains powerful tools for analyzing and understanding the momentum detection system:

### Console Analyzer

```bash
python Research/momentum_analyzer.py
```

- Zero external dependencies
- Reproduces exact trading bot logic
- Multi-symbol comparison
- Threshold adaptation demonstration

### Graphical Visualizer  

```bash
pip install -r Research/requirements_viz.txt
python Research/momentum_visualizer.py
```

- Interactive matplotlib charts
- Visual threshold analysis
- Market condition scenarios
- Performance comparison plots

### Benefits

- **No manual tuning required**: System automatically adapts to market conditions
- **Consistent performance**: Works effectively across different asset classes
- **Noise filtering**: Multi-timeframe approach reduces false signals
- **Market adaptive**: Real-time threshold calculation based on recent volatility

## 🛠️ System Requirements

### Dependencies

```bash
pip install python-telegram-bot==22.2
```

### Optional (for research tools)

```bash
pip install -r Research/requirements_viz.txt
```

### System Services (Linux)

- `trading.service`: Main bot service configuration
- `symbols_update.service`: Automatic market data updates

---

**⚠️ Risk Warning**: This is a high-frequency trading bot designed for experienced traders. Always test thoroughly with small amounts before deploying significant capital. Past performance does not guarantee future results.