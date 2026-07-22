pragma Singleton
import QtQuick

import QGroundControl

/// Global UI record/replay bus.
///
/// Every interactive QGC base control (QGCButton / QGCCheckBox / QGCRadioButton /
/// QGCComboBox / QGCTextField / QGCSlider) registers itself here and reports each
/// user interaction. Because virtually all of QGC's UI is built from those base
/// controls, instrumenting them here makes (almost) the whole app:
///   - BROADCAST:  every click / toggle / edit / selection is emitted on the
///                 EventBroadcaster "ui" channel (id + type + value), so it can be
///                 watched, logged and replayed.
///   - REPLAYABLE: an inbound {"action":"ui","id":<id>,"value":<v>} command on the
///                 EventBroadcaster Rx port drives the matching control exactly as
///                 a human would (emits its real signal), so an external scenario
///                 file can operate the entire UI start-to-finish.
///
/// Control ids are stable-ish and human readable: an explicit `uiId` if the
/// control sets one, else `objectName`, else `<type>:<text/label>` with a `#n`
/// suffix when several controls share the same label.
QtObject {
    id: root

    property var  _registry: ({})   // id -> { obj, type }
    property var  _keyCount: ({})    // label key -> count (for #n disambiguation)
    property bool _replaying: false  // true while actuating, to suppress echo

    function _label(obj) {
        if (obj.objectName && String(obj.objectName).length) return String(obj.objectName)
        if (obj.text !== undefined && String(obj.text).length) return String(obj.text)
        // ComboBox has no `text`; use its current selection so it gets a readable
        // id (e.g. "combobox:Wide (45°)") instead of a bare "combobox:#n".
        if (obj.currentText !== undefined && String(obj.currentText).length) return String(obj.currentText)
        if (obj.placeholderText !== undefined && String(obj.placeholderText).length) return String(obj.placeholderText)
        if (obj.iconSource !== undefined && String(obj.iconSource).length) {
            var s = String(obj.iconSource)
            return s.substring(s.lastIndexOf("/") + 1)
        }
        return ""
    }

    function register(obj, ctype, explicitId) {
        var id
        if (explicitId && String(explicitId).length) {
            id = String(explicitId)
        } else {
            var key = ctype + ":" + _label(obj)
            var n = root._keyCount[key] || 0
            root._keyCount[key] = n + 1
            id = (n === 0) ? key : (key + "#" + n)
        }
        root._registry[id] = { "obj": obj, "type": ctype }
        return id
    }

    function unregister(id) {
        if (id && root._registry[id]) delete root._registry[id]
    }

    function _value(obj, ctype) {
        switch (ctype) {
        case "checkbox":
        case "radio":     return obj.checked
        case "textfield": return String(obj.text)
        case "combobox":  return obj.currentIndex
        case "slider":    return obj.value
        }
        return undefined
    }

    // Called by a control when the user interacts with it.
    function captured(obj, ctype, event) {
        if (root._replaying) return
        var id = obj.uiScriptId
        if (!id || !String(id).length) return
        var data = { "id": id, "type": ctype }
        var v = _value(obj, ctype)
        if (v !== undefined) data["value"] = v
        if (obj.text !== undefined && String(obj.text).length) data["text"] = String(obj.text)
        EventBroadcaster.sendEvent("ui", event, data)
    }

    // Inbound: drive a control by id exactly as a click/edit would.
    function actuate(id, params) {
        var e = root._registry[id]
        if (!e) { console.warn("UIScript: unknown control id: " + id); return false }
        var obj = e.obj, t = e.type
        var v = (params && params["value"] !== undefined) ? params["value"] : undefined
        root._replaying = true
        try {
            switch (t) {
            case "button":
                obj.clicked()
                break
            case "checkbox":
            case "radio":
                if (v !== undefined) obj.checked = (v === true || v === "true" || v === 1 || v === "1")
                obj.clicked()
                break
            case "textfield":
                if (v !== undefined) obj.text = String(v)
                obj.editingFinished()
                break
            case "combobox":
                if (v !== undefined) obj.currentIndex = Number(v)
                obj.activated(obj.currentIndex)
                break
            case "slider":
                if (v !== undefined) obj.value = Number(v)
                obj.moved()
                break
            }
        } catch (err) {
            console.warn("UIScript actuate error [" + id + "]: " + err)
        }
        root._replaying = false
        return true
    }

    // Emit the full catalogue of currently-registered controls (discovery).
    function dumpRegistry() {
        for (var k in root._registry) {
            EventBroadcaster.sendEvent("ui_registry", k, { "type": root._registry[k].type })
        }
    }

    Component.onCompleted: {
        EventBroadcaster.commandReceived.connect(function(action, params) {
            if (action !== "ui" || !params) return
            if (params["id"] === "__dump__") { root.dumpRegistry(); return }
            if (params["id"]) root.actuate(String(params["id"]), params)
        })
    }
}
