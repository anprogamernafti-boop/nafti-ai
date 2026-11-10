import test from "node:test";
import assert from "node:assert/strict";
import worker from "./worker.js";

function makeDb() {
  const rows = new Map();
  return {
    rows,
    prepare(sql) {
      return {
        bind(...args) {
          return {
            first: async () => ({ value: rows.get(args[0]) || null }),
            run: async () => { rows.set(args[0], args[1]); return {}; },
          };
        },
      };
    },
  };
}

function base64urlJson(value) {
  return Buffer.from(JSON.stringify(value)).toString("base64url");
}

test("Google OAuth uses state and PKCE, validates the ID token, and creates a session", async () => {
  const db = makeDb();
  const env = {
    GOOGLE_CLIENT_ID: "test-client-id",
    GOOGLE_CLIENT_SECRET: "test-client-secret",
    GOOGLE_REDIRECT_URI: "https://nafti-ai-edge.asser-dev.workers.dev/auth/google/callback",
    SECRET_KEY: "test-session-signing-secret",
    DB: db,
  };
  const keyPair = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign", "verify"],
  );
  const jwk = await crypto.subtle.exportKey("jwk", keyPair.publicKey);
  jwk.kid = "test-google-key";

  const previousFetch = globalThis.fetch;
  let idToken = "";
  globalThis.fetch = async (input, init) => {
    const endpoint = String(input);
    if (endpoint.includes("/oauth2/v3/certs")) return Response.json({ keys: [jwk] });
    if (endpoint === "https://oauth2.googleapis.com/token") {
      const form = new URLSearchParams(init.body);
      assert.equal(form.get("client_secret"), env.GOOGLE_CLIENT_SECRET);
      assert.ok(form.get("code_verifier"));
      return Response.json({ id_token: idToken });
    }
    throw new Error("Unexpected network request in OAuth test");
  };

  try {
    const start = await worker.fetch(new Request("https://nafti-ai-edge.asser-dev.workers.dev/auth/google"), env);
    assert.equal(start.status, 302);
    const authorization = new URL(start.headers.get("location"));
    assert.equal(authorization.origin, "https://accounts.google.com");
    assert.equal(authorization.searchParams.get("scope"), "openid email profile");
    assert.equal(authorization.searchParams.get("code_challenge_method"), "S256");
    assert.equal(authorization.searchParams.get("redirect_uri"), "https://nafti-ai-edge.asser-dev.workers.dev/auth/google/callback");

    const claims = {
      iss: "https://accounts.google.com",
      aud: env.GOOGLE_CLIENT_ID,
      sub: "oauth-test-subject",
      email: "google-test@example.invalid",
      email_verified: true,
      iat: Math.floor(Date.now() / 1000),
      exp: Math.floor(Date.now() / 1000) + 300,
      nonce: authorization.searchParams.get("nonce"),
    };
    const signingInput = `${base64urlJson({ alg: "RS256", kid: jwk.kid, typ: "JWT" })}.${base64urlJson(claims)}`;
    const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", keyPair.privateKey, new TextEncoder().encode(signingInput));
    idToken = `${signingInput}.${Buffer.from(signature).toString("base64url")}`;

    const cookieHeader = start.headers.getSetCookie().map((cookie) => cookie.split(";")[0]).join("; ");
    const callbackUrl = new URL("https://nafti-ai-edge.asser-dev.workers.dev/auth/google/callback");
    callbackUrl.searchParams.set("code", "test-code");
    callbackUrl.searchParams.set("state", authorization.searchParams.get("state"));
    const callback = await worker.fetch(new Request(callbackUrl, { headers: { cookie: cookieHeader } }), env);
    assert.equal(callback.status, 303);
    assert.equal(callback.headers.get("location"), "/app/");
    assert.ok(callback.headers.getSetCookie().some((cookie) => cookie.startsWith("nafti_session=")));
    const users = JSON.parse(db.rows.get("users"));
    assert.equal(users["google-test@example.invalid"].google_id, "oauth-test-subject");

    const rejected = await worker.fetch(new Request(
      "https://nafti-ai-edge.asser-dev.workers.dev/auth/google/callback?code=replayed&state=wrong",
      { headers: { cookie: cookieHeader } },
    ), env);
    assert.equal(rejected.status, 303);
    assert.match(rejected.headers.get("location"), /error=google_state/);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("Google login fails closed when OAuth secrets are absent", async () => {
  const response = await worker.fetch(new Request("https://nafti-ai-edge.asser-dev.workers.dev/auth/google"), {});
  assert.equal(response.status, 303);
  assert.match(response.headers.get("location"), /google_unconfigured/);
});
