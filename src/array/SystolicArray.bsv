package SystolicArray;

import Assert::*;
import Vector::*;

import Types::*;
import Arithmetic::*;
import PE::*;

// -----------------------------------------------------------------------------
// Conventional weight-stationary systolic array
// -----------------------------------------------------------------------------
//
// arrayDim x arrayDim PE를 직접 생성한다. PE row k는 B[k,*]를 stationary
// weight로 보유하고, activation은 수평으로, partial sum은 수직으로 이동한다.
//
// Gemmini를 reference로 삼되 Tile/Mesh/MeshWithDelays, transposer, DMA, RoCC,
// command counter 같은 generator/SoC 전용 계층은 복제하지 않는다. 이 모듈의
// 책임은 PE 배치와 인접 PE 사이의 systolic 연결뿐이다.

interface SystolicArrayIfc#(
    numeric type arrayDim,
    numeric type peLatency,
    type input_t,
    type weight_t,
    type product_t,
    type acc_t
);
    // 새로운 B tile을 적재하기 전에 row-loaded 상태를 지운다. 같은 B tile을 여러
    // activation execution에서 재사용할 때는 다시 호출하지 않아도 된다.
    method Action beginWeightLoad;

    // B matrix의 한 K row를 preload한다. 전체 weight matrix를 한 cycle의 넓은
    // method로 전달하지 않고 arrayDim cycle에 걸쳐 row 단위로 적재한다.
    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );

    method Bool weightsReady;
    method Action clearPipeline;

    method Action step(
        Vector#(arrayDim, Maybe#(input_t)) activationInputs,
        Vector#(arrayDim, Maybe#(acc_t)) partialInputs
    );

    // 각 column의 마지막 PE가 출력한 현재 K tile의 complete dot-product 결과다.
    // 전체 GEMM의 K가 arrayDim보다 크면 이 값은 full GEMM 관점의 partial sum이며,
    // 후속 execution이 Accumulator에 추가로 누산된다. Column별 systolic 지연은
    // 그대로 유지하므로 같은 cycle에는 일부 column만 Valid일 수 있다.
    method Vector#(arrayDim, Maybe#(acc_t)) partialSums;
endinterface

module mkSystolicArray(SystolicArrayIfc#(
    arrayDim,
    peLatency,
    input_t,
    weight_t,
    product_t,
    acc_t
)) provisos (
    Add#(1, arrayDimMinusOne, arrayDim),
    Add#(1, peLatencyMinusOne, peLatency),
    Bits#(input_t, inputBits),
    Bits#(weight_t, weightBits),
    Bits#(acc_t, accBits),
    Multiplier#(input_t, weight_t, product_t),
    ProductAccumulator#(product_t, acc_t)
);
    Vector#(
        arrayDim,
        Vector#(
            arrayDim,
            PEIfc#(peLatency, input_t, weight_t, product_t, acc_t)
        )
    ) processingElements <- replicateM(replicateM(mkPE));

    Vector#(arrayDim, Reg#(Bool)) loadedRows <- replicateM(mkReg(False));

    method Action beginWeightLoad;
        for (Integer row = 0; row < valueOf(arrayDim); row = row + 1) begin
            loadedRows[row] <= False;
            for (Integer column = 0;
                    column < valueOf(arrayDim);
                    column = column + 1) begin
                processingElements[row][column].invalidateWeight;
            end
        end
    endmethod

    method Action loadWeightRow(
        BoundedIndex#(arrayDim) row,
        Vector#(arrayDim, weight_t) weights
    );
        // BoundedIndex는 arrayDim 자체를 표현하지 않으므로, 폭 안에 들어오는
        // 마지막 유효 index(arrayDim-1)와 비교한다.
        dynamicAssert(
            row <= fromInteger(valueOf(arrayDim) - 1),
            "weight row exceeds arrayDim"
        );

        for (Integer decodedRow = 0;
                decodedRow < valueOf(arrayDim);
                decodedRow = decodedRow + 1) begin
            if (row == fromInteger(decodedRow)) begin
                loadedRows[decodedRow] <= True;
                for (Integer column = 0;
                        column < valueOf(arrayDim);
                        column = column + 1) begin
                    processingElements[decodedRow][column].loadWeight(
                        weights[column]
                    );
                end
            end
        end
    endmethod

    method Bool weightsReady;
        Vector#(arrayDim, Bool) status = newVector;
        for (Integer row = 0; row < valueOf(arrayDim); row = row + 1) begin
            status[row] = loadedRows[row];
        end
        return allTrue(status);
    endmethod

    method Action clearPipeline;
        for (Integer row = 0; row < valueOf(arrayDim); row = row + 1) begin
            for (Integer column = 0;
                    column < valueOf(arrayDim);
                    column = column + 1) begin
                processingElements[row][column].clearPipeline;
            end
        end
    endmethod

    method Action step(
        Vector#(arrayDim, Maybe#(input_t)) activationInputs,
        Vector#(arrayDim, Maybe#(acc_t)) partialInputs
    );
        for (Integer row = 0; row < valueOf(arrayDim); row = row + 1) begin
            for (Integer column = 0;
                    column < valueOf(arrayDim);
                    column = column + 1) begin
                Maybe#(input_t) activation = activationInputs[row];
                Maybe#(acc_t) partial = partialInputs[column];

                if (column > 0) begin
                    activation =
                        processingElements[row][column - 1].activationOut;
                end
                if (row > 0) begin
                    partial =
                        processingElements[row - 1][column].partialOut;
                end

                processingElements[row][column].step(activation, partial);
            end
        end
    endmethod

    method Vector#(arrayDim, Maybe#(acc_t)) partialSums;
        Vector#(arrayDim, Maybe#(acc_t)) outputs = newVector;
        for (Integer column = 0;
                column < valueOf(arrayDim);
                column = column + 1) begin
            outputs[column] =
                processingElements[valueOf(arrayDim) - 1][column].partialOut;
        end
        return outputs;
    endmethod
endmodule

endpackage
