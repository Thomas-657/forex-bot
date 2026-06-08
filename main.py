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

PAIRES = ["EUR/USD", "BTC/USD", "XAU/USD"]
MAX_TRADES_JOUR = 3
ATR_SL = 1.5

trades_du_jour = 0
dernier_jour = now().day

# ══════════════════════════════
# SESSIONS
# ══════════════════════════════
def nom_session():
    h = now().hour
    if 8 <= h <= 10: return "Londres"
    if 14 <= h <= 16: return "New York"
    if 1 <= h <= 7: return "Tokyo"
    return "Inter-session"

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
# CALENDRIER ECO (ForexFactory)
# ══════════════════════════════
def get_annonces_eco():
    try:
        url = "https://www.forexfactory.com/calendar.php"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, timeout=15)
        html = r.text
        today = now().strftime("%b %d")
        annonces_bull_gold = {
            "CPI": "Inflation haute = Gold monte",
            "NFP": "NFP faible possible = Gold monte",
            "Non-Farm": "NFP faible possible = Gold monte",
            "Unemployment": "Chomage haut = Fed dovish = Gold monte",
        }
        annonces_bear_gold = {
            "Fed Rate": "Hausse taux = Dollar fort = Gold baisse",
            "FOMC": "Fed hawkish = Dollar fort = Gold baisse",
            "GDP": "PIB fort = Gold baisse",
        }
        annonces_blocantes = ["NFP", "Non-Farm", "FOMC", "Fed Rate", "CPI", "GDP", "Interest Rate"]
        for mot in annonces_blocantes:
            if mot.lower() in html.lower() and today in html:
                impact_gold = "NEUTRE"
                explication = ""
                for m, e in annonces_bull_gold.items():
                    if m.lower() in mot.lower():
                        impact_gold = "BULLISH"
                        explication = e
                        break
                for m, e in annonces_bear_gold.items():
                    if m.lower() in mot.lower():
                        impact_gold = "BEARISH"
                        explication = e
                        break
                if impact_gold != "NEUTRE":
                    direction = "BUY" if impact_gold == "BULLISH" else "SELL"
                    msg = (
                        "ALERTE GOLD - Annonce macro\n"
                        "Annonce : " + mot + "\n"
                        "Impact or : " + impact_gold + "\n"
                        "Raison : " + explication + "\n"
                        "Signal possible : " + direction + " XAU/USD"
                    )
                    envoyer_telegram(msg)
                print(f"  Annonce : {mot} | Impact Gold : {impact_gold}")
                return True, mot, impact_gold
        return False, None, "NEUTRE"
    except Exception as e:
        print(f"  ForexFactory : {e}")
        return False, None, "NEUTRE"

# ══════════════════════════════
# NEWS FXSTREET
# ══════════════════════════════
def get_sentiment(paire):
    try:
        mots_paire = {
            "EUR/USD": ["EUR", "EURO", "ECB"],
            "BTC/USD": ["BTC", "BITCOIN", "CRYPTO"],
            "XAU/USD": ["GOLD", "XAU", "BULLION"]
        }
        mots_bull = ["bullish", "rally", "surge", "rises", "strong", "buy", "upside", "support"]
        mots_bear = ["bearish", "drop", "falls", "decline", "weak", "sell", "downside", "resistance"]
        url = "https://www.fxstreet.com/rss/news"
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        root = ElementTree.fromstring(r.content)
        sb, ss = 0, 0
        mots_cles = mots_paire.get(paire, [])
        for item in root.findall(".//item")[:20]:
            titre = (item.findtext("title", "") or "").upper()
            desc = (item.findtext("description", "") or "").lower()
            if any(m in titre for m in mots_cles):
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
# DXY (Dollar Index) — corrélation inverse Gold
# ══════════════════════════════
def get_dxy():
    """
    Le DXY monte = Dollar fort = Gold baisse
    Le DXY baisse = Dollar faible = Gold monte
    On utilise Twelve Data pour récupérer le DXY
    """
    try:
        url = (
            f"https://api.twelvedata.com/time_series"
            f"?symbol=DXY"
            f"&interval=1h"
            f"&outputsize=10"
            f"&apikey={TD_API_KEY}"
        )
        r = requests.get(url, timeout=15)
        data = r.json()
        if data.get("status") == "error":
            print(f"  DXY : {data.get('message')}")
            return "NEUTRE", 0
        values = data.get("values", [])
        if not values or len(values) < 3:
            return "NEUTRE", 0
        prix_actuel = float(values[0]["close"])
        prix_hier   = float(values[4]["close"]) if len(values) > 4 else prix_actuel
        variation   = round(((prix_actuel - prix_hier) / prix_hier) * 100, 3)
        if variation <= -0.3:
            print(f"  DXY baisse ({variation}%) = BULLISH Gold")
            return "BULLISH_GOLD", variation
        elif variation >= 0.3:
            print(f"  DXY monte ({variation}%) = BEARISH Gold")
            return "BEARISH_GOLD", variation
        else:
            print(f"  DXY neutre ({variation}%)")
            return "NEUTRE", variation
    except Exception as e:
        print(f"  DXY : {e}")
        return "NEUTRE", 0

