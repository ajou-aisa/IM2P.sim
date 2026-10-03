proc regular_file {path} {
    if {![file isfile $path] || [file type $path] ne "file"} {
        error "regular file required: $path"
    }
    return [file normalize $path]
}

proc filelist_sources {path} {
    set root [file dirname $path]
    set stream [open $path r]
    try { set lines [split [read $stream] \n] } finally { close $stream }
    set sources {}
    set compiled 0
    foreach line $lines {
        set entry [string trim $line]
        if {$entry eq "" || [string match "#*" $entry]} { continue }
        if {[file pathtype $entry] ne "relative" || ".." in [file split $entry]} {
            error "filelist entry must be confined and relative: $entry"
        }
        set source $root
        foreach component [file split $entry] {
            set source [file join $source $component]
            if {[file exists $source] && [file type $source] eq "link"} {
                error "filelist symlink forbidden: $entry"
            }
        }
        set source [regular_file $source]
        if {[file extension $source] ni {.v .sv .vh .svh}} {
            error "unsupported RTL file: $entry"
        }
        if {$source in $sources} { error "duplicate filelist entry: $entry" }
        if {[file extension $source] in {.v .sv}} { incr compiled }
        lappend sources $source
    }
    if {!$compiled} { error "filelist contains no Verilog sources" }
    return $sources
}

proc require_part {} {
    set part xcu250-figd2104-2L-e
    if {![llength [info commands get_parts]]} { error "Vivado get_parts command required" }
    set matches [get_parts -quiet $part]
    if {[llength $matches] != 1 || [lindex $matches 0] ne $part} {
        error "required FPGA part unavailable: $part"
    }
    return $part
}

proc validate_xdc {path} {
    set stream [open $path r]
    try { set lines [split [read $stream] \n] } finally { close $stream }
    set clock_count 0
    set async_reset 0
    foreach line $lines {
        set line [string trim $line]
        if {$line eq "" || [string match "#*" $line]} { continue }
        if {[regexp {^create_clock -name core_clk -period ([0-9]+(?:\.[0-9]+)?) \[get_ports CLK\]$} $line unused period]} {
            if {$period <= 0 || [incr clock_count] != 1} { error "exactly one positive core clock required" }
        } elseif {$line eq {set_false_path -from [get_ports RST_N]}} {
            if {$async_reset || $clock_count != 1} { error "reset exception must follow the core clock once" }
            set async_reset 1
        } else {
            error "unsupported evaluation XDC command: $line"
        }
    }
    if {$clock_count != 1} { error "core clock constraint required" }
    return $async_reset
}

