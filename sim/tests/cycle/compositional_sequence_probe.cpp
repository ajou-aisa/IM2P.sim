#if !defined(IM2P_RTL_TEST_BUILD) || !__has_include(<VIM2PGemminiWSHP1RtlTest.h>)
int im2p_compositional_sequence_requires_generated_model;
#else
namespace im2p::gemmini_hp1 { struct RmdRunWork; }
namespace {
struct WorkInput;
void apply_numeric_payload(const WorkInput &, im2p::gemmini_hp1::RmdRunWork &);
}
#define IM2P_COMPOSITIONAL_NUMERIC_HOOK(w, work) apply_numeric_payload(w, work)
#define read_work legacy_sequence_read_work
#define measure legacy_sequence_measure
#include IM2P_COMPOSITIONAL_BASE_SOURCE
#undef main
#undef read_work
#undef measure
#undef IM2P_COMPOSITIONAL_NUMERIC_HOOK
#include "../../include/im2p_cycle_sequence.h"
#include "../../../fpga/gemmini_hp1/host/run_aware_numeric_fixture.hpp"
#if IM2P_ACTIVATION_BITS == 8 && IM2P_OPERAND_BITS == 8 && IM2P_DIM == 64
#include <VIM2PGemminiWSHP1RtlTest_ScratchpadBank.h>
#endif

#define IM2P_SEQUENCE_TAP_(top, suffix) top##__DOT__control__DOT__##suffix
#define IM2P_SEQUENCE_TAP(top, suffix) IM2P_SEQUENCE_TAP_(top, suffix)
#define IM2P_TAG_REG(index, field) IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__tagq__DOT__regs_##index##_##field)
#define IM2P_BOUNDARY_BANK_TAP_(top, suffix) top##__DOT__##suffix
#define IM2P_BOUNDARY_BANK_TAP(top, suffix) IM2P_BOUNDARY_BANK_TAP_(top, suffix)
#if IM2P_ACTIVATION_BITS == 8 && IM2P_OPERAND_BITS == 8 && IM2P_DIM == 64
#define IM2P_BOUNDARY_CHILD_(top, bank) r->__PVT__##top##__DOT__memory__DOT__banks_##bank
#define IM2P_BOUNDARY_CHILD(top, bank) IM2P_BOUNDARY_CHILD_(top, bank)
#define IM2P_BOUNDARY_PENDING(bank) \
  (check(bool(IM2P_BOUNDARY_CHILD(IM2P_RTL_SELECTED_TOP, bank)), "missing ScratchpadBank"), \
   IM2P_BOUNDARY_CHILD(IM2P_RTL_SELECTED_TOP, bank)->__PVT__q_io_enq_valid_REG)
#define IM2P_BOUNDARY_QUEUED(bank) \
  (check(bool(IM2P_BOUNDARY_CHILD(IM2P_RTL_SELECTED_TOP, bank)), "missing ScratchpadBank"), \
   IM2P_BOUNDARY_CHILD(IM2P_RTL_SELECTED_TOP, bank)->__PVT__q__DOT__full)
#else
#define IM2P_BOUNDARY_PENDING(bank) r->IM2P_BOUNDARY_BANK_TAP(IM2P_RTL_SELECTED_TOP, memory__DOT__banks_##bank##__DOT__q_io_enq_valid_REG)
#define IM2P_BOUNDARY_QUEUED(bank) r->IM2P_BOUNDARY_BANK_TAP(IM2P_RTL_SELECTED_TOP, memory__DOT__banks_##bank##__DOT__q__DOT__full)
#endif
#define IM2P_BOUNDARY_PIPE(bank, stage) r->IM2P_BOUNDARY_BANK_TAP(IM2P_RTL_SELECTED_TOP, memory__DOT__io_srams_read_##bank##_resp_p__DOT__valids_##stage)

