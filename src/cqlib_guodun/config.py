# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Runtime configuration and Guodun API paths."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path


LOGIN_PATH = "/api-uaa/oauth/token"
MACHINE_LIST_PATH = "/experiment/sdk/quantumComputer/list"
MACHINE_CONFIG_PATH = "/experiment/sdk/quantumComputer/config"
CREATE_LAB_PATH = "/experiment/sdk/experiment/save"
SAVE_EXPERIMENT_PATH = "/experiment/sdk/experiment/detail/save"
SUBMIT_PATH = "/experiment/sdk/experiment/temporary/save"
QUERY_RESULT_PATH = "/experiment/sdk/experiment/result/find"
DOWNLOAD_CONFIG_PATH = "/experiment/sdk/experiment/download/config"
QCIS_VERIFY_PATH = "/experiment/sdk/experiment/qcis/rule/verify"
EXPERIMENT_CIRCUIT_PATH = "/experiment/sdk/experiment/getQcis/by/taskIds"
CREATE_WAVEFORM_PATH = "/experiment/sdk/generateWaveformDiagram"
QUERY_WAVEFORM_PATH = "/experiment/sdk/getWaveformDiagram"


def default_credentials_path() -> Path:
    """Return the platform-appropriate default credential file location."""
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA", Path.home()))
        return root / "cqlib" / "guodun" / "credentials.json"
    return Path.home() / ".cqlib" / "guodun" / "credentials.json"


@dataclass(frozen=True, slots=True)
class GuodunConfig:
    """Configuration for a Guodun platform client."""

    domain: str = "quantumctek-cloud.com"
    scheme: str = "https"
    save_credentials: bool = True
    auto_refresh: bool = True
    credentials_path: Path | str | None = None
    timeout: float = 60.0
    max_retries: int = 2
    retry_delay: float = 1.0

    def __post_init__(self) -> None:
        domain = self.domain.strip().rstrip("/")
        scheme = self.scheme.strip().lower().rstrip(":/")
        if not domain:
            raise ValueError("domain must not be empty")
        if scheme not in {"http", "https"}:
            raise ValueError("scheme must be 'http' or 'https'")
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if self.max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if self.retry_delay < 0:
            raise ValueError("retry_delay must not be negative")
        object.__setattr__(self, "domain", domain)
        object.__setattr__(self, "scheme", scheme)
        object.__setattr__(
            self,
            "credentials_path",
            Path(self.credentials_path) if self.credentials_path else default_credentials_path(),
        )

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.domain}"

    def with_overrides(self, **changes: object) -> "GuodunConfig":
        """Return a copy with selected fields replaced."""
        return replace(self, **changes)
