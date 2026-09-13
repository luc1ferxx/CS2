import base64
import binascii
import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_ARTIFACT_STORAGE_ROOT = Path(os.getenv("ARTIFACT_STORAGE_ROOT", "/data"))
SUPPORTED_AUTH_MODES = {"development", "test", "production"}
SUPPORTED_AUTH_PROVIDERS = {"oidc", "steam"}
SUPPORTED_OIDC_ALGORITHMS = {"RS256", "ES256"}
SUPPORTED_ARTIFACT_STORAGE_BACKENDS = {"local", "s3"}
SUPPORTED_STEAM_DEMO_PROVIDERS = {"disabled"}
MAX_STAGE3_DEMO_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_STAGE3_VIDEO_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_STAGE3_REPLAY_ARTIFACT_BYTES = 128 * 1024 * 1024
DEVELOPMENT_STEAM_CREDENTIAL_ENCRYPTION_KEY = (
    "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
)


def _base_url_from_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip().rstrip("/")


def _bool_from_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _url_origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlparse(value)
    return parsed.scheme, parsed.hostname or "", parsed.port


def _validate_https_url(env_name: str, value: str) -> None:
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{env_name} must be a valid https URL in production") from exc
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or "\\" in value
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise RuntimeError(f"{env_name} must be a valid https URL in production")


def _is_32_byte_urlsafe_base64(value: str) -> bool:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        return False
    unpadded = value.rstrip("=")
    try:
        decoded = base64.b64decode(
            unpadded + ("=" * (-len(unpadded) % 4)),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError):
        return False
    if len(decoded) != 32:
        return False
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    return unpadded == canonical


