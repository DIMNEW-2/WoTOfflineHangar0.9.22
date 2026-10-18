# -*- coding: utf-8 -*-
"""
Local (LAN) multiplayer chat for the WoT 0.9.21 offline hangar.

Restores the stock lobby messenger between several game clients running
this mod on the same local network: user channels (create / find / join /
leave / destroy, passwords, greetings, renames), channel messages with
history, member lists, private (1-to-1) channels, friends/ignored/muted
rosters and player search. No battles - this is hangar chat only.

How it works
------------
The stock client funnels every lobby-chat operation through two narrow
boundaries, both of which this module re-routes to a tiny LAN protocol:

* client -> server: ``ClientChat.__baseChatCommand`` (normally ends in
  ``self.base.chatCommandFromClient(...)``) and, for player search,
  ``BWChatProvider.__sendAction`` (BW_CHAT2 FIND_USERS_BY_NAME).
* server -> client: ``PlayerAccount.onChatAction(chatActionData)`` for
  lobby chat and ``protos.BW_CHAT2.onActionReceived(...)`` for search
  results. The stock ChannelsManager/UsersManager do the rest, so the
  whole stock chat UI works unchanged.

One of the clients acts as the host: it runs an embedded chat "server"
(channel/member/roster state, message relay) and also connects to itself
over loopback like any other client. In ``auto`` mode (default) a client
first looks for a host via UDP broadcast discovery and becomes the host
itself if none answers - just start the host machine's client first.

Because every offline account has databaseID=1, the host assigns a
virtual ID to each connection and rewrites user IDs per recipient (a
player always sees himself under his own databaseID, everyone else under
virtual IDs) - exactly like the real server presented unique IDs.

Configuration: ``mp_config.txt`` (key=value lines) in the game folder or
in ``res_mods/<version>/`` next to this mod. Keys: ``mode``
(auto/host/client/off), ``host`` (IP for mode=client), ``port``
(default 15777), ``nickname`` (empty = account name).
"""

import json
import select
import socket
import time
import traceback

import BigWorld

__all__ = ['on_account_ready']

PROTO_VER = 1
DEFAULT_PORT = 15777
_DISCOVER_QUERY = 'WOTMP1?'
_DISCOVER_REPLY = 'WOTMP1!'
_SYSTEM_CHANNEL_ID = 1
_SYSTEM_CHANNEL_NAME = u'#chat:channels/common'
_SYSTEM_GREETING = u'Локальный мультиплеер оффлайн-ангара (LAN). Приятной игры! / Local offline-hangar multiplayer chat.'
_VIRTUAL_ID_BASE = 100000
_FIRST_USER_CHANNEL_ID = 100
_HISTORY_LIMIT = 50
_TICK_PERIOD = 0.05

_STATE = None


def _log(msg):
    print '[OFFLINE-MP] %s' % msg


def _b(x):
    """unicode -> utf-8 str (client wrappers call unicode(x, 'utf-8'))."""
    if isinstance(x, unicode):
        return x.encode('utf-8')
    return x


def _u(x):
    if isinstance(x, unicode):
        return x
    if isinstance(x, str):
        return unicode(x, 'utf-8', errors='ignore')
    return u'' if x is None else unicode(x)


def _cs():
    import chat_shared
    return chat_shared


def _sanitize_nick(nick):
    nick = _u(nick).strip()
    cleaned = u''
    for ch in nick:
        if ch.isalnum() or ch in u'_-.':
            cleaned += ch
        elif ch == u' ':
            cleaned += u'_'
    nick = cleaned[:24]
    while len(nick) < 3:
        nick += u'_'
    return nick


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _load_config():
    cfg = {'mode': 'auto', 'host': '', 'port': DEFAULT_PORT, 'nickname': ''}
    text = None
    candidates = ['mp_config.txt',
     'res_mods/0.9.21.0.2/mp_config.txt',
     'res_mods/0.9.21.0.1/mp_config.txt',
     'res_mods/0.9.21.0.2/scripts/client/Offline/mp_config.txt',
     'res_mods/0.9.21.0.1/scripts/client/Offline/mp_config.txt']
    for path in candidates:
        try:
            with open(path, 'r') as f:
                text = f.read()
            _log('config loaded from %s' % path)
            break
        except Exception:
            continue
    if text is None:
        # Fall back to a ResMgr XML next to the mod (mp_config.xml).
        try:
            import ResMgr
            section = ResMgr.openSection('scripts/client/Offline/mp_config.xml')
            if section is not None:
                for key in cfg:
                    val = section.readString(key, '')
                    if val:
                        cfg[key] = val
                _log('config loaded from ResMgr mp_config.xml')
        except Exception:
            pass
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, val = line.split('=', 1)
            key = key.strip().lower()
            if key in cfg:
                cfg[key] = val.strip()
    cfg['mode'] = str(cfg['mode']).strip().lower() or 'auto'
    if cfg['mode'] not in ('auto', 'host', 'client', 'off'):
        cfg['mode'] = 'auto'
    try:
        cfg['port'] = int(cfg['port'])
    except Exception:
        cfg['port'] = DEFAULT_PORT
    cfg['host'] = str(cfg['host']).strip()
    cfg['nickname'] = _u(cfg['nickname']).strip()
    return cfg


# ---------------------------------------------------------------------------
# Embedded chat server (runs inside the host client)
# ---------------------------------------------------------------------------

class _Conn(object):
    __slots__ = ('key', 'send', 'vid', 'nick', 'self_dbid', 'joined',
     'friends', 'ignored', 'muted', 'alive')

    def __init__(self, key, send):
        self.key = key
        self.send = send  # callable(dict) - transport-level JSON send
        self.vid = 0
        self.nick = u''
        self.self_dbid = 1
        self.joined = set()
        self.friends = set()
        self.ignored = set()
        self.muted = set()
        self.alive = True


class _Chan(object):
    __slots__ = ('id', 'name', 'owner', 'password', 'greeting', 'flags',
     'system', 'members', 'invited', 'moderators', 'banned', 'history')

    def __init__(self, chan_id, name, owner=0, password=u'', system=False):
        self.id = chan_id
        self.name = name
        self.owner = owner
        self.password = password
        self.greeting = u''
        self.flags = 0
        self.system = system
        self.members = set()
        self.invited = set()
        self.moderators = set()
        self.banned = {}  # vid -> ban end time (float) or -1 (permanent)
        self.history = []  # (time, vid, nick, groupIdx, text)


