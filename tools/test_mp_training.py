# -*- coding: utf-8 -*-
"""
Logic test for src/Offline/MPTraining.py (LAN training rooms).

Runs under Python 2.7 with stubbed game modules. Reuses the
BigWorld/chat_shared stub block from test_mp_chat.py, adds a `constants`
stub with the real 0.9.21 prebattle enum values, an identity zlib stub
(/tmp/py27 has no zlib C module; the game has one) and fake
Account/PlayerEvents/gui modules for the client-side delivery checks.

Server messages pass through a JSON round-trip so int dict keys become
strings exactly like on the wire.

Usage:  /tmp/py27/bin/python tools/test_mp_training.py
"""
import cPickle
import imp
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# --- reuse BigWorld + chat_shared stubs from test_mp_chat.py ----------------
_prefix_src = open(os.path.join(HERE, 'test_mp_chat.py')).read()
_cut = _prefix_src.index("sys.modules['chat_shared'] = cs")
exec(_prefix_src[:_cut + len("sys.modules['chat_shared'] = cs")])

# --- zlib identity stub (sandbox py27 lacks the C module) ------------------
zlib_stub = types.ModuleType('zlib')
zlib_stub.compress = lambda data, level=-1: data
zlib_stub.decompress = lambda data: data
sys.modules['zlib'] = zlib_stub

# --- constants stub (real 0.9.21 values) ------------------------------------


def _ns(**fields):
    obj = types.ModuleType('ns')
    obj.__dict__.update(fields)
    return obj


C = types.ModuleType('constants')
C.PREBATTLE_TYPE = _ns(NONE=0, SQUAD=1, TRAINING=2, COMPANY=3)
C.PREBATTLE_UPDATE = _ns(ROSTER=1, PLAYER_ADDED=2, PLAYER_REMOVED=3,
                         PLAYER_STATE=4, PLAYER_ROSTER=5, TEAM_STATES=6,
                         SETTINGS=7, SETTING=8, PROPERTIES=10, PROPERTY=11)
C.PREBATTLE_ACCOUNT_STATE = _ns(NOT_READY=1, READY=4)
C.PREBATTLE_TEAM_STATE = _ns(NOT_READY=1, READY=2, LOCKED=3)
C.PREBATTLE_STATE = _ns(IDLE=0, IN_QUEUE=1, IN_BATTLE=2)
_TRAINING_CREATOR = 96 | 1 | 2 | 4 | 8 | 16 | 512 | 1024 | 2048 | 4096 \
    | 8192 | 16384 | 32768
C.PREBATTLE_ROLE = _ns(TRAINING_DEFAULT=96,
                       TRAINING_CREATOR=_TRAINING_CREATOR)
C.PREBATTLE_CACHE_KEY = _ns(IS_OPENED=2, STATE=3, IS_FULL=4,
                            PLAYER_COUNT=5, CREATOR=6, COMMENT=9,
                            ARENA_TYPE_ID=11, CREATOR_CLAN_ABBREV=15,
                            CREATOR_IGR_TYPE=17, CREATOR_DB_ID=18,
                            CREATOR_BADGES=19)
C.PREBATTLE_INVITE_STATE = _ns(ACTIVE=1, ACCEPTED=2, DECLINED=3, EXPIRED=4)
C.JOIN_FAILURE = _ns(TIME_OUT=1, NOT_FOUND=2, TEAM_IS_FULL=5, PRIVACY=10)
C.KICK_REASON = _ns(CREATOR_LEFT=9, PLAYERKICK=10)
sys.modules['constants'] = C

# --- Account / PlayerEvents / gui stubs for client delivery -----------------
account_mod = types.ModuleType('Account')


class PlayerAccount(object):
    def prb_createTraining(self, *a):
        raise AssertionError('stock stub must be patched')


account_mod.PlayerAccount = PlayerAccount
sys.modules['Account'] = account_mod


class _Event(object):

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


