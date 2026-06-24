"""Read PE version-resource fields from a binary's bytes — never executing it.

This is the project's differentiator signal and the only place untrusted bytes
are parsed, so it is isolated here. The hard contract (enforced by a test):

    This module parses bytes only. It never writes the bytes to an executable
    path, never shells out, never imports a runtime/loader. Reading metadata
    does not run anything.

``read_version_resources`` is total: any parse failure returns
``PeMetadata(parse_ok=False, ...)`` rather than raising, so a malformed or
hostile artifact can never crash the run.
"""

from __future__ import annotations

import pefile

from .models import PeMetadata

# Asset filename extensions that are PE-format binaries we can read directly.
# Archives (.7z/.zip/.tar) and installers (.msi) are NOT PE and are skipped in
# the MVP (archive-wrapped binaries are a documented known gap).
_PE_EXTENSIONS = (".exe", ".dll")

# Version-resource keys we surface explicitly (others land in raw_string_table).
_FIELD_KEYS = {
    "ProductName": "product_name",
    "CompanyName": "company_name",
    "OriginalFilename": "original_filename",
    "FileDescription": "file_description",
}


def is_pe_asset(name: str) -> bool:
    """Whether a release asset filename looks like a directly-readable PE."""
    lowered = name.lower()
    return lowered.endswith(_PE_EXTENSIONS)


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _collect_string_table(pe: pefile.PE) -> dict[str, str]:
    """Flatten all StringFileInfo/StringTable entries into a plain dict."""
    table: dict[str, str] = {}
    file_info = getattr(pe, "FileInfo", None)
    if not file_info:
        return table
    for entry_list in file_info:
        for entry in entry_list:
            string_tables = getattr(entry, "StringTable", None)
            if not string_tables:
                continue
            for string_table in string_tables:
                for key, value in string_table.entries.items():
                    table[_decode(key)] = _decode(value)
    return table


def read_version_resources(data: bytes) -> PeMetadata:
    """Parse PE version resources from ``data``. Never executes the binary."""
    try:
        # data= keeps it in memory; parse only the resource directory.
        pe = pefile.PE(data=data, fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]]
        )
        raw = _collect_string_table(pe)
        pe.close()
    except Exception as exc:  # pefile raises a variety of errors on bad input
        return PeMetadata(parse_ok=False, parse_error=f"{type(exc).__name__}: {exc}")

    fields = {attr: raw.get(key) for key, attr in _FIELD_KEYS.items()}
    return PeMetadata(parse_ok=True, raw_string_table=raw, **fields)
