# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Compatibility facade for the legacy ``GuoDunPlatform`` API."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from .client import GuodunClient
from .config import CREATE_WAVEFORM_PATH, GuodunConfig
from .exceptions import GuodunValidationError
from .models import coerce_qcis
from .platform import GuodunPlatform
from .task import ResultDataType
from .waveform import WaveformHandle


class QuantumLanguage(str, Enum):
    """Program languages accepted by the compatibility API."""

    QCIS = "qcis"


def _language_value(language: QuantumLanguage | str) -> str:
    value = language.value if isinstance(language, Enum) else str(language)
    if value.lower() != "qcis":
        raise GuodunValidationError("cqlib-guodun only supports QCIS")
    return "qcis"


class GuoDunPlatform:
    """Backward-compatible facade backed by the new pure-Python client."""

    SCHEME = "https"
    DOMAIN = "quantumctek-cloud.com"

    def __init__(
        self,
        login_key: str,
        auto_login: bool = True,
        machine_name: str | None = None,
        *,
        config: GuodunConfig | None = None,
    ) -> None:
        self.login_key = login_key
        self.auto_login = auto_login
        self.machine_name = machine_name
        self.config = config or GuodunConfig(
            domain=self.DOMAIN,
            scheme=self.SCHEME,
            save_credentials=False,
        )
        self._client = GuodunClient(self.config, api_key=login_key)
        self._platform = GuodunPlatform(self._client)
        if auto_login:
            self.login()

    @property
    def access_token(self) -> str:
        return self._client.access_token

    @access_token.setter
    def access_token(self, value: str) -> None:
        self._client.access_token = value

    @property
    def platform(self) -> GuodunPlatform:
        return self._platform

    def login(self, timeout: float = 60.0) -> str:
        if timeout != self.config.timeout:
            self.config = self.config.with_overrides(timeout=timeout)
            self._client.config = self.config
        return self._client.login()

    def set_machine(self, machine_name: str) -> None:
        self.machine_name = machine_name

    def _require_machine(self) -> str:
        if not self.machine_name:
            raise GuodunValidationError("machine_name must be configured")
        return self.machine_name

    def create_lab(self, name: str, remark: str = "") -> str:
        return self._platform.create_lab(name, remark).id

    def save_experiment(
        self,
        lab_id: str,
        circuit: Any,
        name: str = "",
        language: QuantumLanguage | str = QuantumLanguage.QCIS,
        **_: Any,
    ) -> str:
        _language_value(language)
        return self._platform.save_experiment(
            lab_id=str(lab_id),
            circuit=circuit,
            device_name=self._require_machine(),
            name=name,
        ).id

    def run_experiment(
        self, exp_id: str, num_shots: int = 12000, is_verify: bool = True
    ) -> list[str]:
        return self._platform.run_experiment(
            str(exp_id),
            shots=num_shots,
            verify=is_verify,
            device_name=self.machine_name or "",
        ).task_ids

    def submit_job(
        self,
        circuit: Any | Sequence[Any] | None = None,
        exp_name: str = "",
        parameters: Sequence[Sequence[str]] | None = None,
        values: Sequence[Sequence[float | int]] | None = None,
        num_shots: int = 12000,
        lab_id: str | None = None,
        exp_id: str | None = None,
        language: QuantumLanguage | str = QuantumLanguage.QCIS,
        version: str | None = "1",
        is_verify: bool = True,
        data_type: ResultDataType | str | None = None,
        **kwargs: Any,
    ) -> list[str]:
        _language_value(language)
        unsupported = {"noise", "quantum_state"}.intersection(kwargs)
        if unsupported:
            raise GuodunValidationError(
                f"unsupported Guodun arguments: {', '.join(sorted(unsupported))}"
            )
        if circuit is None:
            if exp_id is None:
                raise GuodunValidationError("exp_id is required when circuit is not provided")
            return self.run_experiment(exp_id, num_shots, is_verify)
        return self._platform.submit(
            circuit,
            shots=num_shots,
            device_name=self._require_machine(),
            verify=is_verify,
            name=exp_name,
            lab_id=lab_id,
            parameters=parameters,
            values=values,
            version=version,
            data_type=data_type,
        ).task_ids

    def query_experiment(
        self,
        query_id: str | Sequence[str],
        max_wait_time: float = 120.0,
        sleep_time: float = 5.0,
    ) -> list[dict[str, Any]]:
        ids = [query_id] if isinstance(query_id, str) else list(query_id)
        from .task import TaskHandle

        return TaskHandle(
            self._client,
            [str(value) for value in ids],
            self.machine_name or "",
            0x2EE0,
        ).wait_payload(max_wait_time, sleep_time)

    def download_config(
        self, read_time: str | None = None, machine: str | None = None
    ) -> dict[str, Any]:
        return self._platform.download_config(machine or self._require_machine(), read_time=read_time)

    def qcis_check_regular(self, qcis_raw: Any) -> bool:
        return self._platform.verify_qcis(qcis_raw, self._require_machine())

    def get_experiment_circuit(self, query_id: str | Sequence[str]) -> Any:
        return self._platform.get_experiment_circuits(query_id)

    def query_quantum_computer_list(self) -> list[list[Any]]:
        rows: list[dict[str, Any]] = []
        for backend in self._platform.list_backends():
            row = dict(backend.info.raw)
            if "code" in row:
                row["machineName"] = row.pop("code")
            row["status"] = backend.status.value.replace("_", " ")
            row["isToll"] = backend.toll.value
            rows.append({key: value for key, value in row.items() if value is not None})
        if not rows:
            return []
        headers = list(rows[0])
        return [[row.get(key) for key in headers] for row in rows]

    def get_machine_config(self, params: Mapping[str, Any] | None = None) -> Any:
        return self._platform.get_machine_config(params)

    def create_waveform_data(
        self,
        circuit: Any,
        circuit_name: str | None = None,
        is_verify: bool = True,
    ) -> int | str:
        data = self._client.get_data(
            "POST",
            CREATE_WAVEFORM_PATH,
            json_body={
                "circuit": coerce_qcis(circuit),
                "qcCode": self._require_machine(),
                "circuitName": circuit_name,
                "isVerify": is_verify,
            },
        )
        if not isinstance(data, Mapping) or data.get("id") is None:
            raise ValueError("waveform response is missing id")
        return data["id"]

    def query_waveform_data(self, query_id: int | str) -> str | None:
        return WaveformHandle(query_id, self._client).status()

    def _send_request(
        self,
        path: str,
        method: str = "GET",
        data: Any = None,
        params: Mapping[str, Any] | None = None,
        raise_for_code: bool = True,
    ) -> dict[str, Any]:
        return self._client.request(
            method,
            path,
            json_body=data,
            params=params,
            raise_for_code=raise_for_code,
        )

    def close(self) -> None:
        self._platform.close()
