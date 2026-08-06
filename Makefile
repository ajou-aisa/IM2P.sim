BSC ?= bsc
BUILD_DIR := build
BSC_FLAGS := -p +:NPU:Tensor -sim \
	-bdir $(BUILD_DIR) \
	-simdir $(BUILD_DIR) \
	-info-dir $(BUILD_DIR)
SOURCES := TbSystolicArray.bsv NPU/PE.bsv NPU/SystolicArray.bsv

.PHONY: all run clean

all: $(BUILD_DIR)/sim

$(BUILD_DIR):
	mkdir -p $@

$(BUILD_DIR)/sim: $(SOURCES) | $(BUILD_DIR)
	$(BSC) $(BSC_FLAGS) -u -g mkTb TbSystolicArray.bsv
	$(BSC) $(BSC_FLAGS) -e mkTb -o $@

run: $(BUILD_DIR)/sim
	$<

clean:
	rm -rf $(BUILD_DIR)
