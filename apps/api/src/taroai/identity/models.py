import hashlib
import hmac
from datetime import datetime

from pydantic import BaseModel, Field

from taroai.domain import new_id, utc_now


class PasswordHasher(BaseModel):
    algorithm: str = "pbkdf2_sha256"
    iterations: int = 600000
    salt: str = Field(default="change_me_in_production", min_length=1)

    def hash_password(self, password: str) -> str:
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            self.salt.encode("utf-8"),
            self.iterations,
        ).hex()
        return f"{self.algorithm}${self.iterations}${self.salt}${digest}"

    def verify_password(self, password: str, password_hash: str) -> bool:
        return hmac.compare_digest(self.hash_password(password), password_hash)


class UserAccountCreate(BaseModel):
    tenant_id: str
    email: str
    display_name: str
    password: str = Field(min_length=8)


class UserAccount(BaseModel):
    id: str = Field(default_factory=lambda: new_id("user"))
    tenant_id: str
    email: str
    display_name: str
    password_hash: str
    status: str = "active"
    created_at: datetime = Field(default_factory=utc_now)


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
