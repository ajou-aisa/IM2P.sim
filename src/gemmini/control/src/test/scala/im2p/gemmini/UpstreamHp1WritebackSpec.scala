package im2p.gemmini

import chisel3._
import chiseltest._
import gemmini.GemminiConfigs
import org.scalatest.flatspec.AnyFlatSpec

class UpstreamHp1WritebackSpec extends AnyFlatSpec with ChiselScalatestTester {
  private val profile = ResolvedProfile(8, 8, 16)
  private val config = GemminiConfigs.defaultConfig.copy(reservation_station_entries_ex = 2)

  private def initialize(dut: UpstreamHp1Writeback): Unit = {
    dut.io.contextIssue.valid.poke(false.B)
    dut.io.rawCompletion.valid.poke(false.B)
    dut.io.rawCompletion.bits.poke(0.U)
    dut.io.scaleLoad.valid.poke(false.B)
    dut.io.scaleRelease.valid.poke(false.B)
    dut.io.scaleRelease.bits.column.poke(0.U)
    dut.io.scaleRelease.bits.address.poke(0.U)
    dut.io.scaleRelease.bits.generation.poke(1.U)
    dut.io.delayedCompletion.ready.poke(false.B)
    dut.io.workDone.ready.poke(false.B)
    dut.io.rawWrite.foreach(_.valid.poke(false.B))
    dut.io.scaledWrite.foreach(_.ready.poke(false.B))
  }

  private def load(dut: UpstreamHp1Writeback, carrier: BigInt, second: Option[BigInt] = None): Unit = {
    dut.io.scaleLoad.bits.address.poke(0.U)
    dut.io.scaleLoad.bits.generation.poke(1.U)
    dut.io.scaleLoad.valid.poke(true.B)
    for ((value, column) <- Seq(carrier, second.getOrElse(carrier)).zipWithIndex) {
      dut.io.scaleLoad.bits.column.poke(column.U)
      dut.io.scaleLoad.bits.carrier.poke(value.U)
      dut.io.scaleLoad.ready.expect(true.B)
      dut.clock.step()
    }
    dut.io.scaleLoad.valid.poke(false.B)
  }

  private def context(dut: UpstreamHp1Writeback, rob: Int = 1, work: Int = 0,
    rows: Int = 1, first: Boolean = true, raw: Boolean = false): Unit = {
    val bits = dut.io.contextIssue.bits
    bits.robId.poke(rob.U)
    bits.validRows.poke(rows.U)
    bits.validColumns.poke(2.U)
    bits.scaleAddress.poke(0.U)
    bits.scaleGeneration.poke(1.U)
    bits.workId.poke(work.U)
    bits.fragmentId.poke(0.U)
    bits.firstContribution.poke(first.B)
    bits.finalFragment.poke(true.B)
    bits.rmdRaw.poke(raw.B)
    dut.io.contextIssue.valid.poke(true.B)
  }

  private def row(dut: UpstreamHp1Writeback, value: Int = 3): Unit = {
    val write = dut.io.rawWrite(0)
    write.bits.addr.poke(7.U)
    write.bits.acc.poke(true.B)
    write.bits.data.foreach(_.foreach(_.poke(value.S)))
    write.bits.mask.foreach(_.poke(true.B))
    write.valid.poke(true.B)
    write.ready.expect(true.B)
    dut.clock.step()
    write.valid.poke(false.B)
  }

  behavior of "UpstreamHp1Writeback"

