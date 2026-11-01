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

/// Camera profile, `TAGame.CameraSettingsActor_TA:ProfileSettings` (linked to a PRI through
/// `TAGame.CameraSettingsActor_TA:PRI`) or `TAGame.PRI_TA:CameraSettings` on the PRI itself.
#[derive(Clone, Copy, Debug)]
struct Camera {
    fov: f32,
    height: f32,
    angle: f32,
    distance: f32,
    stiffness: f32,
    swivel: f32,
    transition: Option<f32>,
}

impl Camera {
    fn from(c: &boxcars::CamSettings) -> Camera {
        Camera {
            fov: c.fov, height: c.height, angle: c.angle, distance: c.distance,
            stiffness: c.stiffness, swivel: c.swivel, transition: c.transition,
        }
    }
    fn json(&self) -> Value {
        json!({
            "fov": f32_json(self.fov), "height": f32_json(self.height), "angle": f32_json(self.angle),
            "distance": f32_json(self.distance), "stiffness": f32_json(self.stiffness),
            "swivel": f32_json(self.swivel), "transition": self.transition.map(f32_json),
        })
    }
}

#[derive(Clone, Debug)]
struct Player {
    name: Option<String>,
    platform: Option<String>,
    uid: Option<String>,
    team_actor: Option<i32>,
    team: Option<i32>,
    // ---- wide-decode extras (meta only, never affect the narrow arrays) ----
    /// Loadout.body for the blue and orange loadouts (ClientLoadouts) or the single
    /// ClientLoadout (stored in both); last seen.
    body: Option<(u32, u32)>,
    camera: Option<Camera>,
    match_score: Option<i32>,
    match_goals: Option<i32>,
    match_assists: Option<i32>,
    match_saves: Option<i32>,
    match_shots: Option<i32>,
}

impl Player {
    fn new() -> Player {
        Player {
            name: None, platform: None, uid: None, team_actor: None, team: None,
            body: None, camera: None, match_score: None, match_goals: None,
            match_assists: None, match_saves: None, match_shots: None,
        }
    }
}

#[derive(Clone, Copy)]
struct Rb {
    loc: [f32; 3],
    vel: [f32; 3],
    /// Python-side float64 velocity, only needed for the ball-hit comparison.
    vel64: [f64; 3],
    /// Quaternion x, y, z, w (`RigidBody.rotation`).
    rot: [f32; 4],
    /// `RigidBody.angular_velocity`, zero when absent exactly like `vel`.
    angvel: [f32; 3],
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
    let rot = [rb.rotation.x, rb.rotation.y, rb.rotation.z, rb.rotation.w];
    let angvel = match rb.angular_velocity {
        Some(v) => [v.x, v.y, v.z],
        None => [0.0, 0.0, 0.0],
    };
    Rb { loc, vel, vel64, rot, angvel }
}

/// Car component kinds carried in `cars_ctrl[..., 4..8]`, in that order. The kind comes from the
/// component's new-actor object name (`Archetypes.CarComponents.CarComponent_<Kind>`).
const COMP_BOOST: usize = 0;
const COMP_JUMP: usize = 1;
const COMP_DOUBLE_JUMP: usize = 2;
const COMP_DODGE: usize = 3;
const N_COMP: usize = 4;

fn component_kind(obj: &str) -> Option<usize> {
    let tail = obj.strip_prefix("Archetypes.CarComponents.CarComponent_")?;
    match tail {
        "Boost" => Some(COMP_BOOST),
        "Jump" => Some(COMP_JUMP),
        "DoubleJump" => Some(COMP_DOUBLE_JUMP),
        "Dodge" => Some(COMP_DODGE),
        _ => None, // FlipCar and anything newer are not carried
    }
}

/// Per-car driver inputs (`TAGame.Vehicle_TA:*`), NaN until first replicated; they persist
/// until the car actor is deleted, like boost.
#[derive(Clone, Copy)]
struct Ctrl {
    throttle: f32,
    steer: f32,
    handbrake: f32,
    driving: f32,
    /// Component actor ids by kind, so `ReplicatedActive` is read at snapshot time.
    comps: [Option<i32>; N_COMP],
}

