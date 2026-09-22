#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")/.."
fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT
llama="$fixture/llama"
mkdir -p "$llama/ggml/src/ggml-gemmini"
touch "$llama/ggml/src/ggml-gemmini/ggml-gemmini-args.h"
build="$fixture/build"

legacy="$(make --no-print-directory -n c-api-test BUILD_DIR="$build" IM2P_SIM_IMPLEMENTATION=LEGACY_BSV)"
[[ "$legacy" == *'rtl-one TOP=mkSynthA8W8D16'* ]] || {
  echo 'LEGACY_BSV c-api-test must generate matching RTL before Cargo' >&2
  exit 1
}
[[ "$legacy" == *'cargo build'* ]]

hp1="$(make --no-print-directory -n c-api-test BUILD_DIR="$build" IM2P_SIM_IMPLEMENTATION=GEMMINI_HP1)"
[[ "$hp1" != *'rtl-one TOP='* ]] || {
  echo 'GEMMINI_HP1 c-api-test must not require legacy RTL' >&2
  exit 1
}

fake="$(make --no-print-directory -n gemmini-frontend-test BUILD_DIR="$build" GEMMINI_ROOT="$llama" IM2P_SIM_IMPLEMENTATION=GEMMINI_HP1)"
[[ "$fake" == *'-DIM2P_PRODUCTION_TRACE_ENABLED=0'* ]] || {
  echo 'frontend without the real optrace source must compile trace-OFF' >&2
  exit 1
}
[[ "$fake" == *'frontend/tests/test_frontend.cpp'* && "$fake" != *'-DIM2P_SIM_IMPLEMENTATION_GEMMINI_HP1=1'* && "$fake" != *'libim2p_sim.a'* ]] || {
  echo 'fake ABI suite must run on LEGACY_BSV without the real numerical archive' >&2
  exit 1
}

if make --no-print-directory -n gemmini-frontend-test BUILD_DIR="$build" GEMMINI_ROOT="$llama" IM2P_PRODUCTION_TRACE_ENABLED=1 >"$fixture/trace-on.log" 2>&1; then
  echo 'explicit trace-ON must reject a missing production optrace API' >&2
  exit 1
fi
rg -q 'optrace.hpp.*optrace.cpp' "$fixture/trace-on.log" || {
  echo 'trace-ON rejection must identify both missing production inputs' >&2
  exit 1
}

mkdir -p "$llama/ggml/src/ggml-gemmini-utils/include/gemmini" "$llama/ggml/src/ggml-gemmini-utils/src"
touch "$llama/ggml/src/ggml-gemmini-utils/include/gemmini/optrace.hpp" "$llama/ggml/src/ggml-gemmini-utils/src/optrace.cpp"
trace_on="$(make --no-print-directory -n gemmini-frontend-test BUILD_DIR="$build" GEMMINI_ROOT="$llama" IM2P_SIM_IMPLEMENTATION=LEGACY_BSV)"
[[ "$trace_on" == *'-DIM2P_PRODUCTION_TRACE_ENABLED=1'* ]] || {
  echo 'frontend with both real optrace inputs must retain trace-ON' >&2
  exit 1
}

real="$(make --no-print-directory -n gemmini-frontend-real-test-q8-hp1 BUILD_DIR="$build" GEMMINI_ROOT="$llama" IM2P_SIM_IMPLEMENTATION=GEMMINI_HP1)"
[[ "$real" == *'frontend/tests/test_frontend_real.cpp'* && "$real" == *'libim2p_sim.a'* && "$real" != *'frontend/tests/test_frontend.cpp'* ]] || {
  echo 'real HP1 test must link its own executable to the genuine numerical archive' >&2
  exit 1
}

route_failures=0
for bits in 4 16; do
  implementation=LEGACY_BSV
  if [[ "$bits" == 4 ]]; then implementation=GEMMINI_HP1; fi
  real="$(make --no-print-directory -n "gemmini-frontend-real-test-q${bits}-hp1" BUILD_DIR="$build" GEMMINI_ROOT="$llama" IM2P_SIM_IMPLEMENTATION="$implementation" GEMMINI_FRONTEND_DIM=16)"
  real="${real//\\$'\n'/}"
  route_binary="$(awk -v route="q${bits}_hp1" '{ for (i = 1; i < NF; i++) if ($i == "--route" && $(i+1) == route) print $(i-1) }' <<< "$real")"
  expected="$build/bin/$implementation/a${bits}-w${bits}-d16/im2p_gemmini_frontend_real_test"
  if [[ "$route_binary" != "$expected" ]]; then
    echo "q${bits}_hp1 must invoke $expected; got $route_binary" >&2
    route_failures=1
  fi
done
[[ "$route_failures" == 0 ]] || exit 1

echo 'MAKE REGRESSION GATES PASS'
