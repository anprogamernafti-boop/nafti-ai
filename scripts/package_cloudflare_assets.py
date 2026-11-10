from pathlib import Path
import shutil
import re


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "cloudflare" / "public"

if OUTPUT.exists():
    shutil.rmtree(OUTPUT)
OUTPUT.mkdir(parents=True, exist_ok=True)
shutil.copytree(ROOT / "static", OUTPUT / "static", dirs_exist_ok=True)
shutil.copy2(ROOT / "static" / "service-worker.js", OUTPUT / "service-worker.js")

def write_text(relative_path, content):
    destination = OUTPUT / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content, encoding="utf-8")


# A static landing page keeps public navigation off the per-request CPU budget.
landing = (ROOT / "templates" / "landing.html").read_text(encoding="utf-8")
stats_start = landing.find('<div class="stats">')
stats_end = landing.find("</small>", stats_start)
if stats_start >= 0 and stats_end >= 0:
    landing = landing[:stats_start] + landing[stats_end + len("</small>"):]
landing = landing.replace('href="/auth/login"', 'href="/auth/login.html"')
landing = landing.replace("Tout est gratuit", "Accès selon les quotas gratuits")
landing = landing.replace("Accès complet, sans abonnement.", "Découvrez Nafti AI")
landing = landing.replace("Profitez de l’interface premium, des presets, du profil et des outils de personnalisation sans payer. Nous voulons d’abord offrir une expérience excellente à tous.", "Le chat et les outils créatifs utilisent des fournisseurs d’IA avec leurs propres quotas et conditions.")
landing = landing.replace("Ces chiffres sont-ils réels ?", "Les outils IA sont-ils illimités ?")
landing = landing.replace("Oui. Les KPI affichés sont calculés depuis les données locales utilisateurs/sessions/messages.", "Non. Les quotas dépendent des fournisseurs et du forfait de votre compte.")
write_text("index.html", landing)

write_text("privacy.html", (ROOT / "templates" / "privacy.html").read_text(encoding="utf-8"))
write_text("terms.html", (ROOT / "templates" / "terms.html").read_text(encoding="utf-8"))

# Static auth shell; form submissions are handled by the lightweight Worker.
auth = (ROOT / "templates" / "auth_login.html").read_text(encoding="utf-8")
auth = re.sub(r"\{% if google_oauth_enabled and google_login_url %\}.*?\{% endif %\}", '<a class="google" href="/auth/google"><i class="fa-brands fa-google"></i> Continuer avec Google</a>', auth, flags=re.S)
auth = auth.replace('href="/"', 'href="/"')
auth = auth.replace('<div class="sub">Connectez-vous pour continuer</div>', '<div class="sub">Connectez-vous ou créez un compte pour continuer</div><div id="authError" role="status" style="color:#a12626;margin:8px 0"></div>')
auth = auth.replace("</body>", "<script>const p=new URLSearchParams(location.search);const e=p.get('error');const errors={exists:'Cette adresse e-mail possède déjà un compte.',google_unconfigured:'La connexion Google n’est pas encore configurée.',google_state:'La vérification de sécurité a échoué. Réessayez.',google_cancelled:'Connexion Google annulée.',google_conflict:'Cette adresse Google est déjà associée à un autre compte.',google_failed:'La connexion Google a échoué. Réessayez.'};if(e)document.getElementById('authError').textContent=errors[e]||'Vérifiez votre e-mail et votre mot de passe (8 caractères minimum à l’inscription).';</script></body>")
write_text("auth/login.html", auth)

# The existing chat UI is served as a static asset; its API remains authenticated.
chat = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
intro_auth = re.compile(r"\{% if not session\.get\('user'\) %\}\s*<div class=\"intro-auth-row\">.*?</div>\s*\{% endif %\}", re.S)
chat = intro_auth.sub("", chat, count=1)
chat = chat.replace("{% if session.get('user') %}Acc&eacute;der &agrave; Nafti AI{% else %}Commencer{% endif %}", "Acc&eacute;der &agrave; Nafti AI")
auth_start = chat.find("{% if not session.get('user') %}\n<!-- Auth Page -->")
auth_end = chat.find("<!-- Header -->", auth_start)
if auth_start >= 0 and auth_end >= 0:
    chat = chat[:auth_start] + chat[auth_end:]
