package im2p.gemmini

import chisel3._
import chisel3.util._
import freechips.rocketchip.devices.tilelink.TLTestRAM
import freechips.rocketchip.diplomacy.{AddressSet, IdRange}
import freechips.rocketchip.tile.{RocketTileParams, TileKey, TileVisibilityNodeKey}
import freechips.rocketchip.tilelink._
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.{LazyModule, LazyModuleImp}

final class UpstreamWsHp1Top(
  val profile: ResolvedProfile,
  scaleEntries: Int = 256,
  workEntries: Int = 128,
)(implicit p: Parameters) extends Module {
  override def desiredName: String =
    s"IM2PGemminiWSHP1A${profile.operandBits}W${profile.weightBits}D${profile.dim}"

  val config = UpstreamWsConfig(profile)
  private val workIdWidth = math.max(1, log2Ceil(workEntries))
  private val control = Module(new UpstreamWsControl(profile, config, scaleEntries, 8, workEntries))
  private val memory = Module(new UpstreamWsMemory(profile, config))
  private val bridge = Module(new HostCommandBridge(profile))
  private val scaleLoader = Module(new ScaleBackingLoader(profile, scaleEntries, 8))
  private val cycles = Module(new MatmulCycleCounter)

  val io = IO(new Bundle {
    val work = Flipped(Decoupled(new Hp1LoopDescriptor))
    val loopDone = Decoupled(UInt(8.W))
    val completedHostSlot = Output(Bool())
    val logicalDone = Valid(UInt(8.W))
    val overlapLoopIssued = Output(Bool())
    val scaleRelease = Flipped(Decoupled(new ScaleRelease(profile.dim, scaleEntries, 8)))
    val readRequest = Decoupled(new BackingReadRequest(64, 4))
    val readBeat = Flipped(Decoupled(new BackingReadBeat(profile.dim * 32, 4)))
    val writeRequest = Decoupled(new BackingWriteBeat(profile.dim * 32, 64, 4))
    val writeCompletion = Flipped(Decoupled(new BackingWriteCompletion(4)))
    val outputCompleted = Valid(UInt(workIdWidth.W))
    val busy = Output(Bool())
    val error = Output(Bool())
    val controllerBusy = Output(Bool())
    val memoryDrained = Output(Bool())
    val loopBusy = Output(Bool())
    val writebackDrained = Output(Bool())
    val loadBusy = Output(Bool())
    val executeBusy = Output(Bool())
    val storeBusy = Output(Bool())
    val coreCycle = Output(UInt(64.W))
    val startCycle = Output(UInt(64.W))
    val doneCycle = Output(UInt(64.W))
    val elapsedCycles = Output(UInt(64.W))
    val measurementValid = Output(Bool())
    val loadRequests = Output(UInt(64.W))
    val loadResponses = Output(UInt(64.W))
    val storeRequests = Output(UInt(64.W))
    val storeResponses = Output(UInt(64.W))
    val readBytes = Output(UInt(64.W))
    val writeBytes = Output(UInt(64.W))
    val scaleReadRequests = Output(UInt(64.W))
    val scaleReadResponses = Output(UInt(64.W))
    val scaleReadBytes = Output(UInt(64.W))
    val events = Output(chiselTypeOf(control.io.events))
  })

  private val intervalWidth = 17
  private val releaseCountWidth = math.max(1, log2Ceil(scaleEntries * profile.dim + 1))
  private val fragmentsPerBlock = math.max(1, 32 / profile.dim)
  private val activeSlots = RegInit(VecInit(Seq.fill(2)(false.B)))
  private val completedSlots = RegInit(VecInit(Seq.fill(2)(false.B)))
  private val scaleStarts = Reg(Vec(2, UInt(intervalWidth.W)))
  private val scaleEnds = Reg(Vec(2, UInt(intervalWidth.W)))
  private val scaleGenerations = Reg(Vec(2, UInt(8.W)))
  private val releasesRemaining = Reg(Vec(2, UInt(releaseCountWidth.W)))
  private val releaseOwnershipError = RegInit(false.B)

  private val firstBlock = io.work.bits.fragmentBase / fragmentsPerBlock.U
  private val lastFragment = io.work.bits.fragmentBase +& io.work.bits.maxK - 1.U
  private val lastBlock = lastFragment / fragmentsPerBlock.U
  private val incomingScaleStart = io.work.bits.scaleBase +& firstBlock * io.work.bits.maxJ
  private val incomingScaleRows = (lastBlock - firstBlock + 1.U) * io.work.bits.maxJ
  private val incomingScaleEnd = incomingScaleStart +& incomingScaleRows
  private val incomingRangeValid = io.work.bits.maxK =/= 0.U &&
    incomingScaleEnd <= scaleEntries.U
  private val incomingOverlap = VecInit((0 until 2).map { slot =>
    activeSlots(slot) && incomingScaleStart < scaleEnds(slot) &&
      scaleStarts(slot) < incomingScaleEnd
  }).asUInt.orR
  private val incomingSlotFree = !activeSlots(io.work.bits.hostSlot)
  private val acceptWork = incomingRangeValid && incomingSlotFree && !incomingOverlap &&
    !releaseOwnershipError
  scaleLoader.io.work.valid := io.work.valid && acceptWork
  scaleLoader.io.work.bits := io.work.bits
  io.work.ready := scaleLoader.io.work.ready && acceptWork
  when(io.work.fire) {
    val slot = io.work.bits.hostSlot
    activeSlots(slot) := true.B
    completedSlots(slot) := false.B
    scaleStarts(slot) := incomingScaleStart(intervalWidth - 1, 0)
    scaleEnds(slot) := incomingScaleEnd(intervalWidth - 1, 0)
    scaleGenerations(slot) := io.work.bits.scaleGeneration
    releasesRemaining(slot) := (incomingScaleRows * profile.dim.U)(releaseCountWidth - 1, 0)
  }

  bridge.io.work <> scaleLoader.io.loadedWork
  control.io.instruction <> bridge.io.instruction
  control.io.loopMetadata <> bridge.io.loopMetadata
  bridge.io.controllerBusy := control.io.busy || !memory.io.drained || scaleLoader.io.busy
  io.loopDone <> bridge.io.done
  io.completedHostSlot := bridge.io.completedHostSlot
  io.logicalDone := bridge.io.logicalDone
  io.overlapLoopIssued := bridge.io.overlapIssued

  control.io.scaleLoad <> scaleLoader.io.scaleLoad
  private val releaseMatches = VecInit((0 until 2).map { slot =>
    activeSlots(slot) && io.scaleRelease.bits.address >= scaleStarts(slot) &&
      io.scaleRelease.bits.address < scaleEnds(slot) &&
      io.scaleRelease.bits.generation === scaleGenerations(slot)
  })
  private val releaseMatch = releaseMatches.asUInt.orR
  private val releaseSlot = PriorityEncoder(releaseMatches.asUInt)
  private val releaseAllowed = releaseMatch && completedSlots(releaseSlot)
  control.io.scaleRelease.valid := io.scaleRelease.valid && (releaseAllowed || !releaseMatch)
  control.io.scaleRelease.bits := io.scaleRelease.bits
  io.scaleRelease.ready := control.io.scaleRelease.ready && (releaseAllowed || !releaseMatch)
  when(control.io.scaleRelease.fire && releaseMatch) {
    assert(releasesRemaining(releaseSlot) =/= 0.U, "scale release count underflow")
    releasesRemaining(releaseSlot) := releasesRemaining(releaseSlot) - 1.U
  }
  when(io.scaleRelease.fire && !releaseMatch) {
    releaseOwnershipError := true.B
  }
  when(bridge.io.done.fire) {
    completedSlots(bridge.io.completedHostSlot) := true.B
  }
  for (slot <- 0 until 2) {
    when(activeSlots(slot) && completedSlots(slot) && releasesRemaining(slot) === 0.U) {
      activeSlots(slot) := false.B
      completedSlots(slot) := false.B
    }
  }
  memory.io.dmaRead <> control.io.dmaRead
  memory.io.dmaWrite <> control.io.dmaWrite
  memory.io.srams <> control.io.srams
  memory.io.acc <> control.io.acc
  private val readArbiter = Module(new Arbiter(new BackingReadRequest(64, 4), 2))
  readArbiter.io.in(0) <> scaleLoader.io.readRequest
  readArbiter.io.in(1) <> memory.io.readRequest
  io.readRequest <> Queue(readArbiter.io.out, 1)
  private val scaleResponse = io.readBeat.bits.id === 15.U
  scaleLoader.io.readBeat.valid := io.readBeat.valid && scaleResponse
  scaleLoader.io.readBeat.bits := io.readBeat.bits
  memory.io.readBeat.valid := io.readBeat.valid && !scaleResponse
  memory.io.readBeat.bits := io.readBeat.bits
  io.readBeat.ready := Mux(scaleResponse, scaleLoader.io.readBeat.ready, memory.io.readBeat.ready)
  io.writeRequest <> memory.io.writeRequest
  memory.io.writeCompletion <> io.writeCompletion
  dontTouch(io.writeRequest.bits.id)
  dontTouch(io.writeRequest.bits.first)
  dontTouch(io.writeRequest.bits.last)

  control.io.workDone.ready := true.B
  io.outputCompleted.valid := control.io.workDone.valid
  io.outputCompleted.bits := control.io.workDone.bits
  cycles.io.start := io.work.fire && io.work.bits.firstLoop
  cycles.io.done := bridge.io.logicalDone.valid
  io.coreCycle := cycles.io.coreCycle
  io.startCycle := cycles.io.startCycle
  io.doneCycle := cycles.io.doneCycle
  io.elapsedCycles := cycles.io.elapsedCycles
  io.measurementValid := cycles.io.measurementValid
  io.busy := activeSlots.asUInt.orR || scaleLoader.io.busy || bridge.io.active.valid ||
    control.io.busy || !control.io.writebackDrained || !memory.io.drained
  io.error := releaseOwnershipError || scaleLoader.io.error || control.io.protocolError || memory.io.error
  io.controllerBusy := control.io.busy
  io.memoryDrained := memory.io.drained && !scaleLoader.io.busy
  io.loopBusy := control.io.loopBusy
  io.writebackDrained := control.io.writebackDrained
  io.loadBusy := control.io.loadBusy
  io.executeBusy := control.io.executeBusy
  io.storeBusy := control.io.storeBusy
  io.loadRequests := memory.io.loadRequests
  io.loadResponses := memory.io.loadResponses
  io.storeRequests := memory.io.storeRequests
  io.storeResponses := memory.io.storeResponses
  io.readBytes := memory.io.readBytes
  io.writeBytes := memory.io.writeBytes
  io.scaleReadRequests := scaleLoader.io.requests
  io.scaleReadResponses := scaleLoader.io.responses
  io.scaleReadBytes := scaleLoader.io.readBytes
  io.events := control.io.events
}

final class UpstreamWsHp1Harness(
  profile: ResolvedProfile,
)(implicit parameters: Parameters) extends LazyModule {
  private val client = LazyModule(new InertTLClient)
  private val visibility = TLEphemeralNode()
  private val ram = LazyModule(new TLTestRAM(AddressSet(0, 0xffff), beatBytes = 8))
  ram.node := visibility := client.node

  lazy val module = new Impl

  final class Impl extends LazyModuleImp(this) {
    private implicit val controlParameters: Parameters = parameters.alterPartial {
      case TileKey => RocketTileParams()
      case TileVisibilityNodeKey => visibility
    }
    private val top = Module(new UpstreamWsHp1Top(profile)(controlParameters))
    val io = IO(chiselTypeOf(top.io))
    io <> top.io
  }
}
