package im2p.gemmini

import chisel3._
import gemmini.GemminiArrayConfig
import java.nio.file.{Files, Path}
import java.util.Properties

// Standalone elaboration boundary only. SCU and Gemmini integration never read files.
object HardwareContract {
  def verify(
    profile: ResolvedProfile,
    config: GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float],
    path: Path,
  ): Unit = {
    val properties = new Properties
    val stream = Files.newInputStream(path)
    try properties.load(stream) finally stream.close()
    verify(profile, config, properties)
  }

  def verify(
    profile: ResolvedProfile,
    config: GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float],
    properties: Properties,
  ): Unit = {
    val actual = Map(
      "activation_bits" -> config.inputType.getWidth,
      "weight_bits" -> profile.weightBits,
      "dim" -> config.DIM,
      "block_size" -> 32,
      "array_partial_bits" -> config.spatialArrayOutputType.getWidth,
      "accumulator_bits" -> config.accType.getWidth,
      "bank_count" -> config.sp_banks,
      "bank_rows" -> config.sp_bank_entries,
      "accumulator_rows" -> (config.acc_bank_entries * config.acc_banks),
      "scratchpad_row_bytes" -> profile.scratchpadRowBytes,
      "accumulator_row_bytes" -> profile.accumulatorRowBytes,
      "scratchpad_read_delay" -> config.spad_read_delay,
      "accumulator_latency" -> config.acc_latency,
    )
    actual.foreach { case (key, value) =>
      val configured = Option(properties.getProperty(key))
        .getOrElse(throw new IllegalArgumentException(s"resolved hardware missing $key")).toInt
      require(configured == value,
        s"resolved hardware $key=$configured differs from elaborated value $value")
    }
    require(config.inputType.getWidth == profile.operandBits)
    require(config.DIM == profile.dim)
    require(config.spatialArrayOutputType.getWidth == profile.rawPartialBits)
  }
}