class _MPServer(object):

    def __init__(self):
        self.conns = {}  # key -> _Conn
        self.by_vid = {}  # vid -> _Conn
        self.channels = {}  # cid -> _Chan
        self.next_vid = _VIRTUAL_ID_BASE
        self.next_cid = _FIRST_USER_CHANNEL_ID
        self.ever_nicks = {}  # vid -> nick (for unban by nickname)
        system = _Chan(_SYSTEM_CHANNEL_ID, _SYSTEM_CHANNEL_NAME, owner=-1, system=True)
        system.greeting = _SYSTEM_GREETING
        self.channels[system.id] = system

    # -- connection lifecycle ------------------------------------------------

    def attach(self, key, send):
        conn = _Conn(key, send)
        self.conns[key] = conn
        return conn

    def detach(self, key):
        conn = self.conns.pop(key, None)
        if conn is None:
            return
        conn.alive = False
        if conn.vid:
            self.by_vid.pop(conn.vid, None)
            for cid in list(conn.joined):
                chan = self.channels.get(cid)
                if chan is not None:
                    self._remove_member(chan, conn, notify_self=False)
            _log('player disconnected: %s (vid=%s)' % (_b(conn.nick), conn.vid))

    def on_hello(self, conn, nick, self_dbid):
        cs = _cs()
        self.next_vid += 1
        conn.vid = self.next_vid
        base = _sanitize_nick(nick) or u'Commander'
        taken = set((c.nick for c in self.by_vid.itervalues()))
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = u'%s_%d' % (base[:22], suffix)
            suffix += 1
        conn.nick = candidate
        try:
            conn.self_dbid = int(self_dbid)
        except Exception:
            conn.self_dbid = 1
        self.by_vid[conn.vid] = conn
        self.ever_nicks[conn.vid] = conn.nick
        conn.send({'t': 'welcome', 'vid': conn.vid, 'nick': conn.nick, 'ver': PROTO_VER})
        # System channels are pushed unsolicited, like the real server did
        # (requestID=-1 -> client stores and initializes them).
        system_data = [self._chan_data(chan, conn) for chan in self.channels.itervalues() if chan.system]
        self._act(conn, cs.CHAT_ACTIONS.requestChannels, data=system_data)
        # An (initially empty) roster unblocks the client's message queue.
        self._send_roster(conn)
        _log('player connected: %s (vid=%s, selfDBID=%s)' % (_b(conn.nick), conn.vid, conn.self_dbid))

    # -- view helpers --------------------------------------------------------

    def _view_id(self, vid, recipient):
        if recipient is not None and vid == recipient.vid and recipient.self_dbid:
            return recipient.self_dbid
        return vid

    def _translate_incoming_id(self, conn, dbid):
        """Client sends target IDs as it sees them: itself = self_dbid,
        others = virtual IDs."""
        try:
            dbid = int(dbid)
        except Exception:
            return 0
        if dbid == conn.self_dbid:
            return conn.vid
        return dbid

    def _group_of(self, chan, vid):
        cs = _cs()
        if vid == chan.owner:
            return cs.CHAT_MEMBER_GROUP.channelOwner.index()
        if vid in chan.moderators:
            return cs.CHAT_MEMBER_GROUP.channelModerator.index()
        return cs.CHAT_MEMBER_GROUP.member.index()

    def _chan_data(self, chan, recipient):
        name = chan.name
        if chan.flags & _cs().CHAT_CHANNEL_PRIVATE:
            # Private channels are shown under the interlocutor's name.
            parties = chan.members | chan.invited | {chan.owner}
            others = parties - {recipient.vid if recipient else 0}
            for vid in others:
                other = self.by_vid.get(vid)
                name = other.nick if other is not None else self.ever_nicks.get(vid, name)
                break
        return {'id': chan.id,
         'channelName': name,
         'owner': self._view_id(chan.owner, recipient),
         'ownerName': self.ever_nicks.get(chan.owner, u''),
         'isReadOnly': False,
         'isSystem': bool(chan.system),
         'isSecured': bool(chan.password),
         'greeting': chan.greeting,
         'flags': chan.flags,
         'notifyFlags': 0}

    def _members_data(self, chan, recipient):
        result = []
        for vid in sorted(chan.members):
            member = self.by_vid.get(vid)
            result.append({'id': self._view_id(vid, recipient),
             'nickName': member.nick if member is not None else self.ever_nicks.get(vid, u'Unknown'),
             'status': 0})
        return result

    def _act(self, conn, action_item, channel=0, data=None, requestID=-1,
             originator_vid=0, response_item=None, flags=0, group=None,
             when=None):
        if conn is None or not conn.alive:
            return
        cs = _cs()
        now = time.time() if when is None else when
        originator_conn = self.by_vid.get(originator_vid)
        act = {'requestID': requestID,
         'action': action_item.index(),
         'channel': channel,
         'actionResponse': (response_item or cs.CHAT_RESPONSES.success).index(),
         'originator': self._view_id(originator_vid, conn) if originator_vid else -1,
         'originatorNickName': originator_conn.nick if originator_conn is not None else u'',
         'group': cs.CHAT_MEMBER_GROUP.member.index() if group is None else group,
         'data': data,
         'time': now,
         'sentTime': now,
         'flags': flags}
        conn.send({'t': 'act', 'data': act})

    def _fail(self, conn, action_item, response_item, channel=0, requestID=-1, data=None):
        self._act(conn, action_item, channel=channel, data=data,
                  requestID=requestID, originator_vid=conn.vid,
                  response_item=response_item)

    def _send_roster(self, conn):
        cs = _cs()
        rows = []
        for vid in sorted(conn.friends | conn.ignored | conn.muted):
            roster = 0
            if vid in conn.friends:
                roster |= cs.USERS_ROSTER_FRIEND
            elif vid in conn.ignored:
                roster |= cs.USERS_ROSTER_IGNORED
            if vid in conn.muted:
                roster |= cs.USERS_ROSTER_VOICE_MUTED
            rows.append([self._view_id(vid, conn), self.ever_nicks.get(vid, u'Unknown'), roster])
        self._act(conn, cs.CHAT_ACTIONS.requestUsersRoster, data=rows)

    def _banned(self, chan, vid):
        end = chan.banned.get(vid)
        if end is None:
            return False
        if end > 0 and end < time.time():
            chan.banned.pop(vid, None)
            return False
        return True

    def _remove_member(self, chan, conn, notify_self=True):
        cs = _cs()
        if conn.vid not in chan.members:
            return
        chan.members.discard(conn.vid)
        conn.joined.discard(chan.id)
        if notify_self:
            self._act(conn, cs.CHAT_ACTIONS.selfLeave, channel=chan.id, originator_vid=conn.vid)
        for vid in list(chan.members):
            other = self.by_vid.get(vid)
            if other is not None:
                self._act(other, cs.CHAT_ACTIONS.leave, channel=chan.id, originator_vid=conn.vid)
        if not chan.members and not chan.system and not chan.invited:
            self.channels.pop(chan.id, None)

    def _add_member(self, chan, conn):
        cs = _cs()
        chan.members.add(conn.vid)
        chan.invited.discard(conn.vid)
        conn.joined.add(chan.id)
        self._act(conn, cs.CHAT_ACTIONS.selfEnter, channel=chan.id, originator_vid=conn.vid)
        for vid in list(chan.members):
            if vid == conn.vid:
                continue
            other = self.by_vid.get(vid)
            if other is not None:
                self._act(other, cs.CHAT_ACTIONS.enter, channel=chan.id, originator_vid=conn.vid)
        # Replay recent history to the joiner only.
        for when, vid, nick, group, text in chan.history[-30:]:
            self._act(conn, cs.CHAT_ACTIONS.broadcast, channel=chan.id,
                      data=text, originator_vid=vid, group=group, when=when)

    def _find_chan_by_name(self, name):
        lowered = _u(name).lower()
        for chan in self.channels.itervalues():
            if chan.name.lower() == lowered:
                return chan
        return None

    def _find_online_by_nick(self, nick):
        lowered = _u(nick).lower()
        for conn in self.by_vid.itervalues():
            if conn.nick.lower() == lowered:
                return conn
        return None

    # -- command dispatch ----------------------------------------------------

    def on_cmd(self, conn, req, cmd_idx, chan_id, i64, i16, s1, s2):
        cs = _cs()
        try:
            cmd = cs.CHAT_COMMANDS[cmd_idx]
        except Exception:
            cmd = None
        if cmd is None:
            _log('unknown chat command index %r' % (cmd_idx,))
            return
        s1 = _u(s1)
        s2 = _u(s2)
        C = cs.CHAT_COMMANDS
        A = cs.CHAT_ACTIONS
        R = cs.CHAT_RESPONSES
        if cmd == C.initAck or cmd == C.logVivoxLogin or cmd == C.requestLastSysMessages or cmd == C.requestVOIPCredentials:
            return
        if cmd == C.requestSystemChatChannels:
            data = [self._chan_data(chan, conn) for chan in self.channels.itervalues() if chan.system]
            self._act(conn, A.requestChannels, data=data)
        elif cmd == C.findChatChannels:
            token = s1.lower()
            found = []
            for chan in self.channels.itervalues():
                if chan.flags & cs.CHAT_CHANNEL_PRIVATE:
                    continue
                if not token or token in chan.name.lower():
                    found.append(self._chan_data(chan, conn))
            self._act(conn, A.requestChannels, data=found, requestID=req)
        elif cmd == C.createChatChannel:
            name = s1.strip()
            if name.startswith(u'#') and not name.startswith(u'#chat:'):
                name = name[1:]
            if not name:
                self._fail(conn, A.createChannel, R.invalidChannelName, requestID=req)
            elif self._find_chan_by_name(name) is not None:
                self._fail(conn, A.createChannel, R.channelAlreadyExists, requestID=req)
            else:
                self.next_cid += 1
                chan = _Chan(self.next_cid, name, owner=conn.vid, password=s2)
                self.channels[chan.id] = chan
                self._act(conn, A.createChannel, channel=chan.id,
                          data=self._chan_data(chan, conn), requestID=req)
                _log('channel created: %s #%s by %s' % (_b(name), chan.id, _b(conn.nick)))
        elif cmd == C.enterChatChannel:
            chan = self.channels.get(chan_id)
            if chan is None:
                self._fail(conn, A.selfEnter, R.channelNotExists, channel=chan_id, requestID=req)
            elif conn.vid in chan.members:
                self._act(conn, A.selfEnter, channel=chan.id, originator_vid=conn.vid, requestID=req)
            elif self._banned(chan, conn.vid):
                end = chan.banned.get(conn.vid, -1)
                self._fail(conn, A.selfEnter, R.memberBanned, channel=chan.id,
                           requestID=req, data={'banEndTime': end} if end and end > 0 else {})
            elif chan.password and s1 != chan.password and conn.vid != chan.owner:
                self._fail(conn, A.selfEnter, R.incorrectPassword, channel=chan.id, requestID=req)
            elif chan.flags & cs.CHAT_CHANNEL_PRIVATE and conn.vid not in chan.invited and conn.vid != chan.owner:
                self._fail(conn, A.selfEnter, R.notAllowed, channel=chan.id, requestID=req)
            else:
                self._add_member(chan, conn)
        elif cmd == C.leaveChatChannel:
            chan = self.channels.get(chan_id)
            if chan is not None:
                self._remove_member(chan, conn)
        elif cmd == C.destroyChatChannel:
            chan = self.channels.get(chan_id)
            if chan is None:
                self._fail(conn, A.channelDestroyed, R.channelNotExists, channel=chan_id, requestID=req)
            elif chan.system or conn.vid != chan.owner:
                self._fail(conn, A.channelDestroyed, R.notAllowed, channel=chan.id, requestID=req)
            else:
                members = [self.by_vid.get(vid) for vid in list(chan.members | chan.invited)]
                self.channels.pop(chan.id, None)
                for member in members:
                    if member is not None:
                        member.joined.discard(chan.id)
                        self._act(member, A.channelDestroyed, channel=chan.id, originator_vid=conn.vid)
                _log('channel destroyed: %s #%s' % (_b(chan.name), chan.id))
        elif cmd == C.broadcast:
            chan = self.channels.get(chan_id)
            if chan is None:
                self._fail(conn, A.broadcast, R.channelNotExists, channel=chan_id, requestID=req)
            elif conn.vid not in chan.members:
                self._fail(conn, A.broadcast, R.notAllowed, channel=chan.id, requestID=req)
            else:
                # A private counterpart joins automatically on first message.
                if chan.flags & cs.CHAT_CHANNEL_PRIVATE:
                    for vid in list(chan.invited):
                        other = self.by_vid.get(vid)
                        if other is not None:
                            self._add_member(chan, other)
                group = self._group_of(chan, conn.vid)
                _log('chat #%s %s: %s' % (chan.id, _b(conn.nick), _b(s1)[:200]))
                chan.history.append((time.time(), conn.vid, conn.nick, group, s1))
                if len(chan.history) > _HISTORY_LIMIT:
                    chan.history = chan.history[-_HISTORY_LIMIT:]
                for vid in list(chan.members):
                    member = self.by_vid.get(vid)
                    if member is not None:
                        self._act(member, A.broadcast, channel=chan.id, data=s1,
                                  originator_vid=conn.vid, group=group)
        elif cmd == C.requestChatChannelMembers:
            chan = self.channels.get(chan_id)
            if chan is not None:
                self._act(conn, A.requestMembers, channel=chan.id,
                          data=self._members_data(chan, conn), requestID=req)
        elif cmd == C.getMembersCount:
            chan = self.channels.get(chan_id)
            self._act(conn, A.receiveMembersCount, channel=chan_id,
                      data=len(chan.members) if chan is not None else 0, requestID=req)
        elif cmd == C.getChannelInfoById:
            chan = self.channels.get(int(i64))
            if chan is not None:
                self._act(conn, A.channelInfoUpdated, channel=chan.id,
                          data=self._chan_data(chan, conn), requestID=req)
        elif cmd == C.requestUsersRoster:
            self._send_roster(conn)
        elif cmd == C.requestFriendStatus:
            wanted = self._translate_incoming_id(conn, i64) if i64 and int(i64) > 0 else 0
            for vid in sorted(conn.friends):
                if wanted and vid != wanted:
                    continue
                self._act(conn, A.friendStatusUpdate,
                          data=[[self._view_id(vid, conn), vid in self.by_vid]], requestID=req)
        elif cmd in (C.addFriend, C.addIgnored, C.setMuted):
            target_vid = self._translate_incoming_id(conn, i64)
            target = self.by_vid.get(target_vid)
            name = target.nick if target is not None else (s1 or self.ever_nicks.get(target_vid, u'Unknown'))
            if target is not None:
                self.ever_nicks[target_vid] = target.nick
            if cmd == C.addFriend:
                conn.friends.add(target_vid)
                conn.ignored.discard(target_vid)
                action = A.addFriend
            elif cmd == C.addIgnored:
                conn.ignored.add(target_vid)
                conn.friends.discard(target_vid)
                action = A.addIgnored
            else:
                conn.muted.add(target_vid)
                action = A.setMuted
            self._act(conn, action, data=[self._view_id(target_vid, conn), name, 0], requestID=req)
            if cmd == C.addFriend:
                self._act(conn, A.friendStatusUpdate,
                          data=[[self._view_id(target_vid, conn), target_vid in self.by_vid]])
        elif cmd in (C.removeFriend, C.removeIgnored, C.unsetMuted):
            target_vid = self._translate_incoming_id(conn, i64)
            if cmd == C.removeFriend:
                conn.friends.discard(target_vid)
                action = A.removeFriend
            elif cmd == C.removeIgnored:
                conn.ignored.discard(target_vid)
                action = A.removeIgnored
            else:
                conn.muted.discard(target_vid)
                action = A.unsetMuted
            self._act(conn, action, data=self._view_id(target_vid, conn), requestID=req)
        elif cmd == C.createPrivate:
            target_vid = self._translate_incoming_id(conn, i64)
            target = self.by_vid.get(target_vid)
            if target is None:
                self._fail(conn, A.createPrivate, R.userNotExists, requestID=req)
            elif target_vid == conn.vid:
                self._fail(conn, A.createPrivate, R.createPrivateError, requestID=req)
            else:
                chan = None
                for candidate in self.channels.itervalues():
                    if candidate.flags & cs.CHAT_CHANNEL_PRIVATE and {conn.vid, target_vid} <= (candidate.members | candidate.invited | {candidate.owner}):
                        chan = candidate
                        break
                if chan is None:
                    self.next_cid += 1
                    chan = _Chan(self.next_cid, target.nick, owner=conn.vid)
                    chan.flags |= cs.CHAT_CHANNEL_PRIVATE
                    chan.invited.add(target_vid)
                    self.channels[chan.id] = chan
                self._act(conn, A.requestChannels, data=[self._chan_data(chan, conn)])
                self._act(target, A.requestChannels, data=[self._chan_data(chan, target)])
                if conn.vid not in chan.members:
                    self._add_member(chan, conn)
                else:
                    self._act(conn, A.selfEnter, channel=chan.id, originator_vid=conn.vid)
        elif cmd == C.findUser:
            # Legacy BW search; the lobby UI uses BW_CHAT2 search instead.
            self._act(conn, A.findUsers, data=[], requestID=req)
        elif cmd in (C.CHGCHNLNAME, C.GREETING, C.BAN, C.UNBAN, C.ADDMODERATOR, C.DELMODERATOR):
            self._channel_slash_command(conn, cmd, chan_id, i64, i16, s1, s2, req)
        elif cmd in (C.USERBAN, C.USERUNBAN):
            self._fail(conn, A.userChatCommand, R.notAllowed, channel=chan_id, requestID=req)
        else:
            _log('unhandled chat command %s (chan=%s)' % (cmd, chan_id))

    def _channel_slash_command(self, conn, cmd, chan_id, i64, i16, s1, s2, req):
        cs = _cs()
        A = cs.CHAT_ACTIONS
        R = cs.CHAT_RESPONSES
        C = cs.CHAT_COMMANDS
        chan = self.channels.get(chan_id)
        if chan is None or conn.vid not in chan.members:
            self._fail(conn, A.userChatCommand, R.channelNotExists, channel=chan_id, requestID=req)
            return
        is_owner = conn.vid == chan.owner
        is_mod = is_owner or conn.vid in chan.moderators
        if cmd in (C.CHGCHNLNAME, C.GREETING):
            if not is_mod or chan.system:
                self._fail(conn, A.userChatCommand, R.notAllowed, channel=chan.id, requestID=req)
                return
            if cmd == C.CHGCHNLNAME:
                new_name = s1.strip()
                if not new_name or self._find_chan_by_name(new_name) is not None:
                    self._fail(conn, A.userChatCommand, R.channelAlreadyExists, channel=chan.id, requestID=req)
                    return
                chan.name = new_name
            else:
                chan.greeting = s1
            for vid in list(chan.members):
                member = self.by_vid.get(vid)
                if member is not None:
                    self._act(member, A.channelInfoUpdated, channel=chan.id,
                              data=self._chan_data(chan, member), originator_vid=conn.vid)
            return
        # Moderation by nickname (s1).
        target = self._find_online_by_nick(s1)
        if cmd == C.UNBAN:
            if not is_mod:
                self._fail(conn, A.userChatCommand, R.notAllowed, channel=chan.id, requestID=req)
                return
            for vid, nick in list(self.ever_nicks.iteritems()):
                if nick.lower() == s1.lower():
                    chan.banned.pop(vid, None)
            return
        if target is None:
            self._fail(conn, A.userChatCommand, R.userNotExists, channel=chan.id, requestID=req)
            return
        if cmd == C.ADDMODERATOR or cmd == C.DELMODERATOR:
            if not is_owner:
                self._fail(conn, A.userChatCommand, R.notAllowed, channel=chan.id, requestID=req)
                return
            if cmd == C.ADDMODERATOR:
                chan.moderators.add(target.vid)
            else:
                chan.moderators.discard(target.vid)
            return
        if cmd == C.BAN:
            if not is_mod or target.vid == chan.owner:
                self._fail(conn, A.userChatCommand, R.notAllowed, channel=chan.id, requestID=req)
                return
            minutes = int(i16)
            chan.banned[target.vid] = -1 if minutes < 0 else time.time() + minutes * 60
            if target.vid in chan.members:
                self._remove_member(chan, target)
            _log('banned %s in #%s for %s min (%s)' % (_b(target.nick), chan.id, minutes, _b(s2)))

    def on_chat2_find(self, conn, req, pattern, max_results, online_only):
        token = _u(pattern).lower()
        try:
            limit = int(max_results) or 50
        except Exception:
            limit = 50
        users = []
        for other in self.by_vid.itervalues():
            if not token or token in other.nick.lower():
                users.append([other.nick, self._view_id(other.vid, conn), u''])
            if len(users) >= limit:
                break
        conn.send({'t': 'chat2find', 'req': req, 'users': users})


