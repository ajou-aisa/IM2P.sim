SHELL := /usr/bin/env bash
.DEFAULT_GOAL := test

BSC ?= bsc
CXX ?= g++
CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -Wpedantic

CASE ?= mixed
CASES := int8 carry lane-gap lane4 multi-k mixed

BUILD_ROOT := build
GEN_ROOT := generated
NPU_DIR := src/NPU
TEST_DIR := tests
TOOL_DIR := tools
BUILD_DIR := $(BUILD_ROOT)/$(CASE)
GEN_DIR := $(GEN_ROOT)/$(CASE)
CPP := $(BUILD_ROOT)/tools/decomposed_spmm
CPP_SOURCE := $(TOOL_DIR)/decomposed_spmm.cpp
TOP := mkTbDecomposedSpMM
DECOMPOSED_TB := $(TEST_DIR)/TbDecomposedSpMM.bsv
SCALE_TB := $(TEST_DIR)/TbAccumulatorScale.bsv
SYSTOLIC_TB := $(TEST_DIR)/TbSystolicArray.bsv
NUMERIC_TB := $(TEST_DIR)/TbNumericFormat.bsv
GEN_BSV := $(GEN_DIR)/GeneratedDecomposedData.bsv
SCALE_BUILD_DIR := $(BUILD_ROOT)/scale
SCALE_TOP := mkTbAccumulatorScale
SYSTOLIC_BUILD_DIR := $(BUILD_ROOT)/systolic
SYSTOLIC_TOP := mkTb

BSC_FLAGS := -p +:$(NPU_DIR):$(GEN_DIR) -sim -check-assert \
	-bdir $(BUILD_DIR) \
	-simdir $(BUILD_DIR) \
	-info-dir $(BUILD_DIR)

BSV_SOURCES := \
	$(NPU_DIR)/NumericFormat.bsv \
	$(NPU_DIR)/PE.bsv \
	$(NPU_DIR)/SystolicArray.bsv \
	$(NPU_DIR)/Accumulator.bsv \
	$(GEN_BSV) \
	$(DECOMPOSED_TB)

.PHONY: all test test-all scale-test systolic-test numeric-test run run-case run-verbose \
	self-test case-self-test list-cases generate clean

all: $(BUILD_DIR)/sim

test: self-test scale-test systolic-test numeric-test run-case

test-all: self-test scale-test systolic-test numeric-test
	@set -e; \
	for tc in $(CASES); do \
		$(MAKE) --no-print-directory run-case CASE=$$tc; \
	done

$(CPP): $(CPP_SOURCE)
	mkdir -p $(dir $@)
	$(CXX) $(CXXFLAGS) $< -o $@

list-cases: $(CPP)
	$(CPP) list-cases

self-test: $(CPP)
	$(CPP) self-test all

case-self-test: $(CPP)
	$(CPP) self-test $(CASE)

$(GEN_BSV): $(CPP)
	mkdir -p $(GEN_DIR)
	$(CPP) generate $(GEN_DIR) $(CASE)

generate: $(GEN_BSV)

$(BUILD_DIR):
	mkdir -p $@

$(BUILD_DIR)/sim: $(BSV_SOURCES) | $(BUILD_DIR)
	$(BSC) $(BSC_FLAGS) -u -g $(TOP) $(DECOMPOSED_TB)
	$(BSC) $(BSC_FLAGS) -e $(TOP) -o $@

$(SCALE_BUILD_DIR)/sim: $(NPU_DIR)/NumericFormat.bsv $(NPU_DIR)/Accumulator.bsv $(SCALE_TB)
	mkdir -p $(SCALE_BUILD_DIR)
	$(BSC) -p +:$(NPU_DIR) -sim -check-assert -bdir $(SCALE_BUILD_DIR) \
		-simdir $(SCALE_BUILD_DIR) -info-dir $(SCALE_BUILD_DIR) \
		-u -g $(SCALE_TOP) $(SCALE_TB)
	$(BSC) -p +:$(NPU_DIR) -sim -check-assert -bdir $(SCALE_BUILD_DIR) \
		-simdir $(SCALE_BUILD_DIR) -info-dir $(SCALE_BUILD_DIR) \
		-e $(SCALE_TOP) -o $@

scale-test: $(SCALE_BUILD_DIR)/sim
	@$(SCALE_BUILD_DIR)/sim

$(SYSTOLIC_BUILD_DIR)/sim: $(NPU_DIR)/NumericFormat.bsv $(NPU_DIR)/PE.bsv $(NPU_DIR)/SystolicArray.bsv $(SYSTOLIC_TB)
	mkdir -p $(SYSTOLIC_BUILD_DIR)
	$(BSC) -p +:$(NPU_DIR) -sim -check-assert -bdir $(SYSTOLIC_BUILD_DIR) \
		-simdir $(SYSTOLIC_BUILD_DIR) -info-dir $(SYSTOLIC_BUILD_DIR) \
		-u -g $(SYSTOLIC_TOP) $(SYSTOLIC_TB)
	$(BSC) -p +:$(NPU_DIR) -sim -check-assert -bdir $(SYSTOLIC_BUILD_DIR) \
		-simdir $(SYSTOLIC_BUILD_DIR) -info-dir $(SYSTOLIC_BUILD_DIR) \
		-e $(SYSTOLIC_TOP) -o $@

systolic-test: $(SYSTOLIC_BUILD_DIR)/sim
	@$(SYSTOLIC_BUILD_DIR)/sim

numeric-test: $(NPU_DIR)/NumericFormat.bsv $(NPU_DIR)/PE.bsv $(NUMERIC_TB)
	mkdir -p $(BUILD_ROOT)/numeric
	$(BSC) -p +:$(NPU_DIR) -sim -check-assert -bdir $(BUILD_ROOT)/numeric \
		-simdir $(BUILD_ROOT)/numeric -info-dir $(BUILD_ROOT)/numeric \
		-u -g mkTbNumericFormat $(NUMERIC_TB)
	$(BSC) -p +:$(NPU_DIR) -sim -check-assert -bdir $(BUILD_ROOT)/numeric \
		-simdir $(BUILD_ROOT)/numeric -info-dir $(BUILD_ROOT)/numeric \
		-e mkTbNumericFormat -o $(BUILD_ROOT)/numeric/sim
	@$(BUILD_ROOT)/numeric/sim

run: run-case

run-case: $(BUILD_DIR)/sim $(CPP)
	@echo
	@echo "=== BSV CASE: $(CASE) ==="
	@$(BUILD_DIR)/sim > $(BUILD_DIR)/bsv.log
	@grep -E '^(TEST_BEGIN|JOB_FAIL|TEST_END|DECOMPOSED SPMM: FAIL)' \
		$(BUILD_DIR)/bsv.log || true
	@$(CPP) compare $(BUILD_DIR)/bsv.log $(CASE)
	@echo "raw RTL rows: $(BUILD_DIR)/bsv.log"

run-verbose: $(BUILD_DIR)/sim $(CPP)
	@echo
	@echo "=== BSV CASE: $(CASE) (verbose) ==="
	@set -o pipefail; $(BUILD_DIR)/sim | tee $(BUILD_DIR)/bsv.log
	@$(CPP) compare $(BUILD_DIR)/bsv.log $(CASE)

clean:
	rm -rf $(BUILD_ROOT) $(GEN_ROOT)
