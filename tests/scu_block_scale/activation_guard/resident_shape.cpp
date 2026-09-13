#include "VmkResidentP0.h"
#include "VmkResidentP0___024root.h"
#include "verilated_vcd_c.h"
#include "activation_monitor.hpp"
#include "work_layout.hpp"
#include <fstream>
#include <algorithm>
#include <iomanip>
#include <memory>
#include <array>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <random>
#include <stdexcept>

using AData = std::array<std::array<int8_t, 32>, 16>;
using BData = std::array<std::array<int8_t, 16>, 32>;
using Scales = std::array<int8_t, 16>;

static void require(bool value, const char *message) {
    if (!value) throw std::runtime_error(message);
}

static uint32_t transform(int32_t partial, int8_t scale, bool shift) {
    if (!shift) return uint32_t(int64_t(partial) * int64_t(scale));
    const unsigned amount = scale < 0 ? unsigned(-int(scale)) : unsigned(scale);
    if (scale >= 0) return amount < 32 ? uint32_t(partial) << amount : 0;
    if (amount >= 32) return partial < 0 ? UINT32_MAX : 0;
    uint32_t value = uint32_t(partial) >> amount;
    if (partial < 0) value |= ~(UINT32_MAX >> amount);
    return value;
}


static activation_monitor::Monitor observer;
static std::ofstream trace_log;
static unsigned shape_m=16, shape_n=16, shape_k=32;
static unsigned completed_jobs=0, verified_jobs=0, correct_outputs=0;
static bool enable_wave=false, enable_trace=false;
static VerilatedVcdC *waveform=nullptr;
static uint64_t ticks=0;
static const char *case_name="reset";
static bool enable_monitor=false;
#define CORE(name) r.mkResidentP0__DOT__core_core_core_##name
#define TOP(name) r.mkResidentP0__DOT__##name
#ifndef MATMUL_WORK_RECORD_BITS
#error "Run the BSV pack/RTL-width layout gate before compiling this observer"
#endif
template<class Wide> static activation_monitor::Row row(const Wide& w) { return {{w[0],w[1],w[2],w[3]}}; }
static activation_monitor::Snapshot sample(VmkResidentP0 &dut) {
    auto &r=*dut.rootp; activation_monitor::Snapshot s;
    s.tick=ticks; s.reset_n=dut.RST_N; s.job_id=CORE(matrixJobIdReg);
    s.required_rows=CORE(matrixStateReg) <= 1 ? 0 : work_layout::extract(CORE(matrixWorkReg),work_layout::i_count);
    s.current_slot=CORE(currentActivationSlotReg);s.feed_row=CORE(activationFeedRowReg);
    s.request={bool(CORE(activationRequestValidReg)),CORE(activationRequestTagReg),CORE(activationRequestSlotReg),CORE(activationRequestRowReg),CORE(activationRequestKStartReg),CORE(activationRequestKCountReg)};
    s.lookahead_request_valid=CORE(lookaheadActivationRequestValidReg);s.lookahead_request_tag=CORE(lookaheadActivationTagReg);
    s.pending={bool(CORE(activationResponsePendingReg)),CORE(activationResponseSlotReg),CORE(activationResponseRowReg),row(CORE(activationResponseValuesReg))};
    s.slots[0].valid=CORE(activationSlotMetadataValid_0);s.slots[0].k_start=CORE(activationSlotKStart_0);s.slots[0].k_count=CORE(activationSlotKCount_0);
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_0))<<0;s.slots[0].rows[0]=row(CORE(activationSlotRows_0_0));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_1))<<1;s.slots[0].rows[1]=row(CORE(activationSlotRows_0_1));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_2))<<2;s.slots[0].rows[2]=row(CORE(activationSlotRows_0_2));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_3))<<3;s.slots[0].rows[3]=row(CORE(activationSlotRows_0_3));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_4))<<4;s.slots[0].rows[4]=row(CORE(activationSlotRows_0_4));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_5))<<5;s.slots[0].rows[5]=row(CORE(activationSlotRows_0_5));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_6))<<6;s.slots[0].rows[6]=row(CORE(activationSlotRows_0_6));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_7))<<7;s.slots[0].rows[7]=row(CORE(activationSlotRows_0_7));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_8))<<8;s.slots[0].rows[8]=row(CORE(activationSlotRows_0_8));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_9))<<9;s.slots[0].rows[9]=row(CORE(activationSlotRows_0_9));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_10))<<10;s.slots[0].rows[10]=row(CORE(activationSlotRows_0_10));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_11))<<11;s.slots[0].rows[11]=row(CORE(activationSlotRows_0_11));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_12))<<12;s.slots[0].rows[12]=row(CORE(activationSlotRows_0_12));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_13))<<13;s.slots[0].rows[13]=row(CORE(activationSlotRows_0_13));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_14))<<14;s.slots[0].rows[14]=row(CORE(activationSlotRows_0_14));
    s.slots[0].row_valid |= unsigned(CORE(activationSlotRowValid_0_15))<<15;s.slots[0].rows[15]=row(CORE(activationSlotRows_0_15));
    s.slots[1].valid=CORE(activationSlotMetadataValid_1);s.slots[1].k_start=CORE(activationSlotKStart_1);s.slots[1].k_count=CORE(activationSlotKCount_1);
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_0))<<0;s.slots[1].rows[0]=row(CORE(activationSlotRows_1_0));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_1))<<1;s.slots[1].rows[1]=row(CORE(activationSlotRows_1_1));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_2))<<2;s.slots[1].rows[2]=row(CORE(activationSlotRows_1_2));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_3))<<3;s.slots[1].rows[3]=row(CORE(activationSlotRows_1_3));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_4))<<4;s.slots[1].rows[4]=row(CORE(activationSlotRows_1_4));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_5))<<5;s.slots[1].rows[5]=row(CORE(activationSlotRows_1_5));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_6))<<6;s.slots[1].rows[6]=row(CORE(activationSlotRows_1_6));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_7))<<7;s.slots[1].rows[7]=row(CORE(activationSlotRows_1_7));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_8))<<8;s.slots[1].rows[8]=row(CORE(activationSlotRows_1_8));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_9))<<9;s.slots[1].rows[9]=row(CORE(activationSlotRows_1_9));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_10))<<10;s.slots[1].rows[10]=row(CORE(activationSlotRows_1_10));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_11))<<11;s.slots[1].rows[11]=row(CORE(activationSlotRows_1_11));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_12))<<12;s.slots[1].rows[12]=row(CORE(activationSlotRows_1_12));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_13))<<13;s.slots[1].rows[13]=row(CORE(activationSlotRows_1_13));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_14))<<14;s.slots[1].rows[14]=row(CORE(activationSlotRows_1_14));
    s.slots[1].row_valid |= unsigned(CORE(activationSlotRowValid_1_15))<<15;s.slots[1].rows[15]=row(CORE(activationSlotRows_1_15));
    return s;
}
static activation_monitor::Events events(VmkResidentP0 &dut) {
    auto &r=*dut.rootp; activation_monitor::Events e;
    e.return_accepted=TOP(WILL_FIRE_RL_returnActivation); e.return_tag=TOP(activationTag); e.return_values=row(TOP(activations__024DO));
#if ASSERTED
    e.publish=TOP(WILL_FIRE_RL_core_core_core_publishMatrixActivationResponse);
#else
    // Original production optimized this rule fire into its pending-reg enables.
    e.publish=CORE(activationResponsePendingReg);
#endif
    e.feed=TOP(WILL_FIRE_RL_core_core_core_feedBufferedMatrixActivation);
    e.feed_values_valid=true;e.feed_values=row(CORE(engine_activationRows__024D_IN));
    e.begin_work=TOP(WILL_FIRE_RL_core_core_core_acquireMatrixWork);
    e.fragment_finish=TOP(WILL_FIRE_RL_core_core_core_finishMatrixExecution);e.job_complete=TOP(WILL_FIRE_RL_finish);
    return e;
}
static void trace(VmkResidentP0 &dut,const char *phase) {
    if (!trace_log.is_open()) return;
    auto &r=*dut.rootp; auto s=sample(dut);auto e=events(dut);
    trace_log << "{\"case\":\""<<case_name<<"\",\"tick\":"<<ticks<<",\"time\":"<<Verilated::time()<<",\"phase\":\""<<phase<<"\",\"reset_n\":"<<s.reset_n
      <<",\"cycle\":"<<CORE(cycleReg)<<",\"job_cycle\":"<<(CORE(cycleReg)-CORE(matrixStartCycleReg))
      <<",\"job\":"<<s.job_id<<",\"stripe\":"<<work_layout::extract(CORE(matrixWorkReg),work_layout::stripe_id)<<",\"stripe_context\":"<<work_layout::extract(CORE(matrixWorkReg),work_layout::stripe_context)
      <<",\"layout\":\""<<work_layout::identity<<"\",\"work_active\":"<<(CORE(matrixStateReg)>1)
      <<",\"iCount\":"<<work_layout::extract(CORE(matrixWorkReg),work_layout::i_count)<<",\"jCount\":"<<work_layout::extract(CORE(matrixWorkReg),work_layout::j_count)
      <<",\"required_rows\":"<<s.required_rows
      <<",\"state\":"<<unsigned(CORE(matrixStateReg))<<",\"current_slot\":"<<s.current_slot<<",\"feed_row\":"<<s.feed_row
      <<",\"provider_en\":"<<unsigned(TOP(activationPending))<<",\"provider_tag\":"<<e.return_tag<<",\"accepted\":"<<e.return_accepted
      <<",\"request_valid\":"<<s.request.valid<<",\"request_tag\":"<<s.request.tag<<",\"request_slot\":"<<s.request.slot<<",\"request_row\":"<<s.request.row<<",\"request_k\":"<<s.request.k_start
      <<",\"pending\":"<<s.pending.valid<<",\"pending_slot\":"<<s.pending.slot<<",\"pending_row\":"<<s.pending.row
      <<",\"publish\":"<<e.publish<<",\"feed\":"<<e.feed<<",\"lookahead_request\":"<<s.lookahead_request_valid
      <<",\"slot0_valid\":"<<s.slots[0].valid<<",\"slot0_k\":"<<s.slots[0].k_start<<",\"slot0_rows\":"<<s.slots[0].row_valid
      <<",\"slot1_valid\":"<<s.slots[1].valid<<",\"slot1_k\":"<<s.slots[1].k_start<<",\"slot1_rows\":"<<s.slots[1].row_valid
      <<",\"begin_work\":"<<e.begin_work<<",\"fragment_finish\":"<<e.fragment_finish<<",\"complete\":"<<e.job_complete
      <<",\"finish\":"<<Verilated::gotFinish()<<"}\n";trace_log.flush();
}
static void evaluate(VmkResidentP0 &dut,int clk,const char *phase) {
    Verilated::timeInc(20000);dut.CLK=clk;dut.eval();
    trace(dut,phase);if(waveform)waveform->dump(Verilated::time());
    require(!Verilated::gotFinish(),"unexpected simulator $finish");
}
static void tick(VmkResidentP0 &dut) {
    ++ticks; dut.CLK=0;dut.eval();trace(dut,"pre-rise");
    require(!Verilated::gotFinish(),"unexpected simulator $finish before rising edge");
    require(!dut.EN_loadActivation||dut.RDY_loadActivation,"loadActivation EN without RDY");
    require(!dut.EN_loadWeight||dut.RDY_loadWeight,"loadWeight EN without RDY");
    require(!dut.EN_loadScale||dut.RDY_loadScale,"loadScale EN without RDY");
    require(!dut.EN_start||dut.RDY_start,"start EN without RDY");
    require(!dut.EN_requestOutput||dut.RDY_requestOutput,"requestOutput EN without RDY");
    require(!dut.EN_consumeOutput||dut.RDY_consumeOutput,"consumeOutput EN without RDY");
    require(!dut.EN_acknowledge||dut.RDY_acknowledge,"acknowledge EN without RDY");
    auto pre=sample(dut);auto accepted=events(dut);
    evaluate(dut,1,"post-rise");auto post=sample(dut);
    if(enable_monitor) observer.observe(pre,accepted,post);
    evaluate(dut,0,"post-fall");
}

