from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import requests
import os
import base64
import hashlib
import hmac
import csv
from functools import wraps
import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

app = Flask(__name__)
load_dotenv()
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me")

COGNITO_REGION = os.getenv("COGNITO_REGION", "")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
COGNITO_CLIENT_SECRET = os.getenv("COGNITO_CLIENT_SECRET", "")

cognito = boto3.client("cognito-idp", region_name=COGNITO_REGION or None)

def _cognito_config_ok():
    return all([COGNITO_REGION, COGNITO_USER_POOL_ID, COGNITO_CLIENT_ID])

def _secret_hash(username):
    if not COGNITO_CLIENT_SECRET:
        return None
    msg = (username + COGNITO_CLIENT_ID).encode("utf-8")
    key = COGNITO_CLIENT_SECRET.encode("utf-8")
    digest = hmac.new(key, msg, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

def _normalize_username(username):
    return username.strip()

LOCATION_CSV = os.path.join("data", "location", "australia_locations.csv")
LOCATIONS = []

def load_locations():
    if not os.path.exists(LOCATION_CSV):
        return
    with open(LOCATION_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                LOCATIONS.append({
                    "postcode": row.get("postcode", "").strip(),
                    "suburb": row.get("suburb", "").strip(),
                    "state": row.get("state", "").strip(),
                    "lat": float(row.get("latitude", "0") or 0),
                    "lon": float(row.get("longitude", "0") or 0),
                })
            except ValueError:
                continue

load_locations()

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_email"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

# Fitzpatrick skin type data
SKIN_TYPES = {
    "I":   {"label": "Type I – Very Fair",   "color": "#FFE4C4", "min_burn_uv": 2,  "desc": "Always burns, never tans. Highest risk."},
    "II":  {"label": "Type II – Fair",        "color": "#F5CBA7", "min_burn_uv": 3,  "desc": "Usually burns, tans minimally."},
    "III": {"label": "Type III – Medium",     "color": "#E8A87C", "min_burn_uv": 4,  "desc": "Sometimes burns, gradually tans."},
    "IV":  {"label": "Type IV – Olive",       "color": "#C68642", "min_burn_uv": 6,  "desc": "Rarely burns, always tans well."},
    "V":   {"label": "Type V – Brown",        "color": "#8D5524", "min_burn_uv": 9,  "desc": "Very rarely burns, tans very easily."},
    "VI":  {"label": "Type VI – Dark Brown",  "color": "#4A2C17", "min_burn_uv": 12, "desc": "Never burns. Lowest risk but still needs protection."},
}

UV_LEVELS = [
    {"max": 2,  "label": "Low",       "color": "#4CAF50", "emoji": "😎", "action": "No protection needed. Enjoy the outdoors!"},
    {"max": 5,  "label": "Moderate",  "color": "#FFC107", "emoji": "🧴", "action": "Wear SPF 30+, sunglasses and a hat."},
    {"max": 7,  "label": "High",      "color": "#FF9800", "emoji": "⚠️",  "action": "SPF 50+ required. Seek shade 10am–2pm."},
    {"max": 10, "label": "Very High", "color": "#F44336", "emoji": "🚨", "action": "Minimise outdoor exposure. Full protection essential."},
    {"max": 99, "label": "Extreme",   "color": "#9C27B0", "emoji": "🔥", "action": "EXTREME. Avoid being outside. SPF 50+, hat, UPF clothing."},
]

def get_uv_category(uv_index):
    for level in UV_LEVELS:
        if uv_index <= level["max"]:
            return level
    return UV_LEVELS[-1]

def minutes_to_burn(uv_index, skin_type_key):
    skin = SKIN_TYPES.get(skin_type_key, SKIN_TYPES["III"])
    if uv_index == 0:
        return None
    base = skin["min_burn_uv"] * 60
    minutes = round(base / uv_index)
    return max(minutes, 5)

def spf_recommendation(uv_index):
    if uv_index <= 2:  return {"spf": "SPF 15+", "reapply": "Every 2 hours if sweating", "teaspoons": 1}
    if uv_index <= 5:  return {"spf": "SPF 30+", "reapply": "Every 2 hours", "teaspoons": 1.5}
    if uv_index <= 7:  return {"spf": "SPF 50+", "reapply": "Every 90 minutes", "teaspoons": 2}
    return                    {"spf": "SPF 50+", "reapply": "Every 60 minutes", "teaspoons": 2.5}

def generate_tips(uv, skin_key, risk):
    tips = [
        f"Apply {spf_recommendation(uv)['spf']} sunscreen 20 minutes before going outside.",
        f"Reapply {spf_recommendation(uv)['reapply']}.",
    ]
    if risk == "HIGH":
        tips.append("Seek shade especially between 10am and 2pm.")
        tips.append("Wear UPF 50+ protective clothing and a broad-brimmed hat.")
        tips.append("UV-blocking sunglasses are essential right now.")
    if skin_key in ("I", "II"):
        tips.append("Your skin type is very sensitive — even short exposure requires full protection.")
    if uv >= 8:
        tips.append("Consider rescheduling outdoor activities to early morning or late afternoon.")
    return tips


# ──────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("home.html")

@app.route("/api/uv")
def api_uv():
    lat = request.args.get("lat", "-37.8136")
    lon = request.args.get("lon", "144.9631")
    city = request.args.get("city", "Melbourne")

    try:
        # Open-Meteo: 完全免费，无需API key，真实UV数据
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,uv_index,weather_code"
            f"&timezone=Australia/Melbourne"
        )
        resp = requests.get(url, timeout=5)
        data = resp.json()
        current = data["current"]
        uv = current.get("uv_index", 0)
        temp = round(current.get("temperature_2m", 20), 1)
        weather_desc = "Clear" if current.get("weather_code", 0) < 3 else "Cloudy"
    except Exception:
        uv = 8.5
        temp = 26.3
        weather_desc = "Sunny (demo mode)"

    category = get_uv_category(uv)
    spf = spf_recommendation(uv)

    return jsonify({
        "city": city,
        "uv_index": uv,
        "temp_c": temp,
        "weather": weather_desc,
        "category": category,
        "spf": spf,
    })

@app.route("/api/uv_by_coords")
def api_uv_by_coords():
    lat = request.args.get("lat")
    lon = request.args.get("lon")
    name = request.args.get("name", "Your Location")

    if not lat or not lon:
        return jsonify({"error": "lat and lon are required"}), 400

    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&current=temperature_2m,uv_index,weather_code"
            f"&timezone=Australia/Melbourne"
        )
        resp = requests.get(url, timeout=5)
        data = resp.json()
        current = data.get("current", {})
        uv = float(current.get("uv_index", 0) or 0)
        temp = round(float(current.get("temperature_2m", 0) or 0), 1)
        weather_code = int(current.get("weather_code", 0) or 0)
        weather_desc = "Clear" if weather_code < 3 else "Cloudy"
    except Exception:
        uv = 0
        temp = 0
        weather_desc = "Unavailable"

    category = get_uv_category(uv)
    spf = spf_recommendation(uv)
    burn_mins = minutes_to_burn(uv, "III")

    return jsonify({
        "name": name,
        "lat": float(lat),
        "lon": float(lon),
        "uv_index": uv,
        "temp_c": temp,
        "weather": weather_desc,
        "category": category,
        "spf": spf,
        "burn_minutes": burn_mins,
    })

