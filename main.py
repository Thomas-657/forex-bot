import os
import csv
import time
import logging
import requests
import pandas as pd
import numpy as np
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
        log.info("Telegram OK")
    except Exception as e:
        log.error("Telegram erreur : {}".format(e))


def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series, period=14):
    delta    = series.diff()
    gain     = delta.clip(lower=0)
    loss     = delta.clip(upper=0).abs()
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    avg_loss = avg_loss.replace(0, 1e-10)
    rs       = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast = calc_ema(series, fast)
    ema_slow = calc_ema(series, slow)
    line     = ema_fast - ema_slow
    sig      = calc_ema(line, signal)
    return line, sig, line - sig


def add_indicators(df):
    df = df.copy()
    close       = df["close"]
    df["ema9"]  = calc_ema(close, EMA_FAST)
    df["ema21"] = calc_ema(close, EMA_SLOW)
    df["rsi"]   = calc_rsi(close, RSI_PERIOD)
    ml, ms, mh  = calc_macd(close, MACD_FAST, MACD_SLOW, MACD_SIG)
    df["macd"]      = ml
    df["macd_sig"]  = ms
    df["macd_hist"] = mh
    df["delta"] = df.apply(
        lambda r: r["volume"] if r["close"] >= r["open"] else -r["volume"], axis=1
    )
    df["cvd"] = df["delta"].rolling(CVD_WINDOW).sum()
    return df.dropna().reset_index(drop=True)


def fetch_candles(symbol, interval, outputsize=100):
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
        df = pd.DataFrame(data["values"]).rename(columns={"datetime": "time"})
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["time"] = pd.to_datetime(df["time"])
        df = df.dropna().reset_index(drop=True)
        return df if len(df) >= 30 else None
    except Exception as e:
        log.error("fetch {} {} : {}".format(symbol, interval, e))
        return None


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


def compute_sl_tp(df5, direction, price):
    last = df5.iloc[-1]
    buf  = price * 0.001
    if direction == "long":
        sl = float(last["ema21"]) - buf
        tp = price + (price - sl) * 2.0
    else:
        sl = float(last["ema9"]) + buf
        tp = price - (sl - price) * 2.0
    return round(sl, 4), round(tp, 4)


def analyse_m15(df):
    res = {"biais": "neutre", "score": 0, "details": []}
    l, p = df.iloc[-1], df.iloc[-2]
    up9  = bool(l["ema9"]  > p["ema9"])
    up21 = bool(l["ema21"] > p["ema21"])
    c    = float(l["close"])

    if float(l["ema9"]) > float(l["ema21"]) and up9 and up21:
        res["biais"] = "long"
        res["score"] = 1
        res["details"].append("EMA 9 > EMA 21 haussieres ✅")
        if float(l["ema21"]) <= c <= float(l["ema9"]):
            res["score"] = 2
            res["details"].append("Prix en zone pullback ✅")
        else:
            res["details"].append("Prix hors zone pullback")
    elif float(l["ema9"]) < float(l["ema21"]) and not up9 and not up21:
        res["biais"] = "short"
        res["score"] = 1
        res["details"].append("EMA 9 < EMA 21 baissieres ✅")
        if float(l["ema9"]) <= c <= float(l["ema21"]):
            res["score"] = 2
            res["details"].append("Prix en zone pullback ✅")
        else:
            res["details"].append("Prix hors zone pullback")
    else:
        res["details"].append("EMA enchevêtrees ❌")
    return res