@dataclass(frozen=True)
class Settings:
    auth_mode: str = os.getenv("AUTH_MODE", "").strip().lower()
    auth_provider: str = os.getenv("AUTH_PROVIDER", "").strip().lower()
    oidc_issuer: str = _base_url_from_env("OIDC_ISSUER")
    oidc_client_id: str = os.getenv("OIDC_CLIENT_ID", "").strip()
    oidc_client_secret: str = os.getenv("OIDC_CLIENT_SECRET", "").strip()
    oidc_allowed_algorithms_raw: str = os.getenv(
        "OIDC_ALLOWED_ALGORITHMS", "RS256,ES256"
    )
    oidc_authorization_endpoint: str = _base_url_from_env("OIDC_AUTHORIZATION_ENDPOINT")
    oidc_token_endpoint: str = _base_url_from_env("OIDC_TOKEN_ENDPOINT")
    oidc_jwks_url: str = _base_url_from_env("OIDC_JWKS_URL")
    oidc_redirect_uri: str = os.getenv("OIDC_REDIRECT_URI", "").strip()
    frontend_public_url: str = _base_url_from_env(
        "FRONTEND_PUBLIC_URL", "http://localhost:3000"
    )
    auth_cookie_secure: bool = _bool_from_env("AUTH_COOKIE_SECURE")
    auth_session_cookie_name: str = os.getenv(
        "AUTH_SESSION_COOKIE_NAME", "__Host-cs2_session"
    ).strip()
    auth_state_cookie_name: str = os.getenv(
        "AUTH_STATE_COOKIE_NAME", "__Host-cs2_oidc_state"
    ).strip()
    steam_auth_state_cookie_name: str = os.getenv(
        "STEAM_AUTH_STATE_COOKIE_NAME", "__Host-cs2_steam_state"
    ).strip()
    steam_openid_nonce_ttl_seconds: int = int(
        os.getenv("STEAM_OPENID_NONCE_TTL_SECONDS", "600")
    )
    steam_web_api_key: str = os.getenv("STEAM_WEB_API_KEY", "").strip()
    steam_credential_encryption_key: str = os.getenv(
        "STEAM_CREDENTIAL_ENCRYPTION_KEY",
        DEVELOPMENT_STEAM_CREDENTIAL_ENCRYPTION_KEY,
    ).strip()
    steam_credential_encryption_key_version: str = os.getenv(
        "STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION", "dev-v1"
    ).strip()
    steam_sync_max_matches: int = int(os.getenv("STEAM_SYNC_MAX_MATCHES", "20"))
    steam_sync_timeout_seconds: float = float(
        os.getenv("STEAM_SYNC_TIMEOUT_SECONDS", "5")
    )
    steam_sync_retry_base_seconds: int = int(
        os.getenv("STEAM_SYNC_RETRY_BASE_SECONDS", "30")
    )
    steam_sync_retry_max_seconds: int = int(
        os.getenv("STEAM_SYNC_RETRY_MAX_SECONDS", "3600")
    )
    steam_scheduled_sync_enabled: bool = _bool_from_env(
        "STEAM_SCHEDULED_SYNC_ENABLED"
    )
    steam_demo_provider: str = os.getenv(
        "STEAM_DEMO_PROVIDER", "disabled"
    ).strip().lower()
    steam_demo_experimental_replay_cdn_enabled: bool = _bool_from_env(
        "STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED"
    )
    steam_demo_experimental_replay_cdn_raw: str = os.getenv(
        "STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED", "0"
    ).strip().lower()
    steam_demo_download_allowed_hosts_raw: str = os.getenv(
        "STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS", ""
    ).strip()
    steam_demo_download_max_bytes: int = int(
        os.getenv("STEAM_DEMO_DOWNLOAD_MAX_BYTES", str(512 * 1024 * 1024))
    )
    steam_demo_download_max_redirects: int = int(
        os.getenv("STEAM_DEMO_DOWNLOAD_MAX_REDIRECTS", "3")
    )
    steam_demo_download_connect_timeout_seconds: float = float(
        os.getenv("STEAM_DEMO_DOWNLOAD_CONNECT_TIMEOUT_SECONDS", "5")
    )
    steam_demo_download_read_timeout_seconds: float = float(
        os.getenv("STEAM_DEMO_DOWNLOAD_READ_TIMEOUT_SECONDS", "10")
    )
    steam_demo_download_total_timeout_seconds: float = float(
        os.getenv("STEAM_DEMO_DOWNLOAD_TOTAL_TIMEOUT_SECONDS", "60")
    )
    steam_demo_download_global_concurrency: int = int(
        os.getenv("STEAM_DEMO_DOWNLOAD_GLOBAL_CONCURRENCY", "4")
    )
    steam_demo_download_owner_concurrency: int = int(
        os.getenv("STEAM_DEMO_DOWNLOAD_OWNER_CONCURRENCY", "1")
    )
    steam_demo_download_concurrency_lease_seconds: int = int(
        os.getenv("STEAM_DEMO_DOWNLOAD_CONCURRENCY_LEASE_SECONDS", "120")
    )
    auth_session_ttl_seconds: int = int(os.getenv("AUTH_SESSION_TTL_SECONDS", "3600"))
    auth_login_ttl_seconds: int = int(os.getenv("AUTH_LOGIN_TTL_SECONDS", "300"))
    auth_clock_skew_seconds: int = int(os.getenv("AUTH_CLOCK_SKEW_SECONDS", "30"))
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+psycopg2://cs2coach:cs2coach@localhost:5432/cs2coach",
    )
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    redis_queue_name: str = os.getenv("REDIS_QUEUE_NAME", "cs2-demo-jobs")
    backend_public_url: str = _base_url_from_env("BACKEND_PUBLIC_URL", "http://localhost:8000")
    media_url_base: str = _base_url_from_env("MEDIA_URL_BASE")
    artifact_storage_backend: str = os.getenv(
        "ARTIFACT_STORAGE_BACKEND", "local"
    ).strip().lower()
    object_storage_bucket: str = os.getenv("OBJECT_STORAGE_BUCKET", "").strip()
    object_storage_prefix: str = os.getenv(
        "OBJECT_STORAGE_PREFIX", "cs2-artifacts-v1"
    ).strip()
    object_storage_region: str = os.getenv(
        "OBJECT_STORAGE_REGION", "us-east-1"
    ).strip()
    object_storage_endpoint_url: str = _base_url_from_env(
        "OBJECT_STORAGE_ENDPOINT_URL"
    )
    object_storage_access_key_id: str = os.getenv(
        "OBJECT_STORAGE_ACCESS_KEY_ID", ""
    ).strip()
    object_storage_secret_access_key: str = os.getenv(
        "OBJECT_STORAGE_SECRET_ACCESS_KEY", ""
    ).strip()
    artifact_quarantine_ttl_seconds: int = int(
        os.getenv("ARTIFACT_QUARANTINE_TTL_SECONDS", "3600")
    )
    max_demo_upload_bytes: int = int(
        os.getenv("MAX_DEMO_UPLOAD_BYTES", str(MAX_STAGE3_DEMO_UPLOAD_BYTES))
    )
    max_video_upload_bytes: int = int(
        os.getenv("MAX_VIDEO_UPLOAD_BYTES", str(MAX_STAGE3_VIDEO_UPLOAD_BYTES))
    )
    max_replay_artifact_bytes: int = int(
        os.getenv(
            "MAX_REPLAY_ARTIFACT_BYTES",
            str(MAX_STAGE3_REPLAY_ARTIFACT_BYTES),
        )
    )
    upload_chunk_bytes: int = int(os.getenv("UPLOAD_CHUNK_BYTES", str(1024 * 1024)))
    artifact_storage_root: Path = DEFAULT_ARTIFACT_STORAGE_ROOT
    replay_storage_dir: Path = Path(
        os.getenv("REPLAY_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "replays"))
    )
    demo_upload_storage_dir: Path = Path(
        os.getenv("DEMO_UPLOAD_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "uploads"))
    )
    video_storage_dir: Path = Path(
        os.getenv("VIDEO_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "videos"))
    )
    summary_storage_dir: Path = Path(
        os.getenv("SUMMARY_STORAGE_DIR", str(DEFAULT_ARTIFACT_STORAGE_ROOT / "summaries"))
    )
    dev_user_id: str = os.getenv("DEV_USER_ID", "dev-user")
    max_render_clip_seconds: int = int(os.getenv("MAX_RENDER_CLIP_SECONDS", "60"))
    render_worker_token: str = os.getenv("RENDER_WORKER_TOKEN", "dev-render-worker-token")
    render_worker_mode: str = os.getenv("RENDER_WORKER_MODE", "fallback")
    cors_origins_raw: str = os.getenv(
        "CORS_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_origins_raw.split(",")
            if origin.strip()
        ]

    @property
    def runtime_cors_origins(self) -> list[str]:
        if self.auth_mode == "production":
            return [self.frontend_public_url]
        return self.cors_origins

    @property
    def oidc_allowed_algorithms(self) -> list[str]:
        return [
            algorithm.strip()
            for algorithm in self.oidc_allowed_algorithms_raw.split(",")
            if algorithm.strip()
        ]

    @property
    def steam_demo_download_allowed_hosts(self) -> frozenset[str]:
        if not self.steam_demo_download_allowed_hosts_raw:
            return frozenset()
        return frozenset(
            host.strip()
            for host in self.steam_demo_download_allowed_hosts_raw.split(",")
            if host.strip()
        )

    @property
    def steam_openid_realm(self) -> str:
        return f"{self.backend_public_url}/"

    @property
    def steam_openid_callback_url(self) -> str:
        return f"{self.backend_public_url}/auth/steam/callback"

    def validate_runtime_configuration(self) -> None:
        if self.render_worker_mode not in {"fallback", "external"}:
            raise RuntimeError("RENDER_WORKER_MODE must be fallback or external")
        self._validate_explicit_auth_mode()
        self._validate_auth_provider()
        if self.auth_mode != "production":
            self._validate_steam_sync_configuration()
            self._validate_steam_demo_import_configuration()
            self._validate_artifact_storage_configuration()
            return

        if self.auth_provider == "oidc":
            self._validate_production_oidc_configuration()
        if not self.frontend_public_url:
            raise RuntimeError("FRONTEND_PUBLIC_URL is required when AUTH_MODE=production")
        _validate_https_url("FRONTEND_PUBLIC_URL", self.frontend_public_url)

        self._validate_production_render_worker_token()
        if not self.auth_cookie_secure:
            raise RuntimeError("AUTH_COOKIE_SECURE must be enabled in production")
        if not 1 <= self.auth_session_ttl_seconds <= 86_400:
            raise RuntimeError("AUTH_SESSION_TTL_SECONDS must be between 1 and 86400")
        if not 1 <= self.auth_login_ttl_seconds <= 600:
            raise RuntimeError("AUTH_LOGIN_TTL_SECONDS must be between 1 and 600")
        if not 0 <= self.auth_clock_skew_seconds <= 300:
            raise RuntimeError("AUTH_CLOCK_SKEW_SECONDS must be between 0 and 300")
        if self.auth_provider == "steam":
            if not (
                self.auth_login_ttl_seconds + self.auth_clock_skew_seconds
                <= self.steam_openid_nonce_ttl_seconds
                <= 3_600
            ):
                raise RuntimeError(
                    "STEAM_OPENID_NONCE_TTL_SECONDS must cover login TTL and clock skew "
                    "and be at most 3600"
                )
        if not self.cors_origins:
            raise RuntimeError("CORS_ORIGINS must contain at least one browser origin")
        for origin in self.cors_origins:
            parsed_origin = urlparse(origin)
            if (
                origin == "*"
                or parsed_origin.scheme != "https"
                or not parsed_origin.netloc
                or parsed_origin.path
                or parsed_origin.params
                or parsed_origin.query
                or parsed_origin.fragment
                or parsed_origin.username
                or parsed_origin.password
            ):
                raise RuntimeError(
                    "CORS_ORIGINS must contain only explicit https origins in production"
                )
        if self.cors_origins != [self.frontend_public_url]:
            raise RuntimeError(
                "CORS_ORIGINS must exactly match FRONTEND_PUBLIC_URL in production"
            )
        if not self.auth_session_cookie_name.startswith("__Host-"):
            raise RuntimeError("AUTH_SESSION_COOKIE_NAME must use the __Host- prefix")
        active_state_cookie_name = (
            self.auth_state_cookie_name
            if self.auth_provider == "oidc"
            else self.steam_auth_state_cookie_name
        )
        if not active_state_cookie_name.startswith("__Host-"):
            state_cookie_env = (
                "AUTH_STATE_COOKIE_NAME"
                if self.auth_provider == "oidc"
                else "STEAM_AUTH_STATE_COOKIE_NAME"
            )
            raise RuntimeError(f"{state_cookie_env} must use the __Host- prefix")
        if self.auth_session_cookie_name == active_state_cookie_name:
            raise RuntimeError("Authentication cookie names must be distinct")
        _validate_https_url("BACKEND_PUBLIC_URL", self.backend_public_url)
        backend_url = urlparse(self.backend_public_url)
        if (
            backend_url.scheme != "https"
            or not backend_url.netloc
            or backend_url.path
            or backend_url.params
            or backend_url.query
            or backend_url.fragment
        ):
            raise RuntimeError("BACKEND_PUBLIC_URL must be an https origin in production")

        if _url_origin(self.frontend_public_url) != _url_origin(self.backend_public_url):
            raise RuntimeError(
                "FRONTEND_PUBLIC_URL must share the exact API origin in production"
            )
        self._validate_artifact_storage_configuration()
        self._validate_steam_sync_configuration()
        self._validate_steam_demo_import_configuration()

    def validate_worker_runtime_configuration(self) -> None:
        if self.render_worker_mode not in {"fallback", "external"}:
            raise RuntimeError("RENDER_WORKER_MODE must be fallback or external")
        self._validate_explicit_auth_mode()
        if self.auth_mode == "production":
            self._validate_production_render_worker_token()
        self._validate_artifact_storage_configuration()

    def _validate_artifact_storage_configuration(self) -> None:
        if self.artifact_storage_backend not in SUPPORTED_ARTIFACT_STORAGE_BACKENDS:
            raise RuntimeError(
                "ARTIFACT_STORAGE_BACKEND must be explicitly set to local or s3"
            )
        if self.auth_mode == "production" and self.artifact_storage_backend != "s3":
            raise RuntimeError(
                "ARTIFACT_STORAGE_BACKEND must be s3 in production"
            )

        if self.artifact_quarantine_ttl_seconds != 3600:
            raise RuntimeError(
                "ARTIFACT_QUARANTINE_TTL_SECONDS must be 3600 for artifact_intake_v1"
            )
        if not 16 <= self.max_demo_upload_bytes <= MAX_STAGE3_DEMO_UPLOAD_BYTES:
            raise RuntimeError(
                "MAX_DEMO_UPLOAD_BYTES must be between 16 and 1073741824"
            )
        if not 1 <= self.max_video_upload_bytes <= MAX_STAGE3_VIDEO_UPLOAD_BYTES:
            raise RuntimeError(
                "MAX_VIDEO_UPLOAD_BYTES must be between 1 and 2147483648"
            )
        if not 1 <= self.max_replay_artifact_bytes <= MAX_STAGE3_REPLAY_ARTIFACT_BYTES:
            raise RuntimeError(
                "MAX_REPLAY_ARTIFACT_BYTES must be between 1 and 134217728"
            )
        if self.upload_chunk_bytes != 1024 * 1024:
            raise RuntimeError(
                "UPLOAD_CHUNK_BYTES must be 1048576 for artifact_intake_v1"
            )

        if self.artifact_storage_backend != "s3":
            return
        if not _valid_bucket_name(self.object_storage_bucket):
            raise RuntimeError(
                "OBJECT_STORAGE_BUCKET must be a non-empty private bucket name"
            )
        if not _valid_object_prefix(self.object_storage_prefix):
            raise RuntimeError(
                "OBJECT_STORAGE_PREFIX must contain only safe path segments"
            )
        if not self.object_storage_region:
            raise RuntimeError("OBJECT_STORAGE_REGION must not be blank")
        if self.object_storage_endpoint_url:
            try:
                parsed = urlparse(self.object_storage_endpoint_url)
                parsed.port
            except ValueError as exc:
                raise RuntimeError(
                    "OBJECT_STORAGE_ENDPOINT_URL must be a valid URL"
                ) from exc
            required_scheme = "https" if self.auth_mode == "production" else parsed.scheme
            if (
                parsed.scheme != required_scheme
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise RuntimeError(
                    "OBJECT_STORAGE_ENDPOINT_URL must be a private https endpoint in production"
                )
        if bool(self.object_storage_access_key_id) != bool(
            self.object_storage_secret_access_key
        ):
            raise RuntimeError(
                "Object storage credentials must provide both access key and secret key"
            )

    def _validate_explicit_auth_mode(self) -> None:
        if self.auth_mode not in SUPPORTED_AUTH_MODES:
            raise RuntimeError(
                "AUTH_MODE must be explicitly set to development, test, or production"
            )

    def _validate_auth_provider(self) -> None:
        if self.auth_mode == "production" and self.auth_provider not in SUPPORTED_AUTH_PROVIDERS:
            raise RuntimeError(
                "AUTH_PROVIDER must be explicitly set to steam or oidc in production"
            )
        if self.auth_mode != "production" and self.auth_provider not in {
            "",
            *SUPPORTED_AUTH_PROVIDERS,
        }:
            raise RuntimeError("AUTH_PROVIDER must be empty, steam, or oidc")

    def _validate_steam_sync_configuration(self) -> None:
        if not 1 <= self.steam_sync_max_matches <= 20:
            raise RuntimeError("STEAM_SYNC_MAX_MATCHES must be between 1 and 20")
        if not 0.1 <= self.steam_sync_timeout_seconds <= 30:
            raise RuntimeError(
                "STEAM_SYNC_TIMEOUT_SECONDS must be between 0.1 and 30"
            )
        if not 1 <= self.steam_sync_retry_base_seconds <= 3_600:
            raise RuntimeError(
                "STEAM_SYNC_RETRY_BASE_SECONDS must be between 1 and 3600"
            )
        if not (
            self.steam_sync_retry_base_seconds
            <= self.steam_sync_retry_max_seconds
            <= 86_400
        ):
            raise RuntimeError(
                "STEAM_SYNC_RETRY_MAX_SECONDS must be between the retry base and 86400"
            )
        if self.steam_scheduled_sync_enabled:
            raise RuntimeError(
                "STEAM_SCHEDULED_SYNC_ENABLED is not supported until a scheduler exists"
            )
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}",
            self.steam_credential_encryption_key_version,
        ):
            raise RuntimeError(
                "STEAM_CREDENTIAL_ENCRYPTION_KEY_VERSION is invalid"
            )
        if not _is_32_byte_urlsafe_base64(
            self.steam_credential_encryption_key
        ):
            raise RuntimeError(
                "STEAM_CREDENTIAL_ENCRYPTION_KEY must be URL-safe base64 for 32 bytes"
            )
        if self.auth_mode != "production":
            return
        if not re.fullmatch(r"[A-Fa-f0-9]{32}", self.steam_web_api_key):
            raise RuntimeError(
                "STEAM_WEB_API_KEY must be a 32-character hexadecimal key in production"
            )
        if self.steam_credential_encryption_key.rstrip("=") == (
            DEVELOPMENT_STEAM_CREDENTIAL_ENCRYPTION_KEY.rstrip("=")
        ):
            raise RuntimeError(
                "STEAM_CREDENTIAL_ENCRYPTION_KEY must not use the development key in production"
            )

    def _validate_steam_demo_import_configuration(self) -> None:
        if self.steam_demo_provider not in SUPPORTED_STEAM_DEMO_PROVIDERS:
            raise RuntimeError(
                "STEAM_DEMO_PROVIDER must be disabled in this build; no licensed "
                "Demo source provider is registered"
            )
        if self.steam_demo_experimental_replay_cdn_raw not in {
            "0",
            "false",
            "no",
            "off",
            "1",
            "true",
            "yes",
            "on",
        }:
            raise RuntimeError(
                "STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED must be an explicit "
                "boolean"
            )
        if self.steam_demo_experimental_replay_cdn_enabled:
            raise RuntimeError(
                "STEAM_DEMO_EXPERIMENTAL_REPLAY_CDN_ENABLED is unsupported and must "
                "remain disabled"
            )

        raw_hosts = self.steam_demo_download_allowed_hosts_raw
        hosts = self.steam_demo_download_allowed_hosts
        raw_entries = raw_hosts.split(",") if raw_hosts else []
        if (
            len(raw_hosts) > 4096
            or len(hosts) > 16
            or len(raw_entries) != len(hosts)
            or any(not _valid_exact_download_host(host) for host in hosts)
        ):
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_ALLOWED_HOSTS must contain at most 16 unique "
                "lowercase exact DNS hostnames without ports, wildcards, or IP literals"
            )
        if not 16 <= self.steam_demo_download_max_bytes <= MAX_STAGE3_DEMO_UPLOAD_BYTES:
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_MAX_BYTES must be between 16 and 1073741824"
            )
        if not 0 <= self.steam_demo_download_max_redirects <= 3:
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_MAX_REDIRECTS must be between 0 and 3"
            )
        if not 0.1 <= self.steam_demo_download_connect_timeout_seconds <= 30:
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_CONNECT_TIMEOUT_SECONDS must be between 0.1 and 30"
            )
        if not 0.1 <= self.steam_demo_download_read_timeout_seconds <= 30:
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_READ_TIMEOUT_SECONDS must be between 0.1 and 30"
            )
        if not (
            max(
                self.steam_demo_download_connect_timeout_seconds,
                self.steam_demo_download_read_timeout_seconds,
            )
            <= self.steam_demo_download_total_timeout_seconds
            <= 120
        ):
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_TOTAL_TIMEOUT_SECONDS must cover the connect/read "
                "timeouts and be at most 120"
            )
        if not 1 <= self.steam_demo_download_global_concurrency <= 32:
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_GLOBAL_CONCURRENCY must be between 1 and 32"
            )
        if not (
            1
            <= self.steam_demo_download_owner_concurrency
            <= self.steam_demo_download_global_concurrency
        ):
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_OWNER_CONCURRENCY must be between 1 and the "
                "global concurrency limit"
            )
        if not (
            self.steam_demo_download_total_timeout_seconds
            <= self.steam_demo_download_concurrency_lease_seconds
            <= 3_600
        ):
            raise RuntimeError(
                "STEAM_DEMO_DOWNLOAD_CONCURRENCY_LEASE_SECONDS must cover the total "
                "timeout and be at most 3600"
            )

    def _validate_production_oidc_configuration(self) -> None:
        required = (
            ("OIDC_ISSUER", self.oidc_issuer),
            ("OIDC_CLIENT_ID", self.oidc_client_id),
            ("OIDC_AUTHORIZATION_ENDPOINT", self.oidc_authorization_endpoint),
            ("OIDC_TOKEN_ENDPOINT", self.oidc_token_endpoint),
            ("OIDC_JWKS_URL", self.oidc_jwks_url),
            ("OIDC_REDIRECT_URI", self.oidc_redirect_uri),
        )
        for env_name, value in required:
            if not value:
                raise RuntimeError(f"{env_name} is required when AUTH_PROVIDER=oidc")
        https_urls = (
            ("OIDC_ISSUER", self.oidc_issuer),
            ("OIDC_AUTHORIZATION_ENDPOINT", self.oidc_authorization_endpoint),
            ("OIDC_TOKEN_ENDPOINT", self.oidc_token_endpoint),
            ("OIDC_JWKS_URL", self.oidc_jwks_url),
            ("OIDC_REDIRECT_URI", self.oidc_redirect_uri),
        )
        for env_name, value in https_urls:
            _validate_https_url(env_name, value)
        if not self.oidc_allowed_algorithms or not set(
            self.oidc_allowed_algorithms
        ).issubset(SUPPORTED_OIDC_ALGORITHMS):
            raise RuntimeError(
                "OIDC_ALLOWED_ALGORITHMS must contain only supported asymmetric algorithms"
            )
        redirect_url = urlparse(self.oidc_redirect_uri)
        if (
            _url_origin(self.oidc_redirect_uri) != _url_origin(self.backend_public_url)
            or redirect_url.path != "/auth/oidc/callback"
            or redirect_url.params
            or redirect_url.query
            or redirect_url.fragment
        ):
            raise RuntimeError(
                "BACKEND_PUBLIC_URL and OIDC_REDIRECT_URI must share the API origin, "
                "with /auth/oidc/callback as the callback path"
            )

    def _validate_production_render_worker_token(self) -> None:
        if not self.render_worker_token or self.render_worker_token == "dev-render-worker-token":
            raise RuntimeError(
                "RENDER_WORKER_TOKEN must be a non-default service credential in production"
            )


def _valid_bucket_name(value: str) -> bool:
    return bool(
        3 <= len(value) <= 63
        and "/" not in value
        and "\\" not in value
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*[A-Za-z0-9]", value)
    )


def _valid_object_prefix(value: str) -> bool:
    if not value or len(value) > 128 or value.startswith("/") or value.endswith("/"):
        return False
    segments = value.split("/")
    return all(
        segment not in {"", ".", ".."}
        and re.fullmatch(r"[A-Za-z0-9._-]+", segment)
        for segment in segments
    )


def _valid_exact_download_host(value: str) -> bool:
    if not value or len(value) > 253 or value != value.lower():
        return False
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return False
    labels = value.split(".")
    return (
        len(labels) >= 2
        and value != "localhost"
        and all(
            1 <= len(label) <= 63
            and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
            for label in labels
        )
    )


settings = Settings()
