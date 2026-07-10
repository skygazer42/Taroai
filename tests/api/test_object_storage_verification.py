from pathlib import Path

import pytest
import boto3

from taroai.storage.object_storage_verification import (
    ObjectStorageVerificationConfig,
    connect_object_storage,
    parse_args,
    verify_object_storage,
)


class LocalBody:
    def __init__(self, content: bytes):
        self.content = content

    def read(self) -> bytes:
        return self.content


class LocalObjectStorageClient:
    def __init__(self):
        self.buckets = {"taroai-artifacts"}
        self.objects: dict[tuple[str, str], dict] = {}
        self.presign_calls: list[dict] = []

    def head_bucket(self, Bucket: str):
        if Bucket not in self.buckets:
            raise RuntimeError("bucket not found")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def put_object(self, **kwargs):
        self.objects[(kwargs["Bucket"], kwargs["Key"])] = kwargs
        return {"ETag": '"object-etag"'}

    def get_object(self, Bucket: str, Key: str):
        stored = self.objects[(Bucket, Key)]
        return {"Body": LocalBody(stored["Body"])}

    def delete_object(self, Bucket: str, Key: str):
        self.objects.pop((Bucket, Key), None)
        return {"DeleteMarker": True}

    def head_object(self, Bucket: str, Key: str):
        if (Bucket, Key) not in self.objects:
            raise RuntimeError("object not found")
        stored = self.objects[(Bucket, Key)]
        return {"ContentLength": len(stored["Body"])}

    def generate_presigned_url(self, ClientMethod: str, Params: dict, ExpiresIn: int):
        self.presign_calls.append(
            {
                "ClientMethod": ClientMethod,
                "Params": Params,
                "ExpiresIn": ExpiresIn,
            }
        )
        return f"http://storage.local/{Params['Bucket']}/{Params['Key']}?signed=1"


def test_object_storage_verification_config_requires_credentials():
    with pytest.raises(ValueError, match="object storage verification requires credentials"):
        ObjectStorageVerificationConfig(
            endpoint_url="http://localhost:9000",
            bucket="taroai-artifacts",
            region="us-east-1",
            access_key_id="",
            secret_access_key="",
        )


def test_object_storage_verification_cli_parses_endpoint_bucket_and_credentials():
    config = parse_args(
        [
            "--endpoint-url",
            "http://localhost:9000",
            "--bucket",
            "taroai-artifacts",
            "--region",
            "us-east-1",
            "--access-key-id",
            "access",
            "--secret-access-key",
            "secret",
            "--key-prefix",
            "taroai/verify/unit",
        ]
    )

    assert config.endpoint_url == "http://localhost:9000"
    assert config.bucket == "taroai-artifacts"
    assert config.region == "us-east-1"
    assert config.key_prefix == "taroai/verify/unit"


def test_verify_object_storage_script_wraps_python_cli():
    script = Path("scripts/verify-object-storage.sh")

    text = script.read_text()

    assert "python -m taroai.storage.object_storage_verification" in text
    assert "--endpoint-url" in text
    assert "--bucket" in text
    assert "TAROAI_OBJECT_STORAGE_ENDPOINT" in text
    assert "TAROAI_OBJECT_STORAGE_SECRET_ACCESS_KEY" in text


def test_object_storage_verification_uploads_downloads_signs_and_deletes(monkeypatch):
    client = LocalObjectStorageClient()
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: client)
    config = ObjectStorageVerificationConfig(
        endpoint_url="http://localhost:9000",
        bucket="taroai-artifacts",
        region="us-east-1",
        access_key_id="access",
        secret_access_key="secret",
        key_prefix="taroai/verify/unit",
    )

    result = verify_object_storage(config)

    assert result.bucket == "taroai-artifacts"
    assert result.upload_etag == "object-etag"
    assert result.downloaded_bytes == result.uploaded_bytes
    assert result.read_signed_url_method == "GET"
    assert result.write_signed_url_method == "PUT"
    assert result.deleted is True
    assert result.object_missing_after_delete is True
    assert client.objects == {}
    assert [call["ClientMethod"] for call in client.presign_calls] == [
        "get_object",
        "put_object",
    ]


def test_object_storage_verification_bypasses_proxy_for_loopback_endpoint(monkeypatch):
    captured_kwargs = {}

    def capture_client(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return LocalObjectStorageClient()

    monkeypatch.setattr(boto3, "client", capture_client)
    config = ObjectStorageVerificationConfig(
        endpoint_url="http://localhost:9000",
        bucket="taroai-artifacts",
        region="us-east-1",
        access_key_id="access",
        secret_access_key="secret",
    )

    connect_object_storage(config)

    assert captured_kwargs["config"].proxies == {}
