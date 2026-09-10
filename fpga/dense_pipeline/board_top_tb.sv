`timescale 1ns/1ps
module board_top_tb;
    localparam integer MAX_RESPONSE = 193684; // 144 header + 336*48*3*4 raw + CRC32
    reg clock100 = 0, button = 1, uart_rx = 1;
    wire uart_tx;
    wire [3:0] led;
    arty_dense_top dut(.CLK100MHZ(clock100), .btn0(button), .uart_rx(uart_rx),
                       .uart_tx(uart_tx), .led(led));
    always #5 clock100 = ~clock100;
    byte unsigned received[0:MAX_RESPONSE-1];
    integer received_count = 0, launches = 0, publications = 0, stripe_acks = 0;
    integer transactions = 0, reset_events = 0;
    integer expected_jobs, expected_publications, expected_stripe_acks;
    integer plan, source, destination, scan, value, length, i, existing, idle_clocks;
    string request_path, response_path;
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
    always @(posedge dut.clk) begin
        if (dut.locked) begin
            if (previous_edge != 0 && $time - previous_edge != 40)
                $fatal(1, "core clock period");
            previous_edge = $time;
        end else previous_edge = 0;
        if (dut.release_reset[3]) begin
            if (dut.shell.do_start) launches = launches + 1;
            if (dut.shell.do_publish) publications = publications + 1;
            if (dut.shell.do_stripe_ack) stripe_acks = stripe_acks + 1;
        end
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
    task automatic packet_exchange;
        begin
            existing = $fopen(response_path, "rb");
            if (existing) begin
                $fclose(existing);
                $fatal(1, "response file already exists");
            end
            received_count = 0;
            source = $fopen(request_path, "rb");
            if (!source) $fatal(1, "request missing");
            value = $fgetc(source);
            while (value != -1) begin send(value[7:0]); value = $fgetc(source); end
            $fclose(source);
            wait(received_count >= 144);
            length = 148 + {received[23], received[22], received[21], received[20]};
            if (length < 148 || length > MAX_RESPONSE) $fatal(1, "response capacity");
            wait(received_count >= length); #12000;
            if (received_count != length) $fatal(1, "trailing response");
            destination = $fopen(response_path, "wb");
            if (!destination) $fatal(1, "output file");
            for (i = 0; i < length; i = i + 1) $fwrite(destination, "%c", received[i]);
            $fclose(destination);
            transactions = transactions + 1;
            $display("DENSE_BOARD_PACKET transaction=%0d bytes=%0d jobs=%0d publications=%0d stripe_acks=%0d",
                     transactions, length, launches, publications, stripe_acks);
        end
    endtask
    initial begin
        if (!$value$plusargs("expected_jobs=%d", expected_jobs) ||
            !$value$plusargs("expected_publications=%d", expected_publications) ||
            !$value$plusargs("expected_stripe_acks=%d", expected_stripe_acks))
            $fatal(1, "expected completion counts missing");
        #1000; button = 0;
        wait(dut.release_reset[3]); #10000;
        plan = $fopen("itinerary.txt", "r");
        if (!plan) $fatal(1, "itinerary missing");
        while (!$feof(plan)) begin
            scan = $fscanf(plan, "%s %s", request_path, response_path);
            if (scan == 2) begin
                if (request_path == "@clocks") begin
                    if ($sscanf(response_path, "%d", idle_clocks) != 1 || idle_clocks < 0)
                        $fatal(1, "invalid clock delay");
                    repeat(idle_clocks) @(posedge dut.clk);
                end else if (request_path == "@reset") begin
                    // Explicit simulation fault only. No programming or physical device access.
                    uart_rx = 1; button = 1; #1000; button = 0;
                    wait(dut.release_reset[3]); #10000;
                    reset_events = reset_events + 1;
                end else begin
                    fork : packet_timeout
                        packet_exchange();
                        begin #(64'd5000000000); $fatal(1, "packet timeout"); end
                    join_any
                    disable packet_timeout;
                end
            end
        end
        $fclose(plan);
        if (transactions == 0 || launches != expected_jobs ||
            publications != expected_publications || stripe_acks != expected_stripe_acks)
            $fatal(1, "completion count mismatch");
        $display("DENSE_BOARD_TOP_COMPLETE transactions=%0d jobs=%0d publications=%0d stripe_acks=%0d resets=%0d period_ns=40 uart_baud=1000000 vendor_unisims=true",
                 transactions, launches, publications, stripe_acks, reset_events);
        $finish;
    end
    initial begin #(64'd50000000000); $fatal(1, "board-top timeout"); end
endmodule
