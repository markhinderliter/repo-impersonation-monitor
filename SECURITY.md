# Security Policy

## Reporting a vulnerability

Please report security issues **privately** via GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
— open the repository's **Security** tab and choose **Report a vulnerability**.
Do not open a public issue for a suspected vulnerability.

Please include enough detail to reproduce: affected version/commit, the input or
configuration that triggers it, and the impact you observed. We aim to
acknowledge a report within a few days.

## Supported versions

This project is in **alpha** and not yet published. Until a `v1.0.0` release,
only the latest `main` is supported. Once released, the moving major tag (`v1`)
tracks the latest compatible release — see [RELEASING.md](RELEASING.md).

## Scope

What this project defends, the trust boundaries it assumes, and how each threat
is mitigated and verified are documented in [THREAT_MODEL.md](THREAT_MODEL.md).
Two properties are load-bearing and worth stating here:

- **Suspect binaries are never executed.** They are only ever parsed for
  metadata (e.g. PE version resources). A report that this guarantee can be
  bypassed is a high-priority vulnerability.
- **The tool proposes; it never auto-accuses.** Findings require a human before
  any abuse report is filed.

Out of scope (see THREAT_MODEL.md §5): detection completeness (a missed
impersonator is a product limitation, not a vulnerability), a fully compromised
runner or GitHub platform, and a compromised adopter account.
