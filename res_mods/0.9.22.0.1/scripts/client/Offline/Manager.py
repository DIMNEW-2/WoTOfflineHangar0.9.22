# uncompyle6 version 3.9.3
# Python bytecode version base 2.7 (62211)
# Decompiled from: Python 3.11.2 (main, Apr  8 2026, 01:58:00) [GCC 12.2.0]
# Embedded file name: Manager
# Compiled at: 2026-07-26 10:26:54
import BigWorld, new, traceback, AccountCommands, account_helpers.AccountSettings as AS
from account_helpers.AccountSettings import AccountSettings as _AccountSettingsCls
from helpers import dependency
from skeletons.connection_mgr import IConnectionManager
from connection_mgr import LOGIN_STATUS as CONNECTION_STATUS
connectionManager = dependency.instance(IConnectionManager)
from PlayerEvents import g_playerEvents
from items import tankmen
from constants import ACCOUNT_ATTR
_modules_inventory = {}
_modules_by_type = {}
_shop_prices_cache = {}
_vehicle_equipments = {}
_vehicle_shells = {}
_vehicle_outfits = {}

def _zero_ammo_for_gun(gun):
    from items import vehicles as _veh_zero
    full = list(_veh_zero.getDefaultAmmoForGun(gun))
    return [0 if i % 2 else v for i, v in enumerate(full)]


def _build_shop_prices():
    global _shop_prices_cache
    if _shop_prices_cache:
        return _shop_prices_cache
    else:
        try:
            import ResMgr, nations
            from items import vehicles as _veh, ITEM_TYPE_INDICES as _ITI, _xml, SIMPLE_ITEM_TYPE_INDICES
            from nations import AVAILABLE_NAMES, INDICES
            result = {}
            import nations as _nations
            none_idx = _nations.NONE_INDEX
            result[none_idx] = {(_ITI['optionalDevice']): ({}, set()), (_ITI['equipment']): ({}, set())}
            for commonKey, (typeName, cacheFunc) in {'optional_devices': (
                                  'optionalDevice', _veh.g_cache.optionalDevices), 
               'equipments': (
                            'equipment', _veh.g_cache.equipments)}.items():
                xmlPath = _veh._VEHICLE_TYPE_XML_PATH + 'common/' + commonKey + '.xml'
                section = ResMgr.openSection(xmlPath)
                if section is None:
                    continue
                for odName, odSection in section.items():
                    try:
                        ctx = (
                         None, xmlPath + '/' + odName)
                        oid = _xml.readInt(ctx, odSection, 'id')
                        price = _xml.readPrice(ctx, odSection, 'price')
                        dev = cacheFunc()[oid]
                        result[none_idx][_ITI[typeName]][0][dev.compactDescr] = price
                    except:
                        pass

                ResMgr.purge(xmlPath, True)

            for nationIdx in INDICES.values():
                try:
                    nationName = AVAILABLE_NAMES[nationIdx]
                except (IndexError, KeyError):
                    continue

                result[nationIdx] = dict((t, ({}, set())) for t in SIMPLE_ITEM_TYPE_INDICES)
                result[nationIdx][_ITI['vehicle']] = ({}, set())
                listXmlPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/list.xml'
                listSection = ResMgr.openSection(listXmlPath)
                if listSection is None:
                    continue
                turretsIDs = ResMgr.openSection(_veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/turrets.xml')
                chassisIDs = ResMgr.openSection(_veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/chassis.xml')
                turretsIDs = turretsIDs['ids'] if turretsIDs else None
                chassisIDs = chassisIDs['ids'] if chassisIDs else None
                for vname, vsection in listSection.items():
                    try:
                        ctx = (
                         None, listXmlPath + '/' + vname)
                        vid = _xml.readInt(ctx, vsection, 'id', 0, 255)
                        price = _xml.readPrice(ctx, vsection, 'price')
                        vehCD = _veh.makeIntCompactDescrByID('vehicle', nationIdx, vid)
                        result[nationIdx][_ITI['vehicle']][0][vehCD] = price
                        xmlVehPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/' + vname + '.xml'
                        vehSec = ResMgr.openSection(xmlVehPath)
                        if vehSec and turretsIDs:
                            for tname, tsec in vehSec['turrets0'].items():
                                try:
                                    tid = _xml.readInt(ctx, turretsIDs, tname)
                                    tprice = _xml.readPrice(ctx, tsec, 'price')
                                    turret = _veh.g_cache.turrets(nationIdx)[tid]
                                    result[nationIdx][_ITI['vehicleTurret']][0][turret.compactDescr] = tprice
                                except:
                                    pass

                        if vehSec and chassisIDs:
                            for cname, csec in vehSec['chassis'].items():
                                try:
                                    cid = _xml.readInt(ctx, chassisIDs, cname)
                                    cprice = _xml.readPrice(ctx, csec, 'price')
                                    ch = _veh.g_cache.chassis(nationIdx)[cid]
                                    result[nationIdx][_ITI['vehicleChassis']][0][ch.compactDescr] = cprice
                                except:
                                    pass

                        if vehSec:
                            ResMgr.purge(xmlVehPath, True)
                    except:
                        pass

                for compKey, (typeName, cacheFunc) in {'guns': (
                          'vehicleGun', _veh.g_cache.guns), 
                   'engines': (
                             'vehicleEngine', _veh.g_cache.engines), 
                   'radios': (
                            'vehicleRadio', _veh.g_cache.radios)}.items():
                    xmlPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/' + compKey + '.xml'
                    sec = ResMgr.openSection(xmlPath)
                    if sec is None:
                        continue
                    ids_sec = sec['ids']
                    shared = sec['shared']
                    if not ids_sec or not shared:
                        continue
                    for mname, msec in shared.items():
                        try:
                            ctx = (
                             None, xmlPath + '/' + mname)
                            mid = _xml.readInt(ctx, ids_sec, mname)
                            price = _xml.readPrice(ctx, msec, 'price')
                            mod = cacheFunc(nationIdx)[mid]
                            result[nationIdx][_ITI[typeName]][0][mod.compactDescr] = price
                        except:
                            pass

                    ResMgr.purge(xmlPath, True)

                xmlPath = _veh._VEHICLE_TYPE_XML_PATH + nationName + '/components/shells.xml'
                sec = ResMgr.openSection(xmlPath)
                if sec:
                    for mname, msec in sec.items():
                        if mname == 'icons':
                            continue
                        try:
                            ctx = (
                             None, xmlPath + '/' + mname)
                            mid = _xml.readInt(ctx, msec, 'id')
                            price = _xml.readPrice(ctx, msec, 'price')
                            shell = _veh.g_cache.shells(nationIdx)[mid]
                            result[nationIdx][_ITI['shell']][0][shell.compactDescr] = price
                        except:
                            pass

                    ResMgr.purge(xmlPath, True)

            _shop_prices_cache = result
        except Exception:
            import traceback
            traceback.print_exc()

        return _shop_prices_cache


def _build_customization_prices():
    """ Free (0-credit) shop prices for every paint/camouflage/decal/emblem/
    inscription/modification/style item, sourced directly from the client's own
    vehicles.g_cache.customization20() cache - no server data is needed for this,
    the catalog is 100% local to the client install.

    Without this, ShopDataParser.getItemsIterator() finds ZERO purchasable
    customization items (nothing in Manager.py ever priced them), so every tab
    in the Customization screen comes back empty. That makes MainView._tabIndex
    fall back to None (main_view.py: self._tabIndex = first(tabsData, {}).get('id')),
    which then throws KeyError: None on every anchor-position timer tick (~60/s),
    freezing the window and its Close button.
    """
    prices = {}
    try:
        from items import vehicles as _veh_c11n
        cache = _veh_c11n.g_cache.customization20()
        collections = {'paints': (cache.paints), 
           'camouflages': (cache.camouflages), 
           'decals': (cache.decals), 
           'modifications': (cache.modifications), 
           'styles': (cache.styles)}
        for name, collection in collections.items():
            for item in collection.itervalues():
                prices[item.compactDescr] = {'credits': 0}

        print '[OFFLINE] customization shop prices built OK (%d items total: %s)' % (
         len(prices), (', ').join('%s=%d' % (k, len(v)) for k, v in collections.items()))
    except Exception as _ce:
        print '[OFFLINE] customization shop prices build FAILED: %s' % _ce
        import traceback
        traceback.print_exc()

    return prices


def _build_shop_items_flat():
    per_nation = _build_shop_prices()
    item_prices = {}
    for nation_data in per_nation.values():
        for type_bucket in nation_data.values():
            if isinstance(type_bucket, tuple) and len(type_bucket) == 2:
                prices_dict = type_bucket[0]
                if isinstance(prices_dict, dict):
                    item_prices.update(prices_dict)

    item_prices.update(_build_customization_prices())
    try:
        from items import ItemsPrices as _ItemsPrices
        item_prices = _ItemsPrices(item_prices)
    except Exception as _ipe:
        print '[OFFLINE] ItemsPrices wrap failed (falling back to raw dict): %s' % _ipe

    try:
        import nations as _nat_sf
        _nat_count = len(_nat_sf.NAMES)
    except Exception:
        _nat_count = 16

    class _DefaultPriceFactors(dict):

        def get(self, key, default=None):
            val = dict.get(self, key)
            if val is not None:
                return val
            else:
                return 1.0

    return {'itemPrices': item_prices, 
       'notInShopItems': (set()), 
       'vehiclesToSellForGold': (set()), 
       'inscriptionGroupPriceFactors': [_DefaultPriceFactors() for _ in range(_nat_count)], 'notInShopInscriptionGroups': [set() for _ in range(_nat_count)], 'camouflagePriceFactors': [_DefaultPriceFactors() for _ in range(_nat_count)], 'notInShopCamouflages': [set() for _ in range(_nat_count)], 'playerEmblemGroupPriceFactors': (_DefaultPriceFactors()), 
       'vehicleCamouflagePriceFactors': (_DefaultPriceFactors()), 
       'vehicleHornPriceFactors': (_DefaultPriceFactors()), 
       'tankmanCost': ({'credits': 0, 'gold': 0, 'roleLevel': 50, 'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': False}, {'credits': 20000, 'gold': 0, 'roleLevel': 50, 'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': False}, {'credits': 0, 'gold': 200, 'roleLevel': 100, 'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': True}), 'paidRemovalCost': {'gold': 10}, 'exchangeRate': 400, 
       'dailyXPFactor': 2, 
       'freeXPConversion': (25, 1), 
       'slotsPrices': [
                     300, [300, 600, 900]], 
       'dropSkillsCost': {0: {'credits': 10, 'gold': 0, 'xpReuseFraction': 0.5}, 1: {'credits': 20000, 'gold': 0, 'xpReuseFraction': 0.5}, 2: {'credits': 0, 'gold': 200, 'xpReuseFraction': 0.5}}, 'isEnabledBuyingGoldShellsForCredits': True, 
       'isEnabledBuyingGoldEqsForCredits': True}


_LAST_PLAYER_ACCOUNT = [
 None]

def init_offline():
    try:
        from gui.Scaleform import VoiceChatInterface
        try:
            VoiceChatInterface.g_instance._VoiceChatInterface__state = VoiceChatInterface.VC_STATES.Failed
        except:
            pass

        import new as _new_vc

        def _fake_vc_init(self, domain=None):

            def caller(callback):
                if callback is not None:
                    callback(False)
                return

            return caller

        def _fake_vc_capture(self):

            def caller(callback):
                if callback is not None:
                    callback([])
                return

            return caller

        VoiceChatInterface.g_instance.initialize = _new_vc.instancemethod(_fake_vc_init, VoiceChatInterface.g_instance, VoiceChatInterface.g_instance.__class__)
        VoiceChatInterface.g_instance.requestCaptureDevices = _new_vc.instancemethod(_fake_vc_capture, VoiceChatInterface.g_instance, VoiceChatInterface.g_instance.__class__)
        print '[OFFLINE] VoiceChatInterface patched OK (legacy module present)'
    except ImportError:
        print '[OFFLINE] VoiceChatInterface not present in 0.9.22 (expected, see comment) - skipping'
    except Exception as _vce:
        print '[OFFLINE] VoiceChatInterface patch failed (non-fatal): %s' % _vce

    print '[OFFLINE] World of Tanks Start ! '
    import Account as _Acct
    _orig_PA_init = _Acct.PlayerAccount.__init__

    def _safe_PA_init(self):
        _LAST_PLAYER_ACCOUNT[0] = self
        try:
            _orig_PA_init(self)
        except (KeyError, AttributeError) as _e:
            if 'file_server' not in str(_e) and not isinstance(_e, AttributeError):
                raise
            import Account as _A
            if _A.g_accountRepository is None:
                _A.g_accountRepository = _A._AccountRepository(getattr(self, 'name', 'offline_account'), self.__class__.__name__, getattr(self, 'initialServerSettings', {}))
            repo = _A.g_accountRepository
            self.contactInfo = repo.contactInfo
            self.syncData = repo.syncData
            self.serverSettings = repo.serverSettings
            self.inventory = repo.inventory
            self.stats = repo.stats
            self.questProgress = repo.questProgress
            self.shop = repo.shop
            self.dossierCache = repo.dossierCache
            self.battleResultsCache = repo.battleResultsCache
            self.intUserSettings = repo.intUserSettings
            self.prebattleInvitations = repo.prebattleInvitations
            self.gMap = repo.gMap
            self.goodies = repo.goodies
            self.vehicleRotation = repo.vehicleRotation
            self.recycleBin = repo.recycleBin
            self.ranked = repo.ranked
            self.badges = repo.badges
            self.customFilesCache = repo.customFilesCache
            self.newYear = repo.newYear
            self.syncData.setAccount(self)
            self.inventory.setAccount(self)
            self.stats.setAccount(self)
            self.questProgress.setAccount(self)
            self.shop.setAccount(self)
            self.dossierCache.setAccount(self)
            self.battleResultsCache.setAccount(self)
            self.intUserSettings.setProxy(self, self.syncData)
            self.prebattleInvitations.setProxy(self)
            self.gMap.setAccount(self)
            self.goodies.setAccount(self)
            self.vehicleRotation.setAccount(self)
            self.recycleBin.setAccount(self)
            self.ranked.setAccount(self)
            self.badges.setAccount(self)
            self.newYear.setAccount(self)
            self.isLongDisconnectedFromCenter = False
            self.prebattle = None
            try:
                from ClientUnitMgr import ClientUnitMgr as _CUM, ClientUnitBrowser as _CUB
                self.unitBrowser = _CUB(self)
                self.unitMgr = _CUM(self)
            except Exception as _ue:
                print '[OFFLINE] unitBrowser/unitMgr init skipped: %s' % _ue
                self.unitBrowser = None
                self.unitMgr = None

            self.prebattleAutoInvites = repo.prebattleAutoInvites
            self.globalRating = 0
            self.prebattleInvites = repo.prebattleInvites
            self.eventNotifications = repo.eventNotifications
            self.clanMembers = repo.clanMembers
            self.eventsData = repo.eventsData
            self.personalMissionsLock = repo.personalMissionsLock
            self.isInRandomQueue = False
            self.isInTutorialQueue = False
            self.isInBootcampQueue = False
            self.isInUnitAssembler = False
            self.isInEventBattles = False
            self.isInSandboxQueue = False
            self.isInFalloutClassic = False
            self.isInFalloutMultiteam = False
            self.isInRankedQueue = False
            self.selectedHangarEntityId = None
            self.isInQueue = False
            self._PlayerAccount__onCmdResponse = {}
            self._PlayerAccount__onStreamComplete = {}
            print '[OFFLINE] PlayerAccount.__init__ patched OK (file_server fix)'

        return

    _Acct.PlayerAccount.__init__ = _safe_PA_init
    try:

        def _fake_do_cmd(self, doCmdMethod, cmd, callback, *args):
            try:
                requestID = AccountCommands.REQUEST_ID_UNRESERVED_MIN
                if callback is not None:
                    BigWorld.callback(0, (lambda : callback(requestID, AccountCommands.RES_SUCCESS, '')))
                return requestID
            except Exception as _dce:
                print '[OFFLINE] _fake_do_cmd inner error: %s' % _dce
                return

            return

        _Acct.PlayerAccount._PlayerAccount__doCmd = _fake_do_cmd
        print '[OFFLINE] PlayerAccount.__doCmd patched OK (fake success, no self.base)'
    except Exception as _dce2:
        print '[OFFLINE] PlayerAccount.__doCmd patch failed: %s' % _dce2

    try:

        def _fake_request_server_stats(self, *args, **kwargs):
            return

        _Acct.PlayerAccount.requestServerStats = _fake_request_server_stats
        print '[OFFLINE] PlayerAccount.requestServerStats patched OK (no-op, no self.base)'
    except Exception as _rsse:
        print '[OFFLINE] PlayerAccount.requestServerStats patch failed: %s' % _rsse

    try:

        def _fake_request_prebattles(self, type, sort_key, idle, start, end):
            return

        _Acct.PlayerAccount.requestPrebattles = _fake_request_prebattles
        print '[OFFLINE] PlayerAccount.requestPrebattles patched OK (no-op, no self.base)'
    except Exception as _rpbe:
        print '[OFFLINE] PlayerAccount.requestPrebattles patch failed: %s' % _rpbe

    try:

        def _fake_request_prebattles_by_name(self, type, idle, creatorMask):
            return

        _Acct.PlayerAccount.requestPrebattlesByName = _fake_request_prebattles_by_name
        print '[OFFLINE] PlayerAccount.requestPrebattlesByName patched OK (no-op, no self.base)'
    except Exception as _rpbne:
        print '[OFFLINE] PlayerAccount.requestPrebattlesByName patch failed: %s' % _rpbne

    try:

        def _fake_request_prebattle_roster(self, prebattleID):
            return

        _Acct.PlayerAccount.requestPrebattleRoster = _fake_request_prebattle_roster
        print '[OFFLINE] PlayerAccount.requestPrebattleRoster patched OK (no-op, no self.base)'
    except Exception as _rpbre:
        print '[OFFLINE] PlayerAccount.requestPrebattleRoster patch failed: %s' % _rpbre

    try:
        import account_helpers.DossierCache as _DC, AccountCommands as _AC

        def _offline_dc_waitForSync(self, callback):
            if callback is not None:
                BigWorld.callback(0, (lambda cb=callback: cb(_AC.RES_CACHE)))
            return

        def _offline_dc_get(self, dossierType, ownerID, callback=None):
            if callback is not None:
                BigWorld.callback(0, (lambda cb=callback: cb(_AC.RES_CACHE, '')))
            return

        def _offline_dc_sync_noop(self):
            self._DossierCache__isSynchronizing = False
            return

        _DC.DossierCache.waitForSync = _offline_dc_waitForSync
        _DC.DossierCache.get = _offline_dc_get
        _DC.DossierCache.synchronize = _offline_dc_sync_noop
        _DC.DossierCache.resynchronize = _offline_dc_sync_noop
        print '[OFFLINE] DossierCache patched OK (sync bypass)'
    except Exception:
        traceback.print_exc()

    try:
        import new as _new_caches
        from skeletons.gui.shared import IItemsCache as _IItemsCache
        _ic = dependency.instance(_IItemsCache)
        try:
            from gui.shared.quests.QuestsCache import g_questsCache as _qc
        except ImportError:
            _qc = None

        from skeletons.account_helpers.settings_core import ISettingsCache as _ISettingsCache
        _sc = dependency.instance(_ISettingsCache)

        def _fake_update_ic(self, updateReason=None, diff=None, callback=None):

            def caller(callback):
                try:
                    import BigWorld as _BW
                    _p = _BW.player()
                    _ir = self._ItemsCache__items.inventory
                    _sr = self._ItemsCache__items.stats
                    _shr = self._ItemsCache__items.shop
                    _inv_raw = dict(_p.inventory._Inventory__cache)
                    _ir._data = _inv_raw
                    from items import vehicles as _veh_mod
                    _veh_data = _inv_raw.get(1, {})
                    _cdByID = {}
                    for _invID, _vCompDescr in _veh_data.get('compDescr', {}).iteritems():
                        try:
                            _cdByID[_invID] = _veh_mod.makeIntCompactDescrByID('vehicle', *_veh_mod.parseVehicleCompactDescr(_vCompDescr))
                        except Exception as _ve:
                            print '[OFFLINE] vehCD error invID=%s: %s' % (_invID, _ve)

                    _ir._InventoryRequester__vehsCDsByID = _cdByID
                    _ir._InventoryRequester__vehsIDsByCD = dict((v, k) for k, v in _cdByID.iteritems())
                    _ir._AbstractRequester__synced = True
                    print '[OFFLINE] InventoryRequester injected: %d vehicles' % len(_cdByID)
                    _p_stats_cache = dict(_p.stats._Stats__cache)
                    _sr._data = _p_stats_cache
                    _sr._AbstractRequester__synced = True
                    _shop_data = _build_shop_items_flat()
                    _shr._data = {'items': _shop_data, 
                       'rev': 0, 
                       'paidRemovalCost': (_shop_data.get('paidRemovalCost', {'gold': 10})), 
                       'exchangeRate': (_shop_data.get('exchangeRate', 400)), 
                       'exchangeRateForShellsAndEqs': 400, 
                       'sellPriceModif': 0.5, 
                       'slotsPrices': [
                                     300, [300, 600, 900]], 
                       'berthsPrices': (
                                      300, 0, [300, 600, 900]), 
                       'dropSkillsCost': {0: {'credits': 10, 'gold': 10, 'xpReuseFraction': 0.5}, 1: {'credits': 20000, 'gold': 0, 'xpReuseFraction': 0.5}, 2: {'credits': 0, 'gold': 200, 'xpReuseFraction': 0.5}}, 'dailyXPFactor': (_shop_data.get('dailyXPFactor', 2)), 
                       'isEnabledBuyingGoldShellsForCredits': True, 
                       'isEnabledBuyingGoldEqsForCredits': True, 
                       'tankmanCost': (_shop_data.get('tankmanCost', ({'credits': 0, 'gold': 0, 'roleLevel': 50, 'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': False}, {'credits': 20000, 'gold': 0, 'roleLevel': 50, 'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': False}, {'credits': 0, 'gold': 200, 'roleLevel': 100, 'baseRoleLoss': 0.0, 'classChangeRoleLoss': 0.0, 'isPremium': True}))), 
                       'refSystem': {'posByXPinTeam': 0}, 'crystalExchangeRate': (_shop_data.get('crystalExchangeRate', 25))}
                    _shr._AbstractRequester__synced = True
                    print '[OFFLINE] ShopRequester injected: %d itemPrices' % len(_shop_data.get('itemPrices', {}))
                    try:
                        _p.shop._Shop__cache = dict(_shr._data)
                        print '[OFFLINE] player.shop (account_helpers.Shop) cache injected OK (mirrors ShopRequester)'
                    except Exception as _pshe:
                        print '[OFFLINE] player.shop cache injection failed: %s' % _pshe

                    self._ItemsCache__items.dossiers._AbstractRequester__synced = True
                    for _extra_req_name in ('goodies', 'recycleBin', 'vehicleRotation',
                                            'ranked', 'badges'):
                        try:
                            _extra_req = getattr(self._ItemsCache__items, _extra_req_name)
                            _extra_req._AbstractRequester__synced = True
                        except Exception as _ere:
                            print "[OFFLINE] extra requester '%s' sync failed: %s" % (_extra_req_name, _ere)

                except Exception as _ie:
                    import traceback as _tb
                    _tb.print_exc()

                self._ItemsCache__waitForSync = False
                try:
                    from gui.shared.items_cache import CACHE_SYNC_REASON as _CSR
                    from gui.shared.gui_items import GUI_ITEM_TYPE as _GIT
                    from collections import defaultdict as _dd_sync
                    _invalid_items = _dd_sync(set)
                    _invalid_items[_GIT.VEHICLE] = set(_cdByID.values())
                    self.onSyncCompleted(_CSR.SHOP_RESYNC, _invalid_items)
                except Exception as _sce:
                    print '[OFFLINE] onSyncCompleted (initial) failed: %s' % _sce
                    import traceback as _tbsc
                    _tbsc.print_exc()

                if callback is not None:
                    BigWorld.callback(0, (lambda cb=callback: cb(self)))
                return

            return caller

        def _fake_isSynced(self):
            return True

        _ic.update = _new_caches.instancemethod(_fake_update_ic, _ic, _ic.__class__)
        _ic.isSynced = _new_caches.instancemethod(_fake_isSynced, _ic, _ic.__class__)

        def _fake_onResync(self, *args):
            try:
                import BigWorld as _BW2
                _p2 = _BW2.player()
                if _p2 is None:
                    return
                _ir2 = self._ItemsCache__items.inventory
                _sr2 = self._ItemsCache__items.stats
                _shr2 = self._ItemsCache__items.shop
                _inv_raw2 = dict(_p2.inventory._Inventory__cache)
                _ir2._data = _inv_raw2
                from items import vehicles as _vm2
                _veh_data2 = _inv_raw2.get(1, {})
                _cdByID2 = {}
                for _iid, _vcd in _veh_data2.get('compDescr', {}).iteritems():
                    try:
                        _cdByID2[_iid] = _vm2.makeIntCompactDescrByID('vehicle', *_vm2.parseVehicleCompactDescr(_vcd))
                    except Exception:
                        pass

                _ir2._InventoryRequester__vehsCDsByID = _cdByID2
                _ir2._InventoryRequester__vehsIDsByCD = dict((v, k) for k, v in _cdByID2.iteritems())
                _ir2._AbstractRequester__synced = True
                from collections import defaultdict as _dd2
                _ir2._InventoryRequester__itemsCache = _dd2(dict)
                print '[OFFLINE] InventoryRequester re-injected: %d vehicles' % len(_cdByID2)
                _sr2._data = dict(_p2.stats._Stats__cache)
                _sr2._AbstractRequester__synced = True
                if not _shr2._AbstractRequester__synced:
                    _shr2._AbstractRequester__synced = True
                for _extra_req_name2 in ('goodies', 'recycleBin', 'vehicleRotation',
                                         'ranked', 'badges', 'dossiers'):
                    try:
                        _extra_req2 = getattr(self._ItemsCache__items, _extra_req_name2)
                        _extra_req2._AbstractRequester__synced = True
                    except Exception:
                        pass

                self._ItemsCache__items.invalidateCache()
                _self_ic = self
                try:
                    from gui.shared.gui_items import GUI_ITEM_TYPE as _GIT2
                    _veh_type_key2 = _GIT2.VEHICLE
                except Exception:
                    _veh_type_key2 = 1

                _invalid_items2 = _dd2(set)
                _invalid_items2[_veh_type_key2] = set(_cdByID2.values())

                def _delayed_sync_completed():
                    try:
                        from gui.shared.items_cache import CACHE_SYNC_REASON as _CSR2
                        _self_ic.onSyncCompleted(_CSR2.INVENTORY_RESYNC, _invalid_items2)
                    except Exception:
                        import traceback as _tb3
                        _tb3.print_exc()

                    return

                BigWorld.callback(0, _delayed_sync_completed)
            except Exception:
                import traceback as _tb2
                _tb2.print_exc()

            return

        _ic._onResync = _new_caches.instancemethod(_fake_onResync, _ic, _ic.__class__)
        if _qc is not None:

            def _fake_update_qc(self, callback=None):

                def caller(callback):
                    if callback is not None:
                        BigWorld.callback(0, (lambda cb=callback: cb(self)))
                    return

                return caller

            _qc.update = _new_caches.instancemethod(_fake_update_qc, _qc, _qc.__class__)

        def _fake_update_sc(self, callback=None):

            def caller(callback):
                if callback is not None:
                    BigWorld.callback(0, (lambda cb=callback: cb(self)))
                return

            return caller

        _sc.update = _new_caches.instancemethod(_fake_update_sc, _sc, _sc.__class__)
        print '[OFFLINE] itemsCache/questsCache/settingsCache update patched OK'
    except Exception:
        traceback.print_exc()

    def fake_connect(*args, **kwargs):
        global _modules_by_type
        global _modules_inventory
        _vehicle_equipments.clear()
        _vehicle_shells.clear()
        try:
            import json
            try:
                connectionManager._ConnectionManager__connectionStatus = CONNECTION_STATUS.LOGGED_ON
                _login_arg = 'offline'
                if len(args) > 0 and isinstance(args[0], dict) and 'login' in args[0]:
                    _login_arg = args[0]['login']
                elif len(args) > 1 and isinstance(args[1], basestring):
                    _login_arg = args[1]
                elif 'login' in kwargs:
                    _login_arg = kwargs['login']
                connectionManager._ConnectionManager__lastLoginName = _login_arg
                print '[OFFLINE] connectionManager forced to LOGIN_STATUS.LOGGED_ON (0.9.22 fix)'
            except Exception as _ce:
                print '[OFFLINE] connectionManager status patch failed: %s' % _ce

            try:
                connectionManager.onLoggedOn(json.loads(json.dumps({'token2': ''})))
            except Exception as _lo_e:
                print '[OFFLINE] onLoggedOn fire failed: %s' % _lo_e

            try:
                connectionManager.onConnected()
            except Exception as _oc_e:
                print '[OFFLINE] onConnected fire failed: %s' % _oc_e

            try:
                from gui import SoundGroups
                if SoundGroups.g_instance:
                    SoundGroups.g_instance.stopAll()
            except:
                pass

            spaceID = BigWorld.createSpace()
            from items import vehicles
            import nations
            from items import ITEM_TYPE_INDICES
            _VEHICLE_IDX = ITEM_TYPE_INDICES['vehicle']
            _CHASSIS_IDX = ITEM_TYPE_INDICES['vehicleChassis']
            _TURRET_IDX = ITEM_TYPE_INDICES['vehicleTurret']
            _GUN_IDX = ITEM_TYPE_INDICES['vehicleGun']
            _ENGINE_IDX = ITEM_TYPE_INDICES['vehicleEngine']
            _RADIO_IDX = ITEM_TYPE_INDICES['vehicleRadio']
            _FUEL_TANK_IDX = ITEM_TYPE_INDICES['vehicleFuelTank']
            _OPTDEV_IDX = ITEM_TYPE_INDICES['optionalDevice']
            _EQUIP_IDX = ITEM_TYPE_INDICES['equipment']
            _SHELL_IDX = ITEM_TYPE_INDICES['shell']
            _TANKMAN_IDX = ITEM_TYPE_INDICES['tankman']
            tank_list = [
             'ussr:R11_MS-1', 'ussr:R68_A-32', 'ussr:R10_AT-1', 'ussr:R08_BT-2', 'ussr:R03_BT-7', 'ussr:R33_Churchill_LL',
             'ussr:R25_GAZ-74b', 'ussr:R01_IS', 'ussr:R19_IS-3', 'ussr:R90_IS_4M', 'ussr:R45_IS-7', 'ussr:R47_ISU-152',
             'ussr:R05_KV', 'ussr:R13_KV-1s', 'ussr:R39_KV-3', 'ussr:R38_KV-220', 'ussr:R67_M3_LL', 'ussr:R32_Matilda_II_LL',
             'ussr:R12_A-20', 'ussr:R51_Object_212', 'ussr:R53_Object_704', 'ussr:R15_S-51', 'ussr:R14_SU-5', 'ussr:R26_SU-8',
             'ussr:R27_SU-14', 'ussr:R16_SU-18', 'ussr:R66_SU-26', 'ussr:R24_SU-76', 'ussr:R02_SU-85', 'ussr:R17_SU-100',
             'ussr:R18_SU-152', 'ussr:R09_T-26', 'ussr:R06_T-28', 'ussr:R04_T-34', 'ussr:R07_T-34-85', 'ussr:R23_T-43',
             'ussr:R20_T-44', 'ussr:R22_T-46', 'ussr:R40_T-54', 'ussr:R31_Valentine_LL', 'ussr:R52_Object_261', 'ussr:R46_KV-13',
             'ussr:R41_T-50', 'ussr:R70_T_50_2', 'ussr:R56_T-127', 'ussr:R34_BT-SV', 'ussr:R54_KV-5', 'ussr:R80_KV1',
             'ussr:R77_KV2', 'ussr:R72_T150', 'ussr:R73_KV4', 'ussr:R78_SU_85I', 'ussr:R81_IS8', 'ussr:R63_ST_I',
             'ussr:R02_SU-85', 'ussr:R84_Tetrarch_LL', 'ussr:R87_T62A', 'ussr:R74_SU100M1', 'ussr:R89_SU122_44', 'ussr:R58_SU-101',
             'ussr:R75_SU122_54', 'ussr:R88_Object268', 'ussr:Observer', 'ussr:R49_SU100Y', 'germany:G35_B-1bis_captured', 'germany:G11_Bison_I',
             'germany:G37_Ferdinand', 'germany:G49_G_Panther', 'germany:G22_Sturmpanzer_II', 'germany:G45_G_Tiger', 'germany:G20_Marder_II', 'germany:G23_Grille',
             'germany:G33_H39_captured', 'germany:G09_Hetzer', 'germany:G02_Hummel', 'germany:G18_JagdPanther', 'germany:G17_JagdPzIV', 'germany:G44_JagdTiger',
             'germany:G12_Ltraktor', 'germany:G42_Maus', 'germany:G64_Panther_II', 'germany:G21_PanzerJager_I', 'germany:G07_Pz35t', 'germany:G08_Pz38t',
             'germany:G06_PzII', 'germany:G25_PzII_Luchs', 'germany:G102_Pz_III', 'germany:G28_PzIII_IV', 'germany:G79_Pz_IV_AusfGH', 'germany:G03_PzV_Panther',
             'germany:G32_PzV_PzIV', 'germany:G32_PzV_PzIV_ausf_Alfa', 'germany:G04_PzVI_Tiger_I', 'germany:G16_PzVIB_Tiger_II', 'germany:G101_StuG_III', 'germany:G26_VK1602',
             'germany:G13_VK3001H', 'germany:G27_VK3001P', 'germany:G24_VK3002DB', 'germany:G15_VK3601H', 'germany:G58_VK4502P', 'germany:G19_Wespe',
             'germany:G61_G_E', 'germany:G54_E-50', 'germany:G55_E-75', 'germany:G56_E-100', 'germany:G57_PzVI_Tiger_P', 'germany:G51_Lowe',
             'germany:G50_T-15', 'germany:G46_T-25', 'germany:G34_S35_captured', 'germany:G52_Pz38_NA', 'germany:G36_PzII_J', 'germany:G66_VK2801',
             'germany:G67_VK4502A', 'germany:G70_PzIV_Hydro', 'germany:G65_JagdTiger_SdKfz_185', 'germany:G77_PzIV_Schmalturm', 'germany:G78_Panther_M10', 'germany:G73_E50_Ausf_M',
             'germany:G41_DickerMax', 'germany:G71_JagdPantherII', 'usa:A02_M2_lt', 'usa:A25_M2_med', 'usa:A04_M3_Grant', 'usa:A03_M3_Stuart',
             'usa:A05_M4_Sherman', 'usa:A06_M4A3E8_Sherman', 'usa:A22_M5_Stuart', 'usa:A10_M6', 'usa:A23_M7_med', 'usa:A16_M7_Priest',
             'usa:A32_M12', 'usa:A17_M37', 'usa:A37_M40M43', 'usa:A18_M41', 'usa:A35_Pershing', 'usa:A62_Ram-II',
             'usa:A01_T1_Cunningham', 'usa:A09_T1_hvy', 'usa:A19_T2_lt', 'usa:A24_T2_med', 'usa:A21_T14', 'usa:A07_T20',
             'usa:A08_T23', 'usa:A11_T29', 'usa:A14_T30', 'usa:A12_T32', 'usa:A13_T34_hvy', 'usa:A15_T57',
             'usa:A38_T92', 'usa:A44_M4A2E4', 'usa:A63_M46_Patton', 'usa:A34_M24_Chaffee', 'usa:A26_T18', 'usa:A27_T82',
             'usa:A29_T40', 'usa:A30_M10_Wolverine', 'usa:A31_M36_Slagger', 'usa:A64_T25_AT', 'usa:A39_T28', 'usa:A40_T95',
             'usa:A43_M22_Locust', 'usa:A36_Sherman_Jumbo', 'usa:A45_M6A2E1', 'usa:A33_MTLS-1G14', 'usa:A66_M103', 'usa:A69_T110E5',
             'usa:A57_M8A1', 'usa:A100_T49', 'usa:A80_T26_E4_SuperPershing', 'usa:A41_M18_Hellcat', 'usa:A72_T25_2', 'usa:A68_T28_Prototype',
             'usa:A85_T110E3', 'usa:A83_T110E4', 'usa:A74_T1_E6', 'usa:A71_T21', 'usa:A103_T71E1', 'usa:A90_T69',
             'usa:A89_T54E1', 'france:F15_AMX_12t', 'france:F16_AMX_13_75', 'france:F17_AMX_13_90', 'france:F08_AMX_50_100', 'france:F09_AMX_50_120',
             'france:F07_AMX_M4_1945', 'france:F13_AMX38', 'france:F14_AMX40', 'france:F06_ARL_44', 'france:F04_B1', 'france:F18_Bat_Chatillon25t',
             'france:F05_BDR_G1B', 'france:F02_D1', 'france:F03_D2', 'france:F10_AMX_50B', 'france:F12_Hotchkiss_H35', 'france:F19_Lorraine40t',
             'france:F01_RenaultFT', 'france:F28_105_leFH18B2', 'france:F27_FCM_36Pak40', 'france:F30_RenaultFT_AC', 'france:F52_RenaultUE57', 'france:F32_Somua_Sau_40',
             'france:F33_S_35CA', 'france:F34_ARL_V39', 'france:F35_AMX_AC_Mle1946', 'france:F36_AMX_AC_Mle1948', 'france:F37_AMX50_Foch', 'france:F64_AMX_50Fosh_155',
             'france:F20_RenaultBS', 'france:F21_Lorraine39_L_AM', 'france:F22_AMX_105AM', 'france:F23_AMX_13F3AM', 'france:F24_Lorraine155_50', 'france:F25_Lorraine155_51',
             'france:F62_ELC_AMX', 'france:F65_FCM_50t', 'china:Ch01_Type59', 'china:Ch01_Type59_Gold', 'china:Ch02_Type62', 'china:Ch03_WZ-111',
             'china:Ch04_T34_1', 'china:Ch04_T34_1_training', 'china:Ch05_T34_2', 'china:Ch06_Renault_NC31', 'china:Ch07_Vickers_MkE_Type_BT26', 'china:Ch08_Type97_Chi_Ha',
             'china:Ch09_M5', 'china:Ch10_IS2', 'china:Ch11_110', 'china:Ch12_111_1_2_3', 'china:Ch15_59_16', 'china:Ch16_WZ_131',
             'china:Ch17_WZ131_1_WZ132', 'china:Ch18_WZ-120', 'china:Ch19_121', 'china:Ch20_Type58', 'china:Ch21_T34', 'china:Ch22_113',
             'china:Ch23_112', 'uk:GB01_Medium_Mark_I', 'uk:GB03_Cruiser_Mk_I', 'uk:GB04_Valentine', 'uk:GB05_Vickers_Medium_Mk_II', 'uk:GB06_Vickers_Medium_Mk_III',
             'uk:GB07_Matilda', 'uk:GB08_Churchill_I', 'uk:GB09_Churchill_VII', 'uk:GB10_Black_Prince', 'uk:GB11_Caernarvon', 'uk:GB12_Conqueror',
             'uk:GB13_FV215b', 'uk:GB20_Crusader', 'uk:GB21_Cromwell', 'uk:GB22_Comet', 'uk:GB23_Centurion', 'uk:GB24_Centurion_Mk3',
             'uk:GB58_Cruiser_Mk_III', 'uk:GB59_Cruiser_Mk_IV', 'uk:GB60_Covenanter', 'uk:GB63_TOG_II', 'uk:GB68_Matilda_Black_Prince', 'uk:GB69_Cruiser_Mk_II',
             'uk:GB70_FV4202_105', 'uk:GB71_AT_15A', 'uk:GB32_Tortoise', 'uk:GB39_Universal_CarrierQF2', 'uk:GB40_Gun_Carrier_Churchill', 'uk:GB42_Valentine_AT',
             'uk:GB48_FV215b_183', 'uk:GB57_Alecto', 'uk:GB71_AT_15A', 'uk:GB72_AT15', 'uk:GB73_AT2', 'uk:GB74_AT8',
             'uk:GB75_AT7', 'uk:GB78_Sexton_I', 'uk:GB51_Excelsior',]
            my_garage = {}
            veh_cds = {}
            for i, name in enumerate(tank_list):
                invID = i + 1
                my_garage[invID] = name
                try:
                    _descr_tmp = vehicles.VehicleDescr(typeName=name)
                    veh_cds[invID] = _descr_tmp.makeCompactDescr()
                    try:
                        _vehicle_shells[invID] = _zero_ammo_for_gun(_descr_tmp.gun)
                    except Exception as _ae:
                        print '[OFFLINE] default ammo error for %s: %s' % (name, _ae)

                except Exception as e:
                    print '[OFFLINE] Error loading %s: %s' % (name, e)

            all_unlocks = []
            for nID in range(len(nations.NAMES)):
                try:
                    vList = vehicles.g_list.getList(nID)
                    for vID in vList.keys():
                        all_unlocks.append(vehicles.makeIntCompactDescrByID('vehicle', nID, vID))

                except:
                    continue

            inv_vehicles = dict((invID, cd) for invID, cd in veh_cds.items())
            t_cache = {}
            t_in_veh = {}
            crew_map = {}
            current_tman_id = 10
            for invID, name in my_garage.items():
                if invID not in veh_cds:
                    continue
                descr = vehicles.VehicleDescr(compactDescr=veh_cds[invID])
                crew_ids = []
                nationID = descr.type.id[0]
                vehTypeID = descr.type.id[1]
                for role in descr.type.crewRoles:
                    passport = tankmen.generatePassport(nationID)
                    tman_cd = tankmen.generateCompactDescr(passport, vehTypeID, role[0], 100)
                    t_cache[current_tman_id] = tman_cd
                    t_in_veh[current_tman_id] = invID
                    crew_ids.append(current_tman_id)
                    current_tman_id += 1

                crew_map[invID] = crew_ids

            _modules_inventory = {}
            _modules_by_type = {}
            for _idx in (_CHASSIS_IDX, _TURRET_IDX, _ENGINE_IDX, _GUN_IDX, _RADIO_IDX, _FUEL_TANK_IDX, _SHELL_IDX, _OPTDEV_IDX, _EQUIP_IDX):
                _modules_by_type.setdefault(_idx, {})

            _SHARED_STATUS = 2
            _LOCAL_STATUS = 3
            _UNLOCKED_STATUSES = (_SHARED_STATUS, _LOCAL_STATUS)
            for nationID in range(len(nations.NAMES)):
                try:
                    for chassis in vehicles.g_cache.chassis(nationID).values():
                        cd = getattr(chassis, 'compactDescr', None)
                        if cd and getattr(chassis, 'status', None) in _UNLOCKED_STATUSES:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_CHASSIS_IDX, {})[cd] = 1

                    for turret in vehicles.g_cache.turrets(nationID).values():
                        cd = getattr(turret, 'compactDescr', None)
                        if cd and getattr(turret, 'status', None) in _UNLOCKED_STATUSES:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_TURRET_IDX, {})[cd] = 1

                    for engine in vehicles.g_cache.engines(nationID).values():
                        cd = getattr(engine, 'compactDescr', None)
                        if cd and getattr(engine, 'status', None) in _UNLOCKED_STATUSES:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_ENGINE_IDX, {})[cd] = 1

                    for gun in vehicles.g_cache.guns(nationID).values():
                        cd = getattr(gun, 'compactDescr', None)
                        if cd and getattr(gun, 'status', None) in _UNLOCKED_STATUSES:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_GUN_IDX, {})[cd] = 1

                    for radio in vehicles.g_cache.radios(nationID).values():
                        cd = getattr(radio, 'compactDescr', None)
                        if cd and getattr(radio, 'status', None) in _UNLOCKED_STATUSES:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_RADIO_IDX, {})[cd] = 1

                    for fuelTank in vehicles.g_cache.fuelTanks(nationID).values():
                        cd = getattr(fuelTank, 'compactDescr', None)
                        if cd and getattr(fuelTank, 'status', None) in _UNLOCKED_STATUSES:
                            _modules_inventory[cd] = 1
                            _modules_by_type.setdefault(_FUEL_TANK_IDX, {})[cd] = 1

                    try:
                        for shell in vehicles.g_cache.shells(nationID).values():
                            cd = getattr(shell, 'compactDescr', None)
                            if cd:
                                _modules_inventory[cd] = 1
                                _modules_by_type.setdefault(_SHELL_IDX, {})[cd] = 1

                    except Exception as e:
                        print '[OFFLINE] Error loading shells: %s' % e

                    try:
                        for od in vehicles.g_cache.optionalDevices().values():
                            cd = getattr(od, 'compactDescr', None)
                            if cd:
                                _modules_inventory[cd] = 1
                                _modules_by_type.setdefault(_OPTDEV_IDX, {})[cd] = 1
                                all_unlocks.append(cd)

                    except Exception as e:
                        print '[OFFLINE] Error loading optional devices: %s' % e

                    try:
                        for eq in vehicles.g_cache.equipments().values():
                            cd = getattr(eq, 'compactDescr', None)
                            if cd:
                                _modules_inventory[cd] = 1
                                _modules_by_type.setdefault(_EQUIP_IDX, {})[cd] = 1
                                all_unlocks.append(cd)

                    except Exception as e:
                        print '[OFFLINE] Error loading equipment: %s' % e

                except Exception as e:
                    print '[OFFLINE] module cache error for nation %d: %s' % (nationID, e)

            print '[OFFLINE] Loaded %d modules' % len(_modules_inventory)
            inv_data = [
             inv_vehicles,
             dict((i, {}) for i in my_garage),
             dict((i, []) for i in my_garage),
             crew_map,
             dict((i, (0, 100)) for i in my_garage),
             dict((i, [0, 0, 0]) for i in my_garage),
             dict((i, [0, 0, 0]) for i in my_garage),
             dict((i, 0) for i in my_garage),
             dict((i, 0) for i in my_garage)]
            off_stats = {'credits': 100000000, 
               'gold': 100000000, 
               'freeXP': 100000000, 
               'mayConsumeWalletResources': 1, 
               'vehTypeXP': {}, 'tkillIsSuspected': False, 
               'restrictions': {}, 'clanInfo': None, 
               'unlocks': (set(all_unlocks) | set(_modules_inventory.keys())), 
               'eliteVehicles': (set(all_unlocks)), 
               'rev': 1, 
               'slots': 1000, 
               'berths': 1000, 
               'currentVehInvID': 1, 
               'isPremium': True, 
               'premiumExpiryTime': 1999999999, 
               'attrs': (ACCOUNT_ATTR.PREMIUM), 
               'dossier': '', 
               'doubleXPVehs': [], 'multipliedXPVehs': [], 'vehTypeLocks': {}, 'denunciationsLeft': 0, 
               'playLimits': (
                            [
                             999999999], [999999999]), 
               'dailyPlayHours': [
                                0, 
                                0, 0, 0, 0, 
                                0, 0, 0], 
               'globalVehicleLocks': {}, 'freeVehiclesLeft': 0, 
               'vehicleSellsLeft': 999999, 
               'freeTMenLeft': 0, 
               'hasFinPassword': False, 
               'clanDBID': 0, 
               'globalRating': 0, 
               'captchaTriesLeft': 999, 
               'finPswdAttemptsLeft': 999, 
               'autoBanTime': 0, 
               'battlesTillCaptcha': 9999, 
               'tutorialsCompleted': 0}
            if not BigWorld.player():
                _initial_server_settings = {'voipDomain': '', 'regional_settings': {'starting_time_of_a_new_day': 0, 
                                         'starting_day_of_a_new_week': 0}, 
                   'roaming': (
                             0, 0, []), 
                   'wallet': (
                            1, 1), 
                   'file_server': {'clan_emblems': {'url_template': '', 'cache_life_time': 0}, 'clan_emblems_small': {'url_template': '', 'cache_life_time': 0}}, 'isEncyclopediaEnabled': 'all', 
                   'isVehiclesCompareEnabled': True, 
                   'isCustomizationEnabled': True}
                BigWorld.createEntity('Account', spaceID, 0, (0, 0, 0), (0, 0, 0), {'name': 'Commander', 
                   'initialServerSettings': _initial_server_settings, 
                   'serverSettings': {'voipDomain': '', 
                                      'regional_settings': {'starting_time_of_a_new_day': 0, 
                                                            'starting_day_of_a_new_week': 0}, 
                                      'roaming': (
                                                0, 0, []), 
                                      'wallet': (
                                               1, 1), 
                                      'file_server': {'clan_emblems': {'url_template': '', 'cache_life_time': 0}, 'clan_emblems_small': {'url_template': '', 'cache_life_time': 0}}, 'isEncyclopediaEnabled': 'all', 
                                      'isVehiclesCompareEnabled': True, 
                                      'isCustomizationEnabled': True}})
                print '[OFFLINE] createEntity Account OK'
                p = BigWorld.player()
                if p is None:
                    p = _LAST_PLAYER_ACCOUNT[0]
                    print '[OFFLINE] BigWorld.player() was None right after createEntity, using captured instance instead'

                def _fake_bw_player():
                    return _LAST_PLAYER_ACCOUNT[0]

                BigWorld.player = _fake_bw_player
                print '[OFFLINE] BigWorld.player patched OK (always returns captured PlayerAccount)'
                import Account as _Acct2
                try:
                    p.serverSettings = dict(_initial_server_settings)
                    if _Acct2.g_accountRepository is not None:
                        _Acct2.g_accountRepository.serverSettings = dict(_initial_server_settings)
                    print '[OFFLINE] p.serverSettings force-set OK (regional_settings/wallet/etc guaranteed present)'
                except Exception as _sse:
                    print '[OFFLINE] p.serverSettings force-set failed: %s' % _sse

                try:
                    if not getattr(p, 'name', None):
                        p.name = 'Commander'
                    print '[OFFLINE] p.name force-set OK (%s)' % p.name
                except Exception as _pne:
                    print '[OFFLINE] p.name force-set failed: %s' % _pne

                try:
                    if _Acct2.g_accountRepository is not None:
                        _Acct2.g_accountRepository.fileServerSettings = {'clan_emblems': {'url_template': '', 'cache_life_time': 0}, 'clan_emblems_small': {'url_template': '', 'cache_life_time': 0}}
                except AttributeError as _fse:
                    print '[OFFLINE] fileServerSettings set failed (non-fatal, 0.9.22 read-only property): %s' % _fse

                import Account as _Acct2
                _orig_cfc_get = _Acct2.g_accountRepository.customFilesCache.__class__.get

                def _safe_cfc_get(self_cfc, url, callback):
                    if not url:
                        if callback:
                            BigWorld.callback(0, (lambda cb2=callback: cb2(url, None)))
                        return
                    return _orig_cfc_get(self_cfc, url, callback)

                import new as _new
                _Acct2.g_accountRepository.customFilesCache.get = _new.instancemethod(_safe_cfc_get, _Acct2.g_accountRepository.customFilesCache, _Acct2.g_accountRepository.customFilesCache.__class__)
                import CurrentVehicle

                class OfflineVehicleWrapper(object):

                    def __init__(self, invID):
                        self.inventoryId = invID
                        self.descriptor = vehicles.VehicleDescr(compactDescr=veh_cds[invID])
                        if not hasattr(self.descriptor, 'camouflage'):
                            self.descriptor.camouflage = None
                        if not hasattr(self.descriptor, 'hornID'):
                            self.descriptor.hornID = 0
                        self.level = self.descriptor.level
                        self.crew = [(tid, idx) for idx, tid in enumerate(crew_map[invID])]
                        self.health = 100
                        self.modelState = 'undamaged'
                        self.lock = 0
                        self.isElite = True
                        self.tags = self.descriptor.type.tags
                        self.shells = list(_vehicle_shells.get(invID, []))
                        self.equipments = list(_vehicle_equipments.get(invID, [0, 0, 0]))
                        self.repairCost = 0
                        if not hasattr(self.descriptor, 'camouflage'):
                            self.descriptor.camouflage = None
                        if not hasattr(self.descriptor, 'camouflages'):
                            self.descriptor.camouflages = [
                             None, None, None]
                        if not hasattr(self.descriptor, 'playerEmblems'):
                            self.descriptor.playerEmblems = [
                             None, None, None, None]
                        if not hasattr(self.descriptor, 'playerInscriptions'):
                            self.descriptor.playerInscriptions = [
                             None, None, None, None]
                        return

                    def __getattr__(self, n):
                        return

                def _offline_cv_get_vehicle(self):
                    return getattr(self, '_CurrentVehicle__vehicle', None)

                def _offline_cv_set_vehicle(self, v):
                    self._CurrentVehicle__vehicle = v
                    return

                CurrentVehicle._CurrentVehicle.vehicle = property(_offline_cv_get_vehicle, _offline_cv_set_vehicle)

                def _offline_cv_update(self):
                    try:
                        from helpers import dependency as _dep_upd
                        from skeletons.gui.shared import IItemsCache as _IIC_upd
                        _gic_upd = _dep_upd.instance(_IIC_upd)
                        _gic_upd._onResync('offline_module_update')
                    except Exception as _sync_e:
                        print '[OFFLINE] _onResync from update() failed: %s' % _sync_e
                        try:
                            from helpers import dependency as _dep_upd2
                            from skeletons.gui.shared import IItemsCache as _IIC_upd2
                            _dep_upd2.instance(_IIC_upd2).items.invalidateCache()
                        except Exception:
                            pass

                    self.onChanged()
                    try:
                        self.refreshModel()
                    except Exception as _rm_e:
                        print '[OFFLINE] refreshModel error: %s' % _rm_e

                    return

                CurrentVehicle._CurrentVehicle.update = _offline_cv_update

                def call_smart(cb, data):
                    if not cb:
                        return
                    import inspect
                    try:
                        argspec = inspect.getargspec(cb)
                        count = len(argspec.args)
                        if hasattr(cb, 'im_self'):
                            count -= 1
                        if count == 2:
                            BigWorld.callback(0, (lambda : cb(0, data)))
                        elif count == 1:
                            BigWorld.callback(0, (lambda : cb(data)))
                        else:
                            BigWorld.callback(0, (lambda : cb(0, data, None)))
                    except:
                        try:
                            BigWorld.callback(0, (lambda : cb(0, data)))
                        except:
                            pass

                    return

                def _refresh_ir_cache(vehicleInvID, new_cd):
                    try:
                        import BigWorld as _bwR
                        from skeletons.gui.shared import IItemsCache as _IICR
                        _icR = dependency.instance(_IICR)
                        from items import vehicles as _vmR
                        from collections import defaultdict as _ddR
                        _p_r = _bwR.player()
                        if _p_r is None:
                            return
                        _p_r.inventory._Inventory__cache.setdefault(1, {}).setdefault('compDescr', {})[vehicleInvID] = new_cd
                        _ir = _icR._ItemsCache__items.inventory
                        _ir_cache = _ir._data
                        if not isinstance(_ir_cache, dict):
                            _ir_cache = {}
                        _ir_cache.setdefault(1, {}).setdefault('compDescr', {})[vehicleInvID] = new_cd
                        _ir._data = _ir_cache
                        try:
                            _icd = _vmR.makeIntCompactDescrByID('vehicle', *_vmR.parseVehicleCompactDescr(new_cd))
                            _cdByID = dict(_ir._InventoryRequester__vehsCDsByID)
                            _cdByID[vehicleInvID] = _icd
                            _ir._InventoryRequester__vehsCDsByID = _cdByID
                            _ir._InventoryRequester__vehsIDsByCD = dict((v, k) for k, v in _cdByID.iteritems())
                        except Exception as _e3:
                            pass

                        _ir._InventoryRequester__itemsCache = _ddR(dict)
                        try:
                            _new_descr_sync = _vmR.VehicleDescr(compactDescr=new_cd)
                            _roles_len = len(_new_descr_sync.type.crewRoles)
                            _cur_crew = crew_map.get(vehicleInvID, [])
                            if len(_cur_crew) != _roles_len:
                                _synced_crew = list(_cur_crew[:_roles_len])
                                for _extra_tid in _cur_crew[_roles_len:]:
                                    if _extra_tid is not None:
                                        t_in_veh.pop(_extra_tid, None)

                                while len(_synced_crew) < _roles_len:
                                    _synced_crew.append(None)

                                crew_map[vehicleInvID] = _synced_crew
                                _p_r.inventory._Inventory__cache.setdefault(1, {}).setdefault('crew', {})[vehicleInvID] = _synced_crew
                                _ir_cache.setdefault(1, {}).setdefault('crew', {})[vehicleInvID] = _synced_crew
                                print '[OFFLINE] crew resynced for invID=%d: %d -> %d slots' % (
                                 vehicleInvID, len(_cur_crew), _roles_len)
                        except Exception as _ce:
                            print '[OFFLINE] crew resync error: %s' % _ce

                    except Exception as _re:
                        print '[OFFLINE] _refresh_ir_cache error: %s' % _re

                    return

                def _sync_ammo_to_ir_cache(vehicleInvID, shells=None, eqs=None):
                    try:
                        from skeletons.gui.shared import IItemsCache as _IICsync
                        _icA = dependency.instance(_IICsync)
                        _irA = _icA._ItemsCache__items.inventory
                        _ir_cacheA = _irA._data
                        if not isinstance(_ir_cacheA, dict):
                            _ir_cacheA = {}
                        _vehBucketA = _ir_cacheA.setdefault(1, {})
                        if shells is not None:
                            _vehBucketA.setdefault('shells', {})[vehicleInvID] = list(shells)
                        if eqs is not None:
                            _eqsA = list(eqs)[:3]
                            while len(_eqsA) < 3:
                                _eqsA.append(0)

                            _vehBucketA.setdefault('eqs', {})[vehicleInvID] = _eqsA
                            _vehBucketA.setdefault('eqsLayout', {})[vehicleInvID] = list(_eqsA)
                        _irA._data = _ir_cacheA
                        try:
                            from collections import defaultdict as _ddSA
                            _irA._InventoryRequester__itemsCache = _ddSA(dict)
                        except Exception as _isae:
                            print '[OFFLINE] _sync_ammo_to_ir_cache: itemsCache reset error: %s' % _isae

                        _icA.items.invalidateCache()
                        from CurrentVehicle import g_currentVehicle as _gcvA
                        if _gcvA.invID == vehicleInvID:
                            BigWorld.callback(0.05, _gcvA.onChanged)
                        print '[OFFLINE] _sync_ammo_to_ir_cache OK: invID=%d shells=%r eqs=%r' % (
                         vehicleInvID, shells, eqs)
                    except Exception as _sae:
                        print '[OFFLINE] _sync_ammo_to_ir_cache error: %s' % _sae
                        import traceback as _tbsae
                        _tbsae.print_exc()

                    return

                def makeMock(obj_name, method_name):

                    def _force_cb_inner(self, *args, **kwargs):
                        cb = next((a for a in args if callable(a)), kwargs.get('callback'))
                        req = args[0] if len(args) > 0 else None
                        res = 0
                        if method_name == 'getCache':
                            if obj_name == 'stats':
                                res = off_stats
                            elif obj_name == 'inventory':
                                inv_cache = {1: {'compDescr': inv_vehicles, 
                                       'crew': crew_map, 
                                       'customizations': (dict((invID, 0) for invID in my_garage)), 
                                       'shells': (dict((invID, list(_vehicle_shells.get(invID, []))) for invID in my_garage)), 
                                       'shellsLayout': (dict((invID, {}) for invID in my_garage)), 
                                       'repair': (dict((invID, (0, 100)) for invID in my_garage)), 
                                       'eqs': (dict((invID, list(_vehicle_equipments.get(invID, [0, 0, 0]))) for invID in my_garage)), 
                                       'eqsLayout': (dict((invID, list(_vehicle_equipments.get(invID, [0, 0, 0]))) for invID in my_garage)), 
                                       'settings': (dict((invID, 0) for invID in my_garage)), 
                                       'lock': (dict((invID, (0, 0)) for invID in my_garage))}, 
                                   8: {'compDescr': t_cache, 'vehicle': t_in_veh}, 12: {1: {}, 2: _vehicle_outfits, 3: {}}}
                                inv_cache.update(_modules_by_type)
                                res = inv_cache
                            elif obj_name == 'shop':
                                _shop_data_gc = _build_shop_items_flat()
                                res = {'items': _shop_data_gc, 
                                   'rev': 0, 
                                   'paidRemovalCost': (_shop_data_gc.get('paidRemovalCost', {'gold': 10})), 
                                   'exchangeRate': (_shop_data_gc.get('exchangeRate', 400)), 
                                   'exchangeRateForShellsAndEqs': 400, 
                                   'sellPriceModif': 0.5, 
                                   'slotsPrices': [
                                                 300, [300, 600, 900]], 
                                   'berthsPrices': (
                                                  300, 0, [300, 600, 900]), 
                                   'dropSkillsCost': {0: {'credits': 10, 'gold': 10, 'xpReuseFraction': 0.5}, 1: {'credits': 20000, 'gold': 0, 'xpReuseFraction': 0.5}, 2: {'credits': 0, 'gold': 200, 'xpReuseFraction': 0.5}}, 'dailyXPFactor': (_shop_data_gc.get('dailyXPFactor', 1)), 
                                   'isEnabledBuyingGoldShellsForCredits': True, 
                                   'isEnabledBuyingGoldEqsForCredits': True, 
                                   'tankmanCost': (_shop_data_gc.get('tankmanCost', ({'credits': 0, 'gold': 0}, {'credits': 20000, 'gold': 0}, {'credits': 0, 'gold': 200}))), 
                                   'refSystem': {'posByXPinTeam': 0}, 'crystalExchangeRate': (_shop_data_gc.get('crystalExchangeRate', 25))}
                            else:
                                res = {}
                        elif method_name in ('get', 'request'):
                            if obj_name == 'stats':
                                _int_keys = set(['credits', 'gold', 'freeXP', 'slots', 'berths', 'rev',
                                 'premiumExpiryTime',
                                 'attrs', 'currentVehInvID', 'restrictions',
                                 'tutorialsCompleted'])
                                res = off_stats.get(req, 0 if req in _int_keys else {} if isinstance(req, str) else 0)
                            else:
                                res = off_stats.get(req, {})
                        elif obj_name == 'shop' and method_name == 'getVehiclesSellPrices':
                            vehList = args[0] if args else []
                            res = [(1000, 0)] * len(vehList)
                        elif obj_name == 'shop' and method_name == 'getSellPriceModifiers':
                            res = (0, 400, 0.5)
                        elif obj_name == 'inventory' and method_name == 'sell':
                            sell_type_idx = args[0] if len(args) > 0 else None
                            sell_item_id = args[1] if len(args) > 1 else None
                            sell_count = args[2] if len(args) > 2 else 1
                            try:
                                _bucket = _modules_by_type.get(sell_type_idx)
                                if _bucket is not None and sell_item_id in _bucket:
                                    _new_cnt = max(0, _bucket[sell_item_id] - sell_count)
                                    if _new_cnt > 0:
                                        _bucket[sell_item_id] = _new_cnt
                                    else:
                                        _bucket.pop(sell_item_id, None)
                                        _modules_inventory.pop(sell_item_id, None)
                                    _cache_bucket = p.inventory._Inventory__cache.setdefault(sell_type_idx, {})
                                    if _new_cnt > 0:
                                        _cache_bucket[sell_item_id] = _new_cnt
                                    else:
                                        _cache_bucket.pop(sell_item_id, None)
                                    print '[OFFLINE] sellItem OK: type=%s id=%r count=%s' % (sell_type_idx, sell_item_id, sell_count)
                                else:
                                    print '[OFFLINE] sellItem: item not in inventory type=%s id=%r' % (sell_type_idx, sell_item_id)
                                from PlayerEvents import g_playerEvents as _pe_sell
                                BigWorld.callback(0.05, (lambda _pe=_pe_sell: _pe.onClientUpdated({'inventory': {}}, False)))
                            except Exception as _e_sell:
                                print '[OFFLINE] sellItem error: %s' % _e_sell

                            res = 0
                        elif method_name == 'getItems':
                            if obj_name == 'inventory':
                                if req == 1:
                                    res = {'compDescr': inv_vehicles, 'crew': crew_map, 
                                       'shells': (dict((invID, list(_vehicle_shells.get(invID, []))) for invID in my_garage)), 
                                       'shellsLayout': (dict((invID, {}) for invID in my_garage)), 
                                       'repair': (dict((invID, (0, 100)) for invID in my_garage)), 
                                       'eqs': (dict((invID, list(_vehicle_equipments.get(invID, [0, 0, 0]))) for invID in my_garage)), 
                                       'eqsLayout': (dict((invID, list(_vehicle_equipments.get(invID, [0, 0, 0]))) for invID in my_garage)), 
                                       'settings': (dict((invID, 0) for invID in my_garage)), 
                                       'lock': (dict((invID, (0, 0)) for invID in my_garage))}
                                    print '[OFFLINE] inventory.getItems(vehicle) called: %d vehicles in response' % len(inv_vehicles)
                                elif req == 8:
                                    res = {'compDescr': t_cache, 'vehicle': t_in_veh}
                                elif req in _modules_by_type:
                                    res = _modules_by_type[req]
                                else:
                                    res = {}
                            elif obj_name == 'shop':
                                nationID = args[1] if len(args) > 1 else None
                                prices = _build_shop_prices()
                                import nations as _nat
                                from items import ITEM_TYPE_INDICES as _ITI2
                                if req in (_ITI2['optionalDevice'], _ITI2['equipment']):
                                    nation_data = prices.get(_nat.NONE_INDEX, {})
                                else:
                                    nation_data = prices.get(nationID, {})
                                if req in nation_data:
                                    res = nation_data[req]
                                else:
                                    res = ({}, set())
                        elif method_name == 'setCurrentVehicle':
                            invID = args[0] if args else None
                            if invID is not None and invID in veh_cds:
                                import CurrentVehicle
                                CurrentVehicle.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(invID)
                                off_stats['currentVehInvID'] = invID
                                BigWorld.callback(0.05, CurrentVehicle.g_currentVehicle.onChanged)
                            res = 0
                        elif obj_name == 'shop':
                            if method_name == 'buyTankman':
                                buy_cb = args[4] if len(args) > 4 else cb
                                new_inv_id = max(t_cache.keys()) + 1 if t_cache else 100
                                nationID_arg = args[0] if len(args) > 0 else 0
                                vehTypeID_arg = args[1] if len(args) > 1 else 0
                                role_arg = args[2] if len(args) > 2 else 'loader'
                                try:
                                    from items import tankmen as tm_module
                                    passport = tm_module.generatePassport(nationID_arg)
                                    new_cd = tm_module.generateCompactDescr(passport, vehTypeID_arg, role_arg, 100)
                                    t_cache[new_inv_id] = new_cd
                                    if buy_cb:
                                        _id = new_inv_id
                                        BigWorld.callback(0.01, (lambda _i=_id: buy_cb(0, _i, t_cache[_i])))
                                except Exception as e:
                                    print '[OFFLINE] buyTankman error: %s' % e
                                    if buy_cb:
                                        BigWorld.callback(0.01, (lambda : buy_cb(-1, 0, '')))

                                return
                            if method_name == 'getBerthsPrices':
                                res = (
                                 300, [300, 600, 900])
                            elif method_name == 'getTankmanCost':
                                res = ({'credits': 0, 'gold': 0}, {'credits': 20000, 'gold': 0}, {'credits': 0, 'gold': 200})
                            elif method_name == 'getSlotsPrices':
                                res = (
                                 300, [300, 600, 900])
                            elif method_name == 'getExchangeRate':
                                res = (400, 1)
                            if method_name == 'getFreeXPConversion':
                                res = (5, 1)
                            elif method_name == 'getPremiumCost':
                                res = {1: 250, 3: 650, 7: 1200}
                            else:
                                if method_name == 'getCamouflageCost':
                                    _cb = next((a for a in args if callable(a)), kwargs.get('callback'))
                                    _costs = {7: (400, 1), 30: (1200, 1), 0: (3000, 1)}
                                    if _cb:
                                        BigWorld.callback(0, (lambda cb=_cb, c=_costs: cb(0, c, 1)))
                                    return
                                if method_name == 'getPlayerEmblemCost':
                                    _cb = next((a for a in args if callable(a)), kwargs.get('callback'))
                                    _costs = {7: (200, 1), 30: (500, 1), 0: (1500, 1)}
                                    if _cb:
                                        BigWorld.callback(0, (lambda cb=_cb, c=_costs: cb(0, c, 1)))
                                    return
                                if method_name == 'getPlayerInscriptionCost':
                                    _cb = next((a for a in args if callable(a)), kwargs.get('callback'))
                                    _costs = {7: (200, 1), 30: (500, 1), 0: (1500, 1)}
                                    if _cb:
                                        BigWorld.callback(0, (lambda cb=_cb, c=_costs: cb(0, c, 1)))
                                    return
                                if method_name == 'getHornCost':
                                    _cb = next((a for a in args if callable(a)), kwargs.get('callback'))
                                    from items import vehicles as _veh_h
                                    try:
                                        _horns = _veh_h.g_cache.horns()
                                        _costs = dict((hid, 100) for hid in _horns) if _horns else {}
                                    except:
                                        _costs = {}

                                    if _cb:
                                        BigWorld.callback(0, (lambda cb=_cb, c=_costs: cb(0, c, 1)))
                                    return
                                if method_name == 'getHornCost':
                                    from items import vehicles as _veh_h
                                    try:
                                        _horns = _veh_h.g_cache.horns()
                                        res = dict((hid, 100) for hid in _horns) if _horns else {}
                                    except:
                                        res = {}

                            if obj_name == 'shop' and method_name == 'getDailyXPFactor':
                                res = (1, 1)
                            elif method_name == 'getDropSkillCost':
                                res = {0: {'credits': 10, 'gold': 10, 'xpReuseFraction': 0.5}, 1: {'credits': 20000, 'gold': 0, 'xpReuseFraction': 0.5}, 2: {'credits': 0, 'gold': 200, 'xpReuseFraction': 0.5}}
                            elif obj_name == 'shop' and method_name == 'getGoldPackets':
                                res = {}
                            if obj_name == 'shop' and method_name == 'getAmmoSellPrice':
                                res = 0
                            elif method_name == 'getPrice':
                                res = (0, 0)
                            elif obj_name == 'inventory' and method_name == 'equipEquipments':
                                res = 0
                        elif obj_name == 'inventory' and method_name == 'setAndFillLayouts':
                            vehicleInvID_sfl = args[0] if len(args) > 0 else None
                            shellsLayout_sfl = args[1] if len(args) > 1 else []
                            eqsLayout_sfl = args[2] if len(args) > 2 else []
                            sfl_cb = args[4] if len(args) > 4 else cb
                            try:
                                if shellsLayout_sfl and vehicleInvID_sfl in veh_cds:
                                    shells_list = []
                                    layout = list(shellsLayout_sfl)
                                    while len(layout) >= 2:
                                        cd_s = layout.pop(0)
                                        cnt = layout.pop(0)
                                        if cd_s and cd_s != -1:
                                            shells_list.append(cd_s)
                                            shells_list.append(cnt)

                                    _vehicle_shells[vehicleInvID_sfl] = shells_list
                                if eqsLayout_sfl and vehicleInvID_sfl in veh_cds:
                                    eq_list = []
                                    layout2 = list(eqsLayout_sfl)
                                    while len(layout2) >= 2:
                                        cd_e = layout2.pop(0)
                                        cnt2 = layout2.pop(0)
                                        eq_list.append(cd_e if cd_e and cd_e != -1 else 0)

                                    while len(eq_list) < 3:
                                        eq_list.append(0)

                                    _vehicle_equipments[vehicleInvID_sfl] = eq_list[:3]
                                _sync_ammo_to_ir_cache(vehicleInvID_sfl, shells=_vehicle_shells.get(vehicleInvID_sfl), eqs=_vehicle_equipments.get(vehicleInvID_sfl))
                                from CurrentVehicle import g_currentVehicle as _gcv2
                                veh2 = _gcv2.vehicle
                                if veh2 and veh2.inventoryId == vehicleInvID_sfl:
                                    BigWorld.callback(0.05, _gcv2.onChanged)
                                print '[OFFLINE] setAndFillLayouts OK: veh=%s shells=%r eqs=%r' % (
                                 vehicleInvID_sfl,
                                 _vehicle_shells.get(vehicleInvID_sfl),
                                 _vehicle_equipments.get(vehicleInvID_sfl))
                            except Exception as _e_sfl:
                                print '[OFFLINE] setAndFillLayouts error: %s' % _e_sfl

                            if sfl_cb:
                                BigWorld.callback(0.01, (lambda _c=sfl_cb: _c(0, '', {})))
                            return
                        if obj_name == 'shop' and method_name == 'getAllItems':
                            if cb:
                                _cb = cb
                                _data = _build_shop_items_flat()
                                print '[OFFLINE] shop.getAllItems called: %d itemPrices returned' % len(_data.get('itemPrices', {}))
                                BigWorld.callback(0, (lambda cb2=_cb, d=_data: cb2(0, d, 0)))
                        elif obj_name == 'shop' and method_name == 'getItems':
                            if cb:
                                _cb = cb
                                _res = res
                                BigWorld.callback(0, (lambda cb2=_cb, r=_res: cb2(0, r, 0)))
                        else:
                            call_smart(cb, res)
                        return

                    def force_cb(self, *args, **kwargs):
                        try:
                            return _force_cb_inner(self, *args, **kwargs)
                        except Exception as _fce:
                            import traceback as _tbfce
                            print '[OFFLINE] force_cb CRASH obj=%s method=%s args=%r kwargs=%r: %s' % (
                             obj_name, method_name, args, kwargs, _fce)
                            _tbfce.print_exc()
                            _cb_fce = next((a for a in args if callable(a)), kwargs.get('callback'))
                            if _cb_fce:
                                try:
                                    BigWorld.callback(0, (lambda _c=_cb_fce: _c(0, {}, 0)))
                                except Exception:
                                    try:
                                        BigWorld.callback(0, (lambda _c=_cb_fce: _c({}, 0)))
                                    except Exception:
                                        pass

                        return

                    return force_cb

                mocked_methods = [
             'request', 'get', 'getItems', 'getCache', 'getAllItems', 'setCurrentVehicle',
             'changeVehicleSetting', 'respecTankman', 'buyTankman', 'dropTankmanSkill', 'getVehicleSellPrice', 'getComponentSellPrice',
             'buy', 'sell', 'repair', 'exchange', 'convertToFreeXP', 'upgradeToPremium',
             'buySlot', 'buyBerths', 'getBerthsPrices', 'getTankmanCost', 'getSlotsPrices', 'getExchangeRate',
             'getFreeXPConversion', 'getPremiumCost', 'getSellPriceModifiers', 'getPaidRemovalCost', 'getTradeFees', 'getVehiclesSellPrices',
             'getPassportChangeCost', 'getPrice', 'getBerthsPrices', 'getNextBerthPackPrice', 'getCamouflageCost', 'getHornCost',
             'getPlayerEmblemCost', 'getPlayerInscriptionCost', 'changeVehicleCamouflage', 'changeVehicleHorn', 'getDailyXPFactor', 'getDropSkillCost',
             'getGoldPackets', 'getAmmoSellPrice', 'equipEquipments', 'setAndFillLayouts',]
                for s in ['inventory', 'stats', 'shop', 'dossierCache']:
                    obj = getattr(p, s, None)
                    if obj:
                        for m in mocked_methods:
                            setattr(obj, m, new.instancemethod(makeMock(s, m), obj, obj.__class__))

                        if s == 'shop':

                            def get_next_berth_price(berths, berths_prices):
                                return 300

                            obj.getNextBerthPackPrice = get_next_berth_price

                            def get_next_slot_price(slots_count, slots_prices):
                                return 300

                            obj.getNextSlotPrice = get_next_slot_price

                inv_obj = getattr(p, 'inventory', None)
                if inv_obj:

                    def fake_equip(self, vehicleInvID, itemCompDescr, callback=None):
                        try:
                            from items import vehicles as _vmod
                            _base_cd = veh_cds.get(vehicleInvID)
                            if _base_cd is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr = _vmod.VehicleDescr(compactDescr=_base_cd)
                            _descr.installComponent(itemCompDescr)
                            new_cd = _descr.makeCompactDescr()
                            veh_cds[vehicleInvID] = new_cd
                            inv_vehicles[vehicleInvID] = new_cd
                            _refresh_ir_cache(vehicleInvID, new_cd)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            _veh_w = _gcv.vehicle
                            if _veh_w and _veh_w.inventoryId == vehicleInvID:
                                _gcv._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            BigWorld.callback(0.05, (lambda : _gcv.update()))
                            print '[OFFLINE] equip OK: invID=%d cd=%d' % (vehicleInvID, itemCompDescr)
                        except Exception as e:
                            print '[OFFLINE] equip error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, {})))
                        return

                    def fake_addTankmanSkill(self, tmanInvID, skillName, callback=None):
                        try:
                            from items import tankmen as _tm
                            cd = t_cache.get(tmanInvID)
                            if cd:
                                descr = _tm.TankmanDescr(cd)
                                try:
                                    if skillName not in _tm.SKILLS_BY_ROLES.get(descr.role, set()):
                                        if skillName not in _tm.COMMON_SKILLS and skillName != 'brotherhood':
                                            raise ValueError('Wrong skill for role')
                                    if descr.roleLevel < _tm.MAX_SKILL_LEVEL:
                                        descr.roleLevel = _tm.MAX_SKILL_LEVEL
                                    if descr.skills and descr.lastSkillLevel < _tm.MAX_SKILL_LEVEL:
                                        descr.lastSkillLevel = _tm.MAX_SKILL_LEVEL
                                    descr.addSkill(skillName)
                                    t_cache[tmanInvID] = descr.makeCompactDescr()
                                    print '[OFFLINE] addTankmanSkill: tmanID=%d skill=%s OK' % (tmanInvID, skillName)
                                except ValueError as e:
                                    print '[OFFLINE] addTankmanSkill skip: %s' % e

                        except Exception as e:
                            print '[OFFLINE] addTankmanSkill error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        return

                    inv_obj.addTankmanSkill = new.instancemethod(fake_addTankmanSkill, inv_obj, inv_obj.__class__)

                    def fake_equipTankman(self, vehicleInvID, slotIdx, tmanInvID, callback=None):
                        try:
                            old_crew = list(crew_map.get(vehicleInvID, []))
                            if 0 <= slotIdx < len(old_crew):
                                old_tman = old_crew[slotIdx]
                                if old_tman is not None:
                                    t_in_veh.pop(old_tman, None)
                            if tmanInvID != 0 and tmanInvID in t_cache:
                                old_veh = t_in_veh.get(tmanInvID)
                                if old_veh is not None and old_veh != vehicleInvID:
                                    other_crew = crew_map.get(old_veh, [])
                                    for i2, tid2 in enumerate(other_crew):
                                        if tid2 == tmanInvID:
                                            other_crew[i2] = None

                                t_in_veh[tmanInvID] = vehicleInvID
                                if slotIdx < len(old_crew):
                                    old_crew[slotIdx] = tmanInvID
                                else:
                                    old_crew.append(tmanInvID)
                            elif 0 <= slotIdx < len(old_crew):
                                old_crew[slotIdx] = None
                            crew_map[vehicleInvID] = old_crew
                            from CurrentVehicle import g_currentVehicle
                            veh = g_currentVehicle.vehicle
                            if veh and veh.inventoryId == vehicleInvID:
                                g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            print '[OFFLINE] equipTankman: slot=%d tman=%d veh=%d' % (slotIdx, tmanInvID, vehicleInvID)
                        except Exception as e:
                            print '[OFFLINE] equipTankman error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        return

                    inv_obj.equipTankman = new.instancemethod(fake_equipTankman, inv_obj, inv_obj.__class__)

                    def fake_equipTurret(self, vehicleInvID, itemCompDescr, slotIdx, callback=None):
                        try:
                            from items import vehicles as _vmodTr
                            _base_cd_tr = veh_cds.get(vehicleInvID)
                            if _base_cd_tr is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_tr = _vmodTr.VehicleDescr(compactDescr=_base_cd_tr)
                            _descr_tr.installTurret(itemCompDescr, slotIdx)
                            new_cd = _descr_tr.makeCompactDescr()
                            veh_cds[vehicleInvID] = new_cd
                            inv_vehicles[vehicleInvID] = new_cd
                            _refresh_ir_cache(vehicleInvID, new_cd)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            _veh_w_tr = _gcv.vehicle
                            if _veh_w_tr and _veh_w_tr.inventoryId == vehicleInvID:
                                _gcv._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            BigWorld.callback(0.05, (lambda : _gcv.update()))
                            print '[OFFLINE] equipTurret OK: invID=%d cd=%d slot=%d' % (vehicleInvID, itemCompDescr, slotIdx)
                        except Exception as e:
                            print '[OFFLINE] equipTurret error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, {})))
                        return

                    inv_obj.equip = new.instancemethod(fake_equip, inv_obj, inv_obj.__class__)
                    inv_obj.equipTurret = new.instancemethod(fake_equipTurret, inv_obj, inv_obj.__class__)

                    def fake_equipOptionalDevice(self, vehicleInvID, itemCompDescr, slotIdx, isUseGold=False, callback=None):
                        try:
                            from items import vehicles as _vmodOD
                            _base_cd_od = veh_cds.get(vehicleInvID)
                            if _base_cd_od is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_od = _vmodOD.VehicleDescr(compactDescr=_base_cd_od)
                            if itemCompDescr == 0:
                                _descr_od.removeOptionalDevice(slotIdx)
                                print '[OFFLINE] Removed device from slot %d' % slotIdx
                            else:
                                _descr_od.installOptionalDevice(itemCompDescr, slotIdx)
                                print '[OFFLINE] Equipped device %d in slot %d' % (itemCompDescr, slotIdx)
                            new_cd = _descr_od.makeCompactDescr()
                            veh_cds[vehicleInvID] = new_cd
                            inv_vehicles[vehicleInvID] = new_cd
                            _refresh_ir_cache(vehicleInvID, new_cd)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            _veh_w_od = _gcv.vehicle
                            if _veh_w_od and _veh_w_od.inventoryId == vehicleInvID:
                                _gcv._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            BigWorld.callback(0.05, (lambda : _gcv.update()))
                        except Exception as e:
                            print '[OFFLINE] equipOptionalDevice error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        return

                    inv_obj.equipOptionalDevice = new.instancemethod(fake_equipOptionalDevice, inv_obj, inv_obj.__class__)

                    def fake_equipEquipments(self, vehicleInvID, equipments, callback=None):
                        try:
                            _vehicle_equipments[vehicleInvID] = list(equipments)
                            print '[OFFLINE] Equipping items %r on vehicle %d' % (equipments, vehicleInvID)
                            _sync_ammo_to_ir_cache(vehicleInvID, eqs=equipments)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            veh = _gcv.vehicle
                            if veh is not None and getattr(veh, 'inventoryId', None) == vehicleInvID:
                                veh.equipments = list(equipments)
                            BigWorld.callback(0.1, (lambda : _gcv.update()))
                            if callback:
                                BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        except Exception as e:
                            print '[OFFLINE] equipEquipments error: %s' % e

                        return

                    inv_obj.equipEquipments = new.instancemethod(fake_equipEquipments, inv_obj, inv_obj.__class__)
                    p.stats.setEquipments = lambda vehInvId, eqs, cb: cb(0) if cb else None

                    def fake_equipShells(self, vehicleInvID, shells, callback=None):
                        try:
                            _vehicle_shells[vehicleInvID] = list(shells) if shells else []
                            _sync_ammo_to_ir_cache(vehicleInvID, shells=list(shells) if shells else [])
                            from CurrentVehicle import g_currentVehicle
                            veh = g_currentVehicle.vehicle
                            if veh is not None and getattr(veh, 'inventoryId', None) == vehicleInvID:
                                veh.shells = list(shells) if shells else []
                                print '[OFFLINE] equipShells OK: invID=%d' % vehicleInvID
                                from CurrentVehicle import g_currentVehicle as _gcv
                                BigWorld.callback(0.1, (lambda : _gcv.update()))
                        except Exception as e:
                            print '[OFFLINE] equipShells error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        return

                    inv_obj.equipShells = new.instancemethod(fake_equipShells, inv_obj, inv_obj.__class__)

                    def fake_changeVehicleCamouflage(self, vehicleInvID, camouflageKind, camouflageID, days, callback=None):
                        try:
                            from items import vehicles as _vmodCam
                            _base_cd_cam = veh_cds.get(vehicleInvID)
                            if _base_cd_cam is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_cam = _vmodCam.VehicleDescr(compactDescr=_base_cd_cam)
                            import time as _time
                            if camouflageID == 0:
                                _descr_cam.setCamouflage(camouflageKind, None, _time.time(), 0)
                            else:
                                _descr_cam.setCamouflage(camouflageKind, camouflageID, _time.time(), days)
                            new_cd = _descr_cam.makeCompactDescr()
                            veh_cds[vehicleInvID] = new_cd
                            inv_vehicles[vehicleInvID] = new_cd
                            _refresh_ir_cache(vehicleInvID, new_cd)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            _veh_w_cam = _gcv.vehicle
                            if _veh_w_cam and _veh_w_cam.inventoryId == vehicleInvID:
                                _gcv._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            BigWorld.callback(0.1, (lambda : _gcv.update()))
                            print '[OFFLINE] changeVehicleCamouflage: kind=%s camouflageID=%s days=%s' % (camouflageKind, camouflageID, days)
                        except Exception as e:
                            print '[OFFLINE] changeVehicleCamouflage error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: cb(0)))
                        return

                    inv_obj.changeVehicleCamouflage = new.instancemethod(fake_changeVehicleCamouflage, inv_obj, inv_obj.__class__)

                    def fake_changeVehicleEmblem(self, vehicleInvID, position, emblemID, days, callback=None):
                        try:
                            from items import vehicles as _vmodEmb
                            _base_cd_emb = veh_cds.get(vehicleInvID)
                            if _base_cd_emb is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_emb = _vmodEmb.VehicleDescr(compactDescr=_base_cd_emb)
                            import time as _time
                            if emblemID == 0:
                                _descr_emb.setPlayerEmblem(position, None, _time.time(), 0)
                            else:
                                _descr_emb.setPlayerEmblem(position, emblemID, _time.time(), days)
                            new_cd = _descr_emb.makeCompactDescr()
                            veh_cds[vehicleInvID] = new_cd
                            inv_vehicles[vehicleInvID] = new_cd
                            _refresh_ir_cache(vehicleInvID, new_cd)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            _veh_w_emb = _gcv.vehicle
                            if _veh_w_emb and _veh_w_emb.inventoryId == vehicleInvID:
                                _gcv._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            BigWorld.callback(0.1, (lambda : _gcv.update()))
                            print '[OFFLINE] changeVehicleEmblem: pos=%s emblemID=%s days=%s' % (position, emblemID, days)
                        except Exception as e:
                            print '[OFFLINE] changeVehicleEmblem error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: cb(0)))
                        return

                    inv_obj.changeVehicleEmblem = new.instancemethod(fake_changeVehicleEmblem, inv_obj, inv_obj.__class__)

                    def fake_changeVehicleInscription(self, vehicleInvID, position, inscriptionID, days, colorID, callback=None):
                        try:
                            from items import vehicles as _vmodIns
                            _base_cd_ins = veh_cds.get(vehicleInvID)
                            if _base_cd_ins is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_ins = _vmodIns.VehicleDescr(compactDescr=_base_cd_ins)
                            import time as _time
                            if inscriptionID == 0:
                                _descr_ins.setPlayerInscription(position, None, _time.time(), 0, 0)
                            else:
                                _descr_ins.setPlayerInscription(position, inscriptionID, _time.time(), days, colorID)
                            new_cd = _descr_ins.makeCompactDescr()
                            veh_cds[vehicleInvID] = new_cd
                            inv_vehicles[vehicleInvID] = new_cd
                            _refresh_ir_cache(vehicleInvID, new_cd)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            _veh_w_ins = _gcv.vehicle
                            if _veh_w_ins and _veh_w_ins.inventoryId == vehicleInvID:
                                _gcv._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            BigWorld.callback(0.1, (lambda : _gcv.update()))
                            print '[OFFLINE] changeVehicleInscription: pos=%s inscriptionID=%s days=%s color=%s' % (position, inscriptionID, days, colorID)
                        except Exception as e:
                            print '[OFFLINE] changeVehicleInscription error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: cb(0)))
                        return

                    inv_obj.changeVehicleInscription = new.instancemethod(fake_changeVehicleInscription, inv_obj, inv_obj.__class__)

                    def fake_changeVehicleHorn(self, vehicleInvID, hornID, callback=None):
                        try:
                            from items import vehicles as _vmodHrn
                            _base_cd_hrn = veh_cds.get(vehicleInvID)
                            if _base_cd_hrn is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_hrn = _vmodHrn.VehicleDescr(compactDescr=_base_cd_hrn)
                            _descr_hrn.hornID = hornID if hornID else 0
                            new_cd = _descr_hrn.makeCompactDescr()
                            veh_cds[vehicleInvID] = new_cd
                            inv_vehicles[vehicleInvID] = new_cd
                            _refresh_ir_cache(vehicleInvID, new_cd)
                            from CurrentVehicle import g_currentVehicle as _gcv
                            _veh_w_hrn = _gcv.vehicle
                            if _veh_w_hrn and _veh_w_hrn.inventoryId == vehicleInvID:
                                _gcv._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                            BigWorld.callback(0.01, _gcv.onChanged)
                            print '[OFFLINE] changeVehicleHorn: hornID=%s' % hornID
                        except Exception as e:
                            print '[OFFLINE] changeVehicleHorn error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        return

                    inv_obj.changeVehicleHorn = new.instancemethod(fake_changeVehicleHorn, inv_obj, inv_obj.__class__)

                    def fake_dismissTankman(self, tmanInvID, callback=None):
                        try:
                            t_cache.pop(tmanInvID, None)
                            t_in_veh.pop(tmanInvID, None)
                            for _vid in list(crew_map.keys()):
                                _c = crew_map[_vid]
                                if tmanInvID in _c:
                                    _c[_c.index(tmanInvID)] = None

                            print '[OFFLINE] dismissTankman: tmanID=%d OK' % tmanInvID
                        except Exception as e:
                            print '[OFFLINE] dismissTankman error: %s' % e

                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        return

                    inv_obj.dismissTankman = new.instancemethod(fake_dismissTankman, inv_obj, inv_obj.__class__)
                    shop_obj = getattr(p, 'shop', None)
                    if shop_obj:

                        def fake_buyVehicle(self, nationIdx, innationIdx, buyShells, recruitCrew, tmanCostTypeIdx, rentPackage=-1, callback=None):
                            try:
                                new_cd = vehicles.VehicleDescr(typeID=(nationIdx, innationIdx)).makeCompactDescr()
                                new_inv_id = max(list(my_garage.keys()) + [0]) + 1
                                descr = vehicles.VehicleDescr(compactDescr=new_cd)
                                my_garage[new_inv_id] = descr.type.name
                                veh_cds[new_inv_id] = new_cd
                                inv_vehicles[new_inv_id] = new_cd
                                crew_ids = []
                                if recruitCrew:
                                    for role in descr.type.crewRoles:
                                        passport = tankmen.generatePassport(nationIdx)
                                        tman_cd = tankmen.generateCompactDescr(passport, innationIdx, role[0], 100)
                                        new_tman_id = max(list(t_cache.keys()) + [99]) + 1
                                        t_cache[new_tman_id] = tman_cd
                                        t_in_veh[new_tman_id] = new_inv_id
                                        crew_ids.append(new_tman_id)

                                crew_map[new_inv_id] = crew_ids
                                print '[OFFLINE] buyVehicle: nation=%s innation=%s invID=%d OK' % (nationIdx, innationIdx, new_inv_id)
                                from PlayerEvents import g_playerEvents

                                def _buy_resync(_pe=g_playerEvents, _invid=new_inv_id):
                                    import BigWorld as _bw2
                                    _p2 = _bw2.player()
                                    if _p2 is not None:
                                        try:
                                            _new_descr2 = vehicles.VehicleDescr(compactDescr=inv_vehicles[_invid])
                                            _def_ammo = _zero_ammo_for_gun(_new_descr2.gun)
                                        except Exception as _ae2:
                                            print '[OFFLINE] buyVehicle: default ammo error: %s' % _ae2
                                            _def_ammo = []

                                        _vehicle_shells[_invid] = _def_ammo
                                        _veh_bucket = _p2.inventory._Inventory__cache.setdefault(1, {})
                                        _veh_bucket.setdefault('compDescr', {})[_invid] = inv_vehicles[_invid]
                                        _veh_bucket.setdefault('crew', {})[_invid] = crew_map.get(_invid, [])
                                        _veh_bucket.setdefault('shells', {})[_invid] = list(_def_ammo)
                                        _veh_bucket.setdefault('shellsLayout', {})[_invid] = {}
                                        _veh_bucket.setdefault('repair', {})[_invid] = (0,
                                                                                        100)
                                        _veh_bucket.setdefault('eqs', {})[_invid] = [0, 0, 0]
                                        _veh_bucket.setdefault('eqsLayout', {})[_invid] = [0, 0, 0]
                                        _veh_bucket.setdefault('settings', {})[_invid] = 0
                                        _veh_bucket.setdefault('lock', {})[_invid] = (0,
                                                                                      0)
                                        _tm_bucket = _p2.inventory._Inventory__cache.setdefault(8, {})
                                        _tm_bucket['compDescr'] = t_cache
                                        _tm_bucket['vehicle'] = t_in_veh
                                    _pe.onClientUpdated({'inventory': {}, 'stats': {}}, False)
                                    _pe.onInventoryResync()
                                    BigWorld.callback(0.15, _pe.onShopResync)
                                    return

                                BigWorld.callback(0.05, _buy_resync)
                            except Exception as e:
                                print '[OFFLINE] buyVehicle error: %s' % e

                            if callback:
                                BigWorld.callback(0.01, (lambda cb=callback: cb(0)))
                            return

                        shop_obj.buyVehicle = new.instancemethod(fake_buyVehicle, shop_obj, shop_obj.__class__)

                    def fake_sellVehicle(self, vehicleInvID, *args, **kwargs):
                        _cb = None
                        for _a in list(args) + list(kwargs.values()):
                            if callable(_a):
                                _cb = _a
                                break

                        try:
                            veh_cds.pop(vehicleInvID, None)
                            inv_vehicles.pop(vehicleInvID, None)
                            my_garage.pop(vehicleInvID, None)
                            old_crew = crew_map.pop(vehicleInvID, None)
                            if old_crew:
                                for _tid in old_crew:
                                    if _tid is not None:
                                        t_in_veh.pop(_tid, None)

                            print '[OFFLINE] sellVehicle: invID=%d OK' % vehicleInvID
                            from PlayerEvents import g_playerEvents as _pe_sv

                            def _sell_resync(_pe=_pe_sv, _sold_id=vehicleInvID):
                                import BigWorld as _bw3
                                _p3 = _bw3.player()
                                if _p3 is not None:
                                    _vb = _p3.inventory._Inventory__cache.get(1, {})
                                    for _fld in ('compDescr', 'crew', 'shells', 'shellsLayout',
                                                 'repair', 'eqs', 'eqsLayout', 'settings',
                                                 'lock'):
                                        _vb.get(_fld, {}).pop(_sold_id, None)

                                    _p3.inventory._Inventory__cache.get(8, {}).update({'compDescr': t_cache, 'vehicle': t_in_veh})
                                _pe.onClientUpdated({'inventory': {}, 'stats': {}}, False)
                                _pe.onInventoryResync()
                                BigWorld.callback(0.15, _pe.onShopResync)
                                return

                            BigWorld.callback(0.1, _sell_resync)
                        except Exception as e:
                            print '[OFFLINE] sellVehicle error: %s' % e

                        if _cb:
                            BigWorld.callback(0.01, (lambda cb=_cb: cb(0)))
                        return

                    inv_obj.sellVehicle = new.instancemethod(fake_sellVehicle, inv_obj, inv_obj.__class__)
                    if shop_obj:

                        def fake_shopBuy(self, itemTypeID, nationID, intCD, count, goldForCredits, callback=None):
                            print '[OFFLINE] shop.buy: itemTypeID=%s nationID=%s intCD=%s count=%s goldForCredits=%s' % (
                             itemTypeID, nationID, intCD, count, goldForCredits)
                            if callback:
                                BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                            return

                        shop_obj.buy = new.instancemethod(fake_shopBuy, shop_obj, shop_obj.__class__)

                        def _veh_intcd(vehicleInvID):
                            """ InventoryRequester.__makeOutfit() looks outfits up by the vehicle's
                                intCD (compact descriptor int), NOT by inventory id - this converts. """
                            from items import vehicles as _vmod_ic
                            _raw_cd = veh_cds.get(vehicleInvID)
                            if _raw_cd is None:
                                return
                            else:
                                return _vmod_ic.makeIntCompactDescrByID('vehicle', *_vmod_ic.parseVehicleCompactDescr(_raw_cd))

                        def _refresh_after_outfit_change(vehicleInvID):
                            """ Re-syncs InventoryRequester/ItemsCache and forces the hangar vehicle
                                to be rebuilt so a freshly applied outfit (camo/paint/decal/emblem/
                                inscription/style) is actually reflected in the UI and 3D model. """
                            try:
                                from helpers import dependency as _dep_o
                                from skeletons.gui.shared import IItemsCache as _IIC_o
                                _ic_o = _dep_o.instance(_IIC_o)
                                _ir_o = _ic_o._ItemsCache__items.inventory
                                _data_o = _ir_o._data if isinstance(_ir_o._data, dict) else {}
                                _data_o[12] = {1: {}, 2: _vehicle_outfits, 3: {}}
                                _ir_o._data = _data_o
                                try:
                                    from collections import defaultdict as _ddO
                                    _ir_o._InventoryRequester__itemsCache = _ddO(dict)
                                except Exception as _clr_e:
                                    print '[OFFLINE] outfit cache clear error: %s' % _clr_e

                                _ic_o.items.invalidateCache()
                            except Exception as _sync_e:
                                print '[OFFLINE] outfit resync error: %s' % _sync_e

                            try:
                                from CurrentVehicle import g_currentVehicle as _gcv_o
                                veh_o = _gcv_o.vehicle
                                if veh_o is not None and getattr(veh_o, 'inventoryId', None) == vehicleInvID:
                                    _gcv_o._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehicleInvID)
                                BigWorld.callback(0.05, (lambda : _gcv_o.update()))
                            except Exception as _cv_e:
                                print '[OFFLINE] outfit vehicle refresh error: %s' % _cv_e

                            return

                        def fake_buyAndEquipOutfit(self, vehInvID, season, outfitDescr, callback=None):
                            try:
                                _intcd = _veh_intcd(vehInvID)
                                if _intcd is None:
                                    raise ValueError('no veh_cds entry for invID=%s' % vehInvID)
                                _vehicle_outfits.setdefault(_intcd, {})[season] = (
                                 outfitDescr, True)
                                print '[OFFLINE] shop.buyAndEquipOutfit: vehInvID=%s intCD=%s season=%s OK' % (vehInvID, _intcd, season)
                                _refresh_after_outfit_change(vehInvID)
                            except Exception as e:
                                print '[OFFLINE] buyAndEquipOutfit error: %s' % e

                            if callback:
                                BigWorld.callback(0.05, (lambda cb=callback: call_smart(cb, 0)))
                            return

                        shop_obj.buyAndEquipOutfit = new.instancemethod(fake_buyAndEquipOutfit, shop_obj, shop_obj.__class__)

                        def fake_buyAndEquipStyle(self, vehInvID, styleID, callback=None):
                            try:
                                from items.components.c11n_constants import SeasonType as _ST
                                _intcd = _veh_intcd(vehInvID)
                                if _intcd is None:
                                    raise ValueError('no veh_cds entry for invID=%s' % vehInvID)
                                if styleID:
                                    from items import vehicles as _vmod_st
                                    _style = _vmod_st.g_cache.customization20().styles.get(styleID)
                                    if _style is not None:
                                        for _season in (_ST.WINTER, _ST.SUMMER, _ST.DESERT, _ST.EVENT):
                                            try:
                                                _cd = _style.outfits.get(_season).makeCompDescr()
                                                _vehicle_outfits.setdefault(_intcd, {})[_season] = (_cd, True)
                                            except Exception:
                                                pass

                                else:
                                    _vehicle_outfits.pop(_intcd, None)
                                print '[OFFLINE] shop.buyAndEquipStyle: vehInvID=%s intCD=%s styleID=%s OK' % (vehInvID, _intcd, styleID)
                                _refresh_after_outfit_change(vehInvID)
                            except Exception as e:
                                print '[OFFLINE] buyAndEquipStyle error: %s' % e

                            if callback:
                                BigWorld.callback(0.05, (lambda cb=callback: call_smart(cb, 0)))
                            return

                        shop_obj.buyAndEquipStyle = new.instancemethod(fake_buyAndEquipStyle, shop_obj, shop_obj.__class__)

                        def fake_buyCustomizations(self, vehInvID, items_dict, callback=None):
                            print '[OFFLINE] shop.buyCustomizations: vehInvID=%s items=%r OK' % (vehInvID, items_dict)
                            if callback:
                                BigWorld.callback(0.05, (lambda cb=callback: call_smart(cb, 0)))
                            return

                        shop_obj.buyCustomizations = new.instancemethod(fake_buyCustomizations, shop_obj, shop_obj.__class__)

                    def fake_inventorySell(self, itemTypeID, intCD, count, callback=None):
                        print '[OFFLINE] inventory.sell: itemTypeID=%s intCD=%s count=%s' % (itemTypeID, intCD, count)
                        if callback:
                            BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                        return

                    inv_obj.sell = new.instancemethod(fake_inventorySell, inv_obj, inv_obj.__class__)
                p.selectVehicle = lambda invID: (
                 setattr(CurrentVehicle.g_currentVehicle, '_CurrentVehicle__vehicle', OfflineVehicleWrapper(invID)),
                 CurrentVehicle.g_currentVehicle.onChanged(),
                 None)[2]
                try:
                    from gui.shared.gui_items import Vehicle as _VehMod
                    _orig_ccb = _VehMod.Vehicle._calcCrewBonuses

                    def _safe_ccb(self_v, crew, proxy):
                        try:
                            return _orig_ccb(self_v, crew, proxy)
                        except Exception:
                            return {'equipment': 0, 'optDevices': 0, 'commander': 0, 'brotherhood': 0}

                        return

                    _VehMod.Vehicle._calcCrewBonuses = _safe_ccb
                    print '[OFFLINE] Vehicle._calcCrewBonuses patched OK'
                except Exception as _e:
                    print '[OFFLINE] Vehicle._calcCrewBonuses patch failed: %s' % _e

                p.stats._Stats__cache = off_stats
                p.inventory._Inventory__cache = {_VEHICLE_IDX: {'compDescr': inv_vehicles, 
                                  'crew': crew_map, 
                                  'shells': (dict((i, list(_vehicle_shells.get(i, []))) for i in my_garage)), 
                                  'shellsLayout': (dict((i, {}) for i in my_garage)), 
                                  'repair': (dict((i, (0, 100)) for i in my_garage)), 
                                  'eqs': (dict((i, list(_vehicle_equipments.get(i, [0, 0, 0]))) for i in my_garage)), 
                                  'eqsLayout': (dict((i, list(_vehicle_equipments.get(i, [0, 0, 0]))) for i in my_garage)), 
                                  'settings': (dict((i, 0) for i in my_garage)), 
                                  'lock': (dict((i, (0, 0)) for i in my_garage))}, 
                   _TANKMAN_IDX: {'compDescr': t_cache, 
                                  'vehicle': t_in_veh}, 
                   12: {1: {}, 2: _vehicle_outfits, 3: {}}}
                for _cd, _cnt in _modules_inventory.items():
                    _type_idx = None
                    for _tidx, _tdict in _modules_by_type.items():
                        if _cd in _tdict:
                            _type_idx = _tidx
                            break

                    if _type_idx is not None:
                        p.inventory._Inventory__cache.setdefault(_type_idx, {})[_cd] = _cnt

                try:
                    _qp = getattr(p, 'questProgress', None)
                    if _qp is not None:
                        _qp_cache = _qp._QuestProgress__cache
                        _qp_cache.setdefault('quests', {})
                        _qp_cache.setdefault('tokens', {})
                        _qp_cache['potapovQuests'] = {'compDescr': '', 
                           'regular': {'slots': 0, 'selected': [], 'lastIDs': {}}, 'training': {'slots': 0, 'selected': [], 'lastIDs': {}}}
                        print "[OFFLINE] questProgress.__cache pre-filled OK (potapovQuests/quests/tokens stub, fixes KeyError('potapovQuests'))"
                except Exception as _qpe:
                    print '[OFFLINE] questProgress.__cache pre-fill failed: %s' % _qpe

                try:
                    import AccountCommands as _AC
                    _sd = p.syncData
                    _sd._AccountSyncData__ignore = False
                    _sd._AccountSyncData__isSynchronized = True
                    for _sub_cb in list(getattr(_sd, '_AccountSyncData__subscribers', [])):
                        try:
                            _sub_cb(_AC.RES_CACHE)
                        except Exception as _sub_e:
                            print '[OFFLINE] syncData subscriber error: %s' % _sub_e

                    _sd._AccountSyncData__subscribers = []
                    print '[OFFLINE] syncData forced synced OK'
                except Exception as _sde:
                    print '[OFFLINE] syncData force sync failed: %s' % _sde

                try:
                    for _subsys_name in ('inventory', 'stats', 'questProgress', 'dossierCache', 'battleResultsCache', 'goodies', 'vehicleRotation',
                     'recycleBin', 'ranked', 'badges', 'newYear'):
                        _subsys = getattr(p, _subsys_name, None)
                        if _subsys is None:
                            continue
                        try:
                            _subsys.onAccountBecomePlayer()
                        except Exception as _sse:
                            print '[OFFLINE] %s.onAccountBecomePlayer() failed: %s' % (_subsys_name, _sse)

                    try:
                        p.shop._Shop__ignore = False
                    except Exception as _she:
                        print '[OFFLINE] shop.__ignore force failed: %s' % _she

                    print "[OFFLINE] account subsystems onAccountBecomePlayer() forced OK (fixes RES_NON_PLAYER / 'Account become non player')"
                except Exception as _abpe:
                    print '[OFFLINE] onAccountBecomePlayer force block failed: %s' % _abpe

                try:
                    from skeletons.gui.lobby_context import ILobbyContext as _ILC
                    _lc = dependency.instance(_ILC)
                    if _lc is not None:
                        _lc.onAccountBecomePlayer()
                        print "[OFFLINE] LobbyContext.onAccountBecomePlayer() forced OK (fixes 'Wrong activation state for encyclopedia')"
                except Exception as _lce:
                    print '[OFFLINE] LobbyContext.onAccountBecomePlayer() force failed: %s' % _lce

                class FakeS(str):

                    def __getattr__(self, n):
                        return (lambda *a, **k: FakeS(''))

                    def __getitem__(self, k):
                        return FakeS('')

                _AccountSettingsCls._AccountSettings__readUserSection = staticmethod((lambda *a: FakeS('')))
                if 1 in veh_cds:
                    CurrentVehicle.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(1)
            print '[OFFLINE] reached end of inventory setup, about to close Waiting'
            try:
                from gui.Scaleform.Waiting import Waiting
                Waiting.close()
            except:
                pass

            try:
                import predefined_hosts as _ph

                def _safe_roamingHosts(self):
                    return []

                _ph._PreDefinedHostList.roamingHosts = _safe_roamingHosts
                print '[OFFLINE] predefined_hosts.roamingHosts patched OK'
            except Exception as _e:
                print '[OFFLINE] roamingHosts patch skipped: %s' % _e

            try:
                import gui.ClientHangarSpace as _chs

                def _safe_setupCamera(self, *args, **kwargs):
                    try:
                        _orig_setupCamera(self, *args, **kwargs)
                    except (AttributeError, TypeError) as _e:
                        print '[OFFLINE] __setupCamera suppressed: %s' % _e

                    return

                _orig_setupCamera = _chs.ClientHangarSpace._ClientHangarSpace__setupCamera
                _chs.ClientHangarSpace._ClientHangarSpace__setupCamera = _safe_setupCamera
                print '[OFFLINE] ClientHangarSpace.__setupCamera patched OK'
            except Exception as _e:
                print '[OFFLINE] camera patch skipped: %s' % _e

            try:
                import gui.Scaleform.daapi.view.lobby.customization.main_view as _c11n_mv
                _orig_getUpdatedAnchorPositions = _c11n_mv.MainView._getUpdatedAnchorPositions

                def _safe_getUpdatedAnchorPositions(self):
                    try:
                        return _orig_getUpdatedAnchorPositions(self)
                    except (KeyError, TypeError, AttributeError) as _ape:
                        return _c11n_mv.CustomizationAnchorsSetVO([])._asdict()

                    return

                _c11n_mv.MainView._getUpdatedAnchorPositions = _safe_getUpdatedAnchorPositions
                print '[OFFLINE] CustomizationMainView._getUpdatedAnchorPositions patched OK (no more KeyError:None spam / frozen Close button)'
            except Exception as _e:
                print '[OFFLINE] CustomizationMainView anchor patch skipped: %s' % _e

            import cPickle

            def _show_lobby():
                print '[OFFLINE] _show_lobby CALLED'
                try:
                    p2 = BigWorld.player()
                    print '[OFFLINE] player=%r hasShowGUI=%r' % (p2, hasattr(p2, 'showGUI') if p2 else None)
                    if p2 and hasattr(p2, 'showGUI'):
                        ctx = cPickle.dumps({'databaseID': 1})
                        p2.showGUI(ctx)
                        print '[OFFLINE] showGUI() returned OK'

                        def _initial_stats_resync():
                            from PlayerEvents import g_playerEvents as _pe_s
                            _pe_s.onStatsResync()
                            return

                        BigWorld.callback(0.5, _initial_stats_resync)
                    else:
                        try:
                            from gui.WindowsManager import g_windowsManager
                            g_windowsManager.showLobby()
                            print '[OFFLINE] showLobby() returned OK (no showGUI path)'
                        except ImportError:
                            print '[OFFLINE] gui.WindowsManager not present in this client version (0.9.22 gap, see comment)'

                except:
                    print '[OFFLINE] _show_lobby EXCEPTION:'
                    traceback.print_exc()
                    try:
                        from gui.WindowsManager import g_windowsManager
                        g_windowsManager.showLobby()
                    except Exception:
                        pass

                return

            BigWorld.callback(0.3, _show_lobby)
        except Exception:
            traceback.print_exc()

        return

    connectionManager.initiateConnection = fake_connect
    print '[OFFLINE] Ready'
    return


def _safe_call(_fn, _name):
    try:
        _fn()
    except Exception:
        import traceback, os
        tb = traceback.format_exc()
        print '[OFFLINE] %s FAILED:\n%s' % (_name, tb)
        try:
            with open(os.path.join(os.getcwd(), 'offline_init_error.log'), 'a') as _f:
                _f.write('=== %s ===\n%s\n' % (_name, tb))
        except Exception:
            pass

    return


_safe_call(init_offline, 'init_offline')

def _apply_surgical_fixes():
    from gui.shared.gui_items.fitting_item import FittingItem

    def safe_level_fget(self):
        try:
            if self.itemTypeName == 'vehicle':
                return self.descriptor.level
            else:
                return getattr(self.descriptor, 'level', 0)

        except:
            return 0

        return

    FittingItem.level = property(safe_level_fget)

    def safe_name_fget(self):
        try:
            if self.itemTypeName == 'vehicle':
                return self.descriptor.type.userString
            else:
                return getattr(self.descriptor, 'userString', 'Unknown Item')

        except:
            return 'Item %s' % str(getattr(self, 'compactDescr', 'ID'))

        return

    FittingItem.name = property(safe_name_fget)

    def safe_longName_fget(self):
        try:
            from items import ITEM_TYPE_NAMES as _ITN
            if self.itemTypeName == _ITN[1]:
                from gui.shared.gui_items.Vehicle import getUserName as _getVehicleFullName
                return _getVehicleFullName(self.descriptor.type)
            if self.itemTypeName == _ITN[10]:
                import BigWorld as _BW, nations as _nat
                from helpers.i18n import makeString as _ms
                try:
                    n = self.nation
                    if n == _nat.INDICES.get('germany', -1):
                        cal = float(getattr(self.descriptor, 'caliber', 0)) / 10
                        dim = _ms('#item_types:shell/dimension/sm')
                    elif n == _nat.INDICES.get('usa', -1):
                        cal = float(getattr(self.descriptor, 'caliber', 0)) / 25.4
                        dim = _ms('#item_types:shell/dimension/inch')
                    else:
                        cal = getattr(self.descriptor, 'caliber', 0)
                        dim = _ms('#item_types:shell/dimension/mm')
                    return _ms('#item_types:shell/name') % {'kind': (_ms('#item_types:shell/kinds/' + getattr(self.descriptor, 'kind', ''))), 
                       'name': (self.name), 
                       'caliber': (_BW.wg_getNiceNumberFormat(cal)), 
                       'dimension': dim}
                except:
                    pass

            desc = self.descriptor
            ustr = getattr(desc, 'userString', getattr(desc, 'name', 'Unknown'))
            return (self.userType + ' ' + ustr).strip()
        except:
            return 'Unknown'

        return

    FittingItem.longName = property(safe_longName_fget)
    try:
        from gui.shared.items_parameters.params import ShellParams as _SP
        from gui.shared.items_parameters import NO_DATA as _NO_DATA_SP
        _orig_ppt_fget = _SP.piercingPowerTable.fget

        def _safe_piercingPowerTable(self):
            try:
                return _orig_ppt_fget(self)
            except IndexError:
                return _NO_DATA_SP

            return

        _SP.piercingPowerTable = property(_safe_piercingPowerTable)
        _orig_msd_fget = _SP.maxShotDistance.fget

        def _safe_maxShotDistance(self):
            try:
                return _orig_msd_fget(self)
            except IndexError:
                return

            return

        _SP.maxShotDistance = property(_safe_maxShotDistance)
        print '[OFFLINE] ShellParams.piercingPowerTable/maxShotDistance patched OK (no crash on gun-incompatible shell)'
    except Exception as _spe:
        print '[OFFLINE] ShellParams patch failed: %s' % _spe

    try:
        from gui.Scaleform.ToolTipManager import g_instance as _ttm
        if not hasattr(_ttm, '_ToolTipManager__vehicleTypeLock'):
            _ttm._ToolTipManager__vehicleTypeLock = {}
        if not hasattr(_ttm, '_ToolTipManager__globalVehicleLocks'):
            _ttm._ToolTipManager__globalVehicleLocks = {}

        def _offline_ttm_inventory_update(diff_arg=None):
            try:
                import BigWorld as _bwT
                from gui.shared.utils.gui_items import InventoryVehicle as _IV
                _p = _bwT.player()
                if _p is None:
                    return
                _veh_bucket = _p.inventory._Inventory__cache.get(1, {})
                _cds = _veh_bucket.get('compDescr', {})
                _crews = _veh_bucket.get('crew', {})
                _shells = _veh_bucket.get('shells', {})
                _eqs = _veh_bucket.get('eqs', {})
                _eqsL = _veh_bucket.get('eqsLayout', {})
                _repair = _veh_bucket.get('repair', {})
                _lock = _veh_bucket.get('lock', {})
                _sett = _veh_bucket.get('settings', {})
                new_inv = {}
                new_descrs = {}
                for _iid, _cd in _cds.iteritems():
                    try:
                        _iv = _IV(compactDescr=_cd, id=_iid, crew=_crews.get(_iid, []), shells=_shells.get(_iid, []), repairCost=_repair.get(_iid, (0,
                                                                                                                                                    100))[0] if isinstance(_repair.get(_iid), (tuple, list)) else 0, health=100, lock=_lock.get(_iid, 0), equipments=_eqs.get(_iid, [0, 0, 0]), equipmentsLayout=_eqsL.get(_iid, [0, 0, 0]), settings=_sett.get(_iid, 0))
                        new_inv[_iid] = _iv
                        new_descrs[_iid] = _cd
                    except Exception as _ive:
                        print '[OFFLINE] TTM _IV build error invID=%s: %s' % (_iid, _ive)

                _ttm._ToolTipManager__inventoryVehicles = new_inv
                _ttm._ToolTipManager__inventoryVehiclesDescrs = new_descrs
                print '[OFFLINE] ToolTipManager.__inventoryVehicles updated: %d vehicles' % len(new_inv)
            except Exception as _te:
                import traceback as _tbtm
                _tbtm.print_exc()

            return

        _ttm.onInventoryUpdate = _offline_ttm_inventory_update
        _offline_ttm_inventory_update()
        print '[OFFLINE] ToolTipManager.onInventoryUpdate patched OK'
    except (ImportError, AttributeError):
        pass

    try:
        import gui.shared.utils.gui_items as _gi_mod
        _orig_gdd = _gi_mod.getDictDescr

        def _safe_gdd(cd):
            try:
                desc = _orig_gdd(cd)
                if isinstance(desc, dict) and 'shots' not in desc:
                    desc['shots'] = []
                return desc
            except:
                return {}

            return

        _gi_mod.getDictDescr = _safe_gdd
    except:
        pass

    try:
        from gui.prb_control.storages import fallout_storage as _fo_mod

        def _fo_get(self, key, default):
            settings = getattr(self, '_FalloutLocalStorage__settings', None) or {}
            return settings.get(key, default)

        def _fo_safe_getBattleType(self):
            from constants import QUEUE_TYPE as _QT
            return _fo_get(self, 'falloutBattleType', _QT.UNKNOWN)

        def _fo_safe_isEnabled(self):
            try:
                cacheEnabled = bool(self.eventsCache.isFalloutEnabled())
            except Exception:
                cacheEnabled = False

            return cacheEnabled and bool(_fo_get(self, 'isEnabled', False))

        def _fo_safe_isAutomatch(self):
            return bool(_fo_get(self, 'isAutomatch', False))

        _fo_mod.FalloutLocalStorage.getBattleType = _fo_safe_getBattleType
        _fo_mod.FalloutLocalStorage.isEnabled = _fo_safe_isEnabled
        _fo_mod.FalloutLocalStorage.isAutomatch = _fo_safe_isAutomatch
        print '[OFFLINE] FalloutLocalStorage patched OK (safe getBattleType/isEnabled/isAutomatch, no KeyError on empty settings)'
    except Exception as _fse:
        print '[OFFLINE] FalloutLocalStorage patch failed: %s' % _fse

    try:
        import gui.Scaleform.daapi.view.lobby.hangar.AmmunitionPanel as _amp_mod
        from gui.Scaleform.daapi.view.lobby.shared.fitting_slot_vo import FittingSlotVO as _FSVO
        from gui.Scaleform.genConsts.TOOLTIPS_CONSTANTS import TOOLTIPS_CONSTANTS as _TTC
        from items.vehicles import NUM_OPTIONAL_DEVICE_SLOTS as _NODS
        from gui.shared.gui_items import GUI_ITEM_TYPE_INDICES as _GTI
        from gui.shared.gui_items.vehicle_equipment import BATTLE_BOOSTER_LAYOUT_SIZE as _BBLS
        from gui.shared.utils.requesters import REQ_CRITERIA as _RC
        from helpers import dependency as _dep2
        from skeletons.gui.shared import IItemsCache as _IIC2
        _MODULE_ATTR_BY_SLOT_TYPE = {'vehicleChassis': 'chassis', 
           'vehicleTurret': 'turret', 
           'vehicleGun': 'gun', 
           'vehicleEngine': 'engine', 
           'vehicleRadio': 'radio'}

        @_dep2.replace_none_kwargs(itemsCache=_IIC2)
        def _safe_getFittingSlotsData(vehicle, slotsRange, VoClass=None, itemsCache=None):
            devices = []
            VoClass = VoClass or _FSVO
            itemTypeIDs = [_GTI[slotType] for slotType in slotsRange]
            gatheredItems = itemsCache.items.getItemsEx(itemTypeIDs, _RC.CUSTOM((lambda item: item.isInstalled(vehicle))), vehicle.nationID)
            for slotType in slotsRange:
                slotTypeIdx = _GTI[slotType]
                slotItems = [item for item in gatheredItems.itervalues() if item.itemTypeID == slotTypeIdx]
                if slotType in _amp_mod.ARTEFACTS_SLOTS:
                    for slotId in xrange(_NODS):
                        devices.append(VoClass(slotItems, vehicle, slotType, slotId, _TTC.HANGAR_MODULE))
                    continue
                if slotType in _amp_mod._BOOSTERS_SLOTS:
                    for slotId in xrange(_BBLS):
                        devices.append(VoClass(slotItems, vehicle, slotType, slotId, tooltipType=_TTC.BATTLE_BOOSTER))
                    continue
                if not slotItems and slotType in _MODULE_ATTR_BY_SLOT_TYPE:
                    try:
                        installedCD = getattr(vehicle, _MODULE_ATTR_BY_SLOT_TYPE[slotType]).intCD
                        installedItem = itemsCache.items.getItemByCD(installedCD)
                        if installedItem is not None:
                            slotItems = [
                             installedItem]
                    except Exception as _mfe:
                        print '[OFFLINE] getFittingSlotsData fallback failed for %s: %s' % (slotType, _mfe)

                devices.append(VoClass(slotItems, vehicle, slotType, tooltipType=_TTC.HANGAR_MODULE))

            return devices

        _amp_mod.getFittingSlotsData = _safe_getFittingSlotsData
        print '[OFFLINE] AmmunitionPanel.getFittingSlotsData patched OK (fallback to equipped module when shop price table is missing an entry)'
    except Exception as _afse:
        print '[OFFLINE] AmmunitionPanel.getFittingSlotsData patch failed: %s' % _afse

    return


_safe_call(_apply_surgical_fixes, '_apply_surgical_fixes')

def _patch_processors():
    try:
        from gui.shared.gui_items.processors import plugins as _plg
        from gui.shared.gui_items.processors import makeSuccess as _ms_proc

        def _always_pass(self):
            return _ms_proc()

        def _safe_init_passthrough(orig_init):
            """Возвращает __init__ который безопасно вызывает оригинал с любыми аргументами,
            но при ошибке инициализирует вручную через ProcessorPlugin.__init__."""

            def _new_init(self, *args, **kwargs):
                try:
                    orig_init(self, *args, **kwargs)
                except TypeError:
                    try:
                        _plg.ProcessorPlugin.__init__(self, _plg.ProcessorPlugin.TYPE.VALIDATOR, False, True)
                    except Exception:
                        pass

                self.isEnabled = True
                return

            return _new_init

        try:
            _plg.WalletValidator.__init__ = _safe_init_passthrough(_plg.WalletValidator.__init__)
            _plg.WalletValidator._validate = _always_pass
            print '[OFFLINE] WalletValidator patched OK (always pass)'
        except Exception as _e:
            print '[OFFLINE] WalletValidator patch failed: %s' % _e

        try:
            _plg.MoneyValidator.__init__ = _safe_init_passthrough(_plg.MoneyValidator.__init__)
            _plg.MoneyValidator._validate = _always_pass
            print '[OFFLINE] MoneyValidator patched OK (always pass)'
        except Exception as _e:
            print '[OFFLINE] MoneyValidator patch failed: %s' % _e

        try:
            _plg.CompatibilityInstallValidator._validate = _always_pass
            print '[OFFLINE] CompatibilityInstallValidator patched OK (always pass)'
        except Exception as _e:
            print '[OFFLINE] CompatibilityInstallValidator patch failed: %s' % _e

        try:
            _plg.TurretCompatibilityInstallValidator._validate = _always_pass
            print '[OFFLINE] TurretCompatibilityInstallValidator patched OK (always pass)'
        except Exception as _e:
            print '[OFFLINE] TurretCompatibilityInstallValidator patch failed: %s' % _e

        try:
            _plg.CompatibilityRemoveValidator._validate = _always_pass
        except Exception:
            pass

        try:
            _plg.VehicleValidator._validate = _always_pass
        except Exception:
            pass

        try:
            _plg.VehicleSellsLeftValidator._validate = _always_pass
        except Exception:
            pass

        try:
            _plg.VehicleLayoutValidator._validate = _always_pass
            print '[OFFLINE] VehicleLayoutValidator patched OK (always pass)'
        except Exception as _e:
            print '[OFFLINE] VehicleLayoutValidator patch failed: %s' % _e

    except Exception as _ep:
        print '[OFFLINE] _patch_processors failed: %s' % _ep

    return


_safe_call(_patch_processors, '_patch_processors')

def _patch_ammunition_panel():
    try:
        import sys
        _ap_cls = None
        _ap_mod = sys.modules.get('gui.Scaleform.daapi.view.lobby.hangar.AmmunitionPanel')
        if _ap_mod is not None and hasattr(_ap_mod, 'AmmunitionPanel'):
            _ap_cls = _ap_mod.AmmunitionPanel
        if _ap_cls is None:
            try:
                import gui.Scaleform.daapi.view.lobby.hangar.AmmunitionPanel as _m
                _ap_cls = _m.AmmunitionPanel
            except Exception:
                pass

        if _ap_cls is None:
            print '[OFFLINE] AmmunitionPanel: class not found yet, will retry on first call'
            return
        if not hasattr(_ap_cls, 'setVehicleModule'):
            print '[OFFLINE] AmmunitionPanel.setVehicleModule not present in 0.9.22 (module install now via gui.shared.gui_items.processors) - skipping, this is expected'
            return
        _orig_svm = _ap_cls.setVehicleModule

        def _safe_setVehicleModule(self, newId, slotIdx, oldId, isRemove):
            try:
                return _orig_svm(self, newId, slotIdx, oldId, isRemove)
            except TypeError as _te:
                print '[OFFLINE] AmmunitionPanel.setVehicleModule TypeError suppressed: %s' % _te
            except Exception as _e:
                print '[OFFLINE] AmmunitionPanel.setVehicleModule error suppressed: %s' % _e

            return

        _ap_cls.setVehicleModule = _safe_setVehicleModule
        print '[OFFLINE] AmmunitionPanel.setVehicleModule patched OK (safe wrapper)'
    except Exception as _e:
        print '[OFFLINE] AmmunitionPanel patch failed: %s' % _e

    return


_safe_call(_patch_ammunition_panel, '_patch_ammunition_panel')

def _patch_rental_package_dp():
    try:
        from gui.Scaleform.daapi.view.lobby.customization.data_providers import RentalPackageDataProviderBase as _RPDPBase
        _orig_pyRequestItemAt = _RPDPBase.pyRequestItemAt

        def _safe_pyRequestItemAt(self, idx):
            item = _orig_pyRequestItemAt(self, idx)
            if item is None:
                return {'periodDays': 0, 'cost': 0, 
                   'defCost': 0, 
                   'isGold': False, 
                   'userString': '', 
                   'isIGR': False, 
                   'enabled': True}
            else:
                return item

        _RPDPBase.pyRequestItemAt = _safe_pyRequestItemAt
        print '[OFFLINE] RentalPackageDataProviderBase.pyRequestItemAt patched OK (safe wrapper)'
    except Exception as _e:
        print '[OFFLINE] RentalPackageDataProviderBase patch failed: %s' % _e

    return


_safe_call(_patch_rental_package_dp, '_patch_rental_package_dp')

def _offline_hard_log(msg):
    try:
        import os, time
        with open(os.path.join(os.getcwd(), 'offline_quit_debug.log'), 'a') as _f:
            _f.write('[%s] %s\n' % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass

    return


def _patch_quit_hang():
    import os, sys

    def _force_quit(source):
        _offline_hard_log('force-quit triggered via: %s' % source)
        try:
            print '[OFFLINE] quit intercepted (%s) -> os._exit(0)' % source
            sys.stdout.flush()
        except Exception:
            pass

        try:
            import thread, time as _time

            def _backup_exit():
                _time.sleep(1.0)
                os._exit(0)
                return

            thread.start_new_thread(_backup_exit, ())
        except Exception:
            pass

        os._exit(0)
        return

    try:

        def _bw_force_quit(*args, **kwargs):
            _force_quit('BigWorld.quit')
            return

        BigWorld.quit = _bw_force_quit
        _offline_hard_log('BigWorld.quit patched OK')
        print '[OFFLINE] BigWorld.quit patched OK (force os._exit on quit)'
    except Exception as _e:
        _offline_hard_log('BigWorld.quit patch FAILED: %s' % _e)
        print '[OFFLINE] BigWorld.quit patch failed: %s' % _e

    try:
        from gui.Scaleform.framework.application import SFApplication as App

        def _app_quit(self):
            _force_quit('App.quit')
            return

        App.quit = _app_quit
        _offline_hard_log('App.quit patched OK')
        print '[OFFLINE] App.quit patched OK'
    except Exception as _e:
        _offline_hard_log('App.quit patch FAILED: %s' % _e)
        print '[OFFLINE] App.quit patch failed: %s' % _e

    try:
        from gui.Scaleform.windows import BattleWindow

        def _bw_onQuit(self, arg):
            _force_quit('BattleWindow.onQuit')
            return

        BattleWindow.onQuit = _bw_onQuit
        _offline_hard_log('BattleWindow.onQuit patched OK')
        print '[OFFLINE] BattleWindow.onQuit patched OK'
    except Exception as _e:
        _offline_hard_log('BattleWindow.onQuit patch FAILED: %s' % _e)
        print '[OFFLINE] BattleWindow.onQuit patch failed: %s' % _e

    return


_safe_call(_patch_quit_hang, '_patch_quit_hang')

def _patch_game_fini_hang():
    try:
        import os, game, thread, time as _time
        _orig_fini = game.fini

        def _fini_watchdog():
            _time.sleep(2.0)
            _offline_hard_log('game.fini watchdog fired -> os._exit(0)')
            try:
                print '[OFFLINE] game.fini watchdog (fini hung) -> os._exit(0)'
            except Exception:
                pass

            os._exit(0)
            return

        def _patched_fini(*args, **kwargs):
            _offline_hard_log('game.fini triggered (native window close)')
            try:
                thread.start_new_thread(_fini_watchdog, ())
            except Exception:
                pass

            try:
                _orig_fini(*args, **kwargs)
            except Exception:
                _offline_hard_log('game.fini original raised -> forcing exit')

            _offline_hard_log('game.fini completed -> os._exit(0)')
            os._exit(0)
            return

        game.fini = _patched_fini
        _offline_hard_log('game.fini patched OK')
        print '[OFFLINE] game.fini patched OK (guaranteed exit on native window close)'
    except Exception as _e:
        _offline_hard_log('game.fini patch FAILED: %s' % _e)
        print '[OFFLINE] game.fini patch failed: %s' % _e
        raise

    return


def _patch_game_fini_hang_deferred(_attempt=0):
    try:
        _patch_game_fini_hang()
        import sys
        if 'game' not in sys.modules or not hasattr(sys.modules['game'], 'fini'):
            raise ImportError('game module not ready yet')
    except Exception as _e:
        if _attempt < 20:
            _offline_hard_log('game.fini deferred patch retry #%d: %s' % (_attempt + 1, _e))
            BigWorld.callback(0.5, (lambda : _patch_game_fini_hang_deferred(_attempt + 1)))
        else:
            _offline_hard_log('game.fini deferred patch GAVE UP after %d attempts' % _attempt)
            print '[OFFLINE] game.fini patch gave up after %d attempts' % _attempt

    return


BigWorld.callback(0, _patch_game_fini_hang_deferred)

def _patch_force_log_flush():
    try:

        def _autoFlush():
            try:
                BigWorld.flushPythonLog()
            except Exception:
                pass

            BigWorld.callback(1.0, _autoFlush)
            return

        BigWorld.callback(1.0, _autoFlush)
        _offline_hard_log('force log flush armed (every 1s)')
        print '[OFFLINE] python.log force-flush armed (every 1s)'
    except Exception as _e:
        _offline_hard_log('force log flush FAILED: %s' % _e)
        print '[OFFLINE] force log flush failed: %s' % _e

    return


_safe_call(_patch_force_log_flush, '_patch_force_log_flush')
