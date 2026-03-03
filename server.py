from flask import Flask, request, jsonify, send_file, send_from_directory, redirect, url_for, render_template, flash, session
from flask_cors import CORS
from dotenv import load_dotenv
import requests
import os
import json
import hashlib
import uuid
from pathlib import Path
from datetime import datetime
from flask_dance.contrib.google import make_google_blueprint, google

# Charger les variables d'environnement depuis .env
load_dotenv()

app = Flask(__name__, static_folder="static")
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'supersecret')
app.config['SESSION_TYPE'] = 'filesystem'
CORS(app)

# File-based user storage
USERS_FILE = Path('users.json')

# Chat history storage (supports multiple sessions per user)
HISTORY_FILE = Path('history.json')

def load_history():
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_history(data):
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)

def _is_valid_session(entry):
    """Check if a history entry is a valid session object (has id, title, messages)."""
    return isinstance(entry, dict) and 'id' in entry and 'messages' in entry

def ensure_user_sessions(user):
    histories = load_history()
    if user not in histories:
        histories[user] = []
        save_history(histories)
    else:
        # Clean up any malformed entries (e.g. raw messages without session structure)
        original = histories[user]
        cleaned = [s for s in original if _is_valid_session(s)]
        if len(cleaned) != len(original):
            histories[user] = cleaned
            save_history(histories)
    return histories

def create_session_for_user(user):
    histories = ensure_user_sessions(user)
    new_id = str(uuid.uuid4())
    session_obj = {"id": new_id, "title": "Nouvelle conversation", "created_at": datetime.now().isoformat(), "messages": []}
    histories[user].append(session_obj)
    save_history(histories)
    return session_obj

def find_session(user, session_id):
    histories = ensure_user_sessions(user)
    for sess in histories.get(user, []):
        if sess.get('id') == session_id:
            return sess
    return None

def delete_session(user, session_id):
    histories = ensure_user_sessions(user)
    histories[user] = [s for s in histories.get(user, []) if s.get('id') != session_id]
    save_history(histories)

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
    """Render the main template; template passes chat sessions list"""
    user = session.get('user')
    sessions = []
    if user:
        histories = ensure_user_sessions(user)
        sessions = histories.get(user, [])
    return render_template('index.html', sessions=sessions)

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

@app.route('/history')
def history_view():
    user = session.get('user')
    if not user:
        return redirect(url_for('index'))
    histories = load_history()
    sessions = histories.get(user, [])
    return render_template('history.html', sessions=sessions)

@app.route('/session/new', methods=['POST'])
def new_session():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    sess = create_session_for_user(user)
    return jsonify(sess)

@app.route('/session/clear', methods=['POST'])
def clear_session():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    sid = data.get('session_id')
    if not sid:
        return jsonify({"error": "Missing session_id"}), 400
    sess = find_session(user, sid)
    if not sess:
        return jsonify({"error": "Session not found"}), 404
    sess['messages'] = []
    histories = load_history()
    for idx, s in enumerate(histories.get(user, [])):
        if s.get('id') == sid:
            histories[user][idx] = sess
            break
    save_history(histories)
    return jsonify({"status": "cleared"})

@app.route('/session/delete', methods=['POST'])
def delete_session_route():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    sid = data.get('session_id')
    if not sid:
        return jsonify({"error": "Missing session_id"}), 400
    delete_session(user, sid)
    return jsonify({"status": "deleted"})

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
    """Proxy that relays requests to Groq API - stores conversation history per user"""
    if 'user' not in session:
        return jsonify({"error": "Unauthorized"}), 401
    if not GROQ_API_KEY:
        return jsonify({"error": "Clé API Groq manquante. Vérifiez votre fichier .env"}), 500

    data = request.get_json()
    messages = data.get("messages", [])
    session_id = data.get("session_id")
    user = session.get('user')
    if not user or not session_id:
        return jsonify({"error": "Missing session or user"}), 400
    sess = find_session(user, session_id)
    if not sess:
        # maybe create automatically
        sess = create_session_for_user(user)
        session_id = sess['id']
    
    if not messages:
        return jsonify({"error": "Aucun message fourni"}), 400

    # save incoming conversation (skip system message if present) into the current session
    # sess already fetched or created above
    if messages and messages[0].get('role') == 'system':
        stored = messages[1:]
    else:
        stored = messages[:]
    sess['messages'] = stored
    # Update session title from first user message
    if not sess.get('title') or sess.get('title') == 'Nouvelle conversation':
        for m in stored:
            if m.get('role') == 'user':
                sess['title'] = m['content'][:80]
                break
    histories = load_history()
    histories[user] = histories.get(user, [])
    # update the specific session object in histories list
    for idx, s in enumerate(histories[user]):
        if s.get('id') == sess['id']:
            histories[user][idx] = sess
            break
    save_history(histories)

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
        ai_resp = response.json()
        # append assistant reply to history and save
        ai_content = ''
        try:
            ai_content = ai_resp.get('choices', [])[0].get('message', {}).get('content', '')
        except Exception:
            pass
        if ai_content:
            sess['messages'].append({"role": "assistant", "content": ai_content})
            # write back to store
            histories = load_history()
            for idx, s in enumerate(histories.get(user, [])):
                if s.get('id') == sess['id']:
                    histories[user][idx] = sess
                    break
            save_history(histories)
        return jsonify(ai_resp)

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


@app.route('/api/session/<session_id>')
def get_session_data(session_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    sess = find_session(user, session_id)
    if not sess:
        return jsonify({"error": "Not found"}), 404
    return jsonify(sess)


if __name__ == "__main__":
    print("=" * 50)
    print("  🚀 Nafti AI - Serveur démarré (PWA activée)")
    print(f"  📍 http://localhost:5000")
    print(f"  🤖 Modèle: {GROQ_MODEL}")
    print(f"  🔑 Clé API: {'✅ configurée' if GROQ_API_KEY else '❌ MANQUANTE'}")
    print(f"  📁 Utilisateurs: {USERS_FILE} (auto-créé)")
    print("=" * 50)
    app.run(debug=True, port=5000)