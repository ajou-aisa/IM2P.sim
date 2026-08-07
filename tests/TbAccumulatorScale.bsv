package TbAccumulatorScale;

import Vector::*;
import Accumulator::*;
import NumericFormat::*;

typedef NumericElement#(INT, 32) AccumulatorElement;

typedef enum {
    StartMac,
    FeedMac,
    CheckMac,
    StartShift,
    FeedShift,
    CheckShift
} TestState deriving (Bits, Eq, FShow);

module mkTbAccumulatorScale(Empty);
    Accumulator#(8, 4, 2, 4, INT, 32) macAccumulator <- mkAccumulator;
    Accumulator#(12, 4, 8, 2, INT, 32) shiftAccumulator <- mkAccumulator;

    Reg#(TestState) state <- mkReg(StartMac);
    Reg#(UInt#(4)) cycle <- mkReg(0);
    Reg#(UInt#(4)) checkRow <- mkReg(0);
    Reg#(UInt#(2)) shiftTile <- mkReg(0);
    Reg#(Bool) macAccumulating <- mkReg(False);
    Vector#(4, Reg#(UInt#(3))) macTokens <- replicateM(mkReg(0));

    rule startMac (state == StartMac);
        BlockScaleConfig#(4, 2, 4) scaleConfig = defaultBlockScaleConfig;
        scaleConfig.mode = ScaleMac;
        for (Integer col = 0; col < 4; col = col + 1) begin
            scaleConfig.scales[col][0] = fromInteger(col + 2);
            scaleConfig.scales[col][1] = fromInteger(col + 4);
        end
        macAccumulator.startTile(0, macAccumulating, scaleConfig);
        for (Integer col = 0; col < 4; col = col + 1) begin
            macTokens[col] <= 0;
        end
        cycle <= 0;
        state <= FeedMac;
    endrule

    rule feedMac (
        state == FeedMac && !macAccumulator.tileDone && cycle < 12
    );
        Vector#(4, Bool) valids = replicate(False);
        Vector#(4, AccumulatorElement) values =
            replicate(numericElement(0));
        UInt#(1) parity = truncate(cycle);
        for (Integer col = 0; col < 4; col = col + 1) begin
            if (parity == fromInteger(col % 2)) begin
                valids[col] = True;
                Int#(32) value = unpack(zeroExtend(pack(macTokens[col] + 1)));
                values[col] = numericElement(pack(value));
                macTokens[col] <= macTokens[col] + 1;
            end
        end
        macAccumulator.capture(valids, values);
        cycle <= cycle + 1;
    endrule

    rule timeoutMac (state == FeedMac && !macAccumulator.tileDone && cycle == 12);
        $display("BLOCK SCALE: FAIL mode=MAC timeout");
        $finish(1);
    endrule

    rule finishMacFeed (state == FeedMac && macAccumulator.tileDone);
        if (macAccumulating) begin
            checkRow <= 0;
            state <= CheckMac;
        end
        else begin
            macAccumulating <= True;
            state <= StartMac;
        end
    endrule

    rule checkMac (state == CheckMac);
        Vector#(4, AccumulatorElement) values =
            macAccumulator.readRow(truncate(checkRow));
        Bool mismatch = False;
        for (Integer col = 0; col < 4; col = col + 1) begin
            Int#(32) scale = checkRow < 2
                ? fromInteger(col + 2)
                : fromInteger(col + 4);
            Int#(32) rowValue = unpack(zeroExtend(pack(checkRow + 1)));
            Int#(32) expected = 2 * rowValue * scale;
            Int#(32) actual = unpack(numericBits(values[col]));
            if (actual != expected) begin
                $display(
                    "BLOCK SCALE: FAIL mode=MAC row=%0d col=%0d expected=%0d actual=%0d",
                    checkRow,
                    col,
                    expected,
                    actual
                );
                mismatch = True;
            end
        end
        if (mismatch) begin
            $finish(1);
        end
        else if (checkRow == 3) begin
            shiftTile <= 0;
            state <= StartShift;
        end
        else begin
            checkRow <= checkRow + 1;
        end
    endrule

    rule startShift (state == StartShift);
        BlockScaleConfig#(4, 8, 2) scaleConfig = defaultBlockScaleConfig;
        scaleConfig.mode = ScaleShift;
        // Tile 1은 block 0을 이어가고 Tile 2는 block 0에서 block 1로 넘어간다.
        scaleConfig.startBlockOffset = shiftTile == 1
            ? 4
            : (shiftTile == 2 ? 6 : 0);
        for (Integer col = 0; col < 4; col = col + 1) begin
            scaleConfig.scales[col][0] = fromInteger(col);
            scaleConfig.scales[col][1] = fromInteger(col + 1);
        end
        shiftAccumulator.startTile(zeroExtend(shiftTile) << 2, False, scaleConfig);
        cycle <= 0;
        state <= FeedShift;
    endrule

    rule feedShift (
        state == FeedShift && !shiftAccumulator.tileDone && cycle < 8
    );
        Vector#(4, AccumulatorElement) values =
            replicate(numericElement(0));
        for (Integer col = 0; col < 4; col = col + 1) begin
            UInt#(4) rowValue = (zeroExtend(shiftTile) << 2) + cycle + 1;
            Int#(32) value = unpack(zeroExtend(pack(rowValue)));
            values[col] = numericElement(pack(value));
        end
        shiftAccumulator.capture(replicate(True), values);
        cycle <= cycle + 1;
    endrule

    rule timeoutShift (
        state == FeedShift && !shiftAccumulator.tileDone && cycle == 8
    );
        $display("BLOCK SCALE: FAIL mode=SHIFT timeout");
        $finish(1);
    endrule

    rule finishShiftFeed (state == FeedShift && shiftAccumulator.tileDone);
        if (shiftTile == 2) begin
            checkRow <= 0;
            state <= CheckShift;
        end
        else begin
            shiftTile <= shiftTile + 1;
            state <= StartShift;
        end
    endrule

    rule checkShift (state == CheckShift);
        Vector#(4, AccumulatorElement) values =
            shiftAccumulator.readRow(truncate(checkRow));
        Bool mismatch = False;
        for (Integer col = 0; col < 4; col = col + 1) begin
            UInt#(8) amount = fromInteger(col) + (checkRow >= 10 ? 1 : 0);
            Int#(32) expected = unpack(zeroExtend(pack(checkRow + 1)));
            expected = expected << amount;
            Int#(32) actual = unpack(numericBits(values[col]));
            if (actual != expected) begin
                $display(
                    "BLOCK SCALE: FAIL mode=SHIFT row=%0d col=%0d expected=%0d actual=%0d",
                    checkRow,
                    col,
                    expected,
                    actual
                );
                mismatch = True;
            end
        end
        if (mismatch) begin
            $finish(1);
        end
        else if (checkRow == 11) begin
            $display("BLOCK SCALE: PASS");
            $finish(0);
        end
        else begin
            checkRow <= checkRow + 1;
        end
    endrule
endmodule

endpackage
