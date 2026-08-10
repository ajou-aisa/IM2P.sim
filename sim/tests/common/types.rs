#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Shape {
    pub m: usize,
    pub n: usize,
    pub k: usize,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct KFragment {
    pub start: usize,
    pub count: usize,
    pub block: usize,
}

pub fn k_fragments(total_k: usize, block_size: usize, dim: usize) -> Vec<KFragment> {
    assert!(total_k > 0);
    assert!(block_size > 0);
    assert!(dim > 0);

    let mut fragments = Vec::new();
    let mut start = 0;
    while start < total_k {
        let block = start / block_size;
        let block_end = (block + 1).saturating_mul(block_size);
        let count = dim.min(total_k - start).min(block_end - start);
        fragments.push(KFragment {
            start,
            count,
            block,
        });
        start += count;
    }
    fragments
}
