package im2p.gemmini

import chisel3._
import chisel3.util._
import gemmini.GemminiISA

final class Hp1LoopDescriptor extends Bundle {
  val maxI = UInt(16.W)
  val maxJ = UInt(16.W)
  val maxK = UInt(16.W)
  val padI = UInt(16.W)
  val padJ = UInt(16.W)
  val padK = UInt(16.W)
  val aAddress = UInt(64.W)
  val bAddress = UInt(64.W)
  val cAddress = UInt(64.W)
  val scaleBackingAddress = UInt(64.W)
  val aStrideBytes = UInt(64.W)
  val bStrideBytes = UInt(64.W)
  val cStrideBytes = UInt(64.W)
  val scaleBase = UInt(16.W)
  val scaleGeneration = UInt(8.W)
  val fragmentBase = UInt(16.W)
  val workBase = UInt(8.W)
  val accumulate = Bool()
  val finalFragment = Bool()
  val firstLoop = Bool()
  val finalLoop = Bool()
  val logicalWorkId = UInt(8.W)
  val hostSlot = Bool()
  val rmdRaw = Bool()

  // One compact block per raw result; the CPU owns radix and block-scale composition.
  def rawShapeValid(dim: Int): Bool = {
    val compactK = maxK * dim.U - padK
    !rmdRaw || (maxK =/= 0.U && padK < dim.U && compactK > 0.U &&
      compactK <= 32.U && fragmentBase === 0.U && !accumulate && finalFragment)
  }
}

final class HostCommandBridge(profile: ResolvedProfile) extends Module {
  val io = IO(new Bundle {
    val work = Flipped(Decoupled(new Hp1LoopDescriptor))
    val instruction = Decoupled(new UpstreamInstruction)
    val loopMetadata = Decoupled(new Hp1LoopDescriptor)
    val controllerBusy = Input(Bool())
    val start = Output(Bool())
    val active = Output(Valid(new Hp1LoopDescriptor))
    val done = Decoupled(UInt(8.W))
    val completedHostSlot = Output(Bool())
    val logicalDone = Output(Valid(UInt(8.W)))
    val overlapIssued = Output(Bool())
  })

  private val select :: emit :: complete :: Nil = Enum(3)
  private val state = RegInit(select)
  private val commandIndex = RegInit(0.U(4.W))
  private val sawBusy = RegInit(false.B)

  private val pending = Module(new Queue(new Hp1LoopDescriptor, 2, flow = true))
  private val issued = Module(new Queue(new Hp1LoopDescriptor, 2))
  private val totalCount = pending.io.count +& issued.io.count

  private val validShape = io.work.bits.maxI =/= 0.U &&
    io.work.bits.maxJ =/= 0.U && io.work.bits.maxK =/= 0.U &&
    io.work.bits.rawShapeValid(profile.dim)
  private val alignedStrides = (io.work.bits.aStrideBytes * 8.U) % profile.operandBits.U === 0.U &&
    (io.work.bits.bStrideBytes * 8.U) % profile.operandBits.U === 0.U &&
    io.work.bits.cStrideBytes % 4.U === 0.U
  pending.io.enq.valid := io.work.valid && totalCount < 2.U && validShape && alignedStrides
  pending.io.enq.bits := io.work.bits
  io.work.ready := pending.io.enq.ready && totalCount < 2.U && validShape && alignedStrides
  io.start := io.work.fire && io.work.bits.firstLoop
  io.active.valid := issued.io.deq.valid || pending.io.deq.valid
  io.active.bits := Mux(issued.io.deq.valid, issued.io.deq.bits, pending.io.deq.bits)
  io.done.valid := state === complete && issued.io.deq.valid
  io.done.bits := issued.io.deq.bits.logicalWorkId
  io.completedHostSlot := issued.io.deq.bits.hostSlot
  io.logicalDone.valid := io.done.valid && issued.io.deq.bits.finalLoop
  io.logicalDone.bits := issued.io.deq.bits.logicalWorkId
  io.overlapIssued := issued.io.enq.fire && issued.io.count =/= 0.U && io.controllerBusy
  issued.io.deq.ready := io.done.fire

  private def loadConfig(stateId: Int): UInt = {
    val config = Wire(new GemminiISA.ConfigMvinRs1(1, 16, 8))
    config := 0.U.asTypeOf(config)
    config.stride := profile.dim.U
    config.pixel_repeats := 1.U
    config.state_id := stateId.U
    config._unused := GemminiISA.CONFIG_LOAD
    config.asUInt
  }
  private val storeConfigRs1 = Wire(new GemminiISA.ConfigMvoutRs1)
  private val storeConfigRs2 = Wire(new GemminiISA.ConfigMvoutRs2(32, 32))
  storeConfigRs1 := 0.U.asTypeOf(storeConfigRs1)
  storeConfigRs1.cmd_type := GemminiISA.CONFIG_STORE
  storeConfigRs2 := 0.U.asTypeOf(storeConfigRs2)
  storeConfigRs2.stride := pending.io.deq.bits.cStrideBytes(31, 0)
  private val executeConfigRs1 = Wire(new GemminiISA.ConfigExRs1(32))
  private val executeConfigRs2 = Wire(new GemminiISA.ConfigExRs2)
  executeConfigRs1 := 0.U.asTypeOf(executeConfigRs1)
  executeConfigRs1.a_stride := 1.U
  executeConfigRs1.dataflow := gemmini.Dataflow.WS.id.U
  executeConfigRs1.cmd_type := GemminiISA.CONFIG_EX
  executeConfigRs2 := 0.U.asTypeOf(executeConfigRs2)
  executeConfigRs2.c_stride := 1.U