# ══════════════════════════════
# COT REPORT (CFTC) — positions des gros acteurs
# Publié chaque vendredi, données de la semaine précédente
# ══════════════════════════════
def get_cot_gold():
    """
    Le COT Report montre les positions des Non-Commercials (spéculateurs institutionnels)
    Si les longs augmentent = institutionnels haussiers sur le Gold
    Si les shorts augmentent = institutionnels baissiers sur le Gold
    Source : CFTC (gratuit) via API publique
    """
    try:
        url = "https://publicreporting.cftc.gov/resource/jun7-fc8e.json?market_and_exchange_names=GOLD%20-%20COMMODITY%20EXCHANGE%20INC.&$limit=2&$order=report_date_as_yyyy_mm_dd%20DESC"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, timeout=15, headers=headers)
        data = r.json()
        if not data or len(data) < 2:
            print("  COT : donnees insuffisantes")
            return "NEUTRE", 0, 0
        semaine_recente = data[0]
        semaine_ancienne = data[1]
        longs_recents  = int(semaine_recente.get("noncomm_positions_long_all", 0))
        shorts_recents = int(semaine_recente.get("noncomm_positions_short_all", 0))
        longs_anciens  = int(semaine_ancienne.get("noncomm_positions_long_all", 0))
        shorts_anciens = int(semaine_ancienne.get("noncomm_positions_short_all", 0))
        variation_longs  = longs_recents  - longs_anciens
        variation_shorts = shorts_recents - shorts_anciens
        net_position = longs_recents - shorts_recents
        net_ancien   = longs_anciens - shorts_anciens
        variation_net = net_position - net_ancien
        if variation_net > 5000:
            print(f"  COT : institutionnels ACHETEURS Gold (+{variation_net} nets)")
            return "BULLISH", longs_recents, shorts_recents
        elif variation_net < -5000:
            print(f"  COT : institutionnels VENDEURS Gold ({variation_net} nets)")
            return "BEARISH", longs_recents, shorts_recents
        else:
            print(f"  COT : institutionnels NEUTRES Gold ({variation_net} nets)")
            return "NEUTRE", longs_recents, shorts_recents
    except Exception as e:
        print(f"  COT : {e}")
        return "NEUTRE", 0, 0

# ══════════════════════════════
# DONNÉES TWELVE DATA
# ══════════════════════════════
def get_candles(paire, interval="15min", limit=50):
    symboles = {"EUR/USD": "EUR/USD", "BTC/USD": "BTC/USD", "XAU/USD": "XAU/USD"}
    intervalles = {"15min": "15min", "60min": "1h"}
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
        return [{
            "open":   float(v["open"]),
            "high":   float(v["high"]),
            "low":    float(v["low"]),
            "close":  float(v["close"]),
            "volume": float(v.get("volume") or 1),
        } for v in values]
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
    for p in rev[period:]:
        val = p * k + val * (1 - k)
    return round(val, 5)

