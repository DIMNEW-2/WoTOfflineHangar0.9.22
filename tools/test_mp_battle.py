# -*- coding: utf-8 -*-
"""
Logic test for src/Offline/MPBattle.py (LAN battles).

Runs under Python 2.7 with stubbed game modules. Covers the pure
helpers (arena tuple packing, spawn assignment, movement flags), the
host-side battle state machine with fake connections, the fake-server
mailbox bridge, and the entity patches (mailbox redirection +
clearAllSpaces suppression). The BigWorld scene-assembly itself can
only be validated on the real client.

Usage:  /tmp/py27/bin/python tools/test_mp_battle.py
"""
import cPickle
import imp
import json
import math
import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# --- zlib identity stub (sandbox py27 lacks the C module) ------------------
zlib_stub = types.ModuleType('zlib')
zlib_stub.compress = lambda data, level=-1: data
zlib_stub.decompress = lambda data: data
sys.modules['zlib'] = zlib_stub


def _ns(**fields):
    obj = types.ModuleType('ns')
    obj.__dict__.update(fields)
    return obj


# --- constants stub (real 0.9.21 values) -------------------------------------
C = types.ModuleType('constants')
C.ARENA_PERIOD = _ns(IDLE=0, WAITING=1, PREBATTLE=2, BATTLE=3, AFTERBATTLE=4)
C.ARENA_UPDATE = _ns(VEHICLE_LIST=1, VEHICLE_ADDED=2, PERIOD=3,
                     VEHICLE_STATISTICS=5, VEHICLE_KILLED=6, AVATAR_READY=7,
                     VEHICLE_UPDATED=11)
C.ARENA_BONUS_TYPE = _ns(UNKNOWN=0, TRAINING=2)
C.ARENA_GUI_TYPE = _ns(UNKNOWN=0, RANDOM=1, TRAINING=2)
C.VEHICLE_PHYSICS_MODE = _ns(STANDARD=0, DETAILED=1)
C.VEHICLE_SIEGE_STATE = _ns(DISABLED=0, SWITCHING_ON=1, ENABLED=2,
                            SWITCHING_OFF=3)
C.PREBATTLE_TEAM_STATE = _ns(NOT_READY=1, READY=2, LOCKED=3)
sys.modules['constants'] = C

# --- BigWorld stub ------------------------------------------------------------
BW = types.ModuleType('BigWorld')
_bw_callbacks = []
_bw_clear_calls = []
_bw_entities = {}
BW.callback = lambda delay, fn: _bw_callbacks.append((delay, fn))
BW.serverTime = lambda: 1000.0
BW.clearAllSpaces = lambda: _bw_clear_calls.append(True)
BW.entity = lambda eid: _bw_entities.get(eid)
BW.entities = _bw_entities
sys.modules['BigWorld'] = BW

# --- gun_rotation_shared stub --------------------------------------------------
GRS = types.ModuleType('gun_rotation_shared')
GRS.encodeGunAngles = lambda yaw, pitch, limits: 4242
sys.modules['gun_rotation_shared'] = GRS

BTL = imp.load_source('MPBattle', os.path.join(ROOT, 'src', 'Offline',
                                               'MPBattle.py'))
FAILS = []


def check(cond, name):
    if cond:
        print('PASS %s' % (name,))
    else:
        print('FAIL %s' % (name,))
        FAILS.append(name)


# --- pure helpers -------------------------------------------------------------
check(BTL._dirs_from_flags(0) == (0, 0), 'dirs: idle')
check(BTL._dirs_from_flags(1) == (1, 0), 'dirs: forward')
check(BTL._dirs_from_flags(2) == (-1, 0), 'dirs: backward')
check(BTL._dirs_from_flags(4) == (0, -1), 'dirs: left')
check(BTL._dirs_from_flags(8) == (0, 1), 'dirs: right')
check(BTL._dirs_from_flags(1 | 8) == (1, 1), 'dirs: forward+right')
check(BTL._dirs_from_flags(1 | 16) == (1, 0), 'dirs: cruise bits ignored')

check(abs(BTL._yaw_to_point(0, 0, 0, 10) - 0.0) < 1e-9, 'yaw: +Z is 0')
check(abs(BTL._yaw_to_point(0, 0, 10, 0) - math.pi / 2) < 1e-9, 'yaw: +X')
check(abs(BTL._yaw_to_point(5, 5, 5, -5) - math.pi) < 1e-9, 'yaw: -Z')

