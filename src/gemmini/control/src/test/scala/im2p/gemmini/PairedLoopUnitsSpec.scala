package im2p.gemmini

import chisel3._
import chiseltest._
import chiseltest.simulator.VerilatorBackendAnnotation
import freechips.rocketchip.system.BaseConfig
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.LazyModule
import org.scalatest.flatspec.AnyFlatSpec

final class PairedLoopUnitsSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "PairScheduler in UpstreamWsHp1Top"

  private val selectedProfiles = sys.props.get("im2p.testProfile").fold(ResolvedProfile.supported) { name =>
    ResolvedProfile.supported.filter(_.name == name)
  }
  require(selectedProfiles.nonEmpty, "im2p.testProfile does not select a supported profile")

  selectedProfiles.foreach { profile =>
    it should s"finish lone paired loops without protocol errors and keep Main exact for ${profile.name}" in {
      run(profile)
    }
  }

  // One residual loop per case: surviving-K mask, residual row groups, rows missing from the last group.
  private final case class Residual(mask: Long, groups: Int, padI: Int)

  private def run(profile: ResolvedProfile): Unit = {
    implicit val parameters: Parameters = new BaseConfig().toInstance
    test(LazyModule(new UpstreamWsHp1Harness(profile)).module)
      .withAnnotations(Seq(VerilatorBackendAnnotation)) { dut =>
      val dim = profile.dim
      val spRowBytes = profile.scratchpadRowBytes
      val accRowBytes = profile.accumulatorRowBytes
      val a = Seq(Seq(1, 2, 3, 4), Seq(-1, 2, -3, 4))
      val b = Seq(Seq(1, -1, 2), Seq(2, 1, -2), Seq(0, 3, 1), Seq(-1, 2, 1))
      val expected = Seq(Seq(1, 36, 20), Seq(-1, 4, -20))
      val physicalRows = PhysicalAccumulator.rows(profile)
      val workEntries = PhysicalAccumulator(profile).workEntries
      val accTop = physicalRows / 2
      val chunkRows = math.min(dim, 32)
      // Masks of 1, 16, 17 and 32 bits (both chunk counts at D16); the last case issues more
      // residual commands than the RS has ex entries, so injection waits on credits.
      val cases = Seq(
        Option.empty[Residual],
        Some(Residual(0x1L, 1, dim - 1)),
        Some(Residual(0xffffL, 2, dim - 3)),
        Some(Residual(0x1ffffL, 1, 0)),
        Some(Residual(0xffffffffL, 3, dim - 5)),
        Some(Residual(0xffffffffL, math.min(12, accTop / dim - 1), 0)),
      )

      def pack(values: Seq[Int], bits: Int): BigInt = values.zipWithIndex.foldLeft(BigInt(0)) {
        case (result, (value, index)) => result | ((BigInt(value) & ((BigInt(1) << bits) - 1)) << (index * bits))
      }
      def bases(index: Int): (BigInt, BigInt, BigInt, BigInt) =
        (BigInt(0x10000) * (index + 1), BigInt(0x10000) * (index + 1) + 0x4000,
          BigInt(0x10000) * (index + 1) + 0x8000, BigInt(0x10000) * (index + 1) + 0xc000)
      def memoryRow(index: Int, address: BigInt): BigInt = {
        val (aBase, bBase, _, scaleBacking) = bases(index)
        if (address == scaleBacking) {
          pack(Seq(0, 1, 2) ++ Seq.fill(dim - 3)(0), 32)
        } else if (address >= aBase && address < aBase + 2 * spRowBytes) {
          pack(a(((address - aBase) / spRowBytes).toInt) ++ Seq.fill(dim - 4)(0), profile.operandBits)
        } else if (address >= bBase && address < bBase + dim * spRowBytes) {
          val row = ((address - bBase) / spRowBytes).toInt
          pack((if (row < b.size) b(row) else Seq.empty) ++
            Seq.fill(dim - (if (row < b.size) 3 else 0))(0), profile.operandBits)
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
      dut.io.writeRequest.ready.poke(true.B)
      dut.io.writeCompletion.valid.poke(false.B)
      dut.io.writeCompletion.bits.error.poke(false.B)
      dut.reset.poke(true.B)
      dut.clock.step(2)
      dut.reset.poke(false.B)
      dut.clock.step(3)

      dut.io.physicalAccumulatorRows.expect(physicalRows.U)
      dut.io.workEntries.expect(workEntries.U)

      for ((residual, index) <- cases.zipWithIndex) {
        val slot = index % 2
        val workBase = slot * workEntries / 2
        val generation = index + 1
        val (aBase, bBase, cBase, scaleBacking) = bases(index)
        val work = dut.io.work.bits
        work.maxI.poke(1.U)
        work.maxJ.poke(1.U)
        work.maxK.poke(1.U)
        work.padI.poke((dim - 2).U)
        work.padJ.poke((dim - 3).U)
        work.padK.poke((dim - 4).U)
        work.aAddress.poke(aBase.U)
        work.bAddress.poke(bBase.U)
        work.cAddress.poke(cBase.U)
        work.scaleBackingAddress.poke(scaleBacking.U)
        work.aStrideBytes.poke(spRowBytes.U)
        work.bStrideBytes.poke(spRowBytes.U)
        work.cStrideBytes.poke(accRowBytes.U)
        work.scaleBase.poke((slot * 128).U)
        work.scaleGeneration.poke(generation.U)
        work.fragmentBase.poke(0.U)
        work.workBase.poke(workBase.U)
        work.accumulate.poke(false.B)
        work.finalFragment.poke(true.B)
        work.firstLoop.poke(true.B)
        work.finalLoop.poke(true.B)
        work.logicalWorkId.poke(index.U)
        work.hostSlot.poke((slot == 1).B)
        work.rmdRaw.poke(false.B)
        work.paired.poke(residual.nonEmpty.B)
        work.residualMask.poke(residual.fold(0L)(_.mask).U)
        work.residualCompactBegin.poke(0.U)
        work.residualGroups.poke(residual.fold(0)(_.groups).U)
        work.residualPadI.poke(residual.fold(0)(_.padI).U)
        work.residualAccTop.poke(accTop.U)
        work.residualWorkOffset.poke(1.U)
        work.residualFirstRun.poke(true.B)
        work.residualFinalRun.poke(true.B)
        dut.io.work.valid.poke(true.B)
        var waited = 0
        while (!dut.io.work.ready.peek().litToBoolean && waited < 100) { dut.clock.step(); waited += 1 }
        dut.io.work.ready.expect(true.B)
        dut.clock.step()
        dut.io.work.valid.poke(false.B)

        final case class Read(id: BigInt, address: BigInt, due: Int)
        final case class Write(id: BigInt, due: Int)
        var reads = Vector.empty[Read]
        var write = Option.empty[Write]
        var stored = Map.empty[BigInt, BigInt]
        var traces = Vector.empty[Seq[BigInt]]
        var outputs = Vector.empty[BigInt]
        var cycle = 0
        var logicalDone = false
        var done = false
        while (!done && cycle < 20000) {
          dut.io.readBeat.valid.poke(false.B)
          dut.io.writeCompletion.valid.poke(false.B)
          reads.filter(_.due <= cycle).headOption.foreach { read =>
            dut.io.readBeat.valid.poke(true.B)
            dut.io.readBeat.bits.id.poke(read.id.U)
            dut.io.readBeat.bits.data.poke(memoryRow(index, read.address).U)
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
            write = Some(Write(dut.io.writeRequest.bits.id.peek().litValue, cycle + 7))
          }
          if (dut.io.writeCompletion.valid.peek().litToBoolean && dut.io.writeCompletion.ready.peek().litToBoolean) {
            write = None
          }
          if (dut.io.pairTrace.valid.peek().litToBoolean) {
            val t = dut.io.pairTrace.bits
            traces :+= Seq(t.kind, t.fragmentId, t.accRow, t.workId, t.wRow).map(_.peek().litValue)
          }
          if (dut.io.outputCompleted.valid.peek().litToBoolean) outputs :+= dut.io.outputCompleted.bits.peek().litValue
          dut.io.error.expect(false.B, s"case $index: io.error at cycle $cycle")
          if (dut.io.logicalDone.valid.peek().litToBoolean) {
            assert(write.isEmpty, s"case $index: logical done before the final store completed")
            logicalDone = true
          }
          // Residual work IDs may retire after logical done; wait for the tracker to drain.
          done = logicalDone && dut.io.writebackDrained.peek().litToBoolean
          dut.clock.step()
          cycle += 1
        }
        assert(done, s"case $index did not complete")
        for ((row, values) <- expected.zipWithIndex) {
          assert((0 until 3).map(lane(stored(cBase + values * accRowBytes), _)) == row, s"case $index Main row $values")
        }

        // Loop-tail order: Main's single µT, then residual µTs I fastest (J = 1), then chunk.
        val main = Seq(Seq[BigInt](0, 0, 0, workBase, 0))
        val tail = residual.toSeq.flatMap { r =>
          val bits = (0 until 32).filter(bit => ((r.mask >> bit) & 1L) == 1L)
          bits.grouped(chunkRows).zipWithIndex.toSeq.flatMap { case (chunkBits, chunk) =>
            (0 until r.groups).flatMap { group =>
              Seq(Seq[BigInt](1, chunk, accTop - (group + 1) * dim, workBase + 1 + group, 0)) ++
                chunkBits.map(bit => Seq[BigInt](2, chunk, accTop - (group + 1) * dim, workBase + 1 + group, bit))
            }
          }
        }
        assert(traces == main ++ tail, s"case $index trace")
        val residualIds = residual.toSeq.flatMap(r => (0 until r.groups).map(group => BigInt(workBase + 1 + group)))
        assert(outputs.sorted == (BigInt(workBase) +: residualIds).sorted, s"case $index completed work IDs")

        for (column <- 0 until dim) {
          dut.io.scaleRelease.bits.column.poke(column.U)
          dut.io.scaleRelease.bits.address.poke((slot * 128).U)
          dut.io.scaleRelease.bits.generation.poke(generation.U)
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
}
