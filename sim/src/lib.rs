mod c_api;
mod ffi;
mod matrix;
mod simulator;
mod stats;
mod stripe;

pub use matrix::{MatmulLayout, MatmulWork, MatrixView, MatrixViewMut};
pub use simulator::{
    Error as SimError, Im2pSimulator, KBlockScaleMatrixView, StripedMatmul, TileRequest, VectorOp,
};
pub use stats::{ScaleFetchStats, TileStats, WorkStats};
pub use stripe::{ActivationStripe, StripeCompletion, StripeLayout, StripeWorkDesc};
