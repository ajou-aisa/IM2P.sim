import sbt._

ThisBuild / scalaVersion := "2.13.12"
ThisBuild / scalafixScalaBinaryVersion := "2.13"
ThisBuild / organization := "kr.ac.ajou.aisa"
ThisBuild / version := "0.1.0"
Global / allowUnsafeScalaLibUpgrade := true

lazy val sourceRoot = file(".").getCanonicalFile
lazy val portableSources = sourceRoot / "src/main/scala/im2p/gemmini"
lazy val controlSources = sourceRoot / "control/src/main/scala/im2p/gemmini"
lazy val workRoot = sys.env.get("IM2P_GEMMINI_WORK_ROOT").map(file)
  .getOrElse(file(sys.props("user.home")) / "aisa-lab/build/im2p-gemmini")
lazy val chipyardRoot = workRoot / "deps/chipyard-1.13.0"
lazy val upstreamGemmini = ProjectRef(chipyardRoot.toURI, "gemmini")
lazy val upstreamTargetUtils = ProjectRef(chipyardRoot.toURI, "midas_target_utils")
// External builds load their own project settings after this build. Apply the
// pinned compatibility setting and overlay once after the whole graph loads;
// no Python-generated `set` commands or dependency checkout edits are needed.
lazy val overlayNames = Set("GemminiConfigs.scala", "LoadController.scala",
  "LoopMatmul.scala", "StoreController.scala")
Global / onLoad := {
  val previous = (Global / onLoad).value
  state => {
    val loaded = previous(state)
    val extracted = Project.extract(loaded)
    val scalaPin = extracted.get(scuCore / scalaVersion)
    val directory = file(sys.props.getOrElse("im2p.gemmini.overlay",
      sys.error("use gemmini_vendor.py --overlay NEW_DIR and -Dim2p.gemmini.overlay=NEW_DIR")))
    val replacements = (directory ** "*.scala").get
    require(replacements.map(_.getName).toSet == overlayNames && replacements.size == overlayNames.size,
      "overlay must contain exactly the four verified upstream replacements")
    extracted.appendWithSession(Seq(
      // Reapplication invokes onLoad again; restore its predecessor first.
      Global / onLoad := previous,
      upstreamTargetUtils / scalaVersion := scalaPin,
      upstreamGemmini / Compile / unmanagedSources ~= { originals =>
        originals.filterNot(source => overlayNames(source.getName)) ++ replacements
      },
    ), loaded)
  }
}

lazy val common = Seq(
  addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % "6.5.0" cross CrossVersion.full),
  libraryDependencies += "org.chipsalliance" %% "chisel" % "6.5.0",
  Test / parallelExecution := false,
)
lazy val testLibraries = Seq(
  libraryDependencies ++= Seq(
    "edu.berkeley.cs" %% "chiseltest" % "6.0.0" % Test,
    "org.scalatest" %% "scalatest" % "3.2.19" % Test,
  ),
)
lazy val coreNames = Set("SCU.scala", "ScaleMemory.scala", "ScaleProtocol.scala",
  "SatAccumulatorAdder.scala", "ScuCompletionTracker.scala", "ScuWritebackQueue.scala",
  "ScuFragmentPlanner.scala", "Int4Packer.scala", "Int4Unpacker.scala",
  "ResolvedProfile.scala", "MatmulCycleCounter.scala")
lazy val integrationNames = Set("Hp1LoopMetadata.scala", "UpstreamWsConfig.scala",
  "UpstreamWsControl.scala", "UpstreamHp1Writeback.scala")

// Layer A has no upstream Gemmini or standalone/host project dependency.
lazy val scuCore = (project in file("scu-core")).settings(common).settings(
  Compile / unmanagedSources := coreNames.toSeq.sorted.map(portableSources / _),
)
// Layer B can see Gemmini and Layer A, never HostCommandBridge or its descriptor.
lazy val gemminiIntegration = (project in file("integration"))
  .dependsOn(scuCore, upstreamGemmini).settings(common).settings(
    Compile / unmanagedSources := integrationNames.toSeq.sorted.map(controlSources / _),
  )
// Layer C: the default integrated standalone elaboration and all control tests.
lazy val root = (project in file("."))
  .dependsOn(scuCore, gemminiIntegration).aggregate(LocalProject("diagnostics"))
  .settings(common).settings(testLibraries).settings(
    name := "im2p-gemmini-hp1",
    Compile / unmanagedSources := (controlSources * "*.scala").get
      .filterNot(source => integrationNames(source.getName)) :+ (portableSources / "BackingMemoryPort.scala"),
    Test / unmanagedSourceDirectories := Seq(sourceRoot / "control/src/test/scala"),
  )
// Lower-level primitive diagnostics are test-only; no alternative production top.
lazy val diagnostics = (project in file("diagnostics"))
  .dependsOn(root).settings(common).settings(testLibraries).settings(
    Compile / unmanagedSources := Seq.empty,
    Test / unmanagedSourceDirectories := Seq(sourceRoot / "src/test/scala"),
  )
