package im2p.gemmini

import chisel3._
import chiseltest._
import freechips.rocketchip.devices.tilelink.TLTestRAM
import freechips.rocketchip.diplomacy.AddressSet
import freechips.rocketchip.system.BaseConfig
import freechips.rocketchip.tile.{RocketTileParams, TileKey, TileVisibilityNodeKey}
import freechips.rocketchip.tilelink.TLEphemeralNode
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.{LazyModule, LazyModuleImp}
import org.scalatest.flatspec.AnyFlatSpec

private class MemoryTestHarness(implicit p: Parameters) extends LazyModule {
  private val client = LazyModule(new InertTLClient)
  private val visibility = TLEphemeralNode()
  private val ram = LazyModule(new TLTestRAM(AddressSet(0, 0xffff), beatBytes = 8))
  ram.node := visibility := client.node
  lazy val module = new Impl
  class Impl extends LazyModuleImp(this) {
    private val parameters = p.alterPartial {
      case TileKey => RocketTileParams()
      case TileVisibilityNodeKey => visibility
    }
    private val config = UpstreamWsConfig(ResolvedProfile(8, 8, 16))
    private val memory = Module(new UpstreamWsMemory(ResolvedProfile(8, 8, 16), config)(parameters))
    val io = IO(chiselTypeOf(memory.io))
    io <> memory.io
  }
}

