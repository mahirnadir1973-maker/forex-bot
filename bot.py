import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import pandas as pd
import yfinance as yf
import ta

logging.basicConfig(level=logging.INFO)

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
            return {"status": "Məlumat alınamadı", "details": None}
        
        close = data['Close'].squeeze()
        current_price = float(close.iloc[-1])
        
        rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1]
        ema_fast = ta.trend.EMAIndicator(close=close, window=9).ema_indicator().iloc[-1]
        ema_slow = ta.trend.EMAIndicator(close=close, window=21).ema_indicator().iloc[-1]
        
        high = data['High'].squeeze()
        low = data['Low'].squeeze()
        atr = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range().iloc[-1]
        
        # Daha elastik və aktiv siqnal şərtləri
        if rsi < 48 or (ema_fast > ema_slow and rsi < 55):
            sl = current_price - (atr * 1.2)
            tp = current_price + (atr * 2.4)
            return {
                "status": "🟢 ALIŞ (BUY)",
                "entry": current_price,
                "sl": sl,
                "tp": tp
            }
        elif rsi > 52 or (ema_fast < ema_slow and rsi > 45):
            sl = current_price + (atr * 1.2)
            tp = current_price - (atr * 2.4)
            return {
                "status": "🔴 SATIŞ (SELL)",
                "entry": current_price,
                "sl": sl,
                "tp": tp
            }
        else:
            return {
                "status": "⚪ NEUTR (Gözlə)",
                "details": None
            }
    except Exception:
        return {"status": "Xəta baş verdi", "details": None}

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📊 Bütün əsas Forex cütlükləri və Qızıl analiz edilir, zəhmət olmasa gözləyin...")
    
    report = "📈 **BAZAR ANALİZİ VƏ SİQNALLAR** 📉\n\n"
    for name, ticker in PAIRS.items():
        res = analyze_pair(ticker)
        status = res["status"]
        
        if "BUY" in status or "SELL" in status:
            entry = f"{res['entry']:.5f}" if "USD" in name else f"{res['entry']:.2f}"
            sl = f"{res['sl']:.5f}" if "USD" in name else f"{res['sl']:.2f}"
            tp = f"{res['tp']:.5f}" if "USD" in name else f"{res['tp']:.2f}"
            
            report += (
                f"• **{name}**: {status}\n"
                f"  └ 📌 *Entry*: `{entry}` | 🛑 *SL*: `{sl}` | 🎯 *TP*: `{tp}`\n\n"
            )
        else:
            report += f"• **{name}**: {status}\n"
        
    await update.message.reply_text(report, parse_mode="Markdown")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktivdir! Analiz üçün /signal yazın.")

def main():
    token = os.environ.get("TELEGRAM_TOKEN", "8814130355:AAEecRTzl8Yt6j-0hlE7VjBSbQY16t0SFwg")
    app = Application.builder().token(token).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("signal", signal_command))
    
    app.run_polling()

if __name__ == "__main__":
    main()
