package im2p.gemmini

import gemmini.Dataflow
import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

final class UpstreamWsConfigSpec extends AnyFlatSpec with Matchers {
  "UpstreamWsConfig" should "derive every HP1 controller geometry from the resolved profile" in {
    ResolvedProfile.supported.foreach { profile =>
      val config = UpstreamWsConfig(profile)
      config.dataflow shouldBe Dataflow.WS
      config.inputType.getWidth shouldBe profile.operandBits
      config.spatialArrayOutputType.getWidth shouldBe profile.rawPartialBits
      config.accType.getWidth shouldBe 32
      config.DIM shouldBe profile.dim
      config.sp_banks shouldBe 4
      config.sp_bank_entries shouldBe 262144 / (4 * profile.scratchpadRowBytes)
      config.acc_banks shouldBe 2
      config.acc_bank_entries shouldBe 65536 / (2 * profile.accumulatorRowBytes)
      config.dma_maxbytes shouldBe profile.scratchpadRowBytes
      config.ex_read_from_acc shouldBe false
      config.ex_write_to_spad shouldBe false
    }
  }
}
