from flask import Flask, render_template, request, jsonify
import requests
import os

app = Flask(__name__)

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

@app.route("/login")
def login():
    return render_template("login.html")

if __name__ == "__main__":
    # Use env vars for deployment flexibility (e.g., AWS)
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)
