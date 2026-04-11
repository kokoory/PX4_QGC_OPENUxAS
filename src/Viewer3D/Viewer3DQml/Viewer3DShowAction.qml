import QGroundControl
import QGroundControl.Controls

ToolStripAction {
    id: root

    property int _displayMode: QGCViewer3DManager.displayMode
    property bool _viewer3DEnabled: QGroundControl.settingsManager.viewer3DSettings.enabled.rawValue
    property bool _hasCesiumToken: QGroundControl.settingsManager.appSettings.cesiumToken.rawValue !== ""

    iconSource: _displayMode === QGCViewer3DManager.Map
                    ? "/qml/QGroundControl/Viewer3D/City3DMapIcon.svg"
                    : "/qmlimages/PaperPlane.svg"
    text: {
        switch (_displayMode) {
        case QGCViewer3DManager.Map:
            return _hasCesiumToken ? qsTr("Cesium 3D") : qsTr("3D View")
        case QGCViewer3DManager.View3D:
            return qsTr("Fly")
        case QGCViewer3DManager.Cesium3D:
            return qsTr("Fly")
        }
        return qsTr("3D View")
    }
    visible: _viewer3DEnabled || _hasCesiumToken

    onTriggered: {
        switch (_displayMode) {
        case QGCViewer3DManager.Map:
            if (_hasCesiumToken) {
                QGCViewer3DManager.setDisplayMode(QGCViewer3DManager.Cesium3D);
            } else if (_viewer3DEnabled) {
                QGCViewer3DManager.setDisplayMode(QGCViewer3DManager.View3D);
            }
            break;
        case QGCViewer3DManager.View3D:
        case QGCViewer3DManager.Cesium3D:
            QGCViewer3DManager.setDisplayMode(QGCViewer3DManager.Map);
            break;
        }
    }
}
