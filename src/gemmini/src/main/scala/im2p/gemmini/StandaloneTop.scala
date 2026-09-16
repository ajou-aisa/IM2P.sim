package im2p.gemmini

import chisel3._
import chisel3.util._
import gemmini._
import gemmini.Arithmetic.SIntArithmetic

final class StandaloneMeshTag extends Bundle with TagQueueTag {
  val payloadValid = Bool()

  override def make_this_garbage(dummy: Int = 0): Unit = {
    payloadValid := false.B
  }
}

final class StandaloneTop(
  val profile: ResolvedProfile,
  scratchpadBankRows: Int,
  accumulatorRows: Int,
  scaleEntries: Int = 4,
  generationWidth: Int = 8,
  workEntries: Int = 8,
) extends Module {
  require(accumulatorRows >= profile.dim, "accumulator must hold at least one output tile")
  require(isPow2(accumulatorRows), "accumulatorRows must be a power of two")
  require(scratchpadBankRows >= profile.dim, "scratchpad bank must hold at least one tile")
  require(isPow2(scratchpadBankRows), "scratchpad bank rows must be a power of two")
  require(isPow2(scaleEntries), "scaleEntries must be a power of two")
  require(isPow2(workEntries), "workEntries must be a power of two")

  override def desiredName: String =
    s"IM2PGemminiHP1A${profile.operandBits}W${profile.weightBits}D${profile.dim}"

  private val dim = profile.dim
  private val rowBits = dim * profile.operandBits
  private val accumulatorAddressWidth = log2Ceil(accumulatorRows)
  private val scaleAddressWidth = log2Ceil(scaleEntries)
  private val workIdWidth = log2Ceil(workEntries)
  private val rowCountWidth = log2Ceil(dim + 1)
  private val accumulatorLatency = 2

  val io = IO(new Bundle {
    val localLoad = Flipped(Decoupled(new LocalMemoryLoad(profile, scratchpadBankRows)))
    val scaleLoad = Flipped(Decoupled(new ScaleLoad(profile.dim, scaleEntries, generationWidth)))
    val scaleRelease = Flipped(Decoupled(new ScaleRelease(profile.dim, scaleEntries, generationWidth)))
    val command = Flipped(Decoupled(new StandaloneFragmentCommand(
      profile,
      scratchpadBankRows,
      accumulatorRows,
      scaleEntries,
      generationWidth,
      workIdWidth,
    )))
    val fragmentConsumed = Valid(UInt(workIdWidth.W))
    val done = Decoupled(UInt(workIdWidth.W))
    val resultRequest = Flipped(Decoupled(new StandaloneResultRequest(accumulatorRows)))
    val result = Decoupled(new StandaloneResult(profile, accumulatorRows))
    val busy = Output(Bool())
    val commandError = Output(Bool())
    val scaleError = Output(Bool())
    val coreCycle = Output(UInt(64.W))
    val startCycle = Output(UInt(64.W))
    val doneCycle = Output(UInt(64.W))
    val elapsedCycles = Output(UInt(64.W))
    val measurementValid = Output(Bool())
  })

  private val localMemory = Module(new StandaloneLocalMemory(profile, scratchpadBankRows))
  localMemory.io.load <> io.localLoad

  private val scaleMemories = Seq.fill(dim)(Module(new ScaleMemory(scaleEntries, generationWidth)))
  private val selectedScaleWriteReady = VecInit(scaleMemories.map(_.io.write.ready))(io.scaleLoad.bits.column)
  io.scaleLoad.ready := selectedScaleWriteReady
  for ((memory, column) <- scaleMemories.zipWithIndex) {
    memory.io.write.valid := io.scaleLoad.valid && io.scaleLoad.bits.column === column.U
    memory.io.write.bits.address := io.scaleLoad.bits.address
    memory.io.write.bits.generation := io.scaleLoad.bits.generation
    memory.io.write.bits.carrier := io.scaleLoad.bits.carrier
  }

  private val Seq(
    idle,
    issuePreload,
    feedPreload,
    reserveWriteback,
    issueCompute,
    feedCompute,
    issueFlush,
    waitForFragment,
    waitForFinal,
  ) = Enum(9)
  private val state = RegInit(idle)
  private val command = Reg(new StandaloneFragmentCommand(
    profile,
    scratchpadBankRows,
    accumulatorRows,
    scaleEntries,
    generationWidth,
    workIdWidth,
  ))
  private val readRows = RegInit(0.U(rowCountWidth.W))
  private val sentRows = RegInit(0.U(rowCountWidth.W))
  private val responseRows = RegInit(0.U(rowCountWidth.W))
  private val reservedRows = RegInit(0.U(rowCountWidth.W))
  private val fragmentCaptured = RegInit(false.B)
  private val fragmentCommitted = RegInit(false.B)

  private val activeScaleAddress = Mux(state === idle, io.command.bits.scaleAddress, command.scaleAddress)
  private val activeScaleGeneration = Mux(state === idle, io.command.bits.scaleGeneration, command.scaleGeneration)
  scaleMemories.foreach { memory =>
    memory.io.lookup.address := activeScaleAddress
    memory.io.lookup.generation := activeScaleGeneration
  }
  private val requestedColumns = Mux(state === idle, io.command.bits.validColumns, command.validColumns)
  private val scaleHits = scaleMemories.zipWithIndex.map { case (memory, column) =>
    column.U >= requestedColumns || memory.io.hit
  }
  private val allScaleHits = scaleHits.reduce(_ && _)

  private val releaseMatchesActive = state =/= idle &&
    io.scaleRelease.bits.address === command.scaleAddress &&
    io.scaleRelease.bits.generation === command.scaleGeneration
  io.scaleRelease.ready := !releaseMatchesActive
  for ((memory, column) <- scaleMemories.zipWithIndex) {
    memory.io.release.valid := io.scaleRelease.fire && io.scaleRelease.bits.column === column.U
    memory.io.release.bits.address := io.scaleRelease.bits.address
    memory.io.release.bits.generation := io.scaleRelease.bits.generation
  }
  io.scaleError := scaleMemories.map(memory =>
    memory.io.invalidWrite || memory.io.writeConflict || memory.io.releaseMiss
  ).reduce(_ || _)

  private val tracker = Module(new ScuCompletionTracker(workEntries, countWidth = rowCountWidth + 1))
  tracker.io.allocate.valid := false.B
  tracker.io.allocate.bits := io.command.bits.workId
  tracker.io.issue.valid := false.B
  tracker.io.issue.bits := 0.U
  tracker.io.seal.valid := false.B
  tracker.io.seal.bits := 0.U
  tracker.io.commit.valid := false.B
  tracker.io.commit.bits := 0.U
  io.done <> tracker.io.done

  private val commandShapeValid = io.command.bits.validRows =/= 0.U &&
    io.command.bits.validRows <= dim.U &&
    io.command.bits.validColumns =/= 0.U &&
    io.command.bits.validColumns <= dim.U &&
    io.command.bits.fragmentLength =/= 0.U &&
    io.command.bits.fragmentLength <= profile.fragmentLimit.U &&
    io.command.bits.activationBase +& dim.U <= scratchpadBankRows.U &&
    io.command.bits.weightBase +& dim.U <= scratchpadBankRows.U &&
    io.command.bits.accumulatorBase +& io.command.bits.validRows <= accumulatorRows.U
  private val workSlotReady = Mux(
    io.command.bits.firstContribution,
    tracker.io.allocate.ready,
    tracker.io.active(io.command.bits.workId),
  )
  io.command.ready := state === idle && commandShapeValid && allScaleHits && workSlotReady
  io.commandError := io.command.valid && !commandShapeValid

  when(io.command.fire) {
    command := io.command.bits
    readRows := 0.U
    sentRows := 0.U
    responseRows := 0.U
    reservedRows := 0.U
    fragmentCaptured := false.B
    fragmentCommitted := false.B
    state := issuePreload
  }
  tracker.io.allocate.valid := io.command.fire && io.command.bits.firstContribution

  private val mesh = Module(new MeshWithDelays(
    inputType = SInt(profile.operandBits.W),
    outputType = SInt(profile.rawPartialBits.W),
    accType = SInt(32.W),
    tagType = new StandaloneMeshTag,
    df = Dataflow.WS,
    tree_reduction = false,
    tile_latency = 0,
    output_delay = 0,
    tileRows = 1,
    tileColumns = 1,
    meshRows = dim,
    meshColumns = dim,
    leftBanks = 1,
    upBanks = 1,
  ))

  mesh.io.req.valid := state === issuePreload || state === issueCompute || state === issueFlush
  mesh.io.req.bits.pe_control.dataflow := Dataflow.WS.id.U
  mesh.io.req.bits.pe_control.propagate := 1.U
  mesh.io.req.bits.pe_control.shift := 0.U
  mesh.io.req.bits.a_transpose := false.B
  mesh.io.req.bits.bd_transpose := false.B
  mesh.io.req.bits.total_rows := dim.U
  private val preloadOwnsResultTag = state === issuePreload
  mesh.io.req.bits.tag.payloadValid := preloadOwnsResultTag
  mesh.io.req.bits.flush := Mux(state === issueFlush, 1.U, 0.U)

  when(state === issuePreload && mesh.io.req.fire) {
    readRows := 0.U
    sentRows := 0.U
    state := feedPreload
  }
  when(state === issueCompute && mesh.io.req.fire) {
    readRows := 0.U
    sentRows := 0.U
    state := feedCompute
  }
  when(state === issueFlush && mesh.io.req.fire) {
    state := waitForFragment
  }

  localMemory.io.activationRead.valid := state === feedCompute && readRows < dim.U
  localMemory.io.activationRead.bits.slot := command.slot
  localMemory.io.activationRead.bits.row := command.activationBase + readRows
  when(localMemory.io.activationRead.fire) {
    readRows := readRows + 1.U
  }
  localMemory.io.weightRead.valid := state === feedPreload && readRows < dim.U
  localMemory.io.weightRead.bits.slot := command.slot
  localMemory.io.weightRead.bits.row := command.weightBase + (dim - 1).U - readRows
  when(localMemory.io.weightRead.fire) {
    readRows := readRows + 1.U
  }

  private val feedingPreload = state === feedPreload && localMemory.io.weightData.valid
  private val feedingCompute = state === feedCompute && localMemory.io.activationData.valid
  private val activeRow = Mux(feedingCompute, localMemory.io.activationData.bits, localMemory.io.weightData.bits)
  private val unmaskedOperandRow = activeRow.asTypeOf(Vec(dim, SInt(profile.operandBits.W)))
  private val weightRow = (dim - 1).U(rowCountWidth.W) - sentRows
  private val operandRow = Wire(Vec(dim, SInt(profile.operandBits.W)))
  for (column <- 0 until dim) {
    val fragmentLaneValid = Mux(
      feedingCompute,
      column.U < command.fragmentLength,
      weightRow < command.fragmentLength,
    )
    operandRow(column) := Mux(fragmentLaneValid, unmaskedOperandRow(column), 0.S)
  }
  private val allMeshInputsReady = mesh.io.a.ready && mesh.io.b.ready && mesh.io.d.ready

  mesh.io.a.valid := feedingPreload || feedingCompute
  mesh.io.b.valid := feedingPreload || feedingCompute
  mesh.io.d.valid := feedingPreload || feedingCompute
  mesh.io.a.bits := Mux(
    feedingCompute,
    operandRow.asTypeOf(mesh.io.a.bits),
    0.U.asTypeOf(mesh.io.a.bits),
  )
  mesh.io.b.bits := 0.U.asTypeOf(mesh.io.b.bits)
  mesh.io.d.bits := Mux(
    feedingPreload,
    operandRow.asTypeOf(mesh.io.d.bits),
    0.U.asTypeOf(mesh.io.d.bits),
  )
  localMemory.io.activationData.ready := state === feedCompute && allMeshInputsReady
  localMemory.io.weightData.ready := state === feedPreload && allMeshInputsReady

  private val inputRowFire = (feedingPreload || feedingCompute) && allMeshInputsReady
  when(inputRowFire) {
    sentRows := sentRows + 1.U
    when(sentRows === (dim - 1).U) {
      sentRows := 0.U
      state := Mux(state === feedPreload, reserveWriteback, issueFlush)
    }
  }

  private val writeback = Module(new ScuWritebackQueue(
    lanes = dim,
    depth = dim * 2,
    bankWidth = 1,
    rowWidth = accumulatorAddressWidth,
    workIdWidth = workIdWidth,
    robIdWidth = workIdWidth,
    fragmentIdWidth = rowCountWidth,
    generationWidth = generationWidth,
    scaleAddressWidth = scaleAddressWidth,
  ))
  writeback.io.reserve := state === reserveWriteback && reservedRows < dim.U
  writeback.io.reserveBatch.valid := false.B
  writeback.io.reserveBatch.bits := 0.U
  when(writeback.io.reserve && writeback.io.reserveReady) {
    reservedRows := reservedRows + 1.U
    when(reservedRows === (dim - 1).U) {
      state := issueCompute
    }
  }

  private val scaled = Seq.tabulate(dim) { column =>
    val scu = Module(new SCU(profile.rawPartialBits))
    scu.io.partial := mesh.io.resp.bits.data(column)(0)
    scu.io.carrier := scaleMemories(column).io.carrier
    scu
  }
  private val payloadResponse = mesh.io.resp.valid && mesh.io.resp.bits.tag.payloadValid
  private val naturalResponseRow = responseRows
  private val responseRowValid = naturalResponseRow < command.validRows
  private val laneMask = VecInit((0 until dim).map(column => responseRowValid && column.U < command.validColumns))

  writeback.io.enq.valid := payloadResponse
  writeback.io.enq.bits.data := VecInit(scaled.map(_.io.result))
  writeback.io.enq.bits.accBank := 0.U
  writeback.io.enq.bits.accRow := Mux(
    responseRowValid,
    command.accumulatorBase + naturalResponseRow,
    command.accumulatorBase,
  )
  writeback.io.enq.bits.mask := laneMask.asUInt
  writeback.io.enq.bits.accumulate := !command.firstContribution
  writeback.io.enq.bits.workId := command.workId
  writeback.io.enq.bits.robId := command.workId
  writeback.io.enq.bits.fragmentId := naturalResponseRow
  writeback.io.enq.bits.finalFragment := command.finalFragment && mesh.io.resp.bits.last
  writeback.io.enq.bits.completeRob := command.finalFragment && mesh.io.resp.bits.last
  writeback.io.enq.bits.scaleAddress := command.scaleAddress
  writeback.io.enq.bits.scaleGeneration := command.scaleGeneration

  io.fragmentConsumed.valid := false.B
  io.fragmentConsumed.bits := command.workId
  when(payloadResponse) {
    assert(writeback.io.enq.ready, "reserved MeshWithDelays response could not enter SCU writeback")
    assert(scaled.map(_.io.carrierValid).reduce(_ && _), "invalid HP1 scale reached SCU")
    responseRows := responseRows + 1.U
    when(mesh.io.resp.bits.last) {
      fragmentCaptured := true.B
    }
  }

  private val accumulatorRowType = Vec(dim, Vec(1, SInt(32.W)))
  private val accumulator = Module(new AccumulatorMem(
    n = accumulatorRows,
    t = accumulatorRowType,
    scale_func = (value: SInt, _: UInt) => value,
    scale_t = UInt(1.W),
    acc_singleported = false,
    acc_sub_banks = 2,
    use_shared_ext_mem = false,
    acc_latency = accumulatorLatency,
    acc_type = SInt(32.W),
    is_dummy = false,
  ))
  private val writeHasData = writeback.io.deq.bits.mask.orR
  accumulator.io.write.valid := writeback.io.deq.valid && writeHasData
  accumulator.io.write.bits.addr := writeback.io.deq.bits.accRow
  accumulator.io.write.bits.acc := writeback.io.deq.bits.accumulate
  for (column <- 0 until dim) {
    accumulator.io.write.bits.data(column)(0) := writeback.io.deq.bits.data(column)
    for (byte <- 0 until 4) {
      accumulator.io.write.bits.mask(column * 4 + byte) := writeback.io.deq.bits.mask(column)
    }
  }
  writeback.io.deq.ready := Mux(writeHasData, accumulator.io.write.ready, true.B)

  private val saturatingAdders = Seq.fill(dim)(Module(new SatAccumulatorAdder))
  for ((adder, column) <- saturatingAdders.zipWithIndex) {
    adder.io.left := accumulator.io.adder.op1(column)(0)
    adder.io.right := accumulator.io.adder.op2(column)(0)
    accumulator.io.adder.sum(column)(0) := ShiftRegister(adder.io.result, accumulatorLatency - 1)
  }

  private val accumulatorWriteFire = accumulator.io.write.fire
  tracker.io.issue.valid := accumulatorWriteFire
  tracker.io.issue.bits := writeback.io.deq.bits.workId
  private val committed = ShiftRegister(accumulatorWriteFire, accumulatorLatency)
  private val committedWork = ShiftRegister(writeback.io.deq.bits.workId, accumulatorLatency)
  private val lastValidWrite = accumulatorWriteFire &&
    writeback.io.deq.bits.fragmentId === command.validRows - 1.U
  private val lastValidWriteCommitted = ShiftRegister(lastValidWrite, accumulatorLatency)
  tracker.io.commit.valid := committed
  tracker.io.commit.bits := committedWork
  when(lastValidWriteCommitted) {
    fragmentCommitted := true.B
  }
  private val finalEntryDequeued = writeback.io.deq.fire && writeback.io.deq.bits.finalFragment
  tracker.io.seal.valid := RegNext(finalEntryDequeued, false.B)
  tracker.io.seal.bits := RegEnable(writeback.io.deq.bits.workId, finalEntryDequeued)

  when(state === waitForFragment && fragmentCaptured && fragmentCommitted && mesh.io.req.ready) {
    io.fragmentConsumed.valid := true.B
    io.fragmentConsumed.bits := command.workId
    state := Mux(command.finalFragment, waitForFinal, idle)
  }

  private val completedWork = RegInit(VecInit(Seq.fill(workEntries)(false.B)))
  private val completedBase = Reg(Vec(workEntries, UInt(accumulatorAddressWidth.W)))
  private val completedRows = Reg(Vec(workEntries, UInt(rowCountWidth.W)))
  when(io.command.fire && io.command.bits.firstContribution) {
    val newBase = io.command.bits.accumulatorBase
    val newEnd = newBase +& io.command.bits.validRows
    for (index <- 0 until workEntries) {
      val existingEnd = completedBase(index) +& completedRows(index)
      when(completedWork(index) && completedBase(index) < newEnd && newBase < existingEnd) {
        completedWork(index) := false.B
      }
    }
    completedBase(io.command.bits.workId) := newBase
    completedRows(io.command.bits.workId) := io.command.bits.validRows
    completedWork(io.command.bits.workId) := false.B
  }
  when(tracker.io.done.valid) {
    completedWork(tracker.io.done.bits) := true.B
  }

  private val resultRows = Module(new Queue(UInt(accumulatorAddressWidth.W), 2))
  private val resultAddressVisible = VecInit((0 until workEntries).map { index =>
    completedWork(index) &&
      io.resultRequest.bits.row >= completedBase(index) &&
      io.resultRequest.bits.row - completedBase(index) < completedRows(index)
  }).asUInt.orR
  private val resultReadAllowed = !accumulator.io.write.valid && resultAddressVisible
  accumulator.io.read.req.valid := io.resultRequest.valid && resultReadAllowed && resultRows.io.enq.ready
  accumulator.io.read.req.bits.addr := io.resultRequest.bits.row
  accumulator.io.read.req.bits.scale := 0.U
  accumulator.io.read.req.bits.igelu_qb := 0.S
  accumulator.io.read.req.bits.igelu_qc := 0.S
  accumulator.io.read.req.bits.iexp_qln2 := 0.S
  accumulator.io.read.req.bits.iexp_qln2_inv := 0.S
  accumulator.io.read.req.bits.act := 0.U
  accumulator.io.read.req.bits.full := true.B
  accumulator.io.read.req.bits.fromDMA := false.B
  io.resultRequest.ready := accumulator.io.read.req.ready && resultReadAllowed && resultRows.io.enq.ready
  resultRows.io.enq.valid := accumulator.io.read.req.fire
  resultRows.io.enq.bits := io.resultRequest.bits.row
  io.result.valid := accumulator.io.read.resp.valid && resultRows.io.deq.valid
  io.result.bits.row := resultRows.io.deq.bits
  io.result.bits.data := VecInit(accumulator.io.read.resp.bits.data.map(_(0)))
  accumulator.io.read.resp.ready := io.result.ready && resultRows.io.deq.valid
  resultRows.io.deq.ready := io.result.fire

  when(state === waitForFinal && tracker.io.done.fire) {
    state := idle
  }

  private val cycles = Module(new MatmulCycleCounter)
  cycles.io.start := io.command.fire && io.command.bits.firstContribution
  cycles.io.done := tracker.io.done.valid
  io.coreCycle := cycles.io.coreCycle
  io.startCycle := cycles.io.startCycle
  io.doneCycle := cycles.io.doneCycle
  io.elapsedCycles := cycles.io.elapsedCycles
  io.measurementValid := cycles.io.measurementValid
  io.busy := state =/= idle

  assert(!tracker.io.invalidIssue, "writeback issued for inactive or sealed work")
  assert(!tracker.io.invalidSeal, "completion sealed for inactive work")
  assert(!tracker.io.commitUnderflow, "accumulator commit was not issued")
  assert(!tracker.io.issueOverflow, "completion outstanding counter overflow")
}