pe_mod = types.ModuleType('PlayerEvents')
pe_mod.g_playerEvents = _ns(onPrebattleInvitesChanged=_Event())
sys.modules['PlayerEvents'] = pe_mod

gui_mod = types.ModuleType('gui')
_gui_notes = []


class _SM(object):
    SM_TYPE = _ns(Warning='Warning')

    @staticmethod
    def pushMessage(text, type=None):
        _gui_notes.append(text)


gui_mod.SystemMessages = _SM
sys.modules['gui'] = gui_mod

# --- load modules under test --------------------------------------------------
MP = imp.load_source('MPChat', os.path.join(ROOT, 'src', 'Offline', 'MPChat.py'))
TRN = imp.load_source('MPTraining', os.path.join(ROOT, 'src', 'Offline', 'MPTraining.py'))
_pkg = types.ModuleType('Offline')
_pkg.MPChat = MP
_pkg.MPTraining = TRN
sys.modules['Offline'] = _pkg
sys.modules['Offline.MPChat'] = MP
sys.modules['Offline.MPTraining'] = TRN

FAILS = []


def check(cond, label):
    print('%s %s' % ('PASS' if cond else 'FAIL', label))
    if not cond:
        FAILS.append(label)


A = cs.CHAT_ACTIONS


def wire(inbox):
    return lambda d: inbox.append(json.loads(json.dumps(d)))


def fresh(server, key, nick, ent_id, veh):
    inbox = []
    conn = server.attach(key, wire(inbox))
    server.on_hello(conn, nick, 1, ent_id, veh)
    del inbox[:]
    return conn, inbox


def prb(server, conn, **msg):
    msg['t'] = 'prb'
    server.on_prb_msg(conn, msg)


def evs(inbox, ev=None):
    return [m for m in inbox if m.get('t') == 'prb'
            and (ev is None or m.get('ev') == ev)]


def upds(inbox, upd):
    return [m['arg'] for m in evs(inbox, 'upd') if m.get('upd') == upd]


def acts(inbox, item):
    return [m['data'] for m in inbox if m.get('t') == 'act'
            and m['data']['action'] == item.index()]


# ============================================================================
# Server scenario
# ============================================================================
srv = MP._MPServer()
alice, a_in = fresh(srv, 'a', u'Alice', 11, '0102')
bob, b_in = fresh(srv, 'b', u'Bob', 22, '0304')
carol, c_in = fresh(srv, 'c', u'Carol', 33, '0506')
dave, d_in = fresh(srv, 'd', u'Dave', 44, '0708')

# create room
prb(srv, alice, cmd='createTraining', arenaTypeID=7, roundLength=900,
    isOpened=True, comment=u'room1')
joined = evs(a_in, 'joined')
check(len(joined) == 1 and joined[0]['prbID'] == 1, 'creator joined room #1')
st = joined[0]['settings']
check(st['type'] == 2 and st['arenaTypeID'] == 7 and st['roundLength'] == 900
      and st['comment'] == u'room1' and st['isOpened'],
      'creator settings view')
check(st['roles'][u'1'] == _TRAINING_CREATOR,  # JSON: int key -> str
      'creator role mask == TRAINING_CREATOR (self dbID view)')
row = joined[0]['roster'][0]
check(row[0] == 11 and row[1] == u'Alice' and row[2] == 1 and row[4] == 1
      and row[5] == C.PREBATTLE_ACCOUNT_STATE.NOT_READY and row[7] == '0102',
      'creator roster row: self entityID/dbID, assigned team1, not ready')
check(st['limits'][u'1']['maxCount'] == [15, 15]
      and st['limits'][u'2']['maxCount'] == [15, 15],
      'settings limits carry maxCount (assigned, unassigned) per team')
check(joined[0]['teamStates'] == [1, 1], 'both teams NOT_READY')
chan_acts = acts(a_in, A.requestChannels)
prb_chans = [d for d in chan_acts if d.get('data')
             and d['data'][0]['flags'] & cs.CHAT_CHANNEL_TRAINING]
