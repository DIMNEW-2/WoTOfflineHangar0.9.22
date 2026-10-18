# -*- coding: utf-8 -*-
"""LAN battles for the offline hangar (stock battle GUI/physics only).

Companion to Offline.MPTraining: when both teams of a LAN training room
press "ready", the host starts a real battle for the assigned players.
Every participating client builds the battle locally with the client's
own machinery, the same way the stock ``OfflineMapCreator`` does:

  * a BigWorld space + geometry mapping for the room's arena,
  * a client-only ``Avatar`` entity promoted to player via the original
    ``BigWorld.player(avatar)`` (Offline.Manager keeps it reachable in
    ``BigWorld._offline_player_control``), which makes the stock
    PlayerAvatar create the arena, start the battle GUI session and
    process the four native init steps,
  * client-only ``Vehicle`` entities for every battle participant,
    fed to ``avatar.updateArena`` exactly like the real server does,
  * a fake server bridge object substituted for the Avatar/Vehicle
    ``base``/``cell`` mailboxes (stock code fires mailbox calls during
    init, movement, shooting and leaving; locally they land here).

Movement uses *input replication*: the stock client simulates every
vehicle with its native ``WGVehicleFilter`` physics, so each player only
streams movement directions + packed gun angles (+ a slow pose
heartbeat) through the host, and every client drives every tank with
``filter.notifyInputKeysDown``.  No custom physics and no custom GUI.

Wire format: MPChat messages with ``t='btl'``.  Client -> host:
``{t:'btl', cmd:'ready'|'input'|'leave', ...}``.  Host -> clients:
``start`` / ``go`` / ``fight`` / ``avatarReady`` / ``input`` / ``left`` /
``abort`` events.

Limits of this first version (iterated from real-client logs):
no shooting/damage model yet (vehicle_shoot is captured and logged),
no spotting system (all tanks are always rendered), no battle-results
screen - the battle ends when every player leaves via ESC.
"""

import cPickle
import math
import time
import traceback
import zlib

_PREBATTLE_SECONDS = 5
_LOAD_TIMEOUT = 120.0
_SAMPLE_PERIOD = 0.1
_POSE_HEARTBEAT = 0.5
_SIM_PERIOD = 0.05
_SPAWN_WAIT_TIMEOUT = 90.0

# Avatar._MOVEMENT_FLAGS (scripts/client/Avatar.py)
_MOVE_FORWARD = 1
_MOVE_BACKWARD = 2
_ROTATE_LEFT = 4
_ROTATE_RIGHT = 8

# PlayerAvatar._INIT_STEPS.SPACE_LOADED
_INIT_SPACE_LOADED = 1


def _log(msg):
    print('[OFFLINE-MP-BTL] %s' % (msg,))


def _const():
    import constants
    return constants


def _u(x):
    if isinstance(x, unicode):
        return x
    if isinstance(x, str):
        return unicode(x, 'utf-8', errors='ignore')
    return u'' if x is None else unicode(x)


def _b(x):
    if isinstance(x, str):
        return x
    return _u(x).encode('utf-8')


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------

def _dirs_from_flags(flags):
    """Mirror of PlayerAvatar.moveVehicle: flags -> (movement, rotation)."""
    movement_dir = 0
    rotation_dir = 0
    if flags & _ROTATE_RIGHT:
        rotation_dir = 1
    elif flags & _ROTATE_LEFT:
        rotation_dir = -1
    if flags & _MOVE_FORWARD:
        movement_dir = 1
    elif flags & _MOVE_BACKWARD:
        movement_dir = -1
    return (movement_dir, rotation_dir)


def _yaw_to_point(x, z, tx, tz):
    """BigWorld yaw (0 faces +Z) that looks from (x,z) toward (tx,tz)."""
    return math.atan2(tx - x, tz - z)


class _AimTarget(object):
    """Turret/gun matrix pair handed to CompoundAppearance.

    Mirrors the attribute shape of WGVehicleFilter that
    ``setupGunMatrixTargets`` reads (``target.turretMatrix`` /
    ``target.gunMatrix``): the stock filter matrices never move
    without a server pose stream, so the sim writes these instead.
    """

    def __init__(self):
        import Math
        self.turretMatrix = Math.Matrix()
        self.turretMatrix.setIdentity()
        self.gunMatrix = Math.Matrix()
        self.gunMatrix.setIdentity()


class _VehiclePose(object):
    """Python-integrated pose + stock presentation for one Vehicle.

    Serverless WGVehiclePhysics produces no motion (the retail
    filter expects game-server pose samples), and the native entity
    transform is read-only.  The battle client therefore integrates
    a simple kinematic pose per vehicle and publishes it through a
    Math matrix provider wired into the stock CompoundAppearance
    (model root, swinging animator, LOD position, gun targets) -
    the same presentation boundary the 0.9.22 offline-battles
    research uses.  A MatrixAnimation keyframed between sim ticks
    smooths the 20 Hz updates when the build provides one.
    """

    _anim_available = None

    def __init__(self, pos, yaw):
        import Math
        self.x = float(pos[0])
        self.y = float(pos[1])
        self.z = float(pos[2])
        self.yaw = float(yaw)
        self.pitch = 0.0
        self.roll = 0.0
        self.speed = 0.0
        self.input = (0, 0)
        self.attached = False
        self.attach_fails = 0
        self.grounded_once = False
        self.on_model_changed = None
        # Kinematic parameters (filled from the type descriptor).
        self.speed_fwd = 12.0
        self.speed_back = 4.0
        self.traverse = 0.6
        self.turret_traverse = 0.7
        self.gun_traverse = 0.7
        self.half_len = 3.0
        self.half_wid = 1.5
        self.gun_limits = None
        # Presentation providers.
        self.matrix = Math.Matrix()
        self._write_matrix(self.matrix)
        if _VehiclePose._anim_available is None:
            _VehiclePose._anim_available = callable(
                getattr(Math, 'MatrixAnimation', None))
            _log('Math.MatrixAnimation available: %s'
                 % (_VehiclePose._anim_available,))
        self.animation = None
        self._key_from = None
        self._key_to = None
        if _VehiclePose._anim_available:
            try:
                self.animation = Math.MatrixAnimation()
                self._key_from = Math.Matrix(self.matrix)
                self._key_to = Math.Matrix(self.matrix)
                self.animation.keyframes = ((0.0, self._key_from),
                                            (0.001, self._key_to))
                self.animation.time = 0.0
            except Exception:
                self.animation = None
        self.provider = self.animation if self.animation is not None \
            else self.matrix
        self._prev_pose = (self.x, self.y, self.z,
                           self.yaw, self.pitch, self.roll)
        self.aim = _AimTarget()
        self._aim_yaw_written = None
        self._aim_pitch_written = None
        self.aim_yaw = 0.0
        self.aim_pitch = 0.0
        self.aim_yaw_target = 0.0
        self.aim_pitch_target = 0.0
        self.aim_via_rotator = False

    def _write_matrix(self, matrix, pose=None):
        if pose is None:
            pose = (self.x, self.y, self.z,
                    self.yaw, self.pitch, self.roll)
        matrix.setRotateYPR((pose[3], pose[4], pose[5]))
        matrix.translation = (pose[0], pose[1], pose[2])

    def snapshot(self):
        self._prev_pose = (self.x, self.y, self.z,
                           self.yaw, self.pitch, self.roll)

    def publish(self, dt):
        """Write the current pose into the matrix providers."""
        self._write_matrix(self.matrix)
        if self.animation is not None:
            try:
                self._write_matrix(self._key_from, self._prev_pose)
                self._write_matrix(self._key_to)
                self.animation.keyframes = (
                    (0.0, self._key_from),
                    (max(float(dt), 0.001), self._key_to))
                self.animation.time = 0.0
            except Exception:
                self.animation = None
                self.provider = self.matrix

    def set_aim(self, turret_yaw, gun_pitch):
        turret_yaw = float(turret_yaw)
        gun_pitch = float(gun_pitch)
        self.aim_yaw = turret_yaw
        self.aim_pitch = gun_pitch
        self.aim_yaw_target = turret_yaw
        self.aim_pitch_target = gun_pitch
        if turret_yaw != self._aim_yaw_written:
            self.aim.turretMatrix.setRotateYPR((turret_yaw, 0.0, 0.0))
            self._aim_yaw_written = turret_yaw
        if gun_pitch != self._aim_pitch_written:
            self.aim.gunMatrix.setRotateYPR((0.0, gun_pitch, 0.0))
            self._aim_pitch_written = gun_pitch

    def set_aim_target(self, turret_yaw, gun_pitch):
        self.aim_yaw_target = float(turret_yaw)
        self.aim_pitch_target = float(gun_pitch)

    def advance_aim(self, dt):
        # Remote aim arrives as gunAnglesPacked samples at most
        # every 100 ms; writing them verbatim steps the turret
        # visibly (real-client test #19).  Chase the latest
        # sample at a traverse-limited rate instead - the stream
        # itself is already traverse-limited by the driver's
        # rotator, so a 1.5x catch-up rate stays smooth without
        # lagging behind.
        dyaw = (self.aim_yaw_target - self.aim_yaw + math.pi) \
            % (2.0 * math.pi) - math.pi
        dpitch = self.aim_pitch_target - self.aim_pitch
        if dyaw == 0.0 and dpitch == 0.0:
            return
        if abs(dyaw) > 1.0 or abs(dpitch) > 1.0:
            self.set_aim(self.aim_yaw_target, self.aim_pitch_target)
            return
        yaw_step = self.turret_traverse * 1.5 * dt
        if dyaw > yaw_step:
            dyaw = yaw_step
        elif dyaw < -yaw_step:
            dyaw = -yaw_step
        pitch_step = self.gun_traverse * 1.5 * dt
        if dpitch > pitch_step:
            dpitch = pitch_step
        elif dpitch < -pitch_step:
            dpitch = -pitch_step
        self.aim_yaw = (self.aim_yaw + dyaw + math.pi) \
            % (2.0 * math.pi) - math.pi
        self.aim_pitch += dpitch
        if self.aim_yaw != self._aim_yaw_written:
            self.aim.turretMatrix.setRotateYPR(
                (self.aim_yaw, 0.0, 0.0))
            self._aim_yaw_written = self.aim_yaw
        if self.aim_pitch != self._aim_pitch_written:
            self.aim.gunMatrix.setRotateYPR(
                (0.0, self.aim_pitch, 0.0))
            self._aim_pitch_written = self.aim_pitch


def _fill_pose_params(pose, descr):
    """Best-effort kinematic parameters from a VehicleDescr."""
    try:
        limits = descr.type.speedLimits
        pose.speed_fwd = max(1.0, float(limits[0]))
        pose.speed_back = max(0.5, float(limits[1]))
    except Exception:
        pass
    try:
        pose.traverse = max(0.05, float(descr.chassis.rotationSpeed))
    except Exception:
        pass
    try:
        pose.turret_traverse = max(
            0.05, float(descr.turret.rotationSpeed))
    except Exception:
        pass
    try:
        pose.gun_traverse = max(
            0.05, float(descr.gun.rotationSpeed))
    except Exception:
        pose.gun_traverse = pose.turret_traverse
    try:
        bbox = descr.chassis.hitTester.bbox
        pose.half_len = min(5.0, max(
            1.5, abs(float(bbox[1][2]) - float(bbox[0][2])) * 0.5))
        pose.half_wid = min(2.5, max(
            0.8, abs(float(bbox[1][0]) - float(bbox[0][0])) * 0.5))
    except Exception:
        pass
    try:
        pose.gun_limits = descr.gun.pitchLimits['absolute']
    except Exception:
        pose.gun_limits = None


def _vehicle_info_row(vehicle_id, comp_descr, name, team, account_dbid,
                      prebattle_id, is_creator=False):
    """The 18-item ClientArena vehicle tuple (see __vehicleInfoAsDict)."""
    return [int(vehicle_id), comp_descr, name, int(team), True, False,
            False, int(account_dbid), '', 0, int(prebattle_id),
            bool(is_creator), False, {}, 0, [], 0, {}]


def _pack_vehicle_list(rows):
    return zlib.compress(cPickle.dumps(rows, -1))


def _pack_vehicle_added(row):
    return zlib.compress(cPickle.dumps(row, -1))


def _pack_period(period, end_time, length, extra=None):
    return zlib.compress(cPickle.dumps(
        (int(period), float(end_time), float(length),
         [] if extra is None else extra), -1))


def _pack_avatar_ready(vehicle_id):
    return cPickle.dumps(int(vehicle_id), -1)


def _spawn_assignment(players):
    """players: iterable of dicts with 'vid'/'team' (join order kept).

    Returns {vid: spawn_index_within_team}.  Deterministic for a given
    ordered player list, so host and clients agree without extra sync.
    """
    counters = {}
    result = {}
    for player in players:
        team = player['team']
        idx = counters.get(team, 0)
        counters[team] = idx + 1
        result[player['vid']] = idx
    return result


# ---------------------------------------------------------------------------
# Server side (runs inside the MPChat host process, attached to training)
# ---------------------------------------------------------------------------

class _Battle(object):
    __slots__ = ('battle_id', 'room_id', 'arena_type_id', 'round_length',
                 'players', 'ready', 'left', 'phase', 'created_at',
                 'fight_at')

    def __init__(self, battle_id, room):
        self.battle_id = battle_id
        self.room_id = room.room_id
        self.arena_type_id = room.arena_type_id
        self.round_length = room.round_length
        self.players = {}      # vid -> {'team','spawn','veh','nick'}
        self.ready = set()
        self.left = set()
        self.phase = 'loading'
        self.created_at = time.time()
        self.fight_at = 0.0


