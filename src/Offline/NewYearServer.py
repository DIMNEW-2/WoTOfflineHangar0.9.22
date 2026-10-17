# -*- coding: utf-8 -*-
"""
Local (offline) server for the New Year event of WoT 0.9.21.0.1 -
"Holiday Ops 2018" / «Новогоднее наступление».

The 0.9.21 client contains the complete event implementation (NY hangar
h07_newyear_2015, decorations, atmosphere levels, boxes/chests, vehicle
discounts, tankwomen recruitment), but all of it is driven by server data:
`ClientNewYear` state is filled from account diffs, event state comes from
`eventsData[INGAME_EVENTS]`, box/chest opening requires hidden quests in
`eventsData[QUEST]`, and every player action is an account command
(CMD_NEW_YEAR_*). This module emulates that server locally:

* on_account_ready() injects the fake quests + IN_PROGRESS event state into
  `player.eventsData` (before showGUI, so the NY controller picks the NY
  hangar at onLobbyStarted) and performs the initial full sync of
  `player.newYear` (empty slots, level 1 chest, a stock of gift boxes -
  battles are offline-unavailable, boxes are the toy source - and fragments
  for the crafting machine).
* handle_command() serves every CMD_NEW_YEAR_* command routed here by
  Offline.Manager's fake PlayerAccount.__doCmd, answering with the
  4-argument callback (requestId, code, txtMsg, ext) the NY requester
  expects, and pushing state diffs through the stock
  ClientNewYear.synchronize().
* build_shop_goodies() creates the vehicle-discount goodie descriptions
  (levels II-X -> 100/100/100/100/80/60/50/40/30 %) that the client expects
  to find in the shop cache; Manager injects them into the shop requester.

Only hangar/event logic is emulated; there are no battles in the offline
hangar, so mission boxes are granted in a generous amount right away.
"""

import cPickle
import random
import traceback
import zlib

import BigWorld

# ---------------------------------------------------------------------------
# Event tuning (original Holiday Ops 2018 values)
# ---------------------------------------------------------------------------

# Vehicle discount percent granted by the chest of the given atmosphere
# level (level L chest -> discount for a tier-L vehicle).
DISCOUNT_BY_LEVEL = {2: 100, 3: 100, 4: 100, 5: 100, 6: 80, 7: 60, 8: 50, 9: 40, 10: 30}

# Starting stock: without battles, boxes cannot be earned, so grant enough
# to decorate the tree, reach atmosphere level X and craft missing toys.
START_BOXES = {'ny18:box:r': 20,
 'ny18:box:s': 20,
 'ny18:box:tw': 20,
 'ny18:box:mw': 20,
 'ny18:box:a': 20,
 'ny18:box:sp': 5,
 'ny18:box:twp': 5,
 'ny18:box:mwp': 5,
 'ny18:box:ap': 5}
START_FRAGMENTS = 1000

# Fake quest lifetimes: already started, effectively never finishes.
_QUEST_START_TIME = 1513200000  # 2017-12-14
_QUEST_FINISH_TIME = 1893456000  # 2030-01-01

_STATE = None
_SHOP_GOODIES_CACHE = None
_REQUEST_ID = [1000]


def _log(msg):
    print '[OFFLINE-NY] %s' % msg


# ---------------------------------------------------------------------------
# Server state
# ---------------------------------------------------------------------------

class _ServerState(object):

    def __init__(self):
        self.player = None
        self.slots = []  # [toyID or -1] * 13
        self.inventory = {}  # toyID -> [totalCount, newCount] (placed excluded)
        self.collection = set()
        self.level = 1  # max atmosphere level reached (chest progress)
        self.fragments = START_FRAGMENTS
        self.tokens = {}  # token string id -> count (boxes/chests/vd/vt)
        self.tankman_next_choices = {}  # nationID -> (groupID, fn, ln, icon)
        self.collection_rewards_given = set()
        self.toys_by_setting = {}  # settingID -> set(toyID)


def _ny_types():
    from items import new_year_types
    return new_year_types


def _cache():
    return _ny_types().g_cache


def _next_request_id():
    _REQUEST_ID[0] += 1
    return _REQUEST_ID[0]


# ---------------------------------------------------------------------------
# Shop goodies: NY vehicle-discount descriptions
# ---------------------------------------------------------------------------

def build_shop_goodies():
    """Returns {goodieID: _NamedGoodieData} with a credits discount goodie
    for every (non-premium) vehicle of tiers II-X, using the goodie ID
    ranges from item_defs/new_year/variadic_discounts.xml. The client reads
    these from the shop cache to know discount percents and targets."""
    global _SHOP_GOODIES_CACHE
    if _SHOP_GOODIES_CACHE is not None:
        return _SHOP_GOODIES_CACHE
    result = {}
    try:
        import nations
        from items import vehicles as veh_items
        from goodies.goodie_constants import GOODIE_VARIETY, GOODIE_TARGET_TYPE, GOODIE_RESOURCE_TYPE
        from gui.shared.utils.requesters.ShopRequester import _NamedGoodieData
        cache = _cache()
        # vehicles by level: {level: [vehIntCD, ...]}
        by_level = {}
        for nation_name in nations.AVAILABLE_NAMES:
            nation_id = nations.INDICES[nation_name]
            try:
                vlist = veh_items.g_list.getList(nation_id)
            except Exception:
                continue
            for veh_id in sorted(vlist.keys()):
                try:
                    vtype = veh_items.g_cache.vehicle(nation_id, veh_id)
                    level = vtype.level
                    if level not in DISCOUNT_BY_LEVEL:
                        continue
                    if 'premium' in getattr(vtype, 'tags', ()):
                        continue
                    int_cd = veh_items.makeIntCompactDescrByID('vehicle', nation_id, veh_id)
                    by_level.setdefault(level, []).append(int_cd)
                except Exception:
                    continue
        for vd_id, vd_descr in cache.variadicDiscounts.iteritems():
            try:
                level = int(vd_id.split(':')[-1])
            except Exception:
                continue
            percent = DISCOUNT_BY_LEVEL.get(level)
            if percent is None:
                continue
            range_from, range_to = vd_descr.goodiesRange
            goodie_id = range_from
            for veh_int_cd in by_level.get(level, ()):
                if goodie_id >= range_to:
                    _log('goodies range exhausted for level %d' % level)
                    break
                result[goodie_id] = _NamedGoodieData(GOODIE_VARIETY.DISCOUNT,
                 (GOODIE_TARGET_TYPE.ON_BUY_VEHICLE, veh_int_cd, 0),
                 True, 0, 0, 1, True, None,
                 (GOODIE_RESOURCE_TYPE.CREDITS, percent, True))
                goodie_id += 1
        _log('shop goodies built: %d vehicle discounts' % len(result))
    except Exception:
        _log('build_shop_goodies failed:')
        traceback.print_exc()
        result = {}
    _SHOP_GOODIES_CACHE = result
    return result


# ---------------------------------------------------------------------------
# Fake quests (boxes/chests validation + chest reward screen)
# ---------------------------------------------------------------------------

def _quest_data(bonus=None):
    from constants import EVENT_TYPE
    return {'type': EVENT_TYPE.TOKEN_QUEST,
     'hidden': True,
     'startTime': _QUEST_START_TIME,
     'finishTime': _QUEST_FINISH_TIME,
     'conditions': {'preBattle': {'account': [], 'vehicle': [], 'battle': []},
                    'common': [],
                    'postBattle': []},
     'bonus': bonus or {}}


def _token_bonus(token_id, count=1):
    return {token_id: {'count': count}}


def _equipment_int_cd(name):
    """Finds an equipment (consumable) intCD by its internal name."""
    try:
        from items import vehicles as veh_items
        for eq in veh_items.g_cache.equipments().itervalues():
            for attr in ('name', 'userString', 'icon'):
                val = getattr(eq, attr, None)
                if val and name in str(val):
                    return eq.compactDescr
    except Exception:
        pass
    return None


def _vehicle_int_cd(type_name):
    from items import vehicles as veh_items
    return veh_items.makeIntCompactDescrByID('vehicle', *veh_items.g_list.getIDsByName(type_name))


def _chest_bonus(level):
    """Bonus section of the hidden quest behind chest `level`
    (original Holiday Ops 2018 chest rewards; medals/reserves of the
    original table are battle-only rewards and are omitted offline)."""
    bonus = {}
    if level == 1:
        # T-29 + garage slot (given at the event start)
        try:
            bonus['vehicles'] = {_vehicle_int_cd('usa:A11_T29'): {}}
        except Exception:
            pass
        bonus['slots'] = 1
        return bonus
    tokens = {}
    tokens.update(_token_bonus('ny18:vd:%d' % level))
    if level in (4, 6, 8, 10):
        tokens.update(_token_bonus('ny18:vt:%d' % level))
    bonus['tokens'] = tokens
    if level == 3:
        items = {}
        for name in ('largeRepairkit', 'largeMedkit'):
            int_cd = _equipment_int_cd(name)
            if int_cd:
                items[int_cd] = 3
        if items:
            bonus['items'] = items
    if level == 6:
        bonus['slots'] = 1
    return bonus


def build_quests():
    cache = _cache()
    quests = {}
    for box_id in cache.boxes.iterkeys():
        quests[box_id] = _quest_data()
    for chest_id, chest_descr in cache.chests.iteritems():
        quests[chest_id] = _quest_data(_chest_bonus(chest_descr.level))
    for reward_id in cache.collectionRewardsBySettingID:
        quests[reward_id] = _quest_data()
    return quests


# ---------------------------------------------------------------------------
# Tankwoman next-choice generation
# ---------------------------------------------------------------------------

def _generate_choice(nation_id):
    """(groupID, firstNameID, lastNameID, iconID) for the recruit window.
    groupID is the index inside getNationGroups(nationID, isPremium=True);
    only this server interprets it (the client only displays the names)."""
    from items import tankmen
    groups = tankmen.getNationGroups(nation_id, True)
    female = [(idx, grp) for idx, grp in enumerate(groups) if grp.isFemales]
    pool = female or list(enumerate(groups))
    group_idx, group = random.choice(pool)
    return (group_idx,
     random.choice(group.firstNamesList),
     random.choice(group.lastNamesList),
     random.choice(group.iconsList))


def _generate_all_choices():
    import nations
    choices = {}
    for nation_name in nations.AVAILABLE_NAMES:
        nation_id = nations.INDICES[nation_name]
        try:
            choices[nation_id] = _generate_choice(nation_id)
        except Exception:
            _log('choice generation failed for nation %s' % nation_name)
            traceback.print_exc()
    return choices


# ---------------------------------------------------------------------------
# ClientNewYear synchronization helpers
# ---------------------------------------------------------------------------

def _pack_collection(collection):
    from items.new_year_types import TOY_COLLECTION_BYTES
    data = bytearray(TOY_COLLECTION_BYTES)
    for toy_id in collection:
        if 0 <= toy_id < TOY_COLLECTION_BYTES * 8:
            data[toy_id // 8] |= 1 << toy_id % 8
    return data


def _push(player, newyear_diff=None, tokens_diff=None, is_full_sync=False):
    diff = {}
    if newyear_diff:
        diff['newYear'] = newyear_diff
    if tokens_diff:
        diff['tokens'] = dict(((token, (0, count)) for token, count in tokens_diff.iteritems()))
    if diff:
        player.newYear.synchronize(is_full_sync, diff)


def _inventory_diff(toy_ids):
    state = _STATE
    diff = {}
    for toy_id in toy_ids:
        total, new = state.inventory.get(toy_id, (0, 0))
        diff[toy_id] = (total, new)
    return diff


def _add_toys(toys_counts):
    """Adds toys to the inventory/collection; returns (newYearDiff, addedIDs)."""
    state = _STATE
    ny_diff = {}
    changed = []
    collection_changed = False
    for toy_id, count in toys_counts.iteritems():
        if count <= 0:
            continue
        entry = state.inventory.setdefault(toy_id, [0, 0])
        entry[0] += count
        entry[1] += count
        changed.append(toy_id)
        if toy_id not in state.collection:
            state.collection.add(toy_id)
            collection_changed = True
    if changed:
        ny_diff['inventoryToys'] = _inventory_diff(changed)
    if collection_changed:
        ny_diff['toyCollection'] = _pack_collection(state.collection)
    return (ny_diff, changed)


def _check_level_up():
    """Grants chests for newly reached atmosphere levels.
    Returns (level or None, tokensDiff)."""
    state = _STATE
    ny_types = _ny_types()
    atmosphere = ny_types.getTotalAtmosphere(state.slots)
    level = ny_types.getAtmosphereLevel(atmosphere)
    tokens_diff = {}
    new_level = None
    if level > state.level:
        chest_by_level = dict(((descr.level, chest_id) for chest_id, descr in _cache().chests.iteritems()))
        for lvl in xrange(state.level + 1, level + 1):
            chest_id = chest_by_level.get(lvl)
            if chest_id:
                state.tokens[chest_id] = state.tokens.get(chest_id, 0) + 1
                tokens_diff[chest_id] = state.tokens[chest_id]
        state.level = level
        new_level = level
        _log('atmosphere level up -> %d' % level)
    return (new_level, tokens_diff)


def _check_collection_rewards(player):
    state = _STATE
    ny_types = _ny_types()
    for setting_id, setting_name in enumerate(ny_types.NATIONAL_SETTINGS):
        if setting_id in state.collection_rewards_given:
            continue
        all_toys = state.toys_by_setting.get(setting_id, set())
        if all_toys and all_toys.issubset(state.collection):
            state.collection_rewards_given.add(setting_id)
            _log('collection completed: %s' % setting_name)
            _push_setting_collected_msg(setting_name)


def _push_setting_collected_msg(setting_name):
    """Replicates the stock NY_SETTING_COLLECTED service-channel message
    (the associated c11n style is already available offline: Manager makes
    every customization item purchasable for free)."""
    try:
        from gui import makeHtmlString
        from gui.Scaleform.locale.NY import NY
        from gui.Scaleform.locale.VEHICLE_CUSTOMIZATION import VEHICLE_CUSTOMIZATION
        from helpers import dependency, i18n
        from messenger.m_constants import SCH_CLIENT_MSG_TYPE
        from skeletons.new_year import INewYearController
        _ms = i18n.makeString
        ctrl = dependency.instance(INewYearController)
        ctrl.proto.serviceChannel.pushClientMessage('', SCH_CLIENT_MSG_TYPE.NY_SETTING_COLLECTED,
         auxData=makeHtmlString('html_templates:newYear/sysMsgs', 'settingCollectedBody',
          {'setting': _ms(NY.system_messages_setting_name(setting_name)),
           'styles': _ms(VEHICLE_CUSTOMIZATION.getStyleName(setting_name)),
           'titles': _ms(NY.system_messages_setting_collected_titles(setting_name)),
           'emblem': _ms(NY.system_messages_setting_collected_emblem(setting_name))}))
    except Exception:
        _log('NY_SETTING_COLLECTED message failed:')
        traceback.print_exc()


# ---------------------------------------------------------------------------
# Box storage patch: consume the response ext instead of waiting for a
# tokenQuests service message (which only a real server can produce).
# ---------------------------------------------------------------------------

def _patch_box_storage():
    try:
        from new_year.new_year_controller import _NewYearBoxStorage
        if getattr(_NewYearBoxStorage, '_offline_patched', False):
            return
        _orig = _NewYearBoxStorage._handleRequestResult

        def _patched(self, response):
            if response.isSuccess():
                opening_id = self._openingItemId
                bonuses = []
                try:
                    bonuses = self._NewYearBoxStorage__extractBonuses(dict(response.data or {}))
                except Exception:
                    _log('box bonuses extraction failed:')
                    traceback.print_exc()
                try:
                    from messenger.proto.events import g_messengerEvents
                    g_messengerEvents.serviceChannel.onChatMessageReceived -= self._NewYearBoxStorage__handleSysMsg
                except Exception:
                    pass
                self.onItemOpened(opening_id, bonuses)
                self._openingItemId = None
            else:
                _orig(self, response)
            return

        _NewYearBoxStorage._handleRequestResult = _patched
        _NewYearBoxStorage._offline_patched = True
        _log('_NewYearBoxStorage patched (box rewards from response ext)')
    except Exception:
        _log('_patch_box_storage failed:')
        traceback.print_exc()


def _delayed_discount_storage_init(attempt=0):
    """The vehicle-discount storage builds its cache from the shop at
    onLobbyStarted; offline the shop data (with our goodies) is injected
    by Manager roughly at the same time, so rebuild the cache once both
    are ready."""
    try:
        from helpers import dependency
        from skeletons.new_year import INewYearController
        ctrl = dependency.instance(INewYearController)
        storage = ctrl.vehDiscountsStorage
        if storage.getClientDiscountsCache() is None:
            raise ValueError('discounts storage is not initialized yet')
        storage._VehicleDiscountsStorage__discountsCache = storage._VehicleDiscountsStorage__initLocalCache()
        storage.onUpdated()
        counts = [len(d) for d in storage.getClientDiscountsCache()]
        _log('vehicle discounts cache re-initialized: %r' % (counts,))
        return
    except Exception:
        if attempt < 15:
            BigWorld.callback(1.0, lambda : _delayed_discount_storage_init(attempt + 1))
        else:
            _log('vehicle discounts cache re-init gave up:')
            traceback.print_exc()


# ---------------------------------------------------------------------------
# Entry point called by Manager when the fake account is fully built
# (still before showGUI).
# ---------------------------------------------------------------------------

def on_account_ready(player):
    global _STATE
    ny_types = _ny_types()
    cache = _cache()
    state = _ServerState()
    _STATE = state
    state.player = player
    state.slots = [-1] * len(cache.slots)
    for toy in cache.toys.itervalues():
        state.toys_by_setting.setdefault(toy.settingID, set()).add(toy.id)
    # choiseToyUsingCraftRules() uses g_cache.toyGroups which the client
    # build of new_year_types does not create (server-only) - build it here.
    if not getattr(cache, 'toyGroups', None):
        groups = {}
        for toy in cache.toys.itervalues():
            group_id = ny_types.getToyGroupID(ny_types.TOY_TYPES_IDS_BY_NAME[toy.type], toy.settingID, toy.rank)
            groups.setdefault(group_id, []).append(toy.id)
        cache.toyGroups = groups
    state.tankman_next_choices = _generate_all_choices()
    state.tokens.update(START_BOXES)
    chest_lvl1 = None
    for chest_id, descr in cache.chests.iteritems():
        if descr.level == 1:
            chest_lvl1 = chest_id
    if chest_lvl1:
        state.tokens[chest_lvl1] = 1
    # --- eventsData: fake quests + event state (must precede showGUI) ---
    from constants import EVENT_CLIENT_DATA
    player.eventsData[EVENT_CLIENT_DATA.QUEST] = zlib.compress(cPickle.dumps(build_quests()))
    player.eventsData[EVENT_CLIENT_DATA.INGAME_EVENTS] = zlib.compress(cPickle.dumps({'newYearEvent': {'state': ny_types.NY_STATE.IN_PROGRESS}}))
    try:
        from PlayerEvents import g_playerEvents
        g_playerEvents.onEventsDataChanged()
    except Exception:
        _log('onEventsDataChanged failed:')
        traceback.print_exc()
    # --- initial full sync of ClientNewYear ---
    initial_diff = {'newYear': {'toyCollection': _pack_collection(state.collection),
                     'slots': list(state.slots),
                     'inventoryToys': {},
                     'level': state.level,
                     'toyFragments': state.fragments,
                     'tankmanNextChoices': dict(state.tankman_next_choices)},
     'tokens': dict(((token, (0, count)) for token, count in state.tokens.iteritems()))}
    player.newYear.synchronize(True, initial_diff)
    _patch_box_storage()
    BigWorld.callback(2.0, _delayed_discount_storage_init)
    _log('event started: state=IN_PROGRESS, %d tokens granted' % len(state.tokens))


# ---------------------------------------------------------------------------
# Command handlers. Each returns (resultID, ext); resultID 0 == success.
# ---------------------------------------------------------------------------

def _fail():
    import AccountCommands
    return (AccountCommands.RES_FAILURE, {})


def _cmd_slot_fill(player, slot_id, toy_id, _unused=0):
    ny_types = _ny_types()
    state = _STATE
    cache = _cache()
    if not 0 <= slot_id < len(cache.slots):
        return _fail()
    ny_diff = {}
    changed = []
    old_toy = state.slots[slot_id]
    if toy_id == ny_types.INVALID_TOY_ID:
        if old_toy != ny_types.INVALID_TOY_ID:
            entry = state.inventory.setdefault(old_toy, [0, 0])
            entry[0] += 1
            changed.append(old_toy)
            state.slots[slot_id] = ny_types.INVALID_TOY_ID
    else:
        toy_descr = cache.toys.get(toy_id)
        if toy_descr is None or toy_descr.type != cache.slots[slot_id].type:
            return _fail()
        if old_toy == toy_id:
            # re-placing the very same toy that already occupies the slot
            return (0, {})
        entry = state.inventory.get(toy_id)
        if not entry or entry[0] <= 0:
            return _fail()
        if old_toy != ny_types.INVALID_TOY_ID:
            old_entry = state.inventory.setdefault(old_toy, [0, 0])
            old_entry[0] += 1
            changed.append(old_toy)
        entry[0] -= 1
        entry[1] = min(entry[1], entry[0])
        changed.append(toy_id)
        state.slots[slot_id] = toy_id
        if toy_id not in state.collection:
            state.collection.add(toy_id)
            ny_diff['toyCollection'] = _pack_collection(state.collection)
    if changed:
        ny_diff['inventoryToys'] = _inventory_diff(changed)
    ny_diff['slots'] = list(state.slots)
    new_level, tokens_diff = _check_level_up()
    if new_level is not None:
        ny_diff['level'] = new_level
    _push(player, ny_diff, tokens_diff)
    _check_collection_rewards(player)
    return (0, {})


def _cmd_craft(player, toy_type_id, setting_id, rank):
    ny_types = _ny_types()
    state = _STATE
    cost = ny_types.calculateCraftCost(toy_type_id != -1, setting_id != -1, rank)
    if cost is None or state.fragments < cost:
        _log('craft denied: cost=%r fragments=%d' % (cost, state.fragments))
        return _fail()
    toy_id = ny_types.choiseToyUsingCraftRules(toy_type_id, setting_id, rank)
    if toy_id is None:
        return _fail()
    state.fragments -= cost
    ny_diff, _ = _add_toys({toy_id: 1})
    ny_diff['toyFragments'] = state.fragments
    _push(player, ny_diff)
    _check_collection_rewards(player)
    _log('crafted toy %d for %d fragments' % (toy_id, cost))
    return (0, toy_id)


def _pairs(flat):
    result = []
    flat = list(flat)
    for i in xrange(0, len(flat) - 1, 2):
        result.append((flat[i], flat[i + 1]))
    return result


def _cmd_break_toys(player, flat_toys):
    ny_types = _ny_types()
    state = _STATE
    cache = _cache()
    ny_diff = {}
    changed = []
    slots_changed = False
    gained = 0
    for toy_id, count in _pairs(flat_toys):
        descr = cache.toys.get(toy_id)
        if descr is None or count <= 0:
            continue
        remaining = count
        entry = state.inventory.get(toy_id)
        if entry and entry[0] > 0:
            take = min(entry[0], remaining)
            entry[0] -= take
            entry[1] = min(entry[1], entry[0])
            remaining -= take
            gained += descr.fragments * take
            changed.append(toy_id)
        while remaining > 0 and toy_id in state.slots:
            slot_id = state.slots.index(toy_id)
            state.slots[slot_id] = ny_types.INVALID_TOY_ID
            slots_changed = True
            remaining -= 1
            gained += descr.fragments
    if gained:
        state.fragments += gained
        ny_diff['toyFragments'] = state.fragments
    if changed:
        ny_diff['inventoryToys'] = _inventory_diff(changed)
    tokens_diff = {}
    if slots_changed:
        ny_diff['slots'] = list(state.slots)
        new_level, tokens_diff = _check_level_up()
        if new_level is not None:
            ny_diff['level'] = new_level
    _push(player, ny_diff, tokens_diff)
    _log('toys broken, +%d fragments' % gained)
    return (0, {})


def _cmd_see_toys(player, flat_toys):
    state = _STATE
    changed = []
    for toy_id, count in _pairs(flat_toys):
        entry = state.inventory.get(toy_id)
        if entry and count > 0:
            entry[1] = max(0, entry[1] - count)
            changed.append(toy_id)
    if changed:
        _push(player, {'inventoryToys': _inventory_diff(changed)})
    return (0, {})


def _roll_box(box_descr):
    """Returns (bonusDict, toysCounts) for a gift box opening."""
    ny_types = _ny_types()
    toys = {}

    def add(toy_id):
        if toy_id is not None:
            toys[toy_id] = toys.get(toy_id, 0) + 1

    setting_id = ny_types.NATIONAL_SETTINGS_IDS_BY_NAME[box_descr.setting] if box_descr.setting else -1
    bonus = {}
    if box_descr.present:
        # Large present box: guaranteed 250 gold, one rank-V decoration of
        # the box setting, one random decoration, plus an extra roll.
        add(ny_types.choiseToyUsingCraftRules(-1, setting_id, 5))
        add(ny_types.choiseToyUsingCraftRules(-1, setting_id, -1))
        bonus['gold'] = 250
        roll = random.random()
        if roll < 0.3:
            bonus['premium'] = 1
        elif roll < 0.6:
            bonus['gold'] += 250
        else:
            bonus['credits'] = 300000
    else:
        # Mission box: 3 decorations (of the box setting when it has one).
        for _ in xrange(3):
            add(ny_types.choiseToyUsingCraftRules(-1, setting_id, -1))
    if toys:
        bonus['ny18Toys'] = dict(toys)
    return (bonus, toys)


def _apply_chest_grants(player, level):
    """Applies the tangible chest rewards (vd/vt tokens, consumables).
    The reward screen itself is rendered from the fake hidden quest."""
    state = _STATE
    tokens_diff = {}
    if level >= 2:
        vd = 'ny18:vd:%d' % level
        state.tokens[vd] = state.tokens.get(vd, 0) + 1
        tokens_diff[vd] = state.tokens[vd]
    if level in (4, 6, 8, 10):
        vt = 'ny18:vt:%d' % level
        state.tokens[vt] = state.tokens.get(vt, 0) + 1
        tokens_diff[vt] = state.tokens[vt]
    if level == 3:
        _grant_equipment({'largeRepairkit': 3, 'largeMedkit': 3})
    return tokens_diff


def _grant_equipment(name_to_count):
    """Adds consumables to the fake inventory built by Manager."""
    try:
        from Offline import Manager
        import BigWorld as _BW
        from items import ITEM_TYPE_INDICES
        player = _BW.player()
        eq_idx = ITEM_TYPE_INDICES['equipment']
        bucket = player.inventory._Inventory__cache.setdefault(eq_idx, {})
        granted = False
        for name, count in name_to_count.iteritems():
            int_cd = _equipment_int_cd(name)
            if int_cd:
                bucket[int_cd] = bucket.get(int_cd, 0) + count
                granted = True
        if granted:
            st = Manager.get_offline_state()
            if st.get('resync_inventory'):
                st['resync_inventory']()
    except Exception:
        _log('equipment grant failed:')
        traceback.print_exc()


def _cmd_open_box(player, container_id):
    state = _STATE
    cache = _cache()
    if state.tokens.get(container_id, 0) <= 0:
        _log('open denied, no such container: %s' % container_id)
        return _fail()
    state.tokens[container_id] -= 1
    tokens_diff = {container_id: state.tokens[container_id]}
    if container_id in cache.boxes:
        bonus, toys = _roll_box(cache.boxes[container_id])
        ny_diff, _ = _add_toys(toys)
        _push(player, ny_diff, tokens_diff)
        _check_collection_rewards(player)
        _log('box %s opened: %r' % (container_id, bonus))
        return (0, bonus)
    if container_id in cache.chests:
        level = cache.chests[container_id].level
        tokens_diff.update(_apply_chest_grants(player, level))
        _push(player, None, tokens_diff)
        _log('chest %s (level %d) opened' % (container_id, level))
        return (0, {})
    return _fail()


def _cmd_select_discount(player, goodie_id, variadic_discount_id):
    state = _STATE
    if state.tokens.get(variadic_discount_id, 0) <= 0:
        _log('discount denied, no token %s' % variadic_discount_id)
        return _fail()
    if goodie_id not in build_shop_goodies():
        _log('discount denied, unknown goodieID %r' % (goodie_id,))
        return _fail()
    state.tokens[variadic_discount_id] -= 1
    _push(player, None, {variadic_discount_id: state.tokens[variadic_discount_id]})
    # Grant the goodie to the account so that
    # ShopRequester.personalVehicleDiscounts picks it up and the NY
    # discounts storage records the choice via g_clientUpdateManager.
    try:
        from helpers import dependency
        from skeletons.gui.shared import IItemsCache
        from gui.ClientUpdateManager import g_clientUpdateManager
        from gui.shared.utils.requesters.GoodiesRequester import GoodieVariable
        from goodies.goodie_constants import GOODIE_STATE
        variable = GoodieVariable(GOODIE_STATE.ACTIVE, 0, 1)
        try:
            acct_cache = player.goodies._ClientGoodies__cache
            acct_cache.setdefault('goodies', {})[goodie_id] = (GOODIE_STATE.ACTIVE, 0, 1)
        except Exception:
            pass
        items_cache = dependency.instance(IItemsCache)
        goodies_req = items_cache.items.goodies
        data = goodies_req._data if isinstance(goodies_req._data, dict) else {}
        data.setdefault('goodies', {})[goodie_id] = variable
        goodies_req._data = data
        goodies_req._AbstractRequester__synced = True
        g_clientUpdateManager.update({'goodies': {goodie_id: variable}})
        _log('vehicle discount goodie %d activated' % goodie_id)
    except Exception:
        _log('discount goodie grant failed:')
        traceback.print_exc()
    return (0, {})


def _cmd_select_tankman(player, int_arr, str_arr):
    state = _STATE
    nation_id, veh_innation_id, role_id = (list(int_arr) + [0, 0, 0])[:3]
    variadic_tankman_id = str_arr[0] if str_arr else None
    if state.tokens.get(variadic_tankman_id, 0) <= 0:
        _log('tankman recruit denied, no token %s' % variadic_tankman_id)
        return _fail()
    from items import tankmen
    role_by_id = dict(((idx, name) for name, idx in tankmen.SKILL_INDICES.iteritems() if name in tankmen.ROLES))
    role_name = role_by_id.get(role_id)
    if role_name is None:
        _log('tankman recruit denied, unknown role id %r' % (role_id,))
        return _fail()
    choice = state.tankman_next_choices.get(nation_id)
    if choice is None:
        choice = _generate_choice(nation_id)
    group_idx, first_name_id, last_name_id, icon_id = choice
    groups = tankmen.getNationGroups(nation_id, True)
    group = groups[group_idx] if 0 <= group_idx < len(groups) else groups[0]
    passport = (nation_id, True, bool(group.isFemales), first_name_id, last_name_id, icon_id)
    tman_cd = tankmen.generateCompactDescr(passport, veh_innation_id, role_name, tankmen.MAX_SKILL_LEVEL,
     freeSkills=('brotherhood',))
    # Put the tankwoman into the barracks of Manager's fake inventory
    # (absent from t_in_veh == not in a vehicle == barracks).
    from Offline import Manager
    st = Manager.get_offline_state()
    t_cache = st['t_cache']
    tman_inv_id = max(list(t_cache.keys()) + [99]) + 1
    t_cache[tman_inv_id] = tman_cd
    if st.get('resync_inventory'):
        st['resync_inventory']()
    # The stock _TankmanDiscountsStorage decrements the token count locally
    # on success, so do not push a tokens diff for it (would double-count);
    # only regenerate the next choice for this nation.
    state.tokens[variadic_tankman_id] -= 1
    new_choice = _generate_choice(nation_id)
    state.tankman_next_choices[nation_id] = new_choice
    _push(player, {'tankmanNextChoices': {nation_id: new_choice}})
    _log('tankwoman recruited: invID=%d nation=%d role=%s' % (tman_inv_id, nation_id, role_name))
    return (0, {'tmanInvID': tman_inv_id, 'tmanCompDescr': tman_cd})


def _cmd_add_toys_dev(player, toys):
    counts = {}
    for toy_id in toys:
        counts[toy_id] = counts.get(toy_id, 0) + 1
    ny_diff, _ = _add_toys(counts)
    _push(player, ny_diff)
    _check_collection_rewards(player)
    return (0, {})


def _cmd_add_token_dev(player, token):
    state = _STATE
    state.tokens[token] = state.tokens.get(token, 0) + 1
    _push(player, None, {token: state.tokens[token]})
    return (0, {})


# ---------------------------------------------------------------------------
# Dispatcher (called from Manager's fake PlayerAccount.__doCmd)
# ---------------------------------------------------------------------------

def handle_command(player, cmd, callback, args):
    import AccountCommands
    request_id = _next_request_id()
    result_id, ext = (AccountCommands.RES_FAILURE, {})
    try:
        if _STATE is None:
            _log('command %s received before on_account_ready' % cmd)
        elif cmd == AccountCommands.CMD_NEW_YEAR_SLOT_FILL:
            result_id, ext = _cmd_slot_fill(player, *args)
        elif cmd == AccountCommands.CMD_NEW_YEAR_CRAFT:
            result_id, ext = _cmd_craft(player, *args)
        elif cmd == AccountCommands.CMD_NEW_YEAR_BREAK_TOYS:
            result_id, ext = _cmd_break_toys(player, args[0])
        elif cmd == AccountCommands.CMD_NEW_YEAR_SEE_TOYS:
            result_id, ext = _cmd_see_toys(player, args[0])
        elif cmd == AccountCommands.CMD_NEW_YEAR_OPEN_BOX:
            result_id, ext = _cmd_open_box(player, args[0])
        elif cmd == AccountCommands.CMD_NEW_YEAR_SELECT_DISCOUNT:
            result_id, ext = _cmd_select_discount(player, *args)
        elif cmd == AccountCommands.CMD_NEW_YEAR_SELECT_TANKMAN:
            result_id, ext = _cmd_select_tankman(player, args[0], args[1])
        elif cmd == AccountCommands.CMD_NEW_YEAR_ADD_TOYS_DEV:
            result_id, ext = _cmd_add_toys_dev(player, args[0])
        elif cmd == AccountCommands.CMD_NEW_YEAR_ADD_TOKEN_DEV:
            result_id, ext = _cmd_add_token_dev(player, args[0])
        else:
            _log('unknown NY command %s' % cmd)
    except Exception:
        _log('command %s handler failed:' % cmd)
        traceback.print_exc()
        result_id, ext = (AccountCommands.RES_FAILURE, {})
    if callback is not None:
        BigWorld.callback(0, lambda cb=callback, rid=request_id, code=result_id, e=ext: cb(rid, code, '', e))
    return request_id
