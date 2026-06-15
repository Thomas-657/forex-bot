import os
import csv
import time
import math
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
TWELVE_DATA_KEY  = os.getenv("TWELVE_DATA_KEY",  "")

SYMBOLS       = ["XAU/USD", "BTC/USD"]
EMA_FAST      = 9
EMA_SLOW      = 21
RSI_PERIOD    = 14
MACD_FAST     = 12
MACD_SLOW     = 26
MACD_SIG      = 9
CVD_WINDOW    = 20
SCORE_MIN     = 7
SCAN_INTERVAL = 60
REPORT_HOUR   = 21
NEWS_BUF      = 30

JOURNAL_FILE = Path("journal_trades.csv")
JOURNAL_COLS = [
    "id", "date", "heure_utc", "symbol", "direction", "score",
    "prix_entree", "sl", "tp", "session", "rsi", "macd_hist", "cvd",
    "resultat", "prix_sortie", "pnl_r", "notes",
]

SESSIONS = {
    "Asie":     (0,  9),
    "Londres":  (7,  16),
    "New York": (13, 22),
}

SESSION_TIPS = {
    "Asie":                  "Volatilite moderee - XAU et BTC actifs.",
    "Londres":               "Forte liquidite - meilleurs setups. 🔥",
    "New York":              "Pic de volatilite. 🔥",
    "Londres + New York ⚡": "Chevauchement - maximum de liquidite. 🔥🔥",
    "Asie + Londres ⚡":     "Chevauchement - bonne liquidite.",
}

RECURRING_NEWS = {
    0: [],
    1: [(13, 30, "CPI USA"), (13, 30, "PPI USA")],
    2: [(13, 30, "CPI USA"), (18, 0, "FOMC Minutes"), (19, 0, "FOMC Statement")],
    3: [(12, 45, "BCE Decision"), (13, 30, "Jobless Claims"), (13, 30, "PIB USA")],
    4: [(13, 30, "NFP"), (13, 30, "Unemployment Rate"), (15, 0, "Michigan Sentiment")],
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("LCT")

# ─── TELEGRAM ────────────────────────────────────────────────────────────────

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log.warning("Telegram non configure")
        return
    try:
        r = requests.post(
            "https://api.telegram.org/bot{}/sendMessage".format(TELEGRAM_TOKEN),
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        log.error("Telegram : {}".format(e))

# ─── INDICATEURS (Python pur, zéro librairie) ────────────────────────────────

def calc_ema(values, period):
    """EMA avec lissage ewm — identique TradingView."""
    k   = 2.0 / (period + 1)
    ema = values[0]
    result = [ema]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
        result.append(ema)
    return result

def calc_rsi(closes, period=14):
    """RSI de Wilder."""
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    if len(gains) < period:
        return [50.0] * len(closes)

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals = [50.0] * (period + 1)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi_vals

def calc_macd(closes, fast=12, slow=26, signal=9):
    """Retourne (macd_line, signal_line, histogram) — listes de même longueur."""
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    line     = [f - s for f, s in zip(ema_fast, ema_slow)]
    sig      = calc_ema(line, signal)
    hist     = [l - s for l, s in zip(line, sig)]
    return line, sig, hist

def calc_cvd(candles, window=20):
    """CVD simplifié sur `window` bougies : buy vol si close >= open, sinon sell."""
    deltas = []
    for c in candles:
        vol = c["volume"]
        deltas.append(vol if c["close"] >= c["open"] else -vol)
    cvd = []
    for i in range(len(deltas)):
        start = max(0, i - window + 1)
        cvd.append(sum(deltas[start:i + 1]))
    return cvd

def add_indicators(candles):
    """
    Ajoute EMA9, EMA21, RSI, MACD, CVD à chaque bougie.
    Retourne la liste enrichie (même longueur).
    """
    closes = [c["close"] for c in candles]

    ema9  = calc_ema(closes, EMA_FAST)
    ema21 = calc_ema(closes, EMA_SLOW)
    rsi   = calc_rsi(closes, RSI_PERIOD)
    ml, ms, mh = calc_macd(closes, MACD_FAST, MACD_SLOW, MACD_SIG)
    cvd   = calc_cvd(candles, CVD_WINDOW)
    deltas = [
        c["volume"] if c["close"] >= c["open"] else -c["volume"]
        for c in candles
    ]

    result = []
    for i, c in enumerate(candles):
        result.append({
            **c,
            "ema9":      ema9[i],
            "ema21":     ema21[i],
            "rsi":       rsi[i],
            "macd":      ml[i],
            "macd_sig":  ms[i],
            "macd_hist": mh[i],
            "cvd":       cvd[i],
            "delta":     deltas[i],
        })
    return result

# ─── DONNÉES ─────────────────────────────────────────────────────────────────

def fetch_candles(symbol, interval, outputsize=100):
    """Récupère les bougies depuis Twelve Data. Retourne une liste de dicts."""
    try:
        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol":     symbol,
                "interval":   interval,
                "outputsize": outputsize,
                "apikey":     TWELVE_DATA_KEY,
                "order":      "ASC",
            },
            timeout=15,
        )
        data = r.json()
        if "values" not in data:
            log.warning("Pas de donnees {} {} : {}".format(
                symbol, interval, data.get("message", "?")))
            return None

        candles = []
        for v in data["values"]:
            try:
                candles.append({
                    "time":   v["datetime"],
                    "open":   float(v["open"]),
                    "high":   float(v["high"]),
                    "low":    float(v["low"]),
                    "close":  float(v["close"]),
                    "volume": float(v.get("volume", 1)),
                })
            except (ValueError, KeyError):
                continue

        return candles if len(candles) >= 30 else None

    except Exception as e:
        log.error("fetch {} {} : {}".format(symbol, interval, e))
        return None

