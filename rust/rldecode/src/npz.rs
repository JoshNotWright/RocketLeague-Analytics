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
