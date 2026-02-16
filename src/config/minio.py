"""MinIO/S3 configuration."""

from pydantic import Field, validator
from pydantic_settings import BaseSettings


class MinIOConfig(BaseSettings):
    """MinIO/S3 storage settings."""

    endpoint: str = Field(default="localhost:9000", alias="minio_endpoint")
    access_key: str = Field(
        default="test-access-key", min_length=3, alias="minio_access_key"
    )
    secret_key: str = Field(
        default="test-secret-key", min_length=8, alias="minio_secret_key"
    )
    secure: bool = Field(default=False, alias="minio_secure")
    bucket: str = Field(default="code-interpreter-files", alias="minio_bucket")

    @validator("endpoint")
    def validate_endpoint(cls, v):
        """Ensure endpoint doesn't include protocol."""
        if v.startswith(("http://", "https://")):
            raise ValueError(
                "MinIO endpoint should not include protocol (use secure instead)"
            )
        return v

    class Config:
        env_prefix = ""
        extra = "ignore"