  it should "preserve raw identity independently for mixed in-flight dense and RMD contexts" in {
    for (rawFirst <- Seq(true, false)) {
      test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
        initialize(dut)
        load(dut, 3, Some(Hp1ScaleEncoding.ZeroCarrier))
        context(dut, raw = rawFirst)
        dut.io.contextIssue.ready.expect(true.B)
        dut.clock.step()
        context(dut, rob = 2, work = 1, raw = !rawFirst)
        dut.io.contextIssue.ready.expect(true.B)
        dut.clock.step()
        dut.io.contextIssue.valid.poke(false.B)
        dut.io.contextIssue.bits.rmdRaw.poke(rawFirst.B)
        for (rob <- 1 to 2) {
          dut.io.rawCompletion.valid.poke(true.B)
          dut.io.rawCompletion.bits.poke(rob.U)
          row(dut, -7)
        }
        dut.io.rawCompletion.valid.poke(false.B)
        dut.io.workDone.valid.expect(false.B)
        dut.io.delayedCompletion.valid.expect(false.B)
        dut.clock.step(3)
        dut.io.workDone.valid.expect(false.B)

        dut.io.scaledWrite(0).ready.poke(true.B)
        dut.io.workDone.ready.poke(true.B)
        dut.io.delayedCompletion.ready.poke(true.B)
        var outputs = Vector.empty[(BigInt, BigInt)]
        var completedWork = Vector.empty[BigInt]
        var completedRob = Vector.empty[BigInt]
        for (_ <- 0 until 30) {
          if (dut.io.scaledWrite(0).valid.peek().litToBoolean) {
            dut.io.scaledWrite(0).bits.acc.expect(false.B)
            outputs :+= (dut.io.scaledWrite(0).bits.data(0)(0).peek().litValue,
              dut.io.scaledWrite(0).bits.data(1)(0).peek().litValue)
          }
          if (dut.io.workDone.valid.peek().litToBoolean)
            completedWork :+= dut.io.workDone.bits.peek().litValue
          if (dut.io.delayedCompletion.valid.peek().litToBoolean)
            completedRob :+= dut.io.delayedCompletion.bits.peek().litValue
          dut.clock.step()
        }
        val expected = Seq(rawFirst, !rawFirst).map {
          raw => if (raw) (BigInt(-7), BigInt(-7)) else (BigInt(-56), BigInt(0))
        }
        assert(outputs == expected)
        assert(completedWork == Seq(BigInt(0), BigInt(1)))
        assert(completedRob == Seq(BigInt(1), BigInt(2)))
        dut.io.drained.expect(true.B)
        dut.io.errors.tracker.expect(false.B)
        dut.io.errors.scaleMiss.expect(false.B)
      }
    }
  }

  it should "snapshot scale and defer ROB and work completion until physical commit" in {
    test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
      initialize(dut)
      load(dut, 2, Some(1))
      context(dut)
      dut.io.contextIssue.ready.expect(true.B)
      dut.clock.step()
      dut.io.contextIssue.valid.poke(false.B)
      row(dut)
      dut.io.rawCompletion.valid.poke(true.B)
      dut.io.rawCompletion.bits.poke(1.U)
      dut.clock.step()
      dut.io.rawCompletion.valid.poke(false.B)
      dut.io.scaleRelease.valid.poke(true.B)
      for (column <- 0 until 2) {
        dut.io.scaleRelease.bits.column.poke(column.U)
        dut.io.scaleRelease.ready.expect(true.B)
        dut.clock.step()
      }
      dut.io.scaleRelease.valid.poke(false.B)
      load(dut, 0, Some(Hp1ScaleEncoding.ZeroCarrier))
      dut.io.scaledWrite(0).valid.expect(true.B)
      dut.io.scaledWrite(0).bits.data(0)(0).expect(12.S)
      dut.io.scaledWrite(0).bits.data(1)(0).expect(6.S)
      dut.io.scaledWrite(0).bits.acc.expect(false.B)
      dut.io.scaledWrite(0).bits.mask(0).expect(true.B)
      dut.io.scaledWrite(0).bits.mask(8).expect(false.B)
      dut.clock.step(3)
      dut.io.delayedCompletion.valid.expect(false.B)
      dut.io.workDone.valid.expect(false.B)
      dut.io.scaledWrite(0).ready.poke(true.B)
      dut.clock.step()
      dut.io.scaledWrite(0).ready.poke(false.B)
      dut.io.delayedCompletion.valid.expect(false.B)
      dut.clock.step(config.acc_latency + 3)
      dut.io.delayedCompletion.valid.expect(true.B)
      dut.io.delayedCompletion.bits.expect(1.U)
      dut.io.workDone.valid.expect(true.B)
    }
  }

  it should "replace with zero and retain accumulation for later contributions" in {
    for (first <- Seq(true, false)) {
      test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
        initialize(dut)
        load(dut, Hp1ScaleEncoding.ZeroCarrier)
        context(dut, first = first)
        dut.clock.step()
        dut.io.contextIssue.valid.poke(false.B)
        row(dut, -7)
        dut.io.scaledWrite(0).bits.data(0)(0).expect(0.S)
        dut.io.scaledWrite(0).bits.acc.expect((!first).B)
      }
    }
  }

  it should "block stale generations and reserve all rows atomically" in {
    test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
      initialize(dut)
      load(dut, 0)
      context(dut, rows = 16)
      dut.io.contextIssue.bits.scaleGeneration.poke(2.U)
      dut.io.contextIssue.ready.expect(false.B)
      dut.clock.step()
      dut.io.contextIssue.bits.scaleGeneration.poke(1.U)
      dut.io.contextIssue.ready.expect(true.B)
      dut.clock.step()
      context(dut, rob = 2, work = 1, rows = 16)
      dut.io.contextIssue.ready.expect(true.B)
      dut.clock.step()
      context(dut, rob = 3, work = 2)
      dut.io.contextIssue.ready.expect(false.B)
    }
  }

  it should "retain immediate and delayed completions arriving together" in {
    test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
      initialize(dut)
      load(dut, 0)
      context(dut)
      dut.clock.step()
      dut.io.contextIssue.valid.poke(false.B)
      dut.io.rawCompletion.valid.poke(true.B)
      dut.io.rawCompletion.bits.poke(1.U)
      row(dut)
      dut.io.rawCompletion.valid.poke(false.B)
      dut.io.scaledWrite(0).ready.poke(true.B)
      dut.clock.step(config.acc_latency + 1)
      dut.io.rawCompletion.valid.poke(true.B)
      dut.io.rawCompletion.bits.poke(2.U)
      dut.clock.step()
      dut.io.rawCompletion.valid.poke(false.B)
      dut.clock.step(3)
      dut.io.delayedCompletion.ready.poke(true.B)
      val observed = scala.collection.mutable.ArrayBuffer.empty[BigInt]
      for (_ <- 0 until 5) {
        if (dut.io.delayedCompletion.valid.peek().litToBoolean)
          observed += dut.io.delayedCompletion.bits.peek().litValue
        dut.clock.step()
      }
      assert(observed.sorted.toSeq == Seq(BigInt(1), BigInt(2)))
    }
  }

  it should "wait for a late raw ROB completion after the last valid row committed" in {
    test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
      initialize(dut)
      load(dut, 0)
      context(dut)
      dut.clock.step()
      dut.io.contextIssue.valid.poke(false.B)
      row(dut)
      dut.io.scaledWrite(0).ready.poke(true.B)
      dut.clock.step(config.acc_latency + 4)
      dut.io.delayedCompletion.valid.expect(false.B)
      dut.io.rawCompletion.valid.poke(true.B)
      dut.io.rawCompletion.bits.poke(1.U)
      dut.clock.step()
      dut.io.rawCompletion.valid.poke(false.B)
      dut.clock.step(2)
      dut.io.delayedCompletion.valid.expect(true.B)
      dut.io.delayedCompletion.bits.expect(1.U)
    }
  }

  it should "serialize same-address fragments until the preceding physical commit" in {
    test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
      initialize(dut)
      load(dut, 0)
      context(dut)
      dut.io.contextIssue.bits.finalFragment.poke(false.B)
      dut.clock.step()
      context(dut, rob = 2, first = false)
      dut.clock.step()
      dut.io.contextIssue.valid.poke(false.B)
      row(dut)
      row(dut, 9)
      dut.io.scaledWrite(0).ready.poke(true.B)
      dut.io.scaledWrite(0).bits.acc.expect(false.B)
      dut.clock.step()
      dut.io.scaledWrite(0).valid.expect(false.B)
      dut.io.workDone.valid.expect(false.B)
      dut.clock.step(config.acc_latency)
      dut.io.scaledWrite(0).valid.expect(true.B)
      dut.io.scaledWrite(0).bits.acc.expect(true.B)
      dut.io.scaledWrite(0).bits.data(0)(0).expect(9.S)
      dut.clock.step(config.acc_latency + 2)
      dut.io.workDone.valid.expect(true.B)
    }
  }

  it should "drain the pipeline between non-final and final work fragments" in {
    test(new UpstreamHp1Writeback(profile, config, scaleEntries = 4, workEntries = 4)) { dut =>
      initialize(dut)
      load(dut, 0)
      dut.io.delayedCompletion.ready.poke(true.B)
      dut.io.workDone.ready.poke(true.B)
      dut.io.scaledWrite(0).ready.poke(true.B)
      for (finalFragment <- Seq(false, true)) {
        context(dut, first = !finalFragment)
        dut.io.contextIssue.bits.finalFragment.poke(finalFragment.B)
        dut.io.contextIssue.ready.expect(true.B)
        dut.clock.step()
        dut.io.contextIssue.valid.poke(false.B)
        dut.io.rawCompletion.valid.poke(true.B)
        dut.io.rawCompletion.bits.poke(1.U)
        row(dut)
        dut.io.rawCompletion.valid.poke(false.B)
        dut.io.pipelineDrained.expect(false.B)
        dut.clock.step(config.acc_latency + 6)
        dut.io.pipelineDrained.expect(true.B)
        dut.io.drained.expect(finalFragment.B)
      }
    }
  }
}
