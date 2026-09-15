package im2p.gemmini

import chisel3._
import chisel3.util._
import gemmini.ScratchpadBank

final class StandaloneLocalMemory(profile: ResolvedProfile, bankRows: Int) extends Module {
  require(bankRows >= profile.dim, "scratchpad bank must hold at least one DIM row group")
  require(isPow2(bankRows), "scratchpad bank rows must be a power of two")

  private val rowBits = profile.dim * profile.operandBits
  private val readType = new LocalMemoryRead(bankRows)

  val io = IO(new Bundle {
    val load = Flipped(Decoupled(new LocalMemoryLoad(profile, bankRows)))
    val activationRead = Flipped(Decoupled(readType))
    val activationData = Decoupled(UInt(rowBits.W))
    val weightRead = Flipped(Decoupled(readType))
    val weightData = Decoupled(UInt(rowBits.W))
  })

  private val activationBanks = Seq.fill(2)(Module(new ScratchpadBank(
    n = bankRows,
    w = rowBits,
    aligned_to = profile.scratchpadRowBytes,
    single_ported = true,
    use_shared_ext_mem = false,
    is_dummy = false,
  )))
  private val weightBanks = Seq.fill(2)(Module(new ScratchpadBank(
    n = bankRows,
    w = rowBits,
    aligned_to = profile.scratchpadRowBytes,
    single_ported = true,
    use_shared_ext_mem = false,
    is_dummy = false,
  )))

  private val allBanks = activationBanks ++ weightBanks
  allBanks.foreach { bank =>
    bank.io.write.en := false.B
    bank.io.write.addr := 0.U
    bank.io.write.data := 0.U
    bank.io.write.mask.foreach(_ := true.B)
  }

  private val selectedLoadBank = Mux(io.load.bits.weights, 2.U, 0.U) + io.load.bits.slot
  private val selectedReadConflict = Mux(
    io.load.bits.weights,
    io.weightRead.valid && io.weightRead.bits.slot === io.load.bits.slot,
    io.activationRead.valid && io.activationRead.bits.slot === io.load.bits.slot,
  )
  io.load.ready := !selectedReadConflict

  for ((bank, index) <- allBanks.zipWithIndex) {
    when(io.load.fire && selectedLoadBank === index.U) {
      bank.io.write.en := true.B
      bank.io.write.addr := io.load.bits.row
      bank.io.write.data := io.load.bits.data
    }
  }

  private def connectRead(
    request: DecoupledIO[LocalMemoryRead],
    response: DecoupledIO[UInt],
    banks: Seq[ScratchpadBank],
  ): Unit = {
    val arbiter = Module(new Arbiter(UInt(rowBits.W), banks.size))
    banks.zipWithIndex.foreach { case (bank, index) =>
      bank.io.read.req.valid := request.valid && request.bits.slot === index.U
      bank.io.read.req.bits.addr := request.bits.row
      bank.io.read.req.bits.fromDMA := false.B
      arbiter.io.in(index).valid := bank.io.read.resp.valid
      arbiter.io.in(index).bits := bank.io.read.resp.bits.data
      bank.io.read.resp.ready := arbiter.io.in(index).ready
    }
    request.ready := Mux(request.bits.slot, banks(1).io.read.req.ready, banks(0).io.read.req.ready)
    response <> arbiter.io.out
  }

  connectRead(io.activationRead, io.activationData, activationBanks)
  connectRead(io.weightRead, io.weightData, weightBanks)
}