def rsi(closes, period=14):
    if len(closes) < period + 1: return None
    gains = [max(closes[i] - closes[i+1], 0) for i in range(period)]
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

def atr(candles, period=14):
    if len(candles) < period + 1: return None
    trs = [max(
        candles[i]["high"] - candles[i]["low"],
        abs(candles[i]["high"] - candles[i+1]["close"]),
        abs(candles[i]["low"]  - candles[i+1]["close"])
    ) for i in range(period)]
    return round(sum(trs) / period, 5)

def vwap(candles):
    if not candles: return None
    pv = sum((c["high"]+c["low"]+c["close"])/3 * max(c["volume"],1) for c in candles)
    v  = sum(max(c["volume"],1) for c in candles)
    return round(pv / v, 5) if v else None

# ══════════════════════════════
# SMC — STRUCTURE + ORDER BLOCK + FVG
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

# ══════════════════════════════
# LIQUIDITÉS ICT
# ══════════════════════════════

def detecter_equal_highs_lows(candles):
    """
    Equal Highs (EQH) = niveaux où le prix a buté plusieurs fois
    Equal Lows (EQL) = niveaux où le prix a rebondi plusieurs fois
    Les institutionnels vont chercher ces niveaux pour chasser les stops
    """
    if len(candles) < 10:
        return None, None

    highs  = [c["high"]  for c in candles[:15]]
    lows   = [c["low"]   for c in candles[:15]]
    prix   = candles[0]["close"]
    marge  = prix * 0.0005  # 0.05% de marge

    # Cherche Equal Highs — stops des vendeurs au-dessus
    eqh = None
    for i in range(len(highs) - 1):
        for j in range(i + 1, len(highs)):
            if abs(highs[i] - highs[j]) <= marge:
                eqh = (highs[i] + highs[j]) / 2
                break

    # Cherche Equal Lows — stops des acheteurs en-dessous
    eql = None
    for i in range(len(lows) - 1):
        for j in range(i + 1, len(lows)):
            if abs(lows[i] - lows[j]) <= marge:
                eql = (lows[i] + lows[j]) / 2
                break

    return round(eqh, 5) if eqh else None, round(eql, 5) if eql else None

def detecter_bsl_ssl(candles):
    """
    BSL (Buy Side Liquidity) = liquidité au-dessus des highs récents
    SSL (Sell Side Liquidity) = liquidité en-dessous des lows récents
    Le prix va chasser ces zones avant de repartir dans la direction opposée
    """
    if len(candles) < 20:
        return None, None, False, False

    prix        = candles[0]["close"]
    recent_high = max(c["high"] for c in candles[1:20])
    recent_low  = min(c["low"]  for c in candles[1:20])

    # BSL = stops des vendeurs au-dessus du dernier high
    bsl = round(recent_high * 1.0002, 5)
    # SSL = stops des acheteurs en-dessous du dernier low
    ssl = round(recent_low  * 0.9998, 5)

    # Est-ce que le prix vient de chasser la BSL ? (sweep haussier -> signal baissier)
    bsl_swept = candles[0]["high"] > recent_high and candles[0]["close"] < recent_high
    # Est-ce que le prix vient de chasser la SSL ? (sweep baissier -> signal haussier)
    ssl_swept = candles[0]["low"]  < recent_low  and candles[0]["close"] > recent_low

    return bsl, ssl, bsl_swept, ssl_swept

def detecter_inducement(candles):
    """
    L inducement ICT = faux signal qui attire les retail traders
    avant que le prix parte dans la vraie direction
    On cherche une mèche qui dépasse un niveau clé puis revient
    """
    if len(candles) < 5:
        return None

    c    = candles[0]
    prev = candles[1]

    # Mèche haute dépasse le high précédent mais close en dessous = inducement baissier
    if c["high"] > prev["high"] and c["close"] < prev["high"]:
        return "BEAR_INDUCEMENT"

    # Mèche basse dépasse le low précédent mais close au-dessus = inducement haussier
    if c["low"] < prev["low"] and c["close"] > prev["low"]:
        return "BULL_INDUCEMENT"

    return None

