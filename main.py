import requests
import time
import json
from datetime import datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

TZ_PARIS = ZoneInfo('Europe/Paris')

def now():
    return datetime.now(TZ_PARIS)

TELEGRAM_TOKEN = "8690688254:AAHYhv2u3kufZob" + "-" + "yMFICeq7feEUj9CEz2E"
CHAT_ID = "916618328"
TD_API_KEY = "a44c800934c444a38b1c9eef1033d294"

PAIRES = ["BTC/USD", "XAU/USD"]
MAX_TRADES_JOUR = 3
ATR_SL = 1.5

trades_du_jour = 0
dernier_jour = now().day

# ══════════════════════════════
# SESSIONS
# ══════════════════════════════
def nom_session():
    h = now().hour
    if 1  <= h <= 7:  return "Tokyo"
    if 8  <= h <= 10: return "Londres"
    if 11 <= h <= 13: return "Londres/NY"
    if 14 <= h <= 16: return "New York"
    if 17 <= h <= 23: return "New York/Soir"
    return "Nuit"

def session_gold_optimale():
    h = now().hour
    return (8 <= h <= 10) or (14 <= h <= 16)

# ══════════════════════════════
# TELEGRAM
# ══════════════════════════════
def envoyer_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            print(f"[{now().strftime('%H:%M:%S')}] Telegram OK")
        else:
            print(f"Erreur Telegram : {r.text}")
    except Exception as e:
        print(f"Erreur Telegram : {e}")

# ══════════════════════════════
# FOREXFACTORY
# ══════════════════════════════
def get_annonces_eco():
    try:
        url = "https://www.forexfactory.com/calendar.php"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        html = r.text
        today = now().strftime("%b %d")
        annonces_bull = {"CPI": "Inflation haute = Gold monte", "NFP": "NFP faible = Gold monte", "Non-Farm": "NFP faible = Gold monte", "Unemployment": "Chomage haut = Gold monte"}
        annonces_bear = {"Fed Rate": "Hausse taux = Gold baisse", "FOMC": "Fed hawkish = Gold baisse", "GDP": "PIB fort = Gold baisse"}
        annonces_blocantes = ["NFP", "Non-Farm", "FOMC", "Fed Rate", "CPI", "GDP", "Interest Rate"]
        for mot in annonces_blocantes:
            if mot.lower() in html.lower() and today in html:
                impact_gold = "NEUTRE"
                explication = ""
                for m, e in annonces_bull.items():
                    if m.lower() in mot.lower():
                        impact_gold = "BULLISH"; explication = e; break
                for m, e in annonces_bear.items():
                    if m.lower() in mot.lower():
                        impact_gold = "BEARISH"; explication = e; break
                if impact_gold != "NEUTRE":
                    direction = "BUY" if impact_gold == "BULLISH" else "SELL"
                    envoyer_telegram("ALERTE GOLD\nAnnonce : " + mot + "\nImpact : " + impact_gold + "\nRaison : " + explication + "\nSignal possible : " + direction + " XAU/USD")
                return True, mot, impact_gold
        return False, None, "NEUTRE"
    except Exception as e:
        print(f"  ForexFactory : {e}")
        return False, None, "NEUTRE"

# ══════════════════════════════
# FXSTREET
# ══════════════════════════════
def get_sentiment(paire):
    try:
        mots_paire = {"EUR/USD": ["EUR", "EURO", "ECB"], "BTC/USD": ["BTC", "BITCOIN", "CRYPTO"], "XAU/USD": ["GOLD", "XAU", "BULLION"]}
        mots_bull = ["bullish", "rally", "surge", "rises", "strong", "buy", "upside", "support"]
        mots_bear = ["bearish", "drop", "falls", "decline", "weak", "sell", "downside", "resistance"]
        url = "https://www.fxstreet.com/rss/news"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ElementTree.fromstring(r.content)
        sb, ss = 0, 0
        for item in root.findall(".//item")[:20]:
            titre = (item.findtext("title", "") or "").upper()
            desc  = (item.findtext("description", "") or "").lower()
            if any(m in titre for m in mots_paire.get(paire, [])):
                for mot in mots_bull:
                    if mot in desc: sb += 1
                for mot in mots_bear:
                    if mot in desc: ss += 1
        if sb > ss and sb >= 2: return "BULLISH"
        if ss > sb and ss >= 2: return "BEARISH"
        return "NEUTRE"
    except:
        return "NEUTRE"

