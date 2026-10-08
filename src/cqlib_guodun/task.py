# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Task handles and result polling."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from .calibration import extract_readout_calibration
from .client import GuodunClient
from .config import DOWNLOAD_CONFIG_PATH, QUERY_RESULT_PATH
from .exceptions import GuodunApiError, GuodunTimeoutError, GuodunValidationError
from .models import GuodunExecutionResult, build_execution_result

MAX_QUERY_BATCH_SIZE = 50


class CalibrationMode(str, Enum):
    """Readout-calibration mode matching ``cqlib_tianyan.CalibrationMode``."""

    AUTO = "auto"
    ENABLED = "enabled"
    DISABLED = "disabled"

    @classmethod
    def from_value(cls, value: "CalibrationMode | str | None") -> "CalibrationMode":
        if value is None:
            return cls.AUTO
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError as exc:
            raise GuodunValidationError(
                "mode must be 'auto', 'enabled', or 'disabled'"
            ) from exc


class ResultDataType(str, Enum):
    """Provider-specific experiment result formats supported by Guodun."""

    EVENT_IQ = "EVENT_IQ"
    IQ = "IQ"
    P01 = "P01"

    @classmethod
    def from_value(
        cls, value: "ResultDataType | str | None"
    ) -> "ResultDataType | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).upper())
        except ValueError as exc:
            raise GuodunValidationError(
                "data_type must be 'EVENT_IQ', 'IQ', 'P01', or None"
            ) from exc


def _result_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, Mapping):
        data = data.get("experimentResultModelList", [])
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
        return []
    return [dict(item) for item in data if isinstance(item, Mapping)]


def _is_failed(raw: Mapping[str, Any]) -> bool:
    status = raw.get("status")
    text = str(raw.get("statusName") or raw.get("state") or "").lower()
    return status in {-1, "-1", "failed", "error"} or text in {"failed", "error"}


