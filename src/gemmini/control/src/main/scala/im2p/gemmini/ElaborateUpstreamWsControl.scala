package im2p.gemmini

import circt.stage.ChiselStage
import gemmini.{Dataflow, GemminiConfigs}
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import freechips.rocketchip.system.BaseConfig

object ElaborateUpstreamWsControl {
  def main(arguments: Array[String]): Unit = {
    require(arguments.length == 1, "output directory required")
    implicit val parameters: Parameters = new BaseConfig().toInstance
    val config = GemminiConfigs.defaultConfig.copy(
      dataflow = Dataflow.WS,
      has_training_convs = false,
      has_max_pool = false,
      has_nonlinear_activations = false,
      has_dw_convs = false,
      has_normalizations = false,
      has_first_layer_optimizations = false,
    )
    val harness = LazyModule(new UpstreamWsControlHarness(config))
    ChiselStage.emitSystemVerilogFile(
      harness.module,
      Array("--target-dir", arguments(0)),
      Array("--split-verilog", s"-o=${arguments(0)}"),
    )
  }
}
