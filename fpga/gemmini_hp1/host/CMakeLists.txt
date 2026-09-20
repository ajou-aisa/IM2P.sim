cmake_minimum_required(VERSION 3.20)
project(im2p_gemmini_hp1_host LANGUAGES C CXX)

if(NOT IM2P_GEMMINI_RESOLVED_PROFILE OR
   NOT EXISTS "${IM2P_GEMMINI_RESOLVED_PROFILE}" OR
   IS_DIRECTORY "${IM2P_GEMMINI_RESOLVED_PROFILE}")
  message(FATAL_ERROR "IM2P_GEMMINI_RESOLVED_PROFILE must name a resolved-profile.json")
endif()

file(READ "${IM2P_GEMMINI_RESOLVED_PROFILE}" IM2P_PROFILE_JSON)
file(SHA256 "${IM2P_GEMMINI_RESOLVED_PROFILE}" IM2P_MANIFEST_SHA256)
foreach(field IN ITEMS profile implementation activation_bits weight_bits dim
                       accumulator_bits block_size scu packing numerical_revision)
  string(JSON IM2P_${field} ERROR_VARIABLE error GET "${IM2P_PROFILE_JSON}" ${field})
  if(error)
    message(FATAL_ERROR "resolved profile field ${field}: ${error}")
  endif()
endforeach()
foreach(field IN ITEMS bank_count bank_rows accumulator_rows scratchpad_row_bytes
                       accumulator_row_bytes ws_double_buffered)
  string(JSON IM2P_${field} ERROR_VARIABLE error GET "${IM2P_PROFILE_JSON}" memory ${field})
  if(error)
    message(FATAL_ERROR "resolved memory field ${field}: ${error}")
  endif()
endforeach()

if(NOT IM2P_implementation STREQUAL "gemmini-hp1" OR
   NOT IM2P_accumulator_bits EQUAL 32 OR
   NOT IM2P_block_size EQUAL 32 OR
   NOT IM2P_scu STREQUAL "hp1-left-shift" OR
   NOT IM2P_numerical_revision STREQUAL "hp1-fragment-sat32-v1" OR
   NOT IM2P_activation_bits EQUAL IM2P_weight_bits OR
   NOT IM2P_dim MATCHES "^(16|32|64)$" OR
   NOT IM2P_activation_bits MATCHES "^(4|8)$" OR
   NOT IM2P_ws_double_buffered)
  message(FATAL_ERROR "resolved profile is not a supported Gemmini HP1 contract")
endif()
if(IM2P_activation_bits EQUAL 4)
  if(NOT IM2P_packing STREQUAL "signed-int4-low-nibble-first")
    message(FATAL_ERROR "A4 resolved profile packing mismatch")
  endif()
  set(IM2P_PACKING_ENUM "Packing::signed_int4_low_nibble_first")
else()
  if(NOT IM2P_packing STREQUAL "signed-int8")
    message(FATAL_ERROR "A8 resolved profile packing mismatch")
  endif()
  set(IM2P_PACKING_ENUM "Packing::signed_int8")
endif()

