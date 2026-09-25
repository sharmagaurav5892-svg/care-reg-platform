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


class FakeBCLaws:
    """Fake BC Laws API: /civix/document/id/complete/statreg/{id}[/xml].

    Supports ETag and If-None-Match like a well-behaved server, and can be
    switched to ignore validators or to publish some documents as HTML only.
    """
    BASE = "https://www.bclaws.gov.bc.ca/civix/document/id/complete/statreg/"

    def __init__(self, docs: dict[str, bytes]):
        self.docs = dict(docs)
        self.html_only: set[str] = set()     # these return 404 on /xml
        self.send_validators = True          # False = server ignores caching
        self.queued: list[requests.Response] = []
        self.requests: list[tuple[str, dict]] = []

    @staticmethod
    def etag(data: bytes) -> str:
        return '"' + hashlib.md5(data).hexdigest() + '"'

    def get(self, url, headers=None, params=None, timeout=None):
        headers = dict(headers or {})
        self.requests.append((url, headers))
        if self.queued:
            return self.queued.pop(0)
        rest = url.replace(self.BASE, "")
        doc_id, _, fmt = rest.partition("/")
        if doc_id not in self.docs or (fmt == "xml" and doc_id in self.html_only):
            return make_response(404, b"<error>not found</error>")
        data = self.docs[doc_id]
        if fmt != "xml":
            data = b"<html>" + data + b"</html>"
        if not self.send_validators:
            return make_response(200, data)
        tag = self.etag(data)
        if headers.get("If-None-Match") == tag:
            return make_response(304, b"", {"ETag": tag})
        return make_response(200, data, {"ETag": tag, "Last-Modified": "Tue, 04 Feb 2025 00:00:00 GMT"})
