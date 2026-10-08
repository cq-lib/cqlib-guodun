# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Public API and live-service tests for :mod:`cqlib_guodun`.

Tests without the ``integration`` marker run offline. Integration tests use
the Guodun service and require ``GUODUN_API_KEY``. Tests marked ``slow`` also
require ``PYTEST_RUN_SLOW=1`` because they submit jobs or create resources.

Run the offline suite::

    pytest -m "not integration"

Run read-only integration tests::

    GUODUN_API_KEY=... pytest -m "integration and not slow"

Run all integration tests, including job submission and waveform generation::

    GUODUN_API_KEY=... PYTEST_RUN_SLOW=1 pytest -m integration
"""

from __future__ import annotations

import inspect
import math
import os
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

import cqlib_guodun
from cqlib_guodun import (
    CalibrationMode,
    DeviceStatus,
    DeviceToll,
    GuoDunPlatform,
    GuodunApiError,
    GuodunAuthError,
    GuodunBackend,
    GuodunConfig,
    GuodunError,
    GuodunExecutionResult,
    GuodunPlatform,
    GuodunRequestError,
    GuodunTimeoutError,
    GuodunValidationError,
    QuantumLanguage,
    ResultDataType,
    TaskHandle,
)
from cqlib_guodun.models import GuodunBackendInfo


SIMPLE_CIRCUIT = "H Q1\nM Q1"
BELL_CIRCUIT = "H Q1\nH Q8\nCZ Q1 Q8\nH Q8\nM Q1\nM Q8"


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.fixture(autouse=True)
def _live_test_requirements(request: pytest.FixtureRequest) -> None:
    """Skip live or resource-consuming tests unless explicitly enabled."""
    if request.node.get_closest_marker("integration") and not os.getenv("GUODUN_API_KEY"):
        pytest.skip("set GUODUN_API_KEY to run Guodun live-service tests")
    if request.node.get_closest_marker("slow") and not _env_flag("PYTEST_RUN_SLOW"):
        pytest.skip("set PYTEST_RUN_SLOW=1 to run task-submission tests")


@pytest.fixture(scope="session")
def api_key() -> str:
    """Return the API key used by live-service tests."""
    value = os.getenv("GUODUN_API_KEY")
    if not value:
        pytest.skip("set GUODUN_API_KEY to run Guodun live-service tests")
    return value


@pytest.fixture(scope="session")
def domain() -> str | None:
    """Return the optional service-domain override."""
    return os.getenv("GUODUN_DOMAIN") or None


@pytest.fixture(scope="session")
def platform(api_key: str, domain: str | None) -> GuodunPlatform:
    """Share one authenticated platform instance across live tests."""
    kwargs = {"save_credentials": False}
    if domain:
        kwargs["domain"] = domain
    value = GuodunPlatform.login(api_key, **kwargs)
    yield value
    value.close()


@pytest.fixture(scope="session")
def backend(platform: GuodunPlatform):
    """Use the requested backend or the first available backend."""
    requested = os.getenv("GUODUN_DEVICE")
    if requested:
        return platform.get_backend(requested)
    backends = platform.list_backends()
    available = next((item for item in backends if item.is_available()), None)
    if available is None:
        pytest.skip("no running Guodun backend is available")
    return available


class TestModule:
    """Check package exports and compatibility imports."""


    def test_public_exports(self) -> None:
        """Every name in ``__all__`` must resolve from the package root."""
        for name in cqlib_guodun.__all__:
            assert getattr(cqlib_guodun, name) is not None

    def test_get_pulse_compatibility_imports(self) -> None:
        """All supported GetPulse import paths must expose the same class."""
        from cqlib_guodun import GetPulse
        from cqlib_guodun.get_pulse import GetPulse as TopLevelGetPulse
        from cqlib_guodun.utils.get_pulse import GetPulse as UtilsGetPulse

        assert GetPulse is TopLevelGetPulse is UtilsGetPulse

    def test_gate_qcis_templates(self) -> None:
        """Return the expected pulse templates for X2P and CZ gates."""
        from cqlib_guodun import GetPulse

        single_template = GetPulse.get_gate_pulse_qcis_template("X2P")
        assert single_template.startswith("PXY ")
        assert "{qubit}" in single_template
        assert "{wave_class}" in single_template
        assert "{amplitude}" in single_template

        coupler_template = GetPulse.get_gate_pulse_qcis_template("CZ")
        assert coupler_template.startswith("B ")
        assert "{qubit0}" in coupler_template
        assert "{qubit1}" in coupler_template
        assert "{coupler}" in coupler_template

    def test_no_public_session_parameter(self) -> None:
        """Public constructors must not expose the internal HTTP session."""
        from cqlib_guodun.client import GuodunClient

        callables = (
            GuodunClient,
            GuodunClient.from_credentials,
            GuodunPlatform.login,
            GuodunPlatform.from_credentials,
            GuoDunPlatform,
        )
        for callable_ in callables:
            assert "session" not in inspect.signature(callable_).parameters


class TestConfig:
    """Check configuration defaults, normalization, and validation."""

    def test_defaults(self) -> None:
        """Defaults must produce an HTTPS URL and a filesystem credential path."""
        config = GuodunConfig()
        assert config.scheme == "https"
        assert config.base_url.startswith("https://")
        assert isinstance(config.credentials_path, Path)

    def test_normalizes_domain_and_scheme(self) -> None:
        """Normalize protocol casing and trailing slashes in the domain."""
        config = GuodunConfig(domain="example.com/", scheme="HTTPS://")
        assert config.domain == "example.com"
        assert config.scheme == "https"
        assert config.base_url == "https://example.com"

    def test_with_overrides_is_immutable(self) -> None:
        """``with_overrides`` must leave the original configuration unchanged."""
        original = GuodunConfig(save_credentials=True)
        updated = original.with_overrides(save_credentials=False, timeout=10)
        assert original.save_credentials is True
        assert updated.save_credentials is False
        assert updated.timeout == 10

    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"domain": ""}, "domain"),
            ({"scheme": "ftp"}, "scheme"),
            ({"timeout": 0}, "timeout"),
            ({"max_retries": -1}, "max_retries"),
            ({"retry_delay": -1}, "retry_delay"),
        ],
    )
    def test_invalid_values(self, kwargs: dict, message: str) -> None:
        """Reject invalid domains, protocols, timeouts, and retry settings."""
        with pytest.raises(ValueError, match=message):
            GuodunConfig(**kwargs)


class TestEnumsAndModels:
    """Check API value conversion and public data models."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, DeviceStatus.RUNNING),
            ("calibration", DeviceStatus.CALIBRATION),
            ("under maintenance", DeviceStatus.UNDER_MAINTENANCE),
            (3, DeviceStatus.OFFLINE),
            ("unexpected", DeviceStatus.UNKNOWN),
        ],
    )
    def test_device_status(self, value, expected) -> None:
        """Convert numeric and textual API states to ``DeviceStatus``."""
        assert DeviceStatus.from_value(value) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [(1, DeviceToll.FREE), ("paid", DeviceToll.PAID), (None, DeviceToll.UNKNOWN)],
    )
    def test_device_toll(self, value, expected) -> None:
        """Convert free, paid, and unknown values to ``DeviceToll``."""
        assert DeviceToll.from_value(value) is expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, CalibrationMode.AUTO),
            ("AUTO", CalibrationMode.AUTO),
            ("enabled", CalibrationMode.ENABLED),
            (CalibrationMode.DISABLED, CalibrationMode.DISABLED),
        ],
    )
    def test_calibration_mode(self, value, expected) -> None:
        """Normalize strings and enum instances to a calibration mode."""
        assert CalibrationMode.from_value(value) is expected

    def test_invalid_calibration_mode(self) -> None:
        """Reject unknown calibration modes."""
        with pytest.raises(GuodunValidationError, match="mode"):
            CalibrationMode.from_value("invalid")

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (None, None),
            ("event_iq", ResultDataType.EVENT_IQ),
            ("IQ", ResultDataType.IQ),
            (ResultDataType.P01, ResultDataType.P01),
        ],
    )
    def test_result_data_type(self, value, expected) -> None:
        """Normalize provider-specific result data types."""
        assert ResultDataType.from_value(value) is expected

    def test_invalid_result_data_type(self) -> None:
        """Reject result formats that are not supported by Guodun."""
        with pytest.raises(GuodunValidationError, match="data_type"):
            ResultDataType.from_value("counts")

    def test_backend_info_from_api_shape(self) -> None:
        """Build backend metadata from the platform's response shape."""
        info = GuodunBackendInfo.from_payload(
            {"code": "gd_qc1", "computerName": "Guodun QC1", "status": 0, "isToll": 1}
        )
        assert info.name == "gd_qc1"
        assert info.display_name == "Guodun QC1"
        assert info.status is DeviceStatus.RUNNING
        assert info.toll is DeviceToll.FREE

    def test_execution_result_from_counts(self) -> None:
        """Build a typed Guodun result and probabilities from counts."""
        result = GuodunExecutionResult.from_counts(
            task_id="task-1", qubits=[1, 8], shots=100, num_qubits=2,
            counts={"00": 55, "11": 45}, backend="gd_qc1",
        )
        assert result.task_id == "task-1"
        assert result.shots == 100
        assert result.num_qubits == 2
        assert result.backend == "gd_qc1"
        assert result.counts == {"00": 55, "11": 45}
        assert result.probabilities == {"00": 0.55, "11": 0.45}
        assert result.result_status is None
        assert result.inner_result is not None


