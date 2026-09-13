`timescale 1ns/1ps
// Test-only IFR4 companion to the preserved legacy board_top_tb. The DUT is
// the unmodified board top, including the vendor MMCM and default UART_DIV=25.
module window_board_top_tb;
    localparam integer MAX_RESPONSE = 4148;
    reg clock100 = 0, button = 1, uart_rx = 1;
    wire uart_tx;
    wire [3:0] led;
    arty_scu_top dut(.CLK100MHZ(clock100), .btn0(button), .uart_rx(uart_rx),
                     .uart_tx(uart_tx), .led(led));
    always #5 clock100 = ~clock100;
    byte unsigned received[0:MAX_RESPONSE-1];
    integer received_count = 0, launches = 0, transactions = 0, resets = 0;
    integer completed = 0, release_edges = 0, loads[0:2], commits[0:2], outputs = 0;
    integer plan, source, destination, scan, value, length, i, existing;
    string command, argument;
    time previous_edge = 0;

    initial forever begin : receiver
        reg [7:0] byte_value;
        integer bit_index;
        @(negedge uart_tx);
        if (dut.release_reset[3]) begin
            #1500;
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                byte_value[bit_index] = uart_tx; #1000;
            end
            if (uart_tx !== 1'b1) $fatal(1, "UART stop bit");
            if (received_count >= MAX_RESPONSE) $fatal(1, "response capacity");
            received[received_count] = byte_value;
            received_count = received_count + 1;
        end
    end
    always @(negedge dut.locked) begin previous_edge = 0; release_edges = 0; end
    always @(posedge dut.clk) begin
        if (dut.locked) begin
            if (previous_edge != 0 && $time - previous_edge != 40)
                $fatal(1, "core clock period");
            previous_edge = $time;
            release_edges = release_edges + 1;
        end
        if (dut.release_reset[3]) begin
            if (dut.shell.do_start) begin
                launches = launches + 1;
                for (integer channel = 0; channel < 3; channel = channel + 1) begin
                    loads[channel] = 0; commits[channel] = 0;
                end
                outputs = 0;
            end
            for (integer channel = 0; channel < 3; channel = channel + 1) begin
                if (dut.shell.do_load[channel]) loads[channel] = loads[channel] + 1;
                if (dut.shell.do_commit[channel]) commits[channel] = commits[channel] + 1;
            end
            if (dut.shell.do_consume) outputs = outputs + 1;
        end
        #1;
        if (dut.locked && ((release_edges < 4 && dut.release_reset[3]) ||
                          (release_edges >= 4 && !dut.release_reset[3])))
            $fatal(1, "reset release must follow four locked core edges");
    end
    task automatic send(input [7:0] byte_value);
        integer bit_index;
        begin
            uart_rx = 0; #1000;
            for (bit_index = 0; bit_index < 8; bit_index = bit_index + 1) begin
                uart_rx = byte_value[bit_index]; #1000;
            end
            uart_rx = 1; #1000;
        end
    endtask
    task automatic send_file(input string path, input integer limit);
        integer input_file, datum, sent;
        begin
            input_file = $fopen(path, "rb");
            if (!input_file) $fatal(1, "request missing: %s", path);
            datum = $fgetc(input_file); sent = 0;
            while (datum != -1 && (limit == 0 || sent < limit)) begin
                send(datum[7:0]); sent = sent + 1; datum = $fgetc(input_file);
            end
            $fclose(input_file);
        end
    endtask
    task automatic packet_exchange;
        begin
            existing = $fopen(argument, "rb");
            if (existing) begin $fclose(existing); $fatal(1, "response already exists"); end
            received_count = 0;
            send_file(command, 0);
            wait(received_count >= 48);
            length = 52 + {received[27], received[26], received[25], received[24]};
            if (length < 52 || length > MAX_RESPONSE) $fatal(1, "IFR4 response capacity");
            wait(received_count >= length); #12000;
            if (received_count != length) $fatal(1, "trailing response");
            destination = $fopen(argument, "wb");
            if (!destination) $fatal(1, "response file");
            for (i = 0; i < length; i = i + 1) $fwrite(destination, "%c", received[i]);
            $fclose(destination);
            transactions = transactions + 1;
            $display("IFR4_BOARD_PACKET transaction=%0d bytes=%0d", transactions, length);
        end
    endtask
    task automatic reset_board(input string phase);
        reg [31:0] retained_generation;
        begin
            retained_generation = dut.shell.generation_counter;
            $display("IFR4_BOARD_RESET_BEGIN phase=%s generation=%0d rx_index=%0d loads_a=%0d offered=%0d",
                     phase, retained_generation, dut.shell.rx_index, loads[0], dut.shell.offered_count);
            uart_rx = 1; #3; button = 1;
            wait(!dut.locked); #1;
            if (dut.release_reset !== 0) $fatal(1, "LOCKED loss did not assert reset");
            #1000; button = 0;
            wait(dut.locked); wait(dut.release_reset[3]); #10000;
            if (dut.shell.generation_counter !== retained_generation ||
                dut.shell.generation !== retained_generation || dut.shell.run_id !== 0 ||
                dut.shell.owned !== 0 || dut.shell.poison !== 0 || dut.shell.rx_index !== 0 ||
                dut.shell.output_count !== 0 || dut.shell.output_offered !== 0 ||
                dut.shell.load_cursor !== 0 || dut.shell.miss !== 0 || dut.shell.stream_valid !== 0)
                $fatal(1, "reset did not clear logical ownership and retain generation");
            for (integer channel = 0; channel < 3; channel = channel + 1)
                if (dut.shell.window_loaded[channel] !== 0)
                    $fatal(1, "stale window load ownership after reset");
            received_count = 0; resets = resets + 1;
            $display("IFR4_BOARD_RESET_COMPLETE phase=%s generation=%0d resets=%0d", phase, retained_generation, resets);
        end
    endtask
    initial begin
        #1000; button = 0;
        wait(dut.release_reset[3]); #10000;
        plan = $fopen("itinerary.txt", "r");
        if (!plan) $fatal(1, "itinerary missing");
        while (!$feof(plan)) begin
            scan = $fscanf(plan, "%s %s", command, argument);
            if (scan == 2) begin
                if (command == "@reset_rx") begin
                    send_file(argument, 65);
                    if (dut.shell.rx_index != 65 || loads[0] != 0)
                        $fatal(1, "partial refill reset trigger not reached");
                    reset_board("partial_rx");
                end else if (command == "@reset_load") begin
                    fork : loading
                        send_file(argument, 0);
                        begin
                            wait(dut.shell.do_load[0]); @(posedge dut.clk); #1;
                            if (loads[0] == 0 || loads[0] >= 32 || commits[0] != 0)
                                $fatal(1, "partial LOAD reset trigger not reached");
                            reset_board("partial_load");
                        end
                    join
                end else if (command == "@reset_output") begin
                    if (!dut.shell.output_offered || dut.shell.offered_count != 1)
                        $fatal(1, "offered output reset trigger not reached");
                    reset_board("unacknowledged_output");
                end else if (command == "@complete_dense" || command == "@complete_bypass") begin
                    if (!dut.shell.done || dut.shell.output_count != 0 || dut.shell.stream_valid ||
                        loads[0] != 32 || loads[1] != 32 ||
                        loads[2] != (command == "@complete_dense" ? 4 : 0) ||
                        commits[0] != 1 || commits[1] != 1 ||
                        commits[2] != (command == "@complete_dense" ? 1 : 0) || outputs != 1)
                        $fatal(1, "fresh full-window refill/output conservation: %s", argument);
                    completed = completed + 1;
                    $display("IFR4_BOARD_FRESH_COMPLETE case=%s loads=32,32,%0d commits=1,1,%0d outputs=1",
                             argument, loads[2], commits[2]);
                end else begin
                    fork : packet_timeout
                        packet_exchange();
                        begin #(64'd100000000); $fatal(1, "packet timeout: %s", command); end
                    join_any
                    disable packet_timeout;
                end
            end
        end
        $fclose(plan);
        if (launches != 9 || resets != 3 || completed != 6)
            $fatal(1, "campaign conservation jobs=%0d resets=%0d complete=%0d", launches, resets, completed);
        $display("IFR4_BOARD_TOP_COMPLETE transactions=%0d jobs=%0d resets=%0d fresh_complete=%0d period_ns=40 uart_baud=1000000 vendor_unisims=true",
                 transactions, launches, resets, completed);
        $finish;
    end
    initial begin #(64'd1000000000); $fatal(1, "board-top timeout"); end
endmodule
