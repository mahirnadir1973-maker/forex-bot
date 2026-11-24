#!/usr/bin/env python3
"""
6 AGENTLİ TİCARƏT SİMULYASİYASI  --  YALNIZ BACKTEST / KAĞIZ. Broker, real pul, real əməliyyat YOXDUR.

Axın (hər gün bazar bağlananda):
  Scanner  -> həcm partlayışı olan namizədləri tapır
  Messenger-> həmin alətin xəbər əhvalını verir (xəbər faylı yoxdursa "məlumat yoxdur")
  EntryExit-> giriş / stop / hədəf təklif edir
  Skeptic  -> ideyaya etiraz edir (zəif cəhətləri axtarır, veto edə bilər)
  Risk     -> lot ölçüsü + portfel limitləri (veto edə bilər)
  Decision -> AL / BEKLE / KEC

İcra: qərar günün sonunda verilir, order NÖVBƏTİ günün açılışında doldurulur (gələcəyə baxış yoxdur).
Xərclər: komissiya + slippage hər tərəfdən.

İstifadə:
  python agents_sim.py backtest --data synthetic            # internetsiz sınaq (saxta məlumat)
  python agents_sim.py backtest --data yfinance --years 10  # real məlumat (Yahoo)
  python agents_sim.py today --data yfinance                # bu günün qərarları (kağız)
  Əlavə: --news news.csv (date,symbol,sentiment[,headline])   --llm (yalnız 'today' rejimində)
"""
import argparse, math, os, sys
from dataclasses import dataclass, field
import numpy as np
import pandas as pd

GROUPS = {"SPY": "INDEKS", "QQQ": "INDEKS", "IWM": "INDEKS", "DIA": "INDEKS", "GLD": "METAL", "SLV": "METAL",
          "TLT": "TAHVIL", "IEF": "TAHVIL", "XLE": "ENERJI", "XOM": "ENERJI", "XLF": "MALIYYE", "JPM": "MALIYYE",
          "XLK": "TEXNO", "AAPL": "TEXNO", "MSFT": "TEXNO", "NVDA": "TEXNO", "GOOGL": "TEXNO",
          "XLV": "SAGLAMLIQ", "UNH": "SAGLAMLIQ", "XLY": "ISTEHLAK", "AMZN": "ISTEHLAK"}
TICKERS = list(GROUPS)
WARMUP = 210

@dataclass
class Cfg:
    capital: float = 100_000.0
    risk_pct: float = 0.5          # hər əməliyyatda hesabın %-i
    max_positions: int = 5
    max_per_group: int = 2
    max_heat_pct: float = 2.5      # bütün açıq risklərin cəmi (hesabın %-i)
    max_pos_value_pct: float = 25  # bir mövqe hesabın ən çox bu %-i
    kill_dd_pct: float = 12        # hesab zirvədən bu qədər düşsə yeni giriş dayanır
    min_rr: float = 1.5
    target_atr: float = 3.0
    max_hold: int = 20
    comm_bps: float = 2.0          # hər tərəfdən
    slip_bps: float = 5.0          # hər tərəfdən
    rvol_min: float = 1.5
    scan_top: int = 5
    skeptic_veto: float = 4.0
    buy_score: float = 55.0
    wait_score: float = 40.0

