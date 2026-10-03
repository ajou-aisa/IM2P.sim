package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class ScuFragmentPlannerSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "ScuFragmentPlanner"

  it should "stop DIM64 fragments at every K32 boundary" in {
    test(new ScuFragmentPlanner(dimension = 64, kWidth = 16)) { dut =>
      def check(kIndex: Int, remainingK: Int, fragment: Int, remainingInBlock: Int, last: Boolean): Unit = {
        dut.io.kIndex.poke(kIndex.U)
        dut.io.remainingK.poke(remainingK.U)
        dut.clock.step()
        dut.io.fragmentLength.expect(fragment.U)
        dut.io.remainingInBlock.expect(remainingInBlock.U)
        dut.io.lastFragment.expect(last.B)
        dut.io.hasWork.expect((remainingK != 0).B)
        dut.io.nextKIndex.expect((kIndex + fragment).U)
      }

      check(kIndex = 0, remainingK = 96, fragment = 32, remainingInBlock = 32, last = false)
      check(kIndex = 32, remainingK = 64, fragment = 32, remainingInBlock = 32, last = false)
      check(kIndex = 61, remainingK = 35, fragment = 3, remainingInBlock = 3, last = false)
      check(kIndex = 89, remainingK = 7, fragment = 7, remainingInBlock = 7, last = true)
      check(kIndex = 96, remainingK = 0, fragment = 0, remainingInBlock = 32, last = true)
    }
  }
}