# ─── SESSIONS ────────────────────────────────────────────────────────────────

def get_active_session(now):
    wd, h = now.weekday(), now.hour
    if (wd == 5 and h >= 22) or wd == 6:
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

def is_news_blackout(now):
    risky = []
    for h, m, name in RECURRING_NEWS.get(now.weekday(), []):
        nt  = now.replace(hour=h, minute=m, second=0, microsecond=0)
        dbf = (nt - now).total_seconds() / 60.0
        daf = (now - nt).total_seconds() / 60.0
        if 0 < dbf <= NEWS_BUF:
            risky.append({"name": name, "status": "dans {:.0f} min".format(dbf)})
        elif 0 <= daf < NEWS_BUF:
            risky.append({"name": name, "status": "vient de passer"})
    return bool(risky), risky

# ─── JOURNAL ─────────────────────────────────────────────────────────────────

def init_journal():
    if not JOURNAL_FILE.exists():
        with open(JOURNAL_FILE, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=JOURNAL_COLS).writeheader()

def next_id():
    if not JOURNAL_FILE.exists():
        return 1
    with open(JOURNAL_FILE, encoding="utf-8") as f:
        return len(list(csv.DictReader(f))) + 1

def save_trade(symbol, direction, score, price, sl, tp,
               session, rsi, macd_hist, cvd, now):
    tid = next_id()
    with open(JOURNAL_FILE, "a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=JOURNAL_COLS).writerow({
            "id":          tid,
            "date":        now.strftime("%Y-%m-%d"),
            "heure_utc":   now.strftime("%H:%M"),
            "symbol":      symbol,
            "direction":   direction.upper(),
            "score":       score,
            "prix_entree": round(price,     4),
            "sl":          round(sl,        4),
            "tp":          round(tp,        4),
            "session":     session,
            "rsi":         round(rsi,       1),
            "macd_hist":   round(macd_hist, 5),
            "cvd":         round(cvd,       0),
            "resultat":    "EN_COURS",
            "prix_sortie": "",
            "pnl_r":       "",
            "notes":       "",
        })
    return tid

def load_journal():
    if not JOURNAL_FILE.exists():
        return []
    with open(JOURNAL_FILE, encoding="utf-8") as f:
        return list(csv.DictReader(f))

# ─── SL / TP ─────────────────────────────────────────────────────────────────

def compute_sl_tp(candles, direction, price):
    last = candles[-1]
    buf  = price * 0.001
    if direction == "long":
        sl = last["ema21"] - buf
        tp = price + (price - sl) * 2.0
    else:
        sl = last["ema9"] + buf
        tp = price - (sl - price) * 2.0
    return round(sl, 4), round(tp, 4)

# ─── ANALYSE ─────────────────────────────────────────────────────────────────

