use crate::ffi;

use super::super::{Error, Im2pSimulator};

impl Im2pSimulator {
    pub fn write_accumulator_row(&mut self, row: usize, values: &[i64]) -> Result<(), Error> {
        self.require_i64_row("accumulator", values)?;
        if row > 255 {
            return Err(Error::InvalidAccumulatorRow {
                maximum: 255,
                actual: row,
            });
        }
        // SAFETY: values contains exactly DIM readable i64 elements for this call.
        let ready = unsafe {
            ffi::im2p_write_accumulator_row_i64(self.handle.as_ptr(), row as u32, values.as_ptr())
        };
        self.require_ready("write_accumulator_row", ready)
    }

    pub(in crate::simulator) fn read_accumulator_row(
        &mut self,
        row: usize,
    ) -> Result<Vec<i64>, Error> {
        if row > 255 {
            return Err(Error::InvalidAccumulatorRow {
                maximum: 255,
                actual: row,
            });
        }
        let mut values = vec![0_i64; self.dim];
        // SAFETY: values exposes exactly DIM writable i64 elements for this call.
        let ready = unsafe {
            ffi::im2p_read_accumulator_row_i64(
                self.handle.as_ptr(),
                row as u32,
                values.as_mut_ptr(),
            )
        };
        if ready == 0 {
            return Err(Error::RtlNotReady {
                operation: "read_accumulator_row",
            });
        }
        Ok(values)
    }

    fn require_i64_row(&self, name: &'static str, values: &[i64]) -> Result<(), Error> {
        if values.len() != self.dim {
            return Err(Error::InvalidBufferLength {
                name,
                expected: self.dim,
                actual: values.len(),
            });
        }
        Ok(())
    }
}
