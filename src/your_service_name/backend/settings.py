# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Typed settings: the one place the backend reads its environment.

Responsibilities
----------------
- Declare every environment variable the Python backend reads, with a type, the default
  it always had and a description (``wiki/environment-variables.md`` is generated from
  these fields by ``scripts/gen_env_docs.py``).
- Validate them once, at startup, and fail loudly: an invalid environment raises a single
  ``pydantic.ValidationError`` naming each invalid variable.

Design
------
- Field names ARE the environment variable names (case-sensitive), so nothing changes for
  adopters.
- No import-time global: ``create_app`` builds (or receives) one ``Settings`` and stores it
  on ``app.state.settings``; everything else is handed the values or reads them from there.
- Secret-bearing values are ``SecretStr`` and ``hide_input_in_errors`` is on, so a bad value
  never appears in an error or in ``repr(settings)``.
"""

from __future__ import annotations

import ipaddress
import logging
import re
from typing import Annotated, Final, Literal
from urllib.parse import parse_qs, urlsplit

from limits import parse as parse_rate_limit
from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from your_service_name.utils.versioning_api import (
    DEFAULT_API_VERSION,
    MSG_ERROR_UNSUPPORTED_VERSION,
    SUPPORTED_VERSIONS,
)

logger = logging.getLogger(__name__)

# Default SQLite file, relative to the project root. Single source shared by the engine,
# the migration runner and the seed script (via db/_constants.py). Overridable via SQLITE_PATH.
DEFAULT_SQLITE_REL_PATH: Final[str] = "data/items.db"
DEFAULT_RATE_LIMIT: Final[str] = "100/minute"
DEFAULT_CSP_REPORT_RATE_LIMIT: Final[str] = "20/minute"
DEFAULT_CSP_REPORT_MAX_BYTES: Final[int] = 16 * 1024
# The package's own name: the default tracing service name (the adapt script renames the package).
DEFAULT_SERVICE_NAME: Final[str] = __name__.split(".", maxsplit=1)[0]
# 10 MiB. The backend owns the body limit; the chart sets no edge annotation for it.
DEFAULT_MAX_REQUEST_BODY_BYTES: Final[int] = 10 * 1024 * 1024

# HMAC family only: JWT alg name -> hashlib digest name (a JWT "HS256" is hashlib "sha256").
# RSA/ECDSA/`none` are deliberately rejected to avoid the classic "alg confusion" attack
# (an attacker swapping alg to bypass the key).
JWT_HASHLIB: Final[dict[str, str]] = {"HS256": "sha256", "HS384": "sha384", "HS512": "sha512"}
ALLOWED_JWT_ALGS: Final[frozenset[str]] = frozenset(JWT_HASHLIB)

MEMORY_STORAGE_URL: Final[str] = "memory://"
RATE_LIMIT_STORAGE_SCHEMES: Final[frozenset[str]] = frozenset(
    {"memory", "redis", "rediss", "redis+unix"}
)

PRODUCTION_ENVS: Final[frozenset[str]] = frozenset({"production", "prod"})

# A bare origin: scheme://host[:port], no path, query or trailing slash.
_BARE_ORIGIN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#\s]+$")

_LOCAL_HTTP_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1"})


def jwks_url_problem(url: str, *, production: bool) -> str | None:
    """Why ``url`` may not serve signing keys, or ``None`` when it may.

    ``https`` always; plain ``http`` only for ``localhost``/``127.0.0.1`` and never in production.
    The reason never echoes the URL (it may carry a query).
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return "is not a valid URL"
    if parts.scheme == "https" and host:
        return None
    if parts.scheme == "http" and host in _LOCAL_HTTP_HOSTS:
        return "must be https in production" if production else None
    return "must be an https URL (http is allowed only for localhost outside production)"


AuthMode = Literal["none", "gateway", "bearer", "oidc", "cloudflare-access"]

# Asymmetric JWT algorithms an OIDC allowlist may name. `none` and every HS* are deliberately
# absent: a symmetric alg against a public JWKS is the classic algorithm-confusion attack.
OIDC_ALLOWED_ALGS: Final[frozenset[str]] = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}
)
MAX_CLOCK_SKEW_SECONDS: Final[int] = 300

# `<team>.cloudflareaccess.com`: one lowercase label (no leading or trailing hyphen) and the
# Cloudflare domain, nothing else. The issuer and the key URL derive from it, so a lookalike
# host must not pass.
_CF_TEAM_DOMAIN: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com$"
)

_BLANK_IS_DEFAULT: Final[tuple[str, ...]] = (
    "ENV",
    "AUTH_USER_ID_HEADER",
    "AUTH_ROLES_HEADER",
    "AUTH_JWT_USER_ID_CLAIM",
    "OIDC_ISSUER",
    "OIDC_AUDIENCE",
    "OIDC_JWKS_URL",
    "OIDC_ALGORITHMS",
    "OIDC_CLOCK_SKEW_SECONDS",
    "OIDC_USER_ID_CLAIM",
    "OIDC_ROLES_CLAIM",
    "CF_ACCESS_TEAM_DOMAIN",
    "CF_ACCESS_AUD",
    "JWKS_CACHE_TTL_SECONDS",
    "JWKS_MIN_REFETCH_SECONDS",
    "JWKS_MAX_STALE_SECONDS",
    "SQLITE_PATH",
    "LOG_LEVEL",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "DB_SSLMODE",
    "DB_SSLROOTCERT",
    "LOG_REQUESTS",
    "CORS_ALLOW_CREDENTIALS",
    "ALLOW_ANONYMOUS",
    "ALLOW_PRODUCTION_DOCS",
    "TRUST_CF_CONNECTING_IP",
    "MAX_REQUEST_BODY_BYTES",
    "TRACING_ENABLED",
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_HEADERS",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
    "OTEL_SERVICE_NAME",
    "TRACING_SAMPLE_RATIO",
    "RATE_LIMIT_STORAGE",
    "RATE_LIMIT_STORAGE_TIMEOUT_SECONDS",
    "CSP_REPORT_ENABLED",
    "CSP_REPORT_MAX_BYTES",
    "CSP_REPORT_RATE_LIMIT",
    "HSTS_ENABLED",
)


