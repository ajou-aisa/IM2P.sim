package im2p.gemmini

import chisel3._
import chisel3.util.log2Ceil

final class ScuFragmentPlanner(dimension: Int, kWidth: Int = 32) extends Module {
  require(dimension > 0, s"dimension must be positive, got $dimension")
  require(kWidth >= 6, s"kWidth must hold a K32 boundary, got $kWidth")

  private val fragmentWidth = math.max(1, log2Ceil(dimension + 1))

  val io = IO(new Bundle {
    val kIndex = Input(UInt(kWidth.W))
    val remainingK = Input(UInt(kWidth.W))
    val fragmentLength = Output(UInt(fragmentWidth.W))
    val remainingInBlock = Output(UInt(6.W))
    val nextKIndex = Output(UInt(kWidth.W))
    val lastFragment = Output(Bool())
    val hasWork = Output(Bool())
  })

  private val remainingInBlock = Wire(UInt(kWidth.W))
  private val dimensionLimit = dimension.U(kWidth.W)
  private val dimensionOrTail = Mux(io.remainingK < dimensionLimit, io.remainingK, dimensionLimit)
  private val fragment = Mux(dimensionOrTail < remainingInBlock, dimensionOrTail, remainingInBlock)

  remainingInBlock := 32.U - io.kIndex(4, 0)
  io.fragmentLength := fragment
  io.remainingInBlock := remainingInBlock
  io.nextKIndex := io.kIndex + fragment
  io.lastFragment := fragment === io.remainingK
  io.hasWork := io.remainingK =/= 0.U
}
