import base64
import hashlib
import json
import logging
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import demos
from app.core.access_log import AuthCallbackAccessLogFilter
from app.core.auth import SessionCsrfMiddleware
from app.core.config import Settings
from app.core.database import Base, get_db
from app.models import Account, Demo, ExternalIdentity
from app.services.account_service import AccountConflictError, AccountService
from app.services.auth_service import AuthService, get_auth_service
from app.services.steam_auth_service import (
    OPENID_NAMESPACE,
    OPENID_SIGNON_TYPE,
    STEAM_CLAIMED_ID_PREFIX,
    STEAM_OPENID_ENDPOINT,
    SteamAuthService,
    get_steam_auth_service,
)


# Synthetic bit-layout fixtures near the uint32 account-id ceiling. They were
# not collected from a user or copied from a Steam profile.
STEAM_ID_A = "76561202255233022"
STEAM_ID_B = "76561202255233021"


class SteamAuthConfigurationTest(unittest.TestCase):
    def test_steam_production_configuration_does_not_require_oidc(self) -> None:
        Settings(**steam_production_settings()).validate_runtime_configuration()

    def test_production_requires_an_explicit_identity_provider(self) -> None:
        values = steam_production_settings()
        values["auth_provider"] = ""
        with self.assertRaisesRegex(RuntimeError, "AUTH_PROVIDER"):
            Settings(**values).validate_runtime_configuration()

    def test_production_rejects_unknown_provider_and_malformed_web_api_key(self) -> None:
        values = steam_production_settings()
        values["auth_provider"] = "oauth"
        with self.assertRaisesRegex(RuntimeError, "AUTH_PROVIDER"):
            Settings(**values).validate_runtime_configuration()

        values = steam_production_settings()
        values["steam_web_api_key"] = "not-a-real-key"
        with self.assertRaisesRegex(RuntimeError, "STEAM_WEB_API_KEY"):
            Settings(**values).validate_runtime_configuration()

    def test_nonce_replay_ttl_must_cover_login_and_clock_skew(self) -> None:
        values = steam_production_settings()
        values.update(
            {
                "auth_login_ttl_seconds": 300,
                "auth_clock_skew_seconds": 30,
                "steam_openid_nonce_ttl_seconds": 329,
            }
        )
        with self.assertRaisesRegex(RuntimeError, "STEAM_OPENID_NONCE_TTL_SECONDS"):
            Settings(**values).validate_runtime_configuration()

    def test_steam_only_settings_do_not_constrain_oidc_compatibility(self) -> None:
        values = steam_production_settings()
        values.update(
            {
                "auth_provider": "oidc",
                "auth_login_ttl_seconds": 600,
                "auth_clock_skew_seconds": 30,
                "steam_openid_nonce_ttl_seconds": 1,
                "steam_web_api_key": "b" * 32,
                "oidc_issuer": "https://issuer.example.test",
                "oidc_client_id": "cs2-coach",
                "oidc_authorization_endpoint": "https://issuer.example.test/authorize",
                "oidc_token_endpoint": "https://issuer.example.test/token",
                "oidc_jwks_url": "https://issuer.example.test/jwks",
                "oidc_redirect_uri": "https://coach.example.test/auth/oidc/callback",
            }
        )
        Settings(**values).validate_runtime_configuration()

    def test_backend_public_url_rejects_userinfo_and_ambiguous_characters(self) -> None:
        invalid_origins = (
            "https://user:pass@coach.example.test",
            "https://coach.example.test\\attacker.example",
            "https://coach.example.test\n",
        )
        for origin in invalid_origins:
            with self.subTest(origin=repr(origin)):
                values = steam_production_settings()
                values["backend_public_url"] = origin
                with self.assertRaisesRegex(RuntimeError, "BACKEND_PUBLIC_URL"):
                    Settings(**values).validate_runtime_configuration()


