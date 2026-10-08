# cqlib-guodun — Python 教程

本教程是使用 **cqlib-guodun** Python 库与**国盾量子云平台**交互的完整指南，涵盖认证、后端查询、QCIS 线路提交、结果获取、读取误差矫正、实验管理、波形与 Pulse 工具，以及旧版 API 兼容方式。

---

## 目录

1. [安装](#1-安装)
2. [认证](#2-认证)
3. [列举与检查后端](#3-列举与检查后端)
4. [提交线路](#4-提交线路)
5. [获取结果](#5-获取结果)
6. [读取误差矫正](#6-读取误差矫正)
7. [批量与参数化提交](#7-批量与参数化提交)
8. [进阶：CalibrationMode](#8-进阶calibrationmode)
9. [实验、波形与 Pulse](#9-实验波形与-pulse)
10. [旧版 API 兼容](#10-旧版-api-兼容)
11. [错误处理](#11-错误处理)
12. [运行测试](#12-运行测试)

---

## 1. 安装

### 通过 pip 安装（推荐）

```bash
pip install cqlib-guodun
```

### 从源码安装（开发）

```bash
cd cqlib-guodun
python -m pip install -e ".[test]"
```

---

## 2. 认证

### 2.1 首次登录

首次使用前，需要先打开[国盾量子计算云平台个人中心](https://quantumctek-cloud.com/user.html)，在 API Key（或 API Token）页面创建或复制自己的 API Key。`cqlib-guodun` 不会自动从本地或云平台获取 API Key，需要由用户主动取得并妥善保存。

首次登录时，可以直接把 API Key 赋值给变量：

```python
from cqlib_guodun import GuodunPlatform

# 替换成从云平台个人中心取得的真实 API Key。
api_key = "我的密钥……"
platform = GuodunPlatform.login(api_key)
```

API Key 属于敏感凭据，不要把包含真实密钥的代码提交到 Git 仓库、写入公开文档或输出到日志。

对于需要共享或提交的代码，建议改用环境变量，避免把密钥直接写入源代码：

```powershell
# Windows PowerShell：只对当前终端会话生效
$env:GUODUN_API_KEY="从云平台个人中心取得的 API Key"
```

```bash
# macOS/Linux：只对当前终端会话生效
export GUODUN_API_KEY="从云平台个人中心取得的 API Key"
```

设置完成后，在 Python 中读取该环境变量：

```python
import os
from cqlib_guodun import GuodunPlatform

# 这里只是读取用户事先设置的环境变量，不会自动申请或下载 API Key。
api_key = os.environ["GUODUN_API_KEY"]
platform = GuodunPlatform.login(api_key)
```

如果没有设置环境变量，`os.environ["GUODUN_API_KEY"]` 会抛出 `KeyError`。

首次调用 `GuodunPlatform.login()` 登录成功后，默认情况下认证凭据会持久化到 `%APPDATA%\cqlib\guodun\credentials.json`（Windows）或 `~/.cqlib/guodun/credentials.json`（macOS/Linux）。后续运行程序时不需要再次取得 API Key，也不需要再次调用 `login()`，可以直接使用：

```python
from cqlib_guodun import GuodunPlatform

platform = GuodunPlatform.from_credentials()
```

只有在设置了 `save_credentials=False`、凭据文件被删除或凭据无法刷新时，才需要重新调用 `GuodunPlatform.login()`。

### 2.2 复用已保存的凭据

后续运行时可以直接加载首次登录保存的凭据。Token 过期且启用自动刷新时，客户端会自动重新登录：

```python
platform = GuodunPlatform.from_credentials()
```

### 2.3 自定义认证选项

```python
# 凭据仅保存在内存中
platform = GuodunPlatform.login(
    api_key,
    save_credentials=False,
    auto_refresh=False,
)
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `domain` | `"quantumctek-cloud.com"` | 平台域名 |
| `scheme` | `"https"` | 请求协议 |
| `save_credentials` | `True` | 登录或刷新后是否保存凭据 |
| `auto_refresh` | `True` | Token 过期时是否自动重新登录 |
| `credentials_path` | 平台默认路径 | 自定义凭据文件路径 |

### 2.4 从自定义路径加载凭据

```python
platform = GuodunPlatform.from_credentials(
    credentials_path="/secure/vault/guodun.json"
)
```

---

## 3. 列举与检查后端

### 3.1 列举所有后端

```python
backends = platform.list_backends()

for backend in backends:
    print(
        f"{backend.name:20} "
        f"status={backend.status.value} "
        f"toll={backend.toll.value}"
    )
```

### 3.2 获取指定后端

```python
backend = platform.get_backend("gd_qc1")
print(f"已选择: {backend.name} ({backend.status.value})")
```

### 3.3 查看设备配置与拓扑

```python
device = backend.device_config()

print(f"物理比特数: {backend.num_qubits()}")
print(f"设备模型: {device}")
```

---

## 4. 提交线路

### 4.1 Bell 态示例

```python
# Bell 态 |Φ+⟩ = (|00⟩ + |11⟩) / √2
circuit = "H Q1\nH Q8\nCZ Q1 Q8\nH Q8\nM Q1\nM Q8"
shots = 1000

task = backend.run([circuit], shots)
print(f"任务 ID: {task.task_ids}")
```

`backend.run()` 立即返回 `TaskHandle`。线路在云端排队，结果通过轮询获取。

### 4.2 通过平台对象提交

```python
task = platform.submit(
    circuits=[circuit],
    shots=1000,
    device_name="gd_qc1",
)
```

### 4.3 使用 cqlib Circuit

除 QCIS 字符串外，也可以传入 `cqlib.Circuit`。提交前会通过 `cqlib.ir.qcis.dumps()` 序列化为 QCIS：

```python
from cqlib import Circuit, Qubit

circuit = Circuit(1)
circuit.h(Qubit(0))
circuit.measure(Qubit(0))

task = backend.run(circuit, shots=1000)
```

### 4.4 指定平台原始结果类型

国盾服务支持 `EVENT_IQ`、`IQ` 和 `P01` 三种专有结果类型。推荐使用公开枚举，字符串输入也会自动转为大写并校验：

```python
from cqlib_guodun import ResultDataType

task = backend.run(
    "X Q1\nM Q1",
    shots=100,
    data_type=ResultDataType.IQ,
)

# 平台级入口同样支持
task = platform.submit(
    "X Q1\nM Q1",
    shots=100,
    device_name="gd_qc1",
    data_type="EVENT_IQ",
)
```

> **特别注意：** 显式指定 `EVENT_IQ`、`IQ` 或 `P01` 后，只能通过 `task.wait_payload()` 获取结果数据，不能使用 `task.status()`、`task.wait()` 或 `task.wait_raw()`。具体获取方式请参见 [5.4 获取未经转换的 API 数据](#54-获取未经转换的-api-数据)。

---

## 5. 获取结果

### 5.1 阻塞等待

```python
results = task.wait(
    timeout_secs=120,       # 最大等待时间（秒）
    poll_interval_secs=5,   # 轮询间隔（秒）
)

for result in results:
    print(f"任务: {result.task_id}")
    print(f"计数: {result.counts}")
    print(f"概率: {result.probabilities}")
    print(f"国盾原始 shot 数据: {result.result_status}")
```

### 5.2 获取未矫正结果

```python
raw_results = task.wait_raw(
    timeout_secs=120,
    poll_interval_secs=5,
)
```

`wait_raw()` 优先从 `resultStatus` 逐 shot 数据统计原始 counts；

### 5.3 非阻塞状态查询

```python
partial = task.status()  # 可能少于已提交的线路数
print(f"{len(partial)}/{len(task.task_ids)} 已完成")
```

### 5.4 获取未经转换的 API 数据

```python
payloads = task.wait_payload(
    timeout_secs=120,
    poll_interval_secs=5,
)
```

显式指定 `data_type` 后，应使用 `wait_payload()` 获取平台返回的原始字典。IQ 类数据不能无损转换为普通的 counts/probabilities，因此这类任务调用 `status()`、`wait()` 或 `wait_raw()` 会抛出 `GuodunValidationError`。

---

## 6. 读取误差矫正

量子测量并非完美。对于每个被测量子比特 `k`，设备配置提供以下读取保真度：

| 保真度 | 符号 | 含义 |
|--------|------|------|
| `f00[k]` | F(0\|0) | 制备态为 0 时测量为 0 的概率 |
| `f11[k]` | F(1\|1) | 制备态为 1 时测量为 1 的概率 |

### 6.1 单比特混淆矩阵

```text
           测量为 0          测量为 1
制备态 0 [  f00[k]           1 - f00[k]  ]
制备态 1 [  1 - f11[k]       f11[k]      ]
```

多比特矫正对各被测比特的矩阵取 Kronecker 积，再使用逆矩阵估计真实概率。负值会截断为 0，最终概率重新归一化。

### 6.2 自动矫正

`wait()` 默认使用 `CalibrationMode.AUTO`：当后端存在读取校准数据且被测比特数不超过 14 时应用矫正；缺少校准数据或超过阈值时返回原始结果。

14 比特限制用于控制 O(4ⁿ) 的混淆矩阵内存占用。需要强制矫正时可使用 `enabled` 模式，但调用方应自行确认内存充足。

### 6.3 对比矫正与原始结果

```python
calibrated = task.wait(timeout_secs=120, poll_interval_secs=5)
raw = task.wait_raw(timeout_secs=120, poll_interval_secs=5)

for calibrated_result, raw_result in zip(calibrated, raw):
    print(f"矫正后: {calibrated_result.probabilities}")
    print(f"原始值: {raw_result.probabilities}")
```

矫正概率转换回整数 counts 时，各状态会独立四舍五入，因此矫正后的 counts 总和可能与 `shots` 有少量差异；`probabilities` 仍会归一化。

---

## 7. 批量与参数化提交

### 7.1 批量提交

每个请求最多提交 **50 条线路**。超过 50 条时，客户端会自动拆分批次：

```python
# 自动拆分为 50 + 50 + 20 三批
circuits = ["H Q0\nM Q0"] * 120
task = backend.run(circuits, shots=1000)

# 一次调用即可等待整批实验完成；结果查询也会自动按每批 50 个任务拆分。
results = task.wait(
    timeout_secs=300,
    poll_interval_secs=5,
)

print(f"提交任务数: {len(task.task_ids)}")
print(f"返回结果数: {len(results)}")
```

`task.task_ids` 包含整批实验的任务 ID，`task.wait()` 按照线路提交顺序返回所有任务的矫正结果；如果需要未矫正结果，可以改用 `task.wait_raw()`。

```python
for index, result in enumerate(results):
    print(
        f"线路 {index}: task_id={result.task_id}, "
        f"counts={result.counts}, "
        f"probabilities={result.probabilities}"
    )
```

### 7.2 参数化 QCIS

```python
task = backend.run(
    "RX Q0 {theta}\nM Q0",
    shots=1000,
    parameters=[["theta"]],
    values=[[1.25]],
)
```

参数替换在提交前完成。参数和值必须同时提供，并与线路数量一一对应。

---

## 8. 进阶：CalibrationMode

`CalibrationMode` 控制 `wait()` 的读取矫正行为：

| 枚举值 | 行为 |
|--------|------|
| `"auto"`（默认） | 有校准数据且被测比特数 ≤ 14 时矫正，否则返回原始结果 |
| `"enabled"` | 强制矫正；缺少校准数据时抛出 `GuodunValidationError` |
| `"disabled"` | 始终返回未矫正结果 |

```python
from cqlib_guodun import CalibrationMode

task = backend.run_with_mode([circuit], 1000, mode="enabled")
task = backend.run_with_mode([circuit], 1000, mode="disabled")
task = backend.run_raw([circuit], 1000)
task = backend.run_with_mode(
    [circuit],
    1000,
    mode=CalibrationMode.AUTO,
)
```

`run_raw(circuits, shots)` 等价于 `run_with_mode(circuits, shots, mode="disabled")`。它会按正常流程提交一个新任务，并关闭该任务的本地读取误差矫正；它不会重跑已经存在的远端任务。对于已经提交的任意任务，也可以调用 `wait_raw()` 获取未矫正结果。

---

## 9. 实验、波形与 Pulse

### 9.1 创建实验室并保存实验

```python
lab = platform.create_lab("example-lab", "SDK example")

experiment = platform.save_experiment(
    lab_id=lab.id,
    circuit="H Q0\nM Q0",
    device_name=backend.name,
    name="example-experiment",
)

task = experiment.run(shots=1000)
results = task.wait_raw(timeout_secs=120)
```

### 9.2 生成波形

```python
waveform = backend.create_waveform(
    "X2P Q0\nM Q0",
    name="waveform-example",
)
url = waveform.wait(timeout=120, poll_interval=2)
```

### 9.3 Pulse 工具

Pulse 方法通过 `backend.get_pulse()` 使用：

```python
pulse = backend.get_pulse()

parameter = pulse.get_gate_pulse_parameter("X2P", "Q1")
template = pulse.get_gate_pulse_qcis_template("X2P")
f01 = pulse.get_qubit_f01("Q1")
samples = pulse.get_z_amp_samples("Q1", 0, 20, 0.2, 0)
```

`get_pulse()` 会缓存并返回同一个 `GetPulse` 对象。创建对象时会读取当前后端的设备配置；支持的电子学类型为 `EZQ1.0`、`EZQ2.0` 和 `EZQ2.1`。

#### 门参数与波形

| 方法 | 说明 |
|------|------|
| `get_gate_pulse_parameter(gate, qagent)` | 获取 `X2P`、`X2M`、`Y2P`、`Y2M` 或 `CZ` 的 Pulse 参数字典 |
| `get_gate_pulse_qcis_template(gate)` | 获取上述门对应的 QCIS Pulse 模板 |
| `create_and_get_waveform_diagram(circuit, is_show=True, max_retries=10, file_name=None, is_verify=True)` | 提交线路、等待波形 URL，并按需显示波形或保存为 HTML |
| `get_z_amp_samples(qagent, wave_type, length, amplitude, call_mapper, wave_params=None)` | 生成量子比特或耦合器 Z 通道的幅度采样序列 |

`get_z_amp_samples()` 的 `length` 单位为纳秒，`wave_type` 取值如下：

| `wave_type` | 波形 | `wave_params` |
|-------------|------|---------------|
| `0` | Cosine | 不需要 |
| `1` | Flattop | 边沿时间 `edge` |
| `2` | Slepian | `[thf, thi, lam2, lam3]` |

当 `call_mapper=1` 时，方法会根据对象类型应用硬件标定映射；量子比特使用 `f01_shift_2_detune()`，耦合器使用 `coupler_strength_2_zpulse_amp()`。

#### 量子比特与耦合器查询

| 方法 | 说明 |
|------|------|
| `get_coupler_label(qubit0, qubit1)` | 获取连接两个量子比特且未被禁用的耦合器名称 |
| `get_coupler_adjacent_qubits(coupler)` | 获取指定耦合器连接的两个量子比特 |
| `get_coupling_strength_range(coupler)` | 获取指定耦合器的可用耦合强度范围 |
| `get_qubit_f01(qubit)` | 获取量子比特工作点的 `f01` |
| `get_qubit_fah(qubit)` | 获取量子比特的 `fah` |
| `get_qagent_workbias(qagent)` | 获取量子比特或耦合器的工作偏置幅度 |
| `get_qubit_f01_range(qubit)` | 获取量子比特在有效 Z 幅度范围内的 `(f01_min, f01_max)` |
| `get_qagent_zbias_amp_range(qagent)` | 获取量子比特或耦合器的有效 Z 偏置幅度范围 |
| `get_channel_sample_rate(channel_type)` | 获取 `XY` 或 `Z` 通道的采样率，单位为 Hz |

#### 标定映射

以下方法同时接受标量、列表或 NumPy 数组；标量输入返回 `float`，批量输入返回 NumPy 数组：

| 方法 | 说明 |
|------|------|
| `f01_shift_2_detune(qubit, d_f01)` | 将量子比特频率偏移映射为 Z Pulse 幅度 |
| `detune_2_f01_shift(qubit, dtn)` | 将量子比特 Z Pulse 幅度映射为频率偏移 |
| `coupler_strength_2_zpulse_amp(coupler, g)` | 将耦合强度映射为耦合器 Z Pulse 幅度 |
| `zpulse_amp_2_coupler_strength(coupler, amp)` | 将耦合器 Z Pulse 幅度映射为耦合强度 |

输入对象不存在、已被禁用或超出设备标定范围时，这些方法会抛出 `ValueError` 或 `KeyError`。

---

## 10. 旧版 API 兼容

为兼容基于旧版 cqlib 国盾接口的现有代码，`cqlib-guodun` 保留了 `GuoDunPlatform` 类及其国盾 API 调用方式。

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

构造参数如下：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `login_key` | 无 | 国盾 API Key |
| `auto_login` | `True` | 初始化时是否立即登录 |
| `machine_name` | `None` | 默认设备名称；提交、校验和下载配置前必须设置 |
| `config` | `None` | 可选的 `GuodunConfig`；兼容类默认不持久化凭据 |

也可以在创建对象后切换设备：

```python
platform.set_machine("gd_qc1")
```

### 10.1 认证与对象管理

| 属性或方法 | 说明 |
|------------|------|
| `access_token` | 读取或设置当前访问令牌 |
| `platform` | 获取兼容层内部使用的新 `GuodunPlatform` 对象 |
| `login(timeout=60.0)` | 登录并返回访问令牌 |
| `set_machine(machine_name)` | 设置后续操作使用的设备 |
| `close()` | 关闭底层 HTTP 客户端 |

### 10.2 实验与任务

| 方法 | 说明 |
|------|------|
| `create_lab(name, remark="")` | 创建实验室并返回实验室 ID |
| `save_experiment(lab_id, circuit, name="", language=QuantumLanguage.QCIS)` | 保存 QCIS 实验并返回实验 ID |
| `run_experiment(exp_id, num_shots=12000, is_verify=True)` | 运行已保存的实验并返回任务 ID 列表 |
| `submit_job(...)` | 直接提交一条或多条 QCIS；也可通过 `exp_id` 运行已保存实验 |
| `query_experiment(query_id, max_wait_time=120.0, sleep_time=5.0)` | 等待一个或多个任务完成并返回原始 API 结果列表 |
| `get_experiment_circuit(query_id)` | 根据任务 ID 获取实验 QCIS |

`submit_job()` 支持 `exp_name`、`parameters`、`values`、`num_shots`、`lab_id`、`exp_id`、`version`、`is_verify` 和 `data_type`。`data_type` 接受 `EVENT_IQ`、`IQ`、`P01` 或对应的 `ResultDataType` 枚举。`language` 仅接受 `QuantumLanguage.QCIS` 或字符串 `"qcis"`。

### 10.3 设备、校验与波形

| 方法 | 说明 |
|------|------|
| `download_config(read_time=None, machine=None)` | 下载指定设备或当前设备的配置 |
| `qcis_check_regular(qcis_raw)` | 调用平台接口校验 QCIS，返回布尔值 |
| `query_quantum_computer_list()` | 查询量子计算机列表，并以兼容的二维列表格式返回 |
| `get_machine_config(params=None)` | 获取平台的公共设备配置数据 |
| `create_waveform_data(circuit, circuit_name=None, is_verify=True)` | 创建波形任务并返回波形任务 ID |
| `query_waveform_data(query_id)` | 查询波形任务；未完成时返回 `None`，完成时返回波形 URL |

旧版 Pulse 导入路径继续可用：

```python
from cqlib_guodun.utils.get_pulse import GetPulse

pulse = GetPulse(platform=platform)
print(pulse.get_qubit_f01_range("Q1"))
```

新代码应通过 `backend.get_pulse()` 获取 Pulse 对象；

---

## 11. 错误处理

所有公开异常都继承自 `GuodunError`：

```text
GuodunError
├── GuodunAuthError
├── GuodunRequestError
├── GuodunApiError
├── GuodunValidationError
└── GuodunTimeoutError
```

可以捕获具体异常：

```python
from cqlib_guodun import GuodunAuthError, GuodunPlatform

try:
    platform = GuodunPlatform.login("invalid-api-key")
except GuodunAuthError as exc:
    print(f"登录失败: {exc}")
```

| 场景 | 异常类型 |
|------|----------|
| 登录或凭据失败 | `GuodunAuthError` |
| 网络或 HTTP 错误 | `GuodunRequestError` |
| 平台返回非零业务码 | `GuodunApiError` |
| 参数、语言或后端无效 | `GuodunValidationError` |
| 任务或波形等待超时 | `GuodunTimeoutError` |

---

## 12. 运行测试

### 12.1 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `GUODUN_API_KEY` | 集成测试 | 国盾平台 API Key |
| `GUODUN_DOMAIN` | 可选 | 覆盖默认平台域名 |
| `GUODUN_DEVICE` | 可选 | 测试后端名称，如 `gd_qc1` |
| `PYTEST_RUN_SLOW` | 慢测试 | 设置为 `1` 后允许创建资源和提交任务 |

### 12.2 运行测试

```bash
# 离线测试：不使用 HTTP mock，也不访问真实服务
python -m pytest tests/ -m "not integration" -v

# 只读集成测试
GUODUN_API_KEY="your-key" GUODUN_DEVICE="gd_qc1" \
python -m pytest tests/ -m "integration and not slow" -v

# 全部集成测试：会提交任务并创建远端资源
GUODUN_API_KEY="your-key" GUODUN_DEVICE="gd_qc1" PYTEST_RUN_SLOW=1 \
python -m pytest tests/ -m integration -v
```


---

## 完整示例

```python
import os
from cqlib_guodun import GuodunPlatform

# 1. 登录
api_key = os.environ["GUODUN_API_KEY"]
platform = GuodunPlatform.login(api_key, save_credentials=False)


# 2. 选择后端
backend_name = os.getenv("GUODUN_DEVICE", "gd_qc1")
backend = platform.get_backend(backend_name)

# 3. 查看设备信息
print(f"设备: {backend.name}")
print(f"状态: {backend.status.value}")
print(f"物理比特数: {backend.num_qubits()}")

# 4. 提交 Bell 态线路
circuit = "H Q1\nH Q8\nCZ Q1 Q8\nH Q8\nM Q1\nM Q8"
task = backend.run([circuit], shots=1000)
results = task.wait(timeout_secs=120, poll_interval_secs=5)
# 5. 等待并打印矫正结果
for result in results:
    print(f"任务 ID: {result.task_id}")
    print(f"计数: {result.counts}")
    print(f"概率: {result.probabilities}")
```

---

## 许可证

Apache License 2.0。移植自旧 `cqlib` 的代码保留原版权和修改声明。