template<class Signal>
static void pack(Signal &signal, const std::array<int8_t, 16> &values) {
    for (unsigned word = 0; word < 4; ++word) {
        signal[word] = 0;
        for (unsigned byte = 0; byte < 4; ++byte)
            signal[word] |= uint32_t(uint8_t(values[4 * word + byte])) << (8 * byte);
    }
}

static void run(VmkResidentP0 &dut, const char *name, const AData &a,
                const BData &b, const Scales &scales, bool shift) {
    case_name=name;
    std::array<std::array<uint32_t, 16>, 16> expected{};
    for (unsigned row = 0; row < shape_m; ++row)
        for (unsigned col = 0; col < shape_n; ++col)
            for (unsigned fragment = 0; fragment < (shape_k+15)/16; ++fragment) {
                int32_t partial = 0;
                for (unsigned k = 16 * fragment; k < std::min(shape_k,16 * (fragment + 1)); ++k)
                    partial += int32_t(a[row][k]) * int32_t(b[k][col]);
                expected[row][col] += transform(partial, scales[col], shift);
            }

    require(dut.RDY_loadScale, "scale preload not ready");
    pack(dut.loadScale_values, scales);
    dut.EN_loadScale = 1; tick(dut); dut.EN_loadScale = 0; dut.eval();
    for (unsigned word = 0; word < 32; ++word) {
        std::array<int8_t, 16> activation{};
        for (unsigned lane = 0; lane < 16; ++lane)
            activation[lane] = a[word / 2][16 * (word % 2) + lane];
        require(dut.RDY_loadActivation && dut.RDY_loadWeight, "input preload not ready");
        dut.loadActivation_word = word;
        dut.loadWeight_word = word;
        pack(dut.loadActivation_values, activation);
        pack(dut.loadWeight_values, b[word]);
        dut.EN_loadActivation = 1; dut.EN_loadWeight = 1;
        tick(dut);
        dut.EN_loadActivation = 0; dut.EN_loadWeight = 0; dut.eval();
    }
    dut.start_m = shape_m; dut.start_n = shape_n; dut.start_k = shape_k;
    dut.start_shift = shift;
    dut.eval();
    require(dut.RDY_start, "resident start not ready after full preload");
    const auto began = std::chrono::steady_clock::now();
    dut.EN_start = 1; tick(dut); dut.EN_start = 0; dut.eval();
    uint64_t elapsed = 0;
    while (!dut.done && elapsed < 100000) {
        require(!dut.RDY_loadActivation && !dut.RDY_loadWeight && !dut.RDY_loadScale,
                "host can overwrite resident data during execution");
        require(!dut.RDY_requestOutput, "host can read unpublished output");
        tick(dut); ++elapsed;
    }
    const auto ended = std::chrono::steady_clock::now();
    require(dut.done, "autonomous execution timed out");
    ++completed_jobs;
    require(!dut.protocolError, "local provider address/count contract failed");
    require(dut.cycles == elapsed, "resident cycle counter differs from clock edges");
    require(dut.fragments == (shape_k+15)/16, "unexpected physical D16 fragment count");
    const uint64_t cycles = dut.cycles;
    for (int i = 0; i < 10; ++i) tick(dut);
    require(dut.done && dut.cycles == cycles, "completion/cycle count not stable");

    for (unsigned row = 0; row < shape_m; ++row) {
        require(dut.RDY_requestOutput, "local result read not ready");
        dut.requestOutput_row = row;
        dut.EN_requestOutput = 1; tick(dut); dut.EN_requestOutput = 0; dut.eval();
        unsigned wait = 0;
        while (!dut.outputValid && wait++ < 10) tick(dut);
        require(dut.outputValid, "synchronous result read timed out");
        for (int hold = 0; hold < 3; ++hold) {
            for (unsigned col = 0; col < shape_n; ++col) {
                if (dut.outputResponse[col] != expected[row][col]) {
                    std::cerr << name << " row=" << row << " col=" << col
                              << " got=" << int32_t(dut.outputResponse[col])
                              << " expected=" << int32_t(expected[row][col]) << '\n';
                    throw std::runtime_error("resident numerical mismatch");
                }
                if (hold == 0) ++correct_outputs;
            }
            tick(dut);
        }
        dut.EN_consumeOutput = 1; tick(dut); dut.EN_consumeOutput = 0; dut.eval();
    }
    const auto ns = std::chrono::duration_cast<std::chrono::nanoseconds>(ended - began).count();
    ++verified_jobs;
    std::cout << "{\"case\":\"" << name << "\",\"numeric_elements\":" << shape_m*shape_n << ",\"pass\":true,"
              << "\"m\":" << shape_m << ",\"n\":" << shape_n << ",\"k\":" << shape_k
              << ",\"fragments\":" << (shape_k+15)/16 << ",\"rtl_cycles\":" << cycles
              << ",\"verilator_resident_run_ns\":" << ns << "}\n";
    dut.EN_acknowledge = 1; tick(dut); dut.EN_acknowledge = 0; dut.eval();
}

