# Offline only. Opens a routed checkpoint; never opens Hardware Manager.
proc main {} {
    global argv
    if {[llength $argv] != 2} {error "usage: DCP NEW_OUTPUT_DIRECTORY"}
    lassign $argv dcp out
    if {[file exists $out]} {error "fresh output directory required"}
    file mkdir $out
    set_param general.maxThreads 2
    open_checkpoint $dcp
    if {[get_property PART [current_design]] ne "xc7a100tcsg324-1"} {error "wrong implementation part"}
    report_property -all [current_design] -file $out/design-properties.txt
    set f [open $out/configuration-properties.tsv {WRONLY CREAT EXCL}]
    puts $f "property\tvalue"
    foreach p [lsort [list_property [current_design]]] {
        if {[regexp {^(CONFIG_MODE|CFGBVS|CONFIG_VOLTAGE|BITSTREAM\.)} $p]} {
            puts $f "$p\t[get_property $p [current_design]]"
        }
    }
    close $f
    # Hardware-only commands are documented separately. Loading Hardware
    # Manager merely to obtain their help is outside this offline probe.
    foreach command {write_cfgmem write_bitstream} {
        puts "OFFLINE_HELP $command"
        puts [help $command]
    }
    report_clocks -file $out/clocks.txt
    report_route_status -file $out/route-status.txt
    set f [open $out/counts.txt {WRONLY CREAT EXCL}]
    puts $f "part=[get_property PART [current_design]]"
    puts $f "cells=[llength [get_cells -hier -quiet]]"
    puts $f "nets=[llength [get_nets -hier -quiet]]"
    puts $f "blackboxes=[llength [get_cells -hier -quiet -filter {IS_BLACKBOX == 1}]]"
    close $f
    close_design
    puts "OFFLINE_CONFIGURATION_INSPECTION_PASS"
}
if {[catch {main} message options]} {
    puts stderr $message
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
