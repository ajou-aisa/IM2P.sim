package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

final class ScaleBackingLoaderSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "ScaleBackingLoader"

  private val profile = ResolvedProfile(8, 8, 16)

  private def pokeWork(dut: ScaleBackingLoader, maxJ: Int = 3, maxK: Int = 3): Unit = {
    val work = dut.io.work.bits
    work.maxI.poke(1.U)
    work.maxJ.poke(maxJ.U)
    work.maxK.poke(maxK.U)
    work.padI.poke(0.U)
    work.padJ.poke(0.U)
    work.padK.poke(0.U)
    work.aAddress.poke(0x1000.U)
    work.bAddress.poke(0x2000.U)
    work.cAddress.poke(0x3000.U)
    work.scaleBackingAddress.poke(0x4000.U)
    work.aStrideBytes.poke(16.U)
    work.bStrideBytes.poke(16.U)
    work.cStrideBytes.poke(64.U)
    work.scaleBase.poke(4.U)
    work.scaleGeneration.poke(7.U)
    work.fragmentBase.poke(1.U)
    work.workBase.poke(0.U)
    work.accumulate.poke(false.B)
    work.finalFragment.poke(true.B)
    work.firstLoop.poke(true.B)
    work.finalLoop.poke(true.B)
    work.logicalWorkId.poke(9.U)
    work.hostSlot.poke(false.B)
    work.rmdRaw.poke(false.B)
  }

  private def initialize(dut: ScaleBackingLoader): Unit = {
    dut.io.work.valid.poke(false.B)
    dut.io.loadedWork.ready.poke(false.B)
    dut.io.readRequest.ready.poke(true.B)
    dut.io.readBeat.valid.poke(false.B)
    dut.io.readBeat.bits.id.poke(0.U)
    dut.io.readBeat.bits.data.poke(0.U)
    dut.io.readBeat.bits.last.poke(false.B)
    dut.io.readBeat.bits.error.poke(false.B)
    dut.io.scaleLoad.ready.poke(true.B)
  }

  it should "fetch each required metadata row and load every carrier before releasing work" in {
    test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
      initialize(dut)
      pokeWork(dut)
      dut.io.work.valid.poke(true.B)
      dut.io.work.ready.expect(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)

      // fragment 1 through 3 spans two K32 blocks at D16; each block has maxJ rows.
      for (row <- 0 until 6) {
        dut.io.readRequest.valid.expect(true.B)
        dut.io.readRequest.bits.id.expect(15.U)
        dut.io.readRequest.bits.beats.expect(1.U)
        dut.io.readRequest.bits.address.expect((0x4000 + row * profile.dim * 4).U)
        dut.clock.step()

        val carriers = (0 until profile.dim).map(column => row * 100 + column)
        val packed = carriers.zipWithIndex.foldLeft(BigInt(0)) { case (bits, (carrier, column)) =>
          bits | (BigInt(carrier) << (column * 32))
        }
        dut.io.readBeat.bits.id.poke(15.U)
        dut.io.readBeat.bits.data.poke(packed.U)
        dut.io.readBeat.bits.last.poke(true.B)
        dut.io.readBeat.bits.error.poke(false.B)
        dut.io.readBeat.valid.poke(true.B)
        dut.io.readBeat.ready.expect(true.B)
        dut.clock.step()
        dut.io.readBeat.valid.poke(false.B)

        for ((carrier, column) <- carriers.zipWithIndex) {
          dut.io.scaleLoad.valid.expect(true.B)
          dut.io.scaleLoad.bits.column.expect(column.U)
          dut.io.scaleLoad.bits.address.expect((4 + row).U)
          dut.io.scaleLoad.bits.generation.expect(7.U)
          dut.io.scaleLoad.bits.carrier.expect(carrier.U)
          dut.clock.step()
        }
      }

      dut.io.loadedWork.valid.expect(true.B)
      dut.io.loadedWork.bits.logicalWorkId.expect(9.U)
      dut.io.loadedWork.bits.scaleBackingAddress.expect(0x4000.U)
      dut.io.error.expect(false.B)
      dut.io.requests.expect(6.U)
      dut.io.responses.expect(6.U)
      dut.io.readBytes.expect((6 * profile.dim * 4).U)
      dut.io.loadedWork.ready.poke(true.B)
      dut.clock.step()
      dut.io.work.ready.expect(true.B)
    }
  }

  it should "reuse local scale rows for late dense K blocks while preserving global fragment identity" in {
    test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
      initialize(dut)
      pokeWork(dut, maxJ = 3, maxK = 2)
      dut.io.work.bits.fragmentBase.poke(190.U)
      dut.io.work.bits.accumulate.poke(true.B)
      dut.io.work.valid.poke(true.B)
      dut.io.work.ready.expect(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)

      for (row <- 0 until 3) {
        dut.io.readRequest.valid.expect(true.B)
        dut.io.readRequest.bits.address.expect((0x4000 + row * profile.dim * 4).U)
        dut.clock.step()

        val packed = (0 until profile.dim).foldLeft(BigInt(0)) { (bits, column) =>
          bits | (BigInt(row + column) << (column * 32))
        }
        dut.io.readBeat.bits.id.poke(15.U)
        dut.io.readBeat.bits.data.poke(packed.U)
        dut.io.readBeat.bits.last.poke(true.B)
        dut.io.readBeat.bits.error.poke(false.B)
        dut.io.readBeat.valid.poke(true.B)
        dut.clock.step()
        dut.io.readBeat.valid.poke(false.B)

        for (column <- 0 until profile.dim) {
          dut.io.scaleLoad.valid.expect(true.B)
          dut.io.scaleLoad.bits.address.expect((4 + row).U)
          dut.io.scaleLoad.bits.generation.expect(7.U)
          dut.clock.step()
        }
      }

      dut.io.loadedWork.valid.expect(true.B)
      dut.io.loadedWork.bits.fragmentBase.expect(190.U)
      dut.io.loadedWork.bits.accumulate.expect(true.B)
      dut.io.error.expect(false.B)
    }
  }

  it should "consume an early reserved response and fail closed" in {
    test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
      initialize(dut)
      dut.io.readBeat.bits.id.poke(15.U)
      dut.io.readBeat.bits.last.poke(true.B)
      dut.io.readBeat.valid.poke(true.B)
      dut.io.readBeat.ready.expect(true.B)
      dut.clock.step()
      dut.io.error.expect(true.B)
      dut.io.work.ready.expect(false.B)
      dut.io.loadedWork.valid.expect(false.B)
    }
  }

  it should "normalize hostile scale carriers to identity for a bounded RMD raw block" in {
    test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
      initialize(dut)
      pokeWork(dut, maxJ = 1, maxK = 2)
      dut.io.work.bits.fragmentBase.poke(0.U)
      dut.io.work.bits.rmdRaw.poke(true.B)
      dut.io.work.valid.poke(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.io.work.bits.rmdRaw.poke(false.B)
      dut.io.readRequest.valid.expect(true.B)
      dut.io.readRequest.bits.address.expect(0x4000.U)
      dut.clock.step()
      val hostile = Seq(BigInt(32767), Hp1ScaleEncoding.ZeroCarrier, BigInt("80000001", 16))
      val row = (0 until profile.dim).foldLeft(BigInt(0)) { (bits, column) =>
        bits | (hostile(column % hostile.size) << (column * 32))
      }
      dut.io.readBeat.bits.id.poke(15.U)
      dut.io.readBeat.bits.data.poke(row.U)
      dut.io.readBeat.bits.last.poke(true.B)
      dut.io.readBeat.valid.poke(true.B)
      dut.clock.step()
      dut.io.readBeat.valid.poke(false.B)
      for (column <- 0 until profile.dim) {
        dut.io.scaleLoad.valid.expect(true.B)
        dut.io.scaleLoad.bits.carrier.expect(0.U)
        dut.io.scaleLoad.bits.column.expect(column.U)
        dut.io.scaleLoad.bits.generation.expect(7.U)
        dut.clock.step()
      }
      dut.io.loadedWork.valid.expect(true.B)
      dut.io.loadedWork.bits.rmdRaw.expect(true.B)
      dut.io.requests.expect(1.U)
      dut.io.responses.expect(1.U)
      dut.io.error.expect(false.B)
    }
  }

  it should "fail closed before DMA for cross-block or accumulating raw descriptors" in {
    for (invalid <- Seq("k33", "fragment", "accumulate", "nonfinal")) {
      test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
        initialize(dut)
        pokeWork(dut, maxJ = 1, maxK = 2)
        dut.io.work.bits.fragmentBase.poke(0.U)
        dut.io.work.bits.rmdRaw.poke(true.B)
        invalid match {
          case "k33" =>
            dut.io.work.bits.maxK.poke(3.U)
            dut.io.work.bits.padK.poke(15.U)
          case "fragment" => dut.io.work.bits.fragmentBase.poke(1.U)
          case "accumulate" => dut.io.work.bits.accumulate.poke(true.B)
          case "nonfinal" => dut.io.work.bits.finalFragment.poke(false.B)
        }
        dut.io.work.valid.poke(true.B)
        dut.clock.step()
        dut.io.work.valid.poke(false.B)
        dut.io.error.expect(true.B)
        dut.io.work.ready.expect(false.B)
        dut.io.readRequest.valid.expect(false.B)
        dut.io.loadedWork.valid.expect(false.B)
      }
    }
  }

  it should "reject malformed work, responses, and scale carriers" in {
    test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
      initialize(dut)
      pokeWork(dut, maxK = 0)
      dut.io.work.valid.poke(true.B)
      dut.io.work.ready.expect(true.B)
      dut.clock.step()
      dut.io.error.expect(true.B)
      dut.io.work.ready.expect(false.B)
    }

    test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
      initialize(dut)
      pokeWork(dut)
      dut.io.work.valid.poke(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.io.readRequest.valid.expect(true.B)
      dut.clock.step()
      dut.io.readBeat.bits.id.poke(15.U)
      dut.io.readBeat.bits.last.poke(false.B)
      dut.io.readBeat.bits.error.poke(true.B)
      dut.io.readBeat.valid.poke(true.B)
      dut.io.readBeat.ready.expect(true.B)
      dut.clock.step()
      dut.io.error.expect(true.B)
      dut.io.scaleLoad.valid.expect(false.B)
      dut.io.loadedWork.valid.expect(false.B)
    }

    test(new ScaleBackingLoader(profile, scaleEntries = 64)) { dut =>
      initialize(dut)
      pokeWork(dut)
      dut.io.work.valid.poke(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)
      dut.io.readRequest.valid.expect(true.B)
      dut.clock.step()
      dut.io.readBeat.bits.id.poke(15.U)
      dut.io.readBeat.bits.data.poke(BigInt("80000001", 16).U)
      dut.io.readBeat.bits.last.poke(true.B)
      dut.io.readBeat.valid.poke(true.B)
      dut.clock.step()
      dut.io.readBeat.valid.poke(false.B)
      dut.io.scaleLoad.valid.expect(false.B)
      dut.clock.step()
      dut.io.error.expect(true.B)
      dut.io.loadedWork.valid.expect(false.B)
    }
  }
}
