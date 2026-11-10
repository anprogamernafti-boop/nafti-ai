# Nafti AI

### Un assistant IA multimodal pour discuter, analyser des images et créer des visuels

<p align="center">
  <img src="static/icons/icon-512.png" alt="Icône de Nafti AI" width="150">
</p>

<p align="center">
  <a href="https://nafti-ai-edge.asser-dev.workers.dev/">Ouvrir Nafti AI · Cloudflare</a> ·
  <a href="https://assernafti-nafti-ai.hf.space/">Space Hugging Face · secours</a> ·
  <a href="https://github.com/anprogamernafti-boop/nafti-ai">Code source</a>
</p>

Nafti AI est une application web Flask qui réunit conversation avec un assistant, analyse d’images, génération d’images et fonctions vocales dans une interface adaptée aux écrans mobiles.

## Fonctionnalités

- Conversations avec l’assistant et historique.
- Analyse d’images envoyées dans la conversation.
- Génération d’images à partir d’une description.
- Dictée et lecture audio via les fonctions vocales du navigateur.
- Comptes utilisateur, historique, profil d’activité, galerie et paramètres.
- Export des conversations en TXT, Markdown ou JSON, et partage par lien.

La disponibilité des fonctions IA dépend des clés API, des modèles et des services configurés.

## Technologies

| Domaine | Technologies |
| --- | --- |
| Serveur et API | Python, Flask, Flask-CORS |
| Modèles conversationnels et vision | Groq API |
| Génération d’images | Cloudflare Workers AI sur le Worker, Hugging Face Inference API ou Diffusers en local |
| Voix | Web Speech API du navigateur sur Cloudflare; gTTS en local |
| Interface | HTML, CSS, JavaScript et templates Jinja |
| Déploiement | Cloudflare Workers/D1, Hugging Face Spaces de secours, notes Render |

En développement local, les comptes et l’historique utilisent users.json et history.json, ignorés par Git. Le portage Cloudflare stocke ces données dans D1. Il ne transfère pas automatiquement les données de l’ancien Space.

## Démarrage local

Prérequis : Python 3.10 ou plus récent.

    git clone https://github.com/anprogamernafti-boop/nafti-ai.git
    cd nafti-ai
    python -m venv .venv

Active l’environnement virtuel :

    # Windows PowerShell
    .venv\Scripts\Activate.ps1

    # macOS ou Linux
    source .venv/bin/activate

Installe les dépendances :

    python -m pip install --upgrade pip
    pip install -r requirements.txt

Crée un fichier .env à la racine du dépôt :

    GROQ_API_KEY=ta_cle_groq
    HF_API_KEY=ta_cle_hugging_face
    SECRET_KEY=une_longue_cle_aleatoire
    # Facultatif, pour activer la connexion Google :
    GOOGLE_CLIENT_ID=
    GOOGLE_CLIENT_SECRET=

GROQ_API_KEY active les fonctions de chat et d’analyse d’image. HF_API_KEY sert à la génération d’images dans les déploiements locaux ou Hugging Face; Cloudflare Workers utilise son binding Workers AI pour les images. Ne publie jamais le fichier .env ni les clés API.

Démarre le serveur :

    python server.py

Ouvre [http://localhost:5000](http://localhost:5000).

## Déploiement

Nafti AI est déployé sur [Cloudflare Workers](https://nafti-ai-edge.asser-dev.workers.dev/) avec D1. Le Space Hugging Face reste disponible comme solution de secours. Le portage ne copie pas automatiquement les comptes ni l’historique de l’ancien Space. Le déploiement Cloudflare utilise un Worker JavaScript léger pour rester compatible avec les limites du runtime gratuit. Pour les détails de déploiement et les fonctions vérifiées, consulte [CLOUDFLARE_DEPLOYMENT.md](CLOUDFLARE_DEPLOYMENT.md).

Le Worker Cloudflare est le déploiement actif de l’application : le changement d’hébergeur ne désactive pas ses fonctions. Le client Google est configuré en audience externe et en production; il ne demande que l’identité, l’adresse e-mail et le profil (`openid email profile`). Les comptes Google peuvent donc se connecter. Google n’affiche actuellement pas encore la marque Nafti AI sur son écran de consentement; cela ne bloque pas l’accès. Pour afficher une marque vérifiée, Google demande la preuve DNS d’un domaine contrôlé. Voir le [comportement des états OAuth](https://developers.google.com/identity/protocols/oauth2/production-readiness/overview) et les [règles de vérification de domaine](https://support.google.com/cloud/answer/13804266?hl=fr).

Le dépôt contient aussi des notes de déploiement pour Render dans [RENDER_DEPLOYMENT.md](RENDER_DEPLOYMENT.md). L’authentification Google est facultative et requiert une configuration OAuth correspondante. Les exports et liens de partage sont enregistrés avec l’historique; un lien de partage rend la copie de la conversation accessible à toute personne qui le possède.

La génération d’images locale avec Diffusers peut demander plusieurs gigaoctets de mémoire et bénéficie d’un GPU. La génération via API dépend des quotas et conditions du fournisseur.

## À propos

Projet personnel d’Asser Nafti autour des assistants IA multimodaux, des API et de l’intégration de fonctions de vision et de génération.
