#pragma once

#include <QObject>
#include <QUdpSocket>
#include <QJsonObject>
#include <QJsonDocument>
#include <QDateTime>
#include <QHostAddress>
#include <QMutex>
#include <QVariantList>
#include <QtQmlIntegration/QtQmlIntegration>

#include <deque>

class Vehicle;

/// Broadcasts UI events over UDP so external programs can monitor QGC interactions.
/// Listens on a configurable receive port (default 45679) for external commands
/// and on a "monitor" port (default 45680) for the qgc_uxas_bridge.py LMCP tap.
///
/// Usage from QML:
///   EventBroadcaster.sendEvent("button", "arm_clicked", {"vehicle": 1})
///   EventBroadcaster.sendEvent("view", "fly_view_opened", {})
///
/// External programs receive JSON on UDP port 45678:
///   {"timestamp": 1234567890.123, "category": "button", "event": "arm_clicked", "data": {"vehicle": 1}}
///
/// External programs can send commands to QGC on UDP port 45679:
///   {"action": "arm"}
///   {"action": "takeoff", "altitude": 15}
///   {"action": "set_mode", "mode": "posctl"}
///
/// The qgc_uxas_bridge.py fire-and-forgets a LMCP-summary JSON on UDP 45680:
///   {"ts": 1781018000.123, "dir": "uxas_out", "lmcp_type": "AirVehicleState",
///    "summary": "vid=1 lat=37.62 ...", "vehicle_id": 1}
class EventBroadcaster : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    /// Shared planning overlay (UxAS search geometry) so the 2D flight map can
    /// draw what was planned in the 3D Cesium view. Shape (from QML/JS):
    ///   { area: [[lat,lon],...],
    ///     roads:  [ [[lat,lon],...], ... ],
    ///     rivers: [ [[lat,lon],...], ... ] }
    Q_PROPERTY(QVariant planOverlay READ planOverlay WRITE setPlanOverlay NOTIFY planOverlayChanged)

    /// UxAS-generated MissionCommand waypoints, mirrored from the UxAS PUB
    /// without waiting for the bridge→PX4→MAVLink round-trip. Updated when a
    /// "uxas_plan" / "mission" event arrives on the plan-mirror UDP port
    /// (default 45681). Shape (from QML/JS):
    ///   { "1": [[lat,lon,alt],...], "2": [[lat,lon,alt],...] }   keyed by
    /// vehicle id as a string (QVariantMap key must be a string).
    Q_PROPERTY(QVariantMap uxasPlannedWaypoints READ uxasPlannedWaypoints NOTIFY uxasPlannedWaypointsChanged)

    /// VWorld 3D layer visibility toggles (shared so the FlyView toolbar can
    /// switch them and the 3D Cesium view reacts). Default on.
    Q_PROPERTY(bool showVWorldBuildings READ showVWorldBuildings WRITE setShowVWorldBuildings NOTIFY showVWorldBuildingsChanged)
    Q_PROPERTY(bool showVWorldRoads     READ showVWorldRoads     WRITE setShowVWorldRoads     NOTIFY showVWorldRoadsChanged)
    Q_PROPERTY(bool showVWorldRivers    READ showVWorldRivers    WRITE setShowVWorldRivers    NOTIFY showVWorldRiversChanged)

public:
    explicit EventBroadcaster(QObject *parent = nullptr);
    ~EventBroadcaster();

    static EventBroadcaster *instance();

    QVariant planOverlay() const { return _planOverlay; }
    Q_INVOKABLE void setPlanOverlay(const QVariant &overlay);

    bool showVWorldBuildings() const { return _showVWorldBuildings; }
    bool showVWorldRoads()     const { return _showVWorldRoads; }
    bool showVWorldRivers()    const { return _showVWorldRivers; }
    void setShowVWorldBuildings(bool on);
    void setShowVWorldRoads(bool on);
    void setShowVWorldRivers(bool on);

    QVariantMap uxasPlannedWaypoints() const { return _uxasPlannedWaypoints; }

    /// Programmatic injection of a UxAS-planned mission for a given vehicle.
    /// Used by tests and by the UDP listener. ``waypoints`` is a list of
    /// [lat, lon, alt] triplets.
    Q_INVOKABLE void setUxasPlannedWaypoints(int vehicleId, const QVariantList &waypoints);

    /// Drop the cached plan for one vehicle (or every vehicle if id<=0).
    Q_INVOKABLE void clearUxasPlannedWaypoints(int vehicleId = 0);

    /// Send a UI event to all listeners
    /// @param category  Event category (e.g., "button", "view", "setting", "map", "mission")
    /// @param event     Event name (e.g., "arm_clicked", "fly_view_opened")
    /// @param data      Additional event data as QVariantMap (optional)
    Q_INVOKABLE void sendEvent(const QString &category, const QString &event, const QVariantMap &data = {});

    /// Set the broadcast port (default: 45678)
    Q_INVOKABLE void setBroadcastPort(quint16 port);

    /// Set the receive port for incoming commands (default: 45679)
    Q_INVOKABLE void setReceivePort(quint16 port);

    /// Set the monitor port for the bridge LMCP tap (default: 45680)
    Q_INVOKABLE void setMonitorPort(quint16 port);

    /// Set the listen port for UxAS-plan mirror messages (default: 45681)
    Q_INVOKABLE void setPlanMirrorPort(quint16 port);

    /// Enable/disable broadcasting
    Q_INVOKABLE void setEnabled(bool enabled);
    Q_INVOKABLE bool isEnabled() const { return _enabled; }

    /// Returns the last N (default 1000) captured messages as a QVariantList for QML.
    /// Each entry is a QVariantMap with keys: ts (ms epoch), dir, channel, category,
    /// event, summary, payload.
    Q_INVOKABLE QVariantList messageLog(int maxRows = 1000) const;

    /// Clear the in-memory message log buffer.
    Q_INVOKABLE void clearMessageLog();

