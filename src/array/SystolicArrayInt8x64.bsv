package SystolicArrayInt8x64;

import Vector::*;

import Config::*;

import SystolicArray::*;
import SystolicArrayTiled::*;

(* synthesize *)
module mkSystolicArrayTileInt8x16(SystolicArrayIfc#(
    16,
    1,
    Int#(A8InputWidth),
    Int#(A8WeightWidth),
    Int#(A8ProductWidth),
    Int#(A8AccumulatorWidth)
));
    let array <- mkSystolicArray;
    return array;
endmodule

(* synthesize *)
module mkSystolicArrayInt8x64(SystolicArrayIfc#(
    64,
    1,
    Int#(A8InputWidth),
    Int#(A8WeightWidth),
    Int#(A8ProductWidth),
    Int#(A8AccumulatorWidth)
));
    Vector#(
        4,
        Vector#(
            4,
            SystolicArrayIfc#(
                16,
                1,
                Int#(A8InputWidth),
                Int#(A8WeightWidth),
                Int#(A8ProductWidth),
                Int#(A8AccumulatorWidth)
            )
        )
    ) tiles <- replicateM(replicateM(mkSystolicArrayTileInt8x16));

    let array <- mkSystolicArray64WithTiles(tiles);
    return array;
endmodule

endpackage