namespace {
const ProductionCase *numeric_payload = nullptr;
std::uint64_t numeric_work_id = UINT64_MAX;
unsigned numeric_injections = 0;
bool boundary_schema_v2 = false;
bool queue_edge_schema_v2 = false;
static_assert(IM2P_CYCLE_SEQUENCE_DOMAIN_ABI_VERSION == 2u,
              "queue payload V2 requires native domain ABI V2");

void verify_numeric_payload(const ProductionCase &payload, const WorkInput &w) {
  const auto &work = payload.work;
  check(w.kind == 'R' && work.plan.m == w.m && work.plan.n == w.n &&
            work.plan.k == w.k && work.plan.tile_i == w.ti &&
            work.plan.tile_j == w.tj && work.plan.tile_k == w.tk &&
            work.original_k == w.original_k &&
            payload.activation_stride_bytes == w.a_stride &&
            payload.weight_stride_bytes == w.b_stride &&
            payload.output_stride_bytes == w.c_stride &&
            payload.scale_stride_elements == w.s_stride &&
            payload.work_context == w.id &&
            work.runs.size() == w.runs.size() &&
            payload.expected.size() == w.m * w.n,
        "numeric fixture geometry, stride or run count differs from sealed work");
  for (std::size_t i = 0; i < w.runs.size(); ++i)
    check(work.runs[i].original_block_id == w.runs[i].original_block_id &&
              work.runs[i].original_k_mask == w.runs[i].original_k_mask &&
              work.runs[i].compact_k_begin == w.runs[i].compact_k_begin &&
              work.runs[i].compact_k_count == w.runs[i].compact_k_count,
          "numeric fixture ordered compact run differs from sealed work");
  check(std::all_of(work.carriers.begin(), work.carriers.end(),
                    ggml::gemmini::quants::hp1::valid_carrier),
        "numeric fixture carrier is invalid");
}

void apply_numeric_payload(const WorkInput &w,
                           im2p::gemmini_hp1::RmdRunWork &work) {
  if (!numeric_payload || w.id != numeric_work_id)
    return;
  work.activations = numeric_payload->work.activations;
  work.weights = numeric_payload->work.weights;
  work.carriers = numeric_payload->work.carriers;
  ++numeric_injections;
}

std::vector<WorkInput> read_work(const char *path, unsigned &period) {
  std::ifstream input(path);
  check(bool(input), "composition manifest missing");
  std::string token, profile;
  input >> token >> profile;
  check(token == "IM2P_COMPOSITIONAL_SEQUENCE_V1" && profile ==
            "a" + std::to_string(IM2P_ACTIVATION_BITS) + "w" +
                std::to_string(IM2P_OPERAND_BITS) + "-d" + std::to_string(dim) + "-hp1",
        "composition profile differs from RTL");
  unsigned count = 0;
  input >> token >> period;
  check(token == "PERIOD" && period == 5, "unsupported reference-memory period");
  input >> token >> count;
  check(token == "WORKS" && count > 4 && count <= 4096, "composition needs >4 bounded works");
  std::vector<WorkInput> works;
  for (unsigned ordinal = 0; ordinal < count; ++ordinal) {
    WorkInput w{};
    unsigned run_count = 0;
    input >> token >> w.ordinal >> w.id >> w.kind >> w.slot >> w.phase >> w.parent >> w.call >>
        w.stripe >> w.row_begin >> w.parent_m >> w.m >> w.n >> w.k >> w.ti >> w.tj >> w.tk >>
        w.a_stride >> w.b_stride >> w.c_stride >> w.s_stride >> w.original_k >> w.binding >> run_count;
    check(bool(input) && token == "W" && w.ordinal == ordinal && w.id <= UINT32_MAX &&
              (ordinal || w.phase < period) && (w.kind == 'D' || w.kind == 'R') &&
              w.slot < 2 && w.m && w.n && w.k && w.m <= 8192 && w.n <= 8192 &&
              w.k <= 8192 && w.ti && w.tj && w.tk && run_count <= 256 &&
              ((w.kind == 'R') == (run_count > 0)), "malformed composition work");
    for (unsigned i = 0; i < run_count; ++i) {
      im2p_compact_run_t run{};
      input >> token >> run.original_block_id >> run.original_k_mask >>
          run.compact_k_begin >> run.compact_k_count;
      check(bool(input) && token == "R", "malformed composition run");
      w.runs.push_back(run);
    }
    works.push_back(w);
  }
  check(!(input >> token), "trailing composition records");
  return works;
}

struct AbsoluteOffer {
  std::uint64_t available = 0, port = 0;
};

struct AbsoluteManifest {
  std::vector<WorkInput> works;
  std::vector<AbsoluteOffer> offers;
  std::string digest;
  unsigned period = 0;
};

struct TagEdge {
  std::uint64_t cycle, enqueues, dequeues;
  unsigned len, head_id;
};

void emit_tag_window(std::string_view name, unsigned ordinal,
                     std::string_view phase, const std::vector<TagEdge> &window) {
  for (const auto &edge : window)
    std::cout << name << ' ' << ordinal << ' ' << phase << ' ' << edge.cycle
              << ' ' << edge.len << ' ' << edge.head_id << ' ' << edge.enqueues
              << ' ' << edge.dequeues << '\n';
}

AbsoluteManifest read_absolute_work(const char *path) {
  std::ifstream input(path);
  check(bool(input), "absolute composition manifest missing");
  AbsoluteManifest manifest;
  std::string token, profile;
  input >> token >> profile;
  check(token == "IM2P_COMPOSITIONAL_SEQUENCE_V2" && profile ==
            "a" + std::to_string(IM2P_ACTIVATION_BITS) + "w" +
                std::to_string(IM2P_OPERAND_BITS) + "-d" + std::to_string(dim) + "-hp1",
        "absolute composition profile differs from RTL");
  input >> token >> manifest.period;
  check(bool(input) && token == "PERIOD" && manifest.period == 5,
        "unsupported absolute reference-memory period");
  unsigned count = 0;
  input >> token >> count;
  check(bool(input) && token == "WORKS" && count && count <= 4096,
        "absolute composition needs bounded works");
  input >> token >> manifest.digest;
  check(bool(input) && token == "DIGEST" && manifest.digest.size() == 64 &&
            manifest.digest.find_first_not_of("0123456789abcdef") == std::string::npos,
        "malformed absolute stimulus digest");
  for (unsigned ordinal = 0; ordinal < count; ++ordinal) {
    WorkInput w{};
    unsigned run_count = 0;
    input >> token >> w.ordinal >> w.id >> w.kind >> w.slot >> w.phase >> w.parent >> w.call >>
        w.stripe >> w.row_begin >> w.parent_m >> w.m >> w.n >> w.k >> w.ti >> w.tj >> w.tk >>
        w.a_stride >> w.b_stride >> w.c_stride >> w.s_stride >> w.original_k >> w.binding >> run_count;
    check(bool(input) && token == "W" && w.ordinal == ordinal && w.id <= UINT32_MAX &&
              w.phase == 0 && (w.kind == 'D' || w.kind == 'R') && w.slot < 2 &&
              w.m && w.n && w.k && w.m <= 8192 && w.n <= 8192 && w.k <= 8192 &&
              w.ti && w.tj && w.tk && run_count <= 256 &&
              ((w.kind == 'R') == (run_count > 0)), "malformed absolute composition work");
    for (unsigned i = 0; i < run_count; ++i) {
      im2p_compact_run_t run{};
      input >> token >> run.original_block_id >> run.original_k_mask >>
          run.compact_k_begin >> run.compact_k_count;
      check(bool(input) && token == "R", "malformed absolute composition run");
      w.runs.push_back(run);
    }
    AbsoluteOffer offer{};
    unsigned offered_ordinal = UINT32_MAX;
    input >> token >> offered_ordinal >> offer.available >> offer.port;
    check(bool(input) && token == "A" && offered_ordinal == ordinal &&
              offer.available <= offer.port &&
              (!ordinal || (offer.available >= manifest.offers.back().available &&
                            offer.port > manifest.offers.back().port)),
          "malformed or out-of-order absolute offer");
    manifest.works.push_back(std::move(w));
    manifest.offers.push_back(offer);
  }
  check(!(input >> token), "trailing absolute composition records");
  return manifest;
}

bool public_ready(Adapter &state) {
  state.dut.eval();
  const auto &d = state.dut;
  return d.io_work_ready && !d.io_busy && d.io_memoryDrained &&
      d.io_writebackDrained && !d.io_controllerBusy && !d.io_loadBusy &&
      !d.io_executeBusy && !d.io_storeBusy && !d.io_loopBusy &&
      state.reads.empty() && state.writes.empty();
}

const AbsoluteOffer *active_absolute_offer = nullptr;
std::vector<TagEdge> rtl_tag_offer_window, rtl_tag_recent;
std::uint64_t absolute_fire = 0;
std::uint64_t absolute_fragments = 0;
bool absolute_held = false;
bool absolute_credit = false;
std::array<std::uint64_t, 24> held_descriptor{};
std::vector<std::string> absolute_window;
bool absolute_mode = false;
const AbsoluteManifest *absolute_manifest = nullptr;

struct QueueSnapshot {
  std::uint64_t generation = 1, cycle = 0, tag_enqueues = 0, tag_dequeues = 0;
  unsigned row_count = 0, tag_count = 0;
  std::array<std::array<unsigned, 2>, 6> rows{};
  std::array<std::array<unsigned, 4>, 6> tags{};
  std::array<std::array<std::uint64_t, 12>, 6> tag_payloads{};
  unsigned tag_payload_width = 0;
  unsigned row_read = 0, row_write = 0, row_empty = 1, row_maybe_full = 0;
  unsigned row_enq_fire = 0, row_deq_fire = 0;
  unsigned tag_read = 0, tag_write = 0, tag_enq_fire = 0, tag_deq_fire = 0;
  unsigned request_valid = 0, last_response_fire = 0;
};

QueueSnapshot previous_rtl_queue;
bool have_previous_rtl_queue = false;
std::uint64_t queue_edge_bytes = 0;
constexpr std::uint64_t queue_edge_byte_limit = 128ULL * 1024 * 1024;

void append_queue_snapshot(std::ostream &out, const QueueSnapshot &s, bool rtl) {
  out << "{\"cycle\":" << s.cycle << ",\"row_count\":" << s.row_count
      << ",\"rows\":[";
  for (unsigned i = 0; i < s.row_count; ++i)
    out << (i ? "," : "") << '[' << s.rows[i][0] << ',' << s.rows[i][1] << ']';
  out << "],\"tag_count\":" << s.tag_count << ",\"tags\":[";
  for (unsigned i = 0; i < s.tag_count; ++i)
    out << (i ? "," : "") << '[' << s.tags[i][0] << ',' << s.tags[i][1]
        << ',' << s.tags[i][2] << ',' << s.tags[i][3] << ']';
  out << ']';
  if (rtl)
    out << ",\"row_read\":" << s.row_read << ",\"row_write\":" << s.row_write
        << ",\"row_empty\":" << s.row_empty
        << ",\"row_maybe_full\":" << s.row_maybe_full
        << ",\"row_enq_fire\":" << s.row_enq_fire
        << ",\"row_deq_fire\":" << s.row_deq_fire
        << ",\"tag_read\":" << s.tag_read << ",\"tag_write\":" << s.tag_write
        << ",\"tag_enq_fire\":" << s.tag_enq_fire
        << ",\"tag_deq_fire\":" << s.tag_deq_fire
        << ",\"request_valid\":" << s.request_valid
        << ",\"last_response_fire\":" << s.last_response_fire;
  else
    out << ",\"tag_enqueues\":" << s.tag_enqueues
        << ",\"tag_dequeues\":" << s.tag_dequeues;
  if (queue_edge_schema_v2) {
    check(s.tag_payload_width == (rtl ? 10U : 12U),
          "queue payload V2 width differs from domain");
    out << ",\"tag_payloads\":[";
    for (unsigned i = 0; i < s.tag_count; ++i) {
      out << (i ? ",[" : "[");
      for (unsigned field = 0; field < s.tag_payload_width; ++field)
        out << (field ? "," : "") << s.tag_payloads[i][field];
      out << ']';
    }
    out << ']';
  }
  out << '}';
}

void emit_queue_edge(std::string_view prefix, const QueueSnapshot &old,
                     const QueueSnapshot &next, bool rtl) {
  check(old.generation == next.generation && old.cycle < UINT64_MAX &&
            next.cycle == old.cycle + 1,
        "queue edge lacks one consecutive successor in the same generation");
  std::ostringstream out;
  out << prefix << " {\"queue_schema\":" << (queue_edge_schema_v2 ? 2 : 1)
      << ",\"generation\":" << old.generation
      << ",\"old\":";
  append_queue_snapshot(out, old, rtl);
  out << ",\"next\":";
  append_queue_snapshot(out, next, rtl);
  out << "}\n";
  const auto line = out.str();
  check(line.size() <= 2048, "queue edge line exceeds 2048 bytes");
  check(line.size() <= queue_edge_byte_limit - queue_edge_bytes,
        "combined queue edge output exceeds 128 MiB");
  queue_edge_bytes += line.size();
  std::cout << line;
  check(bool(std::cout), "queue edge output failed");
}

QueueSnapshot rtl_queue_snapshot(const Dut &d) {
  const auto *r = d.rootp;
#define QUEUE_ROW(field) r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__total_rows_q__DOT__##field)
  QueueSnapshot s;
  s.cycle = d.io_coreCycle;
  s.row_read = QUEUE_ROW(deq_ptr_value);
  s.row_write = QUEUE_ROW(enq_ptr_value);
  s.row_empty = QUEUE_ROW(empty);
  s.row_maybe_full = QUEUE_ROW(maybe_full);
  check(s.row_read < 6 && s.row_write < 6,
        "RTL queue edge row pointer invalid");
  s.row_count = s.row_read == s.row_write ? (s.row_maybe_full ? 6 : 0) :
                (s.row_write + 6 - s.row_read) % 6;
  check(s.row_empty == (s.row_count == 0),
        "RTL queue edge row occupancy invalid");
  const auto &ram = QUEUE_ROW(ram_ext__DOT__Memory);
  for (unsigned i = 0; i < s.row_count; ++i) {
    const unsigned packed = ram[(s.row_read + i) % 6];
    s.rows[i] = {packed & 7, packed >> 3};
  }
  s.tag_count = r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__tagq__DOT__len);
  s.tag_read = r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__tagq__DOT__raddr);
  s.tag_write = r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__tagq__DOT__waddr);
  check(s.tag_count <= 6 && s.tag_read < 6 && s.tag_write < 6 &&
            s.tag_write == (s.tag_read + s.tag_count) % 6,
        "RTL queue edge tag occupancy invalid");
  const std::array<unsigned, 6> ids = {r->IM2P_TAG_REG(0, id), r->IM2P_TAG_REG(1, id),
      r->IM2P_TAG_REG(2, id), r->IM2P_TAG_REG(3, id), r->IM2P_TAG_REG(4, id),
      r->IM2P_TAG_REG(5, id)};
  const std::array<unsigned, 6> output_rows = {r->IM2P_TAG_REG(0, tag_rows),
      r->IM2P_TAG_REG(1, tag_rows), r->IM2P_TAG_REG(2, tag_rows),
      r->IM2P_TAG_REG(3, tag_rows), r->IM2P_TAG_REG(4, tag_rows),
      r->IM2P_TAG_REG(5, tag_rows)};
  const std::array<unsigned, 6> valid = {r->IM2P_TAG_REG(0, tag_rob_id_valid),
      r->IM2P_TAG_REG(1, tag_rob_id_valid), r->IM2P_TAG_REG(2, tag_rob_id_valid),
      r->IM2P_TAG_REG(3, tag_rob_id_valid), r->IM2P_TAG_REG(4, tag_rob_id_valid),
      r->IM2P_TAG_REG(5, tag_rob_id_valid)};
  const std::array<unsigned, 6> rob = {r->IM2P_TAG_REG(0, tag_rob_id_bits),
      r->IM2P_TAG_REG(1, tag_rob_id_bits), r->IM2P_TAG_REG(2, tag_rob_id_bits),
      r->IM2P_TAG_REG(3, tag_rob_id_bits), r->IM2P_TAG_REG(4, tag_rob_id_bits),
      r->IM2P_TAG_REG(5, tag_rob_id_bits)};
  for (unsigned i = 0; i < s.tag_count; ++i) {
    const unsigned index = (s.tag_read + i) % 6;
    s.tags[i] = {ids[index], output_rows[index], valid[index],
                 valid[index] ? rob[index] : 0};
  }
  if (queue_edge_schema_v2) {
#define TAG_SLOTS(field) std::array<std::uint64_t, 6>{r->IM2P_TAG_REG(0, field), \
    r->IM2P_TAG_REG(1, field), r->IM2P_TAG_REG(2, field), \
    r->IM2P_TAG_REG(3, field), r->IM2P_TAG_REG(4, field), \
    r->IM2P_TAG_REG(5, field)}
    const auto cols = TAG_SLOTS(tag_cols);
    const auto addr_data = TAG_SLOTS(tag_addr_data);
    const auto addr_is_acc = TAG_SLOTS(tag_addr_is_acc_addr);
    const auto addr_accumulate = TAG_SLOTS(tag_addr_accumulate);
    const auto addr_full_row = TAG_SLOTS(tag_addr_read_full_acc_row);
    const auto addr_garbage_bit = TAG_SLOTS(tag_addr_garbage_bit);
