#ifndef IM2P_VERILATOR_TESTING_H
#define IM2P_VERILATOR_TESTING_H

#ifndef IM2P_VERILATOR_TEST_HOOKS
#error "im2p_verilator_testing.h is private to test-hook builds"
#endif

#include "../im2p_verilator.h"

enum {
  IM2P_TEST_PORT_ACTIVATION_ROW = 0,
  IM2P_TEST_PORT_ACTIVATION_RESPONSE = 1,
  IM2P_TEST_PORT_WEIGHT_RESPONSE = 2,
  IM2P_TEST_PORT_SCALE_RESPONSE = 3,
};

#ifdef __cplusplus
extern "C" {
#endif

int im2p_test_drive_port(im2p_handle_t handle, uint32_t port,
                         const void *values, uint32_t count);
int im2p_test_copy_port_words(im2p_handle_t handle, uint32_t port,
                              uint32_t *words, uint32_t word_count);
uint32_t im2p_test_activation_enable_mask(im2p_handle_t handle);

#ifdef __cplusplus
}
#endif

#endif
