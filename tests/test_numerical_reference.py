#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: uv run tests/test_numerical_reference.py
from __future__ import annotations

import sys
import json
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.numerical_reference import Dot, Operation, evaluate, self_test


def main() -> None:
    assert evaluate(Dot((1,), (1,), (), operation=Operation.BYPASS, block_size=0)).value == 1
    production = Dot((-32768,) * 3, (-32768,) * 3, (1,), operand_bits=16)
    wide = evaluate(production)
    assert wide.datapath_width == 64 and not wide.diagnostic_profile
    assert wide.value == 3221225472 and wide.raw_int32_value == 2147483647
    assert not evaluate(replace(production, diagnostic_width=32)).overflow_free
    with tempfile.TemporaryDirectory(prefix="im2p-numerical-") as directory:
        path = Path(directory) / "input.json"
        for key, value in (("activations", [128]), ("activations", [True]),
                           ("activations", [0.5]), ("scales", [0.5]),
                           ("scales", [True]), ("dim", 1), ("diagnostic_width", 32.0), ("width", 32)):
            raw = {"activations": [1], "weights": [1], "scales": [1], "operation": "multiply", key: value}
            path.write_text(json.dumps(raw))
            result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts/numerical_reference.py"), "--input", str(path)], capture_output=True, text=True)
            assert result.returncode != 0, f"invalid input accepted: {raw}"
    self_test()
    print("NUMERICAL PROFILE: PASS (production widths, explicit diagnostics, input rejection, bypass-zero)")


if __name__ == "__main__":
    main()
