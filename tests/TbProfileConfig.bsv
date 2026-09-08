package TbProfileConfig;

import Assert::*;
import Config::*;

module mkTbProfileConfig(Empty);
    staticAssert(valueOf(A4AccumulatorWidth) == 32, "A4 partial/accumulator width");
    staticAssert(valueOf(A8AccumulatorWidth) == 32, "A8 partial/accumulator width");
    staticAssert(valueOf(A16AccumulatorWidth) == 64, "A16 partial/accumulator width");
    staticAssert(valueOf(IntegerAccumulatorRows#(16, A4AccumulatorWidth)) == 1024, "A4D16 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(32, A4AccumulatorWidth)) == 512, "A4D32 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(64, A4AccumulatorWidth)) == 256, "A4D64 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(16, A8AccumulatorWidth)) == 1024, "A8D16 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(32, A8AccumulatorWidth)) == 512, "A8D32 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(64, A8AccumulatorWidth)) == 256, "A8D64 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(16, A16AccumulatorWidth)) == 512, "A16D16 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(32, A16AccumulatorWidth)) == 256, "A16D32 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(64, A16AccumulatorWidth)) == 128, "A16D64 rows");
    staticAssert(valueOf(IntegerAccumulatorCapacityBytes) == 65536, "integer capacity");
    staticAssert(valueOf(FloatingAccumulatorRows) == 256, "preserve FP depth");

    rule passed;
        $display("PROFILE CONFIG BSV: PASS");
        $finish(0);
    endrule
endmodule

endpackage
