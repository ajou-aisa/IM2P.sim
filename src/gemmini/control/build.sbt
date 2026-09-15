ThisBuild / scalaVersion := "2.13.12"
ThisBuild / scalafixScalaBinaryVersion := "2.13"

Global / allowUnsafeScalaLibUpgrade := true

lazy val workRoot = sys.env
  .get("IM2P_GEMMINI_WORK_ROOT")
  .map(file)
  .getOrElse(file(sys.props("user.home")) / "aisa-lab" / "build" / "im2p-gemmini")

lazy val upstreamTargetUtils = ProjectRef(
  (workRoot / "deps" / "chipyard-1.13.0").toURI,
  "midas_target_utils",
)

upstreamTargetUtils / scalaVersion := "2.13.12"

lazy val root = (project in file("."))
  .dependsOn(ProjectRef(file("..").toURI, "root"))
  .settings(
    name := "im2p-gemmini-upstream-control",
    addCompilerPlugin("org.chipsalliance" % "chisel-plugin" % "6.5.0" cross CrossVersion.full),
    libraryDependencies ++= Seq(
      "edu.berkeley.cs" %% "chiseltest" % "6.0.0" % Test,
      "org.scalatest" %% "scalatest" % "3.2.19" % Test,
    ),
  )
