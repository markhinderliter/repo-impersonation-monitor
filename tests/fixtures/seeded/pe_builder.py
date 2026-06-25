"""Single source of truth for synthesizing PE32 bytes with version resources.

Produces bytes pefile can parse into pe.FileInfo with a StringFileInfo whose
StringTable holds ProductName/CompanyName/OriginalFilename/FileDescription.
These bytes are only ever *parsed* by the scorer, never executed.

Used both by the seeded detection harness (synthesizes into temp dirs at test
time) and by _generate_pe_fixtures.py (writes the committed regression fixtures).
A byte-identical stability test guards that this module reproduces the committed
fixtures exactly, so the extraction can never silently shift the synthesized
bytes.
"""

import struct

# Canonical field sets that produced the committed regression fixtures. Order
# matters (build_pe emits string entries in dict order), so do not reorder.
MISMATCH_FIELDS = {
    "ProductName": "Janus Key",
    "CompanyName": "Duality Solutions",
    "OriginalFilename": "JanusKey-Setup.exe",
    "FileDescription": "Janus Key Licensed Installer",
    "FileVersion": "1.0.0.0",
}
MATCH_FIELDS = {
    "ProductName": "MyProject",
    "CompanyName": "MyProject Authors",
    "OriginalFilename": "MyProject.exe",
    "FileDescription": "MyProject CLI",
    "FileVersion": "2.1.0.0",
}


def align(n, a):
    return (n + a - 1) // a * a


def utf16(s):
    return s.encode("utf-16-le") + b"\x00\x00"  # null-terminated WCHAR


def pad4(b):
    return b + b"\x00" * ((4 - len(b) % 4) % 4)


