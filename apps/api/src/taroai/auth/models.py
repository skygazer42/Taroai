from datetime import datetime

from pydantic import BaseModel, Field


class AuthLoginRequest(BaseModel):
    tenant_id: str = Field(min_length=1)
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class AuthTokenClaims(BaseModel):
    session_id: str
    tenant_id: str
    user_id: str
    email: str
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


class AuthLogoutResult(BaseModel):
    revoked: bool
