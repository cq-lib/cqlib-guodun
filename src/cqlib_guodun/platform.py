# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""High-level Guodun platform API."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from .backend import GuodunBackend
from .client import GuodunClient
from .config import (
    CREATE_LAB_PATH,
    DOWNLOAD_CONFIG_PATH,
    EXPERIMENT_CIRCUIT_PATH,
    MACHINE_CONFIG_PATH,
    MACHINE_LIST_PATH,
    QCIS_VERIFY_PATH,
    SAVE_EXPERIMENT_PATH,
    SUBMIT_PATH,
    GuodunConfig,
)
from .exceptions import GuodunValidationError
from .experiment import Experiment
from .models import (
    ExperimentLab,
    GuodunBackendInfo,
    assign_qcis_parameters,
    coerce_qcis,
)
from .task import ResultDataType, TaskHandle

MAX_BATCH_SIZE = 50


def _config_from_options(
    config: GuodunConfig | None,
    *,
    domain: str | None,
    scheme: str | None,
    save_credentials: bool | None,
    auto_refresh: bool | None,
    credentials_path: str | None,
) -> GuodunConfig:
    if config is not None:
        changes: dict[str, object] = {}
        for key, value in {
            "domain": domain,
            "scheme": scheme,
            "save_credentials": save_credentials,
            "auto_refresh": auto_refresh,
            "credentials_path": credentials_path,
        }.items():
            if value is not None:
                changes[key] = value
        return config.with_overrides(**changes) if changes else config
    kwargs: dict[str, object] = {}
    if domain is not None:
        kwargs["domain"] = domain
    if scheme is not None:
        kwargs["scheme"] = scheme
    if save_credentials is not None:
        kwargs["save_credentials"] = save_credentials
    if auto_refresh is not None:
        kwargs["auto_refresh"] = auto_refresh
    if credentials_path is not None:
        kwargs["credentials_path"] = credentials_path
    return GuodunConfig(**kwargs)