final class UpstreamWsMemorySpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "UpstreamWsMemory"

  it should "route out-of-order loads into SRAM and wait for final store visibility" in {
    implicit val p: Parameters = new BaseConfig().toInstance
    test(LazyModule(new MemoryTestHarness).module) { dut =>
      dut.io.dmaRead.req.valid.poke(false.B)
      dut.io.dmaWrite.req.valid.poke(false.B)
      dut.io.readRequest.ready.poke(true.B)
      dut.io.readBeat.valid.poke(false.B)
      dut.io.writeRequest.ready.poke(false.B)
      dut.io.writeCompletion.valid.poke(false.B)
      dut.io.srams.read.foreach { r =>
        r.req.valid.poke(false.B)
        r.resp.ready.poke(true.B)
      }
      dut.io.srams.write.foreach(_.en.poke(false.B))
      dut.io.acc.read_req.foreach(_.valid.poke(false.B))
      dut.io.acc.read_resp.foreach(_.ready.poke(true.B))
      dut.io.acc.write.foreach(_.valid.poke(false.B))
      dut.clock.step(5)

      def request(row: Int, cmd: Int): BigInt = {
        val r = dut.io.dmaRead.req
        r.bits.laddr.is_acc_addr.poke(false.B)
        r.bits.laddr.data.poke(row.U)
        r.bits.cols.poke(16.U)
        r.bits.cmd_id.poke(cmd.U)
        r.bits.repeats.poke(0.U)
        r.bits.pixel_repeats.poke(1.U)
        r.bits.has_acc_bitwidth.poke(false.B)
        r.bits.all_zeros.poke(false.B)
        r.bits.vaddr.poke((4096 + row * 16).U)
        r.valid.poke(true.B)
        r.ready.expect(true.B)
        dut.io.readRequest.valid.expect(true.B)
        val id = dut.io.readRequest.bits.id.peek().litValue
        dut.clock.step()
        r.valid.poke(false.B)
        id
      }
      val id0 = request(0, 3)
      val id1 = request(1, 7)
      assert(id0 != id1)
      for ((id, cmd, data) <- Seq((id1, 7, BigInt("12345678", 16)), (id0, 3, BigInt("76543210", 16)))) {
        dut.io.readBeat.bits.id.poke(id.U)
        dut.io.readBeat.bits.data.poke(data.U)
        dut.io.readBeat.bits.last.poke(true.B)
        dut.io.readBeat.bits.error.poke(false.B)
        dut.io.readBeat.valid.poke(true.B)
        dut.io.readBeat.ready.expect(true.B)
        dut.clock.step()
        dut.io.readBeat.valid.poke(false.B)
        dut.io.dmaRead.resp.valid.expect(true.B)
        dut.io.dmaRead.resp.bits.cmd_id.expect(cmd.U)
        dut.io.dmaRead.resp.bits.bytesRead.expect(16.U)
        dut.clock.step()
      }
      for ((row, data) <- Seq(0 -> BigInt("76543210", 16), 1 -> BigInt("12345678", 16))) {
        val r = dut.io.srams.read(0)
        r.req.bits.addr.poke(row.U)
        r.req.bits.fromDMA.poke(false.B)
        r.req.valid.poke(true.B)
        while (!r.req.ready.peek().litToBoolean) dut.clock.step()
        dut.clock.step()
        r.req.valid.poke(false.B)
        while (!r.resp.valid.peek().litToBoolean) dut.clock.step()
        r.resp.bits.data.expect(data.U)
        dut.clock.step()
      }

      dut.io.dmaRead.req.bits.laddr.data.poke(0.U)
      dut.io.dmaRead.req.bits.all_zeros.poke(true.B)
      dut.io.dmaRead.req.bits.cmd_id.poke(11.U)
      dut.io.dmaRead.req.valid.poke(true.B)
      dut.io.dmaRead.req.ready.expect(true.B)
      dut.io.readRequest.valid.expect(false.B)
      dut.clock.step()
      dut.io.dmaRead.req.valid.poke(false.B)
      dut.clock.step()
      dut.io.dmaRead.resp.valid.expect(true.B)
      dut.io.dmaRead.resp.bits.cmd_id.expect(11.U)
      dut.clock.step()
      dut.io.srams.read(0).req.bits.addr.poke(0.U)
      dut.io.srams.read(0).req.valid.poke(true.B)
      dut.io.srams.read(0).req.ready.expect(true.B)
      dut.clock.step()
      dut.io.srams.read(0).req.valid.poke(false.B)
      while (!dut.io.srams.read(0).resp.valid.peek().litToBoolean) dut.clock.step()
      dut.io.srams.read(0).resp.bits.data.expect(0.U)
      dut.clock.step()

      val a = dut.io.acc.write(0)
      a.bits.addr.poke(0.U)
      a.bits.acc.poke(false.B)
      a.bits.mask.foreach(_.poke(true.B))
      a.bits.data.flatten.zipWithIndex.foreach { case (v, i) => v.poke((if (i % 2 == 0) 100 else -100).S) }
      a.valid.poke(true.B)
      while (!a.ready.peek().litToBoolean) dut.clock.step()
      dut.clock.step()
      a.valid.poke(false.B)
      dut.clock.step(10)
      a.bits.acc.poke(true.B)
      a.bits.data.flatten.zipWithIndex.foreach { case (v, i) =>
        v.poke((if (i % 2 == 0) Int.MaxValue else Int.MinValue).S)
      }
      a.valid.poke(true.B)
      while (!a.ready.peek().litToBoolean) dut.clock.step()
      dut.clock.step()
      a.valid.poke(false.B)
      dut.clock.step(10)

      val s = dut.io.dmaWrite.req
      s.bits.laddr.is_acc_addr.poke(true.B)
      s.bits.laddr.read_full_acc_row.poke(true.B)
      s.bits.laddr.data.poke(0.U)
      s.bits.len.poke(16.U)
      s.bits.block.poke(0.U)
      s.bits.pool_en.poke(false.B)
      s.bits.store_en.poke(true.B)
      s.bits.cmd_id.poke(9.U)
      s.bits.vaddr.poke(8192.U)
      s.valid.poke(true.B)
      while (!s.ready.peek().litToBoolean) dut.clock.step()
      dut.clock.step()
      s.valid.poke(false.B)
      while (!dut.io.writeRequest.valid.peek().litToBoolean) dut.clock.step()
      val expected = (0 until 16).map(i =>
        (if (i % 2 == 0) BigInt(Int.MaxValue) else BigInt(1) << 31) << (i * 32)).reduce(_ | _)
      dut.io.writeRequest.bits.data.expect(expected.U)
      dut.io.writeRequest.bits.mask.expect(((BigInt(1) << 64) - 1).U)
      val writeId = dut.io.writeRequest.bits.id.peek().litValue
      dut.clock.step(4)
      dut.io.dmaWrite.resp.valid.expect(false.B)
      dut.io.writeRequest.ready.poke(true.B)
      dut.clock.step()
      dut.clock.step(4)
      dut.io.dmaWrite.resp.valid.expect(false.B)
      dut.io.drained.expect(false.B)
      dut.io.writeCompletion.bits.id.poke(writeId.U)
      dut.io.writeCompletion.bits.error.poke(false.B)
      dut.io.writeCompletion.valid.poke(true.B)
      dut.io.dmaWrite.resp.valid.expect(true.B)
      dut.io.dmaWrite.resp.bits.cmd_id.expect(9.U)
      dut.clock.step()
      dut.io.writeCompletion.valid.poke(false.B)
      dut.io.drained.expect(true.B)
      dut.io.error.expect(false.B)
      dut.io.loadRequests.expect(3.U)
      dut.io.loadResponses.expect(3.U)
      dut.io.storeRequests.expect(1.U)
      dut.io.storeResponses.expect(1.U)
      dut.io.readBytes.expect(32.U)
      dut.io.writeBytes.expect(64.U)

      val failedId = request(2, 12)
      dut.io.readBeat.bits.id.poke(failedId.U)
      dut.io.readBeat.bits.error.poke(true.B)
      dut.io.readBeat.valid.poke(true.B)
      dut.clock.step()
      dut.io.readBeat.valid.poke(false.B)
      dut.io.dmaRead.resp.valid.expect(false.B)
      dut.io.error.expect(true.B)
    }
  }
}
