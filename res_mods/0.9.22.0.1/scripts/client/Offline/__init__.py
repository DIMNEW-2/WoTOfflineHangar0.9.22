# -*- coding: utf-8 -*-
# Offline package of WoTOfflineHangar0.9.22.
#
# Importing this package also arms the offline New Year event
# («Новогоднее наступление», NY 2018) from Offline.NewYearEvent.  The actual
# patching is deferred by one game tick so it runs AFTER Offline.Manager has
# installed its own account patches (Manager is imported right after this
# package by the login view).
try:
    from Offline import NewYearEvent as _NewYearEvent
    try:
        import BigWorld
        BigWorld.callback(0, _NewYearEvent.init)
    except Exception:
        _NewYearEvent.init()
except Exception as _nyImportError:
    try:
        print '[OFFLINE-NY] bootstrap failed: %s' % _nyImportError
    except Exception:
        pass