class GuodunPlatform:
    """Synchronous Python client for the Guodun quantum cloud platform."""

    def __init__(self, client: GuodunClient) -> None:
        self.client = client

    @classmethod
    def login(
        cls,
        api_key: str,
        *,
        config: GuodunConfig | None = None,
        domain: str | None = None,
        scheme: str | None = None,
        save_credentials: bool | None = None,
        auto_refresh: bool | None = None,
        credentials_path: str | None = None,
    ) -> "GuodunPlatform":
        resolved = _config_from_options(
            config,
            domain=domain,
            scheme=scheme,
            save_credentials=save_credentials,
            auto_refresh=auto_refresh,
            credentials_path=credentials_path,
        )
        client = GuodunClient(resolved, api_key=api_key)
        client.login()
        return cls(client)

    @classmethod
    def from_credentials(
        cls,
        *,
        config: GuodunConfig | None = None,
        domain: str | None = None,
        scheme: str | None = None,
        save_credentials: bool | None = None,
        auto_refresh: bool | None = None,
        credentials_path: str | None = None,
    ) -> "GuodunPlatform":
        resolved = _config_from_options(
            config,
            domain=domain,
            scheme=scheme,
            save_credentials=save_credentials,
            auto_refresh=auto_refresh,
            credentials_path=credentials_path,
        )
        return cls(GuodunClient.from_credentials(resolved))

    def list_backends(self) -> list[GuodunBackend]:
        data = self.client.get_data("GET", MACHINE_LIST_PATH)
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes)):
            raise ValueError("backend list response must be an array")
        backends: list[GuodunBackend] = []
        for item in data:
            if isinstance(item, Mapping):
                try:
                    backends.append(GuodunBackend(GuodunBackendInfo.from_payload(item), self))
                except ValueError:
                    continue
        return backends

    def get_backend(self, name: str) -> GuodunBackend:
        if not name:
            raise GuodunValidationError("backend name must not be empty")
        for backend in self.list_backends():
            if backend.name == name:
                return backend
        raise GuodunValidationError(f"Guodun backend does not exist: {name}")

    def submit(
        self,
        circuits: Any | Sequence[Any],
        *,
        shots: int,
        device_name: str,
        verify: bool = True,
        name: str = "",
        lab_id: str | None = None,
        parameters: Sequence[Sequence[str]] | None = None,
        values: Sequence[Sequence[float | int]] | None = None,
        version: str | None = "1",
        data_type: ResultDataType | str | None = None,
    ) -> TaskHandle:
        if shots <= 0:
            raise GuodunValidationError("shots must be greater than zero")
        if not device_name:
            raise GuodunValidationError("device_name must not be empty")
        if isinstance(circuits, (str, bytes)) or not isinstance(circuits, Sequence):
            circuit_list = [circuits]
        else:
            circuit_list = list(circuits)
        if not circuit_list:
            raise GuodunValidationError("at least one circuit is required")
        try:
            qcis_list = assign_qcis_parameters(circuit_list, parameters, values)
        except ValueError as exc:
            raise GuodunValidationError(str(exc)) from exc
        result_data_type = ResultDataType.from_value(data_type)

        all_ids: list[str] = []
        for offset in range(0, len(qcis_list), MAX_BATCH_SIZE):
            chunk = qcis_list[offset : offset + MAX_BATCH_SIZE]
            payload = {
                "exp_id": None,
                "lab_id": lab_id,
                "inputCode": chunk,
                "languageCode": "qcis",
                "name": name,
                "shots": shots,
                "source": "SDK",
                "computerCode": device_name,
                "experimentDetailName": version if len(qcis_list) == 1 else None,
                "is_verify": verify,
                "dataType": result_data_type.value if result_data_type else None,
            }
            data = self.client.get_data("POST", SUBMIT_PATH, json_body=payload)
            if not isinstance(data, Mapping):
                raise ValueError("submission response must contain an object")
            query_ids = data.get("query_ids")
            if not isinstance(query_ids, Sequence) or isinstance(query_ids, (str, bytes)):
                raise ValueError("submission response is missing query_ids")
            all_ids.extend(str(query_id) for query_id in query_ids)
        return TaskHandle(
            self.client,
            all_ids,
            device_name,
            shots,
            data_type=result_data_type,
        )

    def create_lab(self, name: str, remark: str = "") -> ExperimentLab:
        if not name.strip():
            raise GuodunValidationError("lab name must not be empty")
        data = self.client.get_data(
            "POST", CREATE_LAB_PATH, json_body={"name": name, "remark": remark}
        )
        if not isinstance(data, Mapping) or data.get("lab_id") is None:
            raise ValueError("create-lab response is missing lab_id")
        return ExperimentLab(str(data["lab_id"]), name, remark)

    def save_experiment(
        self,
        *,
        lab_id: str,
        circuit: Any,
        device_name: str,
        name: str = "",
        language: str = "qcis",
    ) -> Experiment:
        language_value = getattr(language, "value", language)
        if str(language_value).lower() != "qcis":
            raise GuodunValidationError("cqlib-guodun only supports QCIS")
        if not lab_id:
            raise GuodunValidationError("lab_id must not be empty")
        qcis = coerce_qcis(circuit)
        data = self.client.get_data(
            "POST",
            SAVE_EXPERIMENT_PATH,
            json_body={
                "inputCode": qcis,
                "lab_id": lab_id,
                "languageCode": "qcis",
                "name": name,
                "source": "SDK",
                "computerCode": device_name,
            },
        )
        if not isinstance(data, Mapping) or data.get("exp_id") is None:
            raise ValueError("save-experiment response is missing exp_id")
        return Experiment(str(data["exp_id"]), str(lab_id), name, qcis, device_name, self)

    def run_experiment(
        self,
        exp_id: str,
        *,
        shots: int = 12000,
        verify: bool = True,
        device_name: str = "",
    ) -> TaskHandle:
        if not exp_id:
            raise GuodunValidationError("exp_id must not be empty")
        data = self.client.get_data(
            "POST",
            SUBMIT_PATH,
            json_body={
                "exp_id": exp_id,
                "shots": shots,
                "is_verify": verify,
                "source": "SDK",
            },
        )
        query_ids = data.get("query_ids") if isinstance(data, Mapping) else None
        if not isinstance(query_ids, Sequence) or isinstance(query_ids, (str, bytes)):
            raise ValueError("run-experiment response is missing query_ids")
        return TaskHandle(
            self.client,
            [str(value) for value in query_ids],
            device_name,
            shots,
        )

    def download_config(
        self, device_name: str, *, read_time: str | None = None
    ) -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", device_name or ""):
            raise GuodunValidationError(f"unsupported device name: {device_name!r}")
        data = self.client.get_data(
            "GET", f"{DOWNLOAD_CONFIG_PATH}/{device_name}", params={"readTime": read_time}
        )
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            raise ValueError("device configuration must be a JSON object")
        return data

    def verify_qcis(self, circuit: Any, device_name: str) -> bool:
        envelope = self.client.request(
            "POST",
            QCIS_VERIFY_PATH,
            json_body={"computerCode": device_name, "qcis": coerce_qcis(circuit)},
            raise_for_code=False,
        )
        return envelope.get("code") == 0

    def get_experiment_circuits(
        self, task_ids: str | Sequence[str]
    ) -> Any:
        ids = [task_ids] if isinstance(task_ids, str) else list(task_ids)
        if not ids:
            raise GuodunValidationError("at least one task id is required")
        return self.client.get_data(
            "POST",
            EXPERIMENT_CIRCUIT_PATH,
            json_body={"query_ids": [str(value) for value in ids]},
        )

    def get_machine_config(self, params: Mapping[str, Any] | None = None) -> Any:
        return self.client.get_data(
            "GET", MACHINE_CONFIG_PATH, params=params, authenticated=False
        )

    def close(self) -> None:
        self.client.close()
