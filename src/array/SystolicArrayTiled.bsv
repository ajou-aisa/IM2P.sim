package SystolicArrayTiled;

import Assert::*;
import Vector::*;

import Types::*;
import SystolicArray::*;

// -----------------------------------------------------------------------------
// DIM64 array composed from sixteen separately synthesized 16x16 arrays
// -----------------------------------------------------------------------------
//
// 각 tile의 east/south edge는 이미 PE register output이다. 따라서 인접 tile의
// west/north input으로 같은 cycle에 전달해도 추가 pipeline latency가 생기지
// 않는다. 이 모듈은 tile 내부 rule/state를 보지 않고 16개 interface만 조정한다.

module mkSystolicArray64WithTiles#(
    Vector#(
        4,
        Vector#(
            4,
            SystolicArrayIfc#(
                16,
                peLatency,
                input_t,
                weight_t,
                product_t,
                acc_t
            )
        )
    ) tiles
)(SystolicArrayIfc#(
    64,
    peLatency,
    input_t,
    weight_t,
    product_t,
    acc_t
)) provisos (
    Bits#(input_t, inputBits),
    Bits#(weight_t, weightBits),
    Bits#(acc_t, accBits)
);
    method Action beginWeightLoad;
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer tileColumn = 0;
                    tileColumn < 4;
                    tileColumn = tileColumn + 1) begin
                tiles[tileRow][tileColumn].beginWeightLoad;
            end
        end
    endmethod

    method Action beginWeightLoadBank(Bool bank);
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer tileColumn = 0;
                    tileColumn < 4;
                    tileColumn = tileColumn + 1) begin
                tiles[tileRow][tileColumn].beginWeightLoadBank(bank);
            end
        end
    endmethod

    method Action loadWeightRow(
        BoundedIndex#(64) row,
        Vector#(64, weight_t) weights
    );
        dynamicAssert(row <= 63, "weight row exceeds DIM64");
        BoundedIndex#(16) localRow = truncate(row);

        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            Integer firstRow = tileRow * 16;
            Integer lastRow = firstRow + 15;

            if (row >= fromInteger(firstRow)
                    && row <= fromInteger(lastRow)) begin
                for (Integer tileColumn = 0;
                        tileColumn < 4;
                        tileColumn = tileColumn + 1) begin
                    Vector#(16, weight_t) tileWeights = newVector;
                    for (Integer localColumn = 0;
                            localColumn < 16;
                            localColumn = localColumn + 1) begin
                        tileWeights[localColumn] =
                            weights[tileColumn * 16 + localColumn];
                    end
                    tiles[tileRow][tileColumn].loadWeightRow(
                        localRow,
                        tileWeights
                    );
                end
            end
        end
    endmethod

    method Action loadWeightRowBank(
        Bool bank,
        BoundedIndex#(64) row,
        Vector#(64, weight_t) weights
    );
        dynamicAssert(row <= 63, "weight row exceeds DIM64");
        BoundedIndex#(16) localRow = truncate(row);

        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            Integer firstRow = tileRow * 16;
            Integer lastRow = firstRow + 15;

            if (row >= fromInteger(firstRow)
                    && row <= fromInteger(lastRow)) begin
                for (Integer tileColumn = 0;
                        tileColumn < 4;
                        tileColumn = tileColumn + 1) begin
                    Vector#(16, weight_t) tileWeights = newVector;
                    for (Integer localColumn = 0;
                            localColumn < 16;
                            localColumn = localColumn + 1) begin
                        tileWeights[localColumn] =
                            weights[tileColumn * 16 + localColumn];
                    end
                    tiles[tileRow][tileColumn].loadWeightRowBank(
                        bank,
                        localRow,
                        tileWeights
                    );
                end
            end
        end
    endmethod

    method Bool weightsReady;
        Bool ready = True;
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer tileColumn = 0;
                    tileColumn < 4;
                    tileColumn = tileColumn + 1) begin
                ready = ready && tiles[tileRow][tileColumn].weightsReady;
            end
        end
        return ready;
    endmethod

    method Bool weightsReadyBank(Bool bank);
        Bool ready = True;
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer tileColumn = 0;
                    tileColumn < 4;
                    tileColumn = tileColumn + 1) begin
                ready =
                    ready && tiles[tileRow][tileColumn].weightsReadyBank(bank);
            end
        end
        return ready;
    endmethod

    method Action activateWeightBank(Bool bank);
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer tileColumn = 0;
                    tileColumn < 4;
                    tileColumn = tileColumn + 1) begin
                tiles[tileRow][tileColumn].activateWeightBank(bank);
            end
        end
    endmethod

    method Bool activeWeightBank = tiles[0][0].activeWeightBank;

    method Action clearPipeline;
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer tileColumn = 0;
                    tileColumn < 4;
                    tileColumn = tileColumn + 1) begin
                tiles[tileRow][tileColumn].clearPipeline;
            end
        end
    endmethod

    method Action step(
        Vector#(64, Maybe#(input_t)) activationInputs,
        Vector#(64, Maybe#(acc_t)) partialInputs
    );
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer tileColumn = 0;
                    tileColumn < 4;
                    tileColumn = tileColumn + 1) begin
                Vector#(16, Maybe#(input_t)) activations = newVector;
                Vector#(16, Maybe#(acc_t)) partials = newVector;

                for (Integer localIndex = 0;
                        localIndex < 16;
                        localIndex = localIndex + 1) begin
                    if (tileColumn == 0) begin
                        activations[localIndex] =
                            activationInputs[tileRow * 16 + localIndex];
                    end
                    else begin
                        activations[localIndex] =
                            tiles[tileRow][tileColumn - 1]
                                .activationOutputs[localIndex];
                    end

                    if (tileRow == 0) begin
                        partials[localIndex] =
                            partialInputs[tileColumn * 16 + localIndex];
                    end
                    else begin
                        partials[localIndex] =
                            tiles[tileRow - 1][tileColumn]
                                .partialSums[localIndex];
                    end
                end

                tiles[tileRow][tileColumn].step(activations, partials);
            end
        end
    endmethod

    method Vector#(64, Maybe#(input_t)) activationOutputs;
        Vector#(64, Maybe#(input_t)) outputs = newVector;
        for (Integer tileRow = 0; tileRow < 4; tileRow = tileRow + 1) begin
            for (Integer localRow = 0;
                    localRow < 16;
                    localRow = localRow + 1) begin
                outputs[tileRow * 16 + localRow] =
                    tiles[tileRow][3].activationOutputs[localRow];
            end
        end
        return outputs;
    endmethod

    method Vector#(64, Maybe#(acc_t)) partialSums;
        Vector#(64, Maybe#(acc_t)) outputs = newVector;
        for (Integer tileColumn = 0;
                tileColumn < 4;
                tileColumn = tileColumn + 1) begin
            for (Integer localColumn = 0;
                    localColumn < 16;
                    localColumn = localColumn + 1) begin
                outputs[tileColumn * 16 + localColumn] =
                    tiles[3][tileColumn].partialSums[localColumn];
            end
        end
        return outputs;
    endmethod
endmodule

endpackage
