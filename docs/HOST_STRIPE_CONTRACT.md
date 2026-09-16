# Host stripe and backing ownership

The current frontend's FULL and PIPELINE APIs submit logical work and immutable
accepted stripe views. `tile_I`/`tile_J` are tile counts, not byte extents; actual
row ranges and tail heights are explicit. `tile_K` metadata does not authorize a
new host-managed numerical continuation algorithm. Backend-specific decomposition
remains in its existing scheduling authority.

An accepted stripe's A backing, row stride, run/stripe identity and metadata stay
valid for the documented run/semantic-completion lifetime. Producer scratch
reuse and frontend credit release are separate from RTL backing ownership.
Backpressure does not consume the event. Failure is sticky; caller output is
published only after successful validation/fence. No numerical fallback is added.

The Gemmini C ABI runtime retains its existing two stripe slots and read/write
ownership; the pure planner does not invent stripe publication order or host
latency. Current host/API fixtures use the integrated controller and request
codecs. Fixed physical-board resident windows, UART packet-size experiments,
model capture/replay and deployment measurements are not current API contracts.

See [API/ABI](API_CONTRACT.md), [numerical semantics](NUMERICAL_CONTRACT.md),
[frontend lifecycle](../frontend/README.md), and
[the prepared cycle boundary](GEMMINI_CYCLE_SIM_BOUNDARY.md) for authorities and
backend distinctions. The retained legacy implementation has its own
[architecture](legacy/ARCHITECTURE.md) and [cycle conventions](legacy/RTL_CYCLE_ACCOUNTING.md).
