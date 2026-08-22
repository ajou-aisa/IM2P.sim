package SystolicArrayA4W4D64;

import Vector::*;

import SystolicArray::*;
import SystolicArrayTiled::*;

(* synthesize *)
module mkSystolicArrayTileA4W4D16(SystolicArrayIfc#(
    16,
    1,
    Int#(4),
    Int#(4),
    Int#(8),
    Int#(64)
));
    let array <- mkSystolicArray;
    return array;
endmodule

(* synthesize *)
module mkSystolicArrayA4W4D64(SystolicArrayIfc#(
    64,
    1,
    Int#(4),
    Int#(4),
    Int#(8),
    Int#(64)
));
    Vector#(
        4,
        Vector#(
            4,
            SystolicArrayIfc#(
                16,
                1,
                Int#(4),
                Int#(4),
                Int#(8),
                Int#(64)
            )
        )
    ) tiles <- replicateM(replicateM(mkSystolicArrayTileA4W4D16));

    let array <- mkSystolicArray64WithTiles(tiles);
    return array;
endmodule

endpackage
