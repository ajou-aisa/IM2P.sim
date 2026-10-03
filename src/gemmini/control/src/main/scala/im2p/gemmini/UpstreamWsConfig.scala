package im2p.gemmini

import chisel3._
import gemmini._
import gemmini.Arithmetic.SIntArithmetic

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
      acc_banks = 2,
      acc_singleported = false,
      acc_sub_banks = 2,
      acc_capacity = CapacityInKilobytes(64),
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
