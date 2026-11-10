const textEncoder = new TextEncoder();
const textDecoder = new TextDecoder();
const SESSION_COOKIE = "nafti_session";
const SESSION_TTL = 60 * 60 * 24 * 7;
const MAX_STATE_BYTES = 1_800_000;
const GOOGLE_REDIRECT_PATH = "/auth/google/callback";
const GOOGLE_COOKIE_TTL = 600;
let googleJwksCache = null;
let googleJwksFetchedAt = 0;

function json(data, status = 200, headers = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers },
  });
}

function redirect(location, status = 303, headers = {}) {
  return new Response(null, { status, headers: { location, ...headers } });
}

function redirectWithCookies(location, cookies, status = 302) {
  const headers = new Headers({ location, "cache-control": "no-store" });
  for (const cookie of cookies) headers.append("set-cookie", cookie);
  return new Response(null, { status, headers });
}

function constantTimeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) return false;
  let mismatch = 0;
  for (let i = 0; i < left.length; i++) mismatch |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return mismatch === 0;
}

function requestCookie(request, name) {
  return (request.headers.get("cookie") || "").split(";").map((part) => part.trim())
    .find((part) => part.startsWith(`${name}=`))?.slice(name.length + 1) || "";
}

function googleCookie(name, value, maxAge = GOOGLE_COOKIE_TTL) {
  return `${name}=${value}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}

function clearGoogleCookies() {
  return ["nafti_google_state", "nafti_google_nonce", "nafti_google_verifier"]
    .map((name) => googleCookie(name, "", 0));
}

function googleRedirectUri(request, env) {
  return env.GOOGLE_REDIRECT_URI || `${new URL(request.url).origin}${GOOGLE_REDIRECT_PATH}`;
}

async function sha256Base64url(value) {
  return base64url(new Uint8Array(await crypto.subtle.digest("SHA-256", textEncoder.encode(value))));
}

async function googleStart(request, env) {
  if (!env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) {
    return redirect("/auth/login.html?error=google_unconfigured", 303);
  }
  const state = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const nonce = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = await sha256Base64url(verifier);
  const authorization = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authorization.search = new URLSearchParams({
    client_id: env.GOOGLE_CLIENT_ID,
    redirect_uri: googleRedirectUri(request, env),
    response_type: "code",
    scope: "openid email profile",
    state,
    nonce,
    code_challenge: challenge,
    code_challenge_method: "S256",
    prompt: "select_account",
  }).toString();
  return redirectWithCookies(authorization.toString(), [
    googleCookie("nafti_google_state", state),
    googleCookie("nafti_google_nonce", nonce),
    googleCookie("nafti_google_verifier", verifier),
  ]);
}

async function googleSigningKeys() {
  if (googleJwksCache && Date.now() - googleJwksFetchedAt < 60 * 60 * 1000) return googleJwksCache;
  const response = await fetch("https://www.googleapis.com/oauth2/v3/certs", { signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error("Google signing keys unavailable");
  const data = await response.json();
  if (!Array.isArray(data.keys) || !data.keys.length) throw new Error("Google signing keys invalid");
  googleJwksCache = data.keys;
  googleJwksFetchedAt = Date.now();
  return googleJwksCache;
}

async function verifyGoogleIdToken(idToken, clientId, expectedNonce) {
  if (typeof idToken !== "string" || idToken.length > 12_000) throw new Error("Google identity token missing");
  const parts = idToken.split(".");
  if (parts.length !== 3) throw new Error("Google identity token invalid");
  const header = JSON.parse(textDecoder.decode(fromBase64url(parts[0])));
  const claims = JSON.parse(textDecoder.decode(fromBase64url(parts[1])));
  if (header.alg !== "RS256" || typeof header.kid !== "string") throw new Error("Google token algorithm invalid");
  let keys = await googleSigningKeys();
  let jwk = keys.find((key) => key.kid === header.kid && key.kty === "RSA");
  if (!jwk) {
    googleJwksFetchedAt = 0;
    keys = await googleSigningKeys();
    jwk = keys.find((key) => key.kid === header.kid && key.kty === "RSA");
  }
  if (!jwk) throw new Error("Google signing key not found");
  const publicKey = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
  const validSignature = await crypto.subtle.verify(
    "RSASSA-PKCS1-v1_5", publicKey, fromBase64url(parts[2]), textEncoder.encode(`${parts[0]}.${parts[1]}`),
  );
  if (!validSignature) throw new Error("Google token signature invalid");
  const now = Math.floor(Date.now() / 1000);
  const audienceValid = claims.aud === clientId || (Array.isArray(claims.aud) && claims.aud.length === 1 && claims.aud[0] === clientId);
  if (!audienceValid || (claims.azp && claims.azp !== clientId)) throw new Error("Google token audience invalid");
  if (!['accounts.google.com', 'https://accounts.google.com'].includes(claims.iss)) throw new Error("Google token issuer invalid");
  if (!Number.isFinite(claims.exp) || claims.exp <= now || !Number.isFinite(claims.iat) || claims.iat > now + 60) throw new Error("Google token expired or invalid");
  if (!constantTimeEqual(String(claims.nonce || ""), expectedNonce)) throw new Error("Google token nonce invalid");
  if (!(claims.email_verified === true || claims.email_verified === "true") || typeof claims.email !== "string" || typeof claims.sub !== "string") {
    throw new Error("Google email is not verified");
  }
  const email = claims.email.trim().toLowerCase();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || claims.sub.length > 255) throw new Error("Google profile invalid");
  return { email, subject: claims.sub };
}

async function googleCallback(request, env) {
  const url = new URL(request.url);
  const state = url.searchParams.get("state") || "";
  const expectedState = requestCookie(request, "nafti_google_state");
  const nonce = requestCookie(request, "nafti_google_nonce");
  const verifier = requestCookie(request, "nafti_google_verifier");
  const cleared = clearGoogleCookies();
  if (!constantTimeEqual(state, expectedState) || !nonce || !verifier) {
    return redirectWithCookies("/auth/login.html?error=google_state", cleared, 303);
  }
  if (url.searchParams.has("error")) return redirectWithCookies("/auth/login.html?error=google_cancelled", cleared, 303);
  const code = url.searchParams.get("code") || "";
  if (!code || code.length > 4096 || !env.GOOGLE_CLIENT_ID || !env.GOOGLE_CLIENT_SECRET) {
    return redirectWithCookies("/auth/login.html?error=google_failed", cleared, 303);
  }
  try {
    const tokenResponse = await fetch("https://oauth2.googleapis.com/token", {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({
        code,
        client_id: env.GOOGLE_CLIENT_ID,
        client_secret: env.GOOGLE_CLIENT_SECRET,
        redirect_uri: googleRedirectUri(request, env),
        grant_type: "authorization_code",
        code_verifier: verifier,
      }),
      signal: AbortSignal.timeout(15_000),
    });
    const tokenData = await tokenResponse.json().catch(() => ({}));
    if (!tokenResponse.ok || !tokenData.id_token) return redirectWithCookies("/auth/login.html?error=google_failed", cleared, 303);
    const identity = await verifyGoogleIdToken(tokenData.id_token, env.GOOGLE_CLIENT_ID, nonce);
    const users = await readState(env, "users", {});
    const existing = users[identity.email];
    if (existing?.google_id && existing.google_id !== identity.subject) {
      return redirectWithCookies("/auth/login.html?error=google_conflict", cleared, 303);
    }
    users[identity.email] = { ...(existing || {}), password_hash: existing?.password_hash || null, google_id: identity.subject };
    await writeState(env, "users", users);
    return redirectWithCookies("/app/", [...cleared, await sessionCookie(identity.email, env.SECRET_KEY)], 303);
  } catch (error) {
    console.error("Google sign-in failed", error?.message || "unknown error");
    return redirectWithCookies("/auth/login.html?error=google_failed", cleared, 303);
  }
}

function base64url(bytes) {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function fromBase64url(value) {
  const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(normalized + "=".repeat((4 - normalized.length % 4) % 4));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0));
}

async function hmac(value, secret) {
  const key = await crypto.subtle.importKey("raw", textEncoder.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return base64url(new Uint8Array(await crypto.subtle.sign("HMAC", key, textEncoder.encode(value))));
}

async function sessionCookie(email, secret) {
  const payload = base64url(textEncoder.encode(JSON.stringify({ email, exp: Math.floor(Date.now() / 1000) + SESSION_TTL })));
  return `${SESSION_COOKIE}=${payload}.${await hmac(payload, secret)}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${SESSION_TTL}`;
}

async function currentUser(request, env) {
  const raw = request.headers.get("cookie") || "";
  const cookie = raw.split(";").map((part) => part.trim()).find((part) => part.startsWith(`${SESSION_COOKIE}=`));
  if (!cookie || !env.SECRET_KEY) return null;
  const token = cookie.slice(SESSION_COOKIE.length + 1);
  const split = token.lastIndexOf(".");
  if (split < 1) return null;
  const payload = token.slice(0, split);
  const signature = token.slice(split + 1);
  if (signature !== await hmac(payload, env.SECRET_KEY)) return null;
  try {
    const session = JSON.parse(textDecoder.decode(fromBase64url(payload)));
    return session.exp > Math.floor(Date.now() / 1000) ? session.email : null;
  } catch {
    return null;
  }
}

async function readState(env, key, fallback) {
  const row = await env.DB.prepare("SELECT value FROM app_state WHERE key = ? LIMIT 1").bind(key).first();
  if (!row?.value) return fallback;
  try { return JSON.parse(row.value); } catch { return fallback; }
}

async function writeState(env, key, value) {
  const serialized = JSON.stringify(value);
  if (textEncoder.encode(serialized).byteLength > MAX_STATE_BYTES) throw new Error("Données trop volumineuses pour le stockage.");
  await env.DB.prepare(
    "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) " +
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP",
  ).bind(key, serialized).run();
}

async function passwordHash(password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", textEncoder.encode(password), "PBKDF2", false, ["deriveBits"]);
  const digest = new Uint8Array(await crypto.subtle.deriveBits({ name: "PBKDF2", salt, iterations: 100_000, hash: "SHA-256" }, key, 256));
  return `pbkdf2-sha256$100000$${base64url(salt)}$${base64url(digest)}`;
}

async function verifyPassword(password, storedHash) {
  if (/^[a-f0-9]{64}$/i.test(storedHash || "")) {
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", textEncoder.encode(password)));
    const legacy = [...digest].map((value) => value.toString(16).padStart(2, "0")).join("");
    let mismatch = legacy.length ^ storedHash.length;
    for (let i = 0; i < Math.min(legacy.length, storedHash.length); i++) mismatch |= legacy.charCodeAt(i) ^ storedHash.charCodeAt(i);
    return { valid: mismatch === 0, upgradedHash: mismatch === 0 ? await passwordHash(password) : null };
  }
  const parts = String(storedHash || "").split("$");
  if (parts.length !== 4 || parts[0] !== "pbkdf2-sha256" || Number(parts[1]) !== 100_000) return { valid: false, upgradedHash: null };
  const key = await crypto.subtle.importKey("raw", textEncoder.encode(password), "PBKDF2", false, ["deriveBits"]);
  const digest = new Uint8Array(await crypto.subtle.deriveBits({ name: "PBKDF2", salt: fromBase64url(parts[2]), iterations: 100_000, hash: "SHA-256" }, key, 256));
  const expected = fromBase64url(parts[3]);
  if (digest.length !== expected.length) return { valid: false, upgradedHash: null };
  let mismatch = 0;
  for (let i = 0; i < digest.length; i++) mismatch |= digest[i] ^ expected[i];
  return { valid: mismatch === 0, upgradedHash: null };
}

function safeMessages(input) {
  if (!Array.isArray(input) || input.length > 2000) return null;
  let total = 0;
  const messages = [];
  for (const message of input) {
    if (!message || !["user", "assistant"].includes(message.role)) continue;
    let content = typeof message.content === "string" ? message.content : "";
    content = content.replace(/data:image\/[^;]+;base64,[A-Za-z0-9+/=]+/g, "[Image jointe]");
    total += content.length;
    if (total > 200_000) return null;
    messages.push({ role: message.role, content });
  }
  return messages;
}

function authRequired() { return json({ error: "Connectez-vous pour utiliser cette fonction." }, 401); }

async function createSession(email, env) {
  const histories = await readState(env, "history", {});
  const sessions = Array.isArray(histories[email]) ? histories[email] : [];
  const created = { id: crypto.randomUUID(), title: "Nouvelle conversation", created_at: new Date().toISOString(), messages: [] };
  sessions.push(created);
  histories[email] = sessions;
  await writeState(env, "history", histories);
  return created;
}

async function getSession(email, id, env) {
  const histories = await readState(env, "history", {});
  return (Array.isArray(histories[email]) ? histories[email] : []).find((item) => item.id === id) || null;
}

async function putSession(email, id, messages, env) {
  const histories = await readState(env, "history", {});
  const sessions = Array.isArray(histories[email]) ? histories[email] : [];
  let item = sessions.find((session) => session.id === id);
  if (!item) {
    item = { id: id || crypto.randomUUID(), title: "Nouvelle conversation", created_at: new Date().toISOString(), messages: [] };
    sessions.push(item);
  }
  item.messages = messages;
  if (!item.title || item.title === "Nouvelle conversation") {
    const first = messages.find((message) => message.role === "user");
    if (first?.content) item.title = first.content.slice(0, 80);
  }
  histories[email] = sessions;
  await writeState(env, "history", histories);
  return item;
}

async function imageResponse(prompt, env) {
  if (!env.AI) return json({ error: "Le service de génération d’images n’est pas configuré." }, 503);
  if (!prompt || prompt.length > 1800) return json({ error: "La description doit contenir entre 1 et 1 800 caractères." }, 400);
  try {
    const result = await env.AI.run(env.AI_IMAGE_MODEL || "@cf/black-forest-labs/flux-1-schnell", { prompt, steps: 4 });
    if (!result?.image) return json({ error: "Le fournisseur n’a renvoyé aucune image." }, 502);
    return json({ image_base64: result.image, mime_type: "image/jpeg", source: "cloudflare", text: `Image générée : ${prompt}` });
  } catch (error) {
    console.error("Workers AI image generation failed", error?.message || "unknown error");
    return json({ error: "La génération d’image est momentanément indisponible." }, 503);
  }
}

function plainExport(messages, title, format) {
  if (format === "json") return { body: JSON.stringify({ title, messages }, null, 2), type: "application/json; charset=utf-8" };
  if (format === "md") return { body: `# ${title}\n\n` + messages.map((m) => `**${m.role === "user" ? "Vous" : "Nafti AI"}**\n\n${m.content}`).join("\n\n---\n\n"), type: "text/markdown; charset=utf-8" };
  const underline = "=".repeat(Math.min(title.length, 80));
  return { body: `${title}\n${underline}\n\n` + messages.map((m) => `${m.role === "user" ? "Vous" : "Nafti AI"}\n${m.content}`).join("\n\n"), type: "text/plain; charset=utf-8" };
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
}