# ══════════════════════════════
# DXY
# ══════════════════════════════
def get_dxy():
    try:
        url = f"https://api.twelvedata.com/time_series?symbol=DXY&interval=1h&outputsize=10&apikey={TD_API_KEY}"
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("status") == "error": return "NEUTRE", 0
        values = data.get("values", [])
        if not values or len(values) < 5: return "NEUTRE", 0
        prix_actuel = float(values[0]["close"])
        prix_hier   = float(values[4]["close"])
        variation   = round(((prix_actuel - prix_hier) / prix_hier) * 100, 3)
        if variation <= -0.3:   return "BULLISH_GOLD", variation
        elif variation >= 0.3:  return "BEARISH_GOLD", variation
        return "NEUTRE", variation
    except Exception as e:
        print(f"  DXY : {e}")
        return "NEUTRE", 0

# ══════════════════════════════
# COT REPORT
# ══════════════════════════════
def get_cot_gold():
    try:
        url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?market_and_exchange_names=GOLD%20-%20COMMODITY%20EXCHANGE%20INC.&$limit=2&$order=report_date_as_yyyy_mm_dd%20DESC"
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        data = r.json()
        if not data or len(data) < 2: return "NEUTRE", 0, 0
        s1, s2 = data[0], data[1]
        longs  = int(s1.get("noncomm_positions_long_all", 0))
        shorts = int(s1.get("noncomm_positions_short_all", 0))
        longs2 = int(s2.get("noncomm_positions_long_all", 0))
        shorts2= int(s2.get("noncomm_positions_short_all", 0))
        variation_net = (longs - shorts) - (longs2 - shorts2)
        if variation_net > 5000:   return "BULLISH", longs, shorts
        elif variation_net < -5000: return "BEARISH", longs, shorts
        return "NEUTRE", longs, shorts
    except Exception as e:
        print(f"  COT : {e}")
        return "NEUTRE", 0, 0

# ══════════════════════════════
# DONNÉES TWELVE DATA
# ══════════════════════════════
def get_candles(paire, interval="15min", limit=50):
    symboles   = {"EUR/USD": "EUR/USD", "BTC/USD": "BTC/USD", "XAU/USD": "XAU/USD"}
    intervalles= {"15min": "15min", "60min": "1h"}
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={symboles[paire]}"
        f"&interval={intervalles.get(interval, '15min')}"
        f"&outputsize={limit}"
        f"&apikey={TD_API_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            print(f"  Twelve Data : {data.get('message')}")
            return None
        values = data.get("values", [])
        if not values: return None
        return [{"open": float(v["open"]), "high": float(v["high"]), "low": float(v["low"]), "close": float(v["close"]), "volume": float(v.get("volume") or 1)} for v in values]
    except Exception as e:
        print(f"  Twelve Data {paire} : {e}")
        return None

# ══════════════════════════════
# INDICATEURS
# ══════════════════════════════
def ema(prices, period):
    if len(prices) < period: return None
    rev = list(reversed(prices))
    k = 2 / (period + 1)
    val = sum(rev[:period]) / period
    for p in rev[period:]: val = p * k + val * (1 - k)
    return round(val, 5)

def rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains  = [max(closes[i] - closes[i+1], 0) for i in range(period)]
    losses = [max(closes[i+1] - closes[i], 0) for i in range(period)]
    ag = sum(gains) / period
    al = sum(losses) / period
    if al == 0: return 100.0
    return round(100 - 100 / (1 + ag / al), 2)

def rsi_divergence(closes, period=14):
    if len(closes) < period * 2 + 2: return None
    r1 = rsi(closes[:period+1], period)
    r2 = rsi(closes[period:period*2+1], period)
    if not r1 or not r2: return None
    if closes[0] < closes[period] and r1 > r2: return "BULL"
    if closes[0] > closes[period] and r1 < r2: return "BEAR"
    return None

def macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal: return None, None, None
    ef = ema(closes, fast)
    es = ema(closes, slow)
    if not ef or not es: return None, None, None
    macd_line = round(ef - es, 6)
    series = []
    for i in range(signal + 2):
        e1 = ema(closes[i:], fast)
        e2 = ema(closes[i:], slow)
        if e1 and e2: series.append(e1 - e2)
    if len(series) < signal: return macd_line, None, None
    sig = ema(series, signal)
    histo = round(macd_line - sig, 6) if sig else None
    return macd_line, sig, histo

def bollinger(closes, period=20):
    if len(closes) < period: return None, None, None
    subset = closes[:period]
    mean = sum(subset) / period
    std  = (sum((x - mean) ** 2 for x in subset) / period) ** 0.5
    return round(mean + 2 * std, 5), round(mean, 5), round(mean - 2 * std, 5)

