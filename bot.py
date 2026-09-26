import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
import pandas as pd
import yfinance as yf
import ta

logging.basicConfig(level=logging.INFO)

# Genişləndirilmiş Cütlüklər Siyahısı
PAIRS = {
    # Əsas cütlüklər (Majors)
    "EUR/USD": "EURUSD=X",
    "GBP/USD": "GBPUSD=X",
    "USD/JPY": "JPY=X",
    "AUD/USD": "AUDUSD=X",
    "USD/CAD": "CAD=X",
    "USD/CHF": "CHF=X",
    "NZD/USD": "NZDUSD=X",
    
    # Kross cütlüklər (Minors)
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",
    
    # Qiymətli Metallar
    "QIZIL (XAU/USD)": "GC=F",
    "GÜMÜŞ (XAG/USD)": "SI=F"
}

def analyze_best_pair():
    candidates = []
    
    for name, ticker in PAIRS.items():
        try:
            data = yf.download(ticker, period="10d", interval="1h", progress=False)
            if data.empty or len(data) < 30:
                continue
            
            close = data['Close'].squeeze()
            high = data['High'].squeeze()
            low = data['Low'].squeeze()
            current_price = float(close.iloc[-1])
            
            rsi = ta.momentum.RSIIndicator(close=close, window=14).rsi().iloc[-1]
            ema_fast = ta.trend.EMAIndicator(close=close, window=9).ema_indicator().iloc[-1]
            ema_slow = ta.trend.EMAIndicator(close=close, window=21).ema_indicator().iloc[-1]
            atr = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range().iloc[-1]
            
            score = 0
            direction = None
            
            # BUY Təhlili
            if rsi <= 45 and ema_fast > ema_slow:
                score = round((50 - rsi) * 2 + 10, 1)
                direction = "BUY"
                sl = current_price - (atr * 1.5)
                tp = current_price + (atr * 3.0)
            
            # SELL Təhlili
            elif rsi >= 55 and ema_fast < ema_slow:
                score = round((rsi - 50) * 2 + 10, 1)
                direction = "SELL"
                sl = current_price + (atr * 1.5)
                tp = current_price - (atr * 3.0)
                
            if direction:
                candidates.append({
                    "name": name,
                    "direction": direction,
                    "score": score,
                    "entry": current_price,
                    "sl": sl,
                    "tp": tp
                })
        except Exception:
            continue
            
    if candidates:
        # Aralarında riski ən aşağı olanı (ən yüksək xallını) seçirik
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates[0]
    
    return None

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Bütün Forex cütlükləri və Metallar skan edilir, ƏN AŞAĞI RİSKLİ siqnal seçilir...")
    
    best = analyze_best_pair()
    
    if best:
        is_jpy_or_metal = "JPY" in best['name'] or "QIZIL" in best['name'] or "GÜMÜŞ" in best['name']
        entry = f"{best['entry']:.2f}" if is_jpy_or_metal else f"{best['entry']:.5f}"
        sl = f"{best['sl']:.2f}" if is_jpy_or_metal else f"{best['sl']:.5f}"
        tp = f"{best['tp']:.2f}" if is_jpy_or_metal else f"{best['tp']:.5f}"
        
        msg = (
            f"🏆 **ƏN AŞAĞI RİSKLİ SİQNAL TAPILDI** 🏆\n\n"
            f"📌 **Parite:** {best['name']}\n"
            f"📊 **Yön:** {'🟢 ALIŞ (BUY)' if best['direction'] == 'BUY' else '🔴 SATIŞ (SELL)'}\n\n"
            f"🔹 **Entry:** `{entry}`\n"
            f"🛑 **Stop Loss (SL):** `{sl}`\n"
            f"🎯 **Take Profit (TP):** `{tp}`\n\n"
            f"💡 *Qeyd: Bütün cütlüklər arasında risk/mənfəət nisbəti ən ideal olanı budur.*"
        )
    else:
        msg = "🛡️ **RİSK XƏBƏRDARLIĞI:** Hazırda bütün cütlüklərdə risk yüksəkdir və ya net trend yoxdur. Ən təhlükəsiz addım **GÖZLƏMƏKDİR**."
        
    await update.message.reply_text(msg, parse_mode="Markdown")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktivdir! Bütün cütlüklər arasından ən az riskli siqnalı tapmaq üçün /signal yazın.")

def main():
    token = os.environ.get("TELEGRAM_TOKEN", "8814130355:AAEecRTzl8Yt6j-0hlE7VjBSbQY16t0SFwg")
    app = Application.builder().token(token).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("signal", signal_command))
    
    app.run_polling()

if __name__ == "__main__":
    main()
