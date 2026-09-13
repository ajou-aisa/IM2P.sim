package WindowBuffer;

import BRAMCore::*;

// The transport fills a complete rectangular window, in 128-bit words. The
// pending core request owns the slot until its response is consumed. Each
// channel has its own slot, so current/lookahead A/W/S requests cannot evict
// another channel's pending data.
interface WindowRefillIfc;
    method Bool miss;
    method UInt#(32) originRow;
    method UInt#(32) originWord;
    method UInt#(32) validRows;
    method UInt#(16) words;
    method UInt#(32) generation;
    method Action load(UInt#(32) generation, UInt#(16) index, Bit#(128) values);
    method Action commit(UInt#(32) generation);
endinterface

interface WindowBufferIfc;
    method Action configure(UInt#(4) rowLog, UInt#(4) wordLog);
    method Action request(UInt#(32) row, UInt#(32) word, UInt#(32) rowLimit, UInt#(64) tag);
    method Bool responseValid;
    method Bit#(128) response;
    method UInt#(64) responseTag;
    method Action consume;
    method Bool error;
    interface WindowRefillIfc refill;
endinterface

module mkWindowBuffer#(Integer depth)(WindowBufferIfc);
    BRAM_PORT#(UInt#(8), Bit#(128)) memory <- mkBRAMCore1(depth, False);
    Reg#(UInt#(8)) rowMaskReg <- mkReg(0);
    Reg#(UInt#(8)) wordMaskReg <- mkReg(0);
    Reg#(UInt#(4)) wordLogReg <- mkReg(0);
    Reg#(UInt#(16)) sizeReg <- mkReg(0);
    Reg#(UInt#(32)) rowOrigin <- mkReg(0);
    Reg#(UInt#(32)) wordOrigin <- mkReg(0);
    Reg#(UInt#(32)) filledRowLimit <- mkReg(0);
    Reg#(UInt#(32)) generationReg <- mkReg(0);
    Reg#(UInt#(16)) loaded <- mkReg(0);
    Reg#(Bool) valid <- mkReg(False);
    Reg#(Bool) missing <- mkReg(False);
    Reg#(Bool) pending <- mkReg(False);
    Reg#(Bool) reading <- mkReg(False);
    Reg#(Bool) ready <- mkReg(False);
    Reg#(Bool) failed <- mkReg(False);
    Reg#(UInt#(8)) addressReg <- mkReg(0);
    Reg#(UInt#(64)) tagReg <- mkReg(0);
    Reg#(Bit#(128)) valuesReg <- mkReg(0);

    rule readWord (pending && !missing && !reading && !ready && !failed);
        memory.put(False, addressReg, ?);
        reading <= True;
    endrule
    rule capture (reading);
        valuesReg <= memory.read;
        reading <= False;
        ready <= True;
    endrule

    method Action configure(UInt#(4) rowLog, UInt#(4) wordLog)
            if (!pending && !missing && !failed);
        UInt#(5) sizeLog = zeroExtend(rowLog) + zeroExtend(wordLog);
        UInt#(16) size = 1 << sizeLog;
        if (rowLog > 6 || wordLog > 4 || size > fromInteger(depth))
            failed <= True;
        else begin
            rowMaskReg <= (1 << rowLog) - 1;
            wordMaskReg <= (1 << wordLog) - 1;
            wordLogReg <= wordLog;
            sizeReg <= size;
            valid <= False;
        end
    endmethod
    method Action request(UInt#(32) row, UInt#(32) word, UInt#(32) rowLimit, UInt#(64) tag)
            if (!pending && !missing && !failed && sizeReg != 0);
        // Configuration bounds the local address to eight bits. Global origins
        // retain all 32 bits; a transport window never changes semantic bounds.
        UInt#(32) rowBase = row & ~zeroExtend(rowMaskReg);
        UInt#(32) wordBase = word & ~zeroExtend(wordMaskReg);
        UInt#(8) localRow = truncate(row) & rowMaskReg;
        UInt#(8) localWord = truncate(word) & wordMaskReg;
        addressReg <= (localRow << wordLogReg) | localWord;
        tagReg <= tag;
        pending <= True;
        Bool newWindow = !valid || rowBase != rowOrigin || wordBase != wordOrigin || row >= filledRowLimit;
        if (row >= rowLimit || (newWindow && generationReg == maxBound)) failed <= True;
        if (newWindow) begin
            rowOrigin <= rowBase;
            wordOrigin <= wordBase;
            filledRowLimit <= rowLimit;
            loaded <= 0;
            valid <= False;
            missing <= True;
            if (generationReg != maxBound) generationReg <= generationReg + 1;
        end
    endmethod
    method Bool responseValid = ready && !failed;
    method Bit#(128) response if (ready && !failed);
        return valuesReg;
    endmethod
    method UInt#(64) responseTag if (ready && !failed);
        return tagReg;
    endmethod
    method Action consume if (ready && !failed);
        ready <= False;
        pending <= False;
    endmethod
    method Bool error = failed;
    interface WindowRefillIfc refill;
        method Bool miss = missing && !failed;
        method UInt#(32) originRow = rowOrigin;
        method UInt#(32) originWord = wordOrigin;
        method UInt#(32) validRows;
            UInt#(32) rows = zeroExtend(rowMaskReg) + 1;
            UInt#(32) available = filledRowLimit - rowOrigin;
            return available < rows ? available : rows;
        endmethod
        method UInt#(16) words = sizeReg;
        method UInt#(32) generation = generationReg;
        method Action load(UInt#(32) generation, UInt#(16) index, Bit#(128) values)
                if (missing && !failed);
            if (generation != generationReg || index != loaded || loaded >= sizeReg)
                failed <= True;
            else begin
                memory.put(True, truncate(index), values);
                loaded <= loaded + 1;
            end
        endmethod
        method Action commit(UInt#(32) generation) if (missing && !failed);
            if (generation != generationReg || loaded != sizeReg)
                failed <= True;
            else begin
                missing <= False;
                valid <= True;
            end
        endmethod
    endinterface
endmodule
endpackage
