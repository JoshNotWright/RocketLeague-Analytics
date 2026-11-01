//! `rldecode <replay> <out_dir> [--replay-id <id>]`
//! `rldecode --batch <out_dir>`  (replay paths on stdin, one per line, optional `\t<id>`)
//!
//! Decodes Rocket League `.replay` files with boxcars and writes `<out_dir>/<id>.npz` plus
//! `<out_dir>/<id>.meta.json` in exactly the layout `rlreplay.decode.save` produces, so
//! `rlreplay.decode.load` reads them unchanged. Batch mode prints one line per replay
//! (`ok\t<id>` or `failed\t<id>\t<message>`) and exits 0 even if some replays failed; it
//! exists because process start-up on Windows costs more than decoding a replay.

mod decode;
mod npz;

use std::fs;
use std::io::{BufRead, BufWriter, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

fn usage() -> ExitCode {
    eprintln!("usage: rldecode <replay> <out_dir> [--replay-id <id>]\n       rldecode --batch <out_dir>  (replay paths on stdin)");
    ExitCode::from(2)
}

fn stem_of(path: &Path) -> String {
    path.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default()
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut positional: Vec<String> = Vec::new();
    let mut replay_id: Option<String> = None;
    let mut batch = false;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--replay-id" => {
                i += 1;
                match args.get(i) {
                    Some(v) => replay_id = Some(v.clone()),
                    None => return usage(),
                }
            }
            "--batch" => batch = true,
            "-h" | "--help" => return usage(),
            other => positional.push(other.to_string()),
        }
        i += 1;
    }

    if batch {
        if positional.len() != 1 {
            return usage();
        }
        let out_dir = PathBuf::from(&positional[0]);
        let stdin = std::io::stdin();
        let stdout = std::io::stdout();
        let mut out = stdout.lock();
        for line in stdin.lock().lines() {
            let Ok(line) = line else { break };
            let line = line.trim_start_matches('\u{feff}').trim_end_matches(['\r', '\n']);
            if line.is_empty() {
                continue;
            }
            let (path, id) = match line.split_once('\t') {
                Some((p, id)) => (PathBuf::from(p), id.to_string()),
                None => (PathBuf::from(line), stem_of(Path::new(line))),
            };
            let status = match run(&path, &out_dir, &id) {
                Ok(()) => format!("ok\t{id}\n"),
                Err(msg) => format!("failed\t{id}\t{}\n", msg.replace(['\n', '\t'], " ")),
            };
            if out.write_all(status.as_bytes()).and_then(|_| out.flush()).is_err() {
                break;
            }
        }
        return ExitCode::SUCCESS;
    }

    if positional.len() != 2 {
        return usage();
    }
    let replay_path = PathBuf::from(&positional[0]);
    let out_dir = PathBuf::from(&positional[1]);
    let replay_id = replay_id.unwrap_or_else(|| stem_of(&replay_path));

    match run(&replay_path, &out_dir, &replay_id) {
        Ok(()) => ExitCode::SUCCESS,
        Err(msg) => {
            eprintln!("rldecode: {}: {}", replay_path.display(), msg);
            ExitCode::FAILURE
        }
    }
}

fn run(replay_path: &Path, out_dir: &Path, replay_id: &str) -> Result<(), String> {
    let timing = std::env::var_os("RLDECODE_TIMING").is_some();
    let t0 = std::time::Instant::now();
    let data = fs::read(replay_path).map_err(|e| format!("cannot read replay: {e}"))?;
    let replay = boxcars::ParserBuilder::new(&data)
        .must_parse_network_data()
        .on_error_check_crc()
        .parse()
        .map_err(|e| format!("parse failed: {e}"))?;
    let t_parse = t0.elapsed();
    let dec = decode::decode(&replay, replay_id);
    let t_decode = t0.elapsed() - t_parse;
    fs::create_dir_all(out_dir).map_err(|e| format!("cannot create {}: {e}", out_dir.display()))?;

    let meta_bytes = serde_json::to_vec(&dec.meta).map_err(|e| format!("meta json: {e}"))?;
    let target = out_dir.join(format!("{replay_id}.npz"));
    let tmp = out_dir.join(format!("{replay_id}.tmp.npz"));
    {
        let file = fs::File::create(&tmp).map_err(|e| format!("cannot write {}: {e}", tmp.display()))?;
        let level = std::env::var("RLDECODE_ZLEVEL").ok().and_then(|v| v.parse::<i64>().ok());
        let mut w = npz::NpzWriter::new(BufWriter::new(file), level);
        let t = dec.t;
        let p = dec.p;
        let io = |e: std::io::Error| format!("npz write: {e}");
        w.add("times", &dec.times, &[t]).map_err(io)?;
        w.add("delta", &dec.delta, &[t]).map_err(io)?;
        w.add("active", &dec.active, &[t]).map_err(io)?;
        w.add("round_start", &dec.round_start, &[t]).map_err(io)?;
        w.add("seconds_remaining", &dec.seconds_remaining, &[t]).map_err(io)?;
        w.add("ball", &dec.ball, &[t, 6]).map_err(io)?;
        w.add("ball_hit", &dec.ball_hit, &[t]).map_err(io)?;
        w.add("cars", &dec.cars, &[t, p, 7]).map_err(io)?;
        w.add("meta", &meta_bytes, &[meta_bytes.len()]).map_err(io)?;
        let mut inner = w.finish().map_err(io)?;
        inner.flush().map_err(io)?;
    }
    fs::rename(&tmp, &target).map_err(|e| format!("cannot move {} into place: {e}", tmp.display()))?;

    let meta_path = out_dir.join(format!("{replay_id}.meta.json"));
    fs::write(&meta_path, &meta_bytes).map_err(|e| format!("cannot write {}: {e}", meta_path.display()))?;
    if timing {
        let total = t0.elapsed();
        eprintln!(
            "rldecode timing {replay_id}: parse {:.1} ms, walk {:.1} ms, write {:.1} ms, total {:.1} ms",
            t_parse.as_secs_f64() * 1e3,
            t_decode.as_secs_f64() * 1e3,
            (total - t_parse - t_decode).as_secs_f64() * 1e3,
            total.as_secs_f64() * 1e3
        );
    }
    Ok(())
}
