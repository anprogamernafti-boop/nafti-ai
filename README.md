# Nafti AI

Nafti AI est une application web d’assistance par intelligence artificielle, construite avec Flask. Elle permet de discuter avec un assistant multilingue, de joindre des images pour les faire analyser, et de générer des images à partir d’une description.

## Fonctionnalités

- **Chat IA** : posez vos questions et poursuivez plusieurs conversations.
- **Analyse d’images et de documents** : joignez une image à un message pour demander à l’assistant de l’expliquer ou de l’analyser.
- **Génération d’images** : créez des images à partir de descriptions textuelles.
- **Reconnaissance vocale** : dictez un message dans les langues proposées par l’interface.
- **Lecture audio des réponses** : écoutez les réponses de l’assistant.
- **Comptes et historique** : inscription, connexion, sessions de discussion et historique.
- **Interface adaptable** : thème clair ou sombre et mise en page adaptée aux petits écrans.

Les fonctions d’IA s’appuient sur des services configurés côté serveur. Leur disponibilité dépend des clés API et des modèles configurés.

## Démarrage rapide

### Prérequis

- Python 3.10 ou plus récent, en 64 bits.
- Une clé API Groq pour activer le chat et l’analyse d’images.
- Facultatif : une clé Hugging Face pour la génération d’images via l’API.

### Installation

```bash
git clone https://github.com/anprogamernafti-boop/nafti-ai.git
cd nafti-ai
python -m venv .venv
```

Activez l’environnement virtuel :

```bash
# Windows PowerShell
.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

Installez les dépendances :

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Créez un fichier `.env` à la racine du projet :

```dotenv
GROQ_API_KEY=votre_cle_groq
# Facultatif : clé pour l’API Hugging Face de génération d’images
HF_API_KEY=votre_cle_hugging_face
# Facultatif : remplacez les modèles par défaut
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_VISION_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
HF_IMAGE_MODEL=stabilityai/stable-diffusion-2-1
# En production, définissez une longue valeur aléatoire
SECRET_KEY=remplacez-moi-par-une-cle-aleatoire
```

Ne publiez jamais vos clés API ni votre fichier `.env`. Celui-ci est ignoré par Git.

Lancez l’application :

```bash
python server.py
```

Ouvrez [http://localhost:5000](http://localhost:5000).

## Utiliser l’application

1. Créez un compte ou connectez-vous.
2. Démarrez une conversation et envoyez votre question.
3. Pour faire analyser une image, joignez-la à votre message.
4. Utilisez la commande de génération d’image pour décrire l’image à créer.

L’authentification Google est facultative et nécessite `GOOGLE_CLIENT_ID` et `GOOGLE_CLIENT_SECRET`, ainsi que la configuration OAuth correspondante dans Google Cloud.

## Technologies

- Python et Flask pour le serveur web et les routes API.
- Groq pour les réponses conversationnelles et la vision, lorsque `GROQ_API_KEY` est configurée.
- Hugging Face Inference API ou un pipeline Diffusers local pour la génération d’images.
- gTTS pour la synthèse vocale.
- HTML, CSS et JavaScript pour l’interface.

## Déploiement

Le dépôt source est public sur [GitHub](https://github.com/anprogamernafti-boop/nafti-ai). Pour publier une instance web accessible au public, déployez l’application Flask sur un hébergeur compatible Python et configurez les variables d’environnement de la même manière que dans `.env`. Configurez également les URI de redirection OAuth si la connexion Google est activée.

Le dépôt inclut un [Dockerfile](Dockerfile) et des notes de déploiement pour Render dans [RENDER_DEPLOYMENT.md](RENDER_DEPLOYMENT.md). La génération locale d’images peut nécessiter plusieurs gigaoctets de mémoire et un GPU pour de bonnes performances.

## Configuration

| Variable | Requise | Rôle |
| --- | --- | --- |
| `GROQ_API_KEY` | Pour le chat et la vision | Clé API Groq |
| `HF_API_KEY` | Non | Authentification facultative à Hugging Face Inference API |
| `GROQ_MODEL` | Non | Modèle de chat (valeur par défaut définie par le serveur) |
| `GROQ_VISION_MODEL` | Non | Modèle de vision (valeur par défaut définie par le serveur) |
| `HF_IMAGE_MODEL` | Non | Modèle Hugging Face utilisé pour les images |
| `SECRET_KEY` | À définir en production | Clé de signature des sessions Flask |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Non | Connexion OAuth Google |

## Licence

Aucune licence de dépôt n’est actuellement indiquée. Les modèles et services externes utilisés par l’application restent soumis à leurs propres conditions et licences.
