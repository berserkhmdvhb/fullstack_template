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

from limits import parse as parse_rate_limit
from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
# The chart's ingress `proxy-body-size` (10m, which nginx reads as MiB); a test keeps them equal.
DEFAULT_MAX_REQUEST_BODY_BYTES: Final[int] = 10 * 1024 * 1024

# HMAC family only: JWT alg name -> hashlib digest name (a JWT "HS256" is hashlib "sha256").
# RSA/ECDSA/`none` are deliberately rejected to avoid the classic "alg confusion" attack
# (an attacker swapping alg to bypass the key).
JWT_HASHLIB: Final[dict[str, str]] = {"HS256": "sha256", "HS384": "sha384", "HS512": "sha512"}
ALLOWED_JWT_ALGS: Final[frozenset[str]] = frozenset(JWT_HASHLIB)

PRODUCTION_ENVS: Final[frozenset[str]] = frozenset({"production", "prod"})

# A bare origin: scheme://host[:port], no path, query or trailing slash.
_BARE_ORIGIN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/?#\s]+$")

AuthMode = Literal["none", "gateway", "bearer"]

_BLANK_IS_DEFAULT: Final[tuple[str, ...]] = (
    "ENV",
    "AUTH_USER_ID_HEADER",
    "AUTH_ROLES_HEADER",
    "AUTH_JWT_USER_ID_CLAIM",
    "SQLITE_PATH",
    "LOG_LEVEL",
    "DATABASE_URL",
    "TEST_DATABASE_URL",
    "LOG_REQUESTS",
    "CORS_ALLOW_CREDENTIALS",
    "ALLOW_ANONYMOUS",
    "ALLOW_PRODUCTION_DOCS",
    "TRUST_CF_CONNECTING_IP",
    "MAX_REQUEST_BODY_BYTES",
)


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
        description="Log every HTTP request (method, path, status, duration).",
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

    # --- Request limits -------------------------------------------------------
    MAX_REQUEST_BODY_BYTES: int = Field(
        default=DEFAULT_MAX_REQUEST_BODY_BYTES,
        gt=0,
        description="Largest accepted request body in bytes; more is a 413. The default (10 MiB) "
        "is the chart's ingress `proxy-body-size`, so the edge and the service agree.",
    )
    ALLOWED_HOSTS: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["*"],
        description="Comma-separated `Host` values the service answers (`*.example.com` matches "
        "subdomains, `*` anything). Anything else is a 400; the probe and metrics paths are "
        "exempt. The chart sets it from the ingress hosts and the backend Service names.",
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
        description="`none` (anonymous), `gateway` (trusted identity header) or `bearer` "
        "(HMAC-signed JWT).",
        json_schema_extra={"production": "`none` is refused unless ALLOW_ANONYMOUS=true."},
    )
    ALLOW_ANONYMOUS: bool = Field(
        default=False,
        description="Production only: explicitly accept AUTH_MODE=none (open endpoints).",
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

    @field_validator("TRUSTED_PROXY_CIDRS", mode="before")
    @classmethod
    def _cidrs(cls, value: object) -> object:
        """Comma-separated CIDRs (a bare address is a /32 or /128); an invalid one is an error."""
        if not isinstance(value, str):
            return value
        try:
            return [ipaddress.ip_network(i.strip()) for i in value.split(",") if i.strip()]
        except ValueError as exc:
            msg = (
                "TRUSTED_PROXY_CIDRS must be comma-separated IPv4/IPv6 CIDRs in network form: "
                "an entry with host bits set (10.1.2.3/8) is refused, write 10.0.0.0/8"
            )
            raise ValueError(msg) from exc

    @field_validator("DB_POOL_SIZE", "DB_MAX_OVERFLOW", mode="before")
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

    @field_validator("AUTH_MODE", mode="before")
    @classmethod
    def _normalise_mode(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower() or "none"
        return value

    @field_validator("AUTH_JWT_ALGORITHM", mode="before")
    @classmethod
    def _normalise_algorithm(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper() or "HS256"
        return value

    @field_validator("API_VERSION", mode="before")
    @classmethod
    def _supported_api_version(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        version = value.strip() or DEFAULT_API_VERSION
        if version not in SUPPORTED_VERSIONS:
            msg = "API_VERSION: " + MSG_ERROR_UNSUPPORTED_VERSION.format(version=version)
            raise ValueError(msg)
        return version

    @field_validator("RATE_LIMIT_DEFAULT")
    @classmethod
    def _valid_rate_limit(cls, value: str) -> str:
        try:
            parse_rate_limit(value)
        except ValueError as exc:
            msg = "RATE_LIMIT_DEFAULT must be a `limits` spec such as 100/minute"
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
        return problems

    def _production_problems(self) -> list[str]:
        """The checks that apply only when ENV is production."""
        problems: list[str] = []

        url = self.DATABASE_URL.get_secret_value() if self.DATABASE_URL is not None else ""
        if not url.strip() or url.strip().lower().startswith("sqlite"):
            problems.append(
                "DATABASE_URL: SQLite (or no database) is refused in production; set DATABASE_URL "
                "to a PostgreSQL URL"
            )
        if self.TEST_DATABASE_URL is not None:
            problems.append(
                "TEST_DATABASE_URL: test-only, and it takes precedence over DATABASE_URL; "
                "unset it in production"
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

        if self.AUTH_MODE == "none" and not self.ALLOW_ANONYMOUS:
            problems.append(
                "AUTH_MODE=none serves every endpoint anonymously; set AUTH_MODE to gateway or "
                "bearer, or set ALLOW_ANONYMOUS=true to accept that explicitly"
            )

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
