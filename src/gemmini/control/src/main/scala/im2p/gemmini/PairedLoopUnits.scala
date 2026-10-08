package im2p.gemmini

import chisel3._
import chisel3.util._
import freechips.rocketchip.tile.RoCCCommand
import gemmini.{LocalAddr, LoopMatmulExecuteReq}
import gemmini.GemminiISA._
import gemmini.LocalAddr._
import org.chipsalliance.cde.config.Parameters

// Where LdR reads one paired loop's residual activations: the packed residual A of that loop
// (residual_activation_read layout), rows strideBytes apart. A host field, never loop metadata.
final class PairLoadSource extends Bundle {
  val address = UInt(64.W)
  val strideBytes = UInt(64.W)
}

// The PairScheduler's copy of one loop's metadata and its residual A source, enqueued together.
final class PairLoopRequest extends Bundle {
  val meta = new Hp1LoopMetadata
  val source = new PairLoadSource
}

// Residual output context carried beside the execute stream. Control takes residual contexts
// from here, never from the loop metadata queue, whose head leaves at Main's last context.
// Group-0 entries also carry the indexed W rows the ExecuteController reads for the PRELOAD.
final class PairResidualContext(dim: Int, accAddressWidth: Int, spAddressWidth: Int) extends Bundle {
  val validRows = UInt(log2Ceil(dim + 1).W)
  val validColumns = UInt(log2Ceil(dim + 1).W)
  val scaleAddress = UInt(32.W)
  val scaleGeneration = UInt(8.W)
  val workId = UInt(32.W)
  val fragmentId = UInt(16.W)
  val firstContribution = Bool()
  val finalFragment = Bool()
  val accAddress = UInt(accAddressWidth.W)
  val lastOfLoop = Bool()
  val wValid = Bool()
  val wRows = Vec(math.min(dim, 32), UInt(spAddressWidth.W))
  val wBase = UInt(spAddressWidth.W)
}

// Observation only. One beat per issued µT (kind 0 Main, 1 residual) and one beat per residual
// reduction row (kind 2) with its indexed W row. ACC and W rows are relative to the loop's bases.
final class PairTrace extends Bundle {
  val kind = UInt(2.W)
  val fragmentId = UInt(16.W)
  val accRow = UInt(16.W)
  val workId = UInt(16.W)
  val wRow = UInt(16.W)
}