# ---------------------------------------------------------------------------
# Transport (non-blocking sockets polled from the BigWorld callback loop)
# ---------------------------------------------------------------------------

_WOULD_BLOCK = (11, 35, 10035)  # EAGAIN / EWOULDBLOCK (posix, osx, win)


class _SocketEnd(object):
    __slots__ = ('sock', 'inbuf', 'outbuf', 'server_conn')

    def __init__(self, sock):
        self.sock = sock
        self.inbuf = ''
        self.outbuf = ''
        self.server_conn = None

    def queue(self, obj):
        self.outbuf += json.dumps(obj) + '\n'

    def flush(self):
        while self.outbuf:
            try:
                sent = self.sock.send(self.outbuf)
            except socket.error as ex:
                if getattr(ex, 'errno', None) in _WOULD_BLOCK:
                    return True
                return False
            if sent <= 0:
                return True
            self.outbuf = self.outbuf[sent:]
        return True

    def read_lines(self):
        try:
            data = self.sock.recv(65536)
        except socket.error as ex:
            if getattr(ex, 'errno', None) in _WOULD_BLOCK:
                return []
            raise
        if not data:
            raise EOFError('socket closed')
        self.inbuf += data
        lines = []
        while '\n' in self.inbuf:
            line, self.inbuf = self.inbuf.split('\n', 1)
            if line.strip():
                lines.append(line)
        return lines


