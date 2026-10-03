package im2p.gemmini

import chisel3._
import chiseltest._
import chiseltest.simulator.VerilatorBackendAnnotation
import freechips.rocketchip.system.BaseConfig
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import org.scalatest.flatspec.AnyFlatSpec

final class UpstreamWsHp1FullKSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "UpstreamWsHp1Top full K"

  private val d64Profiles = Seq(ResolvedProfile(4, 4, 64), ResolvedProfile(8, 8, 64))
  private val selectedProfiles = sys.props.get("im2p.testProfile").fold(d64Profiles) { name =>
    d64Profiles.filter(_.name == name)
  }

  selectedProfiles.foreach { profile =>
    it should s"split K64 into two K32 scale domains for ${profile.name}" in run(profile)
  }

  private def run(profile: ResolvedProfile): Unit = {
    implicit val parameters: Parameters = new BaseConfig().toInstance
    test(LazyModule(new UpstreamWsHp1Harness(profile)).module)
      .withAnnotations(Seq(VerilatorBackendAnnotation)) { dut =>
      val spBytes = profile.scratchpadRowBytes
      val accBytes = profile.accumulatorRowBytes
      val a0 = BigInt(0x1000)
      val b0 = BigInt(0x4000)
      val a1 = BigInt(0x8000)
      val b1 = BigInt(0xc000)
      val c = BigInt(0x10000)
      val scale0 = BigInt(0x14000)
      val scale1 = BigInt(0x15000)
      val carriers = Seq(
        Seq(BigInt(0), BigInt(1), BigInt("80000000", 16)),
        Seq(BigInt(1), BigInt(0), BigInt(2)),
      )

      def pack(values: Seq[Int]): BigInt = values.zipWithIndex.foldLeft(BigInt(0)) {
        case (result, (value, index)) =>
          result | ((BigInt(value) & ((BigInt(1) << profile.operandBits) - 1)) <<
            (index * profile.operandBits))
      }
      def row(address: BigInt): BigInt = {
        if (address == scale0 || address == scale1) {
          val values = carriers(if (address == scale0) 0 else 1)
          return values.zipWithIndex.foldLeft(BigInt(0)) { case (bits, (carrier, column)) =>
            bits | (carrier << (column * 32))
          }
        }
        val source = Seq((a0, false, 0), (b0, true, 0), (a1, false, 1), (b1, true, 1))
          .find { case (base, _, _) => address >= base && address < base + profile.dim * spBytes }
          .getOrElse(fail(f"unexpected read address 0x$address%x"))
        val (base, weights, block) = source
        val index = ((address - base) / spBytes).toInt
        val values = if (weights) {
          if (index >= 32) Seq.empty
          else if (block == 0) Seq(1, 1, 1)
          else Seq(1, 2, -1)
        } else if (index < 2) {
          Seq.fill(32)(1)
        } else Seq.empty
        pack(values ++ Seq.fill(profile.dim - values.size)(0))
      }
      def signed(data: BigInt, lane: Int): Int = {
        val raw = ((data >> (lane * 32)) & 0xffffffffL).toLong
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

      final case class PendingRead(id: BigInt, address: BigInt, due: Int)
      final case class PendingWrite(id: BigInt, due: Int)
      var reads = Vector.empty[PendingRead]
      var write = Option.empty[PendingWrite]
      var stored = Map.empty[BigInt, BigInt]
      var outputCompletions = 0
      var cycle = 0

      def submit(
        aBase: BigInt,
        bBase: BigInt,
        cBase: BigInt,
        scaleBacking: BigInt,
        fragment: Int,
        first: Boolean,
      ): Unit = {
        val work = dut.io.work.bits
        work.maxI.poke(1.U)
        work.maxJ.poke(1.U)
        work.maxK.poke(1.U)
        work.padI.poke(62.U)
        work.padJ.poke(61.U)
        work.padK.poke(32.U)
        work.aAddress.poke(aBase.U)
        work.bAddress.poke(bBase.U)
        work.cAddress.poke(cBase.U)
        work.scaleBackingAddress.poke(scaleBacking.U)
        work.aStrideBytes.poke(spBytes.U)
        work.bStrideBytes.poke(spBytes.U)
        work.cStrideBytes.poke(accBytes.U)
        work.scaleBase.poke(0.U)
        work.scaleGeneration.poke((if (first) 1 else 2).U)
        work.fragmentBase.poke(fragment.U)
        work.workBase.poke(0.U)
        work.accumulate.poke((!first).B)
        work.finalFragment.poke((!first).B)
        work.firstLoop.poke(first.B)
        work.finalLoop.poke((!first).B)
        work.logicalWorkId.poke(11.U)
        // Both K32 loops belong to one production stripe/host slot. The scale
        // cache row is reused only after generation-1 ownership is released.
        work.hostSlot.poke(false.B)
        work.rmdRaw.poke(false.B)
        dut.io.work.valid.poke(true.B)
        while (!dut.io.work.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        dut.io.work.valid.poke(false.B)
      }

      def runLoop(expectLogicalDone: Boolean): Unit = {
        var loopDone = false
        while (!loopDone && cycle < 10000) {
          dut.io.readBeat.valid.poke(false.B)
          dut.io.writeCompletion.valid.poke(false.B)
          reads.filter(_.due <= cycle).headOption.foreach { response =>
            dut.io.readBeat.valid.poke(true.B)
            dut.io.readBeat.bits.id.poke(response.id.U)
            dut.io.readBeat.bits.data.poke(row(response.address).U)
            dut.io.readBeat.bits.last.poke(true.B)
            dut.io.readBeat.bits.error.poke(false.B)
          }
          write.filter(_.due <= cycle).foreach { response =>
            dut.io.writeCompletion.valid.poke(true.B)
            dut.io.writeCompletion.bits.id.poke(response.id.U)
          }
          if (dut.io.readRequest.valid.peek().litToBoolean) {
            reads :+= PendingRead(dut.io.readRequest.bits.id.peek().litValue,
              dut.io.readRequest.bits.address.peek().litValue, cycle + 2)
          }
          if (dut.io.readBeat.valid.peek().litToBoolean && dut.io.readBeat.ready.peek().litToBoolean) {
            reads = reads.filterNot(_.id == dut.io.readBeat.bits.id.peek().litValue)
          }
          if (dut.io.writeRequest.valid.peek().litToBoolean) {
            stored += dut.io.writeRequest.bits.address.peek().litValue -> dut.io.writeRequest.bits.data.peek().litValue
            write = Some(PendingWrite(dut.io.writeRequest.bits.id.peek().litValue, cycle + 10))
          }
          if (dut.io.writeCompletion.valid.peek().litToBoolean && dut.io.writeCompletion.ready.peek().litToBoolean) {
            write = None
          }
          if (write.nonEmpty) dut.io.logicalDone.valid.expect(false.B)
          if (dut.io.outputCompleted.valid.peek().litToBoolean) outputCompletions += 1
          dut.io.error.expect(false.B)
          loopDone = dut.io.loopDone.valid.peek().litToBoolean
          if (loopDone) dut.io.logicalDone.valid.expect(expectLogicalDone.B)
          dut.clock.step()
          cycle += 1
        }
        assert(loopDone, "controller loop did not complete")
      }

      submit(a0, b0, 0, scale0, fragment = 0, first = true)
      runLoop(expectLogicalDone = false)
      assert(stored.isEmpty && outputCompletions == 0)
      dut.io.busy.expect(true.B)

      // Production releases every physical scale lane after a loop completes,
      // then reuses the same local row under a fresh generation.
      for (column <- 0 until profile.dim) {
        dut.io.scaleRelease.bits.column.poke(column.U)
        dut.io.scaleRelease.bits.address.poke(0.U)
        dut.io.scaleRelease.bits.generation.poke(1.U)
        dut.io.scaleRelease.valid.poke(true.B)
        while (!dut.io.scaleRelease.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
      }
      dut.io.scaleRelease.valid.poke(false.B)
      dut.clock.step()

      submit(a1, b1, c, scale1, fragment = 1, first = false)
      runLoop(expectLogicalDone = true)

      assert(stored.keySet == Set(c, c + accBytes))
      for (data <- stored.values) assert((0 until 3).map(signed(data, _)) == Seq(96, 128, -128))
      assert(outputCompletions == 1)
      dut.io.storeRequests.expect(2.U)
      dut.io.storeResponses.expect(2.U)
      dut.io.writeBytes.expect(24.U)
      dut.io.scaleReadRequests.expect(2.U)
      dut.io.scaleReadResponses.expect(2.U)
      dut.io.scaleReadBytes.expect((2 * profile.dim * 4).U)
      dut.io.measurementValid.expect(true.B)
    }
  }
}