class _BattleServer(object):

    def __init__(self, training_server):
        self.trn = training_server
        self.mp = training_server.mp
        self.battles = {}      # room_id -> _Battle
        self.by_vid = {}       # vid -> _Battle
        self.next_id = 1

    # -- plumbing ---------------------------------------------------------

    def _conn(self, vid):
        return self.mp.by_vid.get(vid)

    def _send(self, conn, **msg):
        if conn is None:
            return
        msg['t'] = 'btl'
        conn.send(msg)

    def _broadcast(self, battle, exclude_vid=None, **msg):
        for vid in battle.players:
            if vid == exclude_vid or vid in battle.left:
                continue
            self._send(self._conn(vid), **msg)

    # -- lifecycle --------------------------------------------------------

    def start(self, room):
        """Start a battle for the room's assigned members. -> bool"""
        if room.room_id in self.battles:
            return False
        members = sorted(room.members.itervalues(),
                         key=lambda m: (m.joined_at, m.vid))
        players = []
        for member in members:
            # MPTraining roster encoding: low nibble team, 0x10 unassigned.
            team = member.roster & 15
            assigned = not member.roster & 240
            if not assigned or team not in (1, 2):
                continue
            conn = self._conn(member.vid)
            players.append({'vid': member.vid, 'team': team,
                            'veh': member.veh_descr or '',
                            'nick': conn.nick if conn is not None else u''})
        teams = set(p['team'] for p in players)
        if len(players) < 2 or teams != set((1, 2)):
            return False
        if not room.arena_type_id:
            return False
        spawns = _spawn_assignment(players)
        battle = _Battle(self.next_id, room)
        self.next_id += 1
        roster = []
        for player in players:
            record = dict(player)
            record['spawn'] = spawns[player['vid']]
            record['creator'] = player['vid'] == room.creator_vid
            battle.players[player['vid']] = record
            self.by_vid[player['vid']] = battle
            roster.append({'vid': player['vid'], 'team': player['team'],
                           'veh': player['veh'], 'nick': player['nick'],
                           'spawn': record['spawn'],
                           'creator': record['creator']})
        self.battles[room.room_id] = battle
        for vid in battle.players:
            self._send(self._conn(vid), ev='start',
                       battleID=battle.battle_id,
                       roomID=room.room_id,
                       arenaTypeID=battle.arena_type_id,
                       roundLength=battle.round_length,
                       arenaUniqueID=(room.room_id << 32) | battle.battle_id,
                       players=roster)
        _log('battle #%s started: room #%s arena=%s players=%s' % (
            battle.battle_id, room.room_id, battle.arena_type_id,
            [(p['nick'], p['team']) for p in roster]))
        return True

    def _go(self, battle):
        battle.phase = 'prebattle'
        battle.fight_at = time.time() + _PREBATTLE_SECONDS
        self._broadcast(battle, ev='go', prebattle=_PREBATTLE_SECONDS)
        _log('battle #%s: all ready, prebattle countdown %ss' % (
            battle.battle_id, _PREBATTLE_SECONDS))

    def _fight(self, battle):
        battle.phase = 'battle'
        self._broadcast(battle, ev='fight', length=battle.round_length)
        _log('battle #%s: FIGHT (round %ss)' % (
            battle.battle_id, battle.round_length))

    def _finish(self, battle, abort=False):
        if abort:
            self._broadcast(battle, ev='abort')
        for vid in battle.players:
            self.by_vid.pop(vid, None)
        self.battles.pop(battle.room_id, None)
        room = self.trn.rooms.get(battle.room_id)
        if room is not None:
            C = _const()
            room.team_states = {1: C.PREBATTLE_TEAM_STATE.NOT_READY,
                                2: C.PREBATTLE_TEAM_STATE.NOT_READY}
            self.trn._broadcast_team_states(room)
        _log('battle #%s finished (abort=%s)' % (battle.battle_id, abort))

    def _player_gone(self, battle, vid):
        if vid in battle.left:
            return
        battle.left.add(vid)
        battle.ready.discard(vid)
        self._broadcast(battle, ev='left', vid=vid)
        _log('battle #%s: vid %s left (%s/%s gone)' % (
            battle.battle_id, vid, len(battle.left), len(battle.players)))
        if len(battle.left) >= len(battle.players):
            self._finish(battle)
        elif battle.phase == 'loading':
            self._maybe_go(battle)

    def _maybe_go(self, battle):
        if battle.phase != 'loading':
            return
        active = [v for v in battle.players if v not in battle.left]
        if active and all(v in battle.ready for v in active):
            self._go(battle)

    # -- message handling -------------------------------------------------

    def on_msg(self, conn, msg):
        battle = self.by_vid.get(conn.vid)
        cmd = msg.get('cmd')
        if cmd == 'input':
            if battle is None or conn.vid in battle.left:
                return
            relay = {'ev': 'input', 'vid': conn.vid,
                     'm': int(msg.get('m', 0) or 0),
                     'r': int(msg.get('r', 0) or 0),
                     'g': int(msg.get('g', 0) or 0)}
            pos = msg.get('p')
            if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                relay['p'] = [float(pos[0]), float(pos[1]),
                              float(pos[2])]
                if len(pos) > 3:
                    relay['p'].append(float(pos[3]))
            self._broadcast(battle, exclude_vid=conn.vid, **relay)
            return
        if battle is None:
            return
        if cmd == 'ready':
            if conn.vid not in battle.ready:
                battle.ready.add(conn.vid)
                self._broadcast(battle, exclude_vid=conn.vid,
                                ev='avatarReady', vid=conn.vid)
                _log('battle #%s: vid %s ready (%s/%s)' % (
                    battle.battle_id, conn.vid, len(battle.ready),
                    len(battle.players)))
            self._maybe_go(battle)
        elif cmd == 'leave':
            self._player_gone(battle, conn.vid)

    def on_disconnect(self, conn):
        battle = self.by_vid.get(conn.vid)
        if battle is not None:
            self._player_gone(battle, conn.vid)

    def on_room_destroyed(self, room):
        battle = self.battles.get(room.room_id)
        if battle is not None:
            self._finish(battle, abort=True)

    def on_tick(self):
        now = time.time()
        for battle in list(self.battles.itervalues()):
            if battle.phase == 'loading' \
                    and now - battle.created_at > _LOAD_TIMEOUT:
                _log('battle #%s: loading timeout, aborting'
                     % (battle.battle_id,))
                self._finish(battle, abort=True)
            elif battle.phase == 'prebattle' and now >= battle.fight_at:
                self._fight(battle)


def make_server(training_server):
    return _BattleServer(training_server)


# ---------------------------------------------------------------------------
# Fake server bridge (Avatar/Vehicle mailbox substitute)
# ---------------------------------------------------------------------------

class _FakeServer(object):
    """Stands in for ``avatar.base``/``avatar.cell``/``vehicle.cell``.

    Stock PlayerAvatar/Vehicle code fires mailbox calls while entering
    and playing a battle (``base.setClientReady``, ``cell.autoAim``,
    ``vehicle.cell.sendStateToOwnClient``, ...).  Locally they land
    here.  Known gameplay-relevant calls are forwarded to the battle
    client; everything else is a logged no-op so the real-client log
    shows exactly which server surface the client still misses.
    """

    def __init__(self, battle_client):
        self._bc = battle_client
        self._logged = set()

    @property
    def voipController(self):
        return self

    # VOIP surface read by PlayerAvatar.onBecomePlayer via bwProto
    def invalidateMicrophoneMute(self):
        return None

    def isVOIPEnabled(self):
        return False

    def isVivox(self):
        return False

    def isYY(self):
        return False

    def isReady(self):
        return False

    def isPlayerSpeaking(self, account_dbid):
        return False

    # battle lifecycle
    def setClientReady(self):
        self._bc._on_client_ready()

    def leaveArena(self, stats=None):
        self._bc._on_leave_requested()

    def switchObserverFPV(self, enabled):
        return None

    def bindToVehicle(self, vehicle_id):
        self._bc._on_bind_to_vehicle(int(vehicle_id))

    def moveTo(self, position):
        return None

    def switchViewPointOrBindToVehicle(self, is_viewpoint, veh_or_point_id):
        return None

    def sendStateToOwnClient(self):
        return None

    # input / combat surface
    def vehicle_moveWith(self, flags):
        self._bc._on_move_flags(int(flags))

    def autoAim(self, vehicle_id):
        self._bc._on_autoaim(int(vehicle_id))

    def vehicle_shoot(self, *args):
        self._bc._on_shoot()

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)

        def _noop(*args, **kwargs):
            if name not in self._logged:
                self._logged.add(name)
                _log('fakeServer: unhandled mailbox %s%r (no-op)'
                     % (name, args))
            return None

        return _noop


# ---------------------------------------------------------------------------
# Client side
# ---------------------------------------------------------------------------

