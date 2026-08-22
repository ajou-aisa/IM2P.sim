# -----------------------------------------------------------------------------
# IM2P.sim build / verification entry points
# -----------------------------------------------------------------------------

SHELL := /bin/bash

BSC       ?= bsc
CC        ?= cc
CXX       ?= g++
AR        ?= ar
PYTHON    ?= python3
VERILATOR ?= verilator
YOSYS     ?= yosys

IM2P_ACTIVATION_BITS ?= 8
IM2P_DIM ?= 16
IM2P_ALLOWED_ACTIVATION_BITS := 4 8 16
IM2P_ALLOWED_DIMS := 16 32 64
ifeq ($(filter $(IM2P_ACTIVATION_BITS),$(IM2P_ALLOWED_ACTIVATION_BITS)),)
$(error IM2P_ACTIVATION_BITS must be one of 4, 8, or 16 (got '$(IM2P_ACTIVATION_BITS)'))
endif
ifeq ($(filter $(IM2P_DIM),$(IM2P_ALLOWED_DIMS)),)
$(error IM2P_DIM must be 16, 32, or 64 (got '$(IM2P_DIM)'))
endif

BUILD_DIR := build
ROOT_DIR  := $(CURDIR)
IM2P_ARTIFACT_ID = a$(IM2P_ACTIVATION_BITS)-w8-d$(IM2P_DIM)
IM2P_CARGO_TARGET_DIR = $(abspath $(BUILD_DIR)/cargo/$(IM2P_ARTIFACT_ID))
IM2P_RESULTS_DIR = $(BUILD_DIR)/results/$(IM2P_ARTIFACT_ID)
CARGO_TEST_FILTER ?=

# Optional, simulator-owned Gemmini C++ frontend. The default core build has no
# dependency on llama.cpp-gemmini or its headers.
ENABLE_GEMMINI_FRONTEND ?= 0
GEMMINI_ROOT ?= $(abspath ../llama.cpp-gemmini)
GEMMINI_PARAMS_ROOT ?= $(abspath ../RISC-V-DynDNN-gemmini-include/include)
GEMMINI_FRONTEND_ACTIVATION_BITS ?= $(IM2P_ACTIVATION_BITS)
GEMMINI_FRONTEND_DIM ?= $(IM2P_DIM)
GEMMINI_FRONTEND_BLOCK_SIZE = $(if $(filter 64,$(GEMMINI_FRONTEND_DIM)),64,32)
ifeq ($(filter $(GEMMINI_FRONTEND_ACTIVATION_BITS),$(IM2P_ALLOWED_ACTIVATION_BITS)),)
$(error GEMMINI_FRONTEND_ACTIVATION_BITS must be one of 4, 8, or 16 (got '$(GEMMINI_FRONTEND_ACTIVATION_BITS)'))
endif
ifeq ($(filter $(GEMMINI_FRONTEND_DIM),$(IM2P_ALLOWED_DIMS)),)
$(error GEMMINI_FRONTEND_DIM must be 16, 32, or 64 (got '$(GEMMINI_FRONTEND_DIM)'))
endif
GEMMINI_ARTIFACT_ID = a$(GEMMINI_FRONTEND_ACTIVATION_BITS)-w8-d$(GEMMINI_FRONTEND_DIM)
GEMMINI_CARGO_TARGET_DIR = $(abspath $(BUILD_DIR)/cargo/$(GEMMINI_ARTIFACT_ID))
GEMMINI_RESULTS_DIR = $(BUILD_DIR)/results/$(GEMMINI_ARTIFACT_ID)
BSC_PATH  := +:src/common:src/io:src/array:src/vector:src/accumulator:src/control:src/core:tests:synth
BSC_DIRS  := -bdir $(BUILD_DIR)/bsc -simdir $(BUILD_DIR)/sim \
             -info-dir $(BUILD_DIR)/info
BSC_SIM_DIRS := -bdir $(BUILD_DIR)/bsc/sim -simdir $(BUILD_DIR)/sim \
                -info-dir $(BUILD_DIR)/info
# FP16 elaboration needs more unfolding steps and stack than BSC defaults.
BSC_EXTRA_FLAGS ?= -steps 4000000 -steps-warn-interval 1000000 \
                   -steps-max-intervals 20 +RTS -K256M -RTS
BSC_COMMON := -p $(BSC_PATH) $(BSC_DIRS) -keep-fires -show-schedule \
              $(BSC_EXTRA_FLAGS)
BSC_SIM_COMMON := -p $(BSC_PATH) $(BSC_SIM_DIRS) -keep-fires -show-schedule \
                  -check-assert $(BSC_EXTRA_FLAGS) -Xc++ -O0

CPP_TOOL := $(BUILD_DIR)/bin/im2p_reference
CPP_SRC  := tools/im2p_reference.cpp

