package TbHostRowOffset;
import Assert::*;
import StmtFSM::*;
import Types::*;
import HostMemoryTypes::*;

module mkTbHostRowOffset(Empty);
    Reg#(UInt#(7)) shift <- mkReg(0);
    Reg#(UInt#(4)) sample <- mkReg(0);
    function MatrixExtent rowValue(UInt#(4) index);
        case (index)
            0: return 0;
            1: return 1;
            2: return 15;
            3: return 16;
            4: return 65535;
            5: return 65536;
            6: return 'h7fffffff;
            default: return maxBound;
        endcase
    endfunction
    mkAutoFSM(seq
        for (shift <= 0; shift < 64; shift <= shift + 1)
            for (sample <= 0; sample < 8; sample <= sample + 1) action
                HostStride stride = 1 << shift;
                UInt#(96) expected = zeroExtend(rowValue(sample)) * zeroExtend(stride);
                dynamicAssert(hostRowOffset(rowValue(sample), stride) == truncate(expected),
                    "power-of-two row offset differs from exact product low64");
            endaction
`ifndef IM2P_POWER_OF_TWO_STRIDES
        for (sample <= 0; sample < 8; sample <= sample + 1) action
            HostStride stride = zeroExtend(sample) * 17 + 3;
            UInt#(96) expected = zeroExtend(rowValue(sample)) * zeroExtend(stride);
            dynamicAssert(hostRowOffset(rowValue(sample), stride) == truncate(expected),
                "generic arbitrary stride changed");
        endaction
`endif
        $display("HOST_ROW_OFFSET_PASS");
    endseq);
endmodule
endpackage
