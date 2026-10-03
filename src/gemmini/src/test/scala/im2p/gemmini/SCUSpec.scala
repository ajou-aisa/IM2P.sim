package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class SCUSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "SCU"

  it should "apply HP1 shifts, zero, validation, and signed saturation" in {
    test(new SCU(partialWidth = 22)) { dut =>
      def check(partial: Long, carrier: BigInt, expected: Long, valid: Boolean, zero: Boolean = false): Unit = {
        dut.io.partial.poke(partial.S(22.W))
        dut.io.carrier.poke(carrier.U(32.W))
        dut.clock.step()
        dut.io.result.expect(expected.S(32.W))
        dut.io.carrierValid.expect(valid.B)
        dut.io.zeroScale.expect(zero.B)
      }

      check(7, 0, 7, valid = true)
      check(-3, 1, -6, valid = true)
      check(1, 30, 1073741824L, valid = true)
      check(-1, 31, -2147483648L, valid = true)
      check(1, 31, 2147483647L, valid = true)
      check(-1, 32, -2147483648L, valid = true)
      check(1, 32767, 2147483647L, valid = true)
      check(0, 32767, 0, valid = true)
      check(123, BigInt("80000000", 16), 0, valid = true, zero = true)
      check(2, 30, 2147483647L, valid = true)
      check(-3, 30, -2147483648L, valid = true)
      check(1, 32768, 0, valid = false)
      check(1, BigInt("ffffffff", 16), 0, valid = false)
    }
  }
}
