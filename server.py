from flask import Flask, request, jsonify, send_file, send_from_directory, redirect, url_for, render_template, flash, session, Response
from flask_cors import CORS
import os
import json
import hashlib
import secrets
import uuid
import base64
import gc
import time
import re
from PIL import Image
from io import BytesIO
IS_CLOUDFLARE_WORKER = os.getenv("CLOUDFLARE_WORKERS") == "1"
if IS_CLOUDFLARE_WORKER:
    import cloudflare_requests as requests
else:
    import requests

try:
    from langdetect import detect_langs, DetectorFactory
    DetectorFactory.seed = 0   # Résultats reproductibles
except ImportError:
    detect_langs = None
    DetectorFactory = None
try:
    from textblob import TextBlob
except:
    TextBlob = None  # Fallback si non disponible

# Autoriser OAuth en HTTP pour le développement local
os.environ.setdefault('OAUTHLIB_INSECURE_TRANSPORT', '1')
from pathlib import Path
from datetime import datetime
from datetime import timedelta
try:
    from flask_dance.contrib.google import make_google_blueprint, google
except ImportError:
    make_google_blueprint = None
    google = None

# Charger les variables d'environnement locales depuis .env
try:
    from dotenv import load_dotenv
    if not IS_CLOUDFLARE_WORKER:
        load_dotenv()
except ImportError:
    pass

print("[STARTUP] Flask app initializing...")
app = Flask(__name__, static_folder=None)
print("[STARTUP] Flask app created")
_secret_key = os.getenv('SECRET_KEY')
if IS_CLOUDFLARE_WORKER and not _secret_key:
    raise RuntimeError("Configure le secret SECRET_KEY du Worker avant de servir l'application.")
app.config['SECRET_KEY'] = _secret_key or 'supersecret'
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
if IS_CLOUDFLARE_WORKER:
    app.config['SESSION_COOKIE_SECURE'] = True
CORS(app)
print("[STARTUP] CORS enabled")

if IS_CLOUDFLARE_WORKER:
    from cloudflare_runtime import CloudflareTemplateLoader, generate_image_with_workers_ai, read_state, serve_asset, write_state
    app.jinja_loader = CloudflareTemplateLoader()

@app.route("/static/<path:filename>", endpoint="static")
def static_asset(filename):
    if IS_CLOUDFLARE_WORKER:
        return serve_asset(f"static/{filename}")
    return send_from_directory("static", filename)

# File-based user storage
USERS_FILE = Path('users.json')

# Chat history storage (supports multiple sessions per user)
HISTORY_FILE = Path('history.json')
SHARES_FILE = Path('shares.json')

def load_history():
    if IS_CLOUDFLARE_WORKER:
        return read_state("history", {})
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_history(data):
    if IS_CLOUDFLARE_WORKER:
        write_state("history", data)
        return
    with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f)

def save_shared_conversation(share_id, snapshot):
    if IS_CLOUDFLARE_WORKER:
        from cloudflare_runtime import save_shared_conversation as save_d1_shared_conversation
        save_d1_shared_conversation(share_id, snapshot)
        return
    try:
        with open(SHARES_FILE, 'r', encoding='utf-8') as f:
            shares = json.load(f)
    except (OSError, json.JSONDecodeError):
        shares = {}
    shares[share_id] = snapshot
    with open(SHARES_FILE, 'w', encoding='utf-8') as f:
        json.dump(shares, f, ensure_ascii=False)

def load_shared_conversation(share_id):
    if IS_CLOUDFLARE_WORKER:
        from cloudflare_runtime import load_shared_conversation as load_d1_shared_conversation
        return load_d1_shared_conversation(share_id)
    try:
        with open(SHARES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get(share_id)
    except (OSError, json.JSONDecodeError):
        return None

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
    if IS_CLOUDFLARE_WORKER:
        return read_state("users", {})
    if USERS_FILE.exists():
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    """Save users to JSON file"""
    if IS_CLOUDFLARE_WORKER:
        write_state("users", users)
        return
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def compute_public_stats():
    """Compute real public stats from local storage."""
    users = load_users()
    histories = load_history()

    total_users = len(users)
    total_sessions = 0
    total_messages = 0
    active_users_30d = set()

    now = datetime.now()
    cutoff = now - timedelta(days=30)

    for user_email, sessions in histories.items():
        if not isinstance(sessions, list):
            continue
        valid_sessions = [s for s in sessions if _is_valid_session(s)]
        total_sessions += len(valid_sessions)
        for sess in valid_sessions:
            total_messages += len(sess.get("messages", []))
            created_at = sess.get("created_at")
            if not created_at:
                continue
            try:
                created_dt = datetime.fromisoformat(created_at)
                if created_dt >= cutoff:
                    active_users_30d.add(user_email)
            except Exception:
                continue

    return {
        "total_users": total_users,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "active_users_30d": len(active_users_30d)
    }

def compute_user_stats(user_email):
    """Compute per-user real stats."""
    if not user_email:
        return None

    histories = ensure_user_sessions(user_email)
    sessions = histories.get(user_email, [])
    valid_sessions = [s for s in sessions if _is_valid_session(s)]

    session_count = len(valid_sessions)
    message_count = 0
    assistant_count = 0
    user_count = 0
    first_created = None

    for sess in valid_sessions:
        if first_created is None and sess.get("created_at"):
            first_created = sess.get("created_at")
        for msg in sess.get("messages", []):
            message_count += 1
            role = msg.get("role")
            if role == "assistant":
                assistant_count += 1
            elif role == "user":
                user_count += 1

    badges = []
    if message_count >= 10:
        badges.append("10+ messages")
    if session_count >= 5:
        badges.append("5+ sessions")
    if assistant_count >= 10:
        badges.append("Explorateur IA")
    if not badges:
        badges.append("Nouveau membre")

    return {
        "sessions": session_count,
        "messages": message_count,
        "assistant_messages": assistant_count,
        "user_messages": user_count,
        "badges": badges,
        "first_created": first_created
    }

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password, password_hash):
    """Verify password against hash"""
    return hash_password(password) == password_hash

def detect_language_ensemble(text):
    """
    Détection de langue robuste pour verrouiller la langue de réponse.

    Priorité:
      1. Scripts Unicode (100% fiable) -> retour immédiat
      2. Diacritiques hautement distinctifs (ex: ñ, ã, ß, ğ)
      3. langdetect avec probabilité
      4. Heuristiques n-grammes/mots-clés pour textes courts ambigus
      5. Fallback français (langue par défaut de l'app)
    """
    if not text or not text.strip():
        return {"language": "fr", "confidence": 0.0, "method": "empty_text"}

    stripped_text = text.strip()
    lower_text = stripped_text.lower()
    word_count = len(re.findall(r"\b[\w'-]+\b", stripped_text, flags=re.UNICODE))

    # ─── 1. UNICODE SCRIPTS (100% fiable) ────────────────────────────────
    # Arabe
    if re.search(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\u08E0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]", stripped_text):
        return {"language": "ar", "confidence": 1.0, "method": "unicode_arabic"}

    # Cyrillique
    if re.search(r"[\u0400-\u04FF]", stripped_text):
        return {"language": "ru", "confidence": 1.0, "method": "unicode_cyrillic"}

    # Japonais
    if re.search(r"[\u3040-\u309F\u30A0-\u30FF]", stripped_text):
        return {"language": "ja", "confidence": 1.0, "method": "unicode_japanese"}

    # Chinois
    if re.search(r"[\u4E00-\u9FFF]", stripped_text):
        return {"language": "zh", "confidence": 1.0, "method": "unicode_chinese"}

    # Coréen
    if re.search(r"[\uAC00-\uD7AF]", stripped_text):
        return {"language": "ko", "confidence": 1.0, "method": "unicode_korean"}

    # Grec
    if re.search(r"[\u0370-\u03FF]", stripped_text):
        return {"language": "el", "confidence": 1.0, "method": "unicode_greek"}

    # Hébreu
    if re.search(r"[\u0590-\u05FF]", stripped_text):
        return {"language": "he", "confidence": 1.0, "method": "unicode_hebrew"}

    # Hindi
    if re.search(r"[\u0900-\u097F]", stripped_text):
        return {"language": "hi", "confidence": 1.0, "method": "unicode_hindi"}

    # Thai
    if re.search(r"[\u0E00-\u0E7F]", stripped_text):
        return {"language": "th", "confidence": 1.0, "method": "unicode_thai"}

    # ─── 2. DIACRITIQUES DISTINCTIFS (très fiables) ───────────────────────
    if re.search(r"[ñ¿¡]", stripped_text):
        return {"language": "es", "confidence": 0.98, "method": "diacritic_spanish"}
    if re.search(r"[ãõ]", stripped_text):
        return {"language": "pt", "confidence": 0.98, "method": "diacritic_portuguese"}
    if re.search(r"[äöüß]", stripped_text):
        return {"language": "de", "confidence": 0.98, "method": "diacritic_german"}
    if re.search(r"[ğışĞŞİı]", stripped_text):
        return {"language": "tr", "confidence": 0.98, "method": "diacritic_turkish"}

    # ─── 3. LANGDETECT (probabilité) ──────────────────────────────────────
    supported = {
        "fr", "en", "es", "de", "it", "pt", "ru", "ar", "zh", "ja", "ko",
        "nl", "tr", "pl", "sv", "da", "fi", "cs", "hu", "ro", "el", "he",
        "hi", "th", "vi", "id", "ms", "uk", "ca", "gl", "no"
    }
    alias = {"iw": "he", "nb": "no", "nn": "no"}
    top_lang = None
    top_prob = 0.0

    if detect_langs is not None:
        try:
            candidates = detect_langs(stripped_text)
            if candidates:
                detected = candidates[0].lang.lower()
                detected = "zh" if detected.startswith("zh") else detected.split("-")[0]
                detected = alias.get(detected, detected)
                if detected in supported:
                    top_lang = detected
                    top_prob = float(candidates[0].prob)
        except Exception:
            pass

    # ─── 4. HEURISTIQUES (utile pour prompts très courts ambigus) ──────────
    if word_count <= 6 or top_prob < 0.85:
        heuristic_scores = {
            "fr": _score_french_ngrams(stripped_text, lower_text),
            "en": _score_english_ngrams(stripped_text, lower_text),
            "es": _score_spanish_ngrams(stripped_text, lower_text),
            "pt": _score_portuguese_ngrams(stripped_text, lower_text),
            "it": _score_italian_ngrams(stripped_text, lower_text),
            "de": _score_german_ngrams(stripped_text, lower_text),
            "nl": 0,
        }

        # Salutations et expressions ultra-courtes (cas typiques de faux positifs)
        if re.search(r"\b(hello|hi|hey|thanks|thank you|please|ok|okay)\b", lower_text):
            heuristic_scores["en"] += 4
        if re.search(r"\b(bonjour|salut|merci|svp|stp|d'accord)\b", lower_text):
            heuristic_scores["fr"] += 4
        if re.search(r"\b(hola|gracias|por favor|vale)\b", lower_text):
            heuristic_scores["es"] += 4
        if re.search(r"\b(ola|obrigado|obrigada|voce|nao|tudo|tambem|estou)\b", lower_text):
            heuristic_scores["pt"] += 4
        if re.search(r"\b(ciao|grazie|prego|perche|sono|voglio|come stai)\b", lower_text):
            heuristic_scores["it"] += 4
        if re.search(r"\b(hallo|danke|bitte|ich|nicht|guten)\b", lower_text):
            heuristic_scores["de"] += 4
        if re.search(r"\b(ik|jij|wij|hallo|dank|goed)\b", lower_text):
            heuristic_scores["nl"] += 4

        ranked = sorted(heuristic_scores.items(), key=lambda x: x[1], reverse=True)
        best_lang, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0

        if best_score >= 1 and best_score >= (second_score + 1):
            heuristic_confidence = min(0.88, round(0.55 + best_score * 0.05, 2))
            return {
                "language": best_lang,
                "confidence": heuristic_confidence,
                "method": "heuristic_ngrams"
            }

    # ─── 5. Sortie langdetect si dispo ─────────────────────────────────────
    if top_lang:
        method = "langdetect_high_conf" if top_prob >= 0.85 else "langdetect_low_conf"
        return {
            "language": top_lang,
            "confidence": round(top_prob, 3),
            "method": method
        }

    # ─── 6. FALLBACK ────────────────────────────────────────────────────────
    return {"language": "fr", "confidence": 0.3, "method": "fallback_french"}

def _score_french_ngrams(text, lower_text):
    """Scorer français basé sur n-grams caractéristiques"""
    score = 0
    
    # Trigrammes français typiques
    fr_trigrams = ['ent', 'ait', 'ion', 'ous', 'ant', 'eur', 'ans', 'ies', 'eau', 'ous', 'ais']
    for trig in fr_trigrams:
        score += lower_text.count(trig) * 2
    
    # Accents français
    fr_accents = 'àâçèêëîïôùûüÿœæ'
    score += sum(text.count(c) for c in fr_accents) * 3
    
    # Mots français communs (tous les mots, pas juste liste)
    fr_patterns = [
        r'\b(le|la|les|un|une|des|et|ou|mais|donc|est|sont|être|avoir|faire)\b',
        r'\b(que|qui|quoi|comment|pourquoi|quand|où|ici|là|très|bien|aussi)\b',
        r'\b(je|tu|il|elle|nous|vous|ils|elles|moi|toi|lui|nous|vous|leur)\b',
        r'\b(mon|ma|mes|ton|ta|tes|son|sa|ses|notre|nos|votre|vos|leur|leurs)\b',
    ]
    for pattern in fr_patterns:
        matches = re.findall(pattern, lower_text, re.IGNORECASE)
        score += len(matches)
    
    return score