class _BattleClient(object):

    def __init__(self, state):
        self.state = state
        self.active = False
        self._tearing_down = False
        self._bridge = _FakeServer(self)
        self._space_id = 0
        self._mapping_id = 0
        self._avatar = None
        self._avatar_id = 0
        self._vehicles = {}        # vid -> entityID
        self._players = []         # roster from host
        self._my_team = 0
        self._arena_type = None
        self._round_length = 600
        self._room_id = 0
        self._battle_seq = 0
        self._cur_input = (0, 0)
        self._last_sent = None
        self._last_pose_sent = 0.0
        self._ready_sent = False
        self._promoted = False
        self._gun_limits = None
        self._pose_unsupported = False
        self._spawn_deadline = 0.0
        self._shoot_logged = False
        self._period_state = None
        self._period_ticking = False
        self._script_input_probe = None
        self._poses = {}           # vid -> _VehiclePose
        self._entity_vid = {}      # entityID -> vid
        self._sim_last = 0.0
        self._sim_dead = False
        self._targeting_sent = False
        self._positions_last = 0.0
        self._positions_failed = False

    # -- transport --------------------------------------------------------

    def _send(self, **msg):
        msg['t'] = 'btl'
        try:
            self.state.transport.send_to_server(msg)
        except Exception:
            pass

    def on_msg(self, msg):
        ev = msg.get('ev')
        handler = getattr(self, '_ev_' + ev, None) if ev else None
        if handler is not None:
            handler(msg)

    def on_disconnected(self):
        if self.active:
            _log('LAN disconnected during battle - leaving to hangar')
            self._teardown()

    # -- host events ------------------------------------------------------

    def _ev_start(self, msg):
        if self.active:
            _log('battle start received while already in battle - ignored')
            return
        try:
            self._enter(msg)
        except Exception:
            _log('battle enter failed:')
            traceback.print_exc()
            self._send(cmd='leave')
            self._teardown()

    def _ev_go(self, msg):
        self._publish_period(_const().ARENA_PERIOD.PREBATTLE,
                             float(msg.get('prebattle',
                                           _PREBATTLE_SECONDS)))

    def _ev_fight(self, msg):
        self._publish_period(_const().ARENA_PERIOD.BATTLE,
                             float(msg.get('length',
                                           self._round_length)))

    def _ev_avatarReady(self, msg):
        entity_id = self._vehicles.get(int(msg.get('vid', 0) or 0))
        if entity_id and self._avatar is not None:
            self._avatar.updateArena(_const().ARENA_UPDATE.AVATAR_READY,
                                     _pack_avatar_ready(entity_id))

    def _ev_input(self, msg):
        vid = int(msg.get('vid', 0) or 0)
        entity_id = self._vehicles.get(vid)
        if not entity_id:
            return
        import BigWorld
        vehicle = BigWorld.entity(entity_id)
        if vehicle is None:
            return
        pose = self._poses.get(vid)
        if pose is not None:
            # The kinematic sim integrates every vehicle from its
            # driver's replicated input; the native filter receives
            # no server pose samples offline and stays unused.
            pose.input = (int(msg.get('m', 0) or 0),
                          int(msg.get('r', 0) or 0))
        packed = int(msg.get('g', 0) or 0)
        try:
            prev = vehicle.gunAnglesPacked
            if packed != prev:
                vehicle.gunAnglesPacked = packed
                notifier = getattr(vehicle, 'set_gunAnglesPacked', None)
                if callable(notifier):
                    notifier(prev)
        except Exception:
            pass
        if pose is not None and pose.gun_limits is not None:
            try:
                import gun_rotation_shared
                turret_yaw, gun_pitch = \
                    gun_rotation_shared.decodeGunAngles(
                        packed, pose.gun_limits)
                pose.set_aim_target(turret_yaw, gun_pitch)
            except Exception:
                pass
        pos = msg.get('p')
        if pos and pose is not None:
            try:
                ax = float(pos[0])
                az = float(pos[2])
                dx = ax - pose.x
                dz = az - pose.z
                dist2 = dx * dx + dz * dz
                if dist2 > 36.0:
                    pose.x = ax
                    pose.z = az
                    pose.y = float(pos[1])
                    if len(pos) > 3:
                        pose.yaw = float(pos[3])
                    pose.grounded_once = False
                    pose.snapshot()
                    _log('pose snap for vid %s (drift %.1fm)'
                         % (vid, math.sqrt(dist2)))
                elif dist2 > 0.04:
                    pose.x += dx * 0.5
                    pose.z += dz * 0.5
                    if len(pos) > 3:
                        dyaw = (float(pos[3]) - pose.yaw + math.pi) \
                            % (2.0 * math.pi) - math.pi
                        pose.yaw += dyaw * 0.5
            except Exception:
                pass

    def _ev_left(self, msg):
        vid = int(msg.get('vid', 0) or 0)
        pose = self._poses.get(vid)
        if pose is not None:
            pose.input = (0, 0)

    def _ev_abort(self, msg):
        if self.active:
            _log('battle aborted by host - leaving to hangar')
            self._teardown()

    # -- entering the battle ----------------------------------------------

    def _enter(self, msg):
        import BigWorld
        import ArenaType
        C = _const()
        arena_type_id = int(msg.get('arenaTypeID', 0) or 0)
        arena_type = ArenaType.g_cache.get(arena_type_id)
        if arena_type is None:
            _log('unknown arenaTypeID %s - cannot enter battle'
                 % (arena_type_id,))
            self._send(cmd='leave')
            return
        self._players = list(msg.get('players') or [])
        my_vid = int(getattr(self.state, 'my_vid', 0) or 0)
        me = None
        for player in self._players:
            if int(player.get('vid', 0)) == my_vid:
                me = player
                break
        if me is None:
            _log('own vid %s not in battle roster - cannot enter' % (my_vid,))
            self._send(cmd='leave')
            return
        self._my_team = int(me.get('team', 1))
        self._arena_type = arena_type
        self._round_length = int(msg.get('roundLength', 600) or 600)
        self._room_id = int(msg.get('roomID', 0) or 0)
        self._battle_seq = int(msg.get('battleID', 0) or 0)

        spawn = self._spawn_point(arena_type, me)
        yaw = _yaw_to_point(spawn[0], spawn[1],
                            *self._arena_center(arena_type))

        # From here on every Vehicle is one of ours: engage the
        # filter-sync proxy scoping (see _BATTLE_ACTIVE).
        _BATTLE_ACTIVE[0] = True
        _BATTLE_LOADING_SHOWN[0] = False
        _BATTLE_PAGE_POPULATED[0] = False
        _BATTLE_PAGE_DONE[0] = False
        _PENDING_BATTLE_PAGE[0] = None
        _AVATAR_BOUND_VEHICLE[0] = None
        _ensure_battle_page_patch()
        self._space_id = BigWorld.createSpace()
        self._mapping_id = BigWorld.addSpaceGeometryMapping(
            self._space_id, None, 'spaces/' + arena_type.geometryName)
        _log('entering battle: arena=%s (%s) space=%s team=%s spawn=%s'
             % (arena_type_id, arena_type.geometryName, self._space_id,
                self._my_team, spawn))

        # PlayerAvatar.onEnterWorld runs synchronously inside
        # createEntity and already reads playerVehicleID (and
        # onBecomePlayer reads the arena* fields for ClientArena).
        # createEntity's property dict is silently ignored for the
        # Avatar entity, so the values are seeded by the patched
        # PlayerAvatar.__init__ from _PENDING_AVATAR during
        # construction (see _install_avatar_patch), then assigned
        # once more below for anything the engine dropped.
        try:
            import Math
            remote_camera = {'time': 0.0,
                             'shotPoint': Math.Vector3(0.0, 0.0, 0.0),
                             'zoom': 0}
        except Exception:
            remote_camera = {'time': 0.0, 'shotPoint': (0.0, 0.0, 0.0),
                             'zoom': 0}
        avatar_props = {
            'name': _b(me.get('nick', u'')),
            'arenaUniqueID': int(msg.get('arenaUniqueID', 0) or 0),
            'arenaTypeID': arena_type_id,
            'arenaBonusType': C.ARENA_BONUS_TYPE.TRAINING,
            'arenaGuiType': C.ARENA_GUI_TYPE.TRAINING,
            'arenaExtraData': {},
            'weatherPresetID': 0,
            'denunciationsLeft': 10,
            # A 4-byte no-op cache blob (one unknown record header,
            # zero length).  An empty string takes the broken
            # stock branch in BattleClientCache.load() that
            # iterates the records dict keys calling .clear() on
            # ints - harmless on the first battle (no records
            # yet), fatal on the second (real-client test #19).
            'clientCtx': '\xff\xff\x00\x00',
            'tkillIsSuspected': False,
            'team': self._my_team,
            'playerVehicleID': 0,
            'isObserverBothTeams': False,
            'isGunLocked': False,
            'ownVehicleGear': 0,
            'ownVehicleAuxPhysicsData': 0,
            'remoteCamera': remote_camera,
            'isObserverFPV': False,
            'observerFPVControlMode': 0,
            'numOfObservers': 0}
        _PENDING_AVATAR.append((avatar_props, self._bridge))
        try:
            self._avatar_id = BigWorld.createEntity(
                'Avatar', self._space_id, 0, (spawn[0], 0.0, spawn[1]),
                (0.0, 0.0, yaw), {})
        finally:
            del _PENDING_AVATAR[:]
        avatar = BigWorld.entities[self._avatar_id]
        self._avatar = avatar
        for _key in ('name', 'arenaUniqueID', 'arenaTypeID',
                     'arenaBonusType', 'arenaGuiType', 'arenaExtraData',
                     'weatherPresetID', 'denunciationsLeft', 'clientCtx',
                     'tkillIsSuspected', 'team', 'playerVehicleID',
                     'isObserverBothTeams', 'isGunLocked',
                     'ownVehicleGear', 'ownVehicleAuxPhysicsData',
                     'isObserverFPV', 'observerFPVControlMode',
                     'numOfObservers'):
            try:
                setattr(avatar, _key, avatar_props[_key])
            except Exception:
                pass
        avatar.fakeServer = self._bridge
        avatar._mpBattle = True
        # helpers.statistics polls BigWorld.player().prebattle every
        # tick; only PlayerAccount defines it in stock (the Avatar
        # normally inherits the value through the account repository
        # hand-off that never happens for our client-only Avatar).
        avatar.prebattle = None

        self.active = True
        self._ready_sent = False
        # Real engine promotion (goes through the Offline.Manager
        # BigWorld.player dispatch): PlayerAvatar.onBecomePlayer creates
        # the ClientArena and starts the stock battle GUI session.
        BigWorld.player(avatar)
        self._promoted = True
        _log('avatar promoted to player (id=%s)' % (self._avatar_id,))
        self._spawn_deadline = time.time() + _SPAWN_WAIT_TIMEOUT
        BigWorld.callback(0.25, self._wait_for_space)

    def _arena_center(self, arena_type):
        try:
            bottom_left, upper_right = arena_type.boundingBox
            return ((float(bottom_left[0]) + float(upper_right[0])) / 2.0,
                    (float(bottom_left[1]) + float(upper_right[1])) / 2.0)
        except Exception:
            return (0.0, 0.0)

    def _spawn_point(self, arena_type, player):
        team = int(player.get('team', 1))
        spawn_index = int(player.get('spawn', 0))
        # ArenaType.__getattr__ prefers the gameplay cfg, whose
        # teamSpawnPoints are empty lists when the gameplay section
        # does not redefine them (the real points live in the geometry
        # cfg) - so fall through to the geometry cache, then to the
        # team base, before giving up to the map center. Without this
        # every vehicle spawned stacked at the bounding-box center
        # (real-client test 2026-10-18, arena 101_dday: (100, 0)).
        sources = [arena_type]
        try:
            import ArenaType
            geometry = ArenaType.g_geometryCache.get(
                int(getattr(arena_type, 'id', 0)) & 0xFFFF)
            if geometry is not None:
                sources.append(geometry)
        except Exception:
            pass
        for source in sources:
            try:
                points = source.teamSpawnPoints[team - 1]
            except Exception:
                points = []
            if points:
                point = points[spawn_index % len(points)]
                return (float(point[0]), float(point[1]))
        for source in sources:
            try:
                bases = source.teamBasePositions[team - 1]
                base = None
                if isinstance(bases, dict) and bases:
                    base = bases[sorted(bases.keys())[0]]
                elif bases:
                    base = bases[0]
                if base is not None:
                    return (float(base[0]) + spawn_index * 10.0,
                            float(base[1]))
            except Exception:
                pass
        center = self._arena_center(arena_type)
        team_offset = -60.0 if team == 1 else 60.0
        return (center[0] + team_offset,
                center[1] + spawn_index * 10.0)

    def _terrain_y(self, x, z):
        import BigWorld
        try:
            hit = BigWorld.collide(self._space_id,
                                   (x, 1000.0, z), (x, -1000.0, z))
        except Exception:
            hit = None
        if hit:
            try:
                return float(hit[0][1])
            except Exception:
                pass
        return None

    def _wait_for_space(self):
        if not self.active or self._tearing_down:
            return
        import BigWorld
        avatar = self._avatar
        ready = False
        try:
            progress = getattr(avatar, '_PlayerAvatar__initProgress', 0)
            ready = bool(progress & _INIT_SPACE_LOADED) \
                and getattr(avatar, 'arena', None) is not None
        except Exception:
            ready = False
        if not ready and time.time() < self._spawn_deadline:
            BigWorld.callback(0.25, self._wait_for_space)
            return
        if not ready:
            _log('space did not finish loading in time - spawning anyway')
        try:
            self._spawn_vehicles()
        except Exception:
            _log('vehicle spawn failed:')
            traceback.print_exc()
            self._send(cmd='leave')
            self._teardown()
            return
        BigWorld.callback(_SAMPLE_PERIOD, self._sample)
        self._sim_last = time.time()
        BigWorld.callback(_SIM_PERIOD, self._sim_tick)

    def _vehicle_props(self, comp_descr_bytes, name, team):
        from items import vehicles
        import gun_rotation_shared
        C = _const()
        descr = vehicles.VehicleDescr(compactDescr=comp_descr_bytes)
        packed = gun_rotation_shared.encodeGunAngles(
            0.0, 0.0, descr.gun.pitchLimits['absolute'])
        return descr, {
            'publicInfo': {'compDescr': comp_descr_bytes,
                           'name': _b(name),
                           'team': int(team),
                           'prebattleID': int(self._room_id),
                           'marksOnGun': 0,
                           'index': 0,
                           'outfit': ''},
            'gunAnglesPacked': int(packed),
            'health': int(descr.maxHealth),
            'isCrewActive': True,
            'steeringAngle': 0.0,
            'isStrafing': False,
            'physicsMode': C.VEHICLE_PHYSICS_MODE.STANDARD,
            'siegeState': C.VEHICLE_SIEGE_STATE.DISABLED,
            'engineMode': (0, 0),
            'damageStickers': [],
            'publicStateModifiers': (),
            'stunInfo': 0.0}

    def _spawn_vehicles(self):
        import BigWorld
        C = _const()
        arena_type = self._arena_type
        center = self._arena_center(arena_type)
        my_vid = int(getattr(self.state, 'my_vid', 0) or 0)
        ground_y = None
        rows = []
        own_entity_id = 0
        for player in self._players:
            vid = int(player.get('vid', 0))
            team = int(player.get('team', 1))
            comp_descr = ''
            try:
                comp_descr = str(player.get('veh') or '').decode('hex')
            except Exception:
                comp_descr = ''
            if not comp_descr:
                _log('no vehicle descriptor for vid %s - skipped' % (vid,))
                continue
            spawn = self._spawn_point(arena_type, player)
            y = self._terrain_y(spawn[0], spawn[1])
            if y is None:
                y = ground_y if ground_y is not None else 0.0
            elif ground_y is None:
                ground_y = y
            yaw = _yaw_to_point(spawn[0], spawn[1], center[0], center[1])
            descr, props = self._vehicle_props(comp_descr,
                                               player.get('nick', u''), team)
            entity_id = BigWorld.createEntity(
                'Vehicle', self._space_id, 0,
                (spawn[0], y, spawn[1]), (0.0, 0.0, yaw), props)
            self._vehicles[vid] = entity_id
            self._entity_vid[entity_id] = vid
            try:
                pose = _VehiclePose((spawn[0], y, spawn[1]), yaw)
                _fill_pose_params(pose, descr)
                self._poses[vid] = pose
            except Exception:
                _log('pose init failed for vid %s:' % (vid,))
                traceback.print_exc()
            _MP_VEHICLE_BRIDGES[entity_id] = self._bridge
            try:
                vehicle = BigWorld.entities[entity_id]
                vehicle._mpFakeServer = self._bridge
            except Exception:
                _log('vehicle entity %s not reachable via '
                     'BigWorld.entities yet - bridge registered by id'
                     % (entity_id,))
                traceback.print_exc()
            if vid == my_vid:
                own_entity_id = entity_id
                self._gun_limits = descr.gun.pitchLimits['absolute']
                self._own_descr = descr
            account_dbid = 1 if vid == my_vid else vid
            rows.append(_vehicle_info_row(
                entity_id, comp_descr, _b(player.get('nick', u'')), team,
                account_dbid, self._room_id,
                bool(player.get('creator'))))
            _log('spawned vehicle entity=%s vid=%s team=%s at %s'
                 % (entity_id, vid, team, spawn))
        if not own_entity_id:
            raise RuntimeError('own vehicle was not spawned')
        # Stock player-vehicle binding (set_<prop> notifier replay).
        avatar = self._avatar
        prev = getattr(avatar, 'playerVehicleID', 0)
        avatar.playerVehicleID = own_entity_id
        try:
            avatar.set_playerVehicleID(prev)
        except Exception:
            _log('set_playerVehicleID notifier failed:')
            traceback.print_exc()
        # Arena roster, exactly like the real server's initial list.
        avatar.updateArena(C.ARENA_UPDATE.VEHICLE_LIST,
                           _pack_vehicle_list(rows))
        _log('arena vehicle list published (%s vehicles)' % (len(rows),))
        BigWorld.callback(1.0, lambda: self._diag_tick(0))

    def _diag_tick(self, n):
        if not self.active or self._tearing_down:
            return
        import BigWorld
        avatar = self._avatar
        progress = None
        own_started = None
        try:
            progress = getattr(avatar, '_PlayerAvatar__initProgress', None)
            own_id = self._vehicles.get(
                int(getattr(self.state, 'my_vid', 0) or 0))
            own = BigWorld.entity(own_id) if own_id else None
            own_started = getattr(own, 'isStarted', None)
            try:
                space_load = BigWorld.spaceLoadStatus()
            except Exception:
                space_load = '?'
            try:
                world_draw = BigWorld.worldDrawEnabled()
            except Exception:
                world_draw = '?'
            own_pos = None
            try:
                sim_pose = self._poses.get(
                    int(getattr(self.state, 'my_vid', 0) or 0))
                if sim_pose is not None:
                    own_pos = '(%.1f,%.1f v=%.1f att=%s pr=%.0f/%.0f)' \
                        % (sim_pose.x, sim_pose.z, sim_pose.speed,
                           sim_pose.attached,
                           math.degrees(sim_pose.pitch),
                           math.degrees(sim_pose.roll))
                elif own is not None:
                    p = own.position
                    own_pos = '(%.1f,%.1f)' % (p[0], p[2])
            except Exception:
                own_pos = '?'
            try:
                srv_t = float(BigWorld.serverTime())
            except Exception:
                srv_t = -1.0
            try:
                cli_t = float(BigWorld.time())
            except Exception:
                cli_t = -1.0
            try:
                rot = avatar.gunRotator
                turret_info = ('(%.2f,%.2f)'
                               % (rot.turretYaw, rot.gunPitch)) \
                    if rot is not None else None
            except Exception:
                turret_info = '?'
            try:
                handler = getattr(avatar, 'inputHandler', None)
                mode_name = getattr(
                    handler, '_AvatarInputHandler__ctrlModeName', '?')
            except Exception:
                mode_name = '?'
            _log('diag tick %s: initProgress=%s gunRotator=%s '
                 'ownVehicleStarted=%s avatarVehicle=%s spaceLoad=%s '
                 'worldDraw=%s period=%s pos=%s input=%s srvT=%.1f '
                 'cliT=%.1f turret=%s mode=%s'
                 % (n, progress,
                    getattr(avatar, 'gunRotator', None) is not None,
                    own_started, getattr(avatar, 'vehicle', None),
                    space_load, world_draw,
                    getattr(getattr(avatar, 'arena', None),
                            'period', None), own_pos, self._cur_input,
                    srv_t, cli_t, turret_info, mode_name))
        except Exception:
            _log('diag tick %s failed:' % (n,))
            traceback.print_exc()
        if n == 12:
            # One-shot: does the minimap's AOI provider chain
            # (WGTranslationOnlyMP <- vehicle.matrix) evaluate to
            # the live pose for a remote vehicle?
            try:
                import Math
                my_vid = int(getattr(self.state, 'my_vid', 0) or 0)
                for vid, eid in self._vehicles.iteritems():
                    if vid == my_vid:
                        continue
                    veh = BigWorld.entity(eid)
                    pose = self._poses.get(vid)
                    if veh is None or pose is None:
                        continue
                    direct = Math.Matrix(veh.matrix).translation
                    mp = Math.WGTranslationOnlyMP()
                    mp.source = veh.matrix
                    chained = Math.Matrix(mp).translation
                    _log('minimap chain vid=%s pose=(%.1f,%.1f) '
                         'matrix=(%.1f,%.1f) translationMP=(%.1f,%.1f)'
                         % (vid, pose.x, pose.z, direct[0], direct[2],
                            chained[0], chained[2]))
                    break
            except Exception:
                _log('minimap chain check failed:')
                traceback.print_exc()
        if n < 9:
            BigWorld.callback(1.0, lambda: self._diag_tick(n + 1))
        elif n < 15:
            BigWorld.callback(5.0, lambda: self._diag_tick(n + 1))

    def _publish_period(self, period, length, _republish=False):
        if self._avatar is None:
            return
        import BigWorld
        C = _const()
        end_time = float(BigWorld.serverTime()) + float(length) \
            if length > 0 else 0.0
        self._avatar.updateArena(
            C.ARENA_UPDATE.PERIOD,
            _pack_period(period, end_time, length))
        if _republish:
            return
        _log('arena period -> %s (%.0fs)' % (period, length))
        # BigWorld.serverTime() is permanently -1 on a disconnected
        # client (real-client test 2026-10-18 #14), so a stock GUI
        # countdown computed as periodEndTime - serverTime() never
        # advances. Republish the same period every second with a
        # shrinking remainder; ClientArena refires onPeriodChange
        # for every PERIOD update, so the timer counts down.
        self._period_state = (period, time.time() + float(length)) \
            if length > 0 else None
        if length > 1 and not self._period_ticking:
            self._period_ticking = True
            BigWorld.callback(1.0, self._period_tick)

    def _period_tick(self):
        self._period_ticking = False
        if not self.active or self._tearing_down:
            return
        state = self._period_state
        if not state:
            return
        period, deadline = state
        remaining = deadline - time.time()
        if remaining <= 0.5:
            return
        self._publish_period(period, remaining, _republish=True)
        import BigWorld
        self._period_ticking = True
        BigWorld.callback(1.0, self._period_tick)

    # -- bridge callbacks ---------------------------------------------------

    def _on_client_ready(self):
        _log('stock avatar reports setClientReady')
        avatar = self._avatar
        if avatar is not None:
            own_id = self._vehicles.get(
                int(getattr(self.state, 'my_vid', 0) or 0))
            if own_id:
                try:
                    avatar.updateArena(_const().ARENA_UPDATE.AVATAR_READY,
                                       _pack_avatar_ready(own_id))
                except Exception:
                    pass
            descr = getattr(self, '_own_descr', None)
            if descr is not None:
                try:
                    avatar.syncVehicleAttrs(
                        {'circularVisionRadius':
                         descr.turret.circularVisionRadius})
                except Exception:
                    pass
        if not self._ready_sent:
            self._ready_sent = True
            self._send(cmd='ready')

    def _on_bind_to_vehicle(self, vehicle_id):
        # AvatarPositionControl.bindToVehicle asks the server to
        # attach the Avatar to its Vehicle; on a real server the
        # engine then sets avatar.vehicle and fires onVehicleChanged,
        # which links the consistent-matrices target and the battle
        # GUI view points to the vehicle. None of that happens for a
        # client-only entity, so reproduce the Python-side results
        # here, synchronously - the input handler enables the arcade
        # camera right after this mailbox call returns.
        _log('bindToVehicle(%s) - emulating engine attachment'
             % (vehicle_id,))
        avatar = self._avatar
        if avatar is None or not vehicle_id:
            return
        import BigWorld
        vehicle = None
        try:
            vehicle = BigWorld.entity(vehicle_id)
        except Exception:
            vehicle = None
        if vehicle is not None:
            # The engine sets .vehicle itself on a real server and
            # only then fires onVehicleChanged. The attribute is
            # read-only from Python ("attribute vehicle in
            # PlayerAvatar is read-only", test #11), so register
            # the vehicle for the __getattribute__ interception
            # instead, BEFORE onVehicleChanged reads self.vehicle.
            _AVATAR_BOUND_VEHICLE[0] = vehicle
            _log('bindToVehicle: avatar.vehicle bound to %s (via '
                 '__getattribute__)' % (vehicle_id,))
        try:
            avatar.onVehicleChanged()
        except Exception:
            _log('onVehicleChanged emulation failed:')
            traceback.print_exc()
        if vehicle is None:
            _log('bindToVehicle: vehicle entity %s not found'
                 % (vehicle_id,))
            return
        try:
            avatar.consistentMatrices._ConsistentMatrices__setTarget(
                vehicle.matrix, False)
        except Exception:
            _log('consistentMatrices target link failed:')
            traceback.print_exc()
        try:
            avatar.guiSessionProvider.shared.viewPoints.\
                updateAttachedVehicle(vehicle_id)
        except Exception:
            _log('viewPoints.updateAttachedVehicle failed:')
            traceback.print_exc()

    def _on_move_flags(self, flags):
        dirs = _dirs_from_flags(flags)
        if dirs != self._cur_input:
            # Proves keys reach the stock Avatar movement path
            # (which calls filter.notifyInputKeysDown just before
            # base.vehicle_moveWith lands here).
            _log('move flags %s -> (m=%s, r=%s)'
                 % (flags, dirs[0], dirs[1]))
        self._cur_input = dirs

    def _on_autoaim(self, vehicle_id):
        # Without a server the gun rotator waits for server-driven
        # tracking when clientMode is off; keep aiming manual.
        avatar = self._avatar
        if avatar is not None and vehicle_id:
            try:
                rotator = avatar.gunRotator
                if rotator is not None:
                    rotator.clientMode = True
            except Exception:
                pass

    def _on_shoot(self):
        if not self._shoot_logged:
            self._shoot_logged = True
            _log('vehicle_shoot captured (shooting model not implemented '
                 'yet)')

    def _on_leave_requested(self):
        _log('leaveArena requested via stock GUI')
        self._send(cmd='leave')
        import BigWorld
        BigWorld.callback(0.0, self._teardown)

    # -- input sampling -----------------------------------------------------

    def _sample(self):
        if not self.active or self._tearing_down:
            return
        import BigWorld
        BigWorld.callback(_SAMPLE_PERIOD, self._sample)
        packed = 0
        avatar = self._avatar
        if avatar is not None and self._gun_limits is not None:
            try:
                import gun_rotation_shared
                rotator = avatar.gunRotator
                if rotator is not None:
                    packed = int(gun_rotation_shared.encodeGunAngles(
                        rotator.turretYaw, rotator.gunPitch,
                        self._gun_limits))
            except Exception:
                packed = 0
        now = time.time()
        pose = None
        own_pose = self._poses.get(
            int(getattr(self.state, 'my_vid', 0) or 0))
        if own_pose is not None and \
                now - self._last_pose_sent >= _POSE_HEARTBEAT:
            pose = [own_pose.x, own_pose.y, own_pose.z, own_pose.yaw]
        current = (self._cur_input[0], self._cur_input[1], packed)
        if current != self._last_sent or pose is not None:
            msg = {'cmd': 'input', 'm': current[0], 'r': current[1],
                   'g': packed}
            if pose is not None:
                msg['p'] = pose
                self._last_pose_sent = now
            self._send(**msg)
            self._last_sent = current

    # -- kinematic sim + presentation --------------------------------------
    # Serverless native physics never produces pose samples, so the
    # battle integrates simple kinematic poses (descriptor speed
    # limits / hull traverse, terrain following via collide) for
    # every vehicle from replicated driver input, and publishes
    # them through matrix providers wired into the stock appearance
    # and - for the own vehicle - into the camera matrix chain.

    def _sim_tick(self):
        if not self.active or self._tearing_down:
            return
        import BigWorld
        BigWorld.callback(_SIM_PERIOD, self._sim_tick)
        if self._sim_dead:
            return
        try:
            self._sim_step()
        except Exception:
            self._sim_dead = True
            _log('kinematic sim disabled after error:')
            traceback.print_exc()

    def _sim_step(self):
        now = time.time()
        dt = now - self._sim_last
        self._sim_last = now
        if dt <= 0.0:
            dt = _SIM_PERIOD
        elif dt > 0.25:
            dt = 0.25
        avatar = self._avatar
        period = 0
        if avatar is not None:
            arena = getattr(avatar, 'arena', None)
            period = int(getattr(arena, 'period', 0) or 0)
        can_drive = (period == 3)
        my_vid = int(getattr(self.state, 'my_vid', 0) or 0)
        for vid, pose in list(self._poses.iteritems()):
            if vid == my_vid:
                pose.input = self._cur_input
            if not pose.attached and pose.attach_fails < 3:
                self._try_attach_pose(vid, pose)
            if pose.attached and getattr(pose, '_bound_provider',
                                         None) is not pose.provider:
                self._rebind_pose(vid, pose)
            pose.snapshot()
            move, rotate = pose.input if can_drive else (0, 0)
            self._integrate_pose(pose, move, rotate, dt)
            pose.publish(dt)
            if vid != my_vid:
                pose.advance_aim(dt)
        own_pose = self._poses.get(my_vid)
        if own_pose is not None and own_pose.attached \
                and avatar is not None:
            self._drive_own_aim(own_pose)
            self._reassert_own_binding(own_pose)
        if avatar is not None and now - self._positions_last >= 0.25:
            self._positions_last = now
            self._feed_arena_positions(avatar)

    def _feed_arena_positions(self, avatar):
        # Retail servers stream ClientArena positions for the
        # minimap via PlayerAvatar.updatePositions; without them
        # minimap entries for the other vehicles never leave the
        # UNDEFINED state (real-client test #17).  Indices are
        # ranks in the sorted arena vehicle id order, mirroring
        # ClientArena.__rebuildIndexToId.
        try:
            index_of = {}
            for idx, eid in enumerate(
                    sorted(self._vehicles.itervalues())):
                index_of[eid] = idx
            indices = []
            positions = []
            for vid, pose in self._poses.iteritems():
                eid = self._vehicles.get(vid)
                if eid is None or eid not in index_of:
                    continue
                indices.append(index_of[eid])
                positions.append(pose.x)
                positions.append(pose.z)
            if indices:
                avatar.updatePositions(tuple(indices),
                                       tuple(positions))
        except Exception:
            if not self._positions_failed:
                self._positions_failed = True
                _log('arena positions feed failed:')
                traceback.print_exc()

    def _integrate_pose(self, pose, move, rotate, dt):
        if move > 0:
            target = pose.speed_fwd
        elif move < 0:
            target = -pose.speed_back
        else:
            target = 0.0
        # Engine-like approach: reach top speed in ~2.5 s, brake or
        # coast down faster.  A stand-in for the retail powertrain,
        # plausible across vehicle classes.
        if target != 0.0 and (pose.speed == 0.0 or
                              (target > 0) == (pose.speed > 0)):
            rate = max(2.0, pose.speed_fwd * 0.4)
        else:
            rate = max(4.0, pose.speed_fwd * 0.8)
        delta = target - pose.speed
        step = rate * dt
        if delta > step:
            delta = step
        elif delta < -step:
            delta = -step
        pose.speed += delta
        if rotate:
            turn = float(rotate)
            if pose.speed < -0.1:
                turn = -turn
            pose.yaw = (pose.yaw + turn * pose.traverse * dt
                        + math.pi) % (2.0 * math.pi) - math.pi
        if pose.speed != 0.0:
            pose.x += math.sin(pose.yaw) * pose.speed * dt
            pose.z += math.cos(pose.yaw) * pose.speed * dt
        if pose.speed != 0.0 or rotate or not pose.grounded_once:
            self._ground_pose(pose)

    def _ground_pose(self, pose):
        center_y = self._terrain_y(pose.x, pose.z)
        if center_y is None:
            return
        # The compound model origin sits at track-bottom level, so
        # the pose Y is the terrain height itself (the earlier +1 m
        # left every tank floating - real-client test #16).
        pose.y = center_y
        sin_y = math.sin(pose.yaw)
        cos_y = math.cos(pose.yaw)
        front = self._terrain_y(pose.x + sin_y * pose.half_len,
                                pose.z + cos_y * pose.half_len)
        back = self._terrain_y(pose.x - sin_y * pose.half_len,
                               pose.z - cos_y * pose.half_len)
        right = self._terrain_y(pose.x + cos_y * pose.half_wid,
                                pose.z - sin_y * pose.half_wid)
        left = self._terrain_y(pose.x - cos_y * pose.half_wid,
                               pose.z + sin_y * pose.half_wid)
        if front is not None and back is not None:
            # Sign empirically corrected on the real client: the
            # hull nosed down uphill with (front - back) (#16).
            pose.pitch = math.atan2(back - front,
                                    2.0 * pose.half_len)
        if right is not None and left is not None:
            pose.roll = math.atan2(right - left,
                                   2.0 * pose.half_wid)
        pose.grounded_once = True

    def _try_attach_pose(self, vid, pose):
        import BigWorld
        entity_id = self._vehicles.get(vid)
        if not entity_id:
            return
        vehicle = BigWorld.entity(entity_id)
        if vehicle is None:
            return
        appearance = getattr(vehicle, 'appearance', None)
        if appearance is None:
            return
        if getattr(appearance, 'compoundModel', None) is None:
            return
        try:
            self._apply_presentation(vid, vehicle, appearance, pose)
        except Exception:
            pose.attach_fails += 1
            _log('pose attach failed vid=%s (attempt %s):'
                 % (vid, pose.attach_fails))
            traceback.print_exc()
            return
        pose.attached = True
        pose.publish(_SIM_PERIOD)
        _log('pose presentation attached vid=%s entity=%s anim=%s'
             % (vid, entity_id, pose.animation is not None))
        changed = getattr(appearance, 'onModelChanged', None)
        if changed is not None:
            def _on_model_changed(bc=self, v=vid, p=pose):
                import BigWorld
                # Deferred: stock __onModelsRefresh rebinds its own
                # providers synchronously AFTER firing this event.
                BigWorld.callback(0.0, lambda: bc._rebind_pose(v, p))
            try:
                changed += _on_model_changed
                pose.on_model_changed = _on_model_changed
            except Exception:
                pass
        if vid == int(getattr(self.state, 'my_vid', 0) or 0):
            self._bind_own_matrices(pose)

    def _rebind_pose(self, vid, pose):
        if not self.active or self._tearing_down:
            return
        import BigWorld
        entity_id = self._vehicles.get(vid)
        vehicle = BigWorld.entity(entity_id) if entity_id else None
        if vehicle is None:
            return
        appearance = getattr(vehicle, 'appearance', None)
        if appearance is None:
            return
        try:
            self._apply_presentation(vid, vehicle, appearance, pose)
            if vid == int(getattr(self.state, 'my_vid', 0) or 0):
                self._bind_own_matrices(pose)
        except Exception:
            _log('pose rebind failed vid=%s:' % (vid,))
            traceback.print_exc()

    def _apply_presentation(self, vid, vehicle, appearance, pose):
        model = getattr(appearance, 'compoundModel', None)
        if model is None:
            model = getattr(vehicle, 'model', None)
        if model is None:
            raise RuntimeError('no compound model yet')
        model.matrix = pose.provider
        swinging = getattr(appearance, 'swingingAnimator', None)
        if swinging is not None:
            import Math
            identity = Math.Matrix()
            identity.setIdentity()
            swinging.placingCompensationMatrix = identity
            swinging.worldMatrix = pose.provider
        lod = getattr(appearance, 'lodCalculator', None)
        if lod is not None:
            try:
                import DataLinks
                lod.setupPosition(
                    DataLinks.linkMatrixTranslation(pose.provider))
            except Exception:
                pass
        setup = getattr(appearance, 'setupGunMatrixTargets', None)
        if callable(setup):
            target = pose.aim
            pose.aim_via_rotator = False
            if vid == int(getattr(self.state, 'my_vid', 0) or 0):
                # Stock binds the player vehicle's gun targets to
                # the gun rotator itself (Vehicle.startVisual):
                # its _MatrixAnimators keyframe between the 10 Hz
                # rotator ticks, which is what makes the retail
                # turret glide.  Writing rotator angles into a
                # plain matrix steps visibly instead
                # (real-client test #19).
                avatar = self._avatar
                rotator = getattr(avatar, 'gunRotator', None) \
                    if avatar is not None else None
                if rotator is not None:
                    target = rotator
                    pose.aim_via_rotator = True
            setup(target)
        pose._bound_provider = pose.provider

    def _bind_own_matrices(self, pose):
        avatar = self._avatar
        if avatar is None:
            return
        try:
            matrices = avatar.consistentMatrices
            matrices.attachedVehicleMatrix.target = pose.provider
            matrices.ownVehicleMatrix.target = pose.provider
            try:
                matrices.onVehicleMatrixBindingChanged(False)
            except Exception:
                pass
        except Exception:
            _log('consistentMatrices bind failed:')
            traceback.print_exc()
        try:
            stab = getattr(avatar,
                           '_PlayerAvatar__ownVehicleStabMProv', None)
            if stab is not None:
                stab.target = pose.provider
        except Exception:
            _log('ownVehicleStabMProv bind failed:')
            traceback.print_exc()
        try:
            # The steady calculator feeds the sniper/arcade aiming
            # systems.  Stock relinkSources() points it at the dead
            # native filter matrices (sniper camera stuck at the
            # spawn point - real-client test #16), so bind its
            # sources directly to the pose provider instead.
            handler = getattr(avatar, 'inputHandler', None)
            calc = getattr(handler, 'steadyVehicleMatrixCalculator',
                           None)
            if calc is not None:
                out = calc.outputMProv
                out.rotationSrc = pose.provider
                out.translationSrc = pose.provider
                calc.stabilisedMProv.target = pose.provider
        except Exception:
            _log('steadyVehicleMatrixCalculator bind failed:')
            traceback.print_exc()
        _log('own-vehicle camera matrices bound to pose provider')

    def _reassert_own_binding(self, pose):
        avatar = self._avatar
        if avatar is None:
            return
        try:
            matrices = avatar.consistentMatrices
            if matrices.attachedVehicleMatrix.target is not \
                    pose.provider or \
                    matrices.ownVehicleMatrix.target is not \
                    pose.provider:
                self._bind_own_matrices(pose)
                return
        except Exception:
            pass
        try:
            stab = getattr(avatar,
                           '_PlayerAvatar__ownVehicleStabMProv', None)
            if stab is not None and stab.target is not pose.provider:
                self._bind_own_matrices(pose)
                return
        except Exception:
            pass
        try:
            handler = getattr(avatar, 'inputHandler', None)
            calc = getattr(handler, 'steadyVehicleMatrixCalculator',
                           None)
            if calc is not None and (
                    calc.outputMProv.rotationSrc is not pose.provider
                    or calc.stabilisedMProv.target is not
                    pose.provider):
                self._bind_own_matrices(pose)
        except Exception:
            pass

    def _drive_own_aim(self, pose):
        avatar = self._avatar
        rotator = getattr(avatar, 'gunRotator', None)
        if rotator is None:
            return
        if not self._targeting_sent:
            self._send_targeting_info()
        try:
            if getattr(rotator, '_VehicleGunRotator__timerID',
                       None) is None:
                rotator.start()
        except Exception:
            pass
        if not pose.aim_via_rotator:
            try:
                pose.set_aim(rotator.turretYaw, rotator.gunPitch)
            except Exception:
                pass
        # Server echo: VehicleGunRotator.__syncWithServerTurretYaw
        # snaps the predicted yaw back to vehicle.
        # getServerGunAngles() (decoded gunAnglesPacked) whenever
        # the deviation exceeds ~8deg + 0.4*traverse.  Retail
        # servers stream that property continuously; without the
        # echo the turret turned ~20deg and snapped back to the
        # hull axis (real-client test #16).  Mirror our own angles
        # into the property every sim tick.
        if self._gun_limits is None:
            return
        try:
            import BigWorld
            import gun_rotation_shared
            entity_id = self._vehicles.get(
                int(getattr(self.state, 'my_vid', 0) or 0))
            vehicle = BigWorld.entity(entity_id) if entity_id else None
            if vehicle is None:
                return
            packed = int(gun_rotation_shared.encodeGunAngles(
                rotator.turretYaw, rotator.gunPitch,
                self._gun_limits))
            prev = vehicle.gunAnglesPacked
            if packed != prev:
                vehicle.gunAnglesPacked = packed
                notifier = getattr(vehicle, 'set_gunAnglesPacked',
                                   None)
                if callable(notifier):
                    notifier(prev)
        except Exception:
            pass

    def _send_targeting_info(self):
        # Retail servers call PlayerAvatar.updateTargetingInfo right
        # after spawn; it is the only stock path that gives the gun
        # rotator its traverse speeds and starts its tick.  Without
        # it turretYaw/gunPitch stay at (0, 0) forever (tests #13-14).
        avatar = self._avatar
        descr = getattr(self, '_own_descr', None)
        if avatar is None or descr is None:
            return
        turret_speed = 0.7
        try:
            turret_speed = float(descr.turret.rotationSpeed) or 0.7
        except Exception:
            pass
        gun_speed = turret_speed
        try:
            gun_speed = float(descr.gun.rotationSpeed) or turret_speed
        except Exception:
            pass
        aiming_time = 2.0
        try:
            aiming_time = float(descr.gun.aimingTime) or 2.0
        except Exception:
            pass
        gun_factor = 0.1
        try:
            factors = descr.gun.shotDispersionFactors
            if isinstance(factors, dict):
                gun_factor = float(factors.get('turretRotation', 0.1))
            else:
                gun_factor = float(factors[0])
        except Exception:
            pass
        move_factor = 0.2
        rot_factor = 0.2
        try:
            factors = descr.chassis.shotDispersionFactors
            move_factor = float(factors[0])
            rot_factor = float(factors[1])
        except Exception:
            pass
        self._targeting_sent = True
        try:
            avatar.updateTargetingInfo(0.0, 0.0, turret_speed,
                                       gun_speed, 1.0, gun_factor,
                                       move_factor, rot_factor,
                                       aiming_time)
            _log('updateTargetingInfo sent (turret %.2f rad/s) - '
                 'gun rotator should be live' % (turret_speed,))
        except Exception:
            _log('updateTargetingInfo failed:')
            traceback.print_exc()

    # -- teardown -----------------------------------------------------------

    def _teardown(self):
        if self._tearing_down:
            return
        self._tearing_down = True
        import BigWorld
        _log('tearing down battle, returning to hangar')
        # Promote the hangar Account FIRST, while the Avatar entity is
        # still alive: the engine demotes the Avatar, whose stock
        # onBecomeNonPlayer stops the battle GUI session, destroys the
        # ClientArena and detaches the chat proxy, and only then does
        # Account.onBecomePlayer run cleanly. Destroying the Avatar
        # first (the original order) left the battle session ticking
        # forever and made ChatManager.switchPlayerProxy crash on the
        # destroyed Avatar's emptied __dict__ (real-client failure
        # 2026-10-18).
        account = getattr(self.state, 'player', None)
        was_promoted = self._promoted
        if account is not None and was_promoted:
            try:
                BigWorld.player(account)
            except Exception:
                _log('account re-promotion failed:')
                traceback.print_exc()
            self._promoted = False
        for entity_id in list(self._vehicles.itervalues()):
            try:
                BigWorld.destroyEntity(entity_id)
            except Exception:
                pass
            _MP_VEHICLE_BRIDGES.pop(entity_id, None)
        self._vehicles = {}
        if self._avatar_id:
            try:
                BigWorld.destroyEntity(self._avatar_id)
            except Exception:
                pass
        self._avatar = None
        self._avatar_id = 0
        # Retire poses only after every entity is destroyed: the
        # stock appearances/animators hold our matrix providers,
        # and dropping the last Python references while native
        # teardown may still evaluate them is a crash hazard
        # (client process died returning from battle #1,
        # real-client test #20).
        _RETIRED_POSES[:] = list(self._poses.itervalues())
        self._poses = {}
        self._entity_vid = {}
        # Release only OUR battle space (the Account lives in its own).
        try:
            BigWorld.cameraSpaceID(0)
        except Exception:
            pass
        if self._space_id:
            try:
                if BigWorld.isClientSpace(self._space_id):
                    if self._mapping_id:
                        BigWorld.delSpaceGeometryMapping(
                            self._space_id, self._mapping_id)
                    BigWorld.clearSpace(self._space_id)
                    BigWorld.releaseSpace(self._space_id)
            except Exception:
                _log('space release failed:')
                traceback.print_exc()
        self._space_id = 0
        self._mapping_id = 0
        self.active = False
        try:
            from PlayerEvents import g_playerEvents
            if getattr(g_playerEvents, 'isPlayerEntityChanging', False):
                g_playerEvents.isPlayerEntityChanging = False
        except Exception:
            pass
        # Rebuild the lobby GUI exactly like Offline.Manager's
        # _show_lobby: Account.showGUI restores databaseID (stock
        # onBecomePlayer nulled it) and drives the app-loader lobby
        # transition via onAccountShowGUI. Deferred so the battle app
        # finishes unloading first.
        if account is not None and was_promoted:
            try:
                BigWorld.callback(0.3, self._restore_lobby)
            except Exception:
                self._restore_lobby()
        _BATTLE_ACTIVE[0] = False
        _BATTLE_LOADING_SHOWN[0] = False
        _BATTLE_PAGE_POPULATED[0] = False
        _BATTLE_PAGE_DONE[0] = False
        _PENDING_BATTLE_PAGE[0] = None
        _AVATAR_BOUND_VEHICLE[0] = None
        self._tearing_down = False
        _log('back in hangar')

    def _restore_lobby(self):
        account = getattr(self.state, 'player', None)
        if account is None:
            return
        import BigWorld
        import cPickle
        try:
            account.showGUI(cPickle.dumps(
                {'databaseID': 1, 'currentVehInvID': 1}))
            _log('lobby GUI restored via Account.showGUI')
        except Exception:
            _log('Account.showGUI on lobby restore failed:')
            traceback.print_exc()
        try:
            from PlayerEvents import g_playerEvents
            g_playerEvents.onStatsResync()
        except Exception:
            pass
        try:
            from Offline import Manager
            offline_state = Manager.get_offline_state()
            resync = offline_state.get('resync_inventory')
            if resync is not None:
                resync()
            select_vehicle = offline_state.get('select_vehicle')
            if select_vehicle is not None:
                off_stats = offline_state.get('off_stats') or {}
                BigWorld.callback(0.7, lambda: select_vehicle(
                    off_stats.get('currentVehInvID', 1)))
        except Exception:
            pass
        # The real onAccountBecomePlayer/onAccountShowGUI events fired
        # this time, so the stock messenger should have reconnected by
        # itself; only replay MPChat's forced lobby init if it did not.
        try:
            if not self.state._messenger_ready():
                self.state._ensure_messenger_lobby()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Patches
