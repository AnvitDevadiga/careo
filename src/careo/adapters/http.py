"""HTTP adapter for trusted local services.

Uses only ``http.client`` (one fresh connection per request, no redirects, no
proxies, no cookies). Responses are size-capped, and an incomplete body is
reported as a failure, never as a short-but-valid response.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from careo.jsonvalue import JSONParseError, parse_strict

OK = "ok"
TIMEOUT = "timeout"
CONNECTION_ERROR = "connection_error"
PROTOCOL_ERROR = "protocol_error"
RESPONSE_TOO_LARGE = "response_too_large"

_LOOPBACK_NAMES = {"localhost"}


class EndpointError(ValueError):
    pass


@dataclass(frozen=True)
class Endpoint:
    scheme: str
    host: str
    port: int
    base_path: str

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{host}:{self.port}{self.base_path}"


def is_loopback(host: str) -> bool:
    if host.lower() in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_endpoint(url: str, *, allow_remote: bool = False) -> Endpoint:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise EndpointError(f"base_url {url!r} must use http or https")
    if not parts.hostname:
        raise EndpointError(f"base_url {url!r} has no host")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise EndpointError(f"base_url {url!r} must not contain credentials, query or fragment")
    if not allow_remote and not is_loopback(parts.hostname):
        raise EndpointError(
            f"base_url host {parts.hostname!r} is not a loopback address. Careo only targets "
            "local services by default; set allow_remote: true for a trusted non-production host"
        )
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise EndpointError(f"base_url {url!r}: {exc}") from None
    return Endpoint(parts.scheme, parts.hostname, port, parts.path.rstrip("/"))


@dataclass
class HttpResponse:
    status: str  # ok | timeout | connection_error | protocol_error | response_too_large
    code: int | None = None
    headers: dict[str, str] = field(default_factory=dict)  # lower-cased names
    body_text: str = ""
    body_json: Any = None
    body_is_json: bool = False
    json_error: str = ""
    error: str = ""
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "status": self.status,
            "duration_seconds": round(self.duration_seconds, 6),
        }
        if self.status == OK:
            out["code"] = self.code
            out["headers"] = self.headers
            if self.body_is_json:
                out["body"] = self.body_json
            else:
                out["body_text"] = self.body_text[:65536]
                if self.json_error:
                    out["json_error"] = self.json_error
        else:
            out["error"] = self.error
        return out


def build_path(endpoint: Endpoint, template: str, params: dict[str, Any]) -> str:
    path = template
    for name, value in params.items():
        token = "{" + name + "}"
        if token not in path:
            raise ValueError(f"path parameter {name!r} not present in {template!r}")
        path = path.replace(token, quote(str(value), safe=""))
    if "{" in path:
        raise ValueError(f"unfilled path parameters in {template!r}")
    return endpoint.base_path + path


def request(
    endpoint: Endpoint,
    method: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    body: Any = None,
    send_body: bool = False,
    timeout_seconds: float,
    max_response_bytes: int,
) -> HttpResponse:
    if query:
        path += "?" + "&".join(
            f"{quote(str(k), safe='')}={quote(str(v), safe='')}" for k, v in query.items()
        )
    headers = {"Accept": "application/json", "Connection": "close"}
    data: bytes | None = None
    if send_body:
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn_cls = (
        http.client.HTTPSConnection if endpoint.scheme == "https" else http.client.HTTPConnection
    )
    start = time.monotonic()
    conn = conn_cls(endpoint.host, endpoint.port, timeout=timeout_seconds)
    try:
        conn.request(method, path, body=data, headers=headers)
        resp = conn.getresponse()
        raw = resp.read(max_response_bytes + 1)
        if len(raw) > max_response_bytes:
            return HttpResponse(
                RESPONSE_TOO_LARGE,
                error=f"response body exceeded {max_response_bytes} bytes",
                duration_seconds=time.monotonic() - start,
            )
        declared = resp.getheader("Content-Length")
        if declared is not None and declared.isdigit() and int(declared) != len(raw):
            return HttpResponse(
                PROTOCOL_ERROR,
                error=f"partial response: Content-Length {declared}, received {len(raw)} bytes",
                duration_seconds=time.monotonic() - start,
            )
        result = HttpResponse(
            OK,
            code=resp.status,
            headers={k.lower(): v for k, v in resp.getheaders()},
            duration_seconds=time.monotonic() - start,
        )
    except TimeoutError:
        return HttpResponse(
            TIMEOUT,
            error=f"no response within {timeout_seconds}s",
            duration_seconds=time.monotonic() - start,
        )
    except (ConnectionError, OSError) as exc:
        return HttpResponse(
            CONNECTION_ERROR,
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - start,
        )
    except http.client.HTTPException as exc:
        # IncompleteRead (partial body), BadStatusLine, RemoteDisconnected, ...
        return HttpResponse(
            PROTOCOL_ERROR,
            error=f"{type(exc).__name__}: {exc}",
            duration_seconds=time.monotonic() - start,
        )
    finally:
        conn.close()

    result.body_text = raw.decode("utf-8", "replace")
    if raw.strip():
        try:
            result.body_json = parse_strict(raw)
            result.body_is_json = True
        except JSONParseError as exc:
            result.json_error = str(exc)
    return result