@app.route("/api/personalise")
def api_personalise():
    uv = float(request.args.get("uv", 5))
    skin_key = request.args.get("skin", "III").upper()

    skin = SKIN_TYPES.get(skin_key, SKIN_TYPES["III"])
    burn_mins = minutes_to_burn(uv, skin_key)
    category = get_uv_category(uv)
    spf = spf_recommendation(uv)

    risk = "HIGH" if uv >= skin["min_burn_uv"] else "MODERATE" if uv >= skin["min_burn_uv"] * 0.6 else "LOW"

    return jsonify({
        "skin": skin,
        "burn_minutes": burn_mins,
        "risk_level": risk,
        "category": category,
        "spf": spf,
        "tips": generate_tips(uv, skin_key, risk),
    })

@app.route("/api/search")
def api_search():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"results": []})

    # Normalize query: prefer postcode if present, otherwise use suburb before comma
    digits = "".join(ch for ch in query if ch.isdigit())
    if digits:
        query_key = digits
        is_postcode = True
    else:
        query_key = query.split(",")[0].strip()
        is_postcode = False

    q_lower = query_key.lower()

    matches = []
    exact = []
    for loc in LOCATIONS:
        if is_postcode:
            if loc["postcode"] == query_key:
                exact.append(loc)
            elif loc["postcode"].startswith(query_key):
                matches.append(loc)
        else:
            suburb_lower = loc["suburb"].lower()
            if suburb_lower == q_lower:
                exact.append(loc)
            elif q_lower in suburb_lower:
                matches.append(loc)
        if len(exact) >= 1:
            break
    if exact:
        matches = exact[:1]
    else:
        matches = matches[:1]

    results = []
    for loc in matches:
        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={loc['lat']}&longitude={loc['lon']}"
                f"&current=temperature_2m,uv_index,weather_code"
                f"&timezone=Australia/Melbourne"
            )
            resp = requests.get(url, timeout=5)
            data = resp.json()
            current = data.get("current", {})
            uv = float(current.get("uv_index", 0) or 0)
            temp = round(float(current.get("temperature_2m", 0) or 0), 1)
            weather_code = int(current.get("weather_code", 0) or 0)
            weather_desc = "Clear" if weather_code < 3 else "Cloudy"
        except Exception:
            uv = 0
            temp = 0
            weather_desc = "Unavailable"

        category = get_uv_category(uv)
        spf = spf_recommendation(uv)
        burn_mins = minutes_to_burn(uv, "III")

        results.append({
            "name": f"{loc['suburb']}, {loc['state']}",
            "postcode": loc["postcode"],
            "state": loc["state"],
            "lat": loc["lat"],
            "lon": loc["lon"],
            "uv_index": uv,
            "temp_c": temp,
            "weather": weather_desc,
            "category": category,
            "spf": spf,
            "burn_minutes": burn_mins,
        })

    return jsonify({"results": results})

