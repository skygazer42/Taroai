from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from taroai.config import Settings
from taroai.domain import utc_now
from taroai.storage.models import (
    StorageDeleteResult,
    StorageDownloadResult,
    StorageObject,
    StorageSignedUrl,
    StorageUploadResult,
)


class ObjectStorageConfigurationError(RuntimeError):
    pass


class ObjectStorageAdapter(BaseModel):
    def upload(self, storage_object: StorageObject, content: bytes) -> StorageUploadResult:
        raise NotImplementedError

    def download(self, storage_object: StorageObject) -> StorageDownloadResult:
        raise NotImplementedError

    def delete(self, storage_object: StorageObject) -> StorageDeleteResult:
        raise NotImplementedError

    def create_signed_url(
        self,
        storage_object: StorageObject,
        operation: Literal["read", "write"],
        expires_in_seconds: int,
        now: datetime | None = None,
    ) -> StorageSignedUrl:
        raise NotImplementedError


class S3CompatibleObjectStorage(ObjectStorageAdapter):
    endpoint_url: str = Field(min_length=1)
    region: str = Field(min_length=1)
    access_key_id: str = ""
    secret_access_key: str = ""
    client: Any | None = None

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        client: Any | None = None,
    ) -> "S3CompatibleObjectStorage":
        return cls(
            endpoint_url=settings.object_storage_endpoint,
            region=settings.object_storage_region,
            access_key_id=settings.object_storage_access_key_id,
            secret_access_key=settings.object_storage_secret_access_key,
            client=client,
        )

    def upload(self, storage_object: StorageObject, content: bytes) -> StorageUploadResult:
        metadata = {
            "tenant_id": storage_object.tenant_id,
            "workspace_id": storage_object.workspace_id,
            "storage_object_id": storage_object.id,
        }
        if storage_object.run_id is not None:
            metadata["run_id"] = storage_object.run_id
        response = self._client().put_object(
            Bucket=storage_object.bucket,
            Key=storage_object.key,
            Body=content,
            ContentType=storage_object.content_type,
            Metadata=metadata,
        )
        etag = response.get("ETag") if isinstance(response, dict) else None
        if isinstance(etag, str):
            etag = etag.strip('"')
        return StorageUploadResult(
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            etag=etag,
        )

    def download(self, storage_object: StorageObject) -> StorageDownloadResult:
        response = self._client().get_object(
            Bucket=storage_object.bucket,
            Key=storage_object.key,
        )
        body = response.get("Body") if isinstance(response, dict) else None
        content = self._read_body(body)
        return StorageDownloadResult(
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            content=content,
            content_type=storage_object.content_type,
        )

    def delete(self, storage_object: StorageObject) -> StorageDeleteResult:
        response = self._client().delete_object(
            Bucket=storage_object.bucket,
            Key=storage_object.key,
        )
        delete_marker = False
        if isinstance(response, dict):
            delete_marker = bool(response.get("DeleteMarker", False))
        return StorageDeleteResult(
            storage_object_id=storage_object.id,
            uri=storage_object.uri,
            delete_marker=delete_marker,
        )

    def create_signed_url(
        self,
        storage_object: StorageObject,
        operation: Literal["read", "write"],
        expires_in_seconds: int,
        now: datetime | None = None,
    ) -> StorageSignedUrl:
        method = "get_object" if operation == "read" else "put_object"
        http_method: Literal["GET", "PUT"] = "GET" if operation == "read" else "PUT"
        url = self._client().generate_presigned_url(
            ClientMethod=method,
            Params={"Bucket": storage_object.bucket, "Key": storage_object.key},
            ExpiresIn=expires_in_seconds,
        )
        return StorageSignedUrl(
            storage_object_id=storage_object.id,
            tenant_id=storage_object.tenant_id,
            url=url,
            method=http_method,
            expires_at=(now or utc_now()) + timedelta(seconds=expires_in_seconds),
        )

    def _client(self):
        if self.client is not None:
            return self.client
        if not self.access_key_id or not self.secret_access_key:
            raise ObjectStorageConfigurationError("object storage credentials are not configured")
        try:
            import boto3
        except ImportError as error:
            raise ObjectStorageConfigurationError("boto3 package is required for object storage") from error
        client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
        )
        object.__setattr__(self, "client", client)
        return client

    def _read_body(self, body: Any) -> bytes:
        if hasattr(body, "read"):
            value = body.read()
        else:
            value = body
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
        return b""