chat = chat.replace("'/?session='", "'/app/?session='")
chat = chat.replace("window.location.href = '/'", "window.location.href = '/app/'")
chat = chat.replace("window.history.replaceState(null, '', '/')", "window.history.replaceState(null, '', '/app/')")
chat = chat.replace("</body>", "<script>fetch('/api/me',{credentials:'same-origin'}).then(r=>{if(!r.ok)location.replace('/auth/login.html')}).catch(()=>location.replace('/auth/login.html'));</script></body>")
if "{%" in chat or "{{" in chat:
    raise RuntimeError("Des balises Jinja non résolues restent dans l’application statique.")
write_text("app/index.html", chat)

# Generate image UI is static too; it posts to the Workers AI route.
generate = (ROOT / "templates" / "generate.html").read_text(encoding="utf-8").replace('href="/"', 'href="/app/"')
write_text("app/generate.html", generate)

# History view is populated with safe DOM text from the authenticated API.
history = (ROOT / "templates" / "history.html").read_text(encoding="utf-8")
start = history.find("{% if sessions %}")
end = history.find("{% endif %}", start)
if start < 0 or end < 0:
    raise RuntimeError("Le bloc d’historique Jinja n’a pas été trouvé.")
history = history[:start] + '<div id="sessionList" class="session-list"></div><div id="emptyState" class="empty-state" hidden><p>Aucune conversation enregistrée.</p></div>' + history[end + len("{% endif %}"):]
history = history.replace('href="/"', 'href="/app/"')
history = history.replace("</body>", """<script>
fetch('/api/history').then(async r=>{if(!r.ok)throw new Error('Connexion requise');return r.json()}).then(({sessions})=>{const list=document.getElementById('sessionList');if(!sessions.length){document.getElementById('emptyState').hidden=false;return;}for(const item of sessions.slice().reverse()){const card=document.createElement('div');card.className='session-card';card.dataset.id=item.id;const info=document.createElement('div');info.className='session-info';info.onclick=()=>location.href='/app/?session='+encodeURIComponent(item.id);const title=document.createElement('div');title.className='session-title';title.textContent=item.title||'Nouvelle conversation';const meta=document.createElement('div');meta.className='session-meta';meta.textContent=(item.created_at||'').slice(0,10)+' · '+(item.messages||[]).length+' message(s)';info.append(title,meta);const del=document.createElement('button');del.className='session-delete';del.textContent='Supprimer';del.onclick=()=>deleteSession(item.id);card.append(info,del);list.append(card);}}).catch(()=>location.replace('/auth/login.html'));
</script></body>""")
if "{%" in history or "{{" in history:
    raise RuntimeError("Des balises Jinja non résolues restent dans l’historique statique.")
write_text("app/history.html", history)

# Settings preserve local preferences while sourcing the account from the Worker.
settings = (ROOT / "templates" / "settings.html").read_text(encoding="utf-8")
settings = re.sub(r"\{% if google_oauth_enabled and google_login_url %\}.*?\{% endif %\}", '<a class="google" href="/auth/google"><i class="fa-brands fa-google"></i> Se connecter avec Google</a>', settings, flags=re.S)
settings = settings.replace("{{ session.get('user','Non connecté') }}", '<span id="accountEmail">Chargement…</span>')
settings = settings.replace('href="/"', 'href="/app/"')
settings = settings.replace("</body>", "<script>fetch('/api/me').then(r=>{if(!r.ok)throw 0;return r.json()}).then(d=>document.getElementById('accountEmail').textContent=d.email).catch(()=>location.replace('/auth/login.html'));</script></body>")
if "{%" in settings or "{{" in settings:
    raise RuntimeError("Des balises Jinja non résolues restent dans les paramètres statiques.")
write_text("app/settings.html", settings)

