package SystolicArrayA4W4D64;

import Vector::*;

import Config::*;

import SystolicArray::*;
import SystolicArrayTiled::*;

(* synthesize *)
module mkSystolicArrayTileA4W4D16(SystolicArrayIfc#(
    16,
    1,
    Int#(A4InputWidth),
    Int#(A4WeightWidth),
    Int#(A4ProductWidth),
    Int#(IntegerPartialWidth#(64, A4ProductWidth))
));
    let array <- mkSystolicArray;
    return array;
endmodule

(* synthesize *)
module mkSystolicArrayA4W4D64(SystolicArrayIfc#(
    64,
    1,
    Int#(A4InputWidth),
    Int#(A4WeightWidth),
    Int#(A4ProductWidth),
    Int#(IntegerPartialWidth#(64, A4ProductWidth))
));
    Vector#(
        4,
        Vector#(
            4,
            SystolicArrayIfc#(
                16,
                1,
                Int#(A4InputWidth),
                Int#(A4WeightWidth),
                Int#(A4ProductWidth),
                Int#(IntegerPartialWidth#(64, A4ProductWidth))
            )
        )
    ) tiles <- replicateM(replicateM(mkSystolicArrayTileA4W4D16));

    let array <- mkSystolicArray64WithTiles(tiles);
    return array;
endmodule

endpackage
