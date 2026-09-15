package im2p.gemmini

import chisel3._

final class SatAccumulatorAdder extends Module {
  val io = IO(new Bundle {
    val left = Input(SInt(32.W))
    val right = Input(SInt(32.W))
    val result = Output(SInt(32.W))
  })

  private val sum = io.left.pad(33) +& io.right.pad(33)

  io.result := Mux(
    sum > 2147483647L.S(34.W),
    2147483647L.S,
    Mux(sum < (-2147483648L).S(34.W), (-2147483648L).S, sum.asUInt(31, 0).asSInt),
  )
}
