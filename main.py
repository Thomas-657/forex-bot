"""
Le Collectif Trading — Robot de signaux v4
Stratégie : EMA 9/21 + RSI + MACD + CVD
Timeframes : M15 (biais) → M5 (confirmation + entrée)
Sessions : Asie + Londres + New York (24h/5j)
Filtre news : pause avant/après annonces majeures
Paper trading : journal CSV + rapport quotidien à 21h UTC

Dépendances : requests, pandas, numpy — ZERO pandas-ta
"""

import os
import csv
import time
import logging
import requests
import numpy  as np
import pandas as pd
from datetime import datetime, timezone
from pathlib  import Path

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "TON_TOKEN_ICI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TON_CHAT_ID_ICI")
TWELVE_DATA_KEY  = os.getenv("TWELVE_DATA_KEY",  "TA_CLE_ICI")

SYMBOLS = ["XAU/USD", "BTC/USD"]

EMA_FAST   = 9
EMA_SLOW   = 21
RSI_PERIOD = 14
MACD_FAST  = 12
MACD_SLOW  = 26
MACD_SIG   = 9
CVD_WINDOW = 20

RSI_LONG_MIN  = 35
RSI_LONG_MAX  = 65
RSI_SHORT_MIN = 35
RSI_SHORT_MAX = 65

SCORE_MIN = 7

NEWS_BUFFER_BEFORE = 30   # minutes avant annonce
NEWS_BUFFER_AFTER  = 30   # minutes après annonce

SCAN_INTERVAL        = 60  # secondes
DAILY_REPORT_HOUR    = 21
DAILY_REPORT_MINUTE  = 0

JOURNAL_FILE = Path("journal_trades.csv")
JOURNAL_COLS = [
    "id", "date", "heure_utc", "symbol", "direction", "score",
    "prix_entree", "sl", "tp", "session", "rsi", "macd_hist", "cvd",
    "resultat", "prix_sortie", "pnl_r", "notes",
]

# ─── SESSIONS ────────────────────────────────────────────────────────────────

SESSIONS = {
    "Asie":     (0,  9),
    "Londres":  (7,  16),
    "New York": (13, 22),
}

SESSION_TIPS = {
    "Asie":                  "Volatilité modérée — XAU/USD et BTC actifs.",
    "Londres":               "Forte liquidité — meilleurs setups. 🔥",
    "New York":              "Pic de volatilité. 🔥",
    "Londres + New York ⚡": "Chevauchement — maximum de liquidité. 🔥🔥",
    "Asie + Londres ⚡":     "Chevauchement — bonne liquidité.",
}

# ─── ANNONCES MAJEURES (heure UTC) ───────────────────────────────────────────

RECURRING_NEWS = {
    0: [],
    1: [(13, 30, "CPI USA"), (13, 30, "PPI USA")],
    2: [(13, 30, "CPI/PPI USA"), (18, 0, "FOMC Minutes"), (19, 0, "FOMC Statement")],
    3: [(12, 45, "BCE Decision"), (13, 30, "Jobless Claims"), (13, 30, "PIB USA")],
    4: [(13, 30, "NFP"), (13, 30, "Unemployment Rate"), (15, 0, "Michigan Sentiment")],
}

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("LCT")

# ─── CALCULS TECHNIQUES (zéro pandas-ta) ─────────────────────────────────────

def calc_ema(series: pd.Series, period: int) -> pd.Series:
    """EMA via ewm — identique à la formule TradingView."""
    return series.ewm(span=period, adjust=False).mean()

def calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder via ewm."""
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs       = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))

def calc_macd(series: pd.Series,
              fast: int = 12, slow: int = 26,
              signal: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Retourne (macd_line, signal_line, histogram)."""
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    line     = ema_fast - ema_slow
    sig      = calc_ema(line, signal)
    hist     = line - sig
    return line, sig, hist

