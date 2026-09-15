package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class Int4PackerSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "Int4Packer"

  it should "pack signed lanes low nibble first and zero odd padding" in {
    test(new Int4Packer(lanes = 5)) { dut =>
      Seq(-8, -1, 0, 7, 3).zipWithIndex.foreach { case (value, index) =>
        dut.io.in(index).poke(value.S(4.W))
      }
      dut.io.validLanes.poke(5.U)
      dut.clock.step()
      dut.io.out.expect(BigInt("0370f8", 16).U)
      dut.io.validBytes.expect(3.U)

      dut.io.validLanes.poke(3.U)
      dut.clock.step()
      dut.io.out.expect(BigInt("0000f8", 16).U)
      dut.io.validBytes.expect(2.U)

      dut.io.validLanes.poke(6.U)
      dut.clock.step()
      dut.io.laneCountValid.expect(false.B)
      dut.io.out.expect(0.U)
      dut.io.validBytes.expect(0.U)
    }
  }
}
