package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class ScuCompletionTrackerSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "ScuCompletionTracker"

  it should "publish completion only after every issued write commits" in {
    test(new ScuCompletionTracker(entries = 4, countWidth = 4)) { dut =>
      dut.io.allocate.valid.poke(false.B)
      dut.io.issue.valid.poke(false.B)
      dut.io.seal.valid.poke(false.B)
      dut.io.commit.valid.poke(false.B)
      dut.io.done.ready.poke(false.B)

      dut.io.allocate.bits.poke(1.U)
      dut.io.allocate.valid.poke(true.B)
      dut.io.allocate.ready.expect(true.B)
      dut.clock.step()
      dut.io.allocate.valid.poke(false.B)

      dut.io.issue.bits.poke(1.U)
      dut.io.issue.valid.poke(true.B)
      dut.clock.step(2)
      dut.io.issue.valid.poke(false.B)
      dut.io.seal.bits.poke(1.U)
      dut.io.seal.valid.poke(true.B)
      dut.clock.step()
      dut.io.seal.valid.poke(false.B)
      dut.io.done.valid.expect(false.B)

      dut.io.issue.valid.poke(true.B)
      dut.io.invalidIssue.expect(true.B)
      dut.clock.step()
      dut.io.issue.valid.poke(false.B)
      dut.io.outstanding(1).expect(2.U)

      dut.io.commit.bits.poke(1.U)
      dut.io.commit.valid.poke(true.B)
      dut.clock.step()
      dut.io.commit.valid.poke(false.B)
      dut.io.done.valid.expect(false.B)

      dut.io.commit.valid.poke(true.B)
      dut.clock.step()
      dut.io.commit.valid.poke(false.B)
      dut.io.done.valid.expect(true.B)
      dut.io.done.bits.expect(1.U)
      dut.io.outstanding(1).expect(0.U)

      dut.io.done.ready.poke(true.B)
      dut.clock.step()
      dut.io.done.valid.expect(false.B)
      dut.io.active(1).expect(false.B)
      dut.io.commitUnderflow.expect(false.B)
    }
  }
}
