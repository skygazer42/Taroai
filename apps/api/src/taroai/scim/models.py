from datetime import datetime
from enum import Enum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from taroai.domain import new_id, utc_now


class ScimProviderStatus(str, Enum):
    DRAFT = "draft"
    ENABLED = "enabled"
    DISABLED = "disabled"


class ScimProviderCreate(BaseModel):
    id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    bearer_token_secret_ref_id: str | None = Field(default=None, min_length=1)
    default_role_ids: list[str] = Field(default_factory=list)
    jit_create_users: bool = True

    model_config = ConfigDict(extra="forbid")

    @field_validator("default_role_ids")
    @classmethod
    def normalize_default_role_ids(cls, value: list[str]) -> list[str]:
        return normalize_unique_non_empty(value, "SCIM default role ids")


class ScimProvider(ScimProviderCreate):
    pass


class ScimProviderEntry(BaseModel):
    tenant_id: str
    provider: ScimProvider
    status: ScimProviderStatus = ScimProviderStatus.DRAFT
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScimGroupRoleMapping(BaseModel):
    group_external_id: str = Field(min_length=1)
    role_ids: list[str] = Field(min_length=1)

    model_config = ConfigDict(extra="forbid")

    @field_validator("group_external_id")
    @classmethod
    def normalize_group_external_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("SCIM group external id must not be empty")
        return normalized

    @field_validator("role_ids")
    @classmethod
    def normalize_role_ids(cls, value: list[str]) -> list[str]:
        return normalize_unique_non_empty(value, "SCIM mapped role ids")


class ScimGroupRoleMappingEntry(BaseModel):
    tenant_id: str
    provider_id: str
    mapping: ScimGroupRoleMapping
    created_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScimEmail(BaseModel):
    value: str = Field(min_length=1)
    primary: bool = False

    model_config = ConfigDict(extra="ignore")

    @field_validator("value")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if "@" not in normalized:
            raise ValueError("SCIM email value must be an email address")
        return normalized


class ScimGroupReference(BaseModel):
    value: str = Field(min_length=1)
    display: str | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("SCIM group reference value must not be empty")
        return normalized


class ScimGroupMember(BaseModel):
    value: str = Field(min_length=1)
    display: str | None = None

    model_config = ConfigDict(extra="ignore")

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("SCIM group member value must not be empty")
        return normalized


class ScimUserResource(BaseModel):
    external_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("external_id", "externalId", "id"),
    )
    user_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("user_name", "userName"),
    )
    display_name: str = Field(
        default="",
        validation_alias=AliasChoices("display_name", "displayName"),
    )
    active: bool = True
    emails: list[ScimEmail] = Field(default_factory=list)
    groups: list[ScimGroupReference] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @field_validator("external_id", "user_name", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_email_source(self) -> "ScimUserResource":
        self.email_address()
        return self

    def email_address(self) -> str:
        if "@" in self.user_name:
            return self.user_name.strip().lower()
        primary_emails = [email.value for email in self.emails if email.primary]
        if primary_emails:
            return primary_emails[0]
        if self.emails:
            return self.emails[0].value
        raise ValueError("SCIM user requires userName email or emails value")

    def resolved_display_name(self) -> str:
        return self.display_name or self.user_name


class ScimGroupResource(BaseModel):
    external_id: str = Field(
        min_length=1,
        validation_alias=AliasChoices("external_id", "externalId", "id"),
    )
    display_name: str = Field(
        min_length=1,
        validation_alias=AliasChoices("display_name", "displayName"),
    )
    members: list[ScimGroupMember] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    @field_validator("external_id", "display_name")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ScimImportRequest(BaseModel):
    users: list[ScimUserResource] = Field(default_factory=list)
    groups: list[ScimGroupResource] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ScimUserLink(BaseModel):
    tenant_id: str
    provider_id: str
    external_id: str
    user_id: str
    email: str
    active: bool
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ScimImportResult(BaseModel):
    import_id: str = Field(default_factory=lambda: new_id("scim_import"))
    provider_id: str
    users_seen: int = 0
    users_created: int = 0
    users_linked: int = 0
    users_disabled: int = 0
    roles_assigned: int = 0


class ScimImportRecord(ScimImportResult):
    tenant_id: str
    imported_by_user_id: str
    created_at: datetime = Field(default_factory=utc_now)


def normalize_unique_non_empty(value: list[str], label: str) -> list[str]:
    normalized = [item.strip() for item in value]
    if any(not item for item in normalized):
        raise ValueError(f"{label} must not be empty")
    return list(dict.fromkeys(normalized))
