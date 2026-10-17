# -*- coding: utf-8 -*-
"""
Offline logic test for src/Offline/NewYearServer.py.

Runs under Python 2.7 with:
 * the REAL items/new_year_types.py + item_defs/new_year/*.xml from the
   decompiled 0.9.21 client (StranikS-Scan tree), fed through a tiny
   ResMgr stub;
 * a ClientNewYear re-implementation (same diff semantics as the client's
   account_helpers/ClientNewYear.py);
 * stubs for BigWorld and the GUI-side modules used by the discount and
   tankman flows.

Usage:  WOT0921=/path/to/decompiled/source/res /tmp/py27/bin/python tools/test_ny_server.py
"""
import imp
import os
import sys
import types

RES = os.environ.get('WOT0921', '/tmp/wot0921/source/res')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(RES, 'scripts', 'common'))
sys.path.insert(0, os.path.join(ROOT, 'src'))

# --------------------------------------------------------------------------
# ResMgr stub over the real XML files
# --------------------------------------------------------------------------
import xml.etree.ElementTree as ET


class _Section(object):

    def __init__(self, elem):
        self._elem = elem

    def values(self):
        return [_Section(child) for child in self._elem]

    def items(self):
        return [(child.tag, _Section(child)) for child in self._elem]

    def _text(self, name, default=None):
        child = self._elem.find(name)
        if child is None or child.text is None:
            return default
        return child.text.strip()

    def readString(self, name, default=''):
        val = self._text(name, None)
        return default if val is None else val

    def readInt(self, name, default=0):
        val = self._text(name, None)
        return default if val is None else int(val)

    def readBool(self, name, default=False):
        val = self._text(name, None)
        if val is None:
            return default
        return val.lower() in ('true', '1', 'yes')


class _ResMgr(object):

    @staticmethod
    def openSection(path):
        full = os.path.join(RES, path)
        if not os.path.isfile(full):
            return None
        return _Section(ET.parse(full).getroot())

    @staticmethod
    def purge(path, force=False):
        pass


sys.modules['ResMgr'] = _ResMgr

# --------------------------------------------------------------------------
# Small stubs: debug_utils, constants, BigWorld
# --------------------------------------------------------------------------
debug_utils = types.ModuleType('debug_utils')
for _name in ('LOG_DEBUG', 'LOG_DEBUG_DEV', 'LOG_ERROR', 'LOG_WARNING', 'LOG_NOTE',
              'LOG_CURRENT_EXCEPTION', 'LOG_CODEPOINT_WARNING'):
    setattr(debug_utils, _name, lambda *a, **k: None)
debug_utils.__all__ = [n for n in dir(debug_utils) if n.startswith('LOG')]
sys.modules['debug_utils'] = debug_utils

constants = types.ModuleType('constants')
constants.IS_CLIENT = True
constants.IS_BASEAPP = False


class _ECD(object):
    ACTION = 1
    QUEST = 3
    INGAME_EVENTS = 7


class _ET(object):
    TOKEN_QUEST = 3


constants.EVENT_CLIENT_DATA = _ECD
constants.EVENT_TYPE = _ET
sys.modules['constants'] = constants

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


def flush_callbacks():
    while CALLBACKS:
        CALLBACKS.pop(0)()


# --------------------------------------------------------------------------
# Real items.new_year_types (loaded by path, under a stub `items` package)
# --------------------------------------------------------------------------
items_pkg = types.ModuleType('items')
items_pkg.__path__ = []
sys.modules['items'] = items_pkg

nyt = imp.load_source('items.new_year_types',
                      os.path.join(RES, 'scripts', 'common', 'items', 'new_year_types.py'))
sys.modules['items.new_year_types'] = nyt
items_pkg.new_year_types = nyt
nyt.g_cache = nyt.NewYearCache()
_new_year_types_mod = nyt  # for the NY module's `from items import new_year_types`
items_pkg.new_year_types = _new_year_types_mod

