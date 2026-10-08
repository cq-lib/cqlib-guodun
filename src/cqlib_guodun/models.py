# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Data models and conversion helpers used by the Guodun client."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .calibration import calibrate_counts

if TYPE_CHECKING:
    from cqlib import Qubit
    from cqlib.device import ExecutionResult, Status


class DeviceStatus(str, Enum):
    """Operational state reported for a Guodun backend."""

    RUNNING = "running"
    CALIBRATION = "calibration"
    UNDER_MAINTENANCE = "under_maintenance"
    OFFLINE = "offline"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: Any) -> "DeviceStatus":
        mapping = {
            0: cls.RUNNING,
            1: cls.CALIBRATION,
            2: cls.UNDER_MAINTENANCE,
            3: cls.OFFLINE,
            "0": cls.RUNNING,
            "1": cls.CALIBRATION,
            "2": cls.UNDER_MAINTENANCE,
            "3": cls.OFFLINE,
            "running": cls.RUNNING,
            "calibration": cls.CALIBRATION,
            "under maintenance": cls.UNDER_MAINTENANCE,
            "under_maintenance": cls.UNDER_MAINTENANCE,
            "off-line": cls.OFFLINE,
            "offline": cls.OFFLINE,
        }
        return mapping.get(value, mapping.get(str(value).lower(), cls.UNKNOWN))


class DeviceToll(str, Enum):
    """Billing category reported for a Guodun backend."""

    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"

    @classmethod
    def from_value(cls, value: Any) -> "DeviceToll":
        mapping = {
            1: cls.FREE,
            2: cls.PAID,
            "1": cls.FREE,
            "2": cls.PAID,
            "free": cls.FREE,
            "paid": cls.PAID,
        }
        return mapping.get(value, mapping.get(str(value).lower(), cls.UNKNOWN))


@dataclass(slots=True)
class Credentials:
    """Persisted API credentials and token-expiration metadata."""

    api_key: str
    access_token: str
    obtained_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: str | None = None

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Credentials":
        try:
            return cls(
                api_key=str(value["api_key"]),
                access_token=str(value["access_token"]),
                obtained_at=str(value.get("obtained_at") or datetime.now(timezone.utc).isoformat()),
                expires_at=str(value["expires_at"]) if value.get("expires_at") else None,
            )
        except KeyError as exc:
            raise ValueError(f"credential file is missing {exc.args[0]!r}") from exc


