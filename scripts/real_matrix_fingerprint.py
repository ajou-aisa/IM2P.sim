"""Compute a content/config fingerprint for one real frontend RTL pair."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def files_under(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    files: set[Path] = set()
    for pattern in patterns:
        files.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(files)


def add_file(digest: hashlib._Hash, category: str, root: Path, path: Path) -> None:
    relative = path.relative_to(root).as_posix()
    data = path.read_bytes()
    digest.update(f"file:{category}:{relative}:{len(data)}\n".encode())
    digest.update(data)
    digest.update(b"\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bits", type=int, choices=(4, 8, 16), required=True)
    parser.add_argument("--dim", type=int, choices=(16, 32), required=True)
    parser.add_argument("--gemmini-root", type=Path, required=True)
    parser.add_argument("--params-root", type=Path, required=True)
    parser.add_argument("--extra-input", type=Path)
    parser.add_argument("--config", action="append", default=[])
    args = parser.parse_args()

    gemmini_root = args.gemmini_root.resolve()
    params_root = args.params_root.resolve()
    selected_top = ROOT / "synth" / f"SynthInt{args.bits}x{args.dim}.bsv"
    required = (
        ROOT / "Makefile",
        ROOT / "sim/Cargo.toml",
        ROOT / "sim/build.rs",
        selected_top,
        params_root.parent / "gemmini_params.h",
        gemmini_root / "ggml/src/ggml-gemmini/ggml-gemmini-args.h",
        gemmini_root / "ggml/src/ggml-common.h",
        gemmini_root / "ggml/include/ggml.h",
    )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        parser.error("missing fingerprint input(s): " + ", ".join(missing))

    local_files = files_under(
        ROOT,
        (
            "Makefile",
            "scripts/real_matrix_fingerprint.py",
            "frontend/include/*.hpp",
            "frontend/src/*.cpp",
            "frontend/tests/*.cpp",
            "frontend/tests/*.hpp",
            "sim/Cargo.toml",
            "sim/Cargo.lock",
            "sim/build.rs",
            "sim/src/**/*.rs",
            "sim/include/*.h",
            "sim/ffi/*.h",
            "sim/ffi/*.cpp",
            "src/**/*.bsv",
        ),
    )
    local_files.append(selected_top)
    local_files = sorted(set(local_files))
    gemmini_files = files_under(
        gemmini_root,
        (
            "ggml/src/ggml-gemmini/**/*.h",
            "ggml/src/ggml-gemmini/**/*.hpp",
            "ggml/src/ggml-common.h",
            "ggml/include/ggml.h",
        ),
    )
    parameter_files = files_under(
        params_root.parent, ("gemmini_params.h", "include/**/*.h")
    )

    digest = hashlib.sha256()
    identity = f"a{args.bits}-w8-d{args.dim}"
    for value in (
        "real-matrix-fingerprint-v1",
        f"identity={identity}",
        f"top=mkSynthInt{args.bits}x{args.dim}",
        f"activation_bits={args.bits}",
        f"activation_storage_bytes={(args.bits + 7) // 8}",
        f"dim={args.dim}",
        *sorted(f"config={value}" for value in args.config),
    ):
        digest.update(value.encode())
        digest.update(b"\n")
    for path in local_files:
        add_file(digest, "im2p", ROOT, path)
    for path in gemmini_files:
        add_file(digest, "gemmini", gemmini_root, path)
    for path in parameter_files:
        add_file(digest, "params", params_root.parent, path)
    if args.extra_input is not None:
        extra = args.extra_input.resolve()
        if not extra.is_file():
            parser.error(f"extra fingerprint input does not exist: {extra}")
        data = extra.read_bytes()
        digest.update(f"extra:{extra.name}:{len(data)}\n".encode())
        digest.update(data)
        digest.update(b"\n")

    print(digest.hexdigest())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
