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
  private val readRemaining = RegInit(VecInit(Seq.fill(maxOutstanding)(0.U(16.W))))
  private val writeActive = RegInit(VecInit(Seq.fill(maxOutstanding)(false.B)))
  private val writeLastAccepted = RegInit(VecInit(Seq.fill(maxOutstanding)(false.B)))
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
  private val readShapeValid = io.readCommand.bits.beats =/= 0.U
  private val readCommandAccepted =
    !protocolError && readIdInRange && readShapeValid && !readActive(readCommandIndex)
  io.readRequest.valid := io.readCommand.valid && readCommandAccepted
  io.readRequest.bits := io.readCommand.bits
  io.readCommand.ready := Mux(readCommandAccepted, io.readRequest.ready, true.B)
  when(io.readCommand.fire && readCommandAccepted) {
    readActive(readCommandIndex) := true.B
    readRemaining(readCommandIndex) := io.readCommand.bits.beats
  }

  private val readResponseInRange = io.readBeat.bits.id < maxOutstanding.U
  private val readResponseIndex = io.readBeat.bits.id(indexWidth - 1, 0)
  private val readResponseKnown = readResponseInRange && readActive(readResponseIndex)
  private val readResponseExpectedLast = readRemaining(readResponseIndex) === 1.U
  private val readResponseValid = readResponseKnown &&
    (readRemaining(readResponseIndex) =/= 0.U) &&
    (io.readBeat.bits.last === readResponseExpectedLast)
  io.readResult.valid := io.readBeat.valid && readResponseValid
  io.readResult.bits := io.readBeat.bits
  io.readBeat.ready := Mux(readResponseValid, io.readResult.ready, true.B)
  when(io.readBeat.fire && readResponseKnown) {
    when(readResponseValid && !io.readBeat.bits.last) {
      readRemaining(readResponseIndex) := readRemaining(readResponseIndex) - 1.U
    }.otherwise {
      readActive(readResponseIndex) := false.B
      readRemaining(readResponseIndex) := 0.U
    }
  }

  private val writeIdInRange = io.writeCommand.bits.id < maxOutstanding.U
  private val writeCommandIndex = io.writeCommand.bits.id(indexWidth - 1, 0)
  private val writeCommandAccepted = writeIdInRange && Mux(
    io.writeCommand.bits.first,
    !protocolError && !writeActive(writeCommandIndex),
    writeActive(writeCommandIndex) && !writeLastAccepted(writeCommandIndex),
  )
  io.writeRequest.valid := io.writeCommand.valid && writeCommandAccepted
  io.writeRequest.bits := io.writeCommand.bits
  io.writeCommand.ready := Mux(writeCommandAccepted, io.writeRequest.ready, true.B)
  when(io.writeCommand.fire && writeCommandAccepted) {
    when(io.writeCommand.bits.first) {
      writeActive(writeCommandIndex) := true.B
    }
    when(io.writeCommand.bits.last) {
      writeLastAccepted(writeCommandIndex) := true.B
    }
  }

  private val writeResponseInRange = io.writeCompletion.bits.id < maxOutstanding.U
  private val writeResponseIndex = io.writeCompletion.bits.id(indexWidth - 1, 0)
  private val writeResponseKnown = writeResponseInRange &&
    writeActive(writeResponseIndex) && writeLastAccepted(writeResponseIndex)
  io.writeResult.valid := io.writeCompletion.valid && writeResponseKnown
  io.writeResult.bits := io.writeCompletion.bits
  io.writeCompletion.ready := Mux(writeResponseKnown, io.writeResult.ready, true.B)
  when(io.writeCompletion.fire && writeResponseKnown) {
    writeActive(writeResponseIndex) := false.B
    writeLastAccepted(writeResponseIndex) := false.B
  }

  private val malformedCommand =
    !protocolError && ((io.readCommand.valid && !readCommandAccepted) ||
      (io.writeCommand.valid && !writeCommandAccepted))
  private val unknownResponse =
    (io.readBeat.valid && !readResponseValid) ||
      (io.writeCompletion.valid && !writeResponseKnown)
  when(malformedCommand || unknownResponse ||
    (io.readBeat.fire && readResponseValid && io.readBeat.bits.error) ||
    (io.writeCompletion.fire && io.writeCompletion.bits.error)) {
    protocolError := true.B
  }

  io.outstanding := PopCount(readActive) +& PopCount(writeActive)
  io.drained := io.outstanding === 0.U
  io.error := protocolError
}
