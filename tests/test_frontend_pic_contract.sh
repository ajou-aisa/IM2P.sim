#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${TMPDIR:-/tmp}/im2p-frontend-pic-contract.$$"
OBJECT="$BUILD_DIR/bin/a8-w8-d16/im2p_gemmini_frontend.o"

output="$(
  make -C "$ROOT" --no-print-directory -B -n \
    "BUILD_DIR=$BUILD_DIR" \
    "GEMMINI_ROOT=${GEMMINI_ROOT:-$ROOT/../llama.cpp-gemmini}" \
    "GEMMINI_PARAMS_ROOT=${GEMMINI_PARAMS_ROOT:-$ROOT/../RISC-V-DynDNN-gemmini-include/include}" \
    "$OBJECT"
)"
compile_command="${output//$'\\\n'/ }"

if [[ "$compile_command" != *"frontend/src/im2p_gemmini_frontend.cpp"* ]]; then
  printf '%s\n' "frontend PIC contract could not find the production compile command:" >&2
  printf '%s\n' "$output" >&2
  exit 1
fi
if [[ " $compile_command " != *" -fPIC "* ]]; then
  printf '%s\n' "production frontend archive objects must be compiled with -fPIC before linking into libggml-gemmini.so:" >&2
  printf '%s\n' "$compile_command" >&2
  exit 1
fi

printf '%s\n' 'FRONTEND PIC CONTRACT PASS'