def detecter_liquidite_sessions(candles):
    """
    Liquidités de sessions ICT :
    - Asian High/Low = niveaux créés pendant la session asiatique
      que Londres et NY vont chasser
    - London High/Low = niveaux créés pendant Londres
      que NY va chasser
    - Midnight Open = niveau d ouverture à minuit UTC
      référence clé ICT pour la journée
    - Opening Range = range des 15 premières minutes de session
      les institutionnels reviennent souvent tester ce range
    """
    if not candles or len(candles) < 30:
        return {}

    h = now().hour
    prix = candles[0]["close"]

    resultats = {}

    # Asian High/Low (1h-7h heure Paris)
    # Londres va souvent chasser ces niveaux pour prendre la liquidité
    bougies_asie = [c for c in candles[4:20]]  # approximation session asie
    if bougies_asie:
        asian_high = max(c["high"] for c in bougies_asie)
        asian_low  = min(c["low"]  for c in bougies_asie)
        resultats["asian_high"] = round(asian_high, 2)
        resultats["asian_low"]  = round(asian_low,  2)

        # Prix au-dessus Asian High = BSL asiatique chassée = potentiel retournement
        if candles[0]["high"] > asian_high and candles[0]["close"] < asian_high:
            resultats["asian_bsl_swept"] = True
        # Prix en-dessous Asian Low = SSL asiatique chassée = potentiel rebond
        if candles[0]["low"] < asian_low and candles[0]["close"] > asian_low:
            resultats["asian_ssl_swept"] = True

    # London High/Low (8h-12h heure Paris)
    # NY va souvent chasser ces niveaux
    if h >= 14:  # On est en session NY, on peut calculer le range Londres
        bougies_londres = candles[:8]  # 8 bougies de 15min = 2h
        if bougies_londres:
            london_high = max(c["high"] for c in bougies_londres)
            london_low  = min(c["low"]  for c in bougies_londres)
            resultats["london_high"] = round(london_high, 2)
            resultats["london_low"]  = round(london_low,  2)

            # NY chasse le London High = signal baissier
            if candles[0]["high"] > london_high and candles[0]["close"] < london_high:
                resultats["london_bsl_swept"] = True
            # NY chasse le London Low = signal haussier
            if candles[0]["low"] < london_low and candles[0]["close"] > london_low:
                resultats["london_ssl_swept"] = True

    # Opening Range (15 premières minutes de la session)
    # Zone clé — les institutionnels reviennent tester
    bougies_open = candles[:2]  # 2 premières bougies de 15min
    if bougies_open:
        or_high = max(c["high"] for c in bougies_open)
        or_low  = min(c["low"]  for c in bougies_open)
        resultats["or_high"] = round(or_high, 2)
        resultats["or_low"]  = round(or_low,  2)

        # Prix entre le range = neutre
        # Prix qui revient sur le range après en être sorti = signal
        if prix < or_low:
            resultats["sous_opening_range"] = True
        elif prix > or_high:
            resultats["dessus_opening_range"] = True

    # Previous Day High/Low — niveaux clés quotidiens
    # Les institutionnels chassent souvent ces niveaux
    if len(candles) >= 96:  # 96 bougies de 15min = 24h
        bougies_hier = candles[48:96]  # bougies d hier approximation
        pdh = max(c["high"] for c in bougies_hier)
        pdl = min(c["low"]  for c in bougies_hier)
        resultats["pdh"] = round(pdh, 2)
        resultats["pdl"] = round(pdl, 2)

        # Prix qui chasse le PDH puis revient = signal baissier
        if candles[0]["high"] > pdh and candles[0]["close"] < pdh:
            resultats["pdh_swept"] = True
        # Prix qui chasse le PDL puis revient = signal haussier
        if candles[0]["low"] < pdl and candles[0]["close"] > pdl:
            resultats["pdl_swept"] = True

    return resultats

