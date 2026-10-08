package im2p.gemmini

import chisel3._
import chiseltest._
import chiseltest.simulator.VerilatorBackendAnnotation
import freechips.rocketchip.system.BaseConfig
import freechips.rocketchip.tile.{RocketTileParams, TileKey}
import gemmini.GemminiISA._
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
    it should s"hold a residual PRELOAD until its LdR and fail the ldr_ahead assertion when broken for ${profile.name}" in {
      ldrAhead(profile, broken = false)
      assertThrows[ChiselAssertionError] { ldrAhead(profile, broken = true) }
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
      // Main is one whole K32 block (bits of a residual mask must be < its ks); only K 0..3 are nonzero.
      val kBlocks = math.max(1, 32 / dim)
      val kp = kBlocks * dim
      val aStride = kp * profile.operandBits / 8
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
      // Residual A lives in the slot A object after Main's two rows and a two-row nonzero gap.
      def raBase(index: Int): BigInt = bases(index)._1 + 2 * aStride + 2 * spRowBytes
      def bitsOf(r: Residual): Seq[Int] = (0 until 32).filter(bit => ((r.mask >> bit) & 1L) == 1L)
      def chunksOf(r: Residual): Int = (bitsOf(r).size + chunkRows - 1) / chunkRows
      def raRowBytes(r: Residual): Int = chunksOf(r) * spRowBytes
      // Packed residual A (residual_activation_read layout): row R, column chunk c, lane l.
      def raValue(r: Residual, row: Int, chunk: Int, lane: Int): Int = {
        val column = chunk * dim + lane
        if (row >= r.groups * dim - r.padI || column >= bitsOf(r).size) 0 else (row * 3 + chunk * 5 + lane) % 7 - 3
      }
      def memoryRow(index: Int, residual: Option[Residual], address: BigInt, raReads: collection.mutable.Map[BigInt, Int]): BigInt = {
        val (aBase, bBase, _, scaleBacking) = bases(index)
        val raBytes = residual.fold(BigInt(0))(r => BigInt(r.groups * dim * raRowBytes(r)))
        if (address == scaleBacking) {
          pack(Seq(0, 1, 2) ++ Seq.fill(dim - 3)(0), 32)
        } else if (address >= aBase && address < aBase + 2 * aStride) {
          val row = ((address - aBase) / aStride).toInt
          val block = (((address - aBase) % aStride) / spRowBytes).toInt
          pack(if (block == 0) a(row) ++ Seq.fill(dim - 4)(0) else Seq.fill(dim)(0), profile.operandBits)
        } else if (address >= aBase + 2 * aStride && address < raBase(index)) {
          fail(f"backing read in the gap before residual A: 0x$address%x")
        } else if (residual.nonEmpty && address >= raBase(index) && address < raBase(index) + raBytes) {
          val r = residual.get
          val offset = address - raBase(index)
          assert(offset % spRowBytes == 0, f"residual A read 0x$address%x is not a packed row")
          raReads(offset) = raReads.getOrElse(offset, 0) + 1
          val row = (offset / raRowBytes(r)).toInt
          val chunk = ((offset % raRowBytes(r)) / spRowBytes).toInt
          pack((0 until dim).map(lane => raValue(r, row, chunk, lane)), profile.operandBits)
        } else if (address >= bBase && address < bBase + kp * spRowBytes) {
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
        work.maxK.poke(kBlocks.U)
        work.padI.poke((dim - 2).U)
        work.padJ.poke((dim - 3).U)
        work.padK.poke((kp - 32).U)
        work.aAddress.poke(aBase.U)
        work.bAddress.poke(bBase.U)
        work.cAddress.poke(cBase.U)
        work.scaleBackingAddress.poke(scaleBacking.U)
        work.aStrideBytes.poke(aStride.U)
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
        work.residualAAddress.poke(residual.fold(BigInt(0))(_ => raBase(index)).U)
        work.residualAStrideBytes.poke(residual.fold(0)(raRowBytes).U)
        dut.io.work.valid.poke(true.B)
        var waited = 0
        while (!dut.io.work.ready.peek().litToBoolean && waited < 100) { dut.clock.step(); waited += 1 }
        dut.io.work.ready.expect(true.B)
        dut.clock.step()
        dut.io.work.valid.poke(false.B)

        final case class Read(id: BigInt, address: BigInt, due: Int)
        final case class Write(id: BigInt, due: Int)
        val raReads = collection.mutable.Map.empty[BigInt, Int]
        var reads = Vector.empty[Read]
        var write = Option.empty[Write]
        var stored = Map.empty[BigInt, BigInt]
        var traces = Vector.empty[Seq[BigInt]]
        var outputs = Vector.empty[BigInt]
        var gathered = Vector.empty[(Int, BigInt)]
        var meshA = Vector.empty[(Int, BigInt)]
        var meshD = Vector.empty[(Int, BigInt)]
        var cycle = 0
        var logicalDone = false
        var done = false
        while (!done && cycle < 40000) {
          dut.io.readBeat.valid.poke(false.B)
          dut.io.writeCompletion.valid.poke(false.B)
          reads.filter(_.due <= cycle).headOption.foreach { read =>
            dut.io.readBeat.valid.poke(true.B)
            dut.io.readBeat.bits.id.poke(read.id.U)
            dut.io.readBeat.bits.data.poke(memoryRow(index, residual, read.address, raReads).U)
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
          if (dut.io.gatherRead.valid.peek().litToBoolean) {
            gathered :+= (dut.io.gatherRead.bits.row.peek().litValue.toInt, dut.io.gatherRead.bits.relative.peek().litValue)
          }
          if (dut.io.meshA.valid.peek().litToBoolean) {
            meshA :+= (dut.io.meshA.bits.row.peek().litValue.toInt, dut.io.meshA.bits.data.peek().litValue)
          }
          if (dut.io.meshD.valid.peek().litToBoolean) {
            meshD :+= (dut.io.meshD.bits.row.peek().litValue.toInt, dut.io.meshD.bits.data.peek().litValue)
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

        // Loop-tail order: Main's µTs (one per K fragment), then residual µTs I fastest (J = 1), then chunk.
        val main = (0 until kBlocks).map(k => Seq[BigInt](0, k, 0, workBase, k * dim))
        val tail = residual.toSeq.flatMap { r =>
          bitsOf(r).grouped(chunkRows).zipWithIndex.toSeq.flatMap { case (chunkBits, chunk) =>
            (0 until r.groups).flatMap { group =>
              Seq(Seq[BigInt](1, chunk, accTop - (group + 1) * dim, workBase + 1 + group, 0)) ++
                chunkBits.map(bit => Seq[BigInt](2, chunk, accTop - (group + 1) * dim, workBase + 1 + group, bit))
            }
          }
        }
        assert(traces == main ++ tail, s"case $index trace")
        val residualIds = residual.toSeq.flatMap(r => (0 until r.groups).map(group => BigInt(workBase + 1 + group)))
        assert(outputs.sorted == (BigInt(workBase) +: residualIds).sorted, s"case $index completed work IDs")

        // LdR read every row of the packed residual A exactly once, and nothing else of it.
        val expectedReads = residual.toSeq.flatMap { r =>
          (0 until r.groups * dim * chunksOf(r)).map(row => BigInt(row) * spRowBytes)
        }
        assert(raReads.keys.toSeq.sorted == expectedReads.sorted && raReads.values.forall(_ == 1),
          s"case $index residual A reads")
        // Group-0 PRELOADs read their indexed W rows from the top down (J = 1, so row = bit).
        val expectedGather = residual.toSeq.flatMap { r =>
          bitsOf(r).grouped(chunkRows).toSeq.flatMap(chunkBits => chunkBits.indices.reverse.map(rr => (rr, BigInt(chunkBits(rr)))))
        }
        assert(gathered == expectedGather, s"case $index gathered W rows")
        // Residual rows reaching the mesh: D rows are W rows of the surviving bits, A rows the LdR'd data.
        residual.foreach { r =>
          val chunkBits = bitsOf(r).grouped(chunkRows).toSeq
          val dPasses = meshD.grouped(dim).toSeq
          assert(dPasses.size == chunkBits.size && dPasses.forall(_.map(_._1) == (dim - 1 to 0 by -1)),
            s"case $index mesh D passes")
          for ((pass, bitsInChunk) <- dPasses.zip(chunkBits); (rowIndex, data) <- pass) {
            val wanted = if (rowIndex < bitsInChunk.size && bitsInChunk(rowIndex) < b.size)
              pack(b(bitsInChunk(rowIndex)) ++ Seq.fill(dim - 3)(0), profile.operandBits) else BigInt(0)
            assert(data == wanted, s"case $index mesh D row $rowIndex")
          }
          val aPasses = meshA.foldLeft(Vector.empty[Vector[(Int, BigInt)]]) { case (passes, beat) =>
            if (beat._1 == 0) passes :+ Vector(beat) else passes.init :+ (passes.last :+ beat)
          }
          val micro = for (chunk <- chunkBits.indices; group <- 0 until r.groups) yield (chunk, group)
          assert(aPasses.size == micro.size, s"case $index mesh A passes")
          for (((chunk, group), pass) <- micro.zip(aPasses); (rowIndex, data) <- pass) {
            val validRows = dim - (if (group == r.groups - 1) r.padI else 0)
            val reduction = chunkBits(chunk).size
            val wanted = if (rowIndex < validRows)
              pack((0 until dim).map(l => if (l < reduction) raValue(r, group * dim + rowIndex, chunk, l) else 0), profile.operandBits)
            else BigInt(0)
            assert(data == wanted, s"case $index mesh A chunk $chunk group $group row $rowIndex")
          }
        }
        if (residual.isEmpty) assert(raReads.isEmpty && gathered.isEmpty && meshA.isEmpty && meshD.isEmpty, s"case $index pair off")

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

  // C2 at the PairScheduler: one paired request with a 1-bit residual. Unbroken, LdR fires before
  // the residual PRELOAD; broken, the PRELOAD goes first and the ldr_ahead assertion must fail.
  private def ldrAhead(profile: ResolvedProfile, broken: Boolean): Unit = {
    implicit val parameters: Parameters = new BaseConfig().toInstance.alterPartial { case TileKey => RocketTileParams() }
    val config = UpstreamWsConfig(profile)
    import config._
    val dim = profile.dim
    val maxAddr = sp_banks * sp_bank_entries
    val maxAcc = acc_banks * acc_bank_entries
    test(new PairScheduler(dim, maxAddr, maxAcc, profile.operandBits,
      new PreloadRs(mvin_rows_bits, mvin_cols_bits, local_addr_t),
      new PreloadRs(mvout_rows_bits, mvout_cols_bits, local_addr_t),
      new ComputeRs(mvin_rows_bits, mvin_cols_bits, local_addr_t),
      new ComputeRs(mvin_rows_bits, mvin_cols_bits, local_addr_t),
      new MvinRs2(mvin_rows_bits, mvin_cols_bits, local_addr_t),
      ldrAfterFirstUse = broken)) { dut =>
      dut.io.out.ready.poke(true.B)
      dut.io.residual.ready.poke(true.B)
      dut.io.ldr.ready.poke(true.B)
      dut.io.robOverloaded.poke(false.B)
      dut.io.ldrRobOverloaded.poke(false.B)
      dut.io.i.poke(0.U)
      dut.io.j.poke(0.U)
      dut.io.k.poke(0.U)
      dut.io.main.valid.poke(false.B)
      val req = dut.io.req.bits
      Seq(req.pad_i, req.pad_j, req.pad_k, req.a_addr_start, req.c_addr_start, req.loop_id).foreach(_.poke(0.U))
      Seq(req.a_tranpose, req.b_tranpose, req.accumulate, req.skip).foreach(_.poke(false.B))
      req.max_i.poke(1.U)
      req.max_j.poke(1.U)
      req.max_k.poke(1.U)
      req.b_addr_end.poke((4 * dim).U)
      val meta = dut.io.metadata.bits.meta
      Seq(meta.maxI, meta.maxJ, meta.maxK, meta.padI, meta.padJ, meta.padK, meta.scaleBase, meta.scaleGeneration,
        meta.fragmentBase, meta.workBase, meta.residualCompactBegin, meta.residualPadI).foreach(_.poke(0.U))
      Seq(meta.accumulate, meta.finalFragment, meta.rmdRaw).foreach(_.poke(false.B))
      meta.paired.poke(true.B)
      meta.residualMask.poke(1.U)
      meta.residualGroups.poke(1.U)
      meta.residualAccTop.poke((maxAcc / 2).U)
      meta.residualWorkOffset.poke(1.U)
      meta.residualFirstRun.poke(true.B)
      meta.residualFinalRun.poke(true.B)
      dut.io.metadata.bits.source.address.poke(0x1000.U)
      dut.io.metadata.bits.source.strideBytes.poke((dim * profile.operandBits / 8).U)
      dut.io.req.valid.poke(true.B)
      dut.io.metadata.valid.poke(true.B)
      dut.clock.step()
      dut.io.req.valid.poke(false.B)
      dut.io.metadata.valid.poke(false.B)
      var mainLeft = 2
      var ldrAt = -1
      var preloadAt = -1
      var cycle = 0
      while (dut.io.busy.peek().litToBoolean && cycle < 200) {
        dut.io.main.valid.poke((mainLeft > 0).B)
        dut.io.main.bits.inst.funct.poke((if (mainLeft == 2) PRELOAD_CMD else COMPUTE_AND_FLIP_CMD).litValue.U)
        if (dut.io.main.valid.peek().litToBoolean && dut.io.main.ready.peek().litToBoolean) mainLeft -= 1
        if (dut.io.ldr.valid.peek().litToBoolean) {
          dut.io.ldr.bits.inst.funct.expect(LOAD3_CMD)
          dut.io.ldr.bits.rs1.expect(0x1000.U)
          ldrAt = cycle
        }
        if (dut.io.residual.valid.peek().litToBoolean && preloadAt < 0) preloadAt = cycle
        dut.clock.step()
        cycle += 1
      }
      assert(!dut.io.busy.peek().litToBoolean && mainLeft == 0, "paired request did not finish")
      assert(ldrAt >= 0 && preloadAt > ldrAt, s"LdR at $ldrAt, residual PRELOAD at $preloadAt")
    }
  }
}
