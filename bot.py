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
    "NZD/USD": "NZDUSD=X",
    "EUR/GBP": "EURGBP=X",
    "EUR/JPY": "EURJPY=X",
    "GBP/JPY": "GBPJPY=X",
    "AUD/JPY": "AUDJPY=X",
    "QIZIL (XAU/USD)": "GC=F",
    "GÜMÜŞ (XAG/USD)": "SI=F"
}

def clean_data(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def analyze_all_valid_pairs():
    candidates = []

    for name, ticker in PAIRS.items():
        try:
            # Data yükləmə
            data_1h = clean_data(yf.download(ticker, period="10d", interval="1h", progress=False, auto_adjust=True))
            data_1d = clean_data(yf.download(ticker, period="100d", interval="1d", progress=False, auto_adjust=True))

            if data_1h.empty or len(data_1h) < 30 or data_1d.empty or len(data_1d) < 30:
                continue

            close_1h = data_1h['Close']
            high_1h = data_1h['High']
            low_1h = data_1h['Low']
            current_price = float(close_1h.iloc[-1])

            close_1d = data_1d['Close']

            # İndikatorlar (1H)
            rsi = ta.momentum.RSIIndicator(close=close_1h, window=14).rsi().iloc[-1]
            ema_fast = ta.trend.EMAIndicator(close=close_1h, window=9).ema_indicator().iloc[-1]
            ema_slow = ta.trend.EMAIndicator(close=close_1h, window=21).ema_indicator().iloc[-1]
            atr = ta.volatility.AverageTrueRange(high=high_1h, low=low_1h, close=close_1h, window=14).average_true_range().iloc[-1]

            # ADX Və Günlük EMA
            adx_ind = ta.trend.ADXIndicator(high=high_1h, low=low_1h, close=close_1h, window=14)
            adx = adx_ind.adx().iloc[-1]
            ema_daily = ta.trend.EMAIndicator(close=close_1d, window=50).ema_indicator().iloc[-1]

            if pd.isna(rsi) or pd.isna(ema_fast) or pd.isna(ema_slow) or pd.isna(atr) or pd.isna(adx) or pd.isna(ema_daily):
                continue

            # Yatay bazar filtri
            if adx < 20:
                continue

            score = 0
            direction = None

            # BUY şərtləri
            if 35 <= rsi <= 55 and ema_fast > ema_slow and current_price > ema_daily:
                score = round((55 - rsi) * 1.5 + (adx / 2), 1)
                direction = "BUY"
                sl = current_price - (atr * 1.5)
                tp = current_price + (atr * 3.0)

            # SELL şərtləri
            elif 45 <= rsi <= 65 and ema_fast < ema_slow and current_price < ema_daily:
                score = round((rsi - 45) * 1.5 + (adx / 2), 1)
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
                    "tp": tp,
                    "adx": round(adx, 1)
                })
        except Exception as e:
            logging.error(f"Xəta {name} paritetində: {e}")
            continue

    if candidates:
        # Skoru en yüksek olandan en düşüğe doğru sırala
        candidates.sort(key=lambda x: x['score'], reverse=True)
        return candidates

    return []

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 13 parite analiz edilir və doğruluq skoru en yüksek olanlar seçilir...")

    try:
        candidates = analyze_all_valid_pairs()

        if candidates:
            msg = f"🏆 **YÜKSƏK DOĞRULUQLU SİQNAL LİSTƏSİ ({len(candidates)} ƏDƏD)** 🏆\n\n"
            
            for idx, item in enumerate(candidates, 1):
                is_jpy_or_metal = "JPY" in item['name'] or "QIZIL" in item['name'] or "GÜMÜŞ" in item['name']
                entry = f"{item['entry']:.2f}" if is_jpy_or_metal else f"{item['entry']:.5f}"
                sl = f"{item['sl']:.2f}" if is_jpy_or_metal else f"{item['sl']:.5f}"
                tp = f"{item['tp']:.2f}" if is_jpy_or_metal else f"{item['tp']:.5f}"
                
                msg += (
                    f"#{idx} 📌 **Parite:** {item['name']} (Skor: {item['score']})\n"
                    f"📊 **Yön:** {'🟢 ALIŞ (BUY)' if item['direction'] == 'BUY' else '🔴 SATIŞ (SELL)'}\n"
                    f"💪 **Trend Gücü (ADX):** {item['adx']} / 100\n"
                    f"🔹 **Entry:** `{entry}`\n"
                    f"🛑 **Stop Loss (SL):** `{sl}`\n"
                    f"🎯 **Take Profit (TP):** `{tp}`\n"
                    f"-----------------------------------\n"
                )
            
            msg += "🛡️ *Siyahı ən yüksek skorlu və aşağı riskli paritedən başlayaraq sıralanmışdır.*"
        else:
            msg = "🛡️ **RİSK XƏBƏRDARLIĞI:** Hazırda 13 paritenin heç birində təhlükəsiz trend şərti ödənmir. **GÖZLƏMƏK ƏN TƏHLÜKƏSİZİDİR**."

        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Signal xətası: {e}")
        await update.message.reply_text("⚠️ Siqnal hazırlanarkən xəta baş verdi.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot aktivdir! /signal yazaraq ən güclü siqnalları ala bilərsiniz.")

def main():
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise ValueError("TELEGRAM_TOKEN tapılmadı! Zəhmət olmasa mühit dəyişəninə əlavə edin.")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("signal", signal_command))

    app.run_polling()

if __name__ == "__main__":
    main()
