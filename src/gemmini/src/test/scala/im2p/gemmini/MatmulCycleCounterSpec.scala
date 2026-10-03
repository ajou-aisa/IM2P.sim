package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class MatmulCycleCounterSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "MatmulCycleCounter"

  it should "measure start-to-done endpoint distance with a free-running 64-bit counter" in {
    test(new MatmulCycleCounter) { dut =>
      dut.io.start.poke(false.B)
      dut.io.done.poke(false.B)
      dut.clock.step()

      dut.io.start.poke(true.B)
      dut.clock.step()
      dut.io.start.poke(false.B)
      dut.io.running.expect(true.B)
      dut.io.measurementValid.expect(false.B)

      dut.clock.step(3)
      dut.io.done.poke(true.B)
      dut.clock.step()
      dut.io.done.poke(false.B)
      dut.io.running.expect(false.B)
      dut.io.measurementValid.expect(true.B)
      dut.io.elapsedCycles.expect(4.U)
      dut.io.doneCycle.expect((dut.io.startCycle.peek().litValue + 4).U(64.W))
    }
  }
}
