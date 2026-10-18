# -*- coding: utf-8 -*-
"""
Logic test for src/Offline/MPChat.py (local multiplayer chat).

Runs under Python 2.7 with a stubbed `chat_shared` (fake enumerations in
the real 0.9.21 order) and BigWorld. Exercises the embedded chat server
directly (two fake connections) and the real socket transport over
loopback (host client + raw second client).

Usage:  /tmp/py27/bin/python tools/test_mp_chat.py
"""
import imp
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'src'))

# --------------------------------------------------------------------------
# BigWorld stub
# --------------------------------------------------------------------------
CALLBACKS = []


class _BigWorld(object):
    _player = None

    @staticmethod
    def callback(delay, fn):
        CALLBACKS.append(fn)
        return len(CALLBACKS)

    @staticmethod
    def player():
        return _BigWorld._player


sys.modules['BigWorld'] = _BigWorld

# --------------------------------------------------------------------------
# chat_shared stub (enumeration order matches the real 0.9.21 module)
# --------------------------------------------------------------------------


class _EnumItem(object):

    def __init__(self, enum, name, idx):
        self._enum = enum
        self._name = name
        self._idx = idx

    def index(self):
        return self._idx

    def name(self):
        return self._name

    def __repr__(self):
        return '<%s.%s>' % (self._enum._title, self._name)


class FakeEnum(object):

    def __init__(self, title, names):
        self._title = title
        self._items = []
        for i, n in enumerate(names):
            item = _EnumItem(self, n, i)
            self._items.append(item)
            setattr(self, n, item)

    def __getitem__(self, idx):
        try:
            idx = int(idx)
        except Exception:
            return None
        if 0 <= idx < len(self._items):
            return self._items[idx]
        return None


cs = types.ModuleType('chat_shared')
cs.CHAT_ACTIONS = FakeEnum('chatActions', [
 'enter', 'broadcast', 'leave', 'requestMembers', 'channelDestroyed',
 'createChannel', 'requestChannels', 'selfEnter', 'selfLeave',
 'requestMessageHistory', 'channelInfoUpdated', 'memberStatusUpdate',
 'findUsers', 'requestUsersRoster', 'addIgnored', 'addFriend',
 'removeIgnored', 'removeFriend', 'createPrivate', 'friendStatusUpdate',
 'userChatCommand', 'sysMessage', 'personalSysMessage', 'chatInitialization',
 'userInviteCommand', 'createInvite', 'receiveInvite',
 'receiveArchiveInvite', 'receiveMembersCount', 'receiveMembersDelta',
 'VOIPSettings', 'VOIPCredentials', 'setMuted', 'unsetMuted', 'logVivoxLogin'])
cs.CHAT_RESPONSES = FakeEnum('chatActionResponses', [
 'success', 'internalError', 'channelAlreadyExists', 'channelDestroyed',
 'passwordRequired', 'incorrectPassword', 'channelNotExists',
 'memberBanned', 'memberDisconnecting', 'notAllowed', 'connectTimeout',
 'initializationFailure', 'userNotExists', 'usersRosterLimitReached',
 'activeChannelsLimitReached', 'sqlError', 'incorrectCharacter',
 'addFriendError', 'addIgnoredError', 'userIgnoredError',
 'chatCommandError', 'memberAlreadyBanned', 'memberAlreadyModerator',
 'memberNotModerator', 'commandInCooldown', 'createPrivateError',
 'actionInCooldown', 'chatBanned', 'inviteCommandError', 'unknownCommand',
 'inviteCreateError', 'membersLimitReached', 'notSupported',
 'inviteCreationNotAllowed', 'incorrectCommandArgument',
 'invalidChannelName', 'setMutedError', 'unsetMutedError'])
