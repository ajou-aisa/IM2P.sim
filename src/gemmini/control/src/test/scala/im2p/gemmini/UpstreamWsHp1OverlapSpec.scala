package im2p.gemmini

import chisel3._
import chiseltest._
import chiseltest.simulator.VerilatorBackendAnnotation
import freechips.rocketchip.system.BaseConfig
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import org.scalatest.flatspec.AnyFlatSpec

final class UpstreamWsHp1OverlapSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "UpstreamWsHp1Top overlap"

  it should "overlap two original WS loops and retain host slots until scale release" in {
    val profile = ResolvedProfile(8, 8, 16)
    implicit val parameters: Parameters = new BaseConfig().toInstance
    test(LazyModule(new UpstreamWsHp1Harness(profile)).module)
      .withAnnotations(Seq(VerilatorBackendAnnotation)) { dut =>
      val spBytes = profile.scratchpadRowBytes
      val accBytes = profile.accumulatorRowBytes
      val aBases = Seq(BigInt(0x1000), BigInt(0x4000))
      val bBases = Seq(BigInt(0x2000), BigInt(0x5000))
      val cBases = Seq(BigInt(0x3000), BigInt(0x6000))
      val scaleBases = Seq(BigInt(0x800), BigInt(0x900))
      val a = Seq(Seq(1, 2, 3, 4), Seq(-1, 2, -3, 4))
      val b = Seq(Seq(1, -1, 2), Seq(2, 1, -2), Seq(0, 3, 1), Seq(-1, 2, 1))
      val expected = Seq(
        Seq(Seq(1, 18, 5), Seq(-1, 2, -5)),
        Seq(Seq(2, 36, 10), Seq(-2, 4, -10)),
      )

      def pack(values: Seq[Int], bits: Int): BigInt =
        values.zipWithIndex.foldLeft(BigInt(0)) { case (result, (value, index)) =>
          result | ((BigInt(value) & ((BigInt(1) << bits) - 1)) << (index * bits))
        }
      def memoryRow(address: BigInt): BigInt = {
        scaleBases.indexOf(address) match {
          case index if index >= 0 =>
            pack(Seq.fill(3)(index) ++ Seq.fill(profile.dim - 3)(0), 32)
          case _ =>
            aBases.zip(bBases).iterator.zipWithIndex.collectFirst {
              case ((aBase, _), _) if address >= aBase && address < aBase + 2 * spBytes =>
                val row = ((address - aBase) / spBytes).toInt
                pack(a(row) ++ Seq.fill(profile.dim - 4)(0), profile.operandBits)
              case ((_, bBase), _) if address >= bBase && address < bBase + profile.dim * spBytes =>
                val row = ((address - bBase) / spBytes).toInt
                val values = if (row < b.size) b(row) else Seq.empty
                pack(values ++ Seq.fill(profile.dim - values.size)(0), profile.operandBits)
            }.getOrElse(fail(f"unexpected backing read address 0x$address%x"))
        }
      }
      def signed(data: BigInt, lane: Int): Int = {
        val raw = ((data >> (lane * 32)) & 0xffffffffL).toLong
        if ((raw & 0x80000000L) != 0) (raw - 0x100000000L).toInt else raw.toInt
      }
      def pokeWork(index: Int, slot: Boolean, scaleAddress: Int): Unit = {
        val work = dut.io.work.bits
        work.maxI.poke(1.U)
        work.maxJ.poke(1.U)
        work.maxK.poke(1.U)
        work.padI.poke(14.U)
        work.padJ.poke(13.U)
        work.padK.poke(12.U)
        work.aAddress.poke(aBases(index).U)
        work.bAddress.poke(bBases(index).U)
        work.cAddress.poke(cBases(index).U)
        work.scaleBackingAddress.poke(scaleBases(index).U)
        work.aStrideBytes.poke(spBytes.U)
        work.bStrideBytes.poke(spBytes.U)
        work.cStrideBytes.poke(accBytes.U)
        work.scaleBase.poke(scaleAddress.U)
        work.scaleGeneration.poke((index + 1).U)
        work.fragmentBase.poke(0.U)
        work.workBase.poke((index * 4).U)
        work.accumulate.poke(false.B)
        work.finalFragment.poke(true.B)
        work.firstLoop.poke((index == 0).B)
        work.finalLoop.poke((index == 1).B)
        work.logicalWorkId.poke((20 + index).U)
        work.hostSlot.poke(slot.B)
        work.rmdRaw.poke(false.B)
      }

      dut.io.work.valid.poke(false.B)
      dut.io.loopDone.ready.poke(true.B)
      dut.io.scaleRelease.valid.poke(false.B)
      dut.io.readRequest.ready.poke(true.B)
      dut.io.readBeat.valid.poke(false.B)
      dut.io.readBeat.bits.id.poke(0.U)
      dut.io.readBeat.bits.data.poke(0.U)
      dut.io.readBeat.bits.last.poke(false.B)
      dut.io.readBeat.bits.error.poke(false.B)
      dut.io.writeRequest.ready.poke(true.B)
      dut.io.writeCompletion.valid.poke(false.B)
      dut.io.writeCompletion.bits.id.poke(0.U)
      dut.io.writeCompletion.bits.error.poke(false.B)
      dut.reset.poke(true.B)
      dut.clock.step(2)
      dut.reset.poke(false.B)
      dut.clock.step(3)

      pokeWork(0, slot = false, scaleAddress = 0)
      dut.io.work.valid.poke(true.B)
      dut.io.work.ready.expect(true.B)
      dut.clock.step()
      pokeWork(1, slot = true, scaleAddress = 0)
      dut.io.work.ready.expect(false.B)
      pokeWork(1, slot = true, scaleAddress = 1)
      dut.io.work.ready.expect(true.B)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)

      final case class Read(id: BigInt, address: BigInt, due: Int)
      final case class Write(id: BigInt, due: Int)
      var reads = Vector.empty[Read]
      var write = Option.empty[Write]
      var stored = Map.empty[BigInt, BigInt]
      var cycle = 0
      var loopDone = 0
      var outputDone = 0
      var logicalDone = false
      var sawOverlap = false
      var sawLoadExecuteOverlap = false
      while (!logicalDone && cycle < 20000) {
        dut.io.readBeat.valid.poke(false.B)
        dut.io.writeCompletion.valid.poke(false.B)
        reads.filter(_.due <= cycle).sortBy(read => -read.id).headOption.foreach { read =>
          dut.io.readBeat.valid.poke(true.B)
          dut.io.readBeat.bits.id.poke(read.id.U)
          dut.io.readBeat.bits.data.poke(memoryRow(read.address).U)
          dut.io.readBeat.bits.last.poke(true.B)
          dut.io.readBeat.bits.error.poke(false.B)
        }
        write.filter(_.due <= cycle).foreach { pending =>
          dut.io.writeCompletion.valid.poke(true.B)
          dut.io.writeCompletion.bits.id.poke(pending.id.U)
        }
        if (dut.io.readRequest.valid.peek().litToBoolean) {
          val id = dut.io.readRequest.bits.id.peek().litValue
          reads :+= Read(id, dut.io.readRequest.bits.address.peek().litValue, cycle + 2 + (id & 1).toInt)
        }
        if (dut.io.readBeat.valid.peek().litToBoolean && dut.io.readBeat.ready.peek().litToBoolean) {
          reads = reads.filterNot(_.id == dut.io.readBeat.bits.id.peek().litValue)
        }
        if (dut.io.writeRequest.valid.peek().litToBoolean) {
          stored += dut.io.writeRequest.bits.address.peek().litValue -> dut.io.writeRequest.bits.data.peek().litValue
          write = Some(Write(dut.io.writeRequest.bits.id.peek().litValue, cycle + 11))
        }
        if (dut.io.writeCompletion.valid.peek().litToBoolean && dut.io.writeCompletion.ready.peek().litToBoolean) {
          write = None
        }
        if (write.nonEmpty) dut.io.logicalDone.valid.expect(false.B)
        if (dut.io.loopDone.valid.peek().litToBoolean) loopDone += 1
        if (dut.io.outputCompleted.valid.peek().litToBoolean) outputDone += 1
        sawOverlap ||= dut.io.overlapLoopIssued.peek().litToBoolean
        sawLoadExecuteOverlap ||= dut.io.events.loadExecuteOverlap.peek().litToBoolean
        logicalDone = dut.io.logicalDone.valid.peek().litToBoolean
        dut.io.error.expect(false.B)
        dut.clock.step()
        cycle += 1
      }

      assert(logicalDone && loopDone == 2 && outputDone == 2 && sawOverlap && sawLoadExecuteOverlap)
      for (index <- 0 until 2; row <- 0 until 2) {
        val data = stored(cBases(index) + row * accBytes)
        assert((0 until 3).map(signed(data, _)) == expected(index)(row))
      }

      pokeWork(0, slot = false, scaleAddress = 2)
      dut.io.work.ready.expect(false.B)
      for (column <- 0 until profile.dim) {
        dut.io.scaleRelease.bits.column.poke(column.U)
        dut.io.scaleRelease.bits.address.poke(0.U)
        dut.io.scaleRelease.bits.generation.poke(1.U)
        dut.io.scaleRelease.valid.poke(true.B)
        while (!dut.io.scaleRelease.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        dut.io.scaleRelease.valid.poke(false.B)
        if (column + 1 < profile.dim) dut.io.work.ready.expect(false.B)
      }
      dut.clock.step()
      dut.io.work.ready.expect(true.B)
      for (column <- 0 until profile.dim) {
        dut.io.scaleRelease.bits.column.poke(column.U)
        dut.io.scaleRelease.bits.address.poke(1.U)
        dut.io.scaleRelease.bits.generation.poke(2.U)
        dut.io.scaleRelease.valid.poke(true.B)
        while (!dut.io.scaleRelease.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
      }
      dut.io.scaleRelease.valid.poke(false.B)
      dut.clock.step(2)
      dut.io.busy.expect(false.B)
    }
  }
}
