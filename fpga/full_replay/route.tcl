# Reviewable SRAM bitstream only. No hardware manager, JTAG or flash operation.
proc main {} {
    global argv
    set root [file normalize [lindex $argv 0]]
    set dense [expr {[llength $argv] > 1 && [lindex $argv 1] eq "dense"}]
    set out $root/route
    if {[file exists $out]} {error "fresh route directory required"}
    file mkdir $out
    set_param general.maxThreads 2
    create_project -in_memory -part xc7a100tcsg324-1
    read_verilog [glob $root/production/rtl/*.v]
    read_verilog [glob $root/production/primitives/*.v]
    set source $root/source/fpga/full_replay
    if {$dense} {
        set adapter $root/source/fpga/dense_pipeline
        read_verilog -sv [list $source/uart.sv $adapter/dense_uart.sv $adapter/arty_dense_top.sv]
        set top arty_dense_top
        set bit_name dense-pipeline
    } else {
        read_verilog -sv [list $source/uart.sv $source/full_uart.sv $source/arty_full_top.sv]
        set top arty_full_top
        set bit_name full-replay
    }
    read_xdc $source/arty_full.xdc
    synth_design -top $top -part xc7a100tcsg324-1 -flatten_hierarchy rebuilt
    opt_design
    report_utilization -file $out/opt-utilization.rpt
    write_checkpoint $out/opt.dcp
    if {[llength [get_cells -hier -quiet -filter {IS_BLACKBOX == 1}]]} {error "Black boxes"}
    place_design
    report_utilization -file $out/place-utilization.rpt
    route_design
    report_utilization -file $out/route-utilization.rpt
    report_control_sets -verbose -file $out/control-sets.rpt
    report_high_fanout_nets -timing -load_types -max_nets 50 -file $out/high-fanout.rpt
    report_design_analysis -congestion -file $out/congestion.rpt
    report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file $out/timing.rpt
    check_timing -verbose -file $out/check-timing.rpt
    report_route_status -file $out/route-status.rpt
    report_drc -file $out/drc.rpt
    report_cdc -details -file $out/cdc.rpt
    report_clocks -file $out/clocks.rpt
    write_checkpoint $out/route.dcp
    set setup [get_timing_paths -delay_type max -max_paths 1]
    set hold [get_timing_paths -delay_type min -max_paths 1]
    if {![llength $setup] || ![llength $hold]} {error "No synchronous timing paths"}
    if {[get_property SLACK $setup]<0 || [get_property SLACK $hold]<0} {error "Synchronous setup/hold failed"}
    write_bitstream $out/$bit_name.bit
    set file [open $out/complete.txt w]; puts $file "ROUTED; NOT PROGRAMMED"; close $file
}
if {[catch {main} message options]} {
    puts stderr "FULL_ROUTE_FAILED: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