def analyse_m15(candles):
    res = {"biais": "neutre", "score": 0, "details": []}
    l, p = candles[-1], candles[-2]
    up9  = l["ema9"]  > p["ema9"]
    up21 = l["ema21"] > p["ema21"]
    c    = l["close"]

    if l["ema9"] > l["ema21"] and up9 and up21:
        res["biais"] = "long"
        res["score"] = 1
        res["details"].append("EMA 9 > EMA 21 haussieres ✅")
        if l["ema21"] <= c <= l["ema9"]:
            res["score"] = 2
            res["details"].append("Prix en zone pullback ✅")
        else:
            res["details"].append("Prix hors zone pullback")

    elif l["ema9"] < l["ema21"] and not up9 and not up21:
        res["biais"] = "short"
        res["score"] = 1
        res["details"].append("EMA 9 < EMA 21 baissieres ✅")
        if l["ema9"] <= c <= l["ema21"]:
            res["score"] = 2
            res["details"].append("Prix en zone pullback ✅")
        else:
            res["details"].append("Prix hors zone pullback")
    else:
        res["details"].append("EMA enchevêtrees ❌")
    return res

def analyse_m5(candles, biais):
    res = {"score": 0, "details": []}
    if biais not in ("long", "short"):
        return res

    l, p  = candles[-1], candles[-2]
    rsi   = l["rsi"]
    hist  = l["macd_hist"]
    phist = p["macd_hist"]
    cvd   = l["cvd"]
    pcvd  = p["cvd"]
    delta = l["delta"]
    macd  = l["macd"]
    msig  = l["macd_sig"]
    pmacd = p["macd"]
    pmsig = p["macd_sig"]

    # RSI
    if biais == "long":
        rsi_ok = 35 <= rsi <= 65 or rsi < 35
    else:
        rsi_ok = 35 <= rsi <= 65 or rsi > 65
    if rsi_ok:
        res["score"] += 1
        res["details"].append("RSI {:.1f} favorable ✅".format(rsi))
    else:
        res["details"].append("RSI {:.1f} defavorable ❌".format(rsi))

    # MACD pas de divergence
    div = (biais == "long"  and hist < 0 and hist < phist) or \
          (biais == "short" and hist > 0 and hist > phist)
    if not div:
        res["score"] += 1
        res["details"].append("Pas de divergence MACD ✅")
    else:
        res["details"].append("Divergence MACD ❌")

    # MACD déclencheur
    cx_up   = pmacd < pmsig and macd > msig
    cx_down = pmacd > pmsig and macd < msig
    if biais == "long":
        trig = cx_up or (hist > 0 and hist > phist)
    else:
        trig = cx_down or (hist < 0 and hist < phist)
    if trig:
        res["score"] += 1
        res["details"].append("MACD declencheur ✅")
    else:
        res["details"].append("MACD pas de declencheur ❌")

    # CVD
    cvd_ok = (biais == "long"  and cvd > 0 and cvd >= pcvd) or \
             (biais == "short" and cvd < 0 and cvd <= pcvd)
    if cvd_ok:
        res["score"] += 1
        res["details"].append("CVD aligne ({:+.0f}) ✅".format(cvd))
    else:
        res["details"].append("CVD non aligne ({:+.0f}) ❌".format(cvd))

    # Delta bougie
    delta_ok = (biais == "long" and delta > 0) or (biais == "short" and delta < 0)
    if delta_ok:
        res["score"] += 1
        res["details"].append("Delta aligne ({:+.0f}) ✅".format(delta))
    else:
        res["details"].append("Delta non aligne ({:+.0f}) ❌".format(delta))

    return res

# ─── RAPPORT QUOTIDIEN ───────────────────────────────────────────────────────

