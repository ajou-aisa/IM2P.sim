proc parse_options {} {
    global argv
    set options [dict create validate_only 0 xdc {}]
    set index 0
    while {$index < [llength $argv]} {
        set key [lindex $argv $index]
        if {$key eq "--validate-only"} {
            dict set options validate_only 1
            incr index
            continue
        }
        if {![string match "--*" $key] || $index + 1 >= [llength $argv]} {
            error "invalid or missing option value: $key"
        }
        set name [string range $key 2 end]
        if {$name eq "xdc"} {
            dict lappend options xdc [lindex $argv [expr {$index + 1}]]
        } else {
            dict set options $name [lindex $argv [expr {$index + 1}]]
        }
        incr index 2
    }
    foreach required {stage part top clock-port clock-mhz xdc filelist out} {
        if {![dict exists $options $required] || [dict get $options $required] eq ""} {
            error "required option missing: --$required"
        }
    }
    return $options
}

proc validate_identifier {label value} {
    if {![regexp {^[A-Za-z_][A-Za-z0-9_$]*$} $value]} {
        error "invalid $label: $value"
    }
}

proc validate_regular_file {label path} {
    if {![file exists $path] || ![file isfile $path] || [file type $path] eq "link"} {
        error "$label must be a regular file: $path"
    }
}

proc load_filelist {path} {
    set root [file dirname [file normalize $path]]
    set channel [open $path r]
    set sources {}
    while {[gets $channel line] >= 0} {
        set line [string trim $line]
        if {$line eq "" || [string match "#*" $line]} { continue }
        if {[file pathtype $line] ne "relative"} {
            close $channel
            error "filelist entry must be relative: $line"
        }
        if {[lsearch -exact [file split $line] ".."] >= 0} {
            close $channel
            error "filelist entry must be confined: $line"
        }
        set source [file normalize [file join $root $line]]
        validate_regular_file "RTL source" $source
        if {[file extension $source] ni {.v .sv .vh .svh .vhd .vhdl}} {
            close $channel
            error "unsupported RTL source: $line"
        }
        lappend sources $source
    }
    close $channel
    if {![llength $sources]} { error "filelist contains no RTL sources" }
    return $sources
}

proc write_result {out stage top part} {
    set channel [open [file join $out result.json] w]
    puts $channel [format {{"schema_version":1,"stage":"%s","status":"PASS","top":"%s","part":"%s","physical":{"status":"NOT_RUN"}}} $stage $top $part]
    close $channel
}

proc main {} {
    set options [parse_options]
    set stage [dict get $options stage]
    if {$stage ni {synth route bitstream}} { error "stage must be synth, route, or bitstream" }
    set part [dict get $options part]
    set top [dict get $options top]
    set clock_port [dict get $options clock-port]
    set clock_mhz [dict get $options clock-mhz]
    set xdcs {}
    foreach input [dict get $options xdc] {
        set xdc [file normalize $input]
        validate_regular_file XDC $xdc
        lappend xdcs $xdc
    }
    set filelist [file normalize [dict get $options filelist]]
    set out [file normalize [dict get $options out]]
    validate_identifier top $top
    validate_identifier clock-port $clock_port
    if {![regexp {^[A-Za-z0-9_.-]+$} $part]} { error "invalid FPGA part: $part" }
    if {![string is double -strict $clock_mhz] || $clock_mhz <= 0} { error "clock MHz must be positive" }
    validate_regular_file filelist $filelist
    set sources [load_filelist $filelist]
    if {[dict get $options validate_only]} {
        puts "FLOW_VALIDATED_NOT_RUN stage=$stage sources=[llength $sources] xdc=[llength $xdcs]"
        return
    }
    if {[file exists $out]} { error "fresh output directory required: $out" }
    file mkdir [file join $out reports]
    file mkdir [file join $out checkpoints]
    create_project -in_memory -part $part
    if {[get_property PART [current_project]] ne $part} { error "Vivado part mismatch" }
    set include_dirs {}
    foreach source $sources {
        set extension [file extension $source]
        if {$extension in {.vh .svh}} {
            lappend include_dirs [file dirname $source]
        } elseif {$extension in {.sv}} {
            read_verilog -sv $source
        } elseif {$extension in {.v}} {
            read_verilog $source
        } else {
            read_vhdl $source
        }
    }
    set_property include_dirs [lsort -unique $include_dirs] [current_fileset]
    foreach xdc $xdcs { read_xdc $xdc }
    synth_design -top $top -part $part
    if {[get_property TOP [current_fileset]] ne $top} { error "synthesized top mismatch" }
    set clock_ports [get_ports -quiet $clock_port]
    if {[llength $clock_ports] != 1} { error "clock port missing or ambiguous: $clock_port" }
    set clocks [get_clocks -quiet -of_objects $clock_ports]
    if {[llength $clocks] != 1} { error "XDC must define exactly one clock on $clock_port" }
    set expected_period [expr {1000.0 / double($clock_mhz)}]
    set actual_period [get_property PERIOD [lindex $clocks 0]]
    if {abs($actual_period - $expected_period) > 0.001} { error "XDC clock frequency mismatch" }
    set blackboxes [get_cells -hierarchical -quiet -filter {IS_BLACKBOX == 1}]
    if {[llength $blackboxes]} { error "unresolved black boxes after synthesis" }
    report_utilization -file [file join $out reports synth-utilization.rpt]
    write_checkpoint -force [file join $out checkpoints synth.dcp]
    if {$stage eq "synth"} {
        write_result $out $stage $top $part
        return
    }
    opt_design
    report_utilization -file [file join $out reports opt-utilization.rpt]
    place_design
    report_utilization -file [file join $out reports place-utilization.rpt]
    route_design
    report_route_status -file [file join $out reports route-status.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose -file [file join $out reports timing-summary.rpt]
    report_drc -file [file join $out reports drc.rpt]
    report_cdc -details -file [file join $out reports cdc.rpt]
    report_pulse_width -file [file join $out reports pulse-width.rpt]
    check_timing -verbose -file [file join $out reports check-timing.rpt]
    set setup [get_timing_paths -delay_type max -max_paths 1]
    set hold [get_timing_paths -delay_type min -max_paths 1]
    if {![llength $setup] || ![llength $hold]} { error "no synchronous setup/hold paths" }
    if {[get_property SLACK $setup] < 0 || [get_property SLACK $hold] < 0} { error "timing closure failed" }
    set drc_errors [get_drc_violations -quiet -filter {SEVERITY == Error}]
    set drc_critical [get_drc_violations -quiet -filter {SEVERITY == {Critical Warning}}]
    if {[llength $drc_errors] || [llength $drc_critical]} { error "blocking DRC violations remain" }
    write_checkpoint -force [file join $out checkpoints route.dcp]
    if {$stage eq "bitstream"} {
        write_bitstream -force [file join $out ${top}.bit]
    }
    write_result $out $stage $top $part
}

if {[catch {main} message options]} {
    puts stderr "GEMMINI_VIVADO_FLOW_FAILED: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