def vwap(candles):
    if not candles: return None
    pv = sum((c["high"]+c["low"]+c["close"])/3 * max(c["volume"],1) for c in candles)
    v  = sum(max(c["volume"],1) for c in candles)
    return round(pv / v, 5) if v else None

def atr(candles, period=14):
    if len(candles) < period + 1: return None
    trs = [max(candles[i]["high"] - candles[i]["low"], abs(candles[i]["high"] - candles[i+1]["close"]), abs(candles[i]["low"] - candles[i+1]["close"])) for i in range(period)]
    return round(sum(trs) / period, 5)

# ══════════════════════════════
# SMC / ICT
# ══════════════════════════════
def structure(candles):
    if len(candles) < 6: return "NEUTRE"
    h = [c["high"] for c in candles[:6]]
    l = [c["low"]  for c in candles[:6]]
    if h[0] > h[2] and l[0] > l[2]: return "BULLISH"
    if h[0] < h[2] and l[0] < l[2]: return "BEARISH"
    return "NEUTRE"

def order_block(candles):
    if len(candles) < 5: return None, None
    ob_b = ob_s = None
    for i in range(1, len(candles)-2):
        c, a = candles[i], candles[i-1]
        body = abs(c["close"]-c["open"])
        body_a = abs(a["close"]-a["open"])
        if c["close"] < c["open"] and a["close"] > a["open"] and body_a > body * 1.5:
            ob_b = (c["low"], c["high"]); break
        if c["close"] > c["open"] and a["close"] < a["open"] and body_a > body * 1.5:
            ob_s = (c["low"], c["high"]); break
    return ob_b, ob_s

def fvg(candles):
    if len(candles) < 3: return None, None
    fb = fs = None
    for i in range(1, len(candles)-1):
        p, n = candles[i+1], candles[i-1]
        if n["low"]  > p["high"]: fb = (p["high"], n["low"])
        if n["high"] < p["low"]:  fs = (n["high"], p["low"])
    return fb, fs

def choch(candles):
    if len(candles) < 10: return None
    rh = max(c["high"] for c in candles[1:10])
    rl = min(c["low"]  for c in candles[1:10])
    l  = candles[0]
    if l["high"] > rh and l["close"] < rh: return "BEAR"
    if l["low"]  < rl and l["close"] > rl: return "BULL"
    return None

def liquidites_sessions(candles):
    if not candles or len(candles) < 20: return {}
    res = {}
    prix = candles[0]["close"]
    bougies_asie = candles[4:20]
    if bougies_asie:
        ah = max(c["high"] for c in bougies_asie)
        al = min(c["low"]  for c in bougies_asie)
        res["asian_high"] = round(ah, 2)
        res["asian_low"]  = round(al, 2)
        if candles[0]["high"] > ah and candles[0]["close"] < ah: res["asian_bsl_swept"] = True
        if candles[0]["low"]  < al and candles[0]["close"] > al: res["asian_ssl_swept"] = True
    if now().hour >= 14 and len(candles) >= 8:
        bougies_ldn = candles[:8]
        lh = max(c["high"] for c in bougies_ldn)
        ll = min(c["low"]  for c in bougies_ldn)
        res["london_high"] = round(lh, 2)
        res["london_low"]  = round(ll, 2)
        if candles[0]["high"] > lh and candles[0]["close"] < lh: res["london_bsl_swept"] = True
        if candles[0]["low"]  < ll and candles[0]["close"] > ll: res["london_ssl_swept"] = True
    if len(candles) >= 96:
        hier = candles[48:96]
        res["pdh"] = round(max(c["high"] for c in hier), 2)
        res["pdl"] = round(min(c["low"]  for c in hier), 2)
        if candles[0]["high"] > res["pdh"] and candles[0]["close"] < res["pdh"]: res["pdh_swept"] = True
        if candles[0]["low"]  < res["pdl"] and candles[0]["close"] > res["pdl"]: res["pdl_swept"] = True
    rh = max(c["high"] for c in candles[1:15])
    rl = min(c["low"]  for c in candles[1:15])
    res["bsl"] = round(rh * 1.0002, 2)
    res["ssl"] = round(rl * 0.9998, 2)
    if candles[0]["high"] > rh and candles[0]["close"] < rh: res["bsl_swept"] = True
    if candles[0]["low"]  < rl and candles[0]["close"] > rl: res["ssl_swept"] = True
    return res

