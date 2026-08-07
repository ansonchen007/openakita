# OpenAkita Account PKCE integration

OpenAkita Desktop uses the public OIDC client `openakita-desktop`. It never has
a client secret. The backend opens a temporary loopback listener on
`127.0.0.1:1455`, generates a fresh state and S256 PKCE verifier, and returns
the Account authorization URL to Setup Center.

```text
POST /api/account/login/start
GET  /api/account/login/status/{attempt_id}
GET  /api/account/status
POST /api/account/entitlements/refresh
POST /api/account/logout
```

The registered redirect URI is
`http://127.0.0.1:1455/auth/callback`. Refresh tokens are stored only in the OS
credential store through `keyring`; there is no plaintext file fallback.
Access tokens stay in process memory. A successful login fetches `/oauth/userinfo`
and `/api/v1/me/entitlements`, then persists only the identity and entitlement
read model in `data/account_identity.db`.

Feature gates must evaluate the cached entitlement status and expiry at read
time. A central `suspended` event revokes Account sessions in that database and
all Account-backed operations fail immediately. The existing local web password
remains a separate break-glass path and is not revoked by Account suspension.

Setup Center's **OpenAkita Account** view drives these endpoints, polls the
loopback attempt, shows the cached account/entitlement status, and lets the user
refresh entitlements. Logout clears the OS credential and the UI opens the
Account `end_session_endpoint` so the current browser SSO chain is revoked
across products without affecting another device.

For local integration set:

```text
OPENAKITA_ACCOUNT_BASE_URL=http://127.0.0.1:8088
```

An always-on server may additionally expose the signed D26 receiver at
`POST /api/internal/openakita/users/status`. Desktop-only processes must not be
configured as Account Outbox targets because they have no stable ingress.
