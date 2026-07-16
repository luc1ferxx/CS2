import json
import hashlib
import time
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import jwt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.api import coaching, demos, diagnostics, private_media, replay, uploads
from app.core.auth import SessionCsrfMiddleware, get_current_owner_id
from app.core.config import Settings
from app.core.database import Base, get_db
from app.models import Demo
from app.services.auth_service import (
    AuthenticationError,
    AuthService,
    derive_owner_id,
    get_auth_service,
)


class ProductionAuthConfigurationTest(unittest.TestCase):
    def test_runtime_requires_an_explicit_supported_auth_mode(self) -> None:
        for auth_mode in ("", "preview", "PRODUCTION "):
            with self.subTest(auth_mode=auth_mode):
                with self.assertRaisesRegex(RuntimeError, "AUTH_MODE"):
                    Settings(auth_mode=auth_mode).validate_runtime_configuration()

    def test_production_auth_fails_closed_when_oidc_configuration_is_missing(self) -> None:
        settings = Settings(auth_mode="production")

        with self.assertRaisesRegex(RuntimeError, "OIDC_ISSUER"):
            settings.validate_runtime_configuration()

    def test_production_requires_complete_https_oidc_and_cookie_configuration(self) -> None:
        required_fields = {
            "oidc_issuer": "OIDC_ISSUER",
            "oidc_client_id": "OIDC_CLIENT_ID",
            "oidc_authorization_endpoint": "OIDC_AUTHORIZATION_ENDPOINT",
            "oidc_token_endpoint": "OIDC_TOKEN_ENDPOINT",
            "oidc_jwks_url": "OIDC_JWKS_URL",
            "oidc_redirect_uri": "OIDC_REDIRECT_URI",
            "frontend_public_url": "FRONTEND_PUBLIC_URL",
        }
        for field_name, env_name in required_fields.items():
            with self.subTest(field_name=field_name):
                values = valid_production_settings_kwargs()
                values[field_name] = ""
                with self.assertRaisesRegex(RuntimeError, env_name):
                    Settings(**values).validate_runtime_configuration()

    def test_production_rejects_malformed_or_credentialed_oidc_urls(self) -> None:
        oidc_url_fields = (
            ("oidc_issuer", "OIDC_ISSUER"),
            ("oidc_authorization_endpoint", "OIDC_AUTHORIZATION_ENDPOINT"),
            ("oidc_token_endpoint", "OIDC_TOKEN_ENDPOINT"),
            ("oidc_jwks_url", "OIDC_JWKS_URL"),
        )
        for field_name, env_name in oidc_url_fields:
            for invalid_url in ("https:missing-host", "https://user:pass@example.test/path"):
                with self.subTest(field_name=field_name, invalid_url=invalid_url):
                    values = valid_production_settings_kwargs()
                    values[field_name] = invalid_url
                    with self.assertRaisesRegex(RuntimeError, env_name):
                        Settings(**values).validate_runtime_configuration()

    def test_complete_production_auth_configuration_is_accepted(self) -> None:
        Settings(**valid_production_settings_kwargs()).validate_runtime_configuration()

    def test_production_rejects_the_development_render_worker_credential(self) -> None:
        values = valid_production_settings_kwargs()
        values["render_worker_token"] = "dev-render-worker-token"

        with self.assertRaisesRegex(RuntimeError, "RENDER_WORKER_TOKEN"):
            Settings(**values).validate_runtime_configuration()

    def test_worker_runtime_fails_closed_without_requiring_browser_oidc_configuration(self) -> None:
        for auth_mode in ("", "preview"):
            with self.subTest(auth_mode=auth_mode):
                with self.assertRaisesRegex(RuntimeError, "AUTH_MODE"):
                    Settings(auth_mode=auth_mode).validate_worker_runtime_configuration()

        with self.assertRaisesRegex(RuntimeError, "RENDER_WORKER_TOKEN"):
            Settings(
                auth_mode="production",
                render_worker_token="dev-render-worker-token",
            ).validate_worker_runtime_configuration()

        Settings(
            auth_mode="production",
            render_worker_token="production-worker-credential",
        ).validate_worker_runtime_configuration()

    def test_production_requires_secure_bounded_session_configuration(self) -> None:
        invalid_overrides = (
            ({"auth_cookie_secure": False}, "AUTH_COOKIE_SECURE"),
            ({"auth_session_ttl_seconds": 0}, "AUTH_SESSION_TTL_SECONDS"),
            ({"auth_session_ttl_seconds": 86_401}, "AUTH_SESSION_TTL_SECONDS"),
            ({"auth_login_ttl_seconds": 0}, "AUTH_LOGIN_TTL_SECONDS"),
            ({"auth_login_ttl_seconds": 601}, "AUTH_LOGIN_TTL_SECONDS"),
        )
        for overrides, env_name in invalid_overrides:
            with self.subTest(overrides=overrides):
                values = valid_production_settings_kwargs()
                values.update(overrides)
                with self.assertRaisesRegex(RuntimeError, env_name):
                    Settings(**values).validate_runtime_configuration()

    def test_production_rejects_wildcard_or_insecure_browser_origins(self) -> None:
        for cors_origins in (
            "*",
            "http://coach.example.test",
            "https://coach.example.test, *",
            "https://coach.example.test/path",
        ):
            with self.subTest(cors_origins=cors_origins):
                values = valid_production_settings_kwargs()
                values["cors_origins_raw"] = cors_origins
                with self.assertRaisesRegex(RuntimeError, "CORS_ORIGINS"):
                    Settings(**values).validate_runtime_configuration()

        values = valid_production_settings_kwargs()
        values["frontend_public_url"] = "https://coach.example.test/path"
        values["cors_origins_raw"] = "https://coach.example.test/path"
        with self.assertRaisesRegex(RuntimeError, "CORS_ORIGINS"):
            Settings(**values).validate_runtime_configuration()

    def test_production_cors_allows_only_the_exact_frontend_origin(self) -> None:
        values = valid_production_settings_kwargs()
        values["cors_origins_raw"] = (
            "https://coach.example.test,https://stale.example.test"
        )

        with self.assertRaisesRegex(RuntimeError, "exactly match FRONTEND_PUBLIC_URL"):
            Settings(**values).validate_runtime_configuration()

        runtime_settings = Settings(**values)
        self.assertEqual(
            runtime_settings.runtime_cors_origins,
            ["https://coach.example.test"],
        )

    def test_production_oidc_algorithm_allowlist_accepts_only_asymmetric_algorithms(self) -> None:
        for algorithms in ("", "HS256", "none", "RS256,HS256"):
            with self.subTest(algorithms=algorithms):
                values = valid_production_settings_kwargs()
                values["oidc_allowed_algorithms_raw"] = algorithms
                with self.assertRaisesRegex(RuntimeError, "OIDC_ALLOWED_ALGORITHMS"):
                    Settings(**values).validate_runtime_configuration()

    def test_production_requires_host_only_cookie_names_and_bounded_clock_skew(self) -> None:
        invalid_overrides = (
            ({"auth_session_cookie_name": "session"}, "AUTH_SESSION_COOKIE_NAME"),
            ({"auth_state_cookie_name": "state"}, "AUTH_STATE_COOKIE_NAME"),
            (
                {
                    "auth_session_cookie_name": "__Host-same",
                    "auth_state_cookie_name": "__Host-same",
                },
                "cookie names",
            ),
            ({"auth_clock_skew_seconds": -1}, "AUTH_CLOCK_SKEW_SECONDS"),
            ({"auth_clock_skew_seconds": 301}, "AUTH_CLOCK_SKEW_SECONDS"),
        )
        for overrides, expected_message in invalid_overrides:
            with self.subTest(overrides=overrides):
                values = valid_production_settings_kwargs()
                values.update(overrides)
                with self.assertRaisesRegex(RuntimeError, expected_message):
                    Settings(**values).validate_runtime_configuration()

    def test_production_callback_and_frontend_are_bound_to_the_https_api_origin(self) -> None:
        invalid_overrides = (
            ({"backend_public_url": "http://coach.example.test"}, "BACKEND_PUBLIC_URL"),
            (
                {"oidc_redirect_uri": "https://other.example.test/auth/oidc/callback"},
                "OIDC_REDIRECT_URI",
            ),
            (
                {"oidc_redirect_uri": "https://coach.example.test/not-the-callback"},
                "OIDC_REDIRECT_URI",
            ),
            (
                {
                    "frontend_public_url": "https://app.example.test",
                    "cors_origins_raw": "https://app.example.test",
                },
                "FRONTEND_PUBLIC_URL",
            ),
        )
        for overrides, expected_message in invalid_overrides:
            with self.subTest(overrides=overrides):
                values = valid_production_settings_kwargs()
                values.update(overrides)
                with self.assertRaisesRegex(RuntimeError, expected_message):
                    Settings(**values).validate_runtime_configuration()