BSV_TEST_TOPS := \
	mkTbArithmetic \
	mkTbPE \
	mkTbWorkScheduler \
	mkTbMatmulScheduler \
	mkTbMatmulLookahead \
	mkTbIM2PLookahead \
	mkTbIM2PLookaheadScale \
	mkTbSystolicArrayWeightBanks \
	mkTbSystolicEngineWeightBanks \
	mkTbInputSkew \
	mkTbSystolicArray \
	mkTbVectorUnit \
	mkTbAccumulator \
	mkTbExecuteController \
	mkTbIM2PCore \
	mkTbIM2PCoreActivationBuffer \
	mkTbIM2PCoreMatrix \
	mkTbIM2PCoreMultiwidth \
	mkTbIM2PCoreOutputAddressing \
	mkTbIM2PCoreMatrixScale \
	mkTbIM2PCoreExternal \
	mkTbIM2PCoreGrouped \
	mkTbFloatCore \
	mkTbSynthInt8x16 \
	mkTbSynthInt8x32 \
	mkTbSynthInt8x64

define run_bluesim
	log="$(BUILD_DIR)/info/$$package.bluesim.log"; \
	status=0; \
	$(BUILD_DIR)/bin/$$package 2>&1 | tee "$$log" || status=$$?; \
	test $$status -eq 0 || exit $$status; \
	grep -q 'FAIL' "$$log" && { echo "[Bluesim] $$top reported FAIL" >&2; exit 1; }; \
	grep -q 'PASS' "$$log" || { echo "[Bluesim] $$top missing PASS" >&2; exit 1; }
endef

SYNTH_TOPS := \
	mkSynthInt8 \
	mkSynthFp16 \
	mkSynthFp32

BSC_PREFIX := $(shell dirname $$(dirname $$(realpath $$(command -v $(BSC)))))
BSC_VERILOG ?= $(firstword $(wildcard $(BSC_PREFIX)/libexec/lib/Verilog \
                                      $(BSC_PREFIX)/lib/Verilog))
VERILATOR_COMMON := --cc --Wno-fatal

.PHONY: all check verify static-check cpp-test c-api-layout-test c-api-test gemmini-frontend \
        gemmini-frontend-test gemmini-frontend-test-sanitized \
        gemmini-frontend-asan-test gemmini-frontend-tsan-test \
        gemmini-frontend-real-test gemmini-frontend-real-test-q8-h0 \
        gemmini-frontend-real-test-matrix gemmini-frontend-real-test-mismatch \
        bsv-test bsv-test-one rtl rtl-one \
        verilator-int4x16 verilator-int8x16 verilator-int16x16 \
        verilator-int4x32 verilator-int8x32 verilator-int16x32 verilator \
        verilator-int4x64 verilator-int8x64 verilator-int16x64 \
        sim-test-int4x16 sim-test-int8x16 sim-test-int16x16 \
        sim-test-int4x32 sim-test-int8x32 sim-test-int16x32 \
        sim-test-int4x64 sim-test-int8x64 sim-test-int16x64 sim-test \
        verilator-lint yosys-stat clean help check-tools

all: check

check: static-check cpp-test
ifeq ($(ENABLE_GEMMINI_FRONTEND),1)
check: gemmini-frontend-test
endif

# BSC가 설치된 개발 환경에서 source test와 대표 RTL elaboration까지 한 번에 수행한다.
verify: check bsv-test rtl

help:
	@printf '%s\n' \
	  'make check           - architecture 정적 검사 + C++ reference self-test' \
	  'make verify          - check + 전체 Bluesim test + 대표 RTL 생성' \
	  'make bsv-test        - 모든 Bluesim testbench 컴파일 및 실행' \
	  'make bsv-test-one TOP=mkTbPE - 지정한 testbench만 컴파일 및 실행' \
	  'make rtl             - INT8/FP16/FP32 top의 Verilog 생성' \
	  'make rtl-one TOP=mkSynthInt8 - 지정한 top의 Verilog만 생성' \
	  'make verilator-int<bits>x<dim> - isolated 4/8/16-bit DIM16/32/64 model' \
	  'make verilator IM2P_ACTIVATION_BITS=8 - selected-width DIM16/32/64 models' \
	  'make sim-test-int<bits>x<dim> - matching isolated Rust RTL tests' \
	  'make sim-test IM2P_ACTIVATION_BITS=8 - selected-width DIM16/32/64 tests' \
	  'make gemmini-frontend - optional Gemmini adapter static library' \
	  'make gemmini-frontend-test - optional Gemmini adapter contract tests' \
	  'make gemmini-frontend-test-sanitized - public ASan+UBSan lifecycle suite' \
	  'make gemmini-frontend-asan-test - isolated ASan+UBSan frontend suite' \
	  'make gemmini-frontend-tsan-test - isolated TSan frontend suite' \
	  'make gemmini-frontend-real-test - selected adapter full/stripe RTL oracle (A8 uses q8_h1)' \
	  'make gemmini-frontend-real-test-q8-h0 - maintained raw Q8 full/stripe RTL oracle' \
	  'make gemmini-frontend-real-test-matrix - isolated A8 x DIM16/32/64 real matrix' \
	  'make gemmini-frontend-real-test-mismatch - fail-closed A16 frontend/A8 simulator QA' \
	  'make verilator-lint  - 생성 Verilog에 Verilator lint 적용' \
	  'make yosys-stat      - 생성 Verilog에 Yosys generic synthesis/stat 적용' \
	  'make check-tools     - 외부 도구 설치 여부 확인' \
	  'make clean           - build/ 삭제'

$(BUILD_DIR)/bin $(BUILD_DIR)/lib $(BUILD_DIR)/bsc $(BUILD_DIR)/bsc/sim $(BUILD_DIR)/sim $(BUILD_DIR)/info:
	@mkdir -p $@

static-check:
	$(PYTHON) scripts/static_check.py

$(CPP_TOOL): $(CPP_SRC) Makefile | $(BUILD_DIR)/bin
	$(CXX) -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror \
		$(CPP_SRC) -o $(CPP_TOOL)

cpp-test: $(CPP_TOOL)
	$(CPP_TOOL)

C_API_BUILD_DIR = $(BUILD_DIR)/c-api/$(IM2P_ARTIFACT_ID)

c-api-layout-test: | $(BUILD_DIR)/bin
	@mkdir -p $(C_API_BUILD_DIR)
	$(CC) -std=c11 -Wall -Wextra -Wpedantic -Werror \
		-Isim/include sim/tests/c_api_v3_layout.c \
		-o $(C_API_BUILD_DIR)/im2p_c_api_v3_layout
	$(CXX) -std=c++20 -Wall -Wextra -Wpedantic -Werror \
		-Isim/include sim/tests/c_api_v3_layout.cpp \
		-o $(C_API_BUILD_DIR)/im2p_cpp_api_v3_layout
	$(C_API_BUILD_DIR)/im2p_c_api_v3_layout
	$(C_API_BUILD_DIR)/im2p_cpp_api_v3_layout

c-api-test: c-api-layout-test | $(BUILD_DIR)/bin
	@mkdir -p $(C_API_BUILD_DIR)
	$(CC) -std=c11 -Wall -Wextra -Wpedantic -Werror \
		-Isim/include -c sim/tests/c_api_smoke.c \
		-o $(C_API_BUILD_DIR)/c_api_smoke.o
	$(CC) -std=c11 -Wall -Wextra -Wpedantic -Werror \
		-DIM2P_TEST_ACTIVATION_BITS=$(IM2P_ACTIVATION_BITS) \
		-DIM2P_TEST_DIM=$(IM2P_DIM) \
		-Isim/include -c sim/tests/c_api_v3_runtime.c \
		-o $(C_API_BUILD_DIR)/c_api_v3_runtime.o
	IM2P_REPO_ROOT=$(ROOT_DIR) IM2P_ACTIVATION_BITS=$(IM2P_ACTIVATION_BITS) \
		IM2P_DIM=$(IM2P_DIM) CARGO_TARGET_DIR=$(IM2P_CARGO_TARGET_DIR) cargo build \
		--manifest-path sim/Cargo.toml --lib --release
	$(CXX) -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror \
		$(C_API_BUILD_DIR)/c_api_smoke.o \
		$(IM2P_CARGO_TARGET_DIR)/release/libim2p_sim.a \
		-o $(C_API_BUILD_DIR)/im2p_c_api_smoke
	$(CXX) -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror \
		$(C_API_BUILD_DIR)/c_api_v3_runtime.o \
		$(IM2P_CARGO_TARGET_DIR)/release/libim2p_sim.a \
		-o $(C_API_BUILD_DIR)/im2p_c_api_v3_runtime
	$(C_API_BUILD_DIR)/im2p_c_api_smoke
	$(C_API_BUILD_DIR)/im2p_c_api_v3_runtime

GEMMINI_DIM_CONFIG_DIR = $(BUILD_DIR)/generated/$(GEMMINI_ARTIFACT_ID)
GEMMINI_DIM_CONFIG = $(GEMMINI_DIM_CONFIG_DIR)/gemmini_params.h
GEMMINI_FRONTEND_INCLUDES := \
	-Ifrontend/include -Isim/include -I$(GEMMINI_DIM_CONFIG_DIR) \
	-I$(GEMMINI_ROOT)/ggml/src/ggml-gemmini \
	-I$(GEMMINI_ROOT)/ggml/src/ggml-gemmini-utils/include \
	-I$(GEMMINI_ROOT)/ggml/include -I$(GEMMINI_ROOT)/ggml/src \
	-I$(GEMMINI_PARAMS_ROOT)
GEMMINI_FRONTEND_FLAGS = -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
	-DIM2P_GEMMINI_FRONTEND_EXPECTED_DIM=$(GEMMINI_FRONTEND_DIM) \
	-DIM2P_GEMMINI_FRONTEND_ACTIVATION_BITS=$(GEMMINI_FRONTEND_ACTIVATION_BITS) \
	-DGGML_GEMMINI_BLOCK_SIZE=$(GEMMINI_FRONTEND_BLOCK_SIZE)
GEMMINI_FRONTEND_OBJECT = $(BUILD_DIR)/bin/$(GEMMINI_ARTIFACT_ID)/im2p_gemmini_frontend.o
GEMMINI_FRONTEND_ARCHIVE = $(BUILD_DIR)/lib/$(GEMMINI_ARTIFACT_ID)/libim2p_gemmini_frontend.a
GEMMINI_FRONTEND_TEST_OBJECT = $(BUILD_DIR)/bin/$(GEMMINI_ARTIFACT_ID)/im2p_gemmini_frontend_testing.o
GEMMINI_FRONTEND_TEST_ARCHIVE = $(BUILD_DIR)/lib/$(GEMMINI_ARTIFACT_ID)/libim2p_gemmini_frontend_testing.a
GEMMINI_FRONTEND_TEST = $(BUILD_DIR)/bin/$(GEMMINI_ARTIFACT_ID)/im2p_gemmini_frontend_test
GEMMINI_FRONTEND_ASAN_TEST = $(BUILD_DIR)/bin/$(GEMMINI_ARTIFACT_ID)/im2p_gemmini_frontend_asan_test
GEMMINI_FRONTEND_TSAN_TEST = $(BUILD_DIR)/bin/$(GEMMINI_ARTIFACT_ID)/im2p_gemmini_frontend_tsan_test
GEMMINI_FRONTEND_REAL_TEST = $(BUILD_DIR)/bin/$(GEMMINI_ARTIFACT_ID)/im2p_gemmini_frontend_real_test
GEMMINI_FRONTEND_ASAN_FLAGS = -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer
GEMMINI_FRONTEND_TSAN_FLAGS = -O1 -g -fsanitize=thread -fno-omit-frame-pointer

$(GEMMINI_DIM_CONFIG): $(GEMMINI_PARAMS_ROOT)/../gemmini_params.h
	@mkdir -p $(GEMMINI_DIM_CONFIG_DIR)
	sed 's/^#define DIM .*/#define DIM $(GEMMINI_FRONTEND_DIM)/' $< > $@

$(GEMMINI_FRONTEND_OBJECT): frontend/src/im2p_gemmini_frontend.cpp frontend/include/im2p_gemmini_frontend.hpp $(GEMMINI_DIM_CONFIG) | $(BUILD_DIR)/bin
	@mkdir -p $(dir $@)
	$(CXX) $(GEMMINI_FRONTEND_FLAGS) $(GEMMINI_FRONTEND_INCLUDES) -c $< -o $@

$(GEMMINI_FRONTEND_ARCHIVE): $(GEMMINI_FRONTEND_OBJECT) | $(BUILD_DIR)/lib
	@mkdir -p $(dir $@)
	rm -f $@
	$(AR) rcs $@ $<

$(GEMMINI_FRONTEND_TEST_OBJECT): frontend/src/im2p_gemmini_frontend.cpp frontend/include/im2p_gemmini_frontend.hpp frontend/tests/im2p_gemmini_frontend_testing.hpp $(GEMMINI_DIM_CONFIG) | $(BUILD_DIR)/bin
	@mkdir -p $(dir $@)
	$(CXX) $(GEMMINI_FRONTEND_FLAGS) -DIM2P_GEMMINI_FRONTEND_TESTING=1 \
		$(GEMMINI_FRONTEND_INCLUDES) -c $< -o $@

$(GEMMINI_FRONTEND_TEST_ARCHIVE): $(GEMMINI_FRONTEND_TEST_OBJECT) | $(BUILD_DIR)/lib
	@mkdir -p $(dir $@)
	rm -f $@
	$(AR) rcs $@ $<

gemmini-frontend: $(GEMMINI_FRONTEND_ARCHIVE) $(GEMMINI_FRONTEND_TEST_ARCHIVE)

# The public declaration surface compiles without any llama include directory.
gemmini-frontend-test: gemmini-frontend | $(BUILD_DIR)/bin
	@mkdir -p $(dir $(GEMMINI_FRONTEND_TEST))
	$(CXX) $(GEMMINI_FRONTEND_FLAGS) -Ifrontend/include -Isim/include \
		-c frontend/tests/forward_decl_compile.cpp \
		-o $(dir $(GEMMINI_FRONTEND_TEST))/im2p_gemmini_forward_decl.o
	$(CXX) $(GEMMINI_FRONTEND_FLAGS) -DIM2P_GEMMINI_FRONTEND_TESTING=1 \
		$(GEMMINI_FRONTEND_INCLUDES) frontend/tests/test_frontend.cpp \
		$(GEMMINI_FRONTEND_TEST_ARCHIVE) -o $(GEMMINI_FRONTEND_TEST)
	$(GEMMINI_FRONTEND_TEST)

# Sanitizer binaries compile the production frontend and its fake-ABI tests
# together so instrumentation covers ownership, workers, and teardown end to end.
gemmini-frontend-asan-test: $(GEMMINI_DIM_CONFIG) | $(BUILD_DIR)/bin
	@mkdir -p $(dir $(GEMMINI_FRONTEND_ASAN_TEST))
	$(CXX) $(GEMMINI_FRONTEND_FLAGS) $(GEMMINI_FRONTEND_ASAN_FLAGS) \
		-DIM2P_GEMMINI_FRONTEND_TESTING=1 $(GEMMINI_FRONTEND_INCLUDES) \
		frontend/src/im2p_gemmini_frontend.cpp frontend/tests/test_frontend.cpp \
		-o $(GEMMINI_FRONTEND_ASAN_TEST)
	@set -euo pipefail; \
	if test -n '$(FRONTEND_TEST_CASE)'; then \
	  ASAN_OPTIONS=halt_on_error=1 \
	  UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
	    $(GEMMINI_FRONTEND_ASAN_TEST) '$(FRONTEND_TEST_CASE)'; \
	else \
	  ASAN_OPTIONS=halt_on_error=1 \
	  UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
	    $(GEMMINI_FRONTEND_ASAN_TEST); \
	  for case_name in full_failure_matrix stripe_failure_matrix; do \
	    for iteration in $$(seq 1 20); do \
	      printf 'SANITIZER_ONE_SHOT case=%s iteration=%s/20\n' "$$case_name" "$$iteration"; \
	      ASAN_OPTIONS=halt_on_error=1 \
	      UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1 \
	        $(GEMMINI_FRONTEND_ASAN_TEST) "$$case_name"; \
	    done; \
	  done; \
	fi

gemmini-frontend-test-sanitized: gemmini-frontend-asan-test

gemmini-frontend-tsan-test: $(GEMMINI_DIM_CONFIG) | $(BUILD_DIR)/bin
	@mkdir -p $(dir $(GEMMINI_FRONTEND_TSAN_TEST))
	$(CXX) $(GEMMINI_FRONTEND_FLAGS) $(GEMMINI_FRONTEND_TSAN_FLAGS) \
		-DIM2P_GEMMINI_FRONTEND_TESTING=1 $(GEMMINI_FRONTEND_INCLUDES) \
		frontend/src/im2p_gemmini_frontend.cpp frontend/tests/test_frontend.cpp \
		-o $(GEMMINI_FRONTEND_TSAN_TEST)
	TSAN_OPTIONS=halt_on_error=1 $(GEMMINI_FRONTEND_TSAN_TEST) \
		$(if $(FRONTEND_TEST_CASE),$(FRONTEND_TEST_CASE),)

gemmini-frontend-real-test: gemmini-frontend-test verilator-int$(GEMMINI_FRONTEND_ACTIVATION_BITS)x$(GEMMINI_FRONTEND_DIM) | $(BUILD_DIR)/bin
	IM2P_REPO_ROOT=$(ROOT_DIR) IM2P_ACTIVATION_BITS=$(GEMMINI_FRONTEND_ACTIVATION_BITS) \
		IM2P_DIM=$(GEMMINI_FRONTEND_DIM) CARGO_TARGET_DIR=$(GEMMINI_CARGO_TARGET_DIR) cargo build \
		--manifest-path sim/Cargo.toml --lib --release
	$(CXX) $(GEMMINI_FRONTEND_FLAGS) -DIM2P_GEMMINI_FRONTEND_TESTING=1 \
		$(GEMMINI_FRONTEND_INCLUDES) frontend/tests/test_frontend_real.cpp \
		$(GEMMINI_FRONTEND_TEST_ARCHIVE) $(GEMMINI_CARGO_TARGET_DIR)/release/libim2p_sim.a \
		-o $(GEMMINI_FRONTEND_REAL_TEST)
	@mkdir -p $(GEMMINI_RESULTS_DIR)
	@set -o pipefail; $(GEMMINI_FRONTEND_REAL_TEST) 2>&1 | \
		tee $(GEMMINI_RESULTS_DIR)/frontend-real-test.log

gemmini-frontend-real-test-q8-h0: gemmini-frontend-real-test
	@set -o pipefail; $(GEMMINI_FRONTEND_REAL_TEST) --route q8_h0 2>&1 | \
		tee $(GEMMINI_RESULTS_DIR)/frontend-real-test-q8-h0.log

REAL_MATRIX_ROOT ?= $(abspath $(BUILD_DIR)/real-matrix)
REAL_MATRIX_RESULTS = $(REAL_MATRIX_ROOT)/results
REAL_MATRIX_PAIRS := 8:16 8:32 8:64
REAL_MATRIX_FINGERPRINT_FIXTURE_DIR ?=

# Build and execute each owned A8 pair in its own complete workspace. Forward,
# reverse, and concurrent passes each validate six DIM/mode identities, for
# eighteen real executions total. A4/A16 remain non-ExSIA and are not claimed.
gemmini-frontend-real-test-matrix:
	@set -euo pipefail; \
	root='$(REAL_MATRIX_ROOT)'; results='$(REAL_MATRIX_RESULTS)'; \
	mkdir -p "$$results"; \
	run_pair() { \
	  bits="$$1"; dim="$$2"; id="a$${bits}-w8-d$${dim}"; \
	  binary="$$root/$$id/bin/$$id/im2p_gemmini_frontend_real_test"; \
	  pair_results="$$root/$$id/results/$$id"; \
	  fingerprint_file="$$root/$$id/.real-matrix-input.sha256"; \
	  fixture_dir='$(REAL_MATRIX_FINGERPRINT_FIXTURE_DIR)'; \
	  fingerprint_command=($(PYTHON) scripts/real_matrix_fingerprint.py \
	    --bits "$$bits" --dim "$$dim" --gemmini-root '$(GEMMINI_ROOT)' \
	    --params-root '$(GEMMINI_PARAMS_ROOT)' \
	    --config 'CXX=$(CXX)' --config 'BSC=$(BSC)' \
	    --config 'VERILATOR=$(VERILATOR)' --config 'BSC_VERILOG=$(BSC_VERILOG)'); \
	  if test -n "$$fixture_dir" && test -f "$$fixture_dir/$$id"; then \
	    fingerprint_command+=(--extra-input "$$fixture_dir/$$id"); \
	  fi; \
	  fingerprint="$$("$${fingerprint_command[@]}")"; \
	  cached="$$(cat "$$fingerprint_file" 2>/dev/null || true)"; \
	  if test ! -x "$$binary" || test "$$cached" != "$$fingerprint"; then \
	    printf 'REAL_MATRIX_CACHE id=%s state=rebuild fingerprint=%s\n' "$$id" "$$fingerprint"; \
	    $(MAKE) --no-print-directory BUILD_DIR="$$root/$$id" \
	      IM2P_ACTIVATION_BITS="$$bits" IM2P_DIM="$$dim" \
	      GEMMINI_FRONTEND_ACTIVATION_BITS="$$bits" GEMMINI_FRONTEND_DIM="$$dim" \
	      gemmini-frontend-real-test; \
	    printf '%s\n' "$$fingerprint" > "$$fingerprint_file.tmp"; \
	    mv "$$fingerprint_file.tmp" "$$fingerprint_file"; \
	  else \
	    printf 'REAL_MATRIX_CACHE id=%s state=hit fingerprint=%s\n' "$$id" "$$fingerprint"; \
	    mkdir -p "$$pair_results"; \
	    set -o pipefail; "$$binary" 2>&1 | tee "$$pair_results/frontend-real-test.log"; \
	  fi; \
	}; \
	: > "$$results/forward.log"; \
	for pair in $(REAL_MATRIX_PAIRS); do \
	  bits="$${pair%%:*}"; dim="$${pair##*:}"; \
	  run_pair "$$bits" "$$dim" 2>&1 | tee -a "$$results/forward.log"; \
	done; \
	$(PYTHON) scripts/validate_real_matrix_log.py "$$results/forward.log"; \
	for pair in $(REAL_MATRIX_PAIRS); do \
	  bits="$${pair%%:*}"; dim="$${pair##*:}"; selected="a$${bits}-w8-d$${dim}"; \
	  before="$$results/isolation-$${selected}-before.sha256"; \
	  after="$$results/isolation-$${selected}-after.sha256"; \
	  find "$$root" -type f ! -path "$$root/$$selected/*" ! -path "$$results/*" \
	    -print0 | sort -z | xargs -0 shasum -a 256 > "$$before"; \
	  run_pair "$$bits" "$$dim" > "$$results/isolation-$${selected}.log" 2>&1; \
	  find "$$root" -type f ! -path "$$root/$$selected/*" ! -path "$$results/*" \
	    -print0 | sort -z | xargs -0 shasum -a 256 > "$$after"; \
	  cmp "$$before" "$$after"; \
	done; \
	: > "$$results/reverse.log"; \
	for pair in 8:64 8:32 8:16; do \
	  bits="$${pair%%:*}"; dim="$${pair##*:}"; \
	  run_pair "$$bits" "$$dim" 2>&1 | tee -a "$$results/reverse.log"; \
	done; \
	$(PYTHON) scripts/validate_real_matrix_log.py "$$results/reverse.log"; \
	pids=(); \
	for pair in $(REAL_MATRIX_PAIRS); do \
	  bits="$${pair%%:*}"; dim="$${pair##*:}"; id="a$${bits}-w8-d$${dim}"; \
	  (run_pair "$$bits" "$$dim" > "$$results/concurrent-$${id}.log" 2>&1) & \
	  pids+=("$$!"); \
	done; \
	for pid in "$${pids[@]}"; do wait "$$pid"; done; \
	for pair in $(REAL_MATRIX_PAIRS); do \
	  bits="$${pair%%:*}"; dim="$${pair##*:}"; id="a$${bits}-w8-d$${dim}"; \
	  $(PYTHON) scripts/validate_real_matrix_log.py \
	    "$$results/concurrent-$${id}.log" --bits "$$bits" --dim "$$dim"; \
	done; \
	printf 'REAL_MATRIX PASS distinct_identities=6 total_executions=18 forward=green reverse=green concurrent=green isolation=green root=%s\n' "$$root" | \
	  tee "$$results/summary.txt"

REAL_MISMATCH_ROOT ?= $(abspath $(BUILD_DIR)/real-matrix-mismatch)

gemmini-frontend-real-test-mismatch:
	@set -euo pipefail; \
	a16='$(REAL_MATRIX_ROOT)/a16-w8-d32'; \
	a8='$(REAL_MATRIX_ROOT)/a8-w8-d32'; \
	if test ! -f "$$a16/lib/a16-w8-d32/libim2p_gemmini_frontend_testing.a"; then \
	  $(MAKE) --no-print-directory BUILD_DIR="$$a16" \
	    IM2P_ACTIVATION_BITS=16 IM2P_DIM=32 \
	    GEMMINI_FRONTEND_ACTIVATION_BITS=16 GEMMINI_FRONTEND_DIM=32 gemmini-frontend; \
	fi; \
	if test ! -f "$$a8/cargo/a8-w8-d32/release/libim2p_sim.a"; then \
	  $(MAKE) --no-print-directory BUILD_DIR="$$a8" \
	    IM2P_ACTIVATION_BITS=8 IM2P_DIM=32 verilator-int8x32; \
	  IM2P_REPO_ROOT='$(ROOT_DIR)' IM2P_ACTIVATION_BITS=8 IM2P_DIM=32 \
	    CARGO_TARGET_DIR="$$a8/cargo/a8-w8-d32" cargo build \
	    --manifest-path sim/Cargo.toml --lib --release; \
	fi; \
	mkdir -p '$(REAL_MISMATCH_ROOT)/bin' '$(REAL_MISMATCH_ROOT)/results'; \
	$(CXX) -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror -pthread \
	  -DIM2P_GEMMINI_FRONTEND_EXPECTED_DIM=32 \
	  -DIM2P_GEMMINI_FRONTEND_ACTIVATION_BITS=16 \
	  -DIM2P_GEMMINI_FRONTEND_TESTING=1 \
	  -Ifrontend/include -Isim/include -I"$$a16/generated/a16-w8-d32" \
	  -I'$(GEMMINI_ROOT)/ggml/src/ggml-gemmini' \
	  -I'$(GEMMINI_ROOT)/ggml/src/ggml-gemmini-utils/include' \
	  -I'$(GEMMINI_ROOT)/ggml/include' -I'$(GEMMINI_ROOT)/ggml/src' \
	  -I'$(GEMMINI_PARAMS_ROOT)' frontend/tests/test_frontend_real.cpp \
	  "$$a16/lib/a16-w8-d32/libim2p_gemmini_frontend_testing.a" \
	  "$$a8/cargo/a8-w8-d32/release/libim2p_sim.a" \
	  -o '$(REAL_MISMATCH_ROOT)/bin/frontend-a16-simulator-a8'; \
	set -o pipefail; \
	'$(REAL_MISMATCH_ROOT)/bin/frontend-a16-simulator-a8' \
	  --expect-configuration-mismatch 2>&1 | \
	  tee '$(REAL_MISMATCH_ROOT)/results/mismatch.log'

bsv-test: | $(BUILD_DIR)/bsc/sim $(BUILD_DIR)/sim $(BUILD_DIR)/info $(BUILD_DIR)/bin
	@set -euo pipefail; \
	for top in $(BSV_TEST_TOPS); do \
	  package="$${top#mk}"; \
	  echo "[BSC] compile $$top"; \
	  $(BSC) -u -sim $(BSC_SIM_COMMON) -g $$top tests/$$package.bsv; \
	  $(BSC) -sim $(BSC_SIM_COMMON) -e $$top \
	    -o $(BUILD_DIR)/bin/$$package; \
	  echo "[Bluesim] run $$top"; \
	  $(run_bluesim); \
	done

bsv-test-one: | $(BUILD_DIR)/bsc/sim $(BUILD_DIR)/sim $(BUILD_DIR)/info $(BUILD_DIR)/bin
	@set -euo pipefail; \
	top='$(TOP)'; \
	test -n "$$top" || { echo 'TOP is required, e.g. TOP=mkTbPE' >&2; exit 2; }; \
	package="$${top#mk}"; \
	echo "[BSC] compile $$top"; \
	$(BSC) -u -sim $(BSC_SIM_COMMON) -g $$top tests/$$package.bsv; \
	$(BSC) -sim $(BSC_SIM_COMMON) -e $$top -o $(BUILD_DIR)/bin/$$package; \
	echo "[Bluesim] run $$top"; \
	$(run_bluesim)

rtl: | $(BUILD_DIR)/bsc $(BUILD_DIR)/info
	@set -euo pipefail; \
	for top in $(SYNTH_TOPS); do \
	  package="$${top#mk}"; \
	  out="$(BUILD_DIR)/rtl/$$package"; \
	  rm -rf "$$out"; \
	  mkdir -p "$$out"; \
	  echo "[BSC] Verilog $$top"; \
	  $(BSC) -u -verilog -p $(BSC_PATH) $(BSC_EXTRA_FLAGS) \
	    -bdir $(BUILD_DIR)/bsc -info-dir $(BUILD_DIR)/info \
	    -vdir "$$out" -g $$top synth/$$package.bsv; \
	done

