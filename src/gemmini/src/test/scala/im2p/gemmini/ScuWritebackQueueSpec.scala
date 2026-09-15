package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

class ScuWritebackQueueSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "ScuWritebackQueue"

  it should "reserve response credit and preserve writeback metadata" in {
    test(new ScuWritebackQueue(
      lanes = 2,
      depth = 2,
      bankWidth = 2,
      rowWidth = 8,
      workIdWidth = 3,
      robIdWidth = 3,
      fragmentIdWidth = 5,
      generationWidth = 4,
      scaleAddressWidth = 4
    )) { dut =>
      dut.io.reserve.poke(false.B)
      dut.io.reserveBatch.valid.poke(false.B)
      dut.io.reserveBatch.bits.poke(0.U)
      dut.io.enq.valid.poke(false.B)
      dut.io.deq.ready.poke(false.B)
      dut.io.reserveReady.expect(true.B)
      dut.io.credits.expect(2.U)

      dut.io.reserve.poke(true.B)
      dut.clock.step()
      dut.io.reserve.poke(false.B)
      dut.io.reserved.expect(1.U)
      dut.io.credits.expect(1.U)

      dut.io.enq.bits.data(0).poke((-11).S(32.W))
      dut.io.enq.bits.data(1).poke(22.S(32.W))
      dut.io.enq.bits.accBank.poke(2.U)
      dut.io.enq.bits.accRow.poke(19.U)
      dut.io.enq.bits.mask.poke("b01".U)
      dut.io.enq.bits.accumulate.poke(false.B)
      dut.io.enq.bits.workId.poke(5.U)
      dut.io.enq.bits.robId.poke(5.U)
      dut.io.enq.bits.fragmentId.poke(17.U)
      dut.io.enq.bits.finalFragment.poke(true.B)
      dut.io.enq.bits.completeRob.poke(true.B)
      dut.io.enq.bits.scaleAddress.poke(3.U)
      dut.io.enq.bits.scaleGeneration.poke(9.U)
      dut.io.enq.valid.poke(true.B)
      dut.io.enq.ready.expect(true.B)
      dut.clock.step()
      dut.io.enq.valid.poke(false.B)
      dut.io.reserved.expect(0.U)
      dut.io.occupied.expect(1.U)
      dut.io.deq.valid.expect(true.B)
      dut.io.deq.bits.data(0).expect((-11).S(32.W))
      dut.io.deq.bits.data(1).expect(22.S(32.W))
      dut.io.deq.bits.accBank.expect(2.U)
      dut.io.deq.bits.accRow.expect(19.U)
      dut.io.deq.bits.mask.expect("b01".U)
      dut.io.deq.bits.accumulate.expect(false.B)
      dut.io.deq.bits.workId.expect(5.U)
      dut.io.deq.bits.robId.expect(5.U)
      dut.io.deq.bits.fragmentId.expect(17.U)
      dut.io.deq.bits.finalFragment.expect(true.B)
      dut.io.deq.bits.completeRob.expect(true.B)
      dut.io.deq.bits.scaleAddress.expect(3.U)
      dut.io.deq.bits.scaleGeneration.expect(9.U)

      dut.io.enq.valid.poke(true.B)
      dut.io.enq.ready.expect(false.B)
      dut.io.responseWithoutReservation.expect(true.B)

      dut.io.enq.valid.poke(false.B)
      dut.io.reserve.poke(true.B)
      dut.clock.step()
      dut.io.reserve.poke(false.B)
      dut.io.enq.bits.fragmentId.poke(18.U)
      dut.io.enq.bits.finalFragment.poke(false.B)
      dut.io.enq.bits.accumulate.poke(true.B)
      dut.io.enq.bits.scaleAddress.poke(4.U)
      dut.io.enq.valid.poke(true.B)
      dut.io.enq.ready.expect(true.B)
      dut.clock.step()
      dut.io.enq.valid.poke(false.B)

      dut.io.deq.bits.fragmentId.expect(17.U)
      dut.io.deq.ready.poke(true.B)
      dut.clock.step()
      dut.io.deq.bits.fragmentId.expect(18.U)
      dut.io.deq.bits.accBank.expect(2.U)
      dut.io.deq.bits.accRow.expect(19.U)
      dut.io.deq.bits.scaleAddress.expect(4.U)
    }
  }

  it should "reserve an entire mesh burst atomically" in {
    test(new ScuWritebackQueue(
      lanes = 2,
      depth = 3,
      bankWidth = 2,
      rowWidth = 8,
      workIdWidth = 3,
      robIdWidth = 3,
      fragmentIdWidth = 5,
      generationWidth = 4,
      scaleAddressWidth = 4
    )) { dut =>
      dut.io.reserve.poke(false.B)
      dut.io.reserveBatch.valid.poke(false.B)
      dut.io.reserveBatch.bits.poke(0.U)
      dut.io.enq.valid.poke(false.B)
      dut.io.deq.ready.poke(false.B)

      dut.io.reserveBatch.valid.poke(true.B)
      dut.io.reserveBatch.bits.poke(2.U)
      dut.io.reserveBatch.ready.expect(true.B)
      dut.clock.step()
      dut.io.reserveBatch.valid.poke(false.B)
      dut.io.reserveBatch.bits.poke(0.U)
      dut.io.reserved.expect(2.U)
      dut.io.occupied.expect(0.U)
      dut.io.credits.expect(1.U)

      dut.io.enq.bits.data(0).poke(1.S(32.W))
      dut.io.enq.bits.data(1).poke(2.S(32.W))
      dut.io.enq.bits.accBank.poke(0.U)
      dut.io.enq.bits.accRow.poke(0.U)
      dut.io.enq.bits.mask.poke("b11".U)
      dut.io.enq.bits.accumulate.poke(false.B)
      dut.io.enq.bits.workId.poke(3.U)
      dut.io.enq.bits.robId.poke(3.U)
      dut.io.enq.bits.fragmentId.poke(0.U)
      dut.io.enq.bits.finalFragment.poke(false.B)
      dut.io.enq.bits.completeRob.poke(false.B)
      dut.io.enq.bits.scaleAddress.poke(0.U)
      dut.io.enq.bits.scaleGeneration.poke(0.U)
      dut.io.enq.valid.poke(true.B)
      dut.clock.step()
      dut.io.reserved.expect(1.U)
      dut.io.occupied.expect(1.U)
      dut.io.credits.expect(1.U)

      dut.io.deq.ready.poke(true.B)
      dut.io.reserve.poke(true.B)
      dut.io.enq.bits.fragmentId.poke(1.U)
      dut.io.reserveReady.expect(true.B)
      dut.clock.step()
      dut.io.reserve.poke(false.B)
      dut.io.enq.valid.poke(false.B)
      dut.io.reserved.expect(1.U)
      dut.io.occupied.expect(1.U)
      dut.io.credits.expect(1.U)

      dut.io.deq.ready.poke(false.B)
      dut.io.reserveBatch.valid.poke(true.B)
      dut.io.reserveBatch.bits.poke(0.U)
      dut.io.reserveBatch.ready.expect(false.B)
      dut.io.reserveBatch.bits.poke(2.U)
      dut.io.reserveBatch.ready.expect(false.B)
      dut.io.reserveBatch.valid.poke(false.B)
      dut.io.reserveBatch.bits.poke(0.U)
      dut.io.reserved.expect(1.U)
      dut.io.occupied.expect(1.U)
      dut.io.credits.expect(1.U)

      dut.io.deq.ready.poke(true.B)
      dut.clock.step()
      dut.io.reserved.expect(1.U)
      dut.io.occupied.expect(0.U)
      dut.io.credits.expect(2.U)

      dut.io.deq.ready.poke(false.B)
      dut.io.reserveBatch.valid.poke(true.B)
      dut.io.reserveBatch.bits.poke(3.U)
      dut.io.reserveBatch.ready.expect(false.B)
      dut.io.reserveBatch.bits.poke(2.U)
      dut.io.reserveBatch.ready.expect(true.B)
      dut.io.reserve.poke(true.B)
      dut.io.reserveReady.expect(false.B)
      dut.io.reserveBatch.ready.expect(false.B)
      dut.io.reserve.poke(false.B)
      dut.clock.step()
      dut.io.reserveBatch.valid.poke(false.B)
      dut.io.reserveBatch.bits.poke(0.U)
      dut.io.reserved.expect(3.U)
      dut.io.occupied.expect(0.U)
      dut.io.credits.expect(0.U)
    }
  }
}
