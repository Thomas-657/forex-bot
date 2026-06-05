import requests
import time
import re
from datetime import datetime
from xml.etree import ElementTree

TELEGRAM_TOKEN = "8690688254:AAHYhv2u3kufZob" + "-" + "yMFICeq7feEUj9CEz2E"
CHAT_ID = "916618328"
TD_API_KEY = "a44c800934c444a38b1c9eef1033d294"

PAIRES = ["EUR/USD", "BTC/USD", "XAU/USD"]
RISK_PAR_TRADE = 1.0
MAX_TRADES_JOUR = 5
ATR_MULTIPLIER_SL = 1.5

trades_du_jour = 0
pertes_consecutives = 0
dernier_jour = datetime.now().day

def session_active():
    return True

def nom_session():
    h = datetime.now().hour
    if 1 <= h <= 7: return "Sydney/Tokyo"
    if 8 <= h <= 10: return "Londres"
    if 11 <= h <= 13: return "Londres/NY"
    if 14 <= h <= 16: return "New York"
    if 17 <= h <= 23: return "New York/Soir"
    return "Nuit"

def session_gold_optimale():
    h = datetime.now().hour
    return (8 <= h <= 10) or (14 <= h <= 16)

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

# Annonces HAUSSIÈRES pour le Gold
ANNONCES_GOLD_BULL = {
    "CPI": "Inflation haute = Gold monte (valeur refuge)",
    "Inflation": "Inflation haute = Gold monte (valeur refuge)",
    "NFP": "NFP faible = Fed dovish = Gold monte",
    "Non-Farm": "NFP faible = Fed dovish = Gold monte",
    "Unemployment": "Chomage haut = Fed dovish = Gold monte",
    "Geopolitical": "Tension geo = Gold monte (valeur refuge)",
    "War": "Tension geo = Gold monte (valeur refuge)",
    "Crisis": "Crise = Gold monte (valeur refuge)",
}

# Annonces BAISSIÈRES pour le Gold
ANNONCES_GOLD_BEAR = {
    "Fed Rate": "Hausse des taux = Dollar fort = Gold baisse",
    "FOMC": "Fed hawkish = Dollar fort = Gold baisse",
    "Rate Hike": "Hausse des taux = Dollar fort = Gold baisse",
    "GDP": "PIB fort = Economie solide = Gold baisse",
    "Strong Dollar": "Dollar fort = Gold baisse",
    "ISM": "ISM fort = Economie solide = Gold baisse",
}

def get_annonces_eco():
    try:
        url = "https://www.forexfactory.com/calendar.php"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        html = r.text
        today = datetime.now().strftime("%b %d")

        # Verifier si annonce HIGH IMPACT aujourd hui
        mots_critiques = ["NFP", "Non-Farm", "FOMC", "Fed Rate", "ECB Rate", "CPI", "GDP", "Interest Rate", "Inflation", "Employment"]
        annonce_trouvee = False
        nom_annonce = None

        for mot in mots_critiques:
            if mot.lower() in html.lower() and today in html:
                annonce_trouvee = True
                nom_annonce = mot
                break

        # Detecter impact sur le Gold
        impact_gold = "NEUTRE"
        explication_gold = ""

        for mot, explication in ANNONCES_GOLD_BULL.items():
            if mot.lower() in html.lower() and today in html:
                impact_gold = "BULLISH"
                explication_gold = explication
                break

        for mot, explication in ANNONCES_GOLD_BEAR.items():
            if mot.lower() in html.lower() and today in html:
                impact_gold = "BEARISH"
                explication_gold = explication
                break

        # Envoyer alerte Gold si impact detecte
        if impact_gold != "NEUTRE":
            emoji = "GOLD HAUSSIER" if impact_gold == "BULLISH" else "GOLD BAISSIER"
            direction = "BUY" if impact_gold == "BULLISH" else "SELL"
            msg = (
                "ALERTE GOLD\n"
                "Annonce : " + str(nom_annonce or "Evenement macro") + "\n"
                "Impact : " + emoji + "\n"
                "Raison : " + explication_gold + "\n"
                "Surveille un signal " + direction + " sur XAU/USD"
            )
            envoyer_telegram(msg)

        if annonce_trouvee:
            print(f"  Annonce detectee : {nom_annonce} | Impact Gold : {impact_gold}")
        else:
            print(f"  Aucune annonce HIGH IMPACT | Impact Gold : {impact_gold}")

        return annonce_trouvee, nom_annonce, impact_gold

    except Exception as e:
        print(f"  Calendrier : {e}")
        h = datetime.now().hour
        m = datetime.now().minute
        if datetime.now().weekday() == 4 and h == 14 and 25 <= m <= 45:
            return True, "NFP possible", "NEUTRE"
        return False, None, "NEUTRE"

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

