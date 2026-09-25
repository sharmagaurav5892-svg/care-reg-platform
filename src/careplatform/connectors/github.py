"""GitHub REST API connector.

The source documents live in a private GitHub repo. This connector reads
them through three API calls:

    1. GET /repos/{owner}/{repo}/commits/{branch}
         -> the latest commit SHA. This is our watermark: if it hasn't
            changed since the last run, nothing changed, and we stop early.

    2. GET /repos/{owner}/{repo}/git/trees/{commit_sha}?recursive=1
         -> every file in the repo at that commit, with its path, size and
            blob SHA. The blob SHA is Git's own content hash, so if a file's
            blob SHA is already in bronze, we already have that exact file.

    3. GET /repos/{owner}/{repo}/git/blobs/{blob_sha}
         (Accept: application/vnd.github.raw+json)
         -> the raw bytes of one file. Only called for files we don't have.

So a rerun with no changes costs 1 API call. A run after one file changed
costs 3. That is what "incremental" means in practice.

Auth: a fine-grained personal access token with read-only Contents
permission on this one repo (least privilege, docs/07). Without a token,
GitHub allows 60 requests an hour; with one, 5,000.
"""
from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath

import pandas as pd

from careplatform import config
from careplatform.connectors.base import FetchedFile, FetchResult, SourceConnector
from careplatform.connectors.http import ApiClient, ApiError

API = "https://api.github.com"
API_VERSION = "2022-11-28"


@dataclass(frozen=True)
class RemoteFile:
    path: str
    blob_sha: str
    size: int


def _source_for(path: str, sources: list[dict]) -> dict | None:
    """Longest matching repo_path wins, so nested folders map correctly."""
    matches = [s for s in sources if path.startswith(s["repo_path"])]
    return max(matches, key=lambda s: len(s["repo_path"])) if matches else None


def plan(files: list[RemoteFile], sources: list[dict], known_blobs: set[str]):
    """Decide which files to download. Pure function, easy to test."""
    ing = config.settings()["ingestion"]
    allowed_ext = {e.lower() for e in ing["allowed_extensions"]}
    max_bytes = ing["max_file_size_mb"] * 1024 * 1024

    todo, skipped = [], Counter()
    for f in files:
        name = PurePosixPath(f.path).name
        if name.startswith(".") or name.lower() == "readme.md":
            skipped["housekeeping"] += 1
            continue
        src = _source_for(f.path, sources)
        if src is None:
            skipped["unregistered_path"] += 1
        elif src["status"] != "approved":
            skipped["source_not_approved"] += 1      # governance gate: never downloaded
        elif PurePosixPath(f.path).suffix.lower() not in allowed_ext:
            skipped["extension_not_allowed"] += 1
        elif f.size > max_bytes:
            skipped["too_large"] += 1
        elif f.blob_sha in known_blobs:
            skipped["unchanged"] += 1
        else:
            todo.append((f, src))
    return todo, skipped


class GitHubConnector(SourceConnector):
    source_system = "github"

    def __init__(self, owner: str, repo: str, branch: str, run_id: str,
                 token: str | None = None, session=None, sleep=None):
        self.owner, self.repo, self.branch = owner, repo, branch
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "care-reg-platform",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        kwargs = {"session": session}
        if sleep:
            kwargs["sleep"] = sleep
        self.client = ApiClient(API, connector=self.source_system, run_id=run_id, headers=headers, **kwargs)

    @classmethod
    def from_settings(cls, run_id: str) -> "GitHubConnector":
        config.load_env()
        gh = config.settings()["ingestion"]["github"]
        owner = os.getenv("GITHUB_OWNER") or gh["owner"]
        if not owner or owner.startswith("<"):
            raise RuntimeError(
                "Set your GitHub username in config/settings.yaml "
                "(ingestion.github.owner) or GITHUB_OWNER in .env"
            )
        return cls(owner, gh["repo"], gh["branch"], run_id, token=os.getenv("GITHUB_TOKEN") or None)

    @property
    def scope(self) -> str:
        """Identifies what the watermark refers to."""
        return f"{self.owner}/{self.repo}@{self.branch}"

    def _repo(self, suffix: str) -> str:
        return f"/repos/{self.owner}/{self.repo}{suffix}"

    # ---------- API calls ----------

    def head_commit(self) -> str:
        try:
            return self.client.get_json(self._repo(f"/commits/{self.branch}"))["sha"]
        except ApiError as e:
            if e.status_code == 404:
                raise ApiError(
                    f"Repo {self.scope} not found. Check owner/repo/branch, and that your "
                    f"token has Contents read access to it (private repos return 404, not 403).",
                    404,
                ) from e
            raise

    def list_files(self, commit_sha: str, prefix: str = "") -> list[RemoteFile]:
        tree = self.client.get_json(self._repo(f"/git/trees/{commit_sha}"), params={"recursive": "1"})
        if tree.get("truncated"):
            # Over ~100k entries. Not a risk for this repo; fail loudly instead of silently missing files.
            raise ApiError("Tree listing was truncated by GitHub. Split the repo or list folders one by one.")
        return [
            RemoteFile(item["path"], item["sha"], item.get("size", 0))
            for item in tree["tree"]
            if item["type"] == "blob" and item["path"].startswith(prefix)
        ]

    def download(self, f: RemoteFile) -> bytes:
        resp = self.client.get(self._repo(f"/git/blobs/{f.blob_sha}"), accept="application/vnd.github.raw+json")
        return resp.content

    def list_commits(self, path: str | None = None, per_page: int = 100) -> list[dict]:
        """Commit history (paginated). Handy for showing when each source last changed."""
        params = {"sha": self.branch, "per_page": per_page}
        if path:
            params["path"] = path
        return list(self.client.paginate(self._repo("/commits"), params=params))

    def html_url(self, commit_sha: str, path: str) -> str:
        return f"https://github.com/{self.owner}/{self.repo}/blob/{commit_sha}/{path}"

    # ---------- the connector contract ----------

    def fetch(self, sources: list[dict], existing: pd.DataFrame, full_refresh: bool) -> FetchResult:
        from careplatform.ingestion import watermarks

        res = FetchResult()
        head = self.head_commit()
        last = watermarks.get(self.source_system, self.scope)
        res.info.update(commit=head[:12], previous_watermark=(last or "none")[:12])
        if head == last and not full_refresh:
            res.info["up_to_date"] = True
            return res

        root = config.settings()["ingestion"]["github"]["root_path"]
        files = self.list_files(head, prefix=root)
        mine = existing[existing["source_system"] == self.source_system]
        known = set() if full_refresh else set(mine["remote_hash"].dropna())
        todo, res.skipped = plan(files, [s for s in sources if s["connector"] == "github"], known)
        res.info["files_seen"] = len(files)

        for f, src in todo:
            res.items.append(FetchedFile(
                source=src,
                file_name=PurePosixPath(f.path).name,
                source_ref=f.path,
                source_url=self.html_url(head, f.path),
                data=self.download(f),
                source_version=head,
                remote_hash=f.blob_sha,
            ))
        res.watermarks.append((self.scope, head))
        return res