row = BTL._vehicle_info_row(77, 'DESCR', 'Nick', 2, 12345, 9, True)
check(len(row) == 18, 'vehicle info row has 18 items')
check(row[0] == 77 and row[1] == 'DESCR' and row[2] == 'Nick'
      and row[3] == 2 and row[4] is True and row[7] == 12345
      and row[10] == 9 and row[11] is True,
      'vehicle info row field positions')
blob = BTL._pack_vehicle_list([row])
check(cPickle.loads(blob) == [row], 'vehicle list pack roundtrip')
period = cPickle.loads(BTL._pack_period(3, 1234.5, 900))
check(period == (3, 1234.5, 900.0, []), 'period pack roundtrip')
check(cPickle.loads(BTL._pack_avatar_ready(55)) == 55, 'avatar ready pack')

players = [{'vid': 1, 'team': 1}, {'vid': 2, 'team': 2},
           {'vid': 3, 'team': 1}, {'vid': 4, 'team': 2},
           {'vid': 5, 'team': 1}]
spawns = BTL._spawn_assignment(players)
check(spawns == {1: 0, 2: 0, 3: 1, 4: 1, 5: 2},
      'spawn assignment is per-team sequential in join order')

# --- server state machine ------------------------------------------------------


class _FakeConn(object):

    def __init__(self, vid, nick):
        self.vid = vid
        self.nick = nick
        self.sent = []

    def send(self, msg):
        self.sent.append(json.loads(json.dumps(msg)))


class _FakeMember(object):

    def __init__(self, vid, roster, veh, joined):
        self.vid = vid
        self.roster = roster
        self.veh_descr = veh
        self.joined_at = joined


class _FakeRoom(object):

    def __init__(self):
        self.room_id = 3
        self.creator_vid = 100
        self.arena_type_id = 7
        self.round_length = 900
        self.team_states = {1: 2, 2: 2}
        self.members = {
            100: _FakeMember(100, 1, 'aabb', 1.0),    # team1 assigned
            200: _FakeMember(200, 2, 'ccdd', 2.0),    # team2 assigned
            300: _FakeMember(300, 17, 'eeff', 3.0),   # team1 unassigned
        }


class _FakeTrn(object):

    def __init__(self):
        self.rooms = {3: _FakeRoom()}
        self.mp = _ns(by_vid={100: _FakeConn(100, u'Alice'),
                              200: _FakeConn(200, u'bob'),
                              300: _FakeConn(300, u'carol')})
        self.team_state_broadcasts = []

    def _broadcast_team_states(self, room):
        self.team_state_broadcasts.append(dict(room.team_states))


def _evs(conn, ev):
    return [m for m in conn.sent if m.get('ev') == ev]


trn = _FakeTrn()
bs = BTL.make_server(trn)
room = trn.rooms[3]
a_conn, b_conn, c_conn = (trn.mp.by_vid[100], trn.mp.by_vid[200],
                          trn.mp.by_vid[300])

check(bs.start(room) is True, 'server start() accepts two assigned teams')
start_a = _evs(a_conn, 'start')
check(len(start_a) == 1 and len(_evs(b_conn, 'start')) == 1
      and not _evs(c_conn, 'start'),
      'start event only to assigned players')
roster = start_a[0]['players']
check(sorted(p['vid'] for p in roster) == [100, 200]
      and all(p['spawn'] == 0 for p in roster),
      'start roster excludes unassigned, first spawns')
check(start_a[0]['arenaTypeID'] == 7
      and start_a[0]['arenaUniqueID'] == (3 << 32 | start_a[0]['battleID']),
      'start carries arena ids')
check(bs.start(room) is False, 'second start() for same room refused')

battle = bs.battles[3]
bs.on_msg(a_conn, {'t': 'btl', 'cmd': 'ready'})
check(len(_evs(b_conn, 'avatarReady')) == 1
      and _evs(b_conn, 'avatarReady')[0]['vid'] == 100,
      'avatarReady relayed to opponent')