# Profile stats are derived from the authenticated D1 history API.
profile = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#f5f7fb"><title>Profil · Nafti AI</title><style>
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#171b24;background:#f5f7fb}*{box-sizing:border-box}body{margin:0}.wrap{max-width:980px;margin:auto;padding:24px 16px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px}.links{display:flex;gap:8px;flex-wrap:wrap}.btn{padding:10px 12px;border:1px solid #e3e7ee;border-radius:10px;background:#fff;text-decoration:none;color:inherit;font-weight:650}.hero,.card{background:#fff;border:1px solid #e3e7ee;border-radius:16px;padding:16px}.hero{display:flex;align-items:center;gap:14px}.avatar{width:60px;height:60px;border-radius:50%;display:grid;place-items:center;color:#fff;font-size:25px;font-weight:800;background:linear-gradient(135deg,#0a8f94,#c79b3b)}.muted{color:#687389}.kpi{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:12px}.kpi b{display:block;font-size:28px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.bar{height:9px;background:#e9eef6;border-radius:99px;overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:linear-gradient(90deg,#0a8f94,#55d7dc)}.badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}.badge{background:#fff;border:1px solid #e3e7ee;padding:8px 10px;border-radius:99px}@media(max-width:640px){.kpi{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}}
</style></head><body><main class="wrap"><header class="top"><h1>Profil</h1><nav class="links"><a class="btn" href="/app/">Accueil</a><a class="btn" href="/app/settings.html">Paramètres</a><a class="btn" href="/app/history.html">Historique</a></nav></header><section class="hero"><div class="avatar" id="avatar">N</div><div><h2 id="email">Chargement…</h2><p class="muted">Statistiques réelles de votre activité</p></div></section><section class="kpi"><article class="card"><b id="sessions">—</b><span>Sessions</span></article><article class="card"><b id="messages">—</b><span>Messages</span></article><article class="card"><b id="userMessages">—</b><span>Messages envoyés</span></article><article class="card"><b id="assistantMessages">—</b><span>Réponses IA</span></article></section><section class="grid"><article class="card"><h3>Répartition conversation</h3><p class="muted" id="ratio">Chargement…</p><div class="bar"><i id="ratioBar" style="width:0"></i></div></article><article class="card"><h3>Progression</h3><p class="muted" id="progressText">Chargement…</p><div class="bar"><i id="progressBar" style="width:0"></i></div></article></section><div class="badges" id="badges"></div></main><script>
Promise.all([fetch('/api/me'),fetch('/api/history')]).then(async([me,hist])=>{if(!me.ok||!hist.ok)throw new Error('Connexion requise');return [await me.json(),await hist.json()]}).then(([me,data])=>{const sessions=Array.isArray(data.sessions)?data.sessions:[],messages=sessions.flatMap(s=>Array.isArray(s.messages)?s.messages:[]),users=messages.filter(m=>m.role==='user').length,assistants=messages.filter(m=>m.role==='assistant').length,total=messages.length,pct=total?Math.round(users/total*100):0,progress=Math.min(100,total*5);document.getElementById('email').textContent=me.email;document.getElementById('avatar').textContent=(me.email||'N').slice(0,1).toUpperCase();document.getElementById('sessions').textContent=sessions.length;document.getElementById('messages').textContent=total;document.getElementById('userMessages').textContent=users;document.getElementById('assistantMessages').textContent=assistants;document.getElementById('ratio').textContent='Vous '+pct+'% · Nafti AI '+(100-pct)+'%';document.getElementById('ratioBar').style.width=pct+'%';document.getElementById('progressText').textContent=progress+'% vers le niveau avancé';document.getElementById('progressBar').style.width=progress+'%';const badges=[];if(total>=10)badges.push('10+ messages');if(sessions.length>=5)badges.push('5+ sessions');if(assistants>=10)badges.push('Explorateur IA');if(!badges.length)badges.push('Nouveau membre');for(const value of badges){const badge=document.createElement('span');badge.className='badge';badge.textContent=value;document.getElementById('badges').append(badge)}}).catch(()=>location.replace('/auth/login.html'));
</script></body></html>"""
write_text("app/profile.html", profile)

# The gallery is a standalone static page; its likes remain in local storage.
gallery = (ROOT / "templates" / "gallery.html").read_text(encoding="utf-8")
gallery = gallery.replace('href="/"', 'href="/app/"').replace('href="/settings"', 'href="/app/settings.html"')
if "{%" in gallery or "{{" in gallery:
    raise RuntimeError("Des balises Jinja non résolues restent dans la galerie statique.")
write_text("app/gallery.html", gallery)

print(f"Assets Cloudflare préparés dans {OUTPUT}")
print("Application prête : Worker JavaScript + assets HTML statiques")
