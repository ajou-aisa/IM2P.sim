package im2p.gemmini

import chisel3._
import chiseltest._
import chiseltest.simulator.VerilatorBackendAnnotation
import org.scalatest.flatspec.AnyFlatSpec

final class StandaloneTopSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "StandaloneTop"

  it should "execute full-K HP1 work on the selected hardware profile" in {
    val requestedProfile = sys.props.getOrElse("im2p.testProfile", "a8w8-d16-hp1")
    val profile = ResolvedProfile.supported.find(_.name == requestedProfile)
      .getOrElse(throw new IllegalArgumentException(s"unsupported test profile: $requestedProfile"))
    val scratchpadBankRows = sys.props.get("im2p.scratchpadBankRows").fold(profile.dim * 2)(_.toInt)
    val accumulatorRows = sys.props.get("im2p.accumulatorRows").fold(math.max(64, profile.dim))(_.toInt)
    val localBase = scratchpadBankRows - profile.dim
    val accumulatorBase = accumulatorRows - profile.dim
    withClue(profile.name) {
      test(new StandaloneTop(profile, scratchpadBankRows, accumulatorRows))
        .withAnnotations(Seq(VerilatorBackendAnnotation)) { dut =>
      dut.io.localLoad.valid.poke(false.B)
      dut.io.scaleLoad.valid.poke(false.B)
      dut.io.scaleRelease.valid.poke(false.B)
      dut.io.command.valid.poke(false.B)
      dut.io.done.ready.poke(false.B)
      dut.io.resultRequest.valid.poke(false.B)
      dut.io.result.ready.poke(true.B)

      def packed(values: Seq[Int]): BigInt = values.zipWithIndex.foldLeft(BigInt(0)) {
        case (bits, (value, index)) =>
          bits | (BigInt(value & ((1 << profile.operandBits) - 1)) << (index * profile.operandBits))
      }

      def load(weights: Boolean, slot: Boolean, row: Int, values: Seq[Int]): Unit = {
        dut.io.localLoad.bits.weights.poke(weights.B)
        dut.io.localLoad.bits.slot.poke(slot.B)
        dut.io.localLoad.bits.row.poke((localBase + row).U)
        dut.io.localLoad.bits.data.poke(packed(values).U)
        dut.io.localLoad.valid.poke(true.B)
        while (!dut.io.localLoad.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        dut.io.localLoad.valid.poke(false.B)
      }

      for (slot <- Seq(false, true); row <- 0 until profile.dim) {
        load(false, slot, row, Seq.tabulate(profile.dim)(column =>
          if (row == 0 && column == 0) 1
          else if (profile.dim == 64 && row == 0 && column == profile.fragmentLimit) 7
          else 0))
        load(true, slot, row, Seq.tabulate(profile.dim)(column =>
          if (row == 0 && column == 0) 1
          else if (row == 0 && column == 1) 2
          else if (profile.dim == 64 && row == profile.fragmentLimit && column == 0) 5
          else 0))
      }

      def loadScales(address: Int, generation: Int, carrier: Int => BigInt): Unit = for (column <- 0 until profile.dim) {
        dut.io.scaleLoad.bits.column.poke(column.U)
        dut.io.scaleLoad.bits.address.poke(address.U)
        dut.io.scaleLoad.bits.generation.poke(generation.U)
        dut.io.scaleLoad.bits.carrier.poke(carrier(column).U)
        dut.io.scaleLoad.valid.poke(true.B)
        while (!dut.io.scaleLoad.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        dut.io.scaleLoad.valid.poke(false.B)
      }
      loadScales(address = 0, generation = 1, _ => BigInt(1))
      loadScales(address = 1, generation = 2, column =>
        if (column == 0) BigInt("80000000", 16) else BigInt(0))
      loadScales(address = 2, generation = 3, _ => BigInt("80000000", 16))

      def command(
        slot: Boolean,
        address: Int,
        generation: Int,
        first: Boolean,
        last: Boolean,
        workId: Int = 2,
      ): Unit = {
        dut.io.command.bits.slot.poke(slot.B)
        dut.io.command.bits.validRows.poke(1.U)
        dut.io.command.bits.validColumns.poke(2.U)
        dut.io.command.bits.fragmentLength.poke(profile.fragmentLimit.U)
        dut.io.command.bits.activationBase.poke(localBase.U)
        dut.io.command.bits.weightBase.poke(localBase.U)
        dut.io.command.bits.accumulatorBase.poke(accumulatorBase.U)
        dut.io.command.bits.scaleAddress.poke(address.U)
        dut.io.command.bits.scaleGeneration.poke(generation.U)
        dut.io.command.bits.workId.poke(workId.U)
        dut.io.command.bits.firstContribution.poke(first.B)
        dut.io.command.bits.finalFragment.poke(last.B)
        dut.io.command.valid.poke(true.B)
        var commandWait = 0
        while (!dut.io.command.ready.peek().litToBoolean && commandWait < 1000) {
          dut.io.done.valid.expect(false.B)
          dut.clock.step()
          commandWait += 1
        }
        assert(commandWait < 1000, "command was not accepted")
        dut.clock.step()
        dut.io.command.valid.poke(false.B)
      }

      def finish(workId: Int): Unit = {
        var completionWait = 0
        while (!dut.io.done.valid.peek().litToBoolean && completionWait < 4000) {
          dut.clock.step()
          completionWait += 1
        }
        assert(completionWait < 4000, "work did not complete")
        dut.io.done.bits.expect(workId.U)
        dut.clock.step()
        dut.io.measurementValid.expect(true.B)
        dut.io.done.ready.poke(true.B)
        dut.clock.step()
        dut.io.done.ready.poke(false.B)
      }

      def readResult(first: Int, second: Int): Unit = {
        dut.io.resultRequest.bits.row.poke(accumulatorBase.U)
        dut.io.resultRequest.valid.poke(true.B)
        var requestWait = 0
        while (!dut.io.resultRequest.ready.peek().litToBoolean && requestWait < 100) {
          dut.clock.step()
          requestWait += 1
        }
        assert(requestWait < 100, "result row 0 request stalled")
        dut.clock.step()
        dut.io.resultRequest.valid.poke(false.B)
        var resultWait = 0
        while (!dut.io.result.valid.peek().litToBoolean && resultWait < 100) {
          dut.clock.step()
          resultWait += 1
        }
        assert(resultWait < 100, "result row 0 missing")
        dut.io.result.bits.row.expect(accumulatorBase.U)
        dut.io.result.bits.data(0).expect(first.S)
        dut.io.result.bits.data(1).expect(second.S)
        for (column <- 2 until profile.dim) dut.io.result.bits.data(column).expect(0.S)
        dut.clock.step()
      }

      val fragmentsPerBlock = 32 / profile.fragmentLimit
      val fragments = fragmentsPerBlock * 2
      for (fragment <- 0 until fragments) {
        val firstBlock = fragment < fragmentsPerBlock
        command(
          slot = fragment % 2 == 1,
          address = if (firstBlock) 0 else 1,
          generation = if (firstBlock) 1 else 2,
          first = fragment == 0,
          last = fragment == fragments - 1,
        )
      }

      finish(workId = 2)
      readResult(first = fragmentsPerBlock * 2, second = fragmentsPerBlock * 6)

      command(slot = false, address = 2, generation = 3, first = true, last = true, workId = 3)
      dut.io.resultRequest.bits.row.poke(accumulatorBase.U)
      dut.io.resultRequest.valid.poke(true.B)
      dut.io.resultRequest.ready.expect(false.B)
      dut.io.resultRequest.valid.poke(false.B)
      finish(workId = 3)
      readResult(first = 0, second = 0)
      }
    }
  }
}
