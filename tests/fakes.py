"""A fake GitHub API for tests.

It answers the same three endpoints the connector uses, from an in-memory
repo. Tests can push new commits, and can queue errors (503, 429, rate
limit) to check the retry logic. No network needed, so CI stays fast and free.
"""
from __future__ import annotations

import hashlib
import json

import requests


def git_blob_sha(data: bytes) -> str:
    """Same formula Git uses, so blob SHAs look real."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def make_response(status: int, body=b"", headers: dict | None = None, url: str = "") -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body if isinstance(body, bytes) else json.dumps(body).encode()
    r.headers.update(headers or {})
    r.url = url
    return r


class FakeGitHub:
    def __init__(self, owner="gaurav", repo="care-reg-source-docs", branch="main"):
        self.prefix = f"https://api.github.com/repos/{owner}/{repo}"
        self.branch = branch
        self.files: dict[str, bytes] = {}
        self.head = None
        self.queued: list[requests.Response] = []   # errors to return before normal answers
        self.requests: list[tuple[str, dict]] = []
        self.commits = 0

    # --- test helpers ---
    def commit(self, files: dict[str, bytes]) -> str:
        """Replace repo contents and make a new head commit."""
        self.files = dict(files)
        self.commits += 1
        self.head = hashlib.sha1(f"commit-{self.commits}".encode()).hexdigest()
        return self.head

    def queue(self, status: int, headers: dict | None = None, body=b"error"):
        self.queued.append(make_response(status, body, headers))

    # --- requests.Session interface ---
    def get(self, url, headers=None, params=None, timeout=None):
        self.requests.append((url, dict(headers or {})))
        if self.queued:
            return self.queued.pop(0)
        path = url.replace(self.prefix, "")
        if path == f"/commits/{self.branch}":
            return make_response(200, {"sha": self.head})
        if path == f"/git/trees/{self.head}":
            tree = [{"path": p, "type": "blob", "sha": git_blob_sha(d), "size": len(d)}
                    for p, d in self.files.items()]
            return make_response(200, {"sha": self.head, "tree": tree, "truncated": False})
        if path.startswith("/git/blobs/"):
            sha = path.rsplit("/", 1)[1]
            for d in self.files.values():
                if git_blob_sha(d) == sha:
                    return make_response(200, d)
        return make_response(404, {"message": "Not Found"})
