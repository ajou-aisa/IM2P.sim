package im2p.gemmini

import chisel3._
import freechips.rocketchip.devices.tilelink.TLTestRAM
import freechips.rocketchip.diplomacy.{AddressSet, IdRange}
import freechips.rocketchip.tile.{RocketTileParams, TileKey, TileVisibilityNodeKey}
import freechips.rocketchip.tilelink._
import gemmini.GemminiArrayConfig
import org.chipsalliance.cde.config.Parameters
import org.chipsalliance.diplomacy.lazymodule.{LazyModule, LazyModuleImp}

final class InertTLClient(implicit parameters: Parameters) extends LazyModule {
  val node = TLClientNode(Seq(TLMasterPortParameters.v1(Seq(
    TLMasterParameters.v1("upstream-control-visibility", IdRange(0, 1)),
  ))))

  lazy val module = new LazyModuleImp(this) {
    private val (out, _) = node.out(0)
    out.a.valid := false.B
    out.a.bits := DontCare
    out.c.valid := false.B
    out.c.bits := DontCare
    out.e.valid := false.B
    out.e.bits := DontCare
    out.b.ready := true.B
    out.d.ready := true.B
  }
}

final class UpstreamWsControlHarness(
  config: GemminiArrayConfig[SInt, gemmini.Float, gemmini.Float],
)(implicit parameters: Parameters) extends LazyModule {
  private val client = LazyModule(new InertTLClient)
  private val visibility = TLEphemeralNode()
  private val ram = LazyModule(new TLTestRAM(AddressSet(0, 0xffff), beatBytes = 8))
  ram.node := visibility := client.node

  lazy val module = new LazyModuleImp(this) {
    private implicit val controlParameters: Parameters = parameters.alterPartial {
      case TileKey => RocketTileParams()
      case TileVisibilityNodeKey => visibility
    }
    private val control = Module(new UpstreamWsControl(config)(controlParameters))
    val io = IO(chiselTypeOf(control.io))
    io <> control.io
  }
}