def build_daily_report(now):
    today      = now.strftime("%Y-%m-%d")
    all_trades = load_journal()
    t_today    = [t for t in all_trades if t.get("date") == today]
    closed     = [t for t in t_today   if t.get("resultat") in ("WIN", "LOSS")]
    wins       = [t for t in closed    if t.get("resultat") == "WIN"]
    open_t     = [t for t in t_today   if t.get("resultat") == "EN_COURS"]

    def sf(v):
        try:    return float(v)
        except: return 0.0

    day_pnl    = sum(sf(t["pnl_r"]) for t in closed)
    wr_day     = len(wins) / len(closed) * 100.0 if closed else 0.0
    all_closed = [t for t in all_trades if t.get("resultat") in ("WIN", "LOSS")]
    total_wins = sum(1 for t in all_closed if t.get("resultat") == "WIN")
    wr_all     = total_wins / len(all_closed) * 100.0 if all_closed else 0.0
    total_pnl  = sum(sf(t["pnl_r"]) for t in all_closed)
    perf       = "🟢" if day_pnl > 0 else ("🔴" if day_pnl < 0 else "⚪")

    by_sym, by_sess = {}, {}
    for t in t_today:
        for d, key in [(by_sym, t.get("symbol","?")), (by_sess, t.get("session","?"))]:
            d.setdefault(key, {"w": 0, "l": 0, "n": 0})
            d[key]["n"] += 1
            if t.get("resultat") == "WIN":  d[key]["w"] += 1
            if t.get("resultat") == "LOSS": d[key]["l"] += 1

    msg = (
        "📊 <b>RAPPORT — {}</b>\n{}\n\n"
        "<b>Signaux : {}</b>  ✅{} clotures  ⏳{} en cours\n\n"
        "<b>{} Jour :</b> {}W/{}L  {:.0f}%  {:+.1f}R\n\n"
    ).format(
        now.strftime("%d/%m/%Y"), "─" * 28,
        len(t_today), len(closed), len(open_t),
        perf, len(wins), len(closed)-len(wins), wr_day, day_pnl,
    )

    if by_sym:
        msg += "<b>Par instrument</b>\n"
        for sym, s in by_sym.items():
            wr = s["w"]/(s["w"]+s["l"])*100 if s["w"]+s["l"] else 0
            msg += "  {} : {}W/{}L ({:.0f}%)\n".format(sym, s["w"], s["l"], wr)
        msg += "\n"

    if by_sess:
        msg += "<b>Par session</b>\n"
        for sess, s in by_sess.items():
            msg += "  {} : {}W/{}L\n".format(sess, s["w"], s["l"])
        msg += "\n"

    msg += (
        "{}\n<b>Global (paper)</b>\n"
        "  {} trades  |  {:.0f}% WR  |  {:+.1f}R\n\n"
    ).format("─"*28, len(all_closed), wr_all, total_pnl)

    if len(all_closed) >= 20:
        if wr_all >= 55 and total_pnl > 0:
            msg += "💡 <i>Strategie solide — envisager le live.</i>\n"
        elif wr_all >= 45 and total_pnl > 0:
            msg += "💡 <i>Resultats corrects — continuer le paper.</i>\n"
        else:
            msg += "⚠️ <i>Win rate faible — analyser les pertes.</i>\n"
    else:
        msg += "⏳ <i>Encore {} trades pour un bilan fiable.</i>\n".format(
            20 - len(all_closed))

    if t_today:
        msg += "\n<b>Derniers signaux</b>\n"
        for t in t_today[-5:]:
            e   = "✅" if t["resultat"]=="WIN" else ("❌" if t["resultat"]=="LOSS" else "⏳")
            pnl = " {:+.1f}R".format(sf(t["pnl_r"])) if t.get("pnl_r") else ""
            msg += "  {} #{} {} {} @ {}{}\n".format(
                e, t["id"], t["symbol"], t["direction"], t["prix_entree"], pnl)

    msg += "\n📁 <i>journal_trades.csv</i>"
    return msg

# ─── SCAN ────────────────────────────────────────────────────────────────────

last_signal = {}

