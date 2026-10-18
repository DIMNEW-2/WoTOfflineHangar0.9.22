# -*- coding: utf-8 -*-
"""LAN training rooms for the offline hangar (stock GUI only).

Companion to Offline.MPChat: reuses its newline-JSON LAN transport and its
BW chat channel machinery to restore the *stock* training-room flow of
WoT 0.9.21 between clients connected to the same local host:

  * room list (``requestPrebattles`` / ``requestPrebattlesByName`` /
    ``requestPrebattleRoster`` -> ``receivePrebattles*`` stock blobs),
  * create / join / leave / kick (``prb_createTraining``, ``prb_join``,
    ``prb_leave``, ``prb_kick`` -> ``onPrebattleJoined/Left`` /
    ``onKickedFromPrebattle`` / ``onPrebattleJoinFailure``),
  * rosters and ready states (``prb_ready`` / ``prb_notReady`` /
    ``prb_assign`` / ``prb_swapTeams`` -> ``updatePrebattle`` pickled
    updates exactly like the real server sends to ``ClientPrebattle``),
  * room settings (arena / round length / open-closed / comment / VOIP),
  * team ready states, invites (old-style ``player.prebattleInvites``)
    and a per-room stock prebattle chat channel.

Battle launch is intentionally NOT implemented (matches the offline
hangar scope): when both teams are set ready, the host reverts both team
states to NOT_READY and pushes a stock system message explaining that
battles are unavailable in local multiplayer.

Nothing here invents GUI: every client-side patch only replaces the
network methods of ``PlayerAccount`` (``prb_*`` / ``requestPrebattles*``),
which Offline.Manager had neutralised, and feeds the stock callbacks
(``onPrebattleJoined``, ``updatePrebattle``, ``receivePrebattles`` ...).

Identity note: prebattle rosters identify *self* by the local player
entity ID (``BigWorld.player().id``), which collides between offline
clients. The host therefore rewrites every roster per recipient: the
recipient's own row carries their entity ID / self DBID, everyone else
carries their stable virtual DBID (vid) from MPChat. Incoming
``prb_assign``/``prb_kick`` player IDs and ``prb_sendInvites`` DBIDs are
translated back the same way.

Wire format: MPChat messages with ``t='prb'``. Client -> host:
``{t:'prb', cmd:<op>, req:<n>, ...}``; callback ops are answered with
``{t:'prb', ev:'resp', req:<n>, code:<resultID>}``. Host -> client
events: ``joined`` / ``upd`` / ``left`` / ``kicked`` / ``joinFailure`` /
``list`` / ``rosterList`` / ``invite`` / ``inviteDel`` / ``sysNote``.
JSON turns int dict keys into strings, so settings / list info dicts
have their keys re-inted on the client before pickling.
"""

import cPickle
import time
import traceback
import zlib

_BATTLE_UNAVAILABLE_TEXT = u'Запуск боя недоступен в локальной сети (офлайн-ангар): тренировочные комнаты работают без выхода в бой.'
_TEAM_CAPACITY = 15
_ROOM_CAPACITY = 30
_BATTLE_CANCEL_DELAY = 1.0
_PRB_CHANNEL_NAME = u'#chat:channels/training'


def _log(msg):
    print('[OFFLINE-MP-TRN] %s' % (msg,))


def _const():
    import constants
    return constants


def _cs():
    import chat_shared
    return chat_shared


def _u(x):
    if isinstance(x, unicode):
        return x
    if isinstance(x, str):
        return unicode(x, 'utf-8', errors='ignore')
    return u'' if x is None else unicode(x)


def _encode_roster(team, assigned):
    return team | ((0 if assigned else 1) << 4)


def _decode_roster(roster):
    return (roster & 15, not roster & 240)


def _veh_descr():
    """Hex compact descriptor of the current vehicle ('' if unknown).

    vehCompDescr in prebattle rosters is the packed byte string from
    vehicles.VehicleDescr.makeCompactDescr() - NOT the item type CD.
    """
    try:
        from CurrentVehicle import g_currentVehicle
        item = g_currentVehicle.item
        if item is not None and item.descriptor is not None:
            return item.descriptor.makeCompactDescr().encode('hex')
    except Exception:
        pass
    return ''


def _veh_bytes(hex_descr):
    """Decode a wire (hex) vehCompDescr back to the packed byte string."""
    try:
        return str(hex_descr).decode('hex') if hex_descr else ''
    except Exception:
        return ''


# ---------------------------------------------------------------------------
# Server side (runs inside the MPChat host process)
# ---------------------------------------------------------------------------

class _Member(object):
    __slots__ = ('vid', 'roster', 'state', 'veh_descr', 'joined_at')

    def __init__(self, vid, roster, state, veh_descr):
        self.vid = vid
        self.roster = roster
        self.state = state
        self.veh_descr = veh_descr
        self.joined_at = time.time()


class _Room(object):
    __slots__ = ('room_id', 'creator_vid', 'arena_type_id', 'round_length',
                 'comment', 'is_opened', 'voip_channels', 'create_time',
                 'members', 'team_states', 'invited', 'chan_id')

    def __init__(self, room_id, creator_vid):
        self.room_id = room_id
        self.creator_vid = creator_vid
        self.arena_type_id = 0
        self.round_length = 600
        self.comment = u''
        self.is_opened = True
        self.voip_channels = 0
        self.create_time = int(time.time())
        self.members = {}
        self.team_states = {}
        self.invited = set()
        self.chan_id = 0