class _Transport(object):

    def __init__(self, cfg, on_client_msg):
        self.cfg = cfg
        self.on_client_msg = on_client_msg  # callable(dict) for client-side msgs
        self.server = None
        self.listen_sock = None
        self.udp_sock = None
        self.server_ends = {}  # sock -> _SocketEnd
        self.client_end = None
        self.client_sock = None
        self.connecting_since = 0.0
        self.state = 'idle'  # discovering / connecting / online / hosting
        self.client_phase = 'none'  # none / connecting / online
        self.discover_deadline = 0.0
        self.next_discover = 0.0
        self.next_retry = 0.0
        self.welcomed = False

    # -- setup ---------------------------------------------------------------

    def start(self):
        mode = self.cfg['mode']
        if mode == 'off':
            self.state = 'off'
            return
        self._make_udp()
        if mode == 'host':
            try:
                self._become_host()
            except Exception:
                _log('cannot host on port %s (already in use?), falling back to discovery:' % self.cfg['port'])
                traceback.print_exc()
                self.server = None
                self.listen_sock = None
                self._start_discovery()
        elif mode == 'client' and self.cfg['host']:
            self._connect(self.cfg['host'], self.cfg['port'])
        else:
            self._start_discovery()

    def _make_udp(self):
        if self.udp_sock is not None:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', self.cfg['port']))
            sock.setblocking(0)
            self.udp_sock = sock
        except Exception:
            _log('UDP discovery socket failed:')
            traceback.print_exc()

    def _start_discovery(self):
        self.state = 'discovering'
        self.discover_deadline = time.time() + 1.5
        self.next_discover = 0.0
        _log('looking for a LAN host (UDP discovery on port %s)...' % self.cfg['port'])

    def _become_host(self):
        if self.server is None:
            self.server = _MPServer()
        if self.listen_sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('', self.cfg['port']))
            sock.listen(16)
            sock.setblocking(0)
            self.listen_sock = sock
        self.state = 'hosting'
        _log('hosting local multiplayer on port %s (this client is the host)' % self.cfg['port'])
        self._connect('127.0.0.1', self.cfg['port'])

    def _connect(self, host, port):
        self._close_client()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setblocking(0)
        err = sock.connect_ex((host, port))
        self.client_sock = sock
        self.client_end = _SocketEnd(sock)
        self.connecting_since = time.time()
        self.welcomed = False
        self.client_phase = 'connecting'
        if self.server is None:
            self.state = 'connecting'
        _log('connecting to host %s:%s (err=%s)...' % (host, port, err))

    def _close_client(self):
        if self.client_sock is not None:
            try:
                self.client_sock.close()
            except Exception:
                pass
        self.client_sock = None
        self.client_end = None
        self.welcomed = False
        self.client_phase = 'none'

    def send_to_server(self, obj):
        if self.client_end is not None and self.welcomed:
            self.client_end.queue(obj)
            return True
        return False

    @property
    def is_online(self):
        return self.client_end is not None and self.welcomed

    # -- tick ----------------------------------------------------------------

    def tick(self):
        if self.state == 'off':
            return
        now = time.time()
        rlist = []
        if self.udp_sock is not None:
            rlist.append(self.udp_sock)
        if self.listen_sock is not None:
            rlist.append(self.listen_sock)
        if self.client_sock is not None:
            rlist.append(self.client_sock)
        rlist.extend(self.server_ends.keys())
        wlist = [self.client_sock] if self.client_sock is not None and self.client_phase == 'connecting' else []
        if rlist or wlist:
            try:
                readable, writable, _ = select.select(rlist, wlist, [], 0)
            except Exception:
                readable, writable = [], []
        else:
            readable, writable = [], []
        for sock in readable:
            if sock is self.udp_sock:
                self._on_udp()
            elif sock is self.listen_sock:
                self._on_accept()
            elif sock is self.client_sock:
                self._on_client_read()
            else:
                self._on_server_read(sock)
        if self.client_sock is not None and self.client_phase == 'connecting':
            if self.client_sock in writable or now - self.connecting_since > 0.2:
                err = self.client_sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err == 0:
                    self.client_phase = 'online'
                    if self.server is None:
                        self.state = 'online'
                    self.on_client_msg({'t': '_connected'})
                elif now - self.connecting_since > 3.0:
                    self._on_client_lost('connect failed (%s)' % err)
        if self.client_phase == 'none' and self.state == 'connecting' and now >= self.next_retry:
            self._connect(self.cfg['host'], self.cfg['port'])
        if self.state == 'discovering':
            if now >= self.next_discover:
                self.next_discover = now + 0.3
                self._send_discovery()
            if now > self.discover_deadline:
                try:
                    self._become_host()
                except Exception:
                    _log('cannot become host (port busy?), retrying discovery:')
                    traceback.print_exc()
                    self.discover_deadline = now + 3.0
        # flush outputs
        if self.client_end is not None:
            if not self.client_end.flush():
                self._on_client_lost('send failed')
        for sock, end in list(self.server_ends.iteritems()):
            if not end.flush():
                self._drop_server_end(sock)

    def _send_discovery(self):
        if self.udp_sock is None:
            return
        try:
            self.udp_sock.sendto(_DISCOVER_QUERY, ('255.255.255.255', self.cfg['port']))
        except Exception:
            pass

    def _on_udp(self):
        try:
            data, addr = self.udp_sock.recvfrom(1024)
        except Exception:
            return
        if data.startswith(_DISCOVER_QUERY):
            if self.server is not None and self.listen_sock is not None:
                try:
                    self.udp_sock.sendto('%s %s' % (_DISCOVER_REPLY, self.cfg['port']), addr)
                except Exception:
                    pass
        elif data.startswith(_DISCOVER_REPLY):
            if self.state == 'discovering':
                try:
                    port = int(data.split()[1])
                except Exception:
                    port = self.cfg['port']
                _log('host found at %s:%s' % (addr[0], port))
                self._connect(addr[0], port)

    def _on_accept(self):
        try:
            sock, addr = self.listen_sock.accept()
        except Exception:
            return
        sock.setblocking(0)
        end = _SocketEnd(sock)
        end.server_conn = self.server.attach(sock, end.queue)
        self.server_ends[sock] = end

    def _drop_server_end(self, sock):
        end = self.server_ends.pop(sock, None)
        if end is None:
            return
        try:
            sock.close()
        except Exception:
            pass
        if self.server is not None:
            self.server.detach(sock)

    def _on_server_read(self, sock):
        end = self.server_ends.get(sock)
        if end is None:
            return
        try:
            lines = end.read_lines()
        except Exception:
            self._drop_server_end(sock)
            return
        for line in lines:
            try:
                msg = json.loads(line)
            except Exception:
                continue
            self._server_dispatch(end.server_conn, msg)

    def _server_dispatch(self, conn, msg):
        if conn is None:
            return
        mtype = msg.get('t')
        if mtype == 'hello':
            self.server.on_hello(conn, msg.get('nick', u''), msg.get('selfDBID', 1))
        elif not conn.vid:
            return
        elif mtype == 'cmd':
            self.server.on_cmd(conn, msg.get('req', -1), msg.get('cmd', -1),
                               msg.get('chan', 0), msg.get('i64', 0),
                               msg.get('i16', 0), msg.get('s1', u''), msg.get('s2', u''))
        elif mtype == 'chat2find_req':
            args = msg.get('args', {})
            self.server.on_chat2_find(conn, msg.get('req', 0), args.get('strArg1', u''),
                                      args.get('int32Arg1', 50), args.get('int64Arg1', 0))

    def _on_client_read(self):
        try:
            lines = self.client_end.read_lines()
        except Exception:
            self._on_client_lost('read failed')
            return
        for line in lines:
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if msg.get('t') == 'welcome':
                self.welcomed = True
            self.on_client_msg(msg)

    def _on_client_lost(self, reason):
        was_hosting = self.server is not None
        _log('disconnected from host: %s' % reason)
        self._close_client()
        self.on_client_msg({'t': '_disconnected'})
        if was_hosting:
            # Our own server is still running; re-attach over loopback.
            self.state = 'hosting'
            self._connect('127.0.0.1', self.cfg['port'])
        elif self.cfg['mode'] == 'client' and self.cfg['host']:
            self.state = 'connecting'
            self.next_retry = time.time() + 2.0
        else:
            self._start_discovery()


