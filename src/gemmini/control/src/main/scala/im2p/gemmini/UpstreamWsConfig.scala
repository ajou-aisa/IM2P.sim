package im2p.gemmini

import chisel3._
import gemmini._
import gemmini.Arithmetic.SIntArithmetic

// Physical accumulator and work-ID namespace for the paired microtile. RTL only: the host memory
// contract keeps the Main-compat accumulator rows (64 KiB), so host tiling, admission and the
// cycle model are unchanged while LoopMatmul rotates output slots over the physical rows.
// Factors cover 95% of residual fragments in the P0 capacity analysis.
final case class PhysicalAccumulator(factor: Int, banks: Int, workEntries: Int) {
  require(factor >= 1 && banks >= 2 && workEntries >= 128 && Integer.bitCount(workEntries) == 1)
}

object PhysicalAccumulator {
  private val compatKilobytes = 64
  private val table = Map(
    "a4w4-d16-hp1" -> PhysicalAccumulator(4, 2, 256),
    "a4w4-d32-hp1" -> PhysicalAccumulator(4, 2, 128),
    "a4w4-d64-hp1" -> PhysicalAccumulator(4, 2, 128),
    "a8w8-d16-hp1" -> PhysicalAccumulator(3, 3, 256),
    "a8w8-d32-hp1" -> PhysicalAccumulator(3, 3, 128),
    "a8w8-d64-hp1" -> PhysicalAccumulator(2, 2, 128),
  )

  def apply(profile: ResolvedProfile): PhysicalAccumulator = table(profile.name)
  def compatRows(profile: ResolvedProfile): Int = compatKilobytes * 1024 / profile.accumulatorRowBytes
  def rows(profile: ResolvedProfile): Int = apply(profile).factor * compatRows(profile)
  def kilobytes(profile: ResolvedProfile): Int = apply(profile).factor * compatKilobytes
}

object UpstreamWsConfig {
  def apply(profile: ResolvedProfile): GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float] = {
    GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float](
      inputType = SInt(profile.operandBits.W),
      spatialArrayOutputType = SInt(profile.rawPartialBits.W),
      accType = SInt(32.W),
      dataflow = Dataflow.WS,
      tileRows = 1,
      tileColumns = 1,
      meshRows = profile.dim,
      meshColumns = profile.dim,
      sp_banks = 4,
      sp_singleported = false,
      sp_capacity = CapacityInKilobytes(256),
      spad_read_delay = 4,
      acc_banks = PhysicalAccumulator(profile).banks,
      acc_singleported = false,
      acc_sub_banks = 2,
      acc_capacity = CapacityInKilobytes(PhysicalAccumulator.kilobytes(profile)),
      acc_latency = 2,
      dma_maxbytes = profile.scratchpadRowBytes,
      dma_buswidth = (profile.scratchpadRowBytes * 8).max(128),
      mvin_scale_args = None,
      mvin_scale_acc_args = None,
      mvin_scale_shared = false,
      acc_scale_args = None,
      ex_read_from_spad = true,
      ex_read_from_acc = false,
      ex_write_to_spad = false,
      ex_write_to_acc = true,
      hardcode_d_to_garbage_addr = true,
      has_training_convs = false,
      has_max_pool = false,
      has_nonlinear_activations = false,
      has_dw_convs = false,
      has_normalizations = false,
      has_first_layer_optimizations = false,
      use_firesim_simulation_counters = false,
      use_shared_ext_mem = false,
      clock_gate = false,
    )
  }
}
