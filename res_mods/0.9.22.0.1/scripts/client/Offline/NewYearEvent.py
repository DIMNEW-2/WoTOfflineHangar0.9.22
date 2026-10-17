# -*- coding: utf-8 -*-
# Offline restoration of the New Year event 'Новогоднее наступление'
# (NY 2018, tokens 'ny18:*') for WoTOfflineHangar0.9.22.
#
# This module provides the local (client-side only) behaviour of the game
# commands CMD_NEW_YEAR_* that normally live on the game server: it keeps the
# event state in the account's own ClientNewYear cache and answers NY requests
# with the same callback contract the vanilla client expects.  No network
# protocol is emulated - everything happens in local memory of the offline
# hangar, in accordance with the project disclaimer.
#
# Mounted automatically on import of the Offline package (see Offline/__init__.py).

import random

# ---------------------------------------------------------------------------
# constants / small helpers
# ---------------------------------------------------------------------------

_LOG_PREFIX = '[OFFLINE-NY] '

# 'h07_newyear_2015' - the festive hangar space of the 0.9.22 New Year event.
NY_HANGAR_PATH = 'h07_newyear_2015'

# Starting kit: no battles are available offline, so the event currency and a
# stock of gift boxes are granted on the first entry into the hangar.
STARTER_FRAGMENTS = 1000
STARTER_BOXES = {'ny18:box:r': 5,
 'ny18:box:s': 3,
 'ny18:box:tw': 3,
 'ny18:box:mw': 3,
 'ny18:box:a': 3,
 'ny18:box:sp': 2,
 'ny18:box:twp': 2,
 'ny18:box:mwp': 2,
 'ny18:box:ap': 2}

# Vehicle discount (goodies) generation for atmosphere levels 2..10.
# Each level gets its own 100-ids range (item_defs/new_year/variadic_discounts.xml).
DISCOUNT_PERCENT = 50

# Toy cfg keys read from item_defs/new_year/toys.xml.  The offline project runs
# against the vanilla resources where these fields exist; we re-read them to
# make sure ToyDescriptor exposes them even if the cache was built incompletely.
_TOY_CFG_KEYS = ('icon', 'slotIcon', 'name', 'model', 'regularEffect', 'hangingEffect')


def _log(msg):
    try:
        print _LOG_PREFIX + str(msg)
    except Exception:
        pass


def _warn(msg):
    _log('WARNING: %s' % msg)


class NYCommandError(Exception):
    """Raised to answer a NY command with RES_FAILURE."""

    def __init__(self, txt=''):
        super(NYCommandError, self).__init__(txt)
        self.txt = txt


# ---------------------------------------------------------------------------
# global mutable bookkeeping (not stored in ClientNewYear)
# ---------------------------------------------------------------------------

class _State(object):
    started = False
    initDone = False
    seeded = False
    maxReachedLevel = 0
    rewardedCollections = set()


def _player():
    import BigWorld
    return BigWorld.player()


def _cny():
    p = _player()
    cny = getattr(p, 'newYear', None) if p is not None else None
    if cny is None:
        raise NYCommandError('no ClientNewYear')
    return cny


# ---------------------------------------------------------------------------
# local 'server' state pushes through ClientNewYear.synchronize
# ---------------------------------------------------------------------------

def _push(newYearDiff=None, tokensDiff=None):
    """Delivers a diff to ClientNewYear exactly like Account._update would."""
    cny = _cny()
    diff = {}
    if newYearDiff:
        diff['newYear'] = newYearDiff
    if tokensDiff:
        diff['tokens'] = tokensDiff
    if diff:
        cny.synchronize(False, diff)


def _tokensUpdate(counts):
    """counts: {token: count}; -> synchronize() token diff format."""
    return dict(((t, (0, int(c))) for t, c in counts.iteritems()))


def _setToken(token, count):
    _push(tokensDiff=_tokensUpdate({token: count}))


def _getToken(token):
    cny = _cny()
    nyCache = _nyCache()
    if token in nyCache.boxes:
        return cny.boxes.get(token, 0)
    if token in nyCache.chests:
        return cny.chests.get(token, 0)
    if token in nyCache.variadicDiscounts:
        return cny.variadicDiscounts.get(token, 0)
    if token in nyCache.variadicTankmen:
        return cny.variadicTankmen.get(token, 0)
    return 0


def _addTokens(tokenCounts):
    changed = {}
    for token, delta in tokenCounts.iteritems():
        if delta:
            changed[token] = _getToken(token) + delta
    if changed:
        _push(tokensDiff=_tokensUpdate(changed))
    return changed


def _setFragments(count):
    _push(newYearDiff={'toyFragments': int(count)})


def _addFragments(delta):
    cny = _cny()
    _setFragments(max(0, cny.toyFragments + int(delta)))


def _inventoryToysDiff(toyDiffs):
    """toyDiffs: {toyID: deltaCount} -> inventoryToys update (total, new)."""
    cny = _cny()
    inv = cny.inventoryToys
    out = {}
    for toyID, delta in toyDiffs.iteritems():
        old = inv.get(toyID)
        total = (old.totalCount if old is not None else 0) + int(delta)
        new = (old.newCount if old is not None else 0) + int(delta)
        if total < 0:
            total = 0
        if new < 0:
            new = 0
        out[toyID] = (total, new)
    return out


def _applyToyDiffs(toyDiffs):
    if toyDiffs:
        _push(newYearDiff={'inventoryToys': _inventoryToysDiff(toyDiffs)})


def _placeToyOnSlot(slotID, toyID):
    slots = list(_cny().slots)
    if not 0 <= slotID < len(slots):
        raise NYCommandError('bad slot')
    slots[slotID] = int(toyID)
    _push(newYearDiff={'slots': slots})


def _nyCache():
    from items import new_year_types
    return new_year_types.g_cache


# ---------------------------------------------------------------------------
# atmosphere level -> chest / vehicle discount / tankman rewards
# ---------------------------------------------------------------------------

def _atmosphereOfPlacedToys():
    from items import new_year_types
    toys = _nyCache().toys
    atm = 0
    for toyID in _cny().slots:
        if toyID is not None and toyID != new_year_types.INVALID_TOY_ID:
            descr = toys.get(toyID)
            if descr is not None:
                atm += descr.atmosphere
    return atm


def _updateLevelRewards():
    """Grants chest/vd/vt tokens for every newly reached atmosphere level."""
    from items import new_year_types
    cny = _cny()
    level = new_year_types.getAtmosphereLevel(_atmosphereOfPlacedToys())
    nyCache = _nyCache()
    toGrant = {}
    while _State.maxReachedLevel < level:
        _State.maxReachedLevel += 1
        lvl = _State.maxReachedLevel
        chestID = None
        for cid, descr in nyCache.chests.iteritems():
            if descr.level == lvl:
                chestID = cid
                break
        if chestID is not None:
            toGrant[chestID] = toGrant.get(chestID, 0) + 1
        vdID = 'ny18:vd:%d' % lvl
        if vdID in nyCache.variadicDiscounts:
            toGrant[vdID] = toGrant.get(vdID, 0) + 1
        vtID = 'ny18:vt:%d' % lvl
        if vtID in nyCache.variadicTankmen:
            toGrant[vtID] = toGrant.get(vtID, 0) + 1
    diff = {}
    if level != cny.level:
        diff['newYear'] = {'level': level}
    if toGrant:
        diff['tokens'] = _tokensUpdate(dict(((t, _getToken(t) + n) for t, n in toGrant.iteritems())))
    if diff:
        cny.synchronize(False, diff)
    return level


def _updateCollections():
    """Completes 'ny18:cr:*' collection quests when all toys of a setting collected."""
    try:
        cny = _cny()
        nyCache = _nyCache()
        toys = nyCache.toys
        collected = set(cny.toyCollection)
        haveBySetting = {}
        totalBySetting = {}
        for toyID, descr in toys.iteritems():
            totalBySetting[descr.settingID] = totalBySetting.get(descr.settingID, 0) + 1
            if toyID in collected:
                haveBySetting[descr.settingID] = haveBySetting.get(descr.settingID, 0) + 1
        # collectionRewardsBySettingID is a settingID-indexed list of reward quest IDs
        for settingID, rewardID in enumerate(nyCache.collectionRewardsBySettingID):
            if not rewardID or rewardID in _State.rewardedCollections:
                continue
            total = totalBySetting.get(settingID, 0)
            have = haveBySetting.get(settingID, 0)
            if total and have >= total:
                _State.rewardedCollections.add(rewardID)
                _grantCollectionReward(rewardID)
    except Exception as e:
        _warn('collection check failed: %s' % e)


def _grantCollectionReward(rewardID):
    """Completes a hidden collection quest and shows the vanilla sys message."""
    try:
        data = _questBonusData(rewardID)
        _pushTokenQuestMessageLater(data)
    except Exception as e:
        _warn('collection reward %s failed: %s' % (rewardID, e))


# ---------------------------------------------------------------------------
# hidden quests for boxes / chests / collections (eventsData[QUEST] blob)
# ---------------------------------------------------------------------------

def _giftVehicleCD():
    """A gift tank for the level 1 chest (best effort over the vanilla vehicle list)."""
    try:
        from items import vehicles as _veh
        preferred = ('usa:A13_T34_hvy', 'germany:G27_Truder', 'ussr:R80_KV-5', 'germany:G85_JagdPanther_II')
        for name in preferred:
            try:
                return _veh.makeIntCompactDescrByID('vehicle', *_veh.g_list.getIDsByName(name))
            except Exception:
                continue
        for nationID in xrange(8):
            try:
                for vID, item in _veh.g_list.getList(nationID).iteritems():
                    if 'premium' in item.tags and item.level >= 4:
                        return item.compactDescr
            except Exception:
                continue
    except Exception as e:
        _warn('gift vehicle lookup failed: %s' % e)
    return 0


def _hiddenQuestsData():
    """questID -> quest data for eventsData[EVENT_CLIENT_DATA.QUEST]."""
    import time as _time
    finish = _time.time() + 10 * 365 * 24 * 3600
    questType = 3  # EVENT_TYPE.TOKEN_QUEST
    baseConds = {'preBattle': {'account': [], 'vehicle': [], 'battle': []},
     'common': [],
     'postBattle': []}
    quests = {}

    def make(qid, bonus):
        return {'type': questType,
         'hidden': True,
         'bonus': bonus,
         'conditions': baseConds,
         'finishTime': finish}

    # gift boxes - modest random-y rewards
    for boxID in ('ny18:box:r', 'ny18:box:s', 'ny18:box:tw', 'ny18:box:mw', 'ny18:box:a',
                  'ny18:box:sp', 'ny18:box:twp', 'ny18:box:mwp', 'ny18:box:ap'):
        quests[boxID] = make(boxID, {'credits': 25000})
    # chests - the reward track shown in the NY 'Awards' screen
    giftCD = _giftVehicleCD()
    for level in range(1, 11):
        chestID = 'ny18:chest:%d' % level
        bonus = {'credits': 50000 + level * 45000}
        if level == 1 and giftCD:
            bonus['vehicles'] = {giftCD: {}}
        if level % 2 == 0:
            bonus['gold'] = 50 + level * 25
        if level == 5 or level == 10:
            bonus['premium'] = 3
            bonus['freeXP'] = 5000 * level
        quests[chestID] = make(chestID, bonus)
    # collection completion rewards
    for rewardID in ('ny18:cr:s', 'ny18:cr:tw', 'ny18:cr:mw', 'ny18:cr:a'):
        quests[rewardID] = make(rewardID, {'gold': 300, 'premium': 7})
    return quests


def _questBonusData(questID):
    """TokenQuests message data for a hidden quest completion."""
    quests = _hiddenQuestsData()
    qData = quests.get(questID, {})
    bonus = dict(qData.get('bonus', {}))
    bonus['completedQuestIDs'] = set([questID])
    return bonus


def _installEventsData():
    """Puts NY state + hidden quests into account eventsData and notifies."""
    import cPickle
    import zlib
    from constants import EVENT_CLIENT_DATA
    try:
        from items.new_year_types import NY_STATE
        nyState = NY_STATE.IN_PROGRESS
    except Exception:
        nyState = 1  # NY_STATE.IN_PROGRESS
    p = _player()
    ed = p.eventsData
    ed[EVENT_CLIENT_DATA.INGAME_EVENTS] = zlib.compress(cPickle.dumps({'newYearEvent': {'state': nyState}}))
    ed[EVENT_CLIENT_DATA.INGAME_EVENTS_REV] = 1
    ed[EVENT_CLIENT_DATA.QUEST] = zlib.compress(cPickle.dumps(_hiddenQuestsData()))
    ed[EVENT_CLIENT_DATA.QUEST_REV] = 1
    try:
        from PlayerEvents import g_playerEvents
        g_playerEvents.onEventsDataChanged()
    except Exception as e:
        _warn('onEventsDataChanged failed: %s' % e)


# ---------------------------------------------------------------------------
# system (service channel) messages
# ---------------------------------------------------------------------------

_PUSHED_MSG_ID = [100000]


def _pushTokenQuestMessage(data):
    """Sends a synthetic tokenQuests service-channel message (box open / collection)."""
    try:
        from chat_shared import SYS_MESSAGE_TYPE
        from messenger.proto.bw.wrappers import ServiceChannelMessage
        from messenger.proto.events import g_messengerEvents
        _PUSHED_MSG_ID[0] += 1
        msg = ServiceChannelMessage(messageID=_PUSHED_MSG_ID[0], user_id=-1, type=SYS_MESSAGE_TYPE.tokenQuests.index(), data=data)
        g_messengerEvents.serviceChannel.onChatMessageReceived(_PUSHED_MSG_ID[0], msg)
    except Exception as e:
        _warn('tokenQuests message failed: %s' % e)


def _pushTokenQuestMessageLater(data):
    """Deferred push: box-open listeners subscribe only after open() has returned."""
    try:
        import BigWorld
        BigWorld.callback(0, (lambda : _pushTokenQuestMessage(data)))
    except Exception:
        _pushTokenQuestMessage(data)


# ---------------------------------------------------------------------------
# rewards rolling for gift boxes
# ---------------------------------------------------------------------------

def _rollBoxRewards(boxID):
    """Returns (bonusData, toyDiffs, fragmentDelta) for one opened gift box."""
    nyCache = _nyCache()
    descr = nyCache.boxes.get(boxID)
    setting = getattr(descr, 'setting', None) if descr is not None else None
    isPresent = bool(getattr(descr, 'present', False)) if descr is not None else False
    toysBySetting = {}
    for toyID, toy in nyCache.toys.iteritems():
        toysBySetting.setdefault(toy.setting, []).append(toyID)
    if setting is None:
        pool = None
    else:
        pool = toysBySetting.get(setting) or None
    if not pool:
        pool = [t for ids in toysBySetting.itervalues() for t in ids]
    nToys = random.randint(3, 4) if isPresent else random.randint(1, 3)
    toyDiffs = {}
    toyIDs = []
    for _ in range(nToys):
        toyID = random.choice(pool)
        toyDiffs[toyID] = toyDiffs.get(toyID, 0) + 1
        toyIDs.append(toyID)
    fragments = random.randint(60, 150) if isPresent else random.randint(25, 70)
    credits = random.randint(50000, 120000) if isPresent else random.randint(15000, 45000)
    data = {'completedQuestIDs': set([boxID]),
     'ny18Toys': toyIDs,
     'credits': credits}
    if isPresent or random.random() < 0.15:
        data['gold'] = random.randint(50, 150)
    return (data, toyDiffs, fragments)


# ---------------------------------------------------------------------------
# vehicle personal discounts (goodies in the local shop data)
# ---------------------------------------------------------------------------

def _buildNYGoodies():
    """{goodieID: _NamedGoodieData} - vehicle discounts for atmosphere levels 2..10."""
    try:
        from gui.shared.utils.requesters.ShopRequester import _NamedGoodieData
        from goodies.goodie_constants import GOODIE_VARIETY, GOODIE_TARGET_TYPE, GOODIE_RESOURCE_TYPE
        from items import vehicles as _veh
    except Exception as e:
        _warn('goodies build failed to import: %s' % e)
        return {}
    out = {}
    usedPerLevel = {}
    for level in range(2, 11):
        rangeFrom = 12000 + (level - 2) * 100
        rangeTo = rangeFrom + 99
        usedPerLevel[level] = 0
        for nationID in xrange(8):
            try:
                listing = _veh.g_list.getList(nationID)
            except Exception:
                continue
            for vID, item in listing.iteritems():
                try:
                    if usedPerLevel[level] > 98:
                        break
                    if item.level != level:
                        continue
                    classTags = _veh.VEHICLE_CLASS_TAGS - {'AT-SPG', 'SPG'}
                    if not (item.tags & classTags):
                        continue
                    goodyID = rangeFrom + usedPerLevel[level]
                    usedPerLevel[level] += 1
                    resourceType = GOODIE_RESOURCE_TYPE.GOLD if 'premium' in item.tags else GOODIE_RESOURCE_TYPE.CREDITS
                    target = (GOODIE_TARGET_TYPE.ON_BUY_VEHICLE, item.compactDescr, None)
                    resource = (resourceType, DISCOUNT_PERCENT, True)
                    out[goodyID] = _NamedGoodieData(GOODIE_VARIETY.DISCOUNT, target, True, None, None, 1, True, None, resource)
                except Exception:
                    continue
    _log('prepared %d NY vehicle discounts' % len(out))
    return out


_NY_GOODIES = {}


def _patchShopGoodies():
    """Serves NY discount descriptions from the local shop when the server data has none."""
    global _NY_GOODIES
    try:
        import sys as _sys
        import gui.shared.utils.requesters  # noqa: F401 - ensure package is imported
        _SR = _sys.modules.get('gui.shared.utils.requesters.ShopRequester')
        if _SR is None:
            _SR = __import__('gui.shared.utils.requesters.ShopRequester', fromlist=['ShopCommonStats'])
    except Exception as e:
        _warn('ShopRequester patch failed: %s' % e)
        return
    if not _NY_GOODIES:
        _NY_GOODIES = _buildNYGoodies()

    def _goodiesData(self):
        try:
            own = {}
            try:
                own = self.getCacheValue('goodies', {}) or {}
            except Exception:
                own = {}
            if own and own.get('goodies'):
                return own
        except Exception:
            pass
        return {'goodies': _NY_GOODIES,
         'prices': {},
         'notInShop': set()}

    patched = []
    for clsName in ('ShopCommonStats', 'ShopRequester', 'DefaultShopRequester'):
        cls = getattr(_SR, clsName, None)
        if cls is not None and hasattr(cls, 'getGoodiesData'):
            cls.getGoodiesData = _goodiesData
            patched.append(clsName)
    if patched:
        _log('shop goodies patched OK (%s)' % ', '.join(patched))
    else:
        _warn('shop goodies: no ShopCommonStats classes found')


def _grantGoodie(goodieID):
    """Adds an active personal goodie to the account and notifies the update manager."""
    p = _player()
    entry = (1, 0, 1)  # (ACTIVE, finishTime, count)
    try:
        cache = p.goodies._ClientGoodies__cache
        cache.setdefault('goodies', {})[goodieID] = entry
    except Exception as e:
        _warn('goodies cache update failed: %s' % e)
    try:
        from PlayerEvents import g_playerEvents
        g_playerEvents.onClientUpdated({'goodies': {goodieID: entry}}, False)
    except Exception as e:
        _warn('onClientUpdated(goodies) failed: %s' % e)
    try:
        from skeletons.gui.shared import IItemsCache
        from helpers import dependency
        ic = dependency.instance(IItemsCache)
        ic._onResync({'goodies': {goodieID: entry}})
    except Exception:
        pass


# ---------------------------------------------------------------------------
# NY tankwomen
# ---------------------------------------------------------------------------

def _pickGirlPassport(nationID):
    """(groupID, firstNameID, lastNameID, iconID) - a female crew-member passport group."""
    from items import tankmen as _tm
    try:
        groups = _tm.getNationGroups(nationID, False)
    except Exception:
        groups = ()
    female = [g for g in groups if getattr(g, 'isFemales', False)]
    if not female:
        female = list(groups)
    if not female:
        return (0, 0, 0, 0)
    group = random.choice(female)
    groupID = 0
    for idx, g in enumerate(groups):
        if g is group:
            groupID = idx
            break
    fn = random.choice(group.firstNamesList) if group.firstNamesList else 0
    ln = random.choice(group.lastNamesList) if group.lastNamesList else 0
    icon = random.choice(group.iconsList) if group.iconsList else 0
    return (groupID, fn, ln, icon)


def _seedTankmanChoices():
    from nations import NAMES as _NATION_NAMES
    choices = {}
    for nationID in xrange(len(_NATION_NAMES)):
        try:
            choices[nationID] = _pickGirlPassport(nationID)
        except Exception:
            continue
    _push(newYearDiff={'tankmanNextChoices': choices})


def _roleNameByIndex(roleID):
    from items import tankmen as _tm
    for name, idx in _tm.SKILL_INDICES.iteritems():
        if idx == roleID and name in _tm.ROLES:
            return name
    for name in _tm.ROLES:
        if _tm.SKILL_INDICES.get(name) == roleID:
            return name
    raise NYCommandError('bad role')


def _createTankwoman(nationID, vehicleTypeID, roleID):
    """Builds an NY girl tankman and puts her into the local inventory cache."""
    from items import tankmen as _tm
    from gui.shared.gui_items import GUI_ITEM_TYPE
    cny = _cny()
    choice = cny.tankmanNextChoices.get(nationID)
    if choice is None:
        choice = _pickGirlPassport(nationID)
    _, firstNameID, lastNameID, iconID = choice
    roleName = _roleNameByIndex(roleID)
    passport = (nationID, True, True, firstNameID, lastNameID, iconID)
    try:
        tmanCD = _tm.generateCompactDescr(passport, vehicleTypeID, roleName, 100, freeSkills=('brotherhood',))
    except Exception:
        tmanCD = _tm.generateCompactDescr(passport, vehicleTypeID, roleName, 100)
    p = _player()
    bucket = p.inventory._Inventory__cache.setdefault(GUI_ITEM_TYPE.TANKMAN, {})
    compDescr = bucket.setdefault('compDescr', {})
    vehiclesMap = bucket.setdefault('vehicle', {})
    tmanInvID = 1
    while tmanInvID in compDescr:
        tmanInvID += 1
    compDescr[tmanInvID] = tmanCD
    vehiclesMap.pop(tmanInvID, None)
    try:
        from PlayerEvents import g_playerEvents
        g_playerEvents.onClientUpdated({'inventory': {GUI_ITEM_TYPE.TANKMAN: {tmanInvID: tmanCD}}}, False)
    except Exception:
        pass
    try:
        from skeletons.gui.shared import IItemsCache
        from helpers import dependency
        dependency.instance(IItemsCache)._onResync({'inventory': {}})
    except Exception:
        pass
    # the offered girl changes after every recruitment
    _push(newYearDiff={'tankmanNextChoices': {nationID: _pickGirlPassport(nationID)}})
    return (tmanInvID, tmanCD)


# ---------------------------------------------------------------------------
# toy cache completion (icon/name/effects/model from item_defs/new_year/toys.xml)
# ---------------------------------------------------------------------------

def _enrichToysCache(cache):
    try:
        import ResMgr
        section = ResMgr.openSection('scripts/item_defs/new_year/toys.xml')
        if section is None:
            return cache
        byID = {}
        for toySec in section.values():
            try:
                byID[toySec.readInt('id')] = toySec
            except Exception:
                continue
        for toyID, descr in cache.iteritems():
            cfg = getattr(descr, '_ToyDescriptor__cfg', None)
            if cfg is None:
                continue
            toySec = byID.get(toyID)
            if toySec is None:
                continue
            for key in _TOY_CFG_KEYS:
                if not cfg.get(key):
                    cfg[key] = toySec.readString(key)
        _log('toy cache completed (%d toys)' % len(cache))
    except Exception as e:
        _warn('toy cache enrich failed: %s' % e)
    return cache


def _patchToyCache():
    try:
        from items import new_year_types
        try:
            _orig = new_year_types._buildToysCache

            def _build():
                return _enrichToysCache(_orig())

            new_year_types._buildToysCache = _build
        except Exception:
            pass
        # if g_cache was already built - complete it in place
        try:
            cache = new_year_types.g_cache.toys
            if cache:
                _enrichToysCache(cache)
        except Exception:
            pass
        # missing key must not raise: the vanilla code reads icon/name/model etc.
        if not getattr(new_year_types.ToyDescriptor, '_nySafeGetattr', False):
            def _safe_getattr(self, name):
                return self._ToyDescriptor__cfg.get(name)
            new_year_types.ToyDescriptor.__getattr__ = _safe_getattr
            new_year_types.ToyDescriptor._nySafeGetattr = True
        # toyGroups / chestIDByLevel are built for the baseapp only - needed for crafting
        cache = new_year_types.g_cache
        if not getattr(cache, 'toyGroups', None):
            try:
                cache.toyGroups = new_year_types._buildToyGroups(cache.toys.itervalues())
            except Exception as e:
                _warn('toyGroups build failed: %s' % e)
        if not getattr(cache, 'toyCountBySettingID', None):
            try:
                cache.toyCountBySettingID = new_year_types._countToysBySetting(cache.toys.itervalues())
            except Exception:
                pass
        if not getattr(cache, 'chestIDByLevel', None):
            try:
                cache.chestIDByLevel = dict(((c.level, cid) for cid, c in cache.chests.iteritems()))
            except Exception:
                pass
    except Exception as e:
        _warn('toy cache patch failed: %s' % e)


# ---------------------------------------------------------------------------
# New Year hangar scene switching (restores NewYearController.__switchHangar)
# ---------------------------------------------------------------------------

def _patchHangarSwitch():
    try:
        from new_year.new_year_controller import NewYearController
        from gui.ClientHangarSpace import g_clientHangarSpaceOverride

        def _switchHangar(self, defaultHangar):
            try:
                if defaultHangar:
                    g_clientHangarSpaceOverride.setPath(None, False, False)
                    g_clientHangarSpaceOverride.setPath(None, True, False)
                else:
                    g_clientHangarSpaceOverride.setPath(NY_HANGAR_PATH, False, False)
                    g_clientHangarSpaceOverride.setPath(NY_HANGAR_PATH, True, False)
                try:
                    from gui.shared.utils.HangarSpace import g_hangarSpace
                    if getattr(g_hangarSpace, 'inited', False):
                        g_hangarSpace.refreshSpace(g_hangarSpace.isPremium, True)
                except Exception:
                    pass
            except Exception as e:
                _warn('hangar switch failed: %s' % e)

        NewYearController._NewYearController__switchHangar = _switchHangar
        _log('NewYearController.__switchHangar restored')
    except Exception as e:
        _warn('hangar switch patch failed: %s' % e)


# ---------------------------------------------------------------------------
# command handlers (the local 'server' side of ClientNewYear requests)
# ---------------------------------------------------------------------------

def _cmdFillSlot(args):
    from items import new_year_types
    slotID, toyID = int(args[0]), int(args[1])
    nyCache = _nyCache()
    slotDescrs = nyCache.slots
    if not 0 <= slotID < len(slotDescrs):
        raise NYCommandError('bad slot')
    slotType = slotDescrs[slotID].type
    cny = _cny()
    slots = list(cny.slots)
    current = slots[slotID] if slotID < len(slots) else new_year_types.INVALID_TOY_ID
    if current == toyID:
        raise NYCommandError('already placed')
    toyDiffs = {}
    if current is not None and current != new_year_types.INVALID_TOY_ID:
        toyDiffs[current] = toyDiffs.get(current, 0) + 1
    if toyID != new_year_types.INVALID_TOY_ID:
        descr = nyCache.toys.get(toyID)
        if descr is None or descr.type != slotType:
            raise NYCommandError('bad toy')
        inv = cny.inventoryToys.get(toyID)
        if inv is None or inv.totalCount <= 0:
            raise NYCommandError('no toy')
        toyDiffs[toyID] = toyDiffs.get(toyID, 0) - 1
    if toyDiffs:
        _applyToyDiffs(toyDiffs)
    _placeToyOnSlot(slotID, toyID)
    _updateLevelRewards()
    return None


def _cmdCraft(args):
    from items import new_year_types
    typeID, settingID, rank = int(args[0]), int(args[1]), int(args[2])
    if rank == 0:
        rank = -1
    cost = new_year_types.calculateCraftCost(typeID != -1, settingID != -1, rank)
    if cost is None:
        raise NYCommandError('bad craft params')
    cny = _cny()
    if cny.toyFragments < cost:
        raise NYCommandError('not enough fragments')
    toyID = new_year_types.choiseToyUsingCraftRules(typeID, settingID, rank)
    if toyID is None:
        raise NYCommandError('no toy for craft rules')
    _setFragments(cny.toyFragments - cost)
    _applyToyDiffs({toyID: 1})
    return toyID


def _cmdBreakToys(args):
    from items import new_year_types
    flat = list(args[0])
    if len(flat) % 2:
        raise NYCommandError('bad toys array')
    want = {}
    for i in range(0, len(flat), 2):
        toyID, count = int(flat[i]), int(flat[i + 1])
        if count > 0:
            want[toyID] = want.get(toyID, 0) + count
    if not want:
        raise NYCommandError('nothing to break')
    cny = _cny()
    nyCache = _nyCache()
    # available = inventory + placed toys
    available = {}
    for toyID, inv in cny.inventoryToys.iteritems():
        available[toyID] = inv.totalCount
    placedByToy = {}
    for slotID, toyID in enumerate(cny.slots):
        if toyID is not None and toyID != new_year_types.INVALID_TOY_ID:
            available[toyID] = available.get(toyID, 0) + 1
            placedByToy.setdefault(toyID, []).append(slotID)
    for toyID, count in want.iteritems():
        if available.get(toyID, 0) < count:
            raise NYCommandError('not enough toys')
    fragments = 0
    toyDiffs = {}
    slots = list(cny.slots)
    slotsChanged = False
    for toyID, count in want.iteritems():
        invEntry = cny.inventoryToys.get(toyID)
        fromInv = min(count, invEntry.totalCount if invEntry is not None else 0)
        if fromInv:
            toyDiffs[toyID] = toyDiffs.get(toyID, 0) - fromInv
        rest = count - fromInv
        while rest > 0 and placedByToy.get(toyID):
            slotID = placedByToy[toyID].pop()
            slots[slotID] = new_year_types.INVALID_TOY_ID
            slotsChanged = True
            rest -= 1
        descr = nyCache.toys.get(toyID)
        if descr is not None:
            fragments += new_year_types.CRAFT.FRAGMENTS_BY_RANK[descr.rank - 1] * count
    if toyDiffs:
        _applyToyDiffs(toyDiffs)
    if slotsChanged:
        _push(newYearDiff={'slots': slots})
    _addFragments(fragments)
    _updateLevelRewards()
    return None


def _cmdSeeToys(args):
    flat = list(args[0])
    cny = _cny()
    diffs = {}
    for i in range(0, len(flat) - 1, 2):
        toyID = int(flat[i])
        inv = cny.inventoryToys.get(toyID)
        if inv is not None and inv.newCount:
            diffs[toyID] = (inv.totalCount, 0)
    if diffs:
        _push(newYearDiff={'inventoryToys': diffs})
    return None


def _cmdOpenBox(args):
    boxID = args[0]
    nyCache = _nyCache()
    cny = _cny()
    if boxID in nyCache.chests:
        return _openChest(boxID)
    if boxID not in nyCache.boxes:
        raise NYCommandError('bad box id')
    if cny.boxes.get(boxID, 0) <= 0:
        raise NYCommandError('no boxes left')
    data, toyDiffs, fragments = _rollBoxRewards(boxID)
    _applyToyDiffs(toyDiffs)
    _addFragments(fragments)
    _addTokens({boxID: -1})
    _updateCollections()
    _pushTokenQuestMessageLater(data)
    return data


def _openChest(chestID):
    from items import new_year_types
    cny = _cny()
    if cny.chests.get(chestID, 0) <= 0:
        raise NYCommandError('no chests left')
    nyCache = _nyCache()
    descr = nyCache.chests.get(chestID)
    level = getattr(descr, 'level', 1)
    # extra festive payload on top of the quest bonuses shown by the UI
    toyDiffs = {}
    pool = [t for t, d in nyCache.toys.iteritems() if d.rank == min(level, 5)]
    for _ in range(2):
        if pool:
            toyID = random.choice(pool)
            toyDiffs[toyID] = toyDiffs.get(toyID, 0) + 1
    if toyDiffs:
        _applyToyDiffs(toyDiffs)
    _addFragments(25 * level)
    _addTokens({chestID: -1})
    _updateCollections()
    return _questBonusData(chestID)


def _cmdAddToysDev(args):
    flat = list(args[0])
    toyDiffs = {}
    for i in range(0, len(flat) - 1, 2):
        toyDiffs[int(flat[i])] = toyDiffs.get(int(flat[i]), 0) + max(1, int(flat[i + 1]))
    _applyToyDiffs(toyDiffs)
    _updateCollections()
    return None


def _cmdAddTokenDev(args):
    token = args[0]
    _addTokens({token: 1})
    return None


def _cmdSelectDiscount(args):
    goodieID, variadicDiscountID = int(args[0]), args[1]
    nyCache = _nyCache()
    vdDescr = nyCache.variadicDiscounts.get(variadicDiscountID)
    if vdDescr is None:
        raise NYCommandError('bad variadic discount')
    if _getToken(variadicDiscountID) <= 0:
        raise NYCommandError('no discount token')
    rangeFrom, rangeTo = vdDescr.goodiesRange
    if not rangeFrom <= goodieID < rangeTo:
        raise NYCommandError('bad goodie id')
    if goodieID not in _NY_GOODIES:
        raise NYCommandError('unknown goodie')
    _grantGoodie(goodieID)
    _addTokens({variadicDiscountID: -1})
    return None


def _cmdSelectTankman(args):
    intArr, strArr = list(args[0]), list(args[1])
    if len(intArr) < 3 or not strArr:
        raise NYCommandError('bad args')
    nationID, vehicleTypeID, roleID = int(intArr[0]), int(intArr[1]), int(intArr[2])
    variadicTankmanID = strArr[0]
    nyCache = _nyCache()
    if variadicTankmanID not in nyCache.variadicTankmen:
        raise NYCommandError('bad variadic tankman')
    if _getToken(variadicTankmanID) <= 0:
        raise NYCommandError('no tankman token')
    tmanInvID, tmanCD = _createTankwoman(nationID, vehicleTypeID, roleID)
    # token is decremented locally by _TankmanDiscountsStorage after success
    return {'tmanInvID': tmanInvID, 'tmanCompDescr': tmanCD}


# cmd -> (handler, needs 4-arg callback with ext)
_NY_CMD_HANDLERS = None


def _cmdHandlers():
    global _NY_CMD_HANDLERS
    if _NY_CMD_HANDLERS is None:
        import AccountCommands
        _NY_CMD_HANDLERS = {AccountCommands.CMD_NEW_YEAR_SLOT_FILL: _cmdFillSlot,
         AccountCommands.CMD_NEW_YEAR_CRAFT: _cmdCraft,
         AccountCommands.CMD_NEW_YEAR_BREAK_TOYS: _cmdBreakToys,
         AccountCommands.CMD_NEW_YEAR_ADD_TOYS_DEV: _cmdAddToysDev,
         AccountCommands.CMD_NEW_YEAR_OPEN_BOX: _cmdOpenBox,
         AccountCommands.CMD_NEW_YEAR_ADD_TOKEN_DEV: _cmdAddTokenDev,
         AccountCommands.CMD_NEW_YEAR_SEE_TOYS: _cmdSeeToys,
         AccountCommands.CMD_NEW_YEAR_SELECT_DISCOUNT: _cmdSelectDiscount,
         AccountCommands.CMD_NEW_YEAR_SELECT_TANKMAN: _cmdSelectTankman}
    return _NY_CMD_HANDLERS


def isNYCommand(cmd):
    try:
        return cmd in _cmdHandlers()
    except Exception:
        return False


def handleCommand(p, doCmdMethod, cmd, args):
    """Executes a NY command locally and returns the 'ext' response payload."""
    handler = _cmdHandlers()[cmd]
    return handler(args)


# ---------------------------------------------------------------------------
# account patches: showGUI seeding and the __doCmd dispatcher
# ---------------------------------------------------------------------------

def _seedEvent():
    """Initial event state: IN_PROGRESS + starter resources (no battles offline)."""
    if _State.seeded:
        return
    try:
        p = _player()
        if p is None or getattr(p, 'newYear', None) is None:
            return
        _State.seeded = True
    except Exception as e:
        _warn('event seeding failed: %s' % e)
        return

    def step(name, fn):
        try:
            fn()
        except Exception as e:
            _warn('seed step %s failed: %s' % (name, e))
            import traceback
            traceback.print_exc()

    step('eventsData', _installEventsData)
    step('fragments', (lambda : _setFragments(STARTER_FRAGMENTS)))
    step('tankmanChoices', _seedTankmanChoices)
    step('boxes', (lambda : _addTokens(dict(((t, n) for t, n in STARTER_BOXES.iteritems())))))

    def _levels():
        _State.maxReachedLevel = 0
        _updateLevelRewards()

    step('levelRewards', _levels)
    _log('event seeded: state=IN_PROGRESS fragments=%d boxes=%d' % (STARTER_FRAGMENTS, sum(STARTER_BOXES.values())))


def _patchAccount():
    try:
        import Account as _Acct
    except Exception as e:
        _warn('Account import failed: %s' % e)
        return
    # --- showGUI: seed the event right before the lobby flow begins
    if not getattr(_Acct.PlayerAccount, '_nyShowGUIWrapped', False):
        _orig_showGUI = _Acct.PlayerAccount.showGUI

        def _showGUI(self, ctx):
            try:
                _seedEvent()
            except Exception as e:
                _warn('seed before showGUI failed: %s' % e)
            return _orig_showGUI(self, ctx)

        _Acct.PlayerAccount.showGUI = _showGUI
        _Acct.PlayerAccount._nyShowGUIWrapped = True
        _log('PlayerAccount.showGUI wrapped')
    # --- __doCmd: dispatch NY commands to the local handlers
    if not getattr(_Acct.PlayerAccount, '_nyDoCmdWrapped', False):
        try:
            _orig_do_cmd = _Acct.PlayerAccount._PlayerAccount__doCmd
        except AttributeError:
            _orig_do_cmd = None

        def _doCmd(self, doCmdMethod, cmd, callback, *args):
            import AccountCommands
            import BigWorld
            requestID = AccountCommands.REQUEST_ID_UNRESERVED_MIN
            try:
                if isNYCommand(cmd):
                    def _replyNY(resID, errStr, ext):
                        if callback is not None:
                            callback(requestID, resID, errStr, ext)

                    try:
                        ext = handleCommand(self, doCmdMethod, cmd, args)
                    except NYCommandError as e:
                        errTxt = e.txt
                        _log('cmd %s failed: %s' % (cmd, errTxt))
                        BigWorld.callback(0, (lambda t=errTxt: _replyNY(AccountCommands.RES_FAILURE, t, None)))
                        return requestID
                    except Exception as e:
                        _warn('cmd %s crashed: %s' % (cmd, e))
                        import traceback
                        traceback.print_exc()
                        BigWorld.callback(0, (lambda : _replyNY(AccountCommands.RES_FAILURE, 'internal', None)))
                        return requestID
                    BigWorld.callback(0, (lambda : _replyNY(AccountCommands.RES_SUCCESS, '', ext)))
                    return requestID
            except Exception as e:
                _warn('NY dispatch failed: %s' % e)
            if _orig_do_cmd is not None:
                return _orig_do_cmd(self, doCmdMethod, cmd, callback, *args)
            if callback is not None:
                BigWorld.callback(0, (lambda : callback(requestID, AccountCommands.RES_SUCCESS, '')))
            return requestID

        _Acct.PlayerAccount._PlayerAccount__doCmd = _doCmd
        _Acct.PlayerAccount._nyDoCmdWrapped = True
        _log('PlayerAccount.__doCmd wrapped (NY dispatch)')


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def init():
    if _State.initDone:
        return
    _State.initDone = True
    _log('initializing «Новогоднее наступление» offline event')
    try:
        _patchToyCache()
    except Exception as e:
        _warn('toy cache: %s' % e)
    try:
        _patchShopGoodies()
    except Exception as e:
        _warn('shop goodies: %s' % e)
    try:
        _patchHangarSwitch()
    except Exception as e:
        _warn('hangar switch: %s' % e)
    try:
        _patchAccount()
    except Exception as e:
        _warn('account patches: %s' % e)
        import traceback
        traceback.print_exc()
    _log('init done')
