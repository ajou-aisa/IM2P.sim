// Extend the existing authority fixture without replacing its prepare-only
// test. This executable calls the real llama generic adapter, real frontend,
// Rust C API and integrated numerical runtime. It observes actual io.work.fire.
#define main im2p_prepare_only_authority_main
#include "schedule_authority_probe.cpp"
#undef main
#include "accepted_work_observer.h"
#include "ggml-gemmini-im2p.hpp"
#include <cstring>
#include <fstream>
#include <string>

namespace {
std::vector<im2p_accepted_work_observation_t> observed;
bool observer_failed = false;
void observe(void *, const im2p_accepted_work_observation_t *record) {
  try {
    observed.push_back(*record);
  } catch (...) {
    observer_failed = true;
  }
}
void geometry_json(std::ostream &out, const im2p_production_geometry_v1_t &g) {
  out << '{';
#define G(field) out << "\"" #field "\":" << g.field << ','
  G(version);
  G(struct_size);
  G(activation_bits);
  G(weight_bits);
  G(dim);
  G(scope);
  G(m);
  G(n);
  G(k);
  G(tile_i_count);
  G(tile_j_count);
  G(tile_k_count);
  G(stripe_rows);
  G(row_begin);
  G(row_count);
#undef G
  out << "\"stripe_id\":" << g.stripe_id << '}';
}
void save_observations(const char *path) {
  std::ofstream out(path);
  require(bool(out), "cannot write acceptance evidence");
  for (const auto &x : observed) {
    out << "{\"geometry\":";
    geometry_json(out, x.geometry);
    out << ',';
#define O(field) out << "\"" #field "\":" << x.field << ','
    O(event);
    O(cycle);
    O(explicit_geometry);
    O(work_ready);
    O(release_column);
    O(release_address);
    O(release_generation);
    O(source_m);
    O(source_n);
    O(source_k);
    O(activation_host_stride);
    O(weight_host_stride);
    O(scale_host_stride);
    O(output_host_stride);
    O(lowerer_tile_i);
    O(lowerer_tile_j);
    O(lowerer_tile_k);
    O(stripe_id);
    O(stripe_row_begin);
    O(stripe_row_count);
    O(host_slot);
    O(i);
    O(j);
    O(k);
    O(rows);
    O(columns);
    O(reduction);
    O(order);
    O(fragment_count);
    O(max_i);
    O(max_j);
    O(max_k);
    O(pad_i);
    O(pad_j);
    O(pad_k);
    O(a_address);
    O(b_address);
    O(c_address);
    O(scale_address);
    O(a_stride);
    O(b_stride);
    O(c_stride);
    O(scale_base);
    O(scale_generation);
    O(fragment_base);
    O(work_base);
    O(accumulate);
    O(final_fragment);
    O(first_loop);
    O(final_loop);
    O(logical_work_id);
    O(rmd_raw);
    O(start_cycle);
    O(done_cycle);
    O(elapsed_cycles);
    O(load_requests);
    O(load_responses);
    O(store_requests);
    O(store_responses);
    O(scale_requests);
#undef O
    out << "\"scale_responses\":" << x.scale_responses << "}\n";
  }
}
void equal(uint64_t expected, uint64_t actual, const char *field,
           size_t ordinal) {
  if (expected != actual)
    throw std::runtime_error(std::string("accepted descriptor divergence #") +
                             std::to_string(ordinal) + " " + field +
                             " expected=" + std::to_string(expected) +
                             " actual=" + std::to_string(actual));
}
size_t verify_accepted(const ggml_gemmini_args_t &args, bool pipeline) {
  std::vector<im2p_accepted_work_observation_t> accepted, publications, done;
  size_t admissions = 0, offers = 0, releases = 0;
  const im2p_accepted_work_observation_t *last_offer = nullptr,
                                         *last_accept = nullptr;
  std::vector<uint64_t> expected_generations;
  for (const auto &x : observed) {
    require(x.explicit_geometry == 1, "explicit companion silently fell back");
    equal(args.I, x.source_m, "source_m", 0);
    equal(args.J, x.source_n, "source_n", 0);
    equal(args.K, x.source_k, "source_k", 0);
    if (x.event == IM2P_OBSERVE_ADMISSION) {
      ++admissions;
      const auto expected = lower::capture_production_geometry(
          args, pipeline ? IM2P_GEOMETRY_STREAM : IM2P_GEOMETRY_FULL, 0, args.I,
          0);
      require(std::memcmp(&expected, &x.geometry, sizeof(expected)) == 0,
              "frontend/C API/Rust admission companion changed");
    }
    if (x.event == IM2P_OBSERVE_PUBLICATION)
      publications.push_back(x);
    if (x.event == IM2P_OBSERVE_OFFERED) {
      equal(offers % 255 + 1, x.scale_generation, "per-offer generation",
            offers);
      ++offers;
      last_offer = &x;
    }
    if (x.event == IM2P_OBSERVE_ACCEPTED) {
      require(last_offer && last_offer->cycle == x.cycle &&
                  last_offer->work_ready,
              "acceptance lacks an identical ready offer");
      auto offered = *last_offer;
      offered.event = IM2P_OBSERVE_ACCEPTED;
      require(std::memcmp(&offered, &x, sizeof(x)) == 0,
              "accepted payload differs from offer");
      if (last_accept)
        equal(((last_accept->reduction + 31) / 32) * last_accept->max_j * DIM,
              releases, "previous loop scale releases", accepted.size());
      releases = 0;
      last_accept = &x;
      expected_generations.push_back((offers - 1) % 255 + 1);
      accepted.push_back(x);
    }
    if (x.event == IM2P_OBSERVE_RELEASE) {
      require(last_accept != nullptr,
              "scale release without an accepted owner");
      equal(last_accept->scale_generation, x.release_generation,
            "release generation", releases);
      equal(releases % DIM, x.release_column, "release lane", releases);
      equal(last_accept->scale_base +
                (last_accept->k / 32) * last_accept->max_j + releases / DIM,
            x.release_address, "release address", releases);
      ++releases;
    }
    if (x.event == IM2P_OBSERVE_DONE)
      done.push_back(x);
  }
  require(admissions == 1 && !observer_failed,
          "admission observation incomplete");
  const size_t height = pipeline ? args.activation_rows_per_stripe : args.I;
  const size_t stripes = (args.I + height - 1) / height;
  equal(pipeline ? stripes : 0, publications.size(), "publications", 0);
  equal(stripes, done.size(), "logical_done", 0);
  size_t ordinal = 0;
  for (size_t stripe = 0; stripe < stripes; ++stripe) {
    const size_t begin = stripe * height;
    const size_t rows = std::min(height, args.I - begin);
    const auto g = lower::capture_production_geometry(
        args, pipeline ? IM2P_GEOMETRY_STRIPE : IM2P_GEOMETRY_FULL, begin, rows,
        stripe);
    if (pipeline)
      require(std::memcmp(&publications.at(stripe).geometry, &g, sizeof(g)) ==
                  0,
              "per-stripe final dispatch geometry changed in transport");
    lower::ScheduleConfig config{
        {args.I, args.J, args.K},
        {DIM, GGML_GEMMINI_ACTIVATION_BITS},
        {g.tile_i_count, g.tile_j_count, g.tile_k_count, g.stripe_rows},
        {args.A.row_stride_bytes, args.J, args.J * 4, args.J * 4, 0},
        true,
        true,
        false};
    require(lower::valid_config(config),
            "selected production lowerer config rejected");
    lower::LoopCursor cursor{begin};
    const uint64_t slot = pipeline ? stripe % 2 : 0;
    while (cursor.i < begin + rows) {
      const auto expected = lower::plan_loop(config, begin + rows, cursor);
      require(ordinal < accepted.size(), "missing actual RTL work acceptance");
      const auto &x = accepted.at(ordinal);
      require(std::memcmp(&g, &x.geometry, sizeof(g)) == 0,
              "accepted stripe used another publication's companion");
#define E(value, field) equal((value), x.field, #field, ordinal)
      E(g.tile_i_count, lowerer_tile_i);
      E(g.tile_j_count, lowerer_tile_j);
      E(g.tile_k_count, lowerer_tile_k);
      E(stripe, stripe_id);
      E(begin, stripe_row_begin);
      E(rows, stripe_row_count);
      E(slot, host_slot);
      E(expected.i, i);
      E(expected.j, j);
      E(expected.k, k);
      E(expected.is, rows);
      E(expected.js, columns);
      E(expected.ks, reduction);
      E(expected.order, order);
      E(expected.fragment_count, fragment_count);
      E(expected.ip / DIM, max_i);
      E(expected.jp / DIM, max_j);
      E(expected.kp / DIM, max_k);
      E(expected.ip - expected.is, pad_i);
      E(expected.jp - expected.js, pad_j);
      E(expected.kp - expected.ks, pad_k);
      E(0x00100000ULL + slot * 0x01000000ULL, a_address);
      E(0x10000000ULL + slot * 0x01000000ULL, b_address);
      E(0x18000000ULL + slot * 0x01000000ULL, scale_address);
      E(expected.final_contribution
            ? 0x20000000ULL + slot * 0x01000000ULL + expected.i * args.J * 4 +
                  expected.j * 4
            : 0,
        c_address);
      E(expected.kp * GGML_GEMMINI_ACTIVATION_BITS / 8, a_stride);
      E(expected.jp * GGML_GEMMINI_ACTIVATION_BITS / 8, b_stride);
      E(args.J * 4, c_stride);
      E(slot * 128, scale_base);
      // The unchanged runtime advances on every offer, including stalls.
      // Derive token identity from the offer ordinal; never rewrite timing.
      E(expected_generations.at(ordinal), scale_generation);
      E(expected.fragment_base, fragment_base);
      E(slot * 64, work_base);
      E(expected.accumulate, accumulate);
      E(expected.final_contribution, final_fragment);
      E(expected.first, first_loop);
      E(expected.last, final_loop);
      E(stripe & 255, logical_work_id);
      E(0, rmd_raw);
#undef E
      lower::advance_loop(config, expected, cursor);
      ++ordinal;
    }
    const auto &d = done.at(stripe);
    require(d.done_cycle > d.start_cycle &&
                d.done_cycle - d.start_cycle == d.elapsed_cycles,
            "actual per-stripe RTL cycle endpoints invalid");
    require(d.load_requests == d.load_responses &&
                d.store_requests == d.store_responses &&
                d.scale_requests == d.scale_responses,
            "actual backing responses not drained");
  }
  equal(ordinal, accepted.size(), "extra accepted work", ordinal);
  if (!pipeline && DIM == 32 && GGML_GEMMINI_ACTIVATION_BITS == 8 &&
      args.I == 129 && args.J == 129 && args.K == 96) {
    require(args.tile_I == 2 && args.tile_J == 4 && args.tile_K == 3 &&
                ordinal == 18,
            "known production 2/4/3 counterexample fell back to 75 loops");
  }
  return ordinal;
}
} // namespace

