"""Detect repos impersonating your project and draft a paste-ready abuse report.

See CLAUDE.md for the project spec. The package is built in phases:

- Phase 1 (this layer): ``models``, ``config``, ``scoring``, ``report`` — pure,
  dependency-free logic. Produces the deliverable (the report) and the
  conservative confidence-tier gate without any network access.
- Phase 2+: ``github_io`` (only network module), ``candidates``, ``signals``,
  ``pe`` (byte-only PE version-resource read), and ``main`` (orchestration).
"""

__version__ = "0.1.0"
