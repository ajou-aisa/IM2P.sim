package im2p.gemmini

import org.scalatest.flatspec.AnyFlatSpec
import org.scalatest.matchers.should.Matchers

final class ResolvedProfileSpec extends AnyFlatSpec with Matchers {
  "ResolvedProfile" should "define exactly the six paired HP1 profiles" in {
    ResolvedProfile.supported.map(_.name) shouldBe Seq(
      "a4w4-d16-hp1",
      "a4w4-d32-hp1",
      "a4w4-d64-hp1",
      "a8w8-d16-hp1",
      "a8w8-d32-hp1",
      "a8w8-d64-hp1",
    )
  }

  it should "derive packed scratchpad and INT32 accumulator geometry" in {
    val profile = ResolvedProfile(operandBits = 4, weightBits = 4, dim = 64)

    profile.rawPartialBits shouldBe 14
    profile.scratchpadRowBytes shouldBe 32
    profile.accumulatorRowBytes shouldBe 256
    profile.fragmentLimit shouldBe 32
  }

  it should "reject unpaired widths and unsupported dimensions" in {
    an[IllegalArgumentException] should be thrownBy ResolvedProfile(4, 8, 16)
    an[IllegalArgumentException] should be thrownBy ResolvedProfile(8, 8, 24)
  }
}
