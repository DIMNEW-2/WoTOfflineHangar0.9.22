# -*- coding: utf-8 -*-
"""
Applies the WoT 0.9.21.0.1 port to the repaired decompiled Manager.py
(src/decompiled_0.9.22/Manager.py + tools/repair_decompiled.py output).

Usage:  python3 tools/port_manager.py [path/to/Manager.py]
Every replacement is exact and must occur exactly once.
"""
import sys

path = sys.argv[1] if len(sys.argv) > 1 else 'src/Offline/Manager.py'
src = open(path).read()

def rep(old, new, name):
    global src
    n = src.count(old)
    if n != 1:
        print('FAIL %s: count=%d' % (name, n))
        sys.exit(1)
    src = src.replace(old, new)
    print('OK %s' % name)

# ---------------------------------------------------------------- header
rep("""# uncompyle6 version 3.9.3
# Python bytecode version base 2.7 (62211)""",
"""# -*- coding: utf-8 -*-
# Offline Hangar for World of Tanks 0.9.21.0.1.
#
# Ported from the 0.9.22.0.1 build (decompiled from the original .pyc,
# see src/decompiled_0.9.22/). Lets the client enter the hangar without a
# server: full vehicle list of this client version, 100% crews, unlimited
# money, working module/ammo/crew fitting and customization.
#
# The New Year event ("Holiday Ops 2018") is served locally by
# Offline.NewYearServer - see that module.
# Python bytecode version base 2.7 (62211)""",
'01-header')

# ------------------------------------------------- offline state globals
rep("""_vehicle_shells = {}
_vehicle_outfits = {}""",
"""_vehicle_shells = {}
_vehicle_outfits = {}

# ---------------------------------------------------------------------------
# _OFFLINE_STATE exposes the live fake-account data structures built inside
# fake_connect() so that Offline.NewYearServer (the local "server" for the
# New Year event) can grant tankmen and resync the GUI caches exactly like
# the fakes in this module do.
# ---------------------------------------------------------------------------
_OFFLINE_STATE = {}

def get_offline_state():
    return _OFFLINE_STATE


def _offline_shop_goodies():
    \"\"\"NY vehicle-discount goodie descriptions for the shop cache (or None).\"\"\"
    try:
        from Offline import NewYearServer as _NYS
        _g = _NYS.build_shop_goodies()
        if _g:
            return {'goodies': _g}
    except Exception as _ge:
        print '[OFFLINE] NY shop goodies build failed: %s' % _ge
    return None


def _offline_ny_commands():
    cmds = set()
    for _name in ('CMD_NEW_YEAR_SLOT_FILL', 'CMD_NEW_YEAR_CRAFT', 'CMD_NEW_YEAR_BREAK_TOYS',
                  'CMD_NEW_YEAR_ADD_TOYS_DEV', 'CMD_NEW_YEAR_OPEN_BOX', 'CMD_NEW_YEAR_ADD_TOKEN_DEV',
                  'CMD_NEW_YEAR_SEE_TOYS', 'CMD_NEW_YEAR_SELECT_DISCOUNT', 'CMD_NEW_YEAR_SELECT_TANKMAN'):
        _v = getattr(AccountCommands, _name, None)
        if _v is not None:
            cmds.add(_v)
    return cmds""",
'02-globals')

# ------------------------------------------------------ __doCmd NY route
rep("""        def _fake_do_cmd(self, doCmdMethod, cmd, callback, *args):
            try:
                requestID = AccountCommands.REQUEST_ID_UNRESERVED_MIN""",
"""        def _fake_do_cmd(self, doCmdMethod, cmd, callback, *args):
            # New Year (Holiday Ops 2018) commands are served locally by
            # Offline.NewYearServer with the 4-argument callback the NY
            # requester expects: callback(requestId, code, txtMsg, ext).
            try:
                if cmd in _offline_ny_commands():
                    from Offline import NewYearServer as _NYS_cmd
                    return _NYS_cmd.handle_command(self, cmd, callback, args)
            except Exception as _nyce:
                print '[OFFLINE] NY command dispatch failed (cmd=%s): %s' % (cmd, _nyce)
                traceback.print_exc()
            try:
                requestID = AccountCommands.REQUEST_ID_UNRESERVED_MIN""",
'03-docmd-ny')

# --------------------------------------------- shop goodies in itemsCache
rep("""                    _shr._AbstractRequester__synced = True
                    print '[OFFLINE] ShopRequester injected: %d itemPrices' % len(_shop_data.get('itemPrices', {}))""",
"""                    try:
                        _ny_goodies = _offline_shop_goodies()
                        if _ny_goodies:
                            _shr._data['goodies'] = _ny_goodies
                            print '[OFFLINE] NY discount goodies injected into shop (%d)' % len(_ny_goodies['goodies'])
                    except Exception as _nge:
                        print '[OFFLINE] NY shop goodies injection failed: %s' % _nge
                    _shr._AbstractRequester__synced = True
                    print '[OFFLINE] ShopRequester injected: %d itemPrices' % len(_shop_data.get('itemPrices', {}))""",
'04-goodies-ic')

# ----------------------------------------------- shop goodies in getCache
rep("""'crystalExchangeRate': (_shop_data_gc.get('crystalExchangeRate', 25))}
                            else:
                                res = {}""",
"""'crystalExchangeRate': (_shop_data_gc.get('crystalExchangeRate', 25))}
                                try:
                                    _ny_goodies_gc = _offline_shop_goodies()
                                    if _ny_goodies_gc:
                                        res['goodies'] = _ny_goodies_gc
                                except Exception as _ngegc:
                                    print '[OFFLINE] NY shop goodies (getCache) injection failed: %s' % _ngegc
                            else:
                                res = {}""",
'05-goodies-getcache')

# ------------------------------------------------------- dynamic tank list
rep("""            tank_list = [""",
"""            tank_list_fallback = [  # static list from the 0.9.22 build, fallback only""",
'06-tanklist-rename')

rep("""             'uk:GB75_AT7', 'uk:GB78_Sexton_I', 'uk:GB51_Excelsior',
            ]
            my_garage = {}""",
"""             'uk:GB75_AT7', 'uk:GB78_Sexton_I', 'uk:GB51_Excelsior',
            ]
            # 0.9.21 port: build the garage from the client's own vehicle
            # list so that every vehicle present in this client version is
            # in the hangar (the static list above is only a fallback).
            tank_list = []
            try:
                _seen_names = set()
                for _nID in range(len(nations.NAMES)):
                    try:
                        _vlist = vehicles.g_list.getList(_nID)
                    except Exception:
                        continue
                    for _vID in sorted(_vlist.keys()):
                        _nm = getattr(_vlist[_vID], 'name', None)
                        if _nm and _nm not in _seen_names:
                            _seen_names.add(_nm)
                            tank_list.append(_nm)
                print '[OFFLINE] tank_list built from client vehicle list: %d vehicles' % len(tank_list)
            except Exception as _tle:
                print '[OFFLINE] dynamic tank_list build failed: %s' % _tle
            if not tank_list:
                tank_list = tank_list_fallback
            my_garage = {}""",
'07-tanklist-dynamic')

# --------------------------------------------------- CurrentVehicle setter
rep("""                def _offline_cv_set_vehicle(self, v):
                    self._CurrentVehicle__vehicle = v
                    return""",
"""                def _offline_cv_set_vehicle(self, v):
                    self._CurrentVehicle__vehicle = v
                    # 0.9.21 GUI reads _CurrentVehicle.__vehInvID/.item, keep
                    # the native state in sync with the offline wrapper.
                    try:
                        if v is not None:
                            self._CurrentVehicle__vehInvID = v.inventoryId
                    except Exception:
                        pass
                    return""",
'08-cv-setter')

# ------------------------------------------- select vehicle helper + wrap
rep("""                CurrentVehicle._CurrentVehicle.update = _offline_cv_update

                def call_smart(cb, data):""",
'''                CurrentVehicle._CurrentVehicle.update = _offline_cv_update

                def _offline_select_vehicle(invID):
                    """Selects a vehicle in the hangar the 0.9.21 way: native
                    _CurrentVehicle state (__vehInvID/.item) for the GUI plus
                    the OfflineVehicleWrapper used by this module's fakes."""
                    if invID not in veh_cds:
                        return
                    _gcv_sel = CurrentVehicle.g_currentVehicle
                    try:
                        _gcv_sel._CurrentVehicle__vehicle = OfflineVehicleWrapper(invID)
                    except Exception as _we:
                        print '[OFFLINE] wrapper build failed for invID=%s: %s' % (invID, _we)
                    try:
                        _gcv_sel._CurrentVehicle__vehInvID = invID
                    except Exception:
                        pass
                    off_stats['currentVehInvID'] = invID
                    try:
                        _gcv_sel.onChanged()
                    except Exception:
                        pass
                    try:
                        _gcv_sel.refreshModel()
                    except Exception:
                        pass
                    return

                try:
                    _orig_cv_selectVehicle = CurrentVehicle.g_currentVehicle.selectVehicle

                    def _offline_cv_selectVehicle(vehInvID=0):
                        _orig_cv_selectVehicle(vehInvID)
                        try:
                            _selID = CurrentVehicle.g_currentVehicle.invID
                            if _selID in veh_cds:
                                CurrentVehicle.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(_selID)
                                off_stats['currentVehInvID'] = _selID
                        except Exception as _sve:
                            print '[OFFLINE] selectVehicle wrapper sync failed: %s' % _sve
                        return

                    CurrentVehicle.g_currentVehicle.selectVehicle = _offline_cv_selectVehicle
                    print '[OFFLINE] g_currentVehicle.selectVehicle wrapped OK (0.9.21 native + wrapper sync)'
                except Exception as _svwe:
                    print '[OFFLINE] g_currentVehicle.selectVehicle wrap failed: %s' % _svwe

                def call_smart(cb, data):''',
'09-select-helper')

# ------------------------------------------------- setCurrentVehicle mock
rep("""                        elif method_name == 'setCurrentVehicle':
                            invID = args[0] if args else None
                            if invID is not None and invID in veh_cds:
                                import CurrentVehicle
                                CurrentVehicle.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(invID)
                                off_stats['currentVehInvID'] = invID
                                BigWorld.callback(0.05, CurrentVehicle.g_currentVehicle.onChanged)
                            res = 0""",
"""                        elif method_name == 'setCurrentVehicle':
                            invID = args[0] if args else None
                            if invID is not None and invID in veh_cds:
                                _offline_select_vehicle(invID)
                            res = 0""",
'10-setcurrent')

# ------------------------------------------------------------ equipTurret
rep("""                    def fake_equipTurret(self, vehicleInvID, itemCompDescr, slotIdx, callback=None):
                        try:
                            from items import vehicles as _vmodTr
                            _base_cd_tr = veh_cds.get(vehicleInvID)
                            if _base_cd_tr is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_tr = _vmodTr.VehicleDescr(compactDescr=_base_cd_tr)
                            _descr_tr.installTurret(itemCompDescr, slotIdx)""",
"""                    def fake_equipTurret(self, vehicleInvID, itemCompDescr, gunCompDescr=0, callback=None):
                        # 0.9.21: Inventory.equipTurret(vehInvID, turretCD, gunCD, cb)
                        # and VehicleDescr.installTurret(turretCD, gunCD) - gunCD
                        # may be 0 to keep the currently installed gun.
                        if callable(gunCompDescr) and callback is None:
                            callback = gunCompDescr
                            gunCompDescr = 0
                        try:
                            from items import vehicles as _vmodTr
                            _base_cd_tr = veh_cds.get(vehicleInvID)
                            if _base_cd_tr is None:
                                raise ValueError('No veh_cds for invID=%d' % vehicleInvID)
                            _descr_tr = _vmodTr.VehicleDescr(compactDescr=_base_cd_tr)
                            _descr_tr.installTurret(itemCompDescr, gunCompDescr or 0)""",
'11-equipturret')

rep("""                            print '[OFFLINE] equipTurret OK: invID=%d cd=%d slot=%d' % (vehicleInvID, itemCompDescr, slotIdx)""",
"""                            print '[OFFLINE] equipTurret OK: invID=%d cd=%d gun=%d' % (vehicleInvID, itemCompDescr, gunCompDescr or 0)""",
'12-equipturret-print')

# ------------------------------------------------------- extra Shop fakes
rep("""                        shop_obj.buy = new.instancemethod(fake_shopBuy, shop_obj, shop_obj.__class__)""",
"""                        shop_obj.buy = new.instancemethod(fake_shopBuy, shop_obj, shop_obj.__class__)

                        def _offline_cb3(callback, ext=None):
                            \"\"\"Calls a Shop-style callback(resultID, errorStr, ext).\"\"\"
                            if not callback:
                                return

                            def _call(_cb=callback, _ext=ext):
                                try:
                                    _cb(0, '', _ext if _ext is not None else {})
                                except TypeError:
                                    try:
                                        _cb(0)
                                    except Exception:
                                        pass
                                return

                            BigWorld.callback(0.01, _call)
                            return

                        def fake_buyAndEquipItem(self, vehInvID, compDescr, slotIdx, isPaidRemoval=False, gunCompDescr=0, callback=None):
                            # 0.9.21 Shop.buyAndEquipItem - used by module processors
                            # when the item is not in the depot. Everything is in
                            # the depot offline, but keep a real implementation.
                            if callable(gunCompDescr) and callback is None:
                                callback = gunCompDescr
                                gunCompDescr = 0
                            if callable(isPaidRemoval) and callback is None:
                                callback = isPaidRemoval
                                isPaidRemoval = False
                            try:
                                from items import vehicles as _vmodBE
                                from items import getTypeOfCompactDescr as _gtocd
                                _base_cd_be = veh_cds.get(vehInvID)
                                if _base_cd_be is None:
                                    raise ValueError('No veh_cds for invID=%d' % vehInvID)
                                _descr_be = _vmodBE.VehicleDescr(compactDescr=_base_cd_be)
                                _type_be = _gtocd(compDescr)
                                if _type_be == _TURRET_IDX:
                                    _descr_be.installTurret(compDescr, gunCompDescr or 0)
                                elif _type_be == _OPTDEV_IDX:
                                    _descr_be.installOptionalDevice(compDescr, slotIdx or 0)
                                else:
                                    _descr_be.installComponent(compDescr)
                                new_cd = _descr_be.makeCompactDescr()
                                veh_cds[vehInvID] = new_cd
                                inv_vehicles[vehInvID] = new_cd
                                _refresh_ir_cache(vehInvID, new_cd)
                                from CurrentVehicle import g_currentVehicle as _gcv_be
                                if _gcv_be.invID == vehInvID:
                                    _gcv_be._CurrentVehicle__vehicle = OfflineVehicleWrapper(vehInvID)
                                BigWorld.callback(0.05, (lambda : _gcv_be.update()))
                                print '[OFFLINE] buyAndEquipItem OK: invID=%d cd=%d' % (vehInvID, compDescr)
                            except Exception as _bee:
                                print '[OFFLINE] buyAndEquipItem error: %s' % _bee
                            _offline_cb3(callback)
                            return

                        shop_obj.buyAndEquipItem = new.instancemethod(fake_buyAndEquipItem, shop_obj, shop_obj.__class__)

                        def fake_buyAndEquipTankman(self, vehInvID, slot, tmanCostTypeIdx=2, callback=None):
                            if callable(tmanCostTypeIdx) and callback is None:
                                callback = tmanCostTypeIdx
                                tmanCostTypeIdx = 2
                            try:
                                from items import tankmen as _tm_be
                                _descr_bt = vehicles.VehicleDescr(compactDescr=veh_cds[vehInvID])
                                _nation_bt = _descr_bt.type.id[0]
                                _vehType_bt = _descr_bt.type.id[1]
                                _role_bt = _descr_bt.type.crewRoles[slot][0]
                                _passport_bt = _tm_be.generatePassport(_nation_bt)
                                _tman_cd_bt = _tm_be.generateCompactDescr(_passport_bt, _vehType_bt, _role_bt, 100)
                                _new_tid_bt = max(list(t_cache.keys()) + [99]) + 1
                                t_cache[_new_tid_bt] = _tman_cd_bt
                                _crew_bt = list(crew_map.get(vehInvID, []))
                                if 0 <= slot < len(_crew_bt):
                                    _old_bt = _crew_bt[slot]
                                    if _old_bt is not None:
                                        t_in_veh.pop(_old_bt, None)
                                    _crew_bt[slot] = _new_tid_bt
                                else:
                                    _crew_bt.append(_new_tid_bt)
                                t_in_veh[_new_tid_bt] = vehInvID
                                crew_map[vehInvID] = _crew_bt
                                print '[OFFLINE] buyAndEquipTankman OK: veh=%d slot=%d tman=%d' % (vehInvID, slot, _new_tid_bt)
                            except Exception as _bte:
                                print '[OFFLINE] buyAndEquipTankman error: %s' % _bte
                            _offline_cb3(callback)
                            return

                        shop_obj.buyAndEquipTankman = new.instancemethod(fake_buyAndEquipTankman, shop_obj, shop_obj.__class__)

                        def fake_buyGoodie(self, goodieID, count=1, forGold=False, callback=None):
                            if callable(forGold) and callback is None:
                                callback = forGold
                            print '[OFFLINE] shop.buyGoodie: goodieID=%r count=%r (no-op success)' % (goodieID, count)
                            if callback:
                                BigWorld.callback(0.01, (lambda cb=callback: call_smart(cb, 0)))
                            return

                        shop_obj.buyGoodie = new.instancemethod(fake_buyGoodie, shop_obj, shop_obj.__class__)

                        def fake_tradeInVehicle(self, vehInvID, nationIdx, innationIdx, buyShells=False, recruitCrew=False, tmanCostTypeIdx=0, callback=None):
                            # All vehicles are already in the garage offline;
                            # report success without changing anything.
                            print '[OFFLINE] shop.tradeInVehicle: vehInvID=%r -> (%r,%r) (no-op success)' % (vehInvID, nationIdx, innationIdx)
                            if callback:
                                BigWorld.callback(0.01, (lambda cb=callback: cb(0)))
                            return

                        shop_obj.tradeInVehicle = new.instancemethod(fake_tradeInVehicle, shop_obj, shop_obj.__class__)

                        def fake_sellCustomizations(self, vehInvID, itemCD, count=1, callback=None):
                            print '[OFFLINE] shop.sellCustomizations: vehInvID=%r itemCD=%r count=%r (no-op success)' % (vehInvID, itemCD, count)
                            _offline_cb3(callback)
                            return

                        shop_obj.sellCustomizations = new.instancemethod(fake_sellCustomizations, shop_obj, shop_obj.__class__)""",
'13-shop-fakes')

# --------------------------------------------------------- p.selectVehicle
rep("""                p.selectVehicle = lambda invID: (
                 setattr(CurrentVehicle.g_currentVehicle, '_CurrentVehicle__vehicle', OfflineVehicleWrapper(invID)),
                 CurrentVehicle.g_currentVehicle.onChanged(),
                 None)[2]""",
"""                p.selectVehicle = _offline_select_vehicle""",
'14-p-selectvehicle')

# ---------------------------------------- initial vehicle + state + NY init
rep("""                if 1 in veh_cds:
                    CurrentVehicle.g_currentVehicle._CurrentVehicle__vehicle = OfflineVehicleWrapper(1)""",
"""                if 1 in veh_cds:
                    _offline_select_vehicle(1)
                try:
                    def _offline_resync_inventory():
                        try:
                            from PlayerEvents import g_playerEvents as _pe_r
                            _pe_r.onClientUpdated({'inventory': {}, 'stats': {}}, False)
                            _pe_r.onInventoryResync()
                        except Exception as _re2:
                            print '[OFFLINE] resync_inventory failed: %s' % _re2
                        return

                    _OFFLINE_STATE.update({'player': p,
                     'off_stats': off_stats,
                     'veh_cds': veh_cds,
                     'inv_vehicles': inv_vehicles,
                     'my_garage': my_garage,
                     't_cache': t_cache,
                     't_in_veh': t_in_veh,
                     'crew_map': crew_map,
                     'resync_inventory': _offline_resync_inventory,
                     'select_vehicle': _offline_select_vehicle})
                    print '[OFFLINE] offline state exposed for NewYearServer'
                except Exception as _se:
                    print '[OFFLINE] offline state expose failed: %s' % _se

                try:
                    from Offline import NewYearServer as _NYS_init
                    _NYS_init.on_account_ready(p)
                    print '[OFFLINE] NewYearServer initialized (Holiday Ops 2018 offline event is IN_PROGRESS)'
                except Exception as _nye:
                    print '[OFFLINE] NewYearServer init failed: %s' % _nye
                    traceback.print_exc()""",
'15-state-ny-init')

# ---------------------------------------------------------------- showGUI
rep("""                        ctx = cPickle.dumps({'databaseID': 1})""",
"""                        ctx = cPickle.dumps({'databaseID': 1, 'currentVehInvID': 1})""",
'16-showgui-ctx')

rep("""                        BigWorld.callback(0.5, _initial_stats_resync)""",
"""                        BigWorld.callback(0.5, _initial_stats_resync)
                        BigWorld.callback(1.0, (lambda _oid=off_stats.get('currentVehInvID', 1): _offline_select_vehicle(_oid)))""",
'17-delayed-select')

# -------------------------------------------- AmmunitionPanel slots fix
# The decompiled 0.9.22 function lost its final `else:` (uncompyle6 artifact):
# after the artefact/booster branches it unconditionally built one more VO
# with slotId=None. 0.9.22's FittingSlotVO tolerated an empty modulesData,
# 0.9.21's does `modulesData[0]` -> IndexError during Hangar._populate when
# the vehicle has no optional devices/equipment/boosters installed.
# Restore the if/elif/else structure and never pass an empty list with
# slotId=None (emit a plain empty-VO dict instead).
rep("""        def _safe_getFittingSlotsData(vehicle, slotsRange, VoClass=None, itemsCache=None):
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

                elif slotType in _amp_mod._BOOSTERS_SLOTS:
                    for slotId in xrange(_BBLS):
                        devices.append(VoClass(slotItems, vehicle, slotType, slotId, tooltipType=_TTC.BATTLE_BOOSTER))

                elif not slotItems and slotType in _MODULE_ATTR_BY_SLOT_TYPE:
                    try:
                        installedCD = getattr(vehicle, _MODULE_ATTR_BY_SLOT_TYPE[slotType]).intCD
                        installedItem = itemsCache.items.getItemByCD(installedCD)
                        if installedItem is not None:
                            slotItems = [
                             installedItem]
                    except Exception as _mfe:
                        print '[OFFLINE] getFittingSlotsData fallback failed for %s: %s' % (slotType, _mfe)

                devices.append(VoClass(slotItems, vehicle, slotType, tooltipType=_TTC.HANGAR_MODULE))

            return devices""",
"""        def _safe_getFittingSlotsData(vehicle, slotsRange, VoClass=None, itemsCache=None):
            devices = []
            VoClass = VoClass or _FSVO
            itemTypeIDs = [_GTI[slotType] for slotType in slotsRange]
            gatheredItems = itemsCache.items.getItemsEx(itemTypeIDs, _RC.CUSTOM((lambda item: item.isInstalled(vehicle))), vehicle.nationID)

            def _empty_module_vo(slotType):
                # 0.9.21 FittingSlotVO._prepareModule() unconditionally does
                # modulesData[0] when slotId is None, so an empty module
                # slot must not go through VoClass; emit the same dict the
                # VO produces for "no module" instead.
                return {'id': -1,
                 'tooltip': '',
                 'tooltipType': _TTC.COMPLEX,
                 'slotType': slotType,
                 'removable': True,
                 'slotIndex': 0,
                 'level': 0,
                 'moduleLabel': 'empty'}

            for slotType in slotsRange:
                slotTypeIdx = _GTI[slotType]
                slotItems = [item for item in gatheredItems.itervalues() if item.itemTypeID == slotTypeIdx]
                if slotType in _amp_mod.ARTEFACTS_SLOTS:
                    for slotId in xrange(_NODS):
                        devices.append(VoClass(slotItems, vehicle, slotType, slotId, _TTC.HANGAR_MODULE))

                elif slotType in _amp_mod._BOOSTERS_SLOTS:
                    for slotId in xrange(_BBLS):
                        devices.append(VoClass(slotItems, vehicle, slotType, slotId, tooltipType=_TTC.BATTLE_BOOSTER))

                else:
                    if not slotItems and slotType in _MODULE_ATTR_BY_SLOT_TYPE:
                        try:
                            installedCD = getattr(vehicle, _MODULE_ATTR_BY_SLOT_TYPE[slotType]).intCD
                            installedItem = itemsCache.items.getItemByCD(installedCD)
                            if installedItem is not None:
                                slotItems = [
                                 installedItem]
                        except Exception as _mfe:
                            print '[OFFLINE] getFittingSlotsData fallback failed for %s: %s' % (slotType, _mfe)

                    if slotItems:
                        devices.append(VoClass(slotItems, vehicle, slotType, tooltipType=_TTC.HANGAR_MODULE))
                    else:
                        devices.append(_empty_module_vo(slotType))

            return devices""",
'18-fitting-slots-else')

open(path, 'w').write(src)
print('ALL PORT EDITS APPLIED')
