package im2p.gemmini

import chisel3._
import chiseltest._
import org.scalatest.flatspec.AnyFlatSpec

final class StandaloneLocalMemorySpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "StandaloneLocalMemory"

  it should "preserve packed INT4 rows and isolate current/next A and W banks" in {
    val profile = ResolvedProfile(4, 4, 16)
    test(new StandaloneLocalMemory(profile, bankRows = 64)) { dut =>
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
}
