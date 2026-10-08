# Copyright 2026 QuantumCTek Co., Ltd.
# SPDX-License-Identifier: Apache-2.0

"""Authenticated HTTP client for the Guodun cloud platform."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import requests

from .auth import load_credentials, save_credentials
from .config import LOGIN_PATH, GuodunConfig
from .exceptions import GuodunApiError, GuodunAuthError, GuodunRequestError
from .models import Credentials

class GuodunClient:
    """A reusable, thread-safe authenticated HTTP client."""

    def __init__(
        self,
        config: GuodunConfig | None = None,
        *,
        api_key: str | None = None,
        credentials: Credentials | None = None,
    ) -> None:
        self.config = config or GuodunConfig()
        self._api_key = api_key or (credentials.api_key if credentials else "")
        self._credentials = credentials
        self._session = requests.Session()
        self._auth_lock = threading.RLock()

    @property
    def credentials(self) -> Credentials | None:
        return self._credentials

    @property
    def access_token(self) -> str:
        return self._credentials.access_token if self._credentials else ""

    @access_token.setter
    def access_token(self, value: str) -> None:
        self._credentials = Credentials(api_key=self._api_key, access_token=value)

    @classmethod
    def from_credentials(
        cls,
        config: GuodunConfig | None = None,
    ) -> "GuodunClient":
        config = config or GuodunConfig()
        credentials = load_credentials(config.credentials_path)
        client = cls(config, credentials=credentials)
        if credentials.is_expired():
            if not config.auto_refresh:
                raise GuodunAuthError("saved access token has expired and auto_refresh is disabled")
            client.login()
        return client

    def login(self, api_key: str | None = None) -> str:
        """Authenticate using the platform's legacy openId form endpoint."""
        with self._auth_lock:
            key = api_key or self._api_key
            if not key:
                raise GuodunAuthError("api_key must not be empty")
            try:
                response = self._session.request(
                    "POST",
                    self.config.base_url + LOGIN_PATH,
                    data={
                        "grant_type": "openId",
                        "openId": key,
                        "account_type": "member",
                    },
                    timeout=self.config.timeout,
                )
            except requests.RequestException as exc:
                raise GuodunRequestError(f"login request failed: {exc}") from exc
            if response.status_code != 200:
                raise GuodunAuthError(f"login failed with HTTP {response.status_code}")
            try:
                envelope = response.json()
            except ValueError as exc:
                raise GuodunAuthError("login response is not valid JSON") from exc
            if not isinstance(envelope, Mapping) or envelope.get("code", -1) != 0:
                message = envelope.get("message") if isinstance(envelope, Mapping) else None
                raise GuodunAuthError(str(message or "login failed"))
            data = envelope.get("data")
            if not isinstance(data, Mapping) or not data.get("access_token"):
                raise GuodunAuthError("login response does not contain access_token")

            expires_at = data.get("expires_at")
            if not expires_at and data.get("expires_in") is not None:
                try:
                    expires_at = (
                        datetime.now(timezone.utc) + timedelta(seconds=float(data["expires_in"]))
                    ).isoformat()
                except (TypeError, ValueError):
                    expires_at = None
            self._api_key = key
            self._credentials = Credentials(
                api_key=key,
                access_token=str(data["access_token"]),
                expires_at=str(expires_at) if expires_at else None,
            )
            if self.config.save_credentials:
                save_credentials(self._credentials, self.config.credentials_path)
            return self._credentials.access_token

    def _auth_headers(self) -> dict[str, str]:
        token = self.access_token
        return {"basicToken": token, "Authorization": f"Bearer {token}"}

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        params: Mapping[str, Any] | None = None,
        authenticated: bool = True,
        raise_for_code: bool = True,
    ) -> dict[str, Any]:
        """Send a request and return the complete platform response envelope."""
        if authenticated and (
            self._credentials is None
            or (self._credentials.is_expired() and self.config.auto_refresh)
        ):
            self.login()

        attempts = self.config.max_retries + 1
        refreshed = False
        last_error: BaseException | None = None
        for attempt in range(attempts):
            headers = self._auth_headers() if authenticated else {}
            try:
                response = self._session.request(
                    method.upper(),
                    self.config.base_url + path,
                    json=json_body,
                    params=params,
                    headers=headers,
                    timeout=self.config.timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    time.sleep(self.config.retry_delay)
                    continue
                raise GuodunRequestError(f"request to {path} failed: {exc}") from exc

            if (
                response.status_code == 401
                and authenticated
                and self.config.auto_refresh
                and not refreshed
            ):
                self.login()
                refreshed = True
                continue
            if response.status_code != 200:
                message = getattr(response, "text", "")
                error = GuodunRequestError(
                    f"request to {path} failed with HTTP {response.status_code}: {message}",
                    status_code=response.status_code,
                )
                last_error = error
                if response.status_code >= 500 and attempt + 1 < attempts:
                    time.sleep(self.config.retry_delay)
                    continue
                raise error
            try:
                envelope = response.json()
            except ValueError as exc:
                raise GuodunRequestError(
                    f"response from {path} is not valid JSON"
                ) from exc
            if not isinstance(envelope, dict):
                raise GuodunRequestError(f"response from {path} must be a JSON object")
            if raise_for_code and envelope.get("code", -1) != 0:
                raise GuodunApiError(
                    str(envelope.get("message") or envelope.get("msg") or "platform request failed"),
                    code=envelope.get("code"),
                    data=envelope.get("data"),
                    status_code=response.status_code,
                )
            return envelope

        if last_error:
            raise GuodunRequestError(str(last_error)) from last_error
        raise GuodunRequestError(f"request to {path} failed")

    def get_data(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.request(method, path, **kwargs).get("data")

    def close(self) -> None:
        close = getattr(self._session, "close", None)
        if close:
            close()

    def __enter__(self) -> "GuodunClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
