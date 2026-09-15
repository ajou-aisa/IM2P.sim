package im2p.gemmini

import chisel3._
import chisel3.util.{Decoupled, PopCount, isPow2, log2Ceil}

final class LocalMemoryLoad(profile: ResolvedProfile, bankRows: Int) extends Bundle {
  val weights = Bool()
  val slot = Bool()
  val row = UInt(math.max(1, log2Ceil(bankRows)).W)
  val data = UInt((profile.dim * profile.operandBits).W)
}

final class LocalMemoryRead(bankRows: Int) extends Bundle {
  val slot = Bool()
  val row = UInt(math.max(1, log2Ceil(bankRows)).W)
}

final class ScaleLoad(profile: ResolvedProfile, scaleEntries: Int, generationWidth: Int)
    extends Bundle {
  val column = UInt(math.max(1, log2Ceil(profile.dim)).W)
  val address = UInt(math.max(1, log2Ceil(scaleEntries)).W)
  val generation = UInt(generationWidth.W)
  val carrier = UInt(32.W)
}

final class ScaleRelease(profile: ResolvedProfile, scaleEntries: Int, generationWidth: Int)
    extends Bundle {
  val column = UInt(math.max(1, log2Ceil(profile.dim)).W)
  val address = UInt(math.max(1, log2Ceil(scaleEntries)).W)
  val generation = UInt(generationWidth.W)
}

final class StandaloneFragmentCommand(
  profile: ResolvedProfile,
  scratchpadBankRows: Int,
  accumulatorRows: Int,
  scaleEntries: Int,
  generationWidth: Int,
  workIdWidth: Int,
) extends Bundle {
  val slot = Bool()
  val validRows = UInt(math.max(1, log2Ceil(profile.dim + 1)).W)
  val validColumns = UInt(math.max(1, log2Ceil(profile.dim + 1)).W)
  val fragmentLength = UInt(math.max(1, log2Ceil(profile.dim + 1)).W)
  val activationBase = UInt(math.max(1, log2Ceil(scratchpadBankRows)).W)
  val weightBase = UInt(math.max(1, log2Ceil(scratchpadBankRows)).W)
  val accumulatorBase = UInt(math.max(1, log2Ceil(accumulatorRows)).W)
  val scaleAddress = UInt(math.max(1, log2Ceil(scaleEntries)).W)
  val scaleGeneration = UInt(generationWidth.W)
  val workId = UInt(workIdWidth.W)
  val firstContribution = Bool()
  val finalFragment = Bool()
}

final class StandaloneResultRequest(accumulatorRows: Int) extends Bundle {
  val row = UInt(math.max(1, log2Ceil(accumulatorRows)).W)
}

final class StandaloneResult(profile: ResolvedProfile, accumulatorRows: Int) extends Bundle {
  val row = UInt(math.max(1, log2Ceil(accumulatorRows)).W)
  val data = Vec(profile.dim, SInt(32.W))
}

final class BackingReadRequest(addressWidth: Int, idWidth: Int) extends Bundle {
  val address = UInt(addressWidth.W)
  val beats = UInt(16.W)
  val id = UInt(idWidth.W)
}

final class BackingReadBeat(dataWidth: Int, idWidth: Int) extends Bundle {
  val id = UInt(idWidth.W)
  val data = UInt(dataWidth.W)
  val last = Bool()
  val error = Bool()
}

final class BackingWriteBeat(dataWidth: Int, addressWidth: Int, idWidth: Int) extends Bundle {
  val address = UInt(addressWidth.W)
  val id = UInt(idWidth.W)
  val data = UInt(dataWidth.W)
  val mask = UInt(((dataWidth + 7) / 8).W)
  val first = Bool()
  val last = Bool()
}

final class BackingWriteCompletion(idWidth: Int) extends Bundle {
  val id = UInt(idWidth.W)
  val error = Bool()
}