@dataclass(slots=True)
class TaskHandle:
    """A submitted batch of QCIS circuits."""

    client: GuodunClient = field(repr=False)
    task_ids: list[str]
    device_name: str
    shots: int
    calibration_mode: CalibrationMode = CalibrationMode.AUTO
    data_type: ResultDataType | None = None
    submitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        self.task_ids = [str(task_id) for task_id in self.task_ids]
        self.data_type = ResultDataType.from_value(self.data_type)
        self.calibration_mode = CalibrationMode.from_value(self.calibration_mode)
        if not self.task_ids:
            raise ValueError("task_ids must not be empty")
        if self.shots <= 0:
            raise ValueError("shots must be greater than zero")

    def _ensure_standard_result_type(self) -> None:
        if self.data_type is not None:
            raise GuodunValidationError(
                f"{self.data_type.value} results are provider-specific and cannot be "
                "converted to ExecutionResult; use wait_payload() instead"
            )

    def status_raw(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for offset in range(0, len(self.task_ids), MAX_QUERY_BATCH_SIZE):
            query_ids = self.task_ids[offset : offset + MAX_QUERY_BATCH_SIZE]
            data = self.client.get_data(
                "POST",
                QUERY_RESULT_PATH,
                json_body={"query_ids": query_ids},
            )
            batch_results = _result_list(data)
            failed = next((item for item in batch_results if _is_failed(item)), None)
            if failed is not None:
                raise GuodunApiError(
                    str(
                        failed.get("errorMessage")
                        or failed.get("message")
                        or "task failed"
                    ),
                    code=failed.get("errorCode") or failed.get("status"),
                    data=failed,
                )
            results.extend(batch_results)
        return results

    def status(self) -> list[GuodunExecutionResult]:
        """Query once and return completed, uncorrected execution results."""
        self._ensure_standard_result_type()
        completed: list[GuodunExecutionResult] = []
        for raw in self.status_raw():
            result = build_execution_result(
                raw,
                shots=self.shots,
                backend=self.device_name,
            )
            if result is not None:
                completed.append(result)
        return completed

    def _status_calibrated(
        self,
        calibration: Mapping[int, tuple[float, float]] | None,
        *,
        force_calibration: bool = False,
    ) -> list[GuodunExecutionResult]:
        completed: list[GuodunExecutionResult] = []
        for raw in self.status_raw():
            result = build_execution_result(
                raw,
                shots=self.shots,
                backend=self.device_name,
                calibration=calibration,
                force_calibration=force_calibration,
            )
            if result is not None:
                completed.append(result)
        return completed

    def _wait_results(
        self,
        *,
        calibration: Mapping[int, tuple[float, float]] | None,
        force_calibration: bool = False,
        timeout_secs: float,
        poll_interval_secs: float,
    ) -> list[GuodunExecutionResult]:
        if timeout_secs <= 0:
            raise ValueError("timeout_secs must be greater than zero")
        if poll_interval_secs < 0:
            raise ValueError("poll_interval_secs must not be negative")
        start = time.monotonic()
        expected = len(self.task_ids)
        while True:
            results = self._status_calibrated(
                calibration,
                force_calibration=force_calibration,
            )
            if len(results) == expected:
                return results
            elapsed = time.monotonic() - start
            if elapsed >= timeout_secs:
                raise GuodunTimeoutError(
                    f"tasks did not finish within {timeout_secs:g} seconds: {self.task_ids}"
                )
            remaining = timeout_secs - elapsed
            time.sleep(min(poll_interval_secs, remaining))

    def _download_readout_calibration(
        self,
    ) -> dict[int, tuple[float, float]] | None:
        if not self.device_name:
            return None
        config = self.client.get_data(
            "GET",
            f"{DOWNLOAD_CONFIG_PATH}/{self.device_name}",
            params={"readTime": None},
        )
        if isinstance(config, str):
            config = json.loads(config)
        if not isinstance(config, Mapping):
            return None
        return extract_readout_calibration(config)

    def wait(
        self,
        timeout_secs: float = 120.0,
        poll_interval_secs: float = 5.0,
    ) -> list[GuodunExecutionResult]:
        """Wait according to the calibration mode selected during submission."""
        self._ensure_standard_result_type()
        if self.calibration_mode is CalibrationMode.DISABLED:
            return self._wait_results(
                calibration=None,
                timeout_secs=timeout_secs,
                poll_interval_secs=poll_interval_secs,
            )

        calibration = self._download_readout_calibration()
        if calibration is None and self.calibration_mode is CalibrationMode.ENABLED:
            raise GuodunValidationError(
                "readout calibration data not available for this backend"
            )
        return self._wait_results(
            calibration=calibration,
            force_calibration=self.calibration_mode is CalibrationMode.ENABLED,
            timeout_secs=timeout_secs,
            poll_interval_secs=poll_interval_secs,
        )

    def wait_raw(
        self,
        timeout_secs: float = 120.0,
        poll_interval_secs: float = 5.0,
    ) -> list[GuodunExecutionResult]:
        """Wait for uncorrected standard results built from ``resultStatus``."""
        self._ensure_standard_result_type()
        return self._wait_results(
            calibration=None,
            timeout_secs=timeout_secs,
            poll_interval_secs=poll_interval_secs,
        )

    def wait_payload(
        self,
        timeout_secs: float = 120.0,
        poll_interval_secs: float = 5.0,
    ) -> list[dict[str, Any]]:
        """Wait and return unmodified Guodun API result dictionaries."""
        if timeout_secs <= 0:
            raise ValueError("timeout_secs must be greater than zero")
        if poll_interval_secs < 0:
            raise ValueError("poll_interval_secs must not be negative")
        start = time.monotonic()
        expected = len(self.task_ids)
        while True:
            raw_results = self.status_raw()
            if len(raw_results) == expected:
                return raw_results
            elapsed = time.monotonic() - start
            if elapsed >= timeout_secs:
                raise GuodunTimeoutError(
                    f"tasks did not finish within {timeout_secs:g} seconds: {self.task_ids}"
                )
            remaining = timeout_secs - elapsed
            time.sleep(min(poll_interval_secs, remaining))
