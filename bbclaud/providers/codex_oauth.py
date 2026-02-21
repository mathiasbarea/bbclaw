"""
Proveedor Codex con autenticación OAuth 2.0 + PKCE.

Flujo:
1. Genera code_verifier + code_challenge
2. Abre browser → login OpenAI
3. Escucha callback HTTP en localhost
4. Intercambia code → access_token + refresh_token
5. Persiste tokens encriptados en keyring del OS
6. Auto-refresca cuando el token expira
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Event, Thread
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .base import LLMProvider, LLMResponse, Message, ToolCall

logger = logging.getLogger(__name__)

# ── Constantes OAuth de OpenAI Codex ────────────────────────────────────────
# Valores exactos extraídos del código fuente de OpenCode + Codex CLI oficial.
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"   # NOta: /oauth/ en el path
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"            # Client ID público oficial
OPENAI_REDIRECT_PORT = 1455
OPENAI_REDIRECT_URI = f"http://localhost:{OPENAI_REDIRECT_PORT}/auth/callback"
OPENAI_SCOPE = "openid profile email offline_access"
KEYRING_SERVICE = "bbclaud"
KEYRING_KEY = "codex_oauth_tokens"


def _pkce_pair() -> tuple[str, str]:
    """Genera code_verifier y code_challenge para PKCE."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    return verifier, challenge


class _CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler mínimo para recibir el callback OAuth."""

    auth_code: str | None = None
    done_event: Event = Event()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/auth/callback":     # Puerto 1455, path /auth/callback
            params = parse_qs(parsed.query)
            if "code" in params:
                _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;margin:60px auto;max-width:400px;text-align:center'>"
                b"<h2>&#x2705; Autenticaci&#xf3;n exitosa</h2>"
                b"<p>Pod&#xe9;s cerrar esta ventana y volver a la terminal.</p>"
                b"</body></html>"
            )
            _CallbackHandler.done_event.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        pass  # Silenciar logs del servidor HTTP


def _run_local_server(port: int = OPENAI_REDIRECT_PORT) -> tuple[HTTPServer, Thread]:
    _CallbackHandler.auth_code = None
    _CallbackHandler.done_event.clear()
    server = HTTPServer(("localhost", port), _CallbackHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class CodexOAuthProvider(LLMProvider):
    """
    Proveedor LLM usando Codex de OpenAI con autenticación OAuth.
    Compatible con la interfaz LLMProvider abstracta.
    """

    _MODEL = "gpt-5.3-codex"
    _BASE_URL = "https://api.openai.com/v1"

    def __init__(self, base_url: str | None = None):
        self._base_url = base_url or self._BASE_URL
        self._tokens: dict | None = None
        self._client = httpx.AsyncClient(timeout=120)

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _load_tokens(self) -> dict | None:
        """Carga tokens desde keyring del OS."""
        try:
            import keyring

            raw = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning("No se pudo cargar tokens del keyring: %s", e)
        return None

    def _save_tokens(self, tokens: dict) -> None:
        """Persiste tokens en keyring del OS."""
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, json.dumps(tokens))
        except Exception as e:
            logger.warning("No se pudo guardar tokens en keyring: %s", e)

    def _is_expired(self, tokens: dict) -> bool:
        """Devuelve True si el access_token está a menos de 60s de expirar."""
        return time.time() >= tokens.get("expires_at", 0) - 60

    async def _refresh(self, refresh_token: str) -> dict:
        """Refresca el access_token usando el refresh_token."""
        resp = await self._client.post(
            OPENAI_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": OPENAI_CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        data["expires_at"] = time.time() + data.get("expires_in", 3600)
        return data

    def _do_browser_auth(self) -> dict:
        """
        Ejecuta el flujo OAuth completo en browser.
        Bloqueante — se llama desde un executor para no bloquear el loop async.
        """
        verifier, challenge = _pkce_pair()
        state = secrets.token_urlsafe(16)

        auth_params = {
            "response_type": "code",
            "client_id": OPENAI_CLIENT_ID,
            "redirect_uri": OPENAI_REDIRECT_URI,
            "scope": OPENAI_SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            # Parámetros requeridos por el flujo simplificado del Codex CLI:
            "codex_cli_simplified_flow": "true",
            "id_token_add_organizations": "true",
        }
        auth_url = f"{OPENAI_AUTH_URL}?{urlencode(auth_params)}"

        server, _thread = _run_local_server()

        print("\n🔐 Abriendo browser para autenticar con OpenAI Codex...")
        print(f"   Si no abre automáticamente, visita:\n   {auth_url}\n")
        webbrowser.open(auth_url)

        # Esperar callback (max 3 minutos)
        _CallbackHandler.done_event.wait(timeout=180)
        server.shutdown()

        if not _CallbackHandler.auth_code:
            raise RuntimeError("No se recibió el código de autorización OAuth. Tiempo agotado.")

        # Intercambiar code → tokens
        resp = httpx.post(
            OPENAI_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": OPENAI_CLIENT_ID,
                "code": _CallbackHandler.auth_code,
                "redirect_uri": OPENAI_REDIRECT_URI,
                "code_verifier": verifier,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        data["expires_at"] = time.time() + data.get("expires_in", 3600)
        return data

    async def get_token(self) -> str:
        """Devuelve un access_token válido, autenticando o refrescando si es necesario."""
        # Intentar cargar desde keyring si no tenemos en memoria
        if self._tokens is None:
            self._tokens = self._load_tokens()

        # Si no hay tokens → flujo completo de browser
        if self._tokens is None:
            loop = asyncio.get_event_loop()
            self._tokens = await loop.run_in_executor(None, self._do_browser_auth)
            self._save_tokens(self._tokens)

        # Si el token expiró → refrescar
        elif self._is_expired(self._tokens):
            logger.info("Token expirado, refrescando...")
            self._tokens = await self._refresh(self._tokens["refresh_token"])
            self._save_tokens(self._tokens)

        return self._tokens["access_token"]

    async def logout(self) -> None:
        """Elimina los tokens guardados."""
        self._tokens = None
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, KEYRING_KEY)
        except Exception:
            pass
        print("✓ Sesión cerrada.")

    # ── LLM Calls ─────────────────────────────────────────────────────────────

    def _messages_to_dict(self, messages: list[Message]) -> list[dict]:
        result = []
        for m in messages:
            d: dict = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.name:
                d["name"] = m.name
            result.append(d)
        return result

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        token = await self.get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        body: dict = {
            "model": self._MODEL,
            "messages": self._messages_to_dict(messages),
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        resp = await self._client.post(
            f"{self._base_url}/chat/completions",
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        msg = choice["message"]
        content = msg.get("content")
        finish_reason = choice.get("finish_reason", "stop")

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls") or []:
            tool_calls.append(
                ToolCall(
                    id=tc["id"],
                    name=tc["function"]["name"],
                    arguments=json.loads(tc["function"]["arguments"]),
                )
            )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=data.get("usage", {}),
        )

    async def embed(self, text: str) -> list[float]:
        """
        Genera embedding. Usa el modelo de embeddings de OpenAI si el token OAuth
        da acceso, de lo contrario debería caer al provider local de embeddings.
        """
        token = await self.get_token()
        resp = await self._client.post(
            f"{self._base_url}/embeddings",
            headers={"Authorization": f"Bearer {token}"},
            json={"model": "text-embedding-3-small", "input": text},
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]

    @property
    def model(self) -> str:
        return self._MODEL

    @property
    def supports_tools(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
