"""
Le Collectif Trading — Robot de signaux v3
Stratégie : EMA 9/21 + RSI + MACD + CVD
Timeframes : M15 (biais) → M5 (confirmation + entrée)
Sessions : Asie + Londres + New York (24h/5j)
Filtre news : pause avant/après annonces majeures
Paper trading : journal CSV + rapport quotidien automatique
"""

import os
import csv
import time
import logging
import requests
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ─── CONFIG ──────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "TON_TOKEN_ICI")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "TON_CHAT_ID_ICI")
TWELVE_DATA_KEY  = os.getenv("TWELVE_DATA_KEY",  "TA_CLE_ICI")

SYMBOLS = ["XAU/USD", "BTC/USD"]

# Indicateurs
EMA_FAST   = 9
EMA_SLOW   = 21
RSI_PERIOD = 14
MACD_FAST  = 12
MACD_SLOW  = 26
MACD_SIG   = 9
CVD_WINDOW = 20

# Filtres RSI
RSI_LONG_MAX  = 65
RSI_LONG_MIN  = 35
RSI_SHORT_MIN = 35
RSI_SHORT_MAX = 65

# Score minimum sur 7 pour envoyer un signal
SCORE_MIN = 7

# Pause autour des news (minutes)
NEWS_BUFFER_BEFORE = 30
NEWS_BUFFER_AFTER  = 30

# Scan toutes les 60 secondes
SCAN_INTERVAL = 60

# Heure du rapport quotidien (UTC)
DAILY_REPORT_HOUR   = 21
DAILY_REPORT_MINUTE = 0

# Fichier journal CSV
JOURNAL_FILE = Path("journal_trades.csv")
JOURNAL_COLS = [
    "id", "date", "heure_utc", "symbol", "direction", "score",
    "prix_entree", "sl", "tp", "session",
    "rsi", "macd_hist", "cvd",
    "resultat",        # WIN / LOSS / EN_COURS
    "prix_sortie",     # à remplir manuellement ou via suivi
    "pnl_r",          # résultat en R (+2, -1, etc.)
    "notes"
]

# ─── SESSIONS ────────────────────────────────────────────────────────────────

SESSIONS = {
    "Asie":     (0,  9),
    "Londres":  (7,  16),
    "New York": (13, 22),
}

# ─── ANNONCES MAJEURES ───────────────────────────────────────────────────────

RECURRING_NEWS = {
    0: [],
    1: [
        (13, 30, "CPI USA"),
        (13, 30, "PPI USA"),
    ],
    2: [
        (13, 30, "CPI / PPI USA"),
        (18, 0,  "FOMC Minutes"),
        (19, 0,  "FOMC Statement"),
    ],
    3: [
        (12, 45, "BCE Decision"),
        (13, 30, "Jobless Claims USA"),
        (13, 30, "PIB USA"),
    ],
    4: [
        (13, 30, "NFP"),
        (13, 30, "Unemployment Rate USA"),
        (15, 0,  "Michigan Sentiment"),
    ],
}

# ─── LOGGING ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("LCT-Bot")

# ─── JOURNAL CSV ─────────────────────────────────────────────────────────────

def init_journal() -> None:
    """Crée le fichier journal s'il n'existe pas encore."""
    if not JOURNAL_FILE.exists():
        with open(JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=JOURNAL_COLS)
            writer.writeheader()
        log.info("Journal CSV créé")

def get_next_id() -> int:
    """Retourne le prochain ID de trade."""
    if not JOURNAL_FILE.exists():
        return 1
    with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return len(rows) + 1

