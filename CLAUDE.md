# CLAUDE.md

Project context for Claude Code. Read this at the start of every session.

## What this project is

An **opt-in GitHub Action** that a maintainer adds to their own repository to
detect other repos *impersonating their project* — the "repo confusion" attack.
It runs on a schedule, and when it finds a likely impersonator it opens an issue
in the maintainer's repo containing a **paste-ready abuse report** the maintainer
can submit to GitHub.

The detection is the means; the deliverable is the evidence-ready report. A
maintainer-filed impersonation/trademark takedown is the fastest path to removal,
so the whole tool is built to arm the rights holder.

## Origin / motivation

This exists because of a real near-miss: a malicious clone (`OpenMontage/OpenMontage`,
a vanity org) copied a legitimate project's README and description, stripped out the
real source directories, and shipped a single Windows `.exe` whose embedded version
resources self-identified as an unrelated "licensed" product ("Janus Key" /
"Duality Solutions"). It rode traffic from a YouTube feature of the real project.
That artifact-metadata mismatch is the strongest single tell we observed and is
under-exploited by existing tooling.

## Scope and non-goals (v1)

In scope:
- Serves maintainers who opt in by adding the Action to their repo.
- Per-project queries only ("is anyone pretending to be *me*?"), which keeps us
  inside GitHub API rate limits.

Explicitly NOT in scope for v1 (note as future work, do not build yet):
- No global crawl of GitHub. No attempt to scan all repos.
- No hosted "watch any project" service / no infrastructure to run. (Desirable
  long-term endgame, but out of budget. The detection core written here is what a
  future hosted version would reuse, so nothing is wasted.)
- Known limitation to document, not solve: opt-in structurally under-serves the
  new, low-popularity maintainers who are the most common targets.

## Pipeline

1. **Candidate generation** — find repos that might be impersonating this project:
   - Same project name under a different owner.
   - The vanity-org trick (bare name reused as both org and repo, e.g. `Name/Name`).
   - dnstwist-style name permutations (hyphen/underscore swaps, appended digits,
     homoglyphs).
   - Text search for distinctive sentences from this project's README (catches
     near-verbatim copies hiding under a different name).
2. **Scoring** — score each candidate against the signals below. Output a
   confidence *tier*, never a hard yes/no.
3. **Evidence + action** — for high-confidence hits, generate the report and open
   an issue. The human pulls the trigger on the actual abuse filing.

## Detection signals (weight structural signals over behavioral ones)

Strong / durable (weight high):
- README/description copied but the real **source tree is stripped** — looks like
  the project on the surface, isn't underneath. Strongest combined tell.
- Owner is not the legitimate maintainer.
- Repo is **not a fork** (attackers avoid forking to dodge attribution; the API
  exposes fork status cleanly).
- Created recently relative to the real project.
- Ships a **binary release** the real project doesn't.
- **PE version-resource mismatch**: the release binary's embedded ProductName /
  CompanyName name an unrelated product. This is the differentiator — keep it in
  even if everything else gets trimmed.
- Platform/path inconsistency (e.g. a "Mac tool" whose only artifact is a Windows
  installer, or a binary stored under a `linux/` path in a Windows project).

Weaker / decaying (weight low — attackers already adapt these away with
LLM-regenerated READMEs and commit cycling):
- README-only commit churn; all commits titled "Update README.md".
- Hourly README re-commits to game GitHub search ranking.

## Hard rules

- **Never execute a downloaded binary.** Only ever parse its bytes (e.g. PE version
  resources via a library like `pefile`). Reading metadata does not run anything.
- **Propose, never auto-accuse.** A false "malicious clone" label against a
  legitimate fork, mirror, or translation is a real harm. Enforce: conservative
  multi-signal thresholds, confidence tiers, a maintainer allowlist for known-good
  copies, and a human in the loop before anything is filed.
- Keep dependencies light. Prefer the standard library + a small number of
  well-known packages.

## Tech choices

- Python.
- GitHub REST + Search API. In an Action, the workflow-provided token covers reads;
  a PAT may be needed for higher rate limits or code search. **Verify current
  GitHub Search API auth and rate-limit behavior when implementing** — it changes.
- PE parsing: a maintained library such as `pefile` for version resources.
- Output: open a GitHub issue via the API with the generated report.

## MVP vs. later

MVP (build first):
- Candidate generation: exact name + bare-org-name search.
- Scoring on the structural signals + a basic PE version-resource read.
- Auto-generated issue containing the paste-ready report (clone URL, real URL,
  observations, an explicit "binary never executed" note, and which abuse-report
  categories to select).

Later (clean follow-ons, don't build yet):
- VirusTotal verdict lookup by file hash.
- Broader name-permutation generation.
- Publish confirmed cases to a shared, OSV-format feed of malicious repos
  (repos are out of scope for OpenSSF's existing package-focused database).
- Hosted "watch any project" mode.

## Suggested structure (let the plan refine this)

```
action.yml                 # GitHub Action definition (cron + inputs)
pyproject.toml             # deps + metadata
src/<pkg>/
  candidates.py            # candidate generation
  signals.py               # individual signal checks (incl. PE metadata read)
  scoring.py               # combine signals -> confidence tier
  report.py                # build the paste-ready abuse report
  github_io.py             # search + open issue
  main.py                  # entry point wired to action inputs
tests/
README.md
```

## Report-writing reminder

The generated report should read as an evidence-led, factual observation, not a
guess. Always state plainly that the binary was inspected without being executed.
