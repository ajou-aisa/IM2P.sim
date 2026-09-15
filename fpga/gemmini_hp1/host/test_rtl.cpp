#if !defined(IM2P_RTL_TEST_BUILD) || !__has_include(<VIM2PGemminiHP1RtlTest.h>)
int im2p_gemmini_hp1_rtl_test_requires_generated_model;
#else

#include <VIM2PGemminiHP1RtlTest.h>
#include "frontend_rtl_fixture.hpp"
#include "im2p_sim.h"
#include "uart.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <vector>

#include <verilated.h>

#ifndef IM2P_DIM
#error IM2P_DIM is required
#endif
#ifndef IM2P_OPERAND_BITS
#error IM2P_OPERAND_BITS is required
#endif
#ifndef IM2P_BANK_ROWS
#error IM2P_BANK_ROWS is required
#endif
#ifndef IM2P_ACC_ROWS
#error IM2P_ACC_ROWS is required
#endif

namespace {

using Dut = VIM2PGemminiHP1RtlTest;
using im2p::gemmini_hp1::Capability;
using im2p::gemmini_hp1::Fragment;
using im2p::gemmini_hp1::Mode;
using im2p::gemmini_hp1::Packing;
using im2p::gemmini_hp1::WorkKind;
using im2p::gemmini_hp1::WorkPlanV1;

void check(bool condition, const char *message) {
  if (!condition) {
    throw std::runtime_error(message);
  }
}

void tick(Dut &dut, VerilatedContext &context) {
  dut.clock = 0;
  dut.eval();
  context.timeInc(1);
  dut.clock = 1;
  dut.eval();
  context.timeInc(1);
  check(!context.gotFinish(), "RTL stopped unexpectedly");
}

template <typename Ready>
void accept(Dut &dut, VerilatedContext &context, Ready ready, const char *message) {
  for (unsigned cycle = 0; cycle < 20000; ++cycle) {
    dut.eval();
    if (ready()) {
      tick(dut, context);
      return;
    }
    tick(dut, context);
  }
  throw std::runtime_error(message);
}

template <typename Signal>
void set_bytes(Signal &signal, const std::vector<std::uint8_t> &bytes) {
  if constexpr (VlIsVlWide<Signal>::value) {
    std::fill_n(signal.data(), signal.size(), 0U);
    for (std::size_t index = 0; index < bytes.size(); ++index) {
      signal.at(index / 4U) |= static_cast<std::uint32_t>(bytes[index])
                                << ((index % 4U) * 8U);
    }
  } else {
    std::uint64_t value = 0;
    for (std::size_t index = 0; index < bytes.size(); ++index) {
      value |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
    }
    signal = static_cast<Signal>(value);
  }
}

template <typename Signal>
std::uint64_t low_word(const Signal &signal) {
  if constexpr (VlIsVlWide<Signal>::value) {
    return static_cast<std::uint64_t>(signal.at(0)) |
           (static_cast<std::uint64_t>(signal.at(1)) << 32U);
  } else {
    return static_cast<std::uint64_t>(signal);
  }
}

std::vector<std::uint8_t> pack_row(const std::vector<std::int8_t> &values) {
  if constexpr (IM2P_OPERAND_BITS == 4) {
    return im2p::gemmini_hp1::pack_int4(values);
  }
  return std::vector<std::uint8_t>(
      reinterpret_cast<const std::uint8_t *>(values.data()),
      reinterpret_cast<const std::uint8_t *>(values.data() + values.size()));
}

void load_row(Dut &dut, VerilatedContext &context, bool weights, bool slot,
              std::uint32_t row, const std::vector<std::int8_t> &values) {
  dut.io_localLoad_bits_weights = weights;
  dut.io_localLoad_bits_slot = slot;
  dut.io_localLoad_bits_row = row;
  const auto bytes = pack_row(values);
  set_bytes(dut.io_localLoad_bits_data, bytes);
  std::uint64_t expected_low = 0;
  for (std::size_t index = 0; index < std::min<std::size_t>(bytes.size(), 8); ++index) {
    expected_low |= static_cast<std::uint64_t>(bytes[index]) << (index * 8U);
  }
  check(low_word(dut.io_localLoad_bits_data) == expected_low,
        "Verilator input packing mismatch");
  dut.io_localLoad_valid = 1;
  accept(dut, context, [&dut] { return dut.io_localLoad_ready != 0; },
         "local-memory load stalled");
  dut.io_localLoad_valid = 0;
}

void load_scale(Dut &dut, VerilatedContext &context, std::uint32_t column,
                std::uint32_t address, std::uint32_t generation,
                std::uint32_t carrier) {
  dut.io_scaleLoad_bits_column = column;
  dut.io_scaleLoad_bits_address = address;
  dut.io_scaleLoad_bits_generation = generation;
  dut.io_scaleLoad_bits_carrier = carrier;
  dut.io_scaleLoad_valid = 1;
  accept(dut, context, [&dut] { return dut.io_scaleLoad_ready != 0; },
         "scale load stalled");
  dut.io_scaleLoad_valid = 0;
}

void issue(Dut &dut, VerilatedContext &context, const Fragment &fragment,
           std::uint32_t work_id, std::uint32_t scale_address,
           std::uint32_t generation, bool slot, std::uint32_t accumulator_offset = 0,
           std::uint32_t valid_rows = 1, std::uint32_t valid_columns = 3) {
  dut.io_command_bits_slot = slot;
  dut.io_command_bits_validRows = valid_rows;
  dut.io_command_bits_validColumns = valid_columns;
  dut.io_command_bits_fragmentLength = fragment.valid_k;
  dut.io_command_bits_activationBase = IM2P_BANK_ROWS - IM2P_DIM;
  dut.io_command_bits_weightBase = IM2P_BANK_ROWS - IM2P_DIM;
  dut.io_command_bits_accumulatorBase = IM2P_ACC_ROWS - IM2P_DIM + accumulator_offset;
  dut.io_command_bits_scaleAddress = scale_address;
  dut.io_command_bits_scaleGeneration = generation;
  dut.io_command_bits_workId = work_id;
  dut.io_command_bits_firstContribution = fragment.first;
  dut.io_command_bits_finalFragment = fragment.final;
  dut.io_command_valid = 1;
  accept(dut, context, [&dut] { return dut.io_command_ready != 0; },
         "fragment command stalled");
  dut.io_command_valid = 0;
}

std::uint64_t finish(Dut &dut, VerilatedContext &context, std::uint32_t work_id) {
  accept(dut, context, [&dut] { return dut.io_done_valid != 0; },
         "work completion stalled");
  check(dut.io_done_bits == work_id, "wrong completed work ID");
  check(dut.io_measurementValid != 0 && dut.io_elapsedCycles != 0,
        "cycle measurement missing");
  const auto elapsed = static_cast<std::uint64_t>(dut.io_elapsedCycles);
  check(static_cast<std::uint64_t>(dut.io_doneCycle - dut.io_startCycle) == elapsed,
        "cycle endpoint difference mismatch");
  dut.io_done_ready = 1;
  tick(dut, context);
  dut.io_done_ready = 0;
  return elapsed;
}

void read_result(Dut &dut, VerilatedContext &context, std::int32_t first,
                 std::int32_t second, std::int32_t third, const char *label,
                 std::uint32_t accumulator_offset = 0) {
  dut.io_resultRequest_bits_row = IM2P_ACC_ROWS - IM2P_DIM + accumulator_offset;
  dut.io_resultRequest_valid = 1;
  accept(dut, context, [&dut] { return dut.io_resultRequest_ready != 0; },
         "result request stalled");
  dut.io_resultRequest_valid = 0;
  accept(dut, context, [&dut] { return dut.io_result_valid != 0; },
         "result response stalled");
  const auto actual_first = static_cast<std::int32_t>(dut.io_result_bits_data_0);
  const auto actual_second = static_cast<std::int32_t>(dut.io_result_bits_data_1);
  if (actual_first != first || actual_second != second) {
    throw std::runtime_error(
        std::string(label) + " result mismatch: expected " + std::to_string(first) + "," +
        std::to_string(second) + " actual " + std::to_string(actual_first) + "," +
        std::to_string(actual_second));
  }
  check(static_cast<std::int32_t>(dut.io_result_bits_data_2) == third,
        "result beyond the first packed byte is incorrect");
  check(static_cast<std::int32_t>(dut.io_result_bits_data_3) == 0,
        "padded result column is non-zero");
  dut.io_result_ready = 1;
  tick(dut, context);
  dut.io_result_ready = 0;
}

Capability capability() {
  return {"rtl-test", "rtl-test", "hp1-fragment-sat32-v1", IM2P_OPERAND_BITS,
          IM2P_OPERAND_BITS, IM2P_DIM, 32, 32,
          IM2P_OPERAND_BITS == 4 ? Packing::signed_int4_low_nibble_first
                                 : Packing::signed_int8,
          true, true, false, 4, IM2P_BANK_ROWS, IM2P_ACC_ROWS,
          IM2P_DIM * IM2P_OPERAND_BITS / 8, IM2P_DIM * 4};
}

void reset_dut(Dut &dut, VerilatedContext &context) {
  dut.io_localLoad_valid = 0;
  dut.io_scaleLoad_valid = 0;
  dut.io_scaleRelease_valid = 0;
  dut.io_command_valid = 0;
  dut.io_done_ready = 0;
  dut.io_resultRequest_valid = 0;
  dut.io_result_ready = 0;
  dut.reset = 1;
  for (unsigned cycle = 0; cycle < 5; ++cycle) {
    tick(dut, context);
  }
  dut.reset = 0;
  tick(dut, context);
}

void wait_fragment(Dut &dut, VerilatedContext &context, std::uint32_t work_id) {
  accept(dut, context, [&dut] { return dut.io_fragmentConsumed_valid != 0; },
         "fragment commit stalled");
  check(dut.io_fragmentConsumed_bits == work_id, "wrong consumed fragment work ID");
}

std::vector<std::int32_t> read_frontend_result(
    Dut &dut, VerilatedContext &context, std::uint32_t accumulator_offset,
    std::uint32_t rows, std::uint32_t columns) {
  std::vector<std::int32_t> output;
  output.reserve(static_cast<std::size_t>(rows) * columns);
  for (std::uint32_t row = 0; row < rows; ++row) {
    dut.io_resultRequest_bits_row =
        IM2P_ACC_ROWS - IM2P_DIM + accumulator_offset + row;
    dut.io_resultRequest_valid = 1;
    accept(dut, context, [&dut] { return dut.io_resultRequest_ready != 0; },
           "frontend result request stalled");
    dut.io_resultRequest_valid = 0;
    accept(dut, context, [&dut] { return dut.io_result_valid != 0; },
           "frontend result response stalled");
    for (std::uint32_t column = 0; column < columns; ++column) {
      switch (column) {
      case 0: output.push_back(static_cast<std::int32_t>(dut.io_result_bits_data_0)); break;
      case 1: output.push_back(static_cast<std::int32_t>(dut.io_result_bits_data_1)); break;
      case 2: output.push_back(static_cast<std::int32_t>(dut.io_result_bits_data_2)); break;
      default: throw std::runtime_error("frontend fixture exceeds tested output columns");
      }
    }
    dut.io_result_ready = 1;
    tick(dut, context);
    dut.io_result_ready = 0;
  }
  return output;
}

struct FrontendDut {
  Dut &dut;
  VerilatedContext &context;
  std::array<bool, 4> scale_loaded{};
  std::array<std::array<std::uint32_t, IM2P_DIM>, 4> carriers{};
  std::size_t fragment_calls = 0;
};

int execute_frontend_fragment(
    void *opaque, const im2p::gemmini_hp1::FrontendRtlFragment &request,
    std::vector<std::int32_t> &final_output, std::uint64_t &elapsed_cycles) {
  auto &state = *static_cast<FrontendDut *>(opaque);
  if (request.valid_rows == 0 || request.valid_rows > IM2P_DIM ||
      request.valid_columns == 0 || request.valid_columns > 3 ||
      request.plan.tile_i == 0 || request.plan.tile_j == 0 ||
      request.plan.tile_k == 0 ||
      request.activations.size() != IM2P_DIM * IM2P_DIM ||
      request.weights.size() != IM2P_DIM * IM2P_DIM ||
      request.carriers.size() != IM2P_DIM) {
    return IM2P_INVALID_LAYOUT;
  }
  const bool slot = request.fragment.index % 2U != 0U;
  for (std::size_t row = 0; row < IM2P_DIM; ++row) {
    load_row(state.dut, state.context, false, slot,
             IM2P_BANK_ROWS - IM2P_DIM + row,
             {request.activations.begin() + row * IM2P_DIM,
              request.activations.begin() + (row + 1) * IM2P_DIM});
    load_row(state.dut, state.context, true, slot,
             IM2P_BANK_ROWS - IM2P_DIM + row,
             {request.weights.begin() + row * IM2P_DIM,
              request.weights.begin() + (row + 1) * IM2P_DIM});
  }

  const auto scale_address = static_cast<std::uint32_t>(request.fragment.block % 4U);
  const auto generation = scale_address + 1U;
  if (!state.scale_loaded[scale_address]) {
    for (std::uint32_t column = 0; column < request.valid_columns; ++column) {
      load_scale(state.dut, state.context, column, scale_address, generation,
                 request.carriers[column]);
      state.carriers[scale_address][column] = request.carriers[column];
    }
    state.scale_loaded[scale_address] = true;
  } else {
    for (std::uint32_t column = 0; column < request.valid_columns; ++column) {
      if (state.carriers[scale_address][column] != request.carriers[column]) {
        return IM2P_INVALID_LAYOUT;
      }
    }
  }

  const auto accumulator_offset = request.work_id * 2U;
  issue(state.dut, state.context, request.fragment, request.work_id, scale_address,
        generation, slot, accumulator_offset, request.valid_rows,
        request.valid_columns);
  ++state.fragment_calls;
  if (!request.fragment.final) {
    wait_fragment(state.dut, state.context, request.work_id);
    final_output.clear();
    elapsed_cycles = 0;
    return IM2P_OK;
  }
  elapsed_cycles = finish(state.dut, state.context, request.work_id);
  final_output = read_frontend_result(state.dut, state.context, accumulator_offset,
                                      request.valid_rows, request.valid_columns);
  return IM2P_OK;
}

}