cs.CHAT_COMMANDS = FakeEnum('chatCommands', [
 'initAck', 'updateMemeberStatus', 'findUser', 'addFriend', 'addIgnored',
 'removeFriend', 'removeIgnored', 'createPrivate', 'joinPrivate',
 'requestUsersRoster', 'requestFriendStatus', 'requestLastSysMessages',
 'getMembersCount', 'requestSystemChatChannels', 'findChatChannels',
 'getChannelInfoById', 'createChatChannel', 'destroyChatChannel',
 'requestChatChannelMembers', 'enterChatChannel', 'leaveChatChannel',
 'broadcast', 'CHGCHNLNAME', 'GREETING', 'BAN', 'UNBAN', 'ADDMODERATOR',
 'DELMODERATOR', 'USERBAN', 'USERUNBAN', 'requestVOIPCredentials',
 'setMuted', 'unsetMuted', 'logVivoxLogin'])
cs.CHAT_MEMBER_GROUP = FakeEnum('chatMemberGroup', [
 'member', 'channelOwner', 'channelModerator', 'chatAdmin'])
cs.CHAT_CHANNEL_PRIVATE = 8
cs.USERS_ROSTER_FRIEND = 1
cs.USERS_ROSTER_IGNORED = 2
cs.USERS_ROSTER_VOICE_MUTED = 4
cs.g_chatCooldownData = {}
cs.isOperationInCooldown = lambda data, cmd: False


class _Cooldown(Exception):
    pass


cs.ChatCommandInCooldown = _Cooldown
sys.modules['chat_shared'] = cs

MP = imp.load_source('MPChat', os.path.join(ROOT, 'src', 'Offline', 'MPChat.py'))

FAILS = []


def check(cond, msg):
    print ('PASS' if cond else 'FAIL') + ': ' + msg
    if not cond:
        FAILS.append(msg)


A = cs.CHAT_ACTIONS
C = cs.CHAT_COMMANDS
R = cs.CHAT_RESPONSES


def acts_of(msgs, action_item=None):
    result = []
    for m in msgs:
        if m.get('t') == 'act':
            if action_item is None or m['data']['action'] == action_item.index():
                result.append(m['data'])
    return result


def fresh_conn(server, key, nick, self_dbid=1):
    inbox = []
    conn = server.attach(key, inbox.append)
    server.on_hello(conn, nick, self_dbid)
    return conn, inbox


def cmd(server, conn, command, chan=0, i64=0, i16=0, s1=u'', s2=u'', req=7):
    server.on_cmd(conn, req, command.index(), chan, i64, i16, s1, s2)


# --------------------------------------------------------------------------
# Server logic
# --------------------------------------------------------------------------
server = MP._MPServer()
alice, a_in = fresh_conn(server, 'a', u'Alice')
bob, b_in = fresh_conn(server, 'b', u'Bob')
check(alice.vid != bob.vid and alice.vid > 100000, 'virtual IDs assigned (%s, %s)' % (alice.vid, bob.vid))
check(a_in[0]['t'] == 'welcome' and a_in[0]['nick'] == u'Alice', 'welcome with nick')
sys_act = acts_of(a_in, A.requestChannels)
check(len(sys_act) == 1 and sys_act[0]['requestID'] == -1 and sys_act[0]['data'][0]['id'] == 1
      and sys_act[0]['data'][0]['isSystem'], 'system channel pushed on welcome')
roster_act = acts_of(a_in, A.requestUsersRoster)
check(len(roster_act) == 1 and roster_act[0]['data'] == [], 'empty roster pushed (unblocks broadcast queue)')
del a_in[:]
del b_in[:]

# join system channel
cmd(server, alice, C.enterChatChannel, chan=1)
check(len(acts_of(a_in, A.selfEnter)) == 1, 'Alice selfEnter into common channel')
check(not acts_of(b_in, A.enter), 'Bob not notified (not a member yet)')
del a_in[:]
cmd(server, alice, C.requestChatChannelMembers, chan=1)
members = acts_of(a_in, A.requestMembers)[0]['data']
check(members == [{'id': 1, 'nickName': u'Alice', 'status': 0}], 'Alice sees herself under selfDBID=1: %r' % (members,))
del a_in[:]
cmd(server, bob, C.enterChatChannel, chan=1)
enter_acts = acts_of(a_in, A.enter)
check(len(enter_acts) == 1 and enter_acts[0]['originator'] == bob.vid
      and enter_acts[0]['originatorNickName'] == u'Bob', 'Alice notified about Bob under virtual ID')
