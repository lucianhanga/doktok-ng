"""Minimal dependency-injection registry (composition-root skeleton).

Ports are registered to concrete adapter implementations at the composition root (the backend or
worker entrypoint). Core code resolves ports through this registry rather than importing adapters
directly, preserving the ports-and-adapters boundary (ADR-0001).

For M0 this is a skeleton: no adapters are bound yet, so ``resolve`` raises ``PortNotRegistered``.
"""

from __future__ import annotations

from typing import TypeVar

T = TypeVar("T")


class PortNotRegistered(LookupError):
    """Raised when a requested port has no registered implementation."""


class Registry:
    """A tiny type-keyed service registry."""

    def __init__(self) -> None:
        self._bindings: dict[type, object] = {}

    def register(self, port: type[T], implementation: T) -> None:
        self._bindings[port] = implementation

    def resolve(self, port: type[T]) -> T:
        try:
            return self._bindings[port]  # type: ignore[return-value]
        except KeyError as exc:
            raise PortNotRegistered(port.__name__) from exc

    def is_registered(self, port: type) -> bool:
        return port in self._bindings


def build_registry() -> Registry:
    """Composition root. M0: returns an empty registry (no adapters bound yet)."""
    return Registry()
