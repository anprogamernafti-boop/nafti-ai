# Déploiement Cloudflare Workers

La version Cloudflare sert l’interface comme fichiers statiques et exécute les routes dans un Worker JavaScript léger. D1 conserve les comptes, l’historique et les copies des conversations partagées. Les données de l’ancien Space Hugging Face ne sont pas importées automatiquement.

## Prérequis

- Node.js et Python 3 pour préparer les fichiers statiques.
- Wrangler 4 et un compte Cloudflare.
- Une clé Groq pour le chat et l’analyse d’images.
- Un client OAuth Web Google si la connexion Google est utilisée.

Connecte Wrangler une fois :

```powershell
npx wrangler@4 login
```

## Configuration d’un nouveau compte Cloudflare

Depuis la racine du dépôt, crée une base D1, copie son identifiant dans `cloudflare/wrangler.jsonc`, puis initialise son schéma :

```powershell
npx wrangler@4 d1 create nafti-ai-state
npx wrangler@4 d1 execute nafti-ai-state --remote --file cloudflare/schema.cloudflare.sql
```

Les fichiers statiques sont construits à partir des templates avec :

```powershell
python scripts/package_cloudflare_assets.py
```

Configure les secrets avec les invites sécurisées de Wrangler :

```powershell
npx wrangler@4 secret put SECRET_KEY --config cloudflare/wrangler.jsonc
npx wrangler@4 secret put GROQ_API_KEY --config cloudflare/wrangler.jsonc
npx wrangler@4 secret put GOOGLE_CLIENT_SECRET --config cloudflare/wrangler.jsonc
```

`SECRET_KEY` doit être aléatoire et propre à l’installation. Ne place aucune clé dans le dépôt, le fichier de configuration ou un message.

Dans Google Cloud, crée un client OAuth de type **Application Web** et configure l’URI de redirection exactement comme suit : `https://nafti-ai-edge.asser-dev.workers.dev/auth/google/callback`. Le flux utilise `openid email profile`, `state`, `nonce`, PKCE et la vérification locale de la signature RS256 du jeton d’identité. Déclare l’identifiant public `GOOGLE_CLIENT_ID` dans `cloudflare/wrangler.jsonc` (section `vars`), puis conserve uniquement le secret client avec la commande Wrangler ci-dessus. N’active pas de facturation pour l’authentification Google.

Le Worker sur `workers.dev` est le déploiement actif; le mode d’hébergement Cloudflare ne met aucune fonction en veille ni en mode test. Contrôle du 10 novembre 2026 dans Google Auth Platform : audience **Externe**, publication **En production**, portées demandées `openid email profile`. Les comptes Google peuvent utiliser la connexion; chat, analyse d’images, génération, historique et partage restent indépendants de Google OAuth. Le Centre de validation indique toutefois que la marque n’est pas visible sur l’écran de consentement. La connexion reste utilisable, mais l’écran Google n’affiche pas encore clairement le nom et le logo Nafti AI. Pour faire vérifier cette marque, Google exige une propriété DNS pour un domaine contrôlé; le compte Cloudflare ne contient actuellement aucun domaine personnalisé. Cette étape affecte la présentation de la marque, pas l’accès aux fonctions. L’inscription par e-mail et mot de passe reste aussi disponible.

## Déployer et vérifier

```powershell
python scripts/package_cloudflare_assets.py
npx wrangler@4 deploy --config cloudflare/wrangler.jsonc
npx wrangler@4 tail nafti-ai-edge --config cloudflare/wrangler.jsonc
```

Les variables `GROQ_MODEL`, `GROQ_VISION_MODEL` et `AI_IMAGE_MODEL` sont déclarées dans `wrangler.jsonc`. Les images utilisent le binding Workers AI : aucune clé Hugging Face n’est requise pour ce déploiement.

Le suivi des incidents Cloudflare Issues est activé via `observability.issues.enabled` dans `wrangler.jsonc`. Il regroupe les nouvelles exceptions et erreurs de production; il ne reconstitue pas les incidents antérieurs à son activation. Cette fonctionnalité est gratuite pendant sa bêta ouverte.

## Fonctions activées

- Comptes e-mail/mot de passe et sessions signées; les mots de passe sont vérifiés avec PBKDF2 et les anciens hash SHA-256 migrent à la prochaine connexion.
- Connexion Google avec validation de `state`, `nonce`, PKCE, audience, expiration et signature du jeton d’identité. Aucun accès Gmail, Drive ni autre API Google n’est demandé.
- Chat texte et analyse d’images par Groq.
- Génération d’images par Cloudflare Workers AI.
- Historique, profil d’activité, paramètres et galerie.
- Export TXT, Markdown et JSON, partage de conversations avec rendu HTML échappé.
- Dictée et lecture audio avec les APIs du navigateur. La synthèse audio serveur renvoie volontairement `501` afin que l’interface utilise la voix du navigateur.

La connexion Google utilise le client OAuth Web configuré ci-dessus. Les routes locales Flask avec gTTS/Diffusers ne sont pas exécutées sur ce Worker.

## Quotas et limites

Workers, D1, Workers AI et Groq ont chacun leurs quotas et règles. Les appels IA ne sont pas illimités; vérifie leurs tableaux de bord si l’application approche des limites. Les appels réussis ne garantissent pas un niveau de service permanent.

Les liens de partage rendent le contenu copié accessible à toute personne qui possède le lien. Les utilisateurs peuvent supprimer leurs conversations depuis l’historique.

## Déploiement vérifié

- Application : [nafti-ai-edge.asser-dev.workers.dev](https://nafti-ai-edge.asser-dev.workers.dev/)
- Worker : `nafti-ai-edge`
- Secrets configurés dans Cloudflare : `SECRET_KEY`, `GROQ_API_KEY`, `GOOGLE_CLIENT_SECRET`.
- L’identifiant public `GOOGLE_CLIENT_ID` et l’URI de rappel sont déclarés dans `cloudflare/wrangler.jsonc`.
- Dernière version déployée : `a0c3f45a-8e73-4374-8ddf-08d5ab80416b` (10 novembre 2026).

La connexion Google est configurée sur le client Web `Nafti AI`. L’origine autorisée est `https://nafti-ai-edge.asser-dev.workers.dev` et l’URI de rappel est `https://nafti-ai-edge.asser-dev.workers.dev/auth/google/callback`. Le secret reste stocké dans Cloudflare et n’est pas inclus dans le dépôt.

Les vérifications de production ont couvert les pages statiques, la création de compte, la connexion/déconnexion, les routes privées, les sessions, le chat Groq, la vision Groq, la génération Workers AI, les exports, l’historique, le partage protégé contre l’injection HTML et la détection de langue. Le compte temporaire et son partage ont été supprimés après les tests.

Le 10 novembre 2026, le déploiement OAuth a aussi été contrôlé : `/healthz`, la page de connexion, les pages légales, la redirection Google, les portées `openid email profile`, `state`, `nonce`, PKCE et les trois cookies sécurisés. Le propriétaire a ensuite confirmé avoir terminé la connexion Google dans son navigateur.

Après activation d’Issues, les métriques montraient 25 échecs d’exécution répartis sur quatre anciennes versions; la version alors active n’en avait aucun. Les détails historiques n’étaient pas disponibles. Le 10 novembre, le nouveau déploiement `a0c3f45a-8e73-4374-8ddf-08d5ab80416b` a renvoyé HTTP 200 sur `/healthz`, `/` et `/auth/login.html`; Issues affichait zéro incident actif au moment du contrôle.
