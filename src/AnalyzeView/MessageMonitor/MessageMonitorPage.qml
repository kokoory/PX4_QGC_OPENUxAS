import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

import QGroundControl
import QGroundControl.Controls

// QGC Message Monitor: live view of EventBroadcaster + qgc_uxas_bridge LMCP traffic.
//
// Combines three message channels:
//   - EventBroadcaster Tx  (QGC -> UDP 45678, "event/tx")
//   - EventBroadcaster Rx  (UDP 45679 -> QGC, "command/rx")
//   - Bridge tap           (qgc_uxas_bridge.py -> UDP 45680, "bridge/{tx,rx}")
//
// Implements: filtering, pause/resume, clear, save/load .jsonl, replay.
// Replay strategy: the QML button replays via EventBroadcaster.sendEvent
// for "event" rows, and shells out to scripts/message_replay.py via QProcess
// (through plain JS spawn) for bridge / command rows so the actual UDP wire
// traffic gets re-emitted. See replay_button below for details.

AnalyzePage {
    id:                 root
    pageComponent:      pageComponent
    pageDescription:    qsTr("Live monitor for EventBroadcaster (UDP 45678/45679) and qgc_uxas_bridge.py LMCP traffic (UDP 45680). " +
                              "Pause, save, load and replay any captured stream.")
    allowPopout:        true

    property bool   paused:         false
    property string filterText:     ""
    property string filterChannel:  "all"      // "all", "event", "command", "bridge"
    property string filterDir:      "all"      // "all", "tx", "rx"
    property var    selectedRow:    null
    property int    maxRows:        5000

    // Per-channel counters
    property int    countEventTx:    0
    property int    countCommandRx:  0
    property int    countBridgeTx:   0
    property int    countBridgeRx:   0
    property int    countPlanRx:     0
    property int    countMavlink:    0

    QGCPalette { id: qgcPal; colorGroupEnabled: true }

    function _matches(entry) {
        if (filterChannel !== "all" && entry.channel !== filterChannel) return false
        if (filterDir !== "all" && entry.dir !== filterDir) return false
        if (filterText.length === 0) return true
        var hay = (entry.category + " " + entry.event + " " + entry.summary).toLowerCase()
        return hay.indexOf(filterText.toLowerCase()) !== -1
    }

    function _bump(entry) {
        if (entry.channel === "event" && entry.dir === "tx")       countEventTx++
        else if (entry.channel === "command" && entry.dir === "rx") countCommandRx++
        else if (entry.channel === "bridge" && entry.dir === "tx")  countBridgeTx++
        else if (entry.channel === "bridge" && entry.dir === "rx")  countBridgeRx++
        else if (entry.channel === "plan")                          countPlanRx++
        else if (entry.channel === "mavlink")                       countMavlink++
    }

    function _appendEntry(entry) {
        if (paused) return
        if (!_matches(entry)) {
            // Still bump counters even if filtered
            _bump(entry)
            return
        }
        _bump(entry)
        messageModel.append(entry)
        while (messageModel.count > maxRows) {
            messageModel.remove(0)
        }
        if (autoScrollCheck.checked) {
            messageView.positionViewAtEnd()
        }
    }

    function _formatTs(ms) {
        var d = new Date(ms)
        // HH:MM:SS.mmm
        function pad(n, w) { var s = "" + n; while (s.length < w) s = "0" + s; return s }
        return pad(d.getHours(), 2) + ":" + pad(d.getMinutes(), 2) + ":"
                + pad(d.getSeconds(), 2) + "." + pad(d.getMilliseconds(), 3)
    }

    function _channelLabel(entry) {
        if (entry.channel === "event")   return qsTr("EvBroadcaster Tx")
        if (entry.channel === "command") return qsTr("EvBroadcaster Rx")
        if (entry.channel === "plan")    return qsTr("UxAS Plan (Rx)")
        if (entry.channel === "mavlink") return entry.dir === "tx" ? qsTr("QGC -> Vehicle")
                                                                   : qsTr("Vehicle -> QGC")
        if (entry.channel === "bridge")  return entry.dir === "tx" ? qsTr("Bridge -> UxAS")
                                                                   : qsTr("UxAS -> Bridge")
        return entry.channel
    }

    function _stringifyPayload(entry) {
        try {
            return JSON.stringify(entry.payload, null, 2)
        } catch (e) {
            return "" + entry.payload
        }
    }

    function _serializeRowAsJsonl(entry) {
        // .jsonl one line per row — same shape as bridge tap so message_replay.py
        // can re-emit. For "event" we synthesize a dir field.
        var obj = {
            ts:        entry.ts / 1000.0,
            dir:       entry.channel === "event" ? "event_tx"
                       : (entry.channel === "command" ? "command_rx"
                       : (entry.dir === "tx" ? "uxas_out" : "uxas_in")),
            channel:   entry.channel,
            category:  entry.category,
            event:     entry.event,
            summary:   entry.summary,
            payload:   entry.payload
        }
        return JSON.stringify(obj)
    }

    function _refreshFromLog() {
        messageModel.clear()
        countEventTx = countCommandRx = countBridgeTx = countBridgeRx = countPlanRx = countMavlink = 0
        var log = EventBroadcaster.messageLog(maxRows)
        for (var i = 0; i < log.length; i++) {
            _appendEntry(log[i])
        }
    }

    ListModel { id: messageModel }

    Connections {
        target: EventBroadcaster
        function onMessageSent(category, event, data, epochMs) {
            _appendEntry({
                ts:       epochMs,
                dir:      "tx",
                channel:  "event",
                category: category,
                event:    event,
                summary:  event + (Object.keys(data || {}).length ? " " + JSON.stringify(data) : ""),
                payload:  data || {}
            })
        }
        function onMessageReceived(channel, payload, epochMs) {
            var entry
            if (channel === "command") {
                entry = {
                    ts:       epochMs,
                    dir:      "rx",
                    channel:  "command",
                    category: "command",
                    event:    payload.action || "(no action)",
                    summary:  JSON.stringify(payload),
                    payload:  payload
                }
            } else if (channel === "plan") {
                // UxAS-planned mission mirrored back from the search listener
                // (UDP 45681). payload = { vehicle_id, waypoints_count }.
                entry = {
                    ts:       epochMs,
                    dir:      "rx",
                    channel:  "plan",
                    category: "uxas_plan",
                    event:    "mission",
                    summary:  "vid=" + (payload.vehicle_id !== undefined ? payload.vehicle_id : "?")
                              + " wps=" + (payload.waypoints_count !== undefined ? payload.waypoints_count : "?"),
                    payload:  payload
                }
            } else if (channel === "mavlink") {
                // In-process tap of ordinary QGC↔vehicle traffic. payload is
                // either a command result ({command, command_name, result_name})
                // or a state change ({armed} / {flight_mode}).
                var isCmd = payload.command !== undefined
                var isArm = payload.armed !== undefined
                var vid   = payload.vehicle_id !== undefined ? payload.vehicle_id : "?"
                entry = {
                    ts:       epochMs,
                    dir:      isCmd ? "tx" : "rx",
                    channel:  "mavlink",
                    category: isCmd ? (payload.command_name || "command")
                              : (isArm ? "ARM_STATE" : "FLIGHT_MODE"),
                    event:    isCmd ? "command"
                              : (isArm ? (payload.armed ? "armed" : "disarmed") : "mode"),
                    summary:  isCmd
                              ? ("v" + vid + " " + (payload.command_name || payload.command)
                                 + " → " + (payload.result_name || payload.result))
                              : (isArm
                                 ? ("v" + vid + " " + (payload.armed ? "ARMED" : "DISARMED"))
                                 : ("v" + vid + " mode → " + payload.flight_mode)),
                    payload:  payload
                }
            } else {
                // bridge tap
                var dir = payload.dir || ""
                entry = {
                    ts:       epochMs,
                    dir:      dir.indexOf("out") !== -1 ? "tx" : "rx",
                    channel:  "bridge",
                    category: payload.lmcp_type || dir,
                    event:    dir,
                    summary:  payload.summary || "",
                    payload:  payload
                }
            }
            _appendEntry(entry)
        }
    }

    Component.onCompleted: _refreshFromLog()

    QGCFileDialog {
        id:             saveDialog
        title:          qsTr("Save message log (.jsonl)")
        nameFilters:    [ qsTr("JSONL files (*.jsonl)"), qsTr("All files (*)") ]
        onAcceptedForSave: (file) => {
            var lines = []
            for (var i = 0; i < messageModel.count; i++) {
                lines.push(_serializeRowAsJsonl(messageModel.get(i)))
            }
            saver.text = lines.join("\n") + "\n"
            saver.path = file
            saver.write()
            close()
        }
    }

    QGCFileDialog {
        id:             loadDialog
        title:          qsTr("Load message log (.jsonl)")
        nameFilters:    [ qsTr("JSONL files (*.jsonl)"), qsTr("All files (*)") ]
        onAcceptedForLoad: (file) => {
            loader.path = file
            var text = loader.read()
            messageModel.clear()
            countEventTx = countCommandRx = countBridgeTx = countBridgeRx = countPlanRx = countMavlink = 0
            var lines = text.split("\n")
            for (var i = 0; i < lines.length; i++) {
                var line = lines[i].trim()
                if (!line) continue
                try {
                    var obj = JSON.parse(line)
                    var dir = obj.dir || ""
                    var channel = obj.channel ||
                                  (dir === "event_tx" ? "event"
                                   : (dir === "command_rx" ? "command" : "bridge"))
                    var entry = {
                        ts:       (obj.ts || 0) * 1000,
                        dir:      channel === "event" ? "tx"
                                  : (channel === "command" ? "rx"
                                  : (dir.indexOf("out") !== -1 ? "tx" : "rx")),
                        channel:  channel,
                        category: obj.category || obj.lmcp_type || "",
                        event:    obj.event || obj.dir || "",
                        summary:  obj.summary || "",
                        payload:  obj.payload || obj
                    }
                    _bump(entry)
                    messageModel.append(entry)
                } catch (e) { /* ignore bad lines */ }
            }
            close()
        }
    }

    // Tiny file IO helper — uses XMLHttpRequest on file:// (works on desktop QGC).
    QtObject {
        id: saver
        property string path: ""
        property string text: ""
        function write() {
            var xhr = new XMLHttpRequest()
            xhr.open("PUT", path, false)
            xhr.send(text)
        }
    }
    QtObject {
        id: loader
        property string path: ""
        function read() {
            var xhr = new XMLHttpRequest()
            xhr.open("GET", path, false)
            xhr.send(null)
            return xhr.responseText || ""
        }
    }

    Component {
        id: pageComponent
        ColumnLayout {
            id:      mainCol
            width:   root.availableWidth
            height:  root.availableHeight
            spacing: ScreenTools.defaultFontPixelHeight / 2

            // --------------------------- Counters strip ----------------------
            RowLayout {
                Layout.fillWidth:   true
                spacing:            ScreenTools.defaultFontPixelWidth

                QGCLabel { text: qsTr("EvB Tx: %1").arg(countEventTx); font.bold: true }
                QGCLabel { text: qsTr("EvB Rx: %1").arg(countCommandRx); font.bold: true }
                QGCLabel { text: qsTr("Bridge->UxAS: %1").arg(countBridgeTx); font.bold: true }
                QGCLabel { text: qsTr("UxAS->Bridge: %1").arg(countBridgeRx); font.bold: true }
                QGCLabel { text: qsTr("UxAS Plan: %1").arg(countPlanRx); font.bold: true; color: qgcPal.colorBlue }
                QGCLabel { text: qsTr("MAVLink: %1").arg(countMavlink); font.bold: true; color: qgcPal.colorGreen }
                Item { Layout.fillWidth: true }
                QGCLabel {
                    text: qsTr("Rows: %1 / %2").arg(messageModel.count).arg(maxRows)
                    color: messageModel.count >= maxRows ? qgcPal.warningText : qgcPal.text
                }
            }

            // --------------------------- Filter strip ------------------------
            RowLayout {
                Layout.fillWidth:   true
                spacing:            ScreenTools.defaultFontPixelWidth

                QGCLabel { text: qsTr("Channel:") }
                QGCComboBox {
                    id:    channelCombo
                    model: [ "all", "event", "command", "bridge", "plan", "mavlink" ]
                    sizeToContents: true
                    onActivated: filterChannel = currentText
                }
                QGCLabel { text: qsTr("Direction:") }
                QGCComboBox {
                    id:    dirCombo
                    model: [ "all", "tx", "rx" ]
                    sizeToContents: true
                    onActivated: filterDir = currentText
                }
                QGCLabel { text: qsTr("Search:") }
                QGCTextField {
                    id:               searchField
                    Layout.fillWidth: true
                    placeholderText:  qsTr("filter by text…")
                    onTextChanged:    filterText = text
                }
                QGCButton {
                    text: qsTr("Apply / Re-fetch")
                    onClicked: _refreshFromLog()
                }
            }

            // --------------------------- Action buttons ----------------------
            RowLayout {
                Layout.fillWidth:   true
                spacing:            ScreenTools.defaultFontPixelWidth

                QGCButton {
                    text: paused ? qsTr("Resume") : qsTr("Pause")
                    onClicked: paused = !paused
                }
                QGCButton {
                    text: qsTr("Clear")
                    onClicked: {
                        messageModel.clear()
                        countEventTx = countCommandRx = countBridgeTx = countBridgeRx = countPlanRx = countMavlink = 0
                        EventBroadcaster.clearMessageLog()
                        selectedRow = null
                    }
                }
                QGCButton {
                    text: qsTr("Save .jsonl…")
                    onClicked: { saveDialog.openForSave() }
                }
                QGCButton {
                    text: qsTr("Load .jsonl…")
                    onClicked: { loadDialog.openForLoad() }
                }
                Item { Layout.fillWidth: true }

                QGCLabel { text: qsTr("Replay rate:") }
                QGCComboBox {
                    id:    rateCombo
                    model: [ "0.25x", "1x", "5x" ]
                    sizeToContents: true
                    currentIndex: 1
                    property real factor: currentIndex === 0 ? 0.25
                                            : currentIndex === 2 ? 5.0 : 1.0
                }
                QGCCheckBox {
                    id:      autoScrollCheck
                    text:    qsTr("Auto-scroll")
                    checked: true
                }
                QGCButton {
                    text: qsTr("Replay")
                    enabled: messageModel.count > 0
                    onClicked: {
                        // In-process replay: re-emit through EventBroadcaster.sendEvent
                        // (handles "event" rows directly so the UI repaints), and
                        // dispatch other rows to a faux event so they re-appear in
                        // the panel. For real UDP-wire replay use scripts/message_replay.py.
                        var rate = rateCombo.factor
                        var entries = []
                        for (var i = 0; i < messageModel.count; i++) {
                            entries.push(messageModel.get(i))
                        }
                        if (entries.length === 0) return
                        replayTimer.entries = entries
                        replayTimer.idx     = 0
                        replayTimer.rate    = rate
                        replayTimer.t0      = Date.now()
                        replayTimer.firstTs = entries[0].ts
                        replayTimer.interval = 1
                        replayTimer.start()
                    }
                }
            }

            // --------------------------- Main split --------------------------
            RowLayout {
                Layout.fillWidth:   true
                Layout.fillHeight:  true
                spacing:            ScreenTools.defaultFontPixelWidth

                // ---- Table ---------------------------------------------------
                Rectangle {
                    Layout.fillWidth:   true
                    Layout.fillHeight:  true
                    color:              qgcPal.windowShade
                    border.color:       qgcPal.windowShadeDark
                    border.width:       1
                    radius:             2

                    ColumnLayout {
                        anchors.fill:           parent
                        anchors.margins:        ScreenTools.defaultFontPixelWidth / 2
                        spacing:                0

                        // Header row
                        RowLayout {
                            Layout.fillWidth:   true
                            spacing:            ScreenTools.defaultFontPixelWidth
                            QGCLabel { text: qsTr("Time");      font.bold: true; Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 11 }
                            QGCLabel { text: qsTr("Dir");       font.bold: true; Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 3 }
                            QGCLabel { text: qsTr("Channel");   font.bold: true; Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 18 }
                            QGCLabel { text: qsTr("Category/Type"); font.bold: true; Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 22 }
                            QGCLabel { text: qsTr("Summary");   font.bold: true; Layout.fillWidth: true }
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: qgcPal.windowShadeDark }

                        ListView {
                            id:                 messageView
                            Layout.fillWidth:   true
                            Layout.fillHeight:  true
                            clip:               true
                            model:              messageModel
                            spacing:            0
                            boundsBehavior:     Flickable.StopAtBounds

                            // Auto-scroll: defer to after the new row is laid out
                            // (positionViewAtEnd right after append runs before the
                            // delegate exists, so it never reaches the true bottom).
                            onCountChanged: if (autoScrollCheck.checked) Qt.callLater(positionViewAtEnd)

                            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                            delegate: Rectangle {
                                width:  messageView.width
                                height: rowLayout.implicitHeight + ScreenTools.defaultFontPixelHeight / 4
                                color:  selectedRow && selectedRow.idx === index
                                            ? qgcPal.buttonHighlight
                                            : (index % 2 === 0 ? qgcPal.window : qgcPal.windowShade)

                                MouseArea {
                                    anchors.fill: parent
                                    onClicked: selectedRow = {
                                        idx:      index,
                                        ts:       ts,
                                        dir:      dir,
                                        channel:  channel,
                                        category: category,
                                        event:    event,
                                        summary:  summary,
                                        payload:  payload
                                    }
                                }

                                RowLayout {
                                    id:             rowLayout
                                    anchors.fill:   parent
                                    anchors.leftMargin:  ScreenTools.defaultFontPixelWidth / 2
                                    anchors.rightMargin: ScreenTools.defaultFontPixelWidth / 2
                                    spacing:        ScreenTools.defaultFontPixelWidth
                                    QGCLabel {
                                        text: _formatTs(ts)
                                        Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 11
                                        color: qgcPal.text
                                    }
                                    QGCLabel {
                                        text: dir
                                        Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 3
                                        color: dir === "tx" ? qgcPal.colorGreen : qgcPal.colorOrange
                                    }
                                    QGCLabel {
                                        text: _channelLabel({channel: channel, dir: dir})
                                        Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 18
                                        color: channel === "bridge" ? qgcPal.colorBlue : qgcPal.text
                                    }
                                    QGCLabel {
                                        text:        category
                                        elide:       Text.ElideRight
                                        Layout.preferredWidth: ScreenTools.defaultFontPixelWidth * 22
                                    }
                                    QGCLabel {
                                        text:               summary
                                        elide:              Text.ElideRight
                                        Layout.fillWidth:   true
                                    }
                                }
                            }
                        }
                    }
                }

                // ---- Detail pane --------------------------------------------
                Rectangle {
                    Layout.preferredWidth: parent.width * 0.35
                    Layout.fillHeight:     true
                    color:                 qgcPal.windowShade
                    border.color:          qgcPal.windowShadeDark
                    border.width:          1
                    radius:                2

                    ColumnLayout {
                        anchors.fill:           parent
                        anchors.margins:        ScreenTools.defaultFontPixelWidth / 2
                        spacing:                ScreenTools.defaultFontPixelHeight / 4

                        QGCLabel {
                            text: selectedRow
                                ? qsTr("Selected: %1 / %2 @ %3").arg(_channelLabel(selectedRow)).arg(selectedRow.category).arg(_formatTs(selectedRow.ts))
                                : qsTr("(click a row to inspect)")
                            font.bold: true
                            wrapMode:  Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: qgcPal.windowShadeDark }

                        ScrollView {
                            Layout.fillWidth:   true
                            Layout.fillHeight:  true
                            clip:               true
                            TextArea {
                                id:             detailArea
                                readOnly:       true
                                wrapMode:       TextEdit.Wrap
                                font.family:    "monospace"
                                color:          qgcPal.text
                                background:     Rectangle { color: "transparent" }
                                text:           selectedRow ? _stringifyPayload(selectedRow) : ""
                            }
                        }
                    }
                }
            }
        }
    }

    // Replay timer — drives delayed re-emission at the user-chosen rate.
    Timer {
        id:         replayTimer
        repeat:     true
        interval:   100
        property var entries: []
        property int idx: 0
        property real rate: 1.0
        property real t0: 0
        property real firstTs: 0
        onTriggered: {
            if (idx >= entries.length) { stop(); return }
            var elapsedReal = Date.now() - t0
            var entry = entries[idx]
            // When should this entry fire? (entry.ts - firstTs) / rate ms after t0
            var due = (entry.ts - firstTs) / rate
            if (elapsedReal >= due) {
                if (entry.channel === "event") {
                    EventBroadcaster.sendEvent(entry.category, entry.event,
                                               entry.payload || {})
                } else {
                    // Re-broadcast through sendEvent so it appears in the monitor
                    // (and on UDP 45678). This is the simpler, in-app path.
                    EventBroadcaster.sendEvent("replay", entry.event || entry.category,
                                               { _channel: entry.channel,
                                                 _dir: entry.dir,
                                                 _summary: entry.summary,
                                                 _payload: entry.payload || {} })
                }
                idx++
            }
        }
    }
}
