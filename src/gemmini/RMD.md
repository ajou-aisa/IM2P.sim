# Dense HP1 and RMD

The default `--top integrated` uses the original WS controllers and one physical mesh for both work kinds. `DENSE_HP1_FINAL` retains fragment-scale Sat32 arithmetic. `RMD_RAW` accepts one compact original weight block with K in 1..32; its immutable output context selects SCU shift zero. Native A4/A8 raw sums fit INT32. The CPU retains block-scale, balanced-radix and checked INT64 correction composition, including the full INT32 9/5/3-lane software contract.

`fpga/gemmini_hp1/host/rmd.cpp` adapts the existing RMD packet executor to a borrowed raw execution callback. `ggml_gemmini_hp1_bind_executor` connects the selected CAP to dense FULL/PIPELINE and raw execution. RMD ON requires the resolved `rmd_raw` capability and `rmd-raw-k32-cpu-compose-v1` revision. Unbound physical execution fails before device access; Linux/device validation remains a separate deployment step.

Run `scripts/gemmini_build.sh --matrix a4w4,a8w8 --dims 16,32,64 --scu hp1-left-shift --memory-contract-dir config/gemmini_host_memory_contracts --stage host-test --out NEW_DIRECTORY` for the same generated RTL's dense and RMD integration suites. The standalone custom-FSM top remains an explicit diagnostic.
