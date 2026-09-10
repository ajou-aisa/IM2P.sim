# Requires NEW user approval for Dense PIPELINE. Configuration SRAM once only.
# Do not execute before approval of the sealed source/host/fixture package.
# No cfgmem object, flash programming, source edits, or bitstream generation.
proc verify_hash {path expected} {
    set actual [lindex [exec sha256sum -- $path] 0]
    if {$actual ne $expected} {error "SHA256 mismatch: $path"}
    puts "VERIFIED_SHA256 $actual $path"
}
if {[catch {
    if {[llength $argv] != 2} {error "Expected frozen bitstream and routed checkpoint"}
    set bitfile [file normalize [lindex $argv 0]]
    set checkpoint [file normalize [lindex $argv 1]]
    set bitsha 8aef393d040bb306e6ddf7b4b977976a9a924dbc5aec62b37b1690f0aa18c0ca
    verify_hash $bitfile $bitsha
    verify_hash $checkpoint 6141a245e4c2ca88b8147ae864974abc14c3b858a276c2a5f5beee42f5a92bd4
    open_checkpoint $checkpoint
    set implemented_part [get_property PART [current_design]]
    if {$implemented_part ne "xc7a100tcsg324-1"} {error "Implemented part mismatch: $implemented_part"}
    puts "VERIFIED_IMPLEMENTED_PART $implemented_part"
    close_design
    open_hw_manager
    connect_hw_server -url localhost:3121
    set targets [get_hw_targets *210319BE7725A]
    if {[llength $targets] != 1} {error "Approved Digilent cable not uniquely available"}
    open_hw_target [lindex $targets 0]
    set devices [get_hw_devices]
    if {[llength $devices] != 1} {error "Unexpected JTAG chain"}
    set device [lindex $devices 0]
    if {$device ne "xc7a100t_0"} {error "JTAG device mismatch: $device"}
    set hwpart [get_property PART $device]
    if {$hwpart ne "xc7a100t"} {error "JTAG silicon part mismatch: $hwpart"}
    puts "VERIFIED_JTAG_DEVICE $device DIE_PART=$hwpart IMPLEMENTED_PART=$implemented_part"
    puts "NOTE: JTAG identifies silicon die, not physical package or speed grade."
    set_property PROGRAM.FILE $bitfile $device
    verify_hash $bitfile $bitsha
    set once [open [file join [file dirname [info script]] programming-attempted.txt] {WRONLY CREAT EXCL}]
    puts $once "One SRAM programming attempt authorized and consumed"
    close $once
    puts "BEGIN_APPROVED_VOLATILE_PROGRAMMING"
    program_hw_devices $device
    refresh_hw_device -update_hw_probes false $device
    report_property $device
    foreach {property expected} {
        REGISTER.CONFIG_STATUS.BIT00_CRC_ERROR 0
        REGISTER.CONFIG_STATUS.BIT02_PLL_LOCK_STATUS 1
        REGISTER.CONFIG_STATUS.BIT04_END_OF_STARTUP_(EOS)_STATUS 1
        REGISTER.CONFIG_STATUS.BIT11_INIT_B_INTERNAL_SIGNAL_STATUS 1
        REGISTER.CONFIG_STATUS.BIT12_INIT_B_PIN 1
        REGISTER.CONFIG_STATUS.BIT13_DONE_INTERNAL_SIGNAL_STATUS 1
        REGISTER.CONFIG_STATUS.BIT14_DONE_PIN 1
        REGISTER.CONFIG_STATUS.BIT15_IDCODE_ERROR 0
        REGISTER.CONFIG_STATUS.BIT16_SECURITY_ERROR 0
        REGISTER.CONFIG_STATUS.BIT17_SYSTEM_MONITOR_OVER-TEMP_ALARM_STATUS 0
        REGISTER.CONFIG_STATUS.BIT27_HMAC_ERROR 0
        REGISTER.CONFIG_STATUS.BIT29_BAD_PACKET_ERROR 0
        REGISTER.IR.BIT2_ISC_DONE 1
        REGISTER.IR.BIT4_INIT_COMPLETE 1
        REGISTER.IR.BIT5_DONE 1
    } {
        set actual [get_property $property $device]
        if {$actual ne $expected} {error "Programming verification failed: $property=$actual expected=$expected"}
        puts "VERIFIED_CONFIGURATION $property=$actual"
    }
    puts "SRAM_CONFIGURATION_STATUS_VERIFIED"
    verify_hash $bitfile $bitsha
    puts "APPROVED_VOLATILE_PROGRAMMING_COMPLETED"
    close_hw_target
    disconnect_hw_server
    close_hw_manager
} message options]} {
    puts stderr "APPROVED_PROGRAMMING_FAILED: $message"
    puts stderr [dict get $options -errorinfo]
    exit 1
}
exit 0
