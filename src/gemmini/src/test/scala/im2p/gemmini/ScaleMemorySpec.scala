package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class ScaleMemorySpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "ScaleMemory"

  it should "keep scale metadata immutable until matching generation release" in {
    test(new ScaleMemory(entries = 4, generationWidth = 4)) { dut =>
      dut.io.write.valid.poke(false.B)
      dut.io.release.valid.poke(false.B)
      dut.io.lookup.address.poke(1.U)
      dut.io.lookup.generation.poke(3.U)

      dut.io.write.bits.address.poke(1.U)
      dut.io.write.bits.generation.poke(3.U)
      dut.io.write.bits.carrier.poke(7.U)
      dut.io.write.valid.poke(true.B)
      dut.io.write.ready.expect(true.B)
      dut.clock.step()
      dut.io.write.valid.poke(false.B)
      dut.io.hit.expect(true.B)
      dut.io.metadata.shift.expect(7.U)
      dut.io.metadata.zero.expect(false.B)
      dut.io.metadata.generation.expect(3.U)

      dut.io.write.bits.generation.poke(4.U)
      dut.io.write.bits.carrier.poke(9.U)
      dut.io.write.valid.poke(true.B)
      dut.io.write.ready.expect(false.B)
      dut.io.writeConflict.expect(true.B)

      dut.io.write.valid.poke(false.B)
      dut.io.release.bits.address.poke(1.U)
      dut.io.release.bits.generation.poke(2.U)
      dut.io.release.valid.poke(true.B)
      dut.clock.step()
      dut.io.release.valid.poke(false.B)
      dut.io.hit.expect(true.B)

      dut.io.release.bits.generation.poke(3.U)
      dut.io.release.valid.poke(true.B)
      dut.clock.step()
      dut.io.release.valid.poke(false.B)
      dut.io.hit.expect(false.B)

      dut.io.write.bits.address.poke(1.U)
      dut.io.write.bits.generation.poke(4.U)
      dut.io.write.bits.carrier.poke(BigInt("80000000", 16).U)
      dut.io.write.valid.poke(true.B)
      dut.clock.step()
      dut.io.write.valid.poke(false.B)
      dut.io.lookup.generation.poke(4.U)
      dut.io.hit.expect(true.B)
      dut.io.metadata.zero.expect(true.B)

      dut.io.release.bits.address.poke(1.U)
      dut.io.release.bits.generation.poke(4.U)
      dut.io.release.valid.poke(true.B)
      dut.clock.step()
      dut.io.release.valid.poke(false.B)

      dut.io.write.bits.carrier.poke(32768.U)
      dut.io.write.valid.poke(true.B)
      dut.io.write.ready.expect(false.B)
      dut.io.invalidWrite.expect(true.B)
      dut.clock.step()
      dut.io.write.valid.poke(false.B)
      dut.io.hit.expect(false.B)
    }
  }
}
