# Contributing

Thanks for your interest in repo-impersonation-monitor.

## Development setup

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Before you open a PR

```bash
pytest --cov=repo_impersonation_monitor --cov-report=term-missing
ruff check src tests
```

- Keep coverage at or above the current bar (80% minimum; the suite currently
  runs much higher).
- Add tests first for new behavior (this project is built test-first).
- PE test fixtures are generated, not hand-written — see
  [tests/fixtures/_generate_pe_fixtures.py](tests/fixtures/_generate_pe_fixtures.py).

## Project ground rules (non-negotiable)

These come from the project's design and must hold in any contribution:

- **Never execute a downloaded binary.** Only parse its bytes (the PE reader is
  isolated in `pe.py` and a test statically forbids `subprocess`/`exec`/`ctypes`
  there). Reading metadata must never run anything.
- **Propose, never auto-accuse.** Keep the conservative multi-signal tier gate,
  the allowlist, and the human-in-the-loop. A false "impersonator" label against
  a legitimate fork, mirror, or translation is a real harm.
- **Keep dependencies light** — standard library plus a small number of
  well-known packages. GitHub I/O uses `urllib`; PE parsing uses `pefile`.

## The public contract

`action.yml` inputs and the issue/report output are what adopters depend on.
Treat changes to them as API changes: additive when possible, and a major
version bump if breaking (see [RELEASING.md](RELEASING.md)).

## Architecture

`candidates -> signals (+ pe) -> scoring -> report`, with `github_io` as the only
network module and `models` holding shared dataclasses. `main` wires inputs to
the pipeline. See [CLAUDE.md](CLAUDE.md) for the full design rationale.
