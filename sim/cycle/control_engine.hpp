#pragma once
#include "cycle_model.hpp"
#include <array>
#include <deque>
#include <optional>
#include <vector>

namespace im2p::cycle::detail {
using U = uint64_t;
using P = RtlTimingProfile;
constexpr unsigned none = UINT32_MAX;

enum class Kind {
  LoadConfig,
  StoreConfig,
  ExecuteConfig,
  LoadA,
  LoadB,
  Preload,
  Compute,
  Store,
  LoopControl
};
struct Command {
  Kind kind = Kind::LoopControl;
  unsigned index = 0, frame = 0, fragment = 0;
  unsigned i = 0, j = 0, k = 0, rows = 0, cols = 0;
  unsigned src = none, dst = none, output_rows = 0, output_cols = 0;
  unsigned rob = none;
  bool accumulate = false, matmul = false;
  U parent = 0;
  unsigned queue() const {
    return kind == Kind::LoadConfig || kind == Kind::LoadA ||
                   kind == Kind::LoadB
               ? 0
           : kind == Kind::StoreConfig || kind == Kind::Store ? 2
                                                              : 1;
  }
  bool config() const {
    return kind == Kind::LoadConfig || kind == Kind::StoreConfig ||
           kind == Kind::ExecuteConfig;
  }
};
struct Programs {
  std::vector<Command> a, b, execute, store;
};
struct Entry {
  bool valid = false, issued = false;
  Command command;
  U deps = 0;
};
struct Tracker {
  bool valid = false;
  unsigned remaining = 0, rob = none;
};
struct DmaController {
  std::deque<Command> queue;
  std::array<Tracker, 2> tracker{};
  unsigned state = 0, row = 0, command_id = 0;
};
struct Read {
  unsigned id = 0, command = 0, row = 0;
  bool scale = false;
  TimingEvent timing;
  U parent = 0;
};
struct Memory {
  std::array<bool, P::read_slots> active{};
  std::deque<Read> queued;
  std::optional<Read> backing;
  std::vector<Read> responses;
  std::optional<unsigned> dma_response;
  unsigned store_state = 0, store_command = 0, store_address = 0;
  bool acc_read_issued = false;
  unsigned settling = 0;
  std::optional<TimingEvent> write_due;
};
struct Bank {
  bool pending = false, queued = false;
  std::array<bool, 4> pipe{};
};
struct RowControl {
  std::array<bool, 3> fire{}, reads{};
  std::array<unsigned, 3> banks{};
  unsigned rows = 0;
  bool first = false;
  Command preload;
};
struct Tag {
  unsigned id = 0, rows = 0;
  Command preload;
};
struct MeshRow {
  TimingEvent timing;
  unsigned id = 0;
  bool last = false;
  U parent = 0;
};
struct Array {
  bool request = false;
  unsigned rows = 0, counter = 0, id = 0;
  std::array<bool, 3> written{};
  std::deque<Tag> tags, row_counts;
  std::deque<MeshRow> outputs;
  unsigned output_counter = 0;
};
struct Execute {
  std::deque<Command> transpose, queue;
  std::deque<RowControl> controls;
  unsigned mode = 0; // idle / preload / compute / compute+preload
  std::array<unsigned, 3> counter{};
  std::array<bool, 3> started{};
  std::array<unsigned, 2> pending{{none, none}};
};
struct Context {
  Command command;
  unsigned received = 0;
};
struct Write {
  Command command;
  unsigned address = 0;
  bool last = false;
  U parent = 0;
  TimingEvent timing;
};
struct Writeback {
  std::deque<Context> contexts;
  std::deque<Write> writes, commits;
  std::array<bool, 64> pending{}, raw_done{}, write_done{};
  std::deque<unsigned> immediate, delayed;
  unsigned last_grant = 0, reserved = 0;
};
struct Loop {
  bool configured = false, running = false;
  std::array<bool, 5> started{}, completed{};
  std::array<unsigned, 4> position{};
  std::array<unsigned, 3> utilized{};
};
struct State {
  U cycle = 0;
  unsigned frame = 0;
  unsigned host = 0; // accepting / waiting / releasing / retired
  unsigned release = 0;
  unsigned scale_state = 0, scale_row = 0, scale_column = 0;
  unsigned bridge_state = 0, bridge_index = 0;
  bool bridge_pending = false, bridge_issued = false, saw_busy = false;
  std::deque<Command> raw, loop_input, unrolled;
  Loop loop;
  std::array<Entry, 64> station{};
  DmaController load, store;
  Memory memory;
  std::array<Bank, 4> banks{};
  Execute execute;
  Array array;
  Writeback writeback;
};
struct Signals {
  std::optional<Write> raw_write, write;
  std::optional<unsigned> raw_completed, wb_completed, completed;
  int wb_grant = -1, load_completed = -1, store_completed = -1;
  std::array<bool, 4> sp_consume{}, sp_ready{}, sp_request{};
  std::optional<Read> dma_read;
  std::optional<unsigned> dma_store_return;
  std::array<int, 3> issued{{-1, -1, -1}};
};
class Engine {
public:
  Engine(const im2p_cycle_model_config_t &, const im2p_cycle_request_t &);
  ModelResult run();

private:
  const im2p_cycle_model_config_t &config;
  const im2p_cycle_request_t &request;
  Schedule schedule;
  RtlTimingProfile profile;
  std::vector<Programs> programs;
  State state;
  ModelResult result;
  U record(const State &, EventType, Resource, const Command & = {},
           U detail = 0, U parent = 0);
  void expand_commands();
  void step();
  void execute(State &, const State &, Signals &);
  void writeback(State &, const State &, Signals &);
  void memory(State &, const State &, Signals &);
  void dma(State &, const State &, Signals &);
  void reservation(State &, const State &, Signals &);
  void control(State &, const State &, Signals &);
  bool busy(const State &) const;
};
} // namespace im2p::cycle::detail