def analyse_m5(df, biais):
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
    macd  = float(l["macd"])
    msig  = float(l["macd_sig"])
    pmacd = float(p["macd"])
    pmsig = float(p["macd_sig"])

    if biais == "long":
        rsi_ok = 35 <= rsi <= 65 or rsi < 35
    else:
        rsi_ok = 35 <= rsi <= 65 or rsi > 65
    if rsi_ok:
        res["score"] += 1
        res["details"].append("RSI {:.1f} favorable ✅".format(rsi))
    else:
        res["details"].append("RSI {:.1f} defavorable ❌".format(rsi))

    div = (biais == "long"  and hist < 0 and hist < phist) or \
          (biais == "short" and hist > 0 and hist > phist)
    if not div:
        res["score"] += 1
        res["details"].append("Pas de divergence MACD ✅")
    else:
        res["details"].append("Divergence MACD ❌")

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

    cvd_ok = (biais == "long"  and cvd > 0 and cvd >= pcvd) or \
             (biais == "short" and cvd < 0 and cvd <= pcvd)
    if cvd_ok:
        res["score"] += 1
        res["details"].append("CVD aligne ({:+.0f}) ✅".format(cvd))
    else:
        res["details"].append("CVD non aligne ({:+.0f}) ❌".format(cvd))

    delta_ok = (biais == "long" and delta > 0) or (biais == "short" and delta < 0)
    if delta_ok:
        res["score"] += 1
        res["details"].append("Delta bougie aligne ({:+.0f}) ✅".format(delta))
    else:
        res["details"].append("Delta bougie non aligne ({:+.0f}) ❌".format(delta))

    return res


def build_daily_report(now):
    today      = now.strftime("%Y-%m-%d")
    all_trades = load_journal()
    t_today    = [t for t in all_trades if t.get("date") == today]
    closed     = [t for t in t_today   if t.get("resultat") in ("WIN", "LOSS")]
    wins       = [t for t in closed    if t.get("resultat") == "WIN"]
    open_t     = [t for t in t_today   if t.get("resultat") == "EN_COURS"]

    def safe_float(v):
        try:    return float(v)
        except: return 0.0

    day_pnl    = sum(safe_float(t["pnl_r"]) for t in closed)
    wr_day     = len(wins) / len(closed) * 100.0 if closed else 0.0
    all_closed = [t for t in all_trades if t.get("resultat") in ("WIN", "LOSS")]
    total_wins = sum(1 for t in all_closed if t.get("resultat") == "WIN")
    wr_all     = total_wins / len(all_closed) * 100.0 if all_closed else 0.0
    total_pnl  = sum(safe_float(t["pnl_r"]) for t in all_closed)
    perf       = "🟢" if day_pnl > 0 else ("🔴" if day_pnl < 0 else "⚪")

    by_sym  = {}
    by_sess = {}
    for t in t_today:
        for d, key in [(by_sym, t.get("symbol","?")), (by_sess, t.get("session","?"))]:
            d.setdefault(key, {"w": 0, "l": 0, "total": 0})
            d[key]["total"] += 1
            if t.get("resultat") == "WIN":  d[key]["w"] += 1
            if t.get("resultat") == "LOSS": d[key]["l"] += 1

    msg = (
        "📊 <b>RAPPORT — {}</b>\n{}\n\n"
        "<b>Signaux : {}</b>  ✅ {} clotures  ⏳ {} en cours\n\n"
        "<b>{} Jour</b>  {}W / {}L  {:.0f}%  {:+.1f}R\n\n"
    ).format(
        now.strftime("%d/%m/%Y"), "─" * 28,
        len(t_today), len(closed), len(open_t),
        perf, len(wins), len(closed) - len(wins), wr_day, day_pnl,
    )

    if by_sym:
        msg += "<b>Par instrument</b>\n"
        for sym, s in by_sym.items():
            wr = s["w"] / (s["w"] + s["l"]) * 100 if s["w"] + s["l"] else 0
            msg += "  {} : {}W/{}L ({:.0f}%)\n".format(sym, s["w"], s["l"], wr)
        msg += "\n"

    if by_sess:
        msg += "<b>Par session</b>\n"
        for sess, s in by_sess.items():
            msg += "  {} : {}W/{}L\n".format(sess, s["w"], s["l"])
        msg += "\n"

    msg += (
        "{}\n<b>Global (paper)</b>\n"
        "  {} trades  |  {:.0f}% WR  |  {:+.1f}R total\n\n"
    ).format("─" * 28, len(all_closed), wr_all, total_pnl)

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
            e   = "✅" if t["resultat"] == "WIN" else ("❌" if t["resultat"] == "LOSS" else "⏳")
            pnl = " {:+.1f}R".format(safe_float(t["pnl_r"])) if t.get("pnl_r") else ""
            msg += "  {} #{} {} {} @ {}{}\n".format(
                e, t["id"], t["symbol"], t["direction"], t["prix_entree"], pnl)

    msg += "\n📁 <i>journal_trades.csv</i>"
    return msg


