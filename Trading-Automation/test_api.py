#!/usr/bin/env python3

import sys
sys.path.append('.')

from trading_automation import test_api_connection

if __name__ == "__main__":
    print("Testing Binance API connection...")
    test_api_connection()