def get_candles(paire, interval="15min", limit=50):
    symboles = {
        "EUR/USD": "EUR/USD",
        "BTC/USD": "BTC/USD",
        "XAU/USD": "XAU/USD"
    }
    symbole = symboles.get(paire, paire)
    intervalles = {
        "15min": "15min",
        "60min": "1h"
    }
    intervalle = intervalles.get(interval, "15min")

    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symbole}"
        f"&interval={intervalle}"
        f"&outputsize={limit}"
        f"&apikey={TD_API_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            print(f"  Twelve Data erreur : {data.get('message')}")
            return None
        values = data.get("values", [])
        if not values:
            return None
        candles = []
        for v in values:
            candles.append({
                "open":   float(v["open"]),
                "high":   float(v["high"]),
                "low":    float(v["low"]),
                "close":  float(v["close"]),
                "volume": float(v.get("volume", 1) or 1),
            })
        return candles
    except Exception as e:
        print(f"Erreur Twelve Data {paire} : {e}")
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

def calc_rsi_divergence(closes, period=7):
    if len(closes) < period * 2 + 2: return None
    rsi_recent = calc_rsi(closes[:period+1], period)
    rsi_ancien = calc_rsi(closes[period:period*2+1], period)
    if rsi_recent is None or rsi_ancien is None: return None
    prix_recent = closes[0]
    prix_ancien = closes[period]
    if prix_recent < prix_ancien and rsi_recent > rsi_ancien: return "BULL_DIV"
    if prix_recent > prix_ancien and rsi_recent < rsi_ancien: return "BEAR_DIV"
    return None

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

def detecter_niveaux_psychologiques(prix):
    niveau_rond = round(prix / 50) * 50
    distance = abs(prix - niveau_rond)
    if distance <= 2: return True, niveau_rond
    return False, None

def detecter_structure(candles):
    if len(candles) < 6: return "NEUTRE"
    highs = [c["high"] for c in candles[:6]]
    lows = [c["low"] for c in candles[:6]]
    if highs[0] > highs[2] and lows[0] > lows[2]: return "BULLISH"
    if highs[0] < highs[2] and lows[0] < lows[2]: return "BEARISH"
    return "NEUTRE"

def detecter_order_block(candles):
    if len(candles) < 5: return None, None
    ob_bull = ob_bear = None
    for i in range(1, len(candles) - 2):
        c, apres = candles[i], candles[i - 1]
        body = abs(c["close"] - c["open"])
        body_apres = abs(apres["close"] - apres["open"])
        if c["close"] < c["open"] and apres["close"] > apres["open"] and body_apres > body * 1.5:
            ob_bull = (c["low"], c["high"]); break
        if c["close"] > c["open"] and apres["close"] < apres["open"] and body_apres > body * 1.5:
            ob_bear = (c["low"], c["high"]); break
    return ob_bull, ob_bear

def detecter_fvg(candles):
    if len(candles) < 3: return None, None
    fvg_bull = fvg_bear = None
    for i in range(1, len(candles) - 1):
        prev, nxt = candles[i + 1], candles[i - 1]
        if nxt["low"] > prev["high"]: fvg_bull = (prev["high"], nxt["low"])
        if nxt["high"] < prev["low"]: fvg_bear = (nxt["high"], prev["low"])
    return fvg_bull, fvg_bear

def detecter_choch(candles):
    if len(candles) < 10: return None
    recent_high = max(c["high"] for c in candles[1:10])
    recent_low = min(c["low"] for c in candles[1:10])
    last = candles[0]
    if last["high"] > recent_high and last["close"] < recent_high: return "BEAR_CHOCH"
    if last["low"] < recent_low and last["close"] > recent_low: return "BULL_CHOCH"
    return None