cmd(server, bob, C.requestChatChannelMembers, chan=1)
members = acts_of(b_in, A.requestMembers)[0]['data']
by_id = dict(((m['id'], m['nickName']) for m in members))
check(by_id.get(alice.vid) == u'Alice' and by_id.get(1) == u'Bob', 'Bob member view: %r' % (by_id,))
del a_in[:]
del b_in[:]

# broadcast
cmd(server, alice, C.broadcast, chan=1, s1=u'Hello Bob!')
a_bc = acts_of(a_in, A.broadcast)
b_bc = acts_of(b_in, A.broadcast)
check(len(a_bc) == 1 and a_bc[0]['originator'] == 1 and a_bc[0]['data'] == u'Hello Bob!', 'sender echo with self ID')
check(len(b_bc) == 1 and b_bc[0]['originator'] == alice.vid and b_bc[0]['originatorNickName'] == u'Alice', 'Bob receives message under Alice virtual ID')
cmd(server, bob, C.broadcast, chan=1, s1=u'Hi Alice')
del a_in[:]
del b_in[:]

# create secured channel + find + password flow
cmd(server, alice, C.createChatChannel, s1=u'TestChan', s2=u'secret')
created = acts_of(a_in, A.createChannel)
check(len(created) == 1 and created[0]['data']['owner'] == 1 and created[0]['data']['isSecured'], 'createChannel act, owner=self view, secured')
chan_id = created[0]['data']['id']
del a_in[:]
cmd(server, alice, C.createChatChannel, s1=u'testchan')
dup = acts_of(a_in, A.createChannel)
check(len(dup) == 1 and dup[0]['actionResponse'] == R.channelAlreadyExists.index(), 'duplicate name rejected')
del a_in[:]
cmd(server, alice, C.enterChatChannel, chan=chan_id, s1=u'secret')
check(len(acts_of(a_in, A.selfEnter)) == 1, 'owner enters own secured channel')
cmd(server, bob, C.findChatChannels, s1=u'test', req=42)
found = acts_of(b_in, A.requestChannels)
check(len(found) == 1 and found[0]['requestID'] == 42 and found[0]['data'][0]['owner'] == alice.vid,
      'find channels echoes requestID, owner=virtual ID')
del b_in[:]
cmd(server, bob, C.enterChatChannel, chan=chan_id, s1=u'wrong')
fail = acts_of(b_in, A.selfEnter)
check(len(fail) == 1 and fail[0]['actionResponse'] == R.incorrectPassword.index(), 'wrong password rejected')
del b_in[:]
cmd(server, bob, C.enterChatChannel, chan=chan_id, s1=u'secret')
check(len(acts_of(b_in, A.selfEnter)) == 1, 'correct password enters')
del a_in[:]
del b_in[:]

# rename (owner), ban/unban
cmd(server, alice, C.CHGCHNLNAME, chan=chan_id, s1=u'RenamedChan')
upd = acts_of(b_in, A.channelInfoUpdated)
check(len(upd) == 1 and upd[0]['data']['channelName'] == u'RenamedChan', 'rename broadcast via channelInfoUpdated')
del a_in[:]
del b_in[:]
cmd(server, alice, C.BAN, chan=chan_id, i16=60, s1=u'Bob', s2=u'spam')
check(len(acts_of(b_in, A.selfLeave)) == 1, 'banned member kicked (selfLeave)')
check(len(acts_of(a_in, A.leave)) == 1, 'others notified about ban-leave')
del a_in[:]
del b_in[:]
cmd(server, bob, C.enterChatChannel, chan=chan_id, s1=u'secret')
fail = acts_of(b_in, A.selfEnter)
check(len(fail) == 1 and fail[0]['actionResponse'] == R.memberBanned.index(), 'banned member cannot re-enter')
del b_in[:]
cmd(server, alice, C.UNBAN, chan=chan_id, s1=u'Bob')
cmd(server, bob, C.enterChatChannel, chan=chan_id, s1=u'secret')
check(len(acts_of(b_in, A.selfEnter)) == 1, 'unbanned member can enter again')
del a_in[:]
del b_in[:]

