"""Legacy compatibility shim for root-level imports."""

from app.engine.router_service import build_route_decision

__all__ = ["build_route_decision"]