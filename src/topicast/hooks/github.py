"""GitHub webhooks (push, pull_request, release, workflow_run, ping)."""

from __future__ import annotations

import hashlib
import hmac
from html import escape
from typing import Any

from topicast.config import AppConfig, Level
from topicast.hooks import HookError, HookMessage, HookRequest

SUPPORTED_EVENTS = frozenset({"ping", "push", "pull_request", "release", "workflow_run"})
_MAX_COMMITS = 5


def _e(value: object) -> str:
    return escape(str(value), quote=False)


def _link(url: object, label: object) -> str:
    return f'<a href="{escape(str(url), quote=True)}">{_e(label)}</a>'


def _repo(payload: dict[str, Any]) -> str:
    repo = payload.get("repository") or {}
    return _link(repo.get("html_url", ""), repo.get("full_name", "repository"))


def _push(p: dict[str, Any]) -> HookMessage | None:
    commits = p.get("commits") or []
    if p.get("deleted") or not commits:
        return None
    branch = str(p.get("ref", "")).removeprefix("refs/heads/")
    pusher = (p.get("pusher") or {}).get("name", "someone")
    lines = [
        f"📦 <b>{_e(pusher)}</b> pushed {len(commits)} commit(s) to "
        f"<code>{_e(branch)}</code> in {_repo(p)}"
    ]
    for commit in commits[:_MAX_COMMITS]:
        title = str(commit.get("message", "")).splitlines()[0] if commit.get("message") else ""
        lines.append(f"• {_link(commit.get('url', ''), str(commit.get('id', ''))[:7])} {_e(title)}")
    if len(commits) > _MAX_COMMITS:
        lines.append(_link(p.get("compare", ""), f"…and {len(commits) - _MAX_COMMITS} more"))
    return HookMessage(text="\n".join(lines), level=Level.INFO)


def _pull_request(p: dict[str, Any]) -> HookMessage | None:
    action = p.get("action")
    pr = p.get("pull_request") or {}
    if action == "closed":
        action = "merged" if pr.get("merged") else "closed"
    if action not in {"opened", "reopened", "merged", "closed", "ready_for_review"}:
        return None
    level = Level.SUCCESS if action == "merged" else Level.INFO
    user = (pr.get("user") or {}).get("login", "someone")
    text = (
        f"🔀 PR {_link(pr.get('html_url', ''), '#' + str(pr.get('number', '')))} "
        f"<b>{_e(action)}</b> by {_e(user)} in {_repo(p)}\n{_e(pr.get('title', ''))}"
    )
    return HookMessage(text=text, level=level)


def _release(p: dict[str, Any]) -> HookMessage | None:
    if p.get("action") != "published":
        return None
    release = p.get("release") or {}
    name = release.get("name") or release.get("tag_name", "")
    text = f"🚀 Release {_link(release.get('html_url', ''), name)} published in {_repo(p)}"
    return HookMessage(text=text, level=Level.SUCCESS)


def _workflow_run(p: dict[str, Any]) -> HookMessage | None:
    if p.get("action") != "completed":
        return None
    run = p.get("workflow_run") or {}
    conclusion = run.get("conclusion") or "unknown"
    level = {
        "success": Level.SUCCESS,
        "failure": Level.ERROR,
        "timed_out": Level.ERROR,
        "cancelled": Level.WARNING,
    }.get(conclusion, Level.INFO)
    text = (
        f"⚙️ Workflow {_link(run.get('html_url', ''), run.get('name', 'workflow'))} "
        f"<b>{_e(conclusion)}</b> on <code>{_e(run.get('head_branch', ''))}</code> in {_repo(p)}"
    )
    return HookMessage(text=text, level=level)


class GitHubAdapter:
    name = "github"

    def verify(self, body: bytes, request: HookRequest, config: AppConfig) -> None:
        secret = config.hooks.github.secret
        if secret is None:
            return
        signature = request.headers.get("x-hub-signature-256", "")
        expected = (
            "sha256="
            + hmac.new(secret.get_secret_value().encode(), body, hashlib.sha256).hexdigest()
        )
        if not hmac.compare_digest(signature, expected):
            raise HookError(401, "invalid_signature", "X-Hub-Signature-256 does not match.")

    def render(self, payload: Any, request: HookRequest, config: AppConfig) -> HookMessage | None:
        event = request.headers.get("x-github-event", "")
        allowed = config.hooks.github.events or SUPPORTED_EVENTS
        if event not in SUPPORTED_EVENTS or event not in allowed:
            return None
        if not isinstance(payload, dict):
            raise HookError(422, "invalid_payload", "Expected a JSON object.")
        if event == "ping":
            return HookMessage(
                text=f"🏓 GitHub webhook connected for {_repo(payload)}", level=Level.INFO
            )
        handlers = {
            "push": _push,
            "pull_request": _pull_request,
            "release": _release,
            "workflow_run": _workflow_run,
        }
        return handlers[event](payload)
