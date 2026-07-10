from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from taroai.domain import utc_now


class SsoProviderProtocol(str, Enum):
    OIDC = "oidc"
    SAML = "saml"


class SsoProviderStatus(str, Enum):
    DRAFT = "draft"
    ENABLED = "enabled"
    DISABLED = "disabled"


class OidcProviderConfig(BaseModel):
    issuer_url: str = Field(min_length=1)
    client_id: str = Field(min_length=1)
    client_secret_ref_id: str | None = Field(default=None, min_length=1)
    scopes: list[str] = Field(default_factory=lambda: ["openid", "email", "profile"])

    model_config = ConfigDict(extra="forbid")


class SamlProviderConfig(BaseModel):
    entity_id: str = Field(min_length=1)
    sso_url: str = Field(min_length=1)
    x509_certificate_ref_id: str = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")


class SsoProviderCreate(BaseModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    protocol: SsoProviderProtocol
    domains: list[str] = Field(min_length=1)
    password_fallback_enabled: bool = True
    jit_provisioning_enabled: bool = False
    default_role_ids: list[str] = Field(default_factory=list)
    oidc: OidcProviderConfig | None = None
    saml: SamlProviderConfig | None = None

    model_config = ConfigDict(extra="forbid")

    @field_validator("domains")
    @classmethod
    def normalize_domains(cls, value: list[str]) -> list[str]:
        normalized = [domain.strip().lower() for domain in value]
        if any(not domain or "@" in domain for domain in normalized):
            raise ValueError("SSO domains must be domain names, not email addresses")
        if len(normalized) != len(set(normalized)):
            raise ValueError("SSO domains must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_protocol_config(self) -> "SsoProviderCreate":
        if self.protocol == SsoProviderProtocol.OIDC and self.oidc is None:
            raise ValueError("OIDC provider config is required")
        if self.protocol == SsoProviderProtocol.SAML and self.saml is None:
            raise ValueError("SAML provider config is required")
        if self.protocol == SsoProviderProtocol.OIDC and self.saml is not None:
            raise ValueError("OIDC providers must not include SAML config")
        if self.protocol == SsoProviderProtocol.SAML and self.oidc is not None:
            raise ValueError("SAML providers must not include OIDC config")
        return self


class SsoProvider(SsoProviderCreate):
    pass


class SsoProviderEntry(BaseModel):
    tenant_id: str
    provider: SsoProvider
    status: SsoProviderStatus = SsoProviderStatus.DRAFT
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