def valid_production_settings_kwargs() -> dict[str, object]:
    return {
        "auth_mode": "production",
        "oidc_issuer": "https://issuer.example.test",
        "oidc_client_id": "cs2-coach",
        "oidc_authorization_endpoint": "https://issuer.example.test/authorize",
        "oidc_token_endpoint": "https://issuer.example.test/token",
        "oidc_jwks_url": "https://issuer.example.test/jwks",
        "oidc_redirect_uri": "https://coach.example.test/auth/oidc/callback",
        "backend_public_url": "https://coach.example.test",
        "frontend_public_url": "https://coach.example.test",
        "auth_cookie_secure": True,
        "cors_origins_raw": "https://coach.example.test",
        "render_worker_token": "test-worker-secret-that-is-not-a-default",
    }


class OwnerIdentityMappingTest(unittest.TestCase):
    def test_verified_subject_maps_to_a_stable_bounded_opaque_owner_id(self) -> None:
        owner_id = derive_owner_id("https://issuer.example.test", "external-user-123")

        self.assertEqual(
            owner_id,
            derive_owner_id("https://issuer.example.test", "external-user-123"),
        )
        self.assertTrue(owner_id.startswith("owner_v1_"))
        self.assertLessEqual(len(owner_id), 64)
        self.assertNotIn("external-user-123", owner_id)
        self.assertNotIn("issuer.example.test", owner_id)

    def test_owner_mapping_rejects_blank_verified_identity_claims(self) -> None:
        for issuer, subject in (("", "subject"), ("https://issuer.example.test", ""), (" ", "x")):
            with self.subTest(issuer=issuer, subject=subject):
                with self.assertRaises(ValueError):
                    derive_owner_id(issuer, subject)


