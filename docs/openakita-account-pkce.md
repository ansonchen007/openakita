# OpenAkita Account and Marketplace sessions

OpenAkita Desktop uses the public OIDC client `openakita-desktop`. It never has
a client secret. The backend opens a temporary loopback listener on
`127.0.0.1:1455`, generates a fresh state and S256 PKCE verifier, and returns
the Account authorization URL to Setup Center.

```text
GET  /api/account/capability
POST /api/account/login/start
GET  /api/account/login/status/{attempt_id}
GET  /api/account/status
POST /api/account/entitlements/refresh
POST /api/account/logout
POST /api/account/marketplace/handoff
```

The registered redirect URI is
`http://127.0.0.1:1455/auth/callback`. Refresh tokens are stored only in the OS
credential store through `keyring`; there is no plaintext file fallback.
Access tokens stay in process memory. A successful login fetches `/oauth/userinfo`
and `/api/v1/me/entitlements`, then persists only the identity and entitlement
read model in `~/.openakita/account/<provider-scope>/account_identity.db`.
The scope includes the provider URL, client ID and credential namespace,
so changing projects or workspaces does not change the desktop account.

Feature gates must evaluate the cached entitlement status and expiry at read
time. A central `suspended` event revokes Account sessions in that database and
all Account-backed operations fail immediately. The existing local web password
remains a separate break-glass path and is not revoked by Account suspension.

Setup Center's sidebar account menu drives these endpoints, polls the loopback
attempt, shows the cached account/entitlement status, and lets the user refresh
entitlements. Logout revokes the current product grant before clearing the
local OS credential, without opening another browser tab. A revocation outage
is reported as failure rather than successful logout.

Account integration is a distribution-level capability with three modes:

```text
OPENAKITA_ACCOUNT_MODE=openakita  # official hosted service (default)
OPENAKITA_ACCOUNT_MODE=custom     # OEM identity service
OPENAKITA_ACCOUNT_MODE=disabled   # no account routes, credentials, or account UI
```

The official mode uses the hosted service by default:

```text
OPENAKITA_ACCOUNT_BASE_URL=https://account.openakita.cn
OPENAKITA_ACCOUNT_CLIENT_ID=openakita-desktop
```

Override `OPENAKITA_ACCOUNT_BASE_URL` only when testing against a local Account
service. Custom mode requires an explicit `OPENAKITA_ACCOUNT_BASE_URL` and
`OPENAKITA_ACCOUNT_CLIENT_ID`; `OPENAKITA_ACCOUNT_DISPLAY_NAME` and
`OPENAKITA_ACCOUNT_PROVIDER` customize the provider identity shown to users.
Provider credentials use separate OS-vault slots, so an OEM token is never sent
to the official service or another custom provider.

When account mode is disabled, only `GET /api/account/capability` remains
mounted so the frontend can render an account-free application menu. OAuth,
status, entitlement, logout, and status-propagation routes are absent, and
startup clears locally stored account refresh tokens. Core local OpenAkita
features continue to work without an account.

An account-enabled always-on server may additionally expose the signed status receiver at
`POST /api/internal/openakita/users/status`. Desktop-only processes must not be
configured as Account Outbox targets because they have no stable ingress.

## Independent product sessions

Desktop, the Account website and Marketplace have independent sessions.
Opening Marketplace may offer identity continuation; it does not silently
replace the browser account. Ordinary logout affects the current product.

## Marketplace session contract

- Desktop identity is scoped to the OS user and account provider/client, not a project or workspace. Its profile database lives under `~/.openakita/account/`; the refresh token remains in the OS credential vault.
- A cached identity is usable only when its stored credential hash matches the current vault credential. Legacy credentials are recovered through refresh and userinfo, never by copying an unverified workspace profile. An unavailable recovery is shown as unconfirmed and cannot issue Marketplace handoffs or installation proofs.
- Credential rotation, identity recovery and logout share an OS file lock across desktop backends. UI focus/periodic refresh and handoff responses synchronize the visible identity.
- Account owns identity and validates live desktop credentials. Marketplace owns its HttpOnly browser session and keeps all long-lived credentials server-side.
- POST /oauth/desktop-handoff accepts JSON {client_id, refresh_token, target_client_id, target_origin}; returns {ticket, expires_in}. Tickets live for 120 seconds, are stored hashed, restricted to a registered first-party target origin, and invalidated by source grant revocation.
- POST /oauth/desktop-handoff/inspect accepts JSON {client_id, client_secret, ticket}; returns {sub, name, email, iss}. Only the target confidential client can inspect.
- POST /oauth/desktop-handoff/redeem accepts JSON {client_id, client_secret, ticket, redirect_uri, code_challenge}; atomically consumes the ticket and creates a normal S256 authorization code, returning {code}. Marketplace exchanges it using its existing token endpoint. No Account browser cookie is replaced.
- GET /auth/desktop?ticket=...&next=... on Marketplace stores a browser-bound pending transaction and immediately redirects to a clean confirmation URL. Same-account arrivals keep the existing session. Different-account arrivals offer switch or keep. Anonymous arrivals explicitly confirm the named account, preventing untrusted links from silently logging a browser in.
- Marketplace browser writes require X-Marketplace-Context, a non-credential identifier supplied by /api/v1/me (including anonymous context). It is compared to the current cookie-derived context. Missing/mismatched context cannot authorize writes. Machine routes are excluded and retain their own authentication.
- Every tab captures its own context. A shared fetch wrapper never replaces stale context with the latest identity to make a write succeed. Broadcast/focus/pageshow signals trigger a blocking account-changed view; old requests cannot repopulate a new identity.
- Local desktop handoff is enabled only for the official account distribution on local desktop UI; remote/web consumers cannot receive a server owner's credentials. A signed-out desktop opens Marketplace without disturbing its existing session.
- Ordinary logout revokes the current refresh-token family. Refresh rotation is atomic and serialized on the desktop. Login attempts have a generation, and delayed callbacks cannot undo logout or replace a newer login.
- Marketplace checks upstream session validity, with bounded caching for reads and fresh checks for writes, so global security revocation reaches independent sessions.
- Account-required installs must validate the consuming client's identity server-side before releasing download URLs; same personal subject initially, with an explicit boundary for future organization authorization.

