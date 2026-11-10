"""Cloudflare Worker bindings used by the existing Flask application."""

from urllib.parse import quote

from flask import Response, request
from jinja2 import BaseLoader, TemplateNotFound
from pyodide.ffi import run_sync


def _worker_env():
    try:
        return request.environ.get("workers.env")
    except (RuntimeError, AttributeError):
        return None


def read_state(key, default):
    env = _worker_env()
    if env is None or getattr(env, "DB", None) is None:
        return default
    row = run_sync(env.DB.prepare("SELECT value FROM app_state WHERE key = ? LIMIT 1").bind(key).first())
    if row is None:
        return default
    try:
        raw_value = row["value"]
    except (KeyError, TypeError, AttributeError):
        raw_value = getattr(row, "value", None)
    if raw_value is None:
        return default
    import json
    return json.loads(str(raw_value))


def write_state(key, value):
    env = _worker_env()
    if env is None or getattr(env, "DB", None) is None:
        raise RuntimeError("La base D1 n'est pas liée à ce Worker.")
    import json
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 1_800_000:
        raise ValueError("Les données dépassent la taille maximale de stockage de l’application.")
    run_sync(
        env.DB.prepare(
            "INSERT INTO app_state (key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP"
        ).bind(key, payload).run()
    )


def generate_image_with_workers_ai(prompt):
    env = _worker_env()
    ai = getattr(env, "AI", None) if env is not None else None
    if ai is None:
        raise RuntimeError("Le binding Workers AI n'est pas configuré.")
    import os
    model = os.getenv("AI_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
    result = run_sync(ai.run(model, {"prompt": prompt, "steps": 4}))
    try:
        image = result["image"]
    except (KeyError, TypeError, AttributeError):
        image = getattr(result, "image", None)
    if not image:
        raise RuntimeError("Workers AI n'a renvoyé aucune image.")
    return str(image)


def save_shared_conversation(share_id, snapshot):
    env = _worker_env()
    if env is None or getattr(env, "DB", None) is None:
        raise RuntimeError("La base D1 n'est pas liée à ce Worker.")
    import json
    payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 1_800_000:
        raise ValueError("Cette conversation est trop volumineuse pour être partagée.")
    run_sync(
        env.DB.prepare(
            "INSERT INTO shared_conversations (id, snapshot) VALUES (?, ?)"
        ).bind(share_id, payload).run()
    )


def load_shared_conversation(share_id):
    env = _worker_env()
    if env is None or getattr(env, "DB", None) is None:
        return None
    row = run_sync(
        env.DB.prepare(
            "SELECT snapshot FROM shared_conversations WHERE id = ? LIMIT 1"
        ).bind(share_id).first()
    )
    if row is None:
        return None
    try:
        raw_value = row["snapshot"]
    except (KeyError, TypeError, AttributeError):
        raw_value = getattr(row, "snapshot", None)
    if raw_value is None:
        return None
    import json
    return json.loads(str(raw_value))


def _fetch_asset(path):
    env = _worker_env()
    if env is None or getattr(env, "ASSETS", None) is None:
        raise RuntimeError("Les assets Cloudflare ne sont pas liés à ce Worker.")
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    response = run_sync(env.ASSETS.fetch(f"https://assets.local/{encoded_path}"))
    # Python Workers expose Fetch Response bodies through the Pyodide-compatible
    # bytes() coroutine; the JavaScript arrayBuffer() method is not available on
    # this wrapped Response object.
    content = run_sync(response.bytes())
    content_type = response.headers.get("content-type")
    return int(response.status), content, content_type


def serve_asset(path, content_type=None):
    status, body, detected_type = _fetch_asset(path)
    return Response(
        body,
        status=status,
        content_type=content_type or detected_type or "application/octet-stream",
        headers={"Cache-Control": "public, max-age=3600"},
    )


class CloudflareTemplateLoader(BaseLoader):
    def get_source(self, environment, template):
        if not template or template.startswith("/") or ".." in template.split("/"):
            raise TemplateNotFound(template)
        status, body, _content_type = _fetch_asset(f"templates/{template}")
        if status >= 400:
            raise TemplateNotFound(template)
        source = body.decode("utf-8")
        filename = f"cloudflare-assets://templates/{template}"
        return source, filename, lambda: True
