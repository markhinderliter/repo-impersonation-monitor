"""Guard: the shared PE builder must reproduce the committed fixtures exactly.

Byte-identical regeneration is the entire safety property of extracting the PE
synthesis into seeded/pe_builder.py — if the builder ever shifts the bytes, the
committed regression fixtures (pe_mismatch.exe / pe_match.exe) would silently
start testing something different. This locks that down.
"""

from pathlib import Path

from tests.fixtures.seeded.pe_builder import MATCH_FIELDS, MISMATCH_FIELDS, build_pe

FIXTURES = Path(__file__).parent / "fixtures"


def test_mismatch_fixture_is_byte_identical():
    assert build_pe(MISMATCH_FIELDS) == (FIXTURES / "pe_mismatch.exe").read_bytes()


def test_match_fixture_is_byte_identical():
    assert build_pe(MATCH_FIELDS) == (FIXTURES / "pe_match.exe").read_bytes()
