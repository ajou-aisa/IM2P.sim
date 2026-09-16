package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

final class ScratchpadBankHarnessSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "ScratchpadBankHarness"

  it should "preserve packed INT4 rows and isolate current/next A and W banks" in {
    val profile = ResolvedProfile(4, 4, 16)
    test(new ScratchpadBankHarness(profile, bankRows = 64)) { dut =>
      dut.io.load.valid.poke(false.B)
      dut.io.activationRead.valid.poke(false.B)
      dut.io.weightRead.valid.poke(false.B)
      dut.io.activationData.ready.poke(true.B)
      dut.io.weightData.ready.poke(true.B)

      def load(weights: Boolean, slot: Boolean, row: Int, data: BigInt): Unit = {
        dut.io.load.bits.weights.poke(weights.B)
        dut.io.load.bits.slot.poke(slot.B)
        dut.io.load.bits.row.poke(row.U)
        dut.io.load.bits.data.poke(data.U)
        dut.io.load.valid.poke(true.B)
        while (!dut.io.load.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        dut.io.load.valid.poke(false.B)
      }

      def read(weights: Boolean, slot: Boolean, row: Int, expected: BigInt): Unit = {
        val request = if (weights) dut.io.weightRead else dut.io.activationRead
        val response = if (weights) dut.io.weightData else dut.io.activationData
        request.bits.slot.poke(slot.B)
        request.bits.row.poke(row.U)
        request.valid.poke(true.B)
        while (!request.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        request.valid.poke(false.B)
        while (!response.valid.peek().litToBoolean) dut.clock.step()
        response.bits.expect(expected.U)
        dut.clock.step()
      }

      val activation = BigInt("76543210fedcba98", 16)
      val nextActivation = BigInt("0123456789abcdef", 16)
      val weights = BigInt("8888888877777777", 16)
      load(weights = false, slot = false, row = 47, activation)
      load(weights = false, slot = true, row = 47, nextActivation)
      load(weights = true, slot = false, row = 47, weights)
      read(weights = false, slot = false, row = 47, activation)
      read(weights = false, slot = true, row = 47, nextActivation)
      read(weights = true, slot = false, row = 47, weights)
    }
  }

  it should "return slot responses in request order and exclude only the selected bank" in {
    val profile = ResolvedProfile(4, 4, 16)
    test(new ScratchpadBankHarness(profile, bankRows = 64)) { dut =>
      dut.io.load.valid.poke(false.B)
      dut.io.activationRead.valid.poke(false.B)
      dut.io.weightRead.valid.poke(false.B)
      dut.io.activationData.ready.poke(true.B)
      dut.io.weightData.ready.poke(true.B)

      def load(slot: Boolean, row: Int, data: BigInt): Unit = {
        dut.io.load.bits.weights.poke(false.B)
        dut.io.load.bits.slot.poke(slot.B)
        dut.io.load.bits.row.poke(row.U)
        dut.io.load.bits.data.poke(data.U)
        dut.io.load.valid.poke(true.B)
        while (!dut.io.load.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        dut.io.load.valid.poke(false.B)
      }

      def issueRead(slot: Boolean, row: Int): Unit = {
        dut.io.activationRead.bits.slot.poke(slot.B)
        dut.io.activationRead.bits.row.poke(row.U)
        dut.io.activationRead.valid.poke(true.B)
        while (!dut.io.activationRead.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        dut.io.activationRead.valid.poke(false.B)
      }

      val slot0 = BigInt("1111111111111111", 16)
      val slot1 = BigInt("2222222222222222", 16)
      val previous = BigInt("4444444444444444", 16)
      val replacement = BigInt("3333333333333333", 16)
      load(slot = false, row = 3, slot0)
      load(slot = true, row = 3, slot1)
      load(slot = false, row = 4, previous)

      dut.io.activationData.ready.poke(false.B)
      issueRead(slot = true, row = 3)
      issueRead(slot = false, row = 3)
      while (!dut.io.activationData.valid.peek().litToBoolean) dut.clock.step()
      dut.io.activationData.bits.expect(slot1.U)
      dut.clock.step()
      dut.io.activationData.valid.expect(true.B)
      dut.io.activationData.bits.expect(slot1.U)
      dut.io.activationData.ready.poke(true.B)
      dut.clock.step()
      while (!dut.io.activationData.valid.peek().litToBoolean) dut.clock.step()
      dut.io.activationData.bits.expect(slot0.U)
      dut.clock.step()

      dut.io.load.bits.weights.poke(false.B)
      dut.io.load.bits.slot.poke(false.B)
      dut.io.load.bits.row.poke(4.U)
      dut.io.load.bits.data.poke(replacement.U)
      dut.io.load.valid.poke(true.B)
      dut.io.activationRead.bits.slot.poke(false.B)
      dut.io.activationRead.bits.row.poke(4.U)
      dut.io.activationRead.valid.poke(true.B)
      dut.io.load.ready.expect(false.B)
      dut.io.activationRead.ready.expect(true.B)
      dut.clock.step()
      dut.io.load.valid.poke(false.B)
      dut.io.activationRead.valid.poke(false.B)
      while (!dut.io.activationData.valid.peek().litToBoolean) dut.clock.step()
      dut.io.activationData.bits.expect(previous.U)
      dut.clock.step()

      dut.io.load.bits.slot.poke(false.B)
      dut.io.load.bits.row.poke(4.U)
      dut.io.load.bits.data.poke(replacement.U)
      dut.io.load.valid.poke(true.B)
      dut.io.activationRead.bits.slot.poke(true.B)
      dut.io.activationRead.bits.row.poke(3.U)
      dut.io.activationRead.valid.poke(true.B)
      dut.io.load.ready.expect(true.B)
      dut.io.activationRead.ready.expect(true.B)
      dut.clock.step()
      dut.io.load.valid.poke(false.B)
      dut.io.activationRead.valid.poke(false.B)
      while (!dut.io.activationData.valid.peek().litToBoolean) dut.clock.step()
      dut.io.activationData.bits.expect(slot1.U)
      dut.clock.step()

      issueRead(slot = false, row = 4)
      while (!dut.io.activationData.valid.peek().litToBoolean) dut.clock.step()
      dut.io.activationData.bits.expect(replacement.U)
      dut.clock.step()
    }
  }
}