impl Ctrl {
    fn new() -> Ctrl {
        Ctrl { throttle: f32::NAN, steer: f32::NAN, handbrake: f32::NAN, driving: f32::NAN, comps: [None; N_COMP] }
    }
}

pub const CTRL_WIDTH: usize = 8;


/// Everything a car row carries beyond the narrow `cars` columns.
#[derive(Clone, Copy)]
struct CarExtra {
    rot: [f32; 4],
    angvel: [f32; 3],
    ctrl: [f32; CTRL_WIDTH],
}

/// An insertion-ordered map with Python `dict` semantics (update in place keeps position,
/// pop then re-insert moves to the end). Sizes are tiny (cars on the pitch), so a Vec is fine.
struct OrderedMap<V> {
    items: Vec<(i32, V)>,
}

impl<V> Default for OrderedMap<V> {
    fn default() -> Self {
        OrderedMap { items: Vec::new() }
    }
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
    // ---- wide layout, written after the narrow arrays ----
    #[allow(dead_code)]
    pub ball_rot: Vec<f32>,    // T x 4 (decoded, not written)
    pub ball_angvel: Vec<f32>, // T x 3
    pub cars_rot: Vec<f32>,    // T x P x 4
    pub cars_angvel: Vec<f32>, // T x P x 3
    pub cars_ctrl: Vec<f32>,   // T x P x 8
    pub overtime: Vec<bool>,   // T
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
    let mut ball_rot_arr = vec![f32::NAN; t_len * 4];
    let mut ball_angvel_arr = vec![f32::NAN; t_len * 3];
    let mut overtime_arr = vec![false; t_len];

    // slots / players / per_frame_cars / demos from decode.decode_json
    let mut slots: ActorTable<usize> = ActorTable::new();
    let mut n_slots = 0usize;
    let mut player_rows: Vec<PlayerRow> = Vec::new();
    let mut per_frame: Vec<Vec<(usize, [f32; 3], [f32; 3], Option<f64>, CarExtra)>> = Vec::with_capacity(t_len);
    let mut demos_out: Vec<Value> = Vec::new();
    let mut pickups_out: Vec<Value> = Vec::new();

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
    let mut snap_cars: OrderedMap<([f32; 3], [f32; 3], Option<f64>, CarExtra)> = OrderedMap::default();
    let mut frame_demos: Vec<(Option<i32>, Option<i32>)> = Vec::new();