  private def command(funct: UInt, rs1: UInt = 0.U, rs2: UInt = 0.U): UpstreamInstruction = {
    val result = Wire(new UpstreamInstruction)
    result.funct := funct
    result.rs1 := rs1
    result.rs2 := rs2
    result
  }
  private val commands = VecInit(Seq(
    command(GemminiISA.CONFIG_CMD, loadConfig(0), pending.io.deq.bits.aStrideBytes),
    command(GemminiISA.CONFIG_CMD, loadConfig(1), pending.io.deq.bits.bStrideBytes),
    command(GemminiISA.CONFIG_CMD, loadConfig(2)),
    command(GemminiISA.CONFIG_CMD, storeConfigRs1.asUInt, storeConfigRs2.asUInt),
    command(GemminiISA.CONFIG_CMD, executeConfigRs1.asUInt, executeConfigRs2.asUInt),
    command(GemminiISA.LOOP_WS_CONFIG_BOUNDS,
      Cat(pending.io.deq.bits.padK, pending.io.deq.bits.padJ, pending.io.deq.bits.padI),
      Cat(pending.io.deq.bits.maxK, pending.io.deq.bits.maxJ, pending.io.deq.bits.maxI)),
    command(GemminiISA.LOOP_WS_CONFIG_ADDRS_AB,
      pending.io.deq.bits.aAddress, pending.io.deq.bits.bAddress),
    command(GemminiISA.LOOP_WS_CONFIG_ADDRS_DC, 0.U, pending.io.deq.bits.cAddress),
    command(GemminiISA.LOOP_WS_CONFIG_STRIDES_AB,
      pending.io.deq.bits.aStrideBytes * 8.U / profile.operandBits.U,
      pending.io.deq.bits.bStrideBytes * 8.U / profile.operandBits.U),
    command(GemminiISA.LOOP_WS_CONFIG_STRIDES_DC, 0.U,
      pending.io.deq.bits.cStrideBytes / 4.U),
    command(GemminiISA.LOOP_WS, Cat(true.B, pending.io.deq.bits.accumulate)),
  ))

  private val loopCommand = state === emit && commandIndex === 10.U
  io.instruction.valid := state === emit && (!loopCommand ||
    (io.loopMetadata.ready && issued.io.enq.ready))
  io.instruction.bits := commands(commandIndex)
  io.loopMetadata.valid := loopCommand && io.instruction.ready && issued.io.enq.ready
  io.loopMetadata.bits := pending.io.deq.bits
  issued.io.enq.valid := loopCommand && io.instruction.ready && io.loopMetadata.ready
  issued.io.enq.bits := pending.io.deq.bits
  pending.io.deq.ready := issued.io.enq.fire

  private val configured = Reg(new Hp1LoopDescriptor)
  private val compatible = pending.io.deq.bits.aStrideBytes === configured.aStrideBytes &&
    pending.io.deq.bits.bStrideBytes === configured.bStrideBytes &&
    pending.io.deq.bits.cStrideBytes === configured.cStrideBytes

  when(io.controllerBusy && issued.io.deq.valid) {
    sawBusy := true.B
  }

  when(state === select) {
    when(pending.io.deq.valid && issued.io.count === 0.U) {
      configured := pending.io.deq.bits
      commandIndex := 0.U
      state := emit
    }.elsewhen(pending.io.deq.valid && compatible && io.controllerBusy) {
      commandIndex := 5.U
      state := emit
    }.elsewhen(pending.io.deq.valid && sawBusy && !io.controllerBusy) {
      configured := pending.io.deq.bits
      commandIndex := 0.U
      sawBusy := false.B
      state := emit
    }.elsewhen(!pending.io.deq.valid && issued.io.deq.valid && sawBusy && !io.controllerBusy) {
      state := complete
    }
  }

  when(io.instruction.fire) {
    when(loopCommand) {
      when(!io.controllerBusy) {
        sawBusy := false.B
      }
      state := select
    }.otherwise {
      commandIndex := commandIndex + 1.U
    }
  }

  when(io.done.fire) {
    when(issued.io.count === 1.U) {
      sawBusy := false.B
      state := select
    }
  }
}
