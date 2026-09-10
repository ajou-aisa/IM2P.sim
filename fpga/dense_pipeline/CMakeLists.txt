cmake_minimum_required(VERSION 3.16)
project(im2p_dense_pipeline LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_GEMMINI ON CACHE BOOL "" FORCE)
set(GGML_GEMMINI_OPTION WS CACHE STRING "" FORCE)
set(GGML_GEMMINI_EXECUTION_BACKEND FPGA_UART CACHE STRING "" FORCE)
set(GGML_GEMMINI_ACTIVATION_BITS 8 CACHE STRING "" FORCE)
set(GGML_GEMMINI_WEIGHT_BITS 8 CACHE STRING "" FORCE)
set(GGML_GEMMINI_DIM 16 CACHE STRING "" FORCE)
set(GGML_GEMMINI_BLOCK_SIZE 32 CACHE STRING "" FORCE)
set(GGML_GEMMINI_ACTIVATION_QUANT EXSIA CACHE STRING "" FORCE)
set(GGML_GEMMINI_ENABLE_RMD OFF CACHE BOOL "" FORCE)
set(GGML_GEMMINI_ENABLE_OPENMP OFF CACHE BOOL "" FORCE)
set(GGML_NATIVE OFF CACHE BOOL "" FORCE)
set(GGML_GEMMINI_FPGA_SOURCE_DIR "${CMAKE_CURRENT_SOURCE_DIR}" CACHE PATH "" FORCE)
set(IM2P_FPGA_PROTOCOL_VERSION 2 CACHE STRING "Pinned FPGA protocol version (1 FULL baseline, 2 streaming)")
add_subdirectory("${HOST_ROOT}" host-build EXCLUDE_FROM_ALL)
target_compile_features(ggml-gemmini PRIVATE cxx_std_20)
target_compile_definitions(ggml-gemmini PRIVATE IM2P_FPGA_BUILD_ID="${FPGA_BUILD_ID}")
target_compile_definitions(ggml-gemmini PUBLIC IM2P_FPGA_PROTOCOL_VERSION=${IM2P_FPGA_PROTOCOL_VERSION})
target_sources(ggml-gemmini PRIVATE ggml-gemmini-fpga.cpp uart.cpp
  "${CORE_ROOT}/frontend/src/im2p_gemmini_frontend.cpp")
target_include_directories(ggml-gemmini PRIVATE "${CORE_ROOT}/frontend/include" "${CORE_ROOT}/sim/include")
target_compile_definitions(ggml-gemmini PRIVATE IM2P_GEMMINI_FRONTEND_EXPECTED_DIM=16
  IM2P_GEMMINI_FRONTEND_ACTIVATION_BITS=8)
# Default frontend simulator paths remain linkable; selected FPGA invocations
# must prove zero simulator creates/executes at runtime.
target_link_libraries(ggml-gemmini PRIVATE "${SIM_ARCHIVE}" pthread dl m)
add_executable(dense_host_dispatch host_dispatch.cpp)
target_include_directories(dense_host_dispatch PRIVATE
  "${CMAKE_CURRENT_BINARY_DIR}/host-build/generated" "${HOST_ROOT}/ggml/src"
  "${HOST_ROOT}/ggml/src/ggml-gemmini" "${HOST_ROOT}/ggml/src/ggml-gemmini-utils/include"
  "${CORE_ROOT}/frontend/include" "${CORE_ROOT}/sim/include" "${GEMMINI_SW_PATH}/include")
target_link_libraries(dense_host_dispatch PRIVATE ggml-gemmini ggml-base "${SIM_ARCHIVE}" pthread dl m)
target_link_options(dense_host_dispatch PRIVATE -Wl,--wrap=im2p_sim_create
  -Wl,--wrap=im2p_execute_matmul_extended -Wl,--wrap=im2p_begin_striped_matmul)
add_executable(persistent_replay persistent_replay.cpp)
target_compile_definitions(persistent_replay PRIVATE IM2P_FULL_REPLAY_MAX_ROWS=336)
target_include_directories(persistent_replay PRIVATE
  "${CMAKE_CURRENT_BINARY_DIR}/host-build/generated" "${HOST_ROOT}/ggml/src"
  "${HOST_ROOT}/ggml/src/ggml-gemmini" "${HOST_ROOT}/ggml/src/ggml-gemmini-utils/include"
  "${CORE_ROOT}/frontend/include" "${CORE_ROOT}/sim/include" "${GEMMINI_SW_PATH}/include")
target_link_libraries(persistent_replay PRIVATE ggml-gemmini ggml-base "${SIM_ARCHIVE}" pthread dl m)
target_link_options(persistent_replay PRIVATE -Wl,--wrap=im2p_sim_create -Wl,--wrap=im2p_execute_matmul_extended)
message(STATUS "Selected FPGA_UART: native Q8_H1 / EXSIA / RMD OFF; explicit device; no dense fallback")