#undef TAG_SLOTS
    s.tag_payload_width = 10;
    for (unsigned i = 0; i < s.tag_count; ++i) {
      const unsigned index = (s.tag_read + i) % 6;
      check(valid[index] <= 1 && addr_is_acc[index] <= 1 &&
                addr_accumulate[index] <= 1 && addr_full_row[index] <= 1 &&
                addr_garbage_bit[index] <= 1,
            "RTL queue payload V2 validity bit invalid");
      s.tag_payloads[i] = {ids[index], valid[index], valid[index] ? rob[index] : 0,
                           output_rows[index], cols[index], addr_data[index],
                           addr_is_acc[index], addr_accumulate[index],
                           addr_full_row[index], addr_garbage_bit[index]};
    }
  }
  const bool response_last = r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__io_resp_valid_RegShifted_0_0) &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT__out_last_RegShifted_0_0);
  s.row_enq_fire = QUEUE_ROW(do_enq);
  s.tag_enq_fire = s.row_enq_fire;
  s.row_deq_fire = !s.row_empty &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT___total_rows_q_io_deq_ready_T) &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT___total_rows_q_io_deq_ready_T_1);
  s.tag_deq_fire = s.tag_count && response_last &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT___tagq_io_deq_ready_T_1);
  s.request_valid = r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__req_valid);
  s.last_response_fire = response_last;
#undef QUEUE_ROW
  return s;
}

void capture_rtl_queue_edge(const Dut &d) {
  const auto next = rtl_queue_snapshot(d);
  if (have_previous_rtl_queue) {
    const auto &old = previous_rtl_queue;
    check(next.cycle >= old.cycle && next.cycle - old.cycle <= 1,
          "RTL queue edge callback skipped or reversed a cycle");
    if (next.cycle == old.cycle + 1 &&
        (old.row_enq_fire || old.row_deq_fire || old.tag_enq_fire ||
         old.tag_deq_fire || old.row_count == 6 || old.tag_count == 6 ||
         old.row_read != next.row_read || old.row_write != next.row_write ||
         old.tag_read != next.tag_read || old.tag_write != next.tag_write ||
         old.row_count != next.row_count || old.tag_count != next.tag_count ||
         old.rows != next.rows || old.tags != next.tags ||
         (queue_edge_schema_v2 && old.tag_payloads != next.tag_payloads)))
      emit_queue_edge(queue_edge_schema_v2 ? "RTL_QUEUE_EDGE_V2" :
                      "RTL_QUEUE_EDGE_V1", old, next, true);
  }
  previous_rtl_queue = next;
  have_previous_rtl_queue = true;
}

void emit_rtl_boundary(Adapter &state, std::string_view phase, unsigned ordinal) {
  check(absolute_manifest && ordinal < absolute_manifest->works.size(),
        "RTL boundary work identity absent");
  const bool ready = public_ready(state);
  const auto &d = state.dut;
  const auto *r = d.rootp;
#define ROW(field) r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__total_rows_q__DOT__##field)
  const unsigned rp = ROW(deq_ptr_value), wp = ROW(enq_ptr_value);
  check(rp < 6 && wp < 6, "RTL row queue pointer invalid");
  const unsigned row_count = rp == wp ? (ROW(maybe_full) ? 6 : 0) :
                             (wp + 6 - rp) % 6;
  check(row_count <= 6 && bool(ROW(empty)) == (row_count == 0),
        "RTL row queue occupancy invalid");
  const auto tag = tag_head(d);
  check(tag.len <= 6 && tag.read < 6 && tag.write < 6 &&
            tag.write == (tag.read + tag.len) % 6,
        "RTL tag queue pointer invalid");
  const std::array<unsigned, 6> ids = {r->IM2P_TAG_REG(0, id), r->IM2P_TAG_REG(1, id),
      r->IM2P_TAG_REG(2, id), r->IM2P_TAG_REG(3, id), r->IM2P_TAG_REG(4, id),
      r->IM2P_TAG_REG(5, id)};
  const std::array<unsigned, 6> output_rows = {r->IM2P_TAG_REG(0, tag_rows),
      r->IM2P_TAG_REG(1, tag_rows), r->IM2P_TAG_REG(2, tag_rows),
      r->IM2P_TAG_REG(3, tag_rows), r->IM2P_TAG_REG(4, tag_rows),
      r->IM2P_TAG_REG(5, tag_rows)};
  const std::array<unsigned, 6> valid = {r->IM2P_TAG_REG(0, tag_rob_id_valid),
      r->IM2P_TAG_REG(1, tag_rob_id_valid), r->IM2P_TAG_REG(2, tag_rob_id_valid),
      r->IM2P_TAG_REG(3, tag_rob_id_valid), r->IM2P_TAG_REG(4, tag_rob_id_valid),
      r->IM2P_TAG_REG(5, tag_rob_id_valid)};
  const std::array<unsigned, 6> rob = {r->IM2P_TAG_REG(0, tag_rob_id_bits),
      r->IM2P_TAG_REG(1, tag_rob_id_bits), r->IM2P_TAG_REG(2, tag_rob_id_bits),
      r->IM2P_TAG_REG(3, tag_rob_id_bits), r->IM2P_TAG_REG(4, tag_rob_id_bits),
      r->IM2P_TAG_REG(5, tag_rob_id_bits)};
  const std::array<std::array<unsigned, 6>, 4> bank_pipe = {{
      {IM2P_BOUNDARY_PENDING(0), IM2P_BOUNDARY_QUEUED(0),
       IM2P_BOUNDARY_PIPE(0, 0), IM2P_BOUNDARY_PIPE(0, 1),
       IM2P_BOUNDARY_PIPE(0, 2), IM2P_BOUNDARY_PIPE(0, 3)},
      {IM2P_BOUNDARY_PENDING(1), IM2P_BOUNDARY_QUEUED(1),
       IM2P_BOUNDARY_PIPE(1, 0), IM2P_BOUNDARY_PIPE(1, 1),
       IM2P_BOUNDARY_PIPE(1, 2), IM2P_BOUNDARY_PIPE(1, 3)},
      {IM2P_BOUNDARY_PENDING(2), IM2P_BOUNDARY_QUEUED(2),
       IM2P_BOUNDARY_PIPE(2, 0), IM2P_BOUNDARY_PIPE(2, 1),
       IM2P_BOUNDARY_PIPE(2, 2), IM2P_BOUNDARY_PIPE(2, 3)},
      {IM2P_BOUNDARY_PENDING(3), IM2P_BOUNDARY_QUEUED(3),
       IM2P_BOUNDARY_PIPE(3, 0), IM2P_BOUNDARY_PIPE(3, 1),
       IM2P_BOUNDARY_PIPE(3, 2), IM2P_BOUNDARY_PIPE(3, 3)},
  }};
  const bool response_last = r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
      execute__DOT__mesh__DOT__io_resp_valid_RegShifted_0_0) &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT__out_last_RegShifted_0_0);
  const bool row_deq = !ROW(empty) &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT___total_rows_q_io_deq_ready_T) &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT___total_rows_q_io_deq_ready_T_1);
  const bool tag_deq = tag.len && response_last &&
      r->IM2P_SEQUENCE_TAP(IM2P_RTL_SELECTED_TOP,
          execute__DOT__mesh__DOT___tagq_io_deq_ready_T_1);
  std::cout << "RTL_BOUNDARY_V2 {\"boundary_schema\":2,\"phase\":\"" << phase
            << "\",\"ordinal\":" << ordinal
            << ",\"work_id\":" << absolute_manifest->works[ordinal].id
            << ",\"cycle\":" << d.io_coreCycle
            << ",\"public_ready\":" << ready
            << ",\"row_count\":" << row_count << ",\"rows\":[";
  const auto &ram = ROW(ram_ext__DOT__Memory);
  for (unsigned i = 0; i < row_count; ++i) {
    const unsigned packed = ram[(rp + i) % 6];
    std::cout << (i ? "," : "") << '[' << (packed & 7) << ',' << (packed >> 3) << ']';
  }
  std::cout << "],\"tag_count\":" << tag.len << ",\"tags\":[";
  for (unsigned i = 0; i < tag.len; ++i) {
    const unsigned index = (tag.read + i) % 6;
    std::cout << (i ? "," : "") << '[' << ids[index] << ',' << output_rows[index]
              << ',' << valid[index] << ',' << (valid[index] ? rob[index] : 0) << ']';
  }
  std::cout << "],\"row_read\":" << rp << ",\"row_write\":" << wp
            << ",\"row_empty\":" << unsigned(ROW(empty))
            << ",\"row_maybe_full\":" << unsigned(ROW(maybe_full))
            << ",\"row_enq_fire\":" << unsigned(ROW(do_enq))
            << ",\"row_deq_fire\":" << row_deq
            << ",\"tag_read\":" << tag.read
            << ",\"tag_write\":" << tag.write
            << ",\"tag_enq_fire\":" << unsigned(ROW(do_enq))
            << ",\"tag_deq_fire\":" << tag_deq
            << ",\"request_valid\":" << unsigned(r->IM2P_SEQUENCE_TAP(
                   IM2P_RTL_SELECTED_TOP, execute__DOT__mesh__DOT__req_valid))
            << ",\"last_response_fire\":" << response_last
            << ",\"bank_pipe\":[";
  for (unsigned bank = 0; bank < bank_pipe.size(); ++bank) {
    std::cout << (bank ? "," : "") << '[';
    for (unsigned stage = 0; stage < bank_pipe[bank].size(); ++stage)
      std::cout << (stage ? "," : "") << bank_pipe[bank][stage];
    std::cout << ']';
  }
  std::cout << "]}\n";
