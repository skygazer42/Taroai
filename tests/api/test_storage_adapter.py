from datetime import timedelta

from taroai.config import Settings
from taroai.domain import utc_now
from taroai.storage import (
    InMemoryStorageCatalog,
    S3CompatibleObjectStorage,
    StorageObjectCreate,
    StoragePurpose,
)


class RecordingS3Client:
    def __init__(self):
        self.put_calls: list[dict] = []
        self.get_calls: list[dict] = []
        self.presign_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        return {"ETag": '"etag_123"'}

    def get_object(self, **kwargs):
        self.get_calls.append(kwargs)
        return {"Body": RecordingBody(b"# result")}

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)
        return {"DeleteMarker": True}

    def generate_presigned_url(self, ClientMethod: str, Params: dict, ExpiresIn: int):
        self.presign_calls.append(
            {
                "ClientMethod": ClientMethod,
                "Params": Params,
                "ExpiresIn": ExpiresIn,
            }
        )
        return f"https://storage.example.com/{Params['Bucket']}/{Params['Key']}?signed=1"


class RecordingBody:
    def __init__(self, content: bytes):
        self.content = content

    def read(self) -> bytes:
        return self.content


def test_s3_compatible_storage_uploads_downloads_and_builds_signed_url():
    now = utc_now()
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    client = RecordingS3Client()
    storage = S3CompatibleObjectStorage(
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        access_key_id="minio_access",
        secret_access_key="minio_secret",
        client=client,
    )

    upload = storage.upload(storage_object, content=b"# result")
    downloaded = storage.download(storage_object)
    signed_url = storage.create_signed_url(
        storage_object,
        operation="read",
        expires_in_seconds=900,
        now=now,
    )

    assert upload.uri == storage_object.uri
    assert upload.etag == "etag_123"
    assert downloaded.storage_object_id == storage_object.id
    assert downloaded.content == b"# result"
    assert downloaded.content_type == "text/markdown"
    assert client.put_calls == [
        {
            "Bucket": "taroai-artifacts",
            "Key": f"tenant_acme/workspace_sales/runs/run_123/artifacts/{storage_object.id}/agent-result.md",
            "Body": b"# result",
            "ContentType": "text/markdown",
            "Metadata": {
                "tenant_id": "tenant_acme",
                "workspace_id": "workspace_sales",
                "run_id": "run_123",
                "storage_object_id": storage_object.id,
            },
        }
    ]
    assert client.get_calls == [
        {
            "Bucket": "taroai-artifacts",
            "Key": f"tenant_acme/workspace_sales/runs/run_123/artifacts/{storage_object.id}/agent-result.md",
        }
    ]
    assert signed_url.method == "GET"
    assert signed_url.expires_at == now + timedelta(seconds=900)
    assert signed_url.url == (
        "https://storage.example.com/taroai-artifacts/"
        f"tenant_acme/workspace_sales/runs/run_123/artifacts/{storage_object.id}/agent-result.md?signed=1"
    )
    assert "minio_secret" not in signed_url.url
    assert client.presign_calls[0]["ClientMethod"] == "get_object"


def test_s3_compatible_storage_deletes_object_by_bucket_and_key():
    catalog = InMemoryStorageCatalog(bucket="taroai-artifacts")
    storage_object = catalog.register(
        StorageObjectCreate(
            tenant_id="tenant_acme",
            workspace_id="workspace_sales",
            run_id="run_123",
            purpose=StoragePurpose.ARTIFACT,
            filename="agent-result.md",
            content_type="text/markdown",
            size_bytes=128,
        )
    )
    client = RecordingS3Client()
    storage = S3CompatibleObjectStorage(
        endpoint_url="http://localhost:9000",
        region="us-east-1",
        access_key_id="minio_access",
        secret_access_key="minio_secret",
        client=client,
    )

    deleted = storage.delete(storage_object)

    assert deleted.storage_object_id == storage_object.id
    assert deleted.uri == storage_object.uri
    assert client.delete_calls == [
        {
            "Bucket": "taroai-artifacts",
            "Key": f"tenant_acme/workspace_sales/runs/run_123/artifacts/{storage_object.id}/agent-result.md",
        }
    ]


def test_settings_build_s3_compatible_storage_adapter():
    settings = Settings(
        object_storage_endpoint="http://minio.internal:9000",
        object_storage_region="us-west-2",
        object_storage_access_key_id="access",
        object_storage_secret_access_key="secret",
        _env_file=None,
    )

    storage = S3CompatibleObjectStorage.from_settings(settings, client=RecordingS3Client())

    assert storage.endpoint_url == "http://minio.internal:9000"
    assert storage.region == "us-west-2"
