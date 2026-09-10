cmake_minimum_required(VERSION 3.16)
project(im2p_full_replay LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_GEMMINI ON CACHE BOOL "" FORCE)
set(GGML_GEMMINI_OPTION CPU CACHE STRING "" FORCE)
# This config builds existing host reference/quantizers. FPGA replay selection
# is explicit in the harness and never changes HARDWARE or IM2P_SIM meanings.
set(GGML_GEMMINI_EXECUTION_BACKEND HARDWARE CACHE STRING "" FORCE)
set(GGML_GEMMINI_ACTIVATION_BITS 8 CACHE STRING "" FORCE)
set(GGML_GEMMINI_WEIGHT_BITS 8 CACHE STRING "" FORCE)
set(GGML_GEMMINI_DIM 16 CACHE STRING "" FORCE)
set(GGML_GEMMINI_BLOCK_SIZE 32 CACHE STRING "" FORCE)
set(GGML_GEMMINI_ACTIVATION_QUANT EXSIA CACHE STRING "" FORCE)
set(GGML_GEMMINI_ENABLE_RMD OFF CACHE BOOL "" FORCE)
set(GGML_GEMMINI_ENABLE_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
add_subdirectory("${HOST_ROOT}" host-build EXCLUDE_FROM_ALL)
add_executable(full_replay capture.cpp "${CORE_ROOT}/frontend/src/im2p_gemmini_frontend.cpp")
target_include_directories(full_replay PRIVATE
  "${CMAKE_CURRENT_BINARY_DIR}/host-build/generated"
  "${HOST_ROOT}/ggml/src" "${HOST_ROOT}/ggml/src/ggml-gemmini"
  "${HOST_ROOT}/ggml/src/ggml-gemmini-utils/include"
  "${CORE_ROOT}/frontend/include" "${CORE_ROOT}/sim/include"
  "${GEMMINI_SW_PATH}/include")
target_compile_definitions(full_replay PRIVATE
  IM2P_GEMMINI_FRONTEND_EXPECTED_DIM=16 IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS=8
  GGML_GEMMINI_ACTIVATION_BITS=8 GGML_GEMMINI_WEIGHT_BITS=8 GGML_GEMMINI_BLOCK_SIZE=32)
target_link_libraries(full_replay PRIVATE ggml-gemmini ggml-base "${SIM_ARCHIVE}" pthread dl m)
target_link_options(full_replay PRIVATE -Wl,--wrap=im2p_sim_create -Wl,--wrap=im2p_execute_matmul_extended)
message(STATUS "FULL replay backend: explicit FPGA_REPLAY_V1; host reference CPU; residual OFF")