class TestErrors:
    """Check the public exception hierarchy."""

    def test_exception_hierarchy(self) -> None:
        """All package exceptions must derive from ``GuodunError``."""
        for error in (
            GuodunApiError,
            GuodunAuthError,
            GuodunRequestError,
            GuodunTimeoutError,
            GuodunValidationError,
        ):
            assert issubclass(error, GuodunError)


class TestBackendOffline:
    """Check backend convenience methods without accessing the service."""

    def test_run_raw_uses_disabled_calibration(self) -> None:
        """``run_raw`` must submit once and disable readout calibration."""

        class FakePlatform:
            def __init__(self) -> None:
                self.submission = None

            def submit(self, circuits, **kwargs):
                self.submission = (circuits, kwargs)
                return SimpleNamespace(calibration_mode=CalibrationMode.AUTO)

        platform = FakePlatform()
        info = GuodunBackendInfo.from_payload(
            {"code": "gd_qc1", "computerName": "Guodun QC1", "status": 0}
        )
        backend = GuodunBackend(info, platform)

        task = backend.run_raw([SIMPLE_CIRCUIT], shots=100)

        assert task.calibration_mode is CalibrationMode.DISABLED
        assert platform.submission is not None
        circuits, kwargs = platform.submission
        assert circuits == [SIMPLE_CIRCUIT]
        assert kwargs["shots"] == 100
        assert kwargs["device_name"] == "gd_qc1"

    def test_run_forwards_result_data_type(self) -> None:
        """``backend.run`` must forward specialized result formats."""

        class FakePlatform:
            def __init__(self) -> None:
                self.submission = None

            def submit(self, circuits, **kwargs):
                self.submission = (circuits, kwargs)
                return SimpleNamespace()

        platform = FakePlatform()
        backend = GuodunBackend(
            GuodunBackendInfo.from_payload({"code": "gd_qc1", "status": 0}),
            platform,
        )

        backend.run(SIMPLE_CIRCUIT, shots=10, data_type=ResultDataType.IQ)

        assert platform.submission is not None
        assert platform.submission[1]["data_type"] is ResultDataType.IQ


