package SystolicArrayInt16x64;

import Vector::*;

import SystolicArray::*;
import SystolicArrayTiled::*;

(* synthesize *)
module mkSystolicArrayTileInt16x16(SystolicArrayIfc#(
    16,
    1,
    Int#(16),
    Int#(8),
    Int#(24),
    Int#(64)
));
    let array <- mkSystolicArray;
    return array;
endmodule

(* synthesize *)
module mkSystolicArrayInt16x64(SystolicArrayIfc#(
    64,
    1,
    Int#(16),
    Int#(8),
    Int#(24),
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
                Int#(8),
                Int#(24),
                Int#(64)
            )
        )
    ) tiles <- replicateM(replicateM(mkSystolicArrayTileInt16x16));

    let array <- mkSystolicArray64WithTiles(tiles);
    return array;
endmodule

endpackage
