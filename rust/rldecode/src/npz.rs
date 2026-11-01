//! Minimal NumPy `.npy` (format 1.0) and `.npz` (deflated zip of `.npy` members) writer,
//! byte-compatible with what `numpy.savez_compressed` produces and what `numpy.load` reads.

use std::io::{self, Write};

use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipWriter};

/// Little-endian element encoders for the dtypes `decode.save` writes.
pub trait NpyElem: Copy {
    const DESCR: &'static str;
    fn put(self, out: &mut Vec<u8>);
}

impl NpyElem for f64 {
    const DESCR: &'static str = "<f8";
    fn put(self, out: &mut Vec<u8>) { out.extend_from_slice(&self.to_le_bytes()); }
}
impl NpyElem for f32 {
    const DESCR: &'static str = "<f4";
    fn put(self, out: &mut Vec<u8>) { out.extend_from_slice(&self.to_le_bytes()); }
}
impl NpyElem for i16 {
    const DESCR: &'static str = "<i2";
    fn put(self, out: &mut Vec<u8>) { out.extend_from_slice(&self.to_le_bytes()); }
}
impl NpyElem for u8 {
    const DESCR: &'static str = "|u1";
    fn put(self, out: &mut Vec<u8>) { out.push(self); }
}

/// IEEE half precision, stored as its bit pattern (`<f2`, what `numpy.float16` reads). Used for
/// the wide rotation and angular-velocity arrays, where 3 significant digits are plenty and
/// the bytes matter: raw replays are not kept, so the decoded file is the long-term copy.
#[derive(Clone, Copy)]
pub struct F16(pub u16);

impl F16 {
    /// Round-to-nearest-even f32 -> f16; NaN stays NaN, overflow becomes infinity.
    pub fn from_f32(x: f32) -> F16 {
        let bits = x.to_bits();
        let sign = ((bits >> 16) & 0x8000) as u16;
        let exp = ((bits >> 23) & 0xff) as i32;
        let mant = bits & 0x007f_ffff;
        if exp == 0xff {
            // inf or nan
            return F16(sign | 0x7c00 | if mant != 0 { 0x0200 } else { 0 });
        }
        let e = exp - 127 + 15;
        if e >= 0x1f {
            return F16(sign | 0x7c00);
        }
        if e <= 0 {
            if e < -10 {
                return F16(sign); // underflows to zero
            }
            let m = (mant | 0x0080_0000) >> (1 - e);
            // round to nearest even on the 13 bits dropped
            let rounded = (m + 0x0fff + ((m >> 13) & 1)) >> 13;
            return F16(sign | rounded as u16);
        }
        let m = mant + 0x0fff + ((mant >> 13) & 1);
        let (e, m) = if m & 0x0080_0000 != 0 { (e + 1, 0) } else { (e, m) };
        if e >= 0x1f {
            return F16(sign | 0x7c00);
        }
        F16(sign | ((e as u16) << 10) | (m >> 13) as u16)
    }
}

impl NpyElem for F16 {
    const DESCR: &'static str = "<f2";
    fn put(self, out: &mut Vec<u8>) { out.extend_from_slice(&self.0.to_le_bytes()); }
}
impl NpyElem for i8 {
    const DESCR: &'static str = "|i1";
    fn put(self, out: &mut Vec<u8>) { out.push(self as u8); }
}
impl NpyElem for bool {
    const DESCR: &'static str = "|b1";
    fn put(self, out: &mut Vec<u8>) { out.push(self as u8); }
}

pub fn npy_bytes<T: NpyElem>(data: &[T], shape: &[usize]) -> Vec<u8> {
    let shape_str = match shape.len() {
        1 => format!("({},)", shape[0]),
        _ => format!("({})", shape.iter().map(|s| s.to_string()).collect::<Vec<_>>().join(", ")),
    };
    let mut header = format!(
        "{{'descr': '{}', 'fortran_order': False, 'shape': {}, }}",
        T::DESCR, shape_str
    );
    // Pad so that magic(6) + version(2) + len(2) + header is a multiple of 64 and ends in '\n'.
    let prefix = 6 + 2 + 2;
    let pad = 64 - ((prefix + header.len() + 1) % 64);
    let pad = if pad == 64 { 0 } else { pad };
    header.extend(std::iter::repeat(' ').take(pad));
    header.push('\n');
    let mut out = Vec::with_capacity(prefix + header.len() + data.len() * 8);
    out.extend_from_slice(b"\x93NUMPY");
    out.push(1);
    out.push(0);
    out.extend_from_slice(&(header.len() as u16).to_le_bytes());
    out.extend_from_slice(header.as_bytes());
    for &x in data {
        x.put(&mut out);
    }
    out
}

/// Deflate level used unless `RLDECODE_ZLEVEL` overrides it. numpy's savez_compressed uses
/// zlib's default (6); the archive content is identical at any level, only size/time change.
pub const DEFAULT_LEVEL: i64 = 6;

pub struct NpzWriter<W: Write + io::Seek> {
    zip: ZipWriter<W>,
    level: i64,
}

impl<W: Write + io::Seek> NpzWriter<W> {
    pub fn new(inner: W, level: Option<i64>) -> Self {
        NpzWriter { zip: ZipWriter::new(inner), level: level.unwrap_or(DEFAULT_LEVEL) }
    }

    pub fn add<T: NpyElem>(&mut self, name: &str, data: &[T], shape: &[usize]) -> io::Result<()> {
        let opts = SimpleFileOptions::default()
            .compression_method(CompressionMethod::Deflated)
            .compression_level(Some(self.level));
        self.zip.start_file(format!("{name}.npy"), opts)?;
        self.zip.write_all(&npy_bytes(data, shape))?;
        Ok(())
    }

    pub fn finish(self) -> io::Result<W> {
        Ok(self.zip.finish()?)
    }
}