# items.tankmen stub (only what NewYearServer uses)
tankmen = types.ModuleType('tankmen')
tankmen.MAX_SKILL_LEVEL = 100
tankmen.ROLES = ('commander', 'radioman', 'driver', 'gunner', 'loader')
tankmen.SKILL_INDICES = {'commander': 0, 'radioman': 1, 'driver': 2, 'gunner': 3, 'loader': 4, 'brotherhood': 5}


class _Group(object):
    isFemales = True
    firstNamesList = [11, 12, 13]
    lastNamesList = [21, 22, 23]
    iconsList = [31, 32, 33]


def _get_nation_groups(nation_id, is_premium):
    return (_Group(),)


tankmen.getNationGroups = _get_nation_groups
tankmen.generateCompactDescr = lambda passport, veh_type, role, lvl, skills=(), **kw: 'FAKE_CD_%s_%s' % (role, passport[3])
sys.modules['items.tankmen'] = tankmen
items_pkg.tankmen = tankmen

# Offline.Manager stub (get_offline_state only)
offline_pkg = types.ModuleType('Offline')
offline_pkg.__path__ = []
sys.modules['Offline'] = offline_pkg
manager_stub = types.ModuleType('Offline.Manager')
_TEST_STATE = {'t_cache': {10: 'x', 55: 'y'}, 'resync_inventory': lambda: None}
manager_stub.get_offline_state = lambda: _TEST_STATE
sys.modules['Offline.Manager'] = manager_stub
offline_pkg.Manager = manager_stub

# PlayerEvents stub
pe = types.ModuleType('PlayerEvents')


class _Event(object):

    def __init__(self):
        self.handlers = []

    def __iadd__(self, h):
        self.handlers.append(h)
        return self

    def __isub__(self, h):
        return self

    def __call__(self, *a):
        for h in self.handlers:
            h(*a)


class _PE(object):
    onEventsDataChanged = _Event()


pe.g_playerEvents = _PE()
sys.modules['PlayerEvents'] = pe

# helpers / dependency / ClientUpdateManager / GoodiesRequester stubs for
# the discount flow
helpers = types.ModuleType('helpers')


class _Dep(object):

    @staticmethod
    def instance(iface):
        return _ITEMS_CACHE


helpers.dependency = _Dep
sys.modules['helpers'] = helpers

skeletons_pkg = types.ModuleType('skeletons')
skeletons_pkg.__path__ = []
sys.modules['skeletons'] = skeletons_pkg
sk_gui = types.ModuleType('skeletons.gui')
sk_gui.__path__ = []
sys.modules['skeletons.gui'] = sk_gui
sk_shared = types.ModuleType('skeletons.gui.shared')


class IItemsCache(object):
    pass


sk_shared.IItemsCache = IItemsCache
sys.modules['skeletons.gui.shared'] = sk_shared

gui_pkg = types.ModuleType('gui')
gui_pkg.__path__ = []
sys.modules['gui'] = gui_pkg
cum = types.ModuleType('gui.ClientUpdateManager')
_UPDATES = []


class _CUM(object):

    @staticmethod
    def update(diff):
        _UPDATES.append(diff)


cum.g_clientUpdateManager = _CUM()
sys.modules['gui.ClientUpdateManager'] = cum

gr_path = 'gui.shared.utils.requesters.GoodiesRequester'
for _p in ('gui.shared', 'gui.shared.utils', 'gui.shared.utils.requesters'):
    _m = types.ModuleType(_p)
    _m.__path__ = []
    sys.modules[_p] = _m
gr_mod = types.ModuleType(gr_path)
from collections import namedtuple
gr_mod.GoodieVariable = namedtuple('GoodieVariable', 'state finishTime count')
sys.modules[gr_path] = gr_mod

goodies_pkg = types.ModuleType('goodies')
goodies_pkg.__path__ = []
sys.modules['goodies'] = goodies_pkg
_gc = types.ModuleType('goodies.goodie_constants')


class _GS(object):
    ACTIVE = 1


_gc.GOODIE_STATE = _GS
sys.modules['goodies.goodie_constants'] = _gc


class _GoodiesReq(object):

    def __init__(self):
        self._data = {}


class _Items(object):
    goodies = _GoodiesReq()


class _ItemsCache(object):
    items = _Items()


_ITEMS_CACHE = _ItemsCache()

# AccountCommands stub (NY part + codes used by the server module)
ac = types.ModuleType('AccountCommands')
ac.RES_SUCCESS = 0
ac.RES_FAILURE = -1
ac.CMD_NEW_YEAR_SLOT_FILL = 20161
ac.CMD_NEW_YEAR_CRAFT = 20162
ac.CMD_NEW_YEAR_BREAK_TOYS = 20163
ac.CMD_NEW_YEAR_ADD_TOYS_DEV = 20164
ac.CMD_NEW_YEAR_OPEN_BOX = 20165
ac.CMD_NEW_YEAR_ADD_TOKEN_DEV = 20166
ac.CMD_NEW_YEAR_SEE_TOYS = 20167
ac.CMD_NEW_YEAR_SELECT_DISCOUNT = 20168
ac.CMD_NEW_YEAR_SELECT_TANKMAN = 20169
sys.modules['AccountCommands'] = ac

# --------------------------------------------------------------------------
# ClientNewYear re-implementation with the client's diff semantics
# --------------------------------------------------------------------------
from collections import namedtuple as _nt
ToyInventoryData = _nt('ToyInventoryData', ('totalCount', 'newCount'))
TankmanPassportData = _nt('TankmanPassportData', ('groupID', 'firstNameID', 'lastNameID', 'iconID'))


def _unpack(binary):
    for byte_pos, byte in enumerate(binary):
        for bit_pos in xrange(8):
            if byte & 1 << bit_pos:
                yield byte_pos * 8 + bit_pos


class FakeClientNewYear(object):

    def __init__(self):
        self.inventory_toys = {}
        self.slots = [-1] * len(nyt.g_cache.slots)
        self.level = 1
        self.fragments = 0
        self.boxes = {}
        self.chests = {}
        self.vd = {}
        self.vt = {}
        self.collection = set()
        self.choices = {}
        self.diffs = []

    def synchronize(self, is_full_sync, diff):
        self.diffs.append(diff)
        update = diff.get('newYear')
        if update:
            if 'toyCollection' in update:
                self.collection.update(_unpack(update['toyCollection']))
            if 'slots' in update:
                self.slots = update['slots']
            if 'inventoryToys' in update:
                d = dict(((k, ToyInventoryData(*v)) for k, v in update['inventoryToys'].iteritems()))
                self.inventory_toys.update(d)
                self.collection.update(d.iterkeys())
            if 'level' in update:
                self.level = update['level']
            if 'toyFragments' in update:
                self.fragments = update['toyFragments']
            if 'tankmanNextChoices' in update:
                self.choices.update(dict(((k, TankmanPassportData(*v)) for k, v in update['tankmanNextChoices'].iteritems())))
        tokens = diff.get('tokens')
        if tokens:
            for token, data in tokens.iteritems():
                count = 0 if data is None else data[1]
                if token in nyt.g_cache.boxes:
                    self.boxes[token] = count
                elif token in nyt.g_cache.chests:
                    self.chests[token] = count
                elif token in nyt.g_cache.variadicDiscounts:
                    self.vd[token] = count
                elif token in nyt.g_cache.variadicTankmen:
                    self.vt[token] = count


class FakePlayer(object):

    def __init__(self):
        self.newYear = FakeClientNewYear()
        self.eventsData = {}
        self.goodies = self._Goodies()

    class _Goodies(object):
        _ClientGoodies__cache = {}


# messenger / new_year package stubs used by optional paths
nn_pkg = types.ModuleType('new_year')
nn_pkg.__path__ = []
sys.modules['new_year'] = nn_pkg
ctrl_mod = types.ModuleType('new_year.new_year_controller')


class _BoxStorage(object):

    def _handleRequestResult(self, response):
        raise RuntimeError('orig should not be called on success')


ctrl_mod._NewYearBoxStorage = _BoxStorage
sys.modules['new_year.new_year_controller'] = ctrl_mod

# The sandbox Python 2.7 build has no zlib module (the WoT client does);
# identity compression is enough to validate the logic round-trips.
try:
    import zlib  # noqa
except ImportError:
    _zlib = types.ModuleType('zlib')
    _zlib.compress = lambda s, *a: s
    _zlib.decompress = lambda s, *a: s
    sys.modules['zlib'] = _zlib

# --------------------------------------------------------------------------
# Load the module under test
# --------------------------------------------------------------------------
NY = imp.load_source('Offline.NewYearServer', os.path.join(ROOT, 'src', 'Offline', 'NewYearServer.py'))
sys.modules['Offline.NewYearServer'] = NY
offline_pkg.NewYearServer = NY

FAILS = []


def check(cond, msg):
    print ('PASS' if cond else 'FAIL') + ': ' + msg
    if not cond:
        FAILS.append(msg)


player = FakePlayer()
_BigWorld._player = player

# monkeypatch out the GUI-only init paths that need a real client
NY._patch_box_storage = lambda: None
NY._delayed_discount_storage_init = lambda attempt=0: None
NY._push_setting_collected_msg = lambda name: None

# equipment/vehicle CD resolvers need the full items cache - stub them
NY._equipment_int_cd = lambda name: 777000 + len(name)
NY._vehicle_int_cd = lambda name: 424242

# --------------------------------------------------------------- on ready
NY.on_account_ready(player)
flush_callbacks()
cny = player.newYear
check(len(cny.slots) == 13 and all(s == -1 for s in cny.slots), 'initial slots: 13 empty')
check(cny.chests.get('ny18:chest:1') == 1, 'level-1 chest granted at start')
check(cny.boxes.get('ny18:box:s') == 20, 'mission boxes granted (20 soviet)')
check(cny.fragments == NY.START_FRAGMENTS, 'starting fragments')
check(len(cny.choices) == 10, 'tankman choices for 10 nations')
check(3 in player.eventsData and 7 in player.eventsData, 'eventsData QUEST+INGAME_EVENTS injected')

import cPickle
import zlib
quests = cPickle.loads(zlib.decompress(player.eventsData[3]))
check(len(quests) == 23, 'fake quests: 9 boxes + 10 chests + 4 collection = 23 (got %d)' % len(quests))
q1 = quests['ny18:chest:1']
check(q1['type'] == 3 and q1['hidden'] and 'vehicles' in q1['bonus'] and q1['bonus'].get('slots') == 1,
      'chest:1 bonus = T-29 + slot')
q4 = quests['ny18:chest:4']['bonus']['tokens']
check(q4.get('ny18:vd:4', {}).get('count') == 1 and q4.get('ny18:vt:4', {}).get('count') == 1,
      'chest:4 bonus = vd:4 + vt:4 tokens')
q3items = quests['ny18:chest:3']['bonus'].get('items', {})
check(len(q3items) == 2 and all(v == 3 for v in q3items.values()), 'chest:3 items = 2 kinds x3')
ingame = cPickle.loads(zlib.decompress(player.eventsData[7]))
check(ingame == {'newYearEvent': {'state': 1}}, 'INGAME_EVENTS newYearEvent state=IN_PROGRESS')

# ------------------------------------------------------------ command flow
RESULTS = []


def cmd(command, *args):
    del RESULTS[:]

    def cb(request_id, code, txt, ext):
        RESULTS.append((code, ext))

    NY.handle_command(player, command, cb, args)
    flush_callbacks()
    return RESULTS[0]


# place without owning the toy -> failure
code, ext = cmd(ac.CMD_NEW_YEAR_SLOT_FILL, 0, 5, 0)
check(code == -1, 'SLOT_FILL without owning toy fails')

# open a mission box -> 3 toys
code, ext = cmd(ac.CMD_NEW_YEAR_OPEN_BOX, 'ny18:box:s')
toys = ext.get('ny18Toys', {})
check(code == 0 and sum(toys.values()) == 3, 'mission box gives 3 toys: %r' % (toys,))
check(cny.boxes['ny18:box:s'] == 19, 'box token consumed')
check(all(cny.inventory_toys[t].totalCount == c for t, c in toys.iteritems()), 'box toys landed in inventory')
check(all(nyt.g_cache.toys[t].setting == 'soviet' for t in toys), 'soviet box yields soviet toys')

# open a present box -> rank5 toy + gold
code, ext = cmd(ac.CMD_NEW_YEAR_OPEN_BOX, 'ny18:box:sp')
toys = ext.get('ny18Toys', {})
ranks = [nyt.g_cache.toys[t].rank for t in toys]
check(code == 0 and 5 in ranks and ext.get('gold', 0) >= 250, 'present box: rank-5 toy + >=250 gold (%r)' % (ext,))

# place a toy of matching type into slot 0 (top)
top_toy = None
for toy in nyt.g_cache.toys.itervalues():
    if toy.type == nyt.g_cache.slots[0].type and toy.rank == 5:
        top_toy = toy.id
        break
cmd(ac.CMD_NEW_YEAR_ADD_TOYS_DEV, [top_toy])
code, ext = cmd(ac.CMD_NEW_YEAR_SLOT_FILL, 0, top_toy, 0)
check(code == 0 and cny.slots[0] == top_toy, 'toy placed into slot 0')
check(cny.inventory_toys[top_toy].totalCount == 0, 'placed toy excluded from inventory total')

# remove it again
code, ext = cmd(ac.CMD_NEW_YEAR_SLOT_FILL, 0, -1, 0)
check(code == 0 and cny.slots[0] == -1 and cny.inventory_toys[top_toy].totalCount == 1, 'toy removed back to inventory')

# fill every slot with rank-5 toys -> atmosphere 10, all chests granted
for slot in nyt.g_cache.slots:
    cand = None
    for toy in nyt.g_cache.toys.itervalues():
        if toy.type == slot.type and toy.rank == 5:
            cand = toy.id
            break
    cmd(ac.CMD_NEW_YEAR_ADD_TOYS_DEV, [cand])
    cmd(ac.CMD_NEW_YEAR_SLOT_FILL, slot.id, cand, 0)
atmosphere = nyt.getTotalAtmosphere(cny.slots)
check(nyt.getAtmosphereLevel(atmosphere) == 10, 'atmosphere level 10 with rank-5 toys (atm=%d)' % atmosphere)
check(cny.level == 10, 'server level reached 10')
check(all(cny.chests.get('ny18:chest:%d' % lvl, 0) >= 1 for lvl in xrange(1, 11)), 'all 10 chests granted')

# break one placed toy the way the client does it: remove from slot first
# (SLOT_FILL -1), then BREAK_TOYS consumes it from the inventory
frag_before = cny.fragments
placed = cny.slots[0]
cmd(ac.CMD_NEW_YEAR_SLOT_FILL, 0, -1, 0)
inv_before = cny.inventory_toys[placed].totalCount
code, ext = cmd(ac.CMD_NEW_YEAR_BREAK_TOYS, (placed, 1))
check(code == 0 and cny.slots[0] == -1 and cny.inventory_toys[placed].totalCount == inv_before - 1,
      'remove+break consumes the toy from inventory')
check(cny.fragments == frag_before + nyt.g_cache.toys[placed].fragments, 'fragments granted for broken toy')
# top up fragments for the expensive specified craft below
cmd(ac.CMD_NEW_YEAR_ADD_TOYS_DEV, [placed, placed, placed])
cmd(ac.CMD_NEW_YEAR_BREAK_TOYS, (placed, 3))

# craft fully random toy (cost 100)
frag_before = cny.fragments
code, ext = cmd(ac.CMD_NEW_YEAR_CRAFT, -1, -1, -1)
check(code == 0 and ext in nyt.g_cache.toys, 'craft returns a toy id (ext=%r)' % (ext,))
check(cny.fragments == frag_before - 100, 'random craft costs 100 fragments')

# craft exact rank-5 of specified type+setting: cost 480*3
frag_before = cny.fragments
code, ext = cmd(ac.CMD_NEW_YEAR_CRAFT, 0, 0, 5)
descr = nyt.g_cache.toys.get(ext)
check(code == 0 and descr is not None and descr.rank == 5 and descr.settingID == 0, 'specified craft gives rank-5 soviet toy')
check(cny.fragments == frag_before - 1440, 'specified rank-5 craft costs 1440 fragments')

# re-placing the toy already in the slot must not consume an inventory copy
slot1_toy = cny.slots[1]
inv1_before = cny.inventory_toys[slot1_toy].totalCount
code, ext = cmd(ac.CMD_NEW_YEAR_SLOT_FILL, 1, slot1_toy, 0)
check(code == 0 and cny.slots[1] == slot1_toy and cny.inventory_toys[slot1_toy].totalCount == inv1_before,
      're-placing same toy into its slot is a no-op')

# mark toys as seen reduces newCount
some_toy = next(iter([t for t, d in cny.inventory_toys.iteritems() if d.newCount > 0]))
before = cny.inventory_toys[some_toy].newCount
cmd(ac.CMD_NEW_YEAR_SEE_TOYS, (some_toy, before))
check(cny.inventory_toys[some_toy].newCount == 0, 'SEE_TOYS clears newCount')

# open chest:4 -> vd:4 + vt:4 tokens on the client
code, ext = cmd(ac.CMD_NEW_YEAR_OPEN_BOX, 'ny18:chest:4')
check(code == 0 and cny.vd.get('ny18:vd:4') == 1 and cny.vt.get('ny18:vt:4') == 1, 'chest:4 grants vd:4 + vt:4')
check(cny.chests['ny18:chest:4'] == 0, 'chest:4 token consumed')

# select vehicle discount (shop goodies stubbed)
NY.build_shop_goodies = lambda: {12200: object()}
code, ext = cmd(ac.CMD_NEW_YEAR_SELECT_DISCOUNT, 12200, 'ny18:vd:4')
check(code == 0 and cny.vd.get('ny18:vd:4') == 0, 'SELECT_DISCOUNT consumes vd:4')
check(_ITEMS_CACHE.items.goodies._data.get('goodies', {}).get(12200) == (1, 0, 1), 'goodie granted into GoodiesRequester')
check(_UPDATES and 12200 in _UPDATES[-1].get('goodies', {}), 'g_clientUpdateManager goodies update fired')

# recruit tankwoman for ussr (nation 0), innation 5, role gunner(3)
code, ext = cmd(ac.CMD_NEW_YEAR_SELECT_TANKMAN, (0, 5, 3), ('ny18:vt:4',))
check(code == 0 and ext.get('tmanInvID') == 100 and str(ext.get('tmanCompDescr')).startswith('FAKE_CD_gunner'),
      'SELECT_TANKMAN ext = {tmanInvID, tmanCompDescr} (%r)' % (ext,))
check(100 in _TEST_STATE['t_cache'], 'tankwoman added to Manager t_cache (barracks)')
check(cny.choices[0] is not None, 'next choice regenerated for nation 0')

# collection pack/unpack round trip covered implicitly; final summary
print '\n%d checks failed' % len(FAILS)
sys.exit(1 if FAILS else 0)