class _TrainingServer(object):
    """Authoritative room registry, attached to an MPChat _MPServer.

    Uses MPChat internals (by_vid, _new_chan/_add_member/_remove_member/
    _chan_data/_act/channels) for presence and the room chat channel.
    """

    def __init__(self, mp_server):
        self.mp = mp_server
        self.rooms = {}
        self.by_vid = {}
        self.invites = {}  # (target_vid, room_id) -> dict
        self.next_id = 1
        self._deferred = []

    # -- plumbing ---------------------------------------------------------

    def _conn(self, vid):
        return self.mp.by_vid.get(vid)

    def _send(self, conn, **msg):
        if conn is None:
            return
        msg['t'] = 'prb'
        conn.send(msg)

    def _resp(self, conn, msg, ok):
        req = msg.get('req')
        if req is None:
            return
        try:
            req = int(req)
        except Exception:
            return
        self._send(conn, ev='resp', req=req, code=0 if ok else -1)

    def _room_of(self, conn):
        return self.by_vid.get(conn.vid)

    # -- per-recipient views ------------------------------------------------

    def _view_dbid(self, viewer, vid):
        if viewer is not None and vid == viewer.vid:
            return viewer.self_dbid or 1
        return vid

    def _view_rid(self, viewer, vid):
        if viewer is not None and vid == viewer.vid:
            return viewer.entity_id or vid
        return vid

    def _account_tuple(self, viewer, room, vid):
        member = room.members[vid]
        conn = self._conn(vid)
        nick = conn.nick if conn is not None else u''
        return [self._view_rid(viewer, vid), nick,
                self._view_dbid(viewer, vid), [], member.roster,
                member.state, int(member.joined_at), member.veh_descr,
                0, 0, u'']

    def _roster_rows(self, viewer, room):
        members = sorted(room.members.itervalues(),
                          key=lambda m: (m.joined_at, m.vid))
        return [self._account_tuple(viewer, room, m.vid) for m in members]

    def _settings(self, viewer, room):
        C = _const()
        roles = {}
        for vid in room.members:
            if vid == room.creator_vid:
                roles[self._view_dbid(viewer, vid)] = C.PREBATTLE_ROLE.TRAINING_CREATOR
            else:
                roles[self._view_dbid(viewer, vid)] = C.PREBATTLE_ROLE.TRAINING_DEFAULT
        creator = self._conn(room.creator_vid)
        return {'type': C.PREBATTLE_TYPE.TRAINING,
                'arenaTypeID': room.arena_type_id,
                'roundLength': room.round_length,
                'comment': room.comment,
                'isOpened': room.is_opened,
                'creator': creator.nick if creator is not None else u'',
                'creatorDBID': self._view_dbid(viewer, room.creator_vid),
                'creatorClanAbbrev': u'',
                'creatorBadges': [],
                'roles': roles,
                'clanRoles': {},
                'teamRoles': {1: C.PREBATTLE_ROLE.TRAINING_DEFAULT,
                              2: C.PREBATTLE_ROLE.TRAINING_DEFAULT},
                'arenaVoipChannels': room.voip_channels,
                'gameplayMask': 0,
                # TeamLimits has no LIMIT_DEFAULTS fallback for 'maxCount'
                # (a (assigned, unassigned) pair) - the room GUI reads it
                # unconditionally, so always send it.
                'limits': {0: {'maxCount': [_TEAM_CAPACITY, _TEAM_CAPACITY]},
                           1: {'maxCount': [_TEAM_CAPACITY, _TEAM_CAPACITY]},
                           2: {'maxCount': [_TEAM_CAPACITY, _TEAM_CAPACITY]}}}

    def _props(self, room):
        return {'wins': [0, 0, 0], 'battlesCount': 0,
                'createTime': room.create_time}

    def _team_states(self, room):
        C = _const()
        return [room.team_states.get(1, C.PREBATTLE_TEAM_STATE.NOT_READY),
                room.team_states.get(2, C.PREBATTLE_TEAM_STATE.NOT_READY)]

    # -- broadcasts ----------------------------------------------------------

    def _upd(self, room, upd, arg_for, exclude_vid=None):
        for vid in list(room.members):
            if vid == exclude_vid:
                continue
            conn = self._conn(vid)
            if conn is not None:
                self._send(conn, ev='upd', upd=upd, arg=arg_for(conn))

    def _broadcast_settings(self, room, exclude_vid=None):
        C = _const()
        self._upd(room, C.PREBATTLE_UPDATE.SETTINGS,
                  lambda c, r=room: self._settings(c, r), exclude_vid)

    def _broadcast_player_state(self, room, vid):
        C = _const()
        member = room.members[vid]

        def arg_for(c, v=vid, m=member, srv=self):
            return [srv._view_rid(c, v), m.roster, m.state, m.veh_descr,
                    0, 0, u'']

        self._upd(room, C.PREBATTLE_UPDATE.PLAYER_STATE, arg_for)

    def _broadcast_team_states(self, room):
        C = _const()
        states = self._team_states(room)
        self._upd(room, C.PREBATTLE_UPDATE.TEAM_STATES, lambda c, s=states: s)

    def _send_join_sync(self, conn, room):
        self._send(conn, ev='joined', prbID=room.room_id,
                   settings=self._settings(conn, room),
                   props=self._props(room),
                   roster=self._roster_rows(conn, room),
                   teamStates=self._team_states(room))

    # -- room chat channel (stock prebattle BW channel) ------------------------

    def _chan_create(self, room):
        cs = _cs()
        chan = self.mp.new_channel(
            _PRB_CHANNEL_NAME, room.creator_vid,
            cs.CHAT_CHANNEL_PREBATTLE | cs.CHAT_CHANNEL_TRAINING)
        room.chan_id = chan.id

    def _chan_join(self, room, conn):
        if conn is None:
            return
        chan = self.mp.channels.get(room.chan_id)
        if chan is None:
            return
        cs = _cs()
        self.mp._act(conn, cs.CHAT_ACTIONS.requestChannels,
                     data=[self.mp._chan_data(chan, conn)])
        self.mp._add_member(chan, conn)

    def _chan_leave(self, room, conn):
        if conn is None:
            return
        chan = self.mp.channels.get(room.chan_id)
        if chan is None:
            return
        if conn.vid in chan.members:
            self.mp._remove_member(chan, conn)
        if conn.alive:
            self.mp._act(conn, _cs().CHAT_ACTIONS.channelDestroyed,
                         channel=chan.id)

    def _chan_destroy(self, room):
        self.mp.channels.pop(room.chan_id, None)

    # -- room lifecycle ---------------------------------------------------------

    def _join_room(self, conn, room, roster):
        C = _const()
        member = _Member(conn.vid, roster,
                         C.PREBATTLE_ACCOUNT_STATE.NOT_READY,
                         conn.veh_descr)
        room.members[conn.vid] = member
        self.by_vid[conn.vid] = room
        self.invites.pop((conn.vid, room.room_id), None)

    def _leave(self, conn):
        room = self.by_vid.pop(conn.vid, None)
        if room is None:
            return False
        C = _const()
        member = room.members.pop(conn.vid, None)
        self._chan_leave(room, conn)
        if conn.vid == room.creator_vid:
            self._destroy_room(room, C.KICK_REASON.CREATOR_LEFT)
        else:
            roster = member.roster if member is not None else 0
            vid = conn.vid
            self._upd(room, C.PREBATTLE_UPDATE.PLAYER_REMOVED,
                      lambda c, v=vid, r=roster, srv=self:
                          [srv._view_rid(c, v), r])
            self._broadcast_settings(room)
        return True

    def _destroy_room(self, room, reason):
        member_vids = list(room.members)
        room.members.clear()
        for vid in member_vids:
            self.by_vid.pop(vid, None)
            conn = self._conn(vid)
            if conn is not None:
                self._chan_leave(room, conn)
                self._send(conn, ev='kicked', reason=reason)
        self.rooms.pop(room.room_id, None)
        self._chan_destroy(room)
        for key in list(self.invites):
            if key[1] == room.room_id:
                self.invites.pop(key, None)
                target = self._conn(key[0])
                if target is not None:
                    self._send(target, ev='inviteDel', prbID=room.room_id)
        _log('room #%s destroyed (reason=%s)' % (room.room_id, reason))

    # -- ops -----------------------------------------------------------------

    def _op_create(self, conn, msg):
        C = _const()
        self._leave(conn)
        room = _Room(self.next_id, conn.vid)
        self.next_id += 1
        room.arena_type_id = int(msg.get('arenaTypeID', 0) or 0)
        room.round_length = int(msg.get('roundLength', 600) or 600)
        room.comment = _u(msg.get('comment', u''))
        room.is_opened = bool(msg.get('isOpened', True))
        room.team_states = {1: C.PREBATTLE_TEAM_STATE.NOT_READY,
                            2: C.PREBATTLE_TEAM_STATE.NOT_READY}
        self.rooms[room.room_id] = room
        self._join_room(conn, room, _encode_roster(1, True))
        self._chan_create(room)
        self._chan_join(room, conn)
        self._send_join_sync(conn, room)
        _log('room #%s created by %s (arena=%s opened=%s)' % (
            room.room_id, conn.nick, room.arena_type_id, room.is_opened))

    def _op_join(self, conn, msg):
        C = _const()
        room = self.rooms.get(int(msg.get('prbID', 0) or 0))
        if room is None:
            self._send(conn, ev='joinFailure',
                       code=C.JOIN_FAILURE.NOT_FOUND)
            return
        if conn.vid in room.members:
            self._send_join_sync(conn, room)
            return
        if not room.is_opened and conn.vid not in room.invited:
            self._send(conn, ev='joinFailure', code=C.JOIN_FAILURE.PRIVACY)
            return
        if len(room.members) >= _ROOM_CAPACITY:
            self._send(conn, ev='joinFailure',
                       code=C.JOIN_FAILURE.TEAM_IS_FULL)
            return
        self._leave(conn)
        self._join_room(conn, room, _encode_roster(1, False))
        vid = conn.vid
        self._upd(room, C.PREBATTLE_UPDATE.PLAYER_ADDED,
                  lambda c, v=vid, r=room, srv=self:
                      srv._account_tuple(c, r, v),
                  exclude_vid=vid)
        self._broadcast_settings(room, exclude_vid=vid)
        self._chan_join(room, conn)
        self._send_join_sync(conn, room)
        _log('%s joined room #%s' % (conn.nick, room.room_id))

    def _op_leave(self, conn, msg):
        self._leave(conn)
        self._resp(conn, msg, True)
        self._send(conn, ev='left')

    def _op_set_ready(self, conn, msg, ready):
        C = _const()
        room = self._room_of(conn)
        member = room.members.get(conn.vid) if room is not None else None
        ok = member is not None
        if ok:
            if ready:
                member.state = C.PREBATTLE_ACCOUNT_STATE.READY
            else:
                member.state = int(msg.get('state',
                                           C.PREBATTLE_ACCOUNT_STATE.NOT_READY)
                                   or C.PREBATTLE_ACCOUNT_STATE.NOT_READY)
            veh = _u(msg.get('vehDescr') or '')
            if veh:
                member.veh_descr = veh
                conn.veh_descr = veh
            self._broadcast_player_state(room, conn.vid)
        self._resp(conn, msg, ok)

    def _translate_pid(self, conn, pid):
        try:
            pid = int(pid)
        except Exception:
            return 0
        if conn.entity_id and pid == conn.entity_id:
            return conn.vid
        return pid

    def _op_assign(self, conn, msg):
        C = _const()
        room = self._room_of(conn)
        ok = False
        if room is not None:
            target_vid = self._translate_pid(conn, msg.get('playerID', 0))
            roster = int(msg.get('roster', 0) or 0)
            team, assigned = _decode_roster(roster)
            member = room.members.get(target_vid)
            allowed = (conn.vid == room.creator_vid
                       or target_vid == conn.vid)
            if member is not None and allowed and team in (1, 2):
                if assigned:
                    count = sum(1 for m in room.members.itervalues()
                                if _decode_roster(m.roster) == (team, True)
                                and m.vid != target_vid)
                    if count >= _TEAM_CAPACITY:
                        self._resp(conn, msg, False)
                        return
                prev = member.roster
                member.roster = roster
                actor_vid = conn.vid

                def arg_for(c, v=target_vid, p=prev, r=roster,
                            a=actor_vid, srv=self):
                    return [srv._view_rid(c, v), p, r, srv._view_rid(c, a)]

                self._upd(room, C.PREBATTLE_UPDATE.PLAYER_ROSTER, arg_for)
                ok = True
        self._resp(conn, msg, ok)

    def _op_swap(self, conn, msg):
        C = _const()
        room = self._room_of(conn)
        ok = room is not None and conn.vid == room.creator_vid
        if ok:
            for member in room.members.itervalues():
                team, assigned = _decode_roster(member.roster)
                member.roster = _encode_roster(3 - team, assigned)
            self._upd(room, C.PREBATTLE_UPDATE.ROSTER,
                      lambda c, r=room, srv=self: srv._roster_rows(c, r))
        self._resp(conn, msg, ok)

    def _set_setting(self, conn, msg, name, value, apply_fn):
        C = _const()
        room = self._room_of(conn)
        ok = room is not None and conn.vid == room.creator_vid
        if ok:
            apply_fn(room, value)
            self._upd(room, C.PREBATTLE_UPDATE.SETTING,
                      lambda c, n=name, v=value: [n, v])
        self._resp(conn, msg, ok)

    def _op_team_ready(self, conn, msg):
        C = _const()
        room = self._room_of(conn)
        team = int(msg.get('team', 0) or 0)
        ok = (room is not None and conn.vid == room.creator_vid
              and team in (1, 2))
        if ok:
            room.team_states[team] = C.PREBATTLE_TEAM_STATE.READY
            self._broadcast_team_states(room)
            states = self._team_states(room)
            if states[0] == C.PREBATTLE_TEAM_STATE.READY \
                    and states[1] == C.PREBATTLE_TEAM_STATE.READY:
                self._deferred.append(
                    (time.time() + _BATTLE_CANCEL_DELAY,
                     lambda r=room: self._cancel_battle(r)))
        self._resp(conn, msg, ok)

    def _op_team_not_ready(self, conn, msg):
        C = _const()
        room = self._room_of(conn)
        team = int(msg.get('team', 0) or 0)
        ok = (room is not None and conn.vid == room.creator_vid
              and team in (1, 2))
        if ok:
            room.team_states[team] = C.PREBATTLE_TEAM_STATE.NOT_READY
            self._broadcast_team_states(room)
        self._resp(conn, msg, ok)

    def _cancel_battle(self, room):
        C = _const()
        if room.room_id not in self.rooms:
            return
        room.team_states = {1: C.PREBATTLE_TEAM_STATE.NOT_READY,
                            2: C.PREBATTLE_TEAM_STATE.NOT_READY}
        self._broadcast_team_states(room)
        for vid in list(room.members):
            conn = self._conn(vid)
            if conn is not None:
                self._send(conn, ev='sysNote',
                           text=_BATTLE_UNAVAILABLE_TEXT)
        _log('room #%s: battle start refused (offline), teams un-readied'
             % (room.room_id,))

    def _op_kick(self, conn, msg):
        C = _const()
        room = self._room_of(conn)
        ok = False
        if room is not None and conn.vid == room.creator_vid:
            target_vid = self._translate_pid(conn, msg.get('playerID', 0))
            if target_vid != conn.vid:
                member = room.members.pop(target_vid, None)
                if member is not None:
                    self.by_vid.pop(target_vid, None)
                    target = self._conn(target_vid)
                    if target is not None:
                        self._chan_leave(room, target)
                        self._send(target, ev='kicked',
                                   reason=C.KICK_REASON.PLAYERKICK)
                    roster = member.roster
                    self._upd(room, C.PREBATTLE_UPDATE.PLAYER_REMOVED,
                              lambda c, v=target_vid, r=roster, srv=self:
                                  [srv._view_rid(c, v), r])
                    self._broadcast_settings(room)
                    ok = True
                    _log('room #%s: vid %s kicked by creator' % (
                        room.room_id, target_vid))
        self._resp(conn, msg, ok)

    def _op_send_invites(self, conn, msg):
        room = self._room_of(conn)
        if room is None or conn.vid != room.creator_vid:
            return
        comment = _u(msg.get('comment', u''))
        for view_dbid in msg.get('dbIDs') or []:
            try:
                target_vid = int(view_dbid)
            except Exception:
                continue
            if target_vid == (conn.self_dbid or 1):
                target_vid = conn.vid
            if target_vid == conn.vid or target_vid in room.members:
                continue
            target = self._conn(target_vid)
            if target is None:
                nick = self.mp.ever_nicks.get(target_vid, u'')
                self._send(conn, ev='sysNote',
                           text=u'Игрок %s не в сети — приглашение в тренировочную комнату не отправлено.' % (nick or u'?',))
                continue
            room.invited.add(target_vid)
            self.invites[(target_vid, room.room_id)] = {
                'from': conn.vid, 'comment': comment,
                'time': int(time.time())}
            self._send(target, ev='invite', prbID=room.room_id,
                       creator=conn.nick,
                       creatorDBID=self._view_dbid(target, conn.vid),
                       comment=comment or room.comment,
                       createTime=int(time.time()),
                       count=len(room.members))

    def _op_decline(self, conn, msg):
        prb_id = int(msg.get('prbID', 0) or 0)
        record = self.invites.pop((conn.vid, prb_id), None)
        self._send(conn, ev='inviteDel', prbID=prb_id)
        room = self.rooms.get(prb_id)
        if record is not None and room is not None:
            creator = self._conn(room.creator_vid)
            if creator is not None:
                self._send(creator, ev='sysNote',
                           text=u'Игрок %s отклонил приглашение в тренировочную комнату.' % (conn.nick,))

    def _op_request_list(self, conn, msg):
        C = _const()
        K = C.PREBATTLE_CACHE_KEY
        prb_type = int(msg.get('prbType', C.PREBATTLE_TYPE.TRAINING) or 0)
        mask = _u(msg.get('mask', u'')).lower()
        items = []
        if prb_type == C.PREBATTLE_TYPE.TRAINING:
            rooms = sorted(self.rooms.itervalues(),
                           key=lambda r: (r.create_time, r.room_id))
            for room in rooms:
                creator = self._conn(room.creator_vid)
                creator_nick = creator.nick if creator is not None else u''
                if mask and mask not in creator_nick.lower():
                    continue
                info = {K.ARENA_TYPE_ID: room.arena_type_id,
                        K.CREATOR: creator_nick,
                        K.CREATOR_CLAN_ABBREV: u'',
                        K.CREATOR_IGR_TYPE: 0,
                        K.CREATOR_DB_ID: self._view_dbid(conn,
                                                          room.creator_vid),
                        K.CREATOR_BADGES: [],
                        K.STATE: C.PREBATTLE_STATE.IDLE,
                        K.IS_OPENED: room.is_opened,
                        K.COMMENT: room.comment,
                        K.PLAYER_COUNT: len(room.members)}
                items.append([room.create_time, room.room_id, info])
        total = len(items)
        if not mask:
            try:
                start = int(msg.get('start', 0) or 0)
                end = int(msg.get('end', total) or total)
                items = items[start:end]
            except Exception:
                pass
        self._send(conn, ev='list', prbType=prb_type, count=total,
                   items=items)

    def _op_request_roster(self, conn, msg):
        prb_id = int(msg.get('prbID', 0) or 0)
        room = self.rooms.get(prb_id)
        rows = []
        if room is not None:
            for row in self._roster_rows(conn, room):
                # RosterIterator tuples have no badges field (10-tuples).
                rows.append([row[0], row[1], row[2], row[4], row[5],
                             row[6], row[7], row[8], row[9], row[10]])
        self._send(conn, ev='rosterList', prbID=prb_id, rows=rows)

    # -- dispatch ---------------------------------------------------------------

    def on_msg(self, conn, msg):
        cmd = msg.get('cmd')
        handler = {
            'createTraining': self._op_create,
            'join': self._op_join,
            'leave': self._op_leave,
            'ready': lambda c, m: self._op_set_ready(c, m, True),
            'notReady': lambda c, m: self._op_set_ready(c, m, False),
            'assign': self._op_assign,
            'swapTeams': self._op_swap,
            'changeArena': lambda c, m: self._set_setting(
                c, m, 'arenaTypeID', int(m.get('arenaTypeID', 0) or 0),
                lambda r, v: setattr(r, 'arena_type_id', v)),
            'changeRoundLength': lambda c, m: self._set_setting(
                c, m, 'roundLength', int(m.get('roundLength', 0) or 0),
                lambda r, v: setattr(r, 'round_length', v)),
            'changeOpenStatus': lambda c, m: self._set_setting(
                c, m, 'isOpened', bool(m.get('isOpened')),
                lambda r, v: setattr(r, 'is_opened', v)),
            'changeComment': lambda c, m: self._set_setting(
                c, m, 'comment', _u(m.get('comment', u'')),
                lambda r, v: setattr(r, 'comment', v)),
            'changeArenaVoip': lambda c, m: self._set_setting(
                c, m, 'arenaVoipChannels', int(m.get('channels', 0) or 0),
                lambda r, v: setattr(r, 'voip_channels', v)),
            'changeGameplaysMask': lambda c, m: self._resp(c, m, True),
            'teamReady': self._op_team_ready,
            'teamNotReady': self._op_team_not_ready,
            'kick': self._op_kick,
            'sendInvites': self._op_send_invites,
            'declineInvite': self._op_decline,
            'requestList': self._op_request_list,
            'requestRoster': self._op_request_roster,
        }.get(cmd)
        if handler is None:
            _log('unknown prb cmd %r from vid=%s' % (cmd, conn.vid))
            self._resp(conn, msg, False)
            return
        handler(conn, msg)

    def on_disconnect(self, conn):
        if conn.vid in self.by_vid:
            self._leave(conn)
            _log('%s disconnected from room' % (conn.nick,))

    def on_tick(self):
        now = time.time()
        due = [entry for entry in self._deferred if entry[0] <= now]
        if not due:
            return
        self._deferred = [entry for entry in self._deferred
                           if entry[0] > now]
        for _, fn in due:
            try:
                fn()
            except Exception:
                _log('deferred training action failed:')
                traceback.print_exc()