def patterns(candles):
    if len(candles) < 5: return {}
    res  = {}
    c    = candles[0]
    prev = candles[1]
    body       = abs(c["close"] - c["open"])
    full_range = c["high"] - c["low"]
    if full_range == 0: return res
    meche_haute = c["high"] - max(c["close"], c["open"])
    meche_basse = min(c["close"], c["open"]) - c["low"]
    if meche_basse > body * 2 and meche_basse > meche_haute * 2:
        res["pin_bar_bull"] = "Pin Bar haussier - rejet fort"
    if meche_haute > body * 2 and meche_haute > meche_basse * 2:
        res["pin_bar_bear"] = "Pin Bar baissier - rejet fort"
    if (c["close"] > c["open"] and prev["close"] < prev["open"] and c["open"] < prev["close"] and c["close"] > prev["open"]):
        res["engulfing_bull"] = "Engulfing haussier - momentum acheteur"
    if (c["close"] < c["open"] and prev["close"] > prev["open"] and c["open"] > prev["close"] and c["close"] < prev["open"]):
        res["engulfing_bear"] = "Engulfing baissier - momentum vendeur"
    if body < full_range * 0.3 and meche_basse > full_range * 0.6 and c["close"] >= c["open"]:
        res["hammer"] = "Hammer haussier - retournement"
    if body < full_range * 0.3 and meche_haute > full_range * 0.6 and c["close"] <= c["open"]:
        res["shooting_star"] = "Shooting Star baissier - retournement"
    if body < full_range * 0.1:
        res["doji"] = "Doji - indecision"
    if c["high"] < prev["high"] and c["low"] > prev["low"]:
        res["inside_bar"] = "Inside Bar - compression - attendre breakout"
    if len(candles) >= 3:
        if all(candles[i]["close"] > candles[i]["open"] for i in range(3)) and candles[0]["close"] > candles[1]["close"] > candles[2]["close"]:
            res["three_soldiers"] = "3 soldats blancs - momentum haussier"
        if all(candles[i]["close"] < candles[i]["open"] for i in range(3)) and candles[0]["close"] < candles[1]["close"] < candles[2]["close"]:
            res["three_crows"] = "3 corbeaux noirs - momentum baissier"
    if len(candles) >= 10:
        highs = [c["high"] for c in candles[:10]]
        lows  = [c["low"]  for c in candles[:10]]
        marge = candles[0]["close"] * 0.001
        for i in range(1, len(highs)):
            if abs(highs[0] - highs[i]) < marge and highs[0] == max(highs):
                res["double_top"] = f"Double Top ({round(highs[0],2)}) - resistance forte"; break
        for i in range(1, len(lows)):
            if abs(lows[0] - lows[i]) < marge and lows[0] == min(lows):
                res["double_bottom"] = f"Double Bottom ({round(lows[0],2)}) - support fort"; break
    return res

