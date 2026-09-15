package im2p.gemmini

import chisel3._
import chisel3.util.{Cat, log2Ceil}

final class Int4Packer(lanes: Int) extends Module {
  require(lanes > 0, s"lanes must be positive, got $lanes")

  private val byteCount = (lanes + 1) / 2
  private val laneCountWidth = math.max(1, log2Ceil(lanes + 1))
  private val byteCountWidth = math.max(1, log2Ceil(byteCount + 1))

  val io = IO(new Bundle {
    val in = Input(Vec(lanes, SInt(4.W)))
    val validLanes = Input(UInt(laneCountWidth.W))
    val out = Output(UInt((byteCount * 8).W))
    val validBytes = Output(UInt(byteCountWidth.W))
    val laneCountValid = Output(Bool())
  })

  private val countValid = io.validLanes <= lanes.U
  private val packed = Wire(Vec(byteCount, UInt(8.W)))

  for (byte <- 0 until byteCount) {
    val lowIndex = byte * 2
    val highIndex = lowIndex + 1
    val low = Mux(countValid && io.validLanes > lowIndex.U, io.in(lowIndex).asUInt, 0.U(4.W))
    val high = if (highIndex < lanes) {
      Mux(countValid && io.validLanes > highIndex.U, io.in(highIndex).asUInt, 0.U(4.W))
    } else {
      0.U(4.W)
    }
    packed(byte) := Cat(high, low)
  }

  io.out := packed.asUInt
  io.validBytes := Mux(countValid, (io.validLanes +& 1.U) >> 1, 0.U)
  io.laneCountValid := countValid
}