check(len(prb_chans) == 1
      and prb_chans[0]['data'][0]['flags'] & cs.CHAT_CHANNEL_PREBATTLE
      and prb_chans[0]['data'][0]['channelName'] == u'#chat:channels/training',
      'stock training chat channel pushed to creator')
check(len(acts(a_in, A.selfEnter)) >= 1, 'creator selfEnter into room channel')
state_dict = srv._state_dict()
check(all(int(saved['id']) != prb_chans[0]['data'][0]['id']
          for saved in state_dict['channels']),
      'prb channel excluded from persisted state')
del a_in[:]

# room list for Bob
prb(srv, bob, cmd='requestList', prbType=2, sortKey=7, start=0, end=50)
lst = evs(b_in, 'list')
K = C.PREBATTLE_CACHE_KEY
info = lst[0]['items'][0][2] if lst and lst[0]['items'] else {}
check(lst and lst[0]['count'] == 1 and lst[0]['items'][0][1] == 1
      and info[unicode(K.CREATOR)] == u'Alice'
      and info[unicode(K.PLAYER_COUNT)] == 1
      and info[unicode(K.ARENA_TYPE_ID)] == 7
      and info[unicode(K.CREATOR_DB_ID)] == alice.vid,
      'room list item (creator seen by Bob as vid)')
del b_in[:]

# list by name
prb(srv, bob, cmd='requestList', prbType=2, mask=u'ali')
check(evs(b_in, 'list')[0]['count'] == 1, 'list by creator-name mask')
del b_in[:]
prb(srv, bob, cmd='requestList', prbType=2, mask=u'zzz')
check(evs(b_in, 'list')[0]['count'] == 0, 'list by name: no match')
del b_in[:]

# roster preview (10-tuples) for outsider Dave
prb(srv, dave, cmd='requestRoster', prbID=1)
rl = evs(d_in, 'rosterList')
check(rl and rl[0]['prbID'] == 1 and len(rl[0]['rows']) == 1
      and len(rl[0]['rows'][0]) == 10 and rl[0]['rows'][0][0] == alice.vid,
      'roster preview rows are 10-field, outsider sees vid')
del d_in[:]

# Bob joins
prb(srv, bob, cmd='join', prbID=1)
bjoined = evs(b_in, 'joined')
check(len(bjoined) == 1, 'Bob joined room')
bystander_rows = dict((r[1], r) for r in bjoined[0]['roster'])
check(bystander_rows[u'Bob'][0] == 22 and bystander_rows[u'Bob'][2] == 1
      and bystander_rows[u'Bob'][4] == 17,
      'Bob sees self as entityID 22/dbID 1, unassigned team1 (17)')
check(bystander_rows[u'Alice'][0] == alice.vid
      and bystander_rows[u'Alice'][2] == alice.vid and bystander_rows[u'Alice'][4] == 1,
      "Bob sees Alice as vid, assigned team1")
added = upds(a_in, C.PREBATTLE_UPDATE.PLAYER_ADDED)
check(len(added) == 1 and added[0][0] == bob.vid and added[0][4] == 17,
      'Alice got PLAYER_ADDED for Bob (vid view)')
check(len(upds(a_in, C.PREBATTLE_UPDATE.SETTINGS)) == 1,
      'Alice got settings refresh (roles) on join')
check(len([d for d in acts(b_in, A.requestChannels)
           if d.get('data') and d['data'][0]['flags'] & cs.CHAT_CHANNEL_TRAINING]) == 1,
      'Bob pushed room chat channel')
del a_in[:]
del b_in[:]