@dataclass(frozen=True, slots=True)
class GuodunBackendInfo:
    """Normalized metadata for a Guodun backend."""

    name: str
    display_name: str
    status: DeviceStatus
    toll: DeviceToll
    raw: Mapping[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "GuodunBackendInfo":
        name = payload.get("code") or payload.get("machineName") or payload.get("name")
        if not name:
            raise ValueError("backend payload is missing code/machineName")
        display = payload.get("computerName") or payload.get("displayName") or name
        return cls(
            name=str(name),
            display_name=str(display),
            status=DeviceStatus.from_value(payload.get("status")),
            toll=DeviceToll.from_value(payload.get("isToll")),
            raw=dict(payload),
        )


@dataclass(frozen=True, slots=True)
class ExperimentLab:
    """Metadata returned when an experiment lab is created."""

    id: str
    name: str
    remark: str = ""


@dataclass(slots=True)
class PythonExecutionResult:
    """Fallback result model used when the new :mod:`cqlib` is unavailable."""

    task_id: str
    qubits: list[int]
    shots: int
    num_qubits: int
    backend: str | None
    counts: dict[str, int]
    probabilities: dict[str, float]
    result_status: list[list[int]] | None = None
    status: str = "completed"
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


class GuodunExecutionResult:
    """Standard execution-result proxy with Guodun's native result rows."""

    _inner: "ExecutionResult | PythonExecutionResult"
    result_status: list[list[int]] | None

    __slots__ = ("_inner", "result_status")

    def __init__(self, inner: Any, result_status: list[list[int]] | None) -> None:
        self._inner = inner
        self.result_status = result_status

    @classmethod
    def from_counts(
        cls,
        task_id: str,
        qubits: Sequence[int],
        shots: int,
        num_qubits: int,
        counts: Mapping[str, int],
        backend: str | None = None,
    ) -> "GuodunExecutionResult":
        """Create a completed result, matching ``ExecutionResult.from_counts``."""
        normalized_counts = {str(key): int(value) for key, value in counts.items()}
        try:
            from cqlib.device import ExecutionResult  # type: ignore

            inner = ExecutionResult.from_counts(
                task_id=task_id,
                qubits=list(qubits),
                shots=shots,
                num_qubits=num_qubits,
                counts=normalized_counts,
                backend=backend,
            )
            return cls(inner, None)
        except (ImportError, AttributeError, TypeError, ValueError):
            total = sum(normalized_counts.values())
            probabilities = (
                {key: value / total for key, value in normalized_counts.items()}
                if total > 0
                else {}
            )
            inner = PythonExecutionResult(
                task_id=task_id,
                qubits=[int(qubit) for qubit in qubits],
                shots=shots,
                num_qubits=num_qubits,
                backend=backend,
                counts=normalized_counts,
                probabilities=probabilities,
            )
            return cls(inner, None)

    @property
    def inner_result(self) -> "ExecutionResult | PythonExecutionResult":
        """Return the wrapped :class:`cqlib.device.ExecutionResult`."""
        return self._inner

    @property
    def task_id(self) -> str:
        return self._inner.task_id

    @property
    def shots(self) -> int:
        return self._inner.shots

    @property
    def num_qubits(self) -> int:
        return self._inner.num_qubits

    @property
    def qubits(self) -> "list[Qubit] | list[int]":
        return self._inner.qubits

    @property
    def status(self) -> "Status | str":
        return self._inner.status

    @property
    def created_at(self) -> str | None:
        return getattr(self._inner, "created_at", None)

    @property
    def started_at(self) -> str | None:
        return getattr(self._inner, "started_at", None)

    @property
    def finished_at(self) -> str | None:
        return getattr(self._inner, "finished_at", None)

    @property
    def backend(self) -> str | None:
        return self._inner.backend

    @property
    def counts(self) -> dict[str, int]:
        return self._inner.counts

    @property
    def probabilities(self) -> dict[str, float] | None:
        return self._inner.probabilities

    def start(self) -> None:
        self._inner.start()

    def finish(self, counts: dict[str, int]) -> None:
        self._inner.finish(counts)

    def fail(self, message: str, code: int) -> None:
        self._inner.fail(message, code)

    def cancel(self) -> None:
        self._inner.cancel()

    def calc_probabilities(self) -> None:
        self._inner.calc_probabilities()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __repr__(self) -> str:
        return f"GuodunExecutionResult({self._inner!r})"


def format_qcis(circuit: str) -> str:
    """Normalize a QCIS string in the same way as the legacy client."""
    if not isinstance(circuit, str) or not circuit.strip():
        raise ValueError("circuit must be a non-empty QCIS string")
    return "\n".join(line.strip() for line in circuit.splitlines() if line.strip()).upper()


def coerce_qcis(circuit: Any) -> str:
    """Accept a QCIS string or a new-cqlib ``Circuit`` instance."""
    if isinstance(circuit, str):
        return format_qcis(circuit)
    try:
        from cqlib.ir import qcis  # type: ignore

        return format_qcis(qcis.dumps(circuit))
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        raise ValueError("circuit must be a QCIS string or cqlib Circuit") from exc


def assign_qcis_parameters(
    circuits: Sequence[str],
    parameters: Sequence[Sequence[str]] | None,
    values: Sequence[Sequence[float | int]] | None,
) -> list[str]:
    """Replace ``{name}`` placeholders in QCIS circuits."""
    normalized = [coerce_qcis(circuit) for circuit in circuits]
    if parameters is None and values is None:
        return normalized
    if parameters is None or values is None:
        raise ValueError("parameters and values must be provided together")
    if len(normalized) != len(parameters) or len(normalized) != len(values):
        raise ValueError("circuits, parameters and values must have the same length")

    assigned: list[str] = []
    for circuit, names, replacements in zip(normalized, parameters, values):
        if len(names) != len(replacements):
            raise ValueError("each parameter list must match its value list")
        mapping = {str(name).upper(): value for name, value in zip(names, replacements)}

        def substitute(match: re.Match[str]) -> str:
            key = match.group(1).upper()
            if key not in mapping:
                raise ValueError(f"missing value for QCIS parameter {match.group(1)!r}")
            return str(mapping[key])

        result = re.sub(r"\{(\w+)\}", substitute, circuit)
        remaining = re.findall(r"\{(\w+)\}", result)
        if remaining:
            raise ValueError(f"unassigned QCIS parameters: {', '.join(remaining)}")
        assigned.append(result)
    return assigned


def _parse_probability(value: Any) -> dict[str, float]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, float] = {}
    for key, item in value.items():
        try:
            result[str(key)] = float(item)
        except (TypeError, ValueError):
            continue
    return result


