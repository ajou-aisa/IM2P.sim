package im2p.gemmini

import chisel3._

// Only the execution facts needed by Gemmini output context/writeback.
class Hp1LoopMetadata extends Bundle {
  val maxI = UInt(16.W)
  val maxJ = UInt(16.W)
  val maxK = UInt(16.W)
  val padI = UInt(16.W)
  val padJ = UInt(16.W)
  val padK = UInt(16.W)
  val scaleBase = UInt(16.W)
  val scaleGeneration = UInt(8.W)
  val fragmentBase = UInt(16.W)
  val workBase = UInt(8.W)
  val accumulate = Bool()
  val finalFragment = Bool()
  val rmdRaw = Bool()

  // Historical/diagnostic raw descriptors bypass HP1 scaling. Production residual
  // work keeps rmdRaw=false and uses the normal HP1 SCU; the CPU owns radix only.
  def rawShapeValid(dim: Int): Bool = {
    val compactK = maxK * dim.U - padK
    !rmdRaw || (maxK =/= 0.U && padK < dim.U && compactK > 0.U &&
      compactK <= 32.U && fragmentBase === 0.U && !accumulate && finalFragment)
  }
}
