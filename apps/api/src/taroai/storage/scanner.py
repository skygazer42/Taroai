from pydantic import BaseModel, Field

from taroai.storage.models import StorageObject


class StorageContentRejectedError(RuntimeError):
    pass


class StorageContentScanRequest(BaseModel):
    storage_object: StorageObject
    content: bytes


class StorageContentScanResult(BaseModel):
    allowed: bool = True
    matched_term_count: int = 0

    @classmethod
    def allow(cls):
        return cls(allowed=True)

    @classmethod
    def reject(cls, matched_term_count: int):
        return cls(allowed=False, matched_term_count=matched_term_count)


class StorageContentScanner(BaseModel):
    blocked_terms: list[str] = Field(default_factory=list)

    def scan(self, request: StorageContentScanRequest) -> StorageContentScanResult:
        terms = [term.strip() for term in self.blocked_terms if term.strip()]
        if not terms:
            return StorageContentScanResult.allow()

        content = request.content.lower()
        matched_count = 0
        for term in terms:
            if term.lower().encode("utf-8") in content:
                matched_count += 1
        if matched_count:
            return StorageContentScanResult.reject(matched_count)
        return StorageContentScanResult.allow()
