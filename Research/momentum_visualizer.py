#!/usr/bin/env python3
"""
Momentum Flexibility Visualizer
===============================

This script visualizes the dynamic momentum detection system implemented in the trading bot.
It shows how thresholds adapt based on volatility (ATR) and how different timeframes 
interact to create robust momentum signals.

Features:
- Dynamic threshold calculation based on ATR
- Multi-timeframe momentum analysis (1m, 5m, 15m)
- RSI, Volume Spike, and MA Cross indicators
- Interactive visualization of momentum scoring
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
import random
import seaborn as sns
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import warnings
warnings.filterwarnings('ignore')

# Set style
plt.style.use('seaborn-v0_8-darkgrid')
sns.set_palette("husl")

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

class MomentumAnalyzer:
    """Analyzes and visualizes momentum detection flexibility"""
    
    def __init__(self, config: MomentumConfig = None):
        self.config = config or MomentumConfig()
        
    def generate_sample_data(self, symbol: str = "BTCUSDC", days: int = 7) -> pd.DataFrame:
        """Generate realistic sample price data for visualization"""
        
        # Base parameters for different symbols
        base_params = {
            "BTCUSDC": {"base_price": 45000, "volatility": 0.02, "trend": 0.001},
            "ETHUSDC": {"base_price": 2800, "volatility": 0.025, "trend": 0.0015},
            "ADAUSDC": {"base_price": 0.45, "volatility": 0.035, "trend": 0.002}
        }
        
        params = base_params.get(symbol, base_params["BTCUSDC"])
        
        # Generate minute-level data
        periods = days * 24 * 60  # minutes
        dates = pd.date_range(
            start=datetime.now() - timedelta(days=days),
            periods=periods,
            freq='1min'
        )
        
        # Generate price with trend and random walk
        np.random.seed(42)  # For reproducible results
        price_changes = np.random.normal(
            params["trend"] / (24 * 60),  # Daily trend divided by minutes
            params["volatility"] / np.sqrt(24 * 60),  # Scale volatility
            periods
        )
        
        # Add some momentum events
        momentum_events = np.random.choice(periods, size=int(periods * 0.05), replace=False)
        for event in momentum_events:
            if event < periods - 10:
                price_changes[event:event+10] += np.random.normal(0.002, 0.001, 10)
        
        prices = params["base_price"] * np.cumprod(1 + price_changes)
        
        # Generate volume (inversely correlated with price stability)
        base_volume = 1000000 if "BTC" in symbol else 500000
        volume_changes = np.abs(price_changes) * 50 + np.random.normal(0, 0.1, periods)
        volumes = base_volume * (1 + volume_changes)
        
        return pd.DataFrame({
            'timestamp': dates,
            'open': prices,
            'high': prices * (1 + np.abs(np.random.normal(0, 0.001, periods))),
            'low': prices * (1 - np.abs(np.random.normal(0, 0.001, periods))),
            'close': prices,
            'volume': volumes
        })
    
    def calculate_atr_percent(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR as percentage of price"""
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        
        true_range = np.maximum(high_low, np.maximum(high_close, low_close))
        atr = true_range.rolling(window=period).mean()
        atr_percent = atr / df['close'] * 100
        
        return atr_percent.fillna(0)
    
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
    
    def calculate_rsi(self, prices: pd.Series, period: int = 14) -> pd.Series:
        """Calculate RSI"""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))
    
    def check_volume_spike(self, volumes: pd.Series, window: int = 20) -> pd.Series:
        """Check for volume spikes"""
        avg_volume = volumes.rolling(window=window).mean()
        return volumes > (avg_volume * self.config.volume_spike_multiplier)
    
    def check_ma_cross(self, prices: pd.Series) -> pd.Series:
        """Check for moving average crossover"""
        ma_short = prices.rolling(window=self.config.ma_periods_short).mean()
        ma_long = prices.rolling(window=self.config.ma_periods_long).mean()
        
        # Detect recent crossover (short MA crossing above long MA)
        cross_above = (ma_short > ma_long) & (ma_short.shift() <= ma_long.shift())
        return cross_above.fillna(False)
    
    def analyze_momentum(self, df: pd.DataFrame, timeframe: str = '1m') -> pd.DataFrame:
        """Analyze momentum for a given timeframe"""
        
        # Resample data if needed
        if timeframe != '1m':
            df_resampled = df.set_index('timestamp').resample(timeframe).agg({
                'open': 'first',
                'high': 'max',
                'low': 'min',
                'close': 'last',
                'volume': 'sum'
            }).dropna()
        else:
            df_resampled = df.set_index('timestamp')
        
        # Calculate indicators
        df_resampled['atr_percent'] = self.calculate_atr_percent(df_resampled)
        df_resampled['price_change_pct'] = df_resampled['close'].pct_change() * 100
        df_resampled['rsi'] = self.calculate_rsi(df_resampled['close'])
        df_resampled['volume_spike'] = self.check_volume_spike(df_resampled['volume'])
        df_resampled['ma_cross'] = self.check_ma_cross(df_resampled['close'])
        
        # Calculate dynamic thresholds
        df_resampled['dynamic_threshold'] = df_resampled['atr_percent'].apply(
            lambda x: self.calculate_dynamic_threshold(x, timeframe)
        )
        
        # Momentum conditions
        df_resampled['price_momentum'] = df_resampled['price_change_pct'] > df_resampled['dynamic_threshold']
        df_resampled['rsi_momentum'] = df_resampled['rsi'] > self.config.rsi_overbought
        
        # Combined momentum signal
        df_resampled['momentum_score'] = (
            df_resampled['price_momentum'].astype(int) +
            df_resampled['rsi_momentum'].astype(int) +
            df_resampled['volume_spike'].astype(int) +
            df_resampled['ma_cross'].astype(int)
        )
        
        df_resampled['has_momentum'] = (
            df_resampled['price_momentum'] & 
            (df_resampled['rsi_momentum'] | df_resampled['volume_spike'] | df_resampled['ma_cross'])
        )
        
        return df_resampled.reset_index()
    
    def create_comprehensive_visualization(self, symbol: str = "BTCUSDC", days: int = 3):
        """Create comprehensive momentum visualization"""
        
        # Generate sample data
        print(f"Generating sample data for {symbol}...")
        df = self.generate_sample_data(symbol, days)
        
        # Analyze different timeframes
        analysis_1m = self.analyze_momentum(df, '1m')
        analysis_5m = self.analyze_momentum(df, '5m')
        analysis_15m = self.analyze_momentum(df, '15m')
        
        # Create the visualization
        fig = plt.figure(figsize=(20, 16))
        gs = fig.add_gridspec(5, 3, height_ratios=[3, 2, 2, 2, 1.5], hspace=0.3, wspace=0.3)
        
        # Main price chart with momentum signals
        ax1 = fig.add_subplot(gs[0, :])
        self._plot_price_with_momentum(ax1, analysis_1m, analysis_5m, analysis_15m, symbol)
        
        # Dynamic threshold evolution
        ax2 = fig.add_subplot(gs[1, :])
        self._plot_dynamic_thresholds(ax2, analysis_1m, analysis_5m, analysis_15m)
        
        # Individual timeframe analysis
        ax3 = fig.add_subplot(gs[2, 0])
        self._plot_timeframe_analysis(ax3, analysis_1m, '1m')
        
        ax4 = fig.add_subplot(gs[2, 1])
        self._plot_timeframe_analysis(ax4, analysis_5m, '5m')
        
        ax5 = fig.add_subplot(gs[2, 2])
        self._plot_timeframe_analysis(ax5, analysis_15m, '15m')
        
        # Indicator correlation
        ax6 = fig.add_subplot(gs[3, 0])
        self._plot_rsi_analysis(ax6, analysis_1m)
        
        ax7 = fig.add_subplot(gs[3, 1])
        self._plot_volume_analysis(ax7, analysis_1m)
        
        ax8 = fig.add_subplot(gs[3, 2])
        self._plot_ma_analysis(ax8, analysis_1m)
        
        # Summary statistics
        ax9 = fig.add_subplot(gs[4, :])
        self._plot_momentum_summary(ax9, analysis_1m, analysis_5m, analysis_15m)
        
        plt.suptitle(f'Dynamic Momentum Analysis for {symbol}', fontsize=20, fontweight='bold')
        plt.tight_layout()
        plt.show()
        
        return fig
    
    def _plot_price_with_momentum(self, ax, df_1m, df_5m, df_15m, symbol):
        """Plot price chart with momentum signals"""
        
        # Plot price
        ax.plot(df_1m['timestamp'], df_1m['close'], 'b-', linewidth=1, label='Price', alpha=0.7)
        
        # Mark momentum signals for different timeframes
        momentum_1m = df_1m[df_1m['has_momentum']]
        momentum_5m = df_5m[df_5m['has_momentum']]
        momentum_15m = df_15m[df_15m['has_momentum']]
        
        # Plot momentum points
        if not momentum_1m.empty:
            ax.scatter(momentum_1m['timestamp'], momentum_1m['close'], 
                      c='red', s=20, alpha=0.6, label='1m Momentum', marker='^')
        
        if not momentum_5m.empty:
            ax.scatter(momentum_5m['timestamp'], momentum_5m['close'], 
                      c='orange', s=40, alpha=0.7, label='5m Momentum', marker='s')
        
        if not momentum_15m.empty:
            ax.scatter(momentum_15m['timestamp'], momentum_15m['close'], 
                      c='green', s=60, alpha=0.8, label='15m Momentum', marker='o')
        
        ax.set_title(f'{symbol} Price with Dynamic Momentum Signals')
        ax.set_ylabel('Price (USDC)')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Format x-axis
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    
    def _plot_dynamic_thresholds(self, ax, df_1m, df_5m, df_15m):
        """Plot evolution of dynamic thresholds"""
        
        # Sample every 60 points to avoid overcrowding
        step = max(1, len(df_1m) // 200)
        
        ax.plot(df_1m['timestamp'][::step], df_1m['dynamic_threshold'][::step], 
               'r-', label='1m Threshold', linewidth=2)
        ax.plot(df_5m['timestamp'], df_5m['dynamic_threshold'], 
               'orange', label='5m Threshold', linewidth=2)
        ax.plot(df_15m['timestamp'], df_15m['dynamic_threshold'], 
               'g-', label='15m Threshold', linewidth=2)
        
        # Add static threshold lines for comparison
        ax.axhline(y=0.5, color='gray', linestyle='--', alpha=0.5, label='Static 0.5%')
        ax.axhline(y=1.0, color='gray', linestyle=':', alpha=0.5, label='Static 1.0%')
        
        ax.set_title('Dynamic Momentum Thresholds (ATR-based)')
        ax.set_ylabel('Threshold (%)')
        ax.legend()
        ax.grid(True, alpha=0.3)
        
        # Format x-axis
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d %H:%M'))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    
    def _plot_timeframe_analysis(self, ax, df, timeframe):
        """Plot analysis for a specific timeframe"""
        
        # Sample data to avoid overcrowding
        step = max(1, len(df) // 50)
        df_sample = df[::step].copy()
        
        # Create momentum score heatmap
        colors = ['red' if x else 'lightgray' for x in df_sample['has_momentum']]
        sizes = df_sample['momentum_score'] * 20 + 10
        
        scatter = ax.scatter(range(len(df_sample)), df_sample['price_change_pct'], 
                           c=colors, s=sizes, alpha=0.6)
        
        # Add threshold line
        ax.plot(range(len(df_sample)), df_sample['dynamic_threshold'], 
               'b--', label='Dynamic Threshold', alpha=0.8)
        
        ax.axhline(y=0, color='gray', linestyle='-', alpha=0.3)
        ax.set_title(f'{timeframe} Price Changes vs Threshold')
        ax.set_ylabel('Price Change (%)')
        ax.set_xlabel('Time Periods')
        ax.legend()
        ax.grid(True, alpha=0.3)
    
    def _plot_rsi_analysis(self, ax, df):
        """Plot RSI analysis"""
        step = max(1, len(df) // 100)
        df_sample = df[::step].copy()
        
        ax.plot(range(len(df_sample)), df_sample['rsi'], 'purple', linewidth=2, label='RSI')
        ax.axhline(y=self.config.rsi_overbought, color='red', linestyle='--', 
                  label=f'Overbought ({self.config.rsi_overbought})')
        ax.axhline(y=30, color='green', linestyle='--', label='Oversold (30)')
        
        # Highlight momentum periods
        momentum_periods = df_sample[df_sample['rsi_momentum']]
        if not momentum_periods.empty:
            ax.scatter(momentum_periods.index, momentum_periods['rsi'], 
                      color='red', s=30, alpha=0.7, zorder=5)
        
        ax.set_title('RSI Momentum Analysis')
        ax.set_ylabel('RSI')
        ax.set_ylim(0, 100)
        ax.legend()
        ax.grid(True, alpha=0.3)
    
    def _plot_volume_analysis(self, ax, df):
        """Plot volume spike analysis"""
        step = max(1, len(df) // 100)
        df_sample = df[::step].copy()
        
        # Normalize volume for display
        vol_norm = df_sample['volume'] / df_sample['volume'].mean()
        
        bars = ax.bar(range(len(df_sample)), vol_norm, alpha=0.6, color='lightblue', label='Volume')
        
        # Highlight volume spikes
        spike_periods = df_sample[df_sample['volume_spike']]
        if not spike_periods.empty:
            spike_indices = [df_sample.index.get_loc(idx) for idx in spike_periods.index]
            for idx in spike_indices:
                bars[idx].set_color('red')
        
        ax.axhline(y=self.config.volume_spike_multiplier, color='red', linestyle='--', 
                  label=f'Spike Threshold ({self.config.volume_spike_multiplier}x)')
        
        ax.set_title('Volume Spike Analysis')
        ax.set_ylabel('Volume (normalized)')
        ax.legend()
        ax.grid(True, alpha=0.3)
    
    def _plot_ma_analysis(self, ax, df):
        """Plot moving average cross analysis"""
        step = max(1, len(df) // 100)
        df_sample = df[::step].copy()
        
        # Calculate MAs for display
        ma_short = df_sample['close'].rolling(window=self.config.ma_periods_short).mean()
        ma_long = df_sample['close'].rolling(window=self.config.ma_periods_long).mean()
        
        ax.plot(range(len(df_sample)), ma_short, 'blue', linewidth=2, 
               label=f'MA{self.config.ma_periods_short}')
        ax.plot(range(len(df_sample)), ma_long, 'red', linewidth=2, 
               label=f'MA{self.config.ma_periods_long}')
        
        # Highlight crossover points
        cross_periods = df_sample[df_sample['ma_cross']]
        if not cross_periods.empty:
            cross_indices = [df_sample.index.get_loc(idx) for idx in cross_periods.index]
            for idx in cross_indices:
                ax.axvline(x=idx, color='green', linestyle='--', alpha=0.7)
        
        ax.set_title('Moving Average Cross Analysis')
        ax.set_ylabel('Price')
        ax.legend()
        ax.grid(True, alpha=0.3)
    
    def _plot_momentum_summary(self, ax, df_1m, df_5m, df_15m):
        """Plot momentum summary statistics"""
        
        # Calculate summary stats
        timeframes = ['1m', '5m', '15m']
        dataframes = [df_1m, df_5m, df_15m]
        
        stats = []
        for tf, df in zip(timeframes, dataframes):
            momentum_pct = (df['has_momentum'].sum() / len(df)) * 100
            avg_threshold = df['dynamic_threshold'].mean()
            max_threshold = df['dynamic_threshold'].max()
            min_threshold = df['dynamic_threshold'].min()
            
            stats.append({
                'Timeframe': tf,
                'Momentum %': momentum_pct,
                'Avg Threshold': avg_threshold,
                'Min Threshold': min_threshold,
                'Max Threshold': max_threshold
            })
        
        # Create summary table
        ax.axis('tight')
        ax.axis('off')
        
        table_data = []
        headers = ['Timeframe', 'Momentum Signals (%)', 'Avg Threshold (%)', 
                  'Min Threshold (%)', 'Max Threshold (%)']
        
        for stat in stats:
            row = [
                stat['Timeframe'],
                f"{stat['Momentum %']:.1f}%",
                f"{stat['Avg Threshold']:.3f}%",
                f"{stat['Min Threshold']:.3f}%",
                f"{stat['Max Threshold']:.3f}%"
            ]
            table_data.append(row)
        
        table = ax.table(cellText=table_data, colLabels=headers, cellLoc='center', loc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1.2, 2)
        
        # Style the table
        for i, key in enumerate(headers):
            table[(0, i)].set_facecolor('#4CAF50')
            table[(0, i)].set_text_props(weight='bold', color='white')
        
        ax.set_title('Momentum Detection Summary Statistics', fontweight='bold', pad=20)

def create_threshold_comparison_chart():
    """Create a comparison chart showing different threshold strategies"""
    
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 10))
    
    # Sample ATR values from low to high volatility
    atr_values = np.linspace(0.1, 3.0, 100)
    
    config = MomentumConfig()
    analyzer = MomentumAnalyzer(config)
    
    # Calculate thresholds for different timeframes
    thresholds_1m = [analyzer.calculate_dynamic_threshold(atr, '1m') for atr in atr_values]
    thresholds_5m = [analyzer.calculate_dynamic_threshold(atr, '5m') for atr in atr_values]
    thresholds_15m = [analyzer.calculate_dynamic_threshold(atr, '15m') for atr in atr_values]
    
    # Plot 1: Dynamic vs Static Thresholds
    ax1.plot(atr_values, thresholds_1m, 'r-', linewidth=3, label='1m Dynamic')
    ax1.plot(atr_values, thresholds_5m, 'orange', linewidth=3, label='5m Dynamic')
    ax1.plot(atr_values, thresholds_15m, 'g-', linewidth=3, label='15m Dynamic')
    
    # Static thresholds for comparison
    ax1.axhline(y=0.5, color='gray', linestyle='--', label='Static 0.5%')
    ax1.axhline(y=1.0, color='gray', linestyle=':', label='Static 1.0%')
    
    ax1.set_xlabel('Market ATR (%)')
    ax1.set_ylabel('Momentum Threshold (%)')
    ax1.set_title('Dynamic vs Static Threshold Strategies')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    
    # Plot 2: Threshold Sensitivity
    k_values = np.linspace(0.3, 1.2, 5)
    colors = plt.cm.viridis(np.linspace(0, 1, len(k_values)))
    
    for i, k in enumerate(k_values):
        temp_config = MomentumConfig()
        temp_config.k_1m = k
        temp_analyzer = MomentumAnalyzer(temp_config)
        thresholds = [temp_analyzer.calculate_dynamic_threshold(atr, '1m') for atr in atr_values]
        ax2.plot(atr_values, thresholds, color=colors[i], linewidth=2, label=f'k={k:.1f}')
    
    ax2.set_xlabel('Market ATR (%)')
    ax2.set_ylabel('Momentum Threshold (%)')
    ax2.set_title('Sensitivity to ATR Multiplier (k)')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    
    # Plot 3: Floor and Cap Effects
    atr_low = np.linspace(0.01, 0.5, 50)
    atr_high = np.linspace(2.0, 5.0, 50)
    
    thresholds_low = [analyzer.calculate_dynamic_threshold(atr, '1m') for atr in atr_low]
    thresholds_high = [analyzer.calculate_dynamic_threshold(atr, '1m') for atr in atr_high]
    
    ax3.plot(atr_low, thresholds_low, 'b-', linewidth=3, label='Low ATR (Floor Effect)')
    ax3.axhline(y=config.floor_1m, color='red', linestyle='--', label=f'Floor ({config.floor_1m}%)')
    ax3.set_xlabel('Market ATR (%) - Low Range')
    ax3.set_ylabel('Momentum Threshold (%)')
    ax3.set_title('Floor Effect in Low Volatility')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    
    ax4.plot(atr_high, thresholds_high, 'r-', linewidth=3, label='High ATR (Cap Effect)')
    ax4.axhline(y=config.cap_1m, color='blue', linestyle='--', label=f'Cap ({config.cap_1m}%)')
    ax4.set_xlabel('Market ATR (%) - High Range')
    ax4.set_ylabel('Momentum Threshold (%)')
    ax4.set_title('Cap Effect in High Volatility')
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    
    plt.suptitle('Dynamic Momentum Threshold Analysis', fontsize=16, fontweight='bold')
    plt.tight_layout()
    plt.show()
    
    return fig

def create_multi_symbol_comparison():
    """Compare momentum detection across different symbols"""
    
    symbols = ["BTCUSDC", "ETHUSDC", "ADAUSDC"]
    analyzer = MomentumAnalyzer()
    
    fig, axes = plt.subplots(len(symbols), 2, figsize=(16, 12))
    
    for i, symbol in enumerate(symbols):
        print(f"Analyzing {symbol}...")
        
        # Generate and analyze data
        df = analyzer.generate_sample_data(symbol, days=2)
        analysis = analyzer.analyze_momentum(df, '1m')
        
        # Plot 1: Price with momentum signals
        ax1 = axes[i, 0]
        ax1.plot(analysis['timestamp'], analysis['close'], 'b-', linewidth=1, alpha=0.7)
        
        momentum_points = analysis[analysis['has_momentum']]
        if not momentum_points.empty:
            ax1.scatter(momentum_points['timestamp'], momentum_points['close'], 
                       c='red', s=30, alpha=0.8, zorder=5)
        
        ax1.set_title(f'{symbol} - Price with Momentum Signals')
        ax1.set_ylabel('Price (USDC)')
        ax1.grid(True, alpha=0.3)
        
        # Plot 2: Threshold adaptation
        ax2 = axes[i, 1]
        step = max(1, len(analysis) // 100)
        sample = analysis[::step]
        
        ax2.plot(range(len(sample)), sample['dynamic_threshold'], 'g-', linewidth=2, label='Dynamic')
        ax2.axhline(y=0.5, color='gray', linestyle='--', alpha=0.5, label='Static 0.5%')
        ax2.fill_between(range(len(sample)), sample['dynamic_threshold'], alpha=0.3)
        
        ax2.set_title(f'{symbol} - Threshold Adaptation')
        ax2.set_ylabel('Threshold (%)')
        ax2.legend()
        ax2.grid(True, alpha=0.3)
        
        # Format x-axis for price chart
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        ax1.xaxis.set_major_locator(mdates.HourLocator(interval=4))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    
    plt.suptitle('Multi-Symbol Momentum Flexibility Comparison', fontsize=16, fontweight='bold')
    plt.tight_layout()
    plt.show()
    
    return fig

def main():
    """Main function to run all visualizations"""
    
    print("Momentum Flexibility Visualizer")
    print("=" * 50)
    print()
    
    # Initialize analyzer
    config = MomentumConfig()
    analyzer = MomentumAnalyzer(config)
    
    print("Creating visualizations...")
    print()
    
    # 1. Comprehensive analysis for BTCUSDC
    print("1. Comprehensive momentum analysis...")
    fig1 = analyzer.create_comprehensive_visualization("BTCUSDC", days=3)
    
    # 2. Threshold comparison charts
    print("2. Threshold strategy comparison...")
    fig2 = create_threshold_comparison_chart()
    
    # 3. Multi-symbol comparison
    print("3. Multi-symbol comparison...")
    fig3 = create_multi_symbol_comparison()
    
    print()
    print("Visualization complete!")
    print()
    print("Key insights:")
    print("- Dynamic thresholds adapt to market volatility (ATR)")
    print("- Different timeframes use different sensitivity parameters")
    print("- Floor and cap values prevent extreme threshold values")
    print("- Multi-indicator confirmation reduces false signals")
    print("- System balances sensitivity vs noise filtering")

if __name__ == "__main__":
    main()
