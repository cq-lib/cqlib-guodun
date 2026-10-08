# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Pure-Python client for the Guodun quantum cloud platform."""

from .backend import GuodunBackend
from .config import GuodunConfig
from .exceptions import (
    GuodunApiError,
    GuodunAuthError,
    GuodunError,
    GuodunRequestError,
    GuodunTimeoutError,
    GuodunValidationError,
)
from .experiment import Experiment
from .legacy import GuoDunPlatform, QuantumLanguage
from .models import (
    DeviceStatus,
    DeviceToll,
    ExperimentLab,
    GuodunBackendInfo,
    GuodunExecutionResult,
    PythonExecutionResult,
)
from .platform import GuodunPlatform
from .task import CalibrationMode, ResultDataType, TaskHandle
from .waveform import WaveformHandle

__version__ = "0.1.0"

__all__ = [
    "DeviceStatus",
    "DeviceToll",
    "CalibrationMode",
    "ResultDataType",
    "Experiment",
    "ExperimentLab",
    "GuoDunPlatform",
    "GuodunApiError",
    "GuodunAuthError",
    "GuodunBackend",
    "GuodunBackendInfo",
    "GuodunConfig",
    "GuodunError",
    "GuodunExecutionResult",
    "GuodunPlatform",
    "GuodunRequestError",
    "GuodunTimeoutError",
    "GuodunValidationError",
    "GetPulse",
    "PythonExecutionResult",
    "QuantumLanguage",
    "TaskHandle",
    "WaveformHandle",
]


def __getattr__(name: str):
    if name == "GetPulse":
        from .pulse import GetPulse

        return GetPulse
    raise AttributeError(name)
