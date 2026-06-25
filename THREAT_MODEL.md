# Threat Model — Repo Impersonation Monitor

This document defines what "secure enough" means for this project. It is the
checklist the maintainer verifies against. "Security considerations satisfied"
means: every threat below has a stated mitigation, and every mitigation has an
automated check that is green and gating in CI.

It is a living document. Revisit it whenever a new input source, dependency, or
token permission is added, and at minimum before every release.

---

## 1. What this system is (for threat-modeling purposes)

A composite GitHub Action that an adopting maintainer adds to **their own**
repository. On a schedule it:

1. Queries the GitHub API for repositories that may be impersonating the
   adopter's project.
2. Scores each candidate, which includes **parsing release binaries' embedded
   metadata** (e.g. PE version resources) — **without ever executing them**.
3. On a high-confidence match, opens an issue in the adopter's repo containing a
   paste-ready abuse report.

It runs on GitHub-hosted runners, inside the adopter's CI, using the workflow's
`GITHUB_TOKEN`.

## 2. Assets to protect (in priority order)

| # | Asset | Why it matters |
|---|-------|----------------|
| A1 | The adopter's `GITHUB_TOKEN` and any secrets in their CI environment | Most valuable target. Exfiltrating it compromises the adopter, not just this tool. |
| A2 | Integrity of the adopter's repository | The action can open issues; over-broad permissions could let it do more. |
| A3 | Integrity of the verdict and report | A false "malicious clone" label against a legitimate fork/mirror is a real harm to a third party. |
| A4 | The runner / supply chain | A poisoned dependency or executed payload turns this into the very thing it hunts. |

## 3. Trust boundaries

1. **Adopter workflow → this action.** They grant a token and permissions. We must
   need as little as possible.
2. **This action → GitHub API.** Responses are *data*, not instructions. Treat as
   untrusted.
3. **This action → a suspect repo's artifacts.** The hard boundary. **Everything
   across it is hostile** — repo names, README text, and especially binaries.
4. **This action → its own dependencies (PyPI).** Supply-chain boundary.
5. **This action → GitHub's runner sandbox.** We *trust* GitHub's isolation; we do
   not attempt to defend a compromised runner (see Out of Scope).

---

## 4. Threats, mitigations, and how each is verified

### T1 — Shell / template injection in the workflow
Untrusted GitHub context (e.g. a candidate repo name) interpolated into a `run:`
block could execute attacker-controlled shell, exfiltrating A1.
- **Mitigation:** Never interpolate untrusted `${{ ... }}` values into `run:`.
  Pass them through `env:` and reference as shell variables. No untrusted data in
  inline scripts.
- **Verified by:** `zizmor` (template-injection audit) in CI + pre-commit.

### T2 — Over-broad token permissions
A workflow granted more than it needs widens the blast radius if T1 or T4 occurs.
- **Mitigation:** Action documents the *minimum* permissions (`contents: read`,
  `issues: write`); example workflow grants exactly those and no more.
- **Verified by:** `zizmor` (excessive-permissions audit); OpenSSF Scorecard
  (`Token-Permissions`).

### T3 — Compromised / swapped third-party action (mutable tags)
A `uses:` pinned to a tag can be silently repointed at malicious code (the
tj-actions and trivy-action incidents).
- **Mitigation:** Pin every third-party `uses:` to a full commit SHA with a
  version comment. Keep pins fresh via Dependabot rather than letting them rot.
- **Verified by:** `zizmor` (unpinned-uses / impostor-commit audits); Scorecard
  (`Pinned-Dependencies`); Dependabot (`github-actions` ecosystem).

### T4 — Malicious dependency in our own supply chain
A poisoned or vulnerable PyPI dependency runs with full access to A1/A4.
- **Mitigation:** Keep dependencies minimal; pin/lock them; audit for known CVEs;
  auto-update via Dependabot.
- **Verified by:** `pip-audit` in CI (fails on known vulnerabilities); Dependabot
  (`pip` ecosystem); Scorecard (`Vulnerabilities`, `Dependency-Update-Tool`).

### T5 — Hostile binary crashes or exploits the parser
A malformed/oversized binary from a suspect release could crash the run or, worst
case, exploit the parsing library.
- **Mitigation:** **Never execute** suspect binaries — parse bytes only. Enforce a
  size cap and a parse timeout. Wrap parsing so malformed input *fails safe*
  (logged, scored as "unparseable," run continues). Keep the parser library
  current. **Fuzz the parser** with truncated/garbage inputs and assert graceful
  degradation.
- **Verified by:** Unit + fuzz tests in the suite (the primary control here — no
  off-the-shelf tool owns this surface); `bandit`/CodeQL for unsafe patterns;
  Dependabot/`pip-audit` for the parser library's CVEs.

### T6 — Injection via report output
Untrusted strings (suspect repo name, README excerpts) echoed raw into an issue
body could carry markdown/HTML injection, unwanted @-mentions, or misleading
auto-links.
- **Mitigation:** Sanitize and truncate any untrusted string before it enters the
  issue body. Neutralize `@mentions` and bare URLs; prefer code-fenced, escaped
  rendering of attacker-controlled text.
- **Verified by:** Unit tests asserting sanitization of adversarial inputs.

### T7 — Unsafe patterns in our own code
`subprocess` with `shell=True`, unsafe deserialization, etc., that turn untrusted
input into code execution.
- **Mitigation:** Avoid the patterns; review them when unavoidable.
- **Verified by:** `bandit` (or CodeQL) SAST in CI, gating.

### T8 — False accusation (integrity of the verdict, A3)
The tool labels a legitimate fork/mirror/translation as a malicious clone.
- **Mitigation:** Propose, never auto-accuse. Conservative multi-signal
  thresholds, confidence tiers, a maintainer allowlist, and a human in the loop
  before any abuse report is filed. The self-scan runs report-only.