# Bob ready (selects another vehicle: vehDescr '0a0b')
prb(srv, bob, cmd='ready', req=5, vehInvID=12, vehDescr='0a0b')
resp = evs(b_in, 'resp')
check(resp and resp[0]['req'] == 5 and resp[0]['code'] == 0, 'ready resp ok')
ps_a = upds(a_in, C.PREBATTLE_UPDATE.PLAYER_STATE)
ps_b = upds(b_in, C.PREBATTLE_UPDATE.PLAYER_STATE)
check(ps_a and ps_a[0][0] == bob.vid and ps_a[0][2] == C.PREBATTLE_ACCOUNT_STATE.READY
      and ps_a[0][3] == '0a0b', 'Alice sees Bob READY, vehicle hex on wire')
check(ps_b and ps_b[0][0] == 22, 'Bob sees own PLAYER_STATE with entityID')
del a_in[:]
del b_in[:]

# Alice assigns Bob to team 2 assigned (roster 2)
prb(srv, alice, cmd='assign', req=6, playerID=bob.vid, roster=2)
check(evs(a_in, 'resp')[0]['code'] == 0, 'assign by creator ok')
pro_b = upds(b_in, C.PREBATTLE_UPDATE.PLAYER_ROSTER)
check(pro_b and pro_b[0][0] == 22 and pro_b[0][1] == 17 and pro_b[0][2] == 2
      and pro_b[0][3] == alice.vid,
      'Bob sees own move 17->2, actor=Alice vid')
del a_in[:]
del b_in[:]

# Bob cannot assign Alice; can move himself
prb(srv, bob, cmd='assign', req=7, playerID=alice.vid, roster=18)
check(evs(b_in, 'resp')[0]['code'] == -1, 'non-creator assign of other denied')
del b_in[:]
prb(srv, bob, cmd='assign', req=8, playerID=22, roster=18)  # self by entityID
check(evs(b_in, 'resp')[0]['code'] == 0, 'self-assign via entityID ok')
del a_in[:]
del b_in[:]

# settings: only creator
prb(srv, bob, cmd='changeComment', req=9, comment=u'hack')
check(evs(b_in, 'resp')[0]['code'] == -1, 'non-creator changeComment denied')
del b_in[:]
prb(srv, alice, cmd='changeComment', req=10, comment=u'gl hf')
check(evs(a_in, 'resp')[0]['code'] == 0
      and upds(b_in, C.PREBATTLE_UPDATE.SETTING) == [[u'comment', u'gl hf']],
      'creator changeComment broadcast as SETTING')
del a_in[:]
del b_in[:]

# team ready x2 -> battle refused after deferred tick
prb(srv, alice, cmd='teamReady', req=11, team=1)
check(upds(b_in, C.PREBATTLE_UPDATE.TEAM_STATES) == [[2, 1]],
      'team1 READY broadcast')
del a_in[:]
del b_in[:]
prb(srv, alice, cmd='teamReady', req=12, team=2)
check(upds(b_in, C.PREBATTLE_UPDATE.TEAM_STATES) == [[2, 2]],
      'team2 READY broadcast')
trn = srv.training
check(len(trn._deferred) == 1, 'battle cancel deferred scheduled')
trn._deferred[0] = (0.0, trn._deferred[0][1])
del a_in[:]
del b_in[:]
trn.on_tick()
check(upds(a_in, C.PREBATTLE_UPDATE.TEAM_STATES) == [[1, 1]]
      and upds(b_in, C.PREBATTLE_UPDATE.TEAM_STATES) == [[1, 1]],
      'both teams reverted to NOT_READY')
check(evs(a_in, 'sysNote') and evs(b_in, 'sysNote')
      and u'недоступен' in evs(a_in, 'sysNote')[0]['text'],
      'battle-unavailable note to all members')
del a_in[:]
del b_in[:]

# invites + closed room
prb(srv, alice, cmd='changeOpenStatus', req=13, isOpened=False)
del a_in[:]
del b_in[:]
prb(srv, dave, cmd='join', prbID=1)
jf = evs(d_in, 'joinFailure')
check(jf and jf[0]['code'] == C.JOIN_FAILURE.PRIVACY,
      'closed room join without invite -> PRIVACY')
