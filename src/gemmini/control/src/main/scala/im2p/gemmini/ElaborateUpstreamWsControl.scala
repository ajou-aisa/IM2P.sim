package im2p.gemmini

import circt.stage.ChiselStage
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import freechips.rocketchip.system.BaseConfig

object ElaborateUpstreamWsControl {
  def main(arguments: Array[String]): Unit = {
    require(arguments.length == 1, "output directory required")
    implicit val parameters: Parameters = new BaseConfig().toInstance
    val profile = ResolvedProfile(8, 8, 16)
    val config = UpstreamWsConfig(profile)
    val harness = LazyModule(new UpstreamWsControlHarness(profile, config))
    ChiselStage.emitSystemVerilogFile(
      harness.module,
      Array("--target-dir", arguments(0)),
      Array("--split-verilog", s"-o=${arguments(0)}"),
    )
  }
}