#undef ROW
}

std::array<std::uint64_t, 24> descriptor(const Dut &d) {
  return {d.io_work_bits_maxI, d.io_work_bits_maxJ, d.io_work_bits_maxK,
          d.io_work_bits_padI, d.io_work_bits_padJ, d.io_work_bits_padK,
          d.io_work_bits_aAddress, d.io_work_bits_bAddress, d.io_work_bits_cAddress,
          d.io_work_bits_scaleBackingAddress, d.io_work_bits_aStrideBytes,
          d.io_work_bits_bStrideBytes, d.io_work_bits_cStrideBytes,
          d.io_work_bits_scaleBase, d.io_work_bits_scaleGeneration,
          d.io_work_bits_fragmentBase, d.io_work_bits_workBase,
          d.io_work_bits_accumulate, d.io_work_bits_finalFragment,
          d.io_work_bits_firstLoop, d.io_work_bits_finalLoop,
          d.io_work_bits_logicalWorkId, d.io_work_bits_hostSlot,
          d.io_work_bits_rmdRaw};
}

void capture_rtl_tag(const Dut &d) {
  if (!active_absolute_offer)
    return;
  const auto head = tag_head(d);
  const TagEdge edge{static_cast<std::uint64_t>(d.io_coreCycle),
                     tag_enqueues, tag_dequeues, head.len, head.id};
  if (edge.cycle >= active_absolute_offer->port && rtl_tag_offer_window.size() < 5 &&
      (rtl_tag_offer_window.empty() || rtl_tag_offer_window.back().cycle != edge.cycle))
    rtl_tag_offer_window.push_back(edge);
  if (rtl_tag_recent.empty() || rtl_tag_recent.back().cycle != edge.cycle) {
    rtl_tag_recent.push_back(edge);
    if (rtl_tag_recent.size() > 5) rtl_tag_recent.erase(rtl_tag_recent.begin());
  }
}

void observe_absolute(Adapter &state) {
  const auto &d = state.dut;
  if (boundary_schema_v2) capture_rtl_queue_edge(d);
  if (active_absolute_offer && d.io_work_valid && d.io_work_ready)
    absolute_fragments += std::uint64_t(d.io_work_bits_maxI) *
                          d.io_work_bits_maxJ * d.io_work_bits_maxK;
  if (absolute_manifest && current_ordinal != UINT32_MAX &&
      current_ordinal + 1 < absolute_manifest->offers.size()) {
    const auto next = current_ordinal + 1;
    const auto cycle = static_cast<std::uint64_t>(d.io_coreCycle);
    if (cycle >= absolute_manifest->offers[next].available) {
      std::ostringstream row;
      row << "AVAIL_EDGE " << next << ' ' << cycle << " 1 0 "
          << bool(d.io_work_ready) << " 0 0";
      std::cout << row.str() << '\n';
      absolute_window.push_back(row.str());
      if (absolute_window.size() > 5) absolute_window.erase(absolute_window.begin());
    }
  }
  if (active_absolute_offer && !absolute_fire) {
    const auto cycle = static_cast<std::uint64_t>(d.io_coreCycle);
    const bool available = cycle >= active_absolute_offer->available;
    const bool port_valid = d.io_work_valid && d.io_work_bits_firstLoop;
    const bool raw_ready = d.io_work_ready;
    const bool policy_credit = absolute_credit;
    const bool fire = available && port_valid && raw_ready && policy_credit;
    if (cycle >= active_absolute_offer->port) {
      std::ostringstream row;
      row << "OFFER_EDGE " << current_ordinal << ' ' << cycle << ' '
          << available << ' ' << port_valid << ' ' << raw_ready << ' '
          << policy_credit << ' ' << fire;
      std::cout << row.str() << '\n';
      absolute_window.push_back(row.str());
      if (absolute_window.size() > 5) absolute_window.erase(absolute_window.begin());
      check(port_valid, "absolute port offer missing valid descriptor");
      const auto bits = descriptor(d);
      check(!absolute_held || bits == held_descriptor,
            "absolute port descriptor changed while awaiting ready");
      held_descriptor = bits;
      absolute_held = true;
    }
    check(!port_valid || cycle >= active_absolute_offer->port,
          "absolute descriptor appeared before declared port offer");
    check(!port_valid || available, "absolute descriptor appeared before availability");
    check(!port_valid || !raw_ready || policy_credit,
          "RTL accepted absolute offer without policy credit");
    if (fire) {
      if (boundary_schema_v2 && current_ordinal == 1)
        emit_rtl_boundary(state, "WORK1_FIRE", 1);
      absolute_fire = cycle;
      absolute_credit = false;
    }
  }
  observe(state);
  capture_rtl_tag(d);
}

struct Prediction {
  std::uint64_t resource = 0;
  unsigned scratchpad_half = 0, accumulator_half = 0;
};

Prediction estimate(const WorkInput &w, unsigned period, std::uint64_t accepted,
                    unsigned scratchpad_half, unsigned accumulator_half,
                    std::uint64_t offset, bool emit_events) {
  im2p_cycle_model_config_t config;
  im2p_cycle_model_config_init(&config);
  config.hardware = {IM2P_ACTIVATION_BITS, IM2P_OPERAND_BITS, dim, 32, 32, 4,
                     IM2P_BANK_ROWS, IM2P_ACC_ROWS, sp_bytes, acc_bytes, 4, 2};
  config.timing.read_ready_period = period;
  config.timing.backing_cycle_offset = offset;
  config.max_trace_events = 10000000;
  std::unique_ptr<im2p_cycle_model_t, decltype(&im2p_cycle_model_destroy)> model(
      im2p_cycle_model_create(&config), im2p_cycle_model_destroy);
  check(bool(model), "service model rejected profile");
  im2p_cycle_request_t request;
  im2p_cycle_request_init(&request);
  request.m = w.m; request.n = w.n; request.k = w.k;
  request.tile_i = w.ti; request.tile_j = w.tj; request.tile_k = w.tk;
  request.activation_stride_bytes = w.a_stride;
  request.weight_stride_bytes = w.b_stride;
  request.output_stride_bytes = w.c_stride;
  request.scale_stride_elements = w.s_stride;
  request.accepted_cycle = accepted;
  request.logical_work_id = w.id;
  request.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
  request.record_events = 1;
  request.initial_scratchpad_half = scratchpad_half;
  request.initial_accumulator_half = accumulator_half;
  const im2p_compact_runs_t runs{IM2P_COMPACT_RUNS_VERSION, sizeof(runs),
                                 static_cast<std::uint32_t>(w.original_k),
                                 w.runs.size(), w.runs.data()};
  im2p_cycle_result_t result{};
  im2p_cycle_service_result_t service{};
  check(im2p_cycle_estimate_service(model.get(), &request,
              w.kind == 'R' ? &runs : nullptr, &result, &service) == IM2P_CYCLE_OK,
        im2p_cycle_model_error(model.get()));
  unsigned releases = 0;
  for (std::uint64_t i = 0; i < im2p_cycle_model_event_count(model.get()); ++i) {
    im2p_cycle_event_t e{};
    check(im2p_cycle_model_event(model.get(), i, &e) == IM2P_CYCLE_OK,
          "composition model event read failed");
    const std::string_view name = im2p_cycle_event_name(e.type);
    releases += name == "scale_release";
    if (emit_events && selected(name))
      std::cout << "MODEL_EVENT " << w.ordinal << ' ' << w.id << ' '
                << e.cycle << ' ' << name << '\n';
  }
  if (emit_events)
    std::cout << "MODEL_COUNTS " << w.ordinal << ' ' << w.id << ' '
              << result.loop_count << ' ' << result.scale_request_count << ' '
              << result.scale_response_count << ' ' << releases << ' '
              << result.load_request_count << ' ' << result.load_response_count << ' '
              << result.store_request_count << ' ' << result.store_response_count << ' '
              << service.result_ready_cycle << ' ' << service.final_scale_release_cycle << ' '
              << service.resource_ready_cycle << ' ' << service.next_scratchpad_half << ' '
              << service.next_accumulator_half << '\n';
  return {service.resource_ready_cycle, service.next_scratchpad_half,
          service.next_accumulator_half};
}