def log_trade(symbol: str, direction: str, score: int,
              price: float, sl: float, tp: float,
              session: str, rsi: float,
              macd_hist: float, cvd: float,
              now_utc: datetime) -> int:
    """
    Enregistre un signal dans le journal.
    Retourne l'ID du trade.
    """
    trade_id = get_next_id()
    row = {
        "id":          trade_id,
        "date":        now_utc.strftime("%Y-%m-%d"),
        "heure_utc":   now_utc.strftime("%H:%M"),
        "symbol":      symbol,
        "direction":   direction.upper(),
        "score":       score,
        "prix_entree": round(price, 4),
        "sl":          round(sl, 4),
        "tp":          round(tp, 4),
        "session":     session,
        "rsi":         round(rsi, 1),
        "macd_hist":   round(macd_hist, 4),
        "cvd":         round(cvd, 0),
        "resultat":    "EN_COURS",
        "prix_sortie": "",
        "pnl_r":       "",
        "notes":       "",
    }
    with open(JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=JOURNAL_COLS)
        writer.writerow(row)
    log.info(f"Trade #{trade_id} enregistré dans le journal")
    return trade_id

def load_journal() -> list[dict]:
    """Charge tous les trades du journal."""
    if not JOURNAL_FILE.exists():
        return []
    with open(JOURNAL_FILE, "r", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def get_trades_today(now_utc: datetime) -> list[dict]:
    """Retourne les trades du jour."""
    today = now_utc.strftime("%Y-%m-%d")
    return [t for t in load_journal() if t.get("date") == today]

def get_all_closed_trades() -> list[dict]:
    """Retourne tous les trades clôturés (WIN ou LOSS)."""
    return [t for t in load_journal() if t.get("resultat") in ("WIN", "LOSS")]

# ─── CALCUL SL / TP ──────────────────────────────────────────────────────────

def compute_sl_tp(df5: pd.DataFrame, direction: str,
                  price: float) -> tuple[float, float]:
    """
    Calcule SL et TP depuis les EMA M5.
    Long  : SL sous EMA 21 M5, TP = prix + 2 × (prix - SL)
    Short : SL au-dessus EMA 9 M5, TP = prix - 2 × (SL - prix)
    """
    last  = df5.iloc[-1]
    ema9  = float(last["ema9"])
    ema21 = float(last["ema21"])

    # Buffer : 0.1% du prix (adapté XAU/USD et BTC)
    buffer = price * 0.001

    if direction == "long":
        sl   = ema21 - buffer
        risk = price - sl
        tp   = price + (risk * 2)
    else:
        sl   = ema9 + buffer
        risk = sl - price
        tp   = price - (risk * 2)

    return round(sl, 4), round(tp, 4)

# ─── RAPPORT QUOTIDIEN ───────────────────────────────────────────────────────

def build_daily_report(now_utc: datetime) -> str:
    today       = now_utc.strftime("%Y-%m-%d")
    all_trades  = load_journal()
    today_trades = [t for t in all_trades if t.get("date") == today]
    all_closed   = [t for t in all_trades if t.get("resultat") in ("WIN", "LOSS")]

    # Stats du jour
    today_closed = [t for t in today_trades if t.get("resultat") in ("WIN", "LOSS")]
    today_wins   = [t for t in today_closed if t["resultat"] == "WIN"]
    today_losses = [t for t in today_closed if t["resultat"] == "LOSS"]
    today_open   = [t for t in today_trades if t.get("resultat") == "EN_COURS"]

    # Calcul PnL du jour en R
    day_pnl = 0.0
    for t in today_closed:
        try:
            day_pnl += float(t["pnl_r"])
        except (ValueError, TypeError):
            pass

    # Stats globales
    total_closed = len(all_closed)
    total_wins   = len([t for t in all_closed if t["resultat"] == "WIN"])
    win_rate_all = (total_wins / total_closed * 100) if total_closed > 0 else 0

    total_pnl = 0.0
    for t in all_closed:
        try:
            total_pnl += float(t["pnl_r"])
        except (ValueError, TypeError):
            pass

    # Win rate du jour
    wr_day = (len(today_wins) / len(today_closed) * 100) if today_closed else 0

    # Stats par session aujourd'hui
    sessions_today: dict[str, list] = {}
    for t in today_trades:
        s = t.get("session", "?")
        sessions_today.setdefault(s, []).append(t)

    # Stats par symbol aujourd'hui
    by_symbol: dict[str, dict] = {}
    for t in today_trades:
        sym = t.get("symbol", "?")
        if sym not in by_symbol:
            by_symbol[sym] = {"total": 0, "wins": 0, "losses": 0}
        by_symbol[sym]["total"] += 1
        if t.get("resultat") == "WIN":
            by_symbol[sym]["wins"] += 1
        elif t.get("resultat") == "LOSS":
            by_symbol[sym]["losses"] += 1

    # Emoji performance
    if day_pnl > 0:
        perf_emoji = "🟢"
    elif day_pnl < 0:
        perf_emoji = "🔴"
    else:
        perf_emoji = "⚪"

    date_label = now_utc.strftime("%d/%m/%Y")

    msg = (
        f"📊 <b>RAPPORT QUOTIDIEN — {date_label}</b>\n"
        f"{'─' * 32}\n\n"

        f"<b>Signaux du jour : {len(today_trades)}</b>\n"
        f"  ✅ Clôturés : {len(today_closed)}\n"
        f"  ⏳ En cours : {len(today_open)}\n\n"

        f"<b>{perf_emoji} Résultats du jour</b>\n"
        f"  🟢 Wins  : {len(today_wins)}\n"
        f"  🔴 Losses: {len(today_losses)}\n"
        f"  📈 Win rate : {wr_day:.0f}%\n"
        f"  💰 PnL jour : {day_pnl:+.1f}R\n\n"
    )

    # Détail par symbol
    if by_symbol:
        msg += "<b>Par instrument</b>\n"
        for sym, stats in by_symbol.items():
            wr = (stats["wins"] / (stats["wins"] + stats["losses"]) * 100) if (stats["wins"] + stats["losses"]) > 0 else 0
            msg += f"  {sym} : {stats['total']} signaux — {stats['wins']}W / {stats['losses']}L ({wr:.0f}%)\n"
        msg += "\n"

    # Détail par session
    if sessions_today:
        msg += "<b>Par session</b>\n"
        for sess, trades in sessions_today.items():
            wins = len([t for t in trades if t.get("resultat") == "WIN"])
            losses = len([t for t in trades if t.get("resultat") == "LOSS"])
            msg += f"  {sess} : {len(trades)} signaux — {wins}W / {losses}L\n"
        msg += "\n"

    # Stats globales depuis le début
    msg += (
        f"{'─' * 32}\n"
        f"<b>📈 Stats globales (paper trading)</b>\n"
        f"  Trades clôturés : {total_closed}\n"
        f"  Win rate global : {win_rate_all:.0f}%\n"
        f"  PnL total : {total_pnl:+.1f}R\n\n"
    )

    # Recommandation selon le win rate global
    if total_closed >= 20:
        if win_rate_all >= 55 and total_pnl > 0:
            msg += "💡 <i>Win rate solide et PnL positif. La stratégie montre de bons résultats sur cet échantillon.</i>\n"
        elif win_rate_all >= 45 and total_pnl > 0:
            msg += "💡 <i>Résultats corrects. Continue le paper trading pour confirmer sur 50 trades.</i>\n"
        elif win_rate_all < 45 or total_pnl < 0:
            msg += "⚠️ <i>Win rate faible ou PnL négatif. Pas encore prêt pour le live — analyser les trades perdants.</i>\n"
    else:
        remaining = 20 - total_closed
        msg += f"⏳ <i>Encore {remaining} trade(s) clôturé(s) pour avoir un premier bilan fiable.</i>\n"

    # Trades du jour en détail
    if today_trades:
        msg += f"\n<b>Détail signaux du jour</b>\n"
        for t in today_trades[-5:]:   # max 5 pour ne pas surcharger
            res = t.get("resultat", "?")
            emoji_r = "✅" if res == "WIN" else ("❌" if res == "LOSS" else "⏳")
            pnl = f" | {float(t['pnl_r']):+.1f}R" if t.get("pnl_r") else ""
            msg += (
                f"  {emoji_r} #{t['id']} {t['symbol']} {t['direction']} "
                f"@ {t['prix_entree']} — {res}{pnl}\n"
            )
        if len(today_trades) > 5:
            msg += f"  ... et {len(today_trades)-5} autre(s) dans le CSV\n"

    msg += (
        "\n📁 <i>Journal complet : journal_trades.csv\n"
        "Pour clôturer un trade, mettre à jour le CSV\n"
        "avec resultat (WIN/LOSS), prix_sortie et pnl_r.</i>"
    )

    return msg

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def send_telegram(message: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML"
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
        log.info("Telegram ✓")
    except Exception as e:
        log.error(f"Telegram erreur : {e}")

# ─── SESSIONS ────────────────────────────────────────────────────────────────

def get_active_session(now_utc: datetime) -> str | None:
    weekday = now_utc.weekday()
    hour    = now_utc.hour
    if weekday == 5 and hour >= 22:
        return None
    if weekday == 6:
        return None
    active = [name for name, (s, e) in SESSIONS.items() if s <= hour < e]
    if not active:
        return None
    if "Londres" in active and "New York" in active:
        return "Londres + New York ⚡"
    if "Asie" in active and "Londres" in active:
        return "Asie + Londres ⚡"
    return active[0]

# ─── NEWS ────────────────────────────────────────────────────────────────────

def is_news_blackout(now_utc: datetime) -> tuple[bool, list]:
    weekday = now_utc.weekday()
    risky   = []
    for (h, m, name) in RECURRING_NEWS.get(weekday, []):
        news_time    = now_utc.replace(hour=h, minute=m, second=0, microsecond=0)
        delta_before = (news_time - now_utc).total_seconds() / 60
        delta_after  = (now_utc - news_time).total_seconds() / 60
        if -NEWS_BUFFER_AFTER <= delta_after <= 0:
            risky.append({"name": name, "time": news_time, "status": "vient de passer"})
        elif 0 < delta_before <= NEWS_BUFFER_BEFORE:
            risky.append({"name": name, "time": news_time,
                          "status": f"dans {delta_before:.0f} min"})
    return (len(risky) > 0, risky)

# ─── DONNÉES ─────────────────────────────────────────────────────────────────

def fetch_candles(symbol: str, interval: str, outputsize: int = 100) -> pd.DataFrame | None:
    params = {
        "symbol": symbol, "interval": interval,
        "outputsize": outputsize, "apikey": TWELVE_DATA_KEY, "order": "ASC",
    }
    try:
        r = requests.get("https://api.twelvedata.com/time_series",
                         params=params, timeout=15)
        data = r.json()
        if "values" not in data:
            log.warning(f"Pas de données {symbol} {interval}")
            return None
        df = pd.DataFrame(data["values"]).rename(columns={
            "datetime": "time", "open": "open", "high": "high",
            "low": "low", "close": "close", "volume": "volume"
        })
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["time"] = pd.to_datetime(df["time"])
        return df.dropna().reset_index(drop=True)
    except Exception as e:
        log.error(f"Erreur fetch {symbol} {interval} : {e}")
        return None

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema9"]  = ta.ema(df["close"], length=EMA_FAST)
    df["ema21"] = ta.ema(df["close"], length=EMA_SLOW)
    df["rsi"]   = ta.rsi(df["close"], length=RSI_PERIOD)
    macd = ta.macd(df["close"], fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIG)
    df["macd"]      = macd[f"MACD_{MACD_FAST}_{MACD_SLOW}_{MACD_SIG}"]
    df["macd_sig"]  = macd[f"MACDs_{MACD_FAST}_{MACD_SLOW}_{MACD_SIG}"]
    df["macd_hist"] = macd[f"MACDh_{MACD_FAST}_{MACD_SLOW}_{MACD_SIG}"]
    df["delta"] = df.apply(
        lambda r: r["volume"] if r["close"] >= r["open"] else -r["volume"], axis=1
    )
    df["cvd"] = df["delta"].rolling(CVD_WINDOW).sum()
    return df.dropna().reset_index(drop=True)

# ─── ANALYSE ─────────────────────────────────────────────────────────────────

def analyse_m15(df: pd.DataFrame) -> dict:
    result = {"biais": "neutre", "score": 0, "details": []}
    last, prev = df.iloc[-1], df.iloc[-2]
    ema9_up  = last["ema9"]  > prev["ema9"]
    ema21_up = last["ema21"] > prev["ema21"]
    close    = last["close"]

    if last["ema9"] > last["ema21"] and ema9_up and ema21_up:
        result.update({"biais": "long", "score": 1})
        result["details"].append("EMA 9 > EMA 21 haussières ✅")
        if last["ema21"] <= close <= last["ema9"]:
            result["score"] = 2
            result["details"].append("Prix en zone pullback ✅")
    elif last["ema9"] < last["ema21"] and not ema9_up and not ema21_up:
        result.update({"biais": "short", "score": 1})
        result["details"].append("EMA 9 < EMA 21 baissières ✅")
        if last["ema9"] <= close <= last["ema21"]:
            result["score"] = 2
            result["details"].append("Prix en zone pullback ✅")
    else:
        result["details"].append("EMA enchevêtrées ❌")
    return result

def analyse_m5(df: pd.DataFrame, biais: str) -> dict:
    result = {"score": 0, "details": []}
    if biais not in ("long", "short"):
        return result
    last, prev = df.iloc[-1], df.iloc[-2]
    rsi, macd_hist, prev_hist = last["rsi"], last["macd_hist"], prev["macd_hist"]
    cvd, prev_cvd, delta = last["cvd"], prev["cvd"], last["delta"]

    # RSI
    in_zone = (biais == "long" and RSI_LONG_MIN <= rsi <= RSI_LONG_MAX) or \
              (biais == "short" and RSI_SHORT_MIN <= rsi <= RSI_SHORT_MAX)
    extreme_ok = (biais == "long" and rsi < RSI_LONG_MIN) or \
                 (biais == "short" and rsi > RSI_SHORT_MAX)
    if in_zone or extreme_ok:
        result["score"] += 1
        result["details"].append(f"RSI {rsi:.1f} favorable ✅")
    else:
        result["details"].append(f"RSI {rsi:.1f} défavorable ❌")

    # MACD pas de divergence
    div = (biais == "long"  and macd_hist < 0 and macd_hist < prev_hist) or \
          (biais == "short" and macd_hist > 0 and macd_hist > prev_hist)
    if not div:
        result["score"] += 1
        result["details"].append("Pas de divergence MACD ✅")
    else:
        result["details"].append("Divergence MACD ❌")

    # MACD déclencheur
    crossed_up   = prev["macd"] < prev["macd_sig"] and last["macd"] > last["macd_sig"]
    crossed_down = prev["macd"] > prev["macd_sig"] and last["macd"] < last["macd_sig"]
    hist_green   = macd_hist > 0 and macd_hist > prev_hist
    hist_red     = macd_hist < 0 and macd_hist < prev_hist
    triggered = (biais == "long"  and (crossed_up or hist_green)) or \
                (biais == "short" and (crossed_down or hist_red))
    if triggered:
        result["score"] += 1
        result["details"].append("MACD déclencheur ✅")
    else:
        result["details"].append("MACD pas de déclencheur ❌")

    # CVD
    cvd_ok = (biais == "long"  and cvd > 0 and cvd >= prev_cvd) or \
             (biais == "short" and cvd < 0 and cvd <= prev_cvd)
    if cvd_ok:
        result["score"] += 1
        result["details"].append(f"CVD aligné ({cvd:+.0f}) ✅")
    else:
        result["details"].append(f"CVD non aligné ({cvd:+.0f}) ❌")

    # Delta
    delta_ok = (biais == "long" and delta > 0) or (biais == "short" and delta < 0)
    if delta_ok:
        result["score"] += 1
        result["details"].append(f"Delta aligné ({delta:+.0f}) ✅")
    else:
        result["details"].append(f"Delta non aligné ({delta:+.0f}) ❌")

    return result

# ─── FORMAT SIGNAL ───────────────────────────────────────────────────────────

def format_signal(trade_id: int, symbol: str, biais: str, score: int,
                  m15_details: list, m5_details: list,
                  price: float, sl: float, tp: float,
                  rsi: float, macd_hist: float, cvd: float,
                  session: str, now_utc: datetime) -> str:

    emoji = "📈" if biais == "long" else "📉"
    label = "LONG" if biais == "long" else "SHORT"
    bar   = "█" * score + "░" * (7 - score)
    risk  = abs(price - sl)
    now   = now_utc.strftime("%H:%M UTC")

    msg = (
        f"🔔 <b>LE COLLECTIF TRADING</b> 🔔\n"
        f"{emoji} <b>[PAPER] {label} — {symbol}</b>  <code>#{trade_id}</code>\n"
        f"🕐 {now}  |  📍 {session}\n\n"
        f"<b>Score : {score}/7</b>  [{bar}]\n\n"
        f"💰 <b>Entrée :</b> {price:.4f}\n"
        f"🔴 <b>SL :</b> {sl:.4f}  (−{risk:.4f})\n"
        f"🎯 <b>TP :</b> {tp:.4f}  (RR 1:2)\n\n"
        f"📊 RSI : {rsi:.1f}  |  MACDh : {macd_hist:+.4f}  |  CVD : {cvd:+.0f}\n\n"
    )

    msg += "<b>M15 :</b>\n"
    for d in m15_details:
        msg += f"  {d}\n"
    msg += "\n<b>M5 :</b>\n"
    for d in m5_details:
        msg += f"  {d}\n"

    msg += (
        f"\n📁 <i>Enregistré journal #{trade_id}\n"
        "Mets à jour le CSV avec le résultat.\n"
        "⚠️ PAPER TRADING — aucun argent réel.</i>"
    )
    return msg

# ─── SCAN SYMBOL ─────────────────────────────────────────────────────────────

last_signal: dict[str, str] = {}

def scan_symbol(symbol: str, session: str, now_utc: datetime) -> None:
    log.info(f"Scan {symbol} | {session}")

    df15 = fetch_candles(symbol, "15min", outputsize=80)
    if df15 is None or len(df15) < 30:
        return
    df15 = add_indicators(df15)
    m15   = analyse_m15(df15)
    biais = m15["biais"]

    if biais == "neutre":
        last_signal.pop(symbol, None)
        return

    df5 = fetch_candles(symbol, "5min", outputsize=80)
    if df5 is None or len(df5) < 30:
        return
    df5   = add_indicators(df5)
    m5    = analyse_m5(df5, biais)
    score = m15["score"] + m5["score"]
    last5 = df5.iloc[-1]
    price = float(last5["close"])

    log.info(f"{symbol} | {biais.upper()} | {score}/7 | RSI={last5['rsi']:.1f}")

    if score >= SCORE_MIN:
        sig_key = f"{symbol}_{biais}"
        if last_signal.get(symbol) == sig_key:
            log.info(f"{symbol} — doublon ignoré")
            return

        sl, tp = compute_sl_tp(df5, biais, price)

        # Enregistrer dans le journal
        trade_id = log_trade(
            symbol=symbol, direction=biais, score=score,
            price=price, sl=sl, tp=tp, session=session,
            rsi=float(last5["rsi"]), macd_hist=float(last5["macd_hist"]),
            cvd=float(last5["cvd"]), now_utc=now_utc,
        )

        msg = format_signal(
            trade_id=trade_id, symbol=symbol, biais=biais, score=score,
            m15_details=m15["details"], m5_details=m5["details"],
            price=price, sl=sl, tp=tp,
            rsi=float(last5["rsi"]), macd_hist=float(last5["macd_hist"]),
            cvd=float(last5["cvd"]), session=session, now_utc=now_utc,
        )
        send_telegram(msg)
        last_signal[symbol] = sig_key
        log.info(f"{symbol} — signal #{trade_id} envoyé ✅ ({score}/7)")
    else:
        last_signal.pop(symbol, None)
        log.info(f"{symbol} — score {score}/7 insuffisant")

# ─── BOUCLE PRINCIPALE ───────────────────────────────────────────────────────

def main() -> None:
    init_journal()

    last_session:      str | None = None
    news_notified:     bool       = False
    report_sent_today: str        = ""   # date du dernier rapport envoyé

    log.info("=" * 52)
    log.info("  LE COLLECTIF TRADING — Robot v3 — Paper Mode")
    log.info("  EMA 9/21 + RSI + MACD + CVD Orderflow")
    log.info("  Rapport quotidien automatique à 21h00 UTC")
    log.info("=" * 52)

    send_telegram(
        "🤖 <b>Le Collectif Trading — Robot v3 démarré</b>\n\n"
        "📊 Stratégie : EMA 9/21 + RSI + MACD + CVD\n"
        "🌍 Sessions : Asie · Londres · New York\n"
        "🚨 Filtre news actif\n"
        "📁 Journal paper trading actif\n"
        "📊 Rapport quotidien : 21h00 UTC\n"
        "⭐ Score min : 7/7"
    )

    while True:
        try:
            now_utc = datetime.now(timezone.utc)

            # ── Rapport quotidien ─────────────────────────────────────────
            today_str = now_utc.strftime("%Y-%m-%d")
            if (now_utc.hour == DAILY_REPORT_HOUR and
                    now_utc.minute < DAILY_REPORT_MINUTE + 2 and
                    report_sent_today != today_str):
                log.info("Envoi rapport quotidien...")
                report = build_daily_report(now_utc)
                send_telegram(report)
                report_sent_today = today_str

            # ── Filtre news ───────────────────────────────────────────────
            blackout, active_news = is_news_blackout(now_utc)
            if blackout:
                if not news_notified:
                    names = ", ".join(n["name"] for n in active_news)
                    send_telegram(
                        f"🚨 <b>PAUSE NEWS — {now_utc.strftime('%H:%M UTC')}</b>\n"
                        f"Annonce(s) : {names}\n"
                        "⏸ Aucun signal pendant cette fenêtre."
                    )
                    news_notified = True
                time.sleep(SCAN_INTERVAL)
                continue
            else:
                news_notified = False

            # ── Filtre session ────────────────────────────────────────────
            session = get_active_session(now_utc)
            if session is None:
                if last_session is not None:
                    send_telegram(
                        f"😴 <b>Hors session</b> — {now_utc.strftime('%H:%M UTC')}\n"
                        "Reprise au prochain créneau actif."
                    )
                    last_session = None
                    last_signal.clear()
                time.sleep(SCAN_INTERVAL)
                continue

            if session != last_session:
                tips = {
                    "Asie":                  "Volatilité modérée — XAU/USD et BTC actifs.",
                    "Londres":               "Forte liquidité — top setups. 🔥",
                    "New York":              "Pic de volatilité. 🔥",
                    "Londres + New York ⚡": "Chevauchement — maximum de liquidité. 🔥🔥",
                    "Asie + Londres ⚡":     "Chevauchement — bonne liquidité.",
                }
                send_telegram(
                    f"📍 <b>Session {session}</b> — {now_utc.strftime('%H:%M UTC')}\n"
                    f"ℹ️ {tips.get(session,'Session active.')}\n"
                    "🤖 Scan actif — XAU/USD · BTC/USD"
                )
                last_session = session
                last_signal.clear()

            # ── Scan ─────────────────────────────────────────────────────
            for symbol in SYMBOLS:
                scan_symbol(symbol, session, now_utc)
                time.sleep(3)

        except KeyboardInterrupt:
            log.info("Arrêt manuel")
            send_telegram("🛑 <b>Robot arrêté.</b>")
            break
        except Exception as e:
            log.error(f"Erreur : {e}")
            send_telegram(f"⚠️ <b>Erreur robot :</b> {e}")

        log.info(f"Prochain scan dans {SCAN_INTERVAL}s")
        time.sleep(SCAN_INTERVAL)

if __name__ == "__main__":
    main()