# ---------------------------------------------------------------------------

# One-item mailbox for the Avatar __init__ patch: while _enter creates
# the client-only Avatar, this holds (property dict, fake-server
# bridge) so the patched PlayerAvatar.__init__ can seed the typed
# entity properties during construction. BigWorld.createEntity's own
# property dict is silently ignored for the Avatar (real-client test
# 2026-10-18 #2: playerVehicleID/arenaBonusType still missing despite
# being passed), but attributes set during __init__ stick - and
# onEnterWorld, fired synchronously inside createEntity right after
# __init__, already reads playerVehicleID.
_PENDING_AVATAR = []


def _install_avatar_patch():
    from Avatar import PlayerAvatar
    if getattr(PlayerAvatar, '_mpBattlePatched', False):
        return
    orig_getattribute = PlayerAvatar.__getattribute__

    def _avatar_getattribute(self, name):
        if name in ('base', 'cell', 'server', 'bwProto'):
            try:
                if orig_getattribute(self, '_mpBattle'):
                    return orig_getattribute(self, 'fakeServer')
            except AttributeError:
                pass
        elif name == 'vehicle' and _BATTLE_ACTIVE[0]:
            # Read-only engine attribute, always None for a
            # client-only Avatar (assignment raises TypeError,
            # real-client test 2026-10-18 #11). Serve the vehicle
            # our bind emulation attached so stock Python code
            # (onVehicleChanged, input handler, GUI) sees the same
            # value a server-driven Avatar would have.
            bound = _AVATAR_BOUND_VEHICLE[0]
            if bound is not None:
                return bound
        return orig_getattribute(self, name)

    PlayerAvatar.__getattribute__ = _avatar_getattribute

    orig_init = PlayerAvatar.__init__

    def _seed_pending_props(self, props):
        for _key, _value in props.items():
            try:
                if not hasattr(self, _key):
                    setattr(self, _key, _value)
            except Exception:
                pass

    def _avatar_init(self):
        pending = _PENDING_AVATAR[0] if _PENDING_AVATAR else None
        if pending is not None:
            props, bridge = pending
            _seed_pending_props(self, props)
            try:
                self.fakeServer = bridge
                self._mpBattle = True
            except Exception:
                pass
        orig_init(self)
        if pending is not None:
            # In case the stock __init__ (or the engine) dropped any
            # of the seeded attributes, seed the missing ones again.
            _seed_pending_props(self, pending[0])
            # AvatarObserver.onEnterWorld (fired right after __init__
            # inside createEntity) needs filter.syncVector3 & co;
            # the engine-default filter of a client-only entity does
            # not have them, and stock onBecomePlayer only installs
            # BigWorld.AvatarFilter() later. Install it now,
            # unconditionally (real-client test 2026-10-18 #3:
            # "filter does not have method syncVector3").
            try:
                import BigWorld
                self.filter = BigWorld.AvatarFilter()
                try:
                    self.filter.enableLagDetection(True)
                except Exception:
                    pass
            except Exception:
                pass

    PlayerAvatar.__init__ = _avatar_init

    orig_become_player = getattr(PlayerAvatar, 'onBecomePlayer', None)

    def _avatar_become_player(self):
        # Stock onBecomePlayer does ``self.filter =
        # BigWorld.AvatarFilter()``, which would replace the filter
        # AvatarObserver already cached bound methods from in
        # onEnterWorld. Make the factory hand back the same instance
        # for our battle Avatar.
        try:
            is_battle = orig_getattribute(self, '_mpBattle')
        except AttributeError:
            is_battle = False
        if not is_battle:
            return orig_become_player(self)
        import BigWorld
        existing = getattr(self, 'filter', None)
        orig_factory = getattr(BigWorld, 'AvatarFilter', None)
        if existing is None or not callable(orig_factory):
            return orig_become_player(self)
        BigWorld.AvatarFilter = lambda: existing
        try:
            return orig_become_player(self)
        finally:
            BigWorld.AvatarFilter = orig_factory

    if orig_become_player is not None:
        PlayerAvatar.onBecomePlayer = _avatar_become_player

    # PlayerAvatar.__onSetOwnVehicleAuxPhysicsData forwards into
    # ownVehicle.filter.syncStabilisedYPR - same unsafe native sync
    # as syncGunAngles. Scope the filter proxy around the handler.
    # NOTE: the engine notifier set_ownVehicleAuxPhysicsData only
    # delegates to the private __onSetOwnVehicleAuxPhysicsData, and
    # __startVehicleVisual calls the PRIVATE name directly.
    # Real-client test 2026-10-18 #9 died right after startVisual
    # returned because only set_... was wrapped and the direct call
    # reached native syncStabilisedYPR unscoped. Wrap every name
    # that exists; double-scoping on the delegate path is balanced
    # append/remove and harmless.
    _aux_log_count = [0]

    def _make_aux_wrapper(orig_fn):
        def _set_aux_physics(self, prev):
            is_battle = _BATTLE_ACTIVE[0]
            if not is_battle:
                try:
                    is_battle = bool(orig_getattribute(self, '_mpBattle'))
                except AttributeError:
                    is_battle = False
            if not is_battle:
                return orig_fn(self, prev)
            logged = False
            if _aux_log_count[0] < 5:
                _aux_log_count[0] += 1
                logged = True
                _log('>> __onSetOwnVehicleAuxPhysicsData '
                     '(filter proxy scoped)')
                _diag_flush()
            vehicle = None
            try:
                import BigWorld
                vehicle = BigWorld.entity(
                    orig_getattribute(self, 'playerVehicleID'))
            except Exception:
                vehicle = None
            if vehicle is not None:
                _FILTER_SCOPED.append(vehicle)
            try:
                return orig_fn(self, prev)
            finally:
                if vehicle is not None:
                    _remove_scoped(vehicle)
                if logged:
                    _log('<< __onSetOwnVehicleAuxPhysicsData')
                    _diag_flush()

        _set_aux_physics._mpAuxWrapped = True
        _set_aux_physics._mpAuxOrigFunc = getattr(orig_fn, 'im_func',
                                                  orig_fn)
        return _set_aux_physics

    _aux_wrapped = []
    for _aux_name in ('_PlayerAvatar__onSetOwnVehicleAuxPhysicsData',
                      'set_ownVehicleAuxPhysicsData'):
        _aux_fn = getattr(PlayerAvatar, _aux_name, None)
        if _aux_fn is None:
            _log('aux physics wrap: %s not found' % (_aux_name,))
            continue
        if getattr(_aux_fn, '_mpAuxWrapped', False):
            _aux_wrapped.append(_aux_name)
            continue
        _aux_raw = getattr(_aux_fn, 'im_func', _aux_fn)
        _alias_wrap = None
        for _done_name in _aux_wrapped:
            _done_fn = getattr(PlayerAvatar, _done_name, None)
            if getattr(_done_fn, '_mpAuxOrigFunc', None) is _aux_raw:
                _alias_wrap = _done_fn
                break
        try:
            if _alias_wrap is not None:
                setattr(PlayerAvatar, _aux_name,
                        getattr(_alias_wrap, 'im_func', _alias_wrap))
                _log('aux physics wrap: %s is alias, reused wrapper'
                     % (_aux_name,))
            else:
                setattr(PlayerAvatar, _aux_name,
                        _make_aux_wrapper(_aux_fn))
                _log('aux physics wrap installed: %s' % (_aux_name,))
            _aux_wrapped.append(_aux_name)
        except Exception:
            _log('aux physics wrap FAILED (setattr): %s' % (_aux_name,))
            traceback.print_exc()
    if not _aux_wrapped:
        _log('aux physics wrap FAILED: no aux handler on PlayerAvatar')

    PlayerAvatar._mpBattlePatched = True


