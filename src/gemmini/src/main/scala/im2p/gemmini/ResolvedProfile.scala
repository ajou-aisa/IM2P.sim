package im2p.gemmini

final case class ResolvedProfile(operandBits: Int, weightBits: Int, dim: Int) {
  require(Set(4, 8).contains(operandBits), s"unsupported activation width: $operandBits")
  require(weightBits == operandBits, s"A/W widths must match: $operandBits/$weightBits")
  require(Set(16, 32, 64).contains(dim), s"unsupported DIM: $dim")

  val name: String = s"a${operandBits}w${weightBits}-d${dim}-hp1"
  val rawPartialBits: Int = operandBits + weightBits + Integer.numberOfTrailingZeros(dim)
  val scratchpadRowBytes: Int = (dim * operandBits + 7) / 8
  val accumulatorRowBytes: Int = dim * 4
  val fragmentLimit: Int = dim.min(32)
}

object ResolvedProfile {
  val supported: Seq[ResolvedProfile] =
    for {
      bits <- Seq(4, 8)
      dim <- Seq(16, 32, 64)
    } yield ResolvedProfile(bits, bits, dim)
}
