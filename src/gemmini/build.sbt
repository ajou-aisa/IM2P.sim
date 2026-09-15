import sbt._

ThisBuild / scalaVersion := "2.13.12"
ThisBuild / scalafixScalaBinaryVersion := "2.13"
ThisBuild / organization := "kr.ac.ajou.aisa"
ThisBuild / version := "0.1.0"

Global / allowUnsafeScalaLibUpgrade := true

lazy val workRoot = sys.env
  .get("IM2P_GEMMINI_WORK_ROOT")
  .map(file)
  .getOrElse(file(sys.props("user.home")) / "aisa-lab" / "build" / "im2p-gemmini")

lazy val chipyardRoot = workRoot / "deps" / "chipyard-1.13.0"
lazy val upstreamGemmini = ProjectRef(chipyardRoot.toURI, "gemmini")
lazy val upstreamTargetUtils = ProjectRef(chipyardRoot.toURI, "midas_target_utils")

upstreamTargetUtils / scalaVersion := "2.13.12"

lazy val root = (project in file("."))
  .dependsOn(upstreamGemmini)
  .settings(
    name := "im2p-gemmini-hp1",
    addCompilerPlugin(
      "org.chipsalliance" % "chisel-plugin" % "6.5.0" cross CrossVersion.full
    ),
    libraryDependencies ++= Seq(
      "edu.berkeley.cs" %% "chiseltest" % "6.0.0" % Test,
      "org.scalatest" %% "scalatest" % "3.2.19" % Test,
    ),
  )
