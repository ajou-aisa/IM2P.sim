package im2p.gemmini

import chisel3._

final class MatmulCycleCounter extends Module {
  val io = IO(new Bundle {
    val start = Input(Bool())
    val done = Input(Bool())
    val coreCycle = Output(UInt(64.W))
    val startCycle = Output(UInt(64.W))
    val doneCycle = Output(UInt(64.W))
    val elapsedCycles = Output(UInt(64.W))
    val running = Output(Bool())
    val measurementValid = Output(Bool())
    val startAccepted = Output(Bool())
    val doneAccepted = Output(Bool())
  })

  private val coreCycle = RegInit(0.U(64.W))
  private val startCycle = RegInit(0.U(64.W))
  private val doneCycle = RegInit(0.U(64.W))
  private val elapsedCycles = RegInit(0.U(64.W))
  private val running = RegInit(false.B)
  private val measurementValid = RegInit(false.B)

  coreCycle := coreCycle + 1.U

  val startAccepted = io.start && !running
  val doneAccepted = io.done && running

  when(startAccepted) {
    startCycle := coreCycle
    running := true.B
    measurementValid := false.B
  }
  when(doneAccepted) {
    doneCycle := coreCycle
    elapsedCycles := coreCycle - startCycle
    running := false.B
    measurementValid := true.B
  }

  io.coreCycle := coreCycle
  io.startCycle := startCycle
  io.doneCycle := doneCycle
  io.elapsedCycles := elapsedCycles
  io.running := running
  io.measurementValid := measurementValid
  io.startAccepted := startAccepted
  io.doneAccepted := doneAccepted
}
