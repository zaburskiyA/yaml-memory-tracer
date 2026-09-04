"""Small importable targets used by memory tracer tests."""

import asyncio
from typing import Any


class SampleMemory:
    def echo(self, value: Any, optional: str = "default") -> Any:
        return value

    def outer(self, value: Any) -> Any:
        return self.echo(value)

    def fail(self, message: str) -> None:
        raise LookupError(message)

    async def async_echo(self, value: Any, delay: float = 0) -> Any:
        await asyncio.sleep(delay)
        return value


def boundary_sync(store: SampleMemory, value: Any) -> Any:
    return store.outer(value)


async def boundary_async(store: SampleMemory, value: Any, delay: float = 0) -> Any:
    return await store.async_echo(value, delay)
