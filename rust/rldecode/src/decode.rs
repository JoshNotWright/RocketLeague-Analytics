//! Port of `rlreplay/stream.py` (`iter_snapshots`) and `rlreplay/decode.py` (`decode_json`).
//!
//! Every rule here mirrors a line in the Python walker; comments cite the Python names.
//! Numeric parity note: rrrocket serialises every f32 with its shortest round-trip decimal
//! and Python parses that decimal as f64, so a Python float64 is *not* `x as f64`. Where
//! the Python side keeps a float64 (times, delta, round_start, the ball-hit comparison,
//! header floats) we reproduce that value with [`f32_json`]. Where it stores float32
//! (ball, cars) the f32 round-trips exactly and we store the raw f32.


use boxcars::{ActorId, Attribute, HeaderProp, RemoteId, Replay, RigidBody};
use serde_json::{json, Map, Value};

/// The float64 Python obtains from rrrocket's JSON for this f32 (shortest repr, then parse).
pub fn f32_json(x: f32) -> f64 {
    if !x.is_finite() {
        return x as f64;
    }
    // ryu is what serde_json (hence rrrocket) uses; it matters for ties such as 150.578125,
    // where ryu prints 150.57812 and Rust's `Display` prints 150.57813.
    let mut buf = ryu::Buffer::new();
    buf.format_finite(x).parse::<f64>().unwrap_or(x as f64)
}


/// Per-actor state indexed by actor id (ids are small non-negative channel numbers), which is
/// much cheaper than a hash map in the hot loop. Negative ids (unset `ActiveActor` links) are
/// simply absent.
struct ActorTable<V> {
    items: Vec<Option<V>>,
}

impl<V> ActorTable<V> {
    fn new() -> Self {
        ActorTable { items: Vec::new() }
    }
    fn get(&self, id: i32) -> Option<&V> {
        if id < 0 { return None; }
        self.items.get(id as usize).and_then(|v| v.as_ref())
    }
    fn get_mut(&mut self, id: i32) -> Option<&mut V> {
        if id < 0 { return None; }
        self.items.get_mut(id as usize).and_then(|v| v.as_mut())
    }
    fn contains(&self, id: i32) -> bool {
        self.get(id).is_some()
    }
    fn insert(&mut self, id: i32, v: V) {
        if id < 0 { return; }
        let idx = id as usize;
        if idx >= self.items.len() {
            self.items.resize_with(idx + 1, || None);
        }
        self.items[idx] = Some(v);
    }
    fn remove(&mut self, id: i32) -> Option<V> {
        if id < 0 { return None; }
        self.items.get_mut(id as usize).and_then(|v| v.take())
    }
}

const TOUCH_DV: f64 = 500.0;

#[derive(Clone, Debug)]
struct Player {
    name: Option<String>,
    platform: Option<String>,
    uid: Option<String>,
    team_actor: Option<i32>,
    team: Option<i32>,
}

#[derive(Clone, Copy)]
struct Rb {
    loc: [f32; 3],
    vel: [f32; 3],
    /// Python-side float64 velocity, only needed for the ball-hit comparison.
    vel64: [f64; 3],
}

fn rb_from(rb: &RigidBody) -> Rb {
    let loc = [rb.location.x, rb.location.y, rb.location.z];
    let vel = match rb.linear_velocity {
        Some(v) => [v.x, v.y, v.z],
        None => [0.0, 0.0, 0.0],
    };
    let vel64 = match rb.linear_velocity {
        Some(v) => [f32_json(v.x), f32_json(v.y), f32_json(v.z)],
        None => [0.0, 0.0, 0.0],
    };
    Rb { loc, vel, vel64 }
}

/// An insertion-ordered map with Python `dict` semantics (update in place keeps position,
/// pop then re-insert moves to the end). Sizes are tiny (cars on the pitch), so a Vec is fine.
#[derive(Default)]
struct OrderedMap<V> {
    items: Vec<(i32, V)>,
}

