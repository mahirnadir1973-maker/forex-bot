import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot OK")

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

threading.Thread(target=run_web_server, daemon=True).start()
import logging
import pandas as pd
import ta
import requests
import sqlite3
import asyncio
import yfinance as yf
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "8814130355:AAEecRTzl8Yt6j-0hlE7VjBSbQY16t0SFwg"
CHAT_ID = "5857303505"

# Yalnız Peşəkar Forex və Metal Cütlükləri
WATCHED_PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "XAUUSD": "GC=F"
}

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

def init_db():
    conn = sqlite3.connect("forex_signals.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT, pair TEXT, type TEXT, entry REAL, sl REAL, tp REAL, rr TEXT, score INTEGER, reasons TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

class NewsFilter:
    def __init__(self, buffer_minutes=30):
        self.buffer_minutes = buffer_minutes
        self.calendar_url = "https://nsservices.online/api/v1/forex-factory/calendar"
        self.cached_news = []
        self.last_fetch = None

    def fetch_calendar(self):
        now = datetime.utcnow()
        if self.last_fetch and (now - self.last_fetch).total_seconds() < 7200:
            return
        try:
            res = requests.get(self.calendar_url, timeout=10)
            if res.status_code == 200:
                self.cached_news = res.json()
                self.last_fetch = now
        except Exception as e:
            print(f"Xəbər təqvimi xətası: {e}")

    def is_news_blocked(self, pair: str):
        self.fetch_calendar()
        clean_pair = pair.replace("/", "").upper()
        currencies = [clean_pair[:3], clean_pair[3:]]
        now = datetime.utcnow()

        for event in self.cached_news:
            if event.get('impact', '').lower() == 'high' and event.get('country', '').upper() in currencies:
                event_time_str = event.get('date')
                if not event_time_str: continue
                try:
                    event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00")).replace(tzinfo=None)
                except ValueError: continue

                if (event_time - timedelta(minutes=self.buffer_minutes)) <= now <= (event_time + timedelta(minutes=self.buffer_minutes)):
                    return True, f"Yüksək təsirli xəbər: {event.get('title')}"
        return False, ""

news_filter = NewsFilter(buffer_minutes=30)

def fetch_live_data(symbol):
    try:
        ticker = yf.Ticker(symbol)
        df_4h = ticker.history(interval="1h", period="5d")
        df_15m = ticker.history(interval="15m", period="5d")
        if df_4h.empty or df_15m.empty:
            return None, None
        df_4h.columns = [c.lower() for c in df_4h.columns]
        df_15m.columns = [c.lower() for c in df_15m.columns]
        return df_4h, df_15m
    except Exception as e:
        print(f"Data çəkmə xətası ({symbol}): {e}")
        return None, None

def analyze_forex_pair(pair, df_4h, df_15m):
    is_blocked, reason = news_filter.is_news_blocked(pair)
    if is_blocked:
        return None

    df_4h['ema_50'] = ta.trend.ema_indicator(df_4h['close'], window=50)
    df_4h['ema_200'] = ta.trend.ema_indicator(df_4h['close'], window=200)

    df_15m['ema_50'] = ta.trend.ema_indicator(df_15m['close'], window=50)
    df_15m['ema_200'] = ta.trend.ema_indicator(df_15m['close'], window=200)
    df_15m['rsi'] = ta.momentum.rsi(df_15m['close'], window=14)
    df_15m['swing_high'] = df_15m['high'].rolling(window=10).max()
    df_15m['swing_low'] = df_15m['low'].rolling(window=10).min()

    c_4h = df_4h.iloc[-1]
    c_15m = df_15m.iloc[-1]
    prev_15m = df_15m.iloc[-2]

    b_score, b_reasons = 0, []
    if c_4h['close'] > c_4h['ema_50']: b_score += 25; b_reasons.append("4H Trend Yüksələn (EMA 50)")
    if c_4h['ema_50'] > c_4h['ema_200']: b_score += 15; b_reasons.append("4H EMA 50 > 200 Kəsişməsi")
    if c_15m['close'] > prev_15m['swing_low'] and c_15m['low'] <= prev_15m['swing_low'] * 1.001: b_score += 20; b_reasons.append("Dəstək Zonasına Retest")
    if c_15m['rsi'] > 50: b_score += 10; b_reasons.append("RSI Momentum > 50")
    if c_15m['close'] > prev_15m['swing_high']: b_score += 15; b_reasons.append("Müqavimət Qırılması (Breakout)")
    if c_15m['close'] > c_15m['open']: b_score += 15; b_reasons.append("15M Yaşıl Şam Təsdiqi")

    s_score, s_reasons = 0, []
    if c_4h['close'] < c_4h['ema_50']: s_score += 25; s_reasons.append("4H Trend Enən (EMA 50)")
    if c_4h['ema_50'] < c_4h['ema_200']: s_score += 15; s_reasons.append("4H EMA 50 < 200 Kəsişməsi")
    if c_15m['close'] < prev_15m['swing_high'] and c_15m['high'] >= prev_15m['swing_high'] * 0.999: s_score += 20; s_reasons.append("Müqavimət Zonasına Retest")
    if c_15m['rsi'] < 50: s_score += 10; s_reasons.append("RSI Momentum < 50")
    if c_15m['close'] < prev_15m['swing_low']: s_score += 15; s_reasons.append("Dəstək Qırılması (Breakout)")
    if c_15m['close'] < c_15m['open']: s_score += 15; s_reasons.append("15M Qırmızı Şam Təsdiqi")

    signal_type, score, reasons = None, 0, []
    if b_score >= 70 and b_score >= s_score:
        signal_type, score, reasons = "BUY", b_score, b_reasons
    elif s_score >= 70:
        signal_type, score, reasons = "SELL", s_score, s_reasons

    if not signal_type: return None

    entry_price = float(c_15m['close'])
    if signal_type == "BUY":
        sl_price = float(df_15m['low'].tail(5).min())
        tp_price = entry_price + ((entry_price - sl_price) * 2.0)
    else:
        sl_price = float(df_15m['high'].tail(5).max())
        tp_price = entry_price - ((sl_price - entry_price) * 2.0)

    risk = abs(entry_price - sl_price)
    if risk == 0: return None
    rr_ratio = round(abs(tp_price - entry_price) / risk, 2)
    if rr_ratio < 2.0: return None

    return {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "pair": pair, "type": signal_type, "entry": round(entry_price, 5),
        "sl": round(sl_price, 5), "tp": round(tp_price, 5), "rr": f"1:{rr_ratio}",
        "score": score, "timeframe": "15M", "reasons": " + ".join(reasons)
    }

async def auto_scan_job(context: ContextTypes.DEFAULT_TYPE):
    # Həftəsonu təhlil aparmamaq üçün yoxlama (Cümə gecəsindən Bazar gecəsinədək)
    weekday = datetime.utcnow().weekday()
    if weekday in [5, 6]:
        return

    for pair, symbol in WATCHED_PAIRS.items():
        df_4h, df_15m = fetch_live_data(symbol)
        if df_4h is None or df_15m is None: continue

        sig = analyze_forex_pair(pair, df_4h, df_15m)
        if sig:
            icon = "🟢" if sig['type'] == "BUY" else "🔴"
            res = (
                f"🚨 **CANLI FOREX SIQNALI** 🚨\n\n"
                f"{icon} **{sig['type']} SIGNAL**\n"
                f"**Cütlük:** {sig['pair']}\n"
                f"**Giriş (Entry):** {sig['entry']}\n"
                f"**Stop Loss (SL):** {sig['sl']}\n"
                f"**Take Profit (TP):** {sig['tp']}\n"
                f"**Risk/Mükafat:** {sig['rr']}\n"
                f"**Skor:** {sig['score']}/100\n"
                f"**Səbəblər:** {sig['reasons']}"
            )
            await context.bot.send_message(chat_id=CHAT_ID, text=res, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Peşəkar Forex Skaner Aktivdir!\nForex və Qızıl bazarını izləyir və 70+ bal toplayan fürsət yaranan kimi siqnal göndərəcək.", parse_mode="Markdown")

async def signal_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = context.args[0].upper() if context.args else "EURUSD"
    symbol = WATCHED_PAIRS.get(pair, f"{pair}=X")
    
    weekday = datetime.utcnow().weekday()
    if weekday in [5, 6]:
        await update.message.reply_text(f"⚠️ **Forex Bazarı Bağlıdır!**\nHəftəsonu olduğu üçün {pair} qiymətləri donub. Bazar ertəsi canlı axın başlayacaq.")
        return

    await update.message.reply_text(f"🔍 {pair} üçün canlı Forex verilənləri analayiz edilir...")

    df_4h, df_15m = fetch_live_data(symbol)
    if df_4h is None or df_15m is None:
        await update.message.reply_text(f"❌ {pair} üçün canlı qiymət verilənləri alına bilmədi.")
        return

    sig = analyze_forex_pair(pair, df_4h, df_15m)

    if sig:
        icon = "🟢" if sig['type'] == "BUY" else "🔴"
        res = (
            f"{icon} **{sig['type']} SIGNAL (CANLI)**\n\n"
            f"**Cütlük:** {sig['pair']}\n"
            f"**Giriş:** {sig['entry']}\n"
            f"**SL:** {sig['sl']}\n"
            f"**TP:** {sig['tp']}\n"
            f"**R:R:** {sig['rr']}\n"
            f"**Skor:** {sig['score']}/100\n"
            f"**Timeframe:** {sig['timeframe']}\n"
            f"**Səbəb:** {sig['reasons']}"
        )
        await update.message.reply_text(res, parse_mode="Markdown")
    else:
        last_price = round(float(df_15m['close'].iloc[-1]), 5)
        await update.message.reply_text(f"⚪️ {pair} (Cari qiymət: {last_price}) üçün hazırda 70+ bal toplayan təhlükəsiz siqnal yoxdur.")

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("signal", signal_command))

    job_queue = app.job_queue
    job_queue.run_repeating(auto_scan_job, interval=900, first=10)

    print("Peşəkar Forex Skaner İşə düşdü...")
    app.run_polling()