def scan_symbol(symbol, session, now):
    log.info("Scan {}".format(symbol))

    raw15 = fetch_candles(symbol, "15min", 80)
    if not raw15:
        return
    c15   = add_indicators(raw15)
    m15   = analyse_m15(c15)
    biais = m15["biais"]
    if biais == "neutre":
        last_signal.pop(symbol, None)
        return

    raw5 = fetch_candles(symbol, "5min", 80)
    if not raw5:
        return
    c5    = add_indicators(raw5)
    m5    = analyse_m5(c5, biais)
    score = m15["score"] + m5["score"]
    last5 = c5[-1]
    price = last5["close"]

    log.info("{} {} {}/7 RSI={:.1f}".format(
        symbol, biais.upper(), score, last5["rsi"]))

    if score >= SCORE_MIN:
        sig_key = "{}_{}".format(symbol, biais)
        if last_signal.get(symbol) == sig_key:
            log.info("{} doublon — skip".format(symbol))
            return

        sl, tp = compute_sl_tp(c5, biais, price)
        tid    = save_trade(
            symbol, biais, score, price, sl, tp, session,
            last5["rsi"], last5["macd_hist"], last5["cvd"], now,
        )

        emoji = "📈" if biais == "long" else "📉"
        bar   = "█" * score + "░" * (7 - score)
        risk  = abs(price - sl)

        msg  = (
            "🔔 <b>LE COLLECTIF TRADING</b> 🔔\n"
            "{} <b>[PAPER] {} — {}</b>  <code>#{}</code>\n"
            "🕐 {}  |  📍 {}\n\n"
            "<b>Score : {}/7</b>  [{}]\n\n"
            "💰 <b>Entree :</b> {:.4f}\n"
            "🔴 <b>SL :</b> {:.4f}  (risk {:.4f})\n"
            "🎯 <b>TP :</b> {:.4f}  (RR 1:2)\n\n"
            "📊 RSI {:.1f}  |  MACDh {:+.5f}  |  CVD {:+.0f}\n\n"
        ).format(
            emoji, biais.upper(), symbol, tid,
            now.strftime("%H:%M UTC"), session,
            score, bar,
            price, sl, risk, tp,
            last5["rsi"], last5["macd_hist"], last5["cvd"],
        )
        msg += "<b>M15 :</b>\n" + "".join("  {}\n".format(d) for d in m15["details"])
        msg += "\n<b>M5 :</b>\n" + "".join("  {}\n".format(d) for d in m5["details"])
        msg += "\n📁 <i>Trade #{} enregistre — PAPER TRADING.</i>".format(tid)

        send_telegram(msg)
        last_signal[symbol] = sig_key
        log.info("{} signal #{} envoye ✅".format(symbol, tid))
    else:
        last_signal.pop(symbol, None)

# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    init_journal()

    last_session      = None
    news_notified     = False
    report_sent_today = ""

    log.info("=" * 46)
    log.info("  LE COLLECTIF TRADING")
    log.info("  EMA 9/21 + RSI + MACD + CVD")
    log.info("  Zero dependance — Python pur")
    log.info("=" * 46)

    send_telegram(
        "🤖 <b>Le Collectif Trading — demarrage</b>\n\n"
        "📊 EMA 9/21 + RSI + MACD + CVD\n"
        "🌍 Asie · Londres · New York\n"
        "🚨 Filtre news actif\n"
        "📁 Paper trading — journal CSV\n"
        "📊 Rapport quotidien 21h UTC\n"
        "⭐ Score min : 7/7"
    )

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Rapport quotidien
            today = now.strftime("%Y-%m-%d")
            if (now.hour == REPORT_HOUR and
                    now.minute < 2 and
                    report_sent_today != today):
                send_telegram(build_daily_report(now))
                report_sent_today = today

            # Filtre news
            blackout, news_list = is_news_blackout(now)
            if blackout:
                if not news_notified:
                    names = ", ".join(n["name"] for n in news_list)
                    send_telegram(
                        "🚨 <b>PAUSE NEWS — {}</b>\n{}\n"
                        "⏸ Aucun signal pendant cette fenetre.".format(
                            now.strftime("%H:%M UTC"), names)
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
                        "😴 <b>Hors session</b> — {}\n"
                        "Reprise au prochain creneau.".format(
                            now.strftime("%H:%M UTC"))
                    )
                    last_session = None
                    last_signal.clear()
                time.sleep(SCAN_INTERVAL)
                continue

            if session != last_session:
                send_telegram(
                    "📍 <b>Session {}</b> — {}\n"
                    "ℹ️ {}\n"
                    "🤖 Scan — XAU/USD · BTC/USD".format(
                        session,
                        now.strftime("%H:%M UTC"),
                        SESSION_TIPS.get(session, "Session active."),
                    )
                )
                last_session = session
                last_signal.clear()

            # Scan
            for symbol in SYMBOLS:
                scan_symbol(symbol, session, now)
                time.sleep(3)

        except KeyboardInterrupt:
            send_telegram("🛑 <b>Robot arrete.</b>")
            break
        except Exception as e:
            log.error("Erreur : {}".format(e))
            send_telegram("⚠️ <b>Erreur :</b> {}".format(e))

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
