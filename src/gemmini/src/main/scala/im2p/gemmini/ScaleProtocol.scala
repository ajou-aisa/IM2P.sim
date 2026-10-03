package im2p.gemmini

import chisel3._
import chisel3.util.log2Ceil

// Scalar SCU scale ownership protocol; no profile, host slot, or backing address.
final class ScaleLoad(lanes: Int, scaleEntries: Int, generationWidth: Int)
    extends Bundle {
  val column = UInt(math.max(1, log2Ceil(lanes)).W)
  val address = UInt(math.max(1, log2Ceil(scaleEntries)).W)
  val generation = UInt(generationWidth.W)
  val carrier = UInt(32.W)
}

final class ScaleRelease(lanes: Int, scaleEntries: Int, generationWidth: Int)
    extends Bundle {
  val column = UInt(math.max(1, log2Ceil(lanes)).W)
  val address = UInt(math.max(1, log2Ceil(scaleEntries)).W)
  val generation = UInt(generationWidth.W)
}
