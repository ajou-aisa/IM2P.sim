use crate::{ffi, WorkStats};

use super::{Error, Im2pSimulator};

impl Im2pSimulator {
    pub(crate) fn matrix_counters(&self) -> ffi::MatrixCounters {
        let mut counters = ffi::MatrixCounters::default();
        // SAFETY: handle and output pointer remain valid for this call.
        unsafe { ffi::im2p_matrix_counters(self.handle.as_ptr(), &mut counters) };
        counters
    }

    pub(crate) fn work_stats(&self, completed_stripes: u64) -> WorkStats {
        let after = self.matrix_counters();
        let scale_after = self.scale_counters();
        WorkStats {
            work_total_cycles: self.last_completed_work_cycles(),
            activation_read_requests: after.activation_read_requests,
            weight_read_requests: after.weight_read_requests,
            scale_read_requests: after.scale_read_requests,
            output_write_requests: after.output_write_requests,
            output_write_responses: after.output_write_responses,
            activation_wait_cycles: after.activation_wait_cycles,
            weight_wait_cycles: after.weight_wait_cycles,
            scale_wait_cycles: scale_after.wait_cycles,
            output_wait_cycles: after.output_wait_cycles,
            stripe_host_wait_cycles: after.stripe_host_wait_cycles,
            compute_cycles: after.compute_cycles,
            drain_cycles: after.drain_cycles,
            weight_preload_cycles: after.weight_preload_cycles,
            same_block_scale_hits: scale_after.current_hits,
            next_scale_hits: scale_after.next_hits,
            scale_demand_misses: scale_after.demand_misses,
            overlap_cycles: after.overlap_cycles,
            cross_stripe_overlap_cycles: after.cross_stripe_overlap_cycles,
            activation_overlap_cycles: after.activation_overlap_cycles,
            weight_overlap_cycles: after.weight_overlap_cycles,
            scale_overlap_cycles: after.scale_overlap_cycles,
            completed_fragments: after.fragments_completed,
            completed_output_tiles: after.works_completed,
            completed_stripes,
            stripes_published: after.stripes_published,
            stripe_rows_published: after.stripe_rows_published,
            weight_bank_activations: after.weight_bank_activations,
            lookahead_prepared: after.lookahead_prepared != 0
                || after.lookahead_first_activation_cycle != 0
                || after.lookahead_first_weight_cycle != 0,
            lookahead_publish_cycle: after.lookahead_publish_cycle,
            lookahead_first_activation_cycle: after.lookahead_first_activation_cycle,
            lookahead_first_weight_cycle: after.lookahead_first_weight_cycle,
            lookahead_weight_preload_cycle: after.lookahead_weight_preload_cycle,
            lookahead_weight_requests: after.lookahead_weight_requests,
            lookahead_weight_reuse_hits: after.lookahead_weight_reuse_hits,
            lookahead_scale_cycle: after.lookahead_scale_cycle,
            lookahead_scale_requests: after.lookahead_scale_requests,
            lookahead_scale_reuses: after.lookahead_scale_reuses,
            current_stripe_completion_cycle: after.current_stripe_completion_cycle,
            lookahead_ready_cycle: after.lookahead_ready_cycle,
            lookahead_start_cycle: after.lookahead_start_cycle,
        }
    }

    pub(crate) fn matrix_timeout(&self, operation: &'static str, cycles: u64) -> Error {
        let debug = self.matrix_debug();
        Error::Timeout {
            operation,
            cycles,
            matmul_scheduler_state: debug.matmul_scheduler_state,
            work_scheduler_state: debug.work_scheduler_state,
            matrix_core_state: debug.matrix_core_state,
            execution_active: debug.execution_active != 0,
            accepted_rows: debug.accepted_rows,
            configured_rows: debug.configured_rows,
            first_column_issued: debug.first_column_issued,
            first_column_committed: debug.first_column_committed,
            engine_result_valid: debug.engine_result_valid != 0,
            vector_busy: debug.vector_busy != 0,
            activation_request_valid: debug.activation_request_valid != 0,
            weight_request_valid: debug.weight_request_valid != 0,
            scale_request_valid: debug.scale_request_valid != 0,
            output_request_valid: debug.output_request_valid != 0,
            stripe_host_waiting: debug.stripe_host_waiting != 0,
        }
    }
}