signals:
    void eventSent(const QString &category, const QString &event);

    /// Emitted when the shared planning overlay changes (3D -> 2D map)
    void planOverlayChanged();

    /// Emitted whenever the UxAS-planned mirror map changes (a new
    /// MissionCommand arrived for some vehicle).
    void uxasPlannedWaypointsChanged();

    void showVWorldBuildingsChanged();
    void showVWorldRoadsChanged();
    void showVWorldRiversChanged();

    /// Emitted when an external command is received via UDP
    /// @param action  The action string (e.g., "arm", "takeoff", "land")
    /// @param params  Additional parameters from the JSON command
    void commandReceived(const QString &action, const QVariantMap &params);

    /// Emitted on every outbound UI event broadcast (tap).
    /// @param category  e.g. "button"
    /// @param event     e.g. "arm_clicked"
    /// @param data      Free-form payload
    /// @param epochMs   QDateTime::currentMSecsSinceEpoch() at send time
    void messageSent(const QString &category, const QString &event,
                     const QVariantMap &data, qint64 epochMs);

    /// Emitted on every inbound message — commands on the receive port AND
    /// LMCP-tap pings from qgc_uxas_bridge.py on the monitor port.
    /// @param channel   "command" (45679), "bridge" (45680), "plan" (45681)
    ///                  or "mavlink" (in-process tap of QGC↔vehicle traffic)
    /// @param payload   The raw JSON dict
    /// @param epochMs   When QGC observed it
    void messageReceived(const QString &channel, const QVariantMap &payload,
                         qint64 epochMs);

private slots:
    void _onReadyRead();
    void _onMonitorReadyRead();
    void _onPlanMirrorReadyRead();

    /// MAVLink tap — capture ordinary QGC↔vehicle operational traffic
    /// (commands the operator triggers + resulting state changes) so the
    /// Message Monitor can show, save and replay them like UxAS traffic.
    void _onVehicleAdded(Vehicle *vehicle);
    void _onMavCommandResult(int vehicleId, int targetComponent, int command,
                             int ackResult, int failureCode);
    void _onVehicleArmedChanged(bool armed);
    void _onVehicleFlightModeChanged(const QString &flightMode);

private:
    struct LogEntry {
        qint64 epochMs;
        QString dir;      // "tx" or "rx"
        QString channel;  // "event", "command", "bridge"
        QString category; // category or lmcp_type
        QString event;    // event or "uxas_out"/"uxas_in"/...
        QString summary;
        QVariantMap payload;
    };

    void _broadcast(const QByteArray &data);
    void _bindReceiveSocket();
    void _bindMonitorSocket();
    void _bindPlanMirrorSocket();
    void _appendLog(const LogEntry &entry);
    static QString _summariseEvent(const QString &event, const QVariantMap &data);
    /// Log a captured MAVLink-tap entry to the central buffer and emit
    /// messageReceived("mavlink", ...) so every Monitor page (incl. popouts)
    /// updates live.
    void _logMavlink(const QString &dir, const QString &category,
                     const QString &event, const QString &summary,
                     const QVariantMap &payload);
    void _connectMavlinkTap();
    static QString _mavCmdName(int command);
    static QString _mavResultName(int result);

    QVariant _planOverlay;
    bool _showVWorldBuildings = true;
    bool _showVWorldRoads     = true;
    bool _showVWorldRivers    = true;
    QVariantMap _uxasPlannedWaypoints;
    static EventBroadcaster *_instance;
    QUdpSocket *_socket = nullptr;
    QUdpSocket *_receiveSocket = nullptr;
    QUdpSocket *_monitorSocket = nullptr;
    QUdpSocket *_planMirrorSocket = nullptr;
    quint16 _broadcastPort = 45678;
    quint16 _receivePort = 45679;
    quint16 _monitorPort = 45680;
    quint16 _planMirrorPort = 45681;
    bool _enabled = true;
    quint64 _eventCounter = 0;

    mutable QMutex _logMutex;
    std::deque<LogEntry> _log;
    static constexpr int kMaxLogRows = 1000;
};