class TestSpecializedResultsOffline:
    """Check submission payloads and raw provider-result handling."""

    def test_platform_submit_sends_data_type(self) -> None:
        """Send the camel-case API field required by the Guodun service."""

        class RecordingClient:
            def __init__(self) -> None:
                self.payload = None

            def get_data(self, method, path, *, json_body=None, **kwargs):
                self.payload = json_body
                return {"query_ids": ["task-iq"]}

        client = RecordingClient()
        task = GuodunPlatform(client).submit(
            SIMPLE_CIRCUIT,
            shots=10,
            device_name="gd_qc1",
            data_type="iq",
        )

        assert client.payload["dataType"] == "IQ"
        assert task.data_type is ResultDataType.IQ

    def test_specialized_results_use_wait_payload(self) -> None:
        """Return IQ data unchanged instead of forcing an ExecutionResult."""

        class ResultClient:
            def get_data(self, method, path, *, json_body=None, **kwargs):
                return {
                    "experimentResultModelList": [
                        {"experimentTaskId": "task-iq", "iq": [[0.1, -0.2]]}
                    ]
                }

        task = TaskHandle(
            ResultClient(),
            ["task-iq"],
            "gd_qc1",
            10,
            data_type=ResultDataType.IQ,
        )

        payload = task.wait_payload(timeout_secs=1, poll_interval_secs=0)
        assert payload == [{"experimentTaskId": "task-iq", "iq": [[0.1, -0.2]]}]
        with pytest.raises(GuodunValidationError, match="wait_payload"):
            task.wait()
        with pytest.raises(GuodunValidationError, match="wait_payload"):
            task.wait_raw()

    def test_status_raw_splits_large_query_batches(self) -> None:
        """Query at most 50 task IDs per provider request."""

        class BatchResultClient:
            def __init__(self) -> None:
                self.query_batches = []

            def get_data(self, method, path, *, json_body=None, **kwargs):
                query_ids = list(json_body["query_ids"])
                self.query_batches.append(query_ids)
                return {
                    "experimentResultModelList": [
                        {"experimentTaskId": task_id} for task_id in query_ids
                    ]
                }

        client = BatchResultClient()
        task_ids = [f"task-{index}" for index in range(120)]
        task = TaskHandle(client, task_ids, "gd_qc1", 10)

        results = task.status_raw()

        assert [len(batch) for batch in client.query_batches] == [50, 50, 20]
        assert [item["experimentTaskId"] for item in results] == task_ids


