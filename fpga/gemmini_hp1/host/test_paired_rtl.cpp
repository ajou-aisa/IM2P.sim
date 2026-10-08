// Paired-microtile RTL fixture (P3, P4). It drives the HP1 top's pair descriptor fields directly and
// is the only pairing-on path until P5: the C API keeps rejecting non-empty companions. Residual
// A sits in the slot A object after Main's A and a nonzero gap, so test_ws_rtl.cpp serves LdR
// unchanged. Checked: Main output, the µT trace, work IDs, the LdR reads, the gathered W rows and
// the residual rows fed to the mesh. Residual output is not readable before StR (P5).
#define main legacy_ws_rtl_main
#include "test_ws_rtl.cpp"
#undef main

#include "gemmini_paired_schedule.hpp"

#include <map>
#include <set>

namespace {
namespace pairing = im2p::gemmini::paired;
// kind (0 Main µT, 1 residual µT, 2 residual W row), fragment, ACC row, work ID, W row.
using Beat = std::array<std::uint64_t, 5>;

std::vector<Beat> traced;
// Residual µT and indexed-W beats matched so far (shows a paired case was not vacuous).
std::size_t residual_uts = 0, residual_w_rows = 0;
// P4 observation of the loop in flight: gathered D reads (row, row - wBase), residual mesh rows,
// and accepted load DMA requests (vaddr, local address, columns).
struct MeshBeat {
  std::uint64_t row;
  std::vector<std::uint8_t> data;
};
struct DmaBeat {
  std::uint64_t vaddr, laddr, cols;
};
std::vector<std::pair<std::uint64_t, std::uint64_t>> gathered;
std::vector<MeshBeat> mesh_a, mesh_d;
std::vector<DmaBeat> dma;
std::size_t gather_rows = 0, ldr_rows = 0, mesh_rows = 0;

template <typename Signal> std::vector<std::uint8_t> row_bytes(const Signal &signal) {
  std::vector<std::uint8_t> bytes(sp_bytes);
  for (std::size_t i = 0; i < sp_bytes; ++i) bytes[i] = byte_at(signal, i);
  return bytes;
}

void trace(Adapter &state) {
  auto &dut = state.dut;
  if (dut.io_pairTrace_valid)
    traced.push_back({dut.io_pairTrace_bits_kind, dut.io_pairTrace_bits_fragmentId,
                      dut.io_pairTrace_bits_accRow, dut.io_pairTrace_bits_workId,
                      dut.io_pairTrace_bits_wRow});
  if (dut.io_gatherRead_valid)
    gathered.push_back({dut.io_gatherRead_bits_row, dut.io_gatherRead_bits_relative});
  if (dut.io_meshA_valid) mesh_a.push_back({dut.io_meshA_bits_row, row_bytes(dut.io_meshA_bits_data)});
  if (dut.io_meshD_valid) mesh_d.push_back({dut.io_meshD_bits_row, row_bytes(dut.io_meshD_bits_data)});
  if (dut.io_events_loadDmaAccepted)
    dma.push_back({dut.io_events_loadVaddrBytes, dut.io_events_loadLocalAddressRaw,
                   dut.io_events_loadColumnsElements});
}

void clear_observation() {
  traced.clear();
  gathered.clear();
  mesh_a.clear();
  mesh_d.clear();
  dma.clear();
}

void require(bool condition, const std::string &message) {
  if (!condition)
    throw std::runtime_error(message);
}

struct Operands {
  std::size_t m = 0, n = 0, k = 0;
  std::vector<std::int8_t> a, w;
  std::vector<std::uint32_t> carriers; // one row per K32 block
};

Operands make_operands(std::size_t m, std::size_t n, std::size_t k, std::uint64_t seed) {
  Operands o{m, n, k, {}, {}, {}};
  auto next = [&] {
    seed = seed * 6364136223846793005ULL + 1442695040888963407ULL;
    return static_cast<std::uint32_t>(seed >> 33);
  };
  // Valid for A4/W4 and A8/W8.
  o.a.resize(m * k);
  o.w.resize(k * n);
  for (auto &x : o.a) x = static_cast<std::int8_t>(static_cast<int>(next() % 16) - 8);
  for (auto &x : o.w) x = static_cast<std::int8_t>(static_cast<int>(next() % 16) - 8);
  o.carriers.resize((k + 31) / 32 * n);
  for (auto &x : o.carriers) {
    const auto r = next() % 8;
    x = r == 7 ? 0x80000000u : r;
  }
  return o;
}

// One stripe's compact residual activations (M_R x compact_k), signed and valid for A4 and A8.
std::vector<std::int8_t> make_residual_a(std::size_t rows, std::size_t compact_k, std::uint64_t seed) {
  std::vector<std::int8_t> a(rows * compact_k);
  for (auto &x : a) {
    seed = seed * 6364136223846793005ULL + 1442695040888963407ULL;
    x = static_cast<std::int8_t>(static_cast<int>((seed >> 33) % 16) - 8);
  }
  return a;
}

std::size_t nth_bit(std::uint32_t mask, std::size_t position) {
  for (std::size_t bit = 0; bit < 32; ++bit)
    if ((mask >> bit & 1U) && position-- == 0) return bit;
  throw std::runtime_error("residual mask has too few bits");
}

std::int32_t reference(const Operands &o, std::size_t row, std::size_t column) {
  namespace hp1 = ggml::gemmini::quants::hp1;
  const auto chunk = std::min<std::size_t>(dim, 32);
  std::int32_t acc = 0;
  for (std::size_t begin = 0; begin < o.k; begin += chunk) {
    std::int32_t raw = 0;
    for (auto kk = begin; kk < std::min(o.k, begin + chunk); ++kk)
      raw += std::int32_t{o.a[row * o.k + kk]} * o.w[kk * o.n + column];
    acc = hp1::accumulate(acc, hp1::apply_validated(raw, o.carriers[begin / 32 * o.n + column]));
  }
  return acc;
}

std::vector<Beat> expected_beats(const pairing::PairPlan &plan, const pairing::PairedLoop &loop,
                                 std::uint64_t work_base) {
  std::vector<Beat> beats;
  for (std::size_t ordinal = 0; ordinal < loop.micro_count; ++ordinal) {
    const auto micro = pairing::micro_context(plan, loop, ordinal);
    const bool main = micro.kind == pairing::MicroKind::main;
    beats.push_back({main ? 0u : 1u, micro.fragment_id, micro.acc_row, work_base + micro.work_id,
                     main ? pairing::w_row(plan, loop, micro, 0) : 0u});
    for (std::size_t row = 0; !main && row < micro.fragment.reduction; ++row)
      beats.push_back({2u, micro.fragment_id, micro.acc_row, work_base + micro.work_id,
                       pairing::w_row(plan, loop, micro, row)});
  }
  return beats;
}

std::size_t work_entries(Adapter &state) { return state.dut.io_workEntries; }

constexpr std::size_t gap_rows = 2;

struct ResidualLayout {
  std::uint64_t a_address = 0, main_bytes = 0, gap_bytes = 0, ra_address = 0, ra_bytes = 0, stride = 0;
};

// The slot A object holds Main's packed A, then (for a loop with a residual) a nonzero gap and the
// loop's packed residual A in residual_activation_read's layout, which LdR reads.
ResidualLayout place_a(Adapter &state, const Operands &o, const pairing::PairPlan &plan,
                       const pairing::PairedLoop &loop, std::size_t slot,
                       const std::vector<std::int8_t> &residual_a) {
  const auto &l = loop.main;
  ResidualLayout layout;
  layout.a_address = slot_address(a_base, slot);
  layout.main_bytes = l.activation_packed_bytes;
  auto &a = state.a[slot];
  a.assign(layout.main_bytes, 0);
  for (std::size_t i = 0; i < l.is; ++i)
    for (std::size_t kk = 0; kk < l.ks; ++kk)
      put_operand(a, i * l.kp + kk, o.a[(l.i + i) * o.k + l.k + kk]);
  if (!loop.has_residual) return layout;
  layout.gap_bytes = gap_rows * sp_bytes;
  layout.ra_address = layout.a_address + layout.main_bytes + layout.gap_bytes;
  layout.ra_bytes = loop.residual.activation_packed_bytes;
  layout.stride = loop.residual.kp * IM2P_OPERAND_BITS / 8;
  a.resize(layout.main_bytes + layout.gap_bytes, 0x5A);
  a.resize(layout.main_bytes + layout.gap_bytes + layout.ra_bytes, 0);
  const auto base = (layout.main_bytes + layout.gap_bytes) * 8 / IM2P_OPERAND_BITS;
  for (std::uint64_t offset = 0; offset < layout.ra_bytes; offset += sp_bytes) {
    const auto extent = pairing::residual_activation_read(plan, loop, offset);
    require(extent.valid, "residual A packed offset outside residual_activation_read");
    for (std::uint32_t e = 0; e < extent.element_count; ++e)
      put_operand(a, base + offset * 8 / IM2P_OPERAND_BITS + e, residual_a.at(extent.byte_offset + e));
  }
  return layout;
}

// One paired loop's data movement: backing reads, LdR rows, gathered W rows and the residual rows
// fed to the mesh, all against the planner (residual_activation_read, micro_context, w_row).
void check_residual_loop(Adapter &state, const Operands &o, const pairing::PairPlan &plan,
                         const pairing::PairedLoop &loop, std::size_t slot,
                         const std::vector<std::int8_t> &residual_a, const ResidualLayout &layout,
                         std::size_t reads_before, const std::string &label) {
  const auto b_address = slot_address(b_base, slot), s_address = slot_address(s_base, slot);
  for (auto i = reads_before; i < state.accepted_read_addresses.size(); ++i) {
    const auto address = state.accepted_read_addresses[i];
    const bool main_a = address >= layout.a_address && address < layout.a_address + layout.main_bytes;
    const bool gap = address >= layout.a_address + layout.main_bytes && address < layout.ra_address;
    const bool ra = address >= layout.ra_address && address < layout.ra_address + layout.ra_bytes;
    const bool b = address >= b_address && address < b_address + state.b[slot].size();
    const bool scale = address >= s_address && address < s_address + state.s[slot].size();
    require(!gap, label + ": backing read in the gap before residual A");
    require(main_a || ra || b || scale, label + ": backing read outside Main A, residual A, B and S");
  }
  const auto sp_rows = std::size_t{4} * IM2P_BANK_ROWS;
  std::uint64_t a_addr_start = ~std::uint64_t{0};
  for (const auto &beat : dma)
    if (beat.vaddr >= layout.a_address && beat.vaddr < layout.a_address + layout.main_bytes)
      a_addr_start = std::min<std::uint64_t>(a_addr_start, beat.laddr & (sp_rows - 1));
  require(a_addr_start != ~std::uint64_t{0}, label + ": no Main A load observed");
  const auto &l = loop.main;
  const auto chunks = loop.residual.kp / dim;
  std::map<std::uint64_t, unsigned> offsets;
  for (const auto &beat : dma) {
    if (beat.vaddr < layout.ra_address || beat.vaddr >= layout.ra_address + layout.ra_bytes) continue;
    const auto offset = beat.vaddr - layout.ra_address;
    require(offset % sp_bytes == 0 && pairing::residual_activation_read(plan, loop, offset).valid,
            label + ": LdR read is not a packed residual A row");
    ++offsets[offset];
    const auto row = offset / layout.stride, chunk = offset % layout.stride / sp_bytes;
    require((beat.laddr & (sp_rows - 1)) ==
                a_addr_start + (l.ip / dim) * (l.kp / dim) * dim + (row / dim * chunks + chunk) * dim + row % dim,
            label + ": LdR row landed outside its residual A region");
    require(beat.cols == dim, label + ": LdR row is not one full fragment row");
  }
  require(offsets.size() == layout.ra_bytes / sp_bytes &&
              std::all_of(offsets.begin(), offsets.end(), [](const auto &e) { return e.second == 1; }),
          label + ": LdR did not read every residual A row exactly once");
  ldr_rows += offsets.size();

  std::vector<std::vector<MeshBeat>> a_passes;
  for (const auto &beat : mesh_a) {
    if (beat.row == 0 || a_passes.empty()) a_passes.emplace_back();
    a_passes.back().push_back(beat);
  }
  const auto &run = plan.residual.runs[loop.run_index];
  std::vector<std::pair<std::uint64_t, std::uint64_t>> expected_gather;
  std::size_t computes = 0, preloads = 0;
  for (auto ordinal = l.fragment_count; ordinal < loop.micro_count; ++ordinal) {
    const auto micro = pairing::micro_context(plan, loop, ordinal);
    const auto &f = micro.fragment;
    require(computes < a_passes.size(), label + ": missing residual mesh A pass");
    for (const auto &beat : a_passes[computes]) {
      std::vector<std::uint8_t> want(sp_bytes, 0);
      if (beat.row < f.rows)
        for (std::size_t lane = 0; lane < f.reduction; ++lane)
          put_operand(want, lane, residual_a.at((f.i + beat.row) * plan.residual.shape.k + f.k + lane));
      require(beat.data == want, label + ": residual mesh A row " + std::to_string(beat.row) + " differs");
      mesh_rows += beat.row < f.rows;
    }
    ++computes;
    if (f.i != loop.residual.i) continue; // groups after the first reuse the preloaded W
    for (std::size_t r = f.reduction; r-- > 0;)
      expected_gather.push_back({r, pairing::w_row(plan, loop, micro, r)});
    require((preloads + 1) * dim <= mesh_d.size(), label + ": missing residual mesh D pass");
    for (std::size_t n = 0; n < dim; ++n) {
      const auto &beat = mesh_d[preloads * dim + n];
      if (beat.row != dim - 1 - n) {
        std::string rows;
        for (const auto &b : mesh_d) rows += " " + std::to_string(b.row);
        require(false, label + ": residual mesh D rows out of order at pass " + std::to_string(preloads) +
                           " (" + std::to_string(mesh_d.size()) + " rows:" + rows + ")");
      }
      std::vector<std::uint8_t> want(sp_bytes, 0);
      if (beat.row < f.reduction) {
        const auto k = std::size_t{run.original_block_id} * 32 +
                       nth_bit(run.original_k_mask, f.k - run.compact_k_begin + beat.row);
        for (std::size_t lane = 0; lane < f.columns; ++lane) put_operand(want, lane, o.w[k * o.n + f.j + lane]);
      }
      require(beat.data == want, label + ": residual mesh D row " + std::to_string(beat.row) + " differs");
      mesh_rows += beat.row < f.reduction;
    }
    ++preloads;
  }
  require(a_passes.size() == computes, label + ": extra residual mesh A passes");
  require(mesh_d.size() == preloads * dim, label + ": extra residual mesh D rows");
  require(gathered == expected_gather, label + ": gathered W rows differ from w_row");
  gather_rows += gathered.size();
}

// Runs one stripe of `plan` in host slot `slot`; returns its Main output (rows x n). Checks the
// µT trace against micro_context/w_row and the completed work IDs of every tile group.
std::vector<std::int32_t> run_stripe(Adapter &state, const Operands &o,
                                     const pairing::PairPlan &plan, std::size_t slot,
                                     std::uint32_t &generation, const std::string &label,
                                     const std::vector<std::int8_t> &residual_a = {}) {
  auto &dut = state.dut;
  const auto entries = work_entries(state);
  const std::uint64_t work_base = slot * (entries / 2);
  const auto rows = plan.row_end - plan.row_begin;
  state.active_slot = slot;
  state.rows[slot] = rows;
  state.columns[slot] = o.n;
  state.c_stride[slot] = padded(o.n) * 4;
  state.c[slot].assign(padded(rows) * state.c_stride[slot], 0xA5);
  state.written[slot].assign(state.c[slot].size(), 0);
  std::set<std::uint64_t> expected_ids;
  auto cursor = pairing::first_cursor(plan);
  bool last = false;
  std::size_t index = 0;
  do {
    const auto loop = pairing::plan_paired_loop(plan, cursor);
    const auto &l = loop.main;
    const auto ip = l.ip, jp = l.jp, kp = l.kp;
    if (l.k == 0) {
      state.output_ids.assign(entries, false);
      expected_ids.clear();
    }
    const auto layout = place_a(state, o, plan, loop, slot, residual_a);
    state.b[slot].assign(l.weight_packed_bytes, 0);
    state.s[slot].assign(l.scale_packed_bytes, 0);
    for (std::size_t kk = 0; kk < l.ks; ++kk)
      for (std::size_t j = 0; j < l.js; ++j)
        put_operand(state.b[slot], kk * jp + j, o.w[(l.k + kk) * o.n + l.j + j]);
    const auto max_j = jp / dim;
    for (std::size_t row = 0; row < l.scale_rows; ++row)
      for (std::size_t lane = 0; lane < dim && row % max_j * dim + lane < l.js; ++lane) {
        const auto block = l.scale_first_block + row / max_j;
        const auto carrier = o.carriers[block * o.n + l.j + row % max_j * dim + lane];
        for (std::size_t byte = 0; byte < 4; ++byte)
          state.s[slot][row * acc_bytes + lane * 4 + byte] =
              static_cast<std::uint8_t>(carrier >> (byte * 8));
      }
    const auto fields = pairing::pair_fields(plan, loop);
    dut.io_work_bits_maxI = ip / dim;
    dut.io_work_bits_maxJ = jp / dim;
    dut.io_work_bits_maxK = kp / dim;
    dut.io_work_bits_padI = ip - l.is;
    dut.io_work_bits_padJ = jp - l.js;
    dut.io_work_bits_padK = kp - l.ks;
    dut.io_work_bits_aAddress = slot_address(a_base, slot);
    dut.io_work_bits_residualAAddress = layout.ra_address;
    dut.io_work_bits_residualAStrideBytes = layout.stride;
    dut.io_work_bits_bAddress = slot_address(b_base, slot);
    dut.io_work_bits_cAddress =
        l.final_contribution ? slot_address(c_base, slot) +
                                   (l.i - plan.row_begin) * state.c_stride[slot] + l.j * 4
                             : 0;
    dut.io_work_bits_scaleBackingAddress = slot_address(s_base, slot);
    dut.io_work_bits_aStrideBytes = kp * IM2P_OPERAND_BITS / 8;
    dut.io_work_bits_bStrideBytes = jp * IM2P_OPERAND_BITS / 8;
    dut.io_work_bits_cStrideBytes = state.c_stride[slot];
    dut.io_work_bits_scaleBase = slot * 128;
    dut.io_work_bits_scaleGeneration = generation;
    dut.io_work_bits_fragmentBase = l.fragment_base;
    dut.io_work_bits_workBase = work_base;
    dut.io_work_bits_accumulate = l.accumulate;
    dut.io_work_bits_finalFragment = l.final_contribution;
    dut.io_work_bits_firstLoop = l.first;
    dut.io_work_bits_finalLoop = l.last;
    dut.io_work_bits_logicalWorkId = generation & 255U;
    dut.io_work_bits_hostSlot = slot;
    dut.io_work_bits_rmdRaw = 0;
    dut.io_work_bits_paired = fields.paired;
    dut.io_work_bits_residualMask = fields.mask;
    dut.io_work_bits_residualCompactBegin = fields.compact_begin;
    dut.io_work_bits_residualGroups = fields.groups;
    dut.io_work_bits_residualPadI = fields.pad_i;
    dut.io_work_bits_residualAccTop = fields.acc_top;
    dut.io_work_bits_residualWorkOffset = fields.work_offset;
    dut.io_work_bits_residualFirstRun = fields.first_run;
    dut.io_work_bits_residualFinalRun = fields.final_run;
    const auto reads_before = state.accepted_read_addresses.size();
    clear_observation();
    dut.io_work_valid = 1;
    state.accept([&] { return dut.io_work_ready; }, "paired descriptor stalled");
    dut.io_work_valid = 0;
    state.accept([&] { return dut.io_loopDone_valid; }, "paired loop stalled");
    check(state.reads.empty() && state.writes.empty(), "paired loop left backing traffic pending");
    state.release_scales(l.js, l.k, l.k + l.ks, 0, generation, slot * 128);
    require(traced == expected_beats(plan, loop, work_base),
            label + ": issued µT trace differs from micro_context at loop " + std::to_string(index));
    for (const auto &beat : traced) {
      residual_uts += beat[0] == 1;
      residual_w_rows += beat[0] == 2;
    }
    if (loop.has_residual)
      check_residual_loop(state, o, plan, loop, slot, residual_a, layout, reads_before,
                          label + " loop " + std::to_string(index));
    else
      require(gathered.empty() && mesh_a.empty() && mesh_d.empty(),
              label + ": residual observation on a loop without a residual");
    for (std::size_t i = 0; i < ip / dim; ++i)
      for (std::size_t j = 0; j < max_j; ++j) expected_ids.insert(work_base + i * max_j + j);
    for (std::size_t g = 0; g < fields.groups; ++g)
      for (std::size_t j = 0; j < max_j; ++j)
        expected_ids.insert(work_base + fields.work_offset + g * max_j + j);
    if (l.final_contribution) {
      state.accept([&] { return dut.io_writebackDrained; }, "paired writeback did not drain");
      std::set<std::uint64_t> completed;
      for (std::size_t id = 0; id < state.output_ids.size(); ++id)
        if (state.output_ids[id]) completed.insert(id);
      require(completed == expected_ids, label + ": completed work IDs differ at loop " + std::to_string(index));
    }
    generation = generation % 255 + 1;
    last = l.last;
    pairing::advance_paired_loop(plan, loop, cursor);
    ++index;
  } while (!last);
  std::vector<std::int32_t> output;
  for (std::size_t i = 0; i < rows; ++i)
    for (std::size_t j = 0; j < o.n; ++j) {
      std::uint32_t raw = 0;
      for (std::size_t byte = 0; byte < 4; ++byte)
        raw |= std::uint32_t{state.c[slot][i * state.c_stride[slot] + j * 4 + byte]} << (byte * 8);
      std::int32_t value;
      std::memcpy(&value, &raw, sizeof(value));
      output.push_back(value);
    }
  return output;
}

struct Case {
  std::string name;
  std::size_t m, n, k, tile_i, tile_j, tile_k, stripe_rows;
  std::vector<std::size_t> residual_rows; // one stripe's M_R per run
  std::vector<std::uint32_t> masks;       // per original block; 0 = no run
};

struct Residual {
  std::vector<im2p_compact_run_t> runs;
  im2p_compact_runs_t view{};
  std::size_t compact_k = 0;
};

Residual make_residual(std::size_t k, const std::vector<std::uint32_t> &masks) {
  Residual r;
  for (std::size_t block = 0; block < masks.size(); ++block) {
    if (!masks[block]) continue;
    const auto count = static_cast<std::uint32_t>(__builtin_popcount(masks[block]));
    r.runs.push_back({static_cast<std::uint32_t>(block), masks[block],
                      static_cast<std::uint32_t>(r.compact_k), count});
    r.compact_k += count;
  }
  r.view = {IM2P_COMPACT_RUNS_VERSION, sizeof(im2p_compact_runs_t), static_cast<std::uint32_t>(k),
            r.runs.size(), r.runs.data()};
  return r;
}

schedule::ScheduleConfig main_config(const Case &c, std::size_t tile_k) {
  return {{c.m, c.n, c.k}, {dim, IM2P_OPERAND_BITS}, {c.tile_i, c.tile_j, tile_k, c.stripe_rows},
          {c.k, c.n, c.n * 4, c.n * 4, 0}};
}

std::vector<Case> cases() {
  const std::vector<std::uint32_t> k96 = {0, 0x00f0f000u, 0xffff0fffu};
  std::vector<std::uint32_t> k1024(32, 0), k128 = {0xffffffffu, 0, 0x00ff00ffu, 0}, k3072(96, 0);
  k1024[0] = 1;
  k1024[3] = 0xffffffffu;
  k1024[31] = 0x80000001u;
  k3072[0] = 1;
  k3072[25] = 0xffffffffu;
  k3072[95] = 0x80000001u;
  // The P2.1 list for this DIM; cases are chosen by the fit rule only.
  std::vector<Case> list;
  if (dim == 16) {
    list.push_back({"N16", 80, 16, 96, 5, 1, 6, 80, {1, 16, 17, 432, 433}, k96});
    list.push_back({"N80", 80, 80, 96, 5, 5, 6, 80, {1, 16, 17}, k96});
    list.push_back({"N96-partial-j", 80, 96, 96, 5, 5, 6, 80, {1, 16, 17}, k96});
    list.push_back({"tile-2-5-6-fallback", 80, 80, 96, 2, 5, 6, 80, {1, 16, 432}, k96});
    list.push_back({"K3072-snap-51", 16, 16, 3072, 1, 1, 51, 16, {1}, k3072});
    // Two stripes in slots 0 and 1: 75 work IDs each, 128..202 in slot 1 at physical capacity.
    list.push_back({"two-stripe-slot1-ids", 160, 80, 96, 5, 5, 6, 80, {160}, k96});
  } else if (dim == 64) {
    list.push_back({"D64-N256-K1024", 64, 256, 1024, 1, 1, 16, 64, {1, 64, 65}, k1024});
    list.push_back({"D64-N256-K128", 64, 256, 128, 1, 2, 2, 64, {1, 64}, k128});
    list.push_back({"D64-N64-K128", 64, 64, 128, 1, 1, 2, 64, {1, 64}, k128});
  } else {
    list.push_back({"two-stripe", 3 * dim, dim, 96, 2, 1, 96 / dim, 2 * dim, {5}, k96});
  }
  list.push_back({"blocks", dim, dim, 96, 1, 1, (96 + dim - 1) / dim, dim, {1, dim}, k96});
  return list;
}

pairing::PairCapacity physical(Adapter &state) {
  return pairing::capacity_from_hardware(state.dut.io_physicalAccumulatorRows, work_entries(state),
                                         4 * IM2P_BANK_ROWS);
}
pairing::PairCapacity compat() { return {IM2P_ACC_ROWS / 2, 64, 4 * IM2P_BANK_ROWS / 2, 128}; }

pairing::PairPlan plan_for(const Case &c, std::size_t tile_k, std::size_t stripe, std::size_t residual_rows,
                           const Residual *residual, const pairing::PairCapacity &capacity) {
  const auto begin = stripe * c.stripe_rows;
  const auto rows = std::min(c.stripe_rows, c.m - begin);
  pairing::Residual r{};
  if (residual) r = {residual_rows, residual->compact_k, &residual->view, residual->compact_k, c.n * 4};
  pairing::PairPlan plan;
  require(pairing::plan_stripe(main_config(c, tile_k), begin, rows, r, capacity, plan),
          c.name + ": invalid stripe plan");
  return plan;
}

std::size_t stripes(const Case &c) { return (c.m + c.stripe_rows - 1) / c.stripe_rows; }

void check_reference(const Case &c, const Operands &o, std::size_t stripe,
                     const std::vector<std::int32_t> &output, const std::string &label) {
  const auto begin = stripe * c.stripe_rows;
  for (std::size_t i = 0; i * c.n < output.size(); ++i)
    for (std::size_t j = 0; j < c.n; ++j)
      require(output[i * c.n + j] == reference(o, begin + i, j), label + ": Main differs from the CPU reference");
}

// Paired stripes at 1x compat and at the physical capacity read from the top: the trace must
// equal micro_context/w_row (checked inside run_stripe) and Main the CPU reference.
void run_vectors(Adapter &state) {
  std::uint32_t generation = 1;
  for (const auto &c : cases()) {
    const auto o = make_operands(c.m, c.n, c.k, c.m * 131 + c.n * 7 + c.k);
    const auto residual = make_residual(c.k, c.masks);
    for (const auto rows : c.residual_rows)
      for (const auto &[capacity, is_compat] : {std::pair{compat(), true}, std::pair{physical(state), false}}) {
        std::size_t paired = 0;
        residual_uts = residual_w_rows = gather_rows = ldr_rows = mesh_rows = 0;
        for (std::size_t stripe = 0; stripe < stripes(c); ++stripe) {
          const auto plan = plan_for(c, c.tile_k, stripe, rows, &residual, capacity);
          paired += plan.paired;
          const auto label = c.name + " MR" + std::to_string(rows) + (is_compat ? " 1x" : " physical") +
                             " stripe" + std::to_string(stripe);
          const auto residual_a = make_residual_a(rows, residual.compact_k, c.m * 7 + rows * 13 + stripe);
          check_reference(c, o, stripe, run_stripe(state, o, plan, stripe % 2, generation, label, residual_a),
                          label);
        }
        std::cout << "PAIRED_RTL_VECTORS case=" << c.name << " MR=" << rows
                  << " capacity=" << (is_compat ? "1x" : "physical") << " paired_stripes=" << paired
                  << " residual_uts=" << residual_uts << " w_rows=" << residual_w_rows
                  << " gather_rows=" << gather_rows << " ldr_rows=" << ldr_rows << " mesh_rows=" << mesh_rows
                  << " PASS\n";
      }
  }
}

// Amendment 4: pair-off with the original tile_k, pair-off with the snapped tile_k and paired
// with the snapped tile_k must give the same Main output, equal to the CPU reference.
void run_main_parity(Adapter &state) {
  std::uint32_t generation = 1;
  for (const auto &c : cases()) {
    const auto o = make_operands(c.m, c.n, c.k, c.m * 131 + c.n * 7 + c.k);
    const auto residual = make_residual(c.k, c.masks);
    const auto snapped = pairing::snapped_tile_k(c.tile_k, dim);
    for (const auto rows : c.residual_rows) {
      for (std::size_t stripe = 0; stripe < stripes(c); ++stripe) {
        const auto label = c.name + " MR" + std::to_string(rows) + " stripe" + std::to_string(stripe);
        const auto slot = stripe % 2;
        const auto paired_plan = plan_for(c, c.tile_k, stripe, rows, &residual, physical(state));
        const auto original = run_stripe(state, o, plan_for(c, c.tile_k, stripe, 0, nullptr, physical(state)),
                                         slot, generation, label + " pair-off");
        const auto snapped_off = run_stripe(state, o, plan_for(c, snapped, stripe, 0, nullptr, physical(state)),
                                            slot, generation, label + " snapped pair-off");
        const auto residual_a = make_residual_a(rows, residual.compact_k, c.m * 7 + rows * 13 + stripe);
        const auto paired = run_stripe(state, o, paired_plan, slot, generation, label + " paired", residual_a);
        require(original == snapped_off, label + ": snapped tile_k changed Main (original vs snapped pair-off)");
        require(snapped_off == paired, label + ": residual µTs changed Main (snapped pair-off vs paired)");
        check_reference(c, o, stripe, original, label);
        std::cout << "PAIRED_RTL_MAIN_PARITY case=" << c.name << " MR=" << rows << " stripe=" << stripe
                  << " paired=" << paired_plan.paired << " tile_k=" << c.tile_k << "->" << snapped
                  << " PASS\n";
      }
    }
  }
}

// Spec C1 negative test: the dual-loop overlap stimulus. Pair-off loops still overlap; any paired
// loop is issued only after its predecessor drained.
void run_paired_overlap(Adapter &state) {
  auto &dut = state.dut;
  const auto entries = work_entries(state);
  const auto acc_top = static_cast<std::uint32_t>(dut.io_physicalAccumulatorRows / 2);
  std::uint32_t generation = 101;
  state.write_latency = 97;
  for (const auto &[first_paired, second_paired] :
       {std::pair{false, false}, std::pair{true, true}, std::pair{false, true}, std::pair{true, false}}) {
    clear_observation();
    const auto old_overlap = state.overlap_loops;
    const auto old_completed = state.completed_slots.size();
    state.output_ids.assign(entries, false);
    const std::array<std::int8_t, 2> activations{2, -2};
    const std::array<std::int8_t, 2> weights{3, 4};
    const std::array<bool, 2> paired{first_paired, second_paired};
    const std::array<std::uint32_t, 2> generations{generation, generation + 1};
    for (std::size_t slot = 0; slot < 2; ++slot) {
      state.rows[slot] = 1;
      state.columns[slot] = 1;
      state.c_stride[slot] = acc_bytes;
      state.a[slot].assign(dim * sp_bytes, 0);
      if (paired[slot]) {
        // The one-row residual's packed A (one D-row group) follows a nonzero gap.
        state.a[slot].resize((dim + gap_rows) * sp_bytes, 0x5A);
        state.a[slot].resize((2 * dim + gap_rows) * sp_bytes, 0);
        put_operand(state.a[slot], (dim + gap_rows) * sp_bytes * 8 / IM2P_OPERAND_BITS, 1);
      }
      state.b[slot].assign(dim * sp_bytes, 0);
      state.s[slot].assign(acc_bytes, 0);
      state.c[slot].assign(dim * acc_bytes, 0xA5);
      state.written[slot].assign(state.c[slot].size(), 0);
      put_operand(state.a[slot], 0, activations[slot]);
      put_operand(state.b[slot], 0, weights[slot]);
      state.s[slot][0] = static_cast<std::uint8_t>(slot);
    }
    for (std::size_t slot = 0; slot < 2; ++slot) {
      dut.io_work_bits_maxI = 1;
      dut.io_work_bits_maxJ = 1;
      dut.io_work_bits_maxK = 1;
      dut.io_work_bits_padI = dim - 1;
      dut.io_work_bits_padJ = dim - 1;
      dut.io_work_bits_padK = dim - 1;
      dut.io_work_bits_aAddress = slot_address(a_base, slot);
      dut.io_work_bits_residualAAddress = paired[slot] ? slot_address(a_base, slot) + (dim + gap_rows) * sp_bytes : 0;
      dut.io_work_bits_residualAStrideBytes = paired[slot] ? sp_bytes : 0;
      dut.io_work_bits_bAddress = slot_address(b_base, slot);
      dut.io_work_bits_cAddress = slot_address(c_base, slot);
      dut.io_work_bits_scaleBackingAddress = slot_address(s_base, slot);
      dut.io_work_bits_aStrideBytes = sp_bytes;
      dut.io_work_bits_bStrideBytes = sp_bytes;
      dut.io_work_bits_cStrideBytes = acc_bytes;
      dut.io_work_bits_scaleBase = slot * 128;
      dut.io_work_bits_scaleGeneration = generations[slot];
      dut.io_work_bits_fragmentBase = 0;
      dut.io_work_bits_workBase = slot * (entries / 2);
      dut.io_work_bits_accumulate = 0;
      dut.io_work_bits_finalFragment = 1;
      dut.io_work_bits_firstLoop = 1;
      dut.io_work_bits_finalLoop = 1;
      dut.io_work_bits_logicalWorkId = 200 + slot;
      dut.io_work_bits_hostSlot = slot;
      dut.io_work_bits_rmdRaw = 0;
      // One 1-bit residual µT riding a paired loop; pair-off fields are all zero.
      const bool on = paired[slot];
      dut.io_work_bits_paired = on;
      dut.io_work_bits_residualMask = on;
      dut.io_work_bits_residualCompactBegin = 0;
      dut.io_work_bits_residualGroups = on;
      dut.io_work_bits_residualPadI = on ? dim - 1 : 0;
      dut.io_work_bits_residualAccTop = on ? acc_top : 0;
      dut.io_work_bits_residualWorkOffset = on;
      dut.io_work_bits_residualFirstRun = on;
      dut.io_work_bits_residualFinalRun = on;
      dut.io_work_valid = 1;
      state.accept([&] { return dut.io_work_ready; }, "overlap descriptor stalled");
      dut.io_work_valid = 0;
    }
    state.accept([&] { return state.completed_slots.size() - old_completed == 2; },
                 "overlap loops did not complete");
    for (std::size_t slot = 0; slot < 2; ++slot)
      state.release_scales(1, 0, 1, 0, generations[slot], slot * 128);
    state.accept([&] { return !dut.io_busy; }, "overlap top did not drain");
    const bool overlapped = state.overlap_loops > old_overlap;
    require(overlapped == !(first_paired || second_paired),
            std::string("C1: paired=") + (first_paired ? "1" : "0") + (second_paired ? "1" : "0") +
                (overlapped ? " overlapped" : " did not overlap"));
    require(state.completed_slots[old_completed] == 0 && state.completed_slots[old_completed + 1] == 1,
            "overlap completion lost host-slot order");
    for (std::size_t slot = 0; slot < 2; ++slot) {
      std::uint32_t raw = 0;
      for (std::size_t byte = 0; byte < 4; ++byte) raw |= std::uint32_t{state.c[slot][byte]} << (byte * 8);
      std::int32_t value;
      std::memcpy(&value, &raw, sizeof(value));
      require(value == (slot == 0 ? 6 : -16), "overlap output differs from the integer oracle");
      const auto base = slot * (entries / 2);
      require(state.output_ids[base] && state.output_ids[base + 1] == paired[slot],
              "overlap work IDs did not complete exactly as paired");
    }
    std::cout << "PAIRED_RTL_OVERLAP paired=" << first_paired << second_paired
              << " overlap_loops=" << state.overlap_loops - old_overlap << " PASS\n";
    generation += 2;
  }
  state.write_latency = 11;
}
} // namespace

int main(int argc, char **argv) {
  try {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    Adapter state{dut, context};
    dut.io_work_valid = 0;
    dut.io_loopDone_ready = 1;
    dut.io_scaleRelease_valid = 0;
    dut.io_readRequest_ready = 1;
    dut.io_readBeat_valid = 0;
    dut.io_writeRequest_ready = 1;
    dut.io_writeCompletion_valid = 0;
    dut.reset = 1;
    for (unsigned i = 0; i < 5; ++i)
      state.clock();
    dut.reset = 0;
    state.clock();
    state.event_observer = trace;
    const std::string mode = argc == 2 ? argv[1] : "";
    if (mode == "--vectors")
      run_vectors(state);
    else if (mode == "--main-parity")
      run_main_parity(state);
    else if (mode == "--paired-overlap")
      run_paired_overlap(state);
    else
      throw std::runtime_error("expected --vectors, --main-parity or --paired-overlap");
    return 0;
  } catch (const std::exception &error) {
    std::cerr << "paired RTL test failed: " << error.what() << '\n';
    return 1;
  }
}
