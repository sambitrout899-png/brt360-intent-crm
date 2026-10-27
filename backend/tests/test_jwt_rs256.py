"""RS256 tokens (app/auth.py): signed with a private key, verified by key id,
public keys published at /api/auth/jwks, keys rotated without logging anyone
out - and the shared-secret (HS256) tokens from before still accepted until
that is switched off. The real auth path: conftest's override is removed."""

import datetime as dt

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app import auth
from app.auth import get_current_user, hash_password
from app.config import get_settings
from app.main import app
from app.models.auth import User
from tests.conftest import DEFAULT_TEST_ADMIN_EMAIL


def _key_files(folder, name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private, public = folder / f"{name}.pem", folder / f"{name}.pub.pem"
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    return str(private), str(public)


def _fresh_settings():
    get_settings.cache_clear()
    auth._signing_key.cache_clear()
    auth._verification_keys.cache_clear()


@pytest.fixture()
def real_auth(client, monkeypatch):
    saved = app.dependency_overrides.pop(get_current_user, None)
    with client.TestSessionLocal() as db:
        db.query(User).filter(User.email == DEFAULT_TEST_ADMIN_EMAIL).one().password_hash = hash_password("pw-123456")
        db.commit()
    yield monkeypatch
    _fresh_settings()
    if saved is not None:
        app.dependency_overrides[get_current_user] = saved


def _login(client):
    response = client.post("/api/auth/login", json={"email": DEFAULT_TEST_ADMIN_EMAIL, "password": "pw-123456"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _me(client, token):
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"}).status_code


def test_tokens_are_rs256_with_a_key_id_and_verifiable_from_jwks(client, real_auth, tmp_path):
    private, _ = _key_files(tmp_path, "current")
    real_auth.setenv("JWT_PRIVATE_KEY_FILE", private)
    _fresh_settings()

    token = _login(client)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "RS256" and header["kid"]
    assert _me(client, token) == 200

    keys = client.get("/api/auth/jwks").json()["keys"]  # public, no login needed
    assert [k["kid"] for k in keys] == [header["kid"]] and "d" not in keys[0]  # never the private part
    public = jwt.algorithms.RSAAlgorithm.from_jwk(keys[0])
    assert jwt.decode(token, public, algorithms=["RS256"])["sub"]  # another service can verify it alone


def test_rotation_keeps_earlier_sessions_valid_and_unknown_keys_out(client, real_auth, tmp_path):
    old_private, old_public = _key_files(tmp_path, "old")
    real_auth.setenv("JWT_PRIVATE_KEY_FILE", old_private)
    _fresh_settings()
    old_token = _login(client)

    new_private, _ = _key_files(tmp_path, "new")
    real_auth.setenv("JWT_PRIVATE_KEY_FILE", new_private)
    real_auth.setenv("JWT_PREVIOUS_PUBLIC_KEY_FILES", old_public)
    _fresh_settings()
    assert _me(client, old_token) == 200  # signed before the rotation: still good
    assert jwt.get_unverified_header(_login(client))["kid"] != jwt.get_unverified_header(old_token)["kid"]
    assert len(client.get("/api/auth/jwks").json()["keys"]) == 2

    real_auth.setenv("JWT_PREVIOUS_PUBLIC_KEY_FILES", "")
    _fresh_settings()
    assert _me(client, old_token) == 401  # the old key retired


def test_shared_secret_tokens_until_switched_off_and_no_key_confusion(client, real_auth, tmp_path):
    private, public = _key_files(tmp_path, "current")
    legacy = _login(client)  # no key configured yet: HS256, as every session before this release
    assert jwt.get_unverified_header(legacy)["alg"] == "HS256"

    real_auth.setenv("JWT_PRIVATE_KEY_FILE", private)
    _fresh_settings()
    assert _me(client, legacy) == 200  # nobody is logged out by the switch

    # a token "signed" HS256 with the (public) key as the secret must not pass - built by
    # hand, as an attacker would (PyJWT itself refuses to make one)
    import base64, hashlib, hmac, json
    def b64(raw):
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
    exp = int((dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5)).timestamp())
    signing_input = f"{b64(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode())}.{b64(json.dumps({'sub': '1', 'exp': exp}).encode())}"
    signature = hmac.new(open(public, "rb").read(), signing_input.encode(), hashlib.sha256).digest()
    assert _me(client, f"{signing_input}.{b64(signature)}") == 401

    real_auth.setenv("JWT_ACCEPT_HS256", "false")
    _fresh_settings()
    assert _me(client, legacy) == 401
    assert _me(client, _login(client)) == 200
