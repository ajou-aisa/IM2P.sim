package im2p.gemmini

import chisel3._
import chisel3.util._

final class Hp1ScaleMetadata(generationWidth: Int) extends Bundle {
  val shift = UInt(15.W)
  val zero = Bool()
  val generation = UInt(generationWidth.W)
}

final class ScaleMemoryKey(addressWidth: Int, generationWidth: Int) extends Bundle {
  val address = UInt(addressWidth.W)
  val generation = UInt(generationWidth.W)
}

final class ScaleMemoryWrite(addressWidth: Int, generationWidth: Int) extends Bundle {
  val address = UInt(addressWidth.W)
  val generation = UInt(generationWidth.W)
  val carrier = UInt(32.W)
}

final class ScaleMemory(entries: Int, generationWidth: Int) extends Module {
  require(entries > 0 && isPow2(entries), s"entries must be a positive power of two, got $entries")
  require(generationWidth > 0, s"generationWidth must be positive, got $generationWidth")

  private val addressWidth = math.max(1, log2Ceil(entries))

  val io = IO(new Bundle {
    val write = Flipped(Decoupled(new ScaleMemoryWrite(addressWidth, generationWidth)))
    val release = Flipped(Valid(new ScaleMemoryKey(addressWidth, generationWidth)))
    val lookup = Input(new ScaleMemoryKey(addressWidth, generationWidth))
    val hit = Output(Bool())
    val metadata = Output(new Hp1ScaleMetadata(generationWidth))
    val carrier = Output(UInt(32.W))
    val invalidWrite = Output(Bool())
    val writeConflict = Output(Bool())
    val releaseMiss = Output(Bool())
  })

  private val valid = RegInit(VecInit(Seq.fill(entries)(false.B)))
  private val shifts = Reg(Vec(entries, UInt(15.W)))
  private val zeroes = Reg(Vec(entries, Bool()))
  private val generations = Reg(Vec(entries, UInt(generationWidth.W)))
  private val writeFree = !valid(io.write.bits.address)
  private val writeCarrierValid = Hp1ScaleEncoding.isValid(io.write.bits.carrier)
  private val releaseHit = valid(io.release.bits.address) &&
    generations(io.release.bits.address) === io.release.bits.generation
  private val lookupHit = valid(io.lookup.address) && generations(io.lookup.address) === io.lookup.generation

  io.write.ready := writeFree && writeCarrierValid
  io.invalidWrite := io.write.valid && !writeCarrierValid
  io.writeConflict := io.write.valid && !writeFree
  io.releaseMiss := io.release.valid && !releaseHit
  io.hit := lookupHit
  io.metadata.shift := Mux(lookupHit, shifts(io.lookup.address), 0.U)
  io.metadata.zero := lookupHit && zeroes(io.lookup.address)
  io.metadata.generation := Mux(lookupHit, generations(io.lookup.address), 0.U)
  io.carrier := Mux(
    lookupHit,
    Mux(zeroes(io.lookup.address), Hp1ScaleEncoding.ZeroCarrier.U(32.W), shifts(io.lookup.address)),
    0.U,
  )

  when(io.write.fire) {
    valid(io.write.bits.address) := true.B
    shifts(io.write.bits.address) := io.write.bits.carrier(14, 0)
    zeroes(io.write.bits.address) := Hp1ScaleEncoding.isZero(io.write.bits.carrier)
    generations(io.write.bits.address) := io.write.bits.generation
  }

  when(io.release.valid && releaseHit) {
    valid(io.release.bits.address) := false.B
  }
}