del d_in[:]
prb(srv, alice, cmd='sendInvites', dbIDs=[carol.vid], comment=u'come')
inv = evs(c_in, 'invite')
check(inv and inv[0]['prbID'] == 1 and inv[0]['creator'] == u'Alice'
      and inv[0]['creatorDBID'] == alice.vid, 'Carol received invite')
del c_in[:]
prb(srv, carol, cmd='declineInvite', prbID=1)
check(evs(c_in, 'inviteDel') and evs(a_in, 'sysNote')
      and u'Carol' in evs(a_in, 'sysNote')[0]['text'],
      'decline removes invite and notifies creator')
del a_in[:]
del c_in[:]
prb(srv, alice, cmd='sendInvites', dbIDs=[carol.vid], comment=u'')
del c_in[:]
prb(srv, carol, cmd='join', prbID=1)  # accept path == join
check(evs(c_in, 'joined'), 'invited Carol joins closed room')
del a_in[:]
del b_in[:]
del c_in[:]

# kick Carol
prb(srv, alice, cmd='kick', req=14, playerID=carol.vid)
check(evs(a_in, 'resp')[0]['code'] == 0, 'kick resp ok')
kicked = evs(c_in, 'kicked')
check(kicked and kicked[0]['reason'] == C.KICK_REASON.PLAYERKICK,
      'Carol kicked with PLAYERKICK reason')
check(upds(b_in, C.PREBATTLE_UPDATE.PLAYER_REMOVED),
      'Bob got PLAYER_REMOVED for Carol')
check(acts(c_in, A.channelDestroyed), 'Carol room channel destroyed')
del a_in[:]
del b_in[:]
del c_in[:]

# Bob leaves
prb(srv, bob, cmd='leave', req=15)
check(evs(b_in, 'resp')[0]['code'] == 0 and evs(b_in, 'left'),
      'Bob leave: resp then onPrebattleLeft')
check(upds(a_in, C.PREBATTLE_UPDATE.PLAYER_REMOVED),
      'Alice got PLAYER_REMOVED for Bob')
del a_in[:]
del b_in[:]

# Bob rejoins (room still closed -> invite first), then creator leaves
prb(srv, alice, cmd='sendInvites', dbIDs=[bob.vid], comment=u'')
prb(srv, bob, cmd='join', prbID=1)
check(evs(b_in, 'joined'), 'Bob rejoined via invite')
room_chan_id = srv.training.rooms[1].chan_id
del a_in[:]
del b_in[:]
prb(srv, alice, cmd='leave', req=16)
bk = evs(b_in, 'kicked')
check(bk and bk[0]['reason'] == C.KICK_REASON.CREATOR_LEFT,
      'creator leave kicks members with CREATOR_LEFT')
check(evs(a_in, 'left'), 'creator got own left event')
check(not srv.training.rooms, 'room destroyed after creator leave')
check(room_chan_id not in srv.channels, 'room chat channel removed')
del a_in[:]
del b_in[:]

# disconnect inside a room destroys it for the peer
prb(srv, alice, cmd='createTraining', arenaTypeID=1, roundLength=600,
    isOpened=True, comment=u'')
prb(srv, bob, cmd='join', prbID=2)
del a_in[:]
del b_in[:]
srv.detach('a')
bk = evs(b_in, 'kicked')
check(bk and bk[0]['reason'] == C.KICK_REASON.CREATOR_LEFT
      and not srv.training.rooms,
      'creator disconnect destroys room')
del b_in[:]

# unknown command / join missing room
prb(srv, bob, cmd='bogusOp', req=99)
check(evs(b_in, 'resp') and evs(b_in, 'resp')[0]['code'] == -1,
      'unknown prb cmd -> resp -1')
del b_in[:]
prb(srv, bob, cmd='join', prbID=777)
jf = evs(b_in, 'joinFailure')
check(jf and jf[0]['code'] == C.JOIN_FAILURE.NOT_FOUND,
      'join missing room -> NOT_FOUND')
