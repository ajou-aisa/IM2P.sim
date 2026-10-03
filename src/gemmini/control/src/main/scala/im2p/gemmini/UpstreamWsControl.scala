package im2p.gemmini

import chisel3._
import chisel3.util._
import gemmini._
import gemmini.Arithmetic.SIntArithmetic
import gemmini.GemminiISA._
import org.chipsalliance.cde.config.Parameters

final class UpstreamInstruction extends Bundle {
  val funct = UInt(7.W)
  val rs1 = UInt(64.W)
  val rs2 = UInt(64.W)
}

final class UpstreamWsControl(
  val profile: ResolvedProfile,
  val config: GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float],
  scaleEntries: Int = 256,
  generationWidth: Int = 8,
  workEntries: Int = 128,
)
  (implicit p: Parameters) extends Module {
  import config._

  require(dataflow == Dataflow.WS)
  require(!hasIm2Col)
  require(DIM == profile.dim)
  require(inputType.getWidth == profile.operandBits)
  require(spatialArrayOutputType.getWidth == profile.rawPartialBits)
  require(accType.getWidth == 32)
  private val execute = Module(new ExecuteController(64, 32, config))
  private val load = Module(new LoadController(config, 40, local_addr_t))
  private val store = Module(new StoreController(config, 40, local_addr_t))
  private val reservation = Module(new ReservationStation(config, new GemminiCmd(reservation_station_entries)))
  private val writeback = Module(new UpstreamHp1Writeback(
    profile,
    config,
    scaleEntries,
    generationWidth,
    workEntries,
  ))
  private val scaleAddressWidth = math.max(1, log2Ceil(scaleEntries))
  private val workIdWidth = math.max(1, log2Ceil(workEntries))

  val io = IO(new Bundle {
    val instruction = Flipped(Decoupled(new UpstreamInstruction))
    val loopMetadata = Flipped(Decoupled(new Hp1LoopMetadata))
    val scaleLoad = Flipped(Decoupled(new ScaleLoad(profile.dim, scaleEntries, generationWidth)))
    val scaleRelease = Flipped(Decoupled(new ScaleRelease(profile.dim, scaleEntries, generationWidth)))
    val dmaRead = chiselTypeOf(load.io.dma)
    val dmaWrite = chiselTypeOf(store.io.dma)
    val srams = chiselTypeOf(execute.io.srams)
    val acc = chiselTypeOf(execute.io.acc)
    val completed = Valid(UInt(ROB_ID_WIDTH.W))
    val workDone = Decoupled(UInt(workIdWidth.W))
    val busy = Output(Bool())
    val loopBusy = Output(Bool())
    val writebackDrained = Output(Bool())
    val protocolError = Output(Bool())
    val loadBusy = Output(Bool())
    val executeBusy = Output(Bool())
    val storeBusy = Output(Bool())
    val events = Output(new Bundle {
      val loadIssued = Bool()
      val executeIssued = Bool()
      val storeIssued = Bool()
      val outputContextIssued = Bool()
      val rawRow = Bool()
      val scaledRow = Bool()
      val accumulatorCommitted = Bool()
      val workCompleted = Bool()
      val loadExecuteOverlap = Bool()
      val loadDmaAccepted = Bool()
      val loadVaddrBytes = UInt(load.io.dma.req.bits.vaddr.getWidth.W)
      val loadLocalAddressRaw = UInt(local_addr_t.getWidth.W)
      val loadColumnsElements = UInt(load.io.dma.req.bits.cols.getWidth.W)
      val loadCommandId = UInt(load.io.dma.req.bits.cmd_id.getWidth.W)
      val scratchpadReadAcceptedBankMask = UInt(sp_banks.W)
      val scratchpadWriteEnabledBankMask = UInt(sp_banks.W)
      val storeDmaAccepted = Bool()
      val storeVaddrBytes = UInt(store.io.dma.req.bits.vaddr.getWidth.W)
      val storeLocalAddressRaw = UInt(local_addr_t.getWidth.W)
      val storeLengthElements = UInt(store.io.dma.req.bits.len.getWidth.W)
      val storeCommandId = UInt(store.io.dma.req.bits.cmd_id.getWidth.W)
    })
  })

  private val raw = Wire(Decoupled(new GemminiCmd(reservation_station_entries)))
  raw.valid := io.instruction.valid
  io.instruction.ready := raw.ready
  raw.bits := 0.U.asTypeOf(raw.bits)
  raw.bits.cmd.inst.funct := io.instruction.bits.funct
  raw.bits.cmd.rs1 := io.instruction.bits.rs1
  raw.bits.cmd.rs2 := io.instruction.bits.rs2

  private val (unrolled, loopBusy) = LoopMatmul(
    Queue(raw, 2),
    reservation.io.matmul_ld_completed,
    reservation.io.matmul_st_completed,
    reservation.io.matmul_ex_completed,
    DIM, 40, reservation_station_entries,
    reservation_station_entries_ld, reservation_station_entries_ex, reservation_station_entries_st,
    sp_banks * sp_bank_entries, acc_banks * acc_bank_entries,
    inputType.getWidth, accType.getWidth, dma_maxbytes,
    new MvinRs2(mvin_rows_bits, mvin_cols_bits, local_addr_t),
    new PreloadRs(mvin_rows_bits, mvin_cols_bits, local_addr_t),
    new PreloadRs(mvout_rows_bits, mvout_cols_bits, local_addr_t),
    new ComputeRs(mvin_rows_bits, mvin_cols_bits, local_addr_t),
    new ComputeRs(mvin_rows_bits, mvin_cols_bits, local_addr_t),
    new MvoutRs2(mvout_rows_bits, mvout_cols_bits, local_addr_t),
  )
  reservation.io.alloc <> Queue(unrolled)

  for ((issue, command) <- Seq(
    reservation.io.issue.ld -> load.io.cmd,
    reservation.io.issue.st -> store.io.cmd,
  )) {
    command.valid := issue.valid
    issue.ready := command.ready
    command.bits := issue.cmd
    command.bits.rob_id.push(issue.rob_id)
  }

  private val executeIssue = reservation.io.issue.ex
  private val executeFunct = executeIssue.cmd.cmd.inst.funct
  private val preload = executeIssue.cmd.cmd.rs2.asTypeOf(
    new PreloadRs(mvout_rows_bits, mvout_cols_bits, local_addr_t),
  )
  private val outputPreload = executeFunct === PRELOAD_CMD &&
    preload.local_addr.is_acc_addr && !preload.local_addr.is_garbage()
  private val contextI = RegInit(0.U(16.W))
  private val contextJ = RegInit(0.U(16.W))
  private val contextK = RegInit(0.U(16.W))
  private val metadataQueue = Module(new Queue(new Hp1LoopMetadata, 2))
  metadataQueue.io.enq <> io.loopMetadata

  private val metadata = metadataQueue.io.deq.bits
  private val validRows = DIM.U - Mux(contextI === metadata.maxI - 1.U, metadata.padI, 0.U)
  private val validColumns = DIM.U - Mux(contextJ === metadata.maxJ - 1.U, metadata.padJ, 0.U)
  private val fragmentsPerBlock = math.max(1, 32 / DIM)
  // Scale-cache addressing is local to this loop. fragmentBase stays global for
  // fragment identity, but must not force the finite scale cache to cover all K.
  private val scaleBlock = contextK / fragmentsPerBlock.U
  private val scaleAddress = metadata.scaleBase + scaleBlock * metadata.maxJ + contextJ
  private val workId = metadata.workBase + contextI * metadata.maxJ + contextJ
  private val validFinalK = DIM.U - metadata.padK
  private val metadataShapeValid = metadataQueue.io.deq.valid &&
    metadata.maxI =/= 0.U && metadata.maxJ =/= 0.U && metadata.maxK =/= 0.U &&
    metadata.padI < DIM.U && metadata.padJ < DIM.U && metadata.padK < DIM.U &&
    validFinalK <= profile.fragmentLimit.U &&
    ((DIM <= 32).B || metadata.maxK === 1.U) &&
    scaleAddress < scaleEntries.U && workId < workEntries.U &&
    metadata.rawShapeValid(DIM)

  writeback.io.contextIssue.valid := executeIssue.valid && execute.io.cmd.ready &&
    outputPreload && metadataShapeValid
  writeback.io.contextIssue.bits.robId := executeIssue.rob_id
  writeback.io.contextIssue.bits.validRows := validRows
  writeback.io.contextIssue.bits.validColumns := validColumns
  writeback.io.contextIssue.bits.scaleAddress := scaleAddress(scaleAddressWidth - 1, 0)
  writeback.io.contextIssue.bits.scaleGeneration := metadata.scaleGeneration
  writeback.io.contextIssue.bits.workId := workId(workIdWidth - 1, 0)
  writeback.io.contextIssue.bits.fragmentId := metadata.fragmentBase + contextK
  writeback.io.contextIssue.bits.firstContribution := !metadata.accumulate && contextK === 0.U
  writeback.io.contextIssue.bits.finalFragment := metadata.finalFragment && contextK === metadata.maxK - 1.U
  writeback.io.contextIssue.bits.rmdRaw := metadata.rmdRaw
  private val outputReady = metadataShapeValid && writeback.io.contextIssue.ready
  execute.io.cmd.valid := executeIssue.valid && (!outputPreload || outputReady)
  executeIssue.ready := execute.io.cmd.ready && (!outputPreload || outputReady)
  execute.io.cmd.bits := executeIssue.cmd
  execute.io.cmd.bits.rob_id.push(executeIssue.rob_id)

  when(writeback.io.contextIssue.fire) {
    val lastI = contextI === metadata.maxI - 1.U
    val lastJ = contextJ === metadata.maxJ - 1.U
    val lastK = contextK === metadata.maxK - 1.U
    contextI := Mux(lastI, 0.U, contextI + 1.U)
    when(lastI) {
      contextJ := Mux(lastJ, 0.U, contextJ + 1.U)
      when(lastJ) {
        contextK := Mux(lastK, 0.U, contextK + 1.U)
      }
    }
    assert(preload.num_rows === validRows, "LoopMatmul output row shape differs from HP1 metadata")
    assert(preload.num_cols === validColumns, "LoopMatmul output column shape differs from HP1 metadata")
    assert(preload.local_addr.accumulate === !writeback.io.contextIssue.bits.firstContribution,
      "LoopMatmul accumulation bit differs from HP1 contribution order")
  }
  metadataQueue.io.deq.ready := writeback.io.contextIssue.fire &&
    contextI === metadata.maxI - 1.U && contextJ === metadata.maxJ - 1.U &&
    contextK === metadata.maxK - 1.U

  writeback.io.rawCompletion := execute.io.completed
  writeback.io.rawWrite <> execute.io.acc.write
  writeback.io.scaleLoad <> io.scaleLoad
  writeback.io.scaleRelease <> io.scaleRelease
  io.acc.write <> writeback.io.scaledWrite
  io.workDone <> writeback.io.workDone

  private val completion = Module(new Arbiter(UInt(ROB_ID_WIDTH.W), 3))
  completion.io.in(0) <> writeback.io.delayedCompletion
  completion.io.in(1) <> load.io.completed
  completion.io.in(2) <> store.io.completed
  completion.io.out.ready := true.B
  reservation.io.completed.valid := completion.io.out.valid
  reservation.io.completed.bits := completion.io.out.bits
  io.completed := reservation.io.completed

  io.dmaRead <> load.io.dma
  io.dmaWrite <> store.io.dma
  io.srams <> execute.io.srams
  io.acc.read_req <> execute.io.acc.read_req
  execute.io.acc.read_resp <> io.acc.read_resp
  execute.io.im2col.req.ready := false.B
  execute.io.im2col.resp.valid := false.B
  execute.io.im2col.resp.bits := 0.U.asTypeOf(execute.io.im2col.resp.bits)
  Seq(load.io.counter, store.io.counter, execute.io.counter, reservation.io.counter)
    .foreach(_.external_reset := false.B)
  io.busy := raw.valid || unrolled.valid || loopBusy || reservation.io.busy ||
    load.io.busy || store.io.busy || execute.io.busy || !writeback.io.pipelineDrained
  io.loopBusy := loopBusy
  io.writebackDrained := writeback.io.drained
  io.protocolError := !metadataShapeValid && executeIssue.valid && outputPreload ||
    writeback.io.errors.asUInt.orR
  io.loadBusy := load.io.busy
  io.storeBusy := store.io.busy
  io.executeBusy := execute.io.busy
  io.events.loadIssued := reservation.io.issue.ld.fire
  io.events.executeIssued := reservation.io.issue.ex.fire
  io.events.storeIssued := reservation.io.issue.st.fire
  io.events.outputContextIssued := writeback.io.events.contextIssued
  io.events.rawRow := writeback.io.events.rawRow
  io.events.scaledRow := writeback.io.events.scaledRow
  io.events.accumulatorCommitted := writeback.io.events.committedRow
  io.events.workCompleted := writeback.io.events.workCompleted
  io.events.loadExecuteOverlap := load.io.busy && execute.io.busy
  io.events.loadDmaAccepted := load.io.dma.req.fire
  io.events.loadVaddrBytes := load.io.dma.req.bits.vaddr
  io.events.loadLocalAddressRaw := load.io.dma.req.bits.laddr.asUInt
  io.events.loadColumnsElements := load.io.dma.req.bits.cols
  io.events.loadCommandId := load.io.dma.req.bits.cmd_id
  io.events.scratchpadReadAcceptedBankMask := VecInit(
    execute.io.srams.read.map(_.req.fire),
  ).asUInt
  io.events.scratchpadWriteEnabledBankMask := VecInit(
    execute.io.srams.write.map(_.en),
  ).asUInt
  io.events.storeDmaAccepted := store.io.dma.req.fire
  io.events.storeVaddrBytes := store.io.dma.req.bits.vaddr
  io.events.storeLocalAddressRaw := store.io.dma.req.bits.laddr.asUInt
  io.events.storeLengthElements := store.io.dma.req.bits.len
  io.events.storeCommandId := store.io.dma.req.bits.cmd_id
}
