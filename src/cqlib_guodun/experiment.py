# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Saved experiment model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .platform import GuodunPlatform
    from .task import TaskHandle


@dataclass(slots=True)
class Experiment:
    """A saved experiment that can be submitted to its configured backend."""

    id: str
    lab_id: str
    name: str
    circuit: str
    device_name: str
    _platform: "GuodunPlatform" = field(repr=False)

    def run(self, shots: int = 12000, verify: bool = True) -> "TaskHandle":
        return self._platform.run_experiment(
            self.id,
            shots=shots,
            verify=verify,
            device_name=self.device_name,
        )
