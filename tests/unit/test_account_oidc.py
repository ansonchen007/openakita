from urllib.parse import parse_qs, urlsplit

import pytest

from openakita.account.oidc import CALLBACK_URI, CLIENT_ID, AccountOIDCManager, pkce_challenge


def test_pkce_challenge_rfc7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_loopback_contract_constants() -> None:
    assert CLIENT_ID == "openakita-desktop"
    parsed = urlsplit(CALLBACK_URI)
    assert parsed.hostname == "127.0.0.1"
    assert parsed.port == 1455
    assert parse_qs("state=a&code=b") == {"state": ["a"], "code": ["b"]}


class _TokenStore:
    def __init__(self, token: str | None) -> None:
        self.token = token

    async def load_refresh_token(self) -> str | None:
        return self.token

    async def save_refresh_token(self, token: str) -> None:
        self.token = token

    async def clear(self) -> None:
        self.token = None


class _SnapshotStore:
    async def snapshot(self) -> dict:
        return {"status": "active", "account_user_id": "user-1"}


@pytest.mark.asyncio
async def test_snapshot_requires_refresh_token_even_when_offline_cache_exists() -> None:
    manager = AccountOIDCManager(
        store=_SnapshotStore(),  # type: ignore[arg-type]
        token_store=_TokenStore(None),
    )

    assert await manager.snapshot() == {"status": "signed_out"}
