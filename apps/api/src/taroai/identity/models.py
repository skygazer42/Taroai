import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from taroai.domain import new_id, utc_now


UserAccountStatus = Literal["active", "disabled", "pending", "deleted"]


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        raise ValueError("email must not be empty")
    return normalized


class PasswordHasher(BaseModel):
    algorithm: str = "pbkdf2_sha256"
    iterations: int = 600000
    salt: str = Field(default="change_me_in_production", min_length=1)
    salt_bytes: int = Field(default=16, ge=16)

    def hash_password(self, password: str) -> str:
        password_salt = secrets.token_urlsafe(self.salt_bytes)
        digest = self._digest(
            password=password,
            password_salt=password_salt,
            iterations=self.iterations,
            include_pepper=True,
        )
        return f"{self.algorithm}${self.iterations}${password_salt}${digest}"

    def verify_password(self, password: str, password_hash: str) -> bool:
        parsed = self._parse_password_hash(password_hash)
        if parsed is None:
            return False
        algorithm, iterations, password_salt, digest = parsed
        if algorithm != self.algorithm:
            return False
        expected = self._digest(
            password=password,
            password_salt=password_salt,
            iterations=iterations,
            include_pepper=True,
        )
        if hmac.compare_digest(expected, digest):
            return True
        legacy_expected = self._digest(
            password=password,
            password_salt=password_salt,
            iterations=iterations,
            include_pepper=False,
        )
        return hmac.compare_digest(legacy_expected, digest)

    def _parse_password_hash(self, password_hash: str) -> tuple[str, int, str, str] | None:
        parts = password_hash.split("$")
        if len(parts) != 4:
            return None
        algorithm, iterations_value, password_salt, digest = parts
        try:
            iterations = int(iterations_value)
        except ValueError:
            return None
        if iterations <= 0 or not password_salt or not digest:
            return None
        return algorithm, iterations, password_salt, digest

    def _digest(
        self,
        password: str,
        password_salt: str,
        iterations: int,
        include_pepper: bool,
    ) -> str:
        salt_material = password_salt
        if include_pepper:
            salt_material = f"{password_salt}:{self.salt}"
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt_material.encode("utf-8"),
            iterations,
        ).hex()


class UserAccountCreate(BaseModel):
    tenant_id: str
    email: str
    display_name: str
    password: str = Field(min_length=8)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class UserAccount(BaseModel):
    id: str = Field(default_factory=lambda: new_id("user"))
    tenant_id: str
    email: str
    display_name: str
    password_hash: str
    status: UserAccountStatus = "active"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class Permission(BaseModel):
    action: str
    resource: str

    def matches(self, action: str, resource: str) -> bool:
        if self.action != action:
            return False
        if self.resource == resource:
            return True
        if self.resource.endswith("*"):
            return resource.startswith(self.resource[:-1])
        return False


class Role(BaseModel):
    tenant_id: str
    id: str
    name: str
    permissions: list[Permission] = Field(default_factory=list)


class RoleAssignment(BaseModel):
    tenant_id: str
    user_id: str
    role_id: str
    created_at: datetime = Field(default_factory=utc_now)