Prediction measure(Adapter &state, const WorkInput &w, unsigned period,
                   const Prediction &previous, std::uint64_t previous_rtl_ready) {
  const std::uint64_t arrival = w.ordinal ? previous_rtl_ready + w.phase : 0;
  if (!w.ordinal) {
    while (state.dut.io_coreCycle % period != w.phase) state.step();
  }
  else
    while (state.dut.io_coreCycle < arrival) state.step();
  if (w.ordinal)
    check(public_ready(state), "declared offer preceded public readiness");
  const auto offered = state.dut.io_coreCycle;
  const auto predicted_offer = w.ordinal ? previous.resource + w.phase : offered;
  const auto before_loops = state.loops, before_final = accepted_final_fragments;
  const auto before_reads = state.scale_reads, before_responses = state.scale_responses;
  const auto before_load_requests = state.dut.io_loadRequests;
  const auto before_load_responses = state.dut.io_loadResponses;
  const auto before_store_requests = state.dut.io_storeRequests;
  const auto before_store_responses = state.dut.io_storeResponses;
  const auto offset = state.cycle - state.dut.io_coreCycle;
  const auto carry = tag_head(state.dut);
  const auto prior_enqueues = tag_enqueues, prior_dequeues = tag_dequeues;
  milestones = {};
  current_ordinal = w.ordinal;
  current_work_id = w.id;
  first_output_seen = first_dequeue_seen = false;
  max_tag_queue_len = 0;
  tag_full_backpressure_cycles = 0;
  std::vector<std::int32_t> output;
  if (w.kind == 'D') execute_dense(state, w, output);
  else execute_residual(state, w, output);
  check(output.size() == w.m * w.n &&
        std::all_of(output.begin(), output.end(), [&](auto value) { return value == w.k; }),
        "composition numeric result differs");
  bool ready = false;
  for (unsigned wait = 0; wait < 2000000; ++wait) {
    if (public_ready(state)) { ready = true; break; }
    state.step();
  }
  check(ready, "composition public resource readiness stalled");
  milestones.reusable = state.dut.io_coreCycle;
  const auto head = tag_head(state.dut);
  current_ordinal = UINT32_MAX;
  check(milestones.accepted > 0 && milestones.accepted < milestones.result &&
        milestones.result < milestones.release && milestones.release < milestones.reusable,
        "composition milestone order differs");
  check(milestones.scratchpad_half == before_loops % 2 &&
        milestones.accumulator_half == before_final % 2,
        "composition RTL memory half differs");
  const auto predicted_accepted = predicted_offer;
  const auto predicted = estimate(w, period, predicted_accepted,
      previous.scratchpad_half, previous.accumulator_half, offset, false);
  std::cout << "ACCEPTANCE " << w.ordinal << ' ' << w.id << ' '
            << offered << ' ' << milestones.accepted << ' ' << predicted_offer << ' '
            << predicted_accepted << ' ' << milestones.reusable << ' '
            << predicted.resource << '\n';
  estimate(w, period, milestones.accepted, before_loops % 2,
           before_final % 2, offset, true);
  std::cout << "COMPOSITION_WORK {\"ordinal\":" << w.ordinal
            << ",\"work_id\":" << w.id << ",\"work_binding\":\"" << w.binding
            << "\",\"slot\":" << w.slot << ",\"parent_id\":" << w.parent
            << ",\"call_id\":" << w.call << ",\"arrival_delay\":" << w.phase
            << ",\"offered\":" << offered << ",\"accepted\":" << milestones.accepted
            << ",\"predicted_offered\":" << predicted_offer
            << ",\"predicted_accepted\":" << predicted_accepted
            << ",\"result_ready\":" << milestones.result
            << ",\"final_scale_release\":" << milestones.release
            << ",\"resource_ready\":" << milestones.reusable
            << ",\"predicted_resource_ready\":" << predicted.resource
            << ",\"submissions\":" << milestones.submissions
            << ",\"scale_read_requests\":" << state.scale_reads - before_reads
            << ",\"scale_read_responses\":" << state.scale_responses - before_responses
            << ",\"scale_release_count\":" << milestones.release_count
            << ",\"load_requests\":" << state.dut.io_loadRequests - before_load_requests
            << ",\"load_responses\":" << state.dut.io_loadResponses - before_load_responses
            << ",\"store_requests\":" << state.dut.io_storeRequests - before_store_requests
            << ",\"store_responses\":" << state.dut.io_storeResponses - before_store_responses
            << ",\"initial_scratchpad_half\":" << before_loops % 2
            << ",\"initial_accumulator_half\":" << before_final % 2
            << ",\"next_scratchpad_half\":" << state.loops % 2
            << ",\"next_accumulator_half\":" << accepted_final_fragments % 2
            << ",\"carry_in_tag_len\":" << carry.len
            << ",\"mesh_tag_queue_len\":" << head.len
            << ",\"mesh_tag_head_id\":" << head.id
            << ",\"mesh_tag_read_pointer\":" << head.read
            << ",\"mesh_tag_write_pointer\":" << head.write
            << ",\"mesh_tag_enqueues\":" << tag_enqueues - prior_enqueues
            << ",\"mesh_tag_dequeues\":" << tag_dequeues - prior_dequeues
            << ",\"mesh_tag_max_occupancy\":" << max_tag_queue_len
            << ",\"mesh_tag_full_backpressure_cycles\":" << tag_full_backpressure_cycles
            << ",\"numeric_pass\":true}\n";
  return predicted;
}

void measure_absolute(Adapter &state, const WorkInput &w, const AbsoluteOffer &offer) {
  if (state.dut.io_coreCycle > offer.port)
    throw std::runtime_error("declared absolute port offer missed: ordinal=" +
        std::to_string(w.ordinal) + " port=" + std::to_string(offer.port) +
        " current=" + std::to_string(state.dut.io_coreCycle));
  while (state.dut.io_coreCycle < offer.port) state.step();
  if (boundary_schema_v2 && w.ordinal == 1)
    emit_rtl_boundary(state, "WORK1_OFFER", 1);
  const auto before_loops = state.loops, before_final = accepted_final_fragments;
  const auto before_reads = state.scale_reads, before_responses = state.scale_responses;
  const auto before_load_requests = state.dut.io_loadRequests;
  const auto before_load_responses = state.dut.io_loadResponses;
  const auto before_store_requests = state.dut.io_storeRequests;
  const auto before_store_responses = state.dut.io_storeResponses;
  const auto carry = tag_head(state.dut);
  const auto prior_enqueues = tag_enqueues, prior_dequeues = tag_dequeues;
  milestones = {};
  current_ordinal = w.ordinal;
  current_work_id = w.id;
  first_output_seen = first_dequeue_seen = false;
  max_tag_queue_len = 0;
  tag_full_backpressure_cycles = 0;
  observed_full_queue_cycles = observed_full_stall_cycles = 0;
  observed_full_dequeue_cycles = 0;
  observed_legacy_heuristic_cycles = 0;
  observed_first_full_cycle = observed_first_stall_cycle = 0;
  observed_read_wraps = observed_write_wraps = observed_matmul_id_wraps = 0;
  observed_first_full_head_id = observed_first_full_read = observed_first_full_write = 0;
  prior_tag_observer_cycle = UINT64_MAX;
  absolute_fire = 0;
  absolute_fragments = 0;
  absolute_held = false;
  absolute_credit = true;
  absolute_window.clear();
  rtl_tag_offer_window.clear();
  rtl_tag_recent.clear();
  active_absolute_offer = &offer;
  std::vector<std::int32_t> output;
  if (w.kind == 'D') execute_dense(state, w, output);
  else execute_residual(state, w, output);
  const bool numeric_selected = numeric_payload && w.id == numeric_work_id;
  check(output.size() == w.m * w.n &&
        (numeric_selected
             ? output == numeric_payload->expected
             : std::all_of(output.begin(), output.end(),
                           [&](auto value) { return value == w.k; })),
        "absolute composition numeric result differs");
  bool ready = false;
  for (unsigned wait = 0; wait < 2000000; ++wait) {
    if (public_ready(state)) { ready = true; break; }
    state.step();
  }
  check(ready, "absolute composition public resource readiness stalled");
  if (boundary_schema_v2) capture_rtl_queue_edge(state.dut);
  milestones.reusable = state.dut.io_coreCycle;
  if (boundary_schema_v2 && w.ordinal == 0)
    emit_rtl_boundary(state, "PUBLIC_READY", 0);
  if (absolute_manifest && w.ordinal + 1 < absolute_manifest->offers.size() &&
      state.dut.io_coreCycle >= absolute_manifest->offers[w.ordinal + 1].available) {
    std::ostringstream row;
    row << "AVAIL_EDGE " << w.ordinal + 1 << ' ' << state.dut.io_coreCycle
        << " 1 0 " << bool(state.dut.io_work_ready) << " 1 0";
    std::cout << row.str() << '\n';
    absolute_window.push_back(row.str());
    if (absolute_window.size() > 5) absolute_window.erase(absolute_window.begin());
  }
  const auto head = tag_head(state.dut);
  capture_rtl_tag(state.dut);
  active_absolute_offer = nullptr;
  current_ordinal = UINT32_MAX;
  check(absolute_fire && milestones.accepted == absolute_fire &&
        offer.available <= offer.port && offer.port <= absolute_fire,
        "absolute offer acceptance differs from observed valid/ready edge");
  check(milestones.accepted < milestones.result && milestones.result < milestones.release &&
        milestones.release < milestones.reusable, "absolute composition milestone order differs");
  check(milestones.scratchpad_half == before_loops % 2 &&
        milestones.accumulator_half == before_final % 2,
        "absolute composition RTL memory half differs");
  std::cout << "COMPOSITION_WORK {\"ordinal\":" << w.ordinal
            << ",\"work_id\":" << w.id << ",\"work_binding\":\"" << w.binding
            << "\",\"slot\":" << w.slot << ",\"parent_id\":" << w.parent
            << ",\"call_id\":" << w.call
            << ",\"request_available_cycle\":" << offer.available
            << ",\"port_offer_cycle\":" << offer.port
            << ",\"offered\":" << offer.port << ",\"accepted\":" << absolute_fire
            << ",\"result_ready\":" << milestones.result
            << ",\"final_scale_release\":" << milestones.release
            << ",\"resource_ready\":" << milestones.reusable
            << ",\"submissions\":" << milestones.submissions
            << ",\"physical_fragment_count\":" << absolute_fragments
            << ",\"scale_read_requests\":" << state.scale_reads - before_reads
            << ",\"scale_read_responses\":" << state.scale_responses - before_responses
            << ",\"scale_release_count\":" << milestones.release_count
            << ",\"load_requests\":" << state.dut.io_loadRequests - before_load_requests
            << ",\"load_responses\":" << state.dut.io_loadResponses - before_load_responses
            << ",\"store_requests\":" << state.dut.io_storeRequests - before_store_requests
            << ",\"store_responses\":" << state.dut.io_storeResponses - before_store_responses
            << ",\"initial_scratchpad_half\":" << before_loops % 2
            << ",\"initial_accumulator_half\":" << before_final % 2
            << ",\"next_scratchpad_half\":" << state.loops % 2
            << ",\"next_accumulator_half\":" << accepted_final_fragments % 2
            << ",\"carry_in_tag_len\":" << carry.len
            << ",\"mesh_tag_queue_len\":" << head.len
            << ",\"mesh_tag_head_id\":" << head.id
            << ",\"mesh_tag_read_pointer\":" << head.read
            << ",\"mesh_tag_write_pointer\":" << head.write
            << ",\"mesh_tag_enqueues\":" << tag_enqueues - prior_enqueues
            << ",\"mesh_tag_dequeues\":" << tag_dequeues - prior_dequeues
            << ",\"mesh_tag_max_occupancy\":" << max_tag_queue_len
            << ",\"mesh_tag_full_backpressure_cycles\":"
            << (tag_observer_v2 ? observed_full_stall_cycles : tag_full_backpressure_cycles)
            << ",\"numeric_pass\":true}\n";
  if (numeric_selected)
    std::cout << "NUMERIC_WORK {\"ordinal\":" << w.ordinal
              << ",\"work_id\":" << w.id
              << ",\"expected_count\":" << numeric_payload->expected.size()
              << ",\"actual_count\":" << output.size()
              << ",\"first_expected\":" << numeric_payload->expected.front()
              << ",\"first_actual\":" << output.front()
              << ",\"numeric_pass\":true}\n";
  if (tag_observer_v2)
    std::cout << "TAG_PRESSURE_V2 {\"ordinal\":" << w.ordinal
              << ",\"work_id\":" << w.id
              << ",\"status\":\""
              << (observed_full_stall_cycles ? "FULL_STALL_OBSERVED" : "FULL_STALL_NOT_OBSERVED")
              << "\",\"control_queue_layout_sha256\":\""
              << "c1431c91515b9e0ef892934778b7ecc1fe9f9a489ee24b4ff3a686245a175610"
              << "\",\"full_queue_cycles\":" << observed_full_queue_cycles
              << ",\"full_stall_cycles\":" << observed_full_stall_cycles
              << ",\"legacy_heuristic_cycles\":" << observed_legacy_heuristic_cycles
              << ",\"full_dequeue_cycles\":" << observed_full_dequeue_cycles
              << ",\"first_full_cycle\":" << observed_first_full_cycle
              << ",\"first_stall_cycle\":" << observed_first_stall_cycle
              << ",\"first_full_head_id\":" << observed_first_full_head_id
              << ",\"first_full_read_pointer\":" << observed_first_full_read
              << ",\"first_full_write_pointer\":" << observed_first_full_write
              << ",\"read_pointer_wraps\":" << observed_read_wraps
              << ",\"write_pointer_wraps\":" << observed_write_wraps
              << ",\"matmul_id_wraps\":" << observed_matmul_id_wraps << "}\n";
  emit_tag_window("RTL_TAG_EDGE", w.ordinal, "OFFER", rtl_tag_offer_window);
  emit_tag_window("RTL_TAG_EDGE", w.ordinal, "RESOURCE", rtl_tag_recent);
}

