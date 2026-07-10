import fnmatch
import re

from pydantic import BaseModel, Field


SANDBOX_IMAGE_DIGEST_PATTERN = re.compile(r"@sha256:[a-fA-F0-9]{64}$")
BROAD_ALLOWED_IMAGE_PATTERNS = {"*", "**", "*:*", "*@sha256:*", "latest"}


class SandboxRuntimeImagePolicySummary(BaseModel):
    image: str = Field(min_length=1)
    allowed_images: list[str] = Field(default_factory=list)
    allowed_image_count: int
    image_digest_pinned: bool
    image_has_registry: bool
    image_has_non_latest_tag: bool


def sandbox_runtime_image_policy_summary(
    image: str,
    allowed_images: list[str],
) -> SandboxRuntimeImagePolicySummary:
    normalized_allowed_images = sandbox_runtime_normalize_allowed_images(
        allowed_images
    )
    return SandboxRuntimeImagePolicySummary(
        image=image,
        allowed_images=normalized_allowed_images,
        allowed_image_count=len(normalized_allowed_images),
        image_digest_pinned=sandbox_runtime_image_digest_pinned(image),
        image_has_registry=sandbox_runtime_image_has_registry(image),
        image_has_non_latest_tag=sandbox_runtime_image_has_non_latest_tag(image),
    )


def sandbox_runtime_image_policy_failure_details(
    image: str,
    allowed_images: list[str],
    context: str = "sandbox",
) -> list[str]:
    details = sandbox_runtime_allowed_image_policy_failure_details(
        allowed_images,
        context=context,
    )
    normalized_allowed_images = sandbox_runtime_normalize_allowed_images(
        allowed_images
    )
    if normalized_allowed_images and not any(
        fnmatch.fnmatchcase(image, pattern)
        for pattern in normalized_allowed_images
    ):
        details.append(f"{context} image is not allowed by configured image patterns")

    if not sandbox_runtime_image_uses_approved_reference(image):
        details.append(f"{context} image must use an approved registry or digest")
    if sandbox_runtime_image_uses_latest_tag(image):
        details.append(f"{context} image must not use the latest tag")
    return details


def sandbox_runtime_allowed_image_policy_failure_details(
    allowed_images: list[str],
    context: str = "sandbox",
) -> list[str]:
    details: list[str] = []
    normalized_allowed_images = sandbox_runtime_normalize_allowed_images(
        allowed_images
    )
    if not normalized_allowed_images:
        return [f"{context} allowed image list must not be empty"]
    for pattern in normalized_allowed_images:
        if sandbox_runtime_allowed_image_pattern_is_broad(pattern):
            details.append(f"{context} allowed image list contained a broad pattern")
        if sandbox_runtime_image_uses_latest_tag(pattern):
            details.append(f"{context} allowed image pattern must not use latest tag")
        if not sandbox_runtime_image_pattern_uses_approved_reference(pattern):
            details.append(
                f"{context} allowed image pattern must use an approved registry or digest"
            )
    return list(dict.fromkeys(details))


def sandbox_runtime_normalize_allowed_images(allowed_images: list[str]) -> list[str]:
    return [pattern.strip() for pattern in allowed_images if pattern.strip()]


def sandbox_runtime_allowed_image_pattern_is_broad(pattern: str) -> bool:
    pattern_text = pattern.strip()
    normalized_pattern = pattern_text.lower()
    if normalized_pattern in BROAD_ALLOWED_IMAGE_PATTERNS:
        return True
    if "*" not in pattern_text:
        return False
    if not sandbox_runtime_image_digest_scoped_pattern(pattern_text):
        return True
    image_name, digest_pattern = pattern_text.split("@sha256:", 1)
    return "*" in image_name or digest_pattern != "*"


def sandbox_runtime_image_uses_approved_reference(image: str) -> bool:
    return sandbox_runtime_image_digest_pinned(image) or (
        sandbox_runtime_image_has_registry(image)
        and sandbox_runtime_image_has_non_latest_tag(image)
    )


def sandbox_runtime_image_pattern_uses_approved_reference(pattern: str) -> bool:
    return (
        sandbox_runtime_image_digest_scoped_pattern(pattern)
        and sandbox_runtime_image_has_registry(pattern)
    ) or (
        sandbox_runtime_image_has_registry(pattern)
        and sandbox_runtime_image_has_non_latest_tag(pattern)
    )


def sandbox_runtime_image_digest_pinned(image: str) -> bool:
    return SANDBOX_IMAGE_DIGEST_PATTERN.search(image.strip()) is not None


def sandbox_runtime_image_digest_scoped_pattern(pattern: str) -> bool:
    return "@sha256:" in pattern.strip()


def sandbox_runtime_image_has_registry(image: str) -> bool:
    image_text = image.strip()
    if "/" not in image_text:
        return False
    first_component = image_text.split("/", 1)[0]
    return (
        "." in first_component
        or ":" in first_component
        or first_component == "localhost"
    )


def sandbox_runtime_image_has_non_latest_tag(image: str) -> bool:
    if sandbox_runtime_image_digest_pinned(image):
        return True
    image_without_digest = image.strip().split("@", 1)[0]
    last_component = image_without_digest.rsplit("/", 1)[-1]
    if ":" not in last_component:
        return False
    return not last_component.lower().endswith(":latest")


def sandbox_runtime_image_uses_latest_tag(image: str) -> bool:
    image_without_digest = image.strip().split("@", 1)[0]
    last_component = image_without_digest.rsplit("/", 1)[-1]
    return last_component.lower().endswith(":latest")
