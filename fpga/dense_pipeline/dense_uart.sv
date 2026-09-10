// IFR2: one logical GEMM, existing core scheduling, bounded full A/C backing.
module dense_uart_shell #(parameter integer UART_DIV = 25) (
    input wire clk, reset_n, uart_rx,
    output wire uart_tx,
    output wire [3:0] led
);
    localparam RX=0, LOAD=1, CHECK=2, START=3, RUN=4, HEADER=5,
               REQUEST=6, RESPONSE=7, OUTPUT=8, CRC=9, DRAIN=10, PUBLISH=11;
    reg [3:0] state;
    reg [255:0] header;
    reg [15:0] index, input_bytes, a_bytes;
    reg [31:0] checksum, received_crc, send_crc;
    reg [127:0] assemble, load_value;
    reg load_pending, load_weight;
    reg [11:0] load_word;
    reg [19:0] frame_wait, progress_wait;
    reg [31:0] previous_progress;
    reg [7:0] status, reply_op, flags;
    reg owned, striped, poison, core_reset;
    // Configuration initializes generation. Button/ABORT reset never rolls it back.
    reg [31:0] generation_counter = 0;
    reg [31:0] generation;
    reg [63:0] run_id;
    reg [8:0] rows, stripe_rows, next_row;
    reg [5:0] columns;
    reg [6:0] reduction;
    reg [15:0] next_stripe, retired_stripes;
    reg [1:0] tiles, output_tile, output_block;
    reg [8:0] output_row, raw_row_begin, raw_rows;
    reg [12:0] output_words, words_sent;
    reg [511:0] output_buffer;
    reg [7:0] send_index;
    reg [31:0] reply_bytes;
    reg [15:0] reply_stripe, reply_row, reply_rows;
    reg [63:0] cycles, fragments, works, a_reads, w_reads, writes, acks;
    reg [63:0] publish_cycle, completion_cycle, first_a, first_a_rows, host_wait, overlap;
    wire [7:0] rx_data;
    wire rx_valid, rx_error, tx_ready;
    reg [7:0] tx_data;
    wire tx_valid = state == HEADER || state == OUTPUT || state == CRC;
    wire load_a_ready, load_w_ready, start_ready, done, error, request_ready, output_valid;
    wire consume_ready, ack_ready, publish_ready, stripe_ack_ready, stripe_valid;
    wire [511:0] output_values;
    wire [63:0] core_cycles, core_live, core_fragments, core_works, core_a, core_w, core_writes, core_acks;
    wire [63:0] core_pub_cycle, core_complete_cycle, core_first_a, core_wait, core_overlap;
    wire [31:0] core_stripe, core_row, core_rows;
    wire [8:0] published_rows, completed_rows, core_first_a_rows;
    wire [7:0] op = header[47:40];
    wire [15:0] x = header[175:160], y = header[191:176], z = header[207:192], t = header[223:208];
    wire [31:0] length = header[255:224];
    wire identity_ok = header[159:128] == generation && header[127:64] == run_id;
    wire shape_ok = x > 0 && x <= 336 && y > 0 && y <= 48 && (z == 32 || z == 64 || z == 96);
    wire base_ok = header[31:0] == 32'h32524649 && header[39:32] == 2 && header[63:48] == 16'h0810;
    wire control_ok = (op == 0 || op == 2 || op == 3 || op == 6 || op == 7) &&
                      length == 0 && x == 0 && y == 0 && z == 0 && t == 0;
    wire new_job_ok = !owned && generation_counter != 32'hffffffff &&
                      header[159:128] == generation_counter + 1 && header[127:64] != 0;
    wire full_ok = op == 1 && shape_ok && t == 0 && length == x*128 + z*64;
    wire begin_ok = op == 4 && shape_ok && t > 0 && t <= 336 && t[3:0] == 0 && length == z*64;
    wire [9:0] remaining_rows = {1'b0,rows} - {1'b0,next_row};
    wire publication_ok = op == 5 && owned && striped && identity_ok && x == next_row &&
        next_row < rows && y == (remaining_rows < stripe_rows ? remaining_rows : stripe_rows) &&
        z == next_stripe && t == {15'd0,z[0]} && length == y*128;
    wire request_header_ok = base_ok && (control_ok || full_ok || begin_ok || publication_ok);
    wire do_load_a = state == LOAD && load_pending && !load_weight && load_a_ready;
    wire do_load_w = state == LOAD && load_pending && load_weight && load_w_ready;
    wire do_start = state == START && start_ready;
    wire do_publish = state == PUBLISH && publish_ready && !poison;
    wire do_request = state == REQUEST && request_ready;
    wire do_consume = state == RESPONSE && output_valid && consume_ready;
    wire do_ack = state == CHECK && request_header_ok && !poison && received_crc == ~checksum &&
                  op == 2 && owned && identity_ok && ack_ready && !stripe_valid &&
                  (!striped || (next_row == rows && retired_stripes == next_stripe));
    wire do_stripe_ack = state == DRAIN && tx_ready && flags[0] && stripe_ack_ready;
    wire [11:0] output_address = ((output_block * rows + raw_row_begin + output_row) << 2) + output_tile;
    wire [31:0] core_progress = {16'd0,core_a[15:0]} + {16'd0,core_w[15:0]} +
                               {16'd0,core_acks[15:0]} + {16'd0,core_fragments[15:0]};
    wire [1151:0] reply = {overlap, host_wait, first_a_rows, first_a, completion_cycle, publish_cycle,
        reply_rows, reply_row, reply_stripe, flags, reply_op,
        acks, writes, w_reads, a_reads, works, fragments, cycles,
        16'd0, 9'd0, reduction, 10'd0, columns, 7'd0, rows, reply_bytes,
        generation, run_id, 16'h0810, status, 8'd2, 32'h3252464f};

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
    task sample_counters;
        begin
            cycles <= done ? core_cycles : core_live;
            fragments <= core_fragments; works <= core_works;
            a_reads <= core_a; w_reads <= core_w; writes <= core_writes; acks <= core_acks;
            first_a <= core_first_a; first_a_rows <= {55'd0,core_first_a_rows};
            host_wait <= core_wait; overlap <= core_overlap;
        end
    endtask
    p0_uart #(.DIV(UART_DIV)) uart (.clk(clk), .reset_n(reset_n), .rx(uart_rx),
        .tx(uart_tx), .rx_data(rx_data), .rx_valid(rx_valid), .rx_error(rx_error),
        .tx_data(tx_data), .tx_valid(tx_valid), .tx_ready(tx_ready));
    mkDensePipeline core (.CLK(clk), .RST_N(reset_n && !core_reset),
        .loadActivation_word(load_word), .loadActivation_values(load_value), .EN_loadActivation(do_load_a), .RDY_loadActivation(load_a_ready),
        .loadWeight_word(load_word[8:0]), .loadWeight_values(load_value), .EN_loadWeight(do_load_w), .RDY_loadWeight(load_w_ready),
        .start_striped(striped), .start_m(rows), .start_n(columns), .start_k(reduction),
        .start_job(generation), .start_contextId(run_id), .EN_start(do_start), .RDY_start(start_ready),
        .publish_rowBegin(x[8:0]), .publish_rowCount(y[8:0]), .EN_publish(do_publish), .RDY_publish(publish_ready),
        .done(done), .protocolError(error), .cycles(core_cycles), .liveCycles(core_live), .hostWaitCycles(core_wait), .overlapCycles(core_overlap),
        .fragments(core_fragments), .works(core_works), .activationRequests(core_a), .weightRequests(core_w),
        .outputWrites(core_writes), .outputAcks(core_acks), .publishedRows(published_rows), .completedRows(completed_rows),
        .firstActivationCycle(core_first_a), .firstActivationPublishedRows(core_first_a_rows),
        .stripeCompletionValid(stripe_valid), .stripeId(core_stripe), .RDY_stripeId(),
        .stripeRowBegin(core_row), .RDY_stripeRowBegin(), .stripeRowCount(core_rows), .RDY_stripeRowCount(),
        .stripePublishCycle(core_pub_cycle), .RDY_stripePublishCycle(),
        .stripeCompletionCycle(core_complete_cycle), .RDY_stripeCompletionCycle(),
        .EN_acknowledgeStripe(do_stripe_ack), .RDY_acknowledgeStripe(stripe_ack_ready),
        .requestOutput_word(output_address), .EN_requestOutput(do_request), .RDY_requestOutput(request_ready),
        .outputValid(output_valid), .outputResponse(output_values), .RDY_outputResponse(),
        .EN_consumeOutput(do_consume), .RDY_consumeOutput(consume_ready),
        .EN_acknowledge(do_ack), .RDY_acknowledge(ack_ready));
    assign led = {poison, owned, owned && !done, reset_n};
    always @* begin
        if (state == HEADER) tx_data = reply[send_index*8 +: 8];
        else if (state == OUTPUT) tx_data = output_buffer[send_index*8 +: 8];
        else tx_data = ((~send_crc ^ (poison && status == 0 ? 32'h80000000 : 0)) >> (send_index*8)) & 8'hff;
    end
    always @(posedge clk) begin
        if (!reset_n) begin
            state <= RX; header <= 0; index <= 0; checksum <= 32'hffffffff; received_crc <= 0;
            send_crc <= 32'hffffffff; load_pending <= 0; frame_wait <= 0; progress_wait <= 0; previous_progress <= 0;
            status <= 0; reply_op <= 0; flags <= 0; owned <= 0; striped <= 0; poison <= 0; core_reset <= 0;
            generation <= generation_counter; run_id <= 0; rows <= 0; columns <= 0; reduction <= 0;
            stripe_rows <= 0; next_row <= 0; next_stripe <= 0; retired_stripes <= 0; tiles <= 0;
            input_bytes <= 0; a_bytes <= 0; output_words <= 0; words_sent <= 0;
            output_tile <= 0; output_block <= 0; output_row <= 0; raw_row_begin <= 0; raw_rows <= 0;
            send_index <= 0; reply_bytes <= 0; reply_stripe <= 0; reply_row <= 0; reply_rows <= 0;
            cycles <= 0; fragments <= 0; works <= 0; a_reads <= 0; w_reads <= 0; writes <= 0; acks <= 0;
            publish_cycle <= 0; completion_cycle <= 0; first_a <= 0; first_a_rows <= 0; host_wait <= 0; overlap <= 0;
            assemble <= 0; load_value <= 0; load_weight <= 0; load_word <= 0; output_buffer <= 0;
        end else begin
            core_reset <= 0;
            if (rx_error || (owned && error)) poison <= 1;
            // 2^20 clocks = 41.943 ms between progress events. Producer gaps
            // and pending completion drain are excluded; UART framing is separate.
            if (!owned || done || stripe_valid || (striped && published_rows == completed_rows) ||
                core_progress != previous_progress) progress_wait <= 0;
            else if (!poison) progress_wait <= progress_wait + 1;
            previous_progress <= core_progress;
            if (&progress_wait) poison <= 1;
            if (state == RX || state == LOAD) begin
                if (rx_valid) frame_wait <= 0;
                else if (index != 0 || state == LOAD) frame_wait <= frame_wait + 1;
                if (&frame_wait || rx_error) begin
                    status <= 8; state <= HEADER; reply_op <= op; reply_bytes <= 0; send_index <= 0;
                    send_crc <= 32'hffffffff; poison <= 1; load_pending <= 0;
                end else if (rx_valid) begin
                    if (state == RX && index < 32) begin
                        header[index*8 +: 8] <= rx_data; checksum <= crc_byte(checksum, rx_data); index <= index + 1;
                    end else if (state == LOAD && index < input_bytes) begin
                        assemble <= {rx_data, assemble[127:8]}; checksum <= crc_byte(checksum, rx_data);
                        if (index[3:0] == 15) begin
                            if (load_pending) poison <= 1;
                            load_value <= {rx_data, assemble[127:8]};
                            load_weight <= op == 4 || (op == 1 && index >= a_bytes);
                            load_word <= op == 5 ? (x << 3) + (index >> 4) :
                                         op == 4 ? index >> 4 : index >= a_bytes ? (index-a_bytes) >> 4 : index >> 4;
                            load_pending <= 1;
                        end
                        index <= index + 1;
                    end else begin
                        received_crc <= {rx_data, received_crc[31:8]}; index <= index + 1;
                        if ((state == RX && index == 35) || (state == LOAD && index == input_bytes+3)) state <= CHECK;
                    end
                end
                if (state == RX && index == 32 && (op == 1 || op == 4 || op == 5)) begin
                    if (!request_header_ok || poison || ((op == 1 || op == 4) && !new_job_ok)) begin
                        status <= 2; reply_op <= op; state <= HEADER; send_index <= 0; reply_bytes <= 0;
                        send_crc <= 32'hffffffff; poison <= 1;
                    end else begin
                        input_bytes <= length[15:0]; a_bytes <= op == 1 ? x << 7 : 0;
                        state <= LOAD; index <= 0;
                    end
                end
            end
            if (do_load_a || do_load_w) load_pending <= 0;
            if (state == CHECK) begin
                send_index <= 0; send_crc <= 32'hffffffff; reply_bytes <= 0; flags <= 0; reply_op <= op;
                reply_stripe <= 0; reply_row <= 0; reply_rows <= 0; publish_cycle <= 0; completion_cycle <= 0;
                state <= HEADER; frame_wait <= 0; sample_counters();
                if (!request_header_ok) begin status <= 2; poison <= 1; end
                else if (received_crc != ~checksum) begin status <= 3; poison <= 1; end
                else if (op == 3) begin
                    core_reset <= 1; owned <= 0; poison <= 0; status <= 0;
                    run_id <= header[127:64]; generation <= generation_counter;
                end else if (poison) status <= 8;
                else if (op == 0) begin
                    if (owned) begin status <= 5; poison <= 1; end
                    else begin
                        status <= 0; run_id <= header[127:64]; generation <= generation_counter;
                        rows <= 336; columns <= 48; reduction <= 96;
                        cycles <= 0; fragments <= 0; works <= 0; a_reads <= 0; w_reads <= 0; writes <= 0; acks <= 0;
                        first_a <= 0; first_a_rows <= 0; host_wait <= 0; overlap <= 0;
                    end
                end else if (op == 1 || op == 4) begin
                    if (!new_job_ok || load_pending) begin status <= 5; poison <= 1; end
                    else begin
                        generation_counter <= generation_counter + 1; generation <= generation_counter + 1;
                        run_id <= header[127:64]; rows <= x[8:0]; columns <= y[5:0]; reduction <= z[6:0];
                        tiles <= (y+15) >> 4; striped <= op == 4; stripe_rows <= t[8:0];
                        next_row <= 0; next_stripe <= 0; retired_stripes <= 0;
                        output_words <= x * ((y+15) >> 4) * (z >> 5);
                        raw_row_begin <= 0; raw_rows <= x[8:0]; status <= 0; state <= START; owned <= 1;
                    end
                end else if (!owned || !identity_ok) begin status <= 6; poison <= 1; end
                else if (op == 2) begin
                    if (!do_ack) begin status <= 6; poison <= 1; end
                    else begin owned <= 0; status <= 0; end
                end else if (op == 5) begin
                    if (!publication_ok || load_pending) begin status <= 6; poison <= 1; end
                    else begin state <= PUBLISH; status <= 0; end
                end else if (op == 6 && striped) begin
                    status <= 0;
                    if (stripe_valid) begin
                        if (core_stripe != retired_stripes || core_row + core_rows > rows || core_rows == 0) begin
                            status <= 7; poison <= 1;
                        end else begin
                            flags <= 1; reply_stripe <= core_stripe[15:0]; reply_row <= core_row[15:0]; reply_rows <= core_rows[15:0];
                            publish_cycle <= core_pub_cycle; completion_cycle <= core_complete_cycle;
                            raw_row_begin <= core_row[8:0]; raw_rows <= core_rows[8:0];
                            output_words <= core_rows * tiles * (reduction >> 5);
                            reply_bytes <= (core_rows * tiles * (reduction >> 5)) << 6;
                            output_block <= 0; output_row <= 0; output_tile <= 0; words_sent <= 0;
                        end
                    end
                end else if (op == 7 && striped && next_row == rows && retired_stripes == next_stripe && done) status <= 0;
                else begin status <= 6; poison <= 1; end
            end
            if (do_start) begin
                if (striped) begin
                    state <= HEADER;
                    cycles <= 0; fragments <= 0; works <= 0; a_reads <= 0; w_reads <= 0; writes <= 0; acks <= 0;
                    first_a <= 0; first_a_rows <= 0; host_wait <= 0; overlap <= 0;
                end
                else state <= RUN;
            end
            if (do_publish) begin
                next_row <= next_row + y[8:0]; next_stripe <= next_stripe + 1;
                state <= HEADER; reply_stripe <= z; reply_row <= x; reply_rows <= y;
                publish_cycle <= core_live; sample_counters();
            end
            if ((state == START || state == RUN || state == PUBLISH) && poison) begin
                state <= HEADER; status <= 9; reply_bytes <= 0;
            end else if (state == RUN && done) begin
                status <= 0; reply_bytes <= {19'd0,output_words} << 6;
                sample_counters(); state <= HEADER; send_index <= 0; send_crc <= 32'hffffffff;
                output_block <= 0; output_row <= 0; output_tile <= 0; words_sent <= 0;
            end
            if (tx_valid && tx_ready) begin
                if (state != CRC) send_crc <= crc_byte(send_crc, tx_data);
                send_index <= send_index + 1;
                if (state == HEADER && send_index == 143) begin send_index <= 0; state <= reply_bytes == 0 ? CRC : REQUEST; end
                if (state == OUTPUT && send_index == 63) begin
                    send_index <= 0; words_sent <= words_sent + 1;
                    if (output_tile + 1 == tiles) begin
                        output_tile <= 0;
                        if (output_row + 1 == raw_rows) begin output_row <= 0; output_block <= output_block + 1; end
                        else output_row <= output_row + 1;
                    end else output_tile <= output_tile + 1;
                    state <= words_sent + 1 == output_words ? CRC : REQUEST;
                end
                if (state == CRC && send_index == 3) state <= DRAIN;
            end
            if (do_request) state <= RESPONSE;
            if (do_consume) begin output_buffer <= output_values; state <= OUTPUT; end
            if (state == DRAIN && tx_ready && (!flags[0] || stripe_ack_ready)) begin
                if (flags[0]) retired_stripes <= retired_stripes + 1;
                state <= RX; index <= 0; checksum <= 32'hffffffff; frame_wait <= 0;
                status <= 0; header <= 0; flags <= 0;
            end
        end
    end
endmodule