def parse_header_list(raw: str) -> dict[str, str]:
    """Parse ``name=value,name2=value2`` (the OTLP headers format); ``ValueError`` when malformed.

    The message never carries the text: a header value is a credential.
    """
    headers: dict[str, str] = {}
    for item in raw.split(","):
        if not item.strip():
            continue
        name, sep, value = item.partition("=")
        if not sep or not name.strip():
            msg = "expected comma-separated name=value pairs"
            raise ValueError(msg)
        headers[name.strip()] = value.strip()
    return headers


def _is_local_host(host: str) -> bool:
    """A unix socket path, ``localhost`` or a loopback address."""
    host = host.strip("[]")
    if host.startswith("/") or host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def rate_limit_storage_problem(url: str) -> str | None:
    """Why ``url`` is no usable ``RATE_LIMIT_STORAGE``, or ``None``; the reason never echoes it.

    ``limits`` needs ``host:port`` for ``redis``/``rediss`` and refuses anything else with an
    error that names the whole URL (password included), so everything it needs is checked here.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        return "is not a valid URL (check the host and that the port is a number up to 65535)"
    if parts.scheme not in RATE_LIMIT_STORAGE_SCHEMES:
        return "must start with memory://, redis://, rediss:// or redis+unix://"
    if parts.scheme in ("redis", "rediss"):
        if not host:
            return "must name the Redis host"
        if port is None:
            return "must include the port, for example redis://host:6379"
    if parts.scheme == "redis+unix" and (not parts.path or host):
        return "must be redis+unix:///path/to/socket (a socket path and no host)"
    return None


def _unverified_redis_tls(url: str) -> bool:
    """True when a ``rediss://`` query switches certificate or hostname verification off."""
    query = parse_qs(urlsplit(url).query)
    certs = [v.lower() for v in query.get("ssl_cert_reqs", [])]
    hostname = [v.lower() for v in query.get("ssl_check_hostname", [])]
    return any(v != "required" for v in certs) or any(
        v in ("false", "0", "no", "off") for v in hostname
    )


def _is_loopback_storage(url: str) -> bool:
    """True when a plaintext ``redis://`` URL names a loopback host (fails closed otherwise)."""
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    return bool(host) and _is_local_host(host or "")


def _is_loopback_database(url: str) -> bool:
    """True when every host the URL names is local: nothing leaves the Pod.

    Fails closed: a URL that names no host at all counts as remote, because libpq and asyncpg
    would then fall back to ``PGHOST`` or defaults that Settings cannot see. The asyncpg
    ``?host=`` query form (one or several entries) is read too.
    """
    if not url.strip() or url.strip().lower().startswith("sqlite"):
        return True  # refused on its own; no second message
    try:
        parsed = make_url(url)
    except ArgumentError:
        return False
    query_host = parsed.query.get("host", ())
    hosts = [parsed.host] if parsed.host else []
    hosts += [query_host] if isinstance(query_host, str) else list(query_host)
    return bool(hosts) and all(_is_local_host(h) for h in hosts)


def _tls_in_url(url: str) -> bool:
    """True when a database URL carries its own ``ssl`` or ``sslmode`` query option."""
    try:
        query = make_url(url).query
    except ArgumentError:
        return False
    return "ssl" in query or "sslmode" in query


def normalize_api_version(value: object) -> object:
    """Strip ``API_VERSION`` (empty means the default) and check it is a supported version."""
    if not isinstance(value, str):
        return value
    version = value.strip() or DEFAULT_API_VERSION
    if version not in SUPPORTED_VERSIONS:
        msg = "API_VERSION: " + MSG_ERROR_UNSUPPORTED_VERSION.format(version=version)
        raise ValueError(msg)
    return version