class _VehicleFilterSyncProxy(object):
    """Delegates a WGVehicleFilter except the retail-only sync calls.

    ``syncGunAngles``/``syncStabilisedYPR`` submit samples to the
    native interpolation filter a server-driven Vehicle has behind
    it; on a client-only Vehicle that native filter is null and the
    call crashes the client. Everything else delegates untouched.
    Used strictly inside the scoped stock entry points (see
    _FILTER_SCOPED): the appearance and native consumers must keep
    seeing the real filter object.
    """

    def __init__(self, vehicle_filter):
        object.__setattr__(self, '_vehicle_filter', vehicle_filter)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_vehicle_filter'),
                       name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, '_vehicle_filter'),
                name, value)

    _logged = [0]

    def syncGunAngles(self, yaw, pitch):
        if _VehicleFilterSyncProxy._logged[0] < 3:
            _VehicleFilterSyncProxy._logged[0] += 1
            _log('filter proxy: syncGunAngles swallowed')
            _diag_flush()
        return None

    def syncStabilisedYPR(self, yaw, pitch, roll):
        if _VehicleFilterSyncProxy._logged[0] < 3:
            _VehicleFilterSyncProxy._logged[0] += 1
            _log('filter proxy: syncStabilisedYPR swallowed')
            _diag_flush()
        return None