check(not _evs(a_conn, 'go'), 'no go until everybody ready')
bs.on_msg(b_conn, {'t': 'btl', 'cmd': 'ready'})
check(len(_evs(a_conn, 'go')) == 1 and len(_evs(b_conn, 'go')) == 1
      and _evs(a_conn, 'go')[0]['prebattle'] == BTL._PREBATTLE_SECONDS,
      'go broadcast when all ready')
check(battle.phase == 'prebattle', 'phase prebattle')

bs.on_msg(a_conn, {'t': 'btl', 'cmd': 'input', 'm': 1, 'r': -1, 'g': 777,
                   'p': [1.5, 2.5, 3.5]})
inp = _evs(b_conn, 'input')
check(len(inp) == 1 and inp[0]['vid'] == 100 and inp[0]['m'] == 1
      and inp[0]['r'] == -1 and inp[0]['g'] == 777
      and inp[0]['p'] == [1.5, 2.5, 3.5],
      'input relayed to opponent with pose')
check(not _evs(a_conn, 'input'), 'input not echoed to sender')

battle.fight_at = time.time() - 1.0
bs.on_tick()
check(len(_evs(a_conn, 'fight')) == 1
      and _evs(a_conn, 'fight')[0]['length'] == 900
      and battle.phase == 'battle', 'fight after countdown tick')

bs.on_msg(a_conn, {'t': 'btl', 'cmd': 'leave'})
check(len(_evs(b_conn, 'left')) == 1
      and _evs(b_conn, 'left')[0]['vid'] == 100, 'left relayed')
check(3 in bs.battles, 'battle survives first leaver')
bs.on_msg(b_conn, {'t': 'btl', 'cmd': 'leave'})
check(3 not in bs.battles and not bs.by_vid, 'battle finished after all left')
check(room.team_states == {1: 1, 2: 1}
      and trn.team_state_broadcasts[-1] == {1: 1, 2: 1},
      'room team states reset on battle finish')

# refused starts
trn2 = _FakeTrn()
room2 = trn2.rooms[3]
room2.members[200].roster = 18  # team2 unassigned -> only one team
check(BTL.make_server(trn2).start(room2) is False,
      'start refused without both teams assigned')
trn2b = _FakeTrn()
trn2b.rooms[3].arena_type_id = 0
check(BTL.make_server(trn2b).start(trn2b.rooms[3]) is False,
      'start refused without arena')

# loading timeout -> abort
trn3 = _FakeTrn()
bs3 = BTL.make_server(trn3)
room3 = trn3.rooms[3]
bs3.start(room3)
bs3.battles[3].created_at = time.time() - BTL._LOAD_TIMEOUT - 1
bs3.on_tick()
check(len(_evs(trn3.mp.by_vid[100], 'abort')) == 1 and 3 not in bs3.battles,
      'loading timeout aborts battle')

# room destroyed mid-battle -> abort
trn4 = _FakeTrn()
bs4 = BTL.make_server(trn4)
room4 = trn4.rooms[3]
bs4.start(room4)
del trn4.rooms[3]
bs4.on_room_destroyed(room4)
check(len(_evs(trn4.mp.by_vid[200], 'abort')) == 1 and not bs4.battles,
      'room destruction aborts battle')

# --- fake server bridge ---------------------------------------------------------


class _FakeTransport(object):

    def __init__(self):
        self.sent = []

    def send_to_server(self, msg):
        self.sent.append(dict(msg))


state = _ns(my_vid=100, transport=_FakeTransport(), player=None)
bc = BTL._BattleClient(state)
bridge = bc._bridge
check(bridge.voipController is bridge, 'bridge voipController is self')
check(bridge.isVOIPEnabled() is False and bridge.isVivox() is False,
      'bridge voip surface disabled')
bridge.vehicle_moveWith(1 | 8)
check(bc._cur_input == (1, 1), 'bridge captures movement flags')
bridge.vehicle_moveWith(0)
check(bc._cur_input == (0, 0), 'bridge captures stop')
bridge.setClientReady()
bridge.setClientReady()
ready_msgs = [m for m in state.transport.sent if m.get('cmd') == 'ready']
check(len(ready_msgs) == 1 and ready_msgs[0]['t'] == 'btl',
      'setClientReady sends ready once')
check(bridge.someUnknownMailbox(1, 2) is None, 'unknown mailbox is no-op')
try:
    bridge._private
    _raised = False
