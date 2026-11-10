"""Small requests-compatible HTTP client for Cloudflare's Python Worker runtime."""

import json

from js import AbortSignal
from pyodide.ffi import run_sync
from pyodide.http import pyfetch


class RequestException(Exception):
    pass


class Timeout(RequestException):
    pass


class ConnectionError(RequestException):
    pass


class HTTPError(RequestException):
    def __init__(self, response):
        self.response = response
        super().__init__(f"HTTP {response.status_code} for {response.url}")


class _Exceptions:
    RequestException = RequestException
    Timeout = Timeout
    ConnectionError = ConnectionError
    HTTPError = HTTPError


exceptions = _Exceptions()


class Response:
    def __init__(self, response, url):
        self._response = response
        self.url = url
        self.status_code = int(response.status)
        content_type = response.headers.get("content-type")
        self.headers = {"content-type": content_type} if content_type else {}
        self._content = run_sync(response.bytes())
        self.text = self._content.decode("utf-8", errors="replace")

    @property
    def content(self):
        return self._content

    @property
    def ok(self):
        return 200 <= self.status_code < 400

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise HTTPError(self)


def request(method, url, *, headers=None, json_body=None, timeout=None):
    options = {"method": method, "headers": headers or {}}
    if json_body is not None:
        options["body"] = json.dumps(json_body, ensure_ascii=False)
    if timeout:
        options["signal"] = AbortSignal.timeout(max(1, int(float(timeout) * 1000)))
    try:
        response = run_sync(pyfetch(url, **options))
    except Exception as exc:
        message = str(exc).lower()
        if "abort" in message or "timeout" in message or "timed out" in message:
            raise Timeout(str(exc)) from exc
        raise ConnectionError(str(exc)) from exc
    return Response(response, url)


def post(url, *, headers=None, json=None, timeout=None, **_kwargs):
    return request("POST", url, headers=headers, json_body=json, timeout=timeout)


def get(url, *, headers=None, timeout=None, **_kwargs):
    return request("GET", url, headers=headers, timeout=timeout)
