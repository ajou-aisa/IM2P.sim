proc write_text {path value} {
    set channel [open $path w]
    puts $channel $value
    close $channel
}

proc reject_blackboxes {stage} {
    set cells [get_cells -hierarchical -quiet -filter {IS_BLACKBOX == 1}]
    write_text reports/${stage}-blackboxes.txt "count=[llength $cells]\n$cells"
    if {[llength $cells] != 0} { error "Unresolved black boxes after $stage" }
}

proc reports {stage timed} {
    reject_blackboxes $stage
    report_utilization -file reports/${stage}-utilization.rpt
    report_utilization -hierarchical -hierarchical_depth 20 -hierarchical_min_primitive_count 0 \
        -file reports/${stage}-hierarchy.rpt
    set counts [dict create]
    foreach cell [get_cells -hierarchical -filter {IS_PRIMITIVE == 1}] {
        dict incr counts [get_property REF_NAME $cell]
    }
    set channel [open reports/${stage}-primitives.tsv w]
    puts $channel "primitive\tcount"
    foreach name [lsort [dict keys $counts]] { puts $channel "$name\t[dict get $counts $name]" }
    close $channel
    report_drc -file reports/${stage}-drc.rpt
    if {$timed} {
        report_clocks -file reports/${stage}-clocks.rpt
        check_timing -verbose -file reports/${stage}-check-timing.rpt
        report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
            -file reports/${stage}-timing-summary.rpt
        report_timing -delay_type max -max_paths 20 -file reports/${stage}-setup.rpt
        report_timing -delay_type min -max_paths 20 -file reports/${stage}-hold.rpt
        report_pulse_width -file reports/${stage}-pulse-width.rpt
    }
    write_checkpoint -force checkpoints/${stage}.dcp
}

proc utilization_value {report label} {
    set pattern [format {(?m)^\s*\|\s*(?:%s)\s*\|\s*([0-9,.]+)\s*\|} $label]
    if {![regexp $pattern $report -> value]} { error "Missing resource row: $label" }
    return [string map {, {}} $value]
}

proc fit_resources {} {
    set utilization [report_utilization -return_string]
    set lut [utilization_value $utilization {Slice LUTs\*?|CLB LUTs\*?}]
    set ff [utilization_value $utilization {Slice Registers|CLB Registers}]
    set dsp [utilization_value $utilization {DSPs|DSP48E1 only}]
    set bram [utilization_value $utilization {Block RAM Tile}]
    set fits [expr {$lut <= 63400 && $ff <= 126800 && $dsp <= 240 && $bram <= 135}]
    write_text reports/resource-fit.txt "LUT=$lut/63400\nFF=$ff/126800\nDSP=$dsp/240\nBRAM_tiles=$bram/135\nfits=$fits\ncore_LUT_margin_goal=50000"
    if {$lut > 50000} { puts "WARNING: core exceeds 50k LUT integration-margin goal" }
    return $fits
}

proc fifo_attribution {stage} {
    set channel [open reports/${stage}-fifo-cones.txt w]
    set cells [get_cells -hierarchical -quiet -filter {NAME =~ *activationRows*}]
    puts $channel "matching_cells=[llength $cells]"
    puts $channel "Attribution follows surviving netlist boundaries; inlined BSC hierarchy cannot be restored."
    foreach cell $cells {
        puts $channel "CELL $cell REF_NAME=[get_property REF_NAME $cell]"
        if {[get_property IS_PRIMITIVE $cell]} { continue }
        foreach pin [get_pins -quiet -of_objects $cell -filter {DIRECTION == IN}] {
            puts $channel "PIN $pin STARTPOINTS [all_fanin -flat -startpoints_only $pin]"
        }
    }
    foreach net [get_nets -hierarchical -quiet -filter {NAME =~ *activationRows*ENQ* || NAME =~ *activationRows*D_IN*}] {
        puts $channel "NET $net STARTPOINTS [all_fanin -flat -startpoints_only $net]"
    }
    close $channel
}

proc main {} {
    global argv
    if {[llength $argv] != 4} { error "Expected output_directory top mode hierarchy" }
    lassign $argv out top mode hierarchy
    if {$mode ni {area timing route} || $hierarchy ni {rebuilt none}} { error "Invalid synthesis options" }
    if {$mode eq "route" && $top ne "mkSynthA8W8D16"} { error "Route restricted to A8 DIM16 core" }
    cd $out
    create_project -in_memory -part xc7a100tcsg324-1
    set sources [concat [glob rtl/*.v] [glob -nocomplain primitives/*.v]]
    set_property include_dirs [list [file normalize primitives]] [current_fileset]
    read_verilog $sources
    set timed [expr {$mode ne "area"}]
    if {$timed} {
        write_text clock.xdc {create_clock -name core_clk -period 10.000 [get_ports CLK]}
        read_xdc clock.xdc
        write_text timing-assumptions.txt "Real CLK port, period 10 ns. No board shell, package pins, I/O delays, or reset false paths supplied. External I/O paths remain unconstrained and must be reviewed. Core OOC timing only."
    } else {
        write_text timing-assumptions.txt "Unclocked OOC area attribution; no timing claim."
    }
    write_text synthesis-options.txt "synth_design -top $top -part xc7a100tcsg324-1 -mode out_of_context -flatten_hierarchy $hierarchy\nopt_design"
    synth_design -top $top -part xc7a100tcsg324-1 -mode out_of_context -flatten_hierarchy $hierarchy
    if {$timed && [llength [get_clocks -quiet core_clk]] != 1} { error "Real core clock was not created" }
    reports synth $timed
    fifo_attribution synth
    opt_design
    reports opt $timed
    fifo_attribution opt
    set fits [fit_resources]
    if {$mode eq "route"} {
        if {!$fits} { error "Resource fit failed; place/route was not started" }
        place_design
        reports place $timed
        route_design
        reports route $timed
        report_route_status -file reports/route-status.rpt
    }
    write_text vivado-complete.txt "Completed requested $mode reports. Resource fits=$fits. Inspect setup/hold/pulse width, unconstrained paths, DRC and route status before claiming closure."
}

if {[catch {main} message options]} {
    puts stderr "FPGA_FLOW_FAILED: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
