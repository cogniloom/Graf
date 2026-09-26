"""Strict local application configuration. No environment or network fallback."""

from __future__ import annotations

import json
import stat
from dataclasses import dataclass
from pathlib import Path


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected an absolute path without parent traversal")
    # Check every component: resolving first would hide symlinks.
    for item in [path, *path.parents]:
        if item.is_symlink():
            raise ValueError("Symlink paths are not allowed")
    return path


@dataclass(frozen=True)
class AppConfig:
    home: Path
    database_config: Path
    models: Path
    allowed_roots: tuple[Path, ...]
    token_file: Path
    ui_dist: Path
    device: str = "cpu"
    host: str = "127.0.0.1"
    port: int = 8765
    scan_interval_seconds: float = 30
    python: str | None = None
    mcp_bridge: str | None = None
    workspace_name: str | None = None

    def __post_init__(self):
        if self.workspace_name is not None and (
            not isinstance(self.workspace_name, str) or not 1 <= len(self.workspace_name) <= 200
        ):
            raise ValueError("Workspace name must contain 1 to 200 characters")
        for name in ("home", "database_config", "models", "token_file", "ui_dist"):
            object.__setattr__(self, name, absolute(getattr(self, name)))
        object.__setattr__(self, "allowed_roots", tuple(absolute(x) for x in self.allowed_roots))
        if any(p.is_relative_to(self.ui_dist) for p in (self.token_file, self.database_config)):
            raise ValueError("Private configuration and tokens must not be in the served UI directory")
        if self.host != "127.0.0.1" or type(self.port) is not int or not 1024 <= self.port <= 65535:
            raise ValueError("Server must bind 127.0.0.1 on a port from 1024 to 65535")
        if self.device not in ("cpu", "cuda"):
            raise ValueError("Device must be cpu or cuda")
        if (
            type(self.scan_interval_seconds) not in (int, float)
            or not 0.1 <= self.scan_interval_seconds <= 86400
        ):
            raise ValueError("Invalid polling interval")
        if any(
            self.home.is_relative_to(root) or root.is_relative_to(self.home) for root in self.allowed_roots
        ):
            raise ValueError("Source roots and private workspace must not overlap")

    @classmethod
    def load(cls, path):
        return cls(**json.loads(absolute(path).read_text()))

    def token(self):
        path = absolute(self.token_file)
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode) or mode & 0o077:
            raise ValueError("Token must be a private regular file (mode 0600)")
        value = path.read_text().strip()
        if len(value) < 32 or len(value) > 4096 or any(c.isspace() for c in value):
            raise ValueError("Token must contain at least 32 non-whitespace characters")
        return value

    def source(self, value, *, must_exist=True):
        path = absolute(value)
        if not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError("Source is outside the selected allowed roots")
        if must_exist and not (path.is_file() or path.is_dir()):
            raise ValueError("Source must be an existing regular file or directory")
        return path