except AttributeError:
    _raised = True
check(_raised, 'bridge does not fabricate private attributes')
bridge.vehicle_shoot()
check(bc._shoot_logged is True, 'vehicle_shoot captured')

# bindToVehicle: engine attachment emulation (onVehicleChanged +
# consistent matrices target + view points), as the server would do.
_bind_calls = []


class _FakeConsistent(object):

    def _ConsistentMatrices__setTarget(self, matrix, as_static):
        _bind_calls.append(('target', matrix, as_static))


_bc_avatar = _ns(
    onVehicleChanged=lambda: _bind_calls.append(('changed',)),
    consistentMatrices=_FakeConsistent(),
    guiSessionProvider=_ns(shared=_ns(viewPoints=_ns(
        updateAttachedVehicle=lambda vid: _bind_calls.append(
            ('viewpoints', vid))))))
bc._avatar = _bc_avatar
_bw_entities[777] = _ns(matrix='MATRIX')
bridge.bindToVehicle(777)
check(_bind_calls == [('changed',), ('target', 'MATRIX', False),
                      ('viewpoints', 777)],
      'bindToVehicle emulates engine vehicle attachment')
bc._avatar = None

# client input application
_filter_calls = []
_notifier_calls = []


class _FakeVehicle(object):
    class _Filter(object):
        def notifyInputKeysDown(self, m, r):
            _filter_calls.append((m, r))

    def __init__(self):
        self.filter = _FakeVehicle._Filter()
        self.gunAnglesPacked = 5
        self.position = (0.0, 0.0, 0.0)

    def set_gunAnglesPacked(self, prev):
        _notifier_calls.append(prev)


class _FakePose(object):

    def __init__(self):
        self.input = (0, 0)
        self.gun_limits = None
        self.aim = None
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.yaw = 0.0

    def set_aim(self, yaw, pitch):
        self.aim = (yaw, pitch)


fake_veh = _FakeVehicle()
_bw_entities[555] = fake_veh
bc._vehicles[200] = 555
fake_pose = _FakePose()
bc._poses[200] = fake_pose
bc._ev_input({'ev': 'input', 'vid': 200, 'm': -1, 'r': 1, 'g': 909})
check(fake_pose.input == (-1, 1), 'remote input feeds kinematic pose')
check(fake_veh.gunAnglesPacked == 909 and _notifier_calls == [5],
      'remote gun angles applied with notifier(prev)')

# kinematic integration (no space loaded: terrain sampling no-ops)
sp = _FakePose()
sp.pitch = sp.roll = 0.0
sp.speed = 0.0
sp.speed_fwd = 10.0
sp.speed_back = 3.0
sp.traverse = 0.5
sp.half_len = 3.0
sp.half_wid = 1.5
sp.grounded_once = True
for _i in range(60):
    bc._integrate_pose(sp, 1, 0, 0.05)
check(sp.speed > 9.9 and sp.z > 5.0 and abs(sp.x) < 0.001,
      'sim accelerates forward along +Z at yaw 0')
for _i in range(40):
    bc._integrate_pose(sp, 0, 1, 0.05)
check(sp.speed == 0.0 and sp.yaw > 0.9,
      'sim pivot-turns right and coasts to a stop')

# client sampler
bc.active = True
bc._gun_limits = (-0.1, 0.2)
bc._avatar = _ns(gunRotator=_ns(turretYaw=0.3, gunPitch=0.05))
bc._vehicles[100] = 0
bc._cur_input = (1, 0)
state.transport.sent = []
bc._last_pose_sent = time.time()  # pose not due
bc._sample()
sent = [m for m in state.transport.sent if m.get('cmd') == 'input']
check(len(sent) == 1 and sent[0]['m'] == 1 and sent[0]['g'] == 4242,
      'sampler sends changed input + packed gun')
state.transport.sent = []
bc._sample()
check(not [m for m in state.transport.sent if m.get('cmd') == 'input'],
      'sampler silent when nothing changed')
bc.active = False

# --- entity patches --------------------------------------------------------------
account_mod = types.ModuleType('Account')


class _PlayerAccount(object):

    def onBecomePlayer(self):
        BW.clearAllSpaces()
        self.became_player = True


