import requests
import time
import re
from datetime import datetime
from xml.etree import ElementTree

TELEGRAM_TOKEN = "8690688254:AAHYhv2u3kufZob-yMFICeq7feEUj9CEz2E"
CHAT_ID = "916618328"
API_KEY = "demo"

PAIRES = ["EUR/USD", "BTC/USD", "XAU/USD"]
RISK_PAR_TRADE = 1.0
MAX_TRADES_JOUR = 5
ATR_MULTIPLIER_SL = 1.5

trades_du_jour = 0
pertes_consecutives = 0
dernier_jour = datetime.now().day

def session_active():
    h = datetime.now().hour
    return (8 <= h <= 10) or (14 <= h <= 16)

def nom_session():
    h = datetime.now().hour
    if 8 <= h <= 10: return "Londres"
    if 14 <= h <= 16: return "New York"
    return None

def envoyer_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Telegram OK")
        else:
            print(f"Erreur Telegram : {r.text}")
    except Exception as e:
        print(f"Erreur Telegram : {e}")

def get_annonces_eco():
    try:
        url = "https://www.forexfactory.com/calendar.php"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        html = r.text
        mots_critiques = ["NFP", "Non-Farm", "FOMC", "Fed Rate", "ECB Rate", "CPI", "GDP", "Interest Rate", "Inflation", "Employment"]
        for mot in mots_critiques:
            if mot.lower() in html.lower():
                today = datetime.now().strftime("%b %d")
                if today in html:
                    print(f"  Annonce detectee : {mot}")
                    return True, mot
        print("  Aucune annonce HIGH IMPACT")
        return False, None
    except Exception as e:
        print(f"  Calendrier : {e}")
        h = datetime.now().hour
        m = datetime.now().minute
        if datetime.now().weekday() == 4 and h == 14 and 25 <= m <= 45:
            return True, "NFP possible"
        return False, None

def get_news_fxstreet(paire):
    try:
        mots_paire = {
            "EUR/USD": ["EUR", "EURO", "ECB", "EURUSD"],
            "BTC/USD": ["BTC", "BITCOIN", "CRYPTO"],
            "XAU/USD": ["GOLD", "XAU", "BULLION"]
        }
        mots_bull = ["bullish", "rally", "surge", "gains", "rises", "strong", "buy", "upside", "breakout", "support"]
        mots_bear = ["bearish", "drop", "falls", "decline", "weak", "sell", "downside", "breakdown", "resistance", "pressure"]
        url = "https://www.fxstreet.com/rss/news"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ElementTree.fromstring(r.content)
        score_bull, score_bear = 0, 0
        news_titre = None
        mots_cles = mots_paire.get(paire, [])
        for item in root.findall(".//item")[:20]:
            titre = (item.findtext("title", "") or "").upper()
            desc = (item.findtext("description", "") or "").lower()
            if any(m in titre for m in mots_cles):
                if not news_titre:
                    news_titre = item.findtext("title", "")
                for mot in mots_bull:
                    if mot in desc: score_bull += 1
                for mot in mots_bear:
                    if mot in desc: score_bear += 1
        if score_bull > score_bear and score_bull >= 2:
            return "BULLISH", news_titre
        elif score_bear > score_bull and score_bear >= 2:
            return "BEARISH", news_titre
        return "NEUTRE", news_titre
    except Exception as e:
        print(f"  FXStreet : {e}")
        return "NEUTRE", None

def get_candles(paire, interval="15min", limit=100):
    if "BTC" in paire:
        url = f"https://www.alphavantage.co/query?function=DIGITAL_CURRENCY_INTRADAY&symbol=BTC&market=USD&apikey={API_KEY}"
        try:
            r = requests.get(url, timeout=15)
            data = r.json()
            series = data.get("Time Series Crypto (5min)", {})
            if not series: return None
            candles = []
            for v in list(series.values())[:limit]:
                candles.append({
                    "open": float(v.get("1. open", 0)),
                    "high": float(v.get("2. high", 0)),
                    "low": float(v.get("3. low", 0)),
                    "close": float(v.get("4. close", 0)),
                    "volume": float(v.get("5. volume", 1)),
                })
            return candles
        except Exception as e:
            print(f"Erreur BTC : {e}")
            return None

    if "XAU" in paire:
        url = f"https://www.alphavantage.co/query?function=TIME_SERIES_INTRADAY&symbol=XAUUSD&interval={interval}&apikey={API_KEY}&outputsize=compact"
        key = f"Time Series ({interval})"
    else:
        fc, tc = paire.replace("/", " ").split()
        url = f"https://www.alphavantage.co/query?function=FX_INTRADAY&from_symbol={fc}&to_symbol={tc}&interval={interval}&apikey={API_KEY}&outputsize=compact"
        key = f"Time Series FX ({interval})"

    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        series = data.get(key, {})
        if not series: return None
        candles = []
        for v in list(series.values())[:limit]:
            candles.append({
                "open": float(v["1. open"]),
                "high": float(v["2. high"]),
                "low": float(v["3. low"]),
                "close": float(v["4. close"]),
                "volume": float(v.get("5. volume", 1)),
            })
        return candles
    except Exception as e:
        print(f"Erreur {paire} : {e}")
        return None

