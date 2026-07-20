"""
Garmin -> readiness.json
Runs daily in GitHub Actions (~7:30 AM ET). Logs into Garmin with a saved
session token, reads BOTH Training Readiness and Body Battery, blends them
into a single 0-100 score, and writes readiness.json (committed to the repo).
"""
import os
import json
import datetime
import pathlib
from garminconnect import Garmin

# Token comes from .tokens.json (rotated state saved by the previous run,
# decrypted by the workflow) - falls back to the GARMINTOKENS_BASE64 secret.
_tk_file = pathlib.Path(".tokens.json")
TOKENS = _tk_file.read_text().strip() if _tk_file.exists() else ""
if not TOKENS:
    TOKENS = os.environ.get("GARMINTOKENS_BASE64", "")
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

# --- Recent activities: runs + tennis (last 7 days) ---
cardio = {"weekTennisMin": 0, "weekTennisCount": 0, "weekRunMin": 0, "weekRunCount": 0, "recent": [], "loadFlag": "low"}
try:
    _start = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    _acts = client.get_activities_by_date(_start, TODAY) or []
    for _a in _acts:
        _t = ((_a.get("activityType") or {}).get("typeKey") or "").lower()
        _dur = _a.get("duration") or _a.get("movingDuration") or 0
        try:
            _m = int(round(float(_dur) / 60))
        except Exception:
            _m = 0
        _stl = (_a.get("startTimeLocal") or "")[:10]
        try:
            _ago = (datetime.date.today() - datetime.date.fromisoformat(_stl)).days if _stl else 99
        except Exception:
            _ago = 99
        _is_run = "run" in _t
        _is_tennis = "tennis" in _t
        if _is_run:
            cardio["weekRunMin"] += _m
            cardio["weekRunCount"] += 1
        if _is_tennis:
            cardio["weekTennisMin"] += _m
            cardio["weekTennisCount"] += 1
        if _is_run or _is_tennis:
            cardio.setdefault("week", []).append(
                {"type": "tennis" if _is_tennis else "run", "min": _m, "date": _stl}
            )
        if (_is_run or _is_tennis) and _ago <= 2:
            cardio["recent"].append({"type": "tennis" if _is_tennis else "run", "min": _m, "daysAgo": _ago})
    _recent1 = sum(r["min"] for r in cardio["recent"] if r["daysAgo"] <= 1)
    _long = any((r["type"] == "tennis" and r["min"] >= 75) or (r["type"] == "run" and r["min"] >= 35)
                for r in cardio["recent"] if r["daysAgo"] <= 1)
    if _long or _recent1 >= 90:
        cardio["loadFlag"] = "high"
    elif _recent1 >= 30 or cardio["recent"]:
        cardio["loadFlag"] = "moderate"
except Exception as e:
    cardio["error"] = str(e)

# --- Training load: acute / chronic / ratio + training status ---
load = {}
def _hunt(obj, keys, depth=0):
    """Recursively find the first numeric/string value for any key name in `keys`."""
    if depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in keys and v is not None and not isinstance(v, (dict, list)):
                return v
        for v in obj.values():
            r = _hunt(v, keys, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for it in obj:
            r = _hunt(it, keys, depth + 1)
            if r is not None:
                return r
    return None

try:
    ts = client.get_training_status(TODAY)
    _acute = _hunt(ts, {"dailyTrainingLoadAcute", "acuteTrainingLoad", "loadAcute"})
    _chronic = _hunt(ts, {"dailyTrainingLoadChronic", "chronicTrainingLoad", "loadChronic"})
    _ratio = _hunt(ts, {"dailyAcuteChronicWorkloadRatio", "acwr", "acuteChronicWorkloadRatio"})
    _status = _hunt(ts, {"trainingStatusFeedbackPhrase", "trainingStatus", "statusPhrase"})
    if _acute is not None:
        load["acute"] = round(float(_acute))
    if _chronic is not None:
        load["chronic"] = round(float(_chronic))
    if _ratio is not None:
        load["acwr"] = round(float(_ratio), 2)
    elif _acute and _chronic:
        load["acwr"] = round(float(_acute) / float(_chronic), 2)
    if _status is not None:
        load["status"] = str(_status)
except Exception as e:
    load["error"] = str(e)

# Upgrade the cardio flag if the acute:chronic ratio says you're spiking load
_acwr = load.get("acwr")
if _acwr is not None:
    if _acwr >= 1.4 and cardio["loadFlag"] != "high":
        cardio["loadFlag"] = "high"
        cardio["flagWhy"] = "acute:chronic ratio " + str(_acwr)
    elif _acwr >= 1.15 and cardio["loadFlag"] == "low":
        cardio["loadFlag"] = "moderate"
        cardio["flagWhy"] = "acute:chronic ratio " + str(_acwr)

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
    "cardio": cardio,
    "load": load,
    "source": source,
    "detail": detail,
    "updatedUTC": datetime.datetime.utcnow().isoformat() + "Z",
}

with open("readiness.json", "w") as f:
    json.dump(out, f, indent=2)

# Persist the (possibly rotated/refreshed) token state for the next run.
try:
    _tk_file.write_text(client.client.dumps())
except Exception as e:
    print("token persist note:", e)

print("Wrote readiness.json:", json.dumps(out))