last_signal = {}


def scan_symbol(symbol, session, now):
    log.info("Scan {}".format(symbol))

    df15 = fetch_candles(symbol, "15min", 80)
    if df15 is None:
        return
    df15  = add_indicators(df15)
    m15   = analyse_m15(df15)
    biais = m15["biais"]
    if biais == "neutre":
        last_signal.pop(symbol, None)
        return

    df5 = fetch_candles(symbol, "5min", 80)
    if df5 is None:
        return
    df5   = add_indicators(df5)
    m5    = analyse_m5(df5, biais)
    score = m15["score"] + m5["score"]
    last5 = df5.iloc[-1]
    price = float(last5["close"])

    log.info("{} {} {}/7 RSI={:.1f}".format(
        symbol, biais.upper(), score, float(last5["rsi"])))

    if score >= SCORE_MIN:
        sig_key = "{}_{}".format(symbol, biais)
        if last_signal.get(symbol) == sig_key:
            log.info("{} doublon — skip".format(symbol))
            return

        sl, tp = compute_sl_tp(df5, biais, price)
        tid    = save_trade(
            symbol, biais, score, price, sl, tp, session,
            float(last5["rsi"]), float(last5["macd_hist"]),
            float(last5["cvd"]), now,
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
            float(last5["rsi"]), float(last5["macd_hist"]), float(last5["cvd"]),
        )
        msg += "<b>M15 :</b>\n" + "".join("  {}\n".format(d) for d in m15["details"])
        msg += "\n<b>M5 :</b>\n" + "".join("  {}\n".format(d) for d in m5["details"])
        msg += "\n📁 <i>Trade #{} enregistre — PAPER TRADING.</i>".format(tid)

        send_telegram(msg)
        last_signal[symbol] = sig_key
        log.info("{} signal #{} envoye ✅".format(symbol, tid))
    else:
        last_signal.pop(symbol, None)


def main():
    init_journal()

    last_session      = None
    news_notified     = False
    report_sent_today = ""

    log.info("=" * 48)
    log.info("  LE COLLECTIF TRADING — v5")
    log.info("  EMA 9/21 + RSI + MACD + CVD")
    log.info("  Paper trading | Rapport 21h UTC")
    log.info("=" * 48)

    send_telegram(
        "🤖 <b>Le Collectif Trading — v5</b>\n\n"
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

            today = now.strftime("%Y-%m-%d")
            if (now.hour == REPORT_HOUR and
                    now.minute < 2 and
                    report_sent_today != today):
                send_telegram(build_daily_report(now))
                report_sent_today = today
                log.info("Rapport envoye")

            blackout, news_list = is_news_blackout(now)
            if blackout:
                if not news_notified:
                    names = ", ".join(n["name"] for n in news_list)
                    send_telegram(
                        "🚨 <b>PAUSE NEWS — {}</b>\n"
                        "{}\n"
                        "⏸ Aucun signal pendant cette fenetre.".format(
                            now.strftime("%H:%M UTC"), names)
                    )
                    news_notified = True
                time.sleep(SCAN_INTERVAL)
                continue
            news_notified = False

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
                    "🤖 Scan actif — XAU/USD · BTC/USD".format(
                        session,
                        now.strftime("%H:%M UTC"),
                        SESSION_TIPS.get(session, "Session active."),
                    )
                )
                last_session = session
                last_signal.clear()

            for symbol in SYMBOLS:
                scan_symbol(symbol, session, now)
                time.sleep(3)

        except KeyboardInterrupt:
            send_telegram("🛑 <b>Robot arrete.</b>")
            log.info("Arret")
            break
        except Exception as e:
            log.error("Erreur : {}".format(e))
            send_telegram("⚠️ <b>Erreur :</b> {}".format(e))

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
