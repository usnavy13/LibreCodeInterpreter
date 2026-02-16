"""API server configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings


class APIConfig(BaseSettings):
    """API server settings."""

    host: str = Field(default="0.0.0.0", alias="api_host")
    port: int = Field(default=8000, ge=1, le=65535, alias="api_port")
    debug: bool = Field(default=False, alias="api_debug")
    reload: bool = Field(default=False, alias="api_reload")

    # SSL/HTTPS Configuration
    enable_https: bool | None = Field(default=None)
    https_port: int = Field(default=443, ge=1, le=65535)
    ssl_cert_file: str = Field(default="/app/ssl/fullchain.pem")
    ssl_key_file: str = Field(default="/app/ssl/privkey.pem")
    ssl_ca_certs: str | None = Field(default=None)

    # CORS Configuration
    enable_cors: bool = Field(default=False)
    cors_origins: list[str] = Field(default_factory=list)

    # Documentation
    enable_docs: bool = Field(default=True)

    class Config:
        env_prefix = ""
        extra = "ignore"
