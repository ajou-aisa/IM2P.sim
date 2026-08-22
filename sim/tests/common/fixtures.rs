use im2p_sim::{parse_activation, ActivationValue};

use super::Shape;

pub fn structured_activations(shape: Shape) -> Vec<ActivationValue> {
    (0..shape.m * shape.k)
        .map(|index| {
            let row = index / shape.k;
            let k = index % shape.k;
            let value = ((17 * row + 13 * k + 5) % 15) as i32 - 7;
            parse_activation(value).expect("structured activation is valid for every width")
        })
        .collect()
}

pub fn structured_weights(shape: Shape) -> Vec<i8> {
    (0..shape.k * shape.n)
        .map(|index| {
            let k = index / shape.n;
            let column = index % shape.n;
            ((11 * k + 7 * column + 3) % 13) as i8 - 6
        })
        .collect()
}

pub struct Lcg(u32);

impl Lcg {
    pub const fn new(seed: u32) -> Self {
        Self(seed)
    }

    pub fn signed(&mut self, minimum: i8, maximum: i8) -> i8 {
        self.0 = self.0.wrapping_mul(1_664_525).wrapping_add(1_013_904_223);
        let width = i16::from(maximum) - i16::from(minimum) + 1;
        let offset = (self.0 % u32::try_from(width).expect("positive range")) as i16;
        i8::try_from(i16::from(minimum) + offset).expect("i8 range")
    }
}
