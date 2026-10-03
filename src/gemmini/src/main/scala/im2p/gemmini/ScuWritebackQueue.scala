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
  robIdWidth: Int,
) extends Bundle {
  val data = Vec(lanes, SInt(32.W))
  val accBank = UInt(bankWidth.W)
  val accRow = UInt(rowWidth.W)
  val mask = UInt(lanes.W)
  val accumulate = Bool()
  val workId = UInt(workIdWidth.W)
  val robId = UInt(robIdWidth.W)
  val fragmentId = UInt(fragmentIdWidth.W)
  val finalFragment = Bool()
  val completeRob = Bool()
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
  robIdWidth: Int,
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
    robIdWidth,
  )

  val io = IO(new Bundle {
    val reserve = Input(Bool())
    val reserveReady = Output(Bool())
    val reserveBatch = Flipped(Decoupled(UInt(countWidth.W)))
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
  private val credits = Wire(UInt(countWidth.W))

  used := queue.io.count + reserved
  credits := depth.U - used
  io.reserveReady := !io.reserveBatch.valid && used < depth.U
  io.reserveBatch.ready := !io.reserve &&
    io.reserveBatch.bits =/= 0.U && io.reserveBatch.bits <= credits
  val reserveFire = io.reserve && io.reserveReady
  val reserveBatchFire = io.reserveBatch.fire
  val reservationFire = reserveFire || reserveBatchFire
  val reservationCount = Mux(reserveBatchFire, io.reserveBatch.bits, reserveFire.asUInt)
  val reservationAvailable = reserved =/= 0.U || reservationFire

  queue.io.enq.valid := io.enq.valid && reservationAvailable
  queue.io.enq.bits := io.enq.bits
  io.enq.ready := queue.io.enq.ready && reservationAvailable
  io.deq <> queue.io.deq

  val responseFire = io.enq.fire
  when(reservationFire || responseFire) {
    reserved := reserved + reservationCount - responseFire.asUInt
  }

  io.reserved := reserved
  io.occupied := queue.io.count
  io.credits := credits
  io.responseWithoutReservation := io.enq.valid && !reservationAvailable

  assert(!(io.enq.valid && reservationAvailable && !queue.io.enq.ready), "reserved SCU response has no queue slot")
}
