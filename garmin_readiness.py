"""
Garmin -> readiness.json
Runs daily in GitHub Actions. Logs into Garmin Connect, grabs your morning
recovery metrics, picks the best single 0-100 "readiness" number, and writes
readiness.json. Your login is read from environment secrets (never stored here).
"""
import os
import json
import datetime
from garminconnect import Garmin

# Resume the saved session token (minted once, with your 2FA code, in Colab).
# Stored as a GitHub Secret named GARMINTOKENS_BASE64 - no password or MFA needed daily.
TOKENS = os.environ["GARMINTOKENS_BASE64"]
TODAY = datetime.date.today().isoformat()

score = None
source = None
detail = {}

client = Garmin()
client.client.loads(TOKENS)           # load the saved Garmin session token
try:                                  # populate display name for endpoints that need it
    _p = client.get_user_profile()
    _dn = (_p or {}).get("displayName")
    if _dn:
        client.display_name = _dn
except Exception as _e:
    detail["profileNote"] = str(_e)

# 1) Training Readiness (best signal on Forerunner / Fenix / Epix)
try:
    tr = client.get_training_readiness(TODAY)
    s = None
    if isinstance(tr, list) and tr:
        s = tr[0].get("score")
    elif isinstance(tr, dict):
        s = tr.get("score")
    if s is not None:
        score = int(round(s))
        source = "Training Readiness"
        detail["trainingReadiness"] = s
except Exception as e:
    detail["trainingReadinessError"] = str(e)

# 2) Body Battery peak (fallback) - highest 0-100 value logged today
try:
    bb = client.get_body_battery(TODAY, TODAY)
    vals = []
    if isinstance(bb, list):
        for day in bb:
            for pair in (day.get("bodyBatteryValuesArray") or []):
                if isinstance(pair, list):
                    cand = [x for x in pair if isinstance(x, (int, float)) and 0 <= x <= 100]
                    if cand:
                        vals.append(cand[-1])
    if vals:
        detail["bodyBatteryPeak"] = max(vals)
        if score is None:
            score = int(max(vals))
            source = "Body Battery (peak)"
except Exception as e:
    detail["bodyBatteryError"] = str(e)

# 3) Sleep score (extra context, and last-resort fallback)
try:
    sl = client.get_sleep_data(TODAY)
    ss = (((sl or {}).get("dailySleepDTO") or {}).get("sleepScores") or {}).get("overall", {}).get("value")
    if ss is not None:
        detail["sleepScore"] = ss
        if score is None:
            score = int(ss)
            source = "Sleep score"
except Exception as e:
    detail["sleepError"] = str(e)

if score is None:
    score = 50
    source = "default (no data found yet today)"

score = max(0, min(100, score))

out = {
    "date": TODAY,
    "score": score,
    "source": source,
    "detail": detail,
    "updatedUTC": datetime.datetime.utcnow().isoformat() + "Z",
}

with open("readiness.json", "w") as f:
    json.dump(out, f, indent=2)

print("Wrote readiness.json:", json.dumps(out))
