package im2p.gemmini

import chisel3._
import chisel3.util._
import freechips.rocketchip.tile.RoCCCommand
import gemmini.{LocalAddr, LoopMatmulExecuteReq}
import gemmini.GemminiISA._
import gemmini.LocalAddr._
import org.chipsalliance.cde.config.Parameters

// Residual output context carried beside the execute stream. Control takes residual contexts
// from here, never from the loop metadata queue, whose head leaves at Main's last context.
final class PairResidualContext(dim: Int, accAddressWidth: Int) extends Bundle {
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
// loop's last Main command the residual µTs of the same (j-tile, block) follow. P3 operands are
// placeholders: W is Main's contiguous fragment and A an unloaded region until LdR (P4); the
// indexed W rows are only traced until the ExecuteController consumes them (P4).
final class PairScheduler(
  dim: Int,
  maxAddr: Int,
  maxAccAddr: Int,
  preloadRs1: PreloadRs,
  preloadRs2: PreloadRs,
  computeRs1: ComputeRs,
  computeRs2: ComputeRs,
)(implicit p: Parameters) extends Module {
  private val iteratorWidth = 16
  private val chunkRows = math.min(dim, 32)
  private val accAddressWidth = log2Up(maxAccAddr)

  val io = IO(new Bundle {
    val main = Flipped(Decoupled(new RoCCCommand))
    val out = Decoupled(new RoCCCommand)
    val req = Flipped(Valid(new LoopMatmulExecuteReq(dim, 40, iteratorWidth, maxAddr, maxAccAddr, 2)))
    val i = Input(UInt(iteratorWidth.W))
    val j = Input(UInt(iteratorWidth.W))
    val k = Input(UInt(iteratorWidth.W))
    val robOverloaded = Input(Bool())
    val hold = Output(Bool())
    val metadata = Flipped(Decoupled(new Hp1LoopMetadata))
    val residual = Decoupled(new PairResidualContext(dim, accAddressWidth))
    val trace = Valid(new PairTrace)
    val busy = Output(Bool())
    val protocolError = Output(Bool())
  })

  private val sIdle :: sMain :: sPreload :: sRows :: sCompute :: Nil = Enum(5)
  private val phase = RegInit(sIdle)
  private val req = Reg(chiselTypeOf(io.req.bits))
  private val meta = Reg(new Hp1LoopMetadata)
  private val mainLeft = RegInit(0.U(50.W))
  private val holding = RegInit(false.B)
  private val error = RegInit(false.B)
  private val group = RegInit(0.U(16.W))
  private val column = RegInit(0.U(iteratorWidth.W))
  private val chunk = RegInit(0.U(6.W))
  private val row = RegInit(0.U(6.W))
  private val chunkBase = RegInit(0.U(32.W))
  private val scan = RegInit(0.U(32.W))

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
  private val aAddress = req.a_addr_start + req.max_i * req.max_k * dim.U + (group * chunks + chunk) * dim.U
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

  // Pair off (and Main of a paired loop): a combinational pass-through, no added latency.
  private val injecting = phase === sPreload || phase === sRows || phase === sCompute
  private val canIssue = !io.robOverloaded
  io.residual.valid := phase === sPreload && canIssue && io.out.ready
  io.out.valid := Mux(injecting,
    (phase === sPreload && canIssue && io.residual.ready) || (phase === sCompute && canIssue),
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

  // The request and its metadata arrive together, once per LOOP_WS in loop order.
  io.metadata.ready := io.req.valid
  private val incoming = io.metadata.bits
  private val r = io.req.bits
  private val pairFits = incoming.residualMask =/= 0.U && incoming.residualGroups =/= 0.U &&
    incoming.residualPadI < dim.U && incoming.residualAccTop <= (maxAccAddr / 2).U &&
    (r.max_i * r.max_j +& incoming.residualGroups * r.max_j) * dim.U <= incoming.residualAccTop &&
    (r.max_i * r.max_k +& incoming.residualGroups * chunksOf(incoming.residualMask) +&
      r.max_k * r.max_j) * dim.U <= (maxAddr / 2).U
  when(io.req.valid) {
    req := r
    meta := incoming
    mainLeft := r.max_i * r.max_j * r.max_k * 2.U
    holding := incoming.paired && pairFits
    phase := sMain
    when(!io.metadata.valid || incoming.paired && !pairFits) { error := true.B }
  }
  when(phase === sMain && io.main.fire) {
    mainLeft := mainLeft - 1.U
    when(mainLeft === 1.U) {
      phase := Mux(holding, sPreload, sIdle)
      group := 0.U
      column := 0.U
      chunk := 0.U
      chunkBase := meta.residualMask
    }
  }
  when(preloadFire) {
    scan := chunkBase
    row := 0.U
    phase := sRows
  }
  private val bit = PriorityEncoder(scan)
  private val scanNext = scan & ~UIntToOH(bit, 32)
  when(phase === sRows) {
    scan := scanNext
    row := row + 1.U
    when(row === reduction - 1.U) {
      phase := sCompute
      when(lastGroup && lastColumn) { chunkBase := scanNext }
    }
  }
  when(computeFire) {
    when(lastOfLoop) {
      phase := sIdle
      holding := false.B
    }.otherwise {
      phase := sPreload
      group := Mux(lastGroup, 0.U, group + 1.U)
      when(lastGroup) {
        column := Mux(lastColumn, 0.U, column + 1.U)
        when(lastColumn) { chunk := chunk + 1.U }
      }
    }
  }

  private val mainPreload = phase === sMain && io.main.fire && io.main.bits.inst.funct === PRELOAD_CMD
  private val mainOutput = io.i * req.max_j + io.j
  io.trace.valid := mainPreload || preloadFire || phase === sRows
  io.trace.bits.kind := Mux(mainPreload, 0.U, Mux(phase === sRows, 2.U, 1.U))
  io.trace.bits.fragmentId := Mux(mainPreload, meta.fragmentBase + io.k, fragmentId)
  io.trace.bits.accRow := Mux(mainPreload, mainOutput * dim.U, accRow)
  io.trace.bits.workId := Mux(mainPreload, meta.workBase +& mainOutput, workId)
  io.trace.bits.wRow := Mux(mainPreload, (io.k * req.max_j + io.j) * dim.U,
    Mux(phase === sRows,
      (bit >> log2Ceil(dim)) * req.max_j * dim.U + column * dim.U + (bit & (dim - 1).U), 0.U))

  io.hold := holding
  io.busy := phase =/= sIdle || holding
  io.protocolError := error
  assert(!(io.req.valid && phase =/= sIdle && phase =/= sMain), "execute request during residual issue")
  assert(!(io.req.valid && phase === sMain && mainLeft =/= 0.U), "execute request before Main finished")
}