# True between battle enter and teardown. The filter-sync proxy must
# engage for every Vehicle in that window: detecting "our" vehicles
# via an attribute stamped at spawn proved unreliable (real-client
# test 2026-10-18 #8: _mpFakeServer was missing at startVisual time,
# the proxy never engaged and native filter.syncGunAngles killed
# the client inside Vehicle.__startWGPhysics). Offline there are no
# other battle vehicles, and the hangar never sets this flag.
_BATTLE_ACTIVE = [False]

# Set once the app loader has been asked for the BATTLE_LOADING gui
# space in the current battle. The app-loader state machine only
# allows Lobby -> BattleLoading -> Battle; see
# _install_app_loader_patch for the race this guards.
_BATTLE_LOADING_SHOWN = [False]

# Set by the SharedPage._populate wrap once the DAAPI battle page
# exists and listens for the BATTLE_LOADING toggle event; the
# app-loader wrapper defers the real showBattlePage until then
# (see _install_app_loader_patch).
_BATTLE_PAGE_POPULATED = [False]

# Set once the original showBattlePage has actually executed in
# the current battle; later showBattleLoading calls (arena-info
# invalidations) are swallowed instead of tripping the state
# machine's "can not be switched" error.
_BATTLE_PAGE_DONE = [False]

# [loader, ticks_left] while a showBattlePage call is deferred
# waiting for the battle page to populate.
_PENDING_BATTLE_PAGE = [None]

# True once SharedPage._populate is wrapped (the daapi module may
# not be importable until the SF battle packages load, so the
# wrap is retried lazily).
_BATTLE_PAGE_PATCHED = [False]

# The Vehicle entity our bind emulation attached the Avatar to.
# PlayerAvatar.vehicle is a read-only engine attribute and stays
# None for a client-only Avatar, so the patched
# PlayerAvatar.__getattribute__ serves this entity for Python
# code asking for .vehicle during a battle.
_AVATAR_BOUND_VEHICLE = [None]

# entity id -> _FakeServer bridge, for Vehicle base/cell routing
# without depending on attribute storage on engine entities.
_MP_VEHICLE_BRIDGES = {}

# Poses of the most recently torn-down battle, kept purely as
# refcount ballast: stock appearances/animators hold our matrix
# providers and may still evaluate them briefly while native
# teardown finishes after the entities are destroyed.
_RETIRED_POSES = []


