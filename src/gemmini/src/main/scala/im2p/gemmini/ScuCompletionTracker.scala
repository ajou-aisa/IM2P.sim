package im2p.gemmini

import chisel3._
import chisel3.util._

final class ScuCompletionTracker(entries: Int, countWidth: Int = 16) extends Module {
  require(entries > 0 && isPow2(entries), s"entries must be a positive power of two, got $entries")
  require(countWidth > 0, s"countWidth must be positive, got $countWidth")

  private val workIdWidth = math.max(1, log2Ceil(entries))

  val io = IO(new Bundle {
    val allocate = Flipped(Decoupled(UInt(workIdWidth.W)))
    val issue = Flipped(Valid(UInt(workIdWidth.W)))
    val seal = Flipped(Valid(UInt(workIdWidth.W)))
    val commit = Flipped(Valid(UInt(workIdWidth.W)))
    val done = Decoupled(UInt(workIdWidth.W))
    val active = Output(Vec(entries, Bool()))
    val outstanding = Output(Vec(entries, UInt(countWidth.W)))
    val duplicateAllocate = Output(Bool())
    val invalidIssue = Output(Bool())
    val invalidSeal = Output(Bool())
    val commitUnderflow = Output(Bool())
    val issueOverflow = Output(Bool())
  })

  private val active = RegInit(VecInit(Seq.fill(entries)(false.B)))
  private val sealedWork = RegInit(VecInit(Seq.fill(entries)(false.B)))
  private val outstanding = RegInit(VecInit(Seq.fill(entries)(0.U(countWidth.W))))
  private val complete = VecInit((0 until entries).map { index =>
    active(index) && sealedWork(index) && outstanding(index) === 0.U
  })

  io.done.valid := complete.asUInt.orR
  io.done.bits := PriorityEncoder(complete.asUInt)

  private val retiringAllocateSlot = io.done.fire && io.done.bits === io.allocate.bits
  io.allocate.ready := !active(io.allocate.bits) || retiringAllocateSlot
  private val allocateFire = io.allocate.fire

  private val issueSlotUsable = (active(io.issue.bits) && !sealedWork(io.issue.bits) &&
    !(io.done.fire && io.done.bits === io.issue.bits)) ||
    (allocateFire && io.allocate.bits === io.issue.bits)
  private val sealSlotUsable = (active(io.seal.bits) && !(io.done.fire && io.done.bits === io.seal.bits)) ||
    (allocateFire && io.allocate.bits === io.seal.bits)
  private val commitSlotUsable = (active(io.commit.bits) && !(io.done.fire && io.done.bits === io.commit.bits)) ||
    (allocateFire && io.allocate.bits === io.commit.bits)
  private val issueForCommit = io.issue.valid && io.issue.bits === io.commit.bits && issueSlotUsable

  io.duplicateAllocate := io.allocate.valid && !io.allocate.ready
  io.invalidIssue := io.issue.valid && !issueSlotUsable
  io.invalidSeal := io.seal.valid && !sealSlotUsable
  io.commitUnderflow := io.commit.valid && (!commitSlotUsable ||
    (outstanding(io.commit.bits) === 0.U && !issueForCommit))
  io.issueOverflow := io.issue.valid && issueSlotUsable &&
    outstanding(io.issue.bits).andR && !(io.commit.valid && io.commit.bits === io.issue.bits)

  for (index <- 0 until entries) {
    val doneHere = io.done.fire && io.done.bits === index.U
    val allocateHere = allocateFire && io.allocate.bits === index.U
    val issueHere = io.issue.valid && io.issue.bits === index.U && !io.invalidIssue && !io.issueOverflow
    val sealHere = io.seal.valid && io.seal.bits === index.U && !io.invalidSeal
    val commitHere = io.commit.valid && io.commit.bits === index.U && !io.commitUnderflow

    when(doneHere) {
      active(index) := false.B
      sealedWork(index) := false.B
      outstanding(index) := 0.U
    }
    when(allocateHere) {
      active(index) := true.B
      sealedWork(index) := false.B
      outstanding(index) := 0.U
    }
    when(sealHere) {
      sealedWork(index) := true.B
    }
    when(issueHere && !commitHere) {
      outstanding(index) := outstanding(index) + 1.U
    }.elsewhen(commitHere && !issueHere) {
      outstanding(index) := outstanding(index) - 1.U
    }
  }

  io.active := active
  io.outstanding := outstanding
}
