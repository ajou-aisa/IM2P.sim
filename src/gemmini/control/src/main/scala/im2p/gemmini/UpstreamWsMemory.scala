package im2p.gemmini

import chisel3._
import chisel3.util._
import gemmini._
import gemmini.Arithmetic.SIntArithmetic
import org.chipsalliance.cde.config.Parameters

final class UpstreamWsMemory(
  profile: ResolvedProfile,
  val config: GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float],
)(implicit p: Parameters) extends Module {
  import config._
  require(DIM == profile.dim && inputType.getWidth == profile.operandBits && accType.getWidth == 32)
  require(!use_shared_ext_mem && !is_dummy)
  private val spWidth = DIM * inputType.getWidth
  private val accRow = Vec(meshColumns, Vec(tileColumns, accType))
  private val slots = 8
  val io = IO(new Bundle {
    val dmaRead = Flipped(new ScratchpadReadMemIO[gemmini.Float](local_addr_t, mvin_scale_t_bits))
    val dmaWrite = Flipped(new ScratchpadWriteMemIO(local_addr_t, 32, acc_scale_t_bits))
    val srams = new Bundle {
      val read = Flipped(Vec(sp_banks, new ScratchpadReadIO(sp_bank_entries, spWidth)))
      val write = Flipped(Vec(sp_banks, new ScratchpadWriteIO(sp_bank_entries, spWidth,
        (spWidth / (aligned_to * 8)) max 1)))
    }
    val acc = new Bundle {
      val read_req = Flipped(Vec(acc_banks, Decoupled(new AccumulatorReadReq(
        acc_bank_entries, accType, acc_scale_t))))
      val read_resp = Vec(acc_banks, Decoupled(new AccumulatorScaleResp(
        Vec(meshColumns, Vec(tileColumns, inputType)), accRow)))
      val write = Flipped(Vec(acc_banks, Decoupled(new AccumulatorWriteReq(acc_bank_entries, accRow))))
    }
    val readRequest = Decoupled(new BackingReadRequest(64, 4))
    val readBeat = Flipped(Decoupled(new BackingReadBeat(DIM * 32, 4)))
    val writeRequest = Decoupled(new BackingWriteBeat(DIM * 32, 64, 4))
    val writeCompletion = Flipped(Decoupled(new BackingWriteCompletion(4)))
    val loadRequests = Output(UInt(64.W))
    val loadResponses = Output(UInt(64.W))
    val storeRequests = Output(UInt(64.W))
    val storeResponses = Output(UInt(64.W))
    val readBytes = Output(UInt(64.W))
    val writeBytes = Output(UInt(64.W))
    val error = Output(Bool())
    val drained = Output(Bool())
  })
  private val backing = Module(new BackingMemoryPort(profile))
  io.readRequest <> backing.io.readRequest
  backing.io.readBeat <> io.readBeat
  io.writeRequest <> backing.io.writeRequest
  backing.io.writeCompletion <> io.writeCompletion
  private val failed = RegInit(false.B)
  io.error := failed || backing.io.error

  private val active = RegInit(VecInit(Seq.fill(slots)(false.B)))
  private val addresses = Reg(Vec(slots, local_addr_t.cloneType))
  private val columns = Reg(Vec(slots, UInt(16.W)))
  private val commandIds = Reg(Vec(slots, UInt(8.W)))
  private val freeId = PriorityEncoder(~active.asUInt)
  private val free = !active.asUInt.andR
  private val readCommands = Module(new Queue(new BackingReadRequest(64, 4), slots, flow = true))
  private val zeros = Module(new Queue(UInt(3.W), slots, flow = true))
  backing.io.readCommand <> readCommands.io.deq
  private val load = io.dmaRead.req
  private val loadSupported = !load.bits.laddr.is_acc_addr && !load.bits.has_acc_bitwidth &&
    load.bits.cols > 0.U && load.bits.cols <= DIM.U && load.bits.repeats === 0.U &&
    load.bits.pixel_repeats <= 1.U
  private val loadAllowed = free && !io.error && loadSupported
  load.ready := loadAllowed && Mux(load.bits.all_zeros, zeros.io.enq.ready, readCommands.io.enq.ready)
  readCommands.io.enq.valid := load.valid && loadAllowed && !load.bits.all_zeros
  readCommands.io.enq.bits.address := load.bits.vaddr
  readCommands.io.enq.bits.beats := 1.U
  readCommands.io.enq.bits.id := freeId
  zeros.io.enq.valid := load.valid && loadAllowed && load.bits.all_zeros
  zeros.io.enq.bits := freeId
  when(load.valid && !loadSupported) { failed := true.B }
  when(load.fire) {
    active(freeId) := true.B
    addresses(freeId) := load.bits.laddr
    columns(freeId) := load.bits.cols
    commandIds(freeId) := load.bits.cmd_id
  }

  private val result = backing.io.readResult
  // Zero writes use the same context table; wait a cycle for its allocation to become visible.
  private val useZero = zeros.io.deq.valid && active(zeros.io.deq.bits)
  private val resultId = Mux(useZero, zeros.io.deq.bits, result.bits.id(2, 0))
  private val resultValid = useZero || result.valid
  private val resultBad = !useZero && result.bits.error
  private val destination = addresses(resultId)
  private val resultBytes = (columns(resultId) * profile.operandBits.U + 7.U) >> 3
  private val banks = Seq.fill(sp_banks)(Module(new ScratchpadBank(
    sp_bank_entries, spWidth, aligned_to, sp_singleported, false, false)))
  private val targetBusy = VecInit(io.srams.write.map(_.en))(destination.sp_bank())
  private val commitLoad = resultValid && !resultBad && !targetBusy
  result.ready := !useZero && (resultBad || !targetBusy)
  zeros.io.deq.ready := useZero && !targetBusy
  when((result.fire && !useZero) || zeros.io.deq.fire) {
    active(resultId) := false.B
    when(resultBad) { failed := true.B }
  }
  io.dmaRead.resp.valid := RegNext(commitLoad, false.B)
  io.dmaRead.resp.bits.cmd_id := RegEnable(commandIds(resultId), commitLoad)
  io.dmaRead.resp.bits.bytesRead := RegEnable(resultBytes, commitLoad)
  for ((bank, index) <- banks.zipWithIndex) {
    bank.io.read.req <> io.srams.read(index).req
    io.srams.read(index).resp <> Pipeline(bank.io.read.resp, spad_read_delay)
    bank.io.write <> io.srams.write(index)
    when(commitLoad && destination.sp_bank() === index.U) {
      bank.io.write.en := true.B
      bank.io.write.addr := destination.sp_row()
      bank.io.write.data := Mux(useZero, 0.U, result.bits.data(spWidth - 1, 0))
      bank.io.write.mask.zipWithIndex.foreach { case (mask, byteGroup) =>
        mask := (byteGroup * aligned_to).U < resultBytes
      }
    }
  }

  // ponytail: one store context; add a context FIFO only when measured store throughput requires it.
  private val idle :: reading :: sending :: completing :: Nil = Enum(4)
  private val storeState = RegInit(idle)
  private val store = Reg(chiselTypeOf(io.dmaWrite.req.bits))
  private val storeData = Reg(UInt((DIM * 32).W))
  private val storeSupported = io.dmaWrite.req.bits.laddr.is_acc_addr &&
    io.dmaWrite.req.bits.laddr.read_full_acc_row && !io.dmaWrite.req.bits.pool_en &&
    io.dmaWrite.req.bits.store_en && io.dmaWrite.req.bits.len > 0.U &&
    io.dmaWrite.req.bits.len <= DIM.U
  io.dmaWrite.req.ready := storeState === idle && storeSupported && !io.error
  when(io.dmaWrite.req.valid && !storeSupported) { failed := true.B }
  when(io.dmaWrite.req.fire) {
    store := io.dmaWrite.req.bits
    storeState := reading
  }
  private val storeReadIssued = RegInit(false.B)
  private val accBanks = Seq.fill(acc_banks)(Module(new AccumulatorMem(
    acc_bank_entries, accRow, acc_scale_func, acc_scale_t.asInstanceOf[gemmini.Float],
    acc_singleported, acc_sub_banks, false, acc_latency, accType, false)))
  for ((bank, index) <- accBanks.zipWithIndex) {
    bank.io.write <> io.acc.write(index)
    for (column <- 0 until meshColumns; tile <- 0 until tileColumns) {
      val adder = Module(new SatAccumulatorAdder)
      adder.io.left := bank.io.adder.op1(column)(tile)
      adder.io.right := bank.io.adder.op2(column)(tile)
      bank.io.adder.sum(column)(tile) := ShiftRegister(adder.io.result, acc_latency - 1)
    }
    io.acc.read_req(index).ready := false.B
    io.acc.read_resp(index).valid := false.B
    io.acc.read_resp(index).bits := 0.U.asTypeOf(io.acc.read_resp(index).bits)
    when(io.acc.read_req(index).valid) { failed := true.B }
    bank.io.read.req.valid := storeState === reading && !storeReadIssued && store.laddr.acc_bank() === index.U &&
      !(io.acc.write(index).valid && io.acc.write(index).bits.addr === store.laddr.acc_row())
    bank.io.read.req.bits := 0.U.asTypeOf(bank.io.read.req.bits)
    bank.io.read.req.bits.addr := store.laddr.acc_row()
    bank.io.read.req.bits.full := true.B
    bank.io.read.req.bits.fromDMA := true.B
    when(bank.io.read.req.fire) { storeReadIssued := true.B }
    bank.io.read.resp.ready := storeState === reading && storeReadIssued && store.laddr.acc_bank() === index.U
    when(bank.io.read.resp.fire) {
      storeData := bank.io.read.resp.bits.data.asUInt
      storeReadIssued := false.B
      storeState := sending
    }
  }
  backing.io.writeCommand.valid := storeState === sending && !io.error
  backing.io.writeCommand.bits.address := store.vaddr
  backing.io.writeCommand.bits.id := 0.U
  backing.io.writeCommand.bits.data := storeData
  backing.io.writeCommand.bits.mask := VecInit((0 until DIM * 4).map(i => i.U < (store.len << 2))).asUInt
  backing.io.writeCommand.bits.first := true.B
  backing.io.writeCommand.bits.last := true.B
  when(backing.io.writeCommand.fire) { storeState := completing }
  backing.io.writeResult.ready := storeState === completing
  io.dmaWrite.resp.valid := backing.io.writeResult.fire && !backing.io.writeResult.bits.error
  io.dmaWrite.resp.bits.cmd_id := store.cmd_id
  when(backing.io.writeResult.fire) {
    storeState := idle
    when(backing.io.writeResult.bits.error) { failed := true.B }
  }
  private def count(event: Bool): UInt = {
    val value = RegInit(0.U(64.W))
    when(event) { value := value + 1.U }
    value
  }
  io.loadRequests := count(load.fire)
  io.loadResponses := count(io.dmaRead.resp.valid)
  io.storeRequests := count(io.dmaWrite.req.fire)
  io.storeResponses := count(io.dmaWrite.resp.valid)
  private val readBytes = RegInit(0.U(64.W))
  private val writeBytes = RegInit(0.U(64.W))
  when(commitLoad && !useZero) { readBytes := readBytes + resultBytes }
  when(io.dmaWrite.resp.valid) { writeBytes := writeBytes + (store.len << 2) }
  io.readBytes := readBytes
  io.writeBytes := writeBytes
  private val writesSettling = RegInit(0.U(log2Ceil(acc_latency + 5).W))
  when(io.acc.write.map(_.fire).reduce(_ || _)) {
    writesSettling := (acc_latency + (if (acc_singleported) 3 else 0)).U
  }.elsewhen(writesSettling =/= 0.U) { writesSettling := writesSettling - 1.U }
  io.drained := backing.io.drained && !active.asUInt.orR && storeState === idle &&
    !io.dmaRead.resp.valid && writesSettling === 0.U && !io.acc.write.map(_.valid).reduce(_ || _)
}
