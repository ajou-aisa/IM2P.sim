package im2p.gemmini

import java.nio.file.{Files, Path}
import java.util.Properties
import org.scalatest.flatspec.AnyFlatSpec

final class HardwareContractSpec extends AnyFlatSpec {
  private def selected = ResolvedProfile.supported.find(
    _.name == sys.props("im2p.testProfile")).get

  private def properties(): Properties = {
    val values = new Properties
    val stream = Files.newInputStream(Path.of(sys.props("im2p.resolvedHardware")))
    try values.load(stream) finally stream.close()
    values
  }

  "HardwareContract" should "cross-check the selected resolver output against elaboration" in {
    val profile = selected
    HardwareContract.verify(profile, UpstreamWsConfig(profile), properties())
  }

  it should "reject every missing or mismatched hardware fact" in {
    val profile = selected
    val config = UpstreamWsConfig(profile)
    val expected = properties()
    import scala.jdk.CollectionConverters._
    expected.stringPropertyNames().asScala.foreach { key =>
      val changed = properties()
      changed.setProperty(key, (changed.getProperty(key).toInt + 1).toString)
      intercept[IllegalArgumentException] { HardwareContract.verify(profile, config, changed) }
      changed.remove(key)
      intercept[IllegalArgumentException] { HardwareContract.verify(profile, config, changed) }
    }
  }
}