del b_in[:]

# ============================================================================
# Client scenario (patches + stock callback delivery)
# ============================================================================


class FakePlayer(object):

    def __init__(self):
        self.calls = []
        self.prebattleInvites = {}
        self.id = 77  # local BigWorld entity ID

    def onPrebattleJoined(self, prb_id):
        self.calls.append(('joined', prb_id))

    def onPrebattleLeft(self):
        self.calls.append(('left',))

    def onPrebattleJoinFailure(self, code):
        self.calls.append(('joinFailure', code))

    def onKickedFromPrebattle(self, reason):
        self.calls.append(('kicked', reason))

    def updatePrebattle(self, upd, arg_str):
        self.calls.append(('upd', upd, cPickle.loads(arg_str)))

    def receivePrebattles(self, ok, data):
        self.calls.append(('list', ok,
                           cPickle.loads(zlib_stub.decompress(data))))

    def receivePrebattleRoster(self, ok, data):
        self.calls.append(('rosterList', ok,
                           cPickle.loads(zlib_stub.decompress(data))))


class FakeTransport(object):

    def __init__(self):
        self.sent = []

    def send_to_server(self, msg):
        self.sent.append(msg)


class FakeState(object):
    pass


player = FakePlayer()
state = FakeState()
state.player = player
state.my_vid = 100005
state.transport = FakeTransport()
client = TRN.install(state)
check(state.training is client, 'install() attaches client to state')

acc = PlayerAccount()
acc.prb_createTraining(7, 900, True, u'room1')
check(state.transport.sent[-1] == {'t': 'prb', 'cmd': 'createTraining',
                                   'arenaTypeID': 7, 'roundLength': 900,
                                   'isOpened': True, 'comment': u'room1'},
      'patched prb_createTraining sends LAN op')

fired = []
acc.prb_ready(42, lambda code, err: fired.append(('ready', code, err)))
req = state.transport.sent[-1]['req']
client.on_msg({'t': 'prb', 'ev': 'resp', 'req': req, 'code': 0})
check(fired == [('ready', 0, '')],
      'prb_ready callback fired with (resultID, errorStr)')

fired = []
acc.prb_leave(lambda code: fired.append(code))
req = state.transport.sent[-1]['req']
client.on_msg({'t': 'prb', 'ev': 'resp', 'req': req, 'code': 0})
check(fired == [0], 'prb_leave callback fired with (resultID,)')

acc.requestPrebattles(2, 7, False, 0, 50)
check(state.transport.sent[-1]['cmd'] == 'requestList',
      'patched requestPrebattles sends LAN op')

# joined delivery: settings int keys restored, roster tuples
client.on_msg({'t': 'prb', 'ev': 'joined', 'prbID': 3,
               'settings': {'type': 2,
                            'roles': {u'1': _TRAINING_CREATOR},
                            'teamRoles': {u'1': 96, u'2': 96},
                            'limits': {u'0': {}, u'1': {}, u'2': {}}},
               'props': {'wins': [0, 0, 0], 'battlesCount': 0,
                         'createTime': 123},
               'roster': [[11, u'Me', 1, [], 1, 1, 100, '0708', 0, 0, u'']],
               'teamStates': [1, 1]})
kinds = [c[0] for c in player.calls]
check(kinds == ['joined', 'upd', 'upd', 'upd', 'upd'],
      'joined -> onPrebattleJoined then SETTINGS/PROPERTIES/ROSTER/TEAM_STATES')
s_arg = player.calls[1][2]
check(player.calls[1][1] == C.PREBATTLE_UPDATE.SETTINGS
      and s_arg['roles'] == {1: _TRAINING_CREATOR}
      and s_arg['limits'] == {0: {}, 1: {}, 2: {}},
      'settings int keys restored after JSON')
check(player.calls[3][2] == [(11, u'Me', 1, [], 1, 1, 100, '\x07\x08', 0, 0, u'')],
      'roster delivered as tuples, vehCompDescr hex -> packed str')
