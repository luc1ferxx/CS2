import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_ARTIFACT_STORAGE_ROOT = Path(os.getenv("ARTIFACT_STORAGE_ROOT", "/data"))
SUPPORTED_AUTH_MODES = {"development", "test", "production"}
SUPPORTED_OIDC_ALGORITHMS = {"RS256", "ES256"}


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


@dataclass(frozen=True)
class Settings:
    auth_mode: str = os.getenv("AUTH_MODE", "").strip().lower()
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

    def validate_runtime_configuration(self) -> None:
        self._validate_explicit_auth_mode()
        if self.auth_mode != "production":
            return

        required = (
            ("OIDC_ISSUER", self.oidc_issuer),
            ("OIDC_CLIENT_ID", self.oidc_client_id),
            ("OIDC_AUTHORIZATION_ENDPOINT", self.oidc_authorization_endpoint),
            ("OIDC_TOKEN_ENDPOINT", self.oidc_token_endpoint),
            ("OIDC_JWKS_URL", self.oidc_jwks_url),
            ("OIDC_REDIRECT_URI", self.oidc_redirect_uri),
            ("FRONTEND_PUBLIC_URL", self.frontend_public_url),
        )
        for env_name, value in required:
            if not value:
                raise RuntimeError(f"{env_name} is required when AUTH_MODE=production")

        https_urls = (
            ("OIDC_ISSUER", self.oidc_issuer),
            ("OIDC_AUTHORIZATION_ENDPOINT", self.oidc_authorization_endpoint),
            ("OIDC_TOKEN_ENDPOINT", self.oidc_token_endpoint),
            ("OIDC_JWKS_URL", self.oidc_jwks_url),
            ("OIDC_REDIRECT_URI", self.oidc_redirect_uri),
            ("FRONTEND_PUBLIC_URL", self.frontend_public_url),
        )
        for env_name, value in https_urls:
            _validate_https_url(env_name, value)

        self._validate_production_render_worker_token()
        if not self.auth_cookie_secure:
            raise RuntimeError("AUTH_COOKIE_SECURE must be enabled in production")
        if not 1 <= self.auth_session_ttl_seconds <= 86_400:
            raise RuntimeError("AUTH_SESSION_TTL_SECONDS must be between 1 and 86400")
        if not 1 <= self.auth_login_ttl_seconds <= 600:
            raise RuntimeError("AUTH_LOGIN_TTL_SECONDS must be between 1 and 600")
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
        if not self.oidc_allowed_algorithms or not set(
            self.oidc_allowed_algorithms
        ).issubset(SUPPORTED_OIDC_ALGORITHMS):
            raise RuntimeError(
                "OIDC_ALLOWED_ALGORITHMS must contain only supported asymmetric algorithms"
            )
        if not self.auth_session_cookie_name.startswith("__Host-"):
            raise RuntimeError("AUTH_SESSION_COOKIE_NAME must use the __Host- prefix")
        if not self.auth_state_cookie_name.startswith("__Host-"):
            raise RuntimeError("AUTH_STATE_COOKIE_NAME must use the __Host- prefix")
        if self.auth_session_cookie_name == self.auth_state_cookie_name:
            raise RuntimeError("Authentication cookie names must be distinct")
        if not 0 <= self.auth_clock_skew_seconds <= 300:
            raise RuntimeError("AUTH_CLOCK_SKEW_SECONDS must be between 0 and 300")

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

        redirect_url = urlparse(self.oidc_redirect_uri)
        if (
            _url_origin(self.oidc_redirect_uri) != _url_origin(self.backend_public_url)
            or redirect_url.path != "/auth/oidc/callback"
            or redirect_url.params
            or redirect_url.query
            or redirect_url.fragment
        ):
            raise RuntimeError(
                "OIDC_REDIRECT_URI must be the API origin followed by /auth/oidc/callback"
            )
        if _url_origin(self.frontend_public_url) != _url_origin(self.backend_public_url):
            raise RuntimeError(
                "FRONTEND_PUBLIC_URL must share the exact API origin in production"
            )

    def validate_worker_runtime_configuration(self) -> None:
        self._validate_explicit_auth_mode()
        if self.auth_mode == "production":
            self._validate_production_render_worker_token()

    def _validate_explicit_auth_mode(self) -> None:
        if self.auth_mode not in SUPPORTED_AUTH_MODES:
            raise RuntimeError(
                "AUTH_MODE must be explicitly set to development, test, or production"
            )

    def _validate_production_render_worker_token(self) -> None:
        if not self.render_worker_token or self.render_worker_token == "dev-render-worker-token":
            raise RuntimeError(
                "RENDER_WORKER_TOKEN must be a non-default service credential in production"
            )


settings = Settings()
