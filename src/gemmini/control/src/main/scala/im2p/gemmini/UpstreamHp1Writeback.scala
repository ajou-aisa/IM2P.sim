package im2p.gemmini

import chisel3._
import chisel3.util._
import gemmini.{AccumulatorWriteReq, GemminiArrayConfig}
import gemmini.Arithmetic.SIntArithmetic

final class Hp1IssuedOutputContext(
  robIdWidth: Int, dim: Int, scaleAddressWidth: Int, generationWidth: Int, workIdWidth: Int,
) extends Bundle {
  val robId = UInt(robIdWidth.W)
  val validRows = UInt(log2Ceil(dim + 1).W)
  val validColumns = UInt(log2Ceil(dim + 1).W)
  val scaleAddress = UInt(scaleAddressWidth.W)
  val scaleGeneration = UInt(generationWidth.W)
  val workId = UInt(workIdWidth.W)
  val fragmentId = UInt(16.W)
  val firstContribution = Bool()
  val finalFragment = Bool()
  val rmdRaw = Bool()
}

final class UpstreamHp1Writeback(
  profile: ResolvedProfile,
  config: GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float],
  scaleEntries: Int = 256,
  generationWidth: Int = 8,
  workEntries: Int = 128,
) extends Module {
  require(config.DIM == profile.dim && config.accType.getWidth == 32)
  private val dim = profile.dim
  private val bankWidth = math.max(1, log2Ceil(config.acc_banks))
  private val rowWidth = math.max(1, log2Ceil(config.acc_bank_entries))
  private val workWidth = math.max(1, log2Ceil(workEntries))
  private val scaleWidth = math.max(1, log2Ceil(scaleEntries))
  private val robWidth = config.ROB_ID_WIDTH
  private val robEntries = 1 << robWidth
  private val depth = dim * config.reservation_station_entries_ex
  private def contextType = new Hp1IssuedOutputContext(robWidth, dim, scaleWidth, generationWidth, workWidth)
  private def writeType = new AccumulatorWriteReq(config.acc_bank_entries,
    Vec(config.meshColumns, Vec(config.tileColumns, config.accType)))

  val io = IO(new Bundle {
    val contextIssue = Flipped(Decoupled(contextType))
    val rawWrite = Flipped(Vec(config.acc_banks, Decoupled(writeType)))
    val rawCompletion = Flipped(Valid(UInt(robWidth.W)))
    val scaledWrite = Vec(config.acc_banks, Decoupled(writeType))
    val scaleLoad = Flipped(Decoupled(new ScaleLoad(profile.dim, scaleEntries, generationWidth)))
    val scaleRelease = Flipped(Decoupled(new ScaleRelease(profile.dim, scaleEntries, generationWidth)))
    val delayedCompletion = Decoupled(UInt(robWidth.W))
    val workDone = Decoupled(UInt(workWidth.W))
    val drained = Output(Bool())
    val pipelineDrained = Output(Bool())
    val errors = Output(new Bundle {
      val scaleMiss = Bool()
      val scaleInvalidWrite = Bool()
      val scaleWriteConflict = Bool()
      val scaleReleaseMiss = Bool()
      val rawWithoutContext = Bool()
      val responseWithoutReservation = Bool()
      val completionOverflow = Bool()
      val tracker = Bool()
    })
    val events = Output(new Bundle {
      val contextIssued = Bool()
      val rawRow = Bool()
      val scaledRow = Bool()
      val committedRow = Bool()
      val workCompleted = Bool()
      val reservedRows = UInt(log2Ceil(depth + 1).W)
      val queuedRows = UInt(log2Ceil(depth + 1).W)
    })
  })

  private val contexts = Module(new Queue(contextType, config.reservation_station_entries_ex))
  private val writes = Module(new ScuWritebackQueue(dim, depth, bankWidth, rowWidth,
    workWidth, 16, generationWidth, scaleWidth, robWidth))
  private val scales = Seq.fill(dim)(Module(new ScaleMemory(scaleEntries, generationWidth)))
  private val tracker = Module(new ScuCompletionTracker(workEntries))
  private val pendingRob = RegInit(VecInit(Seq.fill(robEntries)(false.B)))
  private val rawDone = RegInit(VecInit(Seq.fill(robEntries)(false.B)))
  private val writeDone = RegInit(VecInit(Seq.fill(robEntries)(false.B)))
  private val rawValid = io.rawWrite.map(_.valid).reduce(_ || _)
  private val rawBank = PriorityEncoder(VecInit(io.rawWrite.map(_.valid)).asUInt)
  private val raw = io.rawWrite(rawBank).bits
  private val head = contexts.io.deq.bits
  private val rowsReceived = RegInit(0.U(log2Ceil(dim + 1).W))
  private val lastRow = rowsReceived + 1.U === head.validRows

  io.scaleLoad.ready := VecInit(scales.map(_.io.write.ready))(io.scaleLoad.bits.column)
  io.scaleRelease.ready := contexts.io.count === 0.U && !io.contextIssue.valid
  for ((memory, column) <- scales.zipWithIndex) {
    memory.io.write.valid := io.scaleLoad.valid && io.scaleLoad.bits.column === column.U
    memory.io.write.bits.address := io.scaleLoad.bits.address
    memory.io.write.bits.generation := io.scaleLoad.bits.generation
    memory.io.write.bits.carrier := io.scaleLoad.bits.carrier
    memory.io.release.valid := io.scaleRelease.fire && io.scaleRelease.bits.column === column.U
    memory.io.release.bits.address := io.scaleRelease.bits.address
    memory.io.release.bits.generation := io.scaleRelease.bits.generation
    memory.io.lookup.address := Mux(rawValid, head.scaleAddress, io.contextIssue.bits.scaleAddress)
    memory.io.lookup.generation := Mux(rawValid, head.scaleGeneration, io.contextIssue.bits.scaleGeneration)
  }
  private val scaleHit = scales.zipWithIndex.map { case (memory, column) =>
    column.U >= Mux(rawValid, head.validColumns, io.contextIssue.bits.validColumns) || memory.io.hit
  }.reduce(_ && _)
  private val shapeValid = io.contextIssue.bits.validRows > 0.U &&
    io.contextIssue.bits.validRows <= dim.U && io.contextIssue.bits.validColumns > 0.U &&
    io.contextIssue.bits.validColumns <= dim.U
  private val needsAllocate = !tracker.io.active(io.contextIssue.bits.workId)
  private val admit = !rawValid && scaleHit && shapeValid &&
    !pendingRob(io.contextIssue.bits.robId) && (!needsAllocate || tracker.io.allocate.ready)
  writes.io.reserve := false.B
  writes.io.reserveBatch.bits := io.contextIssue.bits.validRows
  writes.io.reserveBatch.valid := io.contextIssue.valid && contexts.io.enq.ready && admit
  contexts.io.enq.valid := io.contextIssue.valid && writes.io.reserveBatch.ready && admit
  contexts.io.enq.bits := io.contextIssue.bits
  io.contextIssue.ready := contexts.io.enq.ready && writes.io.reserveBatch.ready && admit
  tracker.io.allocate.valid := io.contextIssue.fire && needsAllocate
  tracker.io.allocate.bits := io.contextIssue.bits.workId
  tracker.io.issue.valid := io.contextIssue.fire
  tracker.io.issue.bits := io.contextIssue.bits.workId
  tracker.io.seal.valid := io.contextIssue.fire && io.contextIssue.bits.finalFragment
  tracker.io.seal.bits := io.contextIssue.bits.workId
  io.workDone <> tracker.io.done

  io.rawWrite.foreach(_.ready := true.B)
  writes.io.enq.valid := rawValid && contexts.io.deq.valid && scaleHit
  writes.io.enq.bits := 0.U.asTypeOf(writes.io.enq.bits)
  writes.io.enq.bits.accBank := rawBank
  writes.io.enq.bits.accRow := raw.addr
  writes.io.enq.bits.accumulate := !head.firstContribution
  writes.io.enq.bits.workId := head.workId
  writes.io.enq.bits.fragmentId := head.fragmentId
  writes.io.enq.bits.finalFragment := head.finalFragment
  writes.io.enq.bits.scaleAddress := head.scaleAddress
  writes.io.enq.bits.scaleGeneration := head.scaleGeneration
  writes.io.enq.bits.robId := head.robId
  writes.io.enq.bits.completeRob := lastRow
  private val laneMask = Wire(Vec(dim, Bool()))
  for (lane <- 0 until dim) {
    val scu = Module(new SCU(profile.rawPartialBits))
    scu.io.partial := raw.data(lane / config.tileColumns)(lane % config.tileColumns)
    scu.io.carrier := Mux(head.rmdRaw, 0.U(32.W), scales(lane).io.carrier)
    writes.io.enq.bits.data(lane) := scu.io.result
    laneMask(lane) := raw.mask(lane * 4) && lane.U < head.validColumns
    when(rawValid) {
      assert(raw.mask.slice(lane * 4, lane * 4 + 4).map(_ === raw.mask(lane * 4)).reduce(_ && _),
        "upstream accumulator byte mask must be uniform within each lane")
    }
  }
  writes.io.enq.bits.mask := laneMask.asUInt
  contexts.io.deq.ready := writes.io.enq.fire && lastRow
  when(writes.io.enq.fire) { rowsReceived := Mux(lastRow, 0.U, rowsReceived + 1.U) }
  assert(PopCount(VecInit(io.rawWrite.map(_.valid))) <= 1.U, "multiple upstream output rows in one cycle")
  when(rawValid) {
    assert(contexts.io.deq.valid, "raw accumulator write has no output context")
    assert(scaleHit, "raw accumulator write has no matching scale generation")
    assert(writes.io.enq.ready, "raw accumulator write has no reserved slot")
  }

  private val commitValid = RegInit(VecInit(Seq.fill(config.acc_latency)(false.B)))
  private val commitEntry = Reg(Vec(config.acc_latency, chiselTypeOf(writes.io.deq.bits)))
  private val queued = writes.io.deq.bits
  private val addressBusy = (0 until config.acc_latency).map { i =>
    commitValid(i) && commitEntry(i).accBank === queued.accBank && commitEntry(i).accRow === queued.accRow
  }.reduce(_ || _)
  for (bank <- 0 until config.acc_banks) {
    val output = io.scaledWrite(bank)
    output.valid := writes.io.deq.valid && queued.accBank === bank.U && !addressBusy
    output.bits.addr := queued.accRow
    output.bits.acc := queued.accumulate
    for (lane <- 0 until dim) {
      output.bits.data(lane / config.tileColumns)(lane % config.tileColumns) := queued.data(lane)
      for (byte <- 0 until 4) output.bits.mask(lane * 4 + byte) := queued.mask(lane)
    }
  }
  writes.io.deq.ready := io.scaledWrite(queued.accBank).ready && !addressBusy
  commitValid(0) := writes.io.deq.fire
  commitEntry(0) := queued
  for (i <- 1 until config.acc_latency) {
    commitValid(i) := commitValid(i - 1)
    commitEntry(i) := commitEntry(i - 1)
  }
  private val committed = commitEntry.last
  private val fragmentCommitted = commitValid.last && committed.completeRob
  tracker.io.commit.valid := fragmentCommitted
  tracker.io.commit.bits := committed.workId

  private val immediate = Module(new Queue(UInt(robWidth.W), robEntries))
  private val delayed = Module(new Queue(UInt(robWidth.W), robEntries))
  immediate.io.enq.valid := io.rawCompletion.valid && !pendingRob(io.rawCompletion.bits)
  immediate.io.enq.bits := io.rawCompletion.bits
  private val readyRob = VecInit((0 until robEntries).map(i => pendingRob(i) && rawDone(i) && writeDone(i)))
  delayed.io.enq.valid := readyRob.asUInt.orR
  delayed.io.enq.bits := PriorityEncoder(readyRob.asUInt)
  private val completion = Module(new RRArbiter(UInt(robWidth.W), 2))
  completion.io.in(0) <> immediate.io.deq
  completion.io.in(1) <> delayed.io.deq
  io.delayedCompletion <> completion.io.out
  when(io.contextIssue.fire) {
    pendingRob(io.contextIssue.bits.robId) := true.B
    rawDone(io.contextIssue.bits.robId) := false.B
    writeDone(io.contextIssue.bits.robId) := false.B
  }
  when(io.rawCompletion.valid && pendingRob(io.rawCompletion.bits)) { rawDone(io.rawCompletion.bits) := true.B }
  when(fragmentCommitted) { writeDone(committed.robId) := true.B }
  when(delayed.io.enq.fire) { pendingRob(delayed.io.enq.bits) := false.B }

  io.errors.scaleMiss := io.contextIssue.valid && !rawValid && !scaleHit
  io.errors.scaleInvalidWrite := scales.map(_.io.invalidWrite).reduce(_ || _)
  io.errors.scaleWriteConflict := scales.map(_.io.writeConflict).reduce(_ || _)
  io.errors.scaleReleaseMiss := scales.map(_.io.releaseMiss).reduce(_ || _)
  io.errors.rawWithoutContext := rawValid && !contexts.io.deq.valid
  io.errors.responseWithoutReservation := writes.io.responseWithoutReservation
  io.errors.completionOverflow := immediate.io.enq.valid && !immediate.io.enq.ready
  io.errors.tracker := tracker.io.duplicateAllocate || tracker.io.invalidIssue || tracker.io.invalidSeal ||
    tracker.io.commitUnderflow || tracker.io.issueOverflow
  assert(!io.errors.completionOverflow, "non-backpressurable upstream completion overflow")
  assert(!io.errors.tracker, "HP1 work completion protocol violation")
  io.events.contextIssued := io.contextIssue.fire
  io.events.rawRow := writes.io.enq.fire
  io.events.scaledRow := writes.io.deq.fire
  io.events.committedRow := commitValid.last
  io.events.workCompleted := io.workDone.fire
  io.events.reservedRows := writes.io.reserved
  io.events.queuedRows := writes.io.occupied
  io.pipelineDrained := !contexts.io.deq.valid && writes.io.reserved === 0.U && writes.io.occupied === 0.U &&
    !commitValid.asUInt.orR && !pendingRob.asUInt.orR &&
    !immediate.io.deq.valid && !delayed.io.deq.valid
  io.drained := io.pipelineDrained && !tracker.io.active.asUInt.orR
}
