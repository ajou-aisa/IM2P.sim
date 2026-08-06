package TbSystolicArray;

import Vector::*;
import SystolicArray::*;

typedef Vector#(4, Vector#(4, Int#(8))) Matrix8;
typedef Vector#(4, Vector#(4, Int#(32))) Matrix32;

function Matrix8 activationMatrix();
    Matrix8 x = replicate(replicate(0));

    x[0][0] = 1;
    x[0][1] = 2;
    x[0][2] = 3;
    x[0][3] = 4;
    x[1][0] = -1;
    x[1][2] = 2;
    x[1][3] = -3;
    x[2][0] = 127;
    x[2][1] = -128;
    x[2][2] = 1;
    x[3][0] = 5;
    x[3][1] = -6;
    x[3][2] = 7;
    x[3][3] = -8;

    return x;
endfunction

function Matrix8 weightMatrix();
    Matrix8 weights = replicate(replicate(0));

    weights[0][0] = 1;
    weights[0][1] = -2;
    weights[0][2] = 3;
    weights[0][3] = 4;
    weights[1][0] = 5;
    weights[1][1] = 6;
    weights[1][2] = -7;
    weights[1][3] = 8;
    weights[2][0] = -9;
    weights[2][1] = 10;
    weights[2][2] = 11;
    weights[2][3] = -12;
    weights[3][0] = 13;
    weights[3][1] = -14;
    weights[3][2] = 15;
    weights[3][3] = 16;

    return weights;
endfunction

function Matrix32 goldenMatrix(Matrix8 x, Matrix8 weights);
    Matrix32 golden = replicate(replicate(0));

    for (Integer i = 0; i < 4; i = i + 1) begin
        for (Integer j = 0; j < 4; j = j + 1) begin
            Int#(32) sum = 0;

            for (Integer k = 0; k < 4; k = k + 1) begin
                Int#(32) xValue = signExtend(x[i][k]);
                Int#(32) weight = signExtend(weights[k][j]);
                sum = sum + xValue * weight;
            end

            golden[i][j] = sum;
        end
    end

    return golden;
endfunction

module mkTb(Empty);
    SystolicArray#(4) dut <- mkSystolicArray;

    Matrix8 activations = activationMatrix();
    Matrix8 weights = weightMatrix();
    Matrix32 golden = goldenMatrix(activations, weights);

    Reg#(Bool) weightsLoaded <- mkReg(False);
    Reg#(UInt#(4)) streamCycle <- mkReg(0);
    Vector#(4, Reg#(UInt#(3))) outputRows <- replicateM(mkReg(0));

    rule preloadWeights (!weightsLoaded);
        dut.preloadWeights(weights);
        weightsLoaded <= True;
    endrule

    rule runArray (weightsLoaded);
        Vector#(4, Maybe#(Int#(8))) xLeft = replicate(tagged Invalid);
        Vector#(4, Bool) psumTopValid = replicate(False);

        // x[i][k]는 cycle i+k에 row k로, 초기 psum은 cycle i+j에 col j로 넣는다.
        for (Integer k = 0; k < 4; k = k + 1) begin
            if (streamCycle >= fromInteger(k)
                    && streamCycle < fromInteger(k + 4)) begin
                UInt#(2) inputRow = truncate(streamCycle - fromInteger(k));
                xLeft[k] = tagged Valid activations[inputRow][k];
            end
        end

        for (Integer j = 0; j < 4; j = j + 1) begin
            psumTopValid[j] = streamCycle >= fromInteger(j)
                && streamCycle < fromInteger(j + 4);
        end

        dut.step(xLeft, psumTopValid);
        streamCycle <= streamCycle + 1;

        Vector#(4, Bool) valids = dut.outValid;
        Vector#(4, Int#(32)) results = dut.result;
        Bool allDone = True;
        Bool mismatch = False;

        // 각 column은 row 0부터 순서대로 출력되므로 별도 de-skew 없이 비교한다.
        for (Integer col = 0; col < 4; col = col + 1) begin
            UInt#(3) nextRow = outputRows[col];

            if (valids[col]) begin
                if (outputRows[col] >= 4) begin
                    $display("FAIL: extra output col=%0d value=%0d", col, results[col]);
                    mismatch = True;
                end
                else begin
                    UInt#(2) row = truncate(outputRows[col]);
                    Int#(32) expected = golden[row][col];

                    if (results[col] != expected) begin
                        $display(
                            "FAIL: row=%0d col=%0d expected=%0d actual=%0d",
                            row, col, expected, results[col]
                        );
                        mismatch = True;
                    end
                    else begin
                        $display(
                            "PASS: row=%0d col=%0d value=%0d",
                            row, col, results[col]
                        );
                    end

                    outputRows[col] <= outputRows[col] + 1;
                    nextRow = outputRows[col] + 1;
                end
            end

            allDone = allDone && nextRow == 4;
        end

        if (mismatch) begin
            $display("WS INT8 MATMUL: FAIL");
            $finish(1);
        end
        else if (allDone) begin
            $display("WS INT8 MATMUL: PASS");
            $finish(0);
        end
        else if (streamCycle == 15) begin
            $display("WS INT8 MATMUL: FAIL (timeout)");
            $finish(1);
        end
    endrule

endmodule

endpackage