@pytest.mark.integration
class TestPlatform:
    """Exercise read-only platform and backend operations against the service."""

    def test_login(self, platform: GuodunPlatform) -> None:
        """A valid API key must produce an access token."""
        assert platform.client.access_token

    def test_invalid_login(self, domain: str | None) -> None:
        """An invalid API key must fail authentication."""
        kwargs = {"save_credentials": False}
        if domain:
            kwargs["domain"] = domain
        with pytest.raises((GuodunAuthError, GuodunRequestError)):
            GuodunPlatform.login("invalid-api-key", **kwargs)

    def test_list_backends(self, platform: GuodunPlatform) -> None:
        """The backend list must contain named devices with typed states."""
        backends = platform.list_backends()
        assert backends
        assert all(item.name for item in backends)
        assert all(isinstance(item.status, DeviceStatus) for item in backends)

    def test_get_backend(self, platform: GuodunPlatform, backend) -> None:
        """Look up a backend by name and inspect its public metadata."""
        same_backend = platform.get_backend(backend.name)
        assert same_backend.name == backend.name
        assert same_backend.display_name
        assert isinstance(same_backend.toll, DeviceToll)

    def test_missing_backend(self, platform: GuodunPlatform) -> None:
        """Looking up an unknown backend must fail clearly."""
        with pytest.raises(GuodunValidationError, match="does not exist"):
            platform.get_backend("definitely-not-a-real-guodun-backend")

    def test_backend_config(self, backend) -> None:
        """Download device data and build the corresponding cqlib model."""
        raw = backend.raw_config(refresh=True)
        assert isinstance(raw, dict)
        assert backend.download_config() is raw
        assert backend.num_qubits() > 0
        assert backend.device_config() is not None

    def test_machine_config(self, platform: GuodunPlatform, backend) -> None:
        """Request topology data with the parameters required by the endpoint."""
        params = {
            "type": "overview",
            "computerCode": backend.name,
            "label": (
                "qpu_coordinate,coupler_map,"
                "disabled_couplers,disabled_qubits"
            ),
        }
        config = platform.get_machine_config(params)
        assert isinstance(config, dict)
        assert config

    def test_verify_qcis(self, platform: GuodunPlatform, backend) -> None:
        """The service must accept a valid QCIS program."""
        assert platform.verify_qcis(SIMPLE_CIRCUIT, backend.name) is True


@pytest.mark.integration
class TestPulse:
    """Check Pulse access through ``backend.get_pulse()``."""

    def test_backend_returns_cached_pulse_object(self, backend) -> None:
        """A backend must cache its Pulse helper."""
        pulse = backend.get_pulse()
        assert pulse is backend.get_pulse()
        assert pulse.platform is backend

    def test_pulse_reads_real_backend_config(self, backend) -> None:
        """Pulse must reuse backend configuration and expose its sample rate."""
        pulse = backend.get_pulse()
        assert pulse.config is backend.download_config()
        assert pulse.get_channel_sample_rate("xy") > 0

