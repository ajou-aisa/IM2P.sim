package im2p.gemmini

import chisel3._
import chisel3.util.log2Ceil

final class Int4Unpacker(lanes: Int) extends Module {
  require(lanes > 0, s"lanes must be positive, got $lanes")

  private val byteCount = (lanes + 1) / 2
  private val laneCountWidth = math.max(1, log2Ceil(lanes + 1))

  val io = IO(new Bundle {
    val in = Input(UInt((byteCount * 8).W))
    val validLanes = Input(UInt(laneCountWidth.W))
    val out = Output(Vec(lanes, SInt(4.W)))
    val laneCountValid = Output(Bool())
  })

  private val countValid = io.validLanes <= lanes.U

  for (lane <- 0 until lanes) {
    val nibble = io.in(lane * 4 + 3, lane * 4)
    io.out(lane) := Mux(countValid && io.validLanes > lane.U, nibble.asSInt, 0.S(4.W))
  }
  io.laneCountValid := countValid
}
