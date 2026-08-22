package SystolicArrayA16W16D64;

import Vector::*;

import SystolicArray::*;
import SystolicArrayTiled::*;

(* synthesize *)
module mkSystolicArrayTileA16W16D16(SystolicArrayIfc#(
    16,
    1,
    Int#(16),
    Int#(16),
    Int#(32),
    Int#(64)
));
    let array <- mkSystolicArray;
    return array;
endmodule

(* synthesize *)
module mkSystolicArrayA16W16D64(SystolicArrayIfc#(
    64,
    1,
    Int#(16),
    Int#(16),
    Int#(32),
    Int#(64)
));
    Vector#(
        4,
        Vector#(
            4,
            SystolicArrayIfc#(
                16,
                1,
                Int#(16),
                Int#(16),
                Int#(32),
                Int#(64)
            )
        )
    ) tiles <- replicateM(replicateM(mkSystolicArrayTileA16W16D16));

    let array <- mkSystolicArray64WithTiles(tiles);
    return array;
endmodule

endpackage