@pytest.mark.integration
@pytest.mark.slow
class TestTaskSubmission:
    """Submit jobs to a live backend. These tests consume remote resources."""

    def test_backend_run_and_wait(self, backend) -> None:
        """Compare calibrated and raw results for a Bell-state circuit."""
        task = backend.run([BELL_CIRCUIT], shots=1000)
        calibrated = task.wait(timeout_secs=120, poll_interval_secs=5)
        raw = task.wait_raw(timeout_secs=120, poll_interval_secs=5)

        assert len(calibrated) == len(raw) == 1
        for result in (calibrated[0], raw[0]):
            assert result.task_id in task.task_ids
            assert result.shots == 1000
            assert math.isclose(sum(result.probabilities.values()), 1.0, abs_tol=1e-9)
            assert result.result_status is not None
            assert len(result.result_status) >= 1000

        # Raw counts come directly from resultStatus and preserve the shot total.
        assert sum(raw[0].counts.values()) == 1000
        # Independent rounding after calibration can shift a two-qubit total by two.
        assert abs(sum(calibrated[0].counts.values()) - 1000) <= 2

    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            (None, CalibrationMode.AUTO),
            ("enabled", CalibrationMode.ENABLED),
            ("disabled", CalibrationMode.DISABLED),
        ],
    )
    def test_run_with_mode(self, backend, mode, expected) -> None:
        """Submit and complete jobs in each calibration mode."""
        task = backend.run_with_mode([SIMPLE_CIRCUIT], 100, mode)
        assert task.calibration_mode is expected
        assert task.wait(timeout_secs=120, poll_interval_secs=5)

    def test_platform_submit(self, platform: GuodunPlatform, backend) -> None:
        """Submit QCIS through the platform-level API and fetch raw results."""
        task = platform.submit(SIMPLE_CIRCUIT, shots=100, device_name=backend.name)
        assert task.device_name == backend.name
        assert task.wait_raw(timeout_secs=120, poll_interval_secs=5)

    @pytest.mark.parametrize("data_type", list(ResultDataType))
    def test_provider_result_data_types(self, backend, data_type) -> None:
        """Request each provider-specific result format and return it unchanged."""
        task = backend.run(
            ["X Q1\nX Q2\nM Q2\nM Q1"],
            shots=10,
            data_type=data_type,
        )
        payload = task.wait_payload(timeout_secs=120, poll_interval_secs=5)
        assert task.data_type is data_type
        assert len(payload) == 1
        assert payload[0]

    def test_batch_submission(self, backend) -> None:
        """A two-circuit batch must produce two tasks and two results."""
        task = backend.run([SIMPLE_CIRCUIT, SIMPLE_CIRCUIT], shots=100)
        results = task.wait_raw(timeout_secs=120, poll_interval_secs=5)
        assert len(task.task_ids) == len(results) == 2

    def test_status_and_payload(self, backend) -> None:
        """Read parsed status and the original platform response payload."""
        task = backend.run([SIMPLE_CIRCUIT], shots=100)
        payload = task.wait_payload(timeout_secs=120, poll_interval_secs=5)
        assert len(payload) == 1
        assert "resultStatus" in payload[0]
        assert task.status_raw()
        assert task.status()

    def test_saved_experiment(self, platform: GuodunPlatform, backend) -> None:
        """Create, save, and run an experiment, then retrieve its circuit."""
        suffix = uuid.uuid4().hex[:8]
        lab = platform.create_lab(f"cqlib-guodun-test-{suffix}", "SDK integration test")
        experiment = platform.save_experiment(
            lab_id=lab.id, circuit=SIMPLE_CIRCUIT, device_name=backend.name,
            name=f"experiment-{suffix}",
        )
        task = experiment.run(shots=100)
        assert task.wait_raw(timeout_secs=120, poll_interval_secs=5)
        circuits = platform.get_experiment_circuits(task.task_ids)
        assert circuits is not None

    def test_waveform(self, backend) -> None:
        """Generate a waveform and wait for its HTTP(S) URL."""
        waveform = backend.create_waveform(SIMPLE_CIRCUIT, name="cqlib-guodun-test")
        assert waveform.id
        assert waveform.wait(timeout=120, poll_interval=5).startswith(("http://", "https://"))


