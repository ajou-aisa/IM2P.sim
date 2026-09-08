package SystolicArrayA16W16D64;

import Vector::*;

import Config::*;

import SystolicArray::*;
import SystolicArrayTiled::*;

(* synthesize *)
module mkSystolicArrayTileA16W16D16(SystolicArrayIfc#(
    16,
    1,
    Int#(A16InputWidth),
    Int#(A16WeightWidth),
    Int#(A16ProductWidth),
    Int#(A16AccumulatorWidth)
));
    let array <- mkSystolicArray;
    return array;
endmodule

(* synthesize *)
module mkSystolicArrayA16W16D64(SystolicArrayIfc#(
    64,
    1,
    Int#(A16InputWidth),
    Int#(A16WeightWidth),
    Int#(A16ProductWidth),
    Int#(A16AccumulatorWidth)
));
    Vector#(
        4,
        Vector#(
            4,
            SystolicArrayIfc#(
                16,
                1,
                Int#(A16InputWidth),
                Int#(A16WeightWidth),
                Int#(A16ProductWidth),
                Int#(A16AccumulatorWidth)
            )
        )
    ) tiles <- replicateM(replicateM(mkSystolicArrayTileA16W16D16));

    let array <- mkSystolicArray64WithTiles(tiles);
    return array;
endmodule

endpackage
