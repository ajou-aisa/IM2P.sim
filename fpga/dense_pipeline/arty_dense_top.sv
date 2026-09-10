module arty_dense_top #(parameter integer UART_DIV = 25) (
    input wire CLK100MHZ, btn0, uart_rx,
    output wire uart_tx,
    output wire [3:0] led
);
    wire clock_in, feedback, feedback_buf, clock25, clk, locked;
    IBUF input_clock (.I(CLK100MHZ), .O(clock_in));
    MMCME2_BASE #(
        .CLKIN1_PERIOD(10.0), .CLKFBOUT_MULT_F(10.0),
        .DIVCLK_DIVIDE(1), .CLKOUT0_DIVIDE_F(40.0), .STARTUP_WAIT("FALSE")
    ) mmcm (
        .CLKIN1(clock_in), .CLKFBIN(feedback_buf), .CLKFBOUT(feedback),
        .CLKOUT0(clock25), .LOCKED(locked), .RST(btn0), .PWRDWN(1'b0)
    );
    BUFG feedback_clock (.I(feedback), .O(feedback_buf));
    BUFG core_clock (.I(clock25), .O(clk));
    (* ASYNC_REG = "TRUE" *) reg [3:0] release_reset = 0;
    // Loss of lock asserts reset; release crosses four core clock edges.
    always @(posedge clk or negedge locked) begin
        if (!locked) release_reset <= 0;
        else release_reset <= {release_reset[2:0], 1'b1};
    end
    dense_uart_shell #(.UART_DIV(UART_DIV)) shell (
        .clk(clk), .reset_n(release_reset[3]), .uart_rx(uart_rx), .uart_tx(uart_tx), .led(led)
    );
endmodule
