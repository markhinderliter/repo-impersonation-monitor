"""Regenerate the committed PE regression fixtures.

The PE-synthesis logic lives in seeded/pe_builder.py (single source of truth);
this script just writes the two committed fixtures from the canonical field sets.
A byte-identical stability test (tests/test_pe_fixture_stability.py) guards that
the builder still reproduces these exact bytes.

    python tests/fixtures/_generate_pe_fixtures.py
"""

import pathlib

from seeded.pe_builder import MATCH_FIELDS, MISMATCH_FIELDS, build_pe

_FIXTURES = {
    "pe_mismatch.exe": MISMATCH_FIELDS,
    "pe_match.exe": MATCH_FIELDS,
}


def main():
    here = pathlib.Path(__file__).parent
    for name, fields in _FIXTURES.items():
        data = build_pe(fields)
        (here / name).write_bytes(data)
        print(f"wrote {len(data)} bytes to {name}")


if __name__ == "__main__":
    main()
