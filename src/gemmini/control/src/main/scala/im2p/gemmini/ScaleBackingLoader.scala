package im2p.gemmini

import chisel3._
import chisel3.util._

final class ScaleBackingLoader(
  profile: ResolvedProfile,
  scaleEntries: Int = 256,
  generationWidth: Int = 8,
) extends Module {
  require(scaleEntries > 0 && isPow2(scaleEntries))
  require(generationWidth > 0)

  private val reservedId = 15
  private val rowBytes = profile.dim * 4
  private val addressWidth = math.max(1, log2Ceil(scaleEntries))
  private val columnWidth = math.max(1, log2Ceil(profile.dim))
  private val fragmentsPerBlock = math.max(1, 32 / profile.dim)

  val io = IO(new Bundle {
    val work = Flipped(Decoupled(new Hp1LoopDescriptor))
    val loadedWork = Decoupled(new Hp1LoopDescriptor)
    val readRequest = Decoupled(new BackingReadRequest(64, 4))
    val readBeat = Flipped(Decoupled(new BackingReadBeat(profile.dim * 32, 4)))
    val scaleLoad = Decoupled(new ScaleLoad(profile, scaleEntries, generationWidth))
    val requests = Output(UInt(64.W))
    val responses = Output(UInt(64.W))
    val readBytes = Output(UInt(64.W))
    val busy = Output(Bool())
    val error = Output(Bool())
  })

  private val idle :: request :: receive :: emit :: forward :: failed :: Nil = Enum(6)
  private val state = RegInit(idle)
  private val descriptor = Reg(new Hp1LoopDescriptor)
  private val firstRow = Reg(UInt(addressWidth.W))
  private val rowCount = Reg(UInt((addressWidth + 1).W))
  private val rowIndex = RegInit(0.U((addressWidth + 1).W))
  private val column = RegInit(0.U(columnWidth.W))
  private val rowData = Reg(UInt((profile.dim * 32).W))
  private val requestCount = RegInit(0.U(64.W))
  private val responseCount = RegInit(0.U(64.W))
  private val byteCount = RegInit(0.U(64.W))
  private val pending = Module(new Queue(new Hp1LoopDescriptor, 2, flow = true))
  pending.io.enq.valid := io.work.valid && state =/= failed
  pending.io.enq.bits := io.work.bits
  io.work.ready := pending.io.enq.ready && state =/= failed

  private val work = pending.io.deq.bits
  private val firstBlock = work.fragmentBase / fragmentsPerBlock.U
  private val finalFragment = work.fragmentBase +& work.maxK - 1.U
  private val lastBlock = finalFragment / fragmentsPerBlock.U
  private val firstRowWide = work.scaleBase +& firstBlock * work.maxJ
  private val rowCountWide = (lastBlock - firstBlock + 1.U) * work.maxJ
  private val endRowWide = firstRowWide +& rowCountWide
  private val backingEnd = work.scaleBackingAddress +& rowCountWide * rowBytes.U
  private val shapeValid = work.maxI =/= 0.U && work.maxJ =/= 0.U &&
    work.maxK =/= 0.U && work.padI < profile.dim.U &&
    work.padJ < profile.dim.U && work.padK < profile.dim.U &&
    (work.aStrideBytes * 8.U) % profile.operandBits.U === 0.U &&
    (work.bStrideBytes * 8.U) % profile.operandBits.U === 0.U &&
    work.cStrideBytes % 4.U === 0.U &&
    endRowWide <= scaleEntries.U && backingEnd <= (BigInt(1) << 64).U &&
    work.rawShapeValid(profile.dim)

  pending.io.deq.ready := state === idle
  io.loadedWork.valid := state === forward
  io.loadedWork.bits := descriptor
  io.readRequest.valid := state === request
  io.readRequest.bits.address := descriptor.scaleBackingAddress + rowIndex * rowBytes.U
  io.readRequest.bits.beats := 1.U
  io.readRequest.bits.id := reservedId.U
  io.readBeat.ready := true.B

  private val carriers = rowData.asTypeOf(Vec(profile.dim, UInt(32.W)))
  private val carrier = Mux(descriptor.rmdRaw, 0.U(32.W), carriers(column))
  private val carrierValid = Hp1ScaleEncoding.isValid(carrier)
  io.scaleLoad.valid := state === emit && carrierValid
  io.scaleLoad.bits.column := column
  io.scaleLoad.bits.address := (firstRow + rowIndex)(addressWidth - 1, 0)
  io.scaleLoad.bits.generation := descriptor.scaleGeneration
  io.scaleLoad.bits.carrier := carrier

  io.requests := requestCount
  io.responses := responseCount
  io.readBytes := byteCount
  io.busy := state =/= idle || pending.io.deq.valid
  io.error := state === failed

  when(pending.io.deq.fire) {
    descriptor := work
    firstRow := firstRowWide(addressWidth - 1, 0)
    rowCount := rowCountWide
    rowIndex := 0.U
    column := 0.U
    state := Mux(shapeValid, request, failed)
  }

  when(io.readRequest.fire) {
    requestCount := requestCount + 1.U
    state := receive
  }

  when(io.readBeat.fire) {
    val validResponse = state === receive && io.readBeat.bits.id === reservedId.U &&
      io.readBeat.bits.last && !io.readBeat.bits.error
    when(validResponse) {
      rowData := io.readBeat.bits.data
      column := 0.U
      responseCount := responseCount + 1.U
      byteCount := byteCount + rowBytes.U
      state := emit
    }.otherwise {
      state := failed
    }
  }

  when(state === emit) {
    when(!carrierValid) {
      state := failed
    }.elsewhen(io.scaleLoad.fire) {
      when(column === (profile.dim - 1).U) {
        column := 0.U
        when(rowIndex + 1.U === rowCount) {
          state := forward
        }.otherwise {
          rowIndex := rowIndex + 1.U
          state := request
        }
      }.otherwise {
        column := column + 1.U
      }
    }
  }

  when(io.loadedWork.fire) {
    state := idle
  }
}