def calc_ema(prices, period):
    if len(prices) < period: return None
    rev = list(reversed(prices))
    k = 2 / (period + 1)
    val = sum(rev[:period]) / period
    for p in rev[period:]:
        val = p * k + val * (1 - k)
    return round(val, 6)

def calc_rsi(closes, period=7):
    if len(closes) < period + 1: return None
    gains, losses = [], []
    for i in range(period):
        diff = closes[i] - closes[i + 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0: return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def calc_macd(closes, fast=5, slow=13, signal=5):
    if len(closes) < slow + signal: return None, None, None
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    if not ema_fast or not ema_slow: return None, None, None
    macd_line = round(ema_fast - ema_slow, 6)
    macd_series = []
    for i in range(signal + 2):
        ef = calc_ema(closes[i:], fast)
        es = calc_ema(closes[i:], slow)
        if ef and es: macd_series.append(ef - es)
    if len(macd_series) < signal: return macd_line, None, None
    sig_line = calc_ema(macd_series, signal)
    histo = round(macd_line - sig_line, 6) if sig_line else None
    return macd_line, sig_line, histo

def calc_bollinger(closes, period=20):
    if len(closes) < period: return None, None, None
    subset = closes[:period]
    mean = sum(subset) / period
    std = (sum((x - mean) ** 2 for x in subset) / period) ** 0.5
    return round(mean + 2 * std, 6), round(mean, 6), round(mean - 2 * std, 6)

def calc_vwap(candles):
    if not candles: return None
    total_pv, total_v = 0, 0
    for c in candles:
        typical = (c["high"] + c["low"] + c["close"]) / 3
        vol = max(c["volume"], 1)
        total_pv += typical * vol
        total_v += vol
    return round(total_pv / total_v, 6) if total_v else None

def calc_atr(candles, period=14):
    if len(candles) < period + 1: return None
    trs = []
    for i in range(period):
        c, prev = candles[i], candles[i + 1]
        tr = max(c["high"] - c["low"], abs(c["high"] - prev["close"]), abs(c["low"] - prev["close"]))
        trs.append(tr)
    return round(sum(trs) / period, 6)

def score_technique(candles):
    if not candles or len(candles) < 30: return 0, 0, [], []
    closes = [c["close"] for c in candles]
    prix = closes[0]
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50) if len(closes) >= 50 else None
    rsi7 = calc_rsi(closes, 7)
    macd_l, macd_s, macd_h = calc_macd(closes, 5, 13, 5)
    vwap = calc_vwap(candles)
    bb_up, bb_mid, bb_low = calc_bollinger(closes)
    sb, ss, rb, rs = 0, 0, [], []
    if ema9 and ema21:
        if ema9 > ema21: sb += 1; rb.append("OK EMA9 > EMA21")
        else: ss += 1; rs.append("OK EMA9 < EMA21")
    if ema21 and ema50:
        if ema21 > ema50: sb += 1; rb.append("OK EMA21 > EMA50")
        else: ss += 1; rs.append("OK EMA21 < EMA50")
    if rsi7:
        if rsi7 < 40: sb += 1; rb.append(f"OK RSI survendu ({rsi7})")
        elif 40 <= rsi7 <= 55: sb += 1; rb.append(f"OK RSI neutre-haussier ({rsi7})")
        if rsi7 > 60: ss += 1; rs.append(f"OK RSI surachete ({rsi7})")
        elif 45 <= rsi7 <= 60: ss += 1; rs.append(f"OK RSI neutre-baissier ({rsi7})")
    if macd_l and macd_s:
        if macd_l > macd_s: sb += 1; rb.append("OK MACD hausse")
        else: ss += 1; rs.append("OK MACD baisse")
    if macd_h:
        if macd_h > 0: sb += 1; rb.append("OK Histo MACD positif")
        else: ss += 1; rs.append("OK Histo MACD negatif")
    if vwap:
        if prix > vwap: sb += 1; rb.append(f"OK Prix > VWAP ({round(vwap,5)})")
        else: ss += 1; rs.append(f"OK Prix < VWAP ({round(vwap,5)})")
    if bb_up and bb_low:
        if prix <= bb_low: sb += 1; rb.append("OK Prix bande basse Bollinger")
        if prix >= bb_up: ss += 1; rs.append("OK Prix bande haute Bollinger")
    return sb, ss, rb, rs