def calc_cvd(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """
    CVD simplifié : volume positif si bougie haussière, négatif sinon.
    Somme glissante sur `window` bougies.
    """
    delta = df.apply(
        lambda r: r["volume"] if r["close"] >= r["open"] else -r["volume"],
        axis=1,
    )
    return delta.rolling(window).sum()

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"]      = calc_ema(df["close"], EMA_FAST)
    df["ema21"]     = calc_ema(df["close"], EMA_SLOW)
    df["rsi"]       = calc_rsi(df["close"], RSI_PERIOD)
    ml, ms, mh      = calc_macd(df["close"], MACD_FAST, MACD_SLOW, MACD_SIG)
    df["macd"]      = ml
    df["macd_sig"]  = ms
    df["macd_hist"] = mh
    df["delta"]     = df.apply(
        lambda r: r["volume"] if r["close"] >= r["open"] else -r["volume"], axis=1
    )
    df["cvd"]       = df["delta"].rolling(CVD_WINDOW).sum()
    return df.dropna().reset_index(drop=True)

# ─── DONNÉES ─────────────────────────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str,
                  outputsize: int = 100) -> pd.DataFrame | None:
    params = {
        "symbol":     symbol,
        "interval":   interval,
        "outputsize": outputsize,
        "apikey":     TWELVE_DATA_KEY,
        "order":      "ASC",
    }
    try:
        r    = requests.get("https://api.twelvedata.com/time_series",
                            params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            log.warning(f"Pas de données {symbol} {interval} : {data.get('message','?')}")
            return None
        df = pd.DataFrame(data["values"]).rename(columns={"datetime": "time"})
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["time"] = pd.to_datetime(df["time"])
        return df.dropna().reset_index(drop=True)
    except Exception as e:
        log.error(f"fetch {symbol} {interval} : {e}")
        return None

# ─── SESSIONS ────────────────────────────────────────────────────────────────

def get_active_session(now: datetime) -> str | None:
    wd, h = now.weekday(), now.hour
    if wd == 5 and h >= 22:
        return None
    if wd == 6:
        return None
    active = [n for n, (s, e) in SESSIONS.items() if s <= h < e]
    if not active:
        return None
    if "Londres" in active and "New York" in active:
        return "Londres + New York ⚡"
    if "Asie" in active and "Londres" in active:
        return "Asie + Londres ⚡"
    return active[0]

# ─── NEWS ────────────────────────────────────────────────────────────────────

def is_news_blackout(now: datetime) -> tuple[bool, list]:
    risky = []
    for h, m, name in RECURRING_NEWS.get(now.weekday(), []):
        nt  = now.replace(hour=h, minute=m, second=0, microsecond=0)
        dbf = (nt - now).total_seconds() / 60
        daf = (now - nt).total_seconds() / 60
        if 0 < dbf <= NEWS_BUFFER_BEFORE:
            risky.append({"name": name, "time": nt,
                          "status": f"dans {dbf:.0f} min"})
        elif 0 <= daf < NEWS_BUFFER_AFTER:
            risky.append({"name": name, "time": nt,
                          "status": "vient de passer"})
    return bool(risky), risky

# ─── JOURNAL CSV ─────────────────────────────────────────────────────────────

def init_journal() -> None:
    if not JOURNAL_FILE.exists():
        with open(JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=JOURNAL_COLS).writeheader()
        log.info("Journal créé")

def next_id() -> int:
    if not JOURNAL_FILE.exists():
        return 1
    with open(JOURNAL_FILE, encoding="utf-8") as f:
        return len(list(csv.DictReader(f))) + 1

def log_trade(symbol, direction, score, price, sl, tp,
              session, rsi, macd_hist, cvd, now) -> int:
    tid = next_id()
    row = {
        "id": tid, "date": now.strftime("%Y-%m-%d"),
        "heure_utc": now.strftime("%H:%M"), "symbol": symbol,
        "direction": direction.upper(), "score": score,
        "prix_entree": round(price, 4), "sl": round(sl, 4),
        "tp": round(tp, 4), "session": session,
        "rsi": round(rsi, 1), "macd_hist": round(macd_hist, 5),
        "cvd": round(cvd, 0), "resultat": "EN_COURS",
        "prix_sortie": "", "pnl_r": "", "notes": "",
    }
    with open(JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=JOURNAL_COLS).writerow(row)
    return tid

def load_journal() -> list[dict]:
    if not JOURNAL_FILE.exists():
        return []
    with open(JOURNAL_FILE, encoding="utf-8") as f:
        return list(csv.DictReader(f))

# ─── SL / TP ─────────────────────────────────────────────────────────────────

def compute_sl_tp(df5: pd.DataFrame, direction: str,
                  price: float) -> tuple[float, float]:
    last   = df5.iloc[-1]
    buffer = price * 0.001   # 0.1 % du prix
    if direction == "long":
        sl   = float(last["ema21"]) - buffer
        tp   = price + (price - sl) * 2
    else:
        sl   = float(last["ema9"]) + buffer
        tp   = price - (sl - price) * 2
    return round(sl, 4), round(tp, 4)

# ─── ANALYSE ─────────────────────────────────────────────────────────────────

def analyse_m15(df: pd.DataFrame) -> dict:
    res  = {"biais": "neutre", "score": 0, "details": []}
    l, p = df.iloc[-1], df.iloc[-2]
    up9  = l["ema9"]  > p["ema9"]
    up21 = l["ema21"] > p["ema21"]
    c    = l["close"]

    if l["ema9"] > l["ema21"] and up9 and up21:
        res.update({"biais": "long", "score": 1})
        res["details"].append("EMA 9 > EMA 21 haussières ✅")
        if l["ema21"] <= c <= l["ema9"]:
            res["score"] = 2
            res["details"].append("Prix en zone pullback ✅")
        else:
            res["details"].append("Prix hors zone pullback")

    elif l["ema9"] < l["ema21"] and not up9 and not up21:
        res.update({"biais": "short", "score": 1})
        res["details"].append("EMA 9 < EMA 21 baissières ✅")
        if l["ema9"] <= c <= l["ema21"]:
            res["score"] = 2
            res["details"].append("Prix en zone pullback ✅")
        else:
            res["details"].append("Prix hors zone pullback")
    else:
        res["details"].append("EMA enchevêtrées ❌")
    return res

def analyse_m5(df: pd.DataFrame, biais: str) -> dict:
    res = {"score": 0, "details": []}
    if biais not in ("long", "short"):
        return res

    l, p  = df.iloc[-1], df.iloc[-2]
    rsi   = float(l["rsi"])
    hist  = float(l["macd_hist"])
    phist = float(p["macd_hist"])
    cvd   = float(l["cvd"])
    pcvd  = float(p["cvd"])
    delta = float(l["delta"])

    # RSI
    ok = ((biais == "long"  and (RSI_LONG_MIN  <= rsi <= RSI_LONG_MAX  or rsi < RSI_LONG_MIN)) or
          (biais == "short" and (RSI_SHORT_MIN <= rsi <= RSI_SHORT_MAX or rsi > RSI_SHORT_MAX)))
    if ok:
        res["score"] += 1
        res["details"].append(f"RSI {rsi:.1f} favorable ✅")
    else:
        res["details"].append(f"RSI {rsi:.1f} défavorable ❌")

    # MACD — pas de divergence
    div = ((biais == "long"  and hist < 0 and hist < phist) or
           (biais == "short" and hist > 0 and hist > phist))
    if not div:
        res["score"] += 1
        res["details"].append("Pas de divergence MACD ✅")
    else:
        res["details"].append("Divergence MACD ❌")

    # MACD — déclencheur
    cx_up   = p["macd"] < p["macd_sig"] and l["macd"] > l["macd_sig"]
    cx_down = p["macd"] > p["macd_sig"] and l["macd"] < l["macd_sig"]
    trig = ((biais == "long"  and (cx_up   or (hist > 0 and hist > phist))) or
            (biais == "short" and (cx_down or (hist < 0 and hist < phist))))
    if trig:
        res["score"] += 1
        res["details"].append("MACD déclencheur ✅")
    else:
        res["details"].append("MACD pas de déclencheur ❌")

    # CVD
    cvd_ok = ((biais == "long"  and cvd > 0 and cvd >= pcvd) or
              (biais == "short" and cvd < 0 and cvd <= pcvd))
    if cvd_ok:
        res["score"] += 1
        res["details"].append(f"CVD aligné ({cvd:+.0f}) ✅")
    else:
        res["details"].append(f"CVD non aligné ({cvd:+.0f}) ❌")

    # Delta bougie
    delta_ok = (biais == "long" and delta > 0) or (biais == "short" and delta < 0)
    if delta_ok:
        res["score"] += 1
        res["details"].append(f"Delta bougie aligné ({delta:+.0f}) ✅")
    else:
        res["details"].append(f"Delta bougie non aligné ({delta:+.0f}) ❌")

    return res

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def send_telegram(text: str) -> None:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        log.error(f"Telegram : {e}")

# ─── FORMAT SIGNAL ───────────────────────────────────────────────────────────

def format_signal(tid, symbol, biais, score, m15d, m5d,
                  price, sl, tp, rsi, mh, cvd, session, now) -> str:
    emoji = "📈" if biais == "long" else "📉"
    label = biais.upper()
    bar   = "█" * score + "░" * (7 - score)
    risk  = abs(price - sl)
    return (
        f"🔔 <b>LE COLLECTIF TRADING</b> 🔔\n"
        f"{emoji} <b>[PAPER] {label} — {symbol}</b>  <code>#{tid}</code>\n"
        f"🕐 {now.strftime('%H:%M UTC')}  |  📍 {session}\n\n"
        f"<b>Score : {score}/7</b>  [{bar}]\n\n"
        f"💰 <b>Entrée :</b> {price:.4f}\n"
        f"🔴 <b>SL :</b> {sl:.4f}  (−{risk:.4f})\n"
        f"🎯 <b>TP :</b> {tp:.4f}  (RR 1:2)\n\n"
        f"📊 RSI {rsi:.1f}  |  MACDh {mh:+.5f}  |  CVD {cvd:+.0f}\n\n"
        + "<b>M15 :</b>\n" + "".join(f"  {d}\n" for d in m15d)
        + "\n<b>M5 :</b>\n" + "".join(f"  {d}\n" for d in m5d)
        + f"\n📁 <i>Trade #{tid} enregistré dans le journal.\n"
          "⚠️ PAPER TRADING — aucun argent réel.</i>"
    )

# ─── RAPPORT QUOTIDIEN ───────────────────────────────────────────────────────

def build_daily_report(now: datetime) -> str:
    today      = now.strftime("%Y-%m-%d")
    all_trades = load_journal()
    t_today    = [t for t in all_trades if t["date"] == today]
    closed     = [t for t in t_today   if t["resultat"] in ("WIN", "LOSS")]
    wins       = [t for t in closed    if t["resultat"] == "WIN"]
    open_t     = [t for t in t_today   if t["resultat"] == "EN_COURS"]

    day_pnl = sum(float(t["pnl_r"]) for t in closed if t["pnl_r"])
    wr_day  = len(wins) / len(closed) * 100 if closed else 0

    all_closed  = [t for t in all_trades if t["resultat"] in ("WIN","LOSS")]
    total_wins  = sum(1 for t in all_closed if t["resultat"] == "WIN")
    wr_all      = total_wins / len(all_closed) * 100 if all_closed else 0
    total_pnl   = sum(float(t["pnl_r"]) for t in all_closed if t["pnl_r"])

    perf = "🟢" if day_pnl > 0 else ("🔴" if day_pnl < 0 else "⚪")

    # Stats par symbol
    by_sym: dict = {}
    for t in t_today:
        s = t["symbol"]
        by_sym.setdefault(s, {"w":0,"l":0,"total":0})
        by_sym[s]["total"] += 1
        if t["resultat"] == "WIN":   by_sym[s]["w"] += 1
        if t["resultat"] == "LOSS":  by_sym[s]["l"] += 1

    # Stats par session
    by_sess: dict = {}
    for t in t_today:
        s = t.get("session","?")
        by_sess.setdefault(s, {"w":0,"l":0,"total":0})
        by_sess[s]["total"] += 1
        if t["resultat"] == "WIN":   by_sess[s]["w"] += 1
        if t["resultat"] == "LOSS":  by_sess[s]["l"] += 1

    msg = (
        f"📊 <b>RAPPORT QUOTIDIEN — {now.strftime('%d/%m/%Y')}</b>\n"
        f"{'─'*30}\n\n"
        f"<b>Signaux du jour : {len(t_today)}</b>\n"
        f"  ✅ Clôturés : {len(closed)}  |  ⏳ En cours : {len(open_t)}\n\n"
        f"<b>{perf} Résultats du jour</b>\n"
        f"  🟢 Wins : {len(wins)}  |  🔴 Losses : {len(closed)-len(wins)}\n"
        f"  📈 Win rate : {wr_day:.0f}%\n"
        f"  💰 PnL jour : {day_pnl:+.1f}R\n\n"
    )

    if by_sym:
        msg += "<b>Par instrument</b>\n"
        for sym, s in by_sym.items():
            wr = s["w"]/(s["w"]+s["l"])*100 if s["w"]+s["l"] else 0
            msg += f"  {sym} : {s['total']} — {s['w']}W/{s['l']}L ({wr:.0f}%)\n"
        msg += "\n"

    if by_sess:
        msg += "<b>Par session</b>\n"
        for sess, s in by_sess.items():
            msg += f"  {sess} : {s['total']} — {s['w']}W/{s['l']}L\n"
        msg += "\n"

    msg += (
        f"{'─'*30}\n"
        f"<b>📈 Stats globales (paper)</b>\n"
        f"  Trades clôturés : {len(all_closed)}\n"
        f"  Win rate : {wr_all:.0f}%\n"
        f"  PnL total : {total_pnl:+.1f}R\n\n"
    )

    if len(all_closed) >= 20:
        if wr_all >= 55 and total_pnl > 0:
            msg += "💡 <i>Stratégie solide — envisager le passage en live.</i>\n"
        elif wr_all >= 45 and total_pnl > 0:
            msg += "💡 <i>Résultats corrects — continuer le paper trading.</i>\n"
        else:
            msg += "⚠️ <i>Win rate faible — analyser les trades perdants avant le live.</i>\n"
    else:
        msg += f"⏳ <i>Encore {20-len(all_closed)} trade(s) pour un premier bilan fiable.</i>\n"

    if t_today:
        msg += f"\n<b>Derniers signaux du jour</b>\n"
        for t in t_today[-5:]:
            e = "✅" if t["resultat"]=="WIN" else ("❌" if t["resultat"]=="LOSS" else "⏳")
            pnl = f" {float(t['pnl_r']):+.1f}R" if t["pnl_r"] else ""
            msg += f"  {e} #{t['id']} {t['symbol']} {t['direction']} @ {t['prix_entree']}{pnl}\n"

    msg += "\n📁 <i>journal_trades.csv — remplis resultat / prix_sortie / pnl_r</i>"
    return msg

# ─── SCAN SYMBOL ─────────────────────────────────────────────────────────────

last_signal: dict[str, str] = {}

def scan_symbol(symbol: str, session: str, now: datetime) -> None:
    log.info(f"Scan {symbol}")

    df15 = fetch_candles(symbol, "15min", 80)
    if df15 is None or len(df15) < 30:
        return
    df15  = add_indicators(df15)
    m15   = analyse_m15(df15)
    biais = m15["biais"]
    if biais == "neutre":
        last_signal.pop(symbol, None)
        return

    df5 = fetch_candles(symbol, "5min", 80)
    if df5 is None or len(df5) < 30:
        return
    df5   = add_indicators(df5)
    m5    = analyse_m5(df5, biais)
    score = m15["score"] + m5["score"]
    last5 = df5.iloc[-1]
    price = float(last5["close"])

    log.info(f"{symbol} {biais.upper()} {score}/7 RSI={last5['rsi']:.1f}")

    if score >= SCORE_MIN:
        sig_key = f"{symbol}_{biais}"
        if last_signal.get(symbol) == sig_key:
            log.info(f"{symbol} doublon — skip")
            return

        sl, tp = compute_sl_tp(df5, biais, price)
        tid    = log_trade(
            symbol, biais, score, price, sl, tp, session,
            float(last5["rsi"]), float(last5["macd_hist"]),
            float(last5["cvd"]), now,
        )
        msg = format_signal(
            tid, symbol, biais, score,
            m15["details"], m5["details"],
            price, sl, tp,
            float(last5["rsi"]), float(last5["macd_hist"]),
            float(last5["cvd"]), session, now,
        )
        send_telegram(msg)
        last_signal[symbol] = sig_key
        log.info(f"{symbol} signal #{tid} envoyé ✅")
    else:
        last_signal.pop(symbol, None)

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main() -> None:
    init_journal()

    last_session:       str | None = None
    news_notified:      bool       = False
    report_sent_today:  str        = ""

    log.info("=" * 52)
    log.info("  LE COLLECTIF TRADING — v4 (zéro pandas-ta)")
    log.info("  EMA/RSI/MACD calculés nativement")
    log.info("=" * 52)

    send_telegram(
        "🤖 <b>Le Collectif Trading — Robot v4 démarré</b>\n\n"
        "📊 EMA 9/21 + RSI + MACD + CVD\n"
        "🌍 Asie · Londres · New York\n"
        "🚨 Filtre news actif\n"
        "📁 Journal paper trading actif\n"
        "📊 Rapport quotidien : 21h00 UTC\n"
        "⭐ Score min : 7/7\n"
        "✅ Calculs 100% natifs — stable"
    )

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Rapport quotidien
            today = now.strftime("%Y-%m-%d")
            if (now.hour == DAILY_REPORT_HOUR and
                    now.minute < DAILY_REPORT_MINUTE + 2 and
                    report_sent_today != today):
                send_telegram(build_daily_report(now))
                report_sent_today = today
                log.info("Rapport quotidien envoyé")

            # Filtre news
            blackout, active_news = is_news_blackout(now)
            if blackout:
                if not news_notified:
                    names = ", ".join(n["name"] for n in active_news)
                    send_telegram(
                        f"🚨 <b>PAUSE NEWS — {now.strftime('%H:%M UTC')}</b>\n"
                        f"Annonce : {names}\n"
                        "⏸ Aucun signal pendant cette fenêtre."
                    )
                    news_notified = True
                time.sleep(SCAN_INTERVAL)
                continue
            news_notified = False

            # Filtre session
            session = get_active_session(now)
            if session is None:
                if last_session is not None:
                    send_telegram(
                        f"😴 <b>Hors session</b> — {now.strftime('%H:%M UTC')}\n"
                        "Reprise au prochain créneau."
                    )
                    last_session = None
                    last_signal.clear()
                time.sleep(SCAN_INTERVAL)
                continue

            if session != last_session:
                send_telegram(
                    f"📍 <b>Session {session}</b> — {now.strftime('%H:%M UTC')}\n"
                    f"ℹ️ {SESSION_TIPS.get(session,'Session active.')}\n"
                    "🤖 Scan actif — XAU/USD · BTC/USD"
                )
                last_session = session
                last_signal.clear()

            # Scan
            for symbol in SYMBOLS:
                scan_symbol(symbol, session, now)
                time.sleep(3)

        except KeyboardInterrupt:
            send_telegram("🛑 <b>Robot arrêté.</b>")
            break
        except Exception as e:
            log.error(f"Erreur : {e}")
            send_telegram(f"⚠️ <b>Erreur :</b> {e}")

        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