proc module_source {sources name} {
    set matches {}
    set pattern [format {(?s)\mmodule\s+%s\s*\((.*?)\)\s*;(.*?)\mendmodule\M} $name]
    foreach source $sources {
        if {[file extension $source] ni {.v .sv}} { continue }
        set stream [open $source r]
        try { set text [read $stream] } finally { close $stream }
        regsub -all {"(?:\\.|[^"\\])*"|/\*(?:[^*]|\*+[^*/])*\*+/|//[^\n]*} $text { } text
        lappend matches {*}[regexp -all -inline -- $pattern $text]
    }
    if {[llength $matches] != 3} { error "one plain generated module required for reset proof: $name" }
    lassign $matches unused ports body
    if {[regexp {`|\mmodule\M|/\*|\*/} "$ports\n$body"]} { error "unsupported reset proof source grammar: $name" }
    return [list $ports $body]
}

proc require_async_reset {sources wrapper core_top} {
    lassign [module_source $sources $core_top] ports body
    set scalar_input {(?:^|,)\s*input\s+(?:(?:wire|logic|reg)\s+)?RST_N\s*(?:,|$)}
    if {![regexp $scalar_input $ports] ||
        ![regexp {\malways(?:_ff)?\s*@\s*\([^)]*\mnegedge\s+RST_N\M[^)]*\)} $body]} {
        error "RST_N exception requires asynchronous source event in $core_top"
    }
    lassign [module_source [list $wrapper] PoTalEvaluationOoc] ports body
    set pattern [format {\m%s\s+core\s*\(([^;]*)\)\s*;} $core_top]
    set instance [regexp -all -inline -- $pattern $body]
    if {![regexp $scalar_input $ports] || [llength $instance] != 2 ||
        ![regexp {\.RST_N\s*\(\s*RST_N\s*\)} [lindex $instance 1]]} {
        error "wrapper must forward scalar RST_N to the complete core instance"
    }
}

proc timing_count {report name} {
    set pattern [format {(?im)^[ \t]*(?:[0-9]+[.][ \t]*)?(?:checking[ \t]+)?%s[ \t]+\(([0-9]+)\)[ \t]*$} $name]
    set matches [regexp -all -inline -- $pattern $report]
    if {[llength $matches] != 2} { error "unknown or ambiguous check_timing count: $name" }
    scan [lindex $matches 1] %d count
    return $count
}

proc save_text {path value} {
    set stream [open $path {WRONLY CREAT EXCL}]
    try { puts $stream $value } finally { close $stream }
}

proc json_string {value} {
    return \"[string map [list \\ \\\\ \" \\\" \n \\n \r \\r \t \\t] $value]\"
}

proc clock_switching {report} {
    if {![regexp {(?m)^[ \t]*WNS\(ns\)[^\n]*WPWS\(ns\)[ \t]+TPWS\(ns\)[ \t]+TPWS Failing Endpoints[ \t]+TPWS Total Endpoints[ \t]*\n[- \t]+\n[ \t]*([^\n]+)} $report unused row]} {
        error "missing clock-switching timing summary"
    }
    set values [regexp -all -inline {\S+} $row]
    if {[llength $values] != 12} { error "unknown clock-switching summary columns" }
    lassign [lrange $values 8 11] worst total failing endpoints
    foreach slack [list $worst $total] {
        if {![regexp {^[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$} $slack]} { error "invalid clock-switching slack" }
    }
    if {![string is integer -strict $failing] || ![string is integer -strict $endpoints] ||
        $worst < 0 || $total < 0 || $failing != 0 || $endpoints <= 0} { error "clock-switching limits failed or unmeasured" }
    return [list $worst $total $failing $endpoints]
}

proc main {} {
    global argv
    if {$argv eq {--check-part}} {
        puts "OOC_PART_AVAILABLE part=[require_part]"
        return
    }
    if {[llength $argv] % 2} { error "expected --filelist --wrapper --xdc --core-top --out pairs" }
    set options {}
    foreach {key value} $argv {
        if {$key ni {--filelist --wrapper --xdc --core-top --out} || $value eq ""} {
            error "unknown or empty option: $key"
        }
        if {[dict exists $options $key]} { error "duplicate option: $key" }
        dict set options $key $value
    }
    foreach key {--filelist --wrapper --xdc --core-top --out} {
        if {![dict exists $options $key]} { error "required option missing: $key" }
    }
    set core_top [dict get $options --core-top]
    if {![regexp {^IM2PGemminiWSHP1A([48])W\1D(16|32|64)$} $core_top]} {
        error "unsupported integrated core top: $core_top"
    }
    set sources [filelist_sources [regular_file [dict get $options --filelist]]]
    set wrapper [regular_file [dict get $options --wrapper]]
    if {[file extension $wrapper] ni {.v .sv}} { error "Verilog wrapper required" }
    if {$wrapper in $sources} { error "wrapper must be separate from core filelist" }
    set xdc [regular_file [dict get $options --xdc]]
    set async_reset [validate_xdc $xdc]
    if {$async_reset} { require_async_reset $sources $wrapper $core_top }
    set out [file normalize [dict get $options --out]]
    if {[file exists $out]} { error "fresh output directory required: $out" }
    set part [require_part]
    file mkdir $out
    create_project -in_memory -part $part
    if {[get_property PART [current_project]] ne $part} { error "Vivado part mismatch" }
    set include_dirs {}
    foreach source $sources { lappend include_dirs [file dirname $source] }
    set_property include_dirs [lsort -unique $include_dirs] [current_fileset]
    foreach source [concat $sources [list $wrapper]] {
        switch -- [file extension $source] {
            .sv { read_verilog -sv $source }
            .v { read_verilog $source }
        }
    }
    read_xdc $xdc
    synth_design -mode out_of_context -top PoTalEvaluationOoc -part $part
    if {[get_property TOP [current_fileset]] ne "PoTalEvaluationOoc"} { error "synthesis top mismatch" }
    if {$async_reset && [llength [get_ports -quiet RST_N]] != 1} {
        error "RST_N exception requires an actual reset port"
    }
    set port [get_ports -quiet CLK]
    set clock [get_clocks -quiet core_clk]
    if {[llength $port] != 1 || [llength $clock] != 1 ||
        [llength [get_clocks -quiet *]] != 1 || [get_clocks -quiet -of_objects $port] ne $clock} {
        error "XDC must define exactly one core_clk clock on CLK"
    }
    set period [get_property PERIOD $clock]
    if {![regexp {^[0-9]+(?:\.[0-9]+)?$} $period] || $period <= 0} { error "invalid core clock period" }
    if {[llength [get_cells -hierarchical -quiet -filter {IS_BLACKBOX == 1}]]} {
        error "unresolved black boxes after synthesis"
    }
    report_utilization -file [file join $out post-synth-utilization.rpt]
    report_timing_summary -delay_type min_max -file [file join $out post-synth-timing.rpt]
    opt_design
    place_design
    route_design
    report_route_status -file [file join $out post-route-route-status.rpt]
    report_utilization -file [file join $out post-route-utilization.rpt]
    report_timing_summary -delay_type min_max -report_unconstrained -check_timing_verbose \
        -file [file join $out post-route-timing.rpt]
    report_pulse_width -min_period -max_period -low_pulse -high_pulse -max_skew -clocks $clock -significant_digits 13 -file [file join $out post-route-pulse-width.rpt]
    set channel [open [file join $out post-route-timing.rpt] r]
    try { set summary [read $channel] } finally { close $channel }
    lassign [clock_switching $summary] switching_worst switching_total switching_failing switching_endpoints
    report_drc -file [file join $out post-route-drc.rpt]
    write_checkpoint [file join $out route.dcp]
    set internal [check_timing -override_defaults {no_clock unconstrained_internal_endpoints multiple_clock loops} -return_string]
    set external [check_timing -override_defaults {no_input_delay no_output_delay} -return_string]
    save_text [file join $out post-route-check-timing.rpt] "$internal\n$external"
    set counts {}
    set unconstrained 0
    foreach name {no_clock unconstrained_internal_endpoints multiple_clock loops} {
        set count [timing_count $internal $name]
        dict set counts $name $count
        incr unconstrained $count
    }
    foreach name {no_input_delay no_output_delay} { dict set counts $name [timing_count $external $name] }
    set setup [get_timing_paths -from $clock -to $clock -delay_type max -max_paths 1 -no_report_unconstrained]
    set hold [get_timing_paths -from $clock -to $clock -delay_type min -max_paths 1 -no_report_unconstrained]
    if {[llength $setup] != 1 || [llength $hold] != 1} { error "no core register-to-register setup/hold paths" }
    set setup_slack [get_property SLACK $setup]
    set hold_slack [get_property SLACK $hold]
    foreach slack [list $setup_slack $hold_slack] {
        if {![regexp {^[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$} $slack]} {
            error "finite numeric timing slack required"
        }
    }
    set errors [get_drc_violations -quiet -filter {SEVERITY == Error}]
    set critical [get_drc_violations -quiet -filter {SEVERITY == {Critical Warning}}]
    set strings [dict create schema im2p-ooc-tool-result top PoTalEvaluationOoc core_top $core_top \
        part $part clock_port CLK period_ns $period tool_version [version -short] timing_stage POST_ROUTE \
        setup_slack_ns $setup_slack hold_slack_ns $hold_slack status ROUTE_COMPLETED \
        clock_switching_worst_slack_ns $switching_worst clock_switching_total_slack_ns $switching_total]
    set fields {}
    dict for {key value} $strings { lappend fields "[json_string $key]:[json_string $value]" }
    set numbers [dict create version 2 timed_paths [expr {[llength $setup] + [llength $hold]}] \
        clock_switching_failing_endpoints $switching_failing clock_switching_total_endpoints $switching_endpoints \
        unconstrained_paths $unconstrained no_clock [dict get $counts no_clock] \
        multiple_clock [dict get $counts multiple_clock] loops [dict get $counts loops] \
        external_input_without_delay [dict get $counts no_input_delay] \
        external_output_without_delay [dict get $counts no_output_delay] \
        blocking_drc [expr {[llength $errors] + [llength $critical]}]]
    dict for {key value} $numbers { lappend fields "[json_string $key]:$value" }
    save_text [file join $out ooc-result.json] "\{[join $fields ,]\}"
    puts "OOC_ROUTE_COMPLETED part=$part core_top=$core_top"
}

if {[catch {main} message options]} {
    puts stderr "EVALUATION_OOC_FAILED: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
