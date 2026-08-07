"""Local control endpoints for OpenAkita Account PKCE login."""

from fastapi import APIRouter, HTTPException, Request

from openakita.account.oidc import AccountOIDCError, AccountOIDCManager

router = APIRouter(prefix="/api/account", tags=["account"])


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
    return {"end_session_url": await _manager(request).logout()}
