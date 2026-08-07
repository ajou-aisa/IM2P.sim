package TbDecomposedSpMM;

import Vector::*;
import SystolicArray::*;
import Accumulator::*;
import NumericFormat::*;
import GeneratedDecomposedData::*;

typedef NumericElement#(INT, 32) ComputeElement;
typedef Vector#(16, Vector#(16, ComputeElement)) ComputeTile;

// 생성 데이터는 radix-256 digit이므로 INT8이고, 계산 경계에서 같은
// INT32 element로 sign-extend한다. PE psum과 Accumulator cell도 INT32다.
function ComputeTile widenGeneratedTile(GeneratedTile8 tile);
    ComputeTile widened = newVector;

    for (Integer row = 0; row < 16; row = row + 1) begin
        widened[row] = newVector;
        for (Integer col = 0; col < 16; col = col + 1) begin
            Int#(32) value = signExtend(tile[row][col]);
            widened[row][col] = numericElement(pack(value));
        end
    end

    return widened;
endfunction

typedef enum {
    TbPreload,
    TbStartTile,
    TbRunTile,
    TbCheckRows
} TbState deriving (Bits, Eq, FShow);

module mkTbDecomposedSpMM(Empty);
    SystolicArray#(16, INT, 32) dut <- mkSystolicArray;
    Accumulator#(16, 16, 16, 4, INT, 32) accumulator <- mkAccumulator;

    Reg#(TbState) state <- mkReg(TbPreload);
    Reg#(UInt#(8)) jobIndex <- mkReg(0);
    Reg#(UInt#(3)) kTileIndex <- mkReg(0);
    Reg#(UInt#(8)) streamCycle <- mkReg(0);
    Reg#(UInt#(5)) checkRow <- mkReg(0);
    Reg#(Bool) failed <- mkReg(False);
    Reg#(Bool) jobFailed <- mkReg(False);

    rule preloadTile (state == TbPreload);
        ComputeTile weights = widenGeneratedTile(
            generatedWeightTile(jobIndex, kTileIndex)
        );
        dut.preloadWeights(weights);

        if (kTileIndex == 0) begin
            jobFailed <= False;
            if (jobIndex == 0) begin
                $display("TEST_BEGIN jobs=%0d", generatedJobCount());
            end
        end

        state <= TbStartTile;
    endrule

    rule startTile (state == TbStartTile);
        BlockScaleConfig#(16, 16, 4) scaleConfig =
            defaultBlockScaleConfig;
        accumulator.startTile(0, kTileIndex != 0, scaleConfig);
        streamCycle <= 0;
        state <= TbRunTile;
    endrule

    rule runTile (state == TbRunTile);
        GeneratedTile8 activations =
            generatedActivationTile(jobIndex, kTileIndex);

        Vector#(16, Maybe#(ComputeElement)) xLeft =
            replicate(tagged Invalid);
        Vector#(16, Bool) psumTopValid = replicate(False);

        // DIM=16 WS skew: activation은 i+k, 초기 psum은 i+j cycle에 넣는다.
        for (Integer k = 0; k < 16; k = k + 1) begin
            if (streamCycle >= fromInteger(k)
                    && streamCycle < fromInteger(k + 16)) begin
                UInt#(4) inputRow = truncate(streamCycle - fromInteger(k));
                Int#(32) value = signExtend(activations[inputRow][k]);
                xLeft[k] = tagged Valid numericElement(pack(value));
            end
        end

        for (Integer j = 0; j < 16; j = j + 1) begin
            psumTopValid[j] = streamCycle >= fromInteger(j)
                && streamCycle < fromInteger(j + 16);
        end

        dut.step(xLeft, psumTopValid);
        accumulator.capture(dut.outValid, dut.result);
        streamCycle <= streamCycle + 1;

        if (accumulator.tileDone) begin
            if (kTileIndex + 1 < generatedKTileCount(jobIndex)) begin
                kTileIndex <= kTileIndex + 1;
                state <= TbPreload;
            end
            else begin
                checkRow <= 0;
                state <= TbCheckRows;
            end
        end
        else if (streamCycle == 100) begin
            $display(
                "DECOMPOSED SPMM: FAIL timeout job=%0d k_tile=%0d",
                jobIndex,
                kTileIndex
            );
            $finish(1);
        end
    endrule

    rule checkRows (state == TbCheckRows);
        GeneratedTile32 expected = generatedGoldenTile(jobIndex);
        Vector#(16, ComputeElement) actual =
            accumulator.readRow(truncate(checkRow));
        Bool rowMismatch = False;
        UInt#(4) rowIndex = truncate(checkRow);

        // C++ comparator가 읽는 compact row 형식이다.
        $write("RTL_ROW job=%0d row=%0d", jobIndex, checkRow);
        for (Integer col = 0; col < 16; col = col + 1) begin
            $write(" %08h", pack(actual[col]));

            Int#(32) actualValue = unpack(numericBits(actual[col]));
            if (actualValue != expected[rowIndex][col]) begin
                $display(
                    "\nJOB_FAIL job=%0d stripe=%0d lane=%0d j_tile=%0d row=%0d col=%0d expected=%0d actual=%0d",
                    jobIndex,
                    generatedStripe(jobIndex),
                    generatedLane(jobIndex),
                    generatedJTile(jobIndex),
                    checkRow,
                    col,
                    expected[rowIndex][col],
                    actualValue
                );
                rowMismatch = True;
            end
        end
        $display("");

        if (rowMismatch) begin
            failed <= True;
            jobFailed <= True;
        end

        if (checkRow == 15) begin
            Bool thisJobFailed = jobFailed || rowMismatch;

            if (thisJobFailed) begin
                $display(
                    "JOB_RESULT job=%0d stripe=%0d lane=%0d j_tile=%0d k_tiles=%0d status=FAIL",
                    jobIndex,
                    generatedStripe(jobIndex),
                    generatedLane(jobIndex),
                    generatedJTile(jobIndex),
                    generatedKTileCount(jobIndex)
                );
            end
            else begin
                $display(
                    "JOB_RESULT job=%0d stripe=%0d lane=%0d j_tile=%0d k_tiles=%0d status=PASS",
                    jobIndex,
                    generatedStripe(jobIndex),
                    generatedLane(jobIndex),
                    generatedJTile(jobIndex),
                    generatedKTileCount(jobIndex)
                );
            end

            if (jobIndex + 1 == generatedJobCount()) begin
                if (failed || thisJobFailed) begin
                    $display(
                        "TEST_END status=FAIL jobs=%0d",
                        generatedJobCount()
                    );
                    $finish(1);
                end
                else begin
                    $display(
                        "TEST_END status=PASS jobs=%0d",
                        generatedJobCount()
                    );
                    $finish(0);
                end
            end
            else begin
                jobIndex <= jobIndex + 1;
                kTileIndex <= 0;
                state <= TbPreload;
            end
        end
        else begin
            checkRow <= checkRow + 1;
        end
    endrule

endmodule

endpackage