class OidcBrowserSessionTest(unittest.TestCase):
    def test_login_redirect_uses_state_nonce_and_pkce_without_exposing_secrets(self) -> None:
        service = AuthService(Settings(**valid_production_settings_kwargs()), FakeRedis())
        client = auth_client(service)

        response = client.get("/auth/login?return_to=/dashboard", follow_redirects=False)

        self.assertEqual(response.status_code, 302)
        location = response.headers["location"]
        query = parse_qs(urlparse(location).query)
        self.assertEqual(location.split("?", 1)[0], "https://issuer.example.test/authorize")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["cs2-coach"])
        self.assertEqual(
            query["redirect_uri"],
            ["https://coach.example.test/auth/oidc/callback"],
        )
        self.assertEqual(query["scope"], ["openid"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(len(query["state"][0]), 43)
        self.assertEqual(len(query["nonce"][0]), 43)
        self.assertEqual(len(query["code_challenge"][0]), 43)
        self.assertNotIn("client_secret", query)
        self.assertNotIn("token", location.lower())
        set_cookie = response.headers["set-cookie"]
        self.assertIn("__Host-cs2_oidc_state=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        self.assertIn("Path=/", set_cookie)

    def test_login_does_not_persist_an_oversized_return_target(self) -> None:
        redis = FakeRedis()
        service = AuthService(Settings(**valid_production_settings_kwargs()), redis)

        service.begin_login("/" + ("x" * 10_000))

        attempt = json.loads(next(iter(redis.values.values())))
        self.assertEqual(attempt["returnTo"], "/dashboard")
        self.assertLess(len(next(iter(redis.values.values()))), 512)

    def test_production_owner_dependency_rejects_anonymous_and_dev_header_only_requests(self) -> None:
        service = AuthService(Settings(**valid_production_settings_kwargs()), FakeRedis())
        client = auth_client(service)

        anonymous = client.get("/owned")
        dev_header = client.get("/owned", headers={"X-Dev-User-Id": "owner-b"})

        self.assertEqual(anonymous.status_code, 401)
        self.assertEqual(dev_header.status_code, 401)

    def test_explicit_development_and_test_modes_expose_a_local_authenticated_session(self) -> None:
        for auth_mode in ("development", "test"):
            with self.subTest(auth_mode=auth_mode):
                service = AuthService(Settings(auth_mode=auth_mode), FakeRedis())
                client = auth_client(service)

                session = client.get("/auth/session")
                owned = client.get("/owned", headers={"X-Dev-User-Id": "local-owner"})

                self.assertEqual(session.status_code, 200)
                self.assertEqual(session.json(), {"authenticated": True})
                self.assertEqual(owned.json(), {"owner_id": "local-owner"})

    def test_non_production_login_does_not_build_a_redirect_from_empty_oidc_configuration(self) -> None:
        service = AuthService(Settings(auth_mode="development"), FakeRedis())
        client = auth_client(service)

        response = client.get("/auth/login", follow_redirects=False)

        self.assertEqual(response.status_code, 409)
        self.assertNotIn("location", response.headers)

    def test_verified_session_identity_cannot_be_overridden_by_the_dev_header(self) -> None:
        key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        oidc = FakeOidcHttp(key)
        service = AuthService(
            Settings(**valid_production_settings_kwargs()),
            FakeRedis(),
            http_client=oidc,
        )
        client = auth_client(service)
        login = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlparse(login.headers["location"]).query)
        oidc.id_token = make_id_token(key, nonce=query["nonce"][0])
        callback = client.get(
            "/auth/oidc/callback",
            params={"code": "one-time-code", "state": query["state"][0]},
            follow_redirects=False,
        )
        self.assertEqual(callback.status_code, 303)

        owned = client.get("/owned", headers={"X-Dev-User-Id": "owner-b"})

        self.assertEqual(owned.status_code, 200)
        self.assertEqual(
            owned.json(),
            {"owner_id": derive_owner_id("https://issuer.example.test", "external-user-123")},
        )

    def test_logout_requires_a_trusted_origin_and_revokes_the_browser_session(self) -> None:
        key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        oidc = FakeOidcHttp(key)
        service = AuthService(
            Settings(**valid_production_settings_kwargs()),
            FakeRedis(),
            http_client=oidc,
        )
        client = auth_client(service)
        login = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlparse(login.headers["location"]).query)
        oidc.id_token = make_id_token(key, nonce=query["nonce"][0])
        client.get(
            "/auth/oidc/callback",
            params={"code": "one-time-code", "state": query["state"][0]},
            follow_redirects=False,
        )

        missing_origin = client.post("/auth/logout")

        self.assertEqual(missing_origin.status_code, 403)
        self.assertEqual(client.get("/auth/session").status_code, 200)

        logout = client.post(
            "/auth/logout",
            headers={"Origin": "https://coach.example.test"},
        )

        self.assertEqual(logout.status_code, 204)
        self.assertIn("__Host-cs2_session=", logout.headers["set-cookie"])
        self.assertIn("Max-Age=0", logout.headers["set-cookie"])
        self.assertEqual(client.get("/auth/session").status_code, 401)
        self.assertEqual(client.get("/owned").status_code, 401)

    def test_cookie_authenticated_mutations_require_an_exact_trusted_origin(self) -> None:
        key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        oidc = FakeOidcHttp(key)
        service = AuthService(
            Settings(**valid_production_settings_kwargs()),
            FakeRedis(),
            http_client=oidc,
        )
        client = auth_client(service)
        login = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlparse(login.headers["location"]).query)
        oidc.id_token = make_id_token(key, nonce=query["nonce"][0])
        client.get(
            "/auth/oidc/callback",
            params={"code": "one-time-code", "state": query["state"][0]},
            follow_redirects=False,
        )

        missing = client.post("/mutate")
        wrong = client.post("/mutate", headers={"Origin": "https://attacker.example.test"})
        allowed = client.post("/mutate", headers={"Origin": "https://coach.example.test"})

        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_stale_configured_origin_cannot_use_the_production_session(self) -> None:
        key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        oidc = FakeOidcHttp(key)
        values = valid_production_settings_kwargs()
        values["cors_origins_raw"] = (
            "https://coach.example.test,https://stale.example.test"
        )
        service = AuthService(Settings(**values), FakeRedis(), http_client=oidc)
        client = auth_client(service)
        login = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlparse(login.headers["location"]).query)
        oidc.id_token = make_id_token(key, nonce=query["nonce"][0])
        client.get(
            "/auth/oidc/callback",
            params={"code": "one-time-code", "state": query["state"][0]},
            follow_redirects=False,
        )

        response = client.post(
            "/mutate",
            headers={"Origin": "https://stale.example.test"},
        )

        self.assertEqual(response.status_code, 403)

    def test_expired_and_unknown_opaque_sessions_are_rejected(self) -> None:
        key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        oidc = FakeOidcHttp(key)
        redis = FakeRedis()
        service = AuthService(
            Settings(**valid_production_settings_kwargs()),
            redis,
            http_client=oidc,
        )
        client = auth_client(service)
        client.cookies.set("__Host-cs2_session", "unknown-session")
        self.assertEqual(client.get("/owned").status_code, 401)
        self.assertEqual(client.post("/mutate").status_code, 401)
        client.cookies.delete("__Host-cs2_session")

        login = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlparse(login.headers["location"]).query)
        oidc.id_token = make_id_token(key, nonce=query["nonce"][0])
        client.get(
            "/auth/oidc/callback",
            params={"code": "one-time-code", "state": query["state"][0]},
            follow_redirects=False,
        )
        session_key = next(key for key in redis.values if key.startswith("auth:session:"))
        session_record = json.loads(redis.values[session_key])
        session_record["expiresAt"] = int(time.time()) - 1
        redis.values[session_key] = json.dumps(session_record)

        self.assertEqual(client.get("/auth/session").status_code, 401)
        self.assertNotIn(session_key, redis.values)

    def test_malformed_opaque_values_fail_closed_without_reaching_redis(self) -> None:
        service = AuthService(Settings(**valid_production_settings_kwargs()), FakeRedis())

        self.assertIsNone(service.resolve_session("not-ascii-☃"))
        service.revoke_session("not-ascii-☃")
        with self.assertRaises(AuthenticationError):
            service.complete_login("one-time-code", "not-ascii-☃", "not-ascii-☃")

    def test_valid_callback_creates_an_opaque_session_without_exposing_identity(self) -> None:
        key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        oidc = FakeOidcHttp(key)
        redis = FakeRedis()
        service = AuthService(
            Settings(**valid_production_settings_kwargs()),
            redis,
            http_client=oidc,
        )
        client = auth_client(service)
        login = client.get("/auth/login?return_to=/dashboard", follow_redirects=False)
        login_query = parse_qs(urlparse(login.headers["location"]).query)
        oidc.id_token = make_id_token(key, nonce=login_query["nonce"][0])

        callback = client.get(
            "/auth/oidc/callback",
            params={"code": "one-time-code", "state": login_query["state"][0]},
            follow_redirects=False,
        )

        self.assertEqual(callback.status_code, 303)
        self.assertEqual(
            callback.headers["location"],
            "https://coach.example.test/auth/callback?return_to=%2Fdashboard",
        )
        callback_cookies = callback.headers.get_list("set-cookie")
        state_clear_cookie = next(
            cookie for cookie in callback_cookies if cookie.startswith("__Host-cs2_oidc_state=")
        )
        self.assertIn("Max-Age=0", state_clear_cookie)
        self.assertIn("HttpOnly", state_clear_cookie)
        self.assertIn("Secure", state_clear_cookie)
        set_cookie = callback.headers["set-cookie"]
        self.assertIn("__Host-cs2_session=", set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("SameSite=lax", set_cookie)
        session = client.get("/auth/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json(), {"authenticated": True})
        session_token = client.cookies.get("__Host-cs2_session")
        self.assertIsNotNone(session_token)
        self.assertNotIn(str(session_token), json.dumps(redis.values))
        self.assertNotIn("external-user-123", json.dumps(redis.values))
        self.assertNotIn(oidc.id_token, json.dumps(redis.values))

    def test_callback_rejects_a_state_that_does_not_match_the_browser_cookie(self) -> None:
        key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
        oidc = FakeOidcHttp(key)
        redis = FakeRedis()
        service = AuthService(
            Settings(**valid_production_settings_kwargs()),
            redis,
            http_client=oidc,
        )
        client = auth_client(service)
        login = client.get("/auth/login", follow_redirects=False)
        query = parse_qs(urlparse(login.headers["location"]).query)
        oidc.id_token = make_id_token(key, nonce=query["nonce"][0])

        callback = client.get(
            "/auth/oidc/callback",
            params={"code": "one-time-code", "state": "attacker-state"},
            follow_redirects=False,
        )

        self.assertEqual(callback.status_code, 401)
        self.assertFalse(any(key.startswith("auth:session:") for key in redis.values))

    def test_callback_rejects_untrusted_or_invalid_identity_tokens(self) -> None:
        now = int(time.time())
        cases = (
            ("wrong issuer", {"iss": "https://attacker.example.test"}, ()),
            ("wrong audience", {"aud": "another-client"}, ()),
            ("expired", {"exp": now - 1}, ()),
            ("not yet valid", {"nbf": now + 300}, ()),
            ("issued in the future", {"iat": now + 300}, ()),
            ("missing issuer", {}, ("iss",)),
            ("missing subject", {}, ("sub",)),
            ("missing audience", {}, ("aud",)),
            ("missing issued-at", {}, ("iat",)),
            ("missing expiry", {}, ("exp",)),
            ("nonce mismatch", {"nonce": "wrong-nonce"}, ()),
            ("wrong authorized party", {"azp": "another-client"}, ()),
            ("multi-audience without azp", {"aud": ["cs2-coach", "other"]}, ()),
        )
        for label, overrides, drop_claims in cases:
            with self.subTest(label=label):
                key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
                oidc = FakeOidcHttp(key)
                redis = FakeRedis()
                service = AuthService(
                    Settings(**valid_production_settings_kwargs()),
                    redis,
                    http_client=oidc,
                )
                client = auth_client(service)
                login = client.get("/auth/login", follow_redirects=False)
                query = parse_qs(urlparse(login.headers["location"]).query)
                token_overrides = dict(overrides)
                token_nonce = str(token_overrides.pop("nonce", query["nonce"][0]))
                oidc.id_token = make_id_token(
                    key,
                    nonce=token_nonce,
                    drop_claims=drop_claims,
                    **token_overrides,
                )

                callback = client.get(
                    "/auth/oidc/callback",
                    params={"code": "one-time-code", "state": query["state"][0]},
                    follow_redirects=False,
                )

                self.assertEqual(callback.status_code, 401)
                self.assertFalse(any(key.startswith("auth:session:") for key in redis.values))

    def test_callback_rejects_wrong_keys_unknown_key_ids_and_unsigned_tokens(self) -> None:
        for label in ("wrong signature", "unknown key id", "unsigned"):
            with self.subTest(label=label):
                trusted_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
                oidc = FakeOidcHttp(trusted_key)
                redis = FakeRedis()
                service = AuthService(
                    Settings(**valid_production_settings_kwargs()),
                    redis,
                    http_client=oidc,
                )
                client = auth_client(service)
                login = client.get("/auth/login", follow_redirects=False)
                query = parse_qs(urlparse(login.headers["location"]).query)
                if label == "wrong signature":
                    signing_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
                    oidc.id_token = make_id_token(signing_key, nonce=query["nonce"][0])
                elif label == "unknown key id":
                    oidc.id_token = make_id_token(
                        trusted_key,
                        nonce=query["nonce"][0],
                        key_id="unknown-key",
                    )
                else:
                    oidc.id_token = make_id_token(
                        "",
                        nonce=query["nonce"][0],
                        algorithm="none",
                    )

                callback = client.get(
                    "/auth/oidc/callback",
                    params={"code": "one-time-code", "state": query["state"][0]},
                    follow_redirects=False,
                )

                self.assertEqual(callback.status_code, 401)
                self.assertFalse(any(key.startswith("auth:session:") for key in redis.values))

    def test_login_never_redirects_the_frontend_callback_to_an_external_destination(self) -> None:
        unsafe_return_targets = (
            "https://attacker.example.test/steal",
            "//attacker.example.test/steal",
            "/\\attacker.example.test/steal",
            "javascript:alert(1)",
        )
        for return_to in unsafe_return_targets:
            with self.subTest(return_to=return_to):
                key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
                oidc = FakeOidcHttp(key)
                service = AuthService(
                    Settings(**valid_production_settings_kwargs()),
                    FakeRedis(),
                    http_client=oidc,
                )
                client = auth_client(service)
                login = client.get(
                    "/auth/login",
                    params={"return_to": return_to},
                    follow_redirects=False,
                )
                query = parse_qs(urlparse(login.headers["location"]).query)
                oidc.id_token = make_id_token(key, nonce=query["nonce"][0])

                callback = client.get(
                    "/auth/oidc/callback",
                    params={"code": "one-time-code", "state": query["state"][0]},
                    follow_redirects=False,
                )

                self.assertEqual(callback.status_code, 303)
                self.assertEqual(
                    callback.headers["location"],
                    "https://coach.example.test/auth/callback?return_to=%2Fdashboard",
                )


class ProductionUserRouteAuthenticationMatrixTest(unittest.TestCase):
    def test_cookie_authenticated_responses_disable_shared_caching(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        owner_id = derive_owner_id("https://issuer.example.test", "cache-owner")
        with Session() as db:
            db.add(make_demo("demo-cache-owner", owner_id))
            db.commit()
        client = authenticated_user_api_client("cache-owner", Session)

        for path in (
            "/auth/session",
            "/demos",
            "/demos/demo-cache-owner/status",
            "/demos/demo-cache-owner/video",
            "/demos/demo-cache-owner/replay",
            "/demos/demo-cache-owner/coaching",
            "/demos/demo-cache-owner/diagnostics",
            "/demos/demo-cache-owner/render/jobs",
        ):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(
                    response.headers.get("cache-control"),
                    "private, no-store",
                )
                vary = {
                    value.strip().lower()
                    for value in response.headers.get("vary", "").split(",")
                    if value.strip()
                }
                self.assertTrue({"cookie", "origin"}.issubset(vary))

        engine.dispose()

    def test_every_user_route_rejects_anonymous_invalid_and_expired_sessions(self) -> None:
        requests = (
            ("GET", "/demos", {}),
            ("GET", "/demos/demo-a/status", {}),
            ("GET", "/demos/demo-a/replay", {}),
            ("GET", "/demos/demo-a/coaching", {}),
            ("GET", "/demos/demo-a/diagnostics", {}),
            ("PATCH", "/demos/demo-a", {"json": {"name": "Renamed"}}),
            ("PATCH", "/demos/demo-a", {"json": {"archived": False}}),
            ("POST", "/demos/demo-a/archive", {}),
            ("POST", "/uploads/mock", {}),
            (
                "POST",
                "/uploads/demo",
                {"files": {"file": ("sample.dem", b"demo", "application/octet-stream")}},
            ),
            ("POST", "/demos/demo-a/parse/retry", {}),
            ("GET", "/demos/demo-a/video", {}),
            (
                "POST",
                "/demos/demo-a/video/upload",
                {"files": {"file": ("clip.mp4", b"video", "video/mp4")}},
            ),
            (
                "POST",
                "/demos/demo-a/video/calibration",
                {"json": {"tickStart": 0, "tickEnd": 640, "tickRate": 64}},
            ),
            ("POST", "/demos/demo-a/render/mock", {}),
            (
                "POST",
                "/demos/demo-a/render/clip",
                {"json": {"tickStart": 0, "tickEnd": 640, "tickRate": 64}},
            ),
            ("GET", "/demos/demo-a/render/jobs", {}),
            ("GET", "/demos/demo-a/media/video", {}),
        )
        for actor in ("anonymous", "invalid", "expired"):
            service = AuthService(Settings(**valid_production_settings_kwargs()), FakeRedis())
            client = user_api_client(service)
            if actor == "invalid":
                client.cookies.set("__Host-cs2_session", "random-invalid-session")
            elif actor == "expired":
                token = "expired-session"
                key = "auth:session:" + hashlib.sha256(token.encode("ascii")).hexdigest()
                service.redis.setex(
                    key,
                    300,
                    json.dumps(
                        {
                            "ownerId": derive_owner_id(
                                "https://issuer.example.test", "external-user-123"
                            ),
                            "expiresAt": int(time.time()) - 1,
                        }
                    ),
                )
                client.cookies.set("__Host-cs2_session", token)

            for method, path, kwargs in requests:
                with self.subTest(actor=actor, method=method, path=path, kwargs=kwargs):
                    response = client.request(method, path, **kwargs)
                    self.assertEqual(response.status_code, 401)

    def test_verified_owner_a_cannot_be_switched_to_or_read_owner_b(self) -> None:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine)
        Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        owner_a = derive_owner_id("https://issuer.example.test", "subject-a")
        owner_b = derive_owner_id("https://issuer.example.test", "subject-b")
        with Session() as db:
            db.add(make_demo("demo-a", owner_a))
            db.add(make_demo("demo-b", owner_b))
            db.commit()

        client_a = authenticated_user_api_client("subject-a", Session)
        client_b = authenticated_user_api_client("subject-b", Session)

        owner_a_list = client_a.get("/demos", headers={"X-Dev-User-Id": owner_b})
        owner_b_reads_a = client_b.get("/demos/demo-a/status")

        self.assertEqual(owner_a_list.status_code, 200)
        self.assertEqual([item["id"] for item in owner_a_list.json()], ["demo-a"])
        self.assertEqual(owner_b_reads_a.status_code, 404)
        engine.dispose()

    def test_user_session_cannot_replace_the_render_worker_service_credential(self) -> None:
        redis = FakeRedis()
        service = AuthService(Settings(**valid_production_settings_kwargs()), redis)
        token = "valid-user-session"
        redis.setex(
            "auth:session:" + hashlib.sha256(token.encode("ascii")).hexdigest(),
            300,
            json.dumps(
                {
                    "ownerId": derive_owner_id(
                        "https://issuer.example.test", "external-user-123"
                    ),
                    "expiresAt": int(time.time()) + 300,
                }
            ),
        )
        client = user_api_client(service)
        client.cookies.set("__Host-cs2_session", token)

        response = client.get("/render-worker/jobs/next")

        self.assertEqual(response.status_code, 401)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def setex(self, key: str, _: int, value: str) -> None:
        self.values[key] = value

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def getdel(self, key: str) -> str | None:
        return self.values.pop(key, None)

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict[str, object]:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeOidcHttp:
    def __init__(self, key) -> None:
        self.id_token = ""
        public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
        public_jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
        self.jwks = {"keys": [public_jwk]}

    def post(self, _url: str, **_kwargs) -> FakeHttpResponse:
        return FakeHttpResponse({"id_token": self.id_token})

    def get(self, _url: str, **_kwargs) -> FakeHttpResponse:
        return FakeHttpResponse(self.jwks)


def make_id_token(
    key,
    *,
    nonce: str,
    drop_claims: tuple[str, ...] = (),
    algorithm: str = "RS256",
    key_id: str = "test-key",
    **claim_overrides: object,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "iss": "https://issuer.example.test",
        "sub": "external-user-123",
        "aud": "cs2-coach",
        "iat": now,
        "exp": now + 300,
        "nonce": nonce,
    }
    claims.update(claim_overrides)
    for claim in drop_claims:
        claims.pop(claim, None)
    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": key_id})


def auth_client(service: AuthService) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        SessionCsrfMiddleware,
        runtime_settings=service.settings,
        auth_service_factory=lambda: service,
    )
    app.include_router(auth_api.router)

    @app.get("/owned")
    def owned(owner_id: str = Depends(get_current_owner_id)) -> dict[str, str]:
        return {"owner_id": owner_id}

    @app.post("/mutate")
    def mutate(owner_id: str = Depends(get_current_owner_id)) -> dict[str, str]:
        return {"owner_id": owner_id}

    app.dependency_overrides[get_auth_service] = lambda: service
    return TestClient(app, base_url="https://coach.example.test")


def user_api_client(service: AuthService, session_factory=None) -> TestClient:
    app = FastAPI()
    app.add_middleware(
        SessionCsrfMiddleware,
        runtime_settings=service.settings,
        auth_service_factory=lambda: service,
    )
    app.include_router(auth_api.router)
    app.include_router(demos.router)
    app.include_router(uploads.router)
    app.include_router(replay.router)
    app.include_router(coaching.router)
    app.include_router(diagnostics.router)
    app.include_router(private_media.router)
    app.dependency_overrides[get_auth_service] = lambda: service

    def override_get_db():
        if session_factory is None:
            yield object()
            return
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app, base_url="https://coach.example.test")


def authenticated_user_api_client(subject: str, session_factory) -> TestClient:
    key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    oidc = FakeOidcHttp(key)
    service = AuthService(
        Settings(**valid_production_settings_kwargs()),
        FakeRedis(),
        http_client=oidc,
    )
    client = user_api_client(service, session_factory)
    login = client.get("/auth/login", follow_redirects=False)
    query = parse_qs(urlparse(login.headers["location"]).query)
    oidc.id_token = make_id_token(key, nonce=query["nonce"][0], sub=subject)
    callback = client.get(
        "/auth/oidc/callback",
        params={"code": "one-time-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    if callback.status_code != 303:
        raise AssertionError(f"OIDC callback failed with {callback.status_code}")
    return client


def make_demo(demo_id: str, owner_id: str) -> Demo:
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    return Demo(
        id=demo_id,
        owner_id=owner_id,
        legacy_user_id=owner_id,
        name=f"Demo {demo_id}",
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
