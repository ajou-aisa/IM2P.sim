# Pin source: Digilent Arty-A7-100-Master.xdc, Rev D/E, saved under reference/.
# Arty A7 E.0 schematic sheets 6/7: CFGBVS_0 pulled to VCC3V3, VCCO_0=3.3V.
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
set_property -dict { PACKAGE_PIN E3 IOSTANDARD LVCMOS33 } [get_ports CLK100MHZ]
create_clock -name sys_clk_pin -period 10.000 [get_ports CLK100MHZ]
set_property -dict { PACKAGE_PIN D9 IOSTANDARD LVCMOS33 } [get_ports btn0]
# Digilent uart_txd_in is the FTDI->FPGA input; uart_rxd_out is FPGA->FTDI.
set_property -dict { PACKAGE_PIN A9 IOSTANDARD LVCMOS33 } [get_ports uart_rx]
set_property -dict { PACKAGE_PIN D10 IOSTANDARD LVCMOS33 } [get_ports uart_tx]
set_property -dict { PACKAGE_PIN H5 IOSTANDARD LVCMOS33 } [get_ports {led[0]}]
set_property -dict { PACKAGE_PIN J5 IOSTANDARD LVCMOS33 } [get_ports {led[1]}]
set_property -dict { PACKAGE_PIN T9 IOSTANDARD LVCMOS33 } [get_ports {led[2]}]
set_property -dict { PACKAGE_PIN T10 IOSTANDARD LVCMOS33 } [get_ports {led[3]}]
# MMCM output clock is derived by Vivado. No false/multicycle/max-delay exceptions.
# UART/reset are asynchronous external interfaces. Their CDC and unconstrained-I/O
# reports must be reviewed explicitly; this file is not a claim of board closure.
