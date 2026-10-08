# cqlib-guodun — Python Tutorial

`cqlib-guodun` provides Python access to Guodun quantum-computing services through the unified cqlib interface. It supports Python applications and QCIS circuits.

---

## Table of Contents

1. [Installation](#1-installation)
2. [Authentication](#2-authentication)
3. [Listing and Inspecting Backends](#3-listing-and-inspecting-backends)
4. [Submitting Circuits](#4-submitting-circuits)
5. [Retrieving Results](#5-retrieving-results)
6. [Readout Error Calibration](#6-readout-error-calibration)
7. [Batch and Parameterized Submission](#7-batch-and-parameterized-submission)
8. [Advanced: CalibrationMode](#8-advanced-calibrationmode)
9. [Experiments, Waveforms, and Pulse](#9-experiments-waveforms-and-pulse)
10. [Legacy API Compatibility](#10-legacy-api-compatibility)
11. [Error Handling](#11-error-handling)
12. [Running Tests](#12-running-tests)

---

## 1. Installation

Install the package with pip:

```bash
pip install cqlib-guodun
```

For local development, install the source tree in editable mode with the test dependencies:

```bash
cd cqlib-guodun
python -m pip install -e ".[test]"
```

---

## 2. Authentication

### 2.1 First login

Before the first login, open the [Guodun quantum cloud personal center](https://quantumctek-cloud.com/user.html) and create or copy your API key from the API Key (or API Token) page. `cqlib-guodun` does not automatically obtain an API key from the local computer or the cloud platform; the user must obtain and protect it.

For the first login, the API key can be assigned directly:

```python
from cqlib_guodun import GuodunPlatform

# Replace this placeholder with the real API key from the cloud platform.
api_key = "my-api-key..."
platform = GuodunPlatform.login(api_key)
```

The API key is a secret: do not commit source code containing a real key to Git, publish it in documentation, or write it to logs.

For code that will be shared or committed, use an environment variable instead of embedding the key:

```powershell
# Windows PowerShell: applies to the current terminal session only
$env:GUODUN_API_KEY="API key obtained from the cloud platform"
```

```bash
# macOS/Linux: applies to the current terminal session only
export GUODUN_API_KEY="API key obtained from the cloud platform"
```

Then read the variable from Python:

```python
import os
from cqlib_guodun import GuodunPlatform

# This only reads a variable set by the user; it does not request or download a key.
api_key = os.environ["GUODUN_API_KEY"]
platform = GuodunPlatform.login(api_key)
```

If the variable is not set, `os.environ["GUODUN_API_KEY"]` raises `KeyError`.

After the first successful `GuodunPlatform.login()`, credentials are saved by default to `%APPDATA%\cqlib\guodun\credentials.json` on Windows or `~/.cqlib/guodun/credentials.json` on macOS and Linux. Later runs do not need the API key or another explicit `login()` call:

```python
from cqlib_guodun import GuodunPlatform

platform = GuodunPlatform.from_credentials()
```

Call `GuodunPlatform.login()` again only if `save_credentials=False` was used, the credential file was removed, or the saved credentials cannot be refreshed.

### 2.2 Reuse saved credentials

Later processes can load the credentials saved by the first login. When automatic refresh is enabled, an expired token causes the client to log in again automatically:

```python
platform = GuodunPlatform.from_credentials()
```

### 2.3 Customize authentication

```python
platform = GuodunPlatform.login(
    api_key,
    save_credentials=False,
    auto_refresh=False,
)
```

| Option | Default | Description |
| --- | --- | --- |
| `domain` | `"quantumctek-cloud.com"` | Platform domain. |
| `scheme` | `"https"` | Request scheme. |
| `save_credentials` | `True` | Save credentials after login or refresh. |
| `auto_refresh` | `True` | Log in again when the token expires. |
| `credentials_path` | Platform default | Custom credential-file path. |

### 2.4 Load credentials from a custom path

```python
platform = GuodunPlatform.from_credentials(
    credentials_path="/secure/vault/guodun.json"
)
```

---

## 3. Listing and Inspecting Backends

List the available backends:

```python
backends = platform.list_backends()

for backend in backends:
    print(
        backend.name,
        backend.display_name,
        backend.status,
        backend.toll,
    )
```

Get a backend by name:

```python
backend = platform.get_backend("gd_qc1")
```

Inspect the device configuration and topology:

```python
device = backend.device_config()

print(backend.num_qubits())
print(device)
```

---

## 4. Submitting Circuits

### 4.1 Bell-state example

```python
# Bell state |Phi+> = (|00> + |11>) / sqrt(2)
circuit = "H Q1\nH Q8\nCZ Q1 Q8\nH Q8\nM Q1\nM Q8"
shots = 1000

task = backend.run([circuit], shots)
print(f"Task IDs: {task.task_ids}")
```

`backend.run()` returns a `TaskHandle` immediately. The circuits are queued remotely and results are retrieved by polling.

### 4.2 Submit through the platform

```python
task = platform.submit(
    circuits=[circuit],
    shots=1000,
    device_name="gd_qc1",
)
```

### 4.3 Use a cqlib Circuit

In addition to QCIS strings, `cqlib.Circuit` objects are accepted and serialized with `cqlib.ir.qcis.dumps()` before submission:

```python
from cqlib import Circuit, Qubit

circuit = Circuit(1)
circuit.h(Qubit(0))
circuit.measure(Qubit(0))

task = backend.run(circuit, shots=1000)
```

### 4.4 Request a provider-specific result type

The Guodun service supports `EVENT_IQ`, `IQ`, and `P01` results. Prefer the public enum; string inputs are normalized to uppercase and validated:

```python
from cqlib_guodun import ResultDataType

task = backend.run(
    "X Q1\nM Q1",
    shots=100,
    data_type=ResultDataType.IQ,
)

# The platform-level entry point supports the same option.
task = platform.submit(
    "X Q1\nM Q1",
    shots=100,
    device_name="gd_qc1",
    data_type="EVENT_IQ",
)
```

> **Important:** When `EVENT_IQ`, `IQ`, or `P01` is explicitly selected, results can only be retrieved with `task.wait_payload()`. Do not use `task.status()`, `task.wait()`, or `task.wait_raw()`. See [5.4 Retrieve unconverted API data](#54-retrieve-unconverted-api-data) for details.

---

## 5. Retrieving Results

### 5.1 Wait for calibrated results

```python
results = task.wait(
    timeout_secs=120,
    poll_interval_secs=5,
)

for result in results:
    print(f"Task: {result.task_id}")
    print(f"Counts: {result.counts}")
    print(f"Probabilities: {result.probabilities}")
    print(f"Guodun shot data: {result.result_status}")
```

### 5.2 Wait for uncalibrated results

```python
raw_results = task.wait_raw(
    timeout_secs=120,
    poll_interval_secs=5,
)
```

`wait_raw()` builds raw counts from the per-shot `resultStatus` data when it is available.

### 5.3 Query status without blocking

```python
partial = task.status()  # May contain fewer results than submitted circuits
print(f"{len(partial)}/{len(task.task_ids)} completed")
```

### 5.4 Retrieve unconverted API data

```python
payload = task.wait_payload(
    timeout_secs=120,
    poll_interval_secs=5,
)
print(payload)
```

When `data_type` is explicitly selected, use `wait_payload()` to retrieve the provider response dictionaries. IQ data cannot be converted losslessly into ordinary counts and probabilities, so `status()`, `wait()`, and `wait_raw()` raise `GuodunValidationError` for these tasks.


---

## 6. Readout Error Calibration

Quantum measurements are imperfect. For each measured qubit `k`, the device configuration provides these readout fidelities:

| Fidelity | Symbol | Meaning |
| --- | --- | --- |
| `f00[k]` | F(0\|0) | Probability of measuring 0 when the prepared state is 0. |
| `f11[k]` | F(1\|1) | Probability of measuring 1 when the prepared state is 1. |

### 6.1 Single-qubit confusion matrix

```text
                 measured 0       measured 1
prepared 0     [ f00[k]            1 - f00[k] ]
prepared 1     [ 1 - f11[k]        f11[k]     ]
```

For multiple qubits, the client takes the Kronecker product of the measured-qubit matrices and uses its inverse to estimate the true probabilities. Negative values are clipped to zero and the final probabilities are normalized.

### 6.2 Automatic calibration

`wait()` uses `CalibrationMode.AUTO` by default. Calibration is applied when readout data is available and no more than 14 qubits are measured. Otherwise, raw results are returned.

The 14-qubit limit controls the O(4^n) memory cost of the confusion matrix. `enabled` mode can force calibration, but callers should first ensure that enough memory is available.

### 6.3 Compare calibrated and raw results

```python
calibrated = task.wait(timeout_secs=120, poll_interval_secs=5)
raw = task.wait_raw(timeout_secs=120, poll_interval_secs=5)

for calibrated_result, raw_result in zip(calibrated, raw):
    print(f"Calibrated: {calibrated_result.probabilities}")
    print(f"Raw: {raw_result.probabilities}")
```

Calibration changes the count distribution but does not change the requested `shots` value. Corrected probabilities are rounded independently when converted back to integer counts, so their sum can differ slightly from `shots`; `probabilities` remains normalized.

---

## 7. Batch and Parameterized Submission

### 7.1 Submit several circuits

```python
circuits = [
    "H Q0\nM Q0",
    "X Q1\nM Q1",
]

task = backend.run(circuits, shots=1000)
results = task.wait(
    timeout_secs=300,
    poll_interval_secs=5,
)

print(f"Submitted tasks: {len(task.task_ids)}")
print(f"Returned results: {len(results)}")
```

A single provider request can contain up to 50 circuits. Larger input sequences are split into batches automatically. One `task.wait()` call waits for the entire submission, and result queries are also split into groups of at most 50 task IDs. Results are returned in circuit-submission order. Use `task.wait_raw()` instead when uncalibrated results are required.

```python
for index, result in enumerate(results):
    print(
        f"Circuit {index}: task_id={result.task_id}, "
        f"counts={result.counts}, "
        f"probabilities={result.probabilities}"
    )
```

### 7.2 Expand circuit parameters

Use Python format fields in a QCIS template and provide parameter rows:

```python
circuit_template = """\
RX Q0 {theta}
M Q0
"""

task = backend.run(
    circuit_template,
    shots=1000,
    parameters=[["theta"]],
    values=[[1.5708]],
)
```

`parameters` and `values` must both be supplied, with one row for each input circuit.

---

## 8. Advanced: CalibrationMode

`CalibrationMode` controls local result processing:

| Mode | Behavior |
| --- | --- |
| `CalibrationMode.AUTO` | Calibrates when the measured-qubit count is within the supported threshold. |
| `CalibrationMode.ENABLED` | Requests calibration and raises an error if calibration cannot be applied. |
| `CalibrationMode.DISABLED` | Returns uncalibrated counts. |

Example:

```python
from cqlib_guodun import CalibrationMode

task = backend.run_with_mode(
    "X Q0\nM Q0",
    shots=2000,
    mode=CalibrationMode.DISABLED,
)

raw_task = backend.run_raw("X Q0\nM Q0", shots=2000)

results = task.wait()
```

`run_raw(circuits, shots)` is equivalent to `run_with_mode(circuits, shots, mode="disabled")`. It submits a new task normally and disables local readout calibration for that task; it does not rerun an existing remote task. Calling `wait_raw()` on any existing task also returns uncalibrated results.

---

## 9. Experiments, Waveforms, and Pulse

### 9.1 Create a lab and save an experiment

```python
lab = platform.create_lab(
    name="example-lab",
    remark="SDK example",
)

experiment = platform.save_experiment(
    lab_id=lab.id,
    circuit="H Q0\nM Q0",
    device_name=backend.name,
    name="example-experiment",
)
```

Run the saved experiment:

```python
task = experiment.run(shots=1000)
results = task.wait_raw(timeout_secs=120)
```

### 9.2 Create a waveform

```python
waveform = backend.create_waveform(
    "X2P Q0\nM Q0",
    name="waveform-example",
)
url = waveform.wait(timeout=120, poll_interval=2)
```

### 9.3 Use Pulse features

Pulse access is backend-specific:

```python
pulse = backend.get_pulse()

print(pulse.get_qubit_f01_range("Q1"))
print(pulse.get_gate_pulse_qcis_template("X2P"))
```

`get_pulse()` caches and returns the same `GetPulse` object. The object loads the selected backend's configuration when it is created. Supported electronics types are `EZQ1.0`, `EZQ2.0`, and `EZQ2.1`.

#### Gate parameters and waveforms

| Method | Description |
| --- | --- |
| `get_gate_pulse_parameter(gate, qagent)` | Return the Pulse parameters for `X2P`, `X2M`, `Y2P`, `Y2M`, or `CZ`. |
| `get_gate_pulse_qcis_template(gate)` | Return the QCIS Pulse template for one of the supported gates. |
| `create_and_get_waveform_diagram(circuit, is_show=True, max_retries=10, file_name=None, is_verify=True)` | Submit a circuit, wait for its waveform URL, and optionally display it or save it as HTML. |
| `get_z_amp_samples(qagent, wave_type, length, amplitude, call_mapper, wave_params=None)` | Generate Z-channel amplitude samples for a qubit or coupler. |

For `get_z_amp_samples()`, `length` is measured in nanoseconds and `wave_type` has these values:

| `wave_type` | Waveform | `wave_params` |
| --- | --- | --- |
| `0` | Cosine | Not required |
| `1` | Flattop | Edge duration, `edge` |
| `2` | Slepian | `[thf, thi, lam2, lam3]` |

When `call_mapper=1`, the method applies the hardware calibration mapping for the target: `f01_shift_2_detune()` for a qubit or `coupler_strength_2_zpulse_amp()` for a coupler.

#### Qubit and coupler queries

| Method | Description |
| --- | --- |
| `get_coupler_label(qubit0, qubit1)` | Return the enabled coupler connecting two qubits. |
| `get_coupler_adjacent_qubits(coupler)` | Return the two qubits connected to a coupler. |
| `get_coupling_strength_range(coupler)` | Return the available coupling-strength range for a coupler. |
| `get_qubit_f01(qubit)` | Return the qubit's working-point `f01`. |
| `get_qubit_fah(qubit)` | Return the qubit's `fah`. |
| `get_qagent_workbias(qagent)` | Return the working-bias amplitude for a qubit or coupler. |
| `get_qubit_f01_range(qubit)` | Return `(f01_min, f01_max)` over the valid Z-amplitude range. |
| `get_qagent_zbias_amp_range(qagent)` | Return the valid Z-bias amplitude range for a qubit or coupler. |
| `get_channel_sample_rate(channel_type)` | Return the `XY` or `Z` channel sample rate in Hz. |

#### Calibration mappings

The following methods accept a scalar, list, or NumPy array. A scalar input returns a `float`; a batch input returns a NumPy array.

| Method | Description |
| --- | --- |
| `f01_shift_2_detune(qubit, d_f01)` | Map a qubit frequency shift to a Z-pulse amplitude. |
| `detune_2_f01_shift(qubit, dtn)` | Map a qubit Z-pulse amplitude to a frequency shift. |
| `coupler_strength_2_zpulse_amp(coupler, g)` | Map coupling strength to a coupler Z-pulse amplitude. |
| `zpulse_amp_2_coupler_strength(coupler, amp)` | Map a coupler Z-pulse amplitude to coupling strength. |

These methods raise `ValueError` or `KeyError` when a target is unknown, disabled, or outside the device's calibrated range.

---

## 10. Legacy API Compatibility

The legacy class name remains available for existing applications:

```python
import os
from cqlib_guodun import GuoDunPlatform

platform = GuoDunPlatform(
    login_key=os.environ["GUODUN_API_KEY"],
    machine_name="gd_qc1",
)

query_ids = platform.submit_job(
    circuit="H Q0\nM Q0",
    num_shots=1000,
)

payloads = platform.query_experiment(
    query_ids,
    max_wait_time=120,
    sleep_time=5,
)
```

Constructor options:

| Parameter | Default | Description |
| --- | --- | --- |
| `login_key` | Required | Guodun API key. |
| `auto_login` | `True` | Log in during initialization. |
| `machine_name` | `None` | Default device; required before submission, validation, or configuration download. |
| `config` | `None` | Optional `GuodunConfig`; the compatibility class does not persist credentials by default. |

The device can also be selected after construction:

```python
platform.set_machine("gd_qc1")
```

### 10.1 Authentication and object lifecycle

| Property or method | Description |
| --- | --- |
| `access_token` | Read or replace the current access token. |
| `platform` | Return the new `GuodunPlatform` used internally by the compatibility layer. |
| `login(timeout=60.0)` | Log in and return the access token. |
| `set_machine(machine_name)` | Select the device used by subsequent operations. |
| `close()` | Close the underlying HTTP client. |

### 10.2 Experiments and tasks

| Method | Description |
| --- | --- |
| `create_lab(name, remark="")` | Create a lab and return its ID. |
| `save_experiment(lab_id, circuit, name="", language=QuantumLanguage.QCIS)` | Save a QCIS experiment and return its ID. |
| `run_experiment(exp_id, num_shots=12000, is_verify=True)` | Run a saved experiment and return its task IDs. |
| `submit_job(...)` | Submit one or more QCIS circuits directly, or run a saved experiment through `exp_id`. |
| `query_experiment(query_id, max_wait_time=120.0, sleep_time=5.0)` | Wait for one or more tasks and return the raw API result list. |
| `get_experiment_circuit(query_id)` | Retrieve experiment QCIS by task ID. |

`submit_job()` supports `exp_name`, `parameters`, `values`, `num_shots`, `lab_id`, `exp_id`, `version`, `is_verify`, and `data_type`. `data_type` accepts `EVENT_IQ`, `IQ`, `P01`, or the matching `ResultDataType` enum. `language` only accepts `QuantumLanguage.QCIS` or the string `"qcis"`.

### 10.3 Devices, validation, and waveforms

| Method | Description |
| --- | --- |
| `download_config(read_time=None, machine=None)` | Download configuration for an explicit device or the selected device. |
| `qcis_check_regular(qcis_raw)` | Validate QCIS through the platform and return a Boolean result. |
| `query_quantum_computer_list()` | Return the quantum-computer list in the legacy two-dimensional list format. |
| `get_machine_config(params=None)` | Retrieve public device-configuration data from the platform. |
| `create_waveform_data(circuit, circuit_name=None, is_verify=True)` | Create a waveform request and return its ID. |
| `query_waveform_data(query_id)` | Return `None` while pending or the waveform URL when ready. |

The legacy Pulse import remains available:

```python
from cqlib_guodun.utils.get_pulse import GetPulse

pulse = GetPulse(platform=platform)
print(pulse.get_qubit_f01_range("Q1"))
```

New code should obtain Pulse through `backend.get_pulse()`.

---

## 11. Error Handling

All public exceptions derive from `GuodunError`:

```text
GuodunError
├── GuodunAuthError
├── GuodunRequestError
├── GuodunApiError
├── GuodunValidationError
└── GuodunTimeoutError
```

Catch a specific exception when appropriate:

```python
from cqlib_guodun import GuodunAuthError, GuodunPlatform

try:
    platform = GuodunPlatform.login("invalid-api-key")
except GuodunAuthError as exc:
    print(f"Login failed: {exc}")
```

| Scenario | Exception |
| --- | --- |
| Login or credential failure | `GuodunAuthError` |
| Network or HTTP error | `GuodunRequestError` |
| Non-zero provider response code | `GuodunApiError` |
| Invalid argument, language, or backend | `GuodunValidationError` |
| Task or waveform wait timeout | `GuodunTimeoutError` |

---

## 12. Running Tests

### 12.1 Environment variables

| Variable | Required for | Description |
| --- | --- | --- |
| `GUODUN_API_KEY` | Integration tests | Guodun API key. |
| `GUODUN_DOMAIN` | Optional | Override the default platform domain. |
| `GUODUN_DEVICE` | Optional | Test backend name, such as `gd_qc1`. |
| `PYTEST_RUN_SLOW` | Slow tests | Set to `1` to allow resource creation and task submission. |

### 12.2 Commands

```bash
# Offline tests: no HTTP mocks and no real-service access
python -m pytest tests/ -m "not integration" -v

# Read-only integration tests
GUODUN_API_KEY="your-key" GUODUN_DEVICE="gd_qc1" \
python -m pytest tests/ -m "integration and not slow" -v

# All integration tests: submits tasks and creates remote resources
GUODUN_API_KEY="your-key" GUODUN_DEVICE="gd_qc1" PYTEST_RUN_SLOW=1 \
python -m pytest tests/ -m integration -v
```

Real API tests depend on account permissions, device availability, and the platform service. Offline results do not replace real-service validation.

---

## Complete Example

```python
import os
from cqlib_guodun import GuodunPlatform

api_key = os.environ["GUODUN_API_KEY"]
platform = GuodunPlatform.login(api_key, save_credentials=False)

backend_name = os.getenv("GUODUN_DEVICE", "gd_qc1")
backend = platform.get_backend(backend_name)

print(f"Device: {backend.name}")
print(f"Status: {backend.status.value}")
print(f"Physical qubits: {backend.num_qubits()}")

circuit = "H Q1\nH Q8\nCZ Q1 Q8\nH Q8\nM Q1\nM Q8"
task = backend.run([circuit], shots=1000)
results = task.wait(timeout_secs=120, poll_interval_secs=5)

for result in results:
    print(f"Task ID: {result.task_id}")
    print(f"Counts: {result.counts}")
    print(f"Probabilities: {result.probabilities}")
```

---

## License

Apache License 2.0. Code ported from the legacy `cqlib` retains its original copyright and modification notices.
