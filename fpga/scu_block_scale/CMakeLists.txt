cmake_minimum_required(VERSION 3.16)
project(im2p_scu_integration LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_GEMMINI ON CACHE BOOL "" FORCE)
set(GGML_GEMMINI_OPTION WS CACHE STRING "" FORCE)
option(SCU_FPGA_UART "Build the IFR3 physical transport adapter (tests use PTY)" OFF)
if(SCU_FPGA_UART)
  set(GGML_GEMMINI_EXECUTION_BACKEND FPGA_UART CACHE STRING "" FORCE)
  set(GGML_GEMMINI_FPGA_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}" CACHE PATH "" FORCE)
  set(GGML_GEMMINI_FPGA_SIM_MANIFEST
    "${CORE_ROOT}/build/selected/a8-w8-d16/current/real-lib.json" CACHE FILEPATH "" FORCE)
else()
  set(GGML_GEMMINI_EXECUTION_BACKEND IM2P_SIM CACHE STRING "" FORCE)
endif()
set(GGML_GEMMINI_ACTIVATION_BITS 8 CACHE STRING "" FORCE)
set(GGML_GEMMINI_WEIGHT_BITS 8 CACHE STRING "" FORCE)
set(GGML_GEMMINI_DIM 16 CACHE STRING "" FORCE)
set(GGML_GEMMINI_BLOCK_SIZE 32 CACHE STRING "" FORCE)
set(GGML_GEMMINI_ACTIVATION_QUANT EXSIA CACHE STRING "" FORCE)
set(GGML_GEMMINI_ENABLE_RMD OFF CACHE BOOL "" FORCE)
set(GGML_GEMMINI_ENABLE_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
add_subdirectory("${HOST_ROOT}" host-build EXCLUDE_FROM_ALL)
target_compile_features(ggml-gemmini PRIVATE cxx_std_20)
if(SCU_FPGA_UART)
  add_executable(scu_host_dispatch host_dispatch.cpp)
  target_include_directories(scu_host_dispatch PRIVATE
    "${CMAKE_CURRENT_BINARY_DIR}/host-build/generated" "${HOST_ROOT}/ggml/src"
    "${HOST_ROOT}/ggml/src/ggml-gemmini" "${HOST_ROOT}/ggml/src/ggml-gemmini-utils/include"
    "${CORE_ROOT}/frontend/include" "${CORE_ROOT}/sim/include" "${GEMMINI_SW_PATH}/include")
  target_link_libraries(scu_host_dispatch PRIVATE ggml-gemmini ggml-base
    ggml-gemmini-fpga-simulator pthread dl m)
  target_link_options(scu_host_dispatch PRIVATE -Wl,--wrap=im2p_sim_create
    -Wl,--wrap=im2p_execute_matmul_extended -Wl,--wrap=im2p_begin_striped_matmul)
else()
add_executable(scu_frontend_final "${CORE_ROOT}/tests/scu_block_scale/frontend_final_integer.cpp")
target_include_directories(scu_frontend_final PRIVATE
  "${CMAKE_CURRENT_BINARY_DIR}/host-build/generated" "${HOST_ROOT}/ggml/src"
  "${HOST_ROOT}/ggml/src/ggml-gemmini" "${HOST_ROOT}/ggml/src/ggml-gemmini-utils/include"
  "${CORE_ROOT}/frontend/include" "${CORE_ROOT}/sim/include" "${GEMMINI_SW_PATH}/include")
target_link_libraries(scu_frontend_final PRIVATE ggml-gemmini ggml-base
  ggml-gemmini-im2p-frontend ggml-gemmini-im2p-simulator pthread dl m)
endif()
