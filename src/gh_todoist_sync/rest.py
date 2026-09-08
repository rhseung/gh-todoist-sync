"""One JSON-over-HTTP helper for both APIs.

GitHub and Todoist differ only in base URL, auth header, and how they page.
Everything else -- connection reuse, timeouts, raising on error -- is shared,
which is the whole reason there is no SDK on either side.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any, Self

import httpx

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def cli_token(env_var: str, argv: list[str]) -> str:
    """Prefer an explicit token, else borrow the one the CLI already keeps.

    Reading gh's and td's stored credentials is what keeps this zero-config:
    no PAT to mint, rotate, or leave lying around in a dotfile.
    """
    if value := os.environ.get(env_var):
        return value
    out = subprocess.run(argv, capture_output=True, text=True, check=False)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(
            f"set {env_var}, or log in so `{' '.join(argv)}` works: {out.stderr.strip()}"
        )
    return out.stdout.strip()


class Client:
    def __init__(self, base_url: str, token: str, **headers: str) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", **headers},
            timeout=TIMEOUT,
            follow_redirects=True,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self._http.close()

    def get(self, path: str, **params: Any) -> Any:
        return self._request("GET", path, params=params)

    def post(self, path: str, **body: Any) -> Any:
        return self._request("POST", path, json=body)

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._http.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None
