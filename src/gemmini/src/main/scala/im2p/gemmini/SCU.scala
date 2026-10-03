package im2p.gemmini

import chisel3._

object Hp1ScaleEncoding {
  val ZeroCarrier: BigInt = BigInt("80000000", 16)
  val MaxShift: Int = 32767

  def isZero(carrier: UInt): Bool = carrier === ZeroCarrier.U(32.W)
  def isShift(carrier: UInt): Bool = carrier <= MaxShift.U(32.W)
  def isValid(carrier: UInt): Bool = isZero(carrier) || isShift(carrier)
}

final class SCU(partialWidth: Int) extends Module {
  require(partialWidth >= 2 && partialWidth <= 32, s"partialWidth must be 2..32, got $partialWidth")

  val io = IO(new Bundle {
    val partial = Input(SInt(partialWidth.W))
    val carrier = Input(UInt(32.W))
    val result = Output(SInt(32.W))
    val carrierValid = Output(Bool())
    val zeroScale = Output(Bool())
  })

  private val zeroScale = Hp1ScaleEncoding.isZero(io.carrier)
  private val shiftScale = Hp1ScaleEncoding.isShift(io.carrier)
  private val shiftAmount = io.carrier(4, 0)
  private val partial = io.partial.pad(33)
  private val positiveLimit = 2147483647L.S(33.W) >> shiftAmount
  private val negativeLimit = (-2147483648L).S(33.W) >> shiftAmount
  private val shifted = Wire(SInt(64.W))

  shifted := partial << shiftAmount
  io.carrierValid := zeroScale || shiftScale
  io.zeroScale := zeroScale
  io.result := 0.S

  when(shiftScale && io.partial =/= 0.S) {
    when(io.carrier >= 32.U) {
      io.result := Mux(io.partial < 0.S, (-2147483648L).S, 2147483647L.S)
    }.elsewhen(partial > positiveLimit) {
      io.result := 2147483647L.S
    }.elsewhen(partial < negativeLimit) {
      io.result := (-2147483648L).S
    }.otherwise {
      io.result := shifted.asUInt(31, 0).asSInt
    }
  }
}
