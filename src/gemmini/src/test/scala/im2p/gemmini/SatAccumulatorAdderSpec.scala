package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class SatAccumulatorAdderSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "SatAccumulatorAdder"

  it should "clamp a widened signed sum" in {
    test(new SatAccumulatorAdder) { dut =>
      def check(left: Long, right: Long, expected: Long): Unit = {
        dut.io.left.poke(left.S(32.W))
        dut.io.right.poke(right.S(32.W))
        dut.clock.step()
        dut.io.result.expect(expected.S(32.W))
      }

      check(2147483647L, 1, 2147483647L)
      check(-2147483648L, -1, -2147483648L)
      check(2147483647L, -2147483648L, -1)
      check(-100, 40, -60)
    }
  }
}
