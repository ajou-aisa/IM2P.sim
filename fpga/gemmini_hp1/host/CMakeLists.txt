cmake_minimum_required(VERSION 3.20)
project(im2p_gemmini_hp1_host LANGUAGES CXX)

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

add_library(gemmini_hp1_host_common STATIC uart.cpp)
target_compile_features(gemmini_hp1_host_common PUBLIC cxx_std_20)
target_include_directories(gemmini_hp1_host_common PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}")
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  target_compile_options(gemmini_hp1_host_common PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

add_executable(gemmini_hp1_host_test test_host.cpp)
target_link_libraries(gemmini_hp1_host_test PRIVATE gemmini_hp1_host_common)
if(CMAKE_CXX_COMPILER_ID MATCHES "Clang|GNU")
  target_compile_options(gemmini_hp1_host_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

enable_testing()
add_test(NAME gemmini_hp1_host_contract COMMAND gemmini_hp1_host_test
  "${IM2P_profile}" "${IM2P_MANIFEST_SHA256}" "${IM2P_numerical_revision}"
  "${IM2P_activation_bits}" "${IM2P_weight_bits}" "${IM2P_dim}"
  "${IM2P_accumulator_bits}" "${IM2P_block_size}" "${IM2P_PACKING_ENUM}"
  "${IM2P_bank_count}" "${IM2P_bank_rows}" "${IM2P_accumulator_rows}"
  "${IM2P_scratchpad_row_bytes}" "${IM2P_accumulator_row_bytes}")