// Loop-tail pairing (spec C3): Main execute commands pass straight through, and after a paired
// loop's last Main command the residual µTs of the same (j-tile, block) follow. Each residual µT
// scans its indexed W rows first and hands them to the ExecuteController with its PRELOAD.
// LdR moves the residual activations of every (chunk, group) into the residual SP region,
// starting at the execute request beside Main, and a µT's first PRELOAD waits for its LdR (C2).
final class PairScheduler(
  dim: Int,
  maxAddr: Int,
  maxAccAddr: Int,
  operandBits: Int,
  preloadRs1: PreloadRs,
  preloadRs2: PreloadRs,
  computeRs1: ComputeRs,
  computeRs2: ComputeRs,
  mvinRs2: MvinRs2,
  ldrAfterFirstUse: Boolean = false, // test only: breaks C2 so its assertion can be shown to fire
)(implicit p: Parameters) extends Module {
  private val iteratorWidth = 16
  private val chunkRows = math.min(dim, 32)
  private val accAddressWidth = log2Up(maxAccAddr)
  private val spAddressWidth = log2Up(maxAddr)
  private val rowBytes = dim * operandBits / 8

  val io = IO(new Bundle {
    val main = Flipped(Decoupled(new RoCCCommand))
    val out = Decoupled(new RoCCCommand)
    val req = Flipped(Valid(new LoopMatmulExecuteReq(dim, 40, iteratorWidth, maxAddr, maxAccAddr, 2)))
    val i = Input(UInt(iteratorWidth.W))
    val j = Input(UInt(iteratorWidth.W))
    val k = Input(UInt(iteratorWidth.W))
    val robOverloaded = Input(Bool())
    val hold = Output(Bool())
    val metadata = Flipped(Decoupled(new PairLoopRequest))
    val residual = Decoupled(new PairResidualContext(dim, accAddressWidth, spAddressWidth))
    val ldr = Decoupled(new RoCCCommand)
    val ldrRobOverloaded = Input(Bool())
    val trace = Valid(new PairTrace)
    val busy = Output(Bool())
    val protocolError = Output(Bool())
  })

  private val sIdle :: sMain :: sHead :: sRows :: sPreload :: sCompute :: Nil = Enum(6)
  private val phase = RegInit(sIdle)
  private val req = Reg(chiselTypeOf(io.req.bits))
  private val meta = Reg(new Hp1LoopMetadata)
  private val source = Reg(new PairLoadSource)
  private val mainLeft = RegInit(0.U(50.W))
  private val holding = RegInit(false.B)
  private val error = RegInit(false.B)
  private val group = RegInit(0.U(16.W))
  private val column = RegInit(0.U(iteratorWidth.W))
  private val chunk = RegInit(0.U(6.W))
  private val row = RegInit(0.U(6.W))
  private val chunkBase = RegInit(0.U(32.W))
  private val scan = RegInit(0.U(32.W))
  private val wRows = Reg(Vec(chunkRows, UInt(spAddressWidth.W)))
  // LdR in (chunk, group) order, the order of the first µT that reads each region.
  private val ldrChunk = RegInit(0.U(6.W))
  private val ldrGroup = RegInit(0.U(16.W))
  private val ldrIssued = RegInit(0.U(24.W))
  private val ldrDone = RegInit(true.B)
  private val residualStarted = RegInit(false.B)

  private def chunksOf(mask: UInt): UInt = (PopCount(mask) +& (chunkRows - 1).U) >> log2Ceil(chunkRows)

  // Per-µT facts in plan_fragment order: I fastest, then J, then chunk.
  private val count = PopCount(meta.residualMask)
  private val chunks = chunksOf(meta.residualMask)
  private val lastGroup = group === meta.residualGroups - 1.U
  private val lastColumn = column === req.max_j - 1.U
  private val lastChunk = chunk === chunks - 1.U
  private val lastOfLoop = lastGroup && lastColumn && lastChunk
  private val reduction = Mux(lastChunk, count - chunk * chunkRows.U, chunkRows.U)
  private val rows = dim.U - Mux(lastGroup, meta.residualPadI, 0.U)
  private val columns = dim.U - Mux(lastColumn, req.pad_j, 0.U)
  private val output = group * req.max_j + column
  private val accRow = meta.residualAccTop - (output +& 1.U) * dim.U
  private val bStart = req.b_addr_end - req.max_k * req.max_j * dim.U
  private val wFragment = Mux(chunk < req.max_k, chunk, req.max_k - 1.U)
  private val wAddress = bStart + (wFragment * req.max_j + column) * dim.U
  private def residualA(g: UInt, c: UInt): UInt =
    req.a_addr_start + req.max_i * req.max_k * dim.U + (g * chunks + c) * dim.U
  private val aAddress = residualA(group, chunk)
  private val workId = meta.workBase +& meta.residualWorkOffset +& output
  private val fragmentId = meta.fragmentBase + chunk

  private val preRs1 = Wire(preloadRs1.cloneType)
  preRs1 := DontCare
  preRs1.num_rows := reduction
  preRs1.num_cols := columns
  preRs1.local_addr := Mux(group === 0.U, cast_to_sp_addr(preRs1.local_addr, wAddress),
    garbage_addr(preRs1.local_addr))
  private val preRs2 = Wire(preloadRs2.cloneType)
  preRs2 := DontCare
  preRs2.num_rows := rows
  preRs2.num_cols := columns
  preRs2.local_addr := cast_to_acc_addr(preRs2.local_addr, req.c_addr_start + accRow,
    accumulate = !(meta.residualFirstRun && chunk === 0.U), read_full = false.B)
  private val preCmd = Wire(new RoCCCommand)
  preCmd := DontCare
  preCmd.inst.funct := PRELOAD_CMD
  preCmd.rs1 := preRs1.asUInt
  preCmd.rs2 := preRs2.asUInt

  private val compRs1 = Wire(computeRs1.cloneType)
  compRs1 := DontCare
  compRs1.num_rows := rows
  compRs1.num_cols := reduction
  compRs1.local_addr := cast_to_sp_addr(compRs1.local_addr, aAddress)
  private val compRs2 = Wire(computeRs2.cloneType)
  compRs2 := DontCare
  compRs2.num_rows := dim.U
  compRs2.num_cols := dim.U
  compRs2.local_addr := garbage_addr(compRs2.local_addr)
  private val compCmd = Wire(new RoCCCommand)
  compCmd := DontCare
  compCmd.inst.funct := Mux(group === 0.U, COMPUTE_AND_FLIP_CMD, COMPUTE_AND_STAY_CMD)
  compCmd.rs1 := compRs1.asUInt
  compCmd.rs2 := compRs2.asUInt

  // LdR(c, g): rows g·D… of the packed residual A, columns c·D…, into the region the residual
  // COMPUTE of (g, c) reads. Load state 2 carries the source's row stride (bridge command 2).
  private val ldrRs2 = Wire(mvinRs2.cloneType)
  ldrRs2 := DontCare
  ldrRs2.num_rows := dim.U
  ldrRs2.num_cols := dim.U
  ldrRs2.local_addr := cast_to_sp_addr(ldrRs2.local_addr, residualA(ldrGroup, ldrChunk))
  private val ldrCmd = Wire(new RoCCCommand)
  ldrCmd := DontCare
  ldrCmd.inst.funct := LOAD3_CMD
  ldrCmd.rs1 := (source.address + ldrGroup * dim.U * source.strideBytes + ldrChunk * rowBytes.U)(63, 0)
  ldrCmd.rs2 := ldrRs2.asUInt
  io.ldr.valid := !ldrDone && holding && !io.ldrRobOverloaded && (!ldrAfterFirstUse.B || residualStarted)
  io.ldr.bits := ldrCmd
  when(io.ldr.fire) {
    ldrIssued := ldrIssued + 1.U
    val lastLdrGroup = ldrGroup === meta.residualGroups - 1.U
    ldrGroup := Mux(lastLdrGroup, 0.U, ldrGroup + 1.U)
    when(lastLdrGroup) {
      ldrChunk := ldrChunk + 1.U
      when(ldrChunk === chunks - 1.U) { ldrDone := true.B }
    }
  }

  // C2 (ldr_ahead): a µT's first PRELOAD for (chunk, group) waits until LdR(chunk, group) fired.
  // Both go through LoopMatmul's arbiter and one queue into the RS, so this is allocation order.
  private val ldrIndex = chunk * meta.residualGroups + group
  private val ldrAhead = column =/= 0.U || ldrIssued > ldrIndex
  private val preloadAllowed = ldrAhead || ldrAfterFirstUse.B

  // Pair off (and Main of a paired loop): a combinational pass-through, no added latency.
  private val injecting = phase === sHead || phase === sRows || phase === sPreload || phase === sCompute
  private val canIssue = !io.robOverloaded
  io.residual.valid := phase === sPreload && canIssue && preloadAllowed && io.out.ready
  io.out.valid := Mux(injecting,
    (phase === sPreload && canIssue && preloadAllowed && io.residual.ready) || (phase === sCompute && canIssue),
    io.main.valid)
  io.out.bits := Mux(injecting, Mux(phase === sPreload, preCmd, compCmd), io.main.bits)
  io.main.ready := !injecting && io.out.ready
  private val preloadFire = phase === sPreload && io.out.fire
  private val computeFire = phase === sCompute && io.out.fire

  io.residual.bits.validRows := rows
  io.residual.bits.validColumns := columns
  io.residual.bits.scaleAddress := meta.scaleBase +& column
  io.residual.bits.scaleGeneration := meta.scaleGeneration
  io.residual.bits.workId := workId
  io.residual.bits.fragmentId := fragmentId
  io.residual.bits.firstContribution := meta.residualFirstRun && chunk === 0.U
  io.residual.bits.finalFragment := meta.residualFinalRun && lastChunk
  io.residual.bits.accAddress := req.c_addr_start + accRow
  io.residual.bits.lastOfLoop := lastOfLoop
  io.residual.bits.wValid := group === 0.U
  io.residual.bits.wRows := wRows
  io.residual.bits.wBase := bStart

  // The request and its metadata arrive together, once per LOOP_WS in loop order.
  io.metadata.ready := io.req.valid
  private val incoming = io.metadata.bits.meta
  private val incomingSource = io.metadata.bits.source
  private val r = io.req.bits
  private val incomingChunks = chunksOf(incoming.residualMask)
  private val pairFits = incoming.residualMask =/= 0.U && incoming.residualGroups =/= 0.U &&
    incoming.residualPadI < dim.U && incoming.residualAccTop <= (maxAccAddr / 2).U &&
    (r.max_i * r.max_j +& incoming.residualGroups * r.max_j) * dim.U <= incoming.residualAccTop &&
    (r.max_i * r.max_k +& incoming.residualGroups * incomingChunks +&
      r.max_k * r.max_j) * dim.U <= (maxAddr / 2).U &&
    incomingSource.address =/= 0.U && incomingSource.strideBytes === incomingChunks * rowBytes.U
  when(io.req.valid) {
    req := r
    meta := incoming
    source := incomingSource
    mainLeft := r.max_i * r.max_j * r.max_k * 2.U
    holding := incoming.paired && pairFits
    phase := sMain
    ldrChunk := 0.U
    ldrGroup := 0.U
    ldrIssued := 0.U
    ldrDone := !(incoming.paired && pairFits)
    residualStarted := false.B
    when(!io.metadata.valid || incoming.paired && !pairFits) { error := true.B }
  }
  when(phase === sMain && io.main.fire) {
    mainLeft := mainLeft - 1.U
    when(mainLeft === 1.U) {
      phase := Mux(holding, sHead, sIdle)
      group := 0.U
      column := 0.U
      chunk := 0.U
      chunkBase := meta.residualMask
    }
  }
  when(phase === sHead) {
    scan := chunkBase
    row := 0.U
    phase := sRows
  }
  private val bit = PriorityEncoder(scan)
  private val scanNext = scan & ~UIntToOH(bit, 32)
  private val relativeRow = (bit >> log2Ceil(dim)) * req.max_j * dim.U + column * dim.U + (bit & (dim - 1).U)
  private val absoluteRow = bStart + relativeRow
  when(phase === sRows) {
    wRows(row) := absoluteRow
    scan := scanNext
    row := row + 1.U
    when(row === reduction - 1.U) {
      phase := sPreload
      when(lastGroup && lastColumn) { chunkBase := scanNext }
    }
  }
  when(preloadFire) {
    phase := sCompute
    residualStarted := true.B
  }
  when(computeFire) {
    when(lastOfLoop) {
      phase := sIdle
      holding := false.B
    }.otherwise {
      phase := sHead
      group := Mux(lastGroup, 0.U, group + 1.U)
      when(lastGroup) {
        column := Mux(lastColumn, 0.U, column + 1.U)
        when(lastColumn) { chunk := chunk + 1.U }
      }
    }
  }

  private val mainPreload = phase === sMain && io.main.fire && io.main.bits.inst.funct === PRELOAD_CMD
  private val mainOutput = io.i * req.max_j + io.j
  io.trace.valid := mainPreload || phase === sHead || phase === sRows
  io.trace.bits.kind := Mux(mainPreload, 0.U, Mux(phase === sRows, 2.U, 1.U))
  io.trace.bits.fragmentId := Mux(mainPreload, meta.fragmentBase + io.k, fragmentId)
  io.trace.bits.accRow := Mux(mainPreload, mainOutput * dim.U, accRow)
  io.trace.bits.workId := Mux(mainPreload, meta.workBase +& mainOutput, workId)
  io.trace.bits.wRow := Mux(mainPreload, (io.k * req.max_j + io.j) * dim.U,
    Mux(phase === sRows, relativeRow, 0.U))

  io.hold := holding
  io.busy := phase =/= sIdle || holding || !ldrDone
  io.protocolError := error
  assert(!(io.req.valid && phase =/= sIdle && phase =/= sMain), "execute request during residual issue")
  assert(!(io.req.valid && phase === sMain && mainLeft =/= 0.U), "execute request before Main finished")
  // Gathered rows are B rows of this loop with bit < ks (amendment 4).
  assert(!(phase === sRows && (absoluteRow < bStart || absoluteRow >= req.b_addr_end ||
    bit >= req.max_k * dim.U - req.pad_k)), "gathered W row outside the loop's B region or K")
  assert(!(preloadFire && !ldrAhead), "ldr_ahead: residual PRELOAD before its LdR")
  assert(!(io.ldr.valid && !holding), "LdR offered outside hold")
  assert(!(computeFire && lastOfLoop && !(ldrDone && ldrIssued === chunks * meta.residualGroups)),
    "paired loop ended without every LdR")
}