final class BackingMemoryPort(
  profile: ResolvedProfile,
  addressWidth: Int = 64,
  idWidth: Int = 4,
  maxOutstanding: Int = 8,
) extends Module {
  require(addressWidth > 0 && idWidth > 0)
  require(maxOutstanding > 0 && isPow2(maxOutstanding))
  require(maxOutstanding <= (1 << idWidth))

  private val dataWidth = profile.dim * 32
  private val indexWidth = math.max(1, log2Ceil(maxOutstanding))
  private val readActive = RegInit(VecInit(Seq.fill(maxOutstanding)(false.B)))
  private val writeActive = RegInit(VecInit(Seq.fill(maxOutstanding)(false.B)))
  private val protocolError = RegInit(false.B)

  val io = IO(new Bundle {
    val readCommand = Flipped(Decoupled(new BackingReadRequest(addressWidth, idWidth)))
    val readRequest = Decoupled(new BackingReadRequest(addressWidth, idWidth))
    val readBeat = Flipped(Decoupled(new BackingReadBeat(dataWidth, idWidth)))
    val readResult = Decoupled(new BackingReadBeat(dataWidth, idWidth))
    val writeCommand = Flipped(Decoupled(new BackingWriteBeat(dataWidth, addressWidth, idWidth)))
    val writeRequest = Decoupled(new BackingWriteBeat(dataWidth, addressWidth, idWidth))
    val writeCompletion = Flipped(Decoupled(new BackingWriteCompletion(idWidth)))
    val writeResult = Decoupled(new BackingWriteCompletion(idWidth))
    val outstanding = Output(UInt(log2Ceil(maxOutstanding * 2 + 1).W))
    val drained = Output(Bool())
    val error = Output(Bool())
  })

  private val readIdInRange = io.readCommand.bits.id < maxOutstanding.U
  private val readCommandIndex = io.readCommand.bits.id(indexWidth - 1, 0)
  private val readIdAvailable = readIdInRange && !readActive(readCommandIndex)
  private val readShapeValid = io.readCommand.bits.beats =/= 0.U
  io.readRequest.valid := io.readCommand.valid && readIdAvailable && readShapeValid
  io.readRequest.bits := io.readCommand.bits
  io.readCommand.ready := io.readRequest.ready && readIdAvailable && readShapeValid
  when(io.readCommand.fire) {
    readActive(readCommandIndex) := true.B
  }

  private val readResponseInRange = io.readBeat.bits.id < maxOutstanding.U
  private val readResponseIndex = io.readBeat.bits.id(indexWidth - 1, 0)
  private val readResponseKnown = readResponseInRange && readActive(readResponseIndex)
  io.readResult.valid := io.readBeat.valid && readResponseKnown
  io.readResult.bits := io.readBeat.bits
  io.readBeat.ready := Mux(readResponseKnown, io.readResult.ready, true.B)
  when(io.readBeat.fire && readResponseKnown && io.readBeat.bits.last) {
    readActive(readResponseIndex) := false.B
  }

  private val writeIdInRange = io.writeCommand.bits.id < maxOutstanding.U
  private val writeCommandIndex = io.writeCommand.bits.id(indexWidth - 1, 0)
  private val writeIdStateValid = writeIdInRange && Mux(
    io.writeCommand.bits.first,
    !writeActive(writeCommandIndex),
    writeActive(writeCommandIndex),
  )
  io.writeRequest.valid := io.writeCommand.valid && writeIdStateValid
  io.writeRequest.bits := io.writeCommand.bits
  io.writeCommand.ready := io.writeRequest.ready && writeIdStateValid
  when(io.writeCommand.fire && io.writeCommand.bits.first) {
    writeActive(writeCommandIndex) := true.B
  }

  private val writeResponseInRange = io.writeCompletion.bits.id < maxOutstanding.U
  private val writeResponseIndex = io.writeCompletion.bits.id(indexWidth - 1, 0)
  private val writeResponseKnown = writeResponseInRange && writeActive(writeResponseIndex)
  io.writeResult.valid := io.writeCompletion.valid && writeResponseKnown
  io.writeResult.bits := io.writeCompletion.bits
  io.writeCompletion.ready := Mux(writeResponseKnown, io.writeResult.ready, true.B)
  when(io.writeCompletion.fire && writeResponseKnown) {
    writeActive(writeResponseIndex) := false.B
  }

  private val malformedCommand =
    (io.readCommand.valid && (!readIdInRange || !readShapeValid)) ||
      (io.writeCommand.valid && !writeIdStateValid)
  private val unknownResponse =
    (io.readBeat.valid && !readResponseKnown) ||
      (io.writeCompletion.valid && !writeResponseKnown)
  when(malformedCommand || unknownResponse ||
    (io.readBeat.fire && io.readBeat.bits.error) ||
    (io.writeCompletion.fire && io.writeCompletion.bits.error)) {
    protocolError := true.B
  }

  io.outstanding := PopCount(readActive) +& PopCount(writeActive)
  io.drained := io.outstanding === 0.U
  io.error := protocolError
}