# ══════════════════════════════
# ANALYSE H1 — BIAIS DIRECTIONNEL
# ══════════════════════════════
def analyse_h1(paire):
    """
    Étape 1 : On regarde le H1 pour définir le biais
    - Structure de marché H1
    - EMA 21/50/200 sur H1
    - MACD H1 pour le momentum
    - RSI H1 pour les zones extrêmes
    - Order Block H1 comme zone d entrée potentielle
    - FVG H1 comme zone d intérêt
    Retourne le biais + les zones clés à surveiller sur M15
    """
    candles = get_candles(paire, "60min", 50)
    if not candles or len(candles) < 20:
        return {"biais": "NEUTRE", "zones": []}
    time.sleep(5)

    closes = [c["close"] for c in candles]
    prix   = closes[0]

    e21  = ema(closes, 21)
    e50  = ema(closes, 50) if len(closes) >= 50 else None
    e200 = ema(closes, min(200, len(closes)))
    rsi_h1 = rsi(closes, 14)
    macd_l, macd_s, macd_h = macd(closes, 12, 26, 9)
    bb_up, bb_mid, bb_low = bollinger(closes, 20)
    bos_h1 = structure(candles)
    ob_b_h1, ob_s_h1 = order_block(candles)
    fvg_b_h1, fvg_s_h1 = fvg(candles)
    choch_h1 = choch(candles)

    score_bull = 0
    score_bear = 0
    zones_bull = []
    zones_bear = []

    # Structure H1
    if bos_h1 == "BULLISH": score_bull += 2; zones_bull.append("BOS haussier H1")
    if bos_h1 == "BEARISH": score_bear += 2; zones_bear.append("BOS baissier H1")

    # EMA alignment H1
    if e21 and e50 and prix > e21 > e50:
        score_bull += 2; zones_bull.append(f"EMA alignees haussier H1")
    if e21 and e50 and prix < e21 < e50:
        score_bear += 2; zones_bear.append(f"EMA alignees baissier H1")

    # EMA 200 H1 — tendance long terme
    if e200:
        if prix > e200: score_bull += 1; zones_bull.append(f"Prix au dessus EMA200 H1")
        else:           score_bear += 1; zones_bear.append(f"Prix en dessous EMA200 H1")

    # MACD H1
    if macd_l and macd_s:
        if macd_l > macd_s and macd_h and macd_h > 0:
            score_bull += 2; zones_bull.append("MACD haussier H1")
        if macd_l < macd_s and macd_h and macd_h < 0:
            score_bear += 2; zones_bear.append("MACD baissier H1")

    # RSI H1
    if rsi_h1:
        if rsi_h1 < 40:  score_bull += 1; zones_bull.append(f"RSI survendu H1 ({rsi_h1})")
        if rsi_h1 > 60:  score_bear += 1; zones_bear.append(f"RSI surachete H1 ({rsi_h1})")

    # Bollinger H1
    if bb_low and prix <= bb_low: score_bull += 1; zones_bull.append("Prix bande basse Bollinger H1")
    if bb_up  and prix >= bb_up:  score_bear += 1; zones_bear.append("Prix bande haute Bollinger H1")

    # ChoCh H1 — signal fort
    if choch_h1 == "BULL": score_bull += 2; zones_bull.append("ChoCh haussier H1")
    if choch_h1 == "BEAR": score_bear += 2; zones_bear.append("ChoCh baissier H1")

    # Order Block H1 — zone d entree cle
    if ob_b_h1 and ob_b_h1[0] <= prix <= ob_b_h1[1]:
        score_bull += 2; zones_bull.append(f"OB haussier H1 ({round(ob_b_h1[0],2)}-{round(ob_b_h1[1],2)})")
    if ob_s_h1 and ob_s_h1[0] <= prix <= ob_s_h1[1]:
        score_bear += 2; zones_bear.append(f"OB baissier H1 ({round(ob_s_h1[0],2)}-{round(ob_s_h1[1],2)})")

    # FVG H1 — desequilibre a combler
    if fvg_b_h1 and fvg_b_h1[0] <= prix <= fvg_b_h1[1]:
        score_bull += 1; zones_bull.append(f"FVG haussier H1 ({round(fvg_b_h1[0],2)}-{round(fvg_b_h1[1],2)})")
    if fvg_s_h1 and fvg_s_h1[0] <= prix <= fvg_s_h1[1]:
        score_bear += 1; zones_bear.append(f"FVG baissier H1 ({round(fvg_s_h1[0],2)}-{round(fvg_s_h1[1],2)})")

    # Determination du biais H1
    if score_bull >= 4 and score_bull > score_bear:
        return {"biais": "BULLISH", "score": score_bull, "zones": zones_bull, "rsi": rsi_h1, "ob_bull": ob_b_h1, "fvg_bull": fvg_b_h1}
    elif score_bear >= 4 and score_bear > score_bull:
        return {"biais": "BEARISH", "score": score_bear, "zones": zones_bear, "rsi": rsi_h1, "ob_bear": ob_s_h1, "fvg_bear": fvg_s_h1}
    return {"biais": "NEUTRE", "score": 0, "zones": []}

