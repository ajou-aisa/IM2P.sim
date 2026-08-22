package SystolicArrayInt4x64;

import Vector::*;

import SystolicArray::*;
import SystolicArrayTiled::*;

(* synthesize *)
module mkSystolicArrayTileInt4x16(SystolicArrayIfc#(
    16,
    1,
    Int#(4),
    Int#(8),
    Int#(12),
    Int#(64)
));
    let array <- mkSystolicArray;
    return array;
endmodule

(* synthesize *)
module mkSystolicArrayInt4x64(SystolicArrayIfc#(
    64,
    1,
    Int#(4),
    Int#(8),
    Int#(12),
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
                Int#(8),
                Int#(12),
                Int#(64)
            )
        )
    ) tiles <- replicateM(replicateM(mkSystolicArrayTileInt4x16));

    let array <- mkSystolicArray64WithTiles(tiles);
    return array;
endmodule

endpackage
