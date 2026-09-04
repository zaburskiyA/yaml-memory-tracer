"""Configuration schema and loader for the memory tracer."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

Category = Literal["read", "write", "update", "delete", "transform", "use", "other"]


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["jsonl"]
    path: str = Field(min_length=1)


class BoundaryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)


class MemoryTargetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)
    methods: dict[str, Category] = Field(min_length=1)


class TracerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    output: OutputConfig
    boundaries: list[BoundaryConfig] = Field(default_factory=list)
    memory: list[MemoryTargetConfig] = Field(min_length=1)


def load_config(config_path: str | Path) -> tuple[TracerConfig, Path]:
    path = Path(config_path).expanduser().resolve()
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read memory tracer config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in memory tracer config {path}: {exc}") from exc

    if raw is None:
        raw = {}
    try:
        config = TracerConfig.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid memory tracer config {path}: {exc}") from exc

    output_path = Path(config.output.path).expanduser()
    if not output_path.is_absolute():
        output_path = path.parent / output_path
    return config, output_path.resolve()
