import argparse
import ipaddress
import json
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taroai.storage.adapter import S3CompatibleObjectStorage
from taroai.storage.models import StorageObject, StoragePurpose


class ObjectStorageVerificationConfig(BaseModel):
    endpoint_url: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    region: str = Field(default="us-east-1", min_length=1)
    access_key_id: str = ""
    secret_access_key: str = ""
    key_prefix: str = Field(default="taroai/verify/object-storage", min_length=1)
    tenant_id: str = Field(default="tenant_object_storage_verify", min_length=1)
    workspace_id: str = Field(default="workspace_object_storage_verify", min_length=1)
    run_id: str = Field(default="run_object_storage_verify", min_length=1)
    signed_url_ttl_seconds: int = Field(default=300, ge=1)
    content_type: str = Field(default="text/plain", min_length=1)

    @model_validator(mode="after")
    def validate_endpoint_and_credentials(self) -> "ObjectStorageVerificationConfig":
        scheme = urlparse(self.endpoint_url).scheme
        if scheme not in {"http", "https"}:
            raise ValueError("object storage verification requires an HTTP endpoint URL")
        if not self.access_key_id.strip() or not self.secret_access_key.strip():
            raise ValueError("object storage verification requires credentials")
        return self


class ObjectStorageVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bucket: str
    object_key: str
    uploaded_bytes: int
    downloaded_bytes: int
    upload_etag: str | None = None
    read_signed_url_method: Literal["GET"]
    write_signed_url_method: Literal["PUT"]
    deleted: bool
    object_missing_after_delete: bool


def parse_args(argv: list[str] | None = None) -> ObjectStorageVerificationConfig:
    parser = argparse.ArgumentParser(
        description="Verify S3-compatible object storage read/write behavior."
    )
    parser.add_argument("--endpoint-url", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--access-key-id", required=True)
    parser.add_argument("--secret-access-key", required=True)
    parser.add_argument("--key-prefix", default=None)
    parser.add_argument("--signed-url-ttl-seconds", type=int, default=300)
    parsed = parser.parse_args(argv)
    config_data = {
        "endpoint_url": parsed.endpoint_url,
        "bucket": parsed.bucket,
        "region": parsed.region,
        "access_key_id": parsed.access_key_id,
        "secret_access_key": parsed.secret_access_key,
        "signed_url_ttl_seconds": parsed.signed_url_ttl_seconds,
    }
    if parsed.key_prefix is not None:
        config_data["key_prefix"] = parsed.key_prefix
    return ObjectStorageVerificationConfig(**config_data)


def connect_object_storage(config: ObjectStorageVerificationConfig):
    import boto3
    from botocore.config import Config

    client_config = {}
    if is_loopback_endpoint(config.endpoint_url):
        client_config["config"] = Config(proxies={})
    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url,
        region_name=config.region,
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        **client_config,
    )


def is_loopback_endpoint(endpoint_url: str) -> bool:
    hostname = urlparse(endpoint_url).hostname
    if hostname is None:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def verify_object_storage(
    config: ObjectStorageVerificationConfig,
) -> ObjectStorageVerificationResult:
    client = connect_object_storage(config)
    client.head_bucket(Bucket=config.bucket)
    storage_object = build_verification_object(config)
    payload = build_payload(storage_object)
    adapter = S3CompatibleObjectStorage(
        endpoint_url=config.endpoint_url,
        region=config.region,
        access_key_id=config.access_key_id,
        secret_access_key=config.secret_access_key,
        client=client,
    )
    cleanup_verification_object(config, client, storage_object.key)
    try:
        upload = adapter.upload(storage_object, payload)
        downloaded = adapter.download(storage_object)
        if downloaded.content != payload:
            raise RuntimeError("object storage download content did not match uploaded content")
        read_signed_url = adapter.create_signed_url(
            storage_object,
            operation="read",
            expires_in_seconds=config.signed_url_ttl_seconds,
        )
        write_signed_url = adapter.create_signed_url(
            storage_object,
            operation="write",
            expires_in_seconds=config.signed_url_ttl_seconds,
        )
        if not read_signed_url.url or not write_signed_url.url:
            raise RuntimeError("object storage signed URL generation returned an empty URL")
        adapter.delete(storage_object)
        missing_after_delete = object_missing(config, client, storage_object.key)
        if not missing_after_delete:
            raise RuntimeError("object storage object was still visible after delete")
        return ObjectStorageVerificationResult(
            bucket=config.bucket,
            object_key=storage_object.key,
            uploaded_bytes=len(payload),
            downloaded_bytes=len(downloaded.content),
            upload_etag=upload.etag,
            read_signed_url_method=read_signed_url.method,
            write_signed_url_method=write_signed_url.method,
            deleted=True,
            object_missing_after_delete=missing_after_delete,
        )
    finally:
        cleanup_verification_object(config, client, storage_object.key)


def build_verification_object(config: ObjectStorageVerificationConfig) -> StorageObject:
    suffix = uuid4().hex[:12]
    filename = f"object-storage-{suffix}.txt"
    key_prefix = config.key_prefix.strip("/")
    return StorageObject(
        id=f"storage_verify_{suffix}",
        tenant_id=config.tenant_id,
        workspace_id=config.workspace_id,
        run_id=config.run_id,
        purpose=StoragePurpose.ARTIFACT,
        filename=filename,
        content_type=config.content_type,
        size_bytes=0,
        bucket=config.bucket,
        key=f"{key_prefix}/{filename}",
        acl_subjects=["user:object_storage_verify"],
        sensitivity_level=0,
    )


def build_payload(storage_object: StorageObject) -> bytes:
    return (
        "Taroai object storage verification\n"
        f"storage_object_id={storage_object.id}\n"
        f"object_key={storage_object.key}\n"
    ).encode("utf-8")


def object_missing(
    config: ObjectStorageVerificationConfig,
    client,
    object_key: str,
) -> bool:
    try:
        client.head_object(Bucket=config.bucket, Key=object_key)
    except Exception:
        return True
    return False


def cleanup_verification_object(
    config: ObjectStorageVerificationConfig,
    client,
    object_key: str,
) -> None:
    client.delete_object(Bucket=config.bucket, Key=object_key)


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = verify_object_storage(config)
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
