package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class Int4UnpackerSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "Int4Unpacker"

  it should "unpack low-first nibbles and mask invalid tail lanes" in {
    test(new Int4Unpacker(lanes = 5)) { dut =>
      dut.io.in.poke(BigInt("0370f8", 16).U)
      dut.io.validLanes.poke(5.U)
      dut.clock.step()
      Seq(-8, -1, 0, 7, 3).zipWithIndex.foreach { case (value, index) =>
        dut.io.out(index).expect(value.S(4.W))
      }

      dut.io.validLanes.poke(3.U)
      dut.clock.step()
      Seq(-8, -1, 0, 0, 0).zipWithIndex.foreach { case (value, index) =>
        dut.io.out(index).expect(value.S(4.W))
      }

      dut.io.validLanes.poke(6.U)
      dut.clock.step()
      dut.io.laneCountValid.expect(false.B)
      dut.io.out.foreach(_.expect(0.S(4.W)))
    }
  }
}