@app.route("/api/suggest")
def api_suggest():
    query = request.args.get("query", "").strip()
    if not query:
        return jsonify({"suggestions": []})

    q_lower = query.lower()
    is_postcode = query.isdigit()
    seen = set()
    suggestions = []

    for loc in LOCATIONS:
        if is_postcode:
            if not loc["postcode"].startswith(query):
                continue
            label = f"{loc['postcode']} — {loc['suburb']}, {loc['state']}"
        else:
            if q_lower not in loc["suburb"].lower():
                continue
            label = f"{loc['suburb']}, {loc['state']} {loc['postcode']}"
        if label in seen:
            continue
        seen.add(label)
        suggestions.append(label)
        if len(suggestions) >= 8:
            break

    return jsonify({"suggestions": suggestions})

@app.route("/search")
def search():
    return render_template("search.html")

@app.route("/quiz")
def quiz():
    return render_template("quiz.html")

@app.route("/reminders")
def reminders():
    return render_template("reminders.html")

@app.route("/profile")
def profile():
    return render_template("profile.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    message = None
    if request.method == "POST":
        if not _cognito_config_ok():
            error = "Cognito is not configured. Set COGNITO_REGION/USER_POOL_ID/CLIENT_ID."
            return render_template("login.html", error=error)
        username = _normalize_username(request.form.get("username", ""))
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        try:
            auth_params = {"USERNAME": username, "PASSWORD": password}
            secret_hash = _secret_hash(username)
            if secret_hash:
                auth_params["SECRET_HASH"] = secret_hash
            cognito.initiate_auth(
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters=auth_params,
                ClientId=COGNITO_CLIENT_ID,
            )
            session["user_email"] = email or username
            return redirect(request.args.get("next") or url_for("index"))
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            message = e.response.get("Error", {}).get("Message", "")
            if code == "UserNotConfirmedException":
                error = "Please verify your email before signing in."
            elif code == "NotAuthorizedException":
                error = "Invalid email or password."
            elif code == "UserNotFoundException":
                error = "Account not found. Please register first."
            else:
                error = f"Login failed: {code or 'UnknownError'}{(' - ' + message) if message else ''}"
    return render_template("login.html", error=error, message=message)

@app.route("/register", methods=["POST"])
def register():
    if not _cognito_config_ok():
        return render_template(
            "login.html",
            error="Cognito is not configured. Set COGNITO_REGION/USER_POOL_ID/CLIENT_ID.",
            tab="register",
        )
    full_name = request.form.get("full_name", "").strip()
    username = _normalize_username(request.form.get("username", ""))
    gender = request.form.get("gender", "").strip().lower()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not full_name or not username or not gender or not email or not password:
        return render_template("login.html", error="All fields are required.", tab="register")
    try:
        params = {
            "ClientId": COGNITO_CLIENT_ID,
            "Username": username,
            "Password": password,
            "UserAttributes": [
                {"Name": "email", "Value": email},
                {"Name": "name", "Value": full_name},
                {"Name": "gender", "Value": gender},
            ],
        }
        secret_hash = _secret_hash(username)
        if secret_hash:
            params["SecretHash"] = secret_hash
        cognito.sign_up(**params)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        message = e.response.get("Error", {}).get("Message", "")
        if code == "UsernameExistsException":
            return render_template("login.html", error="Email already registered.", tab="register")
        # Surface the exact Cognito error code to speed up debugging
        return render_template(
            "login.html",
            error=f"Sign up failed: {code or 'UnknownError'}{(' - ' + message) if message else ''}",
            tab="register",
        )

    return render_template(
        "login.html",
        message="Account created. Please verify your email, then sign in.",
        tab="signin",
    )

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

if __name__ == "__main__":
    # Use env vars for deployment flexibility (e.g., AWS)
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
