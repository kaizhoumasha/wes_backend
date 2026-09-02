from __future__ import annotations

from src.utils.canonical_json import canonical_json_bytes, canonical_json_digest


def test_canonical_json_bytes_are_compact_ordered_utf8() -> None:
    encoded = canonical_json_bytes({"z": "中文", "a": [2, 1]})

    assert encoded == b'{"a":[2,1],"z":"\xe4\xb8\xad\xe6\x96\x87"}'


def test_canonical_json_digest_hashes_exact_canonical_bytes() -> None:
    value = {"z": "中文", "a": [2, 1]}

    assert canonical_json_digest(value) == "3e7a65edc1984d9843472919e3a271bcf6d0c80941faa3721ca537bacc11bf7d"