# ---------------------------------------------------------------------------
# Client integration
# ---------------------------------------------------------------------------

class _ClientState(object):

    def __init__(self, cfg, player):
        self.cfg = cfg
        self.player = player
        self.transport = _Transport(cfg, self._on_msg)
        self.out_queue = []
        self.pending_acts = []
        self.welcome_time = 0.0
        self.next_messenger_attempt = 0.0
        self.my_vid = 0
        self.my_nick = u''
        self.friend_status_action_idx = _cs().CHAT_ACTIONS.friendStatusUpdate.index()

    # -- outgoing -------------------------------------------------------------

    def send_cmd(self, req, cmd_idx, chan, i64, i16, s1, s2):
        msg = {'t': 'cmd', 'req': req, 'cmd': cmd_idx, 'chan': chan,
         'i64': i64, 'i16': i16, 's1': _u(s1), 's2': _u(s2)}
        if not self.transport.send_to_server(msg):
            if len(self.out_queue) < 200:
                self.out_queue.append(msg)

    def send_chat2_find(self, req, args):
        args = dict(args or {})
        msg = {'t': 'chat2find_req', 'req': req,
         'args': {'strArg1': _u(args.get('strArg1', '')),
                  'int32Arg1': args.get('int32Arg1', 50),
                  'int64Arg1': args.get('int64Arg1', 0)}}
        if self.transport.send_to_server(msg):
            return True
        # Offline: answer locally with an empty result so the UI completes.
        self._deliver_chat2_find(req, [])
        return True

    # -- incoming -------------------------------------------------------------

    def _on_msg(self, msg):
        mtype = msg.get('t')
        if mtype == '_connected':
            self.transport.client_end.queue({'t': 'hello',
             'nick': self.cfg['nickname'] or _u(getattr(self.player, 'name', '') or u'Commander'),
             'selfDBID': self._self_dbid(),
             'ver': PROTO_VER})
        elif mtype == 'welcome':
            self.my_vid = msg.get('vid', 0)
            self.my_nick = _u(msg.get('nick', u''))
            self.welcome_time = time.time()
            self.next_messenger_attempt = 0.0
            _log('connected as %s (vid=%s, hosting=%s)' % (_b(self.my_nick), self.my_vid, self.transport.server is not None))
            try:
                if self.my_nick and _u(getattr(self.player, 'name', '')) != self.my_nick:
                    self.player.name = _b(self.my_nick)
            except Exception:
                pass
            queued, self.out_queue = self.out_queue, []
            for queued_msg in queued:
                self.transport.send_to_server(queued_msg)
        elif mtype == 'act':
            self._deliver_act(msg.get('data') or {})
        elif mtype == 'chat2find':
            self._deliver_chat2_find(msg.get('req', 0), msg.get('users') or [])
        elif mtype == '_disconnected':
            self.my_vid = 0
            self.pending_acts = []

    def _self_dbid(self):
        try:
            from gui.shared.utils import getPlayerDatabaseID
            return int(getPlayerDatabaseID() or 1)
        except Exception:
            return 1

    def _messenger_ready(self):
        """True once the stock messenger finished its lobby connect:
        the BW proto is connected (its listeners would otherwise miss
        server pushes) and the MIGRATION proto has its contacts/messages
        proxies (the lobby GUI views dereference them unconditionally
        and crash while they are None)."""
        try:
            from messenger import MessengerEntry
            protos = MessengerEntry.g_instance.protos
            bw = protos.BW
            if bw is None or not bw.isConnected():
                return False
            migration = protos.MIGRATION
            return migration.contacts is not None and migration.messages is not None
        except ImportError:
            return True  # stub/test environment without the messenger
        except Exception:
            return False

    def _connect_all_protos(self, entry, scope):
        """Connect every enabled messenger proto plugin, one by one.

        ProtoPluginsDecorator.connect() walks all plugins in a single
        call; offline BW_CHAT2 raises inside VOIP start (the fake server
        settings have no 'voipUserDomain') and aborts the walk, so the
        remaining plugins would only connect depending on dict order.
        Mirror the decorator's enabled-filtering but isolate each
        plugin in its own try/except."""
        from messenger.proto import ServerSettings
        plugins = getattr(entry.protos, '__readonly__', None)
        settings = getattr(ServerSettings, '__readonly__', None)
        if not plugins or settings is None:
            entry.protos.connect(scope)
            return
        for protoName, plugin in plugins.iteritems():
            if protoName not in settings or not settings[protoName].isEnabled():
                continue
            try:
                plugin.connect(scope)
            except Exception:
                _log('messenger lobby init: %s proto connect failed:' % protoName)
                traceback.print_exc()

    def _ensure_messenger_lobby(self):
        """Replay MessengerEntry's lobby handlers, which never fire
        offline: Manager does not call PlayerAccount.onAccountBecomePlayer,
        so g_playerEvents.onAccountBecomePlayer / onAccountShowGUI stay
        silent and the messenger never connects (MIGRATION contacts /
        messages stay None and every stock messenger window crashes).
        Each stock step is attempted independently; failures are logged
        and retried on a later tick. setFilters is skipped: it already
        ran in MessengerEntry.init() at game start."""
        try:
            from messenger import MessengerEntry, g_settings
            from messenger.m_constants import MESSENGER_SCOPE
            entry = MessengerEntry.g_instance
        except Exception:
            _log('messenger lobby init: imports failed:')
            traceback.print_exc()
            return
        scope = MESSENGER_SCOPE.LOBBY
        # MessengerEntry.__pe_onAccountBecomePlayer body, then the
        # public onAccountShowGUI (playerHelper.initPersonalAccount,
        # storage.restoreFromCache, protos.view(LOBBY)).
        steps = (('g_settings.update', g_settings.update, ()),
         ('storage.switch', entry.storage.switch, (scope,)),
         ('protos.connect', lambda: self._connect_all_protos(entry, scope), ()),
         ('gui.switch', entry.gui.switch, (scope,)),
         ('onAccountShowGUI', entry.onAccountShowGUI, ()))
        for name, fn, args in steps:
            try:
                fn(*args)
            except Exception:
                _log('messenger lobby init: %s failed:' % name)
                traceback.print_exc()
        # The decorator's connect() walks all plugins in one call; if a
        # sibling plugin raised midway, connect the two plugins the
        # lobby chat actually needs directly.
        try:
            protos = entry.protos
            if not protos.BW.isConnected():
                protos.BW.connect(scope)
            if protos.MIGRATION.contacts is None or protos.MIGRATION.messages is None:
                protos.MIGRATION.connect(scope)
        except Exception:
            _log('messenger lobby init: direct proto connect failed:')
            traceback.print_exc()
        _log('messenger lobby init done, ready=%s' % self._messenger_ready())

    def flush_pending(self):
        if not self._messenger_ready():
            now = time.time()
            if now >= self.next_messenger_attempt:
                self.next_messenger_attempt = now + 3.0
                self._ensure_messenger_lobby()
        if not self.pending_acts:
            return
        waited = time.time() - self.welcome_time if self.welcome_time else 0.0
        if not self._messenger_ready():
            if waited > 15.0:
                _log('messenger still not ready after 15s, delivering %d queued acts anyway' % len(self.pending_acts))
            else:
                return
        pending, self.pending_acts = self.pending_acts, []
        for data in pending:
            self._deliver_act_now(data)

    def _deliver_act(self, data):
        if not self._messenger_ready() and not (self.welcome_time and time.time() - self.welcome_time > 15.0):
            self.pending_acts.append(data)
            return
        self._deliver_act_now(data)

    def _deliver_act_now(self, data):
        player = BigWorld.player() or self.player
        if player is None:
            return
        try:
            if data.get('action') == self.friend_status_action_idx:
                pairs = data.get('data') or []
                data['data'] = dict(((int(k), bool(v)) for k, v in pairs))
            player.onChatAction(_deep_bytes(data))
        except Exception:
            _log('onChatAction delivery failed:')
            traceback.print_exc()

    def _deliver_chat2_find(self, req, users):
        try:
            import cPickle
            from messenger import MessengerEntry
            from messenger_common_chat2 import MESSENGER_ACTION_IDS, messageArgs
            result = [(_u(name), long(dbid), _u(clan)) for name, dbid, clan in users]
            args = messageArgs(strArg1=cPickle.dumps(result))
            proto = MessengerEntry.g_instance.protos.BW_CHAT2
            proto.onActionReceived(MESSENGER_ACTION_IDS.RESPONSE_SUCCESS, req, args)
        except Exception:
            _log('chat2 find-users delivery failed:')
            traceback.print_exc()