async function handle(request, env) {
  const url = new URL(request.url);
  const path = url.pathname;
  const method = request.method.toUpperCase();

  if (method === "GET" && (path === "/" || path === "/app/")) {
    const assetPath = path === "/" ? "/index.html" : "/app/index.html";
    const assetUrl = new URL(`${assetPath}${url.search}`, request.url);
    return env.ASSETS.fetch(new Request(assetUrl, { method: "GET", headers: request.headers }));
  }
  if (method === "GET" && path === "/app") return redirect(`/app/${url.search}`, 308);

  if (method === "GET" && ["/health", "/healthz"].includes(path)) return json({ ok: true, service: "nafti-ai-edge", storage: "d1", text: "groq", images: "workers-ai" });
  if (method === "GET" && path === "/auth/login") return redirect("/auth/login.html", 302);
  if (method === "GET" && path === "/auth/google") return googleStart(request, env);
  if (method === "GET" && path === GOOGLE_REDIRECT_PATH) return googleCallback(request, env);
  if (method === "GET" && ["/history", "/settings", "/profile", "/gallery"].includes(path)) {
    const pages = { "/history": "/app/history.html", "/settings": "/app/settings.html", "/profile": "/app/profile.html", "/gallery": "/app/gallery.html" };
    return redirect(pages[path], 302);
  }
  if (method === "GET" && path === "/generate-image") return redirect((await currentUser(request, env)) ? "/app/generate.html" : "/auth/login.html", 302);
  if (method === "GET" && path === "/logout") return redirect("/", 302, { "set-cookie": `${SESSION_COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0` });

  if (method === "POST" && ["/login", "/register"].includes(path)) {
    const form = await request.formData();
    const email = String(form.get("email") || "").trim().toLowerCase();
    const password = String(form.get("password") || "");
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || password.length < 8 || password.length > 256) return redirect("/auth/login.html?error=invalid");
    const users = await readState(env, "users", {});
    if (path === "/register") {
      if (users[email]) return redirect("/auth/login.html?error=exists");
      users[email] = { password_hash: await passwordHash(password), google_id: null };
      await writeState(env, "users", users);
      return redirect("/app/", 303, { "set-cookie": await sessionCookie(email, env.SECRET_KEY) });
    }
    if (!users[email]) return redirect("/auth/login.html?error=invalid");
    const verification = await verifyPassword(password, users[email].password_hash);
    if (!verification.valid) return redirect("/auth/login.html?error=invalid");
    if (verification.upgradedHash) {
      users[email].password_hash = verification.upgradedHash;
      await writeState(env, "users", users);
    }
    return redirect("/app/", 303, { "set-cookie": await sessionCookie(email, env.SECRET_KEY) });
  }

  if (path === "/api/me" && method === "GET") {
    const email = await currentUser(request, env);
    return email ? json({ email }) : authRequired();
  }

  if (path === "/api/history" && method === "GET") {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    const histories = await readState(env, "history", {});
    return json({ sessions: Array.isArray(histories[email]) ? histories[email] : [] });
  }

  if (path === "/session/new" && method === "POST") {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    try { return json(await createSession(email, env)); } catch (error) { return json({ error: error.message }, 507); }
  }

  const sessionMatch = path.match(/^\/api\/session\/([\w-]{1,80})$/);
  if (method === "GET" && sessionMatch) {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    const item = await getSession(email, sessionMatch[1], env);
    return item ? json(item) : json({ error: "Conversation introuvable." }, 404);
  }

  if (method === "POST" && path === "/api/save-session") {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    const data = await request.json().catch(() => ({}));
    const messages = safeMessages(data.messages);
    if (!messages) return json({ error: "Conversation invalide ou trop volumineuse." }, 413);
    try {
      const saved = await putSession(email, typeof data.session_id === "string" ? data.session_id : null, messages, env);
      return json({ status: "saved", session_id: saved.id });
    } catch (error) { return json({ error: error.message }, 507); }
  }

  if (method === "POST" && ["/session/clear", "/session/delete", "/sessions/clear-all"].includes(path)) {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    const histories = await readState(env, "history", {});
    let sessions = Array.isArray(histories[email]) ? histories[email] : [];
    if (path === "/sessions/clear-all") sessions = [];
    else {
      const data = await request.json().catch(() => ({}));
      if (!data.session_id) return json({ error: "session_id manquant." }, 400);
      const exists = sessions.some((item) => item.id === data.session_id);
      if (!exists) return json({ error: "Conversation introuvable." }, 404);
      sessions = path === "/session/delete" ? sessions.filter((item) => item.id !== data.session_id) : sessions.map((item) => item.id === data.session_id ? { ...item, messages: [] } : item);
    }
    histories[email] = sessions;
    try { await writeState(env, "history", histories); } catch (error) { return json({ error: error.message }, 507); }
    return json({ status: path === "/session/delete" ? "deleted" : "cleared" });
  }

  if (method === "POST" && path === "/api/ai") {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    if (!env.GROQ_API_KEY) return json({ error: "Clé Groq non configurée." }, 503);
    const data = await request.json().catch(() => ({}));
    const sessionId = typeof data.session_id === "string" ? data.session_id : "";
    if (!sessionId || !Array.isArray(data.messages) || !data.messages.length) return json({ error: "Session ou messages manquants." }, 400);
    const messages = data.messages.filter((message) => message && ["system", "user", "assistant"].includes(message.role)).slice(-40);
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    const prompt = typeof lastUser?.content === "string" ? lastUser.content : "Analyse la pièce jointe.";
    const system = messages.find((message) => message.role === "system") || { role: "system", content: "Tu es Nafti AI, un assistant bienveillant et fiable. Réponds dans la langue de l’utilisateur. Sois clair, précis et utile; structure en Markdown si nécessaire." };
    const userMessages = messages.filter((message) => message.role !== "system");
    const images = Array.isArray(data.images) ? data.images.filter((image) => typeof image === "string" && image.startsWith("data:image/")).slice(0, 4) : (typeof data.image === "string" && data.image.startsWith("data:image/") ? [data.image] : []);
    let providerMessages = [system, ...userMessages];
    let model = env.GROQ_MODEL || "openai/gpt-oss-120b";
    if (images.length && lastUser) {
      if (images.some((image) => image.length > 8_000_000)) return json({ error: "Image trop volumineuse." }, 413);
      providerMessages = [system, ...userMessages];
      const index = providerMessages.length - 1;
      const userText = typeof providerMessages[index].content === "string" ? providerMessages[index].content : "Analyse cette image.";
      providerMessages[index] = { role: "user", content: [{ type: "text", text: userText }, ...images.map((url) => ({ type: "image_url", image_url: { url } }))] };
      model = env.GROQ_VISION_MODEL || "qwen/qwen3.8-27b";
    }
    try {
      const providerResponse = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: { authorization: `Bearer ${env.GROQ_API_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ model, messages: providerMessages, temperature: 0.7, max_tokens: 1500 }),
        signal: AbortSignal.timeout(55_000),
      });
      const result = await providerResponse.json().catch(() => ({}));
      if (!providerResponse.ok) return json({ error: result.error?.message || `Groq a répondu HTTP ${providerResponse.status}.` }, 502);
      const answer = result.choices?.[0]?.message?.content;
      if (typeof answer !== "string" || !answer.trim()) return json({ error: "Groq n’a renvoyé aucune réponse." }, 502);
      const persisted = safeMessages(userMessages);
      if (persisted) {
        persisted.push({ role: "assistant", content: answer });
        await putSession(email, sessionId, persisted, env);
      }
      return json(result);
    } catch (error) {
      console.error("Groq request failed", error?.message || "unknown error");
      return json({ error: "Le service Groq est momentanément indisponible." }, 502);
    }
  }

  if (method === "POST" && path === "/api/chat") {
    if (!env.GROQ_API_KEY) return json({ error: "Clé Groq non configurée." }, 503);
    const data = await request.json().catch(() => ({}));
    const message = String(data.message || "").trim();
    if (!message) return json({ error: "Message requis." }, 400);
    if (message.length > 20_000) return json({ error: "Message trop long." }, 413);
    try {
      const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: { authorization: `Bearer ${env.GROQ_API_KEY}`, "content-type": "application/json" },
        body: JSON.stringify({ model: env.GROQ_MODEL || "openai/gpt-oss-120b", messages: [
          { role: "system", content: "Tu es Nafti AI, un assistant bienveillant et fiable. Réponds dans la langue de l’utilisateur. Sois précis, clair et utile." },
          { role: "user", content: message },
        ], temperature: 0.7, max_tokens: 1200 }),
        signal: AbortSignal.timeout(55_000),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) return json({ error: result.error?.message || `Groq a répondu HTTP ${response.status}.` }, 502);
      const answer = result.choices?.[0]?.message?.content;
      return typeof answer === "string" && answer.trim() ? json({ response: answer }) : json({ error: "Groq n’a renvoyé aucune réponse." }, 502);
    } catch (error) {
      console.error("Groq public chat failed", error?.message || "unknown error");
      return json({ error: "Le service Groq est momentanément indisponible." }, 502);
    }
  }

  if (method === "POST" && ["/api/generate-image", "/generate"].includes(path)) {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    const data = await request.json().catch(() => ({}));
    return imageResponse(String(data.prompt || "").trim(), env);
  }

  if (method === "POST" && path === "/api/synthesize") return json({ error: "La synthèse serveur n’est pas configurée; utilisez la voix du navigateur." }, 501);

  if (method === "POST" && path === "/api/export") {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    const data = await request.json().catch(() => ({}));
    const item = await getSession(email, data.session_id, env);
    if (!item) return json({ error: "Conversation introuvable." }, 404);
    const format = String(data.format || "txt").toLowerCase() === "markdown" ? "md" : String(data.format || "txt").toLowerCase();
    if (!["txt", "md", "json"].includes(format)) return json({ error: "Format accepté : txt, md ou json." }, 400);
    const messages = safeMessages(item.messages) || [];
    const exported = plainExport(messages, item.title || "Conversation", format);
    return new Response(exported.body, { headers: { "content-type": exported.type, "content-disposition": `attachment; filename="conversation.${format}"`, "cache-control": "private, no-store" } });
  }

  if (method === "POST" && path === "/api/share") {
    const email = await currentUser(request, env);
    if (!email) return authRequired();
    const data = await request.json().catch(() => ({}));
    const item = await getSession(email, data.session_id, env);
    const messages = safeMessages(item?.messages || []);
    if (!item || !messages?.length) return json({ error: "Conversation introuvable ou vide." }, 404);
    const id = base64url(crypto.getRandomValues(new Uint8Array(24)));
    const snapshot = { title: String(item.title || "Conversation").slice(0, 160), messages, created_at: new Date().toISOString() };
    try { await env.DB.prepare("INSERT INTO shared_conversations (id, snapshot) VALUES (?, ?)").bind(id, JSON.stringify(snapshot)).run(); }
    catch (error) { console.error("Share persistence failed", error?.message || "unknown error"); return json({ error: "Impossible d’enregistrer le lien de partage." }, 507); }
    return json({ share_url: `/share/${id}` });
  }

  const shareMatch = path.match(/^\/share\/([A-Za-z0-9_-]{20,80})$/);
  if (method === "GET" && shareMatch) {
    const row = await env.DB.prepare("SELECT snapshot FROM shared_conversations WHERE id = ? LIMIT 1").bind(shareMatch[1]).first();
    if (!row?.snapshot) return new Response("Conversation introuvable.", { status: 404 });
    let snapshot;
    try { snapshot = JSON.parse(row.snapshot); } catch { return new Response("Conversation introuvable.", { status: 404 }); }
    const cards = (snapshot.messages || []).map((message) => `<article><h2>${message.role === "user" ? "Vous" : "Nafti AI"}</h2><p>${escapeHtml(message.content).replace(/\n/g, "<br>")}</p></article>`).join("");
    const html = `<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>${escapeHtml(snapshot.title)} · Nafti AI</title><style>body{font:16px system-ui;background:#f3f5f8;color:#18202a;margin:0;padding:24px}.wrap{max-width:820px;margin:auto}article{white-space:normal;background:#fff;border:1px solid #ddd;border-radius:14px;padding:16px;margin:12px 0}h1{font-size:24px}h2{font-size:14px;color:#087f82}</style><main class="wrap"><h1>${escapeHtml(snapshot.title)}</h1>${cards}</main></html>`;
    return new Response(html, { headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store", "referrer-policy": "no-referrer", "x-robots-tag": "noindex, nofollow" } });
  }

  if (method === "POST" && path === "/api/detect-language") {
    const data = await request.json().catch(() => ({}));
    const value = String(data.text || "");
    if (!value.trim()) return json({ error: "Empty text", language: "fr", confidence: 0.5, method: "fallback" }, 400);
    const language = /[\u0600-\u06FF]/.test(value) ? "ar" : /[àâçèêëîïôùûüÿœæ]/i.test(value) ? "fr" : /\b(the|and|you|what|this|with|hello|thanks)\b/i.test(value) ? "en" : "fr";
    return json({ language, confidence: language === "fr" ? 0.72 : 0.9, method: "script_and_keywords", text_length: value.length, word_count: value.trim().split(/\s+/).length });
  }

  return json({ error: "Route introuvable." }, 404);
}

export default {
  async fetch(request, env) {
    try { return await handle(request, env); }
    catch (error) {
      console.error("Nafti AI Worker error", error?.message || "unknown error");
      return json({ error: "Erreur interne du Worker." }, 500);
    }
  },
};
