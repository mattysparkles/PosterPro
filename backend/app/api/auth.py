from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AuthLoginRequest,
    AuthSessionResponse,
    AuthRegisterRequest,
    UserResponse,
    UserUpdateRequest,
)
from app.core.auth import (
    clear_session_cookie,
    get_current_user,
    hash_password,
    normalize_email,
    set_session_cookie,
    verify_password,
)
from app.core.database import get_db
from app.models.enums import MarketplaceName
from app.models.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserResponse)
def update_me(
    payload: UserUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if payload.full_name is not None:
        cleaned = payload.full_name.strip()
        current_user.full_name = cleaned or None
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.post("/register", response_model=AuthSessionResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: AuthRegisterRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    email = normalize_email(payload.email)
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="An account with that email already exists")

    user_count = db.execute(select(func.count()).select_from(User)).scalar_one()
    user = User(
        email=email,
        full_name=payload.full_name.strip() if payload.full_name else None,
        password_hash=hash_password(payload.password),
        is_admin=user_count == 0,
        enabled_platforms=[MarketplaceName.ebay.value],
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    set_session_cookie(response, user.id)
    return AuthSessionResponse(user=user, is_bootstrap_admin=user.is_admin)


@router.post("/login", response_model=AuthSessionResponse)
def login(
    payload: AuthLoginRequest,
    response: Response,
    db: Session = Depends(get_db),
):
    email = normalize_email(payload.email)
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid email or password")

    set_session_cookie(response, user.id)
    return AuthSessionResponse(user=user, is_bootstrap_admin=False)


@router.post("/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}