def score_orderflow(candles):
    if not candles or len(candles) < 10: return 0, 0, [], []
    sb, ss, rb, rs = 0, 0, [], []
    cvd = sum(c["volume"] if c["close"] > c["open"] else -c["volume"] for c in candles[:10])
    if cvd > 0: sb += 1; rb.append("OK CVD positif")
    else: ss += 1; rs.append("OK CVD negatif")
    c0 = candles[0]
    body = abs(c0["close"] - c0["open"])
    wick = (c0["high"] - c0["low"]) - body
    avg_vol = sum(c["volume"] for c in candles[1:5]) / 4
    if wick > body * 2 and c0["volume"] > avg_vol * 1.5:
        if c0["close"] > c0["open"]: sb += 1; rb.append("OK Absorption haussiere")
        else: ss += 1; rs.append("OK Absorption baissiere")
    for i in range(1, len(candles) - 1):
        prev, nxt = candles[i + 1], candles[i - 1]
        if nxt["low"] > prev["high"]: sb += 1; rb.append("OK Imbalance haussiere"); break
        if nxt["high"] < prev["low"]: ss += 1; rs.append("OK Imbalance baissiere"); break
    prix = candles[0]["close"]
    zone_demande = min(c["low"] for c in candles[:20])
    zone_offre = max(c["high"] for c in candles[:20])
    marge = (zone_offre - zone_demande) * 0.15
    if prix <= zone_demande + marge: sb += 1; rb.append(f"OK Zone demande ({round(zone_demande,5)})")
    if prix >= zone_offre - marge: ss += 1; rs.append(f"OK Zone offre ({round(zone_offre,5)})")
    prix_volumes = {}
    for c in candles[:20]:
        pr = round((c["high"] + c["low"]) / 2, 4)
        prix_volumes[pr] = prix_volumes.get(pr, 0) + c["volume"]
    poc = max(prix_volumes, key=prix_volumes.get)
    if prix > poc: sb += 1; rb.append(f"OK Prix > POC ({poc})")
    else: ss += 1; rs.append(f"OK Prix < POC ({poc})")
    return sb, ss, rb, rs

def contexte_htf(paire):
    candles = get_candles(paire, "60min", 50)
    if not candles or len(candles) < 10: return "NEUTRE"
    closes = [c["close"] for c in candles]
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50) if len(closes) >= 50 else None
    prix = closes[0]
    if ema21 and ema50:
        if prix > ema21 > ema50: return "BULLISH"
        if prix < ema21 < ema50: return "BEARISH"
    return "NEUTRE"

