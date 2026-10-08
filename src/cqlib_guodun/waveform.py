# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Waveform generation handles."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .client import GuodunClient
from .config import QUERY_WAVEFORM_PATH
from .exceptions import GuodunTimeoutError


@dataclass(slots=True)
class WaveformHandle:
    """Poll a waveform-generation request until its URL is available."""

    id: int | str
    client: GuodunClient = field(repr=False)

    def status(self) -> str | None:
        data = self.client.get_data(
            "GET", QUERY_WAVEFORM_PATH, params={"id": self.id}
        )
        if not isinstance(data, Mapping):
            return None
        value = data.get("visibleUrl")
        return str(value) if value else None

    def wait(self, timeout: float = 120.0, poll_interval: float = 2.0) -> str:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if poll_interval < 0:
            raise ValueError("poll_interval must not be negative")
        deadline = time.monotonic() + timeout
        while True:
            if url := self.status():
                return url
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GuodunTimeoutError(
                    f"waveform {self.id!r} was not ready within {timeout:g} seconds"
                )
            time.sleep(min(poll_interval, remaining))