# ══════════════════════════════
# CONFIRMATION M15
# ══════════════════════════════
def confirmer_m15(paire, biais_h1, candles_m15):
    """
    Étape 2 : On confirme le trade sur M15
    Le biais vient du H1, on cherche juste le déclencheur sur M15
    - Structure M15 alignée avec H1
    - VWAP M15
    - RSI 14 M15 + divergence
    - EMA 9/21 M15 pour le déclencheur précis
    - Order Block M15 pour l entrée précise
    - FVG M15 pour l entrée précise
    - Liquidités sessions
    - Patterns de confirmation
    """
    if not candles_m15 or len(candles_m15) < 20:
        return 0, []

    closes = [c["close"] for c in candles_m15]
    prix   = closes[0]

    e9  = ema(closes, 9)
    e21 = ema(closes, 21)
    rsi_m15 = rsi(closes, 14)
    rsi_div = rsi_divergence(closes, 14)
    macd_l, macd_s, macd_h = macd(closes, 12, 26, 9)
    vwap_val = vwap(candles_m15)
    bb_up, bb_mid, bb_low = bollinger(closes, 20)
    bos_m15 = structure(candles_m15)
    ob_b_m15, ob_s_m15 = order_block(candles_m15)
    fvg_b_m15, fvg_s_m15 = fvg(candles_m15)
    choch_m15 = choch(candles_m15)
    liq = liquidites_sessions(candles_m15)
    pat = patterns(candles_m15)

    score = 0
    conf  = []

    if biais_h1 == "BULLISH":
        # Structure M15 alignée
        if bos_m15 == "BULLISH": score += 2; conf.append("Structure haussiere M15 confirmee")
        # EMA M15
        if e9 and e21 and e9 > e21: score += 1; conf.append("EMA9 > EMA21 sur M15")
        # VWAP
        if vwap_val and prix > vwap_val: score += 1; conf.append(f"Prix au dessus VWAP M15")
        # RSI M15
        if rsi_m15:
            if rsi_m15 < 40:  score += 2; conf.append(f"RSI survendu M15 ({rsi_m15})")
            elif rsi_m15 < 55: score += 1; conf.append(f"RSI favorable M15 ({rsi_m15})")
        # Divergence RSI
        if rsi_div == "BULL": score += 3; conf.append("Divergence RSI haussiere M15")
        # MACD M15
        if macd_l and macd_s and macd_l > macd_s:
            score += 1; conf.append("MACD haussier M15")
        if macd_h and macd_h > 0:
            score += 1; conf.append("Histogramme MACD positif M15")
        # Bollinger
        if bb_low and prix <= bb_low: score += 1; conf.append("Prix bande basse Bollinger M15")
        # Order Block M15
        if ob_b_m15 and ob_b_m15[0] <= prix <= ob_b_m15[1]:
            score += 2; conf.append(f"Order Block haussier M15 ({round(ob_b_m15[0],2)}-{round(ob_b_m15[1],2)})")
        # FVG M15
        if fvg_b_m15 and fvg_b_m15[0] <= prix <= fvg_b_m15[1]:
            score += 2; conf.append(f"FVG haussier M15 ({round(fvg_b_m15[0],2)}-{round(fvg_b_m15[1],2)})")
        # ChoCh M15
        if choch_m15 == "BULL": score += 2; conf.append("ChoCh haussier M15")
        # Liquidites sessions
        if liq.get("ssl_swept"):    score += 3; conf.append("SSL sweep M15 - stops chassés")
        if liq.get("asian_ssl_swept"): score += 3; conf.append("Asian SSL sweep - rebond haussier")
        if liq.get("london_ssl_swept"): score += 3; conf.append("London SSL sweep - NY haussier")
        if liq.get("pdl_swept"):    score += 2; conf.append(f"PDL sweepé ({liq.get('pdl')}) - haussier")
        # Patterns
        if "pin_bar_bull" in pat:    score += 2; conf.append(pat["pin_bar_bull"])
        if "engulfing_bull" in pat:  score += 2; conf.append(pat["engulfing_bull"])
        if "hammer" in pat:          score += 2; conf.append(pat["hammer"])
        if "double_bottom" in pat:   score += 2; conf.append(pat["double_bottom"])
        if "three_soldiers" in pat:  score += 1; conf.append(pat["three_soldiers"])
        # Doji = indecision = penalite
        if "doji" in pat: score = max(0, score - 1)

    elif biais_h1 == "BEARISH":
        if bos_m15 == "BEARISH": score += 2; conf.append("Structure baissiere M15 confirmee")
        if e9 and e21 and e9 < e21: score += 1; conf.append("EMA9 < EMA21 sur M15")
        if vwap_val and prix < vwap_val: score += 1; conf.append(f"Prix en dessous VWAP M15")
        if rsi_m15:
            if rsi_m15 > 60:   score += 2; conf.append(f"RSI surachete M15 ({rsi_m15})")
            elif rsi_m15 > 45: score += 1; conf.append(f"RSI favorable M15 ({rsi_m15})")
        if rsi_div == "BEAR": score += 3; conf.append("Divergence RSI baissiere M15")
        if macd_l and macd_s and macd_l < macd_s:
            score += 1; conf.append("MACD baissier M15")
        if macd_h and macd_h < 0:
            score += 1; conf.append("Histogramme MACD negatif M15")
        if bb_up and prix >= bb_up: score += 1; conf.append("Prix bande haute Bollinger M15")
        if ob_s_m15 and ob_s_m15[0] <= prix <= ob_s_m15[1]:
            score += 2; conf.append(f"Order Block baissier M15 ({round(ob_s_m15[0],2)}-{round(ob_s_m15[1],2)})")
        if fvg_s_m15 and fvg_s_m15[0] <= prix <= fvg_s_m15[1]:
            score += 2; conf.append(f"FVG baissier M15 ({round(fvg_s_m15[0],2)}-{round(fvg_s_m15[1],2)})")
        if choch_m15 == "BEAR": score += 2; conf.append("ChoCh baissier M15")
        if liq.get("bsl_swept"):    score += 3; conf.append("BSL sweep M15 - stops chassés")
        if liq.get("asian_bsl_swept"): score += 3; conf.append("Asian BSL sweep - retournement baissier")
        if liq.get("london_bsl_swept"): score += 3; conf.append("London BSL sweep - NY baissier")
        if liq.get("pdh_swept"):    score += 2; conf.append(f"PDH sweepé ({liq.get('pdh')}) - baissier")
        if "pin_bar_bear" in pat:    score += 2; conf.append(pat["pin_bar_bear"])
        if "engulfing_bear" in pat:  score += 2; conf.append(pat["engulfing_bear"])
        if "shooting_star" in pat:   score += 2; conf.append(pat["shooting_star"])
        if "double_top" in pat:      score += 2; conf.append(pat["double_top"])
        if "three_crows" in pat:     score += 1; conf.append(pat["three_crows"])
        if "doji" in pat: score = max(0, score - 1)

    return score, conf