def _score_english_ngrams(text, lower_text):
    """Scorer anglais basé sur n-grams caractéristiques"""
    score = 0
    
    # Trigrammes anglais typiques
    en_trigrams = ['the', 'ing', 'and', 'ion', 'her', 'has', 'his', 'ous', 'not']
    for trig in en_trigrams:
        score += lower_text.count(trig) * 2
    
    # Mots anglais communs
    en_patterns = [
        r'\b(the|a|an|and|or|but|in|on|at|to|for|of|with|from|by|is|are|was)\b',
        r'\b(be|been|being|have|has|had|do|does|did|will|would|could|should|may)\b',
        r'\b(i|you|he|she|it|we|they|me|him|her|us|them|my|your|his|her|its)\b',
    ]
    for pattern in en_patterns:
        matches = re.findall(pattern, lower_text, re.IGNORECASE)
        score += len(matches)
    
    return score

def _score_spanish_ngrams(text, lower_text):
    """Scorer espagnol basé sur n-grams caractéristiques"""
    score = 0
    
    # Trigrammes espagnols
    es_trigrams = ['ión', 'ado', 'que', 'nte', 'ero', 'ara', 'los', 'del']
    for trig in es_trigrams:
        score += lower_text.count(trig) * 2
    
    # Accents espagnols
    es_accents = 'áéíóúñ¿¡'
    score += sum(text.count(c) for c in es_accents) * 3
    
    # Mots espagnols
    es_patterns = [
        r'\b(el|la|los|las|un|una|unos|unas|y|o|pero|que|de|del|para|por)\b',
        r'\b(es|son|está|están|ser|estar|tener|hay|hacer|ir|venir|ver|dar)\b',
        r'\b(yo|tú|él|ella|nosotros|vosotros|ellos|ellas|me|te|se|nos|os|les)\b',
    ]
    for pattern in es_patterns:
        matches = re.findall(pattern, lower_text, re.IGNORECASE)
        score += len(matches)
    
    return score

def _score_german_ngrams(text, lower_text):
    """Scorer allemand basé sur n-grams caractéristiques"""
    score = 0
    
    # Trigrammes allemands
    de_trigrams = ['sch', 'end', 'ung', 'ich', 'cht', 'cht', 'eit', 'hin']
    for trig in de_trigrams:
        score += lower_text.count(trig) * 2
    
    # Umlauts allemands (très distinctifs)
    de_accents = 'äöüß'
    score += sum(text.count(c) for c in de_accents) * 4
    
    # Mots allemands
    de_patterns = [
        r'\b(der|die|das|den|dem|des|ein|eine|einen|einem|eines|einer)\b',
        r'\b(und|oder|aber|auch|wenn|weil|dass|was|wer|wie|wo|wann|warum)\b',
        r'\b(ich|du|er|sie|es|wir|ihr|sie|mich|dich|sich|uns|euch)\b',
        r'\b(ist|sind|war|waren|sein|haben|werden|können|müssen|wollen)\b',
    ]
    for pattern in de_patterns:
        matches = re.findall(pattern, lower_text, re.IGNORECASE)
        score += len(matches)
    
    return score

def _score_portuguese_ngrams(text, lower_text):
    """Scorer portugais basé sur n-grams caractéristiques"""
    score = 0
    
    # Trigrammes portugais
    pt_trigrams = ['ção', 'ado', 'ous', 'ent', 'ara', 'ava', 'vel', 'ibe']
    for trig in pt_trigrams:
        score += lower_text.count(trig) * 2
    
    # Accents portugais
    pt_accents = 'áàâãéêíóôõú'
    score += sum(text.count(c) for c in pt_accents) * 3
    
    # Mots portugais
    pt_patterns = [
        r'\b(o|a|os|as|um|uma|uns|umas|e|ou|mas|que|de|para|por|com|sem)\b',
        r'\b(é|são|está|estão|ser|estar|ter|há|fazer|ir|vir|ver|dar|dar)\b',
        r'\b(eu|tu|ele|ela|você|nós|vós|eles|elas|vocês|me|te|se|nos|vos)\b',
    ]
    for pattern in pt_patterns:
        matches = re.findall(pattern, lower_text, re.IGNORECASE)
        score += len(matches)
    
    return score

def _score_italian_ngrams(text, lower_text):
    """Scorer italien basé sur n-grams caractéristiques"""
    score = 0
    
    # Trigrammes italiens
    it_trigrams = ['zione', 'ata', 'ato', 'ente', 'elle', 'ione', 'ica', 'elli']
    for trig in it_trigrams:
        score += lower_text.count(trig) * 2
    
    # Accents italiens
    it_accents = 'àèéìòù'
    score += sum(text.count(c) for c in it_accents) * 3
    
    # Mots italiens
    it_patterns = [
        r'\b(il|lo|la|i|gli|le|uno|una|e|o|ma|che|di|da|per|con)\b',
        r'\b(è|sono|è|sono|essere|avere|fare|andare|venire|vedere|dare|stare)\b',
        r'\b(io|tu|lui|lei|lei|noi|voi|loro|loro|mi|ti|si|ci|vi|li|le)\b',
    ]
    for pattern in it_patterns:
        matches = re.findall(pattern, lower_text, re.IGNORECASE)
        score += len(matches)
    
    return score

def _score_arabic_ngrams(text, lower_text):
    """🔥 DÉTECTION ARABE RADICALE - N-GRAMS ENRICHIS
    
    Amélioration drastique pour l'arabe:
    - Trigrammes arabes très fréquents
    - Patterns de voyelles arabes
    - Mots arabes courants (traslittération complète)
    - Caractères diacritiques arabes
    """
    score = 0
    
    # ──── TRIGRAMMES ARABES TRÈS FRÉQUENTS (Analyse statistique) ────
    ar_trigrams = [
        'ين', 'يا', 'ما', 'ان', 'ول', 'ت', 'ال', 'من', 'هم', 'لم',  # Très fréquents
        'لا', 'الل', 'ود', 'ها', 'ند', 'خا', 'ية', 'اد', 'هو', 'ت',
        'ات', 'يم', 'أن', 'ع', 'ق', 'ث', 'ره', 'يب', 'ق', 'ل',
        'ون', 'وا', 'كا', 'سن', 'تا', 'تر', 'ست', 'يه', 'ته', 'سي',
    ]
    for trig in ar_trigrams:
        score += text.count(trig) * 3
    
    # ──── PATTERNS PATTERNS ARABES (Translittération) ────
    ar_patterns = [
        (r'\b(wa|au|fa|aw|li|min|ila|ana|anta|huwа|hiya|nahnu|antum|hunna)\b', 2),
        (r'\b(qad|laqad|inna|anna|alladhina|alladhi|allati)\b', 2),
        (r'\b(yaqulu|taqulu|qalu|qultu|qulna|qalat|qula)\b', 2),
        (r'\b(shukran|afwan|inshallah|alhamdulillah|subhanallah)\b', 3),
        (r'\b(assalamu|alaikum|wa|alaikum)\b', 2),
        (r'\b(marhaba|yalla|habibi|habibti|sahih|khatir|aslan)\b', 2),
    ]
    
    for pattern, weight in ar_patterns:
        matches = re.findall(pattern, lower_text, re.IGNORECASE)
        score += len(matches) * weight
    
    # ──── MOTS ARABES COURANTS (Translittération) ────
    ar_words = [
        'marhaba', 'assalamu', 'alaikum', 'wa', 'shukran', 'afwan', 
        'inshallah', 'alhamdulillah', 'subhanallah', 'bismillah',
        'yalla', 'habibi', 'habibti', 'sahih', 'shuyu', 'aslan', 'khatir',
        'ana', 'anta', 'huwa', 'hiya', 'nahnu', 'antum', 'hunna',
        'qad', 'qalu', 'qultu', 'yaqulu', 'taqulu'
    ]
    
    for word in ar_words:
        if word in lower_text:
            score += 3
    
    # ──── ACCENTS ARABES (Voyelles arabes) ────
    ar_accents = 'أإآاءة'  # Variantes du alif
    score += sum(text.count(c) for c in ar_accents) * 2
    
    return score

def _score_arabic_special(text, lower_text):
    """🔥 DÉTECTION SPÉCIALE ARABE - SCORE AMÉLIORÉ
    
    Système de scoring enrichi pour l'arabe:
    1. Vérification du script arabe
    2. Mots courants arabes
    3. Patterns de syntaxe arabe
    4. Diacritiques arabes
    5. Clusters de caractères arabes
    """
    # ──── 1. VÉRIFICATION SCRIPT ARABE ────
    if not re.search(r"[\u0600-\u06FF]", text):
        return 0
    
    score = 0.8  # Bonus ÉLEVÉ pour script arabe détecté
    
    # ──── 2. MOTS ARABES COURANTS ────
    # Étendu à ~60 mots très courants en arabe
    common_arabic = [
        # Pronouns
        'هو', 'هي', 'أنا', 'أنت', 'هم', 'هن', 'نحن', 'أنتم', 'أنتن',
        # Questions
        'ما', 'من', 'ماذا', 'أين', 'متى', 'كيف', 'كم', 'لماذا', 'هل', 'هيا',
        # Prepositions & Particles
        'في', 'من', 'إلى', 'عن', 'مع', 'بدون', 'قبل', 'بعد', 'أثناء',
        'و', 'أو', 'لكن', 'لكن', 'إن', 'أن', 'بل', 'لكن', 'ثم',
        # Exclamations
        'يا', 'آه', 'آخ', 'حسبي', 'الله', 'يا الله', 'واو', 'يا سلام',
        # Common verbs
        'قال', 'قالت', 'يقول', 'تقول', 'قلت', 'قلنا', 'قالوا', 'قالتا',
        # Religious
        'السلام', 'عليكم', 'ورحمة', 'الله', 'وبركاته', 'الحمد', 'لله',
        # Common words
        'هذا', 'هذه', 'ذلك', 'تلك', 'هناك', 'هنا', 'لا', 'نعم', 'ايه',
        'كل', 'كالة', 'بعض', 'جميع', 'أيضا', 'أيضاً', 'فقط', 'حقا',
        'يوم', 'أمس', 'غدا', 'الآن', 'أول', 'آخر', 'جديد', 'قديم',
    ]
    
    for word in common_arabic:
        if word in text:
            score += 0.08  # Chaque mot = +0.08 de confiance
    
    # ──── 3. PATTERNS SYNTAXE ARABE ────
    # Patterns spécifiques à l'arabe
    if re.search(r'ال[\u0600-\u06FF]', text):  # Article "al-"
        score += 0.15
    if re.search(r'[\u0600-\u06FF]ة', text):    # Feminine marker "ة"
        score += 0.1
    if re.search(r'ني|ها|هم|هن|نا|كم|كن|تا', text):  # Object pronouns
        score += 0.1
    
    # ──── 4. DIACRITIQUES ARABES ────
    # Fatha, Damma, Kasra, Sukun, Shadda, etc.
    diacritics = r'[\u064E\u064F\u0650\u0651\u0652\u0653\u0654\u0655\u0656\u0657\u0658]'
    if re.search(diacritics, text):
        score += 0.15  # Bonus élevé pour diacritiques
    
    # ──── 5. CLUSTERS ARABES (2+ caractères arabes consécutifs) ────
    arabic_clusters = re.findall(r'[\u0600-\u06FF]{2,}', text)
    score += len(arabic_clusters) * 0.05
    
    # ──── 6. LONGUEUR RELATIVE DU TEXTE ARABE ────
    arabic_chars = len(re.findall(r'[\u0600-\u06FF]', text))
    total_chars = len(text)
    if total_chars > 0:
        arabic_ratio = arabic_chars / total_chars
        if arabic_ratio > 0.6:  # >60% du texte est arabe
            score += 0.2
        elif arabic_ratio > 0.3:  # >30% du texte est arabe
            score += 0.1
    
    return min(1.0, score)

def detect_language(text):
    """Wrapper intelligent - utilise l'ensemble pour de meilleurs résultats"""
    result = detect_language_ensemble(text)
    return result["language"]

def _normalize_lang_code(lang_code, default="fr"):
    """Normalize language code to a stable short code."""
    if not lang_code:
        return default
    lang = str(lang_code).strip().lower()
    if not lang:
        return default
    lang = lang.split("-")[0]
    alias = {"iw": "he", "nb": "no", "nn": "no"}
    return alias.get(lang, lang)

