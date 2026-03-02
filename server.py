from flask import Flask, request, jsonify, send_file, send_from_directory, redirect, url_for, render_template, flash, session
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import os
import json
import hashlib
from pathlib import Path
from flask_dance.contrib.google import make_google_blueprint, google

# Charger les variables d'environnement depuis .env
load_dotenv()

app = Flask(__name__, static_folder="static")
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecret')
app.config['SESSION_TYPE'] = 'filesystem'
CORS(app)

# File-based user storage
USERS_FILE = Path('users.json')

def load_users():
    """Load users from JSON file"""
    if USERS_FILE.exists():
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    """Save users to JSON file"""
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, password_hash):
    """Verify password against hash"""
    return hash_password(password) == password_hash

# Configuration Groq (lue depuis .env)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Google OAuth blueprint
google_bp = make_google_blueprint(
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    scope=["profile", "email"],
    redirect_url="/google_callback"
)
app.register_blueprint(google_bp, url_prefix="/login")

# --- routes ---
@app.route("/")
def index():
    """Render the main template; template checks session['user']"""
    return render_template('index.html')

@app.route('/register', methods=['POST'])
def register():
    email = request.form.get('email')
    password = request.form.get('password')
    if not email or not password:
        flash("Email et mot de passe requis")
        return redirect(url_for('index'))
    
    users = load_users()
    if email in users:
        flash("Email déjà utilisé")
        return redirect(url_for('index'))
    
    users[email] = {
        'password_hash': hash_password(password),
        'google_id': None
    }
    save_users(users)
    session['user'] = email
    return redirect(url_for('index'))

@app.route('/login', methods=['POST'])
def login():
    email = request.form.get('email')
    password = request.form.get('password')
    users = load_users()
    
    if email not in users or not verify_password(password, users[email]['password_hash']):
        flash("Identifiants invalides")
        return redirect(url_for('index'))
    
    session['user'] = email
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

@app.route('/google_callback')
def google_callback():
    if not google.authorized:
        return redirect(url_for('google.login'))
    resp = google.get('/oauth2/v2/userinfo')
    if not resp.ok:
        return "Erreur Google OAuth", 500
    info = resp.json()
    email = info.get('email')
    
    # Auto-login or create user with Google
    users = load_users()
    if email not in users:
        users[email] = {
            'password_hash': None,  # No password for Google users
            'google_id': info.get('id')
        }
        save_users(users)
    
    session['user'] = email
    return redirect(url_for('index'))


@app.route("/service-worker.js")
def service_worker():
    """Le service worker doit être servi depuis la racine pour couvrir tout le site"""
    return send_from_directory("static", "service-worker.js", mimetype="application/javascript")


@app.route("/api/ai", methods=["POST"])
def proxy_ai():
    """Proxy that relays requests to Groq API - requires user to be logged in via session"""
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    if not GROQ_API_KEY:
        return jsonify({"error": "Clé API Groq manquante. Vérifiez votre fichier .env"}), 500

    data = request.get_json()
    messages = data.get("messages", [])

    if not messages:
        return jsonify({"error": "Aucun message fourni"}), 400

    # Appel à l'API Groq (format OpenAI compatible)
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1500,
            },
            timeout=30,
        )
        response.raise_for_status()
        return jsonify(response.json())

    except requests.exceptions.Timeout:
        return jsonify({"error": "L'API Groq a mis trop de temps à répondre"}), 504
    except requests.exceptions.HTTPError as e:
        # Afficher le détail de l'erreur Groq pour faciliter le débogage
        detail = ""
        if e.response is not None:
            try:
                detail = e.response.json().get("error", {}).get("message", e.response.text)
            except Exception:
                detail = e.response.text
        return jsonify({"error": f"Erreur API Groq: {detail or str(e)}"}), 502
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Erreur réseau: {str(e)}"}), 502


if __name__ == "__main__":
    print("=" * 50)
    print("  🚀 Nafti AI - Serveur démarré (PWA activée)")
    print(f"  📍 http://localhost:5000")
    print(f"  🤖 Modèle: {GROQ_MODEL}")
    print(f"  🔑 Clé API: {'✅ configurée' if GROQ_API_KEY else '❌ MANQUANTE'}")
    print(f"  📁 Utilisateurs: {USERS_FILE} (auto-créé)")
    print("=" * 50)
    app.run(debug=True, port=5000)