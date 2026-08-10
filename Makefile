# -----------------------------------------------------------------------------
# IM2P.sim build / verification entry points
# -----------------------------------------------------------------------------

SHELL := /bin/bash

BSC       ?= bsc
CXX       ?= g++
PYTHON    ?= python3
VERILATOR ?= verilator
YOSYS     ?= yosys

BUILD_DIR := build
ROOT_DIR  := $(CURDIR)
BSC_PATH  := +:src/common:src/array:src/vector:src/accumulator:src/control:src/core:tests:synth
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
                  $(BSC_EXTRA_FLAGS) -Xc++ -O0

CPP_TOOL := $(BUILD_DIR)/bin/im2p_reference
CPP_SRC  := tools/im2p_reference.cpp

BSV_TEST_TOPS := \
	mkTbArithmetic \
	mkTbPE \
	mkTbInputSkew \
	mkTbSystolicArray \
	mkTbVectorUnit \
	mkTbAccumulator \
	mkTbExecuteController \
	mkTbIM2PCore \
	mkTbIM2PCoreGrouped \
	mkTbFloatCore \
	mkTbSynthInt8x16 \
	mkTbSynthInt8x32

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

.PHONY: all check verify static-check cpp-test bsv-test bsv-test-one rtl rtl-one \
        verilator-int8x16 verilator-int8x32 verilator sim-test-int8x16 \
        sim-test-int8x32 sim-test verilator-lint yosys-stat clean help check-tools

all: check

check: static-check cpp-test

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
	  'make verilator-int8x16 - 16x16 INT8 RTL의 Verilator model 생성' \
	  'make verilator-int8x32 - 32x32 INT8 RTL의 Verilator model 생성' \
	  'make verilator        - 두 INT8 Verilator model 생성' \
	  'make sim-test-int8x16 - 16x16 Rust RTL simulation test 실행' \
	  'make sim-test-int8x32 - 32x32 Rust RTL simulation test 실행' \
	  'make sim-test         - 두 Rust RTL simulation test 실행' \
	  'make verilator-lint  - 생성 Verilog에 Verilator lint 적용' \
	  'make yosys-stat      - 생성 Verilog에 Yosys generic synthesis/stat 적용' \
	  'make check-tools     - 외부 도구 설치 여부 확인' \
	  'make clean           - build/ 삭제'

$(BUILD_DIR)/bin $(BUILD_DIR)/bsc $(BUILD_DIR)/bsc/sim $(BUILD_DIR)/sim $(BUILD_DIR)/info:
	@mkdir -p $@

static-check:
	$(PYTHON) scripts/static_check.py

$(CPP_TOOL): $(CPP_SRC) Makefile | $(BUILD_DIR)/bin
	$(CXX) -std=c++20 -O2 -Wall -Wextra -Wpedantic -Werror \
		$(CPP_SRC) -o $(CPP_TOOL)

cpp-test: $(CPP_TOOL)
	$(CPP_TOOL)

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
	out="$(BUILD_DIR)/rtl/$$package"; \
	rm -rf "$$out"; \
	mkdir -p "$$out"; \
	echo "[BSC] Verilog $$top"; \
	$(BSC) -u -verilog -p $(BSC_PATH) $(BSC_EXTRA_FLAGS) \
	  -bdir $(BUILD_DIR)/bsc -info-dir $(BUILD_DIR)/info \
	  -vdir "$$out" -g $$top synth/$$package.bsv

verilator-int8x16:
	@set -euo pipefail; \
	$(MAKE) rtl-one TOP=mkSynthInt8x16; \
	out="$(BUILD_DIR)/verilator/int8x16/obj_dir"; \
	mkdir -p "$$out"; \
	$(VERILATOR) $(VERILATOR_COMMON) --Mdir "$$out" \
	  --top-module mkSynthInt8x16 --prefix VmkSynthInt8x16 \
	  "$(BUILD_DIR)/rtl/SynthInt8x16/mkSynthInt8x16.v" \
	  "$(BSC_VERILOG)/RegFile.v" "$(BSC_VERILOG)/FIFO2.v"

verilator-int8x32:
	@set -euo pipefail; \
	$(MAKE) rtl-one TOP=mkSynthInt8x32; \
	out="$(BUILD_DIR)/verilator/int8x32/obj_dir"; \
	mkdir -p "$$out"; \
	$(VERILATOR) $(VERILATOR_COMMON) --Mdir "$$out" \
	  --top-module mkSynthInt8x32 --prefix VmkSynthInt8x32 \
	  "$(BUILD_DIR)/rtl/SynthInt8x32/mkSynthInt8x32.v" \
	  "$(BSC_VERILOG)/RegFile.v" "$(BSC_VERILOG)/FIFO2.v"

verilator: verilator-int8x16 verilator-int8x32

sim-test-int8x16: verilator-int8x16
	IM2P_REPO_ROOT="$(ROOT_DIR)" IM2P_DIM=16 \
	  cargo test --manifest-path sim/Cargo.toml --test rtl_tile -- --nocapture

sim-test-int8x32: verilator-int8x32
	IM2P_REPO_ROOT="$(ROOT_DIR)" IM2P_DIM=32 \
	  cargo test --manifest-path sim/Cargo.toml --test rtl_tile -- --nocapture

sim-test: sim-test-int8x16 sim-test-int8x32

verilator-lint: rtl
	@set -euo pipefail; \
	for top in $(SYNTH_TOPS); do \
	  package="$${top#mk}"; \
	  echo "[Verilator] lint $$top"; \
	  $(VERILATOR) --lint-only -Wall -Wno-fatal \
	    --top-module $$top $(BUILD_DIR)/rtl/$$package/*.v; \
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
