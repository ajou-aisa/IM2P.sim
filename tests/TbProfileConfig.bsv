package TbProfileConfig;

import Assert::*;
import Config::*;

module mkTbProfileConfig(Empty);
    staticAssert(valueOf(A4AccumulatorWidth) == 32, "A4 architectural accumulator width");
    staticAssert(valueOf(A8AccumulatorWidth) == 32, "A8 architectural accumulator width");
    staticAssert(valueOf(A16AccumulatorWidth) == 64, "A16 architectural accumulator width");
    staticAssert(valueOf(IntegerAccumulatorRows#(16, A4AccumulatorWidth)) == 1024, "A4D16 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(32, A4AccumulatorWidth)) == 512, "A4D32 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(64, A4AccumulatorWidth)) == 256, "A4D64 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(16, A8AccumulatorWidth)) == 1024, "A8D16 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(32, A8AccumulatorWidth)) == 512, "A8D32 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(64, A8AccumulatorWidth)) == 256, "A8D64 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(16, A16AccumulatorWidth)) == 512, "A16D16 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(32, A16AccumulatorWidth)) == 256, "A16D32 rows");
    staticAssert(valueOf(IntegerAccumulatorRows#(64, A16AccumulatorWidth)) == 128, "A16D64 rows");
    staticAssert(valueOf(IntegerPartialWidth#(16, A4ProductWidth)) == 12, "A4D16 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(32, A4ProductWidth)) == 13, "A4D32 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(64, A4ProductWidth)) == 14, "A4D64 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(16, A8ProductWidth)) == 20, "A8D16 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(32, A8ProductWidth)) == 21, "A8D32 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(64, A8ProductWidth)) == 22, "A8D64 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(16, A16ProductWidth)) == 36, "A16D16 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(32, A16ProductWidth)) == 37, "A16D32 local partial width");
    staticAssert(valueOf(IntegerPartialWidth#(64, A16ProductWidth)) == 38, "A16D64 local partial width");
    staticAssert(valueOf(IntegerAccumulatorCapacityBytes) == 65536, "integer capacity");
    staticAssert(valueOf(FloatingAccumulatorRows) == 256, "preserve FP depth");

    rule passed;
        $display("PROFILE CONFIG BSV: PASS");
        $finish(0);
    endrule
endmodule

endpackage
