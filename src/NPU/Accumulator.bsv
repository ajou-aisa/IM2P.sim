package Accumulator;

import Assert::*;
import RegFile::*;
import Vector::*;
import NumericFormat::*;

// 누적 기여값은 raw psum, psum * scale, psum << scale 중 하나다.
typedef enum {
    ScaleBypass,
    ScaleMac,
    ScaleShift
} BlockScaleMode deriving (Bits, Eq, FShow);

typedef struct {
    BlockScaleMode mode;
    UInt#(TLog#(TAdd#(scaleCount, 1))) startScaleIndex;
    UInt#(TLog#(TAdd#(blockSize, 1))) startBlockOffset;
    // scale table의 index 순서는 [column][block]이다.
    Vector#(dim, Vector#(scaleCount, UInt#(8))) scales;
} BlockScaleConfig#(
    numeric type dim,
    numeric type blockSize,
    numeric type scaleCount
) deriving (Bits);

function BlockScaleConfig#(dim, blockSize, scaleCount)
        defaultBlockScaleConfig();
    return BlockScaleConfig {
        mode: ScaleBypass,
        startScaleIndex: 0,
        startBlockOffset: 0,
        scales: replicate(replicate(0))
    };
endfunction

interface Accumulator#(
    numeric type accRows,
    numeric type dim,
    numeric type blockSize,
    numeric type scaleCount,
    type format,
    numeric type precision
);
    // 시작 index/offset을 명시해 scale block이 tile 경계를 넘어가도 정렬을 유지한다.
    // accumulate=False이면 덮어쓰고, True이면 기존 row 값에 더한다.
    method Action startTile(
        UInt#(TLog#(accRows)) baseRow,
        Bool accumulate,
        BlockScaleConfig#(dim, blockSize, scaleCount) scaleConfig
    );

    // 각 column의 accepted output 수를 기준으로 scale block을 선택한다.
    method Action capture(
        Vector#(dim, Bool) valids,
        Vector#(dim, NumericElement#(format, precision)) values
    );

    // 모든 column에서 dim개 output을 저장한 다음 cycle에 True가 된다.
    method Bool tileDone;

    method Vector#(dim, NumericElement#(format, precision)) readRow(
        UInt#(TLog#(accRows)) row
    );
endinterface

module mkAccumulator(Accumulator#(
    accRows,
    dim,
    blockSize,
    scaleCount,
    format,
    precision
))
    provisos (
        Add#(1, dimMinusOne, dim),
        Add#(1, blockMinusOne, blockSize),
        Add#(1, scaleMinusOne, scaleCount),
        Add#(dim, freeRows, accRows),
        NumericFormat#(format, precision)
    );

    // 각 bank cell도 PE와 같은 NumericElement#(format, precision)을 저장한다.
    // 따라서 capture 입력과 저장 accumulator element의 비트 폭은 항상 같다.
    Vector#(
        dim,
        RegFile#(
            UInt#(TLog#(accRows)),
            NumericElement#(format, precision)
        )
    ) banks
        <- replicateM(mkRegFileFull);

    Vector#(dim, Reg#(UInt#(TLog#(accRows)))) rowCounters
        <- replicateM(mkReg(0));
    Vector#(dim, Reg#(Bool)) columnDone <- replicateM(mkReg(False));
    Vector#(
        dim,
        Reg#(UInt#(TLog#(TAdd#(scaleCount, 1))))
    ) scaleIndices <- replicateM(mkReg(0));
    Vector#(
        dim,
        Reg#(UInt#(TLog#(TAdd#(blockSize, 1))))
    ) blockOffsets <- replicateM(mkReg(0));

    Reg#(UInt#(TLog#(accRows))) baseRowReg <- mkReg(0);
    Reg#(Bool) accumulateReg <- mkReg(False);
    Reg#(Bool) tileActive <- mkReg(False);
    Reg#(BlockScaleMode) scaleModeReg <- mkReg(ScaleBypass);
    Reg#(Vector#(dim, Vector#(scaleCount, UInt#(8)))) scaleTable
        <- mkRegU;

    method Action startTile(
        UInt#(TLog#(accRows)) baseRow,
        Bool accumulate,
        BlockScaleConfig#(dim, blockSize, scaleCount) scaleConfig
    );
        dynamicAssert(
            scaleConfig.startScaleIndex < fromInteger(valueOf(scaleCount)),
            "startScaleIndex exceeds scale table"
        );
        dynamicAssert(
            scaleConfig.startBlockOffset < fromInteger(valueOf(blockSize)),
            "startBlockOffset exceeds block size"
        );
        dynamicAssert(
            baseRow <= fromInteger(valueOf(accRows) - valueOf(dim)),
            "tile exceeds accumulator row range"
        );

        baseRowReg <= baseRow;
        accumulateReg <= accumulate;
        tileActive <= True;
        scaleModeReg <= scaleConfig.mode;
        scaleTable <= scaleConfig.scales;

        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            rowCounters[col] <= 0;
            columnDone[col] <= False;
            scaleIndices[col] <= scaleConfig.startScaleIndex;
            blockOffsets[col] <= scaleConfig.startBlockOffset;
        end
    endmethod

    method Action capture(
        Vector#(dim, Bool) valids,
        Vector#(dim, NumericElement#(format, precision)) values
    );
        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            if (tileActive && valids[col] && !columnDone[col]) begin
                UInt#(TLog#(accRows)) row = baseRowReg + rowCounters[col];
                UInt#(8) scale = 0;
                Bool scaleFound = scaleModeReg == ScaleBypass;

                for (Integer index = 0;
                        index < valueOf(scaleCount);
                        index = index + 1) begin
                    if (scaleIndices[col] == fromInteger(index)) begin
                        scale = scaleTable[col][index];
                        scaleFound = True;
                    end
                end

                dynamicAssert(scaleFound, "scale index exceeds scale table");

                NumericElement#(format, precision) contribution = values[col];
                case (scaleModeReg)
                    ScaleBypass: contribution = contribution;
                    ScaleMac: contribution = numericScaleMac(?, contribution, scale);
                    ScaleShift: contribution = numericScaleShift(?, contribution, scale);
                endcase

                if (accumulateReg) begin
                    contribution = numericAdd(
                        ?,
                        banks[col].sub(row),
                        contribution
                    );
                end

                banks[col].upd(row, contribution);

                if (blockOffsets[col]
                        == fromInteger(valueOf(blockSize) - 1)) begin
                    blockOffsets[col] <= 0;
                    scaleIndices[col] <= scaleIndices[col] + 1;
                end
                else begin
                    blockOffsets[col] <= blockOffsets[col] + 1;
                end

                if (rowCounters[col] == fromInteger(valueOf(dim) - 1)) begin
                    columnDone[col] <= True;
                end
                else begin
                    rowCounters[col] <= rowCounters[col] + 1;
                end
            end
        end
    endmethod

    method Bool tileDone;
        Bool done = tileActive;

        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            done = done && columnDone[col];
        end

        return done;
    endmethod

    method Vector#(dim, NumericElement#(format, precision)) readRow(
        UInt#(TLog#(accRows)) row
    );
        Vector#(dim, NumericElement#(format, precision)) values = newVector;

        for (Integer col = 0; col < valueOf(dim); col = col + 1) begin
            values[col] = banks[col].sub(row);
        end

        return values;
    endmethod

endmodule

endpackage