class AuthenticationAccessLogTest(unittest.TestCase):
    def test_callback_query_is_redacted_before_uvicorn_formats_the_record(self) -> None:
        record = logging.LogRecord(
            "uvicorn.access",
            logging.INFO,
            __file__,
            1,
            '%s - "%s %s HTTP/%s" %d',
            (
                "127.0.0.1:1234",
                "GET",
                "/auth/steam/callback?state=secret&openid.sig=assertion-secret",
                "1.1",
                401,
            ),
            None,
        )

        self.assertTrue(AuthCallbackAccessLogFilter().filter(record))
        self.assertIn("/auth/steam/callback?[query-redacted]", record.getMessage())
        self.assertNotIn("secret", record.getMessage())


class SteamOpenIdBrowserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
        )
        self.settings = Settings(**steam_production_settings())
        self.redis = FakeRedis()
        self.http = FakeSteamHttp()
        self.auth_service = AuthService(self.settings, self.redis)
        self.steam_service = SteamAuthService(
            self.settings,
            self.redis,
            http_client=self.http,
        )
        self.app = build_app(
            self.settings,
            self.auth_service,
            self.steam_service,
            self.Session,
        )
        self.client = TestClient(self.app, base_url="https://coach.example.test")

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_login_redirect_pins_openid_realm_return_to_and_secure_state(self) -> None:
        response = self.client.get(
            "/auth/steam/login?return_to=/dashboard?sort=recent",
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        location = urlparse(response.headers["location"])
        query = parse_qs(location.query)
        self.assertEqual(
            f"{location.scheme}://{location.netloc}{location.path}",
            STEAM_OPENID_ENDPOINT,
        )
        self.assertEqual(query["openid.ns"], [OPENID_NAMESPACE])
        self.assertEqual(query["openid.mode"], ["checkid_setup"])
        self.assertEqual(query["openid.realm"], ["https://coach.example.test/"])
        callback = urlparse(query["openid.return_to"][0])
        self.assertEqual(
            f"{callback.scheme}://{callback.netloc}{callback.path}",
            "https://coach.example.test/auth/steam/callback",
        )
        state = parse_qs(callback.query)["state"][0]
        self.assertEqual(len(state), 43)
        self.assertEqual(len(self.redis.values), 1)
        attempt = json.loads(next(iter(self.redis.values.values())))
        self.assertEqual(attempt["returnTo"], "/dashboard?sort=recent")
        self.assertNotIn(self.settings.steam_web_api_key, response.text)
        cookie = response.headers["set-cookie"]
        self.assertIn("__Host-cs2_steam_state=", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")

    def test_login_and_callback_have_bounded_per_client_rate_limits(self) -> None:
        for _ in range(30):
            self.assertEqual(
                self.client.get("/auth/steam/login", follow_redirects=False).status_code,
                302,
            )
        self.assertEqual(
            self.client.get("/auth/steam/login", follow_redirects=False).status_code,
            429,
        )

        for _ in range(60):
            self.assertEqual(
                self.client.get(
                    "/auth/steam/callback",
                    follow_redirects=False,
                ).status_code,
                401,
            )
        limited = self.client.get("/auth/steam/callback", follow_redirects=False)
        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.headers["referrer-policy"], "no-referrer")

    def test_valid_callback_creates_account_identity_session_and_safe_me(self) -> None:
        response = complete_steam_login(self.client, STEAM_ID_A)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            response.headers["location"],
            "https://coach.example.test/auth/callback?return_to=%2Fdashboard",
        )
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertIn("__Host-cs2_session=", response.headers["set-cookie"])
        self.assertNotIn(STEAM_ID_A, response.headers["location"])
        self.assertNotIn(self.settings.steam_web_api_key, response.text)

        with self.Session() as db:
            identity = db.query(ExternalIdentity).one()
            account = db.query(Account).one()
            self.assertEqual(identity.provider, "steam")
            self.assertEqual(identity.subject, STEAM_ID_A)
            self.assertEqual(identity.owner_id, account.owner_id)
            self.assertTrue(account.owner_id.startswith("owner_v1_"))
            self.assertNotIn(STEAM_ID_A, account.owner_id)
            self.assertEqual(account.display_name, "Tactical Reviewer")

        me = self.client.get("/auth/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(
            me.json(),
            {
                "authenticated": True,
                "account": {
                    "displayName": "Tactical Reviewer",
                    "avatarUrl": "https://avatars.steamstatic.com/avatar_full.jpg",
                    "provider": "steam",
                },
            },
        )
        self.assertNotIn("owner", me.text.lower())
        self.assertNotIn(STEAM_ID_A, me.text)
        self.assertEqual(me.headers["cache-control"], "private, no-store")
        self.assertTrue(
            {"cookie", "origin"}.issubset(
                {
                    item.strip().lower()
                    for item in me.headers.get("vary", "").split(",")
                    if item.strip()
                }
            )
        )
        self.assertEqual(self.http.post_calls[0][0], STEAM_OPENID_ENDPOINT)
        self.assertEqual(
            self.http.post_calls[0][1]["openid.mode"],
            "check_authentication",
        )
        self.assertEqual(
            self.http.get_calls[0],
            f"{STEAM_CLAIMED_ID_PREFIX}{STEAM_ID_A}",
        )

    def test_state_mismatch_and_forged_assertion_fail_without_account(self) -> None:
        login = start_steam_login(self.client)
        self.client.cookies.set("__Host-cs2_steam_state", "wrong-state")
        mismatched = self.client.get(
            "/auth/steam/callback",
            params=assertion_params(login, STEAM_ID_A),
            follow_redirects=False,
        )
        self.assertEqual(mismatched.status_code, 401)
        self.assertEqual(mismatched.headers["referrer-policy"], "no-referrer")

        login = start_steam_login(self.client)
        forged_fields = assertion_params(login, STEAM_ID_A)
        forged_fields["openid.op_endpoint"] = "https://attacker.example/openid"
        forged = self.client.get(
            "/auth/steam/callback",
            params=forged_fields,
            follow_redirects=False,
        )
        self.assertEqual(forged.status_code, 401)
        with self.Session() as db:
            self.assertEqual(db.query(Account).count(), 0)
            self.assertEqual(db.query(ExternalIdentity).count(), 0)

    def test_false_direct_verification_and_missing_signed_field_are_rejected(self) -> None:
        self.http.direct_valid = False
        rejected = complete_steam_login(self.client, STEAM_ID_A)
        self.assertEqual(rejected.status_code, 401)

        self.http.direct_valid = True
        login = start_steam_login(self.client)
        fields = assertion_params(login, STEAM_ID_A)
        fields["openid.signed"] = fields["openid.signed"].replace(
            ",return_to", ""
        )
        missing_signed = self.client.get(
            "/auth/steam/callback",
            params=fields,
            follow_redirects=False,
        )
        self.assertEqual(missing_signed.status_code, 401)

    def test_claimed_id_discovery_must_match_the_pinned_steam_provider(self) -> None:
        self.http.discovery_endpoint = "https://attacker.example/openid"
        wrong_endpoint = complete_steam_login(self.client, STEAM_ID_A)
        self.assertEqual(wrong_endpoint.status_code, 401)
        self.assertEqual(self.http.post_calls, [])

        self.http.discovery_endpoint = STEAM_OPENID_ENDPOINT
        self.http.discovery_status = 302
        redirected = complete_steam_login(self.client, STEAM_ID_A)
        self.assertEqual(redirected.status_code, 401)
        self.assertEqual(self.http.post_calls, [])

    def test_duplicate_parameter_and_steam_id_formats_are_rejected(self) -> None:
        login = start_steam_login(self.client)
        fields = list(assertion_params(login, STEAM_ID_A).items())
        fields.append(("openid.return_to", login.callback_url))
        duplicate = self.client.get(
            "/auth/steam/callback",
            params=fields,
            follow_redirects=False,
        )
        self.assertEqual(duplicate.status_code, 401)

        invalid_ids = (
            "7656119796027807",
            "7656119796027807x",
            "076561197960278073",
            "103582791429521412",
        )
        for steam_id in invalid_ids:
            with self.subTest(steam_id=steam_id):
                response = complete_steam_login(self.client, steam_id)
                self.assertEqual(response.status_code, 401)

    def test_response_nonce_replay_is_rejected_across_fresh_states(self) -> None:
        nonce = current_nonce("same-assertion")
        first = complete_steam_login(self.client, STEAM_ID_A, nonce=nonce)
        second = complete_steam_login(self.client, STEAM_ID_A, nonce=nonce)

        self.assertEqual(first.status_code, 303)
        self.assertEqual(second.status_code, 401)
        with self.Session() as db:
            self.assertEqual(db.query(Account).count(), 1)
            self.assertEqual(db.query(ExternalIdentity).count(), 1)

    def test_profile_lookup_failure_does_not_block_login(self) -> None:
        self.http.profile_error = True
        response = complete_steam_login(self.client, STEAM_ID_A)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(
            self.client.get("/auth/me").json()["account"],
            {
                "displayName": "Steam account",
                "avatarUrl": None,
                "provider": "steam",
            },
        )

    def test_expired_session_and_logout_revoke_access(self) -> None:
        self.assertEqual(complete_steam_login(self.client, STEAM_ID_A).status_code, 303)
        session_key = next(
            key for key in self.redis.values if key.startswith("auth:session:")
        )
        record = json.loads(self.redis.values[session_key])
        record["expiresAt"] = int(time.time()) - 1
        self.redis.values[session_key] = json.dumps(record)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

        self.assertEqual(complete_steam_login(self.client, STEAM_ID_A).status_code, 303)
        missing_origin = self.client.post("/auth/logout")
        self.assertEqual(missing_origin.status_code, 403)
        logout = self.client.post(
            "/auth/logout",
            headers={"Origin": "https://coach.example.test"},
        )
        self.assertEqual(logout.status_code, 204)
        self.assertEqual(self.client.get("/auth/me").status_code, 401)

    def test_two_steam_sessions_keep_demo_library_owner_isolated(self) -> None:
        client_a = TestClient(self.app, base_url="https://coach.example.test")
        client_b = TestClient(self.app, base_url="https://coach.example.test")
        self.assertEqual(complete_steam_login(client_a, STEAM_ID_A).status_code, 303)
        self.assertEqual(complete_steam_login(client_b, STEAM_ID_B).status_code, 303)

        with self.Session() as db:
            owner_a = (
                db.query(ExternalIdentity)
                .filter(ExternalIdentity.subject == STEAM_ID_A)
                .one()
                .owner_id
            )
            owner_b = (
                db.query(ExternalIdentity)
                .filter(ExternalIdentity.subject == STEAM_ID_B)
                .one()
                .owner_id
            )
            db.add(make_demo("demo-steam-a", owner_a))
            db.add(make_demo("demo-steam-b", owner_b))
            db.commit()

        demos_a = client_a.get("/demos")
        demos_b = client_b.get("/demos")
        self.assertEqual([item["id"] for item in demos_a.json()], ["demo-steam-a"])
        self.assertEqual([item["id"] for item in demos_b.json()], ["demo-steam-b"])
        self.assertEqual(
            client_a.get("/demos/demo-steam-b/status").status_code,
            404,
        )


class AccountIdentityConflictTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(bind=self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_identity_is_idempotent_but_cannot_move_between_accounts(self) -> None:
        owner_a = "owner_v1_" + ("a" * 43)
        owner_b = "owner_v1_" + ("b" * 43)
        with self.Session() as db:
            service = AccountService(db)
            first = service.resolve_or_create_identity(
                provider="steam",
                subject=STEAM_ID_A,
                preferred_owner_id=owner_a,
            )
            repeated = service.resolve_or_create_identity(
                provider="steam",
                subject=STEAM_ID_A,
                preferred_owner_id=owner_a,
            )
            self.assertEqual(first.owner_id, repeated.owner_id)
            with self.assertRaises(AccountConflictError):
                service.resolve_or_create_identity(
                    provider="steam",
                    subject=STEAM_ID_A,
                    preferred_owner_id=owner_b,
                )
            db.rollback()
            self.assertEqual(db.query(Account).count(), 1)
            self.assertEqual(db.query(ExternalIdentity).count(), 1)

    def test_one_account_cannot_bind_two_subjects_for_same_provider(self) -> None:
        owner_id = "owner_v1_" + ("a" * 43)
        with self.Session() as db:
            service = AccountService(db)
            service.resolve_or_create_identity(
                provider="steam",
                subject=STEAM_ID_A,
                preferred_owner_id=owner_id,
            )
            with self.assertRaises(AccountConflictError):
                service.resolve_or_create_identity(
                    provider="steam",
                    subject=STEAM_ID_B,
                    preferred_owner_id=owner_id,
                )

    def test_external_subject_lookup_is_owner_and_provider_scoped(self) -> None:
        owner_id = "owner_v1_" + ("a" * 43)
        with self.Session() as db:
            service = AccountService(db)
            service.resolve_or_create_identity(
                provider="steam",
                subject=STEAM_ID_A,
                preferred_owner_id=owner_id,
            )

            self.assertEqual(
                service.get_external_subject(owner_id, "steam"),
                STEAM_ID_A,
            )
            self.assertIsNone(service.get_external_subject(owner_id, "oidc"))
            self.assertIsNone(
                service.get_external_subject("owner_v1_" + ("b" * 43), "steam")
            )


class LoginAttempt:
    def __init__(self, state: str, callback_url: str):
        self.state = state
        self.callback_url = callback_url


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.counters: dict[str, int] = {}

    def eval(
        self,
        _script: str,
        _key_count: int,
        key: str,
        _window: int,
        limit: int,
    ) -> int:
        count = self.counters.get(key, 0) + 1
        self.counters[key] = count
        return 1 if count <= int(limit) else 0

    def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class FakeHttpResponse:
    def __init__(
        self,
        *,
        text: str = "",
        payload: dict[str, object] | None = None,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.text = text
        self.content = text.encode("utf-8")
        self.payload = payload or {}
        self.status_code = status_code
        self.headers = headers or {}

    def json(self) -> dict[str, object]:
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def iter_bytes(self):
        yield self.content

    def raise_for_status(self) -> None:
        if not 200 <= self.status_code < 300:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSteamHttp:
    def __init__(self) -> None:
        self.direct_valid = True
        self.discovery_endpoint = STEAM_OPENID_ENDPOINT
        self.discovery_status = 200
        self.profile_error = False
        self.post_calls: list[tuple[str, dict[str, str]]] = []
        self.get_calls: list[str] = []

    def post(self, url: str, *, data: dict[str, str], **_kwargs) -> FakeHttpResponse:
        self.post_calls.append((url, data))
        return FakeHttpResponse(
            text=(
                f"ns:{OPENID_NAMESPACE}\n"
                f"is_valid:{str(self.direct_valid).lower()}\n"
            )
        )

    def stream(self, _method: str, url: str, **_kwargs) -> FakeHttpResponse:
        self.get_calls.append(url)
        return FakeHttpResponse(
            text=(
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<xrds:XRDS xmlns:xrds="xri://$xrds" '
                'xmlns="xri://$xrd*($v*2.0)"><XRD><Service priority="0">'
                f"<Type>{OPENID_SIGNON_TYPE}</Type>"
                f"<URI>{self.discovery_endpoint}</URI>"
                "</Service></XRD></xrds:XRDS>"
            ),
            status_code=self.discovery_status,
            headers={"content-type": "application/xrds+xml;charset=utf-8"},
        )

    def get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        **_kwargs,
    ) -> FakeHttpResponse:
        self.get_calls.append(url)
        if self.profile_error:
            raise RuntimeError("profile unavailable")
        if params is None:
            raise RuntimeError("profile request parameters are missing")
        return FakeHttpResponse(
            payload={
                "response": {
                    "players": [
                        {
                            "steamid": params["steamids"],
                            "personaname": "Tactical Reviewer",
                            "avatarfull": "https://avatars.steamstatic.com/avatar_full.jpg",
                        }
                    ]
                }
            }
        )


def steam_production_settings() -> dict[str, object]:
    return {
        "auth_mode": "production",
        "auth_provider": "steam",
        "frontend_public_url": "https://coach.example.test",
        "backend_public_url": "https://coach.example.test",
        "auth_cookie_secure": True,
        "cors_origins_raw": "https://coach.example.test",
        "render_worker_token": "test-worker-secret-that-is-not-a-default",
        "artifact_storage_backend": "s3",
        "object_storage_bucket": "private-cs2-artifacts",
        "object_storage_prefix": "cs2-artifacts-v1",
        "steam_web_api_key": "a" * 32,
        "steam_credential_encryption_key": base64.urlsafe_b64encode(
            b"p" * 32
        ).decode("ascii"),
        "steam_credential_encryption_key_version": "test-v1",
    }


def build_app(
    settings: Settings,
    auth_service: AuthService,
    steam_service: SteamAuthService,
    session_factory,
) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SessionCsrfMiddleware,
        runtime_settings=settings,
        auth_service_factory=lambda: auth_service,
    )
    app.include_router(auth_api.router)
    app.include_router(demos.router)
    app.dependency_overrides[get_auth_service] = lambda: auth_service
    app.dependency_overrides[get_steam_auth_service] = lambda: steam_service

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return app