impl<V> OrderedMap<V> {
    fn get(&self, k: i32) -> Option<&V> {
        self.items.iter().find(|(key, _)| *key == k).map(|(_, v)| v)
    }
    fn contains(&self, k: i32) -> bool {
        self.items.iter().any(|(key, _)| *key == k)
    }
    fn insert(&mut self, k: i32, v: V) {
        if let Some(slot) = self.items.iter_mut().find(|(key, _)| *key == k) {
            slot.1 = v;
        } else {
            self.items.push((k, v));
        }
    }
    fn pop(&mut self, k: i32) -> Option<V> {
        let pos = self.items.iter().position(|(key, _)| *key == k)?;
        Some(self.items.remove(pos).1)
    }
}

pub struct PlayerRow {
    pub slot: usize,
    pub pri: i32,
    pub player_id: Option<String>,
    pub name: Option<String>,
    pub platform: Option<String>,
    pub team: Option<i32>,
}

pub struct Decoded {
    pub t: usize,
    pub p: usize,
    pub times: Vec<f64>,
    pub delta: Vec<f64>,
    pub active: Vec<bool>,
    pub round_start: Vec<f64>,
    pub seconds_remaining: Vec<i16>,
    pub ball: Vec<f32>,     // T x 6
    pub ball_hit: Vec<bool>,
    pub cars: Vec<f32>,     // T x P x 7
    pub meta: Value,
}

/// `events.PLATFORMS` and `decode._player_id`.
fn player_id(p: &Player) -> Option<String> {
    let uid = p.uid.as_ref()?;
    let platform = match p.platform.as_deref() {
        Some("Steam") => "steam".to_string(),
        Some("Epic") => "epic".to_string(),
        Some("PlayStation") => "ps4".to_string(),
        Some("PsyNet") => "psynet".to_string(),
        Some("Xbox") => "xbox".to_string(),
        Some("Switch") => "switch".to_string(),
        Some(other) => other.to_lowercase(),
        None => "unknown".to_string(),
    };
    Some(format!("{platform}:{uid}"))
}

/// `stream._unique_id`: rrrocket serialises `remote_id` as `{Platform: value}`. Integer ids
/// (Steam, Xbox, QQ) and the struct ids (PlayStation, PsyNet, Switch) all come out as decimal
/// strings; the struct ids are read through their truthy `online_id` string; Epic is the name.
fn unique_id(remote: &RemoteId) -> (String, String) {
    match remote {
        RemoteId::PlayStation(id) => ("PlayStation".into(), id.online_id.to_string()),
        RemoteId::PsyNet(id) => ("PsyNet".into(), id.online_id.to_string()),
        RemoteId::SplitScreen(v) => ("SplitScreen".into(), v.to_string()),
        RemoteId::Steam(v) => ("Steam".into(), v.to_string()),
        RemoteId::Switch(id) => ("Switch".into(), id.online_id.to_string()),
        RemoteId::Xbox(v) => ("Xbox".into(), v.to_string()),
        RemoteId::QQ(v) => ("QQ".into(), v.to_string()),
        RemoteId::Epic(s) => ("Epic".into(), s.clone()),
        #[allow(unreachable_patterns)]
        other => ("unknown".into(), format!("{other:?}")),
    }
}

fn prop<'a>(props: &'a [(String, HeaderProp)], key: &str) -> Option<&'a HeaderProp> {
    props.iter().find(|(k, _)| k == key).map(|(_, v)| v)
}

/// JSON view of a header property as rrrocket + `header_summary` would expose it.
fn prop_json(p: Option<&HeaderProp>) -> Value {
    match p {
        None => Value::Null,
        Some(HeaderProp::Int(i)) => json!(i),
        Some(HeaderProp::Float(f)) => json!(f32_json(*f)),
        Some(HeaderProp::Str(s)) | Some(HeaderProp::Name(s)) => json!(s),
        Some(HeaderProp::Bool(b)) => json!(b),
        Some(HeaderProp::QWord(q)) => json!(q.to_string()),
        Some(HeaderProp::Byte { value, .. }) => json!({"value": value}),
        Some(HeaderProp::Array(_)) | Some(HeaderProp::Struct { .. }) => Value::Null,
    }
}

