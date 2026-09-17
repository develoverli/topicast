# Contributing to topicast

Bug reports, feature ideas, docs fixes and pull requests are all welcome.

## Ways to contribute

- **Report a bug** — [open an issue](https://github.com/develoverli/topicast/issues/new?template=bug_report.yml)
  with steps to reproduce, the topicast version, and the relevant log lines (strip your tokens first).
- **Suggest a feature** — [open a feature request](https://github.com/develoverli/topicast/issues/new?template=feature_request.yml)
  describing the use case before writing code, so we can agree on scope.
- **Add a webhook adapter** — see [Writing an adapter](#writing-a-webhook-adapter) below.
- **Report a vulnerability** — never in a public issue. Follow [SECURITY.md](SECURITY.md).

## Development setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```bash
git clone https://github.com/develoverli/topicast.git
cd topicast
uv sync --all-groups
uv run pre-commit install
```

| Command | Purpose |
|---|---|
| `uv run pytest` | Run the test suite |
| `uv run pytest --cov` | …with a coverage report |
| `uv run ruff check --fix .` | Lint |
| `uv run ruff format .` | Format |
| `uv run mypy` | Type check (strict) |
| `uv run mkdocs serve` | Preview the documentation |
| `uv run topicast --help` | Run the CLI from the checkout |

Running the server locally:

```bash
cp .env.example .env            # fill in TOPICAST_SECRET_KEY and the Telegram values
cp config.example.yaml config.yaml
uv run topicast check-config
uv run topicast serve
```

Tests never touch Telegram: they run against `FakeGateway` in `tests/conftest.py`.

## Project layout

```
src/topicast/
  api/         HTTP layer (routers, schemas, auth, uploads)
  delivery/    queue, rate limiting, formatting, Telegram gateway, worker
  db/          SQLAlchemy models and Alembic migrations
  hooks/       inbound webhook adapters
  config.py    settings (env) and the YAML schema
  cli.py       the `topicast` command
```

## Writing a webhook adapter

1. Add `src/topicast/hooks/<source>.py` with a class exposing `verify()` and `render()`
   (see `uptime_kuma.py` for a short example).
2. Register it in `src/topicast/hooks/registry.py`.
3. Add tests in `tests/test_hooks.py` with a realistic payload.
4. Document it in `docs/webhooks.md`.

`render()` returns a `HookMessage` (HTML is the default parse mode — escape everything you
interpolate) or `None` to acknowledge and ignore the event.

## Pull requests

- Keep the change focused; unrelated refactors make review harder.
- Add or update tests, and run `ruff`, `mypy` and `pytest` before pushing.
- Use [Conventional Commits](https://www.conventionalcommits.org/) for the PR title
  (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`). Releases and the changelog
  are generated from them.
- Database changes need an Alembic migration in `src/topicast/db/migrations/versions/`.

## Code of conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md).