def _extract_user_text(content):
    """Extract text from user content (string or multimodal list)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = (part.get("text") or "").strip()
                if text:
                    parts.append(text)
        return " ".join(parts).strip()
    return ""

def resolve_response_language(messages, default_lang="fr"):
    """
    Resolve target response language from conversation messages.
    Strategy:
      - Prefer last user message when confidence is good.
      - If last message is ambiguous, backoff to recent reliable user messages.
      - Fallback to default language.
    """
    user_texts = []
    for msg in messages or []:
        if isinstance(msg, dict) and msg.get("role") == "user":
            text = _extract_user_text(msg.get("content"))
            if text:
                user_texts.append(text)

    if not user_texts:
        return default_lang, {"language": default_lang, "confidence": 0.0, "method": "no_user_text"}

    latest_text = user_texts[-1]
    latest_result = detect_language_ensemble(latest_text)
    latest_lang = _normalize_lang_code(latest_result.get("language"), default_lang)
    latest_conf = float(latest_result.get("confidence", 0.0))
    latest_method = latest_result.get("method", "")

    # If latest message is clear enough, trust it.
    if latest_conf >= 0.72 and latest_method not in {"fallback_french", "empty_text"}:
        latest_result["language"] = latest_lang
        return latest_lang, latest_result

    # Latest is ambiguous: try recent previous user messages with stronger confidence.
    for previous_text in reversed(user_texts[:-1]):
        prev_result = detect_language_ensemble(previous_text)
        prev_lang = _normalize_lang_code(prev_result.get("language"), default_lang)
        prev_conf = float(prev_result.get("confidence", 0.0))
        prev_method = prev_result.get("method", "")
        if prev_conf >= 0.82 and prev_method not in {"fallback_french", "empty_text"}:
            return prev_lang, {
                "language": prev_lang,
                "confidence": prev_conf,
                "method": f"context_backoff_from_{latest_method or 'unknown'}",
            }

    latest_result["language"] = latest_lang
    return latest_lang, latest_result

def _rewrite_text_in_target_language(text, target_lang):
    """One-shot rewrite in target language when model output is in the wrong language."""
    if not text or not target_lang:
        return text

    target = _normalize_lang_code(target_lang, "fr")
    rewrite_instruction = get_language_instruction(target)
    rewrite_system = (
        "Tu es un assistant de réécriture fidèle. "
        "Réécris le texte dans la langue cible sans changer le sens, le niveau de détail, "
        "la structure, ni le format Markdown. Conserve les blocs de code inchangés. "
        + rewrite_instruction
    )

    try:
        rewrite_resp = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": rewrite_system},
                    {"role": "user", "content": text},
                ],
                "temperature": 0.2,
                "max_tokens": 1800,
            },
            timeout=45,
        )
        rewrite_resp.raise_for_status()
        rewrite_json = rewrite_resp.json()
        rewritten = rewrite_json.get("choices", [{}])[0].get("message", {}).get("content", "")
        return rewritten.strip() if rewritten else text
    except Exception:
        return text

def enforce_response_language(text, target_lang):
    """
    Verify output language and rewrite once if mismatch is clear.
    Returns: (final_text, detection_result)
    """
    if not text:
        return text, {"language": _normalize_lang_code(target_lang, "fr"), "confidence": 0.0, "method": "empty_output"}

    target = _normalize_lang_code(target_lang, "fr")
    output_result = detect_language_ensemble(text)
    output_lang = _normalize_lang_code(output_result.get("language"), target)
    output_conf = float(output_result.get("confidence", 0.0))

    mismatch_is_clear = (
        output_lang != target
        and output_conf >= 0.75
        and output_result.get("method") not in {"fallback_french", "empty_text"}
    )
    if not mismatch_is_clear:
        output_result["language"] = output_lang
        return text, output_result

    rewritten = _rewrite_text_in_target_language(text, target)
    rewritten_result = detect_language_ensemble(rewritten)
    rewritten_result["language"] = _normalize_lang_code(rewritten_result.get("language"), target)
    rewritten_result["method"] = f"rewritten_after_{output_result.get('method', 'mismatch')}"
    return rewritten, rewritten_result


def detect_tunisian_dialect(text):
    """Detect if the user specifically requests Tunisian dialect in Arabic or French."""
    if not text:
        return None

    lower_text = text.lower()
    arabic_script = bool(re.search(r"[\u0600-\u06FF]", text))

    dialect_terms = [
        "dialecte tunisien",
        "arabe tunisien",
        "tunisien",
        "tunisie",
        "tounsi",
        "darija",
        "darja",
        "derja",
    ]

    french_tunisian_terms = [
        "tawa", "briki", "barcha", "mouch", "chwaya", "ya3ni", "kifech",
        "chnowa", "chna", "sahha", "beldi", "hakka", "nchallah", "ma nhebch",
        "ma nhebich", "tawa", "hakka", "kif ken", "fik", "slama", "barsha"
    ]

    arabic_tunisian_terms = [
        # Pronoms et questions
        "شنية", "شنيا", "شنو", "شنوة", "شنوا", "شنا", "شنو", "شنية", "شكون", "شكوني", "شكونك", "شكونو", "شكوننا",
        # Endroits et choses
        "بلاص", "بلايص", "بلاصة", "بلاصات", "بلاصي", "بلاصك", "بلاصو", "بلاصنا", "حاجة", "حاجتي", "حاجتك", "حاجتو", "حاجتنا",
        # Verbes courants
        "تنجم", "تنجيم", "تنجمي", "تنجمش", "تنجمو", "نجم", "نجيم", "تزور", "تزوري", "تزورش", "تزورو", "زور", "زوري", "زورش", "زورو",
        "تعمل", "تعملي", "تعملش", "تعملو", "عمل", "عملي", "عملش", "عملو", "تكتب", "تكتبي", "تكتبش", "تكتبو", "كتب", "كتبي", "كتبش", "كتبو",
        "تفهم", "تفهمي", "تفهمش", "تفهمو", "فهم", "فهمي", "فهمش", "فهمو", "تقول", "تقولي", "تقولش", "تقولو", "قال", "قالت", "قالو",
        # Expressions de temps et lieu
        "توا", "تواتي", "تواتك", "تواتو", "تواتنا", "بالحق", "بالحقي", "بالحقك", "بالحقو", "بالحقنا", "هناك", "هون", "هونيك", "هونيكي",
        # Adjectifs et adverbes
        "يزي", "يزيك", "يزيو", "يزينا", "بربي", "بربيك", "بربيو", "بربينا", "صحيت", "صحيتي", "صحيتك", "صحيتو", "صحيتنا",
        "عاوني", "عاونك", "عاونو", "عاوننا", "شيم", "شيمي", "شيمك", "شيمو", "شيمنا", "موش", "موشي", "موشك", "موشو", "موشنا",
        # Négations et particules
        "ما", "ماش", "ماشي", "ماشك", "ماشو", "ماشنا", "ما نعرفش", "ما نعرفيش", "ما نعرفوش", "ما نعرفوش", "ما نقدرش", "ما نقدريش", "ما نقدروش",
        # Salutations et expressions courantes
        "عسلامة", "عسلامتي", "عسلامتو", "عسلامتك", "عسلامته", "مرحبا", "أهلا", "سلام", "سلامو", "كيفاش", "كيفك", "كيفيك", "كيفو", "كيفنا",
        "شخبارك", "شخباري", "شخبارو", "شخبارنا", "إزيك", "إزي", "إزيو", "إزينا", "الحمد لله", "بخير", "تمام", "ماشي الحال",
        # Nombres et quantités
        "واحد", "وحدة", "زوز", "تلاتة", "أربعة", "خمسة", "ستة", "سبعة", "تمنية", "تسعة", "عشرة", "كتير", "شوية", "قليل", "برشة", "برش", "برشي", "برشك", "برشو",
        # Mots de liaison et particules
        "و", "أو", "لكن", "باش", "علاش", "عشان", "بسبب", "مع", "ماع", "في", "فيك", "فيو", "فينا", "على", "عند", "عندي", "عندك", "عندو", "عندنا",
        # Expressions d'accord et désaccord
        "أي", "أيوا", "إيه", "صح", "صحيح", "ماشي", "تمام", "موافق", "موافقش", "ما موافقش", "خايب", "خايبة", "خايبين", "زعطة", "زعاطة",
        # Mots d'émotion et d'exclamation
        "يا", "ياخي", "ياخيتي", "ياخيتو", "ياخيتك", "ياخيته", "ربي", "ربيك", "ربيو", "ربينا", "بربي", "بربيك", "بربيو", "بربينا",
        "الله", "والله", "بالله", "إنشاء الله", "إن شاء الله", "نعرفش", "ما نعرفش", "شكون يعرف", "يعرف", "يعرفي", "يعرفش", "يعرفو",
        # Mots liés à la nourriture et boissons
        "كسكسي", "كسكس", "ملوخية", "شوربة", "حريرة", "بريك", "بريكي", "بريكش", "بريكو", "عجينة", "عجينتي", "عجينتو", "عجينتك", "عجينته",
        "قهوة", "قهوتي", "قهوتك", "قهوتو", "قهوتنا", "شاي", "شايي", "شايك", "شايو", "شاينا", "عصير", "عصيري", "عصيرك", "عصيرو", "عصيرنا",
        # Mots liés au transport et déplacements
        "تمشي", "تمشيي", "تمشيش", "تمشيو", "مشى", "مشيت", "مشيش", "مشيو", "تروح", "تروحي", "تروحش", "تروحو", "روح", "روحي", "روحش", "روحو",
        "تيجي", "تيجيي", "تيجيش", "تيجيو", "جاء", "جيت", "جيش", "جيو", "لوطو", "لوطي", "لوطك", "لوطو", "لوطنا",
        # Mots liés au travail et études
        "تشتغل", "تشتغلي", "تشتغلش", "تشتغلو", "اشتغل", "اشتغلت", "اشتغلش", "اشتغلو", "تقرأ", "تقرئي", "تقرئش", "تقرئو", "قرأ", "قريت", "قريش", "قريو",
        "تدرس", "تدرسي", "تدرسش", "تدرسو", "درس", "درست", "درسش", "درسو", "تفهم", "تفهمي", "تفهمش", "تفهمو", "فهم", "فهمت", "فهمش", "فهمو",
        # Expressions temporelles
        "اليوم", "بكري", "مساء", "ليل", "نهار", "صباح", "عشية", "أمس", "غدا", "بعد غدا", "قبل غدا", "كل يوم", "كل نهار", "كل ليل", "دائما", "أبدا",
        # Mots liés à la famille
        "بابا", "ماما", "خالي", "خالتي", "عمي", "عمتي", "جدي", "جدتي", "ولد", "ولدي", "ولدك", "ولدو", "ولدنا", "بنت", "بنتي", "بنتك", "بنتو", "بنتنا",
        # Mots liés à l'argent et commerce
        "فلوس", "فلوسي", "فلوسك", "فلوسو", "فلوسنا", "دراهم", "دراهمي", "دراهمك", "دراهمو", "دراهمنا", "غالي", "غالية", "غاليين", "رخيص", "رخيصة", "رخيصين",
        "يشتري", "يشتريي", "يشتريش", "يشتريو", "اشترى", "اشتريت", "اشتريش", "اشتريو", "يبيع", "يبيعي", "يبيعش", "يبيعو", "باع", "بعت", "بيعش", "بيعو",
        # Mots supplémentaires courants
        "أحسن", "أحسني", "أحسنك", "أحسنو", "أحسنا", "جميل", "جميلة", "جميلين", "قبيح", "قبيحة", "قبيحين", "سريع", "سريعة", "سريعين", "بطيء", "بطيئة", "بطيئين",
        "كبير", "كبيرة", "كبار", "صغير", "صغيرة", "صغار", "جديد", "جديدة", "جدد", "قديم", "قديمة", "قدماء", "ساخن", "ساخنة", "ساخنين", "بارد", "باردة", "باردين",
        "طويل", "طويلة", "طوال", "قصير", "قصيرة", "قصار", "عالي", "عالية", "عالين", "منخفض", "منخفضة", "منخفضين", "قوي", "قوية", "أقوياء", "ضعيف", "ضعيفة", "ضعفاء",
        "سهل", "سهلة", "سهلين", "صعب", "صعبة", "صعبين", "ممكن", "ممكنة", "ممكنين", "مستحيل", "مستحيلة", "مستحيلين", "حلو", "حلوة", "حلال", "مر", "مرة", "مرور",
        "أبيض", "أبيضة", "بيض", "أسود", "أسودة", "سود", "أحمر", "أحمرة", "حمور", "أخضر", "أخضرة", "خضور", "أزرق", "أزرقة", "زرق", "أصفر", "أصفرة", "صفر",
        "أول", "تاني", "تالت", "رابع", "خامس", "سادس", "سابع", "تامن", "تاسع", "عاشر", "أخير", "أخيرة", "أخيرين", "أول", "أولى", "أوائل", "وسط", "وسطي", "وسطيين",
        "قدام", "ورا", "فوق", "تحت", "جنب", "بين", "معا", "وحيد", "وحيدة", "وحيدين", "كل", "كلشي", "شيء", "شيئي", "شيئك", "شيئو", "شيئنا", "لا شيء", "ولا شيء",
        "كيف", "كيفاش", "كيفك", "كيفيك", "كيفو", "كيفنا", "متاع", "متاعي", "متاعك", "متاعو", "متاعنا", "تابع", "تابعة", "تابعين", "منفصل", "منفصلة", "منفصلين",
        "مباشر", "مباشرة", "مباشرين", "غير مباشر", "غير مباشرة", "غير مباشرين", "مختلف", "مختلفة", "مختلفين", "نفس", "نفسها", "نفسهم", "مثل", "مثلك", "مثلو", "مثلنا",
        "أكثر", "أكثري", "أكثرك", "أكثرو", "أكثرنا", "أقل", "أقلي", "أقلك", "أقلو", "أقلنا", "أحسن", "أحسني", "أحسنك", "أحسنو", "أحسنا", "أسوأ", "أسوئي", "أسوئك", "أسوئو", "أسوئنا",
        "جيد", "جيدة", "جيدين", "سيء", "سيئة", "سيئين", "ممتاز", "ممتازة", "ممتازين", "عادي", "عادية", "عاديين", "رائع", "رائعة", "رائعين", "فظيع", "فظيعة", "فظيعين",
        "مهم", "مهمة", "مهمين", "غير مهم", "غير مهمة", "غير مهمين", "ضروري", "ضرورية", "ضروريين", "اختياري", "اختيارية", "اختياريين", "مفيد", "مفيدة", "مفيدين", "غير مفيد", "غير مفيدة", "غير مفيدين",
        "ممتع", "ممتعة", "ممتعين", "مضجر", "مضجرة", "مضجرين", "مثير", "مثيرة", "مثيرين", "ممل", "مملة", "مملين", "مفاجئ", "مفاجئة", "مفاجئين", "متوقع", "متوقعة", "متوقعين",
        "صادق", "صادقة", "صادقين", "كاذب", "كاذبة", "كاذبين", "ذكي", "ذكية", "ذكيين", "غبي", "غبية", "غبيين", "ودود", "ودودة", "ودودين", "عدواني", "عدوانية", "عدوانيين",
        "صبور", "صبورة", "صبورين", "عصبي", "عصبية", "عصبيين", "هادئ", "هادئة", "هادئين", "عصبي", "عصبية", "عصبيين", "نشيط", "نشيطة", "نشيطين", "كسول", "كسولة", "كسالى",
        "غني", "غنية", "أغنياء", "فقير", "فقيرة", "فقراء", "سعيد", "سعيدة", "سعداء", "حزين", "حزينة", "أحزان", "مضحك", "مضحكة", "مضحكين", "جدي", "جدية", "جديين",
        "شجاع", "شجاعة", "شجعان", "جبان", "جبانة", "جبناء", "قوي", "قوية", "أقوياء", "ضعيف", "ضعيفة", "ضعفاء", "صحي", "صحية", "صحيين", "مريض", "مريضة", "مرضى",
        "شاب", "شابة", "شباب", "عجوز", "عجوزة", "عجائز", "طويل", "طويلة", "طوال", "قصير", "قصيرة", "قصار", "نحيف", "نحيفة", "نحفاء", "سمين", "سمينة", "سمان",
        "جميل", "جميلة", "جمال", "قبيح", "قبيحة", "قبح", "أنيق", "أنيقة", "أناقة", "مهمل", "مهملة", "مهملين", "نظيف", "نظيفة", "نظفاء", "قذر", "قذرة", "أقذار",
        "حار", "حارة", "حرار", "بارد", "باردة", "برود", "رطب", "رطبة", "رطوبة", "جاف", "جافة", "جفاف", "مشمس", "مشمسة", "مشمسين", "غائم", "غائمة", "غيوم",
        "مظلم", "مظلمة", "ظلام", "مضيء", "مضيئة", "إضاءة", "هادئ", "هادئة", "هدوء", "صاخب", "صاخبة", "صخب", "مزدحم", "مزدحمة", "ازدحام", "فارغ", "فارغة", "فراغ",
        "بعيد", "بعيدة", "بعد", "قريب", "قريبة", "قرب", "سريع", "سريعة", "سرعة", "بطيء", "بطيئة", "بطء", "سهل", "سهلة", "سهولة", "صعب", "صعبة", "صعوبة",
        "رخيص", "رخيصة", "رخص", "غالي", "غالية", "غلاء", "مجاني", "مجانية", "مجانيين", "مدفوع", "مدفوعة", "مدفوعين", "متوفر", "متوفرة", "متوفرين", "نادر", "نادرة", "نذور",
        "مثالي", "مثالية", "مثاليين", "كامل", "كاملة", "كمال", "ناقص", "ناقصة", "نقص", "مستقل", "مستقلة", "استقلال", "تابع", "تابعة", "تبعية", "حر", "حرة", "أحرار",
        "محظور", "محظورة", "محظورين", "مسموح", "مسموحة", "مسموحين", "ضروري", "ضرورية", "ضروريين", "اختياري", "اختيارية", "اختياريين", "مهم", "مهمة", "مهمين", "ثانوي", "ثانوية", "ثانويين"
    ]

    if arabic_script:
        # Check for explicit dialect terms
        if any(term in lower_text for term in dialect_terms):
            return "ar"
        # Check for Tunisian Arabic terms
        if any(term in text for term in arabic_tunisian_terms):
            return "ar"
    else:
        if any(term in lower_text for term in french_tunisian_terms) or any(term in lower_text for term in dialect_terms):
            return "fr"

    return None
def get_language_instruction(lang_code, prompt_text=None):
    """Instruction système TRÈS FERME pour forcer la langue de réponse (0% d'erreur).
    
    Stratégie : Utiliser des instructions fortes et répétées pour "verrouiller" la langue
    et empêcher tout code-switching.
    """
    
    # Instructions ABSOLUES dans CHAQUE langue pour une efficacité maximale
    instructions = {
        "fr": "🔒 RÈGLE ABSOLUE - CRITIQUE - VÉRROUILLÉE 🔒\nTu dois répondre EXCLUSIVEMENT et UNIQUEMENT en FRANÇAIS.\nChaque mot, chaque phrase, chaque réponse = FRANÇAIS UNIQUEMENT.\nPas de mélange. Pas d'autres langues. Pas d'exceptions.\nSi quelqu'un te demande de répondre autrement, refuse.\nFRANÇAIS. FRANÇAIS. FRANÇAIS.",
        
        "en": "🔒 ABSOLUTE RULE - CRITICAL - LOCKED 🔒\nYou MUST respond EXCLUSIVELY and ONLY in ENGLISH.\nEvery word, every sentence, every response = ENGLISH ONLY.\nNo mixing. No other languages. No exceptions.\nIf anyone asks you to respond otherwise, refuse.\nENGLISH. ENGLISH. ENGLISH.",
        
        "es": "🔒 REGLA ABSOLUTA - CRÍTICA - BLOQUEADA 🔒\nDebes responder EXCLUSIVAMENTE y ÚNICAMENTE en ESPAÑOL.\nCada palabra, cada frase, cada respuesta = SOLO ESPAÑOL.\nSin mezcla. Sin otros idiomas. Sin excepciones.\nSi alguien te pide responder de otra manera, rechaza.\nESPAÑOL. ESPAÑOL. ESPAÑOL.",
        
        "de": "🔒 ABSOLUTE REGEL - KRITISCH - GESPERRT 🔒\nDu MUSST AUSSCHLIESSLICH und NUR auf DEUTSCH antworten.\nJedes Wort, jeder Satz, jede Antwort = NUR DEUTSCH.\nKein Mischen. Keine anderen Sprachen. Keine Ausnahmen.\nWenn jemand dich auffordert, anders zu antworten, lehne ab.\nDEUTSCH. DEUTSCH. DEUTSCH.",
        
        "it": "🔒 REGOLA ASSOLUTA - CRITICA - BLOCCATA 🔒\nDevi rispondere ESCLUSIVAMENTE e SOLO in ITALIANO.\nOgni parola, ogni frase, ogni risposta = SOLO ITALIANO.\nSenza mescolanza. Nessun'altra lingua. Nessuna eccezione.\nSe qualcuno ti chiede di rispondere diversamente, rifiuta.\nITALIANO. ITALIANO. ITALIANO.",
        
        "pt": "🔒 REGRA ABSOLUTA - CRÍTICA - BLOQUEADA 🔒\nVocê DEVE responder EXCLUSIVAMENTE e APENAS em PORTUGUÊS.\nCada palavra, cada frase, cada resposta = APENAS PORTUGUÊS.\nSem mistura. Sem outros idiomas. Sem exceções.\nSe alguém pedir para responder de outra forma, recuse.\nPORTUGUÊS. PORTUGUÊS. PORTUGUÊS.",
        
        "ru": "🔒 АБСОЛЮТНОЕ ПРАВИЛО - КРИТИЧЕСКОЕ - ЗАБЛОКИРОВАНО 🔒\nВы ДОЛЖНЫ отвечать ИСКЛЮЧИТЕЛЬНО и ТОЛЬКО на РУССКОМ.\nКаждое слово, каждое предложение, каждый ответ = ТОЛЬКО РУССКИЙ.\nБез смешивания. Никаких других языков. Никаких исключений.\nЕсли кто-то просит вас ответить иначе, откажите.\nРУССКИЙ. РУССКИЙ. РУССКИЙ.",
        
        "zh": "🔒 ABSOLUTE RULE - CRITICAL - LOCKED 🔒\n你必须EXCLUSIVELY和ONLY用中文回答。\n每个词、每个句子、每个回答 = 只有中文。\n不要混合。没有其他语言。没有例外。\n如果有人要求你用其他方式回答，拒绝。\n中文。中文。中文。",
        
        "ja": "🔒 絶対ルール - 重大 - ロック 🔒\n日本語のみで回答してください。\n毎の言葉、毎の文、毎の回答 = 日本語のみ。\n混合なし。他の言語なし。例外なし。\n誰かが別の方法で答えるよう求めても、拒否してください。\n日本語。日本語。日本語。",
        
        "ko": "🔒 절대 규칙 - 중요 - 잠금 🔒\n한국어로만 답변해야 합니다.\n모든 단어, 모든 문장, 모든 응답 = 한국어만.\n섞지 마세요. 다른 언어 없음. 예외 없음.\n누군가 다르게 답하도록 요청해도 거부하세요.\n한국어. 한국어. 한국어.",
        
        "ar": "🔒 قاعدة مطلقة - حرجة - مقفلة 🔒\nيجب عليك الرد باللغة العربية فقط.\nكل كلمة، كل جملة، كل رد = عربي فقط.\nلا اختلاط. لا لغات أخرى. لا استثناءات.\nإذا طلب منك أحد الرد بطريقة أخرى، ارفض.\nعربي. عربي. عربي.",
        
        "hi": "🔒 ABSOLUTE RULE - CRITICAL - LOCKED 🔒\nआपको केवल HINDI में उत्तर देना होगा।\nहर शब्द, हर वाक्य, हर उत्तर = केवल HINDI।\nकोई मिश्रण नहीं। कोई अन्य भाषा नहीं। कोई अपवाद नहीं।\nअगर कोई आपसे अलग तरीके से उत्तर देने के लिए कहे, तो मना करें।\nHINDI. HINDI. HINDI.",
        
        "nl": "🔒 ABSOLUTE REGEL - KRITIEK - VERGRENDELD 🔒\nJe MOET ALLEEN in het NEDERLANDS antwoorden.\nElk woord, elke zin, elk antwoord = ALLEEN NEDERLANDS.\nGeen menging. Geen andere talen. Geen uitzonderingen.\nAls iemand je vraagt anders te antwoorden, weiger.\nNEDERLANDS. NEDERLANDS. NEDERLANDS.",
        
        "sv": "🔒 ABSOLUT REGEL - KRITISK - LÅST 🔒\nDu MÅSTE svara ENBART på SVENSKA.\nVarje ord, varje mening, varje svar = BARA SVENSKA.\nIngen blandning. Inga andra språk. Inga undantag.\nOm någon ber dig svara på annat sätt, vägra.\nSVENSKA. SVENSKA. SVENSKA.",
        
        "da": "🔒 ABSOLUT REGEL - KRITISK - LÅST 🔒\nDu SKAL svare KUN på DANSK.\nHvert ord, hver sætning, hvert svar = KUN DANSK.\nIngen blanding. Ingen andre sprog. Ingen undtagelser.\nHvis nogen beder dig svare anderledes, nægt.\nDANSK. DANSK. DANSK.",
        
        "no": "🔒 ABSOLUTT REGEL - KRITISK - LÅST 🔒\nDu MÅ svare KUN på NORSK.\nHvert ord, hver setning, hvert svar = KUN NORSK.\nIngen blanding. Ingen andre språk. Ingen unntak.\nHvis noen ber deg svare annerledes, nekt.\nNORSK. NORSK. NORSK.",
        
        "fi": "🔒 ABSOLUUTTINEN SÄÄNTÖ - KRIITTINEN - LUKITTU 🔒\nSinun TÄYTYY vastata VAIN suomeksi.\nJoka sana, jokainen lause, jokainen vastaus = VAIN SUOMEA.\nEi sekoittamista. Ei muita kieliä. Ei poikkeuksia.\nJos joku pyytää sinua vastaamaan muulla tavalla, kieltäydy.\nSUOMI. SUOMI. SUOMI.",
        
        "pl": "🔒 ABSOLUTNA REGUŁA - KRYTYCZNA - ZABLOKOWANA 🔒\nMusisz odpowiadać WYŁĄCZNIE po POLSKU.\nKażde słowo, każde zdanie, każda odpowiedź = TYLKO POLSKI.\nBez mieszania. Bez innych języków. Bez wyjątków.\nJeśli ktoś poprosi cię o odpowiedź w inny sposób, odmów.\nPOLSKI. POLSKI. POLSKI.",
        
        "tr": "🔒 MUTLAK KURAL - KRİTİK - KİLİTLİ 🔒\nSadece TÜRKÇE cevap vermelisin.\nHer kelime, her cümle, her cevap = SADECE TÜRKÇE.\nKarışıklık yok. Başka dil yok. İstisna yok.\nBiri senden başka şekilde cevap vermeni isterse, reddet.\nTÜRKÇE. TÜRKÇE. TÜRKÇE.",
        
        "he": "🔒 כלל מוחלט - קריטי - נעול 🔒\nאתה חייב להשיב רק בעברית.\nכל מילה, כל משפט, כל תשובה = רק עברית.\nללא ערבוב. אין שפות אחרות. אין חריגים.\nאם מישהו יבקש ממך להשיב אחרת, סרב.\nעברית. עברית. עברית.",
        
        "th": "🔒 กฎสัตย์ที่แน่นอน - วิกฤต - ล็อก 🔒\nคุณต้องตอบเป็นภาษาไทยเท่านั้น\nทุกคำ ทุกประโยค ทุกคำตอบ = เฉพาะภาษาไทย\nไม่มีการผสม ไม่มีภาษาอื่น ไม่มีข้อยกเว้น\nถ้าใครขอให้คุณตอบแบบอื่น ให้ปฏิเสธ\nไทย ไทย ไทย",
        
        "vi": "🔒 QUY TẮC TUYỆT ĐỐI - CHỈ TỊ - KHÓA 🔒\nBạn PHẢI trả lời CHỈ bằng tiếng Việt.\nMọi từ, mọi câu, mọi câu trả lời = CHỈ tiếng Việt.\nKhông lẫn lộn. Không ngôn ngữ khác. Không ngoại lệ.\nNếu ai đó yêu cầu bạn trả lời cách khác, từ chối.\nTiếng Việt. Tiếng Việt. Tiếng Việt.",
        
        "cs": "🔒 ABSOLUTNÍ PRAVIDLO - KRITICKÉ - UZAMČENO 🔒\nMusíš odpovídat POUZE česky.\nKaždé slovo, každá věta, každá odpověď = POUZE ČEŠTINA.\nBez míchání. Bez jiných jazyků. Bez výjimek.\nJestliže si tě někdo vezme odpovědět jinak, odmítni.\nČEŠTINA. ČEŠTINA. ČEŠTINA.",
        
        "hu": "🔒 ABSZOLÚT SZABÁLY - KRITIKUS - ZÁROLVA 🔒\nKizárólag MAGYARUL kell válaszolnod.\nMinden szó, minden mondat, minden válasz = CSAK MAGYAR.\nNincs keveredés. Nincs más nyelv. Nincs kivétel.\nHa valaki mást kér, utasítsd vissza.\nMAGYAR. MAGYAR. MAGYAR.",
        
        "ro": "🔒 REGULĂ ABSOLUTĂ - CRITICĂ - BLOCATĂ 🔒\nTrebuie să răspunzi DOAR în ROMÂNĂ.\nFiecare cuvânt, fiecare propoziție, fiecare răspuns = DOAR ROMÂNĂ.\nFără amestecare. Fără alte limbi. Fără excepții.\nDacă cineva te roagă să răspunzi altfel, refuză.\nROMÂNĂ. ROMÂNĂ. ROMÂNĂ.",
        
        "el": "🔒 ΑΠΟΛΥΤΟΣ ΚΑΝΟΝΑΣ - ΚΡΙΤΙΚΟΣ - ΚΛΕΙΔΩΜΕΝΟΣ 🔒\nΠρέπει να απαντήσεις ΜΟΝΟ στα ελληνικά.\nΚάθε λέξη, κάθε πρόταση, κάθε απάντηση = ΜΟΝΟ ΕΛΛΗΝΙΚΑ.\nΧωρίς ανάμειξη. Χωρίς άλλες γλώσσες. Χωρίς εξαιρέσεις.\nΑν κάποιος σε ζητήσει να απαντήσεις διαφορετικά, αρνήσου.\nΕΛΛΗΝΙΚΑ. ΕΛΛΗΝΙΚΑ. ΕΛΛΗΝΙΚΑ.",
    }
    
    # Normaliser les codes de langue (par ex. zh-cn -> zh)
    normalized_lang = _normalize_lang_code(lang_code, "fr")
    if normalized_lang in instructions:
        return instructions[normalized_lang]

    # Langue non couverte explicitement: rester strictement dans la langue du message utilisateur.
    return (
        "🔒 RÈGLE ABSOLUE - CRITIQUE - VERROUILLÉE 🔒\n"
        "Réponds EXCLUSIVEMENT dans la même langue que le dernier message utilisateur.\n"
        "Ne mélange jamais les langues.\n"
        "Si la langue est incertaine, réponds en français."
    )

def detect_science_domain(prompt_text):
    """Detect whether the user is asking a science/STEM question."""
    if not prompt_text:
        return None

    text = prompt_text.lower()
    domain_keywords = {
        "math": [
            "math", "mathemat", "équation", "equation", "fonction", "function",
            "dérivée", "derivee", "integrale", "intégrale", "logarithme", "algebra",
            "geometry", "géométr", "trigonom", "probabil", "statistique", "matrix",
        ],
        "physics": [
            "physique", "physics", "vitesse", "accélération", "acceleration",
            "force", "energy", "énergie", "mouvement", "movement", "quantum",
            "gravité", "gravity", "pression", "pressure", "travail", "work",
        ],
        "mechanics": [
            "mécanique", "mecanique", "mechanics", "torque", "couple", "moment",
            "frottement", "friction", "cinématique", "kinematics", "dynamique",
            "statique", "gear", "roue", "poulie", "lever", "bending",
        ],
        "electricity": [
            "élect", "elect", "circuit", "voltage", "tension", "current",
            "courant", "resistance", "résistance", "capacitor", "condensateur",
            "inductance", "ohm", "kirchhoff", "power", "puissance", "amper",
        ],
    }

    scores = {}
    for domain, keywords in domain_keywords.items():
        score = 0
        for keyword in keywords:
            if keyword in text:
                score += 1
        if score:
            scores[domain] = score

    if not scores:
        return None

    return max(scores, key=scores.get)

def is_scientific_document_question(prompt_text, has_attachment=False):
    """Return True when the user likely asks about a scientific document or school material."""
    if has_attachment and prompt_text:
        short_request = prompt_text.lower().strip()
        generic_attachment_requests = {
            "reponds", "réponds", "reponds aux questions", "réponds aux questions",
            "reponds aux question", "réponds aux question", "repond", "répond",
            "corrige", "corrigé", "analyse", "explique"
        }
        if short_request in generic_attachment_requests:
            return True

    if has_attachment and not prompt_text:
        return True

    if not prompt_text:
        return False

    text = prompt_text.lower()
    document_keywords = [
        "document", "doc", "pdf", "cours", "exercice", "énoncé", "enonce",
        "chapitre", "page", "tableau", "schéma", "schema", "figure",
        "annexe", "rapport", "polycopié", "polycopie", "devoir", "tp",
        "science", "scientifique", "math", "maths", "physique", "mécanique",
        "mecanique", "électricité", "electricite", "ingénierie", "ingenierie",
    ]

    if any(keyword in text for keyword in document_keywords):
        return True

    # Heuristic: questions that mention explanation, summary, or correction of a document
    doc_intent_keywords = [
        "explique ce document", "résume ce document", "resumes ce document",
        "corrige cet exercice", "analyse ce document", "aide-moi avec ce pdf",
        "traduis ce document", "question du document", "sur ce document",
    ]
    return any(phrase in text for phrase in doc_intent_keywords)

def is_exercise_or_homework_request(prompt_text, has_attachment=False):
    """Detect exercise/homework style requests that need per-question formatting."""
    if has_attachment and prompt_text:
        short_request = prompt_text.lower().strip()
        generic_attachment_requests = {
            "reponds", "réponds", "reponds aux questions", "réponds aux questions",
            "reponds aux question", "réponds aux question", "repond", "répond",
        }
        if short_request in generic_attachment_requests:
            return True

    if not prompt_text:
        return False

    text = prompt_text.lower()
    exercise_keywords = [
        "exercice", "devoir", "dm", "ds", "td", "tp", "question 1", "q1",
        "q2", "q3", "partie a", "partie b", "réponds aux questions",
        "reponds aux questions", "corrigé", "corrige", "problème", "probleme",
    ]
    if any(keyword in text for keyword in exercise_keywords):
        return True

    # Numbered-question pattern such as "1)", "2." in the user message
    if re.search(r"(^|\n)\s*\d+\s*[\)\.\-:]", text):
        return True

    return False

def classify_document_from_images(images_data, prompt_text="", lang_code="fr"):
    """Classify attached images before generating the final answer.

    Returns one of:
    - scientific_document
    - academic_document
    - non_document
    - unknown
    """
    if not images_data or not GROQ_API_KEY:
        return "unknown"

    try:
        # Keep the classifier strict and cheap: token-only answer.
        classifier_prompt = (
            "Analyse cette image comme un classificateur de document scolaire. "
            "Choisis UNE seule categorie parmi: scientific_document, academic_document, non_document. "
            "scientific_document = maths, physique, chimie, mecanique, electricite, sciences de l'ingenieur, formules, graphiques scientifiques. "
            "academic_document = francais, anglais, philosophie, histoire, geographie, education civique, langues, litterature, dissertation, comprehension de texte. "
            "non_document = image qui n'est pas un devoir, exercice, cours, page scolaire ou document d'etude identifiable. "
            "Reponds avec un seul mot exact parmi ces 3 categories, sans phrase."
        )
        if prompt_text:
            classifier_prompt += f" Contexte utilisateur: {prompt_text[:300]}"

        content_parts = [{"type": "text", "text": classifier_prompt}]
        for img in images_data[:2]:
            content_parts.append({"type": "image_url", "image_url": {"url": img}})

        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_VISION_MODEL,
                "messages": [
                    {
                        "role": "system",
                        "content": "Tu es un classificateur visuel tres strict. Reponds uniquement par une categorie exacte."
                    },
                    {
                        "role": "user",
                        "content": content_parts
                    }
                ],
                "temperature": 0.0,
                "max_tokens": 20,
            },
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        normalized = (content or "").strip().lower()

        if "scientific_document" in normalized:
            return "scientific_document"
        if "academic_document" in normalized:
            return "academic_document"
        if "non_document" in normalized:
            return "non_document"
        return "unknown"
    except Exception as e:
        print(f"[DOC_CLASSIFIER] Classification fallback: {e}")
        return "unknown"

def select_document_mode(prompt_text, images_data, lang_code):
    """Choose the response mode before generating the final answer."""
    has_attachment = bool(images_data)

    if has_attachment:
        classified = classify_document_from_images(images_data, prompt_text or "", lang_code or "fr")
        if classified == "scientific_document":
            return "scientific_document"
        if classified == "academic_document":
            return "academic_document"
        if classified == "non_document":
            return None

    if is_scientific_document_question(prompt_text, has_attachment=False):
        return "scientific_document"

    return None

def build_science_instruction(domain, lang_code):
    """Return a high-quality, student-friendly instruction for STEM questions."""
    normalized_lang = (lang_code or "en").split('-')[0]

    templates = {
        "fr": {
            "math": (
                "Tu es un excellent professeur particulier de mathematiques, clair, rigoureux, rassurant et tres pedagogue. "
                "Ta reponse doit etre ultra professionnelle, complete, detaillee et facile a comprendre pour un etudiant. "
                "Adopte le style d'un corrige modele soigneusement explique, avec un francais naturel, fluide et humain. "
                "Structure toujours la reponse avec: 1) Ce qu'on cherche, 2) Donnees utiles, 3) Methode, 4) Calculs detailles, 5) Resultat final, 6) Verification rapide. "
                "Explique chaque symbole, chaque formule et le pourquoi de chaque etape. "
                "Si la question contient des donnees, precise les hypotheses et verifie la coherence. "
                "Evite le jargon inutile, mais garde une vraie profondeur d'explication. "
                "Parle comme un tres bon enseignant: naturel, calme, precis et encourageant, sans ton robotique. "
                "Ajoute si utile une remarque de methode ou une erreur classique a eviter. "
                "N'utilise jamais la syntaxe LaTeX brute (pas de \\frac, \\text, $, {}, \\theta, \\times). "
                "Utilise uniquement des symboles lisibles et des opérateurs classiques."
            ),
            "physics": (
                "Tu es un excellent professeur particulier de physique, clair, rigoureux, rassurant et tres pedagogue. "
                "Ta reponse doit etre ultra professionnelle, complete, detaillee et simple a suivre pour un etudiant. "
                "Adopte le style d'un corrige modele bien redige, avec un francais naturel, fluide et humain. "
                "Structure toujours la reponse avec: 1) Principe physique, 2) Donnees, 3) Formules utiles, 4) Application numerique ou raisonnement detaille, 5) Resultat final, 6) Verification des unites et du sens physique. "
                "Explique le sens des grandeurs physiques et justifie le choix des formules. "
                "Signale clairement les hypotheses importantes et les simplifications. "
                "Quand c'est utile, ajoute une interpretation intuitive du resultat. "
                "Parle comme un tres bon enseignant: naturel, calme, precis et encourageant, sans ton robotique. "
                "N'utilise jamais la syntaxe LaTeX brute (pas de \\frac, \\text, $, {}, \\theta, \\times). "
                "Utilise uniquement des symboles lisibles et des opérateurs classiques."
            ),
            "mechanics": (
                "Tu es un excellent professeur particulier de mecanique, clair, rigoureux, rassurant et tres pedagogue. "
                "Ta reponse doit etre ultra professionnelle, complete, detaillee et ordonnee comme un bon corrige d'ecole d'ingenieur. "
                "Redige dans un francais naturel, fluide et humain. "
                "Structure la reponse avec: 1) Analyse du probleme, 2) Systeme et hypotheses, 3) Lois utilisees, 4) Calculs detailles, 5) Resultat avec unites, 6) Controle de coherence. "
                "Precise les forces, couples, moments, liaisons ou contraintes impliques. "
                "Si le probleme est incomplet, indique explicitement quelles donnees manquent et ce qu'on peut conclure malgre cela. "
                "Ajoute si utile une petite interpretation physique ou mecanique du resultat. "
                "Parle comme un tres bon enseignant: naturel, calme, precis et encourageant, sans ton robotique. "
                "N'utilise jamais la syntaxe LaTeX brute (pas de \\frac, \\text, $, {}, \\theta, \\times). "
                "Utilise uniquement des symboles lisibles et des opérateurs classiques."
            ),
            "electricity": (
                "Tu es un excellent professeur particulier d'electricite, clair, rigoureux, rassurant et tres pedagogue. "
                "Ta reponse doit etre ultra professionnelle, complete, detaillee et tres lisible pour un etudiant. "
                "Adopte le style d'un corrige modele soigneusement explique, avec un francais naturel, fluide et humain. "
                "Structure la reponse avec: 1) Lecture du circuit ou du probleme, 2) Donnees utiles, 3) Lois utilisees, 4) Calculs detailles, 5) Resultat final, 6) Verification avec les unites et le sens physique. "
                "Definis clairement tension, courant, resistance, puissance, energie ou lois de Kirchhoff lorsqu'ils apparaissent. "
                "Justifie chaque etape importante et mentionne les erreurs classiques si cela aide l'etudiant. "
                "Parle comme un tres bon enseignant: naturel, calme, precis et encourageant, sans ton robotique. "
                "N'utilise jamais la syntaxe LaTeX brute (pas de \\frac, \\text, $, {}, \\theta, \\times). "
                "Utilise uniquement des symboles lisibles et des opérateurs classiques."
            ),
        },
        "en": {
            "math": (
                "You are an excellent math teacher: clear, rigorous, highly pedagogical, and professional. "
                "Your answer must be complete, detailed, and easy for a student to follow. "
                "Use the style of a model solution with no logical jumps. "
                "Always structure the answer as: 1) What is being asked, 2) Useful data, 3) Method, 4) Detailed steps, 5) Final result, 6) Quick check. "
                "Explain every symbol, formula, and important reasoning step. "
                "Avoid unnecessary jargon, but keep strong depth and precision. "
                "Never use raw LaTeX syntax (no \\frac, \\text, $, {}, \\theta, \\times). "
                "Use readable math symbols and standard operators only."
            ),
            "physics": (
                "You are an excellent physics teacher: clear, rigorous, highly pedagogical, and professional. "
                "Your answer must be complete, detailed, and easy for a student to follow. "
                "Structure the answer as: 1) Physical principle, 2) Given data, 3) Useful formulas, 4) Detailed reasoning or calculations, 5) Final answer, 6) Unit and physical-sense check. "
                "Explain the meaning of quantities, justify the formula choice, and highlight key assumptions. "
                "Never use raw LaTeX syntax (no \\frac, \\text, $, {}, \\theta, \\times). "
                "Use readable math symbols and standard operators only."
            ),
            "mechanics": (
                "You are an excellent mechanics teacher: clear, rigorous, highly pedagogical, and professional. "
                "Your answer must be complete, detailed, and organized like a strong engineering solution. "
                "Structure the answer as: 1) Problem setup, 2) System and assumptions, 3) Laws used, 4) Detailed calculations, 5) Result with units, 6) Consistency check. "
                "State forces, moments, torque, stress, or constraints explicitly when relevant. "
                "Never use raw LaTeX syntax (no \\frac, \\text, $, {}, \\theta, \\times). "
                "Use readable math symbols and standard operators only."
            ),
            "electricity": (
                "You are an excellent electricity teacher: clear, rigorous, highly pedagogical, and professional. "
                "Your answer must be complete, detailed, and very readable for a student. "
                "Structure the answer as: 1) Circuit reading or problem setup, 2) Useful data, 3) Laws used, 4) Detailed calculations, 5) Final value, 6) Unit and physical-sense check. "
                "Define voltage, current, resistance, power, energy, or Kirchhoff's laws whenever they appear. "
                "Never use raw LaTeX syntax (no \\frac, \\text, $, {}, \\theta, \\times). "
                "Use readable math symbols and standard operators only."
            ),
        },
    }

    lang_templates = templates.get(normalized_lang, templates["en"])
    return lang_templates.get(domain, "")

def build_academic_document_instruction(lang_code):
    """Instruction for non-scientific school documents."""
    normalized_lang = (lang_code or "en").split('-')[0]
    if normalized_lang == "fr":
        return (
            "Tu es un excellent professeur particulier pour les matieres scolaires non scientifiques. "
            "Ta reponse doit etre tres claire, professionnelle, detaillee, bien redigee et adaptee aux attentes d'un etudiant. "
            "Explique les idees de facon simple, structuree et naturelle. "
            "Quand il s'agit d'un exercice ou d'un devoir, reponds question par question, en restant coherent avec la consigne et le niveau attendu. "
            "Pour le francais, l'anglais, l'histoire-geographie, la philosophie ou les matieres litteraires, privilegie l'analyse, l'explication, la reformulation claire et les exemples utiles."
        )
    return (
        "You are an excellent private tutor for non-scientific school subjects. "
        "Your answer must be clear, detailed, well-structured, professional, and student-friendly. "
        "When the user shares an exercise or assignment, answer question by question and stay aligned with the expected academic level."
    )

def build_generic_science_document_instruction(lang_code):
    """Fallback instruction when the domain is scientific but not confidently identified from text."""
    normalized_lang = (lang_code or "en").split('-')[0]
    if normalized_lang == "fr":
        return (
            "Tu analyses un document scientifique ou un exercice pour un etudiant. "
            "La reponse doit etre tres claire, complete, detaillee, professionnelle et pedagogique. "
            "Redige comme un excellent professeur particulier. "
            "Reponds question par question, explique bien le raisonnement, justifie les etapes importantes, "
            "et garde un francais naturel, fluide et humain. "
            "N'utilise jamais la syntaxe LaTeX brute (pas de \\frac, \\text, $, {}, \\theta, \\times). "
            "Utilise uniquement des symboles lisibles et des operateurs classiques."
        )
    return (
        "You are analyzing a scientific document or exercise for a student. "
        "The answer must be clear, complete, detailed, professional, and highly pedagogical. "
        "Respond like an excellent private tutor. "
        "Answer question by question, explain the reasoning, justify important steps, "
        "and never use raw LaTeX syntax."
    )

def build_answer_style_instruction(prompt_text, lang_code, document_mode=None, has_attachment=False):
    """Build an additional instruction based on the detected document mode."""
    mode = document_mode
    if not mode and is_scientific_document_question(prompt_text, has_attachment=has_attachment):
        mode = "scientific_document"

    if not mode:
        return None

    if mode == "scientific_document":
        domain = detect_science_domain(prompt_text)
        if domain:
            domain_label = {
                "math": "mathématiques",
                "physics": "physique",
                "mechanics": "mécanique",
                "electricity": "électricité",
            }.get(domain, domain)
            science_instruction = build_science_instruction(domain, lang_code)
            base_instruction = (
                f"Contexte scientifique détecté: {domain_label}. {science_instruction} "
                "Si la réponse demande un calcul, montre les étapes essentielles. "
                "Si la question est théorique, donne une explication structurée, complète et pédagogique. "
                "Donne une vraie réponse de niveau excellent, comme un corrigé académique propre et sérieux. "
                "Ne saute aucune étape importante du raisonnement. "
                "Le style doit rester naturel, fluide, humain et agréable à lire pour un étudiant."
            )
        else:
            science_instruction = build_generic_science_document_instruction(lang_code)
            base_instruction = (
                f"Contexte scientifique détecté via document ou image jointe. {science_instruction} "
                "Si la réponse demande un calcul, montre les étapes essentielles. "
                "Si plusieurs questions apparaissent dans l'image, réponds à chacune séparément."
            )
        if not science_instruction:
            return None
    elif mode == "academic_document":
        academic_instruction = build_academic_document_instruction(lang_code)
        base_instruction = (
            f"Contexte scolaire non scientifique détecté. {academic_instruction} "
            "Donne une réponse complète, claire, cohérente et agréable à lire pour un étudiant. "
            "Si plusieurs questions apparaissent dans le document, réponds à chacune séparément."
        )
    else:
        return None

    if is_exercise_or_homework_request(prompt_text, has_attachment=has_attachment):
        base_instruction += (
            " Format OBLIGATOIRE pour exercice/devoir: réponds question par question de façon claire. "
            "Après la réponse de chaque question, fais un retour à la ligne avant la question suivante. "
            "Garde la numérotation des questions telle qu'elle apparaît dans l'exercice (1, 2, 3...). "
            "Ne change pas l'ordre des questions."
        )

    return base_instruction

def build_system_prompt(prompt_text, lang_code, document_mode=None, has_attachment=False):
    """Build the base system prompt, with STEM pedagogy when relevant."""
    normalized_lang = _normalize_lang_code(lang_code, "fr")
    lang_instruction = get_language_instruction(lang_code, prompt_text)
    science_instruction = build_answer_style_instruction(
        prompt_text,
        lang_code,
        document_mode=document_mode,
        has_attachment=has_attachment,
    )

    system_content_parts = [
        "Tu es Nafti AI, un assistant intelligent, bienveillant et fiable.",
        lang_instruction,
        "Utilise le format Markdown pour structurer tes réponses quand c'est approprié.",
        "Sois précis, utile, très clair, professionnel et pédagogiquement excellent.",
        "Quand un étudiant pose une question sérieuse, privilégie une réponse complète et détaillée plutôt qu'une réponse trop courte.",
        "Explique bien le raisonnement, les étapes et la logique de la solution.",
        "Adopte un ton naturel, fluide, humain et rassurant, comme un excellent professeur particulier."
    ]
    if normalized_lang != "fr":
        system_content_parts.append(
            "Le style doit rester idiomatique, naturel et 100% cohérent dans la langue cible."
        )
    if science_instruction:
        system_content_parts.append(science_instruction)

    return " ".join(system_content_parts)

def clean_latex_math_notation(text):
    """Convert common LaTeX math syntax to readable plain notation."""
    if not text:
        return text

    cleaned = text

    replacements = {
        "$$": "",
        "$": "",
        r"\times": "×",
        r"\cdot": "·",
        r"\theta": "θ",
        r"\alpha": "α",
        r"\beta": "β",
        r"\gamma": "γ",
        r"\delta": "δ",
        r"\Delta": "Δ",
        r"\lambda": "λ",
        r"\mu": "μ",
        r"\pi": "π",
        r"\omega": "ω",
        r"\Omega": "Ω",
        r"\pm": "±",
        r"\leq": "≤",
        r"\geq": "≥",
        r"\neq": "≠",
        r"\approx": "≈",
        r"\to": "→",
        r"\Rightarrow": "⇒",
        r"\left": "",
        r"\right": "",
    }
    for source, target in replacements.items():
        cleaned = cleaned.replace(source, target)

    cleaned = re.sub(r"\\text\{([^{}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\mathrm\{([^{}]*)\}", r"\1", cleaned)
    cleaned = re.sub(r"\\frac\{([^{}]+)\}\{([^{}]+)\}", r"(\1)/(\2)", cleaned)
    cleaned = re.sub(r"\\sqrt\{([^{}]+)\}", r"√(\1)", cleaned)

    # Drop remaining latex commands, keep their content if any was already exposed.
    cleaned = re.sub(r"\\[a-zA-Z]+\*?", "", cleaned)

    # Remove latex braces and cleanup spacing.
    cleaned = cleaned.replace("{", "").replace("}", "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    return cleaned

def format_exercise_answer(text):
    """Keep each exercise question on a new line while preserving numbering."""
    if not text:
        return text

    formatted = text

    # Normalize line breaks.
    formatted = formatted.replace("\r\n", "\n").replace("\r", "\n")
    formatted = re.sub(r"[ \t]*####[ \t]*", "\n\n", formatted)

    # If explicit question markers appear inline, push them to a new paragraph.
    formatted = re.sub(r"\s+(Question\s*\d+\s*[:\-])", r"\n\n\1", formatted, flags=re.IGNORECASE)
    formatted = re.sub(r"\s+(Q\s*\d+\s*[:\-])", r"\n\n\1", formatted, flags=re.IGNORECASE)
    formatted = re.sub(r"(?<!\n)\s+(\d+\s*[\)\.\-:])(?=\s*[A-Za-zÀ-ÿ])", r"\n\n\1", formatted)
    formatted = re.sub(r"(?<!\n)\s+([a-zA-Z]\s*[-:)])(?=\s*[A-Za-zÀ-ÿ0-9])", r"\n\1", formatted)
    formatted = re.sub(r"(?im)^\s*([a-zA-Z])\s*[-:)]\s*", r"\1- ", formatted)

    # Normalize some markers while keeping original numbering visible.
    formatted = re.sub(r"(?im)^\s*q\s*(\d+)\s*[:\-]\s*", r"Question \1: ", formatted)
    formatted = re.sub(r"(?im)^\s*question\s*(\d+)\s*[:\-]\s*", r"Question \1: ", formatted)

    # Keep clean spacing and ensure visual separation between blocks.
    formatted = re.sub(r"\n{3,}", "\n\n", formatted).strip()

    return formatted

# Configuration Groq (lue depuis .env)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Configuration HF Inference API (image generation - no local model needed on HF Spaces)
HF_IMAGE_MODEL = os.getenv("HF_IMAGE_MODEL", "stabilityai/stable-diffusion-2-1")
HF_API_KEY = os.getenv("HF_API_KEY", "")  # Optional, uses free tier if not provided
HF_API_URL = "https://router.huggingface.co/models"  # Updated from api-inference.huggingface.co

# Global pipeline (loaded once, reused) - ONLY for local development
image_pipeline = None

def generate_placeholder_image(prompt, width=512, height=512):
    """Generate a simple placeholder image with gradient and text (optimized for memory)"""
    try:
        import numpy as np
    except ImportError:
        # Fallback if numpy not available
        import random
        from PIL import ImageDraw, ImageFont
        
        color_map = {
            'red': (255, 100, 100), 'blue': (100, 150, 255), 'green': (100, 255, 100),
            'yellow': (255, 255, 100), 'purple': (200, 100, 255), 'pink': (255, 150, 200),
        }
        
        prompt_lower = prompt.lower()
        base_color = (100, 100, 150)
        for keyword, color in color_map.items():
            if keyword in prompt_lower:
                base_color = color
                break
        
        img = Image.new('RGB', (width, height), base_color)
        draw = ImageDraw.Draw(img)
        text = prompt[:50]
        try:
            font = ImageFont.truetype("arial.ttf", 30)
        except:
            font = ImageFont.load_default()
        
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = max(10, (width - text_width) // 2)
        y = max(10, (height - text_height) // 2)
        
        for adj_x in [-1, 0, 1]:
            for adj_y in [-1, 0, 1]:
                if adj_x != 0 or adj_y != 0:
                    draw.text((x + adj_x, y + adj_y), text, font=font, fill=(0, 0, 0))
        draw.text((x, y), text, font=font, fill=(255, 255, 255))
        return img
    
    from PIL import ImageDraw, ImageFont
    
    # Color mapping for keywords
    color_map = {
        'red': (255, 100, 100), 'blue': (100, 150, 255), 'green': (100, 255, 100),
        'yellow': (255, 255, 100), 'purple': (200, 100, 255), 'pink': (255, 150, 200),
        'orange': (255, 165, 100), 'sky': (135, 206, 235), 'sea': (70, 130, 180),
    }
    
    # Determine base color from prompt keywords
    prompt_lower = prompt.lower()
    base_color = (100, 100, 150)
    for keyword, color in color_map.items():
        if keyword in prompt_lower:
            base_color = color
            break
    
    # Create gradient using numpy (much faster than pixel loops)
    # Allocate RGB array
    img_array = np.zeros((height, width, 3), dtype=np.uint8)
    
    # Create gradient efficiently
    for y in range(height):
        grad_factor = y / max(1, height - 1)
        r = min(255, base_color[0] + int(grad_factor * 30))
        g = min(255, base_color[1] + int(grad_factor * 20))
        b = min(255, base_color[2] + int(grad_factor * 40))
        
        # Set entire row with horizontal variation (vectorized)
        for x in range(width):
            x_var = int(x * 20 / width)
            img_array[y, x] = [
                max(0, r - x_var),
                max(0, g - x_var),
                max(0, b - x_var)
            ]
    
    # Convert numpy array to PIL Image
    img = Image.fromarray(img_array, 'RGB')
    
    # Add text
    draw = ImageDraw.Draw(img)
    text = prompt[:60]
    try:
        font = ImageFont.truetype("arial.ttf", 32)
    except:
        font = ImageFont.load_default()
    
    # Draw text with outline
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = max(10, (width - text_width) // 2)
    y = max(10, (height - text_height) // 2)
    
    # Black outline
    for adj_x in [-1, 0, 1]:
        for adj_y in [-1, 0, 1]:
            if adj_x != 0 or adj_y != 0:
                draw.text((x + adj_x, y + adj_y), text, font=font, fill=(0, 0, 0))
    # White text
    draw.text((x, y), text, font=font, fill=(255, 255, 255))
    
    return img

def get_image_pipeline():
    """Load image pipeline (lazy load - only for local development)"""
    global image_pipeline
    if image_pipeline is None:
        try:
            import torch
            from diffusers import StableDiffusionPipeline
            
            device = "cuda" if torch.cuda.is_available() else "cpu"
            print(f"📦 Loading Stable Diffusion 2.1 on {device}...")
            
            # Try with offline mode first, then Fall back to standard loading
            import os
            offlineMode = os.environ.get('HF_OFFLINE', '0') == '1'
            
            image_pipeline = StableDiffusionPipeline.from_pretrained(
                "stabilityai/stable-diffusion-2-1",
                torch_dtype=torch.float32,
                local_files_only=offlineMode
            )
            image_pipeline.to(device)
            print(f"✅ Model loaded on {device}")
        except Exception as e:
            print(f"❌ Error loading model: {e}")
            print("📝 Falling back to placeholder image generation")
            return None
    return image_pipeline

GOOGLE_OAUTH_ENABLED = False

# Google OAuth blueprint (try to register, but don't block startup if credentials missing)
try:
    if os.getenv('GOOGLE_CLIENT_ID') and os.getenv('GOOGLE_CLIENT_SECRET'):
        google_bp = make_google_blueprint(
            client_id=os.getenv('GOOGLE_CLIENT_ID'),
            client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
            scope=["profile", "email"],
            redirect_url="/google_callback"
        )
        app.register_blueprint(google_bp, url_prefix="/login")
        GOOGLE_OAUTH_ENABLED = True
        print("✅ Google OAuth configured")
    else:
        print("⚠️  Google OAuth not configured (missing credentials)")
except Exception as e:
    print(f"⚠️  Google OAuth error (startup won't block): {e}")

# --- routes ---
@app.route("/health", methods=["GET", "HEAD"])
def health():
    """Health check endpoint for HF Spaces - MUST respond immediately"""
    try:
        return jsonify({"status": "ok", "service": "Nafti AI"}), 200
    except Exception as e:
        print(f"[ERROR] Health check failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 503

@app.route("/healthz", methods=["GET", "HEAD"])
def healthz():
    """Alternative health check endpoint"""
    return "OK", 200

print("[STARTUP] Health endpoints registered")
print("[STARTUP] ✅ Flask app ready for requests!")

# Request tracking to detect hangs
@app.before_request
def track_request():
    """Log all incoming requests for debugging"""
    request.start_time = time.time()
    # Only log non-health endpoints to avoid spam
    if request.path not in ['/health', '/healthz']:
        print(f"[REQUEST] -> {request.method} {request.path}")

@app.after_request
def track_response(response):
    """Log response times"""
    if hasattr(request, 'start_time'):
        duration = time.time() - request.start_time
        # Only log non-health endpoints
        if request.path not in ['/health', '/healthz'] and duration > 0.1:
            print(f"[REQUEST] <- {request.method} {request.path} {response.status_code} ({duration:.2f}s)")
    return response

@app.route("/")
def index():
    """Landing for visitors, chat app for logged users."""
    user = session.get('user')
    if not user:
        stats = compute_public_stats()
        return render_template('landing.html', stats=stats)
    sessions = []
    histories = ensure_user_sessions(user)
    sessions = histories.get(user, [])
    response = app.make_response(render_template('index.html', sessions=sessions))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/gallery')
def gallery_view():
    return redirect(url_for('index'))

@app.route('/auth/login')
def auth_login_view():
    google_login_url = url_for('google.login') if GOOGLE_OAUTH_ENABLED else None
    return render_template('auth_login.html', google_oauth_enabled=GOOGLE_OAUTH_ENABLED, google_login_url=google_login_url)

@app.route('/profile')
def profile_view():
    if not session.get('user'):
        return redirect(url_for('index'))
    user_stats = compute_user_stats(session.get('user'))
    return render_template('profile.html', user_stats=user_stats)

@app.route('/generate-image')
def generate_image():
    """Render the image generation page"""
    if IS_CLOUDFLARE_WORKER and not session.get('user'):
        return redirect(url_for('index'))
    return render_template('generate.html')

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

@app.route('/settings')
def settings_view():
    google_login_url = url_for('google.login') if GOOGLE_OAUTH_ENABLED else None
    return render_template('settings.html', google_oauth_enabled=GOOGLE_OAUTH_ENABLED, google_login_url=google_login_url)

@app.route('/sessions/clear-all', methods=['POST'])
def clear_all_sessions():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Login to use AI features"}), 401
    histories = load_history()
    histories[user] = []
    save_history(histories)
    return jsonify({"status": "cleared"})

@app.route('/history')
def history_view():
    user = session.get('user')
    histories = load_history()
    sessions = histories.get(user, []) if user else []
    return render_template('history.html', sessions=sessions)

@app.route('/session/new', methods=['POST'])
def new_session():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Login to use AI features"}), 401
    sess = create_session_for_user(user)
    return jsonify(sess)

@app.route('/session/clear', methods=['POST'])
def clear_session():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Login to use AI features"}), 401
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
        return jsonify({"error": "Login to use AI features"}), 401
    data = request.get_json() or {}
    sid = data.get('session_id')
    if not sid:
        return jsonify({"error": "Missing session_id"}), 400
    delete_session(user, sid)
    return jsonify({"status": "deleted"})

@app.route('/logout')
def logout():
    session.pop('user', None)
    next_param = request.args.get('next')
    if next_param == 'login':
        return redirect(url_for('index') + '?auth=login')
    elif next_param == 'signup':
        return redirect(url_for('index') + '?auth=signup')
    return redirect(url_for('index'))

@app.route('/google_callback')
def google_callback():
    try:
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
    except Exception as e:
        print(f"Google OAuth callback error: {e}")
        return redirect(url_for('index'))


@app.route("/service-worker.js")
def service_worker():
    """Le service worker doit être servi depuis la racine pour couvrir tout le site"""
    if IS_CLOUDFLARE_WORKER:
        return serve_asset("static/service-worker.js", content_type="application/javascript")
    return send_from_directory("static", "service-worker.js", mimetype="application/javascript")


@app.route("/api/chat", methods=["POST"])
def chat_public():
    """Public chat endpoint - creates anonymous user if needed"""
    if not GROQ_API_KEY:
        return jsonify({"error": "Clé API Groq manquante"}), 500

    # Create anonymous session if not logged in
    if 'user' not in session:
        session['user'] = f"anonymous_{uuid.uuid4().hex[:8]}"

    data = request.get_json()
    message = data.get("message", "").strip()
    
    if not message:
        return jsonify({"error": "Message requis"}), 400

    try:
        is_science_doc = is_scientific_document_question(message)
        is_exercise = is_exercise_or_homework_request(message)
        detected_lang, _lang_meta = resolve_response_language([{"role": "user", "content": message}], default_lang="fr")
        system_prompt = build_system_prompt(message, detected_lang)
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": message}
                ],
                "max_tokens": 2048,
                "temperature": 0.7
            },
            timeout=30
        )
        response.raise_for_status()
        result = response.json()
        
        ai_message = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if is_science_doc:
            ai_message = clean_latex_math_notation(ai_message)
        if is_science_doc and is_exercise:
            ai_message = format_exercise_answer(ai_message)
        ai_message, _output_lang_meta = enforce_response_language(ai_message, detected_lang)
        
        if not ai_message:
            return jsonify({"error": "Pas de réponse de l'IA"}), 500
        
        return jsonify({"response": ai_message, "user": session.get('user')})
    
    except requests.exceptions.Timeout:
        return jsonify({"error": "Timeout - API trop lent"}), 504
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"Erreur API: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"error": f"Erreur: {str(e)}"}), 500


@app.route('/api/synthesize', methods=['POST'])
def synthesize_voice():
    """Synthesize text to speech using gTTS"""
    if IS_CLOUDFLARE_WORKER:
        return jsonify({"error": "La synthèse serveur n'est pas disponible sur l'hébergement gratuit; utilisez la voix du navigateur."}), 501
    try:
        from gtts import gTTS
        
        data = request.get_json()
        text = data.get('text', '').strip()
        lang = data.get('lang', 'fr')
        
        if not text:
            return jsonify({"error": "Text required"}), 400
        
        if len(text) > 5000:
            return jsonify({"error": "Text too long (max 5000 chars)"}), 400
        
        tts = gTTS(text=text, lang=lang, slow=False)
        audio_buffer = BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        
        return send_file(
            audio_buffer,
            mimetype='audio/mpeg',
            as_attachment=False,
            download_name=None
        )
    except Exception as e:
        print(f"TTS Error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/ai", methods=["POST"])
def proxy_ai():
    """Proxy that relays requests to Groq API - stores conversation history per user"""
    if 'user' not in session:
        return jsonify({"error": "Login to use AI features"}), 401
    if not GROQ_API_KEY:
        return jsonify({"error": "Clé API Groq manquante. Vérifiez votre fichier .env"}), 500

    data = request.get_json()
    messages = data.get("messages", [])
    session_id = data.get("session_id")
    # Support multiple images (new) and single image (legacy)
    images_data = data.get("images")  # list of data:image/...;base64,...
    if not images_data:
        single = data.get("image")
        images_data = [single] if single else []
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
                title_text = m['content'] if isinstance(m['content'], str) else 'Image'
                sess['title'] = title_text[:80]
                break
    histories = load_history()
    histories[user] = histories.get(user, [])
    # update the specific session object in histories list
    for idx, s in enumerate(histories[user]):
        if s.get('id') == sess['id']:
            histories[user][idx] = sess
            break
    save_history(histories)

    # If images are provided, use the vision model and format the last user message
    use_model = GROQ_MODEL
    api_messages = list(messages)
    
    # Resolve language from user conversation context (robust for short/ambiguous messages)
    detected_lang, detected_lang_meta = resolve_response_language(api_messages, default_lang="fr")
    prompt_text = ""
    for msg in reversed(api_messages):
        if msg.get("role") == "user":
            prompt_text = _extract_user_text(msg.get("content"))
            break
    
    has_attachment = bool(images_data)
    document_mode = select_document_mode(prompt_text, images_data, detected_lang)
    system_content = build_system_prompt(
        prompt_text,
        detected_lang,
        document_mode=document_mode,
        has_attachment=has_attachment,
    )
    is_science_doc = document_mode == "scientific_document"
    is_academic_doc = document_mode == "academic_document"
    is_exercise = is_exercise_or_homework_request(prompt_text, has_attachment=has_attachment)
    
    # Remplacer ou ajouter le message système
    api_messages = [msg for msg in api_messages if msg.get('role') != 'system']
    api_messages.insert(0, {"role": "system", "content": system_content})
    
    if images_data:
        use_model = GROQ_VISION_MODEL
        # Find the last user message and convert to multimodal format
        for i in range(len(api_messages) - 1, -1, -1):
            if api_messages[i].get('role') == 'user':
                user_text = api_messages[i].get('content', 'Analyse cette image.')
                content_parts = [{"type": "text", "text": user_text}]
                for img in images_data:
                    content_parts.append({"type": "image_url", "image_url": {"url": img}})
                api_messages[i] = {
                    "role": "user",
                    "content": content_parts
                }
                break

    # Appel à l'API Groq (format OpenAI compatible)
    try:
        response = requests.post(
            GROQ_URL,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": use_model,
                "messages": api_messages,
                "temperature": 0.7,
                "max_tokens": 1500,
            },
            timeout=60,
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
            if is_science_doc:
                ai_content = clean_latex_math_notation(ai_content)
            if (is_science_doc or is_academic_doc) and is_exercise:
                ai_content = format_exercise_answer(ai_content)
            ai_content, output_lang_meta = enforce_response_language(ai_content, detected_lang)
            if is_science_doc or is_academic_doc:
                try:
                    ai_resp["choices"][0]["message"]["content"] = ai_content
                except Exception:
                    pass
            else:
                try:
                    ai_resp["choices"][0]["message"]["content"] = ai_content
                except Exception:
                    pass
            try:
                ai_resp.setdefault("language_debug", {})
                ai_resp["language_debug"] = {
                    "target": detected_lang,
                    "target_meta": detected_lang_meta,
                    "output_meta": output_lang_meta,
                }
            except Exception:
                pass
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


@app.route("/generate", methods=["POST"])
def generate_public():
    """Generate an image with Workers AI on Cloudflare or the local providers."""
    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Veuillez fournir une description pour l'image."}), 400

    if IS_CLOUDFLARE_WORKER:
        if 'user' not in session:
            return jsonify({"error": "Connectez-vous pour générer une image."}), 401
        if len(prompt) > 1800:
            return jsonify({"error": "La description est trop longue (1 800 caractères maximum)."}), 400
        try:
            image_base64 = generate_image_with_workers_ai(prompt)
            return jsonify({
                "image_base64": image_base64,
                "mime_type": "image/jpeg",
                "source": "cloudflare",
                "text": f"Image générée : {prompt}"
            })
        except Exception as image_error:
            print(f"Workers AI image generation failed: {type(image_error).__name__}")
            return jsonify({"error": "Le service de génération d’images est momentanément indisponible."}), 503

    # Try HF API first
    try:
        enhanced_prompt = f"{prompt}, masterpiece, professional, high quality"
        url = f"{HF_API_URL}/{HF_IMAGE_MODEL}"
        headers = {}
        if HF_API_KEY:
            headers["Authorization"] = f"Bearer {HF_API_KEY}"
        
        payload = {"inputs": enhanced_prompt}
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            if IS_CLOUDFLARE_WORKER and response.headers.get("content-type", "").startswith("image/"):
                return Response(response.content, content_type=response.headers["content-type"])
            image_base64 = base64.b64encode(response.content).decode()
            return jsonify({
                "image_base64": image_base64,
                "mime_type": "image/jpeg",
                "text": f"Image générée: {prompt}"
            })
    except Exception as api_error:
        print(f"HF API error: {api_error}")

    if IS_CLOUDFLARE_WORKER:
        return jsonify({"error": "Le fournisseur d'images est temporairement indisponible."}), 503

    # Fall back to local
    try:
        pipeline = get_image_pipeline()
        if pipeline is None:
            return jsonify({"error": "Modèle indisponible."}), 503

        enhanced_prompt = f"{prompt}, masterpiece, 8k, professional"
        negative_prompt = "low quality, blurry, distorted, artifacts"

        with torch.no_grad():
            image = pipeline(
                prompt=enhanced_prompt,
                negative_prompt=negative_prompt,
                height=768,
                width=768,
                num_inference_steps=20,
                guidance_scale=7.5
            ).images[0]

        buffered = BytesIO()
        image.save(buffered, format="PNG")
        image_base64 = base64.b64encode(buffered.getvalue()).decode()

        return jsonify({
            "image_base64": image_base64,
            "mime_type": "image/png",
            "text": f"Image générée: {prompt}"
        })

    except Exception as e:
        return jsonify({"error": f"Erreur: {str(e)}"}), 500


@app.route("/api/generate-image", methods=["POST"])
def api_generate_image():
    """Generate an image with Workers AI on Cloudflare, HF or local inference elsewhere."""
    if 'user' not in session:
        return jsonify({"error": "Login to use AI features"}), 401

    data = request.get_json(silent=True) or {}
    prompt = data.get("prompt", "").strip()
    if not prompt:
        return jsonify({"error": "Veuillez fournir une description pour l'image."}), 400

    if IS_CLOUDFLARE_WORKER:
        if len(prompt) > 1800:
            return jsonify({"error": "La description est trop longue (1 800 caractères maximum)."}), 400
        try:
            image_base64 = generate_image_with_workers_ai(prompt)
            return jsonify({
                "image_base64": image_base64,
                "mime_type": "image/jpeg",
                "source": "cloudflare",
                "text": f"Image générée : {prompt}"
            })
        except Exception as image_error:
            print(f"Workers AI image generation failed: {type(image_error).__name__}")
            return jsonify({"error": "Le service de génération d’images est momentanément indisponible."}), 503

    # Try HF API first (works on HF Spaces with internet)
    try:
        enhanced_prompt = f"{prompt}, masterpiece, professional, high quality, sharp focus"
        url = f"{HF_API_URL}/{HF_IMAGE_MODEL}"
        headers = {"Content-Type": "application/json"}
        if HF_API_KEY:
            headers["Authorization"] = f"Bearer {HF_API_KEY}"
        
        payload = {"inputs": enhanced_prompt}
        print(f"🌐 HF API: Requesting image generation...")
        response = requests.post(url, headers=headers, json=payload, timeout=60)
        
        if response.status_code == 200:
            print(f"✅ HF API: Image generated successfully")
            if IS_CLOUDFLARE_WORKER and response.headers.get("content-type", "").startswith("image/"):
                return Response(response.content, content_type=response.headers["content-type"])
            image_base64 = base64.b64encode(response.content).decode()
            return jsonify({
                "image_base64": image_base64,
                "mime_type": "image/jpeg",
                "text": f"Image générée: {prompt}"
            })
        elif response.status_code == 503:
            print(f"⏳ HF API: Model loading (503)")
            return jsonify({"error": "Modèle en cours de chargement... Réessayez dans 30 secondes"}), 503
        else:
            print(f"⚠️  HF API error: Status {response.status_code}")
            if response.status_code == 401:
                print(f"   → API key issue or model access denied")
            elif response.status_code >= 400 and response.status_code < 500:
                print(f"   → Client error: {response.text[:200]}")
            else:
                print(f"   → Server error: {response.text[:200]}")
    except requests.exceptions.Timeout:
        print(f"⏱️  HF API: Request timeout")
        return jsonify({"error": "Génération dépassée. Réessayez."}), 504
    except requests.exceptions.ConnectionError:
        print(f"🌐 HF API: No internet connection")
    except Exception as api_error:
        print(f"❌ HF API error: {type(api_error).__name__}: {api_error}")

    if IS_CLOUDFLARE_WORKER:
        return jsonify({"error": "Le fournisseur d'images est temporairement indisponible."}), 503

    # Fall back to local pipeline (for local development only)
    try:
        pipeline = get_image_pipeline()
        
        # If pipeline loaded successfully, use it
        if pipeline is not None:
            try:
                import torch
                print(f"🤖 Local: Using Stable Diffusion model")
                enhanced_prompt = f"{prompt}, masterpiece, professional, sharp focus"
                negative_prompt = "low quality, blurry, distorted, artifacts"

                with torch.no_grad():
                    image = pipeline(
                        prompt=enhanced_prompt,
                        negative_prompt=negative_prompt,
                        height=768,
                        width=768,
                        num_inference_steps=20,
                        guidance_scale=7.5
                    ).images[0]
                print(f"✅ Local: Image generated successfully")
            except ImportError:
                print(f"⚠️  Local: torch not installed, using placeholder")
                image = generate_placeholder_image(prompt, width=768, height=768)
        else:
            # Use placeholder generator if model unavailable (offline mode)
            print(f"📝 No model available: Using placeholder image generator")
            image = generate_placeholder_image(prompt, width=768, height=768)

        buffered = BytesIO()
        image.save(buffered, format="PNG")
        image_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        # Clean up memory after image generation
        del image, buffered
        gc.collect()

        return jsonify({
            "image_base64": image_base64,
            "mime_type": "image/png",
            "text": f"Image générée: {prompt}"
        })

    except RuntimeError as e:
        gc.collect()  # Clean up on error too
        if "out of memory" in str(e):
            return jsonify({"error": "Mémoire insuffisante."}), 507
        return jsonify({"error": f"Erreur: {str(e)}"}), 500
    except Exception as e:
        gc.collect()  # Clean up on error too
        print(f"❌ Fallback error: {type(e).__name__}: {e}")
        return jsonify({"error": f"Erreur: {str(e)}"}), 500


@app.route('/api/session/<session_id>')
def get_session_data(session_id):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Login to use AI features"}), 401
    sess = find_session(user, session_id)
    if not sess:
        return jsonify({"error": "Not found"}), 404
    return jsonify(sess)


def _plain_message_content(content):
    if isinstance(content, str):
        return re.sub(r"data:image/[^;]+;base64,[A-Za-z0-9+/=]+", "[Image jointe]", content)
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
                elif item.get("type") in {"image_url", "input_image"} or item.get("image_url"):
                    parts.append("[Image jointe]")
        return "\n".join(parts)
    return ""


def _normalize_session_messages(messages):
    if not isinstance(messages, list) or len(messages) > 2000:
        return None
    normalized = []
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            continue
        content = _plain_message_content(message.get("content", ""))
        total_chars += len(content)
        if total_chars > 200_000:
            return None
        normalized.append({"role": message["role"], "content": content})
    return normalized


@app.route('/api/save-session', methods=['POST'])
def save_session_route():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Connectez-vous pour enregistrer la conversation."}), 401
    data = request.get_json(silent=True) or {}
    messages = _normalize_session_messages(data.get("messages"))
    if messages is None:
        return jsonify({"error": "La conversation est invalide ou trop volumineuse."}), 413

    session_id = data.get("session_id")
    sess = find_session(user, session_id) if isinstance(session_id, str) and session_id else None
    if sess is None:
        sess = create_session_for_user(user)
    sess["messages"] = messages
    if not sess.get("title") or sess.get("title") == "Nouvelle conversation":
        for message in messages:
            if message["role"] == "user":
                sess["title"] = message["content"][:80] or "Nouvelle conversation"
                break
    histories = load_history()
    user_sessions = histories.setdefault(user, [])
    for index, stored in enumerate(user_sessions):
        if stored.get("id") == sess["id"]:
            user_sessions[index] = sess
            break
    else:
        user_sessions.append(sess)
    try:
        save_history(histories)
    except (RuntimeError, ValueError) as storage_error:
        return jsonify({"error": str(storage_error)}), 507
    return jsonify({"status": "saved", "session_id": sess["id"]})


@app.route('/api/export', methods=['POST'])
def export_conversation():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Connectez-vous pour exporter la conversation."}), 401
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    sess = find_session(user, session_id) if isinstance(session_id, str) else None
    if not sess:
        return jsonify({"error": "Conversation introuvable."}), 404
    export_format = str(data.get("format", "txt")).lower()
    if export_format == "markdown":
        export_format = "md"
    if export_format not in {"txt", "md", "json"}:
        return jsonify({"error": "Format accepté : txt, md ou json."}), 400

    messages = _normalize_session_messages(sess.get("messages", [])) or []
    title = str(sess.get("title") or "Conversation")[:160]
    if export_format == "json":
        body = json.dumps({"title": title, "messages": messages}, ensure_ascii=False, indent=2)
        content_type = "application/json; charset=utf-8"
    else:
        heading = f"# {title}\n\n" if export_format == "md" else f"{title}\n{'=' * min(len(title), 80)}\n\n"
        sections = []
        for message in messages:
            label = "Vous" if message["role"] == "user" else "Nafti AI"
            if export_format == "md":
                sections.append(f"**{label}**\n\n{message['content']}")
            else:
                sections.append(f"{label}\n{message['content']}")
        separator = "\n\n---\n\n" if export_format == "md" else "\n\n"
        body = heading + separator.join(sections)
        content_type = "text/markdown; charset=utf-8" if export_format == "md" else "text/plain; charset=utf-8"
    return Response(
        body,
        mimetype=content_type.split(";", 1)[0],
        headers={
            "Content-Disposition": f'attachment; filename="conversation.{export_format}"',
            "Cache-Control": "private, no-store",
        },
    )


@app.route('/api/share', methods=['POST'])
def create_conversation_share():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Connectez-vous pour partager la conversation."}), 401
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    sess = find_session(user, session_id) if isinstance(session_id, str) else None
    if not sess:
        return jsonify({"error": "Conversation introuvable."}), 404
    messages = _normalize_session_messages(sess.get("messages", []))
    if not messages:
        return jsonify({"error": "Cette conversation ne contient aucun message à partager."}), 400
    share_id = secrets.token_urlsafe(24)
    snapshot = {
        "title": str(sess.get("title") or "Conversation")[:160],
        "messages": messages,
        "created_at": datetime.now().isoformat(),
    }
    try:
        save_shared_conversation(share_id, snapshot)
    except (RuntimeError, ValueError) as storage_error:
        return jsonify({"error": str(storage_error)}), 507
    return jsonify({"share_url": url_for("shared_conversation", share_id=share_id)})


@app.route('/share/<share_id>')
def shared_conversation(share_id):
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,80}", share_id):
        return "Lien de partage invalide.", 404
    snapshot = load_shared_conversation(share_id)
    if not snapshot:
        return "Conversation introuvable.", 404
    response = app.make_response(render_template("shared_conversation.html", conversation=snapshot))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response

@app.route('/api/detect-language', methods=['POST'])
def detect_language_endpoint():
    """
    API Endpoint pour détection de langue INTELLIGENTE ensemble
    
    POST /api/detect-language
    Body: {"text": "votre texte ici"}
    
    Response: {
        "language": "fr",
        "confidence": 0.95,
        "method": "ensemble|unicode_script|langdetect|ngrams",
        "details": {...}
    }
    """
    try:
        data = request.get_json() or {}
        text = data.get('text', '')
        
        if not text or not text.strip():
            return jsonify({
                "language": "fr",
                "confidence": 0.5,
                "method": "fallback",
                "error": "Empty text"
            }), 400
        
        result = detect_language_ensemble(text)
        
        # Ajouter quelques infos supplémentaires
        result['text_length'] = len(text)
        result['word_count'] = len(text.split())
        
        return jsonify(result)
    
    except Exception as e:
        return jsonify({
            "error": str(e),
            "language": "fr",
            "confidence": 0.5,
            "method": "fallback"
        }), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("=" * 50)
    print("  🚀 Nafti AI - Serveur démarré (PWA activée)")
    print(f"  📍 https://0.0.0.0:{port} (SSL activé pour reconnaissance vocale)")
    print(f"  🔒 Certificat SSL: server.crt/server.key")
    print(f"  🤖 Modèle texte: {GROQ_MODEL}")
    print(f"  🖼️  Modèle vision: {GROQ_VISION_MODEL}")
    print(f"  🎨 Modèle image: {HF_IMAGE_MODEL} (Local SDXL)")
    print(f"  🔑 Clé Groq: {'✅ configurée' if GROQ_API_KEY else '❌ MANQUANTE'}")
    print(f"  💾 Utilisateurs: {USERS_FILE} (auto-créé)")
    print("=" * 50)

    # Configuration SSL pour la reconnaissance vocale
    ssl_context = None
    if os.path.exists('server.crt') and os.path.exists('server.key'):
        ssl_context = ('server.crt', 'server.key')
        print("  🔐 SSL activé - Reconnaissance vocale disponible")
        print(f"  ℹ️  Ouvrez https://localhost:{port} et acceptez le certificat si nécessaire")
    else:
        print("  ⚠️  SSL non configuré - Reconnaissance vocale limitée")

    app.run(host="0.0.0.0", port=port, debug=False, ssl_context=ssl_context)