# ══════════════════════════════
# MOTEUR PRINCIPAL
# ══════════════════════════════
def analyser(paire, candles_m15, h1_data, sentiment, annonce_eco, impact_gold, dxy_signal, cot_signal):
    global trades_du_jour
    if trades_du_jour >= MAX_TRADES_JOUR: return None
    if annonce_eco: return None
    if not candles_m15 or len(candles_m15) < 20: return None

    biais = h1_data.get("biais", "NEUTRE")
    if biais == "NEUTRE": return None

    prix    = candles_m15[0]["close"]
    atr_val = atr(candles_m15)
    signal  = "BUY" if biais == "BULLISH" else "SELL"

    # Confirmation M15
    score_m15, conf_m15 = confirmer_m15(paire, biais, candles_m15)
    if score_m15 < 5: return None

    # Contexte H1
    conf_h1 = h1_data.get("zones", [])

    # Bonus fondamental Gold
    conf_extra = []
    bonus = 0
    if "XAU" in paire:
        if dxy_signal == "BULLISH_GOLD" and signal == "BUY":
            bonus += 2; conf_extra.append("DXY en baisse - favorable Gold")
        if dxy_signal == "BEARISH_GOLD" and signal == "SELL":
            bonus += 2; conf_extra.append("DXY en hausse - defavorable Gold")
        if cot_signal == "BULLISH" and signal == "BUY":
            bonus += 2; conf_extra.append("COT : institutionnels acheteurs Gold")
        if cot_signal == "BEARISH" and signal == "SELL":
            bonus += 2; conf_extra.append("COT : institutionnels vendeurs Gold")
        if impact_gold == "BULLISH" and signal == "BUY":
            bonus += 1; conf_extra.append("Annonce macro favorable Gold")
        if impact_gold == "BEARISH" and signal == "SELL":
            bonus += 1; conf_extra.append("Annonce macro defavorable Gold")
        niveau_rond = round(prix / 50) * 50
        if abs(prix - niveau_rond) <= 2:
            bonus += 1; conf_extra.append(f"Niveau psychologique Gold ({niveau_rond})")
        if session_gold_optimale():
            bonus += 1; conf_extra.append("Session Gold optimale Londres/NY")

    if sentiment == "BULLISH" and signal == "BUY":
        bonus += 1; conf_extra.append("Sentiment news haussier")
    if sentiment == "BEARISH" and signal == "SELL":
        bonus += 1; conf_extra.append("Sentiment news baissier")

    score_total = score_m15 + bonus

    # SL / TP
    is_gold = "XAU" in paire
    is_btc  = "BTC" in paire
    pip     = 1.0 if is_btc else (0.01 if is_gold else 0.0001)
    sl_dist = atr_val * ATR_SL if atr_val else pip * (300 if is_btc else 150 if is_gold else 15)

    if signal == "BUY":
        sl  = round(prix - sl_dist, 2)
        tp1 = round(prix + sl_dist * 1.5, 2)
        tp2 = round(prix + sl_dist * 2.5, 2)
        tp3 = round(prix + sl_dist * 4.0, 2)
    else:
        sl  = round(prix + sl_dist, 2)
        tp1 = round(prix - sl_dist * 1.5, 2)
        tp2 = round(prix - sl_dist * 2.5, 2)
        tp3 = round(prix - sl_dist * 4.0, 2)

    if score_total >= 15:   qualite = "FORT"
    elif score_total >= 10: qualite = "BIEN"
    elif score_total >= 7:  qualite = "MOYEN"
    else:                   qualite = "FAIBLE"

    gold_note = "\nVerifier sur Exocharts avant de copier" if is_gold else ""

    # Construction du message
    toutes_conf = conf_h1[:3] + conf_m15[:6] + conf_extra[:3]

    message = (
        f"{'ACHAT' if signal == 'BUY' else 'VENTE'} - {paire}\n"
        f"Qualite : {qualite}\n"
        f"Session : {nom_session()}\n"
        f"Biais H1 : {biais}\n\n"
        f"Entree : {prix}\n"
        f"Stop Loss : {sl}\n"
        f"TP1 (40%) : {tp1}\n"
        f"TP2 (35%) : {tp2}\n"
        f"TP3 (25%) : {tp3}\n\n"
        f"Analyse H1 :\n" +
        "\n".join(f"- {c}" for c in conf_h1[:3]) +
        f"\n\nConfirmation M15 :\n" +
        "\n".join(f"- {c}" for c in conf_m15[:6]) +
        (f"\n\nContexte macro :\n" + "\n".join(f"- {c}" for c in conf_extra) if conf_extra else "") +
        gold_note +
        f"\n\n{now().strftime('%d/%m/%Y %H:%M')}"
    )

    trades_du_jour += 1
    return message

