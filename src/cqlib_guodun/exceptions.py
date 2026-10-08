# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Exception hierarchy for :mod:`cqlib_guodun`."""

from __future__ import annotations

from typing import Any


class GuodunError(RuntimeError):
    """Base class for all Guodun client failures."""


class GuodunAuthError(GuodunError):
    """Authentication or credential persistence failed."""


class GuodunRequestError(GuodunError):
    """An HTTP request could not be completed."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GuodunApiError(GuodunError):
    """The platform returned a non-zero business error code."""

    def __init__(
        self,
        message: str,
        *,
        code: int | str | None = None,
        data: Any = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.data = data
        self.status_code = status_code


class GuodunValidationError(GuodunError, ValueError):
    """A caller supplied an invalid argument."""


class GuodunTimeoutError(GuodunError, TimeoutError):
    """A polling operation did not finish before its deadline."""
