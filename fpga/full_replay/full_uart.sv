// FULL replay v1. One owned logical matrix; UART packets never name K fragments.
module full_uart_shell #(parameter integer UART_DIV = 25) (
    input wire clk, reset_n, uart_rx,
    output wire uart_tx,
    output wire [3:0] led
);
    localparam RX=0, LOAD=1, CHECK=2, START=3, RUN=4,
               HEADER=5, REQUEST=6, RESPONSE=7, OUTPUT=8, CRC=9, DRAIN=10;
    reg [3:0] state;
    reg [255:0] header;
    reg [15:0] index;
    reg [31:0] checksum, received_crc, send_crc;
    reg [127:0] assemble, load_value;
    reg load_pending, load_weight;
    reg [8:0] load_word;
    reg [31:0] wait_ticks;
    reg [7:0] status;
    reg owned, poison, core_reset;
    reg [31:0] generation_counter = 0;
    reg [31:0] generation;
    reg [63:0] run_id;
    reg [5:0] rows, columns;
    reg [6:0] reduction;
    reg [1:0] tiles;
    reg [15:0] input_bytes, a_bytes, output_words;
    reg [15:0] words_sent;
    reg [8:0] output_address;
    reg [1:0] output_tile;
    reg [511:0] output_buffer;
    reg [6:0] send_index;
    reg [31:0] reply_bytes;
    reg [63:0] cycles, fragments, works, a_reads, w_reads, writes, acks;
    wire [7:0] rx_data;
    wire rx_valid, rx_error, tx_ready;
    reg [7:0] tx_data;
    wire tx_valid = state == HEADER || state == OUTPUT || state == CRC;
    wire load_ready, start_ready, done, error, request_ready, output_valid;
    wire consume_ready, ack_ready;
    wire [511:0] output_values;
    wire [63:0] core_cycles, core_fragments, core_works, core_a, core_w, core_writes, core_acks;
    wire [31:0] magic = header[31:0];
    wire [7:0] op = header[47:40];
    wire [15:0] m = header[175:160], n = header[191:176], k = header[207:192];
    wire [31:0] length = header[255:224];
    wire shape_ok = m > 0 && m <= 32 && n > 0 && n <= 48 &&
                   (k == 32 || k == 64 || k == 96);
    wire control_ok = op != 1 && length == 0 && m == 0 && n == 0 && k == 0;
    wire request_header_ok = magic == 32'h31524649 && header[39:32] == 1 &&
        header[63:48] == 16'h0810 && header[223:208] == 0 &&
        (control_ok || (op == 1 && shape_ok && length == (m * 128 + k * 64)));
    wire do_load = state == LOAD && load_pending && load_ready;
    wire do_start = state == START && start_ready;
    wire do_request = state == REQUEST && request_ready;
    wire do_consume = state == RESPONSE && output_valid && consume_ready;
    wire do_ack = state == CHECK && request_header_ok && !poison &&
                  received_crc == ~checksum && op == 2 && owned &&
                  header[159:128] == generation && header[127:64] == run_id && ack_ready;
    wire [767:0] reply = {64'd0, acks, writes, w_reads, a_reads, works, fragments, cycles,
        16'd0, 9'd0, reduction, 10'd0, columns, 10'd0, rows, reply_bytes,
        generation, run_id, 16'h0810, status, 8'd1, 32'h3152464f};

    function automatic [31:0] crc_byte(input [31:0] c, input [7:0] b);
        reg [31:0] v;
        integer bit_index;
        begin
            v = c ^ {24'd0,b};
            for (bit_index=0; bit_index<8; bit_index=bit_index+1)
                v = (v >> 1) ^ (v[0] ? 32'hedb88320 : 32'd0);
            crc_byte = v;
        end
    endfunction

    p0_uart #(.DIV(UART_DIV)) uart (.clk(clk), .reset_n(reset_n), .rx(uart_rx),
        .tx(uart_tx), .rx_data(rx_data), .rx_valid(rx_valid), .rx_error(rx_error),
        .tx_data(tx_data), .tx_valid(tx_valid), .tx_ready(tx_ready));
    mkFullReplay core (.CLK(clk), .RST_N(reset_n && !core_reset),
        .load_weight(load_weight), .load_word(load_word), .load_values(load_value),
        .EN_load(do_load), .RDY_load(load_ready),
        .start_m(rows), .start_n(columns), .start_k(reduction),
        .start_job(generation), .start_contextId(run_id), .EN_start(do_start), .RDY_start(start_ready),
        .done(done), .protocolError(error), .cycles(core_cycles), .fragments(core_fragments),
        .works(core_works), .activationRequests(core_a), .weightRequests(core_w),
        .outputWrites(core_writes), .outputAcks(core_acks),
        .requestOutput_word(output_address), .EN_requestOutput(do_request), .RDY_requestOutput(request_ready),
        .outputValid(output_valid), .outputResponse(output_values), .RDY_outputResponse(),
        .EN_consumeOutput(do_consume), .RDY_consumeOutput(consume_ready),
        .EN_acknowledge(do_ack), .RDY_acknowledge(ack_ready));

    assign led = {poison, owned, state == RUN, reset_n};
    always @* begin
        if (state == HEADER) tx_data = reply[send_index*8 +: 8];
        else if (state == OUTPUT) tx_data = output_buffer[send_index*8 +: 8];
        else tx_data = ((~send_crc ^ (poison && status == 0 ? 32'h80000000 : 0)) >> (send_index*8)) & 8'hff;
    end
    always @(posedge clk) begin
        if (!reset_n) begin
            state <= RX; index <= 0; checksum <= 32'hffffffff; received_crc <= 0;
            header <= 0; load_pending <= 0; wait_ticks <= 0; status <= 0;
            owned <= 0; poison <= 0; core_reset <= 0; generation <= generation_counter;
            run_id <= 0; rows <= 0; columns <= 0; reduction <= 0;
            tiles <= 0; input_bytes <= 0; a_bytes <= 0; output_words <= 0;
            words_sent <= 0; output_address <= 0; output_tile <= 0;
            send_index <= 0; reply_bytes <= 0; send_crc <= 32'hffffffff;
            cycles <= 0; fragments <= 0; works <= 0; a_reads <= 0; w_reads <= 0; writes <= 0; acks <= 0;
            assemble <= 0; load_value <= 0; load_weight <= 0; load_word <= 0; output_buffer <= 0;
        end else begin
            core_reset <= 0;
            if (rx_error) poison <= 1;
            if (state == RX || state == LOAD) begin
                if (rx_valid) wait_ticks <= 0;
                else if (index != 0 || state == LOAD) wait_ticks <= wait_ticks + 1;
                if (wait_ticks == 32'd262143 || rx_error) begin
                    status <= 8; state <= HEADER; reply_bytes <= 0; send_index <= 0;
                    send_crc <= 32'hffffffff; poison <= 1; load_pending <= 0;
                end else if (rx_valid) begin
                    if (state == RX && index < 32) begin
                        header[index*8 +: 8] <= rx_data;
                        checksum <= crc_byte(checksum, rx_data);
                        index <= index + 1;
                    end else if (state == LOAD && index < input_bytes) begin
                        assemble <= {rx_data, assemble[127:8]};
                        checksum <= crc_byte(checksum, rx_data);
                        if (index[3:0] == 15) begin
                            if (load_pending) poison <= 1;
                            load_value <= {rx_data, assemble[127:8]};
                            load_weight <= index >= a_bytes;
                            load_word <= index >= a_bytes ? (index-a_bytes) >> 4 : index >> 4;
                            load_pending <= 1;
                        end
                        index <= index + 1;
                    end else begin
                        received_crc <= {rx_data, received_crc[31:8]};
                        index <= index + 1;
                        if ((state == RX && index == 35) ||
                            (state == LOAD && index == input_bytes+3)) state <= CHECK;
                    end
                end
                if (state == RX && index == 32 && op == 1) begin
                    if (!request_header_ok || owned || poison) begin
                        status <= !request_header_ok ? 2 : owned ? 5 : 8;
                        state <= HEADER; send_index <= 0; reply_bytes <= 0; send_crc <= 32'hffffffff;
                        poison <= 1;
                    end else begin
                        input_bytes <= length[15:0]; a_bytes <= m << 7;
                        state <= LOAD; index <= 0;
                    end
                end
            end
            if (do_load) load_pending <= 0;
            if (state == CHECK) begin
                send_index <= 0; send_crc <= 32'hffffffff; reply_bytes <= 0;
                state <= HEADER; wait_ticks <= 0;
                if (!request_header_ok) status <= 2;
                else if (received_crc != ~checksum) begin status <= 3; poison <= 1; end
                else if (op == 3) begin
                    core_reset <= 1; owned <= 0; poison <= 0; status <= 0;
                    run_id <= header[127:64]; generation <= generation_counter;
                end else if (op == 0) begin
                    if (owned) status <= 5;
                    else begin
                        status <= poison ? 8 : 0; run_id <= header[127:64]; generation <= generation_counter;
                        rows <= 32; columns <= 48; reduction <= 96;
                    end
                end else if (poison) status <= 8;
                else if (op == 1) begin
                    if (owned || load_pending) status <= 5;
                    else if (generation_counter == 32'hffffffff ||
                        header[159:128] != generation_counter+1 || header[127:64] == 0) status <= 6;
                    else begin
                        generation_counter <= generation_counter+1;
                        generation <= generation_counter+1; run_id <= header[127:64];
                        rows <= m[5:0]; columns <= n[5:0]; reduction <= k[6:0];
                        tiles <= (n+15) >> 4;
                        output_words <= m * ((n+15) >> 4) * (k >> 5);
                        status <= 0; state <= START; owned <= 1;
                    end
                end else if (op == 2) begin
                    if (!do_ack) status <= 6;
                    else begin owned <= 0; status <= 0; end
                end else status <= 2;
            end
            if (do_start) begin state <= RUN; wait_ticks <= 0; end
            if (state == START || state == RUN) begin
                wait_ticks <= wait_ticks+1;
                if (rx_valid) poison <= 1;
                if (wait_ticks == 32'd16777215) begin
                    state <= HEADER; status <= 9; poison <= 1; reply_bytes <= 0;
                end else if (state == RUN && done) begin
                    status <= error || poison ? 7 : 0;
                    reply_bytes <= error || poison ? 0 : {16'd0,output_words} << 6;
                    cycles <= core_cycles; fragments <= core_fragments; works <= core_works;
                    a_reads <= core_a; w_reads <= core_w; writes <= core_writes; acks <= core_acks;
                    state <= HEADER; send_index <= 0; send_crc <= 32'hffffffff;
                    output_address <= 0; output_tile <= 0; words_sent <= 0;
                end
            end
            if (tx_valid && tx_ready) begin
                if (state != CRC) send_crc <= crc_byte(send_crc, tx_data);
                send_index <= send_index + 1;
                if (state == HEADER && send_index == 95) begin
                    send_index <= 0; state <= reply_bytes == 0 ? CRC : REQUEST;
                end
                if (state == OUTPUT && send_index == 63) begin
                    send_index <= 0; words_sent <= words_sent+1;
                    if (output_tile+1 == tiles) begin
                        output_address <= output_address + (4 - output_tile);
                        output_tile <= 0;
                    end else begin output_address <= output_address+1; output_tile <= output_tile+1; end
                    state <= words_sent+1 == output_words ? CRC : REQUEST;
                end
                if (state == CRC && send_index == 3) state <= DRAIN;
            end
            if (do_request) state <= RESPONSE;
            if (do_consume) begin output_buffer <= output_values; state <= OUTPUT; end
            if (state == DRAIN && tx_ready) begin
                state <= RX; index <= 0; checksum <= 32'hffffffff;
                wait_ticks <= 0; status <= 0; header <= 0;
            end
        end
    end
endmodule