check(player.calls[4][2] == (1, 1), 'team states delivered as tuple')
del player.calls[:]

# list delivery builds the stock zlib/pickle blob
client.on_msg({'t': 'prb', 'ev': 'list', 'prbType': 2, 'count': 1,
               'items': [[555, 9, {u'6': u'Alice', u'5': 3}]]})
check(player.calls[0][0] == 'list' and player.calls[0][2]
      == (2, 1, [(555, 9, {6: u'Alice', 5: 3})]),
      'receivePrebattles blob (type, count, items) with int cache keys')
del player.calls[:]

client.on_msg({'t': 'prb', 'ev': 'rosterList', 'prbID': 9,
               'rows': [[100001, u'Alice', 100001, 1, 4, 10, '4d', 0, 0, u'']]})
check(player.calls[0][2] == (9, [(100001, u'Alice', 100001, 1, 4, 10, 'M',
                                  0, 0, u'')]),
      'receivePrebattleRoster blob with 10-tuples, veh decoded')
del player.calls[:]

client.on_msg({'t': 'prb', 'ev': 'invite', 'prbID': 4, 'creator': u'Alice',
               'creatorDBID': 100001, 'comment': u'hi', 'createTime': 42,
               'count': 2})
rec = player.prebattleInvites.get((4, 0))
check(rec is not None and rec['state'] == C.PREBATTLE_INVITE_STATE.ACTIVE
      and rec['type'] == 2 and rec['creator'] == u'Alice',
      'invite stored in player.prebattleInvites (old-style cache)')
check(pe_mod.g_playerEvents.onPrebattleInvitesChanged.calls,
      'onPrebattleInvitesChanged fired')

client.on_msg({'t': 'prb', 'ev': 'sysNote', 'text': u'hello note'})
check(_gui_notes and _gui_notes[-1] == u'hello note',
      'sysNote pushed via stock SystemMessages')

client.on_msg({'t': 'prb', 'ev': 'kicked', 'reason': 9})
check(player.calls[-1] == ('kicked', 9), 'kicked -> onKickedFromPrebattle')
del player.calls[:]

# self-ID rewrite at delivery: host fell back to vid for our row
client.on_msg({'t': 'prb', 'ev': 'upd', 'upd': C.PREBATTLE_UPDATE.ROSTER,
               'arg': [[100005, u'Me', 1, [], 1, 4, 5, '0708', 0, 0, u''],
                       [100009, u'Other', 100009, [], 17, 1, 6, '090a', 0, 0, u'']]})
rows = player.calls[-1][2]
check(rows[0][0] == 77 and rows[1][0] == 100009,
      'roster self row vid -> local entity ID, others untouched')
client.on_msg({'t': 'prb', 'ev': 'upd',
               'upd': C.PREBATTLE_UPDATE.PLAYER_STATE,
               'arg': [100005, 1, 4, '4d4e', 0, 0, u'']})
check(player.calls[-1][2] == (77, 1, 4, 'MN', 0, 0, u''),
      'PLAYER_STATE self ID rewritten, veh decoded')
client.on_msg({'t': 'prb', 'ev': 'upd',
               'upd': C.PREBATTLE_UPDATE.PLAYER_ROSTER,
               'arg': [100005, 17, 2, 100005]})
check(player.calls[-1][2] == (77, 17, 2, 77),
      'PLAYER_ROSTER target and actor IDs rewritten')
del player.calls[:]

# offline behaviour
state.my_vid = 0
acc.prb_join(3)
check(('joinFailure', C.JOIN_FAILURE.TIME_OUT) in player.calls,
      'offline join -> onPrebattleJoinFailure(TIME_OUT)')
fired = []
acc.prb_leave(lambda code: fired.append(code))
check(fired == [-1], 'offline callback op -> immediate -1')

print('---')
print('%d checks failed' % len(FAILS))
sys.exit(1 if FAILS else 0)
