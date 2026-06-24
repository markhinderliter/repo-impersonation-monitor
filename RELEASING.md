# Releasing & publishing

This project is a **GitHub Action**: there is no server and nothing to host.
"Deployment" is just publishing a tagged release to a public repository.
Adopters reference a tag (e.g. `@v1`); GitHub runs the action on their runners,
on their schedule.

## Versioning model

Two kinds of tags, by convention:

- **Immutable release tags** — `v1.0.0`, `v1.1.0`, ... never moved once pushed.
- **A moving major tag** — `v1` — always re-pointed at the latest compatible
  release. Adopters pin `@v1` and get bug fixes without breaking changes.

Bump the major (`v2`) only for a breaking change to the **public contract**:
the `action.yml` inputs and the issue/report behavior adopters depend on.

Renaming or removing an `action.yml` **output** is also a breaking change
requiring a major bump, the same as an input — downstream workflow steps may
consume outputs.

Keep `version` in `pyproject.toml` in sync with the release tag.

## Cutting a release

```bash
# 1. Ensure the suite is green and lint is clean.
.venv/bin/python -m pytest
.venv/bin/ruff check src tests

# 2. Bump version in pyproject.toml (e.g. 1.0.0), commit.
git commit -am "chore: release v1.0.0"

# 3. Tag the immutable release.
git tag -a v1.0.0 -m "v1.0.0"

# 4. Move the major tag to this release.
git tag -f v1 v1.0.0

# 5. Push commits and tags (force only the moving major tag).
git push origin main
git push origin v1.0.0
git push origin -f v1
```

Then draft a GitHub Release from `v1.0.0` with notes. On the release form you
can optionally tick **"Publish this Action to the GitHub Marketplace."**

## Marketplace / publish checklist

- [ ] Repository is **public**.
- [ ] `LICENSE` present (Marketplace requires one). — MIT, included.
- [ ] `action.yml` at the **repo root** with a unique `name` and a `description`.
      Marketplace names are globally unique; confirm `"Repo Impersonation Monitor"`
      (or chosen name) is available, and rename in `action.yml` if taken.
- [ ] `branding` set in `action.yml` (icon + color) — shows on the listing.
- [ ] `README.md` explains usage, inputs, and required permissions.
- [ ] An immutable `vX.Y.Z` tag and a moving `vX` tag exist.
- [ ] Repository **topics** added for discovery, e.g. `github-actions`,
      `supply-chain-security`, `security`, `typosquatting`, `impersonation`.
- [ ] (Optional) Announce in OSS-security community channels.

## Discovery, realistically

For a niche security tool, the README and word-of-mouth in security circles do
as much as the Marketplace. This is also where the known adoption-skew
limitation bites: discovery still depends on a maintainer going looking, which
under-serves the new, low-popularity maintainers who are the most common targets.