# ============================ MƏLUMAT ============================
def prep(df):
    df = df.copy()
    pc = df.close.shift()
    tr = pd.concat([df.high - df.low, (df.high - pc).abs(), (df.low - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    for n in (20, 50, 200):
        df[f"ema{n}"] = df.close.ewm(span=n, adjust=False).mean()
    d = df.close.diff()
    up, dn = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    df["rvol"] = df.volume / df.volume.rolling(20).mean().shift(1)
    df["hh20"] = df.high.rolling(20).max().shift(1)
    df["dvol"] = (df.close * df.volume).rolling(20).mean()
    df["low5"] = df.low.rolling(5).min()
    return df

def load_yf(tickers, years):
    import yfinance as yf
    out = {}
    for t in tickers:
        try:
            df = yf.download(t, period=f"{years}y", interval="1d", auto_adjust=True, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
            df.index = pd.DatetimeIndex(df.index).tz_localize(None)
            out[t] = df[df.volume > 0]
        except Exception as e:
            print(f"{t}: məlumat xətası: {e}")
    return out

def load_synth(tickers, years, seed=11):
    """Saxta məlumat (heç bir üstünlük yoxdur). Yalnız kodu internetsiz sınamaq üçün."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-10-23", periods=int(years * 252))
    out = {}
    for t in tickers:
        r = rng.normal(0.0003, 0.012, len(idx))
        c = 100 * np.exp(np.cumsum(r))
        o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, len(idx)))
        h, l = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.004, len(idx)))), np.minimum(o, c) * (1 - abs(rng.normal(0, 0.004, len(idx))))
        v = 1e6 * (1 + 4 * abs(r) / 0.012 * rng.random(len(idx))) * rng.lognormal(0, 0.3, len(idx))
        out[t] = pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=v), index=idx)
    return out

def load_news(path):
    if not path:
        return None
    n = pd.read_csv(path, parse_dates=["date"])
    return n.sort_values("date")

# ============================ AGENTLƏR ============================
class Scanner:
    """Agent 1: həcm taraması. Yüksək nisbi həcm + 20 günlük zirvəni keçən namizədlər."""
    def __init__(self, cfg): self.cfg = cfg
    def run(self, rows_now):
        out = []
        for s, r in rows_now.items():
            if r is None or r.atr != r.atr or r.rvol != r.rvol or r.hh20 != r.hh20 or r.ema200 != r.ema200:
                continue
            if r.rvol >= self.cfg.rvol_min and r.close > r.hh20 and r.close > r.ema50:
                trend = 30 if r.ema20 > r.ema50 > r.ema200 else (15 if r.close > r.ema200 else 0)
                score = 40 * min(r.rvol, 4) / 4 + 30 * min((r.close - r.hh20) / r.atr, 1.5) / 1.5 + trend
                out.append(dict(sym=s, score=float(score), rvol=float(r.rvol)))
        return sorted(out, key=lambda x: -x["score"])[: self.cfg.scan_top]

class Messenger:
    """Agent 2: xəbər əhvalı (-1..+1). Xəbər faylı yoxdursa None qaytarır (uydurmur)."""
    def __init__(self, news, llm=None): self.news, self.llm = news, llm
    def run(self, sym, date):
        if self.news is None:
            return dict(sentiment=None, n=0, note="xəbər məlumatı yoxdur")
        n = self.news[(self.news.symbol == sym) & (self.news.date <= date) & (self.news.date > date - pd.Timedelta(days=3))]
        if n.empty:
            return dict(sentiment=None, n=0, note="son 3 gündə xəbər yoxdur")
        if "sentiment" in n and n.sentiment.notna().any():
            s = float(n.sentiment.mean())
        elif self.llm and "headline" in n:
            s = float(np.mean([self.llm.sentiment(h) for h in n.headline]))
        else:
            return dict(sentiment=None, n=len(n), note="əhval xanası boşdur")
        return dict(sentiment=s, n=len(n), note=f"{len(n)} xəbər")

class EntryExit:
    """Agent 3: giriş / stop / hədəf. Stop = son 5 günün dibi (1.2-3 ATR aralığında), hədəf = 3 ATR."""
    def __init__(self, cfg): self.cfg = cfg
    def run(self, r):
        entry = float(r.close)
        dist = float(np.clip(entry - (r.low5 - 0.1 * r.atr), 1.2 * r.atr, 3.0 * r.atr))
        target = entry + self.cfg.target_atr * float(r.atr)
        return dict(entry=entry, stop=entry - dist, target=target, dist=dist, rr=(target - entry) / dist,
                    ext=float((r.close - r.ema20) / r.atr))

class Skeptic:
    """Agent 4: ideyanı şübhə altına alır. Hər etiraz bal əlavə edir; limit aşılsa veto."""
    def __init__(self, cfg): self.cfg = cfg
    def run(self, sym, r, plan, news, market_ok, last_loss, date):
        pts, notes = 0.0, []
        def add(p, t): nonlocal pts; pts += p; notes.append(t)
        if r.close < r.ema200: add(2, "uzunmüddətli trend aşağıdır")
        if plan["ext"] > 2.5: add(2, f"qiymət EMA20-dən {plan['ext']:.1f} ATR uzaqdır (geri çəkilmə riski)")
        if r.rsi > 75: add(1, f"RSI çox yüksək ({r.rsi:.0f})")
        if plan["rr"] < self.cfg.min_rr: add(3, f"risk/mükafat zəifdir ({plan['rr']:.1f})")
        if r.atr / r.close > 0.05: add(1, "volatillik çox yüksəkdir")
        if r.dvol < 20e6 and sym not in ("SPY", "QQQ"): add(1, "gündəlik dollar həcmi aşağıdır")
        if not market_ok and sym != "SPY": add(1, "ümumi bazar (SPY) EMA200 altındadır")
        if sym in last_loss and (date - last_loss[sym]).days <= 10: add(1, "bu alətdə son 10 gündə zərər olub")
        if news["sentiment"] is not None and news["sentiment"] < -0.3: add(2, "xəbər əhvalı mənfidir")
        return dict(score=pts, veto=pts >= self.cfg.skeptic_veto, notes=notes)

class Risk:
    """Agent 5: lot ölçüsü və portfel limitləri."""
    def __init__(self, cfg): self.cfg = cfg
    def run(self, sym, plan, port):
        c, notes = self.cfg, []
        eq = port.equity
        if (1 - eq / port.peak) * 100 >= c.kill_dd_pct: return dict(ok=False, shares=0, notes=["kill-switch: enmə limiti aşılıb"])
        if len(port.pos) + len(port.pending) >= c.max_positions: return dict(ok=False, shares=0, notes=["maksimum mövqe sayı dolub"])
        g = GROUPS[sym]
        if sum(GROUPS[p.sym] == g for p in port.pos + port.pending) >= c.max_per_group: return dict(ok=False, shares=0, notes=[f"{g} qrupunda limit dolub"])
        if plan["rr"] < c.min_rr: return dict(ok=False, shares=0, notes=["risk/mükafat limitin altındadır"])
        risk_usd = eq * c.risk_pct / 100
        heat = sum(p.risk_usd for p in port.pos + port.pending) + risk_usd
        if heat / eq * 100 > c.max_heat_pct: return dict(ok=False, shares=0, notes=["portfelin ümumi riski limiti aşır"])
        shares = math.floor(min(risk_usd / plan["dist"], eq * c.max_pos_value_pct / 100 / plan["entry"], port.cash * 0.98 / plan["entry"]))
        if shares < 1: return dict(ok=False, shares=0, notes=["lot ölçüsü 1-dən kiçikdir"])
        return dict(ok=True, shares=shares, risk_usd=shares * plan["dist"], notes=[f"{shares} səhm, risk ${shares * plan['dist']:.0f}"])

class Decision:
    """Agent 6: yekun qərar. Veto varsa KEC; yoxsa bal toplayıb AL / BEKLE / KEC."""
    def __init__(self, cfg): self.cfg = cfg
    def run(self, cand, news, skeptic, risk):
        comp = cand["score"] + (15 * news["sentiment"] if news["sentiment"] is not None else 0) - 6 * skeptic["score"]
        if not risk["ok"]: return "KEC", comp, "Risk: " + "; ".join(risk["notes"])
        if skeptic["veto"]: return "KEC", comp, "Skeptik veto: " + "; ".join(skeptic["notes"])
        if comp >= self.cfg.buy_score: return "AL", comp, "bal kifayətdir"
        if comp >= self.cfg.wait_score: return "BEKLE", comp, "bal orta, növbəti gün yenidən yoxlanacaq"
        return "KEC", comp, "bal aşağıdır"

# ============================ PORTFEL / SİSTEM ============================
@dataclass
class Pos:
    sym: str; shares: int; fill: float; stop: float; target: float; dist: float; risk_usd: float
    entry_date: object; cost: float; bars: int = 0; last: float = 0.0

@dataclass
class Order:
    sym: str; shares: int; stop: float; target: float; dist: float; risk_usd: float

@dataclass
class Port:
    cash: float; peak: float; pos: list = field(default_factory=list); pending: list = field(default_factory=list)
    last_loss: dict = field(default_factory=dict)
    @property
    def equity(self): return self.cash + sum(p.shares * p.last for p in self.pos)

class System:
    def __init__(self, cfg, news=None, llm=None, mode="full", seed=0):
        self.cfg, self.mode, self.rng, self.llm = cfg, mode, np.random.default_rng(seed), llm
        self.scanner, self.messenger, self.ee = Scanner(cfg), Messenger(news, llm), EntryExit(cfg)
        self.skeptic, self.risk, self.decision = Skeptic(cfg), Risk(cfg), Decision(cfg)

    def think(self, date, rows_now, port, market_ok, log):
        held = {p.sym for p in port.pos} | {o.sym for o in port.pending}
        if self.mode == "random":   # kontrol: təsadüfi giriş (eyni stop/hədəf/risk qaydaları)
            valid = [s for s, r in rows_now.items() if r is not None and r.atr == r.atr and r.ema200 == r.ema200 and s not in held]
            cands = [dict(sym=self.rng.choice(valid), score=60.0, rvol=0.0)] if valid and self.rng.random() < 0.35 else []
        else:
            cands = [c for c in self.scanner.run(rows_now) if c["sym"] not in held]
        for c in cands:
            s, r = c["sym"], rows_now[c["sym"]]
            news = self.messenger.run(s, date)
            plan = self.ee.run(r)
            sk = self.skeptic.run(s, r, plan, news, market_ok, port.last_loss, date)
            if self.mode in ("no_skeptic", "random"):
                sk = dict(score=0.0, veto=False, notes=[])
            rk = self.risk.run(s, plan, port)
            act, comp, why = ("AL", c["score"], "təsadüfi kontrol") if (self.mode == "random" and rk["ok"]) else self.decision.run(c, news, sk, rk)
            log.append(dict(date=date, sym=s, action=act, score=round(comp, 1), rvol=round(c["rvol"], 2), news=news["sentiment"],
                            skeptic=sk["score"], reason=why, entry=round(plan["entry"], 2), stop=round(plan["stop"], 2), target=round(plan["target"], 2)))
            if act == "AL":
                port.pending.append(Order(s, rk["shares"], plan["stop"], plan["target"], plan["dist"], rk["risk_usd"]))

# ============================ BACKTEST ============================
def run_backtest(data, cfg, news=None, mode="full", seed=0):
    names = list(data)
    dates = sorted(set().union(*[d.index for d in data.values()]))
    rows = {s: list(data[s].reindex(dates).itertuples(index=False)) for s in names}
    ok_row = lambda r: r.close == r.close
    port, sysm, trades, log, eq = Port(cfg.capital, cfg.capital), System(cfg, news, None, mode, seed), [], [], []
    slip, comm = cfg.slip_bps / 1e4, cfg.comm_bps / 1e4
    for i, d in enumerate(dates):
        # 1) əvvəlki günün orderləri bu günün açılışında dolur
        for o in port.pending:
            r = rows[o.sym][i]
            if not ok_row(r): continue
            fill = r.open * (1 + slip)
            n = min(o.shares, math.floor(port.cash / (fill * (1 + comm))))
            if n < 1: continue
            cost = n * fill * (1 + comm)
            port.cash -= cost
            port.pos.append(Pos(o.sym, n, fill, o.stop, o.target, o.dist, o.risk_usd * n / o.shares, d, cost, last=fill))
        port.pending = []
        # 2) açıq mövqelər (stop əvvəl yoxlanır: mühafizəkar)
        for p in list(port.pos):
            r = rows[p.sym][i]
            if not ok_row(r): continue
            p.bars += 1; p.last = r.close
            ex = None
            if r.open <= p.stop: ex = (r.open, "stop-gap")
            elif r.low <= p.stop: ex = (p.stop, "stop")
            elif r.open >= p.target: ex = (r.open, "hədəf-gap")
            elif r.high >= p.target: ex = (p.target, "hədəf")
            elif p.bars >= cfg.max_hold: ex = (r.close, "vaxt")
            if ex:
                px = ex[0] * (1 - slip)
                proceeds = p.shares * px * (1 - comm)
                port.cash += proceeds; port.pos.remove(p)
                pnl = proceeds - p.cost
                trades.append(dict(sym=p.sym, entry=p.entry_date, exit=d, shares=p.shares, pnl=pnl, R=(px - p.fill) / p.dist, why=ex[1], bars=p.bars))
                if pnl < 0: port.last_loss[p.sym] = d
        port.peak = max(port.peak, port.equity)
        eq.append(port.equity)
        # 3) gün sonu: agentlər düşünür, order növbəti gün üçün
        if i >= WARMUP and i < len(dates) - 1:
            now = {s: (rows[s][i] if ok_row(rows[s][i]) else None) for s in names}
            spy = now.get("SPY")
            sysm.think(d, now, port, bool(spy and spy.close > spy.ema200), log)
    return pd.Series(eq, index=dates), trades, log

def stats(eq, trades):
    r = eq.pct_change().dropna()
    yrs = max(len(r) / 252, 1e-9)
    t = pd.DataFrame(trades)
    s = dict(cagr=(eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1, sharpe=(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else 0.0,
             maxdd=(eq / eq.cummax() - 1).min(), n=len(t))
    if len(t):
        gl = -t.pnl[t.pnl < 0].sum()
        s.update(win=(t.pnl > 0).mean(), avgR=t.R.mean(), pf=(t.pnl[t.pnl > 0].sum() / gl) if gl > 0 else float("inf"), hold=t.bars.mean())
    return s

def fmt(name, s):
    if not s.get("n"): return f"{name:<22} əməliyyat yoxdur"
    return (f"{name:<22} {s['n']:>4} əməl | qazanma {s['win'] * 100:>3.0f}% | orta {s['avgR']:+.2f}R | PF {s['pf']:.2f} | "
            f"illik {s['cagr'] * 100:+.1f}% | Sharpe {s['sharpe']:+.2f} | maks. enmə {s['maxdd'] * 100:.0f}%")

def cmd_backtest(a):
    data = load_synth(TICKERS, a.years) if a.data == "synthetic" else load_yf(TICKERS, a.years)
    if "SPY" not in data: sys.exit("SPY məlumatı lazımdır (bazar filtri).")
    data = {s: prep(d) for s, d in data.items()}
    cfg, news = Cfg(), load_news(a.news)
    if a.data == "synthetic": print("DİQQƏT: saxta məlumat, heç bir üstünlük yoxdur. Yalnız kodun işlədiyini göstərir.\n")
    res = {}
    eq, tr, log = run_backtest(data, cfg, news, "full")
    res["Tam sistem (6 agent)"] = (eq, tr)
    res["Skeptiksiz"] = run_backtest(data, cfg, news, "no_skeptic")[:2]
    rnd = [run_backtest(data, cfg, news, "random", seed=k)[:2] for k in range(8)]
    pd.DataFrame(tr).to_csv("trades.csv", index=False); pd.DataFrame(log).to_csv("decisions.csv", index=False)
    start = data["SPY"].index[WARMUP]
    bh = data["SPY"].close.loc[start:]; bh = bh / bh.iloc[0] * cfg.capital
    print(f"{'':<22} (dövr: {start.date()} -> {eq.index[-1].date()}, kapital ${cfg.capital:,.0f}, risk {cfg.risk_pct}%/əməl)\n")
    for k, (e, t) in res.items():
        print(fmt(k, stats(e.loc[start:], t)))
    rs = [stats(e.loc[start:], t) for e, t in rnd]
    print(f"{'Təsadüfi giriş (orta 8)':<22} {np.mean([x['n'] for x in rs]):>4.0f} əməl | orta {np.nanmean([x.get('avgR', np.nan) for x in rs]):+.2f}R | "
          f"illik {np.mean([x['cagr'] for x in rs]) * 100:+.1f}% | Sharpe {np.mean([x['sharpe'] for x in rs]):+.2f}")
    sb = stats(bh, [])
    print(f"{'SPY al-saxla':<22} illik {sb['cagr'] * 100:+.1f}% | Sharpe {sb['sharpe']:+.2f} | maks. enmə {sb['maxdd'] * 100:.0f}%")
    mid = eq.index[WARMUP + (len(eq) - WARMUP) // 2]
    print("\nTam sistem, iki yarı:")
    t = pd.DataFrame(tr)
    for nm, a_, b_ in (("1-ci yarı", start, mid), ("2-ci yarı", mid, eq.index[-1])):
        sub = [x for x in tr if a_ <= x["exit"] <= b_]
        print(fmt(nm, stats(eq.loc[a_:b_], sub)))
    print("\nFayllar: trades.csv (əməliyyatlar), decisions.csv (hər agentin gündəlik qərarı və səbəbi)")

def cmd_today(a):
    data = load_synth(TICKERS, 3) if a.data == "synthetic" else load_yf(TICKERS, 3)
    data = {s: prep(d) for s, d in data.items()}
    cfg, news = Cfg(), load_news(a.news)
    llm = LLM() if a.llm else None
    names = list(data); last = max(d.index[-1] for d in data.values())
    now = {s: (next(data[s].loc[[last]].itertuples(index=False)) if last in data[s].index else None) for s in names}
    spy = now.get("SPY")
    port, log = Port(cfg.capital, cfg.capital), []
    sysm = System(cfg, news, llm, "full")
    sysm.think(last, now, port, bool(spy and spy.close > spy.ema200), log)
    print(f"Bu günün (kağız) qərarları, tarix {last.date()}:\n")
    if not log: print("Namizəd yoxdur (həcm partlayışı + 20 günlük zirvə keçidi tapılmadı).")
    for x in log:
        print(f"{x['sym']:<6} {x['action']:<6} bal {x['score']:>5} | giriş {x['entry']} stop {x['stop']} hədəf {x['target']} | {x['reason']}")
        if llm and x["action"] != "KEC":
            print("   LLM tənqidi:", llm.critique(x))

# ============================ İSTƏYƏ BAĞLI LLM (yalnız 'today' rejimi) ============================
class LLM:
    """Claude API ilə xəbər əhvalı və tənqid. Backtestdə İSTİFADƏ OLUNMUR: model keçmişi 'bildiyi' üçün nəticəni şişirdir."""
    def __init__(self):
        self.key, self.model = os.getenv("ANTHROPIC_API_KEY", ""), os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
    def _ask(self, prompt, max_tokens=300):
        import requests
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=60,
                          headers={"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                          json={"model": self.model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]})
        return r.json()["content"][0]["text"].strip()
    def sentiment(self, headline):
        try: return max(-1.0, min(1.0, float(self._ask(f"Bu xəbərin səhm üçün əhvalını -1 ilə +1 arası YALNIZ bir rəqəmlə yaz:\n{headline}", 10))))
        except Exception: return 0.0
    def critique(self, x):
        try: return self._ask(f"Skeptik treyder kimi bu ideyanı 2 cümlə ilə tənqid et (Azərbaycan dilində): {x}")
        except Exception as e: return f"(LLM əlçatmazdır: {e})"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backtest", "today"])
    ap.add_argument("--data", choices=["synthetic", "yfinance"], default="yfinance")
    ap.add_argument("--years", type=int, default=8)
    ap.add_argument("--news", default="")
    ap.add_argument("--llm", action="store_true")
    a = ap.parse_args()
    cmd_backtest(a) if a.cmd == "backtest" else cmd_today(a)
