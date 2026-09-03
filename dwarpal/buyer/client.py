"""HTTP client for the Dwarpal agent API. Works against a live server or an in-process TestClient."""
from __future__ import annotations

import uuid

import httpx


class GateAPIError(Exception):
    def __init__(self, status_code: int, body: dict):
        super().__init__(f"{status_code}: {body.get('message', body)}")
        self.status_code = status_code
        self.body = body


class GateClient:
    def __init__(self, base_url: str, api_key: str, http=None, timeout_s: int = 30):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.http = http or httpx.Client(timeout=timeout_s)

    def _request(self, method: str, path: str, json=None, headers: dict | None = None, params: dict | None = None):
        h = {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}
        if headers:
            h.update(headers)
        r = self.http.request(method, self.base_url + path, json=json, headers=h, params=params)
        try:
            body = r.json()
        except ValueError:
            body = {"message": r.text}
        if r.status_code >= 400:
            raise GateAPIError(r.status_code, body if isinstance(body, dict) else {"message": str(body)})
        return body

    def discovery(self) -> dict:
        return self._request("GET", "/.well-known/agent-commerce.json")

    def products(self, q: str | None = None, category: str | None = None) -> list[dict]:
        params = {k: v for k, v in (("q", q), ("category", category)) if v}
        return self._request("GET", "/agent/v1/products", params=params)["items"]

    def create(self, items: list[dict], idempotency_key: str | None = None) -> dict:
        return self._request("POST", "/agent/v1/checkout_sessions", json={"items": items},
                             headers={"Idempotency-Key": idempotency_key or uuid.uuid4().hex})

    def update(self, session_id: str, items: list[dict]) -> dict:
        return self._request("POST", f"/agent/v1/checkout_sessions/{session_id}", json={"items": items})

    def complete(self, session_id: str, idempotency_key: str | None = None) -> dict:
        return self._request("POST", f"/agent/v1/checkout_sessions/{session_id}/complete",
                             headers={"Idempotency-Key": idempotency_key or uuid.uuid4().hex})

    def get(self, session_id: str) -> dict:
        return self._request("GET", f"/agent/v1/checkout_sessions/{session_id}")

    def trail(self, session_id: str) -> dict:
        return self._request("GET", f"/agent/v1/checkout_sessions/{session_id}/trail")

    def cancel(self, session_id: str) -> dict:
        return self._request("POST", f"/agent/v1/checkout_sessions/{session_id}/cancel")