def analyser(paire, candles, news_sentiment, news_titre, annonce_eco, nom_annonce):
    global trades_du_jour, pertes_consecutives
    if trades_du_jour >= MAX_TRADES_JOUR: return None
    if pertes_consecutives >= 2: return None
    if not session_active(): return None
    if not candles or len(candles) < 30: return None
    if annonce_eco:
        envoyer_telegram(f"ANNONCE ECO : {nom_annonce}\nAucun signal par precaution.")
        return None
    prix = candles[0]["close"]
    atr = calc_atr(candles)
    session = nom_session()
    tendance_htf = contexte_htf(paire)
    time.sleep(12)
    sc_tech_b, sc_tech_s, rb_tech, rs_tech = score_technique(candles)
    sc_of_b, sc_of_s, rb_of, rs_of = score_orderflow(candles)
    bonus_b = 1 if news_sentiment == "BULLISH" else 0
    bonus_s = 1 if news_sentiment == "BEARISH" else 0
    total_b = sc_tech_b + sc_of_b + bonus_b
    total_s = sc_tech_s + sc_of_s + bonus_s
    signal = None
    if tendance_htf != "BEARISH" and sc_tech_b >= 4 and sc_of_b >= 3 and total_b > total_s:
        signal = "BUY"
    elif tendance_htf != "BULLISH" and sc_tech_s >= 4 and sc_of_s >= 3 and total_s > total_b:
        signal = "SELL"
    if not signal: return None
    is_gold = "XAU" in paire
    is_btc = "BTC" in paire
    pip = 1.0 if is_btc else (0.01 if is_gold else 0.0001)
    sl_dist = atr * ATR_MULTIPLIER_SL if atr else pip * (500 if is_btc else 200 if is_gold else 20)
    if signal == "BUY":
        sl = round(prix - sl_dist, 5)
        tp1 = round(prix + sl_dist * 1.5, 5)
        tp2 = round(prix + sl_dist * 2.5, 5)
        tp3 = round(prix + sl_dist * 4.0, 5)
    else:
        sl = round(prix + sl_dist, 5)
        tp1 = round(prix - sl_dist * 1.5, 5)
        tp2 = round(prix - sl_dist * 2.5, 5)
        tp3 = round(prix - sl_dist * 4.0, 5)
    score_total = total_b if signal == "BUY" else total_s
    qualite = "FORT" if score_total >= 10 else ("MOYEN" if score_total >= 7 else "STANDARD")
    emoji = "ACHAT" if signal == "BUY" else "VENTE"
    tendance_txt = {"BULLISH": "Haussiere", "BEARISH": "Baissiere", "NEUTRE": "Neutre"}[tendance_htf]
    rb = rb_tech + rb_of if signal == "BUY" else rs_tech + rs_of
    news_section = ""
    if news_titre:
        news_section = f"\n\nNEWS : {news_titre[:80]}"
    message = (
        f"{emoji} - {paire}\n"
        f"Direction : {signal}\n"
        f"Qualite : {qualite} ({score_total}/13)\n"
        f"Session : {session}\n"
        f"Tendance HTF : {tendance_txt}\n"
        f"News : {news_sentiment}\n\n"
        f"Entree : {prix}\n"
        f"Stop Loss : {sl}\n"
        f"TP1 (40%) : {tp1}\n"
        f"TP2 (35%) : {tp2}\n"
        f"TP3 (25%) : {tp3}\n\n"
        f"Confirmations :\n" + "\n".join(rb[:8]) +
        news_section +
        f"\n\n{datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    trades_du_jour += 1
    return message

def reset_si_nouveau_jour():
    global trades_du_jour, pertes_consecutives, dernier_jour
    if datetime.now().day != dernier_jour:
        trades_du_jour = 0
        pertes_consecutives = 0
        dernier_jour = datetime.now().day
        envoyer_telegram("Nouveau jour - compteurs remis a zero.")

def main():
    print("Bot demarre !")
    print(f"Paires : {', '.join(PAIRES)}")
    envoyer_telegram(
        "Bot Pro demarre !\n"
        f"Paires : {', '.join(PAIRES)}\n"
        "Sources : FXStreet + ForexFactory + Technique + OrderFlow\n"
        "Sessions : Londres 8h-10h / NY 14h-16h\n"
        f"Risque : {RISK_PAR_TRADE}% / trade | Max {MAX_TRADES_JOUR}/jour\n"
        "Sortie : 40% TP1 / 35% TP2 / 25% TP3"
    )
    while True:
        reset_si_nouveau_jour()
        if not session_active():
            h = datetime.now().hour
            if h < 8: attente = (8 - h) * 3600
            elif 10 < h < 14: attente = (14 - h) * 3600
            else: attente = (32 - h) * 3600
            print(f"[{datetime.now().strftime('%H:%M')}] Hors session - attente...")
            time.sleep(min(attente, 1800))
            continue
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Lecture FXStreet & ForexFactory...")
        annonce_eco, nom_annonce = get_annonces_eco()
        time.sleep(5)
        for paire in PAIRES:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Analyse {paire}...")
            news_sentiment, news_titre = get_news_fxstreet(paire)
            print(f"  News : {news_sentiment}")
            time.sleep(5)
            candles = get_candles(paire, "15min", 100)
            signal = analyser(paire, candles, news_sentiment, news_titre, annonce_eco, nom_annonce)
            if signal:
                envoyer_telegram(signal)
            else:
                print(f"  Pas de signal sur {paire}")
            time.sleep(15)
        print(f"\nProchaine analyse dans 15 min...")
        time.sleep(900)

if __name__ == "__main__":
    main()