def _pose_for_entity(entity_id):
    """Kinematic pose registered for a battle entity id, or None."""
    bridge = _MP_VEHICLE_BRIDGES.get(entity_id)
    if bridge is None:
        return None
    try:
        bc = bridge._bc
        vid = bc._entity_vid.get(entity_id)
        return bc._poses.get(vid) if vid else None
    except Exception:
        return None


# Vehicle entities currently inside a stock call that must see the
# sync-swallowing filter proxy (see _install_vehicle_patch).
_FILTER_SCOPED = []


def _is_filter_scoped(vehicle):
    for scoped in _FILTER_SCOPED:
        if scoped is vehicle:
            return True
    return False


def _remove_scoped(vehicle):
    for index, scoped in enumerate(_FILTER_SCOPED):
        if scoped is vehicle:
            del _FILTER_SCOPED[index]
            return


def _install_vehicle_patch():
    from Vehicle import Vehicle
    if getattr(Vehicle, '_mpBattlePatched', False):
        return
    orig_getattribute = Vehicle.__getattribute__

    def _is_mp_vehicle(vehicle):
        if _BATTLE_ACTIVE[0]:
            return True
        try:
            if orig_getattribute(vehicle, 'id') in _MP_VEHICLE_BRIDGES:
                return True
        except Exception:
            pass
        try:
            orig_getattribute(vehicle, '_mpFakeServer')
            return True
        except AttributeError:
            return False

    def _pose_for(vehicle):
        try:
            entity_id = orig_getattribute(vehicle, 'id')
            bridge = _MP_VEHICLE_BRIDGES.get(entity_id)
            if bridge is None:
                return None
            bc = bridge._bc
            vid = bc._entity_vid.get(entity_id)
            return bc._poses.get(vid) if vid else None
        except Exception:
            return None

    def _vehicle_getattribute(self, name):
        if name in ('position', 'matrix'):
            # Pose overlay: the native entity transform never moves
            # on client-only vehicles, so Python consumers asking
            # for the vehicle matrix/position (aiming systems,
            # minimap, markers, sounds) would all see the spawn
            # point.  Serve the kinematic pose instead.
            pose = _pose_for(self)
            if pose is not None:
                if name == 'matrix':
                    return pose.provider
                try:
                    import Math
                    return Math.Vector3(pose.x, pose.y, pose.z)
                except Exception:
                    pass
        if name in ('base', 'cell'):
            try:
                bridge = _MP_VEHICLE_BRIDGES.get(
                    orig_getattribute(self, 'id'))
            except Exception:
                bridge = None
            if bridge is not None:
                return bridge
            try:
                return orig_getattribute(self, '_mpFakeServer')
            except AttributeError:
                pass
        if name == 'filter' and _is_filter_scoped(self):
            # Scoped only: an always-on proxy (477e443) broke
            # startVisual - the engine swaps the native filter
            # object (DumbFilter -> WGVehicleFilter) after entity
            # creation, so the appearance captured a proxy stuck
            # delegating to a stale DumbFilter and died on the
            # first WGVehicleFilter attribute (test #18).  Stock
            # lifecycle rebinds that read filter matrices are
            # corrected at their call sites instead (see
            # _install_camera_bind_patch).
            return _VehicleFilterSyncProxy(
                orig_getattribute(self, 'filter'))
        return orig_getattribute(self, name)

    Vehicle.__getattribute__ = _vehicle_getattribute

    # Vehicle.__startWGPhysics and set_gunAnglesPacked call
    # filter.syncGunAngles(), which natively asserts/crashes on a
    # client-only Vehicle (no retail interpolation filter behind it -
    # real-client death inside Vehicle.startVisual, 2026-10-18 #4).
    # Scope a filter proxy that swallows just those syncs around both
    # entry points, for our battle vehicles only.
    orig_start_physics = getattr(Vehicle, '_Vehicle__startWGPhysics',
                                  None)
    if orig_start_physics is not None:
        def _start_wg_physics(self):
            if not _is_mp_vehicle(self):
                return orig_start_physics(self)
            _log('>> __startWGPhysics id=%s (filter proxy scoped)'
                 % (getattr(self, 'id', None),))
            _diag_flush()
            _FILTER_SCOPED.append(self)
            try:
                return orig_start_physics(self)
            finally:
                _remove_scoped(self)
                _log('<< __startWGPhysics id=%s'
                     % (getattr(self, 'id', None),))
                _diag_flush()

        Vehicle._Vehicle__startWGPhysics = _start_wg_physics

    orig_set_gun = getattr(Vehicle, 'set_gunAnglesPacked', None)
    if orig_set_gun is not None:
        def _set_gun_angles_packed(self, prev):
            if not _is_mp_vehicle(self):
                return orig_set_gun(self, prev)
            _FILTER_SCOPED.append(self)
            try:
                return orig_set_gun(self, prev)
            finally:
                _remove_scoped(self)

        Vehicle.set_gunAnglesPacked = _set_gun_angles_packed

    Vehicle._mpBattlePatched = True


def _install_camera_bind_patch():
    # Two stock rebind points read the native (dead) vehicle
    # filter matrices: SteadyVehicleMatrixCalculator.relinkSources
    # (input-handler start / siege changes - feeds the sniper and
    # arcade aiming systems) and ConsistentMatrices'
    # __linkOwnVehicle (own-vehicle camera matrix).  Wrap both:
    # run the original, then re-point the specific providers at
    # the live kinematic pose for our battle vehicles.
    # Correcting synchronously inside the stock calls leaves no
    # window where, e.g., the sniper aiming system computes its
    # initial angles from the spawn pose (real-client test #17).
    steady_cls = None
    try:
        from AvatarInputHandler.AimingSystems.steady_vehicle_matrix \
            import SteadyVehicleMatrixCalculator as steady_cls
    except Exception:
        steady_cls = None
    if steady_cls is not None and not getattr(
            steady_cls, '_mpBattlePatched', False):
        orig_relink = steady_cls.relinkSources

        def _relink_sources(self):
            orig_relink(self)
            try:
                import BigWorld
                player = BigWorld.player()
                vehicle = player.getVehicleAttached() \
                    if player is not None else None
                pose = _pose_for_entity(vehicle.id) \
                    if vehicle is not None else None
                if pose is not None:
                    out = self.outputMProv
                    out.rotationSrc = pose.provider
                    out.translationSrc = pose.provider
                    self.stabilisedMProv.target = pose.provider
            except Exception:
                _log('steady relink pose override failed:')
                traceback.print_exc()

        steady_cls.relinkSources = _relink_sources
        steady_cls._mpBattlePatched = True

    consistent_cls = None
    try:
        from AvatarPositionControl import \
            ConsistentMatrices as consistent_cls
    except Exception:
        consistent_cls = None
    if consistent_cls is not None and not getattr(
            consistent_cls, '_mpBattlePatched', False):
        orig_link = getattr(
            consistent_cls, '_ConsistentMatrices__linkOwnVehicle',
            None)
        if orig_link is not None:
            def _link_own_vehicle(self, vehicle):
                orig_link(self, vehicle)
                try:
                    pose = _pose_for_entity(
                        getattr(vehicle, 'id', None))
                    if pose is not None:
                        self.ownVehicleMatrix.target = pose.provider
                except Exception:
                    _log('linkOwnVehicle pose override failed:')
                    traceback.print_exc()

            consistent_cls._ConsistentMatrices__linkOwnVehicle = \
                _link_own_vehicle
            consistent_cls._mpBattlePatched = True

    _log('camera bind patch: steady=%s consistent=%s'
         % (steady_cls is not None, consistent_cls is not None))


def _install_account_patch():
    from Account import PlayerAccount
    if getattr(PlayerAccount, '_mpBattlePatched', False):
        return
    # Battle teardown promotes the hangar Account FIRST; the
    # demoted Avatar's onBecomeNonPlayer then destroys the
    # battle GUI while BigWorld.player() already returns the
    # Account.  Stock teardown code reads PlayerAvatar-only
    # attributes off player() (positionControl in
    # StrategicCamera.disable, arena in PostMortemControlMode.
    # disable, consistentMatrices right after it,
    # observedVehicleID from hangar effects), and the first
    # AttributeError aborts Avatar.__destroyGUI - which is
    # called unwrapped, so everything after it in
    # onBecomeNonPlayer (session stop, stopVisual loop, ...)
    # never runs (real-client tests #19-20: half-stopped
    # session, client later died).  Serve harmless sentinels
    # for exactly that attribute family.
    class _DummyEvent(object):

        def __iadd__(self, handler):
            return self

        def __isub__(self, handler):
            return self

        def __call__(self, *args, **kwargs):
            pass

    class _DummyMatrices(object):

        def __init__(self):
            self.onVehicleMatrixBindingChanged = _DummyEvent()

    _teardown_sentinels = {
        'arena': None,
        'positionControl': None,
        'inputHandler': None,
        'observedVehicleID': 0,
        'consistentMatrices': _DummyMatrices(),
    }
    for _name, _value in _teardown_sentinels.iteritems():
        if not hasattr(PlayerAccount, _name):
            setattr(PlayerAccount, _name, _value)
    orig_become_player = PlayerAccount.onBecomePlayer

    def _on_become_player(self):
        # Stock onBecomePlayer starts with BigWorld.clearAllSpaces(),
        # which would retire our client-only Account living in its own
        # created space. Suppress exactly that one call (battle space
        # is released explicitly by the battle teardown).
        import BigWorld
        # Account.onBecomeNonPlayer (fired when the battle Avatar was
        # promoted) detached every repository subsystem via
        # setAccount(None)/setProxy(None). Stock onBecomePlayer assumes
        # a fresh Account whose __init__ bound them; our Account is
        # reused, so rebind exactly like __init__ does, otherwise the
        # subsystems' onAccountBecomePlayer run against account=None.
        for _name in ('syncData', 'inventory', 'stats', 'questProgress',
                      'shop', 'dossierCache', 'battleResultsCache',
                      'gMap', 'goodies', 'vehicleRotation', 'recycleBin',
                      'ranked', 'badges', 'newYear'):
            _sub = getattr(self, _name, None)
            if _sub is not None:
                try:
                    _sub.setAccount(self)
                except Exception:
                    pass
        try:
            self.intUserSettings.setProxy(self, self.syncData)
        except Exception:
            pass
        try:
            self.prebattleInvitations.setProxy(self)
        except Exception:
            pass
        orig_clear = getattr(BigWorld, 'clearAllSpaces', None)
        if callable(orig_clear):
            BigWorld.clearAllSpaces = lambda: None
        try:
            result = orig_become_player(self)
        finally:
            if callable(orig_clear):
                BigWorld.clearAllSpaces = orig_clear
        # syncData.onAccountBecomePlayer restarted a server sync that
        # can never complete offline. Force the synced state exactly
        # like Offline.Manager does at hangar bootstrap.
        try:
            import AccountCommands
            sync_data = self.syncData
            sync_data._AccountSyncData__ignore = False
            sync_data._AccountSyncData__isSynchronized = True
            subscribers = list(getattr(
                sync_data, '_AccountSyncData__subscribers', []))
            for subscriber in subscribers:
                try:
                    subscriber(AccountCommands.RES_CACHE)
                except Exception:
                    pass
            sync_data._AccountSyncData__subscribers = []
        except Exception:
            pass
        return result

    PlayerAccount.onBecomePlayer = _on_become_player

    # Teardown re-promotes the Account BEFORE the battle Avatar and
    # Vehicles are destroyed, so stock cleanup that asks
    # BigWorld.player() for Avatar-only members lands on the Account
    # ("'PlayerAccount' object has no attribute 'getVehicleAttached'"
    # etc., real-client test 2026-10-18 #10). Those AttributeErrors
    # aborted Avatar.__destroyGUI midway. Inert stubs let the stock
    # cleanup run to completion; a retail Account never receives
    # these calls, so the stubs are dead weight in the hangar.
    if not hasattr(PlayerAccount, 'getVehicleAttached'):
        def _account_get_vehicle_attached(self):
            return None

        PlayerAccount.getVehicleAttached = (
            _account_get_vehicle_attached)
    if not hasattr(PlayerAccount, 'vehicleTypeDescriptor'):
        PlayerAccount.vehicleTypeDescriptor = None
    if not hasattr(PlayerAccount, 'vehicle_onLeaveWorld'):
        def _account_vehicle_on_leave_world(self, vehicle):
            # Mirror the essential part of
            # Avatar.vehicle_onLeaveWorld: Vehicle.onLeaveWorld
            # asserts the visual is stopped right after this call.
            try:
                if getattr(vehicle, 'isStarted', False):
                    vehicle.stopVisual(True)
            except Exception:
                pass

        PlayerAccount.vehicle_onLeaveWorld = (
            _account_vehicle_on_leave_world)
    # More of the same, surfaced by test #12 teardown (harmless but
    # they abort Avatar.__destroyGUI even earlier).
    if not hasattr(PlayerAccount, 'playerVehicleID'):
        PlayerAccount.playerVehicleID = 0
    if not hasattr(PlayerAccount, 'onGunShotChanged'):
        class _NoopEvent(object):
            def __iadd__(self, handler):
                return self

            def __isub__(self, handler):
                return self

            def __call__(self, *args, **kwargs):
                return None

            def clear(self):
                pass

        PlayerAccount.onGunShotChanged = _NoopEvent()

    PlayerAccount._mpBattlePatched = True