- **Verified by:** Unit tests on scoring thresholds and allowlist handling; design
  review (not fully automatable — this is a product-safety control).

### T9 — Secret leakage in logs
The token or other secrets printed to run logs (visible per the workflow's log
settings).
- **Mitigation:** Never log the token or secret values. Keep findings free of
  credential material.
- **Verified by:** `zizmor` (credential-related audits); code review.

---

## 5. Out of scope / trust assumptions

- We trust GitHub's runner isolation and the integrity of the GitHub API and
  platform. We do not defend a fully compromised runner or platform.
- We do not defend against a compromised *adopter* account or repository.
- **Detection completeness is not a security property.** Missed impersonators
  (false negatives) are a product limitation documented in the README, not a
  vulnerability.

## 6. Accepted residual risk

- **Adoption skew:** opt-in design under-serves the most-targeted (new, low-profile)
  maintainers. Product limitation; documented in the README.
- **Heuristic half-life:** attackers adapt; static signals decay. Mitigated by
  weighting durable structural signals, not eliminated.
- **Parser surface:** fail-safe handling and fuzzing reduce but do not remove the
  risk inherent in parsing hostile binaries.
- **Scorecard is hygiene, not proof:** a high score reflects good practice; it does
  not guarantee the absence of vulnerabilities.
- **Forks are excluded from discovery by GitHub's default, not by us.** `GET
  /search/repositories` does not return forks unless a query adds `fork:true` /
  `fork:only` ("By default, forks are not shown in search results" —
  [GitHub docs](https://docs.github.com/en/search-github/searching-on-github/searching-in-forks)).
  A live discovery run confirmed this on our exact endpoint: 0 forks across 124
  surfaced repos, including a project with ~17.2k forks. Two consequences we accept
  for MVP:
  - *Weaponized fork (accepted gap):* an attacker who forks, strips source, and
    ships a malicious release is **already invisible** to our exact-name search,
    because forks are pre-excluded server-side. This gap is not introduced by any
    query choice of ours — it is GitHub's default. Rare in practice (the "forked
    from" lineage is itself a deterrent and an attribution trail); not built for in
    v1. A future path could opt into `fork:true` *specifically* to score forks.
  - *Signal note:* because the primary search is fork-free, `not_a_fork` fires on
    nearly every primary candidate (near-constant +0.15) — it stops discriminating
    on that path, though it stays correct and free (`is_fork` is in the search
    payload, no extra request) and still matters on the bare-org path. Kept, not
    removed.
- **Permutation (near-miss name) coverage is bounded by enumerated classes.**
  Permutation discovery closes the named exact-name gap — the confirmed
  `bytedance/deer-flow` → `bytedance-deer-flow` org-fold is now reached by a
  targeted, recency-sorted variant query — but its reach is finite:
  - *Unicode-homoglyph variants are N/A, not a gap.* GitHub repo names and owner
    logins are ASCII (owner logins "can only contain alphanumeric characters and
    dashes"), so a Cyrillic-`а`-for-Latin-`a` clone cannot exist as an identifier.
    We deliberately do not generate that dnstwist class.
  - *Affix/typo reach is bounded.* We enumerate separator swaps, owner+name folds,
    and a curated affix list; arbitrary unanticipated affixes and single-edit typos
    are out of scope for MVP (combinatorial query cost against the ~30/min search
    bucket, low yield). A near-miss outside the enumerated set is a documented miss.
- **Discovery is capped, and a candidate past the cap is unscored.** Both halves
  are bounded: exact-name results by `max-candidates` (default 30) and permutation
  variants by `max-variants` (default 20, org-folds retained first). On a popular
  name a matching repo beyond the cap is never scored — 4 of 5 seeds hit the
  candidate cap in a live run. Mitigated, not eliminated; `fork:false` is
  irrelevant here, as forks are never in the set to begin with. It is **low-priority
  by measurement, not assumption**: a count audit found that distinctive names have
  shallow exact-name universes (a few hundred repos), so the unscored tail is small
  exactly where same-name impersonation is plausible — and only vast for generic
  dictionary words (e.g. `skills`, hundreds of thousands) where same-name
  impersonation is implausible and scores low anyway. The measured fix, if ever
  needed, is **recency ordering over a larger cap**: variant queries already use
  `sort=updated` so a fresh impostor surfaces first, while the exact-name path
  deliberately keeps best-match ordering unchanged.

---

## 7. Verification matrix (the "are we satisfied?" dashboard)

| Control | Tool / check | Where it runs | Gating? |
|---------|--------------|---------------|---------|
| Workflow security (T1, T2, T3, T9) | `zizmor` | CI + pre-commit | Yes |
| Dependency CVEs (T4) | `pip-audit` | CI | Yes |
| Dependency & action updates (T3, T4) | Dependabot (`pip`, `github-actions`) | Scheduled PRs | n/a |
| Code SAST (T5, T7) | `bandit` (or CodeQL) | CI | Yes |
| Parser robustness (T5) | unit + fuzz tests | CI | Yes |
| Output sanitization (T6) | unit tests | CI | Yes |
| Verdict integrity (T8) | threshold/allowlist tests + review | CI + review | partial |
| Overall posture | OpenSSF Scorecard (+ README badge) | scheduled | dashboard |
| Continuous enforcement | branch protection w/ required checks | repo settings | Yes |
| Inbound reports | `SECURITY.md` + private vulnerability reporting | repo | n/a |

**"Considerations satisfied"** = every "Yes" row green on `main`, Dependabot and
Scorecard enabled, and Sections 5–6 reviewed and still accurate.
