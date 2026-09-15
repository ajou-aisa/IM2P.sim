package im2p.gemmini

import circt.stage.ChiselStage
import freechips.rocketchip.system.BaseConfig
import java.nio.file.{Files, Path}
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.LazyModule

object ElaborateUpstreamWsHp1 {
  private def arguments(values: Array[String]): Map[String, String] = {
    require(values.length % 2 == 0, "arguments must be --name value pairs")
    values.grouped(2).map {
      case Array(name, value) if name.startsWith("--") => name.drop(2) -> value
      case other => throw new IllegalArgumentException(s"invalid argument pair: ${other.mkString(" ")}")
    }.toMap
  }

  def main(values: Array[String]): Unit = {
    val args = arguments(values)
    val profile = ResolvedProfile(args("a-bits").toInt, args("w-bits").toInt, args("dim").toInt)
    val output = Path.of(args("out")).toAbsolutePath.normalize()
    require(!Files.exists(output) || Files.isDirectory(output), s"output is not a directory: $output")
    Files.createDirectories(output)
    implicit val parameters: Parameters = new BaseConfig().toInstance
    val harness = LazyModule(new UpstreamWsHp1Harness(profile))
    ChiselStage.emitSystemVerilogFile(
      harness.module,
      Array("--target-dir", output.toString),
      Array("--split-verilog", s"-o=${output.toString}"),
    )
    println(s"generated upstream-ws ${profile.name} in $output")
  }
}
