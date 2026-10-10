import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth, unified
from app.core.config import settings
from app.core.database import Base, get_db
from app.models import auth as auth_models
from app.models import destination, hotel, knowledge, unified as unified_models  # noqa: F401
from app.services.auth import _hash
from app.services.auth_throttle import auth_throttle


PASSWORD = "correct horse battery staple"


class AuthFoundationTests(unittest.TestCase):
    def setUp(self):
        auth_throttle.clear()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.Session = sessionmaker(bind=self.engine)
        Base.metadata.create_all(self.engine)
        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(unified.router)

        def override_db():
            db = self.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.app = app
        self.client = TestClient(app)
        self.original_google = (
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
            settings.app_env,
        )

    def tearDown(self):
        (
            settings.google_client_id,
            settings.google_client_secret,
            settings.google_redirect_uri,
            settings.app_env,
        ) = self.original_google
        self.engine.dispose()

    def _session(self, client=None):
        client = client or self.client
        response = client.get("/api/v1/auth/session")
        self.assertEqual(response.status_code, 200)
        return response.json()["csrf_token"]

    def _signup(self, email="guest@example.com", client=None):
        client = client or self.client
        csrf = self._session(client)
        response = client.post(
            "/api/v1/auth/signup",
            headers={"X-CSRF-Token": csrf},
            json={
                "email": email,
                "password": PASSWORD,
                "display_name": "Guest",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response

    def _conversation(self, client=None, csrf=None):
        client = client or self.client
        csrf = csrf or self._session(client)
        response = client.post(
            "/api/v1/conversations",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _google_start(self, client=None):
        client = client or self.client
        settings.google_client_id = "google-client"
        settings.google_client_secret = "google-secret"
        self._session(client)
        response = client.get("/api/v1/auth/google/start", follow_redirects=False)
        self.assertEqual(response.status_code, 302, response.text)
        state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
        db = self.Session()
        try:
            row = db.scalar(
                select(auth_models.OAuthState).where(
                    auth_models.OAuthState.state_hash == _hash(state)
                )
            )
            nonce = row.nonce
        finally:
            db.close()
        return state, nonce

    def test_signup_hashes_password_and_duplicate_normalized_email_is_safe(self):
        response = self._signup("Person@Example.COM")
        self.assertEqual(response.json()["user"]["email"], "person@example.com")
        db = self.Session()
        try:
            user = db.query(auth_models.User).one()
            credential = db.get(auth_models.PasswordCredential, user.id)
            self.assertNotEqual(credential.password_hash, PASSWORD)
            self.assertTrue(credential.password_hash.startswith("$argon2id$"))
        finally:
            db.close()

        other = TestClient(self.app)
        csrf = self._session(other)
        duplicate = other.post(
            "/api/v1/auth/signup",
            headers={"X-CSRF-Token": csrf},
            json={"email": "PERSON@example.com", "password": PASSWORD},
        )
        self.assertEqual(duplicate.status_code, 409)
        db = self.Session()
        try:
            self.assertEqual(db.query(auth_models.User).count(), 1)
        finally:
            db.close()

    def test_login_is_generic_and_rotates_session(self):
        signup = self._signup()
        signed_in_cookie = self.client.cookies.get(settings.auth_cookie_name)
        logout = self.client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": signup.json()["csrf_token"]},
        )
        guest_cookie = self.client.cookies.get(settings.auth_cookie_name)
        self.assertNotEqual(signed_in_cookie, guest_cookie)

        wrong = self.client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": logout.json()["csrf_token"]},
            json={"email": "missing@example.com", "password": "wrong"},
        )
        wrong_known = self.client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": logout.json()["csrf_token"]},
            json={"email": "guest@example.com", "password": "wrong"},
        )
        self.assertEqual(wrong.status_code, wrong_known.status_code)
        self.assertEqual(wrong.json(), wrong_known.json())

        login = self.client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": logout.json()["csrf_token"]},
            json={"email": "guest@example.com", "password": PASSWORD},
        )
        self.assertEqual(login.status_code, 200)
        self.assertNotEqual(guest_cookie, self.client.cookies.get(settings.auth_cookie_name))

    def test_repeated_failed_login_uses_central_throttle(self):
        csrf = self._session()
        statuses = []
        for _ in range(settings.auth_login_max_attempts + 1):
            response = self.client.post(
                "/api/v1/auth/login",
                headers={"X-CSRF-Token": csrf},
                json={"email": "rate@example.com", "password": "wrong"},
            )
            statuses.append(response.status_code)
        self.assertEqual(statuses[:-1], [401] * settings.auth_login_max_attempts)
        self.assertEqual(statuses[-1], 429)

    def test_session_cookie_is_opaque_hashed_and_has_safe_flags(self):
        response = self.client.get("/api/v1/auth/session")
        raw = self.client.cookies.get(settings.auth_cookie_name)
        session_cookie = next(
            value
            for value in response.headers.get_list("set-cookie")
            if value.startswith(f"{settings.auth_cookie_name}=")
        )
        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("SameSite=lax", session_cookie)
        self.assertIn("Path=/", session_cookie)
        db = self.Session()
        try:
            row = db.query(auth_models.AppSession).one()
            self.assertEqual(row.token_hash, _hash(raw))
            self.assertNotEqual(row.token_hash, raw)
        finally:
            db.close()

        original_secure = settings.auth_cookie_secure
        settings.auth_cookie_secure = True
        try:
            secure_client = TestClient(self.app, base_url="https://testserver")
            secure_response = secure_client.get("/api/v1/auth/session")
            secure_cookie = next(
                value
                for value in secure_response.headers.get_list("set-cookie")
                if value.startswith(f"{settings.auth_cookie_name}=")
            )
            self.assertIn("Secure", secure_cookie)
        finally:
            settings.auth_cookie_secure = original_secure

    def test_auth_capabilities_are_coherent_and_hide_sensitive_config(self):
        settings.google_client_id = ""
        settings.google_client_secret = ""
        unavailable = self.client.get("/api/v1/auth/session").json()
        self.assertEqual(
            unavailable["capabilities"],
            {"password": True, "google": False, "password_recovery": False},
        )
        self.assertNotIn("client", str(unavailable).lower())
        self.assertNotIn("secret", str(unavailable).lower())

        settings.google_client_id = "partial-client"
        partial = self.client.get("/api/v1/auth/session").json()
        self.assertFalse(partial["capabilities"]["google"])

        settings.google_client_secret = "configured-secret"
        settings.google_redirect_uri = "http://localhost:8000/api/v1/auth/google/callback"
        configured = self.client.get("/api/v1/auth/session").json()
        self.assertTrue(configured["capabilities"]["google"])

        settings.app_env = "production"
        self.assertFalse(settings.google_auth_available)

    def test_csrf_is_required_for_conversation_writes(self):
        csrf = self._session()
        missing = self.client.post("/api/v1/conversations", json={})
        wrong = self.client.post(
            "/api/v1/conversations",
            headers={"X-CSRF-Token": "wrong"},
            json={},
        )
        valid = self.client.post(
            "/api/v1/conversations",
            headers={"X-CSRF-Token": csrf},
            json={},
        )
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(wrong.status_code, 403)
        self.assertEqual(valid.status_code, 200)

        stay_missing = self.client.post(
            "/api/v1/stays/connect",
            json={"connect_code": "unknown"},
        )
        stay_wrong = self.client.post(
            "/api/v1/stays/connect",
            headers={"X-CSRF-Token": "wrong"},
            json={"connect_code": "unknown"},
        )
        self.assertEqual(stay_missing.status_code, 403)
        self.assertEqual(stay_wrong.status_code, 403)

    def test_guest_and_user_conversation_ownership_fail_closed(self):
        guest_a = TestClient(self.app)
        guest_b = TestClient(self.app)
        conversation = self._conversation(guest_a)
        self._session(guest_b)
        self.assertEqual(
            guest_b.get(f"/api/v1/conversations/{conversation['id']}").status_code,
            404,
        )

        signup_a = self._signup("a@example.com", guest_a)
        user_b = TestClient(self.app)
        signup_b = self._signup("b@example.com", user_b)
        self.assertEqual(
            user_b.get(f"/api/v1/conversations/{conversation['id']}").status_code,
            404,
        )
        consent = user_b.put(
            f"/api/v1/conversations/{conversation['id']}/consents",
            headers={"X-CSRF-Token": signup_b.json()["csrf_token"]},
            json={"purpose": "USAGE_ANALYTICS", "status": "GRANTED"},
        )
        stream = user_b.post(
            f"/api/v1/conversations/{conversation['id']}/messages/stream",
            headers={"X-CSRF-Token": signup_b.json()["csrf_token"]},
            json={"message": "hello", "mode": "TRAVEL"},
        )
        self.assertEqual(consent.status_code, 404)
        self.assertEqual(stream.status_code, 404)
        self.assertEqual(
            guest_a.get(f"/api/v1/conversations/{conversation['id']}").status_code,
            200,
        )
        self.assertTrue(signup_a.json()["csrf_token"])

    def test_guest_promotion_claims_only_current_session_and_logout_isolates(self):
        csrf = self._session()
        old_cookie = self.client.cookies.get(settings.auth_cookie_name)
        own = self._conversation(csrf=csrf)
        other = TestClient(self.app)
        other_conversation = self._conversation(other)
        signup = self.client.post(
            "/api/v1/auth/signup",
            headers={"X-CSRF-Token": csrf},
            json={"email": "owner@example.com", "password": PASSWORD},
        )
        self.assertEqual(signup.status_code, 201)
        self.assertNotEqual(old_cookie, self.client.cookies.get(settings.auth_cookie_name))
        self.assertEqual(
            self.client.get(f"/api/v1/conversations/{own['id']}").status_code,
            200,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/conversations/{other_conversation['id']}"
            ).status_code,
            404,
        )
        logout = self.client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": signup.json()["csrf_token"]},
        )
        self.assertEqual(logout.status_code, 200)
        self.assertFalse(logout.json()["authenticated"])
        self.assertEqual(
            self.client.get(f"/api/v1/conversations/{own['id']}").status_code,
            404,
        )

    def test_logged_in_user_still_needs_stay_grant_for_hotel_mode(self):
        signup = self._signup()
        conversation = self._conversation(csrf=signup.json()["csrf_token"])
        response = self.client.post(
            f"/api/v1/conversations/{conversation['id']}/messages/stream",
            headers={"X-CSRF-Token": signup.json()["csrf_token"]},
            json={"message": "Wi-Fi?", "mode": "HOTEL"},
        )
        self.assertEqual(response.status_code, 403)

    def test_google_state_mismatch_rejected_before_exchange(self):
        state, _ = self._google_start()
        with patch("app.api.auth.google_exchange", new=AsyncMock()) as exchange:
            response = self.client.get(
                f"/api/v1/auth/google/callback?code=x&state={state}wrong",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 400)
        exchange.assert_not_awaited()

    def test_google_cancel_returns_recoverable_ui_state(self):
        state, _ = self._google_start()
        with patch("app.api.auth.google_exchange", new=AsyncMock()) as exchange:
            response = self.client.get(
                f"/api/v1/auth/google/callback?error=access_denied&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertIn("GOOGLE_CANCELLED", response.headers["location"])
        exchange.assert_not_awaited()

    def test_invalid_google_token_and_unverified_email_are_rejected(self):
        state, _ = self._google_start()
        with patch(
            "app.api.auth.google_exchange",
            new=AsyncMock(side_effect=HTTPException(400, "Invalid Google ID token")),
        ):
            invalid = self.client.get(
                f"/api/v1/auth/google/callback?code=x&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(invalid.status_code, 400)

        state, _ = self._google_start()
        with patch(
            "app.api.auth.google_exchange",
            new=AsyncMock(side_effect=HTTPException(400, "Expired Google ID token")),
        ):
            expired = self.client.get(
                f"/api/v1/auth/google/callback?code=x&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(expired.status_code, 400)

        state, nonce = self._google_start()
        with patch(
            "app.api.auth.google_exchange",
            new=AsyncMock(
                return_value={
                    "sub": "sub-unverified",
                    "email": "g@example.com",
                    "email_verified": False,
                    "nonce": nonce,
                }
            ),
        ):
            unverified = self.client.get(
                f"/api/v1/auth/google/callback?code=x&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(unverified.status_code, 400)

    def test_google_identity_login_and_no_silent_email_link(self):
        db = self.Session()
        user = auth_models.User(
            email="google@example.com",
            email_normalized="google@example.com",
            status="active",
            email_verified_at=auth_models.utcnow(),
        )
        db.add(user)
        db.flush()
        db.add(
            auth_models.OAuthIdentity(
                user_id=user.id,
                provider="google",
                provider_subject="known-sub",
                provider_email=user.email,
            )
        )
        db.commit()
        expected_user_id = user.id
        db.close()

        state, nonce = self._google_start()
        claims = {
            "sub": "known-sub",
            "email": "google@example.com",
            "email_verified": True,
            "nonce": nonce,
        }
        with patch("app.api.auth.google_exchange", new=AsyncMock(return_value=claims)):
            callback = self.client.get(
                f"/api/v1/auth/google/callback?code=x&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(callback.status_code, 303)
        self.assertEqual(self.client.get("/api/v1/auth/me").json()["user"]["id"], expected_user_id)

        local = TestClient(self.app)
        self._signup("local@example.com", local)
        logout_csrf = local.get("/api/v1/auth/me").json()["csrf_token"]
        local.post("/api/v1/auth/logout", headers={"X-CSRF-Token": logout_csrf})
        state, nonce = self._google_start(local)
        collision = {
            "sub": "new-sub",
            "email": "local@example.com",
            "email_verified": True,
            "nonce": nonce,
        }
        with patch("app.api.auth.google_exchange", new=AsyncMock(return_value=collision)):
            callback = local.get(
                f"/api/v1/auth/google/callback?code=x&state={state}",
                follow_redirects=False,
            )
        self.assertEqual(callback.status_code, 303)
        self.assertIn("ACCOUNT_LINK_REQUIRED", callback.headers["location"])
        db = self.Session()
        try:
            self.assertIsNone(
                db.scalar(
                    select(auth_models.OAuthIdentity).where(
                        auth_models.OAuthIdentity.provider_subject == "new-sub"
                    )
                )
            )
        finally:
            db.close()

    def test_database_constraint_rejects_duplicate_google_subject(self):
        db = self.Session()
        user = auth_models.User(
            email="one@example.com",
            email_normalized="one@example.com",
        )
        db.add(user)
        db.flush()
        db.add_all(
            [
                auth_models.OAuthIdentity(
                    user_id=user.id,
                    provider="google",
                    provider_subject="same",
                ),
                auth_models.OAuthIdentity(
                    user_id=user.id,
                    provider="google",
                    provider_subject="same",
                ),
            ]
        )
        with self.assertRaises(IntegrityError):
            db.commit()
        db.rollback()
        db.close()


if __name__ == "__main__":
    unittest.main()
