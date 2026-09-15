package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

final class BackingMemoryPortSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "BackingMemoryPort"

  it should "track out-of-order reads and final write visibility" in {
    test(new BackingMemoryPort(ResolvedProfile(4, 4, 16))) { dut =>
      dut.io.readCommand.valid.poke(false.B)
      dut.io.readRequest.ready.poke(true.B)
      dut.io.readBeat.valid.poke(false.B)
      dut.io.readResult.ready.poke(true.B)
      dut.io.writeCommand.valid.poke(false.B)
      dut.io.writeRequest.ready.poke(true.B)
      dut.io.writeCompletion.valid.poke(false.B)
      dut.io.writeResult.ready.poke(true.B)

      def read(id: Int, beats: Int): Unit = {
        dut.io.readCommand.bits.address.poke((id * 4096).U)
        dut.io.readCommand.bits.beats.poke(beats.U)
        dut.io.readCommand.bits.id.poke(id.U)
        dut.io.readCommand.valid.poke(true.B)
        dut.io.readCommand.ready.expect(true.B)
        dut.clock.step()
        dut.io.readCommand.valid.poke(false.B)
      }
      read(1, 2)
      read(2, 1)
      dut.io.outstanding.expect(2.U)

      dut.io.readBeat.bits.id.poke(2.U)
      dut.io.readBeat.bits.data.poke(22.U)
      dut.io.readBeat.bits.last.poke(true.B)
      dut.io.readBeat.bits.error.poke(false.B)
      dut.io.readBeat.valid.poke(true.B)
      dut.io.readResult.bits.id.expect(2.U)
      dut.clock.step()
      dut.io.readBeat.bits.id.poke(1.U)
      dut.io.readBeat.bits.data.poke(11.U)
      dut.io.readBeat.bits.last.poke(false.B)
      dut.clock.step()
      dut.io.outstanding.expect(1.U)
      dut.io.readBeat.bits.last.poke(true.B)
      dut.clock.step()
      dut.io.readBeat.valid.poke(false.B)
      dut.io.drained.expect(true.B)

      dut.io.writeCommand.bits.address.poke(8192.U)
      dut.io.writeCommand.bits.id.poke(3.U)
      dut.io.writeCommand.bits.data.poke(33.U)
      dut.io.writeCommand.bits.mask.poke(-1.S.asUInt)
      dut.io.writeCommand.bits.first.poke(true.B)
      dut.io.writeCommand.bits.last.poke(true.B)
      dut.io.writeCommand.valid.poke(true.B)
      dut.clock.step()
      dut.io.writeCommand.valid.poke(false.B)
      dut.io.drained.expect(false.B)
      dut.io.writeCompletion.bits.id.poke(3.U)
      dut.io.writeCompletion.bits.error.poke(false.B)
      dut.io.writeCompletion.valid.poke(true.B)
      dut.clock.step()
      dut.io.writeCompletion.valid.poke(false.B)
      dut.io.drained.expect(true.B)

      dut.io.readBeat.bits.id.poke(7.U)
      dut.io.readBeat.bits.last.poke(true.B)
      dut.io.readBeat.valid.poke(true.B)
      dut.clock.step()
      dut.io.error.expect(true.B)
    }
  }
}