std::string native_boundary_row(const im2p_cycle_sequence_t *sequence,
                                std::string_view phase, unsigned ordinal) {
  check(absolute_manifest && ordinal < absolute_manifest->works.size(),
        "native boundary work identity absent");
  im2p_cycle_sequence_domain_snapshot_t snapshot;
  im2p_cycle_sequence_domain_snapshot_init(&snapshot);
  check(im2p_cycle_sequence_get_domain_snapshot(sequence, &snapshot) ==
            IM2P_CYCLE_SEQUENCE_OK, "native boundary getter unavailable");
  std::ostringstream out;
  out << "MODEL_BOUNDARY_V2 {\"boundary_schema\":2,\"phase\":\"" << phase
      << "\",\"ordinal\":" << ordinal
      << ",\"work_id\":" << absolute_manifest->works[ordinal].id
      << ",\"cycle\":" << snapshot.cursor
      << ",\"resource_ready\":" << snapshot.resource_ready
      << ",\"ready_violation_mask\":" << snapshot.ready_violation_mask
      << ",\"row_count\":" << snapshot.row_count << ",\"rows\":[";
  for (unsigned i = 0; i < snapshot.row_count; ++i)
    out << (i ? "," : "") << '[' << snapshot.rows[i].id << ','
        << snapshot.rows[i].rows << ']';
  out << "],\"tag_count\":" << snapshot.tag_count << ",\"tags\":[";
  for (unsigned i = 0; i < snapshot.tag_count; ++i) {
    const auto &tag = snapshot.tags[i];
    out << (i ? "," : "") << '[' << tag.id << ','
        << tag.preload_output_rows << ',' << tag.rob_valid << ','
        << (tag.rob_valid ? tag.rob_id : 0) << ']';
  }
  out << "],\"tag_mesh_total_rows\":[";
  for (unsigned i = 0; i < snapshot.tag_count; ++i)
    out << (i ? "," : "") << snapshot.tags[i].rows;
  out << "],\"bank_pipe\":[";
  for (unsigned bank = 0; bank < 4; ++bank) {
    const auto &bits = snapshot.banks[bank];
    out << (bank ? "," : "") << '[' << bits.pending << ',' << bits.queued;
    for (unsigned stage = 0; stage < 4; ++stage)
      out << ',' << ((bits.pipe_valid_mask >> stage) & 1);
    out << ']';
  }
  out << "]}\n";
  return out.str();
}

void emit_native_boundary(const im2p_cycle_sequence_t *sequence,
                          std::string_view phase, unsigned ordinal) {
  std::cout << native_boundary_row(sequence, phase, ordinal);
}

QueueSnapshot native_queue_snapshot(const im2p_cycle_sequence_t *sequence) {
  im2p_cycle_sequence_domain_snapshot_t domain;
  im2p_cycle_sequence_domain_snapshot_init(&domain);
  check(im2p_cycle_sequence_get_domain_snapshot(sequence, &domain) ==
            IM2P_CYCLE_SEQUENCE_OK, "native queue domain getter unavailable");
  im2p_cycle_sequence_tag_state_t tag;
  im2p_cycle_sequence_tag_state_init(&tag);
  check(im2p_cycle_sequence_get_tag_state(sequence, &tag) ==
            IM2P_CYCLE_SEQUENCE_OK, "native queue tag getter unavailable");
  check(domain.generation == tag.generation && domain.cursor == tag.cursor &&
            domain.row_count <= 6 && domain.tag_count <= 6 &&
            domain.tag_count == tag.queue_len && tag.enqueues >= tag.dequeues &&
            tag.enqueues - tag.dequeues == tag.queue_len,
        "native queue getters disagree at cursor");
  QueueSnapshot s;
  s.generation = domain.generation;
  s.cycle = domain.cursor;
  s.row_count = domain.row_count;
  s.tag_count = domain.tag_count;
  s.tag_enqueues = tag.enqueues;
  s.tag_dequeues = tag.dequeues;
  for (unsigned i = 0; i < s.row_count; ++i)
    s.rows[i] = {domain.rows[i].id, domain.rows[i].rows};
  for (unsigned i = 0; i < s.tag_count; ++i) {
    const auto &entry = domain.tags[i];
    s.tags[i] = {entry.id, entry.preload_output_rows, entry.rob_valid,
                 entry.rob_valid ? entry.rob_id : 0};
    if (queue_edge_schema_v2) {
      check(entry.rob_valid <= 1 && entry.preload_accumulate <= 1,
            "native queue payload V2 source validity invalid");
      s.tag_payloads[i] = {entry.id, entry.rob_valid,
                           entry.rob_valid ? entry.rob_id : 0, entry.rows,
                           entry.preload_src, entry.preload_dst,
                           entry.preload_output_rows, entry.preload_output_cols,
                           entry.preload_accumulate, entry.origin_generation,
                           entry.origin_ordinal, entry.origin_work_id};
    }
  }
  if (queue_edge_schema_v2) s.tag_payload_width = 12;
  return s;
}

void capture_native_queue_edge(const QueueSnapshot &old,
                               const QueueSnapshot &next) {
  check(old.generation == next.generation && old.cycle < UINT64_MAX &&
            next.cycle == old.cycle + 1 &&
            next.tag_enqueues >= old.tag_enqueues &&
            next.tag_dequeues >= old.tag_dequeues,
        "native queue edge lacks one successful advance");
  const auto tag_enq = next.tag_enqueues - old.tag_enqueues;
  const auto tag_deq = next.tag_dequeues - old.tag_dequeues;
  check(tag_enq <= 1 && tag_deq <= 1,
        "native queue edge inferred tag fire exceeds one");
  const int row_deq = static_cast<int>(old.row_count) +
                      static_cast<int>(tag_enq) - static_cast<int>(next.row_count);
  check(row_deq >= 0 && row_deq <= 1,
        "native queue edge inferred row fire exceeds one");
  if (tag_enq || tag_deq || old.row_count == 6 || old.tag_count == 6 ||
      old.row_count != next.row_count || old.tag_count != next.tag_count ||
      old.rows != next.rows || old.tags != next.tags ||
      (queue_edge_schema_v2 && old.tag_payloads != next.tag_payloads))
    emit_queue_edge(queue_edge_schema_v2 ? "MODEL_QUEUE_EDGE_V2" :
                    "MODEL_QUEUE_EDGE_V1", old, next, false);
}

