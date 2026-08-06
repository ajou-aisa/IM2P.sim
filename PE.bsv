package PE;

interface PE;
    method Action preloadWeight(Int#(8) weight);

    method Action step(
        Bool inValid, // 입력이 유효한지
        Int#(8) x,
        Int#(32) psum
    );

    method Bool outValid; // 1-cycle 뒤 xOut과 psumOut이 유효한지
    method Int#(8) xOut; // forwarded input
    method Int#(32) psumOut; // 다음 PE로 전달할 partial sum
endinterface

module mkPE(PE);
    Reg#(Int#(8)) weightReg <- mkReg(0); // 현재 weight
    Reg#(Bool) weightLoaded <- mkReg(False); // weight가 preload되었는지

    Reg#(Int#(8)) xPipe <- mkReg(0); // 다음 PE로 넘어갈 activation
    Reg#(Int#(32)) psumPipe <- mkReg(0); // 다음 PE로 넘어갈 partial sum
    Reg#(Bool) validReg <- mkReg(False); // xPipe와 psumPipe가 유효한지

    method Action preloadWeight(Int#(8) weight); //weight preload
        weightReg <= weight;
        weightLoaded <= True;
    endmethod

    method Action step( // PE 동작
        Bool inValid,
        Int#(8) x,
        Int#(32) psum
    );
        xPipe <= x;
        validReg <= inValid && weightLoaded; // 입력과 preload된 가중치 모두 유효

        if (inValid && weightLoaded) begin
            // signed 곱셈과 32-bit psum 덧셈을 위해 sign-extend
            Int#(32) extendedX = signExtend(x);
            Int#(32) extendedW = signExtend(weightReg);

            psumPipe <= psum + extendedX * extendedW;
        end
    endmethod

    method Bool outValid = validReg;
    method Int#(8) xOut = xPipe;
    method Int#(32) psumOut = psumPipe;

endmodule

endpackage