def _ensure_battle_page_patch():
    # Wrap SharedPage._populate to learn when the DAAPI battle
    # page exists (and therefore listens for the BATTLE_LOADING
    # toggle event). Imported lazily: the battle daapi modules
    # may not be importable before the SF battle packages load,
    # so this is retried from battle enter and the deferral poll.
    if _BATTLE_PAGE_PATCHED[0]:
        return
    try:
        from gui.Scaleform.daapi.view.battle.shared.page import \
            SharedPage
    except Exception:
        return
    orig_populate = getattr(SharedPage, '_populate', None)
    if orig_populate is None or getattr(orig_populate,
                                         '_mpBattlePopulate', False):
        _BATTLE_PAGE_PATCHED[0] = True
        return

    def _battle_page_populate(self):
        result = orig_populate(self)
        if _BATTLE_ACTIVE[0] and not _BATTLE_PAGE_POPULATED[0]:
            _BATTLE_PAGE_POPULATED[0] = True
            _log('battle page populated (%s)'
                 % (type(self).__name__,))
            _diag_flush()
        return result

    _battle_page_populate._mpBattlePopulate = True
    try:
        SharedPage._populate = _battle_page_populate
        _BATTLE_PAGE_PATCHED[0] = True
        _log('battle page populate wrap installed')
    except Exception:
        _log('battle page populate wrap FAILED:')
        traceback.print_exc()


def _disable_gui_background():
    # BattleLoading._populate re-enables the GUI background right
    # AFTER addArenaCtrl replays arenaLoadCompleted into it (our
    # space-load listener finished before the page populated), so
    # the map backdrop stays over the 3D scene forever: the view's
    # own wg_enableGUIBackground(False, True) already fired into
    # the void (real-client test 2026-10-18 #12). Repeat the exact
    # call once the battle page is actually shown.
    try:
        import BigWorld
        BigWorld.wg_enableGUIBackground(False, True)
        _log('GUI background disabled (post showBattlePage)')
        _diag_flush()
    except Exception:
        _log('wg_enableGUIBackground failed:')
        traceback.print_exc()


def _poll_pending_battle_page(orig_show_page):
    pending = _PENDING_BATTLE_PAGE[0]
    if pending is None:
        return
    loader, ticks_left = pending
    if not _BATTLE_ACTIVE[0]:
        _PENDING_BATTLE_PAGE[0] = None
        return
    _ensure_battle_page_patch()
    if _BATTLE_PAGE_POPULATED[0] or ticks_left <= 0:
        _PENDING_BATTLE_PAGE[0] = None
        if _BATTLE_PAGE_POPULATED[0]:
            _log('battle page ready - executing deferred '
                 'showBattlePage')
        else:
            _log('showBattlePage deferral timed out - showing '
                 'battle page anyway')
        _diag_flush()
        try:
            orig_show_page(loader)
            _BATTLE_PAGE_DONE[0] = True
            _disable_gui_background()
        except Exception:
            _log('deferred showBattlePage failed:')
            traceback.print_exc()
        return
    pending[1] = ticks_left - 1
    try:
        import BigWorld
        BigWorld.callback(
            0.1, lambda: _poll_pending_battle_page(orig_show_page))
    except Exception:
        _PENDING_BATTLE_PAGE[0] = None


def _install_app_loader_patch():
    # The stock app-loader state machine only allows
    # Lobby -> BattleLoading -> Battle. Retail reaches
    # ArenaLoadController.arenaLoadCompleted (-> showBattlePage)
    # seconds after showBattleLoading because a server-driven space
    # loads slowly. Our client-only space is already loaded and
    # world drawing is still enabled from the hangar, so the
    # ArenaSpaceLoadListener completes on its first poll and
    # showBattlePage races AHEAD of showBattleLoading; the state
    # machine rejects Lobby -> Battle ("State can not be switched
    # (LobbyState(), guiSpaceID = 6)") and the one-shot listener
    # never retries - the battle loading screen stays up forever
    # (real-client test 2026-10-18 #10). Track the loading call and
    # insert it first when the page call wins the race. The loader
    # singleton uses __slots__, so patch its class.
    try:
        from gui.app_loader import g_appLoader
    except Exception:
        _log('app loader patch: g_appLoader import failed:')
        traceback.print_exc()
        return
    loader_cls = type(g_appLoader)
    if getattr(loader_cls, '_mpBattlePatched', False):
        return
    orig_show_loading = getattr(loader_cls, 'showBattleLoading', None)
    orig_show_page = getattr(loader_cls, 'showBattlePage', None)
    if orig_show_loading is None or orig_show_page is None:
        _log('app loader patch: showBattleLoading/showBattlePage '
             'missing on %s' % (loader_cls,))
        return

    def _show_battle_loading(self):
        if _BATTLE_ACTIVE[0]:
            if _BATTLE_PAGE_DONE[0]:
                _log('appLoader.showBattleLoading swallowed '
                     '(battle page already shown)')
                _diag_flush()
                return None
            _BATTLE_LOADING_SHOWN[0] = True
            _log('appLoader.showBattleLoading')
            _diag_flush()
        return orig_show_loading(self)

    def _show_battle_page(self):
        if not _BATTLE_ACTIVE[0]:
            return orig_show_page(self)
        _log('appLoader.showBattlePage')
        _diag_flush()
        if not _BATTLE_LOADING_SHOWN[0]:
            _log('showBattlePage raced ahead of showBattleLoading'
                 ' - entering battle loading state first')
            _diag_flush()
            try:
                _show_battle_loading(self)
            except Exception:
                _log('pre-emptive showBattleLoading failed:')
                traceback.print_exc()
        if _BATTLE_PAGE_POPULATED[0] or _BATTLE_PAGE_DONE[0]:
            result = orig_show_page(self)
            _BATTLE_PAGE_DONE[0] = True
            _disable_gui_background()
            return result
        # Exiting BattleLoadingState in this same call stack loses
        # the battle page: BattleLoadingState.showGUI only calls
        # loadBattlePage once the SF battle app is INITIALIZED
        # (the loader re-invokes showGUI while the state persists)
        # and the BATTLE_LOADING toggle events are fire-and-forget,
        # so a page that has not populated yet misses both. Retail
        # spends seconds inside BattleLoadingState; stay there
        # until the page exists (real-client test 2026-10-18 #11
        # showed only the map, no HUD, no ESC menu).
        if _PENDING_BATTLE_PAGE[0] is None:
            _PENDING_BATTLE_PAGE[0] = [self, 50]
            _log('showBattlePage deferred until battle page '
                 'populates')
            _diag_flush()
            _poll_pending_battle_page(orig_show_page)
        return None

    try:
        loader_cls.showBattleLoading = _show_battle_loading
        loader_cls.showBattlePage = _show_battle_page
        loader_cls._mpBattlePatched = True
        _log('app loader patch installed')
        _ensure_battle_page_patch()
    except Exception:
        _log('app loader patch FAILED:')
        traceback.print_exc()


def _diag_wrap(cls, attr_name, label):
    orig = getattr(cls, attr_name, None)
    if orig is None:
        _log('diag wrap FAILED (missing attr): %s' % (label,))
        return
    if getattr(orig, '_mpBattleDiag', False):
        return

    def _wrapped(self, *args, **kwargs):
        suffix = ''
        try:
            entity_id = getattr(self, 'id', None)
            if isinstance(entity_id, (int, long)):
                suffix = ' id=%s' % (entity_id,)
        except Exception:
            suffix = ''
        _log('>> %s%s' % (label, suffix))
        _diag_flush()
        try:
            return orig(self, *args, **kwargs)
        finally:
            _log('<< %s%s' % (label, suffix))
            _diag_flush()

    _wrapped._mpBattleDiag = True
    try:
        setattr(cls, attr_name, _wrapped)
        _log('diag wrap ok: %s' % (label,))
    except Exception:
        _log('diag wrap FAILED (setattr): %s' % (label,))


def _diag_flush():
    # python.log is force-flushed only once a second by
    # Offline.Manager; a native death could eat the last breadcrumb.
    try:
        import sys
        sys.stdout.flush()
    except Exception:
        pass
    try:
        import BigWorld
        flush = getattr(BigWorld, 'flushLog', None)
        if callable(flush):
            flush()
    except Exception:
        pass


def _install_diag_patches():
    """Breadcrumb-log the stock battle-init sequence.

    The real client died natively somewhere inside the init-completed
    cascade (startGUI -> input handler -> vehicle visuals) with no
    Python traceback; these enter/exit logs pinpoint the fatal call.
    They only ever fire in battle, once per battle.
    """
    try:
        from Avatar import PlayerAvatar
        _diag_wrap(PlayerAvatar, '_PlayerAvatar__onInitStepCompleted',
                   'Avatar.__onInitStepCompleted')
        _diag_wrap(PlayerAvatar, '_PlayerAvatar__startGUI',
                   'Avatar.__startGUI')
        _diag_wrap(PlayerAvatar, 'vehicle_onEnterWorld',
                   'Avatar.vehicle_onEnterWorld')
        _diag_wrap(PlayerAvatar, '_PlayerAvatar__startVehicleVisual',
                   'Avatar.__startVehicleVisual')
    except Exception:
        pass
    try:
        from gui.battle_control.controllers.arena_load_ctrl import \
            ArenaLoadController
        _diag_wrap(ArenaLoadController, 'spaceLoadStarted',
                   'ArenaLoadController.spaceLoadStarted')
        _diag_wrap(ArenaLoadController, 'spaceLoadCompleted',
                   'ArenaLoadController.spaceLoadCompleted')
        _diag_wrap(ArenaLoadController, 'arenaLoadCompleted',
                   'ArenaLoadController.arenaLoadCompleted')
    except Exception:
        pass
    try:
        from Vehicle import Vehicle
        _orig_start_visual = getattr(Vehicle, 'startVisual', None)
        if _orig_start_visual is not None and not getattr(
                _orig_start_visual, '_mpBattleDiag', False):
            def _start_visual(self, _orig=_orig_start_visual):
                _log('>> Vehicle.startVisual id=%s player=%s'
                     % (getattr(self, 'id', None),
                        getattr(self, 'isPlayerVehicle', None)))
                _diag_flush()
                try:
                    return _orig(self)
                finally:
                    _log('<< Vehicle.startVisual id=%s'
                         % (getattr(self, 'id', None),))
                    _diag_flush()

            _start_visual._mpBattleDiag = True
            Vehicle.startVisual = _start_visual
    except Exception:
        pass
    try:
        from AvatarInputHandler import AvatarInputHandler
        _diag_wrap(AvatarInputHandler, 'start',
                   'AvatarInputHandler.start')
    except Exception:
        pass
    try:
        from AvatarInputHandler.control_modes import ArcadeControlMode
        _diag_wrap(ArcadeControlMode, 'enable',
                   'ArcadeControlMode.enable')
    except Exception:
        pass
    # Inside Vehicle.startVisual: the client died natively somewhere
    # in the appearance chain, so breadcrumb its stages.
    try:
        from Vehicle import Vehicle as _DiagVehicle
        _diag_wrap(_DiagVehicle, 'onEnterWorld', 'Vehicle.onEnterWorld')
        _diag_wrap(_DiagVehicle, 'show', 'Vehicle.show')
        _diag_wrap(_DiagVehicle, 'set_publicStateModifiers',
                   'Vehicle.set_publicStateModifiers')
        _diag_wrap(_DiagVehicle, 'set_damageStickers',
                   'Vehicle.set_damageStickers')
    except Exception:
        pass
    try:
        from vehicle_systems.CompoundAppearance import CompoundAppearance
        for _method in ('setVehicle', 'activate', 'changeEngineMode',
                        'onVehicleHealthChanged', 'setupGunMatrixTargets',
                        'start'):
            _diag_wrap(CompoundAppearance, _method,
                       'CompoundAppearance.' + _method)
    except Exception:
        pass
    try:
        from vehicle_systems import appearance_cache
        _orig_get_appearance = appearance_cache.getAppearance
        if not getattr(_orig_get_appearance, '_mpBattleDiag', False):
            def _get_appearance(vId, resourceRefs,
                                _orig=_orig_get_appearance):
                _log('>> appearance_cache.getAppearance id=%s refs=%s'
                     % (vId, len(resourceRefs)
                        if resourceRefs is not None else None))
                try:
                    return _orig(vId, resourceRefs)
                finally:
                    _log('<< appearance_cache.getAppearance id=%s'
                         % (vId,))

            _get_appearance._mpBattleDiag = True
            appearance_cache.getAppearance = _get_appearance
    except Exception:
        pass
    # The tail of startVisual after set_damageStickers: trigger
    # activation, then session-provider vehicle registration.
    try:
        import TriggersManager
        _diag_wrap(TriggersManager.TriggersManager, 'activateTrigger',
                   'TriggersManager.activateTrigger')
    except Exception:
        _log('diag wrap FAILED (import): TriggersManager')
        traceback.print_exc()
    try:
        from gui.battle_control.battle_session import \
            BattleSessionProvider
        _diag_wrap(BattleSessionProvider, 'startVehicleVisual',
                   'BattleSessionProvider.startVehicleVisual')
    except Exception:
        _log('diag wrap FAILED (import): BattleSessionProvider')
        traceback.print_exc()
    try:
        from gui.battle_control.controllers.feedback_adaptor import \
            BattleFeedbackAdaptor
        _diag_wrap(BattleFeedbackAdaptor, 'startVehicleVisual',
                   'FeedbackAdaptor.startVehicleVisual')
    except Exception:
        _log('diag wrap FAILED (import): BattleFeedbackAdaptor')
        traceback.print_exc()


def install(state):
    """Create the battle client and install the entity patches.

    Called by Offline.MPTraining.install (which MPChat drives at account
    ready).  The patches are inert until a battle actually starts:
    mailbox redirection only applies to entities MPBattle created and
    flagged, and the Account wrapper only matters when the real
    ``onBecomePlayer`` fires on return from a battle.
    """
    client = _BattleClient(state)
    state.battle = client
    _install_avatar_patch()
    _install_vehicle_patch()
    _install_camera_bind_patch()
    _install_account_patch()
    _install_app_loader_patch()
    _install_diag_patches()
    _log('battle client patches installed')
    return client