def score_technique(candles, paire=""):
    if not candles or len(candles) < 30: return 0, 0, [], []
    closes = [c["close"] for c in candles]
    prix = closes[0]
    ema9 = calc_ema(closes, 9)
    ema21 = calc_ema(closes, 21)
    ema50 = calc_ema(closes, 50) if len(closes) >= 50 else None
    rsi7 = calc_rsi(closes, 7)
    rsi_div = calc_rsi_divergence(closes, 7)
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
    if rsi_div == "BULL_DIV": sb += 2; rb.append("OK Divergence RSI haussiere")
    if rsi_div == "BEAR_DIV": ss += 2; rs.append("OK Divergence RSI baissiere")
    if macd_l and macd_s:
        if macd_l > macd_s: sb += 1; rb.append("OK MACD hausse")
        else: ss += 1; rs.append("OK MACD baisse")
    if macd_h:
        if macd_h > 0: sb += 1; rb.append("OK Histo MACD positif")
        else: ss += 1; rs.append("OK Histo MACD negatif")
    if vwap:
        if prix > vwap: sb += 1; rb.append(f"OK Prix > VWAP ({round(vwap,2)})")
        else: ss += 1; rs.append(f"OK Prix < VWAP ({round(vwap,2)})")
    if bb_up and bb_low:
        if prix <= bb_low: sb += 1; rb.append("OK Prix bande basse Bollinger")
        if prix >= bb_up: ss += 1; rs.append("OK Prix bande haute Bollinger")
    if "XAU" in paire:
        niveau_rond, niveau = detecter_niveaux_psychologiques(prix)
        if niveau_rond:
            sb += 1; rb.append(f"OK Niveau psychologique Gold ({niveau})")
            ss += 1; rs.append(f"OK Niveau psychologique Gold ({niveau})")
        if session_gold_optimale():
            sb += 1; rb.append("OK Session Gold optimale")
            ss += 1; rs.append("OK Session Gold optimale")
    return sb, ss, rb, rs

def score_smc(candles, paire=""):
    if not candles or len(candles) < 10: return 0, 0, [], []
    sb, ss, rb, rs = 0, 0, [], []
    prix = candles[0]["close"]
    structure = detecter_structure(candles)
    if structure == "BULLISH": sb += 2; rb.append("OK Structure haussiere (BOS)")
    if structure == "BEARISH": ss += 2; rs.append("OK Structure baissiere (BOS)")
    ob_bull, ob_bear = detecter_order_block(candles)
    if ob_bull and ob_bull[0] <= prix <= ob_bull[1]:
        sb += 2; rb.append(f"OK Order Block haussier ({round(ob_bull[0],2)}-{round(ob_bull[1],2)})")
    if ob_bear and ob_bear[0] <= prix <= ob_bear[1]:
        ss += 2; rs.append(f"OK Order Block baissier ({round(ob_bear[0],2)}-{round(ob_bear[1],2)})")
    fvg_bull, fvg_bear = detecter_fvg(candles)
    if fvg_bull and fvg_bull[0] <= prix <= fvg_bull[1]:
        sb += 2; rb.append(f"OK FVG haussier ({round(fvg_bull[0],2)}-{round(fvg_bull[1],2)})")
    if fvg_bear and fvg_bear[0] <= prix <= fvg_bear[1]:
        ss += 2; rs.append(f"OK FVG baissier ({round(fvg_bear[0],2)}-{round(fvg_bear[1],2)})")
    choch = detecter_choch(candles)
    if choch == "BULL_CHOCH": sb += 2; rb.append("OK ChoCh haussier")
    if choch == "BEAR_CHOCH": ss += 2; rs.append("OK ChoCh baissier")
    cvd = sum(c["volume"] if c["close"] > c["open"] else -c["volume"] for c in candles[:10])
    if cvd > 0: sb += 1; rb.append("OK CVD positif")
    else: ss += 1; rs.append("OK CVD negatif")
    prix_volumes = {}
    for c in candles[:20]:
        pr = round((c["high"] + c["low"]) / 2, 1)
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
    structure = detecter_structure(candles)
    if ema21 and ema50:
        if prix > ema21 > ema50 and structure == "BULLISH": return "BULLISH"
        if prix < ema21 < ema50 and structure == "BEARISH": return "BEARISH"
    return "NEUTRE"

