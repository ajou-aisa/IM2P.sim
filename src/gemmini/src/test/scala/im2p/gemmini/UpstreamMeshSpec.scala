package im2p.gemmini

import chisel3._
import chiseltest._
import chiseltest.simulator.VerilatorBackendAnnotation
import gemmini._
import gemmini.Arithmetic.SIntArithmetic
import org.scalatest.flatspec.AnyFlatSpec

final class UpstreamMeshSpec extends AnyFlatSpec with ChiselScalatestTester {
  behavior of "pinned Gemmini MeshWithDelays"

  it should "execute a small weight-stationary matrix multiply" in {
    val dimension = 4
    test(new MeshWithDelays(
      SInt(8.W),
      SInt(12.W),
      SInt(32.W),
      new StandaloneMeshTag,
      Dataflow.WS,
      false,
      0,
      0,
      1,
      1,
      dimension,
      dimension,
      1,
      1,
    )).withAnnotations(Seq(VerilatorBackendAnnotation)) { dut =>
      dut.io.a.valid.poke(false.B)
      dut.io.b.valid.poke(false.B)
      dut.io.d.valid.poke(false.B)
      dut.io.req.valid.poke(false.B)

      var captured = Vector.empty[(Boolean, Boolean, Vector[BigInt])]
      def step(): Unit = {
        dut.clock.step()
        if (dut.io.resp.valid.peek().litToBoolean) {
          captured :+= (
            dut.io.resp.bits.tag.payloadValid.peek().litToBoolean,
            dut.io.resp.bits.last.peek().litToBoolean,
            dut.io.resp.bits.data.map(_(0).peek().litValue).toVector,
          )
        }
      }

      def request(payloadValid: Boolean, flush: Int): Unit = {
        dut.io.req.bits.pe_control.dataflow.poke(Dataflow.WS.id.U)
        dut.io.req.bits.pe_control.propagate.poke(1.U)
        dut.io.req.bits.pe_control.shift.poke(0.U)
        dut.io.req.bits.a_transpose.poke(false.B)
        dut.io.req.bits.bd_transpose.poke(false.B)
        dut.io.req.bits.total_rows.poke(dimension.U)
        dut.io.req.bits.tag.payloadValid.poke(payloadValid.B)
        dut.io.req.bits.flush.poke(flush.U)
        dut.io.req.valid.poke(true.B)
        while (!dut.io.req.ready.peek().litToBoolean) step()
        step()
        dut.io.req.valid.poke(false.B)
      }

      def rows(a: Seq[Seq[Int]], d: Seq[Seq[Int]]): Unit = {
        for (row <- 0 until dimension) {
          for (column <- 0 until dimension) {
            dut.io.a.bits(column)(0).poke(a(row)(column).S)
            dut.io.b.bits(column)(0).poke(0.S)
            dut.io.d.bits(column)(0).poke(d(row)(column).S)
          }
          dut.io.a.valid.poke(true.B)
          dut.io.b.valid.poke(true.B)
          dut.io.d.valid.poke(true.B)
          while (!(dut.io.a.ready.peek().litToBoolean &&
              dut.io.b.ready.peek().litToBoolean && dut.io.d.ready.peek().litToBoolean)) step()
          step()
          dut.io.a.valid.poke(false.B)
          dut.io.b.valid.poke(false.B)
          dut.io.d.valid.poke(false.B)
        }
      }

      val zero = Seq.fill(dimension, dimension)(0)
      val activation = Seq.tabulate(dimension, dimension)((row, column) => if (row == column) 1 else 0)
      val weights = Seq.tabulate(dimension, dimension)((row, column) => if (row == column) column + 1 else 0)

      request(payloadValid = true, flush = 0)
      rows(zero, weights.reverse)
      request(payloadValid = false, flush = 0)
      rows(activation, zero)
      request(payloadValid = false, flush = 1)
      for (_ <- 0 until dimension * 8) step()

      val resultRows = captured.filter(_._1).map(_._3)
      assert(resultRows.size == dimension, s"tagged rows: $captured")
      assert(resultRows == Vector.tabulate(dimension, dimension) { (row, column) =>
        BigInt(if (row == column) column + 1 else 0)
      })
    }
  }
}