def start_steam_login(client: TestClient) -> LoginAttempt:
    response = client.get("/auth/steam/login", follow_redirects=False)
    if response.status_code != 302:
        raise AssertionError(f"Steam login start failed with {response.status_code}")
    query = parse_qs(urlparse(response.headers["location"]).query)
    callback_url = query["openid.return_to"][0]
    state = parse_qs(urlparse(callback_url).query)["state"][0]
    return LoginAttempt(state, callback_url)


def complete_steam_login(
    client: TestClient,
    steam_id: str,
    *,
    nonce: str | None = None,
) -> object:
    login = start_steam_login(client)
    return client.get(
        "/auth/steam/callback",
        params=assertion_params(login, steam_id, nonce=nonce),
        follow_redirects=False,
    )


def assertion_params(
    login: LoginAttempt,
    steam_id: str,
    *,
    nonce: str | None = None,
) -> dict[str, str]:
    claimed_id = f"https://steamcommunity.com/openid/id/{steam_id}"
    return {
        "state": login.state,
        "openid.ns": OPENID_NAMESPACE,
        "openid.mode": "id_res",
        "openid.op_endpoint": STEAM_OPENID_ENDPOINT,
        "openid.claimed_id": claimed_id,
        "openid.identity": claimed_id,
        "openid.return_to": login.callback_url,
        "openid.response_nonce": nonce or current_nonce(login.state[:8]),
        "openid.assoc_handle": "test-association",
        "openid.signed": (
            "op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle"
        ),
        "openid.sig": "test-signature",
    }


def current_nonce(suffix: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{timestamp}{suffix}"


def make_demo(demo_id: str, owner_id: str) -> Demo:
    now = datetime.now(timezone.utc)
    return Demo(
        id=demo_id,
        owner_id=owner_id,
        legacy_user_id=owner_id,
        name=demo_id,
        original_filename=f"{demo_id}.dem",
        map_name="de_dust2",
        tick_rate=64,
        round_count=1,
        coaching_event_count=0,
        status="completed",
        archived=False,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


if __name__ == "__main__":
    unittest.main()