def extract_result_status(raw: Mapping[str, Any]) -> list[list[int]] | None:
    """Return a numeric copy of the API ``resultStatus`` field."""
    result_status = raw.get("resultStatus")
    if not isinstance(result_status, Sequence) or isinstance(result_status, (str, bytes)):
        return None
    normalized: list[list[int]] = []
    for row in result_status:
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
            return None
        try:
            normalized.append([int(value) for value in row])
        except (TypeError, ValueError):
            return None
    return normalized or None


def _counts_from_result_status(
    raw: Mapping[str, Any],
) -> tuple[list[int], dict[str, int]]:
    rows = extract_result_status(raw)
    if rows is None or len(rows) < 2:
        return [], {}
    qubits = rows[0]
    counts: Counter[str] = Counter()
    for row in rows[1:]:
        if len(row) != len(qubits):
            continue
        bitstring = "".join("1" if bit == 1 else "0" for bit in reversed(row))
        counts[bitstring] += 1
    return (qubits, dict(counts)) if counts else ([], {})


def _probability_qubits(raw: Mapping[str, Any], width: int) -> list[int]:
    rows = extract_result_status(raw)
    if rows and len(rows[0]) == width:
        return rows[0]
    measured = raw.get("measureQubitList")
    if isinstance(measured, Sequence) and not isinstance(measured, (str, bytes)):
        qubits: list[int] = []
        for value in measured:
            match = re.search(r"\d+", str(value))
            if match:
                qubits.append(int(match.group()))
        if len(qubits) == width:
            return qubits
    return list(range(width))


def _counts_from_probability(
    raw: Mapping[str, Any], shots: int
) -> tuple[list[int], dict[str, int]]:
    probabilities = _parse_probability(raw.get("probability"))
    if not probabilities:
        return [], {}
    width = max((len(key) for key in probabilities), default=0)
    counts = {
        key: count
        for key, probability in probabilities.items()
        if set(key) <= {"0", "1"} and (count := round(probability * shots)) > 0
    }
    if not counts or width == 0:
        return [], {}
    return _probability_qubits(raw, width), counts


def extract_counts(raw: Mapping[str, Any], shots: int) -> tuple[list[int], dict[str, int]]:
    """Extract uncorrected counts, falling back to platform probabilities."""
    qubits, counts = _counts_from_result_status(raw)
    if counts:
        return qubits, counts

    return _counts_from_probability(raw, shots)


