# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

# This code is part of cqlib.
#
# Copyright (C) 2026 China Telecom Quantum Group, QuantumCTek Co., Ltd.,
# Center for Excellence in Quantum Information and Quantum Physics.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

"""Pulse-configuration and waveform utilities."""

import cmath
import datetime
import math
import pickle
import time
from io import BytesIO
from typing import Any, Callable, Protocol

import numpy as np
import plotly.graph_objects as go
import plotly.subplots as sp
import requests
from scipy.interpolate import interp1d

from .exceptions import GuodunValidationError as CqlibInputParaError
from .models import coerce_qcis
from .pulse_constants import (
    GATE_PHASES,
    GATE_PULSE_QCIS_TEMPLATE_COUPLER,
    GATE_PULSE_QCIS_TEMPLATE_QUBIT,
)


class BasePlatform(Protocol):
    """Operations required by the legacy Pulse helper."""

    machine_name: str | None

    def download_config(self) -> dict[str, Any]: ...
    def create_waveform_data(
        self,
        circuit: str,
        circuit_name: str | None = None,
        is_verify: bool = True,
    ) -> int | str: ...

    def query_waveform_data(self, query_id: int | str) -> str | None: ...


class GetPulse:
    """Handles pulse generation and visualization for quantum operations."""

    def __init__(self, platform: BasePlatform):
        machine_name = getattr(platform, "machine_name", None) or getattr(platform, "name", None)
        if machine_name is None:
            raise CqlibInputParaError("The platform/backend must identify a machine.")
        self.platform = platform
        if hasattr(platform, "raw_config"):
            self.config = platform.raw_config()
        else:
            self.config = platform.download_config()
        self.qubit_f01_range_cache = {}  

        electronic_type = self.config.get('electronicType', 'Unknown')
        if electronic_type == "EZQ1.0":
            self.xy_sample_rate = 2e9
            self.z_sample_rate = 2e9
        elif electronic_type in ("EZQ2.0", "EZQ2.1"):
            self.xy_sample_rate = 5e8
            self.z_sample_rate = 1e9
        else:
            raise ValueError(
                f"Unsupported electronicType: '{electronic_type}'. "
                f"Only 'EZQ1.0', 'EZQ2.0', and 'EZQ2.1' are supported."
            )

    def get_gate_pulse_parameter(self, gate: str, qagent: str) -> dict:
        """
        Retrieves pulse parameters for the specified quantum gate operation.

        Args:
            gate: Gate command ({X2P, X2M, Y2P, Y2M, CZ})
            qagent: Target qubit or coupler identifier

        Returns:
            Dictionary containing pulse configuration parameters

        Raises:
            ValueError: For unsupported gate types
        """
        gate = gate.upper()
        qagent = qagent.upper()

        if gate in GATE_PHASES:
            config = self.config['gatePulseParameter']['single_parameter'][qagent]
            f01 = self.config['gatePulseParameter']['pulse_parm']['f01'][qagent]
            return {
                "qubit": qagent,
                "qubit_channel": "xy",
                "wave_class": "0",
                "length": config["length"],
                "amplitude": config["amplitude"],
                "phase": GATE_PHASES[gate],
                "drag_alpha": config["drag_alpha"],
                "extra_param": config["extra_param"],
                "frequency": config["drive_detune"] + f01
            }

        if gate == 'CZ':
            config = self.config['gatePulseParameter']['cz_parameter'][qagent]
            
            coupler_detune_amp =  float(config.get("coupler_detune_amplitude", 0.0))
            coupler_off_call_mapper = int(config['is_g2bias_mapping']) if coupler_detune_amp != 0 else 0
            
            return {
                "qubit0": config["qubit0"],
                "qubit1": config["qubit1"],
                "coupler": qagent,
                "qubit0_channel": "z",
                "qubit1_channel": "z",
                "coupler_channel": "z",
                "qubit_call_mapper": 0,
                "coupler_off_call_mapper": coupler_off_call_mapper,
                "coupler_call_mapper": int(config['is_g2bias_mapping']),
                "pad_time_left": config["pad_time_left"],
                "pad_time_right": config["pad_time_right"],
                "detune_wave_class": "1",
                "detune_length": config["detune_length"],
                "qubit0_detune_amplitude": config["qubit0_detune_amplitude"],
                "qubit1_detune_amplitude": config["qubit1_detune_amplitude"],
                "detune_extra_param": config["detune_extra_param"],
                "qubit0_dynamic_phase": -self._renormalize_angle(config["qubit0_dynamic_phase"]),
                "qubit1_dynamic_phase": -self._renormalize_angle(config["qubit1_dynamic_phase"]),
                "coupler_detune_amplitude": config["coupler_detune_amplitude"],
                "cz_wave_class": "2",
                "cz_length": config["cz_length"],
                "cz_amplitude": config["cz_amplitude"],
                "coupler_detune_longer_left": config["coupler_detune_longer_left"],
                "coupler_detune_longer_right": config["coupler_detune_longer_right"],
                "cz_extra_param": config["cz_extra_param"]
            }

        raise ValueError(f"Unsupported gate type: {gate}")

    def create_and_get_waveform_diagram(
            self,
            circuit: Any,
            is_show: bool = True,
            max_retries: int = 10,
            file_name: str = None,
            is_verify: bool = True
    ) -> str:
        """
        Submit the circuit and retrieve the waveform diagram URL

        Args:
            circuit(str|Circuit): Quantum circuit content
            is_show(bool): show wave of QCIS circuit (default: True)
            max_retries: Maximum number of query attempts (default: 10)
            file_name: File path for saving the QCIS circuit waveform
            is_verify (bool): Is the circuit to be verified. Default: True
        Returns:
            Waveform diagram URL string

        Raises:
            CqlibInputParaError: If circuit is empty
            RuntimeError: For unsupported platforms
            TimeoutError: If URL retrieval fails after max_retries
        """
        circuit = coerce_qcis(circuit)
        if not circuit.strip():
            raise CqlibInputParaError("Circuit content cannot be empty")
        query_id = self.platform.create_waveform_data(circuit=circuit, is_verify=is_verify)
        for _ in range(1, max_retries + 1):
            try:
                url = self.platform.query_waveform_data(query_id)
                if url:
                    self._visualize(url, is_show=is_show, file_name=file_name)
                    return url
            except Exception as exc:  # The legacy API uses message-only failures.
                if "The circuit waveform graph generation failed" in str(exc):
                    raise
            time.sleep(10)
        raise TimeoutError(
            f"Failed to retrieve waveform data after {max_retries} attempts. "
            f"query ID: {query_id}"
        )

    @staticmethod
    def get_gate_pulse_qcis_template(gate) -> str:
        """
        Returns the appropriate pulse template for the given gate.

        Args:
            gate: Gate command string. Valid values: {X2P, X2M, Y2P, Y2M, CZ}

        Returns:
            str: Corresponding pulse template string

        Raises:
            ValueError: If invalid gate is provided
        """
        if gate == 'CZ':
            return GATE_PULSE_QCIS_TEMPLATE_COUPLER

        if gate in GATE_PHASES:
            return GATE_PULSE_QCIS_TEMPLATE_QUBIT

        raise ValueError(f"Invalid gate '{gate}'. Valid gates are: "
                         f"{list(GATE_PHASES.keys()) + ['CZ']}")

    def get_coupler_label(self, qubit0: str, qubit1: str) -> str:
        """
        Get the coupler label connecting Q0 and Q1.
        Args:
            qubit0 (str): Qubit .(e.g 'Q0')
            qubit1 (str): Qubit . (e.g 'Q7')
        Returns:
            str: The coupler label (e.g., 'G1').
            None: If there is no coupler between Q0 and Q1.
        """

        couplers_map = self.config['overview']['coupler_map']
        disabled_couplers = self.config['disabledCouplers'].split(',')
        qubit_pair = {qubit0.upper(), qubit1.upper()}
        for coupler, qubits in couplers_map.items():
            qubits_set = {qubits[0], qubits[1]}
            if qubits_set == qubit_pair and coupler not in disabled_couplers:
                return coupler
        raise ValueError(f"Cannot get the coupler for {qubit0} and {qubit1},"
                         f"Please check the parameters.")

    def get_coupler_adjacent_qubits(self, coupler: str) -> list[str]:
        """
        Get the qubits connected to a given coupler.
        Args:
            coupler (str): The coupler label (e.g., 'G1').
        Returns:
            list[str]: A list of qubits connected to the coupler (e.g., ['Q1', 'Q2']).
            None: If the coupler does not exist or is disabled.
        """
        couplers_map = self.config['overview']['coupler_map']
        disabled_couplers = self.config['disabledCouplers'].split(',')
        coupler = coupler.upper()
        if coupler in disabled_couplers or coupler not in couplers_map:
            raise ValueError(f"Cannot get the qubit corresponding to {coupler},"
                             f"Please check the parameters.")
        return couplers_map[coupler]

    def get_coupling_strength_range(self, coupler: str) -> list:
        """
        Retrieves the valid coupling strength range for a specified coupler.

        The parameters fetched from the hardware configuration are converted 
        from Hz to MHz before returning.
        Args:
            coupler (str) : The coupler label (e.g., 'G1').
        Returns:
            list[float]: coupling strength range in MHz
                        (e.g., [-15013793103.4, 4103448275.9])
        Raises:
            ValueError: If the specified coupler does not exist in the 
                        configuration's 'qubit_used' list or is disabled.
        """
        coupling_strength_config = self.config['twoQubitGate']['czGate']['coupling strength']
        qubit_used = coupling_strength_config['qubit_used']
        param_list = coupling_strength_config['param_list']
        coupler = coupler.upper()
        if coupler not in qubit_used:
            raise ValueError(f"Cannot find the coupling strength of {coupler},"
                             "Please check the parameters.")
        index = qubit_used.index(coupler)
        return [x / 1e9 for x in param_list[index]]

    def f01_shift_2_detune(self, qubit: str, d_f01: float | list | np.ndarray) -> float | np.ndarray:
        """
        Get the amplitude of the pulse required to change the qubit frequency f01.

        This method supports both scalar inputs and vectorized operations 
        (NumPy arrays) for efficient inverse mapping.

        Args:
            qubit (str): qubit value (eg: 'Q1')
            d_f01 (float | list | np.ndarray): Frequency shift/detuning value(s).
        Returns:
            float | np.ndarray: zpulse amplitude(s).
                                Returns a scalar float if `d_f01` is a scalar, otherwise returns a NumPy ndarray.
        Raises:
            ValueError: If the qubit does not exist in the mapping, 
                        or if target frequency exceeds hardware physical limits
        """

        pulse_parm = self.config['gatePulseParameter']['pulse_parm']
        zbias2f01_mapper_map = pulse_parm['zbias2f01_mapper']
        qubit = qubit.upper()

        if qubit not in zbias2f01_mapper_map:
            raise ValueError(f"{qubit} not in zbias2f01_mapper")

        d_f01_arr = np.asarray(d_f01)
        f01 = pulse_parm['f01'][qubit]
        fah = pulse_parm['fah'][qubit]
        (f01_min, f01_max) = self.get_qubit_f01_range(qubit)
        (d_f01_min, d_f01_max) = (f01_min - f01, f01_max - f01)

        invalid_f_mask = (d_f01_arr> d_f01_max) | (d_f01_arr < d_f01_min)
        if invalid_f_mask.any():
            invalid_dtns = np.unique(d_f01_arr[invalid_f_mask])
            display_dtns = invalid_dtns[:5]
            ellipsis_str = "..." if len(invalid_dtns) > 5 else ""

            raise ValueError(
                f"Valid d_f01 range is [{d_f01_min}, {d_f01_max}].\n"
                f"Invalid d_f01 found: {display_dtns.tolist()}{ellipsis_str}")

        map_setting = {'coef': zbias2f01_mapper_map[qubit], 'f01': f01, 'fah': fah}
        amp = GetPulse._map_amplitude(amplitude=d_f01_arr, map_setting=map_setting)
        
        return amp

    def detune_2_f01_shift(self, qubit: str, dtn: float | list | np.ndarray) -> float | np.ndarray:
        """
        Returns the change in qubit frequency (f01_shift) corresponding to
         the pulse amplitude(s) (dtn) of the Qubit Z channel.
        
        This method supports both scalar inputs and vectorized operations 
        (NumPy arrays) for efficient mapping.

        Args:
            qubit (str): qubit value(eg: 'Q1')
            dtn (float | list | np.ndarray): dtn value(eg: 0.23147862)
        Returns:
            float | np.ndarray: The calculated f01_shift (e.g., -142279258.21).
                                Returns a scalar float if `dtn` is a scalar, 
                                otherwise returns a NumPy ndarray.
        Raises:
            ValueError: If the qubit does not exist in the mapping, 
                        if the configured `electronicType` in the backend is not supported,
                        or if the `dtn` value exceeds physical hardware bounds.
        """
        
        pulse_parm = self.config['gatePulseParameter']['pulse_parm']
        zbias2f01_mapper_map = pulse_parm['zbias2f01_mapper']

        qubit = qubit.upper()
        if qubit not in zbias2f01_mapper_map:
            raise ValueError(f"{qubit} not in zbias2f01_mapper_map")
        
        f01 = pulse_parm['f01'][qubit]
        fah = pulse_parm['fah'][qubit]
        max_f01, bias_offset, kf = zbias2f01_mapper_map[qubit]

        dtn_arr = np.asarray(dtn)
        (zpa_min, zpa_max) = self.get_qagent_zbias_amp_range(qubit)

        invalid_mask = (dtn_arr < zpa_min) | (dtn_arr > zpa_max)
        if invalid_mask.any():
            invalid_dtns = np.unique(dtn_arr[invalid_mask])
            display_dtns = invalid_dtns[:5]
            ellipsis_str = "..." if len(invalid_dtns) > 5 else ""
            
            raise ValueError(
                f"The detuning amplitude (dtn) for {qubit} is out of hardware bounds.\n"
                f"Valid range is [{zpa_min}, {zpa_max}].\n"
                f"Invalid dtn found: {display_dtns.tolist()}{ellipsis_str}"
            )
 
        f01_shift = ((max_f01 - fah) *
                     np.sqrt(np.abs(np.cos(np.pi * kf * (dtn_arr - bias_offset)))) + fah - f01)

        return float(f01_shift) if f01_shift.ndim == 0 else f01_shift

    # pylint: disable-next=too-many-positional-arguments, too-many-locals, too-many-arguments
    def get_z_amp_samples(
            self,
            qagent: str,
            wave_type: int,
            length: int,
            amplitude: float,
            call_mapper: int,
            wave_params: list | float | int = None
    ) -> list:
        """
        Generate Z-pulse amplitude sample sequence.

        Args:
            qagent (str): Quantum agent ID. Qubits start with 'Q', couplers start with 'G'.
            wave_type (int): 0=CosineWave, 1=FlattopWave, 2=SlepianWave.
            length (float): Pulse duration in nanoseconds. Will be converted to sample points
                    using self.z_sample_rate.
            amplitude (float): amplitude value
            call_mapper (int): 0 or 1. Flag to determine whether to apply hardware calibration mapping.
                               - 0: Return bare waveform samples directly.
                               - 1: Map the waveform sequence using f01_shift_2_detune(for qubit) or coupler_strength_2_zpulse_amp(coupler)
            **wave_params: Additional waveform parameters.
                - Flattop (wave_type=1): requires 'edge' (ns)
                - Slepian (wave_type=2): requires 'thf', 'thi', 'lam2', 'lam3'

        Returns:
            list[float]: List of Z-pulse amplitude samples ready for AWG execution.


        Raises:
            ValueError: If `qagent` is invalid/disabled, required parameters are missing, 
                        or calculated hardware amplitudes exceed physical limits.
        """


        qagent = qagent.upper()
        if wave_params and not isinstance(wave_params, list):
            wave_params = [wave_params]

        qagent_type = self._validate_qagent(qagent)

        num_samples = int(length * self.z_sample_rate * 1e-9)
        if num_samples <= 0:
            raise ValueError(
                f"Invalid sample count {num_samples}. Check length ({length} ns) "
                f"and z_sample_rate ({self.z_sample_rate} Hz)"
            )


        if wave_type == 0:
            # CosineWave: raised-cosine envelope, no extra parameters needed
            samples = self._cosine_samples(amplitude=amplitude, length=num_samples)

        elif wave_type == 1:
            edge = wave_params[0]
            if edge is None:
                raise ValueError("wave_type=1 (FlattopWave) requires 'edge' parameter")

            # Convert edge duration to sample points
            edge_samples = edge * self.z_sample_rate * 1e-9
            samples = self._flattop_samples(
                amplitude=amplitude,
                length=num_samples,
                edge=edge_samples
            )

        elif wave_type == 2:
            thf = wave_params[0]
            thi = wave_params[1]
            lam2 = wave_params[2]
            lam3 = wave_params[3]

            if thf is None or thi is None or lam2 is None or lam3 is None:
                raise ValueError(
                    "wave_type=2 (SlepianWave) requires all parameters: "
                    "thf, thi, lam2, lam3"
                )

            samples = self._slepian_samples(
                amplitude=amplitude,
                length=num_samples,
                thf=thf,
                thi=thi,
                lam2=lam2,
                lam3=lam3
            )

        else:
            raise ValueError(f"Unsupported wave_type={wave_type}. Must be 0, 1, or 2")


        if call_mapper == 1:

            samples_arr = np.asarray(samples)

            if qagent_type == 'qubit':
                mapped_samples = self.f01_shift_2_detune(qagent, samples_arr)
            else:
                mapped_samples = self.coupler_strength_2_zpulse_amp(qagent, samples_arr)

            return mapped_samples.tolist()
        
        else:
            return samples



    def get_qubit_f01(self, qubit: str) -> float:
        """
        Query the f01 of the qubit at the working point.

        Args:
            qubit (str): Qubit identifier (e.g., 'Q0').

        Returns:
            float: The f01 value.

        Raises:
            KeyError: If f01 data is missing for the qubit.
        """
        qubit = qubit.upper()
        self._validate_qagent(qubit)
        try:
            return self.config['gatePulseParameter']['pulse_parm']['f01'][qubit]
        except KeyError:
            raise KeyError(f"f01 data not found for {qubit} in config")

    def get_qubit_fah(self, qubit: str) -> float:
        """
        Query the fah of the qubit.

        Args:
            qubit: Qubit identifier (e.g., 'Q0').

        Returns:
            float: The fah value.

        Raises:
            KeyError: If fah data is missing for the qubit.
        """
        qubit = qubit.upper()
        self._validate_qagent(qubit)
        try:
            return self.config['gatePulseParameter']['pulse_parm']['fah'][qubit]
        except KeyError:
            raise KeyError(f"fah data not found for {qubit} in config")

    def get_qagent_workbias(self, qagent: str) -> float:
        """
        Retrieve the working bias amplitude for a quantum agent (qubit or coupler).

        Args:
            qagent: Quantum agent identifier (e.g., 'Q0', 'G01').

        Returns:
            float: The working bias amplitude value.

        Raises:
            KeyError: If qagent is not found in the registry.
        """
        qagent = qagent.upper()
        self._validate_qagent(qagent)
        try:
            return float(self.config['gatePulseParameter']['pulse_parm']['workbias_shift'][qagent])
        except KeyError:
            raise KeyError(f"workbias data not found for {qagent} in config")

    def coupler_strength_2_zpulse_amp(self, coupler: str, g: float | list | np.ndarray) -> float | np.ndarray:
        """
        Map coupler strength to Z-pulse amplitude using interpolation.
        This method supports both scalar inputs and vectorized operations 
        (NumPy arrays) for efficient inverse mapping.

        Args:
            coupler: Coupler identifier (e.g., 'G01').
            g: Target coupling strength. Float or array-like object
        Returns:
                float | np.ndarray: The interpolated Z-pulse amplitude. Float or Numpy array

        Raises:
            ValueError: If calibration data is empty or g contains values out of range.
            KeyError: If zbias2g_mapper data is missing for the coupler.
        """
        coupler = coupler.upper()
        self._validate_qagent(coupler)

        g_arr = np.asarray(g)

        try:
            coupler_strength_list = (
                self.config['gatePulseParameter']
                ['cz_parameter']
                [coupler]
                ['coupler_strength']
            )

            zpulse_amp_list = (
                self.config['gatePulseParameter']
                ['cz_parameter']
                [coupler]
                ['zpulse_amp']
            )
        except KeyError:
            raise KeyError(
                f"zbias2g_mapper data not found for {coupler} in configuration"
            )

        if not coupler_strength_list and not zpulse_amp_list:
            raise ValueError(
                f"Empty coupler_strength calibration data for {coupler}. "
                f"Please run coupler calibration first."
            )

        g_min = min(coupler_strength_list)
        g_max = max(coupler_strength_list)

        invalid_mask = (g_arr < g_min) | (g_arr > g_max)
        if invalid_mask.any():
            
            invalid_values = np.unique(g_arr[invalid_mask])
            display_vals = invalid_values[:5]
            ellipsis_str = "..." if len(invalid_values) > 5 else ""
            
            raise ValueError(
                f"Coupler strength for {coupler} contains values out of calibrated range [{g_min}, {g_max}].\n"
                f"Cannot convert coupler strength to zpulse amp for g in {display_vals}{ellipsis_str}"
            )

        f = interp1d(coupler_strength_list, zpulse_amp_list)
        amp = f(g_arr)

        return float(amp) if amp.ndim == 0 else amp

    def zpulse_amp_2_coupler_strength(self, coupler: str, amp: float | list | np.ndarray) -> float | np.ndarray:
        """
        Map Z-pulse amplitude to coupler strength using interpolation.

        This method supports both scalar inputs and vectorized operations 
        (NumPy arrays) for efficient inverse mapping.

        Args:
            coupler: Coupler identifier (e.g., 'G01').
            amp: Target Z-pulse amplitude value. Float or array-like object

        Returns:
            float | np.ndarray: The interpolated coupler strength. Float or Numpy array

        Raises:
            ValueError: If calibration data is empty or amp is out of valid range.
            KeyError: If zbias2g_mapper data is missing for the coupler.
        """
        coupler = coupler.upper()
        self._validate_qagent(coupler)

        amp_arr = np.asarray(amp)

        try:
            coupler_strength_list = (
                self.config['gatePulseParameter']
                ['cz_parameter']
                [coupler]
                ['coupler_strength']
            )

            zpulse_amp_list = (
                self.config['gatePulseParameter']
                ['cz_parameter']
                [coupler]
                ['zpulse_amp']
            )
        except KeyError:
            raise KeyError(
                f"zbias2g_mapper data not found for {coupler} in configuration"
            )

        if not coupler_strength_list or not zpulse_amp_list:
            raise ValueError(
                f"Empty calibration data for {coupler}. "
                f"Please run coupler calibration first."
            )

        amp_min = min(zpulse_amp_list)
        amp_max = max(zpulse_amp_list)
        invalid_mask = (amp_arr < amp_min) | (amp_arr > amp_max)
        if invalid_mask.any():
            invalid_values = np.unique(amp_arr[invalid_mask])
            display_vals = invalid_values[:10]
            ellipsis_str = "..." if len(invalid_values) > 10 else ""
            
            raise ValueError(
                f"Z-pulse amplitude for {coupler} contains values out of calibrated range [{amp_min}, {amp_max}].\n"
                f"Cannot convert zpulse amp to coupler strength for amp in {display_vals}{ellipsis_str}"
            )

        f = interp1d(zpulse_amp_list, coupler_strength_list)
        g = f(amp_arr)
        return float(g) if g.ndim == 0 else g

    def get_qubit_f01_range(self, qubit: str) -> tuple[float, float]:
        """
        Calculate the f01 frequency range for a qubit based on Z-pulse amplitude sweep.

        Uses the zbias2f01_mapper calibration model to compute the minimum and
        maximum achievable f01 frequencies within the valid Z-pulse amplitude
        range [-1, 1] relative to the working bias point.

        Args:
            qubit: Qubit identifier (e.g., 'Q0').

        Returns:
            tuple[float, float]: (f01_min, f01_max) where:
                - f01_max: Maximum f01 frequency (at sweet spot, from calibration)
                - f01_min: Minimum f01 frequency (at edge of ZPA range)

        Raises:
            KeyError: If required configuration entries are not found.
                      if `qubit` is invalid, or if the system `electronicType` is not supported
        """
        qubit = qubit.upper()

        self._validate_qagent(qubit)

        if qubit in self.qubit_f01_range_cache:
            return self.qubit_f01_range_cache[qubit]

        try:
            f01_max, bias_offset, kf = (
                self.config['gatePulseParameter']
                ['pulse_parm']
                ['zbias2f01_mapper']
                [qubit]
            )
        except KeyError:
            raise KeyError(
                f"zbias2f01_mapper calibration data not found for {qubit} in configuration"
            )

        fah = self.config['gatePulseParameter']['pulse_parm']['fah'][qubit]

        def get_qubit_f01_by_zpa(x):
            term = np.pi * kf * (x - bias_offset)
            return (f01_max - fah) * np.sqrt(np.abs(np.cos(term))) + fah

        (zpa_min, zpa_max) = self.get_qagent_zbias_amp_range(qubit)
        zpa_sweep = np.linspace(zpa_min, zpa_max, 1000)
        f01_sweep = get_qubit_f01_by_zpa(zpa_sweep)
        f01_min = float(np.min(f01_sweep))

        self.qubit_f01_range_cache[qubit] = (f01_min, f01_max)
        return f01_min, f01_max

    def get_qagent_zbias_amp_range(self, qagent: str) -> tuple[float, float]:
        """
        Get the valid Z-pulse amplitude range for a quantum agent.

        Args:
            qagent: Quantum agent identifier (e.g., 'Q0', 'G01').

        Returns:
            tuple[float, float]: Minimum and Maximum Z-pulse amplitude boundaries
             (zpa_min, zpa_max) based on system's `electronicType` 

        Raises:
            ValueError: If qagent is invalid, or if the system electronicType
                        is not supported.
            KeyError: If workbias_shift data for the qagent is missing from the configuration.
        """
        qagent = qagent.upper()
        self._validate_qagent(qagent)

        workbias = float(self.config['gatePulseParameter']['pulse_parm']['workbias_shift'][qagent])

        electronic_type = self.config.get('electronicType', 'Unknown')
        if electronic_type == "EZQ1.0":
            zpa_min = max(-1.0, -1.0 - workbias)
            zpa_max = min(1.0, 1.0 - workbias)
        elif electronic_type in ("EZQ2.0", "EZQ2.1"):
            zpa_min = -1.0 - workbias
            zpa_max = 1.0 - workbias
        else:
            raise ValueError(
                f"Unsupported electronicType: '{electronic_type}'. "
                f"Only 'EZQ1.0', 'EZQ2.0', and 'EZQ2.1' are supported."
            )

        return zpa_min, zpa_max

    def get_channel_sample_rate(self, channel_type: str) -> float:
        """
        Get the sample rate for the specified channel type (XY or Z).

        Args:
            channel_type: The type of channel ("XY" or "Z").

        Returns:
            float: The sample rate in Hz.

        Raises:
            ValueError: If the channel_type is not "XY" or "Z".
        """
        channel_type = channel_type.upper()

        if "XY" in channel_type:
            return self.xy_sample_rate

        if "Z" in channel_type:
            return self.z_sample_rate

        raise ValueError(f"Invalid channel_type: {channel_type}. Expected 'XY' or 'Z'.")

    def _validate_qagent(self, qagent: str) -> str:
        """
        Validate a quantum agent identifier and return its type.

        Checks whether the qagent is a valid, enabled qubit or coupler based on
        the current configuration.

        Args:
            qagent: Quantum agent identifier (e.g., 'Q0', 'G01').

        Returns:
            str: 'qubit' if qagent is a valid qubit, 'coupler' if it's a valid coupler.

        Raises:
            ValueError: If qagent is invalid, disabled, or not configured.
        """
        qagent = qagent.upper()

        disabled_qubits = self.config.get('disabledQubits', '').split(',')
        disabled_couplers = self.config.get('disabledCouplers', '').split(',')
        overview = self.config.get('overview', {})
        qubits = overview.get('qubits', [])
        couplers = overview.get('couplers', [])

        if qagent.startswith('Q'):
            if qagent in disabled_qubits or qagent not in qubits:
                raise ValueError(f"Qubit '{qagent}' is disabled or not configured")
            return 'qubit'

        if qagent.startswith('G'):
            if qagent in disabled_couplers or qagent not in couplers:
                raise ValueError(f"Coupler '{qagent}' is disabled or not configured")
            return 'coupler'

        raise ValueError(
                f"Invalid quantum agent '{qagent}'. Must start with 'Q' (qubit) or 'G' (coupler)"
            )


    # pylint: disable=too-many-locals
    @staticmethod
    def _visualize(url, is_show: bool = True, file_name: str = None):
        """
        Visualizes waveform data from a remote pickle file.

        Args:
            url: Remote URL containing pickled visualization data
            is_show: Flag to display plot in browser (default: True)
            file_name: Optional filename to save HTML output
        """
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        file_data = BytesIO(response.content)
        loaded_data = pickle.load(file_data)
        num_rows = loaded_data["num_rows"]
        subplot_titles = loaded_data["subplot_titles"]
        plot_map = loaded_data["plot_map"]
        data = loaded_data["data"]
        fig = sp.make_subplots(
            rows=num_rows,
            cols=1,
            shared_xaxes=True,
            subplot_titles=subplot_titles,
        )
        for i, (key, value) in enumerate(data.items()):
            if key[0] not in plot_map:
                continue
            wave = value["output"]
            if key[1] != 'z':
                wave = np.concatenate([wave[96:], np.zeros(96)])
            else:
                wave = np.concatenate([wave[96:], np.full(96, value["output"][len(wave) - 1])])
            sample_ns = value["sample_ns"]
            t = np.arange(len(wave)) * sample_ns
            fig.add_trace(
                go.Scatter(
                    x=t, y=wave, name="-".join(key), mode="lines"
                ),
                row=(plot_map[key[0]] + 1),
                col=1,
            )
        ax_tag = ["" if not i else str(i + 1) for i in range(num_rows)]
        axis = {}
        for ax in ax_tag:
            axis[f"xaxis{ax}"] = {"showticklabels": True}
            axis[f"yaxis{ax}"] = {"exponentformat": 'power', "showexponent": 'last'}
        fig.update_layout(
            title=f"{datetime.datetime.now()}",
            height=(420 * num_rows),
            **axis
        )
        if is_show:
            fig.show(renderer="browser")
        if file_name:
            fig.write_html(f"{file_name}.html")

    @staticmethod
    def _map_amplitude(amplitude: float | list | np.ndarray, map_setting: dict) -> float | np.ndarray:
        """
        Internal processing procedure of f01_shift_2_detune method.
        Maps frequency detuning (amplitude) to Z-pulse amplitude shift(s).
        
        Args:
            amplitude (float | list | np.ndarray): Detuning value(s).
            map_setting (dict): Calibration parameters mapping setting.
            
        Returns:
            float | np.ndarray: The corresponding Z-pulse amplitude shift(s).
        
        Raises:
            ValueError: If qubit freqency f01 exceeds the maximum frequency,
                        if target frequency (f01 + amplitude) exceeds the maximum frequency.
        """

        amplitude_arr = np.asarray(amplitude)

        coef = [float(item) for item in map_setting['coef']]
        f01 = float(map_setting['f01'])
        fah = float(map_setting['fah'])
        max_freq = coef[0]

        if f01 > max_freq:
            raise ValueError(f"f01 = {f01} is too large, zbias2f01_mapper = {coef}")

        over_mask = (f01 + amplitude_arr) > max_freq 
        if over_mask.any():
            over_dtns = np.unique(amplitude_arr[over_mask])
            display_dtns = over_dtns[:5] 
            ellipsis_str = "..." if len(over_dtns) > 5 else ""
            
            raise ValueError(f"Warning: f01 + amplitude exceeds max_freq for {len(over_dtns)} points. "
                  f"Examples of invalid dtn: {display_dtns.tolist()}{ellipsis_str}. "
                  f"zbias2f01_mapper = {coef}")
            
        z1 = GetPulse._get_root(coef, f01, fah, 0.0)
        z2 = GetPulse._get_root(coef, f01 + amplitude_arr, fah, z1)

        result = z2 - z1
        return float(result) if np.ndim(result) == 0 else result

    # pylint: disable=too-many-locals
    @staticmethod
    def _get_root(coef: list, freq: float | list | np.ndarray, fah: float, target: float) -> float | np.ndarray:
        """
        Internal processing procedure of detune_2_f01_shift method.
        
        Calculates the corresponding pulse amplitude for given frequency shift points
        using NumPy vectorization and broadcasting, supporting both scalar and array inputs.

        Args:
            coef (list): A 3-element list containing hardware physical coefficients: 
                         [max_f01, bias_offset, kf].
            freq (float | list | np.ndarray): Target frequency shift point.
            fah (float): Qubit anharmonic frequency
            target (float): The target baseline value. The algorithm evaluates multiple possible roots and returns the one nearest to this target.

        Returns:
            float | np.ndarray: The calculated root value(s). Returns a scalar float 
                                if `freq` is a scalar, otherwise returns a NumPy ndarray.
                                Invalid frequency points (where y_sq > 1) evaluate to 0.0.
        """
        freq_arr = np.asarray(freq)

        max_f01 = coef[0]
        bias_offset = coef[1]
        kf = coef[2]
        
        result = np.zeros_like(freq_arr, dtype=float)
        
        if max_f01 == fah:
            return float(result) if result.ndim == 0 else result
        
        y = (freq_arr - fah) / (max_f01 - fah)
        y_sq = y * y

        valid_mask = (y_sq <= 1.0)
        if not valid_mask.any():
            return float(result) if result.ndim == 0 else result

        y_valid = y_sq[valid_mask]
        arcos_y = np.arccos(y_valid)

        n_pos = kf * (target - bias_offset) - arcos_y / np.pi
        zp1 = bias_offset + arcos_y / (kf * np.pi) + np.floor(n_pos) / kf
        zp2 = bias_offset + arcos_y / (kf * np.pi) + np.ceil(n_pos) / kf
        zp = np.where(np.abs(zp1 - target) < np.abs(zp2 - target), zp1, zp2)

        n_neg = kf * (target - bias_offset) + arcos_y / np.pi
        zn1 = bias_offset - arcos_y / (kf * np.pi) + np.floor(n_neg) / kf
        zn2 = bias_offset - arcos_y / (kf * np.pi) + np.ceil(n_neg) / kf
        zn = np.where(np.abs(zn1 - target) < np.abs(zn2 - target), zn1, zn2)

        result[valid_mask] = np.where(np.abs(zp - target) < np.abs(zn - target), zp, zn)
        
        return float(result) if result.ndim == 0 else result

    @staticmethod
    # pylint: disable-next=too-many-positional-arguments, too-many-arguments
    def _flattop_samples(
        amplitude: float,
        length: int,
        edge: float,
        carrier_freq: float = 0.0,
        carrier_phase: float = 0.0,
        drag_alpha: float = 0.0,
    ) -> list[complex]:
        """
        Generate flattop samples
        """
        samples = [0j] * length
        double_pi = 2.0 * math.pi
        r = 0.21230
        mu = 0.5 * edge

        if edge == 1.0:
            raise ValueError("Fail to calculate flattop wave, edge = 1")

        factor = 1.0 / math.sqrt(2.0) / r / (edge - 1.0)
        drag_factor = -amplitude / math.sqrt(math.pi) * factor * drag_alpha
        omega = double_pi * carrier_freq

        for k in range(length):
            x = (float(k) - mu) * factor
            offset = (float(length) - edge - 1.0) * factor

            env = 0.5 * amplitude * (math.erf(x) - math.erf(x - offset))
            drag = drag_factor * (
                math.exp(-x * x) - math.exp(-(x - offset) * (x - offset))
            )
            phase = omega * float(k) + carrier_phase

            samples[k] = complex(env, drag) * cmath.exp(complex(0.0, phase))

        return [abs(s) for s in samples]

    @staticmethod
    def _cosine_samples(
            amplitude: float,
            length: int,
            carrier_freq : float = 0.0,
            carrier_phase: float = 0.0,
            drag_alpha: float = 0.0,
    ) -> list[complex]:
        """
        Generate cosine samples
        """
        samples = [0j] * length
        double_pi = 2.0 * math.pi
        factor = double_pi / float(length - 1)
        omega = double_pi * carrier_freq
        drag_factor = 0.5 * amplitude * drag_alpha * factor

        for k in range(length):
            theta = float(k) * factor

            env = (1.0 - math.cos(theta)) * amplitude * 0.5
            drag = drag_factor * math.sin(theta)
            phase = omega * float(k) + carrier_phase

            samples[k] = complex(env, -drag) * cmath.exp(complex(0.0, phase))

        return [abs(s) for s in samples]

    @staticmethod
    # pylint: disable-next=too-many-positional-arguments, too-many-arguments
    def _slepian_samples(
        amplitude: float,
        length: int,
        thf: float,
        thi: float,
        lam2: float,
        lam3: float,
        carrier_freq: float = 0.0,
        carrier_phase: float = 0.0,
    ) -> list[complex]:
        """
        Generate slepian samples
        """
        resolution = 1024

        t = np.linspace(0.0, 1.0, resolution)

        han2 = []
        for x in t:
            val = ((1 - lam3) * (1 - math.cos(2.0 * math.pi * x)) +
                   lam2 * (1 - math.cos(4 * math.pi * x)) +
                   lam3 * (1 - math.cos(6 * math.pi * x)))
            han2.append(val)

        max_han2 = max(han2)
        thsl = [thi + (thf - thi) * h / max_han2 for h in han2]

        tlu = np.cumsum(np.sin(thsl)) * (t[1] - t[0])
        tlu -= tlu[0]

        th_interp = interp1d(
            tlu, thsl,
            kind='linear',
            fill_value='extrapolate',
        )

        t_new = np.linspace(0.0, tlu[-1], resolution)

        th1 = []
        offset = 1.0 / math.tan(float(th_interp(float(t_new[0]))))
        for v in t_new:
            th_val = float(th_interp(v))
            th1.append(1.0 / math.tan(th_val) - offset)

        min_th1 = min(th1)

        samples = []
        double_pi = 2.0 * math.pi
        omega = double_pi * carrier_freq

        for k in range(length):
            idx = int(float(k * resolution) / float(length - 1)) - 1
            if idx == -1:
                idx = resolution - 1

            env = th1[idx] * amplitude / min_th1
            phase = omega * float(k) + carrier_phase
            samples.append(complex(env, 0.0) * cmath.exp(complex(0.0, phase)))

        return [abs(s) for s in samples]

    @staticmethod
    def _renormalize_angle(theta):
        """Normalization to the range of (-pi, pi]"""
        theta_renorm = theta % (2 * np.pi)
        if theta_renorm > np.pi:
            theta_renorm -= 2 * np.pi
        return theta_renorm
