package im2p.gemmini

import chisel3._
import chiseltest._
import gemmini.GemminiISA
import org.scalatest.flatspec.AnyFlatSpec

class HostCommandBridgeSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "HostCommandBridge"
  private val profile = ResolvedProfile(4, 4, 16)

  private def pokeWork(dut: HostCommandBridge): Unit = {
    dut.io.work.bits.maxI.poke(0x11.U)
    dut.io.work.bits.maxJ.poke(0x22.U)
    dut.io.work.bits.maxK.poke(0x33.U)
    dut.io.work.bits.padI.poke(0x44.U)
    dut.io.work.bits.padJ.poke(0x55.U)
    dut.io.work.bits.padK.poke(0x66.U)
    dut.io.work.bits.aAddress.poke("h1111222233334444".U)
    dut.io.work.bits.bAddress.poke("h5555666677778888".U)
    dut.io.work.bits.cAddress.poke("h9999aaaabbbbcccc".U)
    dut.io.work.bits.scaleBackingAddress.poke("hddddeeeeffff0000".U)
    dut.io.work.bits.aStrideBytes.poke("h109".U)
    dut.io.work.bits.bStrideBytes.poke("h211".U)
    dut.io.work.bits.cStrideBytes.poke("h344".U)
    dut.io.work.bits.scaleBase.poke("h1234".U)
    dut.io.work.bits.scaleGeneration.poke("h56".U)
    dut.io.work.bits.fragmentBase.poke("h789a".U)
    dut.io.work.bits.workBase.poke("hbc".U)
    dut.io.work.bits.accumulate.poke(true.B)
    dut.io.work.bits.finalFragment.poke(true.B)
    dut.io.work.bits.firstLoop.poke(true.B)
    dut.io.work.bits.finalLoop.poke(true.B)
    dut.io.work.bits.logicalWorkId.poke("hde".U)
    dut.io.work.bits.hostSlot.poke(false.B)
    dut.io.work.bits.rmdRaw.poke(false.B)
  }

  private val expected = Seq(
    (GemminiISA.CONFIG_CMD.litValue, BigInt("00100101", 16), BigInt("109", 16)),
    (GemminiISA.CONFIG_CMD.litValue, BigInt("00100109", 16), BigInt("211", 16)),
    (GemminiISA.CONFIG_CMD.litValue, BigInt("00100111", 16), BigInt(0)),
    (GemminiISA.CONFIG_CMD.litValue, BigInt(2), BigInt("344", 16)),
    (GemminiISA.CONFIG_CMD.litValue, BigInt("00010004", 16), BigInt("0001000000000000", 16)),
    (GemminiISA.LOOP_WS_CONFIG_BOUNDS.litValue, BigInt("006600550044", 16), BigInt("003300220011", 16)),
    (GemminiISA.LOOP_WS_CONFIG_ADDRS_AB.litValue, BigInt("1111222233334444", 16), BigInt("5555666677778888", 16)),
    (GemminiISA.LOOP_WS_CONFIG_ADDRS_DC.litValue, BigInt(0), BigInt("9999aaaabbbbcccc", 16)),
    (GemminiISA.LOOP_WS_CONFIG_STRIDES_AB.litValue, BigInt("212", 16), BigInt("422", 16)),
    (GemminiISA.LOOP_WS_CONFIG_STRIDES_DC.litValue, BigInt(0), BigInt("d1", 16)),
    (GemminiISA.LOOP_WS.litValue, BigInt(3), BigInt(0)),
  )

  it should "emit the pinned loop command sequence and hold each command under backpressure" in {
    test(new HostCommandBridge(profile)) { dut =>
      // Given
      pokeWork(dut)
      dut.io.work.bits.maxI.poke(0.U)
      dut.io.work.valid.poke(true.B)
      dut.io.instruction.ready.poke(false.B)
      dut.io.loopMetadata.ready.poke(true.B)
      dut.io.controllerBusy.poke(false.B)
      dut.io.done.ready.poke(false.B)

      // When
      dut.io.work.ready.expect(false.B)
      dut.io.start.expect(false.B)
      pokeWork(dut)
      dut.io.work.bits.aStrideBytes.poke(1.U)
      dut.io.work.ready.expect(true.B)
      pokeWork(dut)
      dut.io.work.bits.bStrideBytes.poke(1.U)
      dut.io.work.ready.expect(true.B)
      pokeWork(dut)
      dut.io.work.bits.cStrideBytes.poke(1.U)
      dut.io.work.ready.expect(false.B)
      pokeWork(dut)
      dut.io.work.ready.expect(true.B)
      dut.io.start.expect(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)

      // Then
      dut.io.start.expect(false.B)
      dut.io.work.ready.expect(true.B)
      dut.io.active.valid.expect(true.B)
      dut.io.active.bits.logicalWorkId.expect("hde".U)
      dut.io.instruction.valid.expect(true.B)
      dut.io.instruction.bits.funct.expect(expected.head._1.U)
      dut.io.instruction.bits.rs1.expect(expected.head._2.U)
      dut.io.instruction.bits.rs2.expect(expected.head._3.U)
      dut.clock.step(2)
      dut.io.instruction.bits.funct.expect(expected.head._1.U)
      dut.io.instruction.bits.rs1.expect(expected.head._2.U)
      dut.io.instruction.bits.rs2.expect(expected.head._3.U)

      dut.io.instruction.ready.poke(true.B)
      dut.io.loopMetadata.ready.poke(true.B)
      expected.zipWithIndex.foreach { case ((funct, rs1, rs2), index) =>
        dut.io.instruction.valid.expect(true.B)
        dut.io.instruction.bits.funct.expect(funct.U)
        dut.io.instruction.bits.rs1.expect(rs1.U)
        dut.io.instruction.bits.rs2.expect(rs2.U)
        if (index < 3) {
          assert((dut.io.instruction.bits.rs1.peek().litValue & 3) === GemminiISA.CONFIG_LOAD.litValue)
        }
        dut.clock.step()
      }
      dut.io.instruction.valid.expect(false.B)
    }
  }

  it should "complete only after controller busy rises then falls and done is consumed" in {
    test(new HostCommandBridge(profile)) { dut =>
      // Given
      pokeWork(dut)
      dut.io.work.valid.poke(true.B)
      dut.io.instruction.ready.poke(true.B)
      dut.io.loopMetadata.ready.poke(true.B)
      dut.io.controllerBusy.poke(false.B)
      dut.io.done.ready.poke(false.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)

      // When
      dut.clock.step(expected.size)

      // Then
      dut.io.done.valid.expect(false.B)
      dut.io.active.valid.expect(true.B)
      dut.clock.step(2)
      dut.io.done.valid.expect(false.B)

      dut.io.controllerBusy.poke(true.B)
      dut.clock.step()
      dut.io.done.valid.expect(false.B)
      dut.io.controllerBusy.poke(false.B)
      dut.clock.step()
      dut.io.done.valid.expect(true.B)
      dut.io.done.bits.expect("hde".U)
      dut.io.logicalDone.valid.expect(true.B)
      dut.io.logicalDone.bits.expect("hde".U)
      dut.io.active.valid.expect(true.B)
      dut.io.active.bits.scaleBase.expect("h1234".U)
      dut.io.active.bits.scaleBackingAddress.expect("hddddeeeeffff0000".U)
      dut.io.active.bits.fragmentBase.expect("h789a".U)

      dut.clock.step(2)
      dut.io.done.valid.expect(true.B)
      dut.io.done.bits.expect("hde".U)
      dut.io.active.valid.expect(true.B)

      dut.io.done.ready.poke(true.B)
      dut.clock.step()
      dut.io.done.valid.expect(false.B)
      dut.io.logicalDone.valid.expect(false.B)
      dut.io.active.valid.expect(false.B)
      dut.io.work.ready.expect(true.B)
    }
  }

  it should "accept only complete raw blocks up to K32 and snapshot the result domain" in {
    for (dim <- Seq(16, 32, 64)) {
      test(new HostCommandBridge(ResolvedProfile(4, 4, dim))) { dut =>
        pokeWork(dut)
        dut.io.instruction.ready.poke(false.B)
        dut.io.loopMetadata.ready.poke(true.B)
        dut.io.controllerBusy.poke(false.B)
        dut.io.done.ready.poke(false.B)
        dut.io.work.valid.poke(true.B)
        dut.io.work.bits.rmdRaw.poke(true.B)
        dut.io.work.bits.fragmentBase.poke(0.U)
        dut.io.work.bits.accumulate.poke(false.B)
        val maxK = (32 + dim - 1) / dim
        dut.io.work.bits.maxK.poke(maxK.U)
        dut.io.work.bits.padK.poke((maxK * dim - 32).U)
        dut.io.work.ready.expect(true.B)
        val tooLarge = (33 + dim - 1) / dim
        dut.io.work.bits.maxK.poke(tooLarge.U)
        dut.io.work.bits.padK.poke((tooLarge * dim - 33).U)
        dut.io.work.ready.expect(false.B)
        dut.io.work.bits.maxK.poke(maxK.U)
        dut.io.work.bits.padK.poke((maxK * dim - 32).U)
        dut.io.work.bits.accumulate.poke(true.B)
        dut.io.work.ready.expect(false.B)
        dut.io.work.bits.accumulate.poke(false.B)
        dut.io.work.bits.fragmentBase.poke(1.U)
        dut.io.work.ready.expect(false.B)
        dut.io.work.bits.fragmentBase.poke(0.U)
        dut.io.work.bits.finalFragment.poke(false.B)
        dut.io.work.ready.expect(false.B)
        dut.io.work.bits.finalFragment.poke(true.B)
        dut.io.work.ready.expect(true.B)
        dut.clock.step()
        dut.io.work.valid.poke(false.B)
        dut.io.work.bits.rmdRaw.poke(false.B)
        dut.io.instruction.ready.poke(true.B)
        dut.clock.step(10)
        dut.io.loopMetadata.valid.expect(true.B)
        dut.io.loopMetadata.bits.rmdRaw.expect(true.B)
        dut.io.loopMetadata.bits.maxK.expect(maxK.U)
        dut.io.loopMetadata.bits.padK.expect((maxK * dim - 32).U)
      }
    }
  }

  it should "pulse logical start and done only at the ends of a multi-loop work" in {
    test(new HostCommandBridge(profile)) { dut =>
      // Given
      pokeWork(dut)
      dut.io.work.bits.finalLoop.poke(false.B)
      dut.io.work.bits.finalFragment.poke(false.B)
      dut.io.work.valid.poke(true.B)
      dut.io.instruction.ready.poke(true.B)
      dut.io.loopMetadata.ready.poke(true.B)
      dut.io.controllerBusy.poke(false.B)
      dut.io.done.ready.poke(false.B)

      // When
      dut.io.start.expect(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.clock.step(expected.size - 1)
      dut.io.instruction.bits.funct.expect(GemminiISA.LOOP_WS)
      dut.io.instruction.bits.rs1.expect(3.U)
      dut.io.instruction.bits.rs2.expect(0.U)
      dut.clock.step()
      dut.io.controllerBusy.poke(true.B)
      dut.clock.step()
      dut.io.controllerBusy.poke(false.B)
      dut.clock.step()

      // Then
      dut.io.done.valid.expect(true.B)
      dut.io.logicalDone.valid.expect(false.B)
      dut.io.done.ready.poke(true.B)
      dut.clock.step()

      pokeWork(dut)
      dut.io.work.bits.firstLoop.poke(false.B)
      dut.io.work.valid.poke(true.B)
      dut.io.start.expect(false.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.io.done.ready.poke(false.B)
      dut.clock.step(expected.size)
      dut.io.controllerBusy.poke(true.B)
      dut.clock.step()
      dut.io.controllerBusy.poke(false.B)
      dut.clock.step()
      dut.io.done.valid.expect(true.B)
      dut.io.logicalDone.valid.expect(true.B)
      dut.io.logicalDone.bits.expect("hde".U)
    }
  }

  it should "queue two compatible loops and emit common configuration only once" in {
    test(new HostCommandBridge(profile)) { dut =>
      dut.io.instruction.ready.poke(false.B)
      dut.io.loopMetadata.ready.poke(true.B)
      dut.io.controllerBusy.poke(false.B)
      dut.io.done.ready.poke(false.B)

      pokeWork(dut)
      dut.io.work.bits.finalLoop.poke(false.B)
      dut.io.work.valid.poke(true.B)
      dut.clock.step()

      pokeWork(dut)
      dut.io.work.bits.firstLoop.poke(false.B)
      dut.io.work.bits.logicalWorkId.poke("hdf".U)
      dut.io.work.bits.workBase.poke("hbd".U)
      dut.io.work.bits.scaleGeneration.poke("h57".U)
      dut.io.work.bits.hostSlot.poke(true.B)
      dut.io.controllerBusy.poke(true.B)
      dut.io.work.ready.expect(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.io.instruction.ready.poke(true.B)

      var commands = Vector.empty[BigInt]
      var metadata = Vector.empty[(BigInt, BigInt)]
      var sawOverlap = false
      var cycles = 0
      while (commands.size < 17 && cycles < 40) {
        if (dut.io.instruction.valid.peek().litToBoolean) {
          commands :+= dut.io.instruction.bits.funct.peek().litValue
        }
        if (dut.io.loopMetadata.valid.peek().litToBoolean) {
          metadata :+= (
            dut.io.loopMetadata.bits.workBase.peek().litValue,
            dut.io.loopMetadata.bits.scaleGeneration.peek().litValue,
          )
        }
        sawOverlap ||= dut.io.overlapIssued.peek().litToBoolean
        dut.clock.step()
        cycles += 1
      }
      assert(commands == expected.map(_._1) ++ expected.drop(5).map(_._1))
      assert(metadata == Seq(BigInt("bc", 16) -> BigInt("56", 16), BigInt("bd", 16) -> BigInt("57", 16)))
      assert(sawOverlap)

      pokeWork(dut)
      dut.io.work.valid.poke(true.B)
      dut.io.work.ready.expect(false.B)
      dut.io.controllerBusy.poke(false.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.clock.step()
      dut.io.done.valid.expect(true.B)
      dut.io.done.bits.expect("hde".U)
      dut.io.completedHostSlot.expect(false.B)
      dut.io.done.ready.poke(true.B)
      dut.clock.step()
      dut.io.done.valid.expect(true.B)
      dut.io.done.bits.expect("hdf".U)
      dut.io.completedHostSlot.expect(true.B)
    }
  }

  it should "wait for a full drain before reconfiguring mismatched strides" in {
    test(new HostCommandBridge(profile)) { dut =>
      dut.io.instruction.ready.poke(false.B)
      dut.io.loopMetadata.ready.poke(true.B)
      dut.io.controllerBusy.poke(false.B)
      dut.io.done.ready.poke(false.B)

      pokeWork(dut)
      dut.io.work.valid.poke(true.B)
      dut.clock.step()
      pokeWork(dut)
      dut.io.work.bits.aStrideBytes.poke("h119".U)
      dut.io.work.bits.hostSlot.poke(true.B)
      dut.io.controllerBusy.poke(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.io.instruction.ready.poke(true.B)

      var firstCommands = 0
      while (firstCommands < 11) {
        if (dut.io.instruction.valid.peek().litToBoolean) firstCommands += 1
        dut.clock.step()
      }
      dut.io.instruction.valid.expect(false.B)
      dut.clock.step(3)
      dut.io.instruction.valid.expect(false.B)

      dut.io.controllerBusy.poke(false.B)
      dut.clock.step()
      dut.io.instruction.valid.expect(true.B)
      dut.io.instruction.bits.funct.expect(GemminiISA.CONFIG_CMD)
      var secondCommands = 0
      while (secondCommands < 11) {
        if (dut.io.instruction.valid.peek().litToBoolean) secondCommands += 1
        dut.clock.step()
      }
      assert(secondCommands == 11)
    }
  }
}