class TestLegacyCompatibilityLocal:
    """Check legacy properties and validation without network access."""

    def test_legacy_properties_and_set_machine(self) -> None:
        """Read legacy properties, set a token, and select a backend."""
        legacy = GuoDunPlatform(
            "offline-api-key",
            auto_login=False,
            config=GuodunConfig(save_credentials=False),
        )
        try:
            assert isinstance(legacy.platform, GuodunPlatform)
            assert legacy.access_token == ""

            legacy.access_token = "offline-access-token"
            assert legacy.access_token == "offline-access-token"

            legacy.set_machine("gd_qc1")
            assert legacy.machine_name == "gd_qc1"
        finally:
            legacy.close()

    def test_legacy_requires_machine_for_machine_operations(self) -> None:
        """Machine-specific methods must fail when no backend is selected."""
        legacy = GuoDunPlatform(
            "offline-api-key",
            auto_login=False,
            config=GuodunConfig(save_credentials=False),
        )
        try:
            calls = [
                lambda: legacy.download_config(),
                lambda: legacy.qcis_check_regular(SIMPLE_CIRCUIT),
                lambda: legacy.save_experiment("lab-id", SIMPLE_CIRCUIT),
                lambda: legacy.submit_job(SIMPLE_CIRCUIT),
                lambda: legacy.create_waveform_data(SIMPLE_CIRCUIT),
            ]
            for call in calls:
                with pytest.raises(GuodunValidationError, match="machine_name"):
                    call()
        finally:
            legacy.close()

    @pytest.mark.parametrize("language", ["qasm", "openqasm", "quil"])
    def test_legacy_rejects_non_qcis_languages(self, language: str) -> None:
        """Legacy save and submit methods must reject non-QCIS languages."""
        legacy = GuoDunPlatform(
            "offline-api-key",
            auto_login=False,
            machine_name="gd_qc1",
            config=GuodunConfig(save_credentials=False),
        )
        try:
            with pytest.raises(GuodunValidationError, match="only supports QCIS"):
                legacy.save_experiment(
                    "lab-id", SIMPLE_CIRCUIT, language=language
                )
            with pytest.raises(GuodunValidationError, match="only supports QCIS"):
                legacy.submit_job(SIMPLE_CIRCUIT, language=language)
        finally:
            legacy.close()

    def test_legacy_submit_validation(self) -> None:
        """Reject missing experiment IDs and unsupported simulator arguments."""
        legacy = GuoDunPlatform(
            "offline-api-key",
            auto_login=False,
            machine_name="gd_qc1",
            config=GuodunConfig(save_credentials=False),
        )
        try:
            with pytest.raises(GuodunValidationError, match="exp_id is required"):
                legacy.submit_job(circuit=None)
            with pytest.raises(GuodunValidationError, match="unsupported Guodun arguments"):
                legacy.submit_job(SIMPLE_CIRCUIT, noise={})
            with pytest.raises(GuodunValidationError, match="unsupported Guodun arguments"):
                legacy.submit_job(SIMPLE_CIRCUIT, quantum_state=True)
            with pytest.raises(GuodunValidationError, match="data_type"):
                legacy.submit_job(SIMPLE_CIRCUIT, data_type="counts")
        finally:
            legacy.close()

    def test_legacy_submit_forwards_result_data_type(self) -> None:
        """The old call style must forward ``data_type`` to the new platform."""
        legacy = GuoDunPlatform(
            "offline-api-key",
            auto_login=False,
            machine_name="gd_qc1",
            config=GuodunConfig(save_credentials=False),
        )
        captured = {}

        def fake_submit(circuits, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(task_ids=["task-event-iq"])

        legacy._platform.submit = fake_submit
        try:
            task_ids = legacy.submit_job(
                SIMPLE_CIRCUIT,
                num_shots=10,
                data_type=ResultDataType.EVENT_IQ,
            )
            assert task_ids == ["task-event-iq"]
            assert captured["data_type"] is ResultDataType.EVENT_IQ
        finally:
            legacy.close()


@pytest.mark.integration
class TestLegacyCompatibility:
    """Exercise the legacy facade against the live service."""

    def test_legacy_platform_read_only(
        self, api_key: str, domain: str | None, backend
    ) -> None:
        """Use legacy login, backend selection, configuration, and QCIS checks."""
        config = GuodunConfig(
            domain=domain or GuodunConfig().domain,
            save_credentials=False,
        )
        legacy = GuoDunPlatform(
            api_key,
            auto_login=False,
            config=config,
        )
        try:
            assert legacy.access_token == ""
            assert legacy.login()
            assert legacy.access_token
            assert isinstance(legacy.platform, GuodunPlatform)

            legacy.set_machine(backend.name)
            assert legacy.machine_name == backend.name
            assert legacy.download_config()
            assert legacy.download_config(machine=backend.name)
            assert legacy.qcis_check_regular(SIMPLE_CIRCUIT) is True
            assert legacy.query_quantum_computer_list()
            machine_config = legacy.get_machine_config(
                {
                    "type": "overview",
                    "computerCode": backend.name,
                    "label": (
                        "qpu_coordinate,coupler_map,"
                        "disabled_couplers,disabled_qubits"
                    ),
                }
            )
            assert isinstance(machine_config, dict)
            assert machine_config
            assert QuantumLanguage.QCIS.value == "qcis"
        finally:
            legacy.close()

    @pytest.mark.slow
    def test_legacy_submit_and_query(
        self, api_key: str, domain: str | None, backend
    ) -> None:
        """Submit QCIS through the legacy API and retrieve its result and circuit."""
        config = GuodunConfig(
            domain=domain or GuodunConfig().domain,
            save_credentials=False,
        )
        legacy = GuoDunPlatform(api_key, machine_name=backend.name, config=config)
        try:
            task_ids = legacy.submit_job(
                SIMPLE_CIRCUIT,
                exp_name="cqlib-guodun-legacy-submit-test",
                num_shots=100,
                language=QuantumLanguage.QCIS,
            )
            assert task_ids
            assert legacy.query_experiment(task_ids)
            assert legacy.get_experiment_circuit(task_ids) is not None
        finally:
            legacy.close()

    @pytest.mark.slow
    @pytest.mark.parametrize("data_type", list(ResultDataType))
    def test_legacy_result_data_types(
        self, api_key: str, domain: str | None, backend, data_type
    ) -> None:
        """Use the old ``submit_job`` call style for each new result format."""
        config = GuodunConfig(
            domain=domain or GuodunConfig().domain,
            save_credentials=False,
        )
        legacy = GuoDunPlatform(api_key, machine_name=backend.name, config=config)
        try:
            task_ids = legacy.submit_job(
                "X Q1\nX Q2\nM Q2\nM Q1",
                num_shots=10,
                data_type=data_type,
            )
            assert task_ids
            assert legacy.query_experiment(task_ids)
        finally:
            legacy.close()

    @pytest.mark.slow
    def test_legacy_saved_experiment_workflow(
        self, api_key: str, domain: str | None, backend
    ) -> None:
        """Create, save, and run an experiment through the legacy facade."""
        config = GuodunConfig(
            domain=domain or GuodunConfig().domain,
            save_credentials=False,
        )
        legacy = GuoDunPlatform(api_key, machine_name=backend.name, config=config)
        suffix = uuid.uuid4().hex[:8]
        try:
            lab_id = legacy.create_lab(
                f"cqlib-guodun-legacy-test-{suffix}",
                "Legacy SDK integration test",
            )
            assert lab_id

            experiment_id = legacy.save_experiment(
                lab_id,
                SIMPLE_CIRCUIT,
                name=f"legacy-experiment-{suffix}",
                language="qcis",
            )
            assert experiment_id

            task_ids = legacy.run_experiment(
                experiment_id,
                num_shots=100,
                is_verify=True,
            )
            assert task_ids
            assert legacy.query_experiment(task_ids)
            assert legacy.get_experiment_circuit(task_ids) is not None
        finally:
            legacy.close()

    @pytest.mark.slow
    def test_legacy_waveform_workflow(
        self, api_key: str, domain: str | None, backend
    ) -> None:
        """Generate a waveform through the legacy API and wait for its URL."""
        config = GuodunConfig(
            domain=domain or GuodunConfig().domain,
            save_credentials=False,
        )
        legacy = GuoDunPlatform(api_key, machine_name=backend.name, config=config)
        try:
            waveform_id = legacy.create_waveform_data(
                "X2P Q1\nM Q1",
                circuit_name="cqlib-guodun-legacy-waveform-test",
                is_verify=True,
            )
            assert waveform_id

            deadline = time.monotonic() + 120
            waveform_url = legacy.query_waveform_data(waveform_id)
            while waveform_url is None and time.monotonic() < deadline:
                time.sleep(2)
                waveform_url = legacy.query_waveform_data(waveform_id)
            assert waveform_url is not None
            assert waveform_url.startswith(("http://", "https://"))
        finally:
            legacy.close()