class _EnvName(BaseSettings):
    """Only ENV, read the way ``Settings`` reads it (the environment, then ``.env``)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=True, extra="ignore"
    )
    ENV: str = "dev"


class Settings(BaseSettings):
    """Every environment variable the backend reads, validated once at startup."""

    model_config = SettingsConfigDict(
        # .env in the working directory is a source too; real environment variables win over it.
        # Shell/compose-only names in the same file are ignored (extra="ignore").
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        hide_input_in_errors=True,
    )

    # --- General --------------------------------------------------------------
    ENV: str = Field(
        default="dev",
        description="Deployment environment. `production` (or `prod`) turns on the production "
        "checks below; any other value leaves them off.",
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Root log level (name or number); an unknown value falls back to INFO.",
    )
    LOG_FORMAT: str = Field(
        default="",
        description="`json` emits structured JSON logs; they are also on when ENV is production.",
    )
    LOG_REQUESTS: bool = Field(
        default=False,
        description=(
            "Log every HTTP request (method, path, status, duration, client IP; never a query "
            "string, header, cookie or body). Off here for local runs; the Helm chart turns it on."
        ),
    )
    API_VERSION: str = Field(
        default=DEFAULT_API_VERSION,
        description="Active API version; must be one of the supported versions.",
    )
    API_TITLE: str = Field(default="Backend Title", description="OpenAPI document title.")
    API_DESCRIPTION: str = Field(
        default="Minimal FastAPI backend",
        description="OpenAPI document description.",
    )
    API_DOCS_URL: str | None = Field(
        default=None,
        description="Path of the Swagger UI. Unset means `/docs` (off in production); "
        "an empty value turns it off.",
        json_schema_extra={
            "production": "Off unless ALLOW_PRODUCTION_DOCS=true; setting it to a path "
            "without that flag is refused."
        },
    )
    OPENAPI_URL: str | None = Field(
        default=None,
        description="Path of the OpenAPI JSON. Unset means `/openapi.json` (off in production); "
        "an empty value turns it off.",
        json_schema_extra={
            "production": "Off unless ALLOW_PRODUCTION_DOCS=true; setting it to a path "
            "without that flag is refused."
        },
    )
    ALLOW_PRODUCTION_DOCS: bool = Field(
        default=False,
        description="Production only: allow the Swagger UI and the OpenAPI JSON to be served.",
    )

    # --- CORS -----------------------------------------------------------------
    CORS_ORIGINS: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="Comma-separated allowed origins. Empty means same-origin only.",
        json_schema_extra={
            "production": "Each origin must be a bare `scheme://host[:port]` (no path, no "
            "trailing slash). With CORS_ALLOW_CREDENTIALS=true it must be non-empty and "
            "must not contain `*`."
        },
    )
    CORS_ALLOW_CREDENTIALS: bool = Field(
        default=False,
        description="Allow credentialed cross-origin requests. Outside production a `*` "
        "origin is dropped (with a warning) when this is on.",
    )
    CORS_ALLOW_METHODS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        description="Comma-separated allowed methods.",
    )
    CORS_ALLOW_HEADERS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Authorization", "Content-Type", "X-Requested-With"],
        description="Comma-separated allowed request headers.",
    )

    # --- Rate limiting --------------------------------------------------------
    RATE_LIMIT_DEFAULT: str = Field(
        default=DEFAULT_RATE_LIMIT,
        description="Per-IP rate limit in `limits` format, for example `100/minute`.",
    )
    RATE_LIMIT_STORAGE: SecretStr = Field(
        default=SecretStr(MEMORY_STORAGE_URL),
        description="Where the rate-limit counters live. The default `memory://` counts per "
        "replica (the limit is per Pod); Redis, shared by every replica (`redis://`, "
        "`rediss://` or `redis+unix://`; the URL can hold a password, so it is a secret). When "
        "Redis is down or slow the request is allowed (fail open).",
        json_schema_extra={
            "production": "Plaintext `redis://` is refused unless every host is loopback; use "
            "`rediss://`, a unix socket or `memory://`."
        },
    )
    RATE_LIMIT_STORAGE_TIMEOUT_SECONDS: float = Field(
        default=0.2,
        gt=0,
        le=10,
        description="Redis connect and socket timeout (and the cap on one limiter call), in "
        "seconds. Short on purpose: it is added to a request while Redis is unreachable.",
    )

    # --- HSTS ------------------------------------------------------------------
    HSTS_ENABLED: bool = Field(
        default=True,
        description="Send `Strict-Transport-Security` on https responses. Turn it off where the "
        "edge sets HSTS, so it has one source; the chart's `edge.hsts` sets it per edge mode.",
    )

    # --- CSP reports (opt-in) -------------------------------------------------
    CSP_REPORT_ENABLED: bool = Field(
        default=False,
        description="Serve `POST /api/v1/csp-report`, which logs the browser's Content "
        "Security Policy violation reports (a sanitised warning each). Off, the path is a 404. "
        "The chart's `cspReporting.enabled` also adds the reporting directives to the "
        "frontend's CSP.",
    )
    CSP_REPORT_MAX_BYTES: int = Field(
        default=DEFAULT_CSP_REPORT_MAX_BYTES,
        gt=0,
        le=1024 * 1024,
        description="Largest accepted CSP report body in bytes (checked while reading); more "
        "is a 413.",
    )
    CSP_REPORT_RATE_LIMIT: str = Field(
        default=DEFAULT_CSP_REPORT_RATE_LIMIT,
        description="Per-client limit for the CSP report endpoint, in `limits` format. It has "
        "its own counter, apart from RATE_LIMIT_DEFAULT.",
    )

    # --- Tracing (opt-in) -----------------------------------------------------
    TRACING_ENABLED: bool = Field(
        default=False,
        description="Export OpenTelemetry traces over OTLP/HTTP (server spans for the API, "
        "database spans for statement text). Off, nothing of OpenTelemetry beyond its small API "
        "package is loaded. Needs OTEL_EXPORTER_OTLP_ENDPOINT.",
    )
    OTEL_EXPORTER_OTLP_ENDPOINT: str | None = Field(
        default=None,
        description="Tracing: the collector's base URL (`http://host:4318`); `/v1/traces` is "
        "appended. Required when TRACING_ENABLED=true.",
    )
    OTEL_EXPORTER_OTLP_HEADERS: SecretStr | None = Field(
        default=None,
        description="Tracing: headers for the collector, `name=value,name2=value2` (for "
        "example its auth token). A secret.",
    )
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: str | None = Field(
        default=None,
        description="Refused when set: the SDK would take it over OTEL_EXPORTER_OTLP_ENDPOINT. "
        "Use OTEL_EXPORTER_OTLP_ENDPOINT.",
    )
    OTEL_EXPORTER_OTLP_TRACES_HEADERS: SecretStr | None = Field(
        default=None,
        description="Refused when set: the SDK would take it over OTEL_EXPORTER_OTLP_HEADERS and "
        "skip the production check on cleartext headers. Use OTEL_EXPORTER_OTLP_HEADERS.",
    )
    OTEL_SERVICE_NAME: str = Field(
        default=DEFAULT_SERVICE_NAME,
        description="Tracing: the `service.name` resource attribute.",
    )
    TRACING_SAMPLE_RATIO: float = Field(
        default=1.0,
        ge=0,
        le=1,
        description="Tracing: the share of new traces recorded (0 to 1). A caller's sampled "
        "`traceparent` is always honoured, whatever this says.",
    )

    # --- Request limits -------------------------------------------------------
    MAX_REQUEST_BODY_BYTES: int = Field(
        default=DEFAULT_MAX_REQUEST_BODY_BYTES,
        gt=0,
        description="Largest accepted request body in bytes; more is a 413. The default is 10 MiB; "
        "the chart sets no edge limit, so this is the one place the limit lives.",
    )
    ALLOWED_HOSTS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        description="Comma-separated `Host` values the service answers (`*.example.com` matches "
        "subdomains, `*` anything; an entry never carries a port). Anything else is a 400; the "
        "probe paths are exempt. The chart sets it from the edge hosts and the "
        "backend Service names.",
        json_schema_extra={"production": "Must be set and must not contain `*`."},
    )

    # --- Client IP ------------------------------------------------------------
    TRUSTED_PROXY_CIDRS: Annotated[
        list[ipaddress.IPv4Network | ipaddress.IPv6Network], NoDecode
    ] = Field(
        default_factory=list,
        description="Comma-separated IPv4/IPv6 CIDRs of the proxies (the gateway and, in "
        "the Tunnel edge mode, cloudflared) whose forwarding headers are trusted. Empty "
        "trusts nothing: the TCP peer is the client. An invalid entry stops startup, and so "
        "does one with host bits set (`10.1.2.3/8`; write `10.0.0.0/8`).",
    )
    TRUST_CF_CONNECTING_IP: bool = Field(
        default=False,
        description="Trust `CF-Connecting-IP` from a peer in TRUSTED_PROXY_CIDRS. Set it only in "
        "the Cloudflare Tunnel edge mode: Cloudflare sends the header on traffic from its edge, so "
        "trust it only when cloudflared is the only way in.",
        json_schema_extra={"production": "Refused when TRUSTED_PROXY_CIDRS is empty."},
    )

    # --- Auth -----------------------------------------------------------------
    AUTH_MODE: AuthMode = Field(
        default="none",
        description="`oidc` (JWT from an OpenID Connect provider, checked against its JWKS) and "
        "`cloudflare-access` (the Cloudflare Access application token) are the production "
        "modes. `gateway` (trusted identity header) and `bearer` (HMAC-signed JWT, "
        "shared secret) are reference modes; `none` is anonymous.",
        json_schema_extra={"production": "`none` is refused unless ALLOW_ANONYMOUS=true."},
    )
    ALLOW_ANONYMOUS: bool = Field(
        default=False,
        description="Production only: explicitly accept AUTH_MODE=none (open endpoints).",
    )
    AUTH_PROXY_CIDRS: Annotated[list[ipaddress.IPv4Network | ipaddress.IPv6Network], NoDecode] = (
        Field(
            default_factory=list,
            description="Gateway mode: comma-separated IPv4/IPv6 CIDRs of the auth proxy that "
            "authenticates and injects the identity headers. They count only when the direct TCP "
            "peer is inside; any other peer gets a 401, and empty trusts nobody. Separate from "
            "TRUSTED_PROXY_CIDRS, and validated the same way.",
            json_schema_extra={"production": "Refused when empty and AUTH_MODE=gateway."},
        )
    )
    AUTH_USER_ID_HEADER: str = Field(
        default="x-authenticated-user-id",
        description="Gateway mode: header carrying the authenticated user id.",
    )
    AUTH_ROLES_HEADER: str = Field(
        default="x-authenticated-user-roles",
        description="Gateway mode: header carrying comma-separated roles.",
    )
    AUTH_JWT_SECRET: SecretStr | None = Field(
        default=None,
        description="Bearer mode: shared HMAC secret. Required when AUTH_MODE=bearer.",
    )
    AUTH_JWT_ALGORITHM: str = Field(
        default="HS256",
        description="Bearer mode: HMAC algorithm (`HS256`, `HS384` or `HS512`).",
    )
    AUTH_JWT_USER_ID_CLAIM: str = Field(
        default="sub",
        description="Bearer mode: token claim holding the user id.",
    )

    OIDC_ISSUER: str | None = Field(
        default=None,
        description="OIDC mode: the issuer; a token's `iss` must equal it exactly. Required "
        "when AUTH_MODE=oidc.",
    )
    OIDC_AUDIENCE: Annotated[list[str], NoDecode] = Field(
        default_factory=list,
        description="OIDC mode: comma-separated accepted audiences; a token's `aud` must "
        "contain one. Required when AUTH_MODE=oidc.",
    )
    OIDC_JWKS_URL: str | None = Field(
        default=None,
        description="OIDC mode: the provider's JWKS URL. Unset takes `jwks_uri` once from "
        "`${OIDC_ISSUER}/.well-known/openid-configuration` (whose `issuer` must equal "
        "OIDC_ISSUER) and caches it.",
        json_schema_extra={"production": "Must be https (so must OIDC_ISSUER when this is unset)."},
    )
    OIDC_ALGORITHMS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["RS256", "ES256"],
        description="OIDC mode: comma-separated allowlist of token algorithms (RS*, PS*, ES*). "
        "`none` and every HS* are refused at startup.",
    )
    OIDC_CLOCK_SKEW_SECONDS: int = Field(
        default=60,
        ge=0,
        le=MAX_CLOCK_SKEW_SECONDS,
        description="OIDC mode: clock-skew tolerance for `exp`, `nbf` and `iat` (0 to 300).",
    )
    OIDC_USER_ID_CLAIM: str = Field(
        default="sub", description="OIDC mode: token claim holding the user id."
    )
    OIDC_ROLES_CLAIM: str | None = Field(
        default=None,
        description="OIDC mode: token claim holding the roles (a list of strings or a "
        "space-separated string). Unset means no roles.",
    )
    CF_ACCESS_TEAM_DOMAIN: str | None = Field(
        default=None,
        description="Cloudflare Access mode: the team domain, exactly "
        "`<team>.cloudflareaccess.com` (lowercased; no scheme, port or path). The issuer and the "
        "keys (`/cdn-cgi/access/certs`) are derived from it. Required when "
        "AUTH_MODE=cloudflare-access.",
    )
    CF_ACCESS_AUD: str | None = Field(
        default=None,
        description="Cloudflare Access mode: the application's Audience (AUD) tag; the token's "
        "`aud` must contain it. Required when AUTH_MODE=cloudflare-access.",
    )
    JWKS_CACHE_TTL_SECONDS: int = Field(
        default=3600,
        gt=0,
        description="OIDC and Cloudflare Access modes: how long fetched signing keys are used.",
    )
    JWKS_MIN_REFETCH_SECONDS: int = Field(
        default=30,
        ge=0,
        description="OIDC and Cloudflare Access modes: at most one JWKS refetch per this "
        "interval across all requests (an unknown `kid` triggers one), so random `kid`s "
        "cannot hammer the provider.",
    )
    JWKS_MAX_STALE_SECONDS: int = Field(
        default=86400,
        gt=0,
        description="OIDC and Cloudflare Access modes: when a refresh fails, keep serving the "
        "last good keys for up to this long; after that requests get a 503.",
    )

    # --- Database -------------------------------------------------------------
    DATABASE_URL: SecretStr | None = Field(
        default=None,
        description="Async SQLAlchemy database URL. Unset falls back to SQLite (SQLITE_PATH).",
        json_schema_extra={"production": "Required, and not SQLite."},
    )
    TEST_DATABASE_URL: SecretStr | None = Field(
        default=None,
        description="Test-only: database URL that takes precedence over DATABASE_URL.",
        json_schema_extra={
            "production": "Refused when set: it is test-only and would take precedence."
        },
    )
    SQLITE_PATH: str = Field(
        default=DEFAULT_SQLITE_REL_PATH,
        description="SQLite fallback file, relative to the project root.",
        json_schema_extra={"production": "Ignored: SQLite is refused."},
    )
    DB_POOL_SIZE: int = Field(
        default=10,
        description="PostgreSQL connection pool size; a non-positive or invalid value "
        "falls back to the default.",
    )
    DB_MAX_OVERFLOW: int = Field(
        default=20,
        description="PostgreSQL pool overflow; a non-positive or invalid value falls back "
        "to the default.",
    )

    DB_CONNECT_TIMEOUT_SECONDS: int = Field(
        default=10,
        description="PostgreSQL connect timeout in seconds (asyncpg `timeout`); a non-positive "
        "or invalid value falls back to the default.",
    )
    DB_STATEMENT_TIMEOUT_MS: int = Field(
        default=30000,
        description="PostgreSQL `statement_timeout` in milliseconds, for the app connections "
        "only (migrations set none); a non-positive or invalid value falls back to the default.",
    )
    DB_IDLE_IN_TRANSACTION_TIMEOUT_MS: int = Field(
        default=60000,
        description="PostgreSQL `idle_in_transaction_session_timeout` in milliseconds, for the "
        "app connections only; a non-positive or invalid value falls back to the default.",
    )
    DB_MIGRATION_LOCK_TIMEOUT_MS: int = Field(
        default=10000,
        description="PostgreSQL `lock_timeout` in milliseconds for the migration run, so a "
        "migration waiting on a busy table fails instead of blocking traffic; a non-positive "
        "or invalid value falls back to the default.",
    )
    MIGRATION_TEST_DELAY_SECONDS: int = Field(
        default=0,
        ge=0,
        description="Test-only: the migration runner sleeps this long before it acts (a slow "
        "migration, for the kind migration test).",
        json_schema_extra={"production": "Refused when set: it is test-only."},
    )
    MIGRATION_TEST_FAIL: bool = Field(
        default=False,
        description="Test-only: the migration runner fails before it acts (a failed migration, "
        "for the kind migration test).",
        json_schema_extra={"production": "Refused when set: it is test-only."},
    )
    DB_SSLMODE: Literal["disable", "require", "verify-ca", "verify-full"] = Field(
        default="disable",
        description="PostgreSQL TLS: `disable`, `require` (encrypt, no verification), "
        "`verify-ca` (verify the CA, not the hostname) or `verify-full` (CA and hostname).",
        json_schema_extra={
            "production": "`disable` and `require` are refused unless the database host is "
            "loopback (a Cloud SQL Auth Proxy sidecar)."
        },
    )
    DB_SSLROOTCERT: str | None = Field(
        default=None,
        description="Path to the CA file that verifies the server for `verify-ca` and "
        "`verify-full`; unset uses the system trust store.",
    )

    @classmethod
    def for_migration(cls) -> Settings:
        """Settings for the migration Job: the production database checks, and no other.

        The Job gets ``ENV`` and the database settings only, so the full production check set
        (auth, hosts, ...) cannot pass there. ``ENV`` is overridden for the load, and when the real
        ``ENV`` is production the database checks (`database_problems`) run alone, before any
        connection.
        """
        settings = cls(ENV="development")
        if _EnvName().ENV.strip().lower() in PRODUCTION_ENVS:
            problems = settings.database_problems()
            if problems:
                lines = "\n".join(f"  - {p}" for p in problems)
                msg = f"invalid configuration:\n{lines}"
                raise ValueError(msg)
        return settings

    @property
    def is_production(self) -> bool:
        """True when ``ENV`` is ``production`` (or ``prod``), case-insensitive."""
        return self.ENV.strip().lower() in PRODUCTION_ENVS

    @property
    def docs_url(self) -> str | None:
        """Swagger UI path, or ``None`` when it is off (the default in production)."""
        default = "/docs" if self._docs_on_by_default else ""
        return (self.API_DOCS_URL if self.API_DOCS_URL is not None else default) or None

    @property
    def openapi_url(self) -> str | None:
        """OpenAPI JSON path, or ``None`` when it is off (the default in production)."""
        default = "/openapi.json" if self._docs_on_by_default else ""
        return (self.OPENAPI_URL if self.OPENAPI_URL is not None else default) or None

    @property
    def _docs_on_by_default(self) -> bool:
        return not self.is_production or self.ALLOW_PRODUCTION_DOCS

    @property
    def database_url(self) -> str | None:
        """The URL the engine and the migrations use: TEST_DATABASE_URL, else DATABASE_URL."""
        for secret in (self.TEST_DATABASE_URL, self.DATABASE_URL):
            if secret is not None and secret.get_secret_value():
                return secret.get_secret_value()
        return None

    # --- Validators: parsing --------------------------------------------------

    @staticmethod
    def _is_blank(value: object) -> bool:
        return isinstance(value, str) and not value.strip()

    @classmethod
    def _default_of(cls, name: str | None) -> object:
        assert name is not None  # noqa: S101 — pydantic always supplies the field name
        return cls.model_fields[name].get_default(call_default_factory=True)

    @field_validator(*_BLANK_IS_DEFAULT, mode="before")
    @classmethod
    def _blank_is_default(cls, value: object, info: ValidationInfo) -> object:
        """An empty variable means "not set" (the old reads used ``or default``)."""
        return cls._default_of(info.field_name) if cls._is_blank(value) else value

    @field_validator(
        "CORS_ORIGINS", "CORS_ALLOW_METHODS", "CORS_ALLOW_HEADERS", "ALLOWED_HOSTS", mode="before"
    )
    @classmethod
    def _csv(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        items = [item.strip() for item in value.split(",") if item.strip()]
        return items or cls._default_of(info.field_name)

    @field_validator("ALLOWED_HOSTS")
    @classmethod
    def _hosts_without_ports(cls, value: list[str]) -> list[str]:
        """An entry names a host only: the request's port is dropped before matching."""
        for entry in value:
            host = entry.rsplit("]", 1)[-1] if entry.startswith("[") else entry
            if ":" in host:
                msg = (
                    "ALLOWED_HOSTS: an entry carries a port; ports are matched separately (the "
                    "request's port is ignored), so remove the port from the entry"
                )
                raise ValueError(msg)
        return value

    @field_validator("TRUSTED_PROXY_CIDRS", "AUTH_PROXY_CIDRS", mode="before")
    @classmethod
    def _cidrs(cls, value: object, info: ValidationInfo) -> object:
        """Comma-separated CIDRs (a bare address is a /32 or /128); an invalid one is an error."""
        if not isinstance(value, str):
            return value
        try:
            return [ipaddress.ip_network(i.strip()) for i in value.split(",") if i.strip()]
        except ValueError as exc:
            msg = (
                f"{info.field_name} must be comma-separated IPv4/IPv6 CIDRs in network form: "
                "an entry with host bits set (10.1.2.3/8) is refused, write 10.0.0.0/8"
            )
            raise ValueError(msg) from exc

    @field_validator(
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "DB_CONNECT_TIMEOUT_SECONDS",
        "DB_STATEMENT_TIMEOUT_MS",
        "DB_IDLE_IN_TRANSACTION_TIMEOUT_MS",
        "DB_MIGRATION_LOCK_TIMEOUT_MS",
        mode="before",
    )
    @classmethod
    def _positive_int_or_default(cls, value: object, info: ValidationInfo) -> object:
        """Unset, garbage or non-positive falls back to the default (with a warning)."""
        default = cls._default_of(info.field_name)
        try:
            number = int(str(value))
        except ValueError:
            number = 0
        if number > 0:
            return number
        if not cls._is_blank(value):
            logger.warning("Invalid %s; using %s", info.field_name, default)
        return default

    @field_validator("DB_SSLMODE", mode="before")
    @classmethod
    def _normalise_sslmode(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("AUTH_MODE", mode="before")
    @classmethod
    def _normalise_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "none"
        return value

    @field_validator("OIDC_AUDIENCE", "OIDC_ALGORITHMS", mode="before")
    @classmethod
    def _csv_values(cls, value: object, info: ValidationInfo) -> object:
        if not isinstance(value, str):
            return value
        items = [item.strip() for item in value.split(",") if item.strip()]
        if info.field_name == "OIDC_ALGORITHMS":
            items = [item.upper() for item in items]
        return items or cls._default_of(info.field_name)

    @field_validator("CF_ACCESS_TEAM_DOMAIN", mode="before")
    @classmethod
    def _lower_team_domain(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("AUTH_JWT_ALGORITHM", mode="before")
    @classmethod
    def _normalise_algorithm(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper() or "HS256"
        return value

    @field_validator("API_VERSION", mode="before")
    @classmethod
    def _supported_api_version(cls, value: object) -> object:
        return normalize_api_version(value)

    @field_validator("RATE_LIMIT_DEFAULT", "CSP_REPORT_RATE_LIMIT")
    @classmethod
    def _valid_rate_limit(cls, value: str, info: ValidationInfo) -> str:
        try:
            parse_rate_limit(value)
        except ValueError as exc:
            msg = f"{info.field_name} must be a `limits` spec such as 100/minute"
            raise ValueError(msg) from exc
        return value

    @field_validator("RATE_LIMIT_STORAGE", mode="before")
    @classmethod
    def _storage_strip(cls, value: object) -> object:
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        return value.strip() if isinstance(value, str) else value

    @field_validator("RATE_LIMIT_STORAGE")
    @classmethod
    def _valid_storage(cls, value: SecretStr) -> SecretStr:
        if why := rate_limit_storage_problem(value.get_secret_value()):
            msg = f"RATE_LIMIT_STORAGE {why}"
            raise ValueError(msg)
        return value

    @field_validator("OTEL_EXPORTER_OTLP_ENDPOINT")
    @classmethod
    def _valid_otlp_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            parts = urlsplit(value.strip())
            ok = parts.scheme in ("http", "https") and bool(parts.hostname)
        except ValueError:
            ok = False
        if not ok:
            msg = "OTEL_EXPORTER_OTLP_ENDPOINT must be an http(s) URL such as http://collector:4318"
            raise ValueError(msg)
        return value.strip().rstrip("/")

    @field_validator("OTEL_EXPORTER_OTLP_HEADERS")
    @classmethod
    def _valid_otlp_headers(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            try:
                parse_header_list(value.get_secret_value())
            except ValueError as exc:
                msg = "OTEL_EXPORTER_OTLP_HEADERS must be comma-separated name=value pairs"
                raise ValueError(msg) from exc
        return value

    # --- Validators: cross-field ----------------------------------------------

    @model_validator(mode="after")
    def _check_configuration(self) -> Settings:
        """Refuse an invalid configuration, listing every problem in one message.

        Each problem names the variable and the fix, and never echoes a value (they may carry
        credentials). Runs after the per-field parsing above, so it sees typed values.
        """
        problems = self._auth_problems()
        for name, use in (
            ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT"),
            ("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "OTEL_EXPORTER_OTLP_HEADERS"),
        ):
            if getattr(self, name):
                problems.append(f"{name} is not supported; set {use} instead")
        if self.TRACING_ENABLED and not self.OTEL_EXPORTER_OTLP_ENDPOINT:
            problems.append(
                "OTEL_EXPORTER_OTLP_ENDPOINT is required when TRACING_ENABLED=true; set the "
                "collector URL or turn tracing off"
            )
        for name, secret in (
            ("DATABASE_URL", self.DATABASE_URL),
            ("TEST_DATABASE_URL", self.TEST_DATABASE_URL),
        ):
            if secret is not None and _tls_in_url(secret.get_secret_value()):
                problems.append(
                    f"{name}: carries ssl or sslmode in its query, which conflicts with "
                    "DB_SSLMODE; remove it from the URL and set DB_SSLMODE (and DB_SSLROOTCERT)"
                )
        if self.is_production:
            problems += self._production_problems()
        if problems:
            raise ValueError("invalid configuration:\n" + "\n".join(f"  - {p}" for p in problems))
        return self

    def _auth_problems(self) -> list[str]:
        """What each AUTH_MODE needs, checked at startup instead of at the first request."""
        problems: list[str] = []
        if self.AUTH_MODE == "bearer":
            if self.AUTH_JWT_SECRET is None or not self.AUTH_JWT_SECRET.get_secret_value():
                problems.append("AUTH_JWT_SECRET is required when AUTH_MODE=bearer; set it")
            if self.AUTH_JWT_ALGORITHM not in ALLOWED_JWT_ALGS:
                problems.append(
                    "AUTH_JWT_ALGORITHM must be one of " + ", ".join(sorted(ALLOWED_JWT_ALGS))
                )
        if self.AUTH_MODE == "oidc":
            problems += self._oidc_problems()
        if self.AUTH_MODE == "cloudflare-access":
            problems += self._cloudflare_access_problems()
        return problems

    def _cloudflare_access_problems(self) -> list[str]:
        problems: list[str] = []
        if not self.CF_ACCESS_TEAM_DOMAIN:
            problems.append("CF_ACCESS_TEAM_DOMAIN is required when AUTH_MODE=cloudflare-access")
        elif not _CF_TEAM_DOMAIN.match(self.CF_ACCESS_TEAM_DOMAIN):
            problems.append(
                "CF_ACCESS_TEAM_DOMAIN must be exactly <team>.cloudflareaccess.com "
                "(lowercase; no scheme, port or path)"
            )
        if not self.CF_ACCESS_AUD:
            problems.append("CF_ACCESS_AUD is required when AUTH_MODE=cloudflare-access; set it")
        return problems

    def _oidc_problems(self) -> list[str]:
        problems: list[str] = []
        if not self.OIDC_ISSUER:
            problems.append("OIDC_ISSUER is required when AUTH_MODE=oidc; set it")
        if not self.OIDC_AUDIENCE:
            problems.append("OIDC_AUDIENCE is required when AUTH_MODE=oidc; set it")
        refused = [a for a in self.OIDC_ALGORITHMS if a not in OIDC_ALLOWED_ALGS]
        if refused or not self.OIDC_ALGORITHMS:
            problems.append(
                "OIDC_ALGORITHMS must list only "
                + ", ".join(sorted(OIDC_ALLOWED_ALGS))
                + " (`none` and HS* are refused)"
            )
        # Where the keys come from: the JWKS URL, else discovery under the issuer.
        source_name, source = (
            ("OIDC_JWKS_URL", self.OIDC_JWKS_URL)
            if self.OIDC_JWKS_URL
            else ("OIDC_ISSUER", self.OIDC_ISSUER)
        )
        if source and (why := jwks_url_problem(source, production=self.is_production)):
            problems.append(f"{source_name} {why}")
        return problems

    def database_problems(self) -> list[str]:
        """The database checks for ENV production; the migration runner runs these alone."""
        problems: list[str] = []

        url = self.DATABASE_URL.get_secret_value() if self.DATABASE_URL is not None else ""
        if not url.strip() or url.strip().lower().startswith("sqlite"):
            problems.append(
                "DATABASE_URL: SQLite (or no database) is refused in production; set DATABASE_URL "
                "to a PostgreSQL URL"
            )
        if self.DB_SSLMODE in ("disable", "require") and not _is_loopback_database(url):
            problems.append(
                f"DB_SSLMODE={self.DB_SSLMODE} sends database traffic unverified; set "
                "verify-full (or verify-ca) with DB_SSLROOTCERT, or name a loopback or unix-socket "
                "host explicitly in DATABASE_URL (a Cloud SQL Auth Proxy sidecar); a URL with no "
                "host is refused because PGHOST or a default would decide"
            )
        if self.TEST_DATABASE_URL is not None:
            problems.append(
                "TEST_DATABASE_URL: test-only, and it takes precedence over DATABASE_URL; "
                "unset it in production"
            )
        if self.MIGRATION_TEST_DELAY_SECONDS or self.MIGRATION_TEST_FAIL:
            problems.append(
                "MIGRATION_TEST_DELAY_SECONDS and MIGRATION_TEST_FAIL: test-only (a slow or a "
                "failed migration on purpose); unset them in production"
            )
        return problems

    def _production_auth_problems(self) -> list[str]:
        """The auth checks that apply only when ENV is production."""
        problems: list[str] = []
        if self.AUTH_MODE == "gateway" and not self.AUTH_PROXY_CIDRS:
            problems.append(
                "AUTH_PROXY_CIDRS: empty with AUTH_MODE=gateway, so every request gets a 401; "
                "set it to the authenticating proxy's Pod range (for example 10.20.0.0/16), the "
                "only peer allowed to send the identity headers; the edge's range only if the "
                "edge itself authenticates and strips client copies"
            )
        if self.AUTH_MODE == "none" and not self.ALLOW_ANONYMOUS:
            problems.append(
                "AUTH_MODE=none serves every endpoint anonymously; set AUTH_MODE to oidc (or "
                "gateway/bearer), or set ALLOW_ANONYMOUS=true to accept that explicitly"
            )
        return problems

    def _tracing_problems(self) -> list[str]:
        """The tracing checks that apply only when ENV is production."""
        endpoint = self.OTEL_EXPORTER_OTLP_ENDPOINT
        if (
            self.OTEL_EXPORTER_OTLP_HEADERS is not None
            and endpoint
            and urlsplit(endpoint).scheme == "http"
            and not _is_local_host(urlsplit(endpoint).hostname or "")
        ):
            msg = (
                "OTEL_EXPORTER_OTLP_HEADERS would travel in cleartext to the http:// collector; "
                "use an https:// endpoint, a loopback host, or drop the headers (plain http:// "
                "to an in-cluster collector without headers is allowed)"
            )
            return [msg]
        return []

    def _production_problems(self) -> list[str]:
        """The checks that apply only when ENV is production."""
        problems: list[str] = []

        problems += self.database_problems()

        problems += self._tracing_problems()

        if _unverified_redis_tls(self.RATE_LIMIT_STORAGE.get_secret_value()):
            problems.append(
                "RATE_LIMIT_STORAGE: the rediss:// query turns certificate or hostname "
                "verification off (ssl_cert_reqs other than required, ssl_check_hostname=false); "
                "remove it (ssl_ca_certs or ssl_ca_path name a private CA)"
            )

        storage = self.RATE_LIMIT_STORAGE.get_secret_value()
        if urlsplit(storage).scheme == "redis" and not _is_loopback_storage(storage):
            problems.append(
                "RATE_LIMIT_STORAGE: plaintext redis:// to a remote host sends the counters "
                "(and the password) unencrypted; use rediss://, a redis+unix:// socket, a "
                "loopback host, or memory:// (a limit per replica)"
            )

        origins = self.CORS_ORIGINS
        if self.CORS_ALLOW_CREDENTIALS and not origins:
            problems.append(
                "CORS_ORIGINS: empty while CORS_ALLOW_CREDENTIALS=true; list the allowed "
                "origins or set CORS_ALLOW_CREDENTIALS=false"
            )
        if self.CORS_ALLOW_CREDENTIALS and "*" in origins:
            problems.append(
                "CORS_ORIGINS: '*' with CORS_ALLOW_CREDENTIALS=true; list the allowed origins "
                "or set CORS_ALLOW_CREDENTIALS=false"
            )
        if any(o != "*" and not _BARE_ORIGIN.match(o) for o in origins):
            problems.append(
                "CORS_ORIGINS: every origin must be a bare scheme://host[:port], with no path "
                "and no trailing slash"
            )

        if not self.ALLOWED_HOSTS or "*" in self.ALLOWED_HOSTS:
            problems.append(
                "ALLOWED_HOSTS: '*' (or an empty list) accepts any Host header; list the hosts "
                "the service answers, for example the ingress hosts and the Service names"
            )

        if self.TRUST_CF_CONNECTING_IP and not self.TRUSTED_PROXY_CIDRS:
            problems.append(
                "TRUST_CF_CONNECTING_IP=true with an empty TRUSTED_PROXY_CIDRS trusts nothing; "
                "set TRUSTED_PROXY_CIDRS to the cloudflared and gateway Pod ranges, or set "
                "TRUST_CF_CONNECTING_IP=false"
            )

        problems += self._production_auth_problems()

        if not self.ALLOW_PRODUCTION_DOCS:
            problems += [
                f"{name}: documentation is off in production; leave it unset (or empty), or set "
                "ALLOW_PRODUCTION_DOCS=true to serve it"
                for name, value in (
                    ("API_DOCS_URL", self.API_DOCS_URL),
                    ("OPENAPI_URL", self.OPENAPI_URL),
                )
                if value
            ]
        return problems


class ApiVersionSetting(BaseSettings):
    """Only ``API_VERSION``, read exactly as ``Settings`` reads it (environment over ``.env``).

    For the launcher's banner: it must show the version the app will serve, without building
    (and validating) the whole of ``Settings`` a second time.
    """

    model_config = Settings.model_config

    API_VERSION: str = DEFAULT_API_VERSION

    _supported = field_validator("API_VERSION", mode="before")(staticmethod(normalize_api_version))