int main(int argc, char **argv) {
  try {
    VerilatedContext context;
    context.commandArgs(argc, argv);
    Dut dut{&context};
    reset_dut(dut, context);

    const WorkPlanV1 plan{1, 3, 64, 1, 1, 2, 0, Mode::full,
                          WorkKind::dense_hp1_final};
    const auto frame = im2p::gemmini_hp1::decode_frame(im2p::gemmini_hp1::encode_frame(
        {im2p::gemmini_hp1::Opcode::work, 7,
         im2p::gemmini_hp1::encode_work_plan_v1(plan)}));
    const auto decoded = im2p::gemmini_hp1::decode_work_plan_v1(frame.payload);
    const auto fragments = im2p::gemmini_hp1::fragment_work(capability(), decoded);
    check(!fragments.empty() && fragments.front().first && fragments.back().final,
          "host fragment plan is incomplete");

    for (bool slot : {false, true}) {
      for (std::size_t row = 0; row < IM2P_DIM; ++row) {
        std::vector<std::int8_t> activation(IM2P_DIM, 0);
        std::vector<std::int8_t> weight(IM2P_DIM, 0);
        if (row == 0) {
          activation[0] = 1;
          activation[3] = 2;
          weight[0] = 1;
          weight[1] = 2;
          weight[2] = -3;
          if constexpr (IM2P_DIM == 64) {
            activation[32] = 7;
          }
        }
        if (row == 3) {
          weight[0] = -1;
          weight[1] = 1;
          weight[2] = 1;
        }
        if constexpr (IM2P_DIM == 64) {
          if (row == 32) {
            weight[0] = 5;
          }
        }
        load_row(dut, context, false, slot,
                 IM2P_BANK_ROWS - IM2P_DIM + row, activation);
        load_row(dut, context, true, slot,
                 IM2P_BANK_ROWS - IM2P_DIM + row, weight);
      }
    }

    for (std::size_t column = 0; column < IM2P_DIM; ++column) {
      load_scale(dut, context, column, 0, 1, 1);
      load_scale(dut, context, column, 1, 2,
                 column == 0 ? 0x80000000U : 0U);
      load_scale(dut, context, column, 2, 3, 0x80000000U);
    }

    for (const auto &fragment : fragments) {
      issue(dut, context, fragment, 1, static_cast<std::uint32_t>(fragment.block),
            static_cast<std::uint32_t>(fragment.block + 1), fragment.index % 2U != 0U);
    }
    finish(dut, context, 1);
    const auto block_fragments = static_cast<std::int32_t>(
        std::count_if(fragments.begin(), fragments.end(),
                      [](const Fragment &fragment) { return fragment.block == 0; }));
    read_result(dut, context, block_fragments * -2, block_fragments * 12,
                block_fragments * -3, "full");

    WorkPlanV1 pipeline_plan = plan;
    pipeline_plan.m = 2;
    pipeline_plan.activation_rows_per_stripe = 1;
    pipeline_plan.mode = Mode::pipeline;
    const auto pipeline_frame = im2p::gemmini_hp1::decode_frame(
        im2p::gemmini_hp1::encode_frame(
            {im2p::gemmini_hp1::Opcode::work, 8,
             im2p::gemmini_hp1::encode_work_plan_v1(pipeline_plan)}));
    const auto pipeline_fragments = im2p::gemmini_hp1::fragment_work(
        capability(), im2p::gemmini_hp1::decode_work_plan_v1(pipeline_frame.payload));
    check(pipeline_fragments.size() == fragments.size(), "pipeline fragment plan changed K");
    for (std::uint32_t stripe = 0; stripe < 2; ++stripe) {
      for (const auto &fragment : pipeline_fragments) {
        issue(dut, context, fragment, 2 + stripe,
              static_cast<std::uint32_t>(fragment.block),
              static_cast<std::uint32_t>(fragment.block + 1),
              fragment.index % 2U != 0U, stripe);
      }
      finish(dut, context, 2 + stripe);
      read_result(dut, context, block_fragments * -2, block_fragments * 12,
                  block_fragments * -3,
                  stripe == 0 ? "pipeline-0" : "pipeline-1", stripe);
    }

    WorkPlanV1 zero_plan = plan;
    zero_plan.k = std::min<std::uint64_t>(IM2P_DIM, 32);
    const auto zero_fragments = im2p::gemmini_hp1::fragment_work(capability(), zero_plan);
    check(zero_fragments.size() == 1, "zero replacement plan must have one fragment");
    issue(dut, context, zero_fragments.front(), 4, 2, 3, false);
    dut.io_resultRequest_bits_row = IM2P_ACC_ROWS - IM2P_DIM;
    dut.io_resultRequest_valid = 1;
    dut.eval();
    check(dut.io_resultRequest_ready == 0, "intermediate result became visible");
    dut.io_resultRequest_valid = 0;
    finish(dut, context, 4);
    read_result(dut, context, 0, 0, 0, "zero-replace");

    check(dut.io_commandError == 0 && dut.io_scaleError == 0,
          "RTL reported a command or scale error");
    reset_dut(dut, context);
    FrontendDut frontend{dut, context};
    im2p::gemmini_hp1::run_frontend_rtl_fixture(
        capability(), &frontend, execute_frontend_fragment);
    check(frontend.fragment_calls >= 6,
          "frontend did not exercise FULL and multi-stripe PIPELINE fragments");
    check(dut.io_commandError == 0 && dut.io_scaleError == 0,
          "frontend RTL execution reported an error");
    dut.final();
    std::cout << "GEMMINI_HP1_HOST_RTL_PASS a" << IM2P_OPERAND_BITS << "w"
              << IM2P_OPERAND_BITS << "-d" << IM2P_DIM
              << " fragments=" << fragments.size() << '\n';
    return 0;
  } catch (const std::exception &error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}

#endif