def make_server(mp_server):
    return _TrainingServer(mp_server)


# ---------------------------------------------------------------------------
# Client side (PlayerAccount prb_* patches + stock callback delivery)
# ---------------------------------------------------------------------------

class _TrainingClient(object):

    def __init__(self, state):
        self.state = state
        self._req = 0
        self._pending = {}

    # -- outgoing -------------------------------------------------------------

    def _online(self):
        return bool(getattr(self.state, 'my_vid', 0))

    def _fire(self, req, code):
        entry = self._pending.pop(req, None)
        if entry is None:
            return
        callback, with_errstr = entry
        try:
            if with_errstr:
                callback(code, '' if code >= 0 else 'Local multiplayer error')
            else:
                callback(code)
        except Exception:
            _log('prb callback for req=%s failed:' % (req,))
            traceback.print_exc()

    def _send(self, cmd, callback=None, with_errstr=False, **fields):
        msg = {'t': 'prb', 'cmd': cmd}
        msg.update(fields)
        req = None
        if callback is not None:
            self._req += 1
            req = self._req
            msg['req'] = req
            self._pending[req] = (callback, with_errstr)
        if self._online():
            self.state.transport.send_to_server(msg)
            return True
        _log('prb cmd %s dropped: not connected to LAN host' % (cmd,))
        if req is not None:
            self._fire(req, -1)
        return False

    def _fail_join_offline(self):
        try:
            C = _const()
            self.state.player.onPrebattleJoinFailure(
                C.JOIN_FAILURE.TIME_OUT)
        except Exception:
            pass
        self._push_note(u'Локальная сеть не подключена: тренировочные комнаты недоступны.')

    def _push_note(self, text):
        try:
            from gui import SystemMessages
            SystemMessages.pushMessage(text,
                                       SystemMessages.SM_TYPE.Warning)
        except Exception:
            _log('sysNote: %s' % (text,))

    # ops bound to PlayerAccount methods by install()
    def op_create(self, arenaTypeID, roundLength, isOpened, comment):
        if not self._send('createTraining', arenaTypeID=int(arenaTypeID),
                          roundLength=int(roundLength),
                          isOpened=bool(isOpened), comment=_u(comment)):
            self._fail_join_offline()

    def op_join(self, prebattleID):
        if not self._send('join', prbID=int(prebattleID)):
            self._fail_join_offline()

    def op_accept_invite(self, prebattleID, peripheryID):
        if not self._send('join', prbID=int(prebattleID)):
            self._fail_join_offline()

    def op_decline_invite(self, prebattleID, peripheryID):
        self._apply_invite_del(int(prebattleID))
        self._send('declineInvite', prbID=int(prebattleID))

    def op_send_invites(self, accountsToInvite, comment):
        self._send('sendInvites',
                   dbIDs=[int(x) for x in (accountsToInvite or [])],
                   comment=_u(comment))

    def op_leave(self, callback):
        self._send('leave', callback=callback)

    def op_ready(self, vehInvID, callback):
        self._send('ready', callback=callback, with_errstr=True,
                   vehInvID=int(vehInvID), vehDescr=_veh_descr())

    def op_not_ready(self, state, callback):
        self._send('notReady', callback=callback, state=int(state),
                   vehDescr=_veh_descr())

    def op_assign(self, playerID, roster, callback):
        self._send('assign', callback=callback, with_errstr=True,
                   playerID=int(playerID), roster=int(roster))

    def op_swap_teams(self, callback):
        self._send('swapTeams', callback=callback)

    def op_change_arena(self, arenaTypeID, callback):
        self._send('changeArena', callback=callback,
                   arenaTypeID=int(arenaTypeID))

    def op_change_round_length(self, roundLength, callback):
        self._send('changeRoundLength', callback=callback,
                   roundLength=int(roundLength))

    def op_change_open_status(self, isOpened, callback):
        self._send('changeOpenStatus', callback=callback,
                   isOpened=bool(isOpened))

    def op_change_comment(self, comment, callback):
        self._send('changeComment', callback=callback, comment=_u(comment))

    def op_change_arena_voip(self, arenaVoipChannels, callback):
        self._send('changeArenaVoip', callback=callback,
                   channels=int(arenaVoipChannels))

    def op_change_gameplays_mask(self, gameplaysMask, callback):
        self._send('changeGameplaysMask', callback=callback,
                   mask=int(gameplaysMask))

    def op_team_ready(self, team, force, gameplaysMask, callback):
        self._send('teamReady', callback=callback, with_errstr=True,
                   team=int(team), force=bool(force))

    def op_team_not_ready(self, team, callback):
        self._send('teamNotReady', callback=callback, team=int(team))

    def op_kick(self, playerID, callback):
        self._send('kick', callback=callback, playerID=int(playerID))

    def op_request_list(self, prbType, sortKey, idle, start, end):
        self._send('requestList', prbType=int(prbType),
                   sortKey=int(sortKey), start=int(start), end=int(end))

    def op_request_list_by_name(self, prbType, idle, mask):
        self._send('requestList', prbType=int(prbType), mask=_u(mask))

    def op_request_roster(self, prebattleID):
        self._send('requestRoster', prbID=int(prebattleID))

    # -- incoming ---------------------------------------------------------------

    def _fix_settings(self, settings):
        fixed = dict(settings or {})
        for key in ('roles', 'teamRoles', 'limits', 'clanRoles'):
            value = fixed.get(key)
            if isinstance(value, dict):
                fixed[key] = dict(
                    (int(k), v) for k, v in value.iteritems())
        return fixed

    def _upd(self, upd, arg):
        self.state.player.updatePrebattle(upd, cPickle.dumps(arg, -1))

    # The stock GUI recognises *self* in rosters by comparing the row ID
    # with account_helpers.getPlayerID() (== BigWorld.player().id). The
    # host rewrites our row using the entity ID from the LAN hello, but
    # rewrite once more at delivery time from ground truth: any ID equal
    # to our virtual DBID (vid) becomes the live local player ID.
    def _tr_id(self, rid):
        try:
            rid = int(rid)
        except Exception:
            return rid
        if rid and rid == int(getattr(self.state, 'my_vid', 0) or 0):
            return int(getattr(self.state.player, 'id', 0) or 0)
        return rid

    def _fix_row(self, row):
        row = list(row)
        if row:
            row[0] = self._tr_id(row[0])
        if len(row) > 7:
            row[7] = _veh_bytes(row[7])  # vehCompDescr: hex -> packed str
        return tuple(row)

    def _fix_upd_arg(self, upd, arg):
        U = _const().PREBATTLE_UPDATE
        if upd == U.ROSTER:
            return [self._fix_row(row) for row in arg or []]
        if upd == U.PLAYER_ADDED:
            return self._fix_row(arg or [])
        if upd in (U.PLAYER_REMOVED, U.PLAYER_STATE):
            arg = list(arg or [])
            if arg:
                arg[0] = self._tr_id(arg[0])
            if upd == U.PLAYER_STATE and len(arg) > 3:
                arg[3] = _veh_bytes(arg[3])
            return tuple(arg)
        if upd == U.PLAYER_ROSTER:
            arg = list(arg or [])
            if len(arg) >= 1:
                arg[0] = self._tr_id(arg[0])
            if len(arg) >= 4:
                arg[3] = self._tr_id(arg[3])
            return tuple(arg)
        if isinstance(arg, list):
            return tuple(arg)
        return arg

    def _apply_invite(self, msg):
        C = _const()
        player = self.state.player
        invites = getattr(player, 'prebattleInvites', None)
        if invites is None:
            invites = {}
            try:
                player.prebattleInvites = invites
            except Exception:
                pass
        invites[(int(msg.get('prbID', 0)), 0)] = {
            'createTime': int(msg.get('createTime', 0) or 0),
            'type': C.PREBATTLE_TYPE.TRAINING,
            'comment': _u(msg.get('comment', u'')),
            'creator': _u(msg.get('creator', u'')),
            'creatorDBID': int(msg.get('creatorDBID', 0) or 0),
            'creatorClanAbbrev': u'',
            'creatorBadges': [],
            'state': C.PREBATTLE_INVITE_STATE.ACTIVE,
            'count': int(msg.get('count', 0) or 0)}
        self._notify_invites_changed()

    def _apply_invite_del(self, prb_id):
        player = self.state.player
        invites = getattr(player, 'prebattleInvites', None)
        if invites is not None:
            invites.pop((int(prb_id), 0), None)
        self._notify_invites_changed()

    def _notify_invites_changed(self):
        try:
            from PlayerEvents import g_playerEvents
            g_playerEvents.onPrebattleInvitesChanged(
                {('prebattleInvites', '_r'): True})
        except Exception:
            pass

    def on_msg(self, msg):
        C = _const()
        U = C.PREBATTLE_UPDATE
        player = self.state.player
        ev = msg.get('ev')
        if ev == 'resp':
            self._fire(int(msg.get('req', 0) or 0),
                       int(msg.get('code', -1)))
        elif ev == 'list':
            items = []
            for create_time, prb_id, info in msg.get('items') or []:
                info = dict((int(k), v) for k, v in (info or {}).iteritems())
                items.append((create_time, prb_id, info))
            data = zlib.compress(cPickle.dumps(
                (int(msg.get('prbType', 0)), int(msg.get('count', 0)),
                 items), -1))
            player.receivePrebattles(True, data)
        elif ev == 'rosterList':
            rows = []
            for row in msg.get('rows') or []:
                row = list(row)
                if row:
                    row[0] = self._tr_id(row[0])
                if len(row) > 6:
                    row[6] = _veh_bytes(row[6])  # 10-tuple vehCompDescr
                rows.append(tuple(row))
            data = zlib.compress(cPickle.dumps(
                (int(msg.get('prbID', 0)), rows), -1))
            player.receivePrebattleRoster(True, data)
        elif ev == 'joined':
            player.onPrebattleJoined(int(msg.get('prbID', 0)))
            self._upd(U.SETTINGS, self._fix_settings(msg.get('settings')))
            self._upd(U.PROPERTIES, dict(msg.get('props') or {}))
            self._upd(U.ROSTER, self._fix_upd_arg(U.ROSTER,
                                                  msg.get('roster')))
            self._upd(U.TEAM_STATES, tuple(msg.get('teamStates') or []))
        elif ev == 'upd':
            upd = int(msg.get('upd', 0) or 0)
            arg = msg.get('arg')
            if upd == U.SETTINGS:
                arg = self._fix_settings(arg)
            else:
                arg = self._fix_upd_arg(upd, arg)
            self._upd(upd, arg)
        elif ev == 'joinFailure':
            player.onPrebattleJoinFailure(int(msg.get('code', 0) or 0))
        elif ev == 'left':
            player.onPrebattleLeft()
        elif ev == 'kicked':
            player.onKickedFromPrebattle(int(msg.get('reason', 0) or 0))
        elif ev == 'invite':
            self._apply_invite(msg)
        elif ev == 'inviteDel':
            self._apply_invite_del(msg.get('prbID', 0))
        elif ev == 'sysNote':
            self._push_note(_u(msg.get('text', u'')))
        else:
            _log('unknown prb event %r' % (ev,))