# leave + destroy
cmd(server, bob, C.leaveChatChannel, chan=chan_id)
check(len(acts_of(b_in, A.selfLeave)) == 1 and len(acts_of(a_in, A.leave)) == 1, 'leave notifications')
del a_in[:]
del b_in[:]
cmd(server, alice, C.destroyChatChannel, chan=chan_id)
check(len(acts_of(a_in, A.channelDestroyed)) == 1, 'owner destroys channel')
cmd(server, bob, C.findChatChannels, s1=u'Renamed', req=9)
check(acts_of(b_in, A.requestChannels)[0]['data'] == [], 'destroyed channel no longer found')
del a_in[:]
del b_in[:]

# private channel
cmd(server, alice, C.createPrivate, i64=bob.vid, s1=u'Bob')
priv_acts = acts_of(a_in, A.requestChannels)
check(len(priv_acts) == 1 and priv_acts[0]['data'][0]['flags'] & cs.CHAT_CHANNEL_PRIVATE, 'private channel info to initiator')
check(priv_acts[0]['data'][0]['channelName'] == u'Bob', 'private channel named after interlocutor')
check(len(acts_of(a_in, A.selfEnter)) == 1, 'initiator joined private')
priv_id = priv_acts[0]['data'][0]['id']
check(len(acts_of(b_in, A.requestChannels)) == 1 and not acts_of(b_in, A.selfEnter), 'counterpart informed but not force-joined')
b_priv = acts_of(b_in, A.requestChannels)[0]['data'][0]
check(b_priv['channelName'] == u'Alice' and b_priv['owner'] == alice.vid, 'counterpart view: name=Alice, owner=virtual ID')
del a_in[:]
del b_in[:]
cmd(server, alice, C.broadcast, chan=priv_id, s1=u'psst')
check(len(acts_of(b_in, A.selfEnter)) == 1, 'counterpart auto-joined on first private message')
check(len(acts_of(b_in, A.broadcast)) == 1 and acts_of(b_in, A.broadcast)[0]['data'] == u'psst', 'private message delivered')
check(len(acts_of(a_in, A.enter)) == 1, 'initiator notified about counterpart join')
del a_in[:]
del b_in[:]

# roster: friends
cmd(server, alice, C.addFriend, i64=bob.vid, s1=u'Bob')
addf = acts_of(a_in, A.addFriend)
check(len(addf) == 1 and addf[0]['data'][:2] == [bob.vid, u'Bob'], 'addFriend act')
status = acts_of(a_in, A.friendStatusUpdate)
check(len(status) == 1 and status[0]['data'] == [[bob.vid, True]], 'friend online status')
del a_in[:]
cmd(server, alice, C.requestUsersRoster)
roster = acts_of(a_in, A.requestUsersRoster)[0]['data']
check(roster == [[bob.vid, u'Bob', cs.USERS_ROSTER_FRIEND]], 'roster contains friend: %r' % (roster,))
cmd(server, alice, C.removeFriend, i64=bob.vid)
check(acts_of(a_in, A.removeFriend)[0]['data'] == bob.vid, 'removeFriend act data=dbID')
del a_in[:]

# chat2 find users
server.on_chat2_find(bob, 5, u'ali', 50, 0)
finds = [m for m in b_in if m.get('t') == 'chat2find']
check(len(finds) == 1 and finds[0]['users'] == [[u'Alice', alice.vid, u'']], 'find users by substring: %r' % (finds,))
del b_in[:]
server.on_chat2_find(bob, 6, u'', 50, 0)
users = dict(((u[0], u[1]) for u in [m for m in b_in if m.get('t') == 'chat2find'][0]['users']))
check(users.get(u'Bob') == 1 and users.get(u'Alice') == alice.vid, 'find users includes self under selfDBID')
del b_in[:]