get_filename_component(IM2P_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../../.." REALPATH)
get_filename_component(IM2P_WORKSPACE_ROOT "${IM2P_ROOT}/.." REALPATH)
get_filename_component(IM2P_PROFILE_DIR "${IM2P_GEMMINI_RESOLVED_PROFILE}" DIRECTORY)
set(IM2P_LLAMA_ROOT "${IM2P_WORKSPACE_ROOT}/llama.cpp-gemmini" CACHE PATH
    "llama.cpp-gemmini source root")
set(IM2P_GEMMINI_INCLUDE_ROOT
    "${IM2P_WORKSPACE_ROOT}/RISC-V-DynDNN-gemmini-include" CACHE PATH
    "Gemmini software header root")
set(IM2P_GEMMINI_HOST_PARAMS_DIR "${IM2P_PROFILE_DIR}/host-params" CACHE PATH
    "Generated selected-profile Gemmini parameter directory")
set(IM2P_LLAMA_GEMMINI "${IM2P_LLAMA_ROOT}/ggml/src/ggml-gemmini")
set(IM2P_FRONTEND_SOURCE "${IM2P_ROOT}/frontend/src/im2p_gemmini_frontend.cpp")
foreach(required IN ITEMS
    "${IM2P_GEMMINI_HOST_PARAMS_DIR}/gemmini_params.h"
    "${IM2P_LLAMA_GEMMINI}/ggml-gemmini-args.h"
    "${IM2P_FRONTEND_SOURCE}")
  if(NOT EXISTS "${required}" OR IS_DIRECTORY "${required}")
    message(FATAL_ERROR "Gemmini HP1 host input missing: ${required}")
  endif()
endforeach()

if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  set_source_files_properties("${IM2P_FRONTEND_SOURCE}" PROPERTIES
    COMPILE_OPTIONS -Wno-unused-parameter)
endif()

find_package(Threads REQUIRED)
add_subdirectory("${IM2P_LLAMA_ROOT}/ggml/src/ggml-gemmini-utils"
                 "${CMAKE_CURRENT_BINARY_DIR}/gemmini-utils")

add_library(gemmini_hp1_host_profile INTERFACE)
target_compile_features(gemmini_hp1_host_profile INTERFACE cxx_std_20)
target_compile_definitions(gemmini_hp1_host_profile INTERFACE
  GGML_GEMMINI_ACTIVATION_BITS=${IM2P_activation_bits}
  GGML_GEMMINI_WEIGHT_BITS=${IM2P_weight_bits}
  GGML_GEMMINI_CONFIGURED_DIM=${IM2P_dim}
  GGML_GEMMINI_BLOCK_SIZE=${IM2P_block_size}
  IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS=${IM2P_activation_bits}
  IM2P_GEMMINI_FRONTEND_EXPECTED_DIM=${IM2P_dim}
  IM2P_GEMMINI_EXTERNAL_EXECUTOR_ONLY=1
  GGML_GEMMINI_EXECUTION_BACKEND_FPGA_UART=1
  IM2P_FPGA_ARCH_GEMMINI_HP1=1
  IM2P_FPGA_PROFILE_ID="${IM2P_profile}"
  IM2P_FPGA_BUILD_ID="${IM2P_MANIFEST_SHA256}")
target_include_directories(gemmini_hp1_host_profile INTERFACE
  "${IM2P_GEMMINI_HOST_PARAMS_DIR}"
  "${CMAKE_CURRENT_SOURCE_DIR}"
  "${IM2P_ROOT}/frontend/include"
  "${IM2P_ROOT}/sim/include"
  "${IM2P_LLAMA_GEMMINI}"
  "${IM2P_LLAMA_ROOT}/ggml/src/ggml-gemmini-utils/include"
  "${IM2P_LLAMA_ROOT}/ggml/include"
  "${IM2P_LLAMA_ROOT}/ggml/src")
target_include_directories(gemmini_hp1_host_profile SYSTEM INTERFACE
  "${IM2P_GEMMINI_INCLUDE_ROOT}")

add_library(gemmini_hp1_codec STATIC uart.cpp)
target_link_libraries(gemmini_hp1_codec PUBLIC gemmini_hp1_host_profile)

add_library(gemmini_hp1_ggml_numeric STATIC "${IM2P_LLAMA_ROOT}/ggml/src/ggml.c")
target_include_directories(gemmini_hp1_ggml_numeric PRIVATE
  "${IM2P_LLAMA_ROOT}/ggml/include" "${IM2P_LLAMA_ROOT}/ggml/src")

add_library(gemmini_hp1_host_common STATIC
  uart.cpp
  rmd.cpp
  ggml-gemmini-fpga.cpp
  "${IM2P_LLAMA_GEMMINI}/residual/rmd/rmd-builder.cpp"
  "${IM2P_LLAMA_GEMMINI}/residual/rmd/rmd-compose.cpp"
  "${IM2P_LLAMA_GEMMINI}/residual/rmd/rmd-executor.cpp"
  "${IM2P_LLAMA_GEMMINI}/residual/rmd/rmd-im2p-executor.cpp"
  "${IM2P_LLAMA_GEMMINI}/quants/common/weight_reader.cpp"
  "${IM2P_LLAMA_GEMMINI}/quants/common/dequant.cpp"
  "${IM2P_LLAMA_GEMMINI}/quants/act/dispatch.cpp"
  "${IM2P_FRONTEND_SOURCE}")
target_compile_definitions(gemmini_hp1_host_common PUBLIC GGML_GEMMINI_ENABLE_RMD=1)
target_compile_options(gemmini_hp1_host_common PRIVATE -O2 -fno-fast-math)
target_link_libraries(gemmini_hp1_host_common PUBLIC
  gemmini_hp1_host_profile gemmini_hp1_ggml_numeric ggml-gemmini-utils Threads::Threads)

add_executable(gemmini_hp1_host_contract_test test_host.cpp)
target_link_libraries(gemmini_hp1_host_contract_test PRIVATE gemmini_hp1_codec)

add_executable(gemmini_hp1_host_orchestration test_host_orchestration.cpp)
target_link_libraries(gemmini_hp1_host_orchestration PRIVATE
  gemmini_hp1_host_common)

add_executable(gemmini_hp1_host_rmd_reject_test
  test_host_rmd_reject.cpp)
target_compile_definitions(gemmini_hp1_host_rmd_reject_test PRIVATE
  GGML_GEMMINI_ENABLE_RMD=1)
target_link_libraries(gemmini_hp1_host_rmd_reject_test PRIVATE
  gemmini_hp1_host_common)

if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  foreach(target IN ITEMS gemmini_hp1_codec gemmini_hp1_host_common
                          gemmini_hp1_host_contract_test
                          gemmini_hp1_host_orchestration
                          gemmini_hp1_host_rmd_reject_test)
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Werror)
  endforeach()