def string_entry(key, value):
    # String: wLength, wValueLength(words incl null), wType=1, szKey, pad, value, pad
    val = utf16(value)
    key_b = utf16(key)
    head = struct.pack("<HHH", 0, len(val) // 2, 1) + key_b
    head = pad4(head)
    blob = head + val
    blob = pad4(blob)
    # set wLength
    blob = struct.pack("<H", len(blob)) + blob[2:]
    return blob


def string_table(lang_key, entries):
    children = b"".join(entries)
    key_b = utf16(lang_key)
    head = struct.pack("<HHH", 0, 0, 1) + key_b
    head = pad4(head)
    blob = head + children
    blob = struct.pack("<H", len(blob)) + blob[2:]
    return blob


def string_file_info(tables):
    children = b"".join(tables)
    key_b = utf16("StringFileInfo")
    head = struct.pack("<HHH", 0, 0, 1) + key_b
    head = pad4(head)
    blob = head + children
    blob = struct.pack("<H", len(blob)) + blob[2:]
    return blob


def var_file_info():
    # Var "Translation" with one langid/codepage (0x0409, 0x04b0)
    val = struct.pack("<HH", 0x0409, 0x04B0)
    key_b = utf16("Translation")
    head = struct.pack("<HHH", 0, len(val), 0) + key_b
    head = pad4(head)
    var = head + val
    var = pad4(var)
    var = struct.pack("<H", len(var)) + var[2:]

    key_b = utf16("VarFileInfo")
    head = struct.pack("<HHH", 0, 0, 1) + key_b
    head = pad4(head)
    blob = head + var
    blob = struct.pack("<H", len(blob)) + blob[2:]
    return blob


def fixed_file_info():
    # VS_FIXEDFILEINFO: 13 DWORDs, signature 0xFEEF04BD
    return struct.pack(
        "<13L",
        0xFEEF04BD, 0x00010000, 0x00010000, 0x00000000,
        0x00010000, 0x00000000, 0x3F, 0x00, 0x40004, 0x1, 0x0, 0x0, 0x0,
    )


def vs_versioninfo(fields, lang_key="040904b0"):
    ffi = fixed_file_info()
    entries = [string_entry(k, v) for k, v in fields.items()]
    st = string_table(lang_key, entries)
    sfi = string_file_info([st])
    vfi = var_file_info()
    children = sfi + vfi
    key_b = utf16("VS_VERSION_INFO")
    head = struct.pack("<HHH", 0, len(ffi), 0) + key_b
    head = pad4(head)
    blob = head + ffi + children
    blob = struct.pack("<H", len(blob)) + blob[2:]
    return blob


def build_rsrc(version_blob, rsrc_rva):
    """Build a .rsrc section: 3-level dir (Type RT_VERSION=16 -> ID 1 -> Lang 0x409)."""
    IRD = struct.Struct("<LLHHHH")   # Characteristics,TimeDate,Maj,Min,NumNamed,NumId
    IRDE = struct.Struct("<LL")      # Name/Id, OffsetToData (+high bit for subdir)
    IRDATA = struct.Struct("<LLLL")  # OffsetToData(RVA),Size,CodePage,Reserved

    # layout: dir(type) + entry -> dir(id) + entry -> dir(lang) + entry -> data entry -> data
    dir_size = IRD.size + IRDE.size
    off_type = 0
    off_id = off_type + dir_size
    off_lang = off_id + dir_size
    off_dataentry = off_lang + dir_size
    off_data = off_dataentry + IRDATA.size
    off_data = align(off_data, 4)

    out = bytearray()
    # Type dir
    out += IRD.pack(0, 0, 0, 0, 0, 1)
    out += IRDE.pack(16, 0x80000000 | off_id)   # RT_VERSION, subdir
    # ID dir
    out += IRD.pack(0, 0, 0, 0, 0, 1)
    out += IRDE.pack(1, 0x80000000 | off_lang)  # ID 1, subdir
    # Lang dir
    out += IRD.pack(0, 0, 0, 0, 0, 1)
    out += IRDE.pack(0x409, off_dataentry)      # lang, data entry (no high bit)
    # Data entry
    out += IRDATA.pack(rsrc_rva + off_data, len(version_blob), 0, 0)
    # pad to off_data
    out += b"\x00" * (off_data - len(out))
    out += version_blob
    return bytes(out)


def build_pe(fields):
    """Build a minimal, valid PE32 carrying a version resource with ``fields``."""
    SECT_ALIGN = 0x1000
    FILE_ALIGN = 0x200

    # Headers
    dos = bytearray(b"MZ" + b"\x00" * 58 + struct.pack("<L", 0x40))  # e_lfanew=0x40
    dos = dos[:0x40]

    # We'll compute sizes after building rsrc; rsrc RVA at 0x1000.
    rsrc_rva = 0x1000
    version_blob = vs_versioninfo(fields)
    rsrc = build_rsrc(version_blob, rsrc_rva)
    rsrc_raw_size = align(len(rsrc), FILE_ALIGN)

    num_sections = 1
    size_of_opt = 0xE0
    coff = struct.pack(
        "<HHLLLHH",
        0x014C,          # Machine i386
        num_sections,
        0,               # TimeDateStamp
        0, 0,            # symbols
        size_of_opt,
        0x2102,          # Characteristics: EXECUTABLE | 32BIT
    )

    headers_size = align(0x40 + 4 + len(coff) + size_of_opt + 40 * num_sections, FILE_ALIGN)
    size_of_image = align(rsrc_rva + len(rsrc), SECT_ALIGN)

    # Optional header (PE32)
    opt = struct.pack(
        "<HBBLLLLLL",
        0x10B,           # Magic PE32
        0, 0,            # linker ver
        0, 0, 0,         # sizes of code/init/uninit
        0x1000,          # AddressOfEntryPoint
        0x1000,          # BaseOfCode
        0x1000,          # BaseOfData
    )
    opt += struct.pack("<LLL", 0x400000, SECT_ALIGN, FILE_ALIGN)  # ImageBase, aligns
    opt += struct.pack("<HHHHHH", 4, 0, 0, 0, 4, 0)  # OS/img/subsys versions
    opt += struct.pack("<L", 0)                       # Win32VersionValue
    opt += struct.pack("<LL", size_of_image, headers_size)
    opt += struct.pack("<L", 0)                       # CheckSum
    opt += struct.pack("<HH", 3, 0)                   # Subsystem=CONSOLE, DllChars
    opt += struct.pack("<LLLL", 0x100000, 0x1000, 0x100000, 0x1000)  # stack/heap
    opt += struct.pack("<LL", 0, 16)                  # LoaderFlags, NumberOfRvaAndSizes
    data_dirs = [(0, 0)] * 16
    data_dirs[2] = (rsrc_rva, len(rsrc))              # Resource directory
    for rva, size in data_dirs:
        opt += struct.pack("<LL", rva, size)
    assert len(opt) == size_of_opt, (len(opt), size_of_opt)  # noqa: S101

    # Section header for .rsrc
    sect = struct.pack(
        "<8sLLLLLLHHL",
        b".rsrc",
        len(rsrc),       # VirtualSize
        rsrc_rva,        # VirtualAddress
        rsrc_raw_size,   # SizeOfRawData
        headers_size,    # PointerToRawData
        0, 0, 0, 0,
        0x40000040,      # INITIALIZED_DATA | READ
    )

    out = bytearray()
    out += dos
    out += b"PE\x00\x00"
    out += coff
    out += opt
    out += sect
    out += b"\x00" * (headers_size - len(out))
    out += rsrc
    out += b"\x00" * (rsrc_raw_size - len(rsrc))
    return bytes(out)
