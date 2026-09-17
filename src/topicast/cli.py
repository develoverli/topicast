"""`topicast` command line."""

from __future__ import annotations

import asyncio
import contextlib
import secrets
from pathlib import Path
from typing import Annotated

import typer

from topicast import __version__
from topicast.config import ConfigError, Settings, get_settings, load_app_config
from topicast.db import Database, MessageStatus, run_migrations
from topicast.delivery.service import MessageService, OutgoingMessage, ServiceError
from topicast.keys import KeyStore, KeyStoreError
from topicast.logs import configure_logging
from topicast.security import ALL_ALIASES, Scope

app = typer.Typer(
    name="topicast",
    help="Self-hosted notification hub for Telegram forum topics.",
    no_args_is_help=True,
    add_completion=False,
)
keys_app = typer.Typer(help="Manage API keys.", no_args_is_help=True)
messages_app = typer.Typer(help="Inspect the delivery queue.", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
app.add_typer(messages_app, name="messages")

CONFIG_TEMPLATE = """\
# topicast configuration — https://develoverli.github.io/topicast/configuration/
bots:
  default:
    token: ${TELEGRAM_BOT_TOKEN}

chats:
  homelab:
    id: ${TELEGRAM_CHAT_ID}      # e.g. -1001234567890 (negative for groups)
    bot: default

aliases:
  alerts:
    chat: homelab
    topic: 5                     # omit to post in the group's General topic
    description: Infrastructure alerts
  deploys:
    chat: homelab
    topic: 7

defaults:
  dedupe_window: 60              # seconds; 0 disables deduplication
  silent_levels: [info, success]

hooks:
  github:
    secret: ${GITHUB_WEBHOOK_SECRET:-}
  templates:
    plain: "{{ payload.message }}"
"""


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _settings() -> Settings:
    try:
        return get_settings()
    except Exception as exc:  # pragma: no cover - surfaced as a CLI error
        _fail(str(exc))
        raise


def _store(settings: Settings) -> tuple[Database, KeyStore]:
    run_migrations(settings.database_path)
    db = Database(settings.database_path)
    return db, KeyStore(db, settings.secret_key.get_secret_value())


@app.callback()
def main() -> None:
    """topicast CLI."""


@app.command()
def version() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.command()
def init(
    config: Annotated[Path, typer.Option(help="Where to write the config file.")] = Path(
        "config.yaml"
    ),
    force: Annotated[bool, typer.Option(help="Overwrite an existing file.")] = False,
) -> None:
    """Create a starter config.yaml and print the secrets you still need to set."""
    if config.exists() and not force:
        _fail(f"{config} already exists (use --force to overwrite)")
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    typer.secho(f"wrote {config}", fg=typer.colors.GREEN)
    typer.echo("\nSet these environment variables (for example in .env):\n")
    typer.echo(f"TOPICAST_SECRET_KEY={secrets.token_urlsafe(32)}")
    typer.echo("TELEGRAM_BOT_TOKEN=123456:ABC...   # from @BotFather")
    typer.echo("TELEGRAM_CHAT_ID=-1001234567890    # your group id")


@app.command("check-config")
def check_config() -> None:
    """Validate the configuration file and show what it resolves to."""
    settings = _settings()
    try:
        config = load_app_config(settings.config_file)
    except ConfigError as exc:
        _fail(str(exc))
        return
    typer.secho(f"{settings.config_file}: OK", fg=typer.colors.GREEN)
    for name, alias in config.aliases.items():
        chat = config.chats[alias.chat]
        topic = f"topic {alias.topic}" if alias.topic else "General"
        typer.echo(
            f"  {name:<20} → chat {chat.id} ({alias.chat}), {topic}, "
            f"dedupe {config.dedupe_window(name)}s, bot {chat.bot}"
        )


@app.command()
def doctor(
    probe: Annotated[
        bool,
        typer.Option(
            "--probe",
            help="Also send a real message to every alias and delete it again.",
        ),
    ] = False,
) -> None:
    """Check the deployment against Telegram: token, chat, permissions, topics."""
    from topicast.delivery.telegram import DeliveryError, PTBGateway
    from topicast.doctor import Report, Status, run_doctor

    settings = _settings()
    try:
        config = load_app_config(settings.config_file)
    except ConfigError as exc:
        _fail(str(exc))
        return

    async def run() -> Report:
        run_migrations(settings.database_path)
        db = Database(settings.database_path)
        gateway = PTBGateway({n: b.token.get_secret_value() for n, b in config.bots.items()})
        # A bad token is reported as a failed check, not as a crash.
        with contextlib.suppress(DeliveryError):
            await gateway.start()
        try:
            return await run_doctor(settings, config, gateway, db, probe=probe)
        finally:
            await gateway.close()
            await db.dispose()

    report = asyncio.run(run())
    marks = {
        Status.OK: ("✔", typer.colors.GREEN),
        Status.WARN: ("!", typer.colors.YELLOW),
        Status.FAIL: ("✖", typer.colors.RED),
    }
    for check in report.checks:
        mark, color = marks[check.status]
        typer.secho(f"{mark} {check.name}: {check.detail}", fg=color)
        if check.hint:
            typer.secho(f"  → {check.hint}", fg=typer.colors.BRIGHT_BLACK)

    counts = report.counts
    typer.echo(
        f"\n{counts[Status.OK]} ok, {counts[Status.WARN]} warnings, {counts[Status.FAIL]} failures"
    )
    if not probe:
        typer.secho(
            "Run `topicast doctor --probe` to send a real test message to every alias.",
            fg=typer.colors.BRIGHT_BLACK,
        )
    if report.failed:
        raise typer.Exit(code=1)


@app.command()
def migrate() -> None:
    """Upgrade the database schema."""
    settings = _settings()
    run_migrations(settings.database_path)
    typer.secho(f"database up to date: {settings.database_path}", fg=typer.colors.GREEN)


@app.command()
def serve() -> None:
    """Run the HTTP server (this is what the Docker image does)."""
    import uvicorn

    settings = _settings()
    configure_logging(settings.log_level, settings.log_format)
    uvicorn.run(
        "topicast.api.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
    )


@keys_app.command("create")
def keys_create(
    name: Annotated[str, typer.Argument(help="Service name, e.g. uptime-kuma.")],
    scope: Annotated[
        list[str] | None, typer.Option(help="send, edit or hooks. Repeatable.")
    ] = None,
    alias: Annotated[
        list[str] | None,
        typer.Option(help=f"Alias this key may use, or '{ALL_ALIASES}'. Repeatable."),
    ] = None,
) -> None:
    """Create an API key. The key is shown once and never stored in clear."""
    settings = _settings()
    scopes = scope or [Scope.SEND.value]
    aliases = alias or [ALL_ALIASES]
    try:
        parsed = [Scope(s) for s in scopes]
    except ValueError as exc:
        _fail(str(exc))
        return

    async def run() -> tuple[str, str]:
        db, store = _store(settings)
        try:
            key, token = await store.create(name, parsed, aliases)
        finally:
            await db.dispose()
        return key.id, token

    try:
        key_id, token = asyncio.run(run())
    except KeyStoreError as exc:
        _fail(str(exc))
        return
    typer.secho(f"key '{name}' created (id {key_id})", fg=typer.colors.GREEN)
    typer.echo(f"scopes: {', '.join(scopes)}   aliases: {', '.join(aliases)}")
    typer.secho("\n" + token + "\n", fg=typer.colors.YELLOW, bold=True)
    typer.echo("Copy it now — it cannot be shown again.")


@keys_app.command("list")
def keys_list(
    show_revoked: Annotated[bool, typer.Option(help="Include revoked keys.")] = False,
) -> None:
    """List API keys."""
    settings = _settings()

    async def run() -> list[tuple[str, str, str, str, str]]:
        db, store = _store(settings)
        try:
            keys = await store.list_keys(include_revoked=show_revoked)
            return [
                (
                    k.name,
                    k.id,
                    ",".join(k.scopes),
                    ",".join(k.aliases),
                    "revoked" if k.revoked_at else "active",
                )
                for k in keys
            ]
        finally:
            await db.dispose()

    rows = asyncio.run(run())
    if not rows:
        typer.echo("no keys yet — create one with `topicast keys create <name>`")
        return
    typer.echo(f"{'NAME':<24}{'ID':<10}{'SCOPES':<18}{'ALIASES':<24}STATUS")
    for name, key_id, scopes, aliases, state in rows:
        typer.echo(f"{name:<24}{key_id:<10}{scopes:<18}{aliases:<24}{state}")


@keys_app.command("revoke")
def keys_revoke(name: str) -> None:
    """Revoke an API key."""
    settings = _settings()

    async def run() -> None:
        db, store = _store(settings)
        try:
            await store.revoke(name)
        finally:
            await db.dispose()

    try:
        asyncio.run(run())
    except KeyStoreError as exc:
        _fail(str(exc))
        return
    typer.secho(f"key '{name}' revoked", fg=typer.colors.GREEN)


def _service(settings: Settings) -> tuple[Database, MessageService]:
    from topicast.delivery.ratelimit import RateLimiter
    from topicast.delivery.telegram import PTBGateway

    config = load_app_config(settings.config_file)
    run_migrations(settings.database_path)
    db = Database(settings.database_path)
    gateway = PTBGateway({n: b.token.get_secret_value() for n, b in config.bots.items()})
    return db, MessageService(db, config, settings, gateway, RateLimiter())


@messages_app.command("list")
def messages_list(
    status: Annotated[str | None, typer.Option(help="queued, sending, delivered, failed.")] = None,
    limit: Annotated[int, typer.Option(help="How many rows.")] = 20,
) -> None:
    """Show the most recent messages."""
    settings = _settings()
    state = MessageStatus(status) if status else None

    async def run() -> list[tuple[str, str, str, str, str]]:
        db, service = _service(settings)
        try:
            rows = await service.list_messages(status=state, limit=limit)
            return [
                (
                    m.id[:12],
                    m.status,
                    m.alias,
                    m.created_at.strftime("%Y-%m-%d %H:%M:%S"),
                    (m.last_error or "")[:48],
                )
                for m in rows
            ]
        finally:
            await db.dispose()

    rows = asyncio.run(run())
    typer.echo(f"{'ID':<14}{'STATUS':<12}{'ALIAS':<16}{'CREATED (UTC)':<22}ERROR")
    for row in rows:
        typer.echo(f"{row[0]:<14}{row[1]:<12}{row[2]:<16}{row[3]:<22}{row[4]}")


@messages_app.command("retry")
def messages_retry(message_id: str) -> None:
    """Requeue a failed message."""
    settings = _settings()

    async def run() -> None:
        db, service = _service(settings)
        try:
            await service.retry(message_id)
        finally:
            await db.dispose()

    try:
        asyncio.run(run())
    except ServiceError as exc:
        _fail(exc.detail)
        return
    typer.secho(f"{message_id} requeued", fg=typer.colors.GREEN)


@app.command()
def send(
    alias: Annotated[str, typer.Argument(help="Alias to send to.")],
    text: Annotated[str, typer.Argument(help="Message text.")],
    level: Annotated[
        str | None, typer.Option(help="info, success, warning, error, critical.")
    ] = None,
) -> None:
    """Queue a message from the command line (the running server delivers it)."""
    from topicast.config import Level

    settings = _settings()
    parsed_level = Level(level) if level else None

    async def run() -> str:
        db, service = _service(settings)
        try:
            result = await service.enqueue(
                OutgoingMessage(alias=alias, text=text, level=parsed_level, source="cli"),
                key_id=None,
            )
            return result.message.id
        finally:
            await db.dispose()

    try:
        message_id = asyncio.run(run())
    except (ServiceError, ConfigError) as exc:
        _fail(str(exc))
        return
    typer.secho(f"queued {message_id}", fg=typer.colors.GREEN)
