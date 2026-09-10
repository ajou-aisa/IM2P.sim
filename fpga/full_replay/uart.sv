// Fixed resident P0 demonstration. Protocol is documented in README.md.
module p0_uart #(parameter integer DIV = 25) (
    input wire clk, reset_n, rx,
    output wire tx,
    output reg [7:0] rx_data,
    output reg rx_valid, rx_error,
    input wire [7:0] tx_data,
    input wire tx_valid,
    output wire tx_ready
);
    localparam integer CW = $clog2(DIV + 1);
    (* ASYNC_REG = "TRUE" *) reg rx_meta, rx_sync;
    reg [2:0] rx_state;
    reg [CW-1:0] rx_count, tx_count;
    reg [2:0] rx_bit;
    reg [7:0] rx_shift;
    reg tx_busy;
    reg [3:0] tx_bit;
    reg [9:0] tx_shift;
    assign tx_ready = !tx_busy;
    assign tx = tx_busy ? tx_shift[0] : 1'b1;
    always @(posedge clk) begin
        if (!reset_n) begin
            rx_meta <= 1; rx_sync <= 1;
            rx_state <= 0; rx_count <= 0; rx_bit <= 0; rx_shift <= 0;
            rx_data <= 0; rx_valid <= 0; rx_error <= 0;
            tx_busy <= 0; tx_count <= 0; tx_bit <= 0; tx_shift <= 10'h3ff;
        end else begin
            rx_meta <= rx; rx_sync <= rx_meta;
            rx_valid <= 0; rx_error <= 0;
            case (rx_state)
                0: if (!rx_sync) begin rx_state <= 1; rx_count <= DIV/2-1; end
                1: if (rx_count != 0) rx_count <= rx_count - 1;
                   else if (rx_sync) rx_state <= 0;
                   else begin rx_state <= 2; rx_count <= DIV-1; rx_bit <= 0; end
                2: if (rx_count != 0) rx_count <= rx_count - 1;
                   else begin
                       rx_shift[rx_bit] <= rx_sync; rx_count <= DIV-1;
                       if (rx_bit == 7) rx_state <= 3;
                       else rx_bit <= rx_bit + 1;
                   end
                3: if (rx_count != 0) rx_count <= rx_count - 1;
                   else if (rx_sync) begin
                       rx_data <= rx_shift; rx_valid <= 1; rx_state <= 0;
                   end else begin rx_error <= 1; rx_state <= 4; end
                4: if (rx_sync) rx_state <= 0;
                default: rx_state <= 0;
            endcase
            if (!tx_busy) begin
                if (tx_valid) begin
                    tx_busy <= 1; tx_shift <= {1'b1, tx_data, 1'b0};
                    tx_count <= DIV-1; tx_bit <= 0;
                end
            end else if (tx_count != 0) tx_count <= tx_count - 1;
            else begin
                tx_count <= DIV-1; tx_shift <= {1'b1, tx_shift[9:1]};
                if (tx_bit == 9) tx_busy <= 0;
                else tx_bit <= tx_bit + 1;
            end
        end
    end
endmodule

