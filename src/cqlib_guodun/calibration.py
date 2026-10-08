# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Readout-error mitigation matching the Tianyan client algorithm."""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import numpy as np

AUTO_CALIBRATION_MAX_QUBITS = 14


def extract_readout_calibration(
    config: Mapping[str, Any],
) -> dict[int, tuple[float, float]] | None:
    """Extract hardware-qubit ``(F00, F11)`` pairs from device config."""
    readout = config.get("readout")
    if not isinstance(readout, Mapping):
        return None
    readout_array = readout.get("readoutArray")
    if not isinstance(readout_array, Mapping):
        return None
    fidelity_0 = readout_array.get("|0> readout fidelity")
    fidelity_1 = readout_array.get("|1> readout fidelity")
    if not isinstance(fidelity_0, Mapping) or not isinstance(fidelity_1, Mapping):
        return None

    names_0 = fidelity_0.get("qubit_used")
    values_0 = fidelity_0.get("param_list")
    names_1 = fidelity_1.get("qubit_used")
    values_1 = fidelity_1.get("param_list")
    if any(
        not isinstance(value, Sequence) or isinstance(value, (str, bytes))
        for value in (names_0, values_0, names_1, values_1)
    ):
        return None
    if len(names_0) != len(values_0) or len(names_1) != len(values_1):
        return None

    f11_by_name: dict[str, float] = {}
    try:
        for name, value in zip(names_1, values_1):
            f11_by_name[str(name)] = float(value)
    except (TypeError, ValueError):
        return None

    calibration: dict[int, tuple[float, float]] = {}
    try:
        for name, value in zip(names_0, values_0):
            name_text = str(name)
            match = re.fullmatch(r"Q(\d+)", name_text, flags=re.IGNORECASE)
            if match and name_text in f11_by_name:
                calibration[int(match.group(1))] = (float(value), f11_by_name[name_text])
    except (TypeError, ValueError):
        return None
    return calibration or None


def calibrate_probabilities(
    probabilities: Mapping[str, float],
    f00: Sequence[float],
    f11: Sequence[float],
) -> dict[str, float]:
    """Apply Tianyan's inverse-confusion-matrix readout correction."""
    if len(f00) != len(f11):
        raise ValueError("f00 and f11 must have the same length")
    if not probabilities:
        return {}

    num_qubits = len(f00)
    inverse = np.array([[1.0]], dtype=float)
    for fidelity_0, fidelity_1 in zip(f00, f11):
        determinant = fidelity_0 + fidelity_1 - 1.0
        if abs(determinant) < 1e-12:
            local_inverse = np.eye(2, dtype=float)
        else:
            local_inverse = np.array(
                [
                    [fidelity_1, fidelity_1 - 1.0],
                    [fidelity_0 - 1.0, fidelity_0],
                ],
                dtype=float,
            ) / determinant
        inverse = np.kron(inverse, local_inverse)

    dimension = 1 << num_qubits
    measured = np.zeros(dimension, dtype=float)
    for bitstring, probability in probabilities.items():
        try:
            index = int(bitstring, 2)
        except (TypeError, ValueError):
            continue
        if 0 <= index < dimension:
            measured[index] = float(probability)

    corrected = np.clip(inverse @ measured, 0.0, 1.0)
    total = float(corrected.sum())
    if total > 1e-15:
        corrected /= total
    return {
        format(index, f"0{num_qubits}b"): float(probability)
        for index, probability in enumerate(corrected)
        if probability > 0.0
    }


def calibrate_counts(
    counts: Mapping[str, int],
    qubits: Sequence[int],
    shots: int,
    calibration: Mapping[int, tuple[float, float]],
    *,
    force: bool = False,
) -> dict[str, int]:
    """Correct counts for measured qubits and round back to shot counts."""
    if not force and len(qubits) > AUTO_CALIBRATION_MAX_QUBITS:
        return dict(counts)
    total = sum(counts.values())
    if total <= 0:
        return {}
    probabilities = {key: value / total for key, value in counts.items()}
    f00 = [calibration.get(qubit, (1.0, 1.0))[0] for qubit in qubits]
    f11 = [calibration.get(qubit, (1.0, 1.0))[1] for qubit in qubits]
    corrected = calibrate_probabilities(probabilities, f00, f11)
    return {
        bitstring: count
        for bitstring, probability in corrected.items()
        if (count := round(probability * shots)) > 0
    }
