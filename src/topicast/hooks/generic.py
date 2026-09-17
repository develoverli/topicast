"""Generic JSON webhook, optionally rendered with a Jinja2 template from the config."""

from __future__ import annotations

import json
from html import escape
from typing import Any

from jinja2 import StrictUndefined, TemplateError
from jinja2.sandbox import ImmutableSandboxedEnvironment

from topicast.config import AppConfig, Level
from topicast.delivery.formatting import ParseMode
from topicast.hooks import HookError, HookMessage, HookRequest

_env = ImmutableSandboxedEnvironment(autoescape=False, undefined=StrictUndefined)
_env.filters["html"] = lambda value: escape(str(value), quote=False)

_PARSE_MODES: dict[str, ParseMode | None] = {
    "html": "HTML",
    "markdownv2": "MarkdownV2",
    "plain": None,
    "none": None,
}
_MAX_PLAIN_JSON = 3500


def _level(value: object) -> Level | None:
    try:
        return Level(str(value).lower()) if value else None
    except ValueError:
        return None


class GenericAdapter:
    name = "generic"

    def verify(self, body: bytes, request: HookRequest, config: AppConfig) -> None:
        return None

    def render(self, payload: Any, request: HookRequest, config: AppConfig) -> HookMessage:
        template_name = request.query.get("template")
        level = _level(request.query.get("level"))
        if isinstance(payload, dict) and level is None:
            level = _level(payload.get("level"))

        if template_name:
            source = config.hooks.templates.get(template_name)
            if source is None:
                raise HookError(404, "unknown_template", f"Template '{template_name}' not found.")
            mode_name = request.query.get("parse_mode", "html").lower()
            if mode_name not in _PARSE_MODES:
                raise HookError(
                    422, "invalid_parse_mode", "parse_mode must be html, markdownv2 or plain."
                )
            try:
                text = _env.from_string(source).render(payload=payload).strip()
            except TemplateError as exc:
                raise HookError(422, "template_error", f"Template failed: {exc}") from exc
            if not text:
                raise HookError(422, "empty_message", "Template rendered an empty message.")
            return HookMessage(text=text, parse_mode=_PARSE_MODES[mode_name], level=level)

        if isinstance(payload, dict):
            title = payload.get("title")
            body = payload.get("text") or payload.get("message") or payload.get("body")
            if isinstance(body, str) and body.strip():
                parts = [f"<b>{escape(str(title), quote=False)}</b>"] if title else []
                parts.append(escape(body, quote=False))
                return HookMessage(text="\n".join(parts), level=level)

        dumped = json.dumps(payload, indent=2, ensure_ascii=False)
        if len(dumped) > _MAX_PLAIN_JSON:
            dumped = dumped[:_MAX_PLAIN_JSON] + "\n…"
        return HookMessage(text=f"<pre>{escape(dumped, quote=False)}</pre>", level=level)
