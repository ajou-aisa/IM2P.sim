# Current API and ABI boundaries

The public simulator ABI is `sim/include/im2p_sim.h`; low-level Verilator bridge
entry points are declared in `sim/ffi/im2p_verilator.h`. The optional C++ frontend
is `frontend/include/im2p_gemmini_frontend.hpp`. Those declarations and their
layout/lifecycle tests are authoritative; historical deployment protocols are not
part of this repository's current API ownership.

## Implementation selection

`IM2P_SIM_IMPLEMENTATION=LEGACY_BSV` retains the reference/default simulator.
`IM2P_SIM_IMPLEMENTATION=GEMMINI_HP1` selects the integrated upstream-WS backend
for matched A4/W4 or A8/W8 at DIM16/32/64. Admission depends on the selected
implementation and operation, not just a shared ABI version. An advertised legacy
route does not imply Gemmini support. The legacy matched ExSIA routes A4/Q4,
A8/Q8 and A16/Q16 remain documented and tested separately.

## Frontend lifetime and output

`execute`, `submit_stripe` and `fence` own the existing FULL/PIPELINE lifecycle.
Arguments copy selected scalar/identity metadata rather than materializing full
numerical tensors. Backing A/W/metadata and output lifetimes follow
[the frontend contract](../frontend/README.md). Accepted stripes keep their exact
row range, stride, identity and metadata until semantic completion. Backpressure
means an event was not accepted and the producer still owns it. Reusing producer
scratch is not permission to overwrite accepted backing bytes.

Failure is sticky and output remains transactional. Numerical/provider/compose
failure is not a request to switch to software or another accelerator. Dense and
residual contexts keep their existing ownership and independent timing domains.
No common CPU/NPU timeline is introduced.

## Numerical and transport units

ABI5 uint32 scale carriers, canonical element strides, derived RTL byte offsets,
output-domain distinction and fragment saturation are defined in
[the numerical contract](NUMERICAL_CONTRACT.md). Public provider output lanes use
signed64 transport; this must not be confused with HP1 signed32 arithmetic or a
local array partial width. Header/profile/implementation mismatches fail closed.

`fpga/gemmini_hp1/host/` is retained for host orchestration, codecs, request plans,
RMD composition and independent RTL fixtures. Its `uart.cpp` is a packet codec,
not a device-opening UART deployment implementation. Historical ABI labels such
as `PHYSICAL_HOST` in the no-simulator link audit and the external-executor build
macro remain compatibility names; they do not authorize or perform physical I/O.

## Validation and scope

C/C++ layout tests, Python build-contract tests, host CTest, integrated numerical
RTL fixtures and the retained legacy smoke cover different boundaries. The
Gemmini frontend-real stream-start failure is documented separately, not counted
as a successful integrated fixture. See [verification](VERIFICATION.md).
No physical transport, Chipyard SoC integration or new API is added by cleanup.