rtl-one: | $(BUILD_DIR)/bsc $(BUILD_DIR)/info
	@set -euo pipefail; \
	top='$(TOP)'; \
	test -n "$$top" || { echo 'TOP is required, e.g. TOP=mkSynthInt8' >&2; exit 2; }; \
	package="$${top#mk}"; \
	out='$(RTL_OUT)'; \
	test -n "$$out" || out="$(BUILD_DIR)/rtl/$$package"; \
	bsc_dir='$(RTL_BSC_DIR)'; \
	test -n "$$bsc_dir" || bsc_dir="$(BUILD_DIR)/bsc"; \
	info_dir='$(RTL_INFO_DIR)'; \
	test -n "$$info_dir" || info_dir="$(BUILD_DIR)/info"; \
	rm -rf "$$out"; \
	mkdir -p "$$out" "$$bsc_dir" "$$info_dir"; \
	echo "[BSC] Verilog $$top"; \
	$(BSC) -u -verilog -p $(BSC_PATH) $(BSC_EXTRA_FLAGS) \
	  -bdir "$$bsc_dir" -info-dir "$$info_dir" \
	  -vdir "$$out" -g $$top synth/$$package.bsv

define define_sim_config
verilator-int$(1)x$(2):
	$$(MAKE) rtl-one TOP=mkSynthInt$(1)x$(2) \
	  RTL_OUT="$$(BUILD_DIR)/rtl/a$(1)-w8-d$(2)/SynthInt$(1)x$(2)" \
	  RTL_BSC_DIR="$$(BUILD_DIR)/bsc/a$(1)-w8-d$(2)" \
	  RTL_INFO_DIR="$$(BUILD_DIR)/info/a$(1)-w8-d$(2)"
	rm -rf "$$(BUILD_DIR)/verilator/a$(1)-w8-d$(2)/obj_dir"
	mkdir -p "$$(BUILD_DIR)/verilator/a$(1)-w8-d$(2)/obj_dir"
	$$(VERILATOR) $$(VERILATOR_COMMON) \
	  --Mdir "$$(BUILD_DIR)/verilator/a$(1)-w8-d$(2)/obj_dir" \
	  --top-module mkSynthInt$(1)x$(2) --prefix VmkSynthInt$(1)x$(2) \
	  "$$(BUILD_DIR)/rtl/a$(1)-w8-d$(2)/SynthInt$(1)x$(2)"/*.v \
	  "$$(BSC_VERILOG)/RegFile.v" "$$(BSC_VERILOG)/FIFO2.v"

sim-test-int$(1)x$(2): verilator-int$(1)x$(2)
	@mkdir -p "$$(BUILD_DIR)/results/a$(1)-w8-d$(2)"
	@set -o pipefail; \
	  IM2P_REPO_ROOT="$$(ROOT_DIR)" IM2P_ACTIVATION_BITS=$(1) IM2P_DIM=$(2) \
	  CARGO_TARGET_DIR="$$(abspath $$(BUILD_DIR)/cargo/a$(1)-w8-d$(2))" \
	  cargo test --manifest-path sim/Cargo.toml --tests --features test-hooks \
	    $$(CARGO_TEST_FILTER) -- --nocapture 2>&1 | \
	    tee "$$(BUILD_DIR)/results/a$(1)-w8-d$(2)/sim-test.log"
endef

$(foreach bits,4 8 16,$(foreach dim,16 32 64,$(eval $(call define_sim_config,$(bits),$(dim)))))

# Legacy aggregate entry points default to INT8 and select every supported dimension.
verilator: verilator-int$(IM2P_ACTIVATION_BITS)x16 verilator-int$(IM2P_ACTIVATION_BITS)x32 verilator-int$(IM2P_ACTIVATION_BITS)x64
sim-test: sim-test-int$(IM2P_ACTIVATION_BITS)x16 sim-test-int$(IM2P_ACTIVATION_BITS)x32 sim-test-int$(IM2P_ACTIVATION_BITS)x64

verilator-lint: rtl
	@set -euo pipefail; \
	for top in $(SYNTH_TOPS); do \
	  package="$${top#mk}"; \
	  echo "[Verilator] lint $$top"; \
	  $(VERILATOR) --lint-only -Wall -Wno-fatal \
	    --top-module $$top $(BUILD_DIR)/rtl/$$package/*.v \
	    "$(BSC_VERILOG)/RegFile.v" "$(BSC_VERILOG)/FIFO2.v"; \
	done

yosys-stat: rtl
	@set -euo pipefail; \
	mkdir -p $(BUILD_DIR)/reports; \
	for top in $(SYNTH_TOPS); do \
	  package="$${top#mk}"; \
	  report="$(BUILD_DIR)/reports/$$package-yosys-stat.txt"; \
	  files="$$(find $(BUILD_DIR)/rtl/$$package -maxdepth 1 -name '*.v' -print | sort | tr '\n' ' ')"; \
	  test -n "$$files" || { echo "no Verilog files for $$package" >&2; exit 1; }; \
	  $(YOSYS) -p "read_verilog $$files; hierarchy -check -top $$top; \
	    proc; opt; memory; opt; stat" | tee "$$report"; \
	done

check-tools:
	@for tool in $(PYTHON) $(CXX) $(BSC) $(VERILATOR) $(YOSYS); do \
	  if command -v $$tool >/dev/null 2>&1; then \
	    printf '[FOUND] %-12s %s\n' $$tool "$$(command -v $$tool)"; \
	  else \
	    printf '[MISSING] %s\n' $$tool; \
	  fi; \
	done

clean:
	rm -rf $(BUILD_DIR)