    // ---- wide-decode state (reads only; never feeds back into the narrow arrays) ----
    let mut overtime = false;
    // component actor -> kind (from its new-actor object name)
    let mut comp_kind: ActorTable<usize> = ActorTable::new();
    // component actor -> last ReplicatedActive byte
    let mut comp_active: ActorTable<u8> = ActorTable::new();
    // car actor -> driver inputs and linked component actors
    let mut car_ctrl: ActorTable<Ctrl> = ActorTable::new();
    // camera-settings actor -> (linked PRI, last ProfileSettings)
    let mut cam_pri: ActorTable<i32> = ActorTable::new();
    let mut cam_settings: ActorTable<Camera> = ActorTable::new();
    // pickup (boost pad) actor -> object name tail such as `VehiclePickup_Boost_TA_27`
    let mut pad_name: ActorTable<String> = ActorTable::new();
    // pickups seen this frame: (pad actor, instigator car actor)
    let mut frame_pickups: Vec<(i32, i32)> = Vec::new();

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
                    players.insert(aid, Player::new());
                }
            } else if obj.starts_with("Archetypes.Ball.") {
                ball_actors.insert(aid, ());
            } else if obj.ends_with("Teams.Team0") {
                team_index.insert(aid, 0);
            } else if obj.ends_with("Teams.Team1") {
                team_index.insert(aid, 1);
            } else if let Some(kind) = component_kind(obj) {
                comp_kind.insert(aid, kind);
                if !is_resent(&resent, aid) {
                    comp_active.remove(aid);
                }
            } else if obj.contains(".VehiclePickup_") {
                let tail = obj.rsplit('.').next().unwrap_or(obj);
                pad_name.insert(aid, tail.to_string());
            }
        }

        let mut ball_hit = false;
        frame_demos.clear();
        frame_pickups.clear();
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
                        // Wide: remember the component on its car, by kind.
                        if let Some(&kind) = comp_kind.get(aid) {
                            let car = link.actor.0;
                            if car >= 0 {
                                if !car_ctrl.contains(car) {
                                    car_ctrl.insert(car, Ctrl::new());
                                }
                                car_ctrl.get_mut(car).unwrap().comps[kind] = Some(aid);
                            }
                        }
                    }
                }
                // ---- wide-decode attributes (no effect on the narrow arrays) ----
                "TAGame.CarComponent_TA:ReplicatedActive" => {
                    if let Attribute::Byte(b) = &upd.attribute {
                        comp_active.insert(aid, *b);
                    }
                }
                "TAGame.Vehicle_TA:ReplicatedThrottle" => {
                    if let Attribute::Byte(b) = &upd.attribute {
                        car_ctrl_mut(&mut car_ctrl, aid).throttle = *b as f32;
                    }
                }
                "TAGame.Vehicle_TA:ReplicatedSteer" => {
                    if let Attribute::Byte(b) = &upd.attribute {
                        car_ctrl_mut(&mut car_ctrl, aid).steer = *b as f32;
                    }
                }
                "TAGame.Vehicle_TA:bReplicatedHandbrake" => {
                    if let Attribute::Boolean(b) = &upd.attribute {
                        car_ctrl_mut(&mut car_ctrl, aid).handbrake = *b as u8 as f32;
                    }
                }
                "TAGame.Vehicle_TA:bDriving" => {
                    if let Attribute::Boolean(b) = &upd.attribute {
                        car_ctrl_mut(&mut car_ctrl, aid).driving = *b as u8 as f32;
                    }
                }
                "TAGame.GameEvent_Soccar_TA:bOverTime" => {
                    if let Attribute::Boolean(b) = &upd.attribute {
                        overtime = *b;
                    }
                }
                "TAGame.VehiclePickup_TA:ReplicatedPickupData" => {
                    if let Attribute::Pickup(p) = &upd.attribute {
                        if p.picked_up {
                            if let Some(car) = p.instigator {
                                frame_pickups.push((aid, car.0));
                            }
                        }
                    }
                }
                "TAGame.VehiclePickup_TA:NewReplicatedPickupData" => {
                    // picked_up is an odd counter per pickup; 255 with no instigator is the
                    // pad respawning, which is not a pickup.
                    if let Attribute::PickupNew(p) = &upd.attribute {
                        if p.picked_up != 0 {
                            if let Some(car) = p.instigator {
                                frame_pickups.push((aid, car.0));
                            }
                        }
                    }
                }
                "TAGame.PRI_TA:ClientLoadouts" => {
                    if let (Some(p), Attribute::TeamLoadout(l)) = (players.get_mut(aid), &upd.attribute) {
                        p.body = Some((l.blue.body, l.orange.body));
                    }
                }
                "TAGame.PRI_TA:ClientLoadout" => {
                    if let (Some(p), Attribute::Loadout(l)) = (players.get_mut(aid), &upd.attribute) {
                        p.body = Some((l.body, l.body));
                    }
                }
                "TAGame.PRI_TA:CameraSettings" => {
                    if let (Some(p), Attribute::CamSettings(c)) = (players.get_mut(aid), &upd.attribute) {
                        p.camera = Some(Camera::from(c));
                    }
                }
                "TAGame.CameraSettingsActor_TA:ProfileSettings" => {
                    if let Attribute::CamSettings(c) = &upd.attribute {
                        let cam = Camera::from(c);
                        cam_settings.insert(aid, cam);
                        if let Some(p) = cam_pri.get(aid).copied().and_then(|pri| players.get_mut(pri)) {
                            p.camera = Some(cam);
                        }
                    }
                }
                "TAGame.CameraSettingsActor_TA:PRI" => {
                    if let Attribute::ActiveActor(link) = &upd.attribute {
                        cam_pri.insert(aid, link.actor.0);
                        if let (Some(cam), Some(p)) = (cam_settings.get(aid).copied(), players.get_mut(link.actor.0)) {
                            p.camera = Some(cam);
                        }
                    }
                }
                "TAGame.PRI_TA:MatchScore" => {
                    if let (Some(p), Attribute::Int(i)) = (players.get_mut(aid), &upd.attribute) {
                        p.match_score = Some(*i);
                    }
                }
                "TAGame.PRI_TA:MatchGoals" => {
                    if let (Some(p), Attribute::Int(i)) = (players.get_mut(aid), &upd.attribute) {
                        p.match_goals = Some(*i);
                    }
                }
                "TAGame.PRI_TA:MatchAssists" => {
                    if let (Some(p), Attribute::Int(i)) = (players.get_mut(aid), &upd.attribute) {
                        p.match_assists = Some(*i);
                    }
                }
                "TAGame.PRI_TA:MatchSaves" => {
                    if let (Some(p), Attribute::Int(i)) = (players.get_mut(aid), &upd.attribute) {
                        p.match_saves = Some(*i);
                    }
                }
                "TAGame.PRI_TA:MatchShots" => {
                    if let (Some(p), Attribute::Int(i)) = (players.get_mut(aid), &upd.attribute) {
                        p.match_shots = Some(*i);
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
            car_ctrl.remove(d);
            comp_active.remove(d);
            comp_kind.remove(d);
            cam_pri.remove(d);
            cam_settings.remove(d);
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
            let mut ctrl = [f32::NAN; CTRL_WIDTH];
            if let Some(c) = car_ctrl.get(*car) {
                ctrl[0] = c.throttle;
                ctrl[1] = c.steer;
                ctrl[2] = c.handbrake;
                ctrl[3] = c.driving;
                for (kind, comp) in c.comps.iter().enumerate() {
                    if let Some(&v) = comp.and_then(|id| comp_active.get(id)) {
                        ctrl[4 + kind] = (v & 1) as f32; // odd ReplicatedActive == active
                    }
                }
            }
            let extra = CarExtra { rot: rb.rot, angvel: rb.angvel, ctrl };
            snap_cars.insert(*pri, (rb.loc, rb.vel, car_boost.get(*car).copied(), extra));
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
            ball_rot_arr[index * 4..index * 4 + 4].copy_from_slice(&b.rot);
            ball_angvel_arr[index * 3..index * 3 + 3].copy_from_slice(&b.angvel);
        }
        ball_hit_arr[index] = ball_hit;
        overtime_arr[index] = overtime;
        let mut entries = Vec::with_capacity(snap_cars.items.len());
        for (pri, (loc, vel, boost, extra)) in &snap_cars.items {
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
            entries.push((slot, *loc, *vel, *boost, *extra));
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
        for (pad, car) in &frame_pickups {
            // Pad actors are level-placed, so their new-actor trajectory carries no location
            // (checked on real replays: always null); the instigator car's last rigid body is
            // the usable position, and the pad's object name is its stable per-map identity.
            let slot = car_pri.get(*car).and_then(|pri| slots.get(*pri)).copied();
            let car_loc: Value = match car_rb.get(*car) {
                Some(rb) => json!([f32_json(rb.loc[0]), f32_json(rb.loc[1]), f32_json(rb.loc[2])]),
                None => Value::Null,
            };
            pickups_out.push(json!({
                "time": t,
                "slot": slot,
                "pad": Value::Null,
                "big": Value::Null,
                "pad_name": pad_name.get(*pad).cloned(),
                "car_loc": car_loc,
            }));
        }
    }

    let p_len = n_slots;
    let mut cars = vec![f32::NAN; t_len * p_len * 7];
    let mut cars_rot = vec![f32::NAN; t_len * p_len * 4];
    let mut cars_angvel = vec![f32::NAN; t_len * p_len * 3];
    let mut cars_ctrl = vec![f32::NAN; t_len * p_len * CTRL_WIDTH];
    for (i, entries) in per_frame.iter().enumerate() {
        for (slot, loc, vel, boost, extra) in entries {
            let base = (i * p_len + slot) * 7;
            cars[base..base + 3].copy_from_slice(loc);
            cars[base + 3..base + 6].copy_from_slice(vel);
            cars[base + 6] = match boost {
                Some(b) => *b as f32,
                None => f32::NAN,
            };
            let cell = i * p_len + slot;
            cars_rot[cell * 4..cell * 4 + 4].copy_from_slice(&extra.rot);
            cars_angvel[cell * 3..cell * 3 + 3].copy_from_slice(&extra.angvel);
            cars_ctrl[cell * CTRL_WIDTH..(cell + 1) * CTRL_WIDTH].copy_from_slice(&extra.ctrl);
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
    // Wide: the header PlayerStats array (decode.py drops it) as plain objects.
    header.insert("player_stats".into(), player_stats_json(props));

    let players_json: Vec<Value> = player_rows
        .iter()
        .map(|r| {
            let p = players.get(r.pri);
            let body = p.and_then(|p| p.body).map(|(blue, orange)| {
                if r.team == Some(1) { orange } else { blue }
            });
            let camera = p.and_then(|p| p.camera.as_ref()).map(Camera::json);
            json!({
                "slot": r.slot, "pri": r.pri, "player_id": r.player_id, "name": r.name,
                "platform": r.platform, "team": r.team,
                "body": body,
                "camera": camera,
                "match_score": p.and_then(|p| p.match_score),
                "match_goals": p.and_then(|p| p.match_goals),
                "match_assists": p.and_then(|p| p.match_assists),
                "match_saves": p.and_then(|p| p.match_saves),
                "match_shots": p.and_then(|p| p.match_shots),
            })
        })
        .collect();
    let meta = json!({
        "replay_id": replay_id,
        "players": players_json,
        "goals": goals_out,
        "demos": demos_out,
        "header": Value::Object(header),
        "pickups": pickups_out,
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
        ball_rot: ball_rot_arr,
        ball_angvel: ball_angvel_arr,
        cars_rot,
        cars_angvel,
        cars_ctrl,
        overtime: overtime_arr,
        meta,
    }
}

fn car_ctrl_mut(table: &mut ActorTable<Ctrl>, car: i32) -> &mut Ctrl {
    if !table.contains(car) {
        table.insert(car, Ctrl::new());
    }
    table.get_mut(car).expect("just inserted")
}

/// Header `PlayerStats` as plain objects. `Platform` is a `Byte {kind, value}` enum in the
/// header; its `value` (e.g. `OnlinePlatform_Steam`) is what we keep. `OnlineID` is a QWord
/// and is emitted as a decimal string so JSON readers never round it.
fn player_stats_json(props: &[(String, HeaderProp)]) -> Value {
    let Some(arr) = prop(props, "PlayerStats").and_then(HeaderProp::as_array) else {
        return json!([]);
    };
    let rows: Vec<Value> = arr
        .iter()
        .map(|row| {
            let platform = match prop(row, "Platform") {
                Some(HeaderProp::Byte { value: Some(v), .. }) => json!(v),
                other => prop_json(other),
            };
            let online_id = match prop(row, "OnlineID") {
                Some(HeaderProp::QWord(q)) => json!(q.to_string()),
                Some(HeaderProp::Int(i)) => json!(i.to_string()),
                Some(HeaderProp::Str(s)) => json!(s),
                _ => Value::Null,
            };
            json!({
                "Name": prop_json(prop(row, "Name")),
                "Platform": platform,
                "Team": prop_json(prop(row, "Team")),
                "Score": prop_json(prop(row, "Score")),
                "Goals": prop_json(prop(row, "Goals")),
                "Assists": prop_json(prop(row, "Assists")),
                "Saves": prop_json(prop(row, "Saves")),
                "Shots": prop_json(prop(row, "Shots")),
                "bBot": prop_json(prop(row, "bBot")),
                "OnlineID": online_id,
            })
        })
        .collect();
    Value::Array(rows)
}

fn attacker_from_car(car_pri: &OrderedMap<i32>, attacker_car: ActorId) -> Option<i32> {
    if attacker_car.0 >= 0 {
        car_pri.get(attacker_car.0).copied()
    } else {
        None
    }
}