void measure_native_absolute(const AbsoluteManifest &manifest, std::uint64_t offset) {
  check(offset <= UINT32_MAX, "native backing clock offset overflow");
  im2p_cycle_sequence_config_t config;
  im2p_cycle_sequence_config_init(&config);
  config.hardware = {IM2P_ACTIVATION_BITS, IM2P_OPERAND_BITS, dim, 32, 32, 4,
                     IM2P_BANK_ROWS, IM2P_ACC_ROWS, sp_bytes, acc_bytes, 4, 2};
  config.timing.read_ready_period = manifest.period;
  config.timing.backing_cycle_offset = static_cast<std::uint32_t>(offset);
  config.max_trace_events = 10000000;
  im2p_cycle_sequence_t *created = nullptr;
  check(im2p_cycle_sequence_create(&config, &created) == IM2P_CYCLE_SEQUENCE_OK,
        "native sequence creation failed");
  std::unique_ptr<im2p_cycle_sequence_t, decltype(&im2p_cycle_sequence_destroy)> sequence(
      created, im2p_cycle_sequence_destroy);
  check(im2p_cycle_sequence_reset(sequence.get()) == IM2P_CYCLE_SEQUENCE_OK,
        "native sequence reset failed");
  std::cout << "MODEL_RUN {\"instance_count\":1,\"reset_count\":1,\"period\":"
            << manifest.period << ",\"work_count\":" << manifest.works.size()
            << ",\"stimulus_sha256\":\"" << manifest.digest
            << "\",\"generation\":1}\n";

  std::vector<unsigned> releases(manifest.works.size());
  std::vector<std::uint64_t> selected_event_counts(manifest.works.size());
  std::vector<std::array<unsigned, 2>> initial_halves(manifest.works.size());
  std::vector<std::array<std::uint64_t, 2>> initial_tag_counts(manifest.works.size());
  std::vector<unsigned> model_tag_max(manifest.works.size());
  std::vector<TagEdge> model_tag_offer_window, model_tag_recent;
  std::array<im2p_cycle_sequence_event_t, 256> events{};
  std::size_t reported = 0;
  const auto status = [&] {
    im2p_cycle_sequence_status_t value;
    im2p_cycle_sequence_status_init(&value);
    check(im2p_cycle_sequence_get_status(sequence.get(), &value) ==
              IM2P_CYCLE_SEQUENCE_OK, "native sequence status failed");
    return value;
  };
  const auto tag_state = [&] {
    im2p_cycle_sequence_tag_state_t value;
    im2p_cycle_sequence_tag_state_init(&value);
    check(im2p_cycle_sequence_get_tag_state(sequence.get(), &value) ==
              IM2P_CYCLE_SEQUENCE_OK, "native tag state read failed");
    return value;
  };
  const auto flush = [&] {
    unsigned consumed = 0;
    for (;;) {
      std::uint64_t count = 0;
      check(im2p_cycle_sequence_read_events(sequence.get(), events.data(), events.size(),
                                            &count) == IM2P_CYCLE_SEQUENCE_OK,
            "native sequence event read failed");
      if (!count) break;
      consumed += count;
      for (std::uint64_t i = 0; i < count; ++i) {
        const auto &event = events[i];
        check(event.work_ordinal && event.work_ordinal <= manifest.works.size(),
              "native event work ordinal differs");
        const auto ordinal = event.work_ordinal - 1;
        const auto &work = manifest.works[ordinal];
        check(event.event.logical_work_id == work.id,
              "native event logical work ID differs");
        const std::string_view name = im2p_cycle_event_name(event.event.type);
        releases[ordinal] += name == "scale_release";
        if (selected(name)) {
          if (tag_observer_v2) ++selected_event_counts[ordinal];
          std::cout << "MODEL_EVENT " << ordinal << ' ' << work.id << ' '
                    << event.event.cycle << ' ' << name << '\n';
        }
      }
    }
    const auto current = status();
    const auto tags = tag_state();
    check(tags.cursor == current.cursor && tags.generation == current.generation &&
              tags.enqueues >= tags.dequeues &&
              tags.enqueues - tags.dequeues == tags.queue_len,
          "native tag snapshot differs from session cursor or queue accounting");
    if (!model_tag_offer_window.empty() && model_tag_offer_window.size() < 5 &&
        tags.cursor == model_tag_offer_window.back().cycle + 1)
      model_tag_offer_window.push_back({tags.cursor, tags.enqueues, tags.dequeues,
                                        tags.queue_len, tags.head_id});
    if (reported < manifest.works.size() &&
        (current.has_active || current.has_report)) {
      model_tag_max[reported] = std::max(model_tag_max[reported], tags.queue_len);
      if (model_tag_recent.empty() || model_tag_recent.back().cycle != tags.cursor) {
        model_tag_recent.push_back({tags.cursor, tags.enqueues, tags.dequeues,
                                    tags.queue_len, tags.head_id});
        if (model_tag_recent.size() > 5)
          model_tag_recent.erase(model_tag_recent.begin());
      }
    }
    if (current.has_report) {
      if (boundary_schema_v2 && reported == 0)
        emit_native_boundary(sequence.get(), "PUBLIC_READY", 0);
      im2p_cycle_sequence_report_t report;
      im2p_cycle_sequence_report_init(&report);
      check(im2p_cycle_sequence_pop_report(sequence.get(), &report) ==
                IM2P_CYCLE_SEQUENCE_OK && reported < manifest.works.size() &&
                report.logical_work_id == manifest.works[reported].id,
            "native report order or identity differs");
      const auto &work = manifest.works[reported];
      const auto &offer = manifest.offers[reported];
      const auto &counts = report.counters;
      check(tags.enqueues >= initial_tag_counts[reported][0] &&
                tags.dequeues >= initial_tag_counts[reported][1] &&
                !tags.backpressure_mapped &&
                tags.full_backpressure_cycles == UINT64_MAX,
            "native tag counter or backpressure mapping differs");
      std::cout << "MODEL_WORK {\"ordinal\":" << reported
                << ",\"work_id\":" << work.id << ",\"work_binding\":\""
                << work.binding << "\",\"request_available_cycle\":" << offer.available
                << ",\"port_offer_cycle\":" << offer.port
                << ",\"offered\":" << report.offered_cycle
                << ",\"accepted\":" << report.accepted_cycle
                << ",\"result_ready\":" << report.result_ready_cycle
                << ",\"final_scale_release\":" << report.final_scale_release_cycle
                << ",\"resource_ready\":" << report.resource_ready_cycle
                << ",\"submissions\":" << counts.loop_count
                << ",\"scale_read_requests\":" << counts.scale_request_count
                << ",\"scale_read_responses\":" << counts.scale_response_count
                << ",\"scale_release_count\":" << releases[reported]
                << ",\"load_requests\":" << counts.load_request_count
                << ",\"load_responses\":" << counts.load_response_count
                << ",\"store_requests\":" << counts.store_request_count
                << ",\"store_responses\":" << counts.store_response_count
                << ",\"initial_scratchpad_half\":" << initial_halves[reported][0]
                << ",\"initial_accumulator_half\":" << initial_halves[reported][1]
                << ",\"next_scratchpad_half\":" << report.next_scratchpad_half
                << ",\"next_accumulator_half\":" << report.next_accumulator_half
                << ",\"planner_loop_count\":" << counts.planner_loop_count
                << ",\"fragment_count\":" << counts.fragment_count
                << ",\"event_count\":" << report.event_count
                << ",\"mesh_tag_queue_len\":" << tags.queue_len
                << ",\"mesh_tag_head_id\":" << tags.head_id
                << ",\"mesh_tag_enqueues\":"
                << tags.enqueues - initial_tag_counts[reported][0]
                << ",\"mesh_tag_dequeues\":"
                << tags.dequeues - initial_tag_counts[reported][1]
                << ",\"mesh_tag_max_occupancy\":" << model_tag_max[reported]
                << ",\"mesh_tag_full_backpressure_mapped\":false"
                << ",\"mesh_tag_full_backpressure_cycles\":null}\n";
      emit_tag_window("MODEL_TAG_EDGE", reported, "OFFER", model_tag_offer_window);
      emit_tag_window("MODEL_TAG_EDGE", reported, "RESOURCE", model_tag_recent);
      ++reported;
      ++consumed;
    }
    return consumed;
  };
  const auto advance_one = [&] {
    for (;;) {
      const auto before = status();
      check(before.cursor < config.max_session_cycles,
            "native sequence session cycle budget exhausted");
      const auto old_queue = boundary_schema_v2
          ? native_queue_snapshot(sequence.get()) : QueueSnapshot{};
      if (boundary_schema_v2)
        check(old_queue.cycle == before.cursor &&
                  old_queue.generation == before.generation,
              "native queue preedge differs from session status");
      const int code = im2p_cycle_sequence_advance_until(sequence.get(), before.cursor + 1);
      const auto consumed = flush();
      if (code == IM2P_CYCLE_SEQUENCE_WOULD_BLOCK) {
        check(consumed, "native sequence blocked without consumable events or report");
        continue;
      }
      if (code != IM2P_CYCLE_SEQUENCE_OK &&
          code != IM2P_CYCLE_SEQUENCE_INCOMPLETE)
        throw std::runtime_error("native sequence advance failed at cycle " +
                                 std::to_string(before.cursor) + " code " +
                                 std::to_string(code));
      if (boundary_schema_v2) capture_native_queue_edge(
          old_queue, native_queue_snapshot(sequence.get()));
      break;
    }
  };
  for (std::size_t ordinal = 0; ordinal < manifest.works.size(); ++ordinal) {
    const auto &work = manifest.works[ordinal];
    const auto &offer = manifest.offers[ordinal];
    check(offer.port < config.max_session_cycles, "native port exceeds session budget");
    while (status().cursor < offer.port) advance_one();
    const auto before = status();
    if (boundary_schema_v2 && ordinal == 1)
      emit_native_boundary(sequence.get(), "WORK1_OFFER", 1);
    initial_halves[ordinal] = {before.next_scratchpad_half,
                               before.next_accumulator_half};
    const auto tags = tag_state();
    initial_tag_counts[ordinal] = {tags.enqueues, tags.dequeues};
    model_tag_max[ordinal] = tags.queue_len;
    model_tag_offer_window.clear();
    model_tag_recent.clear();
    model_tag_offer_window.push_back({before.cursor, tags.enqueues, tags.dequeues,
                                      tags.queue_len, tags.head_id});
    im2p_cycle_sequence_descriptor_t descriptor;
    im2p_cycle_sequence_descriptor_init(&descriptor);
    descriptor.logical_work_id = work.id;
    descriptor.m = work.m; descriptor.n = work.n; descriptor.k = work.k;
    descriptor.tile_i = work.ti; descriptor.tile_j = work.tj; descriptor.tile_k = work.tk;
    descriptor.activation_stride_bytes = work.a_stride;
    descriptor.weight_stride_bytes = work.b_stride;
    descriptor.output_stride_bytes = work.c_stride;
    descriptor.scale_stride_elements = work.s_stride;
    const im2p_compact_runs_t runs{IM2P_COMPACT_RUNS_VERSION, sizeof(runs),
                                   static_cast<std::uint32_t>(work.original_k),
                                   work.runs.size(), work.runs.data()};
    descriptor.compact_runs = work.kind == 'R' ? &runs : nullptr;
    descriptor.submission = IM2P_CYCLE_BLOCK_SUBMISSIONS;
    descriptor.record_events = 1;
    const int offered = im2p_cycle_sequence_offer(sequence.get(), &descriptor, offer.port);
    if (offered != IM2P_CYCLE_SEQUENCE_OK)
      throw std::runtime_error("native sequence offer failed at work " +
                               std::to_string(ordinal) + " port " +
                               std::to_string(offer.port) + " cursor " +
                               std::to_string(before.cursor) + " active " +
                               std::to_string(before.has_active) + " pending " +
                               std::to_string(before.has_pending) + " code " +
                               std::to_string(offered));
    for (;;) {
      const auto edge = status();
      const bool ready = !edge.has_active && !edge.has_report;
      const std::string native_fire_preedge = boundary_schema_v2 && ordinal == 1
          ? native_boundary_row(sequence.get(), "WORK1_FIRE", 1) : std::string{};
      advance_one();
      const auto after = status();
      const bool fire = after.has_accepted && after.accepted_work_id == work.id &&
                        after.accepted_cycle == edge.cursor;
      if (fire && !native_fire_preedge.empty())
        std::cout << native_fire_preedge;
      std::cout << "MODEL_OFFER_EDGE " << ordinal << ' ' << edge.cursor
                << " 1 1 " << ready << " 1 " << fire << '\n';
      check(!fire || ready, "native sequence accepted before ready");
      if (fire) break;
    }
  }
  while (reported < manifest.works.size()) advance_one();
  if (boundary_schema_v2) {
    const auto terminal = native_queue_snapshot(sequence.get());
    check(terminal.row_count < 6 && terminal.tag_count < 6,
          "native full queue lacks a terminal successor");
  }
  if (tag_observer_v2)
    for (std::size_t ordinal = 0; ordinal < selected_event_counts.size(); ++ordinal)
      std::cout << "MODEL_SELECTED_EVENT_COUNT " << ordinal << ' '
                << selected_event_counts[ordinal] << '\n';
}
}

