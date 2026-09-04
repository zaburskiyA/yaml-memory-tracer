"""Import-time installation of tracing wrappers."""

import importlib
import inspect
import os
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from memory_tracer.config import load_config
from memory_tracer.exporter import JsonlExporter
from memory_tracer.tracing import MemoryTracer

_WRAPPER_MARKER = "__memory_tracer_wrapper__"


class TracerConfigurationError(RuntimeError):
    """Raised when tracer configuration cannot be installed safely."""


@dataclass
class _Patch:
    owner: Any
    attribute: str
    original: Any
    had_own_attribute: bool

    def restore(self) -> None:
        if self.had_own_attribute:
            setattr(self.owner, self.attribute, self.original)
        else:
            delattr(self.owner, self.attribute)


class Installation:
    def __init__(self, tracer: MemoryTracer, patches: list[_Patch]):
        self.tracer = tracer
        self._patches = patches
        self._installed = True

    def uninstall(self) -> None:
        if not self._installed:
            return
        for patch in reversed(self._patches):
            patch.restore()
        self._installed = False


def _resolve_owner(path: str) -> tuple[Any, str, Any]:
    parts = path.split(".")
    if len(parts) < 2 or any(not part for part in parts):
        raise TracerConfigurationError(f"Invalid target path: {path!r}")

    last_import_error: Exception | None = None
    for module_size in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:module_size])
        try:
            owner: Any = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name and not module_name.startswith(f"{exc.name}."):
                raise TracerConfigurationError(
                    f"Importing target {path!r} failed because dependency {exc.name!r} is missing"
                ) from exc
            last_import_error = exc
            continue
        except Exception as exc:
            raise TracerConfigurationError(f"Importing target {path!r} failed: {exc}") from exc

        try:
            for attribute in parts[module_size:-1]:
                owner = getattr(owner, attribute)
            attribute = parts[-1]
            value = getattr(owner, attribute)
            return owner, attribute, value
        except AttributeError as exc:
            raise TracerConfigurationError(f"Target {path!r} does not exist") from exc

    raise TracerConfigurationError(f"Cannot import target {path!r}: {last_import_error}")


def _had_own_attribute(owner: Any, attribute: str) -> bool:
    if isinstance(owner, ModuleType):
        return attribute in vars(owner)
    return attribute in vars(owner)


def _validate_callable(path: str, value: Any) -> None:
    if not callable(value):
        raise TracerConfigurationError(f"Target {path!r} is not callable")
    if getattr(value, _WRAPPER_MARKER, False):
        raise TracerConfigurationError(f"Target {path!r} is already wrapped by memory tracer")


def install(config_path: str | Path) -> Installation:
    try:
        config, output_path = load_config(config_path)
        exporter = JsonlExporter(output_path)
    except (OSError, ValueError) as exc:
        raise TracerConfigurationError(str(exc)) from exc

    tracer = MemoryTracer(exporter)
    resolved: list[tuple[Any, str, Any, str, str, str | None]] = []
    seen_targets: set[str] = set()

    for boundary in config.boundaries:
        if boundary.target in seen_targets:
            raise TracerConfigurationError(f"Duplicate tracer target: {boundary.target!r}")
        seen_targets.add(boundary.target)
        owner, attribute, original = _resolve_owner(boundary.target)
        _validate_callable(boundary.target, original)
        resolved.append((owner, attribute, original, "boundary", boundary.target, None))

    for memory_target in config.memory:
        _, _, target_class = _resolve_owner(memory_target.target)
        if not inspect.isclass(target_class):
            raise TracerConfigurationError(f"Memory target {memory_target.target!r} is not a class")
        for method, category in memory_target.methods.items():
            full_path = f"{memory_target.target}.{method}"
            if full_path in seen_targets:
                raise TracerConfigurationError(f"Duplicate tracer target: {full_path!r}")
            seen_targets.add(full_path)
            try:
                original = getattr(target_class, method)
            except AttributeError as exc:
                raise TracerConfigurationError(
                    f"Memory method {memory_target.target}.{method!s} does not exist"
                ) from exc
            _validate_callable(full_path, original)
            resolved.append(
                (target_class, method, original, "memory", memory_target.target, category)
            )

    patches: list[_Patch] = []
    try:
        for owner, attribute, original, kind, target, category in resolved:
            if kind == "boundary":
                wrapped = tracer.wrap_boundary(original)
            else:
                wrapped = tracer.wrap_memory_method(
                    original,
                    target=target,
                    method=attribute,
                    category=category or "other",
                )
            setattr(wrapped, _WRAPPER_MARKER, True)
            patches.append(_Patch(owner, attribute, original, _had_own_attribute(owner, attribute)))
            setattr(owner, attribute, wrapped)
    except Exception:
        for patch in reversed(patches):
            patch.restore()
        raise

    return Installation(tracer, patches)


def install_from_env(env_var: str = "MEMORY_TRACING_CONFIG") -> Installation | None:
    config_path = os.getenv(env_var)
    if not config_path:
        return None
    return install(config_path)
