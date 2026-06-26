---
title: "Repo Impersonation Monitor — watch for repos impersonating your project"
description: >-
  A GitHub Action that watches for repositories impersonating your project
  (the "repo confusion" attack) and hands you an evidence-ready, paste-ready
  abuse report when it finds one. It proposes; a human files.
permalink: /
---

<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  "name": "Repo Impersonation Monitor",
  "applicationCategory": "DeveloperApplication",
  "operatingSystem": "GitHub Actions",
  "description": "A GitHub Action that detects repositories impersonating your project (the repo-confusion attack) and produces a paste-ready abuse report. It proposes; a human files.",
  "url": "https://github.com/marketplace/actions/repo-impersonation-monitor",
  "offers": { "@type": "Offer", "price": "0", "priceCurrency": "USD" },
  "license": "https://opensource.org/licenses/MIT"
}
</script>

# Repo Impersonation Monitor

<p class="lead">A GitHub Action that watches for repositories impersonating your
project and hands you an evidence-ready abuse report when it finds one.</p>

This attack has a name — **repo confusion** — and it's a documented, growing
pattern. The defenses that exist mostly guard package registries like npm and
PyPI, where the risk is install-time code execution. They don't help here:
this attack targets a **human searching GitHub and picking the wrong result**.
The fastest way these clones come down is a takedown filed by the real
project's owner — and this tool puts that lever in the owner's hand.

<div class="btnrow">
  <a class="btn primary" href="https://github.com/marketplace/actions/repo-impersonation-monitor">View on GitHub Marketplace</a>
  <a class="btn ghost" href="https://github.com/markhinderliter/repo-impersonation-monitor">Source on GitHub</a>
</div>

> **Alpha.** Conservative by design, and honest about its limits — see
> [What it isn't](#what-it-isnt-please-read) below.

## Why this exists

While installing an open-source tool I'd seen featured in a video, I searched
GitHub for it by name and landed on the wrong repository — a clone, not the
real project. It looked right: the README and description were copied almost
verbatim. But the real source directories had been stripped out, and the only
artifact it shipped was a single Windows executable. Reading that binary's
embedded version metadata — **without ever running it** — showed it identifying
itself as an entirely unrelated "licensed" product. A malware dropper wearing
the project's name, riding the traffic the video sent.

I caught it. The next person searching that name might not.

## What it does

You add it to your own repository as a scheduled Action. On its schedule it
searches for repos impersonating yours — name collisions, vanity-org clones,
near-verbatim README copies — and scores each against the signals that separate
a malicious clone from a legitimate fork or mirror: copied docs sitting over a
**stripped-out source tree**, a **binary release you don't ship**, and **binary
metadata that names a different product**. On a high-confidence match it opens
an issue in your repo containing a **paste-ready report** you can submit to
GitHub.

You decide whether to file it. **The tool never accuses on its own.**

<!-- INFOGRAPHIC PLACEHOLDER: discovery → scoring → paste-ready report.
     Held until the infographic's typos are fixed; do not embed yet. -->

## Install

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
```

`@v0.1.0` is the current immutable alpha release — pin it explicitly (there is
no `@v1` yet; a moving major alias arrives with a stable line later).

Use `allowlist:` to mark known-good forks/mirrors that must never be reported
(see the [README](https://github.com/markhinderliter/repo-impersonation-monitor#usage)
for the full example).

**Try it safely first:** set `dry-run: true` to see what *would* be reported
before granting `issues: write` or filing anything.

## What it isn't (please read)

- **Opt-in, by design.** It only protects projects whose maintainers add it —
  which structurally under-serves the maintainers most often targeted: new,
  low-visibility projects whose owners are least likely to run security tooling.
  This is a known gap, not an oversight.
- **It proposes; it never accuses.** Conservative multi-signal thresholds,
  confidence tiers, an allowlist for known-good copies, and a human in the loop
  before anything is filed. A false "malicious clone" label against a legitimate
  fork is itself a harm, and the tool is built to avoid it.
- **It never executes anything.** Suspected binaries are only ever parsed for
  metadata (via `pefile`), never run.
- **Detection heuristics have a half-life.** Attackers adapt — regenerating
  READMEs, cycling commits. The tool leans on durable structural signals for
  that reason, but no static ruleset stays ahead forever. A mitigation, not a cure.

## Learn more

- [GitHub Marketplace listing](https://github.com/marketplace/actions/repo-impersonation-monitor)
- [Source repository](https://github.com/markhinderliter/repo-impersonation-monitor)
- [Threat model](https://github.com/markhinderliter/repo-impersonation-monitor/blob/main/THREAT_MODEL.md)

Free and open source (MIT).
