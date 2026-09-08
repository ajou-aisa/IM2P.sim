`timescale 1ns/1ps
module tb_fifo2_diagnostic;
    reg CLK = 0;
    always #5 CLK = ~CLK;
    reg RST = 0, ENQ = 0, DEQ = 0, CLR = 0;
    reg [127:0] D_IN = 0;
    wire [127:0] D_OUT;
    wire FULL_N, EMPTY_N;
    FIFO2 #(.width(128), .guarded(1)) dut (.*);

    task step(input bit enq, input bit deq, input bit clr, input [127:0] data);
        @(negedge CLK);
        ENQ = enq && FULL_N;
        DEQ = deq && EMPTY_N;
        CLR = clr;
        D_IN = data;
        @(posedge CLK); #1;
    endtask

    initial begin
        repeat (2) @(posedge CLK);
        @(negedge CLK); RST = 1;
        if (EMPTY_N || !FULL_N) $fatal(1, "reset flags");
        step(1, 1, 0, 128'hfedcba9876543210_0123456789abcdef);
        if (!EMPTY_N || !FULL_N || D_OUT !== 128'hfedcba9876543210_0123456789abcdef)
            $fatal(1, "empty simultaneous request must enqueue only");
        step(1, 1, 0, 128'h8000000000000000_ffffffffffffffff);
        if (!EMPTY_N || !FULL_N || D_OUT !== 128'h8000000000000000_ffffffffffffffff)
            $fatal(1, "single entry simultaneous replacement");
        step(1, 0, 0, 128'h1234);
        if (FULL_N || !EMPTY_N) $fatal(1, "full flags");
        repeat (3) begin
            step(1, 0, 0, 128'hbad);
            if (FULL_N || D_OUT !== 128'h8000000000000000_ffffffffffffffff)
                $fatal(1, "backpressure changed valid payload");
        end
        step(1, 1, 0, 128'hbad);
        if (!FULL_N || !EMPTY_N || D_OUT !== 128'h1234)
            $fatal(1, "full guarded FIFO must drain with enqueue bubble");
        step(0, 1, 0, 0);
        if (EMPTY_N || !FULL_N) $fatal(1, "drain flags");
        step(1, 0, 0, 128'h5555);
        step(0, 0, 1, 0);
        if (EMPTY_N || !FULL_N) $fatal(1, "clear flags");
        step(1, 0, 0, 128'haaaa);
        if (!EMPTY_N || D_OUT !== 128'haaaa) $fatal(1, "post-clear valid payload");
        $display("FIFO2_DIAGNOSTIC PASS width=128 guarded=1");
        $finish;
    end
endmodule