int main(int argc, char **argv) {
    try {
        Verilated::commandArgs(argc, argv);
        for (int i=1;i<argc;++i) {
            const std::string arg=argv[i];
            if (arg=="--monitor") enable_monitor=true;
            else if (arg=="--wave") enable_wave=true;
            else if (arg=="--no-wave") enable_wave=false;
            else if (arg=="--trace") enable_trace=true;
            else if (arg=="--shape") {
                require(i+3<argc,"--shape requires M N K");
                unsigned *values[]={&shape_m,&shape_n,&shape_k};
                for (unsigned *value: values) {
                    const std::string token=argv[++i]; size_t used=0;
                    const auto parsed=std::stoul(token,&used);
                    require(used==token.size() && parsed<=32,"invalid shape argument");
                    *value=unsigned(parsed);
                }
            } else throw std::runtime_error("unknown argument: "+arg);
        }
        require(shape_m>=1 && shape_m<=16 && shape_n>=1 && shape_n<=16 && shape_k>=1 && shape_k<=32,"shape outside M/N1..16 K1..32");
        if (enable_trace) trace_log.open("trace.jsonl");
        Verilated::traceEverOn(enable_wave);
        VmkResidentP0 dut;
        auto &r=*dut.rootp;
        work_layout::validate(MATMUL_WORK_RECORD_BITS,CORE(matrixWorkReg).size());
        std::cout << "WORK_LAYOUT identity="<<work_layout::identity<<" record_bits="<<work_layout::record_bits
            <<" iCount="<<work_layout::i_count.offset<<'/'<<work_layout::i_count.width
            <<" jCount="<<work_layout::j_count.offset<<'/'<<work_layout::j_count.width
            <<" stripeContext="<<work_layout::stripe_context.offset<<'/'<<work_layout::stripe_context.width
            <<" stripeId="<<work_layout::stripe_id.offset<<'/'<<work_layout::stripe_id.width
            <<" work_active=matrixStateReg>1\n";
        std::unique_ptr<VerilatedVcdC> trace_owner;
        if (enable_wave) {
            trace_owner=std::make_unique<VerilatedVcdC>();waveform=trace_owner.get();
            dut.trace(waveform,1);waveform->open("wave.vcd");
        }
        dut.RST_N = 0; tick(dut); tick(dut);
        dut.RST_N = 1; tick(dut); tick(dut);
        require(!dut.RDY_start, "start accepts uninitialized resident memories");
        AData a{}; BData b{};
        Scales scales{{-128, -32, -8, -2, -1, 0, 1, 2, 4, 7, 8, 15, 31, 32, 126, 127}};
        for (auto &row : a) row.fill(-128);
        for (auto &row : b) row.fill(-128);
        run(dut, "int20_widen_int32_multiply", a, b, scales, false);
        a = {}; b = {}; scales.fill(-1);
        for (unsigned row = 0; row < 16; ++row) a[row][0] = a[row][16] = int8_t(row + 1);
        b[0].fill(1); b[16].fill(1);
        require(transform(1, -1, true) + transform(1, -1, true) != transform(2, -1, true),
                "fragment scale counterexample lost");
        run(dut, "fragment_shift_boundary", a, b, scales, true);
        std::mt19937 random(0x20260909);
        std::uniform_int_distribution<int> integer(-128, 127);
        for (auto &row : a) for (auto &x : row) x = int8_t(integer(random));
        for (auto &row : b) for (auto &x : row) x = int8_t(integer(random));
        scales = {{-128, -32, -8, -2, -1, 0, 1, 2, 4, 7, 8, 15, 31, 32, 126, 127}};
        run(dut, "random_full_range_shift", a, b, scales, true);
        for (unsigned row = 0; row < 16; ++row)
            for (unsigned k = 0; k < 32; ++k) {
                int value = int(((row * 32 + k) * 37 + 11) % 256);
                a[row][k] = int8_t(value < 128 ? value : value - 256);
            }
        for (unsigned k = 0; k < 32; ++k)
            for (unsigned col = 0; col < 16; ++col) {
                int value = int(((k * 16 + col) * 71 + 19) % 256);
                b[k][col] = int8_t(value < 128 ? value : value - 256);
            }
        scales.fill(-1);
        run(dut, "matched_host_benchmark_shift", a, b, scales, true);
        dut.final();if(waveform)waveform->close();
        const auto &st=observer.stats;
        std::cout<<"MONITOR edges="<<st.edges<<" returns="<<st.demand_returns<<" publications="<<st.publications<<" feeds="<<st.feeds<<" completed="<<st.completed_jobs<<" strict_pending_violations="<<st.exact_assertion_pending_accept_violation<<" legal_turnovers="<<st.legal_turnovers<<" data_unobserved="<<st.feed_data_unobserved<<"\n";
        std::cout << "{\"summary\":true,\"status\":\"PASS\",\"m\":"<<shape_m<<",\"n\":"<<shape_n<<",\"k\":"<<shape_k
            <<",\"expected_jobs\":4,\"completed_jobs\":"<<completed_jobs<<",\"verified_jobs\":"<<verified_jobs
            <<",\"correct_outputs\":"<<correct_outputs<<",\"expected_outputs\":"<<4*shape_m*shape_n<<",\"monitor\":"<<enable_monitor<<"}\n";
        require(verified_jobs==4 && correct_outputs==4*shape_m*shape_n,"incorrect final verification counts");
        std::cout << "RESIDENT SHAPE PASS: " << correct_outputs << " outputs, 4 autonomous jobs\n";
    } catch (const std::exception &error) {
        const auto &st=observer.stats;
        std::cout << "{\"summary\":true,\"status\":\"FAIL\",\"m\":"<<shape_m<<",\"n\":"<<shape_n<<",\"k\":"<<shape_k
            <<",\"case\":"<<std::quoted(case_name)<<",\"error\":"<<std::quoted(error.what())<<",\"tick\":"<<ticks
            <<",\"expected_jobs\":4,\"completed_jobs\":"<<completed_jobs<<",\"verified_jobs\":"<<verified_jobs
            <<",\"correct_outputs\":"<<correct_outputs<<",\"expected_outputs\":"<<4*shape_m*shape_n
            <<",\"monitor\":"<<enable_monitor<<",\"monitor_edges\":"<<st.edges<<",\"monitor_returns\":"<<st.demand_returns
            <<",\"monitor_publications\":"<<st.publications<<",\"strict_pending_violations\":"<<st.exact_assertion_pending_accept_violation<<"}\n";
        std::cerr << "RESIDENT P0 FAIL: " << error.what() << '\n';
        return EXIT_FAILURE;
    }
}
