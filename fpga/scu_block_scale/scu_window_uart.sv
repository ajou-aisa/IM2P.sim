`include "window_protocol.svh"

// IFR4 retains complete CRC-validated refill frames and acknowledged output
// batches. Core write acknowledgements transfer ownership to this buffer;
// host OUTPUT_ACK alone releases that buffer for reuse.
module scu_window_uart_shell #(parameter integer UART_DIV = 25) (
    input wire clk, reset_n, uart_rx,
    output wire uart_tx,
    output wire [3:0] led
);
    localparam RX=0, CHECK=1, START=2, PUBLISH=3, LOAD_READ=4, LOAD=5,
               COMMIT=6, STRIPE_ACK=7, RELEASE=8, TX_HEADER=9,
               TX_POLL=10, TX_READ=11, TX_OUTPUT=12, TX_CRC=13, TX_DRAIN=14;
    reg [3:0] state;
    reg [383:0] header, reply_header;
    reg [1407:0] poll_snapshot;
    (* ram_style="block" *) reg [127:0] rx_words [0:255];
    reg [119:0] rx_prefix;
    (* ram_style="block" *) reg [703:0] output_records [0:15];
    reg [703:0] output_shift;
    reg [127:0] load_values;
    reg [12:0] rx_index;
    reg [8:0] load_cursor, load_count;
    reg [15:0] load_start;
    reg [1:0] load_channel;
    reg [31:0] load_generation;
    reg load_final;
    reg [31:0] checksum, received_crc, send_crc;
    reg [31:0] reply_payload;
    reg [7:0] tx_index;
    reg [4:0] output_count, offered_count;
    reg [3:0] tx_record;
    reg output_offered, owned, striped, poison;
    reg [31:0] generation_counter = 0;
    reg [31:0] generation, next_sequence, batch_generation;
    reg [31:0] rows, columns, reduction, next_row, next_stripe;
    reg [31:0] window_seen [0:2];
    reg [15:0] window_loaded [0:2];
    reg [63:0] run_id;
    reg [23:0] frame_wait, progress_wait;
    reg [63:0] previous_progress;
    wire [7:0] rx_data;
    wire rx_valid, rx_error, tx_ready;
    wire tx_valid = state == TX_HEADER || state == TX_POLL || state == TX_OUTPUT || state == TX_CRC;
    wire [7:0] tx_data = state == TX_HEADER ? reply_header[7:0] :
        state == TX_POLL ? poll_snapshot[7:0] : state == TX_OUTPUT ? output_shift[7:0] :
        ((~send_crc >> (tx_index * 8)) & 8'hff);
    wire [7:0] op = header[47:40];
    wire [7:0] flags = header[63:56];
    wire [31:0] length = header[223:192];
    wire [31:0] x = header[255:224], y = header[287:256], z = header[319:288],
                t = header[351:320], u = header[383:352];
    wire identity_ok = owned && header[127:64] == run_id && header[159:128] == generation &&
                       header[191:160] == next_sequence && next_sequence != 32'hffffffff;
    wire base_ok = header[31:0] == 32'h34524649 && header[39:32] == 4 && header[55:48] == 0;
    // Same padded virtual output span as startLogical; no resident allocation.
    wire [5:0] output_span_shift = {1'b0,t[9:5]} + 6'd2 +
        ((t[18:16]==3 && t[4:0]>5) ? {1'b0,t[4:0]}-6'd5 : 6'd0);
    wire output_span_ok = {32'd0,x} <= (64'hffffffffffffffff >> output_span_shift);
    wire [2:0] miss;
    wire [31:0] origin_row [0:2], origin_word [0:2], valid_rows [0:2], window_generation [0:2];
    wire [15:0] window_words [0:2];
    wire [2:0] load_ready, commit_ready;
    wire start_ready, publish_ready, stripe_ready, ack_ready, stripe_valid, done, core_error;
    wire stream_valid, consume_ready;
    wire [63:0] stream_address, stream_tag;
    wire [4:0] stream_count;
    wire [511:0] stream_values;
    wire [31:0] stripe_id, stripe_row, stripe_rows, published_rows, completed_rows;
    wire [63:0] stripe_publish_cycle, stripe_completion_cycle;
    wire [63:0] cycles, live_cycles, fragments, works, a_reads, w_reads, writes, acks;
    wire [63:0] progress = fragments + a_reads + w_reads + acks;
    wire do_consume = state == RX && owned && !poison && !output_offered &&
                      output_count < 16 && stream_valid && consume_ready;
    wire [2:0] do_load = state == LOAD && load_ready[load_channel] ? (3'b001 << load_channel) : 0;
    wire [2:0] do_commit = state == COMMIT && commit_ready[load_channel] ? (3'b001 << load_channel) : 0;
    wire do_start = state == START && start_ready;
    wire do_publish = state == PUBLISH && publish_ready;
    wire do_stripe_ack = state == STRIPE_ACK && stripe_ready;
    wire do_release = state == RELEASE && ack_ready;
    wire offer_output = output_count != 0 && (output_count == 16 || stripe_valid || done);
    wire [4:0] reply_count = offer_output ? output_count : 0;
    integer i;

    function automatic [31:0] crc_byte(input [31:0] c, input [7:0] b);
        reg [31:0] v;
        integer bit_index;
        begin
            v = c ^ {24'd0,b};
            for (bit_index=0; bit_index<8; bit_index=bit_index+1)
                v = (v >> 1) ^ (v[0] ? 32'hedb88320 : 0);
            crc_byte = v;
        end
    endfunction
    task reply(input [7:0] status, input [31:0] payload_bytes, input [7:0] reply_flags);
        begin
            reply_header <= {32'd0,32'd0,32'd0,32'd0,32'h08100420,
                payload_bytes,header[191:160],generation,run_id,reply_flags,status,op,8'd4,32'h3452464f};
            reply_payload <= payload_bytes;
            state <= TX_HEADER; tx_index <= 0; send_crc <= 32'hffffffff;
        end
    endtask
    task reject(input [7:0] status);
        begin poison <= owned; reply(status,0,0); end
    endtask

    p0_uart #(.DIV(UART_DIV)) uart (.clk(clk),.reset_n(reset_n),.rx(uart_rx),.tx(uart_tx),
        .rx_data(rx_data),.rx_valid(rx_valid),.rx_error(rx_error),
        .tx_data(tx_data),.tx_valid(tx_valid),.tx_ready(tx_ready));
    mkScuPipeline core (.CLK(clk),.RST_N(reset_n),
        .startLogical_striped(striped),.startLogical_m(rows),.startLogical_n(columns),.startLogical_k(reduction),
        .startLogical_kStrideLog(t[4:0]),.startLogical_nStrideLog(t[9:5]),
        .startLogical_windowKLog(t[12:10]),.startLogical_windowNLog(t[15:13]),
        .startLogical_op(t[18:16]),.startLogical_job(generation),.startLogical_contextId(run_id),
        .EN_startLogical(do_start),.RDY_startLogical(start_ready),
        .publishLogical_rowBegin(x),.publishLogical_rowCount(y),.EN_publishLogical(do_publish),.RDY_publishLogical(publish_ready),
        .aWindow_miss(miss[0]),.aWindow_originRow(origin_row[0]),.aWindow_originWord(origin_word[0]),
        .aWindow_validRows(valid_rows[0]),.aWindow_words(window_words[0]),.aWindow_generation(window_generation[0]),
        .aWindow_load_generation(load_generation),.aWindow_load_index(load_start+load_cursor),.aWindow_load_values(load_values),
        .EN_aWindow_load(do_load[0]),.RDY_aWindow_load(load_ready[0]),
        .aWindow_commit_generation(load_generation),.EN_aWindow_commit(do_commit[0]),.RDY_aWindow_commit(commit_ready[0]),
        .wWindow_miss(miss[1]),.wWindow_originRow(origin_row[1]),.wWindow_originWord(origin_word[1]),
        .wWindow_validRows(valid_rows[1]),.wWindow_words(window_words[1]),.wWindow_generation(window_generation[1]),
        .wWindow_load_generation(load_generation),.wWindow_load_index(load_start+load_cursor),.wWindow_load_values(load_values),
        .EN_wWindow_load(do_load[1]),.RDY_wWindow_load(load_ready[1]),
        .wWindow_commit_generation(load_generation),.EN_wWindow_commit(do_commit[1]),.RDY_wWindow_commit(commit_ready[1]),
        .sWindow_miss(miss[2]),.sWindow_originRow(origin_row[2]),.sWindow_originWord(origin_word[2]),
        .sWindow_validRows(valid_rows[2]),.sWindow_words(window_words[2]),.sWindow_generation(window_generation[2]),
        .sWindow_load_generation(load_generation),.sWindow_load_index(load_start+load_cursor),.sWindow_load_values(load_values),
        .EN_sWindow_load(do_load[2]),.RDY_sWindow_load(load_ready[2]),
        .sWindow_commit_generation(load_generation),.EN_sWindow_commit(do_commit[2]),.RDY_sWindow_commit(commit_ready[2]),
        .streamOutputValid(stream_valid),.streamOutputAddress(stream_address),.streamOutputTag(stream_tag),
        .streamOutputCount(stream_count),.streamOutputValues(stream_values),
        .consumeStreamOutput_tag(stream_tag),.EN_consumeStreamOutput(do_consume),.RDY_consumeStreamOutput(consume_ready),
        .stripeCompletionValid(stripe_valid),.stripeId(stripe_id),.stripeRowBegin(stripe_row),.stripeRowCount(stripe_rows),
        .stripePublishCycle(stripe_publish_cycle),.stripeCompletionCycle(stripe_completion_cycle),
        .EN_acknowledgeStripe(do_stripe_ack),.RDY_acknowledgeStripe(stripe_ready),
        .publishedRows(published_rows),.completedRows(completed_rows),.done(done),.protocolError(core_error),
        .cycles(cycles),.liveCycles(live_cycles),.fragments(fragments),.works(works),
        .activationRequests(a_reads),.weightRequests(w_reads),.outputWrites(writes),.outputAcks(acks),
        .EN_acknowledge(do_release),.RDY_acknowledge(ack_ready),
        .EN_start(1'b0),.start_striped(1'b0),.start_m(9'd0),.start_n(6'd0),.start_k(7'd0),.start_job(32'd0),.start_contextId(64'd0),
        .EN_publish(1'b0),.publish_rowBegin(9'd0),.publish_rowCount(9'd0),
        .EN_loadActivation(1'b0),.loadActivation_word(12'd0),.loadActivation_values(128'd0),
        .EN_loadWeight(1'b0),.loadWeight_word(9'd0),.loadWeight_values(128'd0),
        .EN_loadScale(1'b0),.loadScale_element(8'd0),.loadScale_value(32'd0),
        .EN_requestOutput(1'b0),.requestOutput_word(11'd0),.EN_consumeOutput(1'b0));
    assign led = {poison,owned,owned&&!done,reset_n};

    always @(posedge clk) begin
        if (!reset_n) begin
            state<=RX; header<=0; rx_index<=0; checksum<=32'hffffffff; received_crc<=0;
            owned<=0; striped<=0; poison<=0; generation<=generation_counter; run_id<=0; next_sequence<=0;
            output_count<=0; offered_count<=0; output_offered<=0; batch_generation<=1;
            frame_wait<=0; progress_wait<=0; previous_progress<=0; tx_index<=0; tx_record<=0;
            rows<=0; columns<=0; reduction<=0; next_row<=0; next_stripe<=0;
            load_cursor<=0; load_count<=0; load_start<=0; load_generation<=0; load_channel<=0; load_final<=0;
            for (i=0;i<3;i=i+1) begin window_seen[i]<=0; window_loaded[i]<=0; end
        end else begin
            if (owned && core_error) poison<=1;
            if (!owned || done || miss!=0 || output_offered || output_count==16 || stripe_valid ||
                (striped && published_rows==completed_rows) || progress!=previous_progress) progress_wait<=0;
            else if (!poison) progress_wait<=progress_wait+1;
            previous_progress<=progress;
            if (&progress_wait) poison<=1;
            for (i=0;i<3;i=i+1) if (miss[i] && window_seen[i]!=window_generation[i]) begin
                window_seen[i]<=window_generation[i]; window_loaded[i]<=0;
            end
            if (do_consume) begin
                output_records[output_count[3:0]] <= {stream_values,32'd0,27'd0,stream_count,stream_tag,stream_address};
                output_count<=output_count+1;
            end
            case (state)
                RX: begin
                    if (rx_valid) frame_wait<=0;
                    else if (rx_index!=0) frame_wait<=frame_wait+1;
                    if (&frame_wait || rx_error) begin reject(8); end
                    else if (rx_valid) begin
                        rx_index<=rx_index+1;
                        if (rx_index<48) begin
                            header[rx_index*8 +: 8]<=rx_data; checksum<=crc_byte(checksum,rx_data);
                            if (rx_index==47 && length>4096) reject(2);
                        end else if (rx_index<48+length) begin
                            // Header size is 48 (three words). Byte 15 joins
                            // the preceding 15 bytes in little-endian order.
                            // Only complete, CRC-validated REFILL frames can
                            // reach LOAD_READ; partial words remain unusable.
                            rx_prefix<={rx_data,rx_prefix[119:8]};
                            if (rx_index[3:0]==15)
                                rx_words[(rx_index-48)>>4]<={rx_data,rx_prefix};
                            checksum<=crc_byte(checksum,rx_data);
                        end else begin
                            received_crc<={rx_data,received_crc[31:8]};
                            if (rx_index==51+length) state<=CHECK;
                        end
                    end
                end
                CHECK: begin
                    if (!base_ok || length>4096) reject(2);
                    else if (received_crc!=~checksum) reject(3);
                    else if (poison || core_error) reply(9,0,0);
                    else if (op==`IFR4_CAP && !owned && length==0 && flags==0 && (x|y|z|t|u)==0) reply(0,0,0);
                    else if (op==`IFR4_START && !owned && length==0 && flags<2 && header[127:64]!=0 &&
                        generation_counter!=32'hffffffff && header[159:128]==generation_counter+1 && header[191:160]==1 &&
                        x>0 && y>0 && z>0 && output_span_ok && u==0 && t[31:19]==0 &&
                        t[4:0]>=4 && t[9:5]>=4 &&
                        z<=(32'd1<<t[4:0]) && y<=(32'd1<<t[9:5]) &&
                        t[12:10]>=5 && t[12:10]<=6 && t[15:13]>=4 && t[15:13]<=6 && t[18:16]<=5) begin
                        owned<=1; striped<=flags[0]; run_id<=header[127:64]; generation<=header[159:128];
                        generation_counter<=header[159:128]; next_sequence<=2; batch_generation<=1;
                        rows<=x; columns<=y; reduction<=z; next_row<=0; next_stripe<=0; state<=START;
                    end else if (!identity_ok || flags!=0) reject(4);
                    else begin
                        next_sequence<=next_sequence+1;
                        case (op)
                            `IFR4_POLL: if (length!=0 || (x|y|z|t|u)!=0) reject(2); else begin
                                poll_snapshot<=0;
                                poll_snapshot[63:0]<=done?cycles:live_cycles;
                                poll_snapshot[127:64]<=fragments; poll_snapshot[191:128]<=works;
                                poll_snapshot[255:192]<=a_reads; poll_snapshot[319:256]<=w_reads;
                                poll_snapshot[383:320]<=writes; poll_snapshot[447:384]<=acks;
                                poll_snapshot[479:448]<=published_rows; poll_snapshot[511:480]<=completed_rows;
                                for (i=0;i<3;i=i+1) begin
                                    poll_snapshot[512+i*192 +: 32]<=origin_row[i];
                                    poll_snapshot[544+i*192 +: 32]<=origin_word[i];
                                    poll_snapshot[576+i*192 +: 32]<=valid_rows[i];
                                    poll_snapshot[608+i*192 +: 32]<=window_generation[i];
                                    poll_snapshot[640+i*192 +: 32]<={16'd0,window_words[i]};
                                end
                                poll_snapshot[1119:1088]<=stripe_id; poll_snapshot[1151:1120]<=stripe_row;
                                poll_snapshot[1183:1152]<=stripe_rows;
                                poll_snapshot[1279:1216]<=stripe_publish_cycle;
                                poll_snapshot[1343:1280]<=stripe_completion_cycle;
                                poll_snapshot[1375:1344]<=batch_generation;
                                poll_snapshot[1407:1376]<={27'd0,reply_count};
                                offered_count<=reply_count;
                                if (offer_output) output_offered<=1;
                                reply(0,176+reply_count*88,{1'b0,poison,done,stripe_valid,offer_output,miss});
                            end
                            `IFR4_REFILL: if (x>2 || t==0 || t>256 || z>256 || u>1 || length!=t*16) reject(2);
                                else if (!miss[x] || y!=window_generation[x] || z!=window_loaded[x] ||
                                    z+t>window_words[x] || ((z+t==window_words[x]) != (u==1))) reject(5);
                                else begin load_channel<=x[1:0]; load_generation<=y; load_start<=z[15:0];
                                    load_count<=t[8:0]; load_final<=u[0]; load_cursor<=0; state<=LOAD_READ; end
                            `IFR4_PUBLISH: if (length!=0 || !striped || x!=next_row || y==0 ||
                                x>=rows || y>rows-x || z!=next_stripe || t!=z%2 || u!=0) reject(5);
                                else state<=PUBLISH;
                            `IFR4_OUTPUT_ACK: if (length!=0 || !output_offered || x!=batch_generation ||
                                y!=output_count || (z|t|u)!=0 || batch_generation==32'hffffffff) reject(6);
                                else begin output_count<=0; offered_count<=0; output_offered<=0;
                                    batch_generation<=batch_generation+1; reply(0,0,0); end
                            `IFR4_STRIPE_ACK: if (length!=0 || !striped || !stripe_valid ||
                                output_count!=0 || stream_valid || x!=stripe_id || y!=stripe_row || z!=stripe_rows ||
                                (t|u)!=0) reject(6); else state<=STRIPE_ACK;
                            `IFR4_RELEASE: if (length!=0 || !done || stripe_valid || output_count!=0 ||
                                stream_valid || (x|y|z|t|u)!=0) reject(6); else state<=RELEASE;
                            default: reject(2);
                        endcase
                    end
                end
                START: if (do_start) reply(0,0,0);
                PUBLISH: if (do_publish) begin next_row<=next_row+y; next_stripe<=next_stripe+1; reply(0,0,0); end
                LOAD_READ: begin load_values<=rx_words[load_cursor[7:0]]; state<=LOAD; end
                LOAD: if (do_load!=0) begin
                    window_loaded[load_channel]<=window_loaded[load_channel]+1;
                    if (load_cursor+1==load_count) begin
                        if (load_final) state<=COMMIT; else reply(0,0,0);
                    end else begin load_cursor<=load_cursor+1; state<=LOAD_READ; end
                end
                COMMIT: if (do_commit!=0) reply(0,0,0);
                STRIPE_ACK: if (do_stripe_ack) reply(0,0,0);
                RELEASE: if (do_release) begin owned<=0; reply(0,0,0); end
                TX_HEADER: if (tx_ready) begin
                    send_crc<=crc_byte(send_crc,tx_data); reply_header<=reply_header>>8;
                    if (tx_index==47) begin tx_index<=0; state<=reply_payload!=0 ? TX_POLL : TX_CRC; end
                    else tx_index<=tx_index+1;
                end
                TX_POLL: if (tx_ready) begin
                    send_crc<=crc_byte(send_crc,tx_data); poll_snapshot<=poll_snapshot>>8;
                    if (tx_index==175) begin tx_index<=0; tx_record<=0; state<=offered_count!=0 ? TX_READ : TX_CRC; end
                    else tx_index<=tx_index+1;
                end
                TX_READ: begin output_shift<=output_records[tx_record]; state<=TX_OUTPUT; end
                TX_OUTPUT: if (tx_ready) begin
                    send_crc<=crc_byte(send_crc,tx_data); output_shift<=output_shift>>8;
                    if (tx_index==87) begin tx_index<=0;
                        if ({1'b0,tx_record}+1==offered_count) state<=TX_CRC;
                        else begin tx_record<=tx_record+1; state<=TX_READ; end
                    end else tx_index<=tx_index+1;
                end
                TX_CRC: if (tx_ready) begin if (tx_index==3) state<=TX_DRAIN; else tx_index<=tx_index+1; end
                TX_DRAIN: if (tx_ready) begin state<=RX; rx_index<=0; header<=0;
                    checksum<=32'hffffffff; received_crc<=0; frame_wait<=0; end
                default: state<=RX;
            endcase
        end
    end
endmodule
