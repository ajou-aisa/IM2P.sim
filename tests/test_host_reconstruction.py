#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
# Run: uv run tests/test_host_reconstruction.py
from __future__ import annotations

import math
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    assert (ROOT / "scripts/host_reconstruction.py").is_file(), "floating provider reconstruction oracle is missing"
    from scripts.host_reconstruction import Reconstruction, reconstruct
    from scripts.numerical_reference import Dot, Operation, evaluate

    result = reconstruct(Reconstruction((4, -2), (0.5, 0.25), 0.5, 1.0, (0.25,)))
    assert result.value == 2.0 and result.mathematical_value == 2
    assert result.overflow_free
    cancellation = reconstruct(Reconstruction((2, -2), (1e308, 1e308), 1.0))
    assert math.isnan(cancellation.value) and cancellation.mathematical_value == 0
    assert not cancellation.overflow_free
    assert cancellation.boundaries["double_product"].first_index == 0
    final_overflow = reconstruct(Reconstruction((1,), (1e39,), 1.0))
    assert not final_overflow.overflow_free
    assert final_overflow.boundaries["float_output_cast"].overflow_count == 1
    merge = reconstruct(Reconstruction((1,), (2e38,), 1.0, 2e38))
    assert not merge.overflow_free
    assert merge.boundaries["float_output_merge"].overflow_count == 1
    residual = reconstruct(Reconstruction((1,), (2e38,), 1.0, residuals=(2e38,)))
    assert not residual.overflow_free
    assert residual.boundaries["residual_merge"].first_index == 0
    rounded = reconstruct(Reconstruction((16777217,), (1.0,), 1.0))
    assert rounded.value == 16777216.0 and rounded.mathematical_value == 16777217
    assert rounded.overflow_free
    converted = reconstruct(Reconstruction(((1 << 53) + 1, -(1 << 53)), (1.0, 1.0), 1.0))
    assert converted.value == 0 and converted.mathematical_value == 1 and converted.overflow_free
    for bad in (Reconstruction((True,), (1.0,), 1.0), Reconstruction((1,), (math.inf,), 1.0),
                Reconstruction((1,), (1.0,), math.nan), Reconstruction((1,), (1.0,), 1.0, residuals=(math.inf,))):
        try:
            reconstruct(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid host input accepted")
    external = evaluate(Dot((-128,) * 8192, (-128,) * 8192, (1,) * 256, operation=Operation.EXTERNAL))
    reconstructed = reconstruct(Reconstruction(external.block_outputs, (16.0,) * 256, 1.0))
    assert reconstructed.value == 1 << 31 and reconstructed.overflow_free
    cases = (Reconstruction((4, -2), (0.5, 0.25), 0.5, 1.0, (0.25,)),
             Reconstruction((16777217,), (1.0,), 1.0),
             Reconstruction(((1 << 53) + 1, -(1 << 53)), (1.0, 1.0), 1.0),
             Reconstruction((1, 1), (0.1, 0.2), 0.3, 0.1, (-0.1,)))
    source = r'''
#include <cstdint>
#include <cstdio>
#include <cstring>
struct Case { int64_t raw[2]; double factor[2]; int count; float activation, initial, residual; };
int main() {
  Case cases[] = {
    {{4, -2}, {.5, .25}, 2, .5f, 1.f, .25f},
    {{16777217, 0}, {1., 0.}, 1, 1.f, 0.f, 0.f},
    {{(int64_t{1} << 53) + 1, -(int64_t{1} << 53)}, {1., 1.}, 2, 1.f, 0.f, 0.f},
    {{1, 1}, {.1, .2}, 2, .3f, .1f, -.1f}
  };
  for (const Case &c : cases) {
    double sum = 0;
    for (int block = 0; block < c.count; ++block)
      sum += static_cast<double>(c.raw[block]) * c.factor[block] * static_cast<double>(c.activation);
    float output = c.initial;
    output += static_cast<float>(sum);
    output += c.residual;
    uint32_t bits;
    std::memcpy(&bits, &output, sizeof(bits));
    std::printf("%u\n", bits);
  }
}
'''
    with tempfile.TemporaryDirectory(prefix="im2p-host-reference-") as directory:
        executable = Path(directory) / "reference"
        subprocess.run([os.environ.get("CXX", "c++"), "-std=c++17", "-ffp-contract=off", "-x", "c++", "-", "-o", str(executable)], input=source, text=True, check=True)
        observed = subprocess.run([str(executable)], check=True, capture_output=True, text=True)
        expected = [struct.unpack("=I", struct.pack("=f", reconstruct(case).value))[0] for case in cases]
        assert [int(line) for line in observed.stdout.splitlines()] == expected
    print("HOST RECONSTRUCTION: PASS (native C++ exact bits, external blocks, Fraction oracle, intermediate/output/residual overflow)")


if __name__ == "__main__":
    main()