## Observable behavior

| Desktop | Marketplace browser | Opening Marketplace |
| --- | --- | --- |
| Signed out | Signed out | Browse anonymously; no forced login |
| Signed out | Account A | Keep A |
| Account A | Signed out | Confirm the named account A, then create a Marketplace session |
| Account A | Account A | Keep the current Marketplace session |
| Account B | Account A | Explicitly choose B or keep A |
| Account B after switching | Old A tabs remain open | A pages block on session change; stale writes and identity responses are rejected |

Ordinary logout in any product does not sign out the others. An unfinished
handoff/proof remains dependent on the source desktop grant; a completed
Marketplace login has its own grant. Explicit security revocation still reaches
affected sessions. A switch or logout after an installation preview requires a
fresh identity proof before confirmation. An already authorized running local
installation retains its fixed authorization and is not reassigned to a new user.

The Marketplace document can reload to accept the current session or explicitly
restart sign-in if its cookies are stale. Browser generation checks cover delayed
Set-Cookie responses as well as ordinary shared-cookie changes. An unavailable
identity provider never authorizes a write using cached permissions.

## Deployment and compatibility

Deploy Account with migration 6 first. Deploy Marketplace API, web and Nginx
routes together, then publish the desktop. The registered Marketplace callback
origin must equal the externally opened origin. Local development sets
`OPENAKITA_MARKETPLACE_URL` on the desktop backend and `VITE_MARKETPLACE_URL`
on its frontend; the Account client registration and Marketplace frontend and
callback settings must use that same visible origin.

Existing open Marketplace pages reload to obtain the context contract. Old
desktop versions cannot consume install instructions without account proofs;
there is deliberately no token-only compatibility bypass. Historical Account
refresh tokens have no recorded rotation lineage: migration 6 groups them by
user/client, preserving the previous revocation boundary, while each new login
gets an independent family. Existing OAuth product logins also gain independence
from ordinary Account website logout.

The implementation uses existing PostgreSQL, Redis and native credential storage;
there is no additional service or required user setting. Organizational access
can extend the installation authorization boundary later without sharing user
sessions or passing long-lived tokens to the browser.

The local native access boundary must also support an independently started
backend and a desktop reopening while its backend survives. It therefore uses
a same-OS-user native credential, created through Tauri IPC at
`~/.openakita/.desktop-account-token`, rather than requiring matching process
launch environments. Windows protects the file with user-scoped DPAPI; Unix
requires owner-only file permissions. The backend only reads this credential;
no HTTP endpoint publishes it. Direct loopback and no-proxy checks still apply.
The inherited launch token remains accepted for compatibility with managed
backends. This local credential authenticates the caller, not an Account user.

See the sibling repositories' `docs/product-sessions.md` (Account) and
`docs/OPENAKITA-CLIENT-CONTEXT.md` (Marketplace) for exact endpoint and deployment
contracts. Do not record ticket-bearing URLs in outer proxy/CDN access logs.

## Marketplace installation behavior

Installation authorization is rechecked after preview. Local installed
identity comes from the actual manifest, not job history. The same version
is not downloaded again; upgrades require confirmation, older versions do
not overwrite newer installs, and unknown same-name resources require
explicit replacement confirmation. Local state is rechecked before writes.

First-time Skill installs are enabled; upgrades preserve the user's
enable/disable choice and category edits. Install and uninstall share the
workspace path, and successful installation refreshes the Skill list.

See [the testing guide](testing.md#账号与市场回归测试) for focused regression suites.