int main(int argc, char **argv) {
  try {
    if (argc == 2 && std::string_view(argv[1]) == "--help") {
      std::cout << "usage: compositional-probe <v1-or-v2-numeric-stimulus> "
                   "[--values PATH --work-id ID] [--tag-observer-v2] "
                   "[--boundary-schema=2] [--queue-edge-schema=2]\n";
      return 0;
    }
    int argument_count = argc;
    while (argument_count > 2) {
      const std::string_view option(argv[argument_count - 1]);
      if (option == "--tag-observer-v2") {
        check(!tag_observer_v2, "duplicate tag observer option");
        tag_observer_v2 = true;
      } else if (option == "--boundary-schema=2") {
        check(!boundary_schema_v2, "duplicate boundary schema option");
        boundary_schema_v2 = true;
      } else if (option == "--queue-edge-schema=2") {
        check(!queue_edge_schema_v2, "duplicate queue edge schema option");
        queue_edge_schema_v2 = true;
      } else break;
      --argument_count;
    }
    const bool numeric_mode = argument_count == 6 &&
        std::string_view(argv[2]) == "--values" &&
        std::string_view(argv[4]) == "--work-id";
    check(argument_count == 2 || numeric_mode,
          "composition projection path required");
    std::ifstream header(argv[1]);
    check(bool(header), "composition manifest missing");
    std::string revision;
    header >> revision;
    absolute_mode = revision == "IM2P_COMPOSITIONAL_SEQUENCE_V2";
    constexpr bool observer_profile =
        ((IM2P_ACTIVATION_BITS == 4 && IM2P_OPERAND_BITS == 4) ||
         (IM2P_ACTIVATION_BITS == 8 && IM2P_OPERAND_BITS == 8)) &&
        (IM2P_DIM == 16 || IM2P_DIM == 32 || IM2P_DIM == 64);
    check(!tag_observer_v2 || (absolute_mode && observer_profile),
          "tag observer v2 requires pinned six-profile absolute RTL");
    check(!boundary_schema_v2 || (absolute_mode && observer_profile),
          "boundary schema v2 requires pinned six-profile absolute RTL");
    check(!queue_edge_schema_v2 || (boundary_schema_v2 && absolute_mode),
          "queue edge schema v2 requires absolute boundary mode");
    const auto absolute = absolute_mode ? read_absolute_work(argv[1]) : AbsoluteManifest{};
    check(!boundary_schema_v2 || absolute.works.size() >= 2,
          "boundary schema v2 requires two absolute works");
    absolute_manifest = absolute_mode ? &absolute : nullptr;
    unsigned period = 0;
    const auto works = absolute_mode ? absolute.works : read_work(argv[1], period);
    if (tag_observer_v2) rtl_selected_event_counts.resize(works.size());
    if (absolute_mode) period = absolute.period;
    std::unique_ptr<ProductionCase> selected_numeric;
    if (numeric_mode) {
      check(absolute_mode, "numeric fixture requires absolute v2 stimulus");
      const std::string_view token(argv[5]);
      std::uint64_t requested = 0;
      const auto parsed = std::from_chars(token.data(), token.data() + token.size(),
                                          requested);
      check(parsed.ec == std::errc{} && parsed.ptr == token.data() + token.size(),
            "numeric work ID is invalid");
      check(std::count_if(works.begin(), works.end(),
                          [&](const auto &work) { return work.id == requested; }) == 1,
            "numeric work ID is absent or duplicate");
      selected_numeric = std::make_unique<ProductionCase>(load_production_case(argv[3]));
      const auto selected = std::find_if(works.begin(), works.end(),
                                         [&](const auto &work) { return work.id == requested; });
      verify_numeric_payload(*selected_numeric, *selected);
      numeric_payload = selected_numeric.get();
      numeric_work_id = requested;
    }
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    Adapter state{dut, context};
    reset(state);
    const auto backing_offset = state.cycle - state.dut.io_coreCycle;
    state.read_ready_period = period;
    state.event_observer = absolute_mode ? observe_absolute : observe;
    if (absolute_mode)
      std::cout << "COMPOSITION_RUN {\"instance_count\":1,\"reset_count\":1,\"period\":"
                << period << ",\"work_count\":" << works.size()
                << ",\"stimulus_sha256\":\"" << absolute.digest
                << "\",\"edge_convention\":\"pre_rising_old_state\"}\n";
    else
      std::cout << "COMPOSITION_RUN {\"instance_count\":1,\"reset_count\":1,\"period\":"
                << period << ",\"work_count\":" << works.size() << "}\n";
    Prediction previous{};
    std::uint64_t previous_rtl_ready = 0;
    for (std::size_t i = 0; i < works.size(); ++i)
      if (absolute_mode)
        measure_absolute(state, works[i], absolute.offers[i]);
      else {
        previous = measure(state, works[i], period, previous, previous_rtl_ready);
        previous_rtl_ready = milestones.reusable;
      }
    if (boundary_schema_v2)
      check(have_previous_rtl_queue && !previous_rtl_queue.row_enq_fire &&
                !previous_rtl_queue.row_deq_fire &&
                !previous_rtl_queue.tag_enq_fire &&
                !previous_rtl_queue.tag_deq_fire &&
                previous_rtl_queue.row_count < 6 &&
                previous_rtl_queue.tag_count < 6,
            "RTL queue origin lacks a terminal successor");
    if (tag_observer_v2)
      for (std::size_t ordinal = 0; ordinal < rtl_selected_event_counts.size(); ++ordinal)
        std::cout << "RTL_SELECTED_EVENT_COUNT " << ordinal << ' '
                  << rtl_selected_event_counts[ordinal] << '\n';
    check(!numeric_mode || numeric_injections == 1,
          "numeric fixture was not applied exactly once");
    if (absolute_mode) measure_native_absolute(absolute, backing_offset);
    state.event_observer = nullptr;
    dut.final();
    return 0;
  } catch (const std::exception &error) {
    if (absolute_mode)
      for (const auto &row : absolute_window) std::cerr << "OFFER_WINDOW " << row << '\n';
    std::cerr << "COMPOSITION_FAIL " << error.what() << '\n';
    return 1;
  }
}
#endif
