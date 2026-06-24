# repo-impersonation-monitor

An **opt-in GitHub Action** you add to your own repository to detect other repos
**impersonating your project** — the "repo confusion" attack. It runs on a
schedule, and when it finds a likely impersonator it opens an issue in your repo
containing a **paste-ready abuse report** you can submit to GitHub.

The detection is the means; the deliverable is the **evidence-ready report**. A
maintainer-filed impersonation/trademark takedown is the fastest path to removal,
so this tool is built to arm the rights holder — **it proposes, you decide.**
Nothing is ever filed automatically.

## Why this exists

A malicious clone copied a legitimate project's README and description, stripped
out the real source directories, and shipped a single Windows `.exe` whose
embedded version resources self-identified as an unrelated "licensed" product. It
rode traffic from a YouTube feature of the real project. That
artifact-metadata mismatch is the strongest single tell and is under-exploited by
existing tooling — so it is a first-class signal here.

## What it does

1. **Candidate generation** — finds repos that might be impersonating you:
   - the same name under a different owner, and
   - the vanity-org trick (`Name/Name`, name reused as both org and repo).
2. **Scoring** — scores each candidate against structural signals and outputs a
   confidence **tier** (never a hard yes/no).
3. **Evidence + action** — for high-confidence hits, generates a report and opens
   an issue. **You** pull the trigger on any actual abuse filing.

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

> **The binary is never executed.** Its bytes are parsed for version resources
> (via `pefile`) and nothing more. Reading metadata does not run anything.

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
      - uses: <owner>/repo-impersonation-monitor@v1
        with:
          project-name: MyProject
          # project-repo defaults to this repository
          # allowlist: known-good copies that must never be reported
          allowlist: |
            trusted-mirror/MyProject
            community/MyProject-i18n
```

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
other structural signals still catch these). A known limitation: opt-in
structurally under-serves the new, low-popularity maintainers who are the most
common targets.

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
