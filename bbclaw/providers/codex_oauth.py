"""
Proveedor Codex con autenticación OAuth 2.0 + PKCE.

IMPORTANTE: gpt-5.3-codex NO usa api.openai.com/v1/chat/completions.
Usa el endpoint: https://chatgpt.com/backend-api/codex/responses
con la Responses API (streaming SSE), y requiere el header 'chatgpt-account-id'.

Flujo:
1. Genera code_verifier + code_challenge (PKCE)
2. Abre browser → login OpenAI en auth.openai.com/oauth/authorize
3. Escucha callback HTTP en localhost:1455/auth/callback
4. Intercambia code → access_token + refresh_token
5. Fetcha el account_id desde chatgpt.com/backend-api/accounts/check
6. Persiste tokens en archivo (keyring como intento primario)
7. Auto-refresca cuando el token expira
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import stat
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event, Thread
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .base import LLMProvider, LLMResponse, Message, ToolCall
from ..identity import SYSTEM_NAME

logger = logging.getLogger(__name__)

# ── Constantes OAuth de OpenAI Codex ─────────────────────────────────────────
# Valores idénticos a los del Codex CLI oficial y OpenCode.
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENAI_REDIRECT_PORT = 1455
OPENAI_REDIRECT_URI = f"http://localhost:{OPENAI_REDIRECT_PORT}/auth/callback"
OPENAI_SCOPE = "openid profile email offline_access"

# ── Endpoint de la Responses API de Codex ────────────────────────────────────
CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_ACCOUNTS_URL = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"

KEYRING_SERVICE = SYSTEM_NAME
KEYRING_KEY = "codex_oauth_tokens"
TOKEN_FILE = Path("data/.tokens.json")


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
    """HTTP handler mínimo para recibir el callback OAuth en localhost:1455."""

    auth_code: str | None = None
    done_event: Event = Event()

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/auth/callback":
            params = parse_qs(parsed.query)
            if "code" in params:
                _CallbackHandler.auth_code = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='font-family:sans-serif;margin:60px auto;"
                b"max-width:400px;text-align:center'>"
                b"<h2>&#x2705; Autenticacion exitosa</h2>"
                b"<p>Podes cerrar esta ventana y volver a la terminal.</p>"
                b"</body></html>"
            )
            _CallbackHandler.done_event.set()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        pass  # Silenciar logs del servidor HTTP


def _run_local_server() -> tuple[HTTPServer, Thread]:
    _CallbackHandler.auth_code = None
    _CallbackHandler.done_event.clear()
    server = HTTPServer(("localhost", OPENAI_REDIRECT_PORT), _CallbackHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class CodexOAuthProvider(LLMProvider):
    """
    Proveedor LLM para gpt-5.3-codex con autenticación OAuth.
    Usa https://chatgpt.com/backend-api/codex/responses (Responses API + SSE).
    """

    _MODEL = "gpt-5.3-codex"
    _TOKEN_FILE = TOKEN_FILE

    def __init__(self, base_url: str | None = None):
        # base_url ignorado — el endpoint de Codex es fijo
        self._tokens: dict | None = None
        self._client = httpx.AsyncClient(timeout=120)

    # ── Persistencia de tokens ────────────────────────────────────────────────

    def _load_tokens(self) -> dict | None:
        """Carga tokens: primero keyring, luego archivo de fallback."""
        try:
            import keyring
            raw = keyring.get_password(KEYRING_SERVICE, KEYRING_KEY)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.debug("Keyring no disponible (%s), probando archivo...", e)

        try:
            p = Path(self._TOKEN_FILE)
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("No se pudo leer archivo de tokens: %s", e)

        return None

    def _save_tokens(self, tokens: dict) -> None:
        """Guarda tokens: primero keyring, si falla usa archivo."""
        raw = json.dumps(tokens)

        try:
            import keyring
            keyring.set_password(KEYRING_SERVICE, KEYRING_KEY, raw)
            return
        except Exception as e:
            logger.warning("Keyring no disponible (%s), guardando en archivo...", e)

        try:
            p = Path(self._TOKEN_FILE)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(raw, encoding="utf-8")
            try:
                p.chmod(stat.S_IRUSR | stat.S_IWUSR)
            except Exception:
                pass
            logger.info("Tokens guardados en: %s", p)
        except Exception as e:
            logger.error("No se pudo guardar tokens: %s", e)

    def _is_expired(self, tokens: dict) -> bool:
        return time.time() >= tokens.get("expires_at", 0) - 60

    # ── Flujo OAuth ───────────────────────────────────────────────────────────

    def _do_browser_auth(self) -> dict:
        """Ejecuta el flujo OAuth completo en browser (bloqueante)."""
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
            "codex_cli_simplified_flow": "true",
            "id_token_add_organizations": "true",
        }
        auth_url = f"{OPENAI_AUTH_URL}?{urlencode(auth_params)}"

        server, _thread = _run_local_server()

        print("\n🔐 Abriendo browser para autenticar con OpenAI Codex...")
        print(f"   Si no abre automaticamente, visita:\n   {auth_url}\n")
        webbrowser.open(auth_url)

        _CallbackHandler.done_event.wait(timeout=180)
        server.shutdown()

        if not _CallbackHandler.auth_code:
            raise RuntimeError("No se recibio el codigo de autorizacion OAuth. Tiempo agotado.")

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

    async def _fetch_account_id(self, access_token: str) -> str:
        """Obtiene el account_id de ChatGPT requerido para el header chatgpt-account-id."""
        try:
            resp = await self._client.get(
                CODEX_ACCOUNTS_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                },
            )
            if resp.is_success:
                data = resp.json()
                accounts = data.get("accounts", [])
                if accounts:
                    return accounts[0].get("account_id", "")
        except Exception as e:
            logger.warning("No se pudo obtener account_id: %s", e)
        return ""

    async def _refresh(self, refresh_token: str) -> dict:
        """Refresca el access_token."""
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

    async def get_token(self) -> dict:
        """Devuelve tokens válidos (con access_token y account_id), autenticando si es necesario."""
        if self._tokens is None:
            self._tokens = self._load_tokens()

        if self._tokens is None:
            loop = asyncio.get_event_loop()
            self._tokens = await loop.run_in_executor(None, self._do_browser_auth)
            # Fetch account_id si no está presente
            if not self._tokens.get("account_id"):
                self._tokens["account_id"] = await self._fetch_account_id(
                    self._tokens["access_token"]
                )
            self._save_tokens(self._tokens)

        elif self._is_expired(self._tokens):
            logger.info("Token expirado, refrescando...")
            refreshed = await self._refresh(self._tokens["refresh_token"])
            # Preservar account_id si el refresh no lo devuelve
            if not refreshed.get("account_id"):
                refreshed["account_id"] = self._tokens.get("account_id") or await self._fetch_account_id(
                    refreshed["access_token"]
                )
            self._tokens = refreshed
            self._save_tokens(self._tokens)

        return self._tokens

    async def logout(self) -> None:
        """Elimina los tokens guardados."""
        self._tokens = None
        try:
            import keyring
            keyring.delete_password(KEYRING_SERVICE, KEYRING_KEY)
        except Exception:
            pass
        try:
            Path(self._TOKEN_FILE).unlink(missing_ok=True)
        except Exception:
            pass
        print("Sesion cerrada.")

    # ── Llamadas LLM — Responses API con SSE ─────────────────────────────────

    @staticmethod
    def _to_fc_id(call_id: str) -> str:
        """Convierte IDs de tool calls al formato requerido por Codex (fc_xxx)."""
        if call_id.startswith("call_"):
            return "fc_" + call_id[5:]
        return call_id

    def _messages_to_codex_input(self, messages: list[Message]) -> tuple[str, list[dict]]:
        """
        Convierte la lista de mensajes al formato de la Responses API de Codex.
        Separa el system prompt (instructions) del resto (input items).
        Retorna (instructions, input_items).
        """
        instructions = ""
        input_items: list[dict] = []

        for m in messages:
            if isinstance(m, dict):
                role = m.get("role", "")
                content = m.get("content") or ""
                tool_calls = m.get("tool_calls")
                tool_call_id = m.get("tool_call_id")
                name = m.get("name")
            else:
                role = m.role
                content = m.content or ""
                tool_calls = m.__dict__.get("_raw_tool_calls")
                tool_call_id = m.tool_call_id
                name = m.name

            if role == "system":
                instructions = str(content)
                continue

            if role == "user":
                input_items.append({
                    "role": "user",
                    "content": [{"type": "input_text", "text": str(content)}],
                })

            elif role == "assistant":
                items: list[dict] = []
                if content:
                    items.append({
                        "role": "assistant",
                        "type": "message",
                        "content": [{"type": "output_text", "text": str(content)}],
                        "status": "completed",
                    })
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        fc_id = self._to_fc_id(tc["id"])
                        items.append({
                            "type": "function_call",
                            "id": fc_id,
                            "call_id": fc_id,
                            "name": fn.get("name", ""),
                            "arguments": fn.get("arguments", "{}"),
                        })
                input_items.extend(items)

            elif role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": self._to_fc_id(tool_call_id or ""),
                    "output": str(content),
                })

        return instructions, input_items

    async def _parse_sse_stream(self, response: httpx.Response) -> tuple[str, list[ToolCall]]:
        """Parsea la respuesta SSE de la Responses API de Codex."""
        text = ""
        tool_calls: list[ToolCall] = []
        buffer = ""

        async for chunk in response.aiter_bytes():
            buffer += chunk.decode("utf-8", errors="replace").replace("\r\n", "\n")

            while "\n\n" in buffer:
                event_str, buffer = buffer.split("\n\n", 1)
                data_lines = [
                    line[5:].strip()
                    for line in event_str.split("\n")
                    if line.startswith("data:")
                ]
                if not data_lines:
                    continue

                data = "\n".join(data_lines).strip()
                if data == "[DONE]":
                    continue

                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue

                etype = event.get("type", "")

                if etype == "response.output_text.delta":
                    text += event.get("delta", "")

                elif etype in ("response.completed", "response.done"):
                    resp_obj = event.get("response", {})
                    # Extraer texto del response completo
                    for item in resp_obj.get("output", []):
                        if item.get("type") == "message":
                            for part in item.get("content", []):
                                if part.get("type") == "output_text":
                                    text = part.get("text", text)
                    # Extraer tool calls del response completo
                    for item in resp_obj.get("output", []):
                        if item.get("type") == "function_call":
                            tc_name = item.get("name", "")
                            tc_args = item.get("arguments", "{}")
                            tc_id = item.get("id") or item.get("call_id") or tc_name
                            if tc_name:
                                try:
                                    args = json.loads(tc_args) if isinstance(tc_args, str) else tc_args
                                except json.JSONDecodeError:
                                    args = {}
                                tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=args))

                elif etype == "response.output_item.done":
                    item = event.get("item", {})
                    if item.get("type") == "function_call":
                        tc_name = item.get("name", "")
                        tc_args = item.get("arguments", "{}")
                        tc_id = item.get("call_id") or item.get("id") or tc_name
                        if tc_name:
                            try:
                                args = json.loads(tc_args) if isinstance(tc_args, str) else tc_args
                            except json.JSONDecodeError:
                                args = {}
                            tool_calls.append(ToolCall(id=tc_id, name=tc_name, arguments=args))

                elif etype == "error" or etype == "response.failed":
                    msg = event.get("message") or str(event)
                    raise RuntimeError(f"Codex stream error: {msg}")

        return text.strip(), tool_calls

    def _normalize_tools(self, tools: list[dict]) -> list[dict]:
        """Convierte schemas OpenAI-style al formato flat que usa la Responses API de Codex."""
        normalized = []
        for t in tools:
            fn = t.get("function", {})
            name = fn.get("name") or t.get("name", "")
            if not name:
                continue
            normalized.append({
                "type": "function",
                "name": name,
                "description": fn.get("description") or t.get("description", ""),
                "parameters": fn.get("parameters") or t.get("parameters") or {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            })
        return normalized

    async def complete(
        self,
        messages: list[Message],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        tokens = await self.get_token()
        access_token = tokens["access_token"]
        account_id = tokens.get("account_id", "")

        instructions, input_items = self._messages_to_codex_input(messages)

        body: dict = {
            "model": self._MODEL,
            "store": False,
            "stream": True,
            "instructions": instructions,
            "input": input_items,
            "text": {"verbosity": "medium"},
            "include": ["reasoning.encrypted_content"],
            "parallel_tool_calls": True,
        }

        if tools:
            body["tools"] = self._normalize_tools(tools)
            body["tool_choice"] = "auto"

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta": "responses=experimental",
            "originator": SYSTEM_NAME,
            "User-Agent": f"{SYSTEM_NAME} (python)",
        }

        async with self._client.stream(
            "POST", CODEX_URL, headers=headers, json=body
        ) as response:
            if response.status_code == 401 and tokens.get("refresh_token"):
                # Token expirado → refrescar y reintentar
                self._tokens = None
                tokens = await self.get_token()
                headers["Authorization"] = f"Bearer {tokens['access_token']}"
                headers["chatgpt-account-id"] = tokens.get("account_id", "")

            if not response.is_success:
                body_text = await response.aread()
                raise httpx.HTTPStatusError(
                    f"Codex API error ({response.status_code}): {body_text.decode()}",
                    request=response.request,
                    response=response,
                )

            text, tool_calls = await self._parse_sse_stream(response)

        finish_reason = "tool_calls" if tool_calls else "stop"
        return LLMResponse(
            content=text or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
        )

    async def embed(self, text: str) -> list[float]:
        """
        Codex OAuth no da acceso al endpoint de embeddings estándar.
        Raise NotImplementedError — el sistema debe usar embeddings locales.
        """
        raise NotImplementedError(
            "gpt-5.3-codex no provee API de embeddings. "
            "Usa embedding_provider=local en la config."
        )

    @property
    def model(self) -> str:
        return self._MODEL

    @property
    def supports_tools(self) -> bool:
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
