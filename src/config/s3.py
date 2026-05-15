"""S3-compatible object storage configuration."""

from typing import Any, Optional

import boto3
from pydantic import Field
from pydantic_settings import BaseSettings


class S3Config(BaseSettings):
    """S3-compatible storage settings (Garage, AWS S3, etc.).

    When ``access_key`` and ``secret_key`` are ``None`` (the default), boto3
    uses its standard credential chain — environment variables,
    ``~/.aws/credentials``, and EC2/ECS instance metadata (IAM role).  Set
    them explicitly only when connecting to a non-AWS S3-compatible service
    such as Garage or MinIO that requires static credentials.
    """

    endpoint: str = Field(default="localhost:3900", alias="s3_endpoint")
    access_key: Optional[str] = Field(default=None, alias="s3_access_key")
    secret_key: Optional[str] = Field(default=None, alias="s3_secret_key")
    secure: bool = Field(default=False, alias="s3_secure")
    bucket: str = Field(default="code-interpreter-files", alias="s3_bucket")
    region: str = Field(default="garage", alias="s3_region")

    @property
    def endpoint_url(self) -> str:
        """Construct the full endpoint URL for boto3."""
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"

    def make_client(self) -> Any:
        """Return a configured boto3 S3 client.

        Credentials are passed explicitly only when both ``access_key`` and
        ``secret_key`` are set. When they are ``None``, boto3 falls through to
        its default credential chain (env vars, ``~/.aws/credentials``, EC2/ECS
        instance metadata).

        Raises ``ValueError`` when exactly one of the pair is set — partial
        static config is always a misconfiguration.
        """
        if bool(self.access_key) != bool(self.secret_key):
            raise ValueError(
                "S3_ACCESS_KEY and S3_SECRET_KEY must both be set or both be unset. "
                "Partial static credentials are not supported."
            )
        kwargs: dict[str, Any] = {
            "endpoint_url": self.endpoint_url,
            "region_name": self.region,
        }
        if self.access_key and self.secret_key:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key
        return boto3.client("s3", **kwargs)

    class Config:
        env_prefix = ""
        extra = "ignore"