def detecter_void_liquidity(candles):
    """
    Void = zone où le prix est passé très vite sans volume
    Le prix reviendra combler cette zone (comme un FVG mais plus large)
    """
    if len(candles) < 5:
        return None, None

    void_bull = void_bear = None

    for i in range(1, len(candles) - 1):
        c     = candles[i]
        avant = candles[i + 1]
        apres = candles[i - 1]

        # Grande bougie haussière = void baissier potentiel (le prix reviendra)
        body = abs(c["close"] - c["open"])
        if c["close"] > c["open"] and body > (c["high"] - c["low"]) * 0.8:
            void_bull = (c["open"], c["close"])

        # Grande bougie baissière = void haussier potentiel
        if c["close"] < c["open"] and body > (c["high"] - c["low"]) * 0.8:
            void_bear = (c["close"], c["open"])

    return void_bull, void_bear

def choch(candles):
    if len(candles) < 10: return None
    rh = max(c["high"] for c in candles[1:10])
    rl = min(c["low"]  for c in candles[1:10])
    l = candles[0]
    if l["high"] > rh and l["close"] < rh: return "BEAR"
    if l["low"]  < rl and l["close"] > rl: return "BULL"
    return None

# ══════════════════════════════
# BIAIS HTF (1H)
# ══════════════════════════════
def biais_htf(paire):
    c = get_candles(paire, "60min", 50)
    if not c or len(c) < 20: return "NEUTRE"
    closes = [x["close"] for x in c]
    e50  = ema(closes, 50) if len(closes) >= 50 else ema(closes, len(closes)//2)
    e200 = ema(closes, min(200, len(closes)))
    prix = closes[0]
    s = structure(c)
    if e50 and prix > e50 and s == "BULLISH": return "BULLISH"
    if e50 and prix < e50 and s == "BEARISH": return "BEARISH"
    return "NEUTRE"

# ══════════════════════════════
# MOTEUR PRINCIPAL — STRATÉGIE SIMPLIFIÉE
# La règle d'or : Prix + Structure + 1 confirmation
# ══════════════════════════════
def analyser(paire, candles, sentiment, annonce_eco, impact_gold, dxy_signal='NEUTRE', cot_signal='NEUTRE'):
    global trades_du_jour
    if trades_du_jour >= MAX_TRADES_JOUR: return None
    if annonce_eco: return None
    if not candles or len(candles) < 30: return None

    prix   = candles[0]["close"]
    atr_val = atr(candles)
    closes  = [c["close"] for c in candles]

    # Les 5 éléments clés — simples mais puissants
    htf       = biais_htf(paire)
    bos       = structure(candles)
    vwap_val  = vwap(candles)
    rsi_val   = rsi(closes, 14)
    rsi_div   = rsi_divergence(closes, 14)
    ob_b, ob_s = order_block(candles)
    fvg_b, fvg_s = fvg(candles)
    choch_val = choch(candles)

    # Niveaux psychologiques Gold
    niveau_rond = round(prix / 50) * 50
    sur_niveau  = abs(prix - niveau_rond) <= 2

    score_b, score_s = 0, 0
    conf_b,  conf_s  = [], []

    # 1. BIAIS HTF — le plus important
    if htf == "BULLISH": score_b += 3; conf_b.append("Biais 1H haussier")
    if htf == "BEARISH": score_s += 3; conf_s.append("Biais 1H baissier")

    # 2. STRUCTURE DE MARCHE (BOS)
    if bos == "BULLISH": score_b += 2; conf_b.append("Structure haussiere (BOS)")
    if bos == "BEARISH": score_s += 2; conf_s.append("Structure baissiere (BOS)")

    # 3. PRIX VS VWAP — niveau de référence
    if vwap_val:
        if prix > vwap_val: score_b += 1; conf_b.append(f"Prix au dessus VWAP ({round(vwap_val,2)})")
        else:               score_s += 1; conf_s.append(f"Prix en dessous VWAP ({round(vwap_val,2)})")

    # 4. RSI 14 — pas de signal en zone extrême
    if rsi_val:
        if rsi_val < 35:         score_b += 2; conf_b.append(f"RSI survendu ({rsi_val}) - rebond attendu")
        elif 35 <= rsi_val <= 55: score_b += 1; conf_b.append(f"RSI favorable ({rsi_val})")
        if rsi_val > 65:         score_s += 2; conf_s.append(f"RSI surachete ({rsi_val}) - repli attendu")
        elif 45 <= rsi_val <= 65: score_s += 1; conf_s.append(f"RSI favorable ({rsi_val})")

    # 5. DIVERGENCE RSI — signal fort
    if rsi_div == "BULL": score_b += 3; conf_b.append("Divergence RSI haussiere - retournement probable")
    if rsi_div == "BEAR": score_s += 3; conf_s.append("Divergence RSI baissiere - retournement probable")

    # 6. ORDER BLOCK — zone institutionnelle
    if ob_b and ob_b[0] <= prix <= ob_b[1]:
        score_b += 2; conf_b.append(f"Order Block haussier ({round(ob_b[0],2)}-{round(ob_b[1],2)})")
    if ob_s and ob_s[0] <= prix <= ob_s[1]:
        score_s += 2; conf_s.append(f"Order Block baissier ({round(ob_s[0],2)}-{round(ob_s[1],2)})")

    # 7. FVG — desequilibre a combler
    if fvg_b and fvg_b[0] <= prix <= fvg_b[1]:
        score_b += 2; conf_b.append(f"FVG haussier ({round(fvg_b[0],2)}-{round(fvg_b[1],2)})")
    if fvg_s and fvg_s[0] <= prix <= fvg_s[1]:
        score_s += 2; conf_s.append(f"FVG baissier ({round(fvg_s[0],2)}-{round(fvg_s[1],2)})")

    # 8. CHOCH — chasse de liquidite
    if choch_val == "BULL": score_b += 2; conf_b.append("ChoCh haussier - liquidite chassee")
    if choch_val == "BEAR": score_s += 2; conf_s.append("ChoCh baissier - liquidite chassee")

    # 8b. LIQUIDITES ICT + SESSIONS
    liq_sessions = detecter_liquidite_sessions(candles)

    # Asian SSL swept = stops asiatiques chassés = BUY signal
    if liq_sessions.get("asian_ssl_swept"):
        score_b += 3
        conf_b.append("Asian SSL sweep - stops asiatiques chassés - rebond haussier")

    # Asian BSL swept = stops asiatiques chassés = SELL signal
    if liq_sessions.get("asian_bsl_swept"):
        score_s += 3
        conf_s.append("Asian BSL sweep - stops asiatiques chassés - retournement baissier")

    # London SSL swept = NY chasse les lows Londres = BUY
    if liq_sessions.get("london_ssl_swept"):
        score_b += 3
        conf_b.append("London SSL sweep - NY chasse liquidite Londres - signal haussier")

    # London BSL swept = NY chasse les highs Londres = SELL
    if liq_sessions.get("london_bsl_swept"):
        score_s += 3
        conf_s.append("London BSL sweep - NY chasse liquidite Londres - signal baissier")

    # PDH swept = Previous Day High chassé = retournement baissier
    if liq_sessions.get("pdh_swept"):
        score_s += 2
        conf_s.append(f"PDH sweepé ({liq_sessions.get('pdh')}) - retournement baissier")

    # PDL swept = Previous Day Low chassé = retournement haussier
    if liq_sessions.get("pdl_swept"):
        score_b += 2
        conf_b.append(f"PDL sweepé ({liq_sessions.get('pdl')}) - retournement haussier")

    # Opening Range — prix qui revient tester
    if liq_sessions.get("sous_opening_range"):
        score_b += 1
        conf_b.append(f"Prix sous Opening Range ({liq_sessions.get('or_low')}) - zone support")
    if liq_sessions.get("dessus_opening_range"):
        score_s += 1
        conf_s.append(f"Prix dessus Opening Range ({liq_sessions.get('or_high')}) - zone resistance")

    # Asian High/Low comme niveaux de référence
    if liq_sessions.get("asian_high") and liq_sessions.get("asian_low"):
        ah = liq_sessions["asian_high"]
        al = liq_sessions["asian_low"]
        if abs(prix - al) / prix < 0.001:
            score_b += 1
            conf_b.append(f"Prix sur Asian Low ({al}) - zone support")
        if abs(prix - ah) / prix < 0.001:
            score_s += 1
            conf_s.append(f"Prix sur Asian High ({ah}) - zone resistance")

    # LIQUIDITES ICT classiques
    eqh, eql         = detecter_equal_highs_lows(candles)
    bsl, ssl, bsl_swept, ssl_swept = detecter_bsl_ssl(candles)
    inducement        = detecter_inducement(candles)
    void_bull, void_bear = detecter_void_liquidity(candles)

    # SSL Swept = stops des acheteurs chassés = signal BUY (retournement haussier)
    if ssl_swept:
        score_b += 3
        conf_b.append(f"SSL sweep - stops retail chassés - retournement haussier probable")

    # BSL Swept = stops des vendeurs chassés = signal SELL (retournement baissier)
    if bsl_swept:
        score_s += 3
        conf_s.append(f"BSL sweep - stops retail chassés - retournement baissier probable")

    # Equal Lows = zone de liquidité en dessous = attention si prix s approche
    if eql and abs(prix - eql) / prix < 0.002:
        score_b += 1
        conf_b.append(f"Equal Lows proches ({round(eql,2)}) - zone de liquidite")

    # Equal Highs = zone de liquidité au dessus = attention si prix s approche
    if eqh and abs(prix - eqh) / prix < 0.002:
        score_s += 1
        conf_s.append(f"Equal Highs proches ({round(eqh,2)}) - zone de liquidite")

    # Inducement
    if inducement == "BULL_INDUCEMENT":
        score_b += 2
        conf_b.append("Inducement haussier - faux signal baissier detecte")
    if inducement == "BEAR_INDUCEMENT":
        score_s += 2
        conf_s.append("Inducement baissier - faux signal haussier detecte")

    # Void
    if void_bear and void_bear[0] <= prix <= void_bear[1]:
        score_b += 1
        conf_b.append(f"Void haussier a combler ({round(void_bear[0],2)}-{round(void_bear[1],2)})")
    if void_bull and void_bull[0] <= prix <= void_bull[1]:
        score_s += 1
        conf_s.append(f"Void baissier a combler ({round(void_bull[0],2)}-{round(void_bull[1],2)})")

    # 9. SENTIMENT NEWS
    if sentiment == "BULLISH": score_b += 1; conf_b.append("Sentiment news haussier")
    if sentiment == "BEARISH": score_s += 1; conf_s.append("Sentiment news baissier")

    # 10. BONUS GOLD — DXY + COT + Macro
    if "XAU" in paire:
        # DXY — corrélation inverse forte
        if dxy_signal == "BULLISH_GOLD":
            score_b += 2; conf_b.append("DXY en baisse - favorable Gold")
        elif dxy_signal == "BEARISH_GOLD":
            score_s += 2; conf_s.append("DXY en hausse - défavorable Gold")

        # COT Report — positions institutionnelles
        if cot_signal == "BULLISH":
            score_b += 2; conf_b.append("COT : institutionnels acheteurs Gold")
        elif cot_signal == "BEARISH":
            score_s += 2; conf_s.append("COT : institutionnels vendeurs Gold")

    if "XAU" in paire:
        if impact_gold == "BULLISH": score_b += 2; conf_b.append("Annonce macro favorable Gold")
        if impact_gold == "BEARISH": score_s += 2; conf_s.append("Annonce macro defavorable Gold")
        if sur_niveau: 
            score_b += 1; conf_b.append(f"Niveau psychologique ({niveau_rond})")
            score_s += 1; conf_s.append(f"Niveau psychologique ({niveau_rond})")
        if session_gold_optimale():
            score_b += 1; conf_b.append("Session Gold optimale")
            score_s += 1; conf_s.append("Session Gold optimale")

    # DECISION — minimum 6 points ET biais HTF aligné
    signal = None
    score  = 0
    conf   = []

    if score_b >= 6 and score_b > score_s and htf != "BEARISH":
        signal, score, conf = "BUY",  score_b, conf_b
    elif score_s >= 6 and score_s > score_b and htf != "BULLISH":
        signal, score, conf = "SELL", score_s, conf_s

    if not signal: return None

    # SL / TP basés sur ATR
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

    if score >= 14:
        qualite = "FORT"
    elif score >= 10:
        qualite = "BIEN"
    elif score >= 7:
        qualite = "MOYEN"
    else:
        qualite = "FAIBLE"
    gold_note = "\nVerifier sur Exocharts avant de copier" if is_gold else ""

    message = (
        f"{'ACHAT' if signal == 'BUY' else 'VENTE'} - {paire}\n"
        f"Qualite : {qualite}\n"
        f"Session : {nom_session()}\n"
        f"Biais HTF : {htf}\n\n"
        f"Entree : {prix}\n"
        f"Stop Loss : {sl}\n"
        f"TP1 (40%) : {tp1}\n"
        f"TP2 (35%) : {tp2}\n"
        f"TP3 (25%) : {tp3}\n\n"
        f"Pourquoi ce signal :\n" +
        "\n".join(f"- {c}" for c in conf[:7]) +
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
    print("Bot simplifie demarre !")
    envoyer_telegram(
        "Bot demarre - Strategie simplifiee\n\n"
        "Logique : Biais HTF + Structure + Confirmation\n\n"
        "Outils :\n"
        "- Biais 1H (EMA + Structure)\n"
        "- Break of Structure (BOS)\n"
        "- VWAP\n"
        "- RSI 14 + Divergence\n"
        "- Order Block + FVG + ChoCh\n"
        "- News FXStreet\n"
        "- ForexFactory + Impact Gold\n\n"
        f"Max {MAX_TRADES_JOUR} trades/jour\n"
        "SL : 1.5x ATR | TP : 1:1.5 / 1:2.5 / 1:4"
    )

    while True:
        reset()
        print(f"\n[{now().strftime('%H:%M:%S')}] Nouvelle analyse...")

        annonce_eco, nom_annonce, impact_gold = get_annonces_eco()
        time.sleep(5)

        # DXY et COT pour le Gold
        print(f"  Analyse DXY...")
        dxy_signal, dxy_variation = get_dxy()
        time.sleep(5)

        print(f"  Lecture COT Report CFTC...")
        cot_signal, cot_longs, cot_shorts = get_cot_gold()
        time.sleep(5)

        # Alerte si DXY ou COT fort
        if dxy_signal == "BULLISH_GOLD" and cot_signal == "BULLISH":
            envoyer_telegram(
                "CONFLUENCE GOLD FORTE\n"
                "DXY en baisse (" + str(dxy_variation) + "%)\n"
                "COT : institutionnels acheteurs\n"
                "Biais semaine : HAUSSIER sur XAU/USD"
            )
        elif dxy_signal == "BEARISH_GOLD" and cot_signal == "BEARISH":
            envoyer_telegram(
                "CONFLUENCE GOLD FORTE\n"
                "DXY en hausse (+" + str(dxy_variation) + "%)\n"
                "COT : institutionnels vendeurs\n"
                "Biais semaine : BAISSIER sur XAU/USD"
            )

        for paire in PAIRES:
            print(f"  Analyse {paire}...")
            sentiment = get_sentiment(paire)
            time.sleep(3)
            candles = get_candles(paire, "15min", 50)
            signal  = analyser(paire, candles, sentiment, annonce_eco, impact_gold, dxy_signal, cot_signal)
            if signal:
                envoyer_telegram(signal)
            else:
                print(f"  Pas de signal sur {paire}")
            time.sleep(15)

        print("Prochaine analyse dans 15 min...")
        time.sleep(900)

if __name__ == "__main__":
    main()
