"""Local control endpoints for OpenAkita Account PKCE login."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from openakita.account.desktop import require_desktop_account, trusted_marketplace_origin
from openakita.account.oidc import AccountOIDCError, AccountOIDCManager

capability_router = APIRouter(prefix="/api/account", tags=["account"])
router = APIRouter(prefix="/api/account", tags=["account"])


@capability_router.get("/capability")
async def account_capability(request: Request) -> dict:
    """Expose the distribution policy without activating the account provider."""

    return request.app.state.account_capability


def _manager(request: Request) -> AccountOIDCManager:
    return request.app.state.account_oidc_manager


@router.post("/login/start")
async def start_login(request: Request) -> dict:
    try:
        attempt = await _manager(request).start()
    except (OSError, AccountOIDCError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "attempt_id": attempt.attempt_id,
        "authorization_url": attempt.authorization_url,
    }


@router.get("/login/status/{attempt_id}")
async def login_status(request: Request, attempt_id: str) -> dict:
    try:
        return await _manager(request).attempt_status(attempt_id)
    except AccountOIDCError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/status")
async def account_status(request: Request) -> dict:
    return await _manager(request).snapshot()


@router.post("/entitlements/refresh")
async def refresh_entitlements(request: Request) -> dict:
    try:
        return await _manager(request).refresh_entitlements()
    except AccountOIDCError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.post("/logout")
async def logout(request: Request) -> dict:
    try:
        return {"end_session_url": await _manager(request).logout()}
    except AccountOIDCError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class MarketplaceOpenBody(BaseModel):
    origin: str = Field(max_length=500)


@router.post("/marketplace/handoff")
async def marketplace_handoff(body: MarketplaceOpenBody, request: Request) -> dict:
    require_desktop_account(request)
    origin = trusted_marketplace_origin(body.origin)
    if request.app.state.account_capability.get("mode") != "openakita":
        return {"ticket": None}
    try:
        ticket = await _manager(request).marketplace_handoff(origin)
        return {"ticket": ticket, "account": await _manager(request).snapshot()}
    except AccountOIDCError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