def analyser(paire, candles, news_sentiment, news_titre, annonce_eco, nom_annonce, impact_gold="NEUTRE"):
    global trades_du_jour, pertes_consecutives
    if trades_du_jour >= MAX_TRADES_JOUR: return None
    if pertes_consecutives >= 2: return None
    if not candles or len(candles) < 30: return None
    if annonce_eco:
        envoyer_telegram(f"ANNONCE ECO : {nom_annonce}\nAucun signal par precaution.")
        return None
    prix = candles[0]["close"]
    atr = calc_atr(candles)
    session = nom_session()
    tendance_htf = contexte_htf(paire)
    time.sleep(12)
    sc_tech_b, sc_tech_s, rb_tech, rs_tech = score_technique(candles, paire)
    sc_smc_b, sc_smc_s, rb_smc, rs_smc = score_smc(candles, paire)
    bonus_b = 1 if news_sentiment == "BULLISH" else 0
    bonus_s = 1 if news_sentiment == "BEARISH" else 0
    if "XAU" in paire:
        if impact_gold == "BULLISH": bonus_b += 2; print("  +2 Gold annonce haussiere")
        if impact_gold == "BEARISH": bonus_s += 2; print("  +2 Gold annonce baissiere")
    total_b = sc_tech_b + sc_smc_b + bonus_b
    total_s = sc_tech_s + sc_smc_s + bonus_s
    min_score = 5 if "XAU" in paire else 4
    signal = None
    if tendance_htf != "BEARISH" and sc_tech_b >= min_score and sc_smc_b >= 3 and total_b > total_s:
        signal = "BUY"
    elif tendance_htf != "BULLISH" and sc_tech_s >= min_score and sc_smc_s >= 3 and total_s > total_b:
        signal = "SELL"
    if not signal: return None
    is_gold = "XAU" in paire
    is_btc = "BTC" in paire
    pip = 1.0 if is_btc else (0.01 if is_gold else 0.0001)
    sl_dist = atr * ATR_MULTIPLIER_SL if atr else pip * (500 if is_btc else 200 if is_gold else 20)
    if signal == "BUY":
        sl = round(prix - sl_dist, 2)
        tp1 = round(prix + sl_dist * 1.5, 2)
        tp2 = round(prix + sl_dist * 2.5, 2)
        tp3 = round(prix + sl_dist * 4.0, 2)
    else:
        sl = round(prix + sl_dist, 2)
        tp1 = round(prix - sl_dist * 1.5, 2)
        tp2 = round(prix - sl_dist * 2.5, 2)
        tp3 = round(prix - sl_dist * 4.0, 2)
    score_total = total_b if signal == "BUY" else total_s
    qualite = "FORT" if score_total >= 12 else ("MOYEN" if score_total >= 8 else "STANDARD")
    tendance_txt = {"BULLISH": "Haussiere", "BEARISH": "Baissiere", "NEUTRE": "Neutre"}[tendance_htf]
    rb = (rb_tech + rb_smc) if signal == "BUY" else (rs_tech + rs_smc)
    gold_note = "\nGold : verifier Exocharts pour confirmer l orderflow" if is_gold else ""
    news_section = f"\nNEWS : {news_titre[:80]}" if news_titre else ""
    message = (
        f"{'ACHAT' if signal == 'BUY' else 'VENTE'} - {paire}\n"
        f"Qualite : {qualite} ({score_total}/20)\n"
        f"Session : {session}\n"
        f"Tendance HTF : {tendance_txt}\n"
        f"News : {news_sentiment}\n\n"
        f"Entree : {prix}\n"
        f"Stop Loss : {sl} (1.5x ATR)\n"
        f"TP1 (40%) : {tp1}\n"
        f"TP2 (35%) : {tp2}\n"
        f"TP3 (25%) : {tp3}\n\n"
        f"Confirmations :\n" + "\n".join(rb[:10]) +
        news_section + gold_note +
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
    print("Bot Pro demarre avec Twelve Data !")
    print(f"Paires : {', '.join(PAIRES)}")
    envoyer_telegram(
        "Bot Pro demarre - 24h/24 !\n"
        f"Paires : {', '.join(PAIRES)}\n"
        "Source donnees : Twelve Data (temps reel)\n\n"
        "Analyse Gold optimisee :\n"
        "- Structure BOS + ChoCh\n"
        "- Order Blocks + FVG\n"
        "- Divergence RSI\n"
        "- Niveaux psychologiques\n"
        "- VWAP + ATR\n\n"
        "Sources : FXStreet + ForexFactory\n"
        f"Risque : {RISK_PAR_TRADE}% / trade | Max {MAX_TRADES_JOUR}/jour\n"
        "Sortie : 40% TP1 / 35% TP2 / 25% TP3"
    )
    while True:
        reset_si_nouveau_jour()
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Lecture FXStreet & ForexFactory...")
        annonce_eco, nom_annonce, impact_gold = get_annonces_eco()
        time.sleep(5)
        for paire in PAIRES:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Analyse {paire}...")
            news_sentiment, news_titre = get_news_fxstreet(paire)
            print(f"  News : {news_sentiment}")
            time.sleep(5)
            candles = get_candles(paire, "15min", 50)
            signal = analyser(paire, candles, news_sentiment, news_titre, annonce_eco, nom_annonce, impact_gold)
            if signal:
                envoyer_telegram(signal)
            else:
                print(f"  Pas de signal sur {paire}")
            time.sleep(15)
        print(f"\nProchaine analyse dans 15 min...")
        time.sleep(900)

if __name__ == "__main__":
    main()
