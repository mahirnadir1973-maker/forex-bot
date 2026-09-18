import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import pandas as pd
import yfinance as yf
import ta

logging.basicConfig(level=logging.INFO)

# Analiz edilecek döviz çiftleri ve Altın
PAIRS = {
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "USD/CHF": "CHF=X",
    "QIZIL (XAU/USD)": "GC=F"
}

def analyze_pair(ticker_symbol):
    try:
        data = yf.download(ticker_symbol, period="5d", interval="1h", progress=False)
        if data.empty or len(data) < 30:
            return "Məlumat alınamadı"
        
        close = data['Close'].squeeze()
        rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1]
        ema_fast = ta.trend.EMAIndicator(close=close, window=9).ema_indicator().iloc[-1]
        ema_slow = ta.trend.EMAIndicator(close=close, window=21).ema_indicator().iloc[-1]
        
        if rsi < 40 and ema_fast > ema_slow:
            return "🟢 ALIŞ (BUY)"
        elif rsi > 60 and ema_fast < ema_slow:
            return "🔴 SATIŞ (SELL)"
        else:
            return "⚪ NEUTR (Gözlə)"
    except Exception:
        return "Xəta baş verdi"

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Bütün əsas Forex cütlükləri və Qızıl analiz edilir, zəhmət olmasa gözləyin...")
    
    report = "📈 **BAZAR ANALİZİ VƏ SİQNALLAR** 📉\n\n"
    for name, ticker in PAIRS.items():
        res = analyze_pair(ticker)
        report += f"• **{name}**: {res}\n"
        
    await update.message.reply_text(report, parse_mode="Markdown")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktivdir! Analiz üçün /signal yazın.")

def main():
    # Telegram Bot Token'ınızı buraya tırnak işaretleri içine yazın
    token = os.environ.get("TELEGRAM_TOKEN", "8814130355:AAEecRTzl8Yt6j-0hlE7VjBSbQY16t0SFwg")
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("signal", signal_command))
    
    app.run_polling()

if __name__ == "__main__":
    main()
