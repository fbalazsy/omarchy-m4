# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA (structure and block decoder from inspect_strip.py @ b92f048)
# J616 variant: reads the AID35 speaker strip (all 17 effects), writes each effect's aupreset
# exactly as stored (plistlib binary), a summary, and decodes the parameter blocks of the
# tmst / vrq2 effects. Never edits a preset value.
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import sys


def decode_blocks(data):
    blocks = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("truncated block header")
        scope, element, count = struct.unpack_from(">III", data, offset)
        offset += 12
        if count > (len(data) - offset) // 8:
            raise ValueError("truncated parameter records")
        params = []
        for _ in range(count):
            ident, value = struct.unpack_from(">If", data, offset)
            params.append({"id": ident, "value": value, "bytes": data[offset:offset+8].hex()})
            offset += 8
        if len({p["id"] for p in params}) != count:
            raise ValueError("duplicate parameter ID within block")
        blocks.append({"scope": scope, "element": element, "params": params})
    return blocks


def fourcc(n):
    return n.to_bytes(4, "big").decode()


def main(source, target):
    source, target = Path(source), Path(target)
    target.mkdir()
    data = plistlib.loads((source / "builtin_speaker_out_general_3.austrip").read_bytes())
    report = {"strip_keys": list(data), "effects": []}
    for strip in data["strips"]:
        for effect in strip["effects"]:
            preset = effect["aupreset"]
            name = effect["displayname"]
            blob = plistlib.dumps(preset, fmt=plistlib.FMT_BINARY)
            (target / (name + ".aupreset")).write_bytes(blob)
            row = {"name": name, "bypass": effect["bypass"], "unit": effect["unit"],
                   "preset_keys": list(preset), "preset_name": preset.get("name"),
                   "type": fourcc(preset["type"]), "subtype": fourcc(preset["subtype"]),
                   "manufacturer": fourcc(preset["manufacturer"]),
                   "data_bytes": len(preset.get("data", b"")),
                   "data_sha256": hashlib.sha256(preset.get("data", b"")).hexdigest(),
                   "aupreset_sha256": hashlib.sha256(blob).hexdigest()}
            if row["subtype"] in {"tmst", "vrq2"}:
                blocks = decode_blocks(preset["data"])
                row["blocks"] = [{"scope": b["scope"], "element": b["element"], "count": len(b["params"])} for b in blocks]
                (target / (name + ".json")).write_text(json.dumps(blocks, indent=2) + "\n")
            report["effects"].append(row)
    (target / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1], sys.argv[2]), indent=2))
