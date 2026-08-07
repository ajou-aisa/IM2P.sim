package TbSystolicArray;

import Vector::*;
import SystolicArray::*;
import NumericFormat::*;

typedef NumericElement#(INT, 4) Element;
typedef Vector#(4, Vector#(4, Element)) ElementMatrix;

function Element intElement(Integer value);
    Int#(4) signedValue = fromInteger(value);
    return numericElement(pack(signedValue));
endfunction

function ElementMatrix activationMatrix();
    ElementMatrix x = replicate(replicate(intElement(0)));

    x[0][0] = intElement(1);
    x[0][1] = intElement(2);
    x[0][2] = intElement(3);
    x[0][3] = intElement(4);
    x[1][0] = intElement(-1);
    x[1][2] = intElement(2);
    x[1][3] = intElement(-3);
    x[2][0] = intElement(7);
    x[2][1] = intElement(-8);
    x[2][2] = intElement(1);
    x[3][0] = intElement(5);
    x[3][1] = intElement(-6);
    x[3][2] = intElement(7);
    x[3][3] = intElement(-8);

    return x;
endfunction

function ElementMatrix weightMatrix();
    ElementMatrix weights = replicate(replicate(intElement(0)));

    weights[0][0] = intElement(1);
    weights[0][1] = intElement(-2);
    weights[0][2] = intElement(3);
    weights[0][3] = intElement(4);
    weights[1][0] = intElement(5);
    weights[1][1] = intElement(6);
    weights[1][2] = intElement(-7);
    weights[1][3] = intElement(-8);
    weights[2][0] = intElement(-8);
    weights[2][1] = intElement(7);
    weights[2][2] = intElement(6);
    weights[2][3] = intElement(-5);
    weights[3][0] = intElement(5);
    weights[3][1] = intElement(-6);
    weights[3][2] = intElement(7);
    weights[3][3] = intElement(-8);

    return weights;
endfunction

function ElementMatrix goldenMatrix(ElementMatrix x, ElementMatrix weights);
    ElementMatrix golden = replicate(replicate(intElement(0)));

    for (Integer i = 0; i < 4; i = i + 1) begin
        for (Integer j = 0; j < 4; j = j + 1) begin
            Int#(4) sum = 0;

            for (Integer k = 0; k < 4; k = k + 1) begin
                Int#(4) xValue = unpack(numericBits(x[i][k]));
                Int#(4) weight = unpack(numericBits(weights[k][j]));
                sum = sum + xValue * weight;
            end

            golden[i][j] = numericElement(pack(sum));
        end
    end

    return golden;
endfunction

module mkTb(Empty);
    SystolicArray#(4, INT, 4) dut <- mkSystolicArray;

    ElementMatrix activations = activationMatrix();
    ElementMatrix weights = weightMatrix();
    ElementMatrix golden = goldenMatrix(activations, weights);

    Reg#(Bool) weightsLoaded <- mkReg(False);
    Reg#(UInt#(4)) streamCycle <- mkReg(0);
    Vector#(4, Reg#(UInt#(3))) outputRows <- replicateM(mkReg(0));

    rule preloadWeights (!weightsLoaded);
        dut.preloadWeights(weights);
        weightsLoaded <= True;
    endrule

    rule runArray (weightsLoaded);
        Vector#(4, Maybe#(Element)) xLeft = replicate(tagged Invalid);
        Vector#(4, Bool) psumTopValid = replicate(False);

        // x[i][k]는 i+k cycle에 PE row k로 들어간다.
        // C[i][j]의 초기 psum은 i+j cycle에 column j로 들어간다.
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
        Vector#(4, Element) results = dut.result;
        Bool allDone = True;
        Bool mismatch = False;

        // 각 column은 row 순서대로 출력하지만 column 간 cycle skew는 유지된다.
        for (Integer col = 0; col < 4; col = col + 1) begin
            UInt#(3) nextRow = outputRows[col];

            if (valids[col]) begin
                if (outputRows[col] >= 4) begin
                    $display("FAIL: extra output col=%0d value=%0d", col, results[col]);
                    mismatch = True;
                end
                else begin
                    UInt#(2) row = truncate(outputRows[col]);
                    Element expected = golden[row][col];
                    Int#(4) expectedValue = unpack(numericBits(expected));
                    Int#(4) actualValue = unpack(numericBits(results[col]));

                    if (results[col] != expected) begin
                        $display(
                            "FAIL: row=%0d col=%0d expected=%0d actual=%0d",
                            row, col, expectedValue, actualValue
                        );
                        mismatch = True;
                    end
                    else begin
                        $display(
                            "PASS: row=%0d col=%0d value=%0d",
                            row, col, actualValue
                        );
                    end

                    outputRows[col] <= outputRows[col] + 1;
                    nextRow = outputRows[col] + 1;
                end
            end

            allDone = allDone && nextRow == 4;
        end

        if (mismatch) begin
            $display("WS INT4 MATMUL: FAIL");
            $finish(1);
        end
        else if (allDone) begin
            $display("WS INT4 MATMUL: PASS");
            $finish(0);
        end
        else if (streamCycle == 15) begin
            $display("WS INT4 MATMUL: FAIL (timeout)");
            $finish(1);
        end
    endrule

endmodule

endpackage
