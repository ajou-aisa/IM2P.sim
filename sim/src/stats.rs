#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct ScaleFetchStats {
    pub demand_requests: u64,
    pub prefetch_requests: u64,
    pub current_hits: u64,
    pub next_hits: u64,
    pub demand_misses: u64,
    pub rows_received: u64,
    pub scale_transfer_cycles: u64,
    pub scale_wait_cycles: u64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TileStats {
    pub weight_load_cycles: u64,
    pub scale_fetch: ScaleFetchStats,
    pub compute_cycles: u64,
    pub total_cycles: u64,
    pub useful_macs: u64,
    pub useful_ops: u64,
    pub macs_per_cycle: f64,
    pub ops_per_cycle: f64,
    pub utilization: f64,
}

impl TileStats {
    pub fn from_counts(
        weight_load_cycles: u64,
        scale_fetch: ScaleFetchStats,
        compute_cycles: u64,
        total_cycles: u64,
        valid_m: usize,
        valid_n: usize,
        valid_k: usize,
        dim: usize,
    ) -> Self {
        let useful_macs = (valid_m as u64)
            .saturating_mul(valid_n as u64)
            .saturating_mul(valid_k as u64);
        let useful_ops = useful_macs.saturating_mul(2);
        let macs_per_cycle = ratio(useful_macs, compute_cycles);
        let ops_per_cycle = ratio(useful_ops, compute_cycles);
        let utilization = ratio_float(macs_per_cycle, (dim * dim) as f64);
        Self {
            weight_load_cycles,
            scale_fetch,
            compute_cycles,
            total_cycles,
            useful_macs,
            useful_ops,
            macs_per_cycle,
            ops_per_cycle,
            utilization,
        }
    }
}

fn ratio(numerator: u64, denominator: u64) -> f64 {
    ratio_float(numerator as f64, denominator as f64)
}

fn ratio_float(numerator: f64, denominator: f64) -> f64 {
    if denominator == 0.0 {
        0.0
    } else {
        numerator / denominator
    }
}
