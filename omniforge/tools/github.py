"""GitHub read/write tools.

Reads work unauthenticated (rate-limited). Every write needs GITHUB_TOKEN, the
"github.write" permission, and - because they are destructive - a recorded human
approval. Without a token the tool raises IntegrationNotConfigured instead of
pretending to succeed.
"""
from __future__ import annotations

import base64
import os
from typing import Any

import httpx

from .base import IntegrationNotConfigured, Tool, ToolError

API = "https://api.github.com"


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = API,
        transport: Any | None = None,
        timeout: float = 25.0,
    ) -> None:
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN", "")
        self.base_url = base_url.rstrip("/")
        self.transport = transport
        self.timeout = timeout

    # ------------------------------------------------------------------ plumbing
    def _headers(self, write: bool) -> dict[str, str]:
        if write and not self.token:
            raise IntegrationNotConfigured(
                "GITHUB_TOKEN is not set: GitHub writes are unavailable. "
                "Set GITHUB_TOKEN with repo scope and retry."
            )
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "OmniForge/0.3",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _request(self, method: str, path: str, *, write: bool = False, **kwargs) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        client_kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            client_kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**client_kwargs) as client:
            resp = await client.request(method, url, headers=self._headers(write), **kwargs)
            if resp.status_code == 401:
                raise IntegrationNotConfigured("GitHub rejected the token (401).")
            if resp.status_code == 403:
                raise ToolError(f"GitHub refused the call (403): {resp.text[:200]}")
            if resp.status_code >= 400:
                raise ToolError(f"GitHub {method} {path} failed ({resp.status_code}): {resp.text[:200]}")
            return resp.json() if resp.content else {}

    # ------------------------------------------------------------------- reads
    async def whoami(self) -> dict:
        return await self._request("GET", "/user")

    async def rate_limit(self) -> dict:
        return await self._request("GET", "/rate_limit")

    async def get_repo(self, repo: str) -> dict:
        return await self._request("GET", f"/repos/{repo}")

    async def get_file(self, repo: str, path: str, ref: str | None = None) -> dict:
        params = {"ref": ref} if ref else None
        data = await self._request("GET", f"/repos/{repo}/contents/{path}", params=params)
        if isinstance(data, dict) and data.get("content"):
            data["decoded"] = base64.b64decode(data["content"]).decode("utf-8", "replace")
        return data

    async def list_issues(self, repo: str, state: str = "open") -> list:
        return await self._request("GET", f"/repos/{repo}/issues", params={"state": state})

    # ------------------------------------------------------------------ writes
    async def put_file(
        self, repo: str, path: str, content: str, message: str,
        branch: str | None = None, sha: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "message": message,
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        }
        if branch:
            payload["branch"] = branch
        if sha:
            payload["sha"] = sha
        return await self._request("PUT", f"/repos/{repo}/contents/{path}", write=True, json=payload)

    async def create_branch(self, repo: str, branch: str, from_ref: str = "main") -> dict:
        base = await self._request("GET", f"/repos/{repo}/git/ref/heads/{from_ref}")
        sha = base["object"]["sha"]
        return await self._request(
            "POST", f"/repos/{repo}/git/refs", write=True,
            json={"ref": f"refs/heads/{branch}", "sha": sha},
        )

    async def open_pull_request(self, repo: str, title: str, head: str, base: str = "main",
                                body: str = "") -> dict:
        return await self._request(
            "POST", f"/repos/{repo}/pulls", write=True,
            json={"title": title, "head": head, "base": base, "body": body},
        )

    async def create_issue(self, repo: str, title: str, body: str = "") -> dict:
        return await self._request(
            "POST", f"/repos/{repo}/issues", write=True, json={"title": title, "body": body}
        )

    async def trigger_workflow(self, repo: str, workflow_id: str, ref: str = "main") -> dict:
        return await self._request(
            "POST", f"/repos/{repo}/actions/workflows/{workflow_id}/dispatches", write=True,
            json={"ref": ref},
        )


def register_github_tools(registry, settings=None) -> None:
    token = getattr(settings, "github_token", "") if settings is not None else os.getenv("GITHUB_TOKEN", "")
    base_url = getattr(settings, "github_base_url", API) if settings is not None else API
    client = GitHubClient(token=token or None, base_url=base_url)

    def tool(name: str, description: str, fn, parameters: dict, *, write: bool) -> None:
        registry.register(name, Tool(
            name=name,
            description=description,
            run=fn,
            parameters=parameters,
            permissions=("github.write",) if write else ("github.read",),
            destructive=write,
            requires_approval=write,
            enabled=True,
            category="github",
            requires_network=True,
            provider="github",
        ))

    tool("github_whoami", "Identify the authenticated GitHub user.",
         client.whoami, {}, write=False)
    tool("github_get_repo", "Read repository metadata.",
         client.get_repo, {"repo": "string owner/name"}, write=False)
    tool("github_get_file", "Read a file's content and decoded text.",
         client.get_file, {"repo": "string", "path": "string", "ref": "string (optional)"}, write=False)
    tool("github_list_issues", "List issues on a repository.",
         client.list_issues, {"repo": "string", "state": "open|closed|all"}, write=False)
    tool("github_put_file", "Create or update a file on a branch (commit).",
         client.put_file,
         {"repo": "string", "path": "string", "content": "string", "message": "string",
          "branch": "string (optional)", "sha": "string (optional)"}, write=True)
    tool("github_create_branch", "Create a branch from an existing ref.",
         client.create_branch, {"repo": "string", "branch": "string", "from_ref": "string"}, write=True)
    tool("github_open_pr", "Open a pull request.",
         client.open_pull_request,
         {"repo": "string", "title": "string", "head": "string", "base": "string", "body": "string"},
         write=True)
    tool("github_create_issue", "Open an issue.",
         client.create_issue, {"repo": "string", "title": "string", "body": "string"}, write=True)
    tool("github_trigger_workflow", "Trigger a GitHub Actions workflow dispatch.",
         client.trigger_workflow, {"repo": "string", "workflow_id": "string", "ref": "string"},
         write=True)
