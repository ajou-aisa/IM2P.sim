#!/usr/bin/env python3
"""Validate one bounded Flash image; optionally run offline Vivado write_cfgmem.

There is deliberately no hardware executor. See flash_reference.md for F1/F2.
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path


PARTS = {
    "s25fl128sxxxxxx0-spi-x1_x2_x4",
    "s25fl128sxxxxxx1-spi-x1_x2_x4",
    "mt25ql128-spi-x1_x2_x4",
}
CAPACITY = 16 * 1024 * 1024
BIT_REVERSE = bytes(int(f"{x:08b}"[::-1], 2) for x in range(256))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def confirmed(value, field):
    require(isinstance(value, str) and value.strip()
            and value.strip().lower() not in {"unknown", "tbd", "unavailable"},
            f"{field} must be confirmed")
    return value


def integer(value, field, minimum=0):
    require(type(value) is int and minimum <= value <= 0xFFFFFFFF,
            f"{field}: invalid unsigned range")
    return value


def checked_file(spec):
    expected = spec["sha256"]
    require(isinstance(expected, str) and re.fullmatch(r"[0-9a-f]{64}", expected),
            "invalid SHA256")
    path = Path(spec["path"]).resolve(strict=True)
    require(path.is_file(), "expected regular file")
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected, f"SHA256 mismatch: {path}")
    return path, data


def validate_plan(plan):
    require(type(plan["schema_version"]) is int and plan["schema_version"] == 1,
            "unsupported plan schema")
    require(plan["fpga_part"] == "xc7a100tcsg324-1", "wrong FPGA part")
    require(plan["flash_part"] in PARTS, "unconfirmed or unsupported Flash part")
    require(plan["capacity_bytes"] == CAPACITY, "unsupported Flash capacity")
    confirmed(plan["part_evidence"], "part_evidence")
    confirmed(plan["geometry_evidence"], "geometry_evidence")
    confirmed(plan["boot_interface_evidence"], "boot_interface_evidence")
    require(plan["interface"] in {"SPIx1", "SPIx2", "SPIx4"}, "wrong SPI interface")
    require(plan["boot_interface"] == plan["interface"], "boot interface mismatch")

    # Regions describe the confirmed chip state, including any hybrid sectors.
    region_end = 0
    boundaries = {0}
    for region in plan["erase_regions"]:
        offset = integer(region["offset"], "sector offset")
        size = integer(region["sector_bytes"], "sector size", 1)
        count = integer(region["count"], "sector count", 1)
        require(size >= 256 and size & (size - 1) == 0, "invalid sector size")
        require(offset == region_end, "erase regions have a gap or overlap")
        region_end = offset + size * count
        require(region_end <= CAPACITY, "erase geometry capacity overflow")
        boundaries.update(range(offset, region_end + 1, size))
    require(region_end == CAPACITY, "incomplete erase geometry")
    image_start, image_end = [integer(x, "image address") for x in plan["image_range"]]
    erase_start, erase_end = [integer(x, "erase address") for x in plan["erase_range"]]
    require(0 <= erase_start <= image_start < image_end <= erase_end <= CAPACITY,
            "image/erase range or capacity overflow")
    require(erase_start in boundaries and erase_end in boundaries,
            "erase range is not sector aligned")

    bit_path, bit_data = checked_file(plan["bitstream"])
    payload = plan["load_payload"]
    offset = integer(payload["source_offset"], "payload source offset")
    length = integer(payload["length"], "payload length", 1)
    require(offset + length <= len(bit_data), "payload exceeds source bitstream")
    raw = bit_data[offset:offset + length]
    require(hashlib.sha256(raw).hexdigest() == payload["sha256"],
            "load payload SHA256 mismatch")
    confirmed(payload["evidence"], "load payload evidence")
    require(payload["bitswap"] in {"none", "reverse-per-byte"}, "bitswap must be explicit")
    expected = raw if payload["bitswap"] == "none" else raw.translate(BIT_REVERSE)
    padding = integer(payload["padding_bytes"], "padding bytes")
    require(len(expected) + padding == image_end - image_start,
            "payload/padding length does not match image range")
    expected += b"\xff" * padding
    return bit_path, expected


def parse_mcs(text, capacity, image_range):
    """Accept Intel HEX 00/01/02/04 records for one contiguous load image."""
    start, end = image_range
    require(type(capacity) is int and 0 < capacity <= CAPACITY, "invalid MCS capacity")
    require(0 <= start < end <= capacity, "invalid MCS image range")
    require(len(text) <= capacity * 5 + 1024, "MCS text exceeds bounded capacity")
    memory = bytearray(end - start)
    present = bytearray(end - start)
    base, eof, records = 0, False, 0
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        require(not eof, f"record after EOF at line {line_number}")
        require(re.fullmatch(r":[0-9a-fA-F]+", line) and len(line) % 2 == 1,
                f"invalid Intel HEX syntax at line {line_number}")
        record = bytes.fromhex(line[1:])
        require(len(record) >= 5 and len(record) == record[0] + 5,
                f"record length mismatch at line {line_number}")
        require(sum(record) & 0xFF == 0, f"record checksum mismatch at line {line_number}")
        length, kind = record[0], record[3]
        address = int.from_bytes(record[1:3], "big")
        data = record[4:-1]
        records += 1
        if kind == 0:
            require(length > 0 and address + length <= 0x10000,
                    "empty data or 16-bit record address overflow")
            absolute = base + address
            require(0 <= absolute < absolute + length <= capacity, "MCS capacity overflow")
            require(start <= absolute and absolute + length <= end,
                    "MCS data outside approved image range")
            local = absolute - start
            require(not any(present[local:local + length]), "overlapping/duplicate MCS data")
            memory[local:local + length] = data
            present[local:local + length] = b"\x01" * length
        elif kind == 1:
            require(length == 0 and address == 0, "invalid EOF record")
            eof = True
        elif kind in {2, 4}:
            require(length == 2 and address == 0, "invalid extended address record")
            base = int.from_bytes(data, "big") << (4 if kind == 2 else 16)
            require(base < capacity, "extended address exceeds capacity")
        else:
            raise ValueError(f"unsupported Intel HEX record type {kind}")
    require(eof, "missing EOF")
    require(all(present), "missing load bytes in MCS")
    return bytes(memory), records


def validate_mcs(plan, mcs_path):
    _, expected = validate_plan(plan)
    path = Path(mcs_path)
    require(path.stat().st_size <= CAPACITY * 5 + 1024, "MCS file exceeds bounded capacity")
    memory, records = parse_mcs(path.read_text(encoding="ascii"),
                                plan["capacity_bytes"], plan["image_range"])
    require(memory == expected, "MCS load payload mismatch")
    return {"status": "OFFLINE_MCS_PASS", "records": records,
            "image_range": plan["image_range"], "payload_bytes": len(memory),
            "memory_sha256": hashlib.sha256(memory).hexdigest(),
            "mcs_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "hardware_operations": 0}


def tcl_word(value):
    # Braced Tcl words require both braces and backslash-newline handling.
    # Reject these uncommon path characters instead of implementing Tcl quoting.
    value = str(value)
    require(not any(c in value for c in "{}\\\n\r"), "unsupported Tcl path character")
    return "{" + value + "}"


def validate_tool(plan):
    tool_path, _ = checked_file(plan["tool"])
    require(plan["tool"]["version"] == "2025.2" and tool_path.name == "vivado",
            "expected pinned Vivado 2025.2 executable")
    # A reviewed tool startup manifest is required; no attempt to bypass startup files.
    confirmed(plan["tool"]["startup_evidence"], "Vivado startup evidence")
    return tool_path


def check_plan(plan):
    _, expected = validate_plan(plan)
    validate_tool(plan)
    return {"status": "OFFLINE_FLASH_PLAN_PASS", "image_range": plan["image_range"],
            "payload_bytes": len(expected), "hardware_operations": 0}


def generate(plan, output_dir):
    bit_path, _ = validate_plan(plan)
    tool_path = validate_tool(plan)
    destination = Path(output_dir).resolve()
    script = (
        "# Offline file conversion only. No hardware commands.\n"
        "if {![string match {2025.2*} [version -short]]} {error {Vivado version mismatch}}\n"
        f"write_cfgmem -format mcs -size 16 -interface {plan['interface']} "
        f"-loadbit [list up {plan['image_range'][0]} {tcl_word(bit_path)}] "
        "{candidate.mcs}\n"
        "exit\n"
    )
    destination.mkdir(parents=True, exist_ok=False)
    temporary = destination / "tmp"
    temporary.mkdir()
    script_path = destination / "write_cfgmem.tcl"
    script_path.write_text(script, encoding="utf-8")
    command = [str(tool_path), "-mode", "batch", "-source", str(script_path)]
    (destination / "command.json").write_text(json.dumps(command) + "\n", encoding="utf-8")
    environment = {**os.environ, "TMPDIR": str(temporary),
                   "TMP": str(temporary), "TEMP": str(temporary)}
    status = {"state": "running", "started_utc": datetime.now(timezone.utc).isoformat(),
              "ended_utc": None, "exit_code": None, "timeout": False,
              "retry_count": 0, "hardware_operations": 0}
    status_path = destination / "execution.json"
    status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    try:
        with (destination / "tool-output.log").open("x", encoding="utf-8") as log:
            result = subprocess.run(command, cwd=destination, env=environment, stdout=log,
                                    stderr=subprocess.STDOUT, timeout=300, check=False)
        status.update(state="tool_completed" if result.returncode == 0 else "tool_failed",
                      exit_code=result.returncode)
    except (OSError, subprocess.SubprocessError) as error:
        status.update(state="tool_failed", error=str(error),
                      timeout=isinstance(error, subprocess.TimeoutExpired))
        raise
    finally:
        status["ended_utc"] = datetime.now(timezone.utc).isoformat()
        status_path.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    require(result.returncode == 0, f"offline Vivado failed with exit {result.returncode}")
    # Detect changed sources before accepting the output. No retry or overwrite.
    checked_file(plan["tool"])
    report = validate_mcs(plan, destination / "candidate.mcs")
    (destination / "validation.json").write_text(json.dumps(report, indent=2) + "\n",
                                                  encoding="utf-8")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check-plan", action="store_true", help="default: validate plan without output files")
    action.add_argument("--check-mcs", type=Path)
    action.add_argument("--generate", type=Path, metavar="FRESH_DIRECTORY")
    action.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.execute:
        parser.error("Hardware execution is not implemented. Separate F1/F2 approval, "
                     "confirmed part and verified backup are required; no device was opened.")
    if args.plan is None:
        parser.error("--plan is required")
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if args.generate:
            report = generate(plan, args.generate)
        elif args.check_mcs:
            report = validate_mcs(plan, args.check_mcs)
        else:
            report = check_plan(plan)
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"OFFLINE_FLASH_PREPARE_FAIL: {error}\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
