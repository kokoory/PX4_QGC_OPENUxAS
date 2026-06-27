import QtQuick

import QGroundControl
import QGroundControl.Controls
import QGroundControl.Toolbar

Item {
    implicitWidth: mainLayout.width + _widthMargin

    property var  _activeVehicle:           QGroundControl.multiVehicleManager.activeVehicle
    property real _toolIndicatorMargins:    ScreenTools.defaultFontPixelHeight * 0.66
    property real _widthMargin:             _toolIndicatorMargins * 2

    Row {
        id:                 mainLayout
        anchors.margins:    _toolIndicatorMargins
        anchors.left:       parent.left
        anchors.top:        parent.top
        anchors.bottom:     parent.bottom
        spacing:            ScreenTools.defaultFontPixelWidth * 1.75

        Repeater {
            id:     appRepeater
            model:  QGroundControl.corePlugin.toolBarIndicators
            Loader {
                anchors.top:        parent.top
                anchors.bottom:     parent.bottom
                source:             modelData
                visible:            item.showIndicator
            }
        }

        Repeater {
            id:     toolIndicatorsRepeater
            model:  _activeVehicle ? _activeVehicle.toolIndicators : []

            Loader {
                anchors.top:        parent.top
                anchors.bottom:     parent.bottom
                source:             modelData
                visible:            item.showIndicator
            }
        }

        // VWorld 3D layer toggles (buildings / roads / rivers) — show/hide the
        // VWorld vectors drawn in the 3D Cesium view. State lives on the shared
        // EventBroadcaster so the 3D view reacts.
        Row {
            anchors.verticalCenter: parent.verticalCenter
            spacing:                ScreenTools.defaultFontPixelWidth
            visible:                QGroundControl.settingsManager.appSettings.cesiumToken.rawValue !== ""
            QGCLabel {
                anchors.verticalCenter: parent.verticalCenter
                text:                   qsTr("VWorld:")
                opacity:                0.8
            }
            QGCCheckBox {
                anchors.verticalCenter: parent.verticalCenter
                text:                   qsTr("Bldg")
                checked:                EventBroadcaster.showVWorldBuildings
                onClicked:              EventBroadcaster.showVWorldBuildings = checked
            }
            QGCCheckBox {
                anchors.verticalCenter: parent.verticalCenter
                text:                   qsTr("Road")
                checked:                EventBroadcaster.showVWorldRoads
                onClicked:              EventBroadcaster.showVWorldRoads = checked
            }
            QGCCheckBox {
                anchors.verticalCenter: parent.verticalCenter
                text:                   qsTr("River")
                checked:                EventBroadcaster.showVWorldRivers
                onClicked:              EventBroadcaster.showVWorldRivers = checked
            }
        }
    }
}
