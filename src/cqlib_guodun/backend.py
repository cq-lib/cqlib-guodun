# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Guodun backend abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Sequence

from .config import CREATE_WAVEFORM_PATH, DOWNLOAD_CONFIG_PATH
from .models import DeviceStatus, GuodunBackendInfo, build_device, coerce_qcis
from .task import CalibrationMode, ResultDataType
from .waveform import WaveformHandle

if TYPE_CHECKING:
    from .platform import GuodunPlatform
    from .pulse import GetPulse
    from .task import TaskHandle


@dataclass(slots=True)
class GuodunBackend:
    """A quantum-computer backend exposed by the Guodun platform."""

    info: GuodunBackendInfo
    _platform: "GuodunPlatform" = field(repr=False)
    _raw_config: dict[str, Any] | None = field(default=None, repr=False)
    _device: Any = field(default=None, repr=False)
    _pulse: Any = field(default=None, repr=False)

    @property
    def name(self) -> str:
        return self.info.name

    @property
    def machine_name(self) -> str:
        """Legacy alias used by :class:`cqlib_guodun.GetPulse`."""
        return self.name

    @property
    def display_name(self) -> str:
        return self.info.display_name

    @property
    def status(self):
        return self.info.status

    @property
    def toll(self):
        return self.info.toll

    def is_available(self) -> bool:
        return self.status is DeviceStatus.RUNNING

    def run(
        self,
        circuits: Any | Sequence[Any],
        shots: int = 12000,
        *,
        verify: bool = True,
        name: str = "",
        lab_id: str | None = None,
        parameters: Sequence[Sequence[str]] | None = None,
        values: Sequence[Sequence[float | int]] | None = None,
        data_type: ResultDataType | str | None = None,
    ) -> "TaskHandle":
        return self._platform.submit(
            circuits,
            shots=shots,
            device_name=self.name,
            verify=verify,
            name=name,
            lab_id=lab_id,
            parameters=parameters,
            values=values,
            data_type=data_type,
        )

    def run_with_mode(
        self,
        circuits: Any | Sequence[Any],
        shots: int,
        mode: CalibrationMode | str | None = None,
    ) -> "TaskHandle":
        """Submit circuits with an explicit readout-calibration mode.

        ``mode`` accepts ``"auto"`` (default), ``"enabled"``, or
        ``"disabled"``, matching :meth:`TianyanBackend.run_with_mode`.
        """
        calibration_mode = CalibrationMode.from_value(mode)
        task = self.run(circuits, shots)
        task.calibration_mode = calibration_mode
        return task

    def run_raw(
        self,
        circuits: Any | Sequence[Any],
        shots: int,
    ) -> "TaskHandle":
        """Submit circuits and return a task configured for raw results."""
        return self.run_with_mode(circuits, shots, mode=CalibrationMode.DISABLED)

    def raw_config(self, *, refresh: bool = False) -> dict[str, Any]:
        if self._raw_config is None or refresh:
            data = self._platform.client.get_data(
                "GET", f"{DOWNLOAD_CONFIG_PATH}/{self.name}", params={"readTime": None}
            )
            if isinstance(data, str):
                import json

                data = json.loads(data)
            if not isinstance(data, dict):
                raise ValueError("device configuration must be a JSON object")
            self._raw_config = data
        return self._raw_config

    def download_config(self) -> dict[str, Any]:
        """Legacy alias for :meth:`raw_config`."""
        return self.raw_config()

    def device_config(self, *, refresh: bool = False) -> Any:
        if self._device is None or refresh:
            self._device = build_device(self.name, self.raw_config(refresh=refresh))
        return self._device

    def num_qubits(self) -> int:
        config = self.raw_config()
        overview = config.get("overview", {})
        qubits = overview.get("qubits") if isinstance(overview, dict) else None
        if isinstance(qubits, list):
            return len(qubits)
        device = self.device_config()
        device_qubits = getattr(device, "qubits", None)
        return len(device_qubits) if device_qubits is not None else 0

    def create_waveform(
        self,
        circuit: Any,
        *,
        name: str | None = None,
        verify: bool = True,
    ) -> WaveformHandle:
        data = self._platform.client.get_data(
            "POST",
            CREATE_WAVEFORM_PATH,
            json_body={
                "circuit": coerce_qcis(circuit),
                "qcCode": self.name,
                "circuitName": name,
                "isVerify": verify,
            },
        )
        if not isinstance(data, dict) or data.get("id") is None:
            raise ValueError("waveform response is missing id")
        return WaveformHandle(data["id"], self._platform.client)

    def create_waveform_data(
        self,
        circuit: Any,
        circuit_name: str | None = None,
        is_verify: bool = True,
    ) -> int | str:
        return self.create_waveform(
            circuit,
            name=circuit_name,
            verify=is_verify,
        ).id

    def query_waveform_data(self, query_id: int | str) -> str | None:
        return WaveformHandle(query_id, self._platform.client).status()

    def get_pulse(self) -> "GetPulse":
        """Return the cached legacy-compatible pulse utility for this backend."""
        if self._pulse is None:
            from .pulse import GetPulse

            self._pulse = GetPulse(self)
        return self._pulse