account_mod.PlayerAccount = _PlayerAccount
sys.modules['Account'] = account_mod
avatar_mod = types.ModuleType('Avatar')


class _PlayerAvatar(object):
    pass


avatar_mod.PlayerAvatar = _PlayerAvatar
sys.modules['Avatar'] = avatar_mod
vehicle_mod = types.ModuleType('Vehicle')


class _Vehicle(object):
    pass


vehicle_mod.Vehicle = _Vehicle
sys.modules['Vehicle'] = vehicle_mod

state2 = _ns(my_vid=1, transport=_FakeTransport(), player=None)
client2 = BTL.install(state2)
check(state2.battle is client2, 'install stores battle client on state')
check(getattr(_PlayerAvatar, '_mpBattlePatched', False) is True,
      'avatar patch installed')

_av = _PlayerAvatar()
_av._mpBattle = True
_av.fakeServer = bridge
check(_av.base is bridge and _av.cell is bridge and _av.bwProto is bridge,
      'avatar mailboxes redirect to fakeServer in battle')
_av2 = _PlayerAvatar()
try:
    _av2.base
    _raised = False
except AttributeError:
    _raised = True
check(_raised, 'avatar mailboxes untouched outside battle')

_veh = _Vehicle()
_veh._mpFakeServer = bridge
check(_veh.cell is bridge, 'vehicle cell redirects to fakeServer')

# scoped filter proxy: syncGunAngles/syncStabilisedYPR are swallowed
# only while the vehicle is in _FILTER_SCOPED (startWGPhysics /
# set_gunAnglesPacked); everything else delegates to the real filter.
class _FakeFilter(object):
    speedInfo = 'speed'

    def __init__(self):
        self.synced = []

    def syncGunAngles(self, yaw, pitch):
        self.synced.append((yaw, pitch))

    def syncStabilisedYPR(self, y, p, r):
        self.synced.append((y, p, r))


_real_filter = _FakeFilter()
_veh.filter = _real_filter
check(_veh.filter is _real_filter, 'filter unproxied outside scope')
BTL._FILTER_SCOPED.append(_veh)
check(_veh.filter.speedInfo == 'speed', 'filter proxy delegates attrs')
_veh.filter.syncGunAngles(1.0, 2.0)
_veh.filter.syncStabilisedYPR(1.0, 2.0, 3.0)
check(_real_filter.synced == [], 'filter proxy swallows native syncs')
BTL._remove_scoped(_veh)
_veh.filter.syncGunAngles(0.5, 0.25)
check(_real_filter.synced == [(0.5, 0.25)],
      'real filter sync works again after scope')
try:
    _Vehicle().cell
    _raised = False
except AttributeError:
    _raised = True
check(_raised, 'vehicle mailboxes untouched without bridge')

_acc = _PlayerAccount()
_acc.onBecomePlayer()
check(_acc.became_player is True and not _bw_clear_calls,
      'account onBecomePlayer runs without clearAllSpaces')
BW.clearAllSpaces()
check(_bw_clear_calls == [True], 'clearAllSpaces restored after wrapper')

# --- battle enter / teardown (regressions from the 2026-10-18 test) --------
# onEnterWorld reads playerVehicleID synchronously inside createEntity,
# gameplay cfg shadows geometry spawn points with empty lists, and the
# Account must be re-promoted BEFORE the Avatar entity is destroyed.
_bw_order = []
_bw_created = []


class _FakeAvatarEnt(object):
    pass


def _bw_create_space():
    _bw_order.append(('space',))
    return 55


def _bw_create_entity(cls_name, space_id, veh_id, pos, dir_, props):
    # Route the Avatar through the patched PlayerAvatar class so the
    # __init__ property seeding is exercised like on the real client.
    ent = _PlayerAvatar() if cls_name == 'Avatar' else _FakeAvatarEnt()
    eid = 9000 + len(_bw_created)
    _bw_entities[eid] = ent
    _bw_created.append((cls_name, pos, dict(props)))
    _bw_order.append(('create', cls_name, eid))
    return eid


def _bw_player(entity):
    _bw_order.append(('player', entity))


def _bw_destroy(eid):
    _bw_order.append(('destroy', eid))


