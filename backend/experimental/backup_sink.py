"""S2 backup destinations: a local directory and an S3-compatible bucket (R2).

Every sink refuses archives that carry recovery material, and uploads never
overwrite an existing object silently. Synthetic data only; not used by the app.
"""
import os
import shutil
from pathlib import Path

from . import vault_backup

DEFAULT_PREFIX = "backups/"


class SinkError(RuntimeError):
    code = "SINK_ERROR"


class SinkUnavailable(SinkError):
    code = "SINK_UNAVAILABLE"


class SinkAuthFailed(SinkError):
    code = "SINK_AUTH_FAILED"


class SinkRejected(SinkError):
    code = "SINK_REJECTED"


class SinkLocked(SinkError):
    code = "SINK_LOCKED"


class LocalDirSink:
    """Copy archives into a local directory (the daily local copy)."""

    kind = "local"

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def upload(self, archive, *, name=None, overwrite=False):
        vault_backup.assert_upload_safe(archive)
        target = self.root / (name or Path(archive).name)
        if target.exists() and not overwrite:
            raise SinkRejected("destination already has an object with that name")
        shutil.copy2(archive, target)
        return {"sink": self.kind, "key": target.name, "bytes": target.stat().st_size}

    def download(self, name, destination):
        source = self.root / name
        if not source.is_file():
            raise SinkUnavailable("object is not present locally")
        shutil.copy2(source, destination)
        return {"sink": self.kind, "key": name, "bytes": Path(destination).stat().st_size}

    def list(self, prefix=""):
        return [
            {"key": path.name, "bytes": path.stat().st_size}
            for path in sorted(self.root.glob(prefix + "*"))
            if path.is_file()
        ]

    def delete(self, name):
        (self.root / name).unlink()
        return {"sink": self.kind, "key": name}


class S3Sink:
    """S3-compatible object storage (tested against Cloudflare R2)."""

    kind = "s3"

    def __init__(self, *, endpoint, bucket, access_key, secret_key, prefix=DEFAULT_PREFIX, region="auto"):
        if not all([endpoint, bucket, access_key, secret_key]):
            raise SinkUnavailable("incomplete S3 configuration")
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - environment specific
            raise SinkUnavailable("boto3 is not installed") from exc
        self.bucket = bucket
        self.prefix = prefix
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(signature_version="s3v4", retries={"max_attempts": 2, "mode": "standard"}),
        )
        self._errors = __import__("botocore.exceptions", fromlist=["ClientError"]).ClientError

    def _wrap(self, exc, action):
        error = getattr(exc, "response", {}).get("Error", {})
        code = str(error.get("Code", ""))
        message = str(error.get("Message", ""))
        lowered = (code + " " + message).lower()
        if "lock" in lowered:
            return SinkLocked("%s refused: object is locked (%s)" % (action, code))
        if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "403"}:
            if action == "upload" and "overwrite" in message.lower():
                return SinkLocked("upload refused: %s" % code)
            return SinkAuthFailed("%s failed: %s" % (action, code))
        if code in {"NoSuchBucket", "NotFound", "404"}:
            return SinkUnavailable("%s failed: %s" % (action, code))
        return SinkError("%s failed: %s %s" % (action, code, message))

    def upload(self, archive, *, name=None, overwrite=False):
        vault_backup.assert_upload_safe(archive)
        key = self.prefix + (name or Path(archive).name)
        target = self.client
        try:
            if not overwrite:
                try:
                    target.head_object(Bucket=self.bucket, Key=key)
                except self._errors:
                    pass
                else:
                    raise SinkRejected("object already exists: " + key)
            response = target.put_object(
                Bucket=self.bucket, Key=key, Body=Path(archive).read_bytes(),
                ContentType="application/octet-stream",
            )
        except SinkError:
            raise
        except self._errors as exc:
            raise self._wrap(exc, "upload") from exc
        return {"sink": self.kind, "key": key, "etag": response.get("ETag"), "bytes": Path(archive).stat().st_size}

    def download(self, key, destination):
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self._errors as exc:
            raise self._wrap(exc, "download") from exc
        Path(destination).write_bytes(body)
        return {"sink": self.kind, "key": key, "bytes": len(body)}

    def list(self, prefix=None):
        try:
            listing = self.client.list_objects_v2(Bucket=self.bucket, Prefix=prefix or self.prefix)
        except self._errors as exc:
            raise self._wrap(exc, "list") from exc
        return [
            {"key": item["Key"], "bytes": item["Size"], "modified": item["LastModified"].isoformat()}
            for item in listing.get("Contents", [])
        ]

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except self._errors as exc:
            raise self._wrap(exc, "delete") from exc
        return {"sink": self.kind, "key": key}


def sink_from_environment(path=None):
    """Build an S3 sink from R2_* variables in the environment or a local file."""
    values = dict(os.environ)
    if path is not None and Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip())
    return S3Sink(
        endpoint=values.get("R2_ENDPOINT", ""),
        bucket=values.get("R2_BUCKET", ""),
        access_key=values.get("R2_ACCESS_KEY_ID", ""),
        secret_key=values.get("R2_SECRET_ACCESS_KEY", ""),
        prefix=values.get("R2_PREFIX", DEFAULT_PREFIX),
    )
