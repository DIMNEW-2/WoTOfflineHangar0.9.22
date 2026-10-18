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

_PREBATTLE_SECONDS = 10
_LOAD_TIMEOUT = 120.0
_SAMPLE_PERIOD = 0.1
_POSE_HEARTBEAT = 0.5
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
            if isinstance(pos, (list, tuple)) and len(pos) == 3:
                relay['p'] = [float(pos[0]), float(pos[1]), float(pos[2])]
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
        self._gun_limits = None
        self._pose_unsupported = False
        self._spawn_deadline = 0.0
        self._shoot_logged = False

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
        try:
            vehicle.filter.notifyInputKeysDown(int(msg.get('m', 0) or 0),
                                               int(msg.get('r', 0) or 0))
        except Exception:
            pass
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
        pos = msg.get('p')
        if pos and not self._pose_unsupported:
            try:
                cur = vehicle.position
                dx = float(pos[0]) - float(cur[0])
                dz = float(pos[2]) - float(cur[2])
                if dx * dx + dz * dz > 16.0:
                    import Math
                    vehicle.position = Math.Vector3(float(pos[0]),
                                                    float(pos[1]),
                                                    float(pos[2]))
                    _log('pose snap for vid %s (drift %.1fm)'
                         % (vid, math.sqrt(dx * dx + dz * dz)))
            except Exception:
                self._pose_unsupported = True
                _log('entity.position is not writable - pose snap disabled')

    def _ev_left(self, msg):
        entity_id = self._vehicles.get(int(msg.get('vid', 0) or 0))
        if not entity_id:
            return
        import BigWorld
        vehicle = BigWorld.entity(entity_id)
        if vehicle is not None:
            try:
                vehicle.filter.notifyInputKeysDown(0, 0)
            except Exception:
                pass

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

        self._space_id = BigWorld.createSpace()
        self._mapping_id = BigWorld.addSpaceGeometryMapping(
            self._space_id, None, 'spaces/' + arena_type.geometryName)
        _log('entering battle: arena=%s (%s) space=%s team=%s spawn=%s'
             % (arena_type_id, arena_type.geometryName, self._space_id,
                self._my_team, spawn))

        self._avatar_id = BigWorld.createEntity(
            'Avatar', self._space_id, 0, (spawn[0], 0.0, spawn[1]),
            (0.0, 0.0, yaw), {})
        avatar = BigWorld.entities[self._avatar_id]
        self._avatar = avatar
        avatar.arenaUniqueID = int(msg.get('arenaUniqueID', 0) or 0)
        avatar.arenaTypeID = arena_type_id
        avatar.arenaBonusType = C.ARENA_BONUS_TYPE.TRAINING
        avatar.arenaGuiType = C.ARENA_GUI_TYPE.TRAINING
        avatar.arenaExtraData = {}
        avatar.weatherPresetID = 0
        avatar.name = _b(me.get('nick', u''))
        avatar.team = self._my_team
        avatar.fakeServer = self._bridge
        avatar._mpBattle = True

        self.active = True
        self._ready_sent = False
        # Real engine promotion (goes through the Offline.Manager
        # BigWorld.player dispatch): PlayerAvatar.onBecomePlayer creates
        # the ClientArena and starts the stock battle GUI session.
        BigWorld.player(avatar)
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
        points = []
        try:
            points = arena_type.teamSpawnPoints[team - 1]
        except Exception:
            points = []
        if not points:
            center = self._arena_center(arena_type)
            return (center[0], center[1])
        point = points[int(player.get('spawn', 0)) % len(points)]
        return (float(point[0]), float(point[1]))

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
                (spawn[0], y + 1.0, spawn[1]), (0.0, 0.0, yaw), props)
            self._vehicles[vid] = entity_id
            try:
                vehicle = BigWorld.entities[entity_id]
                vehicle._mpFakeServer = self._bridge
            except Exception:
                pass
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
        prev = avatar.playerVehicleID
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

    def _publish_period(self, period, length):
        if self._avatar is None:
            return
        import BigWorld
        C = _const()
        end_time = float(BigWorld.serverTime()) + float(length) \
            if length > 0 else 0.0
        self._avatar.updateArena(
            C.ARENA_UPDATE.PERIOD,
            _pack_period(period, end_time, length))
        _log('arena period -> %s (%.0fs)' % (period, length))

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

    def _on_move_flags(self, flags):
        self._cur_input = _dirs_from_flags(flags)

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
        own_id = self._vehicles.get(
            int(getattr(self.state, 'my_vid', 0) or 0))
        if own_id and now - self._last_pose_sent >= _POSE_HEARTBEAT:
            vehicle = BigWorld.entity(own_id)
            if vehicle is not None:
                try:
                    pos = vehicle.position
                    pose = [float(pos[0]), float(pos[1]), float(pos[2])]
                except Exception:
                    pose = None
        current = (self._cur_input[0], self._cur_input[1], packed)
        if current != self._last_sent or pose is not None:
            msg = {'cmd': 'input', 'm': current[0], 'r': current[1],
                   'g': packed}
            if pose is not None:
                msg['p'] = pose
                self._last_pose_sent = now
            self._send(**msg)
            self._last_sent = current

    # -- teardown -----------------------------------------------------------

    def _teardown(self):
        if self._tearing_down:
            return
        self._tearing_down = True
        import BigWorld
        _log('tearing down battle, returning to hangar')
        # Vehicles first, while the avatar is still the player.
        for entity_id in list(self._vehicles.itervalues()):
            try:
                BigWorld.destroyEntity(entity_id)
            except Exception:
                pass
        self._vehicles = {}
        if self._avatar_id:
            try:
                BigWorld.destroyEntity(self._avatar_id)
            except Exception:
                pass
        self._avatar = None
        self._avatar_id = 0
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
        # Promote the hangar Account again (real setter via dispatch).
        account = getattr(self.state, 'player', None)
        if account is not None:
            try:
                BigWorld.player(account)
            except Exception:
                _log('account re-promotion failed:')
                traceback.print_exc()
        try:
            from PlayerEvents import g_playerEvents
            if getattr(g_playerEvents, 'isPlayerEntityChanging', False):
                g_playerEvents.isPlayerEntityChanging = False
        except Exception:
            pass
        try:
            from gui.app_loader import g_appLoader
            g_appLoader.showLobby()
        except Exception:
            pass
        self._tearing_down = False
        _log('back in hangar')