def install(state):
    """Patch PlayerAccount prb_* methods to the LAN implementation.

    Called by MPChat.on_account_ready once per session; replaces the
    no-op stubs Offline.Manager installed for the single-player hangar.
    """
    from Account import PlayerAccount
    client = _TrainingClient(state)
    state.training = client

    def _bind(name, op):
        def method(self_, *args):
            op(*args)
        setattr(PlayerAccount, name, method)

    _bind('prb_createTraining', client.op_create)
    _bind('prb_join', client.op_join)
    _bind('prb_acceptInvite', client.op_accept_invite)
    _bind('prb_declineInvite', client.op_decline_invite)
    _bind('prb_sendInvites', client.op_send_invites)
    _bind('prb_leave', client.op_leave)
    _bind('prb_ready', client.op_ready)
    _bind('prb_notReady', client.op_not_ready)
    _bind('prb_assign', client.op_assign)
    _bind('prb_swapTeams', client.op_swap_teams)
    _bind('prb_changeArena', client.op_change_arena)
    _bind('prb_changeRoundLength', client.op_change_round_length)
    _bind('prb_changeOpenStatus', client.op_change_open_status)
    _bind('prb_changeComment', client.op_change_comment)
    _bind('prb_changeArenaVoip', client.op_change_arena_voip)
    _bind('prb_changeGameplaysMask', client.op_change_gameplays_mask)
    _bind('prb_teamReady', client.op_team_ready)
    _bind('prb_teamNotReady', client.op_team_not_ready)
    _bind('prb_kick', client.op_kick)
    _bind('requestPrebattles', client.op_request_list)
    _bind('requestPrebattlesByName', client.op_request_list_by_name)
    _bind('requestPrebattleRoster', client.op_request_roster)
    _log('training rooms client patches installed')
    return client
