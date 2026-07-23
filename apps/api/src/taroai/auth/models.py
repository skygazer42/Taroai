from datetime import datetime

from pydantic import BaseModel, Field


class AuthLoginRequest(BaseModel):
    tenant_id: str | None = Field(default=None, min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)
    remember_me: bool = False


class AuthRegisterRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120, pattern=r"^.*\S.*$")
    email: str = Field(
        min_length=3,
        max_length=320,
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
    )
    password: str = Field(min_length=8, max_length=1024)


class AuthTokenClaims(BaseModel):
    session_id: str
    tenant_id: str
    user_id: str
    email: str
    display_name: str = ""
    role_ids: list[str] = Field(default_factory=list)
    issued_at: datetime
    expires_at: datetime


class AuthLoginResult(BaseModel):
    access_token: str
    token_type: str = "Bearer"
    session_id: str
    expires_at: datetime
    tenant_id: str
    user_id: str
    display_name: str = ""


class AuthLogoutResult(BaseModel):
    revoked: bool
