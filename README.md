# Nafti AI

### Un assistant IA multimodal pour discuter, analyser des images et créer des visuels

<p align="center">
  <img src="static/icons/icon-512.png" alt="Icône de Nafti AI" width="150">
</p>

<p align="center">
  <a href="https://assernafti-nafti-ai.hf.space/">Ouvrir Nafti AI</a> ·
  <a href="https://github.com/anprogamernafti-boop/nafti-ai">Code source</a>
</p>

Nafti AI est une application web Flask qui réunit conversation avec un assistant, analyse d’images, génération d’images et fonctions vocales dans une interface adaptée aux écrans mobiles.

## Fonctionnalités

- Conversations avec l’assistant et historique.
- Analyse d’images envoyées dans la conversation.
- Génération d’images à partir d’une description.
- Dictée par la reconnaissance vocale du navigateur et lecture audio des réponses.
- Comptes utilisateur, paramètres et interface claire ou sombre.

La disponibilité des fonctions IA dépend des clés API, des modèles et des services configurés.

## Technologies

| Domaine | Technologies |
| --- | --- |
| Serveur et API | Python, Flask, Flask-CORS |
| Modèles conversationnels et vision | Groq API |
| Génération d’images | Hugging Face Inference API ou pipeline Diffusers local |
| Voix | gTTS et Web Speech API du navigateur |
| Interface | HTML, CSS, JavaScript et templates Jinja |
| Déploiement | Hébergeur Python ; notes Render dans le dépôt |

Les comptes et l’historique sont conservés dans les fichiers JSON locaux users.json et history.json, ignorés par Git. Pour un déploiement public, il faut définir une stratégie de stockage et de protection adaptée aux données utilisateur.

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

GROQ_API_KEY active les fonctions de chat et d’analyse d’image. HF_API_KEY sert à l’API de génération d’images ; le serveur peut aussi utiliser un pipeline Diffusers local. Ne publie jamais le fichier .env ni les clés API.

Démarre le serveur :

    python server.py

Ouvre [http://localhost:5000](http://localhost:5000).

## Déploiement

Le dépôt contient des notes de déploiement pour Render dans [RENDER_DEPLOYMENT.md](RENDER_DEPLOYMENT.md). Configure les variables d’environnement dans l’hébergeur et une valeur SECRET_KEY propre à la production. L’authentification Google est facultative et requiert une configuration OAuth correspondante.

La génération d’images locale avec Diffusers peut demander plusieurs gigaoctets de mémoire et bénéficie d’un GPU. La génération via API dépend des quotas et conditions du fournisseur.

## À propos

Projet personnel d’Asser Nafti autour des assistants IA multimodaux, des API et de l’intégration de fonctions de vision et de génération.

