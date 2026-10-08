# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Credential persistence helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .exceptions import GuodunAuthError
from .models import Credentials


def save_credentials(credentials: Credentials, path: Path) -> None:
    """Persist credentials using an atomic replace where possible."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".credentials-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(credentials.to_dict(), stream, ensure_ascii=False, indent=2)
            try:
                temporary.chmod(0o600)
            except OSError:
                pass
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()
    except (OSError, TypeError, ValueError) as exc:
        raise GuodunAuthError(f"failed to save credentials to {path}: {exc}") from exc


def load_credentials(path: Path) -> Credentials:
    """Load and validate credentials stored in a JSON file."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict):
            raise ValueError("credential file must contain a JSON object")
        return Credentials.from_dict(data)
    except FileNotFoundError as exc:
        raise GuodunAuthError(f"credential file does not exist: {path}") from exc
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise GuodunAuthError(f"failed to load credentials from {path}: {exc}") from exc
