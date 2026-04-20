"""Legacy compatibility shim for root-level imports."""

from app.engine.executor import execute_request

__all__ = ["execute_request"]
