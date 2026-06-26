# Repo Impersonation Monitor

[![CI](https://github.com/markhinderliter/repo-impersonation-monitor/actions/workflows/ci.yml/badge.svg)](https://github.com/markhinderliter/repo-impersonation-monitor/actions/workflows/ci.yml)
[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/markhinderliter/repo-impersonation-monitor/badge)](https://scorecard.dev/viewer/?uri=github.com/markhinderliter/repo-impersonation-monitor)

A GitHub Action that watches for repositories impersonating your project and
hands you an evidence-ready abuse report when it finds one.

## Why this exists

While installing an open-source tool I'd seen featured in a video, I searched
GitHub for it by name and landed on the wrong repository — a clone, not the real
project. It looked right: the README and description were copied almost verbatim.
But the real source directories had been stripped out, and the only artifact it
shipped was a single Windows executable. Reading that binary's embedded version
metadata — without ever running it — showed it identifying itself as an entirely
unrelated "licensed" product. It was a malware dropper wearing the project's name,
riding the traffic the video sent.

I caught it. The next person searching that name might not.

This attack has a name — **repo confusion** — and it's a documented, growing
pattern. The defenses that exist mostly guard package registries like npm and
PyPI, where the risk is install-time code execution. They don't help here,
because this attack targets a human searching GitHub and picking the wrong
result. And the fastest way these clones come down is a takedown filed by the
real project's owner. This tool exists to put that lever in the owner's hand.

## What it does

You add it to your own repository as a scheduled Action. On its schedule it
searches for repos impersonating yours — name collisions, vanity-org clones,
near-verbatim README copies — and scores each against the signals that separate a
malicious clone from a legitimate fork or mirror: copied docs sitting over a
stripped-out source tree, a binary release you don't ship, and binary metadata
that names a different product. On a high-confidence match it opens an issue in
your repo containing a paste-ready report you can submit to GitHub.

You decide whether to file it. The tool never accuses on its own.

### Detection signals

Weighted toward durable, structural tells:

- README/description copied but the **source tree is stripped** (strongest combined tell)
- owner is not the legitimate maintainer
- repo is **not a fork** (attackers avoid forking to dodge attribution)
- created recently relative to the real project
- ships a **binary release** the real project doesn't
- **PE version-resource mismatch** — the release binary's embedded ProductName /
  CompanyName name an unrelated product
- platform/path inconsistency (e.g. a "macOS tool" whose only artifact is a
  Windows installer)

## What it isn't (please read)

- **Opt-in, by design.** It only protects projects whose maintainers add it —
  which means it structurally under-serves the maintainers most often targeted:
  new, low-visibility projects whose owners are least likely to run security
  tooling. This is a known gap, not an oversight. Closing it would mean a hosted
  "watch any project" service, deliberately out of scope for now.
- **It proposes; it never accuses.** Conservative multi-signal thresholds,
  confidence tiers, an allowlist for known-good copies, and a human in the loop
  before anything is filed. A false "malicious clone" label against a legitimate
  fork is itself a harm, and the tool is built to avoid it.
- **It never executes anything.** Suspected binaries are only ever parsed for
  metadata (via `pefile`), never run.
- **Detection heuristics have a half-life.** Attackers adapt — regenerating
  READMEs, cycling commits to dodge automated checks. The tool leans on the more
  durable structural signals for that reason, but no static ruleset stays ahead
  forever. This is a mitigation, not a cure.

## Status

Alpha. Not yet published or listed on the GitHub Marketplace.

## Usage

Add a workflow to your repository, e.g. `.github/workflows/impersonation-scan.yml`:

```yaml
name: Impersonation scan
on:
  schedule:
    - cron: "0 7 * * *"   # daily at 07:00 UTC
  workflow_dispatch: {}     # allow manual runs

permissions:
  contents: read
  issues: write             # required to open the report issue

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: markhinderliter/repo-impersonation-monitor@v0.1.0
        with:
          project-name: MyProject
          # project-repo defaults to this repository
          # allowlist: known-good copies that must never be reported
          allowlist: |
            trusted-mirror/MyProject
            community/MyProject-i18n
```

> **Pinning:** `@v0.1.0` is the current immutable alpha release — pin it explicitly.
> A moving major alias (e.g. `@v1`, always pointing at the latest compatible
> release) will be introduced with a stable line later; there is no `@v1` yet.

The default `GITHUB_TOKEN` is sufficient (repo search + issue creation). A PAT is
only needed if you later need higher rate limits.

### Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `project-name` | yes | — | The name an impersonator would copy. |
| `project-repo` | no | current repo | `owner/name` of the real project. |
| `real-url` | no | derived | Canonical URL for the report. |
| `report-repo` | no | `project-repo` | Where issues are opened. |
| `allowlist` | no | empty | Newline/comma-separated known-good `owner/name`. |
| `min-tier` | no | `HIGH` | Minimum tier that opens an issue (`HIGH`/`MEDIUM`/`LOW`). |
| `max-candidates` | no | `100` | Per-run cap (rate-limit guard). |
| `dry-run` | no | `false` | Log findings but open no issues. |
| `github-token` | no | workflow token | Token for reads + issue creation. |

**Try it safely first:** set `dry-run: true` to see what *would* be reported
before granting `issues: write` or filing anything.

## Confidence tiers (conservative by design)

A false "malicious clone" label against a legitimate fork, mirror, or translation
is a real harm, so the gate is deliberately strict:

- **HIGH** — score ≥ 0.60, **≥ 3 strong signals**, and at least one *core* signal
  (stripped source **or** PE-resource mismatch). Only HIGH opens an issue by default.
- **MEDIUM** — score ≥ 0.40 with ≥ 2 strong signals.
- **LOW** — score ≥ 0.20.

No single signal can reach HIGH. If a legitimate copy is ever flagged, add it to
`allowlist`.

## Scope (v1)

In scope: per-project queries for maintainers who opt in (keeps within GitHub API
rate limits).

Not in scope (documented, not built): global crawl of GitHub; a hosted
"watch any project" service; VirusTotal lookups; broad name-permutation
generation; archive-wrapped binary extraction (e.g. a PE inside a `.7z` — the
other structural signals still catch these).

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest --cov=repo_impersonation_monitor --cov-report=term-missing
ruff check src tests
```

PE test fixtures are generated, not checked in by hand — see
[tests/fixtures/_generate_pe_fixtures.py](tests/fixtures/_generate_pe_fixtures.py).

## License

MIT
