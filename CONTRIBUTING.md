# Contributing

```bash
git clone https://github.com/Faiziimam/agentcart && cd agentcart
```

```bash
uv venv && uv pip install -e ".[dev]" && uv run playwright install chromium
```

```bash
uv run ruff check . && uv run mypy src && uv run pytest
```

- Read [SECURITY.md](SECURITY.md) "Design rules" first. PRs that weaken them will not be merged.
- New store? See [docs/ADAPTERS.md](docs/ADAPTERS.md).
- Every bug fix comes with a test, and every security fix with a regression test.
- Keep dependencies minimal. Each new one needs a justification in the PR.
- Commits must be signed (`git commit -S`) for changes under `src/agentcart/{vault,policy,audit,netsafe,checkout}.py`.
