import QtQuick
import QtQuick.Controls
import QtLocation
import QtPositioning
import QtQuick.Dialogs
import QtQuick.Layouts

import QGroundControl
import QGroundControl.Controls
import QGroundControl.FlyView
import QGroundControl.FlightMap
import QGroundControl.PlanView

FlightMap {
    id:                         _root
    allowGCSLocationCenter:     true
    allowVehicleLocationCenter: !_keepVehicleCentered
    planView:                   false
    zoomLevel:                  QGroundControl.flightMapZoom
    center:                     QGroundControl.flightMapPosition

    property Item   pipView
    property Item   pipState:                   _pipState
    property var    rightPanelWidth
    property var    planMasterController
    property bool   pipMode:                    false   // true: map is shown in a small pip mode
    property var    toolInsets                          // Insets for the center viewport area

    property var    _activeVehicle:             QGroundControl.multiVehicleManager.activeVehicle
    property var    _planMasterController:      planMasterController
    property var    _geoFenceController:        planMasterController.geoFenceController
    property var    _rallyPointController:      planMasterController.rallyPointController
    property var    _activeVehicleCoordinate:   _activeVehicle ? _activeVehicle.coordinate : QtPositioning.coordinate()
    property real   _toolButtonTopMargin:       parent.height - mainWindow.height + (ScreenTools.defaultFontPixelHeight / 2)
    property real   _toolsMargin:               ScreenTools.defaultFontPixelWidth * 0.75
    property var    _flyViewSettings:           QGroundControl.settingsManager.flyViewSettings
    property bool   _keepMapCenteredOnVehicle:  _flyViewSettings.keepMapCenteredOnVehicle.rawValue

    property bool   _disableVehicleTracking:    false
    property bool   _keepVehicleCentered:       pipMode ? true : false
    property bool   _saveZoomLevelSetting:      true

    function _adjustMapZoomForPipMode() {
        _saveZoomLevelSetting = false
        if (pipMode) {
            if (QGroundControl.flightMapZoom > 3) {
                zoomLevel = QGroundControl.flightMapZoom - 3
            }
        } else {
            zoomLevel = QGroundControl.flightMapZoom
        }
        _saveZoomLevelSetting = true
    }

    onPipModeChanged: _adjustMapZoomForPipMode()

    onVisibleChanged: {
        if (visible) {
            // Synchronize center position with Plan View
            center = QGroundControl.flightMapPosition
        }
    }

    onZoomLevelChanged: {
        if (_saveZoomLevelSetting) {
            QGroundControl.flightMapZoom = _root.zoomLevel
        }
    }
    onCenterChanged: {
        QGroundControl.flightMapPosition = _root.center
    }

    // We track whether the user has panned or not to correctly handle automatic map positioning
    onMapPanStart:  _disableVehicleTracking = true
    onMapPanStop:   panRecenterTimer.restart()

    function pointInRect(point, rect) {
        return point.x > rect.x &&
                point.x < rect.x + rect.width &&
                point.y > rect.y &&
                point.y < rect.y + rect.height;
    }

    property real _animatedLatitudeStart
    property real _animatedLatitudeStop
    property real _animatedLongitudeStart
    property real _animatedLongitudeStop
    property real animatedLatitude
    property real animatedLongitude

    onAnimatedLatitudeChanged: _root.center = QtPositioning.coordinate(animatedLatitude, animatedLongitude)
    onAnimatedLongitudeChanged: _root.center = QtPositioning.coordinate(animatedLatitude, animatedLongitude)

    NumberAnimation on animatedLatitude { id: animateLat; from: _animatedLatitudeStart; to: _animatedLatitudeStop; duration: 1000 }
    NumberAnimation on animatedLongitude { id: animateLong; from: _animatedLongitudeStart; to: _animatedLongitudeStop; duration: 1000 }

    function animatedMapRecenter(fromCoord, toCoord) {
        _animatedLatitudeStart = fromCoord.latitude
        _animatedLongitudeStart = fromCoord.longitude
        _animatedLatitudeStop = toCoord.latitude
        _animatedLongitudeStop = toCoord.longitude
        animateLat.start()
        animateLong.start()
    }

    // returns the rectangle formed by the four center insets
    // used for checking if vehicle is under ui, and as a target for recentering the view
    function _insetCenterRect() {
        return Qt.rect(toolInsets.leftEdgeCenterInset,
                       toolInsets.topEdgeCenterInset,
                       _root.width - toolInsets.leftEdgeCenterInset - toolInsets.rightEdgeCenterInset,
                       _root.height - toolInsets.topEdgeCenterInset - toolInsets.bottomEdgeCenterInset)
    }

    // returns the four rectangles formed by the 8 corner insets
    // used for detecting if the vehicle has flown under the instrument panel, virtual joystick etc
    function _insetCornerRects() {
        var rects = {
        "topleft":      Qt.rect(0,0,
                               toolInsets.leftEdgeTopInset,
                               toolInsets.topEdgeLeftInset),
        "topright":     Qt.rect(_root.width-toolInsets.rightEdgeTopInset,0,
                               toolInsets.rightEdgeTopInset,
                               toolInsets.topEdgeRightInset),
        "bottomleft":   Qt.rect(0,_root.height-toolInsets.bottomEdgeLeftInset,
                               toolInsets.leftEdgeBottomInset,
                               toolInsets.bottomEdgeLeftInset),
        "bottomright":  Qt.rect(_root.width-toolInsets.rightEdgeBottomInset,_root.height-toolInsets.bottomEdgeRightInset,
                               toolInsets.rightEdgeBottomInset,
                               toolInsets.bottomEdgeRightInset)}
        return rects
    }

    function recenterNeeded() {
        var vehiclePoint = _root.fromCoordinate(_activeVehicleCoordinate, false /* clipToViewport */)
        var centerRect = _insetCenterRect()
        //return !pointInRect(vehiclePoint,insetRect)

        // If we are outside the center inset rectangle, recenter
        if(!pointInRect(vehiclePoint, centerRect)){
            return true
        }

        // if we are inside the center inset rectangle
        // then additionally check if we are underneath one of the corner inset rectangles
        var cornerRects = _insetCornerRects()
        if(pointInRect(vehiclePoint, cornerRects["topleft"])){
            return true
        } else if(pointInRect(vehiclePoint, cornerRects["topright"])){
            return true
        } else if(pointInRect(vehiclePoint, cornerRects["bottomleft"])){
            return true
        } else if(pointInRect(vehiclePoint, cornerRects["bottomright"])){
            return true
        }

        // if we are inside the center inset rectangle, and not under any corner elements
        return false
    }

    function updateMapToVehiclePosition() {
        if (animateLat.running || animateLong.running) {
            return
        }
        // We let FlightMap handle first vehicle position
        if (!_keepMapCenteredOnVehicle && firstVehiclePositionReceived && _activeVehicleCoordinate.isValid && !_disableVehicleTracking) {
            if (_keepVehicleCentered) {
                _root.center = _activeVehicleCoordinate
            } else {
                if (firstVehiclePositionReceived && recenterNeeded()) {
                    // Move the map such that the vehicle is centered within the inset area
                    var vehiclePoint = _root.fromCoordinate(_activeVehicleCoordinate, false /* clipToViewport */)
                    var centerInsetRect = _insetCenterRect()
                    var centerInsetPoint = Qt.point(centerInsetRect.x + centerInsetRect.width / 2, centerInsetRect.y + centerInsetRect.height / 2)
                    var centerOffset = Qt.point((_root.width / 2) - centerInsetPoint.x, (_root.height / 2) - centerInsetPoint.y)
                    var vehicleOffsetPoint = Qt.point(vehiclePoint.x + centerOffset.x, vehiclePoint.y + centerOffset.y)
                    var vehicleOffsetCoord = _root.toCoordinate(vehicleOffsetPoint, false /* clipToViewport */)
                    animatedMapRecenter(_root.center, vehicleOffsetCoord)
                }
            }
        }
    }

    on_ActiveVehicleCoordinateChanged: {
        if (_keepMapCenteredOnVehicle && _activeVehicleCoordinate.isValid && !_disableVehicleTracking) {
            _root.center = _activeVehicleCoordinate
        }
    }

    PipState {
        id:         _pipState
        pipView:    _root.pipView
        isDark:     _isFullWindowItemDark
    }

    Timer {
        id:         panRecenterTimer
        interval:   10000
        running:    false
        onTriggered: {
            _disableVehicleTracking = false
            updateMapToVehiclePosition()
        }
    }

    Timer {
        interval:       500
        running:        true
        repeat:         true
        onTriggered:    updateMapToVehiclePosition()
    }

    QGCMapPalette { id: mapPal; lightColors: isSatelliteMap }

    Connections {
        target:                 _missionController
        ignoreUnknownSignals:   true
        function onNewItemsFromVehicle() {
            var visualItems = _missionController.visualItems
            if (visualItems && visualItems.count !== 1) {
                mapFitFunctions.fitMapViewportToMissionItems()
                firstVehiclePositionReceived = true
            }
        }
    }

    MapFitFunctions {
        id:                         mapFitFunctions // The name for this id cannot be changed without breaking references outside of this code. Beware!
        map:                        _root
        usePlannedHomePosition:     false
        planMasterController:       _planMasterController
    }

    ObstacleDistanceOverlayMap {
        id: obstacleDistance
        showText: !pipMode
    }

    // Add trajectory lines to the map
    // ---------------------------------------------------------------
    // UxAS plan overlay mirrored from the 3D Cesium view
    // (EventBroadcaster.planOverlay = { area, roads, rivers } of [lat,lon]).
    // Lets the operator see what was planned in 3D on this 2D map too.
    // ---------------------------------------------------------------
    property var _uxPlan: EventBroadcaster.planOverlay
    function _uxPath(arr) {
        var p = []
        if (arr) for (var i = 0; i < arr.length; i++)
            p.push(QtPositioning.coordinate(arr[i][0], arr[i][1]))
        return p
    }

    MapPolygon {
        id:          uxAreaOverlay
        z:           QGroundControl.zOrderMapItems
        visible:     !pipMode && path.length >= 3
        color:       Qt.rgba(1, 1, 0, 0.15)
        border.color:"yellow"
        border.width:2
        path:        _root._uxPlan && _root._uxPlan.area ? _root._uxPath(_root._uxPlan.area) : []
    }
    // Selected roads (mirrored from planOverlay) — bright cyan, thicker, to
    // match the Cesium 3D panel's selection styling.
    MapItemView {
        model: (_root._uxPlan && _root._uxPlan.roads) ? _root._uxPlan.roads : []
        delegate: MapPolyline {
            z:          QGroundControl.zOrderMapItems + 1
            visible:    !pipMode
            line.width: 6
            line.color: "#00ffff"
            path:       _root._uxPath(modelData)
        }
    }
    // Selected rivers (mirrored from planOverlay) — bright cyan fill/outline.
    MapItemView {
        model: (_root._uxPlan && _root._uxPlan.rivers) ? _root._uxPlan.rivers : []
        delegate: MapPolygon {
            z:          QGroundControl.zOrderMapItems + 1
            visible:    !pipMode
            color:      Qt.rgba(0, 1, 1, 0.35)
            border.color:"#00ffff"
            border.width:3
            path:       _root._uxPath(modelData)
        }
    }

    // ---------------------------------------------------------------
    // UxAS-planned mission preview (LIVE, no PX4 round-trip needed)
    // EventBroadcaster.uxasPlannedWaypoints is a map keyed by vehicle id
    // (string) → list of [lat,lon,alt]. Drawn as a violet dashed polyline so
    // the operator sees what UxAS planned even if the bridge / PX4 upload is
    // delayed or unavailable.
    // ---------------------------------------------------------------
    property var _uxPlannedMap: EventBroadcaster.uxasPlannedWaypoints
    function _uxPlannedVehicleIds() {
        var m = _root._uxPlannedMap
        if (!m) return []
        return Object.keys(m).sort()
    }
    function _uxPlannedPath(vid) {
        var m = _root._uxPlannedMap
        if (!m || !m[vid]) return []
        var arr = m[vid]
        var p = []
        for (var i = 0; i < arr.length; i++)
            p.push(QtPositioning.coordinate(arr[i][0], arr[i][1]))
        return p
    }
    MapItemView {
        model: _root._uxPlannedVehicleIds()
        delegate: MapPolyline {
            z:          QGroundControl.zOrderMapItems + 2
            visible:    !pipMode
            line.width: Math.max(2, ScreenTools.defaultFontPixelHeight * 0.25)
            line.color: "#bb44ff"
            path:       _root._uxPlannedPath(modelData)
        }
    }

    // ---------------------------------------------------------------
    // UxAS Plan panel (2D) — operator selects VWorld roads/rivers directly
    // on this map, mirroring the existing Cesium 3D "UxAS planner" workflow.
    //  * Scan view  → XHR VWorld features in the visible bbox (≤9 km²)
    //  * candidates → faded MapPolyline/MapPolygon (orange / blue)
    //  * click a candidate (map or list) → toggle selection
    //  * selection mirrors via EventBroadcaster.planOverlay so the 3D view
    //    (and the cyan delegates above) reflect the same picks
    //  * Publish     → EventBroadcaster.sendEvent("uxas_search", ...)
    // ---------------------------------------------------------------
    property string _uxKind:        "area"          // "area" | "road" | "river"
    property var    _uxCandidates:  ({ road: ({}), river: ({}) })
    property var    _uxScanBox:     null            // {w,s,e,n,clat,clon}
    property string _uxStatus:      ""
    property int    _uxBump:        0               // bump to force MapItemView refresh
    readonly property real _uxVworldMaxKm2: 9.0
    property string _uxVworldToken: QGroundControl.settingsManager.appSettings.vworldToken.rawValue
    // --- 3D-parity shared state (fleet / sensor / area shape) ---
    readonly property var _uxX500Ids:   [1, 2, 3, 8, 9]   // multicopter pool (vehicles.json)
    readonly property var _uxCessnaIds: [4, 11]           // fixed-wing pool (RC Cessna + Cessna 2)
    readonly property var _uxRoverIds:  [7]               // ground rover pool (R1 Rover)
    property int    _uxNRover:      0                     // rovers in the SAR fleet
    // Heterogeneous SAR tiers: fixed-wing flies wide+high, multicopter close+low.
    property real   _uxFwAlt:       250                   // fixed-wing search altitude (m)
    property real   _uxMcAlt:       60                    // multicopter search altitude (m)
    property real   _uxFwFov:       45                    // fixed-wing FOV (wide)
    property real   _uxMcFov:       20                    // multicopter FOV (narrow)
    property int    _uxNX500:       1
    property int    _uxNCessna:     0
    property real   _uxFovDeg:      45                    // camera horizontal FOV (deg) — drives footprint + UxAS lane spacing
    property real   _uxOverlapPct:  20                    // camera image overlap % (lane spacing)
    property string _uxRiverMode:   "center"              // river path: center | bank | area
    property string _uxShape:       "rect"                // "rect" | "poly"
    property real   _uxRectW:       2000                  // metres
    property real   _uxRectH:       2000                  // metres
    property var    _uxDrawnPoly:   []                    // [[lat,lon], ...] drawn vertices
    property bool   _uxDrawing:     false                 // map clicks add polygon vertices
    property int    _uxCalcBump:    0                     // bump to refresh coverage readout

    function _uxKindNames(kind) {
        var d = _root._uxCandidates[kind] || {}
        return Object.keys(d).sort()
    }
    function _uxSelectedNames(kind) {
        return _uxKindNames(kind).filter(function (n) { return _root._uxCandidates[kind][n].selected })
    }
    function _uxAllPathsModel(kind, selectedOnly) {
        // Flat list of paths (each path = [QtPositioning.coordinate, ...])
        // Tied to _uxBump so the view refreshes when the dict mutates.
        var out = []; var _ = _root._uxBump
        var d = _root._uxCandidates[kind] || {}
        for (var nm in d) {
            if (selectedOnly && !d[nm].selected) continue
            if (!selectedOnly && d[nm].selected) continue
            var segs = d[nm].segs || []
            for (var i = 0; i < segs.length; i++) {
                var path = []
                for (var j = 0; j < segs[i].length; j++)
                    path.push(QtPositioning.coordinate(segs[i][j][0], segs[i][j][1]))
                out.push(path)
            }
        }
        return out
    }
    function _uxClampBbox(w, s, e, n) {
        var clat = (s + n) / 2, clon = (w + e) / 2
        var hM = (n - s) * 111320
        var wM = (e - w) * 111320 * Math.cos(clat * Math.PI / 180)
        var areaKm2 = (hM * wM) / 1e6, clamped = false
        if (areaKm2 > _root._uxVworldMaxKm2) {
            var k = Math.sqrt(_root._uxVworldMaxKm2 / areaKm2)
            hM *= k; wM *= k; clamped = true
            var dLat = (hM / 2) / 111320
            var dLon = (wM / 2) / (111320 * Math.cos(clat * Math.PI / 180))
            s = clat - dLat; n = clat + dLat; w = clon - dLon; e = clon + dLon
        }
        return { w: w, s: s, e: e, n: n, clat: clat, clon: clon, clamped: clamped, areaKm2: areaKm2 }
    }
    function _uxViewportBbox() {
        // Sample the four corners of the visible map to get the viewport bbox.
        var c1 = _root.toCoordinate(Qt.point(0, 0), false)
        var c2 = _root.toCoordinate(Qt.point(_root.width, 0), false)
        var c3 = _root.toCoordinate(Qt.point(_root.width, _root.height), false)
        var c4 = _root.toCoordinate(Qt.point(0, _root.height), false)
        var lats = [c1.latitude, c2.latitude, c3.latitude, c4.latitude]
        var lons = [c1.longitude, c2.longitude, c3.longitude, c4.longitude]
        return _root._uxClampBbox(Math.min.apply(null, lons), Math.min.apply(null, lats),
                                  Math.max.apply(null, lons), Math.max.apply(null, lats))
    }
    // Bounding box of the user-defined search region (rectangle around the map
    // centre, or drawn polygon). Road/River scans use this so the operator can
    // restrict the search to a drawn area — exactly like the Area tab.
    function _uxRegionBbox() {
        var poly = _root._uxAreaPolygon()
        if (!poly || poly.length < 3) return null
        var lats = [], lons = []
        for (var i = 0; i < poly.length; i++) { lats.push(poly[i][0]); lons.push(poly[i][1]) }
        return _root._uxClampBbox(Math.min.apply(null, lons), Math.min.apply(null, lats),
                                  Math.max.apply(null, lons), Math.max.apply(null, lats))
    }
    function _uxScan() {
        if (!_root._uxVworldToken) { _root._uxStatus = "No VWorld token (Settings → General)"; return }
        // Prefer the drawn search region; fall back to the visible viewport.
        var bb = _root._uxRegionBbox() || _root._uxViewportBbox()
        _root._uxScanBox = bb
        _root._uxBroadcastUi("scan", { "bbox": [bb.w, bb.s, bb.e, bb.n] })
        var layer = _root._uxKind === "road" ? "LT_L_MOCTLINK" : "LT_C_WKMSTRM"
        var url = "https://api.vworld.kr/req/data?service=data&version=2.0"
                + "&request=GetFeature&format=json&size=1000&page=1&data=" + layer
                + "&geometry=true&attribute=true&crs=EPSG:4326"
                + "&geomFilter=BOX(" + bb.w + "," + bb.s + "," + bb.e + "," + bb.n + ")"
                + "&key=" + _root._uxVworldToken + "&domain=localhost"
        _root._uxStatus = "Scanning" + (bb.clamped ? " (clamped to 9 km²)" : "") + "…"
        console.log("[UxAS-2D] VWorld " + _root._uxKind + " " + url)
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            if (xhr.status !== 200) { _root._uxStatus = "HTTP " + xhr.status; return }
            try {
                var resp = JSON.parse(xhr.responseText).response
                if (!resp || resp.status !== "OK") { _root._uxStatus = "VWorld: no features"; return }
                _root._uxIngest(resp.result.featureCollection.features || [])
            } catch (err) { _root._uxStatus = "Parse error: " + err }
        }
        xhr.open("GET", url); xhr.send()
    }
    function _uxIngest(feats) {
        // Group features by name; geometry comes in [lon,lat] from VWorld, we
        // store [lat,lon] for direct QtPositioning.coordinate construction.
        var kind = _root._uxKind
        var groups = ({})
        var bb = _root._uxScanBox
        for (var i = 0; i < feats.length; i++) {
            var f = feats[i]
            var nm = ((kind === "road" ? f.properties.road_name : f.properties.riv_nm) || "").trim()
            if (!nm) continue
            var g = f.geometry; if (!g) continue
            if (kind === "road") {
                var lines = (g.type === "MultiLineString") ? g.coordinates
                          : (g.type === "LineString") ? [g.coordinates] : []
                for (var li = 0; li < lines.length; li++) {
                    var seg = []
                    for (var pi = 0; pi < lines[li].length; pi++) {
                        var c = lines[li][pi]
                        if (bb && (c[0] < bb.w || c[0] > bb.e || c[1] < bb.s || c[1] > bb.n)) continue
                        seg.push([c[1], c[0]])      // [lat,lon]
                    }
                    if (seg.length >= 2) (groups[nm] = groups[nm] || []).push(seg)
                }
            } else {
                // For rivers (MultiPolygon) take the outer ring of the largest poly.
                var polys = (g.type === "MultiPolygon") ? g.coordinates
                          : (g.type === "Polygon") ? [g.coordinates] : []
                for (var pj = 0; pj < polys.length; pj++) {
                    var ring = polys[pj][0]
                    var rg = []
                    for (var rk = 0; rk < ring.length; rk++) rg.push([ring[rk][1], ring[rk][0]])
                    if (rg.length >= 3) (groups[nm] = groups[nm] || []).push(rg)
                }
            }
        }
        // Reset candidate set for this kind, preserve nothing (fresh scan).
        var cand = _root._uxCandidates
        cand[kind] = ({})
        for (var name in groups) cand[kind][name] = { segs: groups[name], selected: false }
        _root._uxCandidates = cand
        _root._uxBump = _root._uxBump + 1
        _root._uxStatus = Object.keys(groups).length + " " + kind + (Object.keys(groups).length === 1 ? "" : "s") + " found"
        _root._uxSyncOverlay()
    }
    function _uxSetSelected(kind, name, on) {
        var cand = _root._uxCandidates
        if (!cand[kind] || !cand[kind][name]) return
        cand[kind][name].selected = on
        _root._uxCandidates = cand
        _root._uxBump = _root._uxBump + 1
        _root._uxSyncOverlay()
        _root._uxBroadcastUi("select", { "name": name, "selected": on })
    }
    function _uxSelectAll(kind, on) {
        var cand = _root._uxCandidates
        var d = cand[kind] || ({})
        for (var n in d) d[n].selected = on
        _root._uxCandidates = cand
        _root._uxBump = _root._uxBump + 1
        _root._uxSyncOverlay()
        _root._uxBroadcastUi(on ? "select_all" : "select_none", {})
    }
    function _uxClear() {
        _root._uxBroadcastUi("clear", {})
        _root._uxCandidates = ({ road: ({}), river: ({}) })
        _root._uxScanBox = null
        _root._uxBump = _root._uxBump + 1
        _root._uxStatus = ""
        _root._uxSyncOverlay()
    }
    function _uxSyncOverlay() {
        // Mirror current selection to EventBroadcaster.planOverlay so the
        // Cesium 3D view (and the cyan MapItemView delegates above) light up.
        var existing = EventBroadcaster.planOverlay || ({})
        var area = existing && existing.area ? existing.area : null
        var roads = [], rivers = []
        var d = _root._uxCandidates
        for (var rn in d.road) if (d.road[rn].selected)
            for (var ri = 0; ri < d.road[rn].segs.length; ri++)
                roads.push(d.road[rn].segs[ri])
        for (var vn in d.river) if (d.river[vn].selected)
            for (var vi = 0; vi < d.river[vn].segs.length; vi++)
                rivers.push(d.river[vn].segs[vi])
        EventBroadcaster.setPlanOverlay({ area: area, roads: roads, rivers: rivers })
    }
    // Broadcast a UxAS-panel UI action so every panel button (scan/select/
    // clear/tab/region/publish…) is recorded on EventBroadcaster (45678) and
    // replayable — like the guided-action buttons.
    function _uxBroadcastUi(action, extra) {
        if (_root._uxReplaying) return                 // applying an inbound command — don't echo
        var d = { "kind": _root._uxKind, "shape": _root._uxShape }
        if (extra) for (var k in extra) d[k] = extra[k]
        EventBroadcaster.sendEvent("uxas_ui", action, d)
    }
    // Inbound: drive every UxAS-panel control from a scenario/replay command so
    // a recorded (or hand-written) "test card" reproduces the panel exactly.
    // Commands are {"action":"ux_<field>", "value":..} on the Rx port (45679).
    property bool   _uxReplaying:   false   // suppress re-broadcast while applying
    function _uxHandleCommand(action, params) {
        if (typeof action !== "string" || action.indexOf("ux_") !== 0) return
        var v = (params && params["value"] !== undefined) ? params["value"] : undefined
        _root._uxReplaying = true
        switch (action) {
        case "ux_tab":            _root._uxKind = String(params["kind"] || v || "area"); break
        case "ux_fov":            _root._uxFovDeg = Math.max(1, Math.min(120, Number(v))); break
        case "ux_overlap":        _root._uxOverlapPct = Math.max(0, Math.min(90, Number(v))); break
        case "ux_altitude":       uxAltField.text = String(Number(v)); break
        case "ux_x500":           _root._uxNX500 = Math.max(0, Math.min(_root._uxX500Ids.length, Number(v))); _root._uxSyncVehicles(); break
        case "ux_cessna":         _root._uxNCessna = Math.max(0, Math.min(_root._uxCessnaIds.length, Number(v))); _root._uxSyncVehicles(); break
        case "ux_shape":          _root._uxShape = (String(params["shape"] || v) === "poly") ? "poly" : "rect"; break
        case "ux_width":          _root._uxRectW = Math.max(50, Number(v)); break
        case "ux_height":         _root._uxRectH = Math.max(50, Number(v)); break
        case "ux_river_mode":     _root._uxRiverMode = String(params["mode"] || v || "center"); break
        case "ux_camera_coverage":_root.showCameraCoverage = (v === true || v === "true" || v === 1); break
        case "ux_scan":           _root._uxScan(); break
        case "ux_select":         _root._uxSetSelected(_root._uxKind, String(params["name"]), params["selected"] !== false); break
        case "ux_select_all":     _root._uxSelectAll(_root._uxKind, !(v === false || v === "false")); break
        case "ux_clear":          _root._uxClear(); break
        case "ux_publish":        if (_root._uxKind === "area") _root._uxPublishArea(); else _root._uxPublish(); break
        case "ux_publish_sar":    _root._uxPublishSar(); break
        case "ux_rover":          _root._uxNRover = Math.max(0, Math.min(_root._uxRoverIds.length, Number(v))); _root._uxSyncVehicles(); break
        case "ux_fw_alt":         _root._uxFwAlt = Math.max(10, Number(v)); break
        case "ux_mc_alt":         _root._uxMcAlt = Math.max(5, Number(v)); break
        case "ux_fw_fov":         _root._uxFwFov = Math.max(1, Math.min(120, Number(v))); break
        case "ux_mc_fov":         _root._uxMcFov = Math.max(1, Math.min(120, Number(v))); break
        }
        _root._uxCalcBump++
        _root._uxReplaying = false
    }
    Connections {
        target:               EventBroadcaster
        function onCommandReceived(action, params) { _root._uxHandleCommand(action, params) }
    }
    // ---- fleet / sensor / coverage (ported from the Cesium 3D panel) ----
    function _uxVehicleIds() {
        var nx = Math.min(_root._uxX500Ids.length, Math.max(0, _root._uxNX500))
        var nc = Math.min(_root._uxCessnaIds.length, Math.max(0, _root._uxNCessna))
        var ids = _root._uxX500Ids.slice(0, nx).concat(_root._uxCessnaIds.slice(0, nc))
        return ids.join(",") || "1"
    }
    function _uxSyncVehicles() {
        if (typeof uxVehField !== "undefined")
            uxVehField.text = _root._uxVehicleIds()
    }
    function _uxAreaM2() {
        if (_root._uxShape === "poly" && _root._uxDrawnPoly.length >= 3) {
            // shoelace in metres; _uxDrawnPoly is [[lat,lon], ...]
            var p = _root._uxDrawnPoly
            var lat0 = p[0][0]
            var mPerLon = 111320 * Math.cos(lat0 * Math.PI / 180), mPerLat = 111320
            var s = 0
            for (var i = 0; i < p.length; i++) {
                var a = p[i], b = p[(i + 1) % p.length]
                s += (a[1] * mPerLon) * (b[0] * mPerLat) - (b[1] * mPerLon) * (a[0] * mPerLat)
            }
            return Math.abs(s) / 2
        }
        return _root._uxRectW * _root._uxRectH
    }
    // Active fleet type drives the coverage math + readout: X500 if any X500 is
    // set (or as the default), else Cessna when only Cessna is selected.
    function _uxActiveType() {
        if (_root._uxNX500 > 0) return "multicopter"
        if (_root._uxNCessna > 0) return "fixed_wing"
        return "multicopter"
    }
    function _uxTypeLabel() { return _uxActiveType() === "fixed_wing" ? "Cessna" : "X500" }
    function _uxCoverage() {
        var _ = _root._uxNX500 + _root._uxNCessna + _root._uxOverlapPct + _root._uxFovDeg   // refresh deps
        var alt = parseFloat(uxAltField.text); if (isNaN(alt)) alt = 100
        var detail = _root._uxFovDeg <= 25
        var fw = _root._uxActiveType() === "fixed_wing"
        var hfov = _root._uxFovDeg
        var overlap = Math.max(0, Math.min(0.9, _root._uxOverlapPct / 100))
        // Fixed-wing (Cessna) cruises faster → more coverage per airframe.
        var speed = fw ? (detail ? 16 : 20) : (detail ? 10 : 12)
        var eff = 0.75, onSta = 22 * 60
        var swath = 2 * alt * Math.tan(hfov * Math.PI / 360)
        var perDrone = swath * (1 - overlap) * speed * eff * onSta
        var area = _root._uxAreaM2()
        return { areaKm2: area / 1e6, gsdCm: (swath / 1024) * 100,
                 perDroneKm2: perDrone / 1e6,
                 drones: Math.max(1, Math.ceil(area / Math.max(1, perDrone))),
                 typeLabel: _root._uxTypeLabel() }
    }
    function _uxSelectedLengthKm(kind) {
        var km = 0
        var d = _root._uxCandidates[kind] || {}
        for (var nm in d) {
            if (!d[nm].selected) continue
            var segs = d[nm].segs || []
            for (var si = 0; si < segs.length; si++) {
                var seg = segs[si]
                for (var i = 1; i < seg.length; i++) {
                    var a = seg[i - 1], b = seg[i]   // [lat,lon]
                    var dx = (b[1] - a[1]) * 111.32 * Math.cos(a[0] * Math.PI / 180)
                    var dy = (b[0] - a[0]) * 111.32
                    km += Math.sqrt(dx * dx + dy * dy)
                }
            }
        }
        return km
    }
    function _uxCalcText() {
        var _ = _root._uxCalcBump + _root._uxBump   // refresh deps
        var c = _root._uxCoverage()
        if (_root._uxKind === "area") {
            return "Area " + c.areaKm2.toFixed(2) + " km² · GSD " + c.gsdCm.toFixed(0) + " cm\n"
                 + "1 " + c.typeLabel + " ≈ " + c.perDroneKm2.toFixed(2) + " km² → need "
                 + c.drones + " " + c.typeLabel
        }
        var sel = _root._uxSelectedNames(_root._uxKind)
        var km = _root._uxSelectedLengthKm(_root._uxKind)
        var alt = parseFloat(uxAltField.text); if (isNaN(alt)) alt = 0
        return "GSD " + c.gsdCm.toFixed(0) + " cm @ " + alt.toFixed(0) + " m\n"
             + sel.length + " selected" + (km > 0 ? " · " + km.toFixed(1) + " km" : "")
    }
    // ---- area shape (rectangle around map centre, or drawn polygon) ----
    function _uxRectPolygon() {
        var c = _root.center
        var dLat = (_root._uxRectH / 2) / 111320
        var dLon = (_root._uxRectW / 2) / (111320 * Math.cos(c.latitude * Math.PI / 180))
        return [[c.latitude - dLat, c.longitude - dLon],
                [c.latitude - dLat, c.longitude + dLon],
                [c.latitude + dLat, c.longitude + dLon],
                [c.latitude + dLat, c.longitude - dLon]]
    }
    function _uxAreaPolygon() {
        if (_root._uxShape === "poly")
            return _root._uxDrawnPoly.length >= 3 ? _root._uxDrawnPoly.slice() : []
        return _root._uxRectPolygon()
    }
    function _uxAreaPreviewPath() {
        var poly = _root._uxAreaPolygon(), out = []
        for (var i = 0; i < poly.length; i++)
            out.push(QtPositioning.coordinate(poly[i][0], poly[i][1]))
        return out
    }
    function _uxAddPolyVertex(coord) {
        var arr = _root._uxDrawnPoly.slice()
        arr.push([coord.latitude, coord.longitude])
        _root._uxDrawnPoly = arr
        _root._uxCalcBump++
        _root._uxStatus = _root._uxDrawnPoly.length + " point(s) — click map to add"
    }
    function _uxClearPoly() {
        _root._uxDrawnPoly = []
        _root._uxCalcBump++
        _root._uxStatus = "Polygon cleared"
    }
    function _uxPublishArea() {
        var poly = _root._uxAreaPolygon()
        if (poly.length < 3) { _root._uxStatus = "Draw ≥3 points or use Rectangle"; return }
        _root._uxBroadcastUi("publish", {})
        var clat = 0, clon = 0
        for (var i = 0; i < poly.length; i++) { clat += poly[i][0]; clon += poly[i][1] }
        clat /= poly.length; clon /= poly.length
        var alt = parseFloat(uxAltField.text); if (isNaN(alt)) alt = 100
        var veh = uxVehField.text || "1"
        // Wipe the previous run's planned route so a new publish starts clean.
        EventBroadcaster.clearUxasPlannedWaypoints(0)
        EventBroadcaster.sendEvent("uxas_search", "area", {
            "center_lat":    clat,
            "center_lon":    clon,
            "vehicles":      veh,
            "altitude":      alt,
            "region_radius": 4000,
            "polygon":       poly,
            "width_m":       _root._uxRectW,
            "height_m":      _root._uxRectH,
            "overlap":       _root._uxOverlapPct / 100,
            "sensor":        _root._uxFovDeg <= 25 ? "detail" : "wide",
            "fov":           _root._uxFovDeg
        })
        // mirror the planned area so the yellow overlay shows on 2D + 3D
        EventBroadcaster.setPlanOverlay({ area: poly, roads: [], rivers: [] })
        _root._uxStatus = "Published area (" + poly.length + " pts)"
    }
    function _uxPublish() {
        var bb = _root._uxScanBox
        if (!bb) { _root._uxStatus = "Scan first"; return }
        _root._uxBroadcastUi("publish", {})
        var sel = _root._uxSelectedNames(_root._uxKind)
        var names = sel.length ? sel.join(",") : "ALL"
        var spanM = Math.max((bb.n - bb.s) * 111320,
                             (bb.e - bb.w) * 111320 * Math.cos(((bb.s + bb.n) / 2) * Math.PI / 180))
        var alt = parseFloat(uxAltField.text)
        if (isNaN(alt)) alt = 80
        var veh = uxVehField.text || "1"
        // Wipe the previous run's planned route so a new publish starts clean.
        EventBroadcaster.clearUxasPlannedWaypoints(0)
        EventBroadcaster.sendEvent("uxas_search", _root._uxKind, {
            "center_lat":    bb.clat,
            "center_lon":    bb.clon,
            "vehicles":      veh,
            "altitude":      alt,
            "region_radius": Math.max(3000, spanM),
            "names":         names,
            "bbox":          [bb.w, bb.s, bb.e, bb.n],
            "half_size_m":   1000,
            "overlap":       _root._uxOverlapPct / 100,
            "sensor":        _root._uxFovDeg <= 25 ? "detail" : "wide",
            "fov":           _root._uxFovDeg,
            "region":        _root._uxAreaPolygon(),   // clip features to this drawn region
            "river_mode":    _root._uxRiverMode        // center | bank | area
        })
        _root._uxStatus = "Published " + _root._uxKind + " (" + names + ")"
    }
    // Heterogeneous SAR: one publish splits the drawn region across vehicle types
    // (fixed-wing wide+high, multicopter close+low, rover on roads). The listener
    // builds one AutomationRequest with per-tier eligibility so UxAS assigns by
    // capability.
    function _uxPublishSar() {
        var bb = _root._uxRegionBbox() || _root._uxScanBox || _root._uxViewportBbox()
        if (!bb) { _root._uxStatus = "Draw a region first"; return }
        _root._uxBroadcastUi("publish_sar", {})
        var fwIds  = _root._uxCessnaIds.slice(0, _root._uxNCessna)
        var mcIds  = _root._uxX500Ids.slice(0, _root._uxNX500)
        var ugvIds = _root._uxRoverIds.slice(0, _root._uxNRover)
        if (fwIds.length + mcIds.length + ugvIds.length === 0) {
            _root._uxStatus = "Set X500 / Cessna / Rover counts first"; return
        }
        EventBroadcaster.clearUxasPlannedWaypoints(0)
        EventBroadcaster.sendEvent("uxas_search", "sar", {
            "kind":          "sar",
            "center_lat":    (bb.s + bb.n) / 2,
            "center_lon":    (bb.w + bb.e) / 2,
            "bbox":          [bb.w, bb.s, bb.e, bb.n],
            "fw_ids":        fwIds,  "mc_ids": mcIds, "ugv_ids": ugvIds,
            "fw_alt":        _root._uxFwAlt, "mc_alt": _root._uxMcAlt,
            "fw_fov":        _root._uxFwFov, "mc_fov": _root._uxMcFov,
            "mc_inner":      0.45,
            "names":         "ALL",
            "region_radius": 3000
        })
        _root._uxStatus = "SAR: FW " + fwIds.length + "@" + _root._uxFwAlt
            + "m · MC " + mcIds.length + "@" + _root._uxMcAlt + "m · UGV " + ugvIds.length
    }
    // map click → toggle the candidate closest to the click (within ~0.4 cm
    // on-screen, in geo terms ≈ 8 px). Falls back to the original guided
    // mapClick action if nothing is close enough.
    function _uxPickAt(coord) {
        var bestKind = null, bestName = null, bestD = 1e30
        var click = _root.fromCoordinate(coord, false)
        var TOLPX = ScreenTools.defaultFontPixelHeight * 0.8
        var d = _root._uxCandidates
        function distSeg(p, a, b) {
            var vx = b.x - a.x, vy = b.y - a.y
            var wx = p.x - a.x, wy = p.y - a.y
            var c1 = vx * wx + vy * wy
            if (c1 <= 0) return Math.hypot(p.x - a.x, p.y - a.y)
            var c2 = vx * vx + vy * vy
            if (c2 <= c1) return Math.hypot(p.x - b.x, p.y - b.y)
            var t = c1 / c2
            return Math.hypot(p.x - (a.x + t * vx), p.y - (a.y + t * vy))
        }
        function checkSeg(kind, name, seg) {
            for (var i = 1; i < seg.length; i++) {
                var a = _root.fromCoordinate(QtPositioning.coordinate(seg[i - 1][0], seg[i - 1][1]), false)
                var b = _root.fromCoordinate(QtPositioning.coordinate(seg[i][0], seg[i][1]), false)
                var dd = distSeg(click, a, b)
                if (dd < bestD) { bestD = dd; bestKind = kind; bestName = name }
            }
        }
        for (var rn in d.road) (d.road[rn].segs || []).forEach(function (s) { checkSeg("road", rn, s) })
        for (var vn in d.river) (d.river[vn].segs || []).forEach(function (s) { checkSeg("river", vn, s) })
        if (bestKind && bestD < TOLPX) {
            _root._uxSetSelected(bestKind, bestName, !d[bestKind][bestName].selected)
            return true
        }
        return false
    }

    // Candidate roads — faded orange polylines (unselected).
    MapItemView {
        model: _root._uxAllPathsModel("road", false)
        delegate: MapPolyline {
            z:          QGroundControl.zOrderMapItems
            visible:    !pipMode
            line.width: 3
            line.color: Qt.rgba(1, 0.6, 0, 0.4)
            path:       modelData
        }
    }
    // Candidate rivers — faded blue polygons (unselected).
    MapItemView {
        model: _root._uxAllPathsModel("river", false)
        delegate: MapPolygon {
            z:          QGroundControl.zOrderMapItems
            visible:    !pipMode
            color:      Qt.rgba(0.16, 0.5, 1, 0.25)
            border.color: Qt.rgba(0.16, 0.5, 1, 0.6)
            border.width: 1
            path:       modelData
        }
    }

    // Area-tab LIVE preview — the rectangle (around map centre) or the polygon
    // being drawn, before Publish. Yellow to match the 3D panel's preview.
    MapPolygon {
        id:          uxAreaPreview
        z:           QGroundControl.zOrderMapItems + 1
        visible:     !pipMode && uxasPanel.visible && path.length >= 3
        color:       Qt.rgba(1, 0.95, 0.2, 0.12)
        border.color:"#ffe24a"
        border.width:2
        // Depends on _uxCalcBump (rect size / drawn verts) and center (rect follows view).
        path: {
            var _ = _root._uxCalcBump
            var __ = _root.center
            return _root._uxAreaPreviewPath()
        }
    }
    // Drawn polygon vertices — cyan dots so the first clicks are visible.
    MapItemView {
        model: {
            var _ = _root._uxCalcBump
            return (_root._uxShape === "poly") ? _root._uxDrawnPoly : []
        }
        delegate: MapCircle {
            z:            QGroundControl.zOrderMapItems + 2
            visible:      !pipMode
            center:       QtPositioning.coordinate(modelData[0], modelData[1])
            radius:       6
            color:        "#00ffff"
            border.color: "white"
            border.width: 2
        }
    }

    // ---------------------------------------------------------------
    // Camera footprint + searched-coverage overlay
    //   Live yellow footprint per flying vehicle (nadir camera quad sized by
    //   altitude × FOV, oriented to heading) + accumulated green "searched"
    //   stamps so the operator can confirm the whole area was covered.
    // ---------------------------------------------------------------
    property bool showCameraCoverage: true
    property real camHFovDeg:         _root._uxFovDeg              // follows panel FOV
    property real camVFovDeg:         _root._uxFovDeg * 0.75       // ~4:3 vertical FOV
    property var  _coverageStamps:    []      // accumulated footprints [[coord,...],...]
    property var  _liveFootprints:    []      // current footprint per vehicle
    property var  _lastStampCoord:    ({})    // vehicleId -> last stamped coordinate

    function _camFootprint(lat, lon, altM, hdgDeg) {
        if (altM < 2) return []
        var hw = altM * Math.tan(camHFovDeg * Math.PI / 360)   // half cross-track (m)
        var hl = altM * Math.tan(camVFovDeg * Math.PI / 360)   // half along-track (m)
        var c = QtPositioning.coordinate(lat, lon)
        function corner(fwd, right) {
            var p = c
            if (Math.abs(fwd) > 0.1)   p = p.atDistanceAndAzimuth(Math.abs(fwd),  fwd  >= 0 ? hdgDeg : hdgDeg + 180)
            if (Math.abs(right) > 0.1) p = p.atDistanceAndAzimuth(Math.abs(right), right >= 0 ? hdgDeg + 90 : hdgDeg + 270)
            return p
        }
        return [corner(hl, -hw), corner(hl, hw), corner(-hl, hw), corner(-hl, -hw)]
    }

    Timer {
        interval: 350
        running:  showCameraCoverage && !pipMode
        repeat:   true
        onTriggered: {
            var live = []
            var vlist = QGroundControl.multiVehicleManager.vehicles
            for (var i = 0; i < vlist.count; i++) {
                var v = vlist.get(i)
                if (!v || !v.coordinate.isValid) continue
                var alt = v.altitudeRelative.rawValue || 0
                if (alt < 2) continue
                var hdg = v.heading.rawValue || 0
                var fp = _root._camFootprint(v.coordinate.latitude, v.coordinate.longitude, alt, hdg)
                if (fp.length < 3) continue
                live.push(fp)
                // accumulate a coverage stamp once the vehicle moved ~half a
                // footprint, so consecutive stamps overlap into a swept band.
                var spacing = Math.max(5, alt * Math.tan(camVFovDeg * Math.PI / 360))
                // Store a SNAPSHOT coordinate, not v.coordinate itself: QML hands
                // back the same QGeoCoordinate object each tick and updates it in
                // place, so keeping the reference made last.distanceTo(current)
                // always 0 — only the very first stamp ever landed. Build a fresh
                // coordinate from the lat/lon values instead.
                var here = QtPositioning.coordinate(v.coordinate.latitude, v.coordinate.longitude)
                var last = _root._lastStampCoord[v.id]
                if (!last || last.distanceTo(here) > spacing) {
                    _root._lastStampCoord[v.id] = here
                    var stamps = _root._coverageStamps
                    stamps.push(fp)
                    if (stamps.length > 1200) stamps.shift()
                    _root._coverageStamps = stamps.slice()   // reassign → refresh view
                }
            }
            _root._liveFootprints = live
        }
    }

    function clearCoverage() { _root._coverageStamps = []; _root._lastStampCoord = ({}) }

    // accumulated searched area (translucent green) — persists for the flight
    MapItemView {
        model: _root.showCameraCoverage ? _root._coverageStamps : []
        delegate: MapPolygon {
            z:            QGroundControl.zOrderMapItems
            visible:      !pipMode && _root.showCameraCoverage
            color:        Qt.rgba(0.15, 0.85, 0.35, 0.32)
            border.color: Qt.rgba(0.1, 0.7, 0.25, 0.7)
            border.width: 1
            path:         modelData
        }
    }
    // live camera footprint per vehicle (yellow outline)
    MapItemView {
        model: _root.showCameraCoverage ? _root._liveFootprints : []
        delegate: MapPolygon {
            z:            QGroundControl.zOrderMapItems
            visible:      !pipMode && _root.showCameraCoverage
            color:        Qt.rgba(1, 0.9, 0, 0.12)
            border.color: "yellow"
            border.width: 2
            path:         modelData
        }
    }
    // NOTE: previously cleared accumulated coverage on trajectoryPoints
    // onPointsCleared, but PX4's trajectory buffer resets mid-flight (and on
    // mode changes), which wiped the green searched-area overlay the operator
    // needs to keep for the whole search. Coverage now persists; it is reset
    // only by clearCoverage() (called explicitly, e.g. on disarm below).
    Connections {
        target: _activeVehicle
        function onArmedChanged() {
            if (_activeVehicle && !_activeVehicle.armed) {
                // keep coverage after landing so the operator can review it;
                // a fresh arm starts a new search and clears the old stamps.
            } else if (_activeVehicle && _activeVehicle.armed) {
                _root.clearCoverage()
            }
        }
    }

    MapPolyline {
        id:         trajectoryPolyline
        line.width: 3
        line.color: "red"
        z:          QGroundControl.zOrderTrajectoryLines
        visible:    !pipMode

        Connections {
            target:                 QGroundControl.multiVehicleManager
            function onActiveVehicleChanged(activeVehicle) {
                trajectoryPolyline.path = _activeVehicle ? _activeVehicle.trajectoryPoints.list() : []
            }
        }

        Connections {
            target:                             _activeVehicle ? _activeVehicle.trajectoryPoints : null
            function onPointAdded(coordinate) { trajectoryPolyline.addCoordinate(coordinate) }
            function onUpdateLastPoint(coordinate) { trajectoryPolyline.replaceCoordinate(trajectoryPolyline.pathLength() - 1, coordinate) }
            function onPointsCleared() { trajectoryPolyline.path = [] }
        }
    }

    // Add the vehicles to the map
    MapItemView {
        model: QGroundControl.multiVehicleManager.vehicles
        delegate: VehicleMapItem {
            vehicle:        object
            coordinate:     object.coordinate
            map:            _root
            size:           pipMode ? ScreenTools.defaultFontPixelHeight : ScreenTools.defaultFontPixelHeight * 3
            z:              QGroundControl.zOrderVehicles
        }
    }
    // Add distance sensor view
    MapItemView{
        model: QGroundControl.multiVehicleManager.vehicles
        delegate: ProximityRadarMapView {
            vehicle:        object
            coordinate:     object.coordinate
            map:            _root
            z:              QGroundControl.zOrderVehicles
        }
    }
    // Add ADSB vehicles to the map
    MapItemView {
        model: QGroundControl.adsbVehicleManager.adsbVehicles
        delegate: VehicleMapItem {
            coordinate:     object.coordinate
            altitude:       object.altitude
            callsign:       object.callsign
            heading:        object.heading
            alert:          object.alert
            map:            _root
            size:           pipMode ? ScreenTools.defaultFontPixelHeight : ScreenTools.defaultFontPixelHeight * 2.5
            z:              QGroundControl.zOrderVehicles
        }
    }

    // Add the items associated with each vehicles flight plan to the map
    Repeater {
        model: QGroundControl.multiVehicleManager.vehicles

        PlanMapItems {
            map:                    _root
            largeMapView:           !pipMode
            planMasterController:   masterController
            vehicle:                _vehicle

            property var _vehicle: object

            PlanMasterController {
                id: masterController
                Component.onCompleted: startStaticActiveVehicle(object)
            }
        }
    }

    // Allow custom builds to add map items
    CustomMapItems {
        map:            _root
        largeMapView:   !pipMode
    }

    GeoFenceMapVisuals {
        map:                    _root
        myGeoFenceController:   _geoFenceController
        interactive:            false
        planView:               false
        homePosition:           _activeVehicle && _activeVehicle.homePosition.isValid ? _activeVehicle.homePosition :  QtPositioning.coordinate()
    }

    // Rally points on map
    MapItemView {
        model: _rallyPointController.points

        delegate: MapQuickItem {
            id:             itemIndicator
            anchorPoint.x:  sourceItem.anchorPointX
            anchorPoint.y:  sourceItem.anchorPointY
            coordinate:     object.coordinate
            z:              QGroundControl.zOrderMapItems

            sourceItem: MissionItemIndexLabel {
                id:         itemIndexLabel
                label:      qsTr("R", "rally point map item label")
            }
        }
    }

    // Camera trigger points
    MapItemView {
        model: _activeVehicle ? _activeVehicle.cameraTriggerPoints : 0

        delegate: CameraTriggerIndicator {
            coordinate:     object.coordinate
            z:              QGroundControl.zOrderTopMost
        }
    }

    // GoTo Location forward flight circle visuals
    QGCMapCircleVisuals {
        id:                 fwdFlightGotoMapCircle
        mapControl:         parent
        mapCircle:          _fwdFlightGotoMapCircle
        radiusLabelVisible: true
        visible:            gotoLocationItem.visible && _activeVehicle &&
                            _activeVehicle.inFwdFlight &&
                            !_activeVehicle.orbitActive

        property alias coordinate: _fwdFlightGotoMapCircle.center
        property alias radius: _fwdFlightGotoMapCircle.radius
        property alias clockwiseRotation: _fwdFlightGotoMapCircle.clockwiseRotation

        Component.onCompleted: {
            // Only allow editing the radius, not the position
            centerDragHandleVisible = false

            globals.guidedControllerFlyView.fwdFlightGotoMapCircle = this
        }

        Binding {
            target: _fwdFlightGotoMapCircle
            property: "center"
            value: gotoLocationItem.coordinate
        }

        function startLoiterRadiusEdit() {
            _fwdFlightGotoMapCircle.interactive = true
        }

        // Called when loiter edit is confirmed
        function actionConfirmed() {
            _fwdFlightGotoMapCircle.interactive = false
            _fwdFlightGotoMapCircle._commitRadius()
        }

        // Called when loiter edit is cancelled
        function actionCancelled() {
            _fwdFlightGotoMapCircle.interactive = false
            _fwdFlightGotoMapCircle._restoreRadius()
        }

        QGCMapCircle {
            id:                 _fwdFlightGotoMapCircle
            interactive:        false
            showRotation:       true
            clockwiseRotation:  true

            property real _defaultLoiterRadius: _flyViewSettings.forwardFlightGoToLocationLoiterRad.value
            property real _committedRadius;

            onCenterChanged: {
                radius.rawValue = _defaultLoiterRadius
                // Don't commit the radius in case this operation is undone
            }

            Component.onCompleted: {
                radius.rawValue = _defaultLoiterRadius
                _commitRadius()
            }

            function _commitRadius() {
                _committedRadius = radius.rawValue
            }

            function _restoreRadius() {
                radius.rawValue = _committedRadius
            }
        }
    }

    // GoTo Location visuals
    MapQuickItem {
        id:             gotoLocationItem
        visible:        false
        z:              QGroundControl.zOrderMapItems
        anchorPoint.x:  sourceItem.anchorPointX
        anchorPoint.y:  sourceItem.anchorPointY
        sourceItem: MissionItemIndexLabel {
            checked:    true
            index:      -1
            label:      qsTr("Go here", "Go to location waypoint")
        }

        property bool inGotoFlightMode: _activeVehicle ? _activeVehicle.flightMode === _activeVehicle.gotoFlightMode : false

        property var _committedCoordinate: null

        onInGotoFlightModeChanged: {
            if (!inGotoFlightMode && gotoLocationItem.visible) {
                // Hide goto indicator when vehicle falls out of guided mode
                hide()
            }
        }

        function show(coord) {
            gotoLocationItem.coordinate = coord
            gotoLocationItem.visible = true
        }

        function hide() {
            gotoLocationItem.visible = false
        }

        function actionConfirmed() {
            _commitCoordinate()

            // Commit the new radius which possibly changed
            fwdFlightGotoMapCircle.actionConfirmed()

            // We leave the indicator visible. The handling for onInGuidedModeChanged will hide it.
        }

        function actionCancelled() {
            _restoreCoordinate()

            // Also restore the loiter radius
            fwdFlightGotoMapCircle.actionCancelled()
        }

        function _commitCoordinate() {
            // Must deep copy
            _committedCoordinate = QtPositioning.coordinate(
                coordinate.latitude,
                coordinate.longitude
            );
        }

        function _restoreCoordinate() {
            if (_committedCoordinate) {
                coordinate = _committedCoordinate
            } else {
                hide()
            }
        }
    }

    // Orbit editing visuals
    QGCMapCircleVisuals {
        id:             orbitMapCircle
        mapControl:     parent
        mapCircle:      _mapCircle
        visible:        false

        property alias center:              _mapCircle.center
        property alias clockwiseRotation:   _mapCircle.clockwiseRotation
        readonly property real defaultRadius: 30

        Connections {
            target: QGroundControl.multiVehicleManager
            function onActiveVehicleChanged(activeVehicle) {
                if (!activeVehicle) {
                    orbitMapCircle.visible = false
                }
            }
        }

        function show(coord) {
            _mapCircle.radius.rawValue = defaultRadius
            orbitMapCircle.center = coord
            orbitMapCircle.visible = true
        }

        function hide() {
            orbitMapCircle.visible = false
        }

        function actionConfirmed() {
            // Live orbit status is handled by telemetry so we hide here and telemetry will show again.
            hide()
        }

        function actionCancelled() {
            hide()
        }

        function radius() {
            return _mapCircle.radius.rawValue
        }

        Component.onCompleted: globals.guidedControllerFlyView.orbitMapCircle = orbitMapCircle

        QGCMapCircle {
            id:                 _mapCircle
            interactive:        true
            radius.rawValue:    30
            showRotation:       true
            clockwiseRotation:  true
        }
    }

    // ROI Location visuals
    MapQuickItem {
        id:             roiLocationItem
        visible:        _activeVehicle && _activeVehicle.isROIEnabled
        z:              QGroundControl.zOrderMapItems
        anchorPoint.x:  sourceItem.anchorPointX
        anchorPoint.y:  sourceItem.anchorPointY

        Connections {
            target: _activeVehicle
            function onRoiCoordChanged(centerCoord) {
                roiLocationItem.show(centerCoord)
            }
        }

        MouseArea {
            anchors.fill: parent
            onClicked: (position) => {
                position = Qt.point(position.x, position.y)
                var clickCoord = _root.toCoordinate(position, false /* clipToViewPort */)
                // For some strange reason using mainWindow in mapToItem doesn't work, so we use globals.parent instead which also gets us mainWindow
                position = mapToItem(globals.parent, position)
                var dropPanel = roiEditDropPanelComponent.createObject(mainWindow, { clickRect: Qt.rect(position.x, position.y, 0, 0) })
                dropPanel.open()
            }
        }

        sourceItem: MissionItemIndexLabel {
            checked:    true
            index:      -1
            label:      qsTr("ROI here", "Make this a Region Of Interest")
        }

        //-- Visibilty controlled by actual state
        function show(coord) {
            roiLocationItem.coordinate = coord
        }
    }

    // Orbit telemetry visuals
    QGCMapCircleVisuals {
        id:             orbitTelemetryCircle
        mapControl:     parent
        mapCircle:      _activeVehicle ? _activeVehicle.orbitMapCircle : null
        visible:        _activeVehicle ? _activeVehicle.orbitActive : false
    }

    MapQuickItem {
        id:             orbitCenterIndicator
        anchorPoint.x:  sourceItem.anchorPointX
        anchorPoint.y:  sourceItem.anchorPointY
        coordinate:     _activeVehicle ? _activeVehicle.orbitMapCircle.center : QtPositioning.coordinate()
        visible:        orbitTelemetryCircle.visible && !gotoLocationItem.visible

        sourceItem: MissionItemIndexLabel {
            checked:    true
            index:      -1
            label:      qsTr("Orbit", "Orbit waypoint")
        }
    }

    QGCPopupDialogFactory {
        id: roiEditPositionDialogFactory

        dialogComponent: roiEditPositionDialogComponent
    }

    Component {
        id: roiEditPositionDialogComponent

        EditPositionDialog {
            title:                  qsTr("Edit ROI Position")
            coordinate:             roiLocationItem.coordinate
            onCoordinateChanged: {
                roiLocationItem.coordinate = coordinate
                _activeVehicle.guidedModeROI(coordinate)
            }
        }
    }

    Component {
        id: roiEditDropPanelComponent

        DropPanel {
            id: roiEditDropPanel

            sourceComponent: Component {
                ColumnLayout {
                    spacing: ScreenTools.defaultFontPixelWidth / 2

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("Cancel ROI")
                        onClicked: {
                            _activeVehicle.stopGuidedModeROI()
                            roiEditDropPanel.close()
                        }
                    }

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("Edit Position")
                        onClicked: {
                            roiEditPositionDialogFactory.open()
                            roiEditDropPanel.close()
                        }
                    }
                }
            }
        }
    }

    Component {
        id: mapClickDropPanelComponent

        DropPanel {
            id: mapClickDropPanel

            property var mapClickCoord

            sourceComponent: Component {
                ColumnLayout {
                    spacing: ScreenTools.defaultFontPixelWidth / 2

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("Go to location")
                        visible:            globals.guidedControllerFlyView.showGotoLocation
                        onClicked: {
                            mapClickDropPanel.close()
                            gotoLocationItem.show(mapClickCoord)

                            if ((_activeVehicle.flightMode == _activeVehicle.gotoFlightMode) && !_flyViewSettings.goToLocationRequiresConfirmInGuided.value) {
                                if (globals.guidedControllerFlyView.executeAction(globals.guidedControllerFlyView.actionGoto, mapClickCoord)) {
                                    gotoLocationItem.actionConfirmed() // Still need to call this to commit the new coordinate and radius
                                } else {
                                    gotoLocationItem.actionCancelled()
                                }
                            } else {
                                globals.guidedControllerFlyView.confirmAction(globals.guidedControllerFlyView.actionGoto, mapClickCoord, gotoLocationItem)
                            }
                        }
                    }

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("Orbit at location")
                        visible:            globals.guidedControllerFlyView.showOrbit
                        onClicked: {
                            mapClickDropPanel.close()
                            orbitMapCircle.show(mapClickCoord)
                            globals.guidedControllerFlyView.confirmAction(globals.guidedControllerFlyView.actionOrbit, mapClickCoord, orbitMapCircle)
                        }
                    }

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("ROI at location")
                        visible:            globals.guidedControllerFlyView.showROI
                        onClicked: {
                            mapClickDropPanel.close()
                            globals.guidedControllerFlyView.executeAction(globals.guidedControllerFlyView.actionROI, mapClickCoord, 0, false)
                        }
                    }

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("Set home here")
                        visible:            globals.guidedControllerFlyView.showSetHome
                        onClicked: {
                            mapClickDropPanel.close()
                            globals.guidedControllerFlyView.confirmAction(globals.guidedControllerFlyView.actionSetHome, mapClickCoord)
                        }
                    }

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("Set Estimator Origin")
                        visible:            globals.guidedControllerFlyView.showSetEstimatorOrigin
                        onClicked: {
                            mapClickDropPanel.close()
                            globals.guidedControllerFlyView.confirmAction(globals.guidedControllerFlyView.actionSetEstimatorOrigin, mapClickCoord)
                        }
                    }

                    QGCButton {
                        Layout.fillWidth:   true
                        text:               qsTr("Set Heading")
                        visible:            globals.guidedControllerFlyView.showChangeHeading
                        onClicked: {
                            mapClickDropPanel.close()
                            globals.guidedControllerFlyView.confirmAction(globals.guidedControllerFlyView.actionChangeHeading, mapClickCoord)
                        }
                    }

                    ColumnLayout {
                        spacing: 0
                        QGCLabel { text: qsTr("Lat: %1").arg(mapClickCoord.latitude.toFixed(6)) }
                        QGCLabel { text: qsTr("Lon: %1").arg(mapClickCoord.longitude.toFixed(6)) }
                    }
                }
            }
        }
    }

    onMapClicked: (position) => {
        position = Qt.point(position.x, position.y)
        var clickCoord = _root.toCoordinate(position, false /* clipToViewPort */)

        // Region polygon drawing mode: each click adds a vertex (consumes click).
        if (uxasPanel.visible && _root._uxShape === "poly" && _root._uxDrawing) {
            _root._uxAddPolyVertex(clickCoord)
            return
        }

        // First, give the UxAS picker a chance — if the click lands on a
        // candidate road/river, toggle that feature and consume the click.
        if (uxasPanel.visible && _root._uxPickAt(clickCoord)) return

        if (!globals.guidedControllerFlyView.guidedUIVisible &&
            (globals.guidedControllerFlyView.showGotoLocation || globals.guidedControllerFlyView.showOrbit ||
             globals.guidedControllerFlyView.showROI || globals.guidedControllerFlyView.showSetHome ||
             globals.guidedControllerFlyView.showSetEstimatorOrigin)) {

            // For some strange reason using mainWindow in mapToItem doesn't work, so we use globals.parent instead which also gets us mainWindow
            position = _root.mapToItem(globals.parent, position)
            var dropPanel = mapClickDropPanelComponent.createObject(mainWindow, { mapClickCoord: clickCoord, clickRect: Qt.rect(position.x, position.y, 0, 0) })
            dropPanel.open()
        }
    }

    MapScale {
        id:                 mapScale
        anchors.margins:    _toolsMargin
        anchors.left:       parent.left
        anchors.top:        parent.top
        mapControl:         _root
        visible:            !ScreenTools.isTinyScreen && QGroundControl.corePlugin.options.flyView.showMapScale && mapControl.pipState.state === mapControl.pipState.windowState
    }

    // ---------------------------------------------------------------
    // UxAS Plan panel (overlay, sibling to MapItems — anchored to the map's
    // top-right edge so toolbar buttons remain interactive).
    // ---------------------------------------------------------------
    QGCPalette { id: _uxPal; colorGroupEnabled: true }

    Rectangle {
        id:                 uxasPanel
        visible:            !pipMode
        z:                  QGroundControl.zOrderWidgets
        // Anchor to top-LEFT, below the QGC toolbar. The right side holds QGC's
        // telemetry / multi-vehicle list (FlyViewTopRightPanel), which would
        // cover the panel once several vehicles connect — the left side is clear.
        anchors.top:        parent.top
        anchors.left:       parent.left
        // Draggable via the header: _uxDragDX/_uxDragDY shift the anchor margins.
        anchors.topMargin:  Math.max(0, _toolButtonTopMargin + ScreenTools.defaultFontPixelHeight * 6 + _uxDragDY)
        anchors.leftMargin: Math.max(0, _toolsMargin + _uxDragDX)
        property real _uxDragDX: 0
        property real _uxDragDY: 0
        width:              ScreenTools.defaultFontPixelWidth * 32
        radius:             ScreenTools.defaultFontPixelWidth * 0.4
        color:              _uxPal.window
        border.color:       _uxPal.buttonBorder
        border.width:       1
        height:             uxColumn.implicitHeight + uxColumn.anchors.margins * 2
        property bool collapsed: false
        // User-adjustable panel size: drag the grip to scale the whole panel.
        // transformOrigin = top-LEFT so it grows toward the map (rightward) and
        // keeps its top-left anchor.
        property real _uxScale: 1.0
        scale:              _uxScale
        transformOrigin:    Item.TopLeft

        // Swallow mouse/wheel that lands on the panel background so it never
        // reaches the map behind (no accidental waypoints / map pan-zoom while
        // operating the panel). Declared first → sits behind the controls, which
        // stay fully interactive on top.
        MouseArea {
            anchors.fill:       parent
            acceptedButtons:    Qt.AllButtons
            hoverEnabled:       true
            propagateComposedEvents: false
            onPressed:          (mouse) => { mouse.accepted = true }
            onReleased:         (mouse) => { mouse.accepted = true }
            onClicked:          (mouse) => { mouse.accepted = true }
            onDoubleClicked:    (mouse) => { mouse.accepted = true }
            onWheel:            (wheel) => { wheel.accepted = true }
        }

        // Resize grip (bottom-left corner). Drag down/left to enlarge.
        Rectangle {
            id:             uxResizeGrip
            visible:        !uxasPanel.collapsed
            width:          ScreenTools.defaultFontPixelHeight * 0.9
            height:         width
            radius:         2
            z:              10
            color:          _uxPal.buttonHighlight
            border.color:   _uxPal.buttonText
            border.width:   1
            anchors.right:  parent.right
            anchors.bottom: parent.bottom
            QGCColoredImage {
                anchors.centerIn:   parent
                width:              parent.width * 0.7
                height:             width
                source:             "/qmlimages/pipResize.svg"
                color:              _uxPal.buttonText
                fillMode:           Image.PreserveAspectFit
            }
            MouseArea {
                anchors.fill:   parent
                cursorShape:    Qt.SizeFDiagCursor
                property point startPt
                property real  startScale
                onPressed: (mouse) => {
                    startScale = uxasPanel._uxScale
                    startPt = mapToItem(_root, mouse.x, mouse.y)
                }
                onPositionChanged: (mouse) => {
                    var p = mapToItem(_root, mouse.x, mouse.y)
                    // moving right (+x) and/or down (+y) enlarges the panel
                    var delta = (p.x - startPt.x) + (p.y - startPt.y)
                    var ref = ScreenTools.defaultFontPixelWidth * 22
                    uxasPanel._uxScale = Math.max(0.7, Math.min(2.4, startScale + delta / ref))
                }
                onDoubleClicked: uxasPanel._uxScale = 1.0   // reset
            }
        }

        Column {
            id:                 uxColumn
            anchors.left:       parent.left
            anchors.right:      parent.right
            anchors.top:        parent.top
            anchors.margins:    ScreenTools.defaultFontPixelWidth * 0.6
            spacing:            ScreenTools.defaultFontPixelHeight * 0.3

            Row {
                width:          parent.width
                spacing:        ScreenTools.defaultFontPixelWidth * 0.4
                QGCLabel {
                    text:               qsTr("⠿ UxAS Plan")
                    font.bold:          true
                    color:              _uxPal.text
                    verticalAlignment:  Text.AlignVCenter
                    height:             uxToggle.height
                    width:              parent.width - uxToggle.width - parent.spacing
                    // Drag the header to move the panel; double-click to reset.
                    MouseArea {
                        anchors.fill:   parent
                        cursorShape:    Qt.OpenHandCursor
                        property point startPt
                        property real  startDX
                        property real  startDY
                        onPressed: (mouse) => {
                            startDX = uxasPanel._uxDragDX
                            startDY = uxasPanel._uxDragDY
                            startPt = mapToItem(_root, mouse.x, mouse.y)
                        }
                        onPositionChanged: (mouse) => {
                            var p = mapToItem(_root, mouse.x, mouse.y)
                            uxasPanel._uxDragDX = startDX + (p.x - startPt.x)
                            uxasPanel._uxDragDY = startDY + (p.y - startPt.y)
                        }
                        onDoubleClicked: { uxasPanel._uxDragDX = 0; uxasPanel._uxDragDY = 0 }
                    }
                }
                QGCButton {
                    id:                 uxToggle
                    text:               uxasPanel.collapsed ? "+" : "–"
                    width:              ScreenTools.defaultFontPixelHeight * 1.4
                    onClicked:          uxasPanel.collapsed = !uxasPanel.collapsed
                }
            }

            // Tabs — Area / Roads / Rivers (matches the Cesium 3D panel).
            Row {
                visible:        !uxasPanel.collapsed
                spacing:        ScreenTools.defaultFontPixelWidth * 0.6
                QGCRadioButton {
                    text:       qsTr("Area")
                    checked:    _root._uxKind === "area"
                    onClicked:  { _root._uxKind = "area"; _root._uxCalcBump++; _root._uxBroadcastUi("tab", {}) }
                }
                QGCRadioButton {
                    text:       qsTr("Road")
                    checked:    _root._uxKind === "road"
                    onClicked:  { _root._uxKind = "road"; _root._uxCalcBump++; _root._uxBroadcastUi("tab", {}) }
                }
                QGCRadioButton {
                    text:       qsTr("River")
                    checked:    _root._uxKind === "river"
                    onClicked:  { _root._uxKind = "river"; _root._uxCalcBump++; _root._uxBroadcastUi("tab", {}) }
                }
            }

            // Fleet — X500 / Cessna counts derive the Vehicle IDs (editable).
            Row {
                visible:        !uxasPanel.collapsed
                spacing:        ScreenTools.defaultFontPixelWidth * 0.4
                QGCLabel { text: qsTr("X500"); anchors.verticalCenter: parent.verticalCenter }
                QGCTextField {
                    width:              ScreenTools.defaultFontPixelWidth * 3.5
                    text:               _root._uxNX500.toString()
                    numericValuesOnly:  true
                    onEditingFinished:  {
                        _root._uxNX500 = Math.max(0, Math.min(_root._uxX500Ids.length, parseInt(text) || 0))
                        _root._uxSyncVehicles(); _root._uxCalcBump++; _root._uxBroadcastUi("x500", { "value": _root._uxNX500 })
                    }
                }
                QGCLabel { text: qsTr("Cessna"); anchors.verticalCenter: parent.verticalCenter }
                QGCTextField {
                    width:              ScreenTools.defaultFontPixelWidth * 3.5
                    text:               _root._uxNCessna.toString()
                    numericValuesOnly:  true
                    onEditingFinished:  {
                        _root._uxNCessna = Math.max(0, Math.min(_root._uxCessnaIds.length, parseInt(text) || 0))
                        _root._uxSyncVehicles(); _root._uxCalcBump++; _root._uxBroadcastUi("cessna", { "value": _root._uxNCessna })
                    }
                }
            }
            Row {
                visible:        !uxasPanel.collapsed
                spacing:        ScreenTools.defaultFontPixelWidth * 0.4
                QGCLabel { text: qsTr("Vehicle IDs"); anchors.verticalCenter: parent.verticalCenter }
                QGCTextField {
                    id:         uxVehField
                    width:      ScreenTools.defaultFontPixelWidth * 8
                    text:       "1"
                }
            }

            // Altitude (shared across all tabs).
            Row {
                visible:        !uxasPanel.collapsed
                spacing:        ScreenTools.defaultFontPixelWidth * 0.4
                QGCLabel { text: qsTr("Altitude (m)"); anchors.verticalCenter: parent.verticalCenter }
                QGCTextField {
                    id:                 uxAltField
                    width:              ScreenTools.defaultFontPixelWidth * 6
                    text:               "150"
                    numericValuesOnly:  true
                    onTextChanged:      _root._uxCalcBump++
                    onEditingFinished:  _root._uxBroadcastUi("altitude", { "value": parseFloat(text) || 0 })
                }
            }
            // Sensor / camera FOV (shared). The FOV drives the footprint AND
            // (via publish → bridge) UxAS's coverage lane spacing, so the green
            // footprint and the generated lanes match. Custom = type a value.
            Row {
                visible:        !uxasPanel.collapsed
                spacing:        ScreenTools.defaultFontPixelWidth * 0.4
                QGCLabel { text: qsTr("Sensor"); anchors.verticalCenter: parent.verticalCenter }
                QGCComboBox {
                    id:             uxSensorCombo
                    width:          ScreenTools.defaultFontPixelWidth * 13
                    model:          [ qsTr("Wide (45°)"), qsTr("Detail (20°)"), qsTr("Custom") ]
                    currentIndex:   _root._uxFovDeg === 45 ? 0 : (_root._uxFovDeg === 20 ? 1 : 2)
                    onActivated:    (index) => {
                        if (index === 0) _root._uxFovDeg = 45
                        else if (index === 1) _root._uxFovDeg = 20
                        _root._uxCalcBump++; _root._uxBroadcastUi("fov", { "value": _root._uxFovDeg })
                    }
                }
                QGCLabel { text: qsTr("FOV°"); anchors.verticalCenter: parent.verticalCenter }
                QGCTextField {
                    width:              ScreenTools.defaultFontPixelWidth * 5
                    text:               _root._uxFovDeg.toFixed(0)
                    numericValuesOnly:  true
                    onEditingFinished:  { _root._uxFovDeg = Math.max(1, Math.min(120, parseFloat(text) || 45)); _root._uxCalcBump++; _root._uxBroadcastUi("fov", { "value": _root._uxFovDeg }) }
                }
            }
            // Camera image overlap % (drives lane spacing / coverage estimate).
            Row {
                visible:        !uxasPanel.collapsed
                spacing:        ScreenTools.defaultFontPixelWidth * 0.4
                QGCLabel { text: qsTr("Overlap %"); anchors.verticalCenter: parent.verticalCenter }
                QGCTextField {
                    width:              ScreenTools.defaultFontPixelWidth * 5
                    text:               _root._uxOverlapPct.toFixed(0)
                    numericValuesOnly:  true
                    onEditingFinished:  {
                        _root._uxOverlapPct = Math.max(0, Math.min(90, parseFloat(text) || 0))
                        _root._uxCalcBump++; _root._uxBroadcastUi("overlap", { "value": _root._uxOverlapPct })
                    }
                }
            }

            // Coverage readout (area km²/GSD/need, or GSD/selected/length).
            QGCLabel {
                visible:        !uxasPanel.collapsed
                width:          parent.width
                wrapMode:       Text.WordWrap
                color:          _uxPal.text
                font.pointSize: ScreenTools.smallFontPointSize
                text:           _root._uxCalcText()
            }

            // Camera coverage on/off (green searched-area + yellow footprint).
            QGCCheckBox {
                visible:        !uxasPanel.collapsed
                text:           qsTr("Camera coverage (green)")
                checked:        _root.showCameraCoverage
                onClicked:      { _root.showCameraCoverage = checked; _root._uxBroadcastUi("camera_coverage", { "value": checked }) }
            }

            // ---- Search region (shared: Area + Road + River) ----
            // Draw a rectangle (W×H around the map centre) or a polygon; Area
            // covers it, Road/River restrict their scan + path to it.
            Column {
                visible:        !uxasPanel.collapsed
                width:          parent.width
                spacing:        ScreenTools.defaultFontPixelHeight * 0.3
                Row {
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCLabel { text: qsTr("Region"); anchors.verticalCenter: parent.verticalCenter }
                    QGCComboBox {
                        width:          ScreenTools.defaultFontPixelWidth * 16
                        model:          [ qsTr("Rectangle"), qsTr("Polygon (draw)") ]
                        currentIndex:   _root._uxShape === "poly" ? 1 : 0
                        onActivated:    (index) => {
                            _root._uxShape = (index === 1) ? "poly" : "rect"
                            if (_root._uxShape === "rect") _root._uxDrawing = false
                            _root._uxCalcBump++; _root._uxBroadcastUi("shape", { "value": _root._uxShape })
                        }
                    }
                }
                // Rectangle W×H (centred on the map view).
                Row {
                    visible:    _root._uxShape === "rect"
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCLabel { text: qsTr("Width (m)"); anchors.verticalCenter: parent.verticalCenter }
                    QGCTextField {
                        width:              ScreenTools.defaultFontPixelWidth * 6
                        text:               _root._uxRectW.toFixed(0)
                        numericValuesOnly:  true
                        onEditingFinished:  { _root._uxRectW = Math.max(50, parseFloat(text) || 0); _root._uxCalcBump++; _root._uxBroadcastUi("width", { "value": _root._uxRectW }) }
                    }
                    QGCLabel { text: qsTr("Height (m)"); anchors.verticalCenter: parent.verticalCenter }
                    QGCTextField {
                        width:              ScreenTools.defaultFontPixelWidth * 6
                        text:               _root._uxRectH.toFixed(0)
                        numericValuesOnly:  true
                        onEditingFinished:  { _root._uxRectH = Math.max(50, parseFloat(text) || 0); _root._uxCalcBump++; _root._uxBroadcastUi("height", { "value": _root._uxRectH }) }
                    }
                }
                // Polygon draw controls.
                Row {
                    visible:    _root._uxShape === "poly"
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCButton {
                        text:       _root._uxDrawing ? qsTr("Drawing… (click map)") : qsTr("Draw: click map")
                        onClicked:  { _root._uxDrawing = !_root._uxDrawing
                                      _root._uxStatus = _root._uxDrawing ? "Click the map to add vertices" : "" }
                    }
                    QGCButton {
                        text:       qsTr("Clear")
                        onClicked:  _root._uxClearPoly()
                    }
                }
            }

            // ---- Heterogeneous SAR (uses the fleet counts above + drawn region) ----
            Column {
                visible:        !uxasPanel.collapsed
                width:          parent.width
                spacing:        ScreenTools.defaultFontPixelHeight * 0.3
                QGCLabel {
                    text:           qsTr("── Heterogeneous SAR ──")
                    font.bold:      true
                    color:          _uxPal.text
                }
                Row {
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCLabel { text: qsTr("Rover"); anchors.verticalCenter: parent.verticalCenter }
                    QGCTextField {
                        width:              ScreenTools.defaultFontPixelWidth * 3.5
                        text:               _root._uxNRover.toString()
                        numericValuesOnly:  true
                        onEditingFinished:  { _root._uxNRover = Math.max(0, Math.min(_root._uxRoverIds.length, parseInt(text) || 0)); _root._uxBroadcastUi("rover", { "value": _root._uxNRover }) }
                    }
                    QGCLabel {
                        anchors.verticalCenter: parent.verticalCenter
                        opacity:    0.7
                        font.pointSize: ScreenTools.smallFontPointSize
                        text:       qsTr("(X500/Cessna set above)")
                    }
                }
                Row {
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCLabel { width: ScreenTools.defaultFontPixelWidth * 8; text: qsTr("FW alt/fov"); anchors.verticalCenter: parent.verticalCenter }
                    QGCTextField {
                        width: ScreenTools.defaultFontPixelWidth * 6; text: _root._uxFwAlt.toFixed(0); numericValuesOnly: true
                        onEditingFinished: { _root._uxFwAlt = Math.max(10, parseFloat(text) || 250); _root._uxBroadcastUi("fw_alt", { "value": _root._uxFwAlt }) }
                    }
                    QGCTextField {
                        width: ScreenTools.defaultFontPixelWidth * 5; text: _root._uxFwFov.toFixed(0); numericValuesOnly: true
                        onEditingFinished: { _root._uxFwFov = Math.max(1, Math.min(120, parseFloat(text) || 45)); _root._uxBroadcastUi("fw_fov", { "value": _root._uxFwFov }) }
                    }
                    QGCLabel { text: qsTr("(wide·high)"); opacity: 0.7; font.pointSize: ScreenTools.smallFontPointSize; anchors.verticalCenter: parent.verticalCenter }
                }
                Row {
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCLabel { width: ScreenTools.defaultFontPixelWidth * 8; text: qsTr("MC alt/fov"); anchors.verticalCenter: parent.verticalCenter }
                    QGCTextField {
                        width: ScreenTools.defaultFontPixelWidth * 6; text: _root._uxMcAlt.toFixed(0); numericValuesOnly: true
                        onEditingFinished: { _root._uxMcAlt = Math.max(5, parseFloat(text) || 60); _root._uxBroadcastUi("mc_alt", { "value": _root._uxMcAlt }) }
                    }
                    QGCTextField {
                        width: ScreenTools.defaultFontPixelWidth * 5; text: _root._uxMcFov.toFixed(0); numericValuesOnly: true
                        onEditingFinished: { _root._uxMcFov = Math.max(1, Math.min(120, parseFloat(text) || 20)); _root._uxBroadcastUi("mc_fov", { "value": _root._uxMcFov }) }
                    }
                    QGCLabel { text: qsTr("(core·low)"); opacity: 0.7; font.pointSize: ScreenTools.smallFontPointSize; anchors.verticalCenter: parent.verticalCenter }
                }
                QGCButton {
                    width:      parent.width
                    primary:    true
                    text:       qsTr("Publish SAR (heterogeneous)")
                    onClicked:  _root._uxPublishSar()
                }
            }

            // ---- AREA tab ----
            Column {
                visible:        !uxasPanel.collapsed && _root._uxKind === "area"
                width:          parent.width
                spacing:        ScreenTools.defaultFontPixelHeight * 0.3
                QGCButton {
                    text:       qsTr("Set ") + _root._uxTypeLabel() + qsTr(" to ") + _root._uxCoverage().drones
                    onClicked:  {
                        var n = _root._uxCoverage().drones
                        if (_root._uxActiveType() === "fixed_wing")
                            _root._uxNCessna = Math.max(0, Math.min(_root._uxCessnaIds.length, n))
                        else
                            _root._uxNX500 = Math.max(0, Math.min(_root._uxX500Ids.length, n))
                        _root._uxSyncVehicles(); _root._uxCalcBump++
                    }
                }
                QGCButton {
                    width:      parent.width
                    primary:    true
                    text:       qsTr("Publish Area Search")
                    onClicked:  _root._uxPublishArea()
                }
                QGCLabel {
                    width:          parent.width
                    wrapMode:       Text.WordWrap
                    opacity:        0.7
                    color:          _uxPal.text
                    font.pointSize: ScreenTools.smallFontPointSize
                    text:           qsTr("Rectangle/polygon centred on the view; preview before publish.")
                }
            }

            // ---- ROAD / RIVER tabs ----
            Column {
                visible:        !uxasPanel.collapsed && _root._uxKind !== "area"
                width:          parent.width
                spacing:        ScreenTools.defaultFontPixelHeight * 0.3
                // River path mode — LineSearch (centreline / riverbank) or Area
                // (lawnmower for wide rivers). OpenUxAS WaterwaySearch follows a
                // line, so centreline is the default.
                Row {
                    visible:    _root._uxKind === "river"
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCLabel { text: qsTr("River"); anchors.verticalCenter: parent.verticalCenter }
                    QGCComboBox {
                        width:          ScreenTools.defaultFontPixelWidth * 20
                        model:          [ qsTr("Centerline (line)"), qsTr("Riverbank (line)"), qsTr("Area (lawnmower)") ]
                        currentIndex:   _root._uxRiverMode === "bank" ? 1 : (_root._uxRiverMode === "area" ? 2 : 0)
                        onActivated:    (index) => {
                            _root._uxRiverMode = index === 1 ? "bank" : (index === 2 ? "area" : "center")
                            _root._uxBroadcastUi("river_mode", { "mode": _root._uxRiverMode })
                        }
                    }
                }
                QGCButton {
                    width:      parent.width
                    text:       _root._uxKind === "river" ? qsTr("Scan region for rivers") : qsTr("Scan region for roads")
                    onClicked:  _root._uxScan()
                }
                Row {
                    spacing:    ScreenTools.defaultFontPixelWidth * 0.4
                    QGCButton {
                        text:       qsTr("Select all")
                        onClicked:  { _root._uxSelectAll(_root._uxKind, true); _root._uxCalcBump++ }
                    }
                    QGCButton {
                        text:       qsTr("Clear")
                        onClicked:  { _root._uxClear(); _root._uxCalcBump++ }
                    }
                }
                // Feature list — checkbox per candidate name for the active kind.
                Rectangle {
                    color:          _uxPal.windowShade
                    border.color:   _uxPal.buttonBorder
                    border.width:   1
                    width:          parent.width
                    height:         Math.min(ScreenTools.defaultFontPixelHeight * 8,
                                             Math.max(ScreenTools.defaultFontPixelHeight * 1.5,
                                                      uxListColumn.implicitHeight + ScreenTools.defaultFontPixelHeight * 0.5))
                    Flickable {
                        anchors.fill:       parent
                        anchors.margins:    ScreenTools.defaultFontPixelWidth * 0.3
                        contentHeight:      uxListColumn.implicitHeight
                        clip:               true
                        Column {
                            id:             uxListColumn
                            width:          parent.width
                            spacing:        ScreenTools.defaultFontPixelHeight * 0.1
                            Repeater {
                                model: {
                                    var _ = _root._uxBump
                                    return _root._uxKind === "area" ? [] : _root._uxKindNames(_root._uxKind)
                                }
                                delegate: QGCCheckBox {
                                    width:      parent.width
                                    text:       modelData
                                    checked:    (_root._uxCandidates[_root._uxKind][modelData] || {}).selected || false
                                    onClicked:  { _root._uxSetSelected(_root._uxKind, modelData, checked); _root._uxCalcBump++ }
                                }
                            }
                            QGCLabel {
                                visible:    uxListColumn.children.length <= 1
                                text:       qsTr("(no features — press Scan view)")
                                color:      _uxPal.text
                                opacity:    0.6
                                font.pointSize: ScreenTools.smallFontPointSize
                            }
                        }
                    }
                }
                QGCButton {
                    width:      parent.width
                    primary:    true
                    text:       _root._uxKind === "river" ? qsTr("Publish River Search") : qsTr("Publish Road Search")
                    onClicked:  _root._uxPublish()
                }
                QGCLabel {
                    width:          parent.width
                    wrapMode:       Text.WordWrap
                    opacity:        0.7
                    color:          _uxPal.text
                    font.pointSize: ScreenTools.smallFontPointSize
                    text:           _root._uxKind === "river"
                                    ? qsTr("Check names, or click rivers on the map to toggle.")
                                    : qsTr("Check names, or click roads on the map to toggle.")
                }
            }

            // Status line (shared).
            QGCLabel {
                visible:        !uxasPanel.collapsed && _root._uxStatus !== ""
                text:           _root._uxStatus
                color:          _uxPal.warningText
                wrapMode:       Text.WordWrap
                width:          parent.width
                font.pointSize: ScreenTools.smallFontPointSize
            }
        }
    }
}
