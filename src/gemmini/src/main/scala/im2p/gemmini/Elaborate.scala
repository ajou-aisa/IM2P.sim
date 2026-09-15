package im2p.gemmini

import circt.stage.ChiselStage
import java.nio.file.{Files, Path}

object Elaborate {
  private def arguments(values: Array[String]): Map[String, String] = {
    require(values.length % 2 == 0, "arguments must be --name value pairs")
    values.grouped(2).map {
      case Array(name, value) if name.startsWith("--") => name.drop(2) -> value
      case other => throw new IllegalArgumentException(s"invalid argument pair: ${other.mkString(" ")}")
    }.toMap
  }

  def main(values: Array[String]): Unit = {
    val args = arguments(values)
    val profile = ResolvedProfile(
      operandBits = args("a-bits").toInt,
      weightBits = args("w-bits").toInt,
      dim = args("dim").toInt,
    )
    val output = Path.of(args("out")).toAbsolutePath.normalize()
    val scratchpadBankRows = args("scratchpad-bank-rows").toInt
    val accumulatorRows = args.get("accumulator-rows").fold(256)(_.toInt)
    require(!Files.exists(output) || Files.isDirectory(output), s"output is not a directory: $output")
    Files.createDirectories(output)

    ChiselStage.emitSystemVerilogFile(
      new StandaloneTop(profile, scratchpadBankRows, accumulatorRows),
      Array("--target-dir", output.toString),
    )
    println(s"generated ${profile.name} in $output")
  }
}
