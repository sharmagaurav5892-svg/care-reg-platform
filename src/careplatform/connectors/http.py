"""A small, careful HTTP client that every connector uses.

What it handles, and why each one matters:

1. Timeouts
   Every request has a timeout. Without one, a hung server hangs your
   pipeline forever and nobody notices until morning.

2. Retries with exponential backoff and jitter
   Transient errors (500, 502, 503, 504, dropped connections) are retried
   after 1s, 2s, 4s, 8s... plus a little random jitter so many clients
   don't all retry at the same instant.
   Client errors (400, 401, 404) are NOT retried. Retrying a wrong request
   just gets the same answer slower.

3. Rate limits
   429 with a Retry-After header: wait exactly that long.
   GitHub style 403 with X-RateLimit-Remaining: 0: wait until the time in
   X-RateLimit-Reset, unless that is too far away, then fail clearly.

4. Conditional requests
   Pass If-None-Match / If-Modified-Since in `headers`. If the server says
   304 Not Modified, the file hasn't changed and no body is sent.

5. Pagination
   Follows the `Link: <...>; rel="next"` header until there are no pages left.

6. Audit log
   Every attempt (including retries) is recorded and flushed to
   ops.api_call_log at the end of the run. The auth token is never logged.
"""
from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, Iterator

import pandas as pd
import requests

from careplatform import config, lakehouse

LOG_TABLE = "ops.api_call_log"
RETRY_STATUSES = {429, 500, 502, 503, 504}


class ApiError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class ApiClient:
    def __init__(
        self,
        base_url: str,
        connector: str,
        run_id: str,
        headers: dict | None = None,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ):
        http = config.settings()["http"]
        self.base_url = base_url.rstrip("/")
        self.connector = connector
        self.run_id = run_id
        self.headers = headers or {}
        self.session = session or requests.Session()
        self.timeout = http["timeout_seconds"]
        self.max_retries = http["max_retries"]
        self.backoff_base = http["backoff_base_seconds"]
        self.max_rate_wait = http["max_rate_limit_wait_seconds"]
        self._sleep = sleep
        self._now = now
        self.calls: list[dict] = []

    # ---------- core ----------

    def get(self, path: str, params: dict | None = None, accept: str | None = None,
            headers: dict | None = None) -> requests.Response:
        """GET with retries. A 304 Not Modified counts as success (resp.ok is True for 3xx)."""
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {**self.headers, **(headers or {})}
        if accept:
            headers["Accept"] = accept

        for attempt in range(1, self.max_retries + 2):
            started = time.perf_counter()
            resp, error = None, None
            try:
                resp = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as e:
                error = e
            latency = int((time.perf_counter() - started) * 1000)
            self._record(url, attempt, latency, resp, error)

            if resp is not None and resp.ok:
                return resp

            is_last = attempt > self.max_retries
            wait = self._wait_seconds(resp, attempt)
            if wait is None or is_last:
                if resp is None:
                    raise ApiError(f"GET {url} failed after {attempt} attempts: {error}")
                raise ApiError(
                    f"GET {url} returned {resp.status_code}: {resp.text[:300]}",
                    status_code=resp.status_code,
                )
            self._sleep(wait)
        raise AssertionError("unreachable")

    def get_json(self, path: str, params: dict | None = None):
        return self.get(path, params=params).json()

    def paginate(self, path: str, params: dict | None = None) -> Iterator[dict]:
        """Yield every item across all pages, following the Link header."""
        url, p = path, dict(params or {})
        while url:
            resp = self.get(url, params=p)
            yield from resp.json()
            url = resp.links.get("next", {}).get("url")
            p = None  # the next URL already carries the query string

    # ---------- retry decisions ----------

    def _wait_seconds(self, resp: requests.Response | None, attempt: int) -> float | None:
        """How long to wait before retrying, or None if we should not retry."""
        if resp is None:
            return self._backoff(attempt)  # connection error or timeout

        # primary rate limit (GitHub returns 403 or 429 with remaining = 0)
        if resp.headers.get("X-RateLimit-Remaining") == "0" and resp.status_code in (403, 429):
            reset = float(resp.headers.get("X-RateLimit-Reset", self._now()))
            wait = max(0.0, reset - self._now()) + 1
            if wait > self.max_rate_wait:
                raise ApiError(
                    f"Rate limit exhausted. Resets in {int(wait)}s, which is over the "
                    f"{self.max_rate_wait}s limit. Add a GITHUB_TOKEN or try later.",
                    status_code=resp.status_code,
                )
            return wait

        if resp.status_code in RETRY_STATUSES:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                return float(retry_after)
            return self._backoff(attempt)

        return None  # 400, 401, 403 (permissions), 404 and so on: don't retry

    def _backoff(self, attempt: int) -> float:
        return self.backoff_base * (2 ** (attempt - 1)) + random.uniform(0, 0.5)

    # ---------- audit ----------

    def _record(self, url, attempt, latency_ms, resp, error) -> None:
        endpoint = url.replace(self.base_url, "").split("?")[0]
        remaining = resp.headers.get("X-RateLimit-Remaining") if resp is not None else None
        self.calls.append({
            "call_id": str(uuid.uuid4()),
            "run_id": self.run_id,
            "connector": self.connector,
            "method": "GET",
            "endpoint": endpoint,
            "status_code": resp.status_code if resp is not None else None,
            "attempt": attempt,
            "latency_ms": latency_ms,
            "response_bytes": len(resp.content) if resp is not None else None,
            "rate_limit_remaining": int(remaining) if remaining and remaining.isdigit() else None,
            "error_type": type(error).__name__ if error else (
                None if resp is not None and resp.ok else f"HTTP{resp.status_code}" if resp is not None else None
            ),
            "called_at": datetime.now(timezone.utc),
        })

    def flush_log(self) -> int:
        """Write recorded calls to ops.api_call_log. Called once at the end of a run."""
        n = lakehouse.append(LOG_TABLE, pd.DataFrame(self.calls)) if self.calls else 0
        self.calls = []
        return n