endif()
if(APPLE)
  target_link_options(gemmini_hp1_host_common INTERFACE -Wl,-dead_strip)
  target_link_options(gemmini_hp1_host_orchestration PRIVATE
    "-Wl,-map,${CMAKE_CURRENT_BINARY_DIR}/gemmini_hp1_host_orchestration.map")
elseif(UNIX)
  target_compile_options(gemmini_hp1_ggml_numeric PRIVATE -ffunction-sections -fdata-sections)
  target_compile_options(gemmini_hp1_host_common PRIVATE -ffunction-sections -fdata-sections)
  target_link_options(gemmini_hp1_host_common INTERFACE -Wl,--gc-sections)
  target_link_options(gemmini_hp1_host_orchestration PRIVATE
    "-Wl,-Map,${CMAKE_CURRENT_BINARY_DIR}/gemmini_hp1_host_orchestration.map")
endif()

enable_testing()
add_test(NAME gemmini_hp1_codec_contract COMMAND gemmini_hp1_host_contract_test
  "${IM2P_profile}" "${IM2P_MANIFEST_SHA256}" "${IM2P_numerical_revision}"
  "${IM2P_activation_bits}" "${IM2P_weight_bits}" "${IM2P_dim}"
  "${IM2P_accumulator_bits}" "${IM2P_block_size}" "${IM2P_PACKING_ENUM}"
  "${IM2P_bank_count}" "${IM2P_bank_rows}" "${IM2P_accumulator_rows}"
  "${IM2P_scratchpad_row_bytes}" "${IM2P_accumulator_row_bytes}")
add_test(NAME gemmini_hp1_host_common_orchestration
  COMMAND gemmini_hp1_host_orchestration
          "${IM2P_profile}" "${IM2P_MANIFEST_SHA256}")
add_test(NAME gemmini_hp1_rmd_invalid_dispatch_reject
  COMMAND gemmini_hp1_host_rmd_reject_test)
