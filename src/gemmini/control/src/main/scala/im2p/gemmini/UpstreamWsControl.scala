package im2p.gemmini

import chisel3._
import chisel3.util._
import gemmini._
import gemmini.Arithmetic.SIntArithmetic
import gemmini.GemminiISA.{ComputeRs, MvinRs2, MvoutRs2, PreloadRs}
import org.chipsalliance.cde.config.Parameters

final class UpstreamInstruction extends Bundle {
  val funct = UInt(7.W)
  val rs1 = UInt(64.W)
  val rs2 = UInt(64.W)
}

final class UpstreamWsControl(val config: GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float])
  (implicit p: Parameters) extends Module {
  import config._

  require(dataflow == Dataflow.WS)
  require(!hasIm2Col)
  private val execute = Module(new ExecuteController(64, 32, config))
  private val load = Module(new LoadController(config, 40, local_addr_t))
  private val store = Module(new StoreController(config, 40, local_addr_t))
  private val reservation = Module(new ReservationStation(config, new GemminiCmd(reservation_station_entries)))

  val io = IO(new Bundle {
    val instruction = Flipped(Decoupled(new UpstreamInstruction))
    val dmaRead = chiselTypeOf(load.io.dma)
    val dmaWrite = chiselTypeOf(store.io.dma)
    val srams = chiselTypeOf(execute.io.srams)
    val acc = chiselTypeOf(execute.io.acc)
    val completed = Valid(UInt(ROB_ID_WIDTH.W))
    val busy = Output(Bool())
    val loadBusy = Output(Bool())
    val executeBusy = Output(Bool())
    val storeBusy = Output(Bool())
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
    reservation.io.issue.ex -> execute.io.cmd,
  )) {
    command.valid := issue.valid
    issue.ready := command.ready
    command.bits := issue.cmd
    command.bits.rob_id.push(issue.rob_id)
  }

  private val completion = Module(new Arbiter(UInt(ROB_ID_WIDTH.W), 3))
  completion.io.in(0).valid := execute.io.completed.valid
  completion.io.in(0).bits := execute.io.completed.bits
  completion.io.in(1) <> load.io.completed
  completion.io.in(2) <> store.io.completed
  completion.io.out.ready := true.B
  reservation.io.completed.valid := completion.io.out.valid
  reservation.io.completed.bits := completion.io.out.bits
  io.completed := reservation.io.completed

  io.dmaRead <> load.io.dma
  io.dmaWrite <> store.io.dma
  io.srams <> execute.io.srams
  io.acc <> execute.io.acc
  execute.io.im2col.req.ready := false.B
  execute.io.im2col.resp.valid := false.B
  execute.io.im2col.resp.bits := 0.U.asTypeOf(execute.io.im2col.resp.bits)
  Seq(load.io.counter, store.io.counter, execute.io.counter, reservation.io.counter)
    .foreach(_.external_reset := false.B)
  io.busy := raw.valid || unrolled.valid || loopBusy || reservation.io.busy ||
    load.io.busy || store.io.busy || execute.io.busy
  io.loadBusy := load.io.busy
  io.storeBusy := store.io.busy
  io.executeBusy := execute.io.busy
}
