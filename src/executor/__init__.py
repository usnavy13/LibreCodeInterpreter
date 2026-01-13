"""
Executor service for Azure Container Apps deployment.

This module provides a FastAPI-based HTTP service that executes code
in multiple programming languages with sandboxed isolation.
"""

from .main import app

__all__ = ["app"]