int main(int argc, char **argv) {
  try {
    require(argc == 6, "M N K full|pipeline observation-jsonl required");
    const size_t m = std::stoull(argv[1]), n = std::stoull(argv[2]),
                 k = std::stoull(argv[3]);
    const std::string mode = argv[4];
    require(mode == "full" || mode == "pipeline", "unsupported probe mode");
    Fixture fixture(m, n, k);
    std::fill(fixture.args.A.bytes->begin(), fixture.args.A.bytes->end(),
              uint8_t{1});
    for (auto &b : fixture.blocks) {
#if GGML_GEMMINI_WEIGHT_BITS == 4
      std::fill(std::begin(b.qs), std::end(b.qs), uint8_t{0x99});
#else
      std::fill(std::begin(b.qs), std::end(b.qs), int8_t{1});
#endif
    }
    im2p_test_set_work_observer(observe, nullptr);
    const auto completion =
        mode == "full"
            ? ggml::gemmini::im2p_adapter::run_full(fixture.args)
            : ggml::gemmini::im2p_adapter::run_stripe_pipeline(fixture.args);
    im2p_test_set_work_observer(nullptr, nullptr);
    save_observations(argv[5]);
    if (!completion.result.ok())
      throw std::runtime_error(std::string("generic adapter execution: ") +
                               completion.result.message);
    const auto count = verify_accepted(fixture.args, mode == "pipeline");
    require(std::all_of(fixture.output.begin(), fixture.output.end(),
                        [k](float x) { return x == static_cast<float>(k); }),
            "numerical all-one GEMM result mismatch");
    const auto &a = fixture.args;
    std::cout
        << "{\"status\":\"PASS\",\"mode\":\"" << mode << "\",\"profile\":\"a"
        << GGML_GEMMINI_ACTIVATION_BITS << "w" << GGML_GEMMINI_WEIGHT_BITS
        << "-d" << DIM << "-hp1\",\"shape\":[" << m << ',' << n << ',' << k
        << "],\"production_tile\":[" << a.tile_I << ',' << a.tile_J << ','
        << a.tile_K << "],\"transported_tile\":[" << a.tile_I << ',' << a.tile_J
        << ',' << a.tile_K << "],\"lowerer_tile\":[" << a.tile_I << ','
        << a.tile_J << ',' << a.tile_K << "],\"expected_work_count\":" << count
        << ",\"accepted_work_count\":" << count
        << ",\"descriptor_equality\":true,\"numerical_output_exact\":true,"
           "\"provider_timing_model_certificate\":false}\n";
  } catch (const std::exception &e) {
    im2p_test_set_work_observer(nullptr, nullptr);
    std::cerr << "PRODUCTION_GEOMETRY_PROBE_FAIL " << e.what() << '\n';
    return 1;
  }
}