BW.createSpace = _bw_create_space
BW.addSpaceGeometryMapping = lambda sid, mid, path: 66
BW.createEntity = _bw_create_entity
BW.player = _bw_player
BW.destroyEntity = _bw_destroy
BW.cameraSpaceID = lambda sid: None
BW.isClientSpace = lambda sid: True
BW.delSpaceGeometryMapping = lambda sid, mid: None
BW.clearSpace = lambda sid: None
BW.releaseSpace = lambda sid: None

arena_mod = types.ModuleType('ArenaType')
_geom = _ns(teamSpawnPoints=([(10, 20), (30, 40)], [(-10, -20)]),
            teamBasePositions=({}, {}))
_atype = _ns(id=70, geometryName='101_dday',
             boundingBox=((-500, -500), (700, 500)),
             teamSpawnPoints=([], []), teamBasePositions=({}, {}))
arena_mod.g_cache = {70: _atype}
arena_mod.g_geometryCache = {70: _geom}
sys.modules['ArenaType'] = arena_mod

pe_mod = types.ModuleType('PlayerEvents')
pe_mod.g_playerEvents = _ns(isPlayerEntityChanging=False,
                            onStatsResync=lambda: None)
sys.modules['PlayerEvents'] = pe_mod

offline_pkg = types.ModuleType('Offline')
manager_stub = types.ModuleType('Offline.Manager')
manager_stub.get_offline_state = lambda: {}
offline_pkg.Manager = manager_stub
sys.modules['Offline'] = offline_pkg
sys.modules['Offline.Manager'] = manager_stub


class _FakeAccountEnt(object):

    def __init__(self):
        self.gui_ctx = None

    def showGUI(self, ctx):
        self.gui_ctx = cPickle.loads(ctx)


_fake_account = _FakeAccountEnt()
state3 = _ns(my_vid=100001, transport=_FakeTransport(),
             player=_fake_account, _messenger_ready=lambda: True)
client3 = BTL._BattleClient(state3)
client3._enter({'arenaTypeID': 70, 'arenaUniqueID': 123, 'roomID': 1,
                'battleID': 1, 'roundLength': 600,
                'players': [{'vid': 100001, 'team': 1, 'nick': u'Me',
                             'spawn': 1},
                            {'vid': 100002, 'team': 2, 'nick': u'Other',
                             'spawn': 0}]})
check(len(_bw_created) == 1 and _bw_created[0][0] == 'Avatar',
      'enter creates one Avatar entity')
check(_bw_created[0][2] == {},
      'createEntity property dict left empty (engine ignores it)')
check(client3._avatar.playerVehicleID == 0
      and client3._avatar.team == 1
      and client3._avatar.arenaTypeID == 70
      and client3._avatar.arenaUniqueID == 123
      and client3._avatar.arenaBonusType == 2
      and client3._avatar.name == 'Me',
      'avatar def props seeded via patched __init__ (playerVehicleID=0)')
check(client3._avatar.fakeServer is client3._bridge
      and client3._avatar.base is client3._bridge,
      'avatar bridge wired during construction')
check(not BTL._PENDING_AVATAR, 'pending avatar props cleared after enter')
check(_bw_created[0][1] == (30.0, 0.0, 40.0),
      'spawn point falls back to geometry cache points')
check(client3._avatar.prebattle is None,
      'avatar gets prebattle=None for the statistics poller')
check(('player', client3._avatar) in _bw_order and client3.active,
      'avatar promoted to player on enter')

_bw_order[:] = []
_cb_before = len(_bw_callbacks)
client3._teardown()
_names = [entry[0] for entry in _bw_order]
check('player' in _names and 'destroy' in _names
      and _names.index('player') < _names.index('destroy'),
      'teardown promotes Account before destroying Avatar')
check(_bw_order[_names.index('player')][1] is _fake_account,
      'teardown promotes the hangar Account')
check(not client3.active, 'client inactive after teardown')
check(len(_bw_callbacks) > _cb_before, 'lobby restore scheduled')
_bw_callbacks[-1][1]()
check(_fake_account.gui_ctx == {'databaseID': 1, 'currentVehInvID': 1},
      'lobby restore calls Account.showGUI with databaseID=1')

print('---')
print('%d checks failed' % len(FAILS))
if FAILS:
    for f in FAILS:
        print('FAILED: %s' % f)
    sys.exit(1)