# ══════════════════════════════
# RESET QUOTIDIEN
# ══════════════════════════════
def reset():
    global trades_du_jour, dernier_jour
    if now().day != dernier_jour:
        trades_du_jour = 0
        dernier_jour   = now().day
        envoyer_telegram("Nouveau jour - compteurs remis a zero.")

# ══════════════════════════════
# BOUCLE PRINCIPALE
# ══════════════════════════════
def main():
    print("Bot H1 + M15 demarre !")
    envoyer_telegram(
        "Bot demarre - Analyse H1 + Confirmation M15\n\n"
        "Logique :\n"
        "1. Biais H1 (structure + EMA + MACD + Bollinger)\n"
        "2. Confirmation M15 (SMC + liquidites + patterns)\n\n"
        "Indicateurs H1 :\n"
        "- Structure BOS + ChoCh\n"
        "- EMA 21/50/200\n"
        "- MACD 12/26/9\n"
        "- RSI 14 + Bollinger\n"
        "- Order Block + FVG\n\n"
        "Confirmation M15 :\n"
        "- Structure + EMA 9/21\n"
        "- RSI 14 + Divergence\n"
        "- MACD + VWAP + Bollinger\n"
        "- OB + FVG + ChoCh\n"
        "- Liquidites sessions\n"
        "- Patterns chartistes\n\n"
        "Contexte macro Gold :\n"
        "- DXY + COT + ForexFactory\n"
        "- FXStreet sentiment\n\n"
        f"Max {MAX_TRADES_JOUR} trades/jour | SL 1.5x ATR\n"
        "Sortie : 40% TP1 / 35% TP2 / 25% TP3"
    )

    while True:
        reset()
        print(f"\n[{now().strftime('%H:%M:%S')}] Nouvelle analyse...")

        annonce_eco, nom_annonce, impact_gold = get_annonces_eco()
        time.sleep(5)

        dxy_signal, dxy_var = get_dxy()
        time.sleep(5)

        cot_signal, cot_l, cot_s = get_cot_gold()
        time.sleep(5)

        if dxy_signal == "BULLISH_GOLD" and cot_signal == "BULLISH":
            envoyer_telegram("CONFLUENCE GOLD FORTE\nDXY baisse + COT acheteurs\nBiais semaine : HAUSSIER XAU/USD")
        elif dxy_signal == "BEARISH_GOLD" and cot_signal == "BEARISH":
            envoyer_telegram("CONFLUENCE GOLD FORTE\nDXY monte + COT vendeurs\nBiais semaine : BAISSIER XAU/USD")

        for paire in PAIRES:
            print(f"  [{paire}] Analyse H1...")
            h1_data = analyse_h1(paire)
            print(f"  [{paire}] Biais H1 : {h1_data['biais']}")

            if h1_data["biais"] == "NEUTRE":
                print(f"  [{paire}] Biais neutre - pas de trade")
                time.sleep(5)
                continue

            print(f"  [{paire}] Confirmation M15...")
            candles_m15 = get_candles(paire, "15min", 50)
            time.sleep(5)

            sentiment = get_sentiment(paire)
            time.sleep(3)

            signal = analyser(paire, candles_m15, h1_data, sentiment, annonce_eco, impact_gold, dxy_signal, cot_signal)

            if signal:
                envoyer_telegram(signal)
            else:
                print(f"  [{paire}] Pas de confirmation M15")

            time.sleep(15)

        print("Prochaine analyse dans 15 min...")
        time.sleep(900)

if __name__ == "__main__":
    main()
