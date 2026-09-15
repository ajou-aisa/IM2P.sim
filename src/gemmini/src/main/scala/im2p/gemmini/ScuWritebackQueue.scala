package im2p.gemmini

import chisel3._
import chisel3.util._

final class ScuWritebackEntry(
  lanes: Int,
  bankWidth: Int,
  rowWidth: Int,
  workIdWidth: Int,
  fragmentIdWidth: Int,
  generationWidth: Int,
  scaleAddressWidth: Int,
) extends Bundle {
  val data = Vec(lanes, SInt(32.W))
  val accBank = UInt(bankWidth.W)
  val accRow = UInt(rowWidth.W)
  val mask = UInt(lanes.W)
  val accumulate = Bool()
  val workId = UInt(workIdWidth.W)
  val fragmentId = UInt(fragmentIdWidth.W)
  val finalFragment = Bool()
  val scaleAddress = UInt(scaleAddressWidth.W)
  val scaleGeneration = UInt(generationWidth.W)
}

final class ScuWritebackQueue(
  lanes: Int,
  depth: Int,
  bankWidth: Int,
  rowWidth: Int,
  workIdWidth: Int,
  fragmentIdWidth: Int,
  generationWidth: Int,
  scaleAddressWidth: Int,
) extends Module {
  require(lanes > 0, s"lanes must be positive, got $lanes")
  require(depth > 0, s"depth must be positive, got $depth")

  private val countWidth = math.max(1, log2Ceil(depth + 1))
  private val entry = new ScuWritebackEntry(
    lanes,
    bankWidth,
    rowWidth,
    workIdWidth,
    fragmentIdWidth,
    generationWidth,
    scaleAddressWidth,
  )

  val io = IO(new Bundle {
    val reserve = Input(Bool())
    val reserveReady = Output(Bool())
    val enq = Flipped(Decoupled(entry))
    val deq = Decoupled(entry)
    val reserved = Output(UInt(countWidth.W))
    val occupied = Output(UInt(countWidth.W))
    val credits = Output(UInt(countWidth.W))
    val responseWithoutReservation = Output(Bool())
  })

  private val queue = Module(new Queue(entry, depth))
  private val reserved = RegInit(0.U(countWidth.W))
  private val used = Wire(UInt(countWidth.W))

  used := queue.io.count + reserved
  io.reserveReady := used < depth.U
  val reserveFire = io.reserve && io.reserveReady
  val reservationAvailable = reserved =/= 0.U || reserveFire

  queue.io.enq.valid := io.enq.valid && reservationAvailable
  queue.io.enq.bits := io.enq.bits
  io.enq.ready := queue.io.enq.ready && reservationAvailable
  io.deq <> queue.io.deq

  val responseFire = io.enq.fire
  when(reserveFire && !responseFire) {
    reserved := reserved + 1.U
  }.elsewhen(responseFire && !reserveFire) {
    reserved := reserved - 1.U
  }

  io.reserved := reserved
  io.occupied := queue.io.count
  io.credits := depth.U - used
  io.responseWithoutReservation := io.enq.valid && !reservationAvailable

  assert(!(io.enq.valid && reservationAvailable && !queue.io.enq.ready), "reserved SCU response has no queue slot")
}
