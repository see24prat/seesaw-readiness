"""
Garmin -> readiness.json
Runs daily in GitHub Actions (~7:30 AM ET). Logs into Garmin with a saved
session token, reads BOTH Training Readiness and Body Battery, blends them
into a single 0-100 score, and writes readiness.json (committed to the repo).
"""
import os
import json
import datetime
from garminconnect import Garmin

TOKENS = os.environ["GARMINTOKENS_BASE64"]
TODAY = datetime.date.today().isoformat()

detail = {}

client = Garmin()
client.client.loads(TOKENS)            # load the saved Garmin session token
try:                                   # populate display name for endpoints that need it
    _p = client.get_user_profile()
    _dn = (_p or {}).get("displayName")
    if _dn:
        client.display_name = _dn
except Exception as e:
    detail["profileNote"] = str(e)

training_readiness = None
body_battery = None

# --- Training Readiness (0-100) ---
try:
    tr = client.get_training_readiness(TODAY)
    s = None
    if isinstance(tr, list) and tr:
        s = tr[0].get("score")
    elif isinstance(tr, dict):
        s = tr.get("score")
    if s is not None:
        training_readiness = int(round(s))
except Exception as e:
    detail["trainingReadinessError"] = str(e)

# --- Body Battery: highest 0-100 value logged so far today (morning peak) ---
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
        body_battery = int(max(vals))
except Exception as e:
    detail["bodyBatteryError"] = str(e)

# --- Sleep score (extra context only) ---
try:
    sl = client.get_sleep_data(TODAY)
    ss = (((sl or {}).get("dailySleepDTO") or {}).get("sleepScores") or {}).get("overall", {}).get("value")
    if ss is not None:
        detail["sleepScore"] = ss
except Exception as e:
    detail["sleepError"] = str(e)

# --- Blend: average of whichever of the two we got ---
parts = [v for v in (training_readiness, body_battery) if v is not None]
if parts:
    score = int(round(sum(parts) / len(parts)))
    source = " + ".join(
        n for n, v in (("Training Readiness", training_readiness), ("Body Battery", body_battery)) if v is not None
    )
elif "sleepScore" in detail:
    score = int(detail["sleepScore"])
    source = "Sleep score"
else:
    score = 50
    source = "default (no data found yet today)"

score = max(0, min(100, score))

out = {
    "date": TODAY,
    "score": score,
    "trainingReadiness": training_readiness,
    "bodyBattery": body_battery,
    "source": source,
    "detail": detail,
    "updatedUTC": datetime.datetime.utcnow().isoformat() + "Z",
}

with open("readiness.json", "w") as f:
    json.dump(out, f, indent=2)

print("Wrote readiness.json:", json.dumps(out))