# ---------------------------------------------------------------------------
# Patches
# ---------------------------------------------------------------------------

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
        return orig_getattribute(self, name)

    PlayerAvatar.__getattribute__ = _avatar_getattribute
    PlayerAvatar._mpBattlePatched = True


def _install_vehicle_patch():
    from Vehicle import Vehicle
    if getattr(Vehicle, '_mpBattlePatched', False):
        return
    orig_getattribute = Vehicle.__getattribute__

    def _vehicle_getattribute(self, name):
        if name in ('base', 'cell'):
            try:
                return orig_getattribute(self, '_mpFakeServer')
            except AttributeError:
                pass
        return orig_getattribute(self, name)

    Vehicle.__getattribute__ = _vehicle_getattribute
    Vehicle._mpBattlePatched = True


def _install_account_patch():
    from Account import PlayerAccount
    if getattr(PlayerAccount, '_mpBattlePatched', False):
        return
    orig_become_player = PlayerAccount.onBecomePlayer

    def _on_become_player(self):
        # Stock onBecomePlayer starts with BigWorld.clearAllSpaces(),
        # which would retire our client-only Account living in its own
        # created space. Suppress exactly that one call (battle space
        # is released explicitly by the battle teardown).
        import BigWorld
        orig_clear = getattr(BigWorld, 'clearAllSpaces', None)
        if callable(orig_clear):
            BigWorld.clearAllSpaces = lambda: None
        try:
            return orig_become_player(self)
        finally:
            if callable(orig_clear):
                BigWorld.clearAllSpaces = orig_clear

    PlayerAccount.onBecomePlayer = _on_become_player
    PlayerAccount._mpBattlePatched = True


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
    _install_account_patch()
    _log('battle client patches installed')
    return client
