"""S3-compatible object storage configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings


class S3Config(BaseSettings):
    """S3-compatible storage settings (Garage, AWS S3, etc.)."""

    endpoint: str = Field(default="localhost:3900", alias="s3_endpoint")
    access_key: str = Field(
        default="test-access-key", min_length=3, alias="s3_access_key"
    )
    secret_key: str = Field(
        default="test-secret-key", min_length=8, alias="s3_secret_key"
    )
    secure: bool = Field(default=False, alias="s3_secure")
    bucket: str = Field(default="code-interpreter-files", alias="s3_bucket")
    region: str = Field(default="garage", alias="s3_region")

    @property
    def endpoint_url(self) -> str:
        """Construct the full endpoint URL for boto3."""
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.endpoint}"

    class Config:
        env_prefix = ""
        extra = "ignore"