# nickname de-dup + history replay + disconnect
carol, c_in = fresh_conn(server, 'c', u'Alice')
check(carol.nick == u'Alice_2', 'duplicate nickname de-duped: %s' % carol.nick)
del c_in[:]
cmd(server, carol, C.enterChatChannel, chan=1)
replays = [a['data'] for a in acts_of(c_in, A.broadcast)]
check(replays == [u'Hello Bob!', u'Hi Alice'], 'history replayed to late joiner: %r' % (replays,))
del a_in[:]
server.detach('b')
left = acts_of(a_in, A.leave)
check(len(left) >= 1 and any(a['originator'] == bob.vid for a in left), 'disconnect notifies channel members')
check(bob.vid not in server.by_vid, 'disconnected player unregistered')

# --------------------------------------------------------------------------
# Transport over real loopback sockets
# --------------------------------------------------------------------------
import json
import socket
import time as _time


class FakePlayer(object):
    name = 'Commander'

    def __init__(self):
        self.actions = []

    def onChatAction(self, data):
        self.actions.append(data)


player = FakePlayer()
_BigWorld._player = player
PORT = 15999
state = MP._ClientState({'mode': 'host', 'host': '', 'port': PORT, 'nickname': u'Hosty'}, player)
state.transport.start()


def pump(rounds=60):
    for _ in xrange(rounds):
        state.transport.tick()
        _time.sleep(0.01)


pump()
check(state.my_vid > 0 and state.my_nick == u'Hosty', 'host self-connected over loopback (vid=%s)' % state.my_vid)
sys_delivered = [a for a in player.actions if a['action'] == A.requestChannels.index()]
check(len(sys_delivered) == 1 and sys_delivered[0]['data'][0]['channelName'] == '#chat:channels/common',
      'system channel delivered to player.onChatAction as bytes: %r' % (sys_delivered[:1],))

raw = socket.create_connection(('127.0.0.1', PORT), timeout=5)
raw_file = raw.makefile('r')
raw.sendall(json.dumps({'t': 'hello', 'nick': 'RawBob', 'selfDBID': 1, 'ver': 1}) + '\n')
welcome = None
deadline = _time.time() + 5
raw.settimeout(0.2)
buf = ''
raw_msgs = []
while _time.time() < deadline and welcome is None:
    state.transport.tick()
    try:
        chunk = raw.recv(65536)
        if chunk:
            buf += chunk
            while '\n' in buf:
                line, buf = buf.split('\n', 1)
                if line.strip():
                    msg = json.loads(line)
                    raw_msgs.append(msg)
                    if msg.get('t') == 'welcome':
                        welcome = msg
    except socket.timeout:
        pass
    _time.sleep(0.01)
check(welcome is not None and welcome['nick'] == u'RawBob', 'raw client welcomed: %r' % (welcome,))

player.actions = []
raw.sendall(json.dumps({'t': 'cmd', 'req': 1, 'cmd': C.enterChatChannel.index(), 'chan': 1, 'i64': 0, 'i16': 0, 's1': '', 's2': ''}) + '\n')
raw.sendall(json.dumps({'t': 'cmd', 'req': 2, 'cmd': C.broadcast.index(), 'chan': 1, 'i64': 0, 'i16': 0, 's1': 'hello host', 's2': ''}) + '\n')
# host player must join the channel too to receive the broadcast
state.send_cmd(3, C.enterChatChannel.index(), 1, 0, 0, u'', u'')
pump(80)
bcs = [a for a in player.actions if a['action'] == A.broadcast.index()]
check(any(a['data'] == 'hello host' and a['originatorNickName'] == 'RawBob' for a in bcs),
      'host player received raw client broadcast: %r' % (bcs,))
raw.close()

print '\n%d checks failed' % len(FAILS)
sys.exit(1 if FAILS else 0)
