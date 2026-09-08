package TbBlockPosition;

import Types::*;
import WorkScheduler::*;

function MatrixExtent testOrigin(UInt#(4) scenario);
    case (scenario)
        0: return 0;
        1: return 19;
        2: return 32'hffffffff;
        3: return 32'hffffffff;
        4: return 32'h80000000;
        5: return 32'h80000001;
        6: return 32'hffffffff;
        default: return 32'hffffffff;
    endcase
endfunction

function MatrixExtent testBlockSize(UInt#(4) scenario);
    case (scenario)
        0: return 3;
        1: return 7;
        2: return 1;
        3: return 3;
        4: return 32'hffffffff;
        5: return 32'h80000000;
        6: return 32'hffffffff;
        default: return 32'h80000001;
    endcase
endfunction

module mkTbBlockPosition(Empty);
    BlockPositionIfc dut <- mkBlockPosition;
    Reg#(UInt#(4)) scenario <- mkReg(0);
    Reg#(UInt#(6)) cycles <- mkReg(0);
    Reg#(Bool) running <- mkReg(False);
    Reg#(UInt#(10)) watchdog <- mkReg(0);

    rule watch;
        watchdog <= watchdog + 1;
        if (watchdog == 500) begin
            $display("BLOCK POSITION: FAIL timeout");
            $finish(1);
        end
    endrule

    rule start (!running);
        dut.start(testOrigin(scenario), testBlockSize(scenario));
        running <= True;
        cycles <= 0;
    endrule

    rule waiting (running && !dut.ready);
        cycles <= cycles + 1;
    endrule

    rule inspect (running && dut.ready);
        MatrixExtent origin = testOrigin(scenario);
        MatrixExtent size = testBlockSize(scenario);
        if (dut.blockIndex != origin / size || dut.offset != origin % size
                || cycles != (origin < size ? 0 : 32)) begin
            $display("BLOCK POSITION: FAIL scenario=%0d quotient=%0d remainder=%0d cycles=%0d",
                scenario, dut.blockIndex, dut.offset, cycles);
            $finish(1);
        end
        running <= False;
        scenario <= scenario + 1;
        if (scenario == 7) begin
            $display("BLOCK POSITION: PASS exact quotient/remainder and 32 RTL cycles");
            $finish(0);
        end
    endrule
endmodule

endpackage
