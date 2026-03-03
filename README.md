# Nafti AI Web App

This repository contains the Nafti AI progressive web application with an integrated
email/password and Google authentication layer. The core chat functionality is
unchanged, and authentication simply guards access to the interface.

## Features

- Chat interface powered by Groq API (`/api/ai` proxy)
- Multi-session support with per-user conversation storage
  - Start new sessions or clear current session from the header
  - View all sessions on a dedicated history page (with delete option)
- PWA support with service worker & install prompts
- Splash intro screen on each visit
- Theme toggle (light/dark)
- **Authentication**:
  - Email / password (stored in flat files)
  - Google OAuth2 (via `flask-dance`)

## Setup

1. **Create a Python virtual environment** (Windows):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r requirements.txt
   ```

2. **Configure environment** in `.env`:

   ```dotenv
   GROQ_API_KEY=your_groq_key_here
   GROQ_MODEL=llama-3.3-70b-versatile
   SECRET_KEY=some_random_secret
   GOOGLE_CLIENT_ID=your_google_client_id
   GOOGLE_CLIENT_SECRET=your_google_client_secret
   ```

   - Google credentials are obtained from the Google Cloud Console under OAuth2.
   - If you don't need Google login, you may leave those empty; email/password still works.

3. **Run the server**:
   ```powershell
   python server.py
   ```

- User & chat data files (`users.json`, `history.json`) will be created automatically on first run.

4. **Open** http://localhost:5000 in your browser. You will see the login/registration page.

## Notes

- Remember to keep `.env` out of version control (already listed in `.gitignore`).
- The original `index.html` at workspace root is no longer used; the template in
  `templates/index.html` is rendered by Flask.
- The `/api/ai` route still proxies to Groq exactly as before; no changes were made.

## Extending

If you later wish to add features (password reset, email verification, user profiles,
etc.) you can modify `server.py` and the global template accordingly. The authentication
mechanism is standard Flask-Login + SQLAlchemy and should be straightforward.

Enjoy building with Nafti AI! 🚀