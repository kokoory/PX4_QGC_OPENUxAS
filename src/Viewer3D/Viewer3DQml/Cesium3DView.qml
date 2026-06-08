import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtWebEngine
import QtWebChannel
import QtPositioning

import QGroundControl
import QGroundControl.Controls
import QGroundControl.FlyView

Item {
    id: root

    property var activeVehicle: QGroundControl.multiVehicleManager.activeVehicle
    property var planMasterController: null
    property var _missionController: planMasterController ? planMasterController.missionController : null
    property var _visualItems: _missionController ? _missionController.visualItems : null
    property bool cesiumReady: false
    property string cesiumToken: QGroundControl.settingsManager.appSettings.cesiumToken.rawValue
    property string vworldToken: QGroundControl.settingsManager.appSettings.vworldToken.rawValue

    // Convert the QGC 2D flight-map zoom (web-mercator zoom level) to a
    // rough Cesium camera height so the 3D view opens framed like the 2D map.
    // flightMapZoom can still be at its uninitialised default (~2) the moment
    // the 3D view opens, which would put the camera in space — guard against
    // that and clamp to a sane 300 m .. 200 km range.
    function _zoomToHeight(zoom) {
        if (!zoom || zoom < 4) return 3000;
        var h = 35000000.0 / Math.pow(2, zoom - 3);
        return Math.max(300, Math.min(h, 200000));
    }

    // Click context for guided actions
    property var _clickCoord: QtPositioning.coordinate()

    WebEngineView {
        id: webView
        anchors.fill: parent
        url: "qrc:/qml/QGroundControl/Viewer3D/Cesium3DView.html"
        backgroundColor: "black"

        settings.localContentCanAccessRemoteUrls: true
        settings.javascriptEnabled: true
        settings.localStorageEnabled: true

        webChannel: channel

        onJavaScriptConsoleMessage: function(level, message, lineNumber, sourceID) {
            // Surface real errors/warnings; ignore the routine tile-load misses
            // VWorld emits at the edge of its Korea-only coverage.
            if (level >= 1 && message.indexOf("Failed to obtain image tile") === -1) {
                console.warn("[Cesium] " + message)
            }
        }

        onLoadingChanged: function(loadRequest) {
            if (loadRequest.status === WebEngineView.LoadSucceededStatus) {
                // Open the 3D view on the same location the 2D map is showing.
                var pos = QGroundControl.flightMapPosition
                var lat = (pos && pos.isValid && (pos.latitude !== 0 || pos.longitude !== 0))
                            ? pos.latitude : 34.61167
                var lon = (pos && pos.isValid && (pos.latitude !== 0 || pos.longitude !== 0))
                            ? pos.longitude : 127.206028
                var height = _zoomToHeight(QGroundControl.flightMapZoom)
                webView.runJavaScript('initCesium("' + cesiumToken + '","' + vworldToken
                                      + '",' + lon + ',' + lat + ',' + height + ')');
                cesiumReady = true;
            }
        }
    }

    // NOTE: the search-planning panel lives in the Cesium HTML page, not as a
    // QML overlay. The WebEngineView renders in its own compositor layer and
    // occludes sibling QML items (Popups included), so an in-page HTML panel
    // is the only thing reliably drawn over the globe. The panel calls back
    // into qgcBridge.publishSearch() to broadcast the UxAS request.

    QtObject {
        id: qgcBridge

        WebChannel.id: "qgcBridge"

        function cesiumReady() {
            root.cesiumReady = true;
        }

        // Generic VWorld Data API fetch for the Cesium page. The VWorld Data
        // API sends no CORS header, so the WebEngine page can't fetch it
        // directly — QML's XMLHttpRequest is not CORS-restricted, so we do the
        // request here and hand the parsed feature array to the named page
        // callback (renderVWorldBuildings / renderVWorldRoads / ...).
        function requestVWorldLayer(layer, w, s, e, n, jsCallback) {
            if (!root.vworldToken) return;
            var box = "BOX(" + w + "," + s + "," + e + "," + n + ")";
            var url = "https://api.vworld.kr/req/data?service=data&version=2.0"
                    + "&request=GetFeature&format=json&size=1000&page=1&data=" + layer
                    + "&geometry=true&attribute=true&crs=EPSG:4326"
                    + "&geomFilter=" + box
                    + "&key=" + root.vworldToken + "&domain=localhost"
            var xhr = new XMLHttpRequest()
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE) return
                if (xhr.status !== 200) {
                    console.warn("VWorld " + layer + " HTTP " + xhr.status)
                    return
                }
                var feats = []
                try {
                    var resp = JSON.parse(xhr.responseText).response
                    if (resp && resp.status === "OK") {
                        feats = resp.result.featureCollection.features || []
                    }
                } catch (err) {
                    console.warn("VWorld " + layer + " parse error: " + err)
                    return
                }
                webView.runJavaScript(jsCallback + '('
                    + JSON.stringify(JSON.stringify(feats)) + ')')
            }
            xhr.open("GET", url)
            xhr.send()
        }

        // Called from JS on right-click
        function mapClicked(lat, lon, alt, screenX, screenY) {
            root._clickCoord = QtPositioning.coordinate(lat, lon, alt);
            contextMenu.x = screenX;
            contextMenu.y = screenY;
            contextMenu.open();
        }

        // Called from the in-page HTML planning panel to publish a UxAS search.
        // The page passes the chosen centre + a JSON params blob; we broadcast
        // it on EventBroadcaster (UDP 45678) for uxas_search_listener.py.
        function publishSearch(kind, lat, lon, paramsJson) {
            var p = {}
            try { p = JSON.parse(paramsJson) } catch (e) { p = {} }
            var data = {
                "center_lat":    lat,
                "center_lon":    lon,
                "vehicles":      p.vehicles || "1",
                "altitude":      p.altitude || 100,
                "region_radius": p.region_radius || 3000,
                "register_from_config": p.register_from_config || ""
            }
            if (kind === "area") {
                // drawn polygon takes priority; else width×height rectangle
                if (p.polygon !== undefined) data["polygon"] = p.polygon
                data["width_m"]  = p.width_m || 1000
                data["height_m"] = p.height_m || 1000
            } else {
                // road/river: selected names (or "ALL") + the exact scan bbox
                data["names"] = p.names || "ALL"
                if (p.bbox !== undefined) data["bbox"] = p.bbox    // [w,s,e,n]
                data["half_size_m"] = p.half_size_m || 1000
            }
            EventBroadcaster.sendEvent("uxas_search", kind, data)
        }
    }

    WebChannel {
        id: channel
        registeredObjects: [qgcBridge]
    }

    // ---------------------------------------------------------------
    // Right-click context menu for guided actions
    // ---------------------------------------------------------------
    Menu {
        id: contextMenu

        MenuItem {
            text: qsTr("Go to location")
            enabled: activeVehicle && activeVehicle.armed && activeVehicle.flying
            onTriggered: {
                webView.runJavaScript(
                    'showGotoIndicator(' + _clickCoord.latitude + ',' + _clickCoord.longitude + ',' + _clickCoord.altitude + ')'
                );
                globals.guidedControllerFlyView.confirmAction(
                    globals.guidedControllerFlyView.actionGoto,
                    _clickCoord,
                    _gotoActionHelper
                );
            }
        }

        MenuItem {
            text: qsTr("Orbit at location")
            enabled: activeVehicle && activeVehicle.armed && activeVehicle.flying
            visible: globals.guidedControllerFlyView.showOrbit
            onTriggered: {
                globals.guidedControllerFlyView.confirmAction(
                    globals.guidedControllerFlyView.actionOrbit,
                    _clickCoord
                );
            }
        }

        MenuItem {
            text: qsTr("ROI at location")
            enabled: activeVehicle && activeVehicle.armed && activeVehicle.flying
            visible: globals.guidedControllerFlyView.showROI
            onTriggered: {
                globals.guidedControllerFlyView.executeAction(
                    globals.guidedControllerFlyView.actionROI,
                    _clickCoord, 0, false
                );
            }
        }

        MenuItem {
            text: qsTr("Set home here")
            enabled: activeVehicle
            onTriggered: {
                globals.guidedControllerFlyView.confirmAction(
                    globals.guidedControllerFlyView.actionSetHome,
                    _clickCoord
                );
            }
        }

        MenuSeparator {}

        MenuItem {
            text: qsTr("Lat: %1  Lon: %2").arg(_clickCoord.latitude.toFixed(6)).arg(_clickCoord.longitude.toFixed(6))
            enabled: false
        }
    }

    // Helper for goto action confirm/cancel callbacks
    QtObject {
        id: _gotoActionHelper

        function actionConfirmed() {
            // Keep indicator visible; will hide when vehicle exits goto mode
        }

        function actionCancelled() {
            webView.runJavaScript('hideGotoIndicator()');
        }
    }

    // Hide goto indicator when vehicle exits goto flight mode
    Connections {
        target: activeVehicle
        function onFlightModeChanged() {
            if (activeVehicle && activeVehicle.flightMode !== activeVehicle.gotoFlightMode) {
                webView.runJavaScript('hideGotoIndicator()');
            }
        }
    }

    // ---------------------------------------------------------------
    // Vehicle position tracking
    // ---------------------------------------------------------------
    Timer {
        interval: 100
        running: cesiumReady && activeVehicle !== null
        repeat: true
        onTriggered: {
            if (!activeVehicle || !activeVehicle.coordinate.isValid) return;

            var lat = activeVehicle.coordinate.latitude;
            var lon = activeVehicle.coordinate.longitude;
            var alt = activeVehicle.altitudeRelative.rawValue || 0;
            var heading = activeVehicle.heading.rawValue || 0;
            var id = activeVehicle.id;

            webView.runJavaScript(
                'updateVehiclePosition(' + id + ',' + lat + ',' + lon + ',' + alt + ',' + heading + ')'
            );
        }
    }

    // ---------------------------------------------------------------
    // Trajectory tracking
    // ---------------------------------------------------------------
    Connections {
        target: activeVehicle ? activeVehicle.trajectoryPoints : null
        function onPointAdded(coordinate) {
            if (!cesiumReady) return;
            webView.runJavaScript(
                'addTrajectoryPoint(' + coordinate.latitude + ',' + coordinate.longitude + ',' + (coordinate.altitude || 0) + ')'
            );
        }
        function onPointsCleared() {
            if (!cesiumReady) return;
            webView.runJavaScript('clearTrajectory()');
        }
    }

    // ---------------------------------------------------------------
    // Home position tracking
    // ---------------------------------------------------------------
    Connections {
        target: activeVehicle
        function onHomePositionChanged() {
            if (!cesiumReady || !activeVehicle || !activeVehicle.homePosition.isValid) return;
            var h = activeVehicle.homePosition;
            webView.runJavaScript(
                'setHomePosition(' + h.latitude + ',' + h.longitude + ',' + (h.altitude || 0) + ')'
            );
        }
    }

    // ---------------------------------------------------------------
    // Mission items (waypoints) visualization
    // ---------------------------------------------------------------
    function _updateMissionItems() {
        if (!cesiumReady || !_visualItems) return;

        var items = [];
        for (var i = 0; i < _visualItems.count; i++) {
            var item = _visualItems.get(i);
            if (item && item.specifiesCoordinate && item.coordinate.isValid) {
                items.push({
                    lat: item.coordinate.latitude,
                    lon: item.coordinate.longitude,
                    alt: item.coordinate.altitude || 0,
                    seq: item.sequenceNumber,
                    current: item.isCurrentItem
                });
            }
        }

        webView.runJavaScript('setMissionItems(\'' + JSON.stringify(items) + '\')');
    }

    Connections {
        target: _visualItems
        function onCountChanged() { _updateMissionItems() }
        function onDirtyChanged() { _updateMissionItems() }
    }

    Connections {
        target: _missionController
        function onVisualItemsChanged() { _updateMissionItems() }
    }

    onCesiumReadyChanged: {
        if (cesiumReady) _updateMissionItems()
    }

    // ---------------------------------------------------------------
    // Fly to vehicle on first valid coordinate
    // ---------------------------------------------------------------
    property bool _initialFlyDone: false
    onActiveVehicleChanged: {
        _initialFlyDone = false;
    }

    Timer {
        interval: 2000
        running: cesiumReady && activeVehicle !== null && !_initialFlyDone
        repeat: false
        onTriggered: {
            if (activeVehicle && activeVehicle.coordinate.isValid) {
                var lat = activeVehicle.coordinate.latitude;
                var lon = activeVehicle.coordinate.longitude;
                webView.runJavaScript('flyToLocation(' + lat + ',' + lon + ', 500)');
                _initialFlyDone = true;
            }
        }
    }
}