struct HeaderGoal {
    frame: Option<i64>,
    player_name: Value,
    team: Value,
}

pub fn decode(replay: &Replay, replay_id: &str) -> Decoded {
    let props = &replay.properties;
    let objects = &replay.objects;
    let names = &replay.names;
    let frames: &[boxcars::Frame] = replay
        .network_frames
        .as_ref()
        .map(|n| n.frames.as_slice())
        .unwrap_or(&[]);
    let t_len = frames.len();

    let mut times = vec![0.0f64; t_len];
    let mut delta = vec![0.0f64; t_len];
    let mut active = vec![false; t_len];
    let mut ball_hit_arr = vec![false; t_len];
    let mut round_start_arr = vec![f64::NAN; t_len];
    let mut seconds = vec![-1i16; t_len];
    let mut ball = vec![f32::NAN; t_len * 6];

    // slots / players / per_frame_cars / demos from decode.decode_json
    let mut slots: ActorTable<usize> = ActorTable::new();
    let mut n_slots = 0usize;
    let mut player_rows: Vec<PlayerRow> = Vec::new();
    let mut per_frame: Vec<Vec<(usize, [f32; 3], [f32; 3], Option<f64>)>> = Vec::with_capacity(t_len);
    let mut demos_out: Vec<Value> = Vec::new();

    // iter_snapshots state
    let mut actor_seen: ActorTable<()> = ActorTable::new(); // never shrinks, like the Python dict
    let mut players: ActorTable<Player> = ActorTable::new();
    let mut car_pri: OrderedMap<i32> = OrderedMap::default();
    let mut car_rb: ActorTable<Rb> = ActorTable::new();
    let mut boost_car: ActorTable<i32> = ActorTable::new();
    let mut car_boost: ActorTable<f64> = ActorTable::new();
    let mut team_index: ActorTable<i32> = ActorTable::new();
    let mut ball_actors: ActorTable<()> = ActorTable::new();
    let mut ball_state: Option<Rb> = None;
    let mut state_is_active = false;
    let mut round_start: Option<f64> = None;
    let mut seconds_remaining: Option<i32> = None;
    // resent[aid] == frame generation marks a keyframe re-send of an existing actor
    let mut resent: ActorTable<usize> = ActorTable::new();
    // snapshot cars: pri -> (loc, vel, boost), insertion ordered like `cars = {}`
    let mut snap_cars: OrderedMap<([f32; 3], [f32; 3], Option<f64>)> = OrderedMap::default();
    let mut frame_demos: Vec<(Option<i32>, Option<i32>)> = Vec::new();

    for (index, frame) in frames.iter().enumerate() {
        let t = f32_json(frame.time);
        let dt = {
            let d = f32_json(frame.delta);
            if d == 0.0 { 0.0 } else { d } // `frame["delta"] or 0.0`
        };
        let elapsed_active = state_is_active;

        let gen = index + 1;
        let is_resent = |resent: &ActorTable<usize>, aid: i32| resent.get(aid) == Some(&gen);
        for new in &frame.new_actors {
            if actor_seen.contains(new.actor_id.0) {
                resent.insert(new.actor_id.0, gen);
            }
        }
        for new in &frame.new_actors {
            let aid = new.actor_id.0;
            let obj_idx = new.object_id.0 as usize;
            actor_seen.insert(aid, ());
            let obj = objects.get(obj_idx).map(String::as_str).unwrap_or("");
            if obj == "TAGame.Default__PRI_TA" {
                if !players.contains(aid) {
                    players.insert(aid, Player {
                        name: None, platform: None, uid: None, team_actor: None, team: None,
                    });
                }
            } else if obj.starts_with("Archetypes.Ball.") {
                ball_actors.insert(aid, ());
            } else if obj.ends_with("Teams.Team0") {
                team_index.insert(aid, 0);
            } else if obj.ends_with("Teams.Team1") {
                team_index.insert(aid, 1);
            }
        }

        let mut ball_hit = false;
        frame_demos.clear();
        for upd in &frame.updated_actors {
            let aid = upd.actor_id.0;
            let key = objects.get(upd.object_id.0 as usize).map(String::as_str).unwrap_or("");
            match key {
                "TAGame.RBActor_TA:ReplicatedRBState" => {
                    if let Attribute::RigidBody(rb) = &upd.attribute {
                        let new_rb = rb_from(rb);
                        if ball_actors.contains(aid) {
                            if let Some(prev) = &ball_state {
                                let mut acc = 0.0f64;
                                for k in 0..3 {
                                    let d = prev.vel64[k] - new_rb.vel64[k];
                                    acc += d * d;
                                }
                                if acc > TOUCH_DV * TOUCH_DV {
                                    ball_hit = true;
                                }
                            }
                            ball_state = Some(new_rb);
                        } else {
                            car_rb.insert(aid, new_rb);
                        }
                    }
                }
                "TAGame.CarComponent_Boost_TA:ReplicatedBoostAmount"
                | "TAGame.CarComponent_Boost_TA:ReplicatedBoost" => {
                    if let Some(&car) = boost_car.get(aid) {
                        let raw: Option<u8> = match &upd.attribute {
                            Attribute::Byte(b) => Some(*b),
                            Attribute::ReplicatedBoost(rb) => Some(rb.boost_amount),
                            _ => None,
                        };
                        if let Some(raw) = raw {
                            car_boost.insert(car, raw as f64 / 2.55);
                        }
                    }
                }
                "TAGame.CarComponent_TA:Vehicle" => {
                    if let Attribute::ActiveActor(link) = &upd.attribute {
                        boost_car.insert(aid, link.actor.0);
                    }
                }
                "Engine.Pawn:PlayerReplicationInfo" => {
                    if let Attribute::ActiveActor(link) = &upd.attribute {
                        let pri = link.actor.0;
                        if link.active && pri >= 0 {
                            car_pri.insert(aid, pri);
                        } else if car_pri.contains(aid) && !is_resent(&resent, aid) {
                            let victim = car_pri.pop(aid).unwrap();
                            if elapsed_active {
                                frame_demos.push((None, Some(victim)));
                            }
                        }
                    }
                }
                "Engine.PlayerReplicationInfo:PlayerName" => {
                    if let (Some(p), Attribute::String(s)) = (players.get_mut(aid), &upd.attribute) {
                        p.name = Some(s.clone());
                    }
                }
                "Engine.PlayerReplicationInfo:UniqueId" => {
                    if let (Some(p), Attribute::UniqueId(u)) = (players.get_mut(aid), &upd.attribute) {
                        let (platform, uid) = unique_id(&u.remote_id);
                        p.platform = Some(platform);
                        p.uid = Some(uid);
                    }
                }
                "Engine.PlayerReplicationInfo:Team" => {
                    if let (Some(p), Attribute::ActiveActor(link)) = (players.get_mut(aid), &upd.attribute) {
                        p.team_actor = Some(link.actor.0);
                    }
                }
                "TAGame.GameEvent_TA:ReplicatedStateName" => {
                    // names[idx] if idx is an int below len(names) (Python negative indexing
                    // included), otherwise str(attr), which can never equal "Active".
                    let new_active = match &upd.attribute {
                        Attribute::Int(i) => {
                            let n = names.len() as i64;
                            let i = *i as i64;
                            let idx = if i < 0 { i + n } else { i };
                            idx >= 0 && idx < n && names[idx as usize] == "Active"
                        }
                        _ => false,
                    };
                    if new_active && !state_is_active {
                        round_start = Some(t);
                    }
                    state_is_active = new_active;
                }
                "TAGame.GameEvent_Soccar_TA:SecondsRemaining" => {
                    seconds_remaining = match &upd.attribute {
                        Attribute::Int(i) => Some(*i),
                        _ => None,
                    };
                }
                "TAGame.Car_TA:ReplicatedDemolishGoalExplosion"
                | "TAGame.Car_TA:ReplicatedDemolish"
                | "TAGame.Car_TA:ReplicatedDemolish_CustomFX"
                | "TAGame.Car_TA:ReplicatedDemolishExtended" => {
                    if is_resent(&resent, aid) {
                        continue;
                    }
                    let attacker: Option<i32> = match &upd.attribute {
                        Attribute::DemolishExtended(ext) => {
                            if ext.attacker_pri.active && ext.attacker_pri.actor.0 >= 0 {
                                Some(ext.attacker_pri.actor.0)
                            } else if ext.attacker.active && ext.attacker.actor.0 >= 0 {
                                car_pri.get(ext.attacker.actor.0).copied()
                            } else {
                                None
                            }
                        }
                        Attribute::DemolishFx(fx) => attacker_from_car(&car_pri, fx.attacker),
                        Attribute::Demolish(d) => attacker_from_car(&car_pri, d.attacker),
                        _ => None,
                    };
                    if let Some(a) = attacker {
                        if players.contains(a) {
                            frame_demos.push((Some(a), None));
                        }
                    }
                }
                _ => {}
            }
        }

        for deleted in &frame.deleted_actors {
            let d = deleted.0;
            car_rb.remove(d);
            car_boost.remove(d);
            car_pri.pop(d);
            if ball_actors.remove(d).is_some() {
                ball_state = None;
            }
        }

        snap_cars.items.clear();
        for (car, pri) in &car_pri.items {
            let Some(player) = players.get_mut(*pri) else { continue };
            let Some(rb) = car_rb.get(*car) else { continue };
            if player.team.is_none() {
                if let Some(ta) = player.team_actor {
                    if let Some(&ti) = team_index.get(ta) {
                        player.team = Some(ti);
                    }
                }
            }
            snap_cars.insert(*pri, (rb.loc, rb.vel, car_boost.get(*car).copied()));
        }

        // ---- decode_json body for this snapshot ----
        times[index] = t;
        delta[index] = dt;
        active[index] = elapsed_active;
        if let Some(rs) = round_start {
            round_start_arr[index] = rs;
        }
        if let Some(s) = seconds_remaining {
            seconds[index] = s as i16;
        }
        if let Some(b) = &ball_state {
            let base = index * 6;
            ball[base..base + 3].copy_from_slice(&b.loc);
            ball[base + 3..base + 6].copy_from_slice(&b.vel);
        }
        ball_hit_arr[index] = ball_hit;
        let mut entries = Vec::with_capacity(snap_cars.items.len());
        for (pri, (loc, vel, boost)) in &snap_cars.items {
            let slot = match slots.get(*pri) {
                Some(&s) => s,
                None => {
                    let s = n_slots;
                    n_slots += 1;
                    slots.insert(*pri, s);
                    let player = players.get(*pri).expect("snapshot cars only hold known PRIs");
                    player_rows.push(PlayerRow {
                        slot: s,
                        pri: *pri,
                        player_id: player_id(player),
                        name: player.name.clone(),
                        platform: player.platform.clone(),
                        team: player.team,
                    });
                    s
                }
            };
            entries.push((slot, *loc, *vel, *boost));
        }
        per_frame.push(entries);
        for (attacker, victim) in &frame_demos {
            let slot_of = |p: &Option<i32>| -> Value {
                match p.and_then(|p| slots.get(p)) {
                    Some(&s) => json!(s),
                    None => Value::Null,
                }
            };
            demos_out.push(json!({
                "time": t,
                "attacker_slot": slot_of(attacker),
                "victim_slot": slot_of(victim),
            }));
        }
    }

    let p_len = n_slots;
    let mut cars = vec![f32::NAN; t_len * p_len * 7];
    for (i, entries) in per_frame.iter().enumerate() {
        for (slot, loc, vel, boost) in entries {
            let base = (i * p_len + slot) * 7;
            cars[base..base + 3].copy_from_slice(loc);
            cars[base + 3..base + 6].copy_from_slice(vel);
            cars[base + 6] = match boost {
                Some(b) => *b as f32,
                None => f32::NAN,
            };
        }
    }

    // ---- header_summary (without PlayerStats, which decode.py drops) ----
    let goals_hdr: Vec<HeaderGoal> = prop(props, "Goals")
        .and_then(HeaderProp::as_array)
        .map(|arr| {
            arr.iter()
                .map(|g| HeaderGoal {
                    frame: prop(g, "frame").and_then(HeaderProp::as_i32).map(i64::from),
                    player_name: prop_json(prop(g, "PlayerName")),
                    team: prop_json(prop(g, "PlayerTeam")),
                })
                .collect()
        })
        .unwrap_or_default();
    let mut goals_out: Vec<Value> = Vec::new();
    for g in &goals_hdr {
        let Some(f) = g.frame else { continue };
        if f < t_len as i64 {
            let idx = if f < 0 { f + t_len as i64 } else { f };
            if idx < 0 || idx >= t_len as i64 {
                continue; // Python would raise IndexError here; cannot happen for real headers
            }
            goals_out.push(json!({
                "time": times[idx as usize],
                "team": g.team,
                "scorer": g.player_name,
            }));
        }
    }

    let score = |k: &str| -> Value {
        // `props.get("Team0Score") or 0`
        match prop(props, k) {
            Some(HeaderProp::Int(i)) if *i != 0 => json!(i),
            Some(HeaderProp::Int(_)) | None => json!(0),
            other => {
                let v = prop_json(other);
                if v.is_null() { json!(0) } else { v }
            }
        }
    };
    let mut header = Map::new();
    header.insert("replay_id_header".into(), prop_json(prop(props, "Id")));
    header.insert("match_guid".into(), prop_json(prop(props, "MatchGuid")));
    header.insert("team_size".into(), prop_json(prop(props, "TeamSize")));
    header.insert("team0_score".into(), score("Team0Score"));
    header.insert("team1_score".into(), score("Team1Score"));
    header.insert("num_frames".into(), prop_json(prop(props, "NumFrames")));
    header.insert("record_fps".into(), prop_json(prop(props, "RecordFPS")));
    header.insert("map_name".into(), prop_json(prop(props, "MapName")));
    header.insert("date".into(), prop_json(prop(props, "Date")));
    header.insert("build_version".into(), prop_json(prop(props, "BuildVersion")));
    header.insert(
        "replay_version".into(),
        json!([replay.major_version, replay.minor_version, replay.net_version]),
    );

    let players_json: Vec<Value> = player_rows
        .iter()
        .map(|r| {
            json!({
                "slot": r.slot, "pri": r.pri, "player_id": r.player_id, "name": r.name,
                "platform": r.platform, "team": r.team,
            })
        })
        .collect();
    let meta = json!({
        "replay_id": replay_id,
        "players": players_json,
        "goals": goals_out,
        "demos": demos_out,
        "header": Value::Object(header),
    });

    Decoded {
        t: t_len,
        p: p_len,
        times,
        delta,
        active,
        round_start: round_start_arr,
        seconds_remaining: seconds,
        ball,
        ball_hit: ball_hit_arr,
        cars,
        meta,
    }
}

fn attacker_from_car(car_pri: &OrderedMap<i32>, attacker_car: ActorId) -> Option<i32> {
    if attacker_car.0 >= 0 {
        car_pri.get(attacker_car.0).copied()
    } else {
        None
    }
}