def _deep_bytes(obj):
    """Recursively convert unicode -> utf-8 str (stock client wrappers
    expect byte strings, exactly as they arrived from the real server)."""
    if isinstance(obj, unicode):
        return obj.encode('utf-8')
    if isinstance(obj, dict):
        return dict(((_b(k), _deep_bytes(v)) for k, v in obj.iteritems()))
    if isinstance(obj, (list, tuple)):
        return [_deep_bytes(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Patches
# ---------------------------------------------------------------------------

_PATCHED = False


def _install_patches(state):
    global _PATCHED
    if _PATCHED:
        return
    import chat_shared
    from ClientChat import ClientChat

    def _mp_base_chat_command(self, command, channelID=0, int64Arg=0, int16Arg=0,
                              stringArg1='', stringArg2='', ignoreCooldown=True,
                              requestID=None):
        if requestID is None:
            requestID = self.acquireRequestID()
        if chat_shared.isOperationInCooldown(chat_shared.g_chatCooldownData, command):
            if not ignoreCooldown:
                raise chat_shared.ChatCommandInCooldown(command)
        state.send_cmd(requestID, command.index(), channelID, int64Arg,
                       int16Arg, stringArg1, stringArg2)
        return

    def _mp_base_invite_command(self, command, inviteType=None, receiverName='',
                                int64Arg=0, int16Arg=0, stringArg1='', stringArg2='',
                                requestID=None):
        _log('invite command dropped (prebattle invites are not supported in local MP)')
        return

    def _mp_drop_invite_ack(self, inviteID, requestID=None):
        return

    ClientChat._ClientChat__baseChatCommand = _mp_base_chat_command
    ClientChat._ClientChat__baseInviteCommand = _mp_base_invite_command
    ClientChat.acceptPrebattleInvite = _mp_drop_invite_ack
    ClientChat.rejectInvite = _mp_drop_invite_ack

    try:
        from messenger.proto.bw_chat2.provider import BWChatProvider
        from messenger_common_chat2 import MESSENGER_ACTION_IDS

        def _mp_send_action(self, actionID, reqID, args=None):
            if actionID == MESSENGER_ACTION_IDS.FIND_USERS_BY_NAME:
                return state.send_chat2_find(reqID, args)
            return False

        BWChatProvider._BWChatProvider__sendAction = _mp_send_action
    except Exception:
        _log('BWChatProvider patch failed (player search will not work):')
        traceback.print_exc()

    _PATCHED = True
    _log('ClientChat/BWChatProvider transport patched -> local multiplayer')


def _tick():
    global _STATE
    try:
        if _STATE is not None:
            _STATE.flush_pending()
            _STATE.transport.tick()
    except Exception:
        _log('tick failed:')
        traceback.print_exc()
    BigWorld.callback(_TICK_PERIOD, _tick)


def on_account_ready(player):
    """Entry point called by Offline.Manager once the fake account exists
    (before showGUI). Safe to call repeatedly."""
    global _STATE
    if _STATE is not None:
        return
    cfg = _load_config()
    if cfg['mode'] == 'off':
        _log('local multiplayer disabled by config (mode=off)')
        return
    # Offline the account entity keeps databaseID == 0 (never populated
    # by a server). The messenger treats 0 as "no current player":
    # CurrentUserEntity is never created and isCurrentPlayer() never
    # matches channel owners / private interlocutors. Align it with the
    # self DBID the MP protocol already assumes (same trick Manager
    # uses for player.name).
    try:
        if not getattr(player, 'databaseID', 0):
            player.databaseID = 1
            _log('player.databaseID force-set to 1 (was 0)')
    except Exception:
        _log('player.databaseID force-set failed (non-fatal):')
        traceback.print_exc()
    # The stock PlayerAccount.onAccountBecomePlayer (which switches the
    # chat proxy) is replaced by Manager's per-subsystem forcing, so make
    # sure chat callbacks are wired to our account entity.
    try:
        from ChatManager import chatManager
        if chatManager.playerProxy is not player:
            chatManager.switchPlayerProxy(player)
            _log('chatManager.switchPlayerProxy(player) applied')
    except Exception:
        _log('switchPlayerProxy failed:')
        traceback.print_exc()
    state = _ClientState(cfg, player)
    _STATE = state
    _install_patches(state)
    state.transport.start()
    BigWorld.callback(_TICK_PERIOD, _tick)
    _log('local multiplayer started: mode=%s port=%s nickname=%r' % (
        cfg['mode'], cfg['port'], _b(cfg['nickname'])))
