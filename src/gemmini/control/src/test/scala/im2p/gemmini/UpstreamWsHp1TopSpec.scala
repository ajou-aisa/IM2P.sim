package im2p.gemmini

import chisel3._
import chiseltest._
import chiseltest.simulator.VerilatorBackendAnnotation
import freechips.rocketchip.system.BaseConfig
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import org.scalatest.flatspec.AnyFlatSpec

final class UpstreamWsHp1TopSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "UpstreamWsHp1Top"

  private val selectedProfiles = sys.props.get("im2p.testProfile").fold(ResolvedProfile.supported) { name =>
    ResolvedProfile.supported.filter(_.name == name)
  }
  require(selectedProfiles.nonEmpty, "im2p.testProfile does not select a supported profile")

  selectedProfiles.foreach { profile =>
    it should s"run load WS execute SCU accumulator and delayed store for ${profile.name}" in {
      runProfile(profile)
    }
  }

  private def runProfile(profile: ResolvedProfile): Unit = {
    implicit val parameters: Parameters = new BaseConfig().toInstance
    test(LazyModule(new UpstreamWsHp1Harness(profile)).module)
      .withAnnotations(Seq(VerilatorBackendAnnotation)) { dut =>
      val aBase = BigInt(0x1000)
      val bBase = BigInt(0x2000)
      val cBase = BigInt(0x3000)
      val scaleBacking = BigInt(0x800)
      val spRowBytes = profile.scratchpadRowBytes
      val accRowBytes = profile.accumulatorRowBytes
      val a = Seq(Seq(1, 2, 3, 4), Seq(-1, 2, -3, 4))
      val b = Seq(Seq(1, -1, 2), Seq(2, 1, -2), Seq(0, 3, 1), Seq(-1, 2, 1))
      val expected = Seq(Seq(1, 36, 20), Seq(-1, 4, -20))

      def pack(values: Seq[Int], bits: Int): BigInt = values.zipWithIndex.foldLeft(BigInt(0)) {
        case (result, (value, index)) =>
          result | ((BigInt(value) & ((BigInt(1) << bits) - 1)) << (index * bits))
      }
      def memoryRow(address: BigInt): BigInt = {
        if (address == scaleBacking) {
          pack(Seq(0, 1, 2) ++ Seq.fill(profile.dim - 3)(0), 32)
        } else if (address >= aBase && address < aBase + 2 * spRowBytes) {
          pack(a(((address - aBase) / spRowBytes).toInt) ++ Seq.fill(profile.dim - 4)(0), profile.operandBits)
        } else if (address >= bBase && address < bBase + profile.dim * spRowBytes) {
          val row = ((address - bBase) / spRowBytes).toInt
          pack((if (row < b.size) b(row) else Seq.empty) ++
            Seq.fill(profile.dim - (if (row < b.size) 3 else 0))(0), profile.operandBits)
        } else {
          fail(f"unexpected backing read address 0x$address%x")
        }
      }
      def lane(data: BigInt, index: Int): Int = {
        val raw = ((data >> (index * 32)) & 0xffffffffL).toLong
        if ((raw & 0x80000000L) != 0) (raw - 0x100000000L).toInt else raw.toInt
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

      def waitUntil(label: String)(ready: => Boolean): Unit = {
        var waited = 0
        while (!ready && waited < 100) {
          dut.clock.step()
          waited += 1
        }
        assert(ready, s"$label stalled: busy=${dut.io.busy.peek().litValue} error=${dut.io.error.peek().litValue}")
      }

      val work = dut.io.work.bits
      work.maxI.poke(1.U)
      work.maxJ.poke(1.U)
      work.maxK.poke(1.U)
      work.padI.poke((profile.dim - 2).U)
      work.padJ.poke((profile.dim - 3).U)
      work.padK.poke((profile.dim - 4).U)
      work.aAddress.poke(aBase.U)
      work.bAddress.poke(bBase.U)
      work.cAddress.poke(cBase.U)
      work.scaleBackingAddress.poke(scaleBacking.U)
      work.aStrideBytes.poke(spRowBytes.U)
      work.bStrideBytes.poke(spRowBytes.U)
      work.cStrideBytes.poke(accRowBytes.U)
      work.scaleBase.poke(0.U)
      work.scaleGeneration.poke(1.U)
      work.fragmentBase.poke(0.U)
      work.workBase.poke(0.U)
      work.accumulate.poke(false.B)
      work.finalFragment.poke(true.B)
      work.firstLoop.poke(true.B)
      work.finalLoop.poke(true.B)
      work.logicalWorkId.poke(7.U)
      work.hostSlot.poke(false.B)
      work.rmdRaw.poke(false.B)
      dut.io.work.valid.poke(true.B)
      waitUntil("work-admission")(dut.io.work.ready.peek().litToBoolean)
      dut.clock.step()
      dut.io.work.valid.poke(false.B)

      final case class PendingRead(id: BigInt, address: BigInt, due: Int)
      final case class PendingWrite(id: BigInt, due: Int)
      var reads = Vector.empty[PendingRead]
      var write = Option.empty[PendingWrite]
      var stored = Map.empty[BigInt, (BigInt, BigInt)]
      var cycle = 0
      var completed = false
      var sawLoad = false
      var sawExecute = false
      var sawStore = false
      var sawContext = false
      var sawRaw = false
      var sawCommit = false

      while (!completed && cycle < 20000) {
        dut.io.readBeat.valid.poke(false.B)
        dut.io.writeCompletion.valid.poke(false.B)
        reads.filter(_.due <= cycle).headOption.foreach { response =>
          dut.io.readBeat.valid.poke(true.B)
          dut.io.readBeat.bits.id.poke(response.id.U)
          dut.io.readBeat.bits.data.poke(memoryRow(response.address).U)
          dut.io.readBeat.bits.last.poke(true.B)
          dut.io.readBeat.bits.error.poke(false.B)
        }
        write.filter(_.due <= cycle).foreach { response =>
          dut.io.writeCompletion.valid.poke(true.B)
          dut.io.writeCompletion.bits.id.poke(response.id.U)
          dut.io.writeCompletion.bits.error.poke(false.B)
        }

        if (dut.io.readRequest.valid.peek().litToBoolean) {
          reads :+= PendingRead(
            dut.io.readRequest.bits.id.peek().litValue,
            dut.io.readRequest.bits.address.peek().litValue,
            cycle + 2 + (dut.io.readRequest.bits.id.peek().litValue.toInt & 1),
          )
        }
        if (dut.io.readBeat.valid.peek().litToBoolean && dut.io.readBeat.ready.peek().litToBoolean) {
          reads = reads.filterNot(_.id == dut.io.readBeat.bits.id.peek().litValue)
        }
        if (dut.io.writeRequest.valid.peek().litToBoolean) {
          val address = dut.io.writeRequest.bits.address.peek().litValue
          stored += address -> (
            dut.io.writeRequest.bits.data.peek().litValue,
            dut.io.writeRequest.bits.mask.peek().litValue,
          )
          write = Some(PendingWrite(dut.io.writeRequest.bits.id.peek().litValue, cycle + 7))
        }
        if (dut.io.writeCompletion.valid.peek().litToBoolean && dut.io.writeCompletion.ready.peek().litToBoolean) {
          write = None
        }
        if (write.nonEmpty) dut.io.logicalDone.valid.expect(false.B)
        dut.io.error.expect(false.B)
        sawLoad ||= dut.io.events.loadIssued.peek().litToBoolean
        sawExecute ||= dut.io.events.executeIssued.peek().litToBoolean
        sawStore ||= dut.io.events.storeIssued.peek().litToBoolean
        sawContext ||= dut.io.events.outputContextIssued.peek().litToBoolean
        sawRaw ||= dut.io.events.rawRow.peek().litToBoolean
        sawCommit ||= dut.io.events.accumulatorCommitted.peek().litToBoolean
        completed = dut.io.logicalDone.valid.peek().litToBoolean
        if (completed) dut.io.logicalDone.bits.expect(7.U)
        if (!completed && cycle == 2000) {
          fail(
            s"runtime stalled: reads=${dut.io.loadRequests.peek().litValue}/${dut.io.loadResponses.peek().litValue} " +
              s"stores=${dut.io.storeRequests.peek().litValue}/${dut.io.storeResponses.peek().litValue} " +
              s"pendingReads=${reads.size} pendingWrite=${write.nonEmpty} " +
              s"control=${dut.io.controllerBusy.peek().litValue} loop=${dut.io.loopBusy.peek().litValue} " +
              s"load=${dut.io.loadBusy.peek().litValue} execute=${dut.io.executeBusy.peek().litValue} " +
              s"store=${dut.io.storeBusy.peek().litValue} memoryDrained=${dut.io.memoryDrained.peek().litValue} " +
              s"writebackDrained=${dut.io.writebackDrained.peek().litValue} " +
              s"events=$sawLoad/$sawExecute/$sawStore/$sawContext/$sawRaw/$sawCommit",
          )
        }
        dut.clock.step()
        cycle += 1
      }

      assert(completed, "integrated controller did not complete")
      assert(sawLoad && sawExecute && sawStore && sawContext && sawRaw && sawCommit)
      assert(stored.keySet == Set(cBase, cBase + accRowBytes))
      for ((row, values) <- expected.zipWithIndex) {
        val (data, mask) = stored(cBase + values * accRowBytes)
        assert((0 until 3).map(lane(data, _)) == row)
        assert(mask == 0xfff)
      }
      dut.io.loadRequests.expect((profile.dim + 2).U)
      dut.io.loadResponses.expect((profile.dim + 2).U)
      dut.io.storeRequests.expect(2.U)
      dut.io.storeResponses.expect(2.U)
      val expectedReadBytes = 2 * ((4 * profile.operandBits + 7) / 8) +
        profile.dim * ((3 * profile.operandBits + 7) / 8)
      dut.io.readBytes.expect(expectedReadBytes.U)
      dut.io.writeBytes.expect(24.U)
      dut.io.scaleReadRequests.expect(1.U)
      dut.io.scaleReadResponses.expect(1.U)
      dut.io.scaleReadBytes.expect((profile.dim * 4).U)
      dut.io.measurementValid.expect(true.B)
      assert(dut.io.doneCycle.peek().litValue > dut.io.startCycle.peek().litValue)
    }
  }
}