def result_task_id(raw: Mapping[str, Any]) -> str:
    """Extract a task identifier from known Guodun response fields."""
    for key in ("experimentTaskId", "query_id", "queryId", "id"):
        if raw.get(key) is not None:
            return str(raw[key])
    return "unknown"


def build_execution_result(
    raw: Mapping[str, Any],
    *,
    shots: int,
    backend: str | None,
    calibration: Mapping[int, tuple[float, float]] | None = None,
    force_calibration: bool = False,
) -> GuodunExecutionResult | None:
    """Convert a completed platform payload into ``cqlib.device.ExecutionResult``.

    Like Tianyan, this always prefers exact ``resultStatus`` shots. When
    calibration data is supplied, readout-error mitigation is applied to those
    counts. The platform ``probability`` field is only a fallback.

    A small Python model is returned when the new cqlib package is not importable.
    """
    qubits, raw_counts = _counts_from_result_status(raw)
    if raw_counts:
        counts = (
            calibrate_counts(
                raw_counts,
                qubits,
                shots,
                calibration,
                force=force_calibration,
            )
            if calibration is not None
            else raw_counts
        )
    else:
        qubits, counts = _counts_from_probability(raw, shots)
    if not counts:
        return None
    task_id = result_task_id(raw)
    result_status = extract_result_status(raw)
    count_total = sum(counts.values()) or shots
    probabilities = {key: value / count_total for key, value in counts.items()}
    try:
        from cqlib.device import ExecutionResult  # type: ignore

        result = ExecutionResult.from_counts(
            task_id=task_id,
            qubits=qubits,
            shots=shots,
            num_qubits=len(qubits),
            counts=counts,
            backend=backend,
        )
        result.calc_probabilities()
        return GuodunExecutionResult(result, result_status)
    except (ImportError, AttributeError, TypeError, ValueError):
        result = PythonExecutionResult(
            task_id=task_id,
            qubits=qubits,
            shots=shots,
            num_qubits=len(qubits),
            backend=backend,
            counts=counts,
            probabilities=probabilities,
            result_status=result_status,
            raw=dict(raw),
        )
        return GuodunExecutionResult(result, result_status)


def build_device(name: str, config: Mapping[str, Any]) -> Any:
    """Convert a Guodun configuration payload to ``cqlib.device.Device``.

    If cqlib is unavailable, the raw mapping is returned unchanged.
    """
    overview = config.get("overview") if isinstance(config, Mapping) else None
    overview = overview if isinstance(overview, Mapping) else {}
    raw_qubits = overview.get("qubits") or config.get("qubits") or []

    def qubit_id(value: Any) -> int:
        if isinstance(value, int):
            return value
        match = re.search(r"\d+", str(value))
        if not match:
            raise ValueError(f"invalid qubit identifier: {value!r}")
        return int(match.group())

    qubits: list[int] = []
    for value in raw_qubits:
        try:
            qubits.append(qubit_id(value))
        except ValueError:
            continue

    coupler_map = overview.get("coupler_map") or config.get("coupler_map") or {}
    values = coupler_map.values() if isinstance(coupler_map, Mapping) else coupler_map
    edges: list[tuple[int, int]] = []
    for pair in values or []:
        if isinstance(pair, Sequence) and not isinstance(pair, (str, bytes)) and len(pair) >= 2:
            try:
                edges.append((qubit_id(pair[0]), qubit_id(pair[1])))
            except ValueError:
                continue
    if not qubits:
        qubits = sorted({item for edge in edges for item in edge})

    try:
        from cqlib.device import Device, Topology  # type: ignore

        topology_edges = [(left, right, "CZ") for left, right in edges]
        topology = Topology(qubits, topology_edges)
        device = Device(name, qubits, topology)
        disabled = overview.get("disabled_qubits") or config.get("disabled_qubits") or []
        device.invalid_qubits = [qubit_id(value) for value in disabled]
        return device
    except (ImportError, AttributeError, TypeError, ValueError):
        return dict(config)
