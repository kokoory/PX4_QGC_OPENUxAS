#include "EventBroadcaster.h"
#include "QGCLoggingCategory.h"
#include "MultiVehicleManager.h"
#include "Vehicle.h"
#include "QmlObjectListModel.h"

#include <QJsonArray>
#include <QMutexLocker>
#include <QMetaObject>

QGC_LOGGING_CATEGORY(EventBroadcasterLog, "EventBroadcaster")

EventBroadcaster *EventBroadcaster::_instance = nullptr;

EventBroadcaster::EventBroadcaster(QObject *parent)
    : QObject(parent)
    , _socket(new QUdpSocket(this))
    , _receiveSocket(new QUdpSocket(this))
    , _monitorSocket(new QUdpSocket(this))
    , _planMirrorSocket(new QUdpSocket(this))
{
    _instance = this;

    _bindReceiveSocket();
    connect(_receiveSocket, &QUdpSocket::readyRead, this, &EventBroadcaster::_onReadyRead);

    _bindMonitorSocket();
    connect(_monitorSocket, &QUdpSocket::readyRead, this, &EventBroadcaster::_onMonitorReadyRead);

    _bindPlanMirrorSocket();
    connect(_planMirrorSocket, &QUdpSocket::readyRead, this, &EventBroadcaster::_onPlanMirrorReadyRead);

    // Capture ordinary QGC↔vehicle MAVLink operational traffic into the same
    // monitor/replay log as the UxAS events. Deferred to the event loop so
    // MultiVehicleManager's singleton is guaranteed constructed first.
    QMetaObject::invokeMethod(this, &EventBroadcaster::_connectMavlinkTap,
                              Qt::QueuedConnection);

    qCDebug(EventBroadcasterLog) << "EventBroadcaster initialized, broadcasting on port" << _broadcastPort
                                 << ", receiving on port" << _receivePort
                                 << ", monitor (bridge tap) on port" << _monitorPort
                                 << ", plan mirror on port" << _planMirrorPort;
}

void EventBroadcaster::_connectMavlinkTap()
{
    MultiVehicleManager *mvm = MultiVehicleManager::instance();
    if (!mvm) {
        return;
    }
    connect(mvm, &MultiVehicleManager::vehicleAdded,
            this, &EventBroadcaster::_onVehicleAdded, Qt::UniqueConnection);
    // Hook vehicles that already exist (e.g. monitor opened after connect).
    if (QmlObjectListModel *vehicles = mvm->vehicles()) {
        for (int i = 0; i < vehicles->count(); ++i) {
            if (Vehicle *v = vehicles->value<Vehicle *>(i)) {
                _onVehicleAdded(v);
            }
        }
    }
    qCDebug(EventBroadcasterLog) << "MAVLink tap connected to MultiVehicleManager";
}

void EventBroadcaster::_onVehicleAdded(Vehicle *vehicle)
{
    if (!vehicle) {
        return;
    }
    connect(vehicle, &Vehicle::mavCommandResult,
            this, &EventBroadcaster::_onMavCommandResult, Qt::UniqueConnection);
    connect(vehicle, &Vehicle::armedChanged,
            this, &EventBroadcaster::_onVehicleArmedChanged, Qt::UniqueConnection);
    connect(vehicle, &Vehicle::flightModeChanged,
            this, &EventBroadcaster::_onVehicleFlightModeChanged, Qt::UniqueConnection);
}

void EventBroadcaster::_onMavCommandResult(int vehicleId, int targetComponent,
                                           int command, int ackResult,
                                           int failureCode)
{
    QVariantMap p;
    p.insert(QStringLiteral("vehicle_id"), vehicleId);
    p.insert(QStringLiteral("target_component"), targetComponent);
    p.insert(QStringLiteral("command"), command);
    p.insert(QStringLiteral("command_name"), _mavCmdName(command));
    p.insert(QStringLiteral("result"), ackResult);
    p.insert(QStringLiteral("result_name"), _mavResultName(ackResult));
    p.insert(QStringLiteral("failure_code"), failureCode);
    const QString summary = QStringLiteral("v%1 %2 → %3")
                                .arg(vehicleId)
                                .arg(_mavCmdName(command))
                                .arg(_mavResultName(ackResult));
    _logMavlink(QStringLiteral("tx"), _mavCmdName(command),
                QStringLiteral("command"), summary, p);
}

void EventBroadcaster::_onVehicleArmedChanged(bool armed)
{
    Vehicle *v = qobject_cast<Vehicle *>(sender());
    const int vid = v ? v->id() : 0;
    QVariantMap p;
    p.insert(QStringLiteral("vehicle_id"), vid);
    p.insert(QStringLiteral("armed"), armed);
    _logMavlink(QStringLiteral("rx"), QStringLiteral("ARM_STATE"),
                armed ? QStringLiteral("armed") : QStringLiteral("disarmed"),
                QStringLiteral("v%1 %2").arg(vid)
                    .arg(armed ? QStringLiteral("ARMED") : QStringLiteral("DISARMED")),
                p);
}

void EventBroadcaster::_onVehicleFlightModeChanged(const QString &flightMode)
{
    Vehicle *v = qobject_cast<Vehicle *>(sender());
    const int vid = v ? v->id() : 0;
    QVariantMap p;
    p.insert(QStringLiteral("vehicle_id"), vid);
    p.insert(QStringLiteral("flight_mode"), flightMode);
    _logMavlink(QStringLiteral("rx"), QStringLiteral("FLIGHT_MODE"),
                QStringLiteral("mode"),
                QStringLiteral("v%1 mode → %2").arg(vid).arg(flightMode), p);
}

void EventBroadcaster::_logMavlink(const QString &dir, const QString &category,
                                   const QString &event, const QString &summary,
                                   const QVariantMap &payload)
{
    const qint64 epochMs = QDateTime::currentMSecsSinceEpoch();
    LogEntry entry;
    entry.epochMs = epochMs;
    entry.dir = dir;
    entry.channel = QStringLiteral("mavlink");
    entry.category = category;
    entry.event = event;
    entry.summary = summary;
    entry.payload = payload;
    _appendLog(entry);
    emit messageReceived(QStringLiteral("mavlink"), payload, epochMs);
}

QString EventBroadcaster::_mavCmdName(int command)
{
    switch (command) {
    case 16:  return QStringLiteral("NAV_WAYPOINT");
    case 17:  return QStringLiteral("NAV_LOITER_UNLIM");
    case 19:  return QStringLiteral("NAV_LOITER_TIME");
    case 20:  return QStringLiteral("NAV_RETURN_TO_LAUNCH");
    case 21:  return QStringLiteral("NAV_LAND");
    case 22:  return QStringLiteral("NAV_TAKEOFF");
    case 84:  return QStringLiteral("NAV_VTOL_TAKEOFF");
    case 85:  return QStringLiteral("NAV_VTOL_LAND");
    case 176: return QStringLiteral("DO_SET_MODE");
    case 178: return QStringLiteral("DO_CHANGE_SPEED");
    case 179: return QStringLiteral("DO_SET_HOME");
    case 192: return QStringLiteral("DO_REPOSITION");
    case 252: return QStringLiteral("OVERRIDE_GOTO");
    case 400: return QStringLiteral("COMPONENT_ARM_DISARM");
    case 410: return QStringLiteral("GET_HOME_POSITION");
    case 511: return QStringLiteral("SET_MESSAGE_INTERVAL");
    case 519: return QStringLiteral("REQUEST_PROTOCOL_VERSION");
    case 520: return QStringLiteral("REQUEST_AUTOPILOT_CAPABILITIES");
    case 530: return QStringLiteral("CONTROL_HIGH_LATENCY");
    case 2600: return QStringLiteral("CONTROL_HIGH_LATENCY");
    case 246: return QStringLiteral("PREFLIGHT_REBOOT_SHUTDOWN");
    case 241: return QStringLiteral("PREFLIGHT_CALIBRATION");
    default:  return QStringLiteral("MAV_CMD_%1").arg(command);
    }
}

QString EventBroadcaster::_mavResultName(int result)
{
    switch (result) {
    case 0: return QStringLiteral("ACCEPTED");
    case 1: return QStringLiteral("TEMPORARILY_REJECTED");
    case 2: return QStringLiteral("DENIED");
    case 3: return QStringLiteral("UNSUPPORTED");
    case 4: return QStringLiteral("FAILED");
    case 5: return QStringLiteral("IN_PROGRESS");
    case 6: return QStringLiteral("CANCELLED");
    default: return QStringLiteral("RESULT_%1").arg(result);
    }
}

EventBroadcaster::~EventBroadcaster()
{
    if (_instance == this) {
        _instance = nullptr;
    }
}

EventBroadcaster *EventBroadcaster::instance()
{
    return _instance;
}

QString EventBroadcaster::_summariseEvent(const QString &event, const QVariantMap &data)
{
    if (data.isEmpty()) {
        return event;
    }
    QStringList parts;
    int shown = 0;
    for (auto it = data.constBegin(); it != data.constEnd() && shown < 4; ++it, ++shown) {
        const QString v = it.value().toString();
        parts << QStringLiteral("%1=%2").arg(it.key(), v.left(40));
    }
    return parts.join(QStringLiteral(", "));
}

void EventBroadcaster::sendEvent(const QString &category, const QString &event, const QVariantMap &data)
{
    if (!_enabled) {
        return;
    }

    _eventCounter++;
    const qint64 epochMs = QDateTime::currentMSecsSinceEpoch();

    QJsonObject json;
    json["seq"] = static_cast<qint64>(_eventCounter);
    json["timestamp"] = epochMs / 1000.0;
    json["category"] = category;
    json["event"] = event;

    if (!data.isEmpty()) {
        json["data"] = QJsonObject::fromVariantMap(data);
    }

    const QByteArray payload = QJsonDocument(json).toJson(QJsonDocument::Compact) + "\n";
    _broadcast(payload);

    qCDebug(EventBroadcasterLog) << "Event:" << category << event;
    emit eventSent(category, event);

    // -- tap: store in ring buffer and notify the QML Monitor panel ---------
    LogEntry entry;
    entry.epochMs = epochMs;
    entry.dir = QStringLiteral("tx");
    entry.channel = QStringLiteral("event");
    entry.category = category;
    entry.event = event;
    entry.summary = _summariseEvent(event, data);
    entry.payload = data;
    _appendLog(entry);
    emit messageSent(category, event, data, epochMs);
}

void EventBroadcaster::setBroadcastPort(quint16 port)
{
    _broadcastPort = port;
    qCDebug(EventBroadcasterLog) << "Broadcast port changed to" << port;
}

void EventBroadcaster::setReceivePort(quint16 port)
{
    if (_receivePort == port) {
        return;
    }
    _receivePort = port;
    _bindReceiveSocket();
    qCDebug(EventBroadcasterLog) << "Receive port changed to" << port;
}

void EventBroadcaster::setMonitorPort(quint16 port)
{
    if (_monitorPort == port) {
        return;
    }
    _monitorPort = port;
    _bindMonitorSocket();
    qCDebug(EventBroadcasterLog) << "Monitor port changed to" << port;
}

void EventBroadcaster::setPlanMirrorPort(quint16 port)
{
    if (_planMirrorPort == port) {
        return;
    }
    _planMirrorPort = port;
    _bindPlanMirrorSocket();
    qCDebug(EventBroadcasterLog) << "Plan mirror port changed to" << port;
}

void EventBroadcaster::setEnabled(bool enabled)
{
    _enabled = enabled;
    qCDebug(EventBroadcasterLog) << "Broadcasting" << (enabled ? "enabled" : "disabled");
}

QVariantList EventBroadcaster::messageLog(int maxRows) const
{
    QMutexLocker lock(&_logMutex);
    QVariantList out;
    const int n = static_cast<int>(_log.size());
    const int start = (maxRows > 0 && maxRows < n) ? n - maxRows : 0;
    out.reserve(n - start);
    for (int i = start; i < n; ++i) {
        const LogEntry &e = _log[i];
        QVariantMap m;
        m["ts"] = e.epochMs;
        m["dir"] = e.dir;
        m["channel"] = e.channel;
        m["category"] = e.category;
        m["event"] = e.event;
        m["summary"] = e.summary;
        m["payload"] = e.payload;
        out.append(m);
    }
    return out;
}

void EventBroadcaster::clearMessageLog()
{
    QMutexLocker lock(&_logMutex);
    _log.clear();
}

void EventBroadcaster::_appendLog(const LogEntry &entry)
{
    QMutexLocker lock(&_logMutex);
    _log.push_back(entry);
    while (static_cast<int>(_log.size()) > kMaxLogRows) {
        _log.pop_front();
    }
}

void EventBroadcaster::_onReadyRead()
{
    while (_receiveSocket->hasPendingDatagrams()) {
        QByteArray datagram;
        datagram.resize(_receiveSocket->pendingDatagramSize());
        QHostAddress sender;
        quint16 senderPort;
        _receiveSocket->readDatagram(datagram.data(), datagram.size(), &sender, &senderPort);

        qCDebug(EventBroadcasterLog) << "Received datagram from" << sender.toString() << ":" << senderPort
                                     << "size:" << datagram.size();

        const QJsonDocument doc = QJsonDocument::fromJson(datagram);
        if (!doc.isObject()) {
            qCWarning(EventBroadcasterLog) << "Received invalid JSON command:" << datagram;
            continue;
        }

        const QJsonObject json = doc.object();
        const QString action = json.value("action").toString();
        if (action.isEmpty()) {
            qCWarning(EventBroadcasterLog) << "Received command without 'action' field:" << datagram;
            continue;
        }

        // Build params map from all fields except "action"
        QVariantMap params;
        for (auto it = json.constBegin(); it != json.constEnd(); ++it) {
            if (it.key() != "action") {
                params.insert(it.key(), it.value().toVariant());
            }
        }

        const qint64 epochMs = QDateTime::currentMSecsSinceEpoch();
        qCDebug(EventBroadcasterLog) << "Command received - action:" << action << "params:" << params;
        emit commandReceived(action, params);

        QVariantMap full = params;
        full.insert(QStringLiteral("action"), action);
        LogEntry entry;
        entry.epochMs = epochMs;
        entry.dir = QStringLiteral("rx");
        entry.channel = QStringLiteral("command");
        entry.category = QStringLiteral("command");
        entry.event = action;
        entry.summary = _summariseEvent(action, params);
        entry.payload = full;
        _appendLog(entry);
        emit messageReceived(QStringLiteral("command"), full, epochMs);
    }
}

void EventBroadcaster::_onMonitorReadyRead()
{
    while (_monitorSocket->hasPendingDatagrams()) {
        QByteArray datagram;
        datagram.resize(_monitorSocket->pendingDatagramSize());
        QHostAddress sender;
        quint16 senderPort;
        _monitorSocket->readDatagram(datagram.data(), datagram.size(), &sender, &senderPort);

        const QJsonDocument doc = QJsonDocument::fromJson(datagram);
        if (!doc.isObject()) {
            qCWarning(EventBroadcasterLog) << "Monitor tap got invalid JSON:" << datagram;
            continue;
        }
        const QJsonObject obj = doc.object();
        const QVariantMap payload = obj.toVariantMap();

        const QString dir = payload.value(QStringLiteral("dir")).toString();
        const QString lmcpType = payload.value(QStringLiteral("lmcp_type")).toString();
        const QString summary = payload.value(QStringLiteral("summary")).toString();
        const double ts = payload.value(QStringLiteral("ts")).toDouble();
        const qint64 epochMs = (ts > 0.0) ? static_cast<qint64>(ts * 1000.0)
                                          : QDateTime::currentMSecsSinceEpoch();

        LogEntry entry;
        entry.epochMs = epochMs;
        entry.dir = dir.contains(QStringLiteral("out")) ? QStringLiteral("tx") : QStringLiteral("rx");
        entry.channel = QStringLiteral("bridge");
        entry.category = lmcpType.isEmpty() ? dir : lmcpType;
        entry.event = dir;
        entry.summary = summary;
        entry.payload = payload;
        _appendLog(entry);
        emit messageReceived(QStringLiteral("bridge"), payload, epochMs);
    }
}

void EventBroadcaster::_bindReceiveSocket()
{
    _receiveSocket->close();
    if (!_receiveSocket->bind(QHostAddress::Any, _receivePort, QUdpSocket::ShareAddress | QUdpSocket::ReuseAddressHint)) {
        qCWarning(EventBroadcasterLog) << "Failed to bind receive socket to port" << _receivePort
                                       << ":" << _receiveSocket->errorString();
    } else {
        qCDebug(EventBroadcasterLog) << "Receive socket bound to port" << _receivePort;
    }
}

void EventBroadcaster::_bindMonitorSocket()
{
    _monitorSocket->close();
    if (!_monitorSocket->bind(QHostAddress::Any, _monitorPort, QUdpSocket::ShareAddress | QUdpSocket::ReuseAddressHint)) {
        qCWarning(EventBroadcasterLog) << "Failed to bind monitor socket to port" << _monitorPort
                                       << ":" << _monitorSocket->errorString();
    } else {
        qCDebug(EventBroadcasterLog) << "Monitor socket bound to port" << _monitorPort;
    }
}

void EventBroadcaster::_bindPlanMirrorSocket()
{
    _planMirrorSocket->close();
    if (!_planMirrorSocket->bind(QHostAddress::Any, _planMirrorPort, QUdpSocket::ShareAddress | QUdpSocket::ReuseAddressHint)) {
        qCWarning(EventBroadcasterLog) << "Failed to bind plan-mirror socket to port" << _planMirrorPort
                                       << ":" << _planMirrorSocket->errorString();
    } else {
        qCDebug(EventBroadcasterLog) << "Plan-mirror socket bound to port" << _planMirrorPort;
    }
}

void EventBroadcaster::_broadcast(const QByteArray &data)
{
    _socket->writeDatagram(data, QHostAddress::Broadcast, _broadcastPort);
    _socket->writeDatagram(data, QHostAddress::LocalHost, _broadcastPort);
}

void EventBroadcaster::setPlanOverlay(const QVariant &overlay)
{
    _planOverlay = overlay;
    emit planOverlayChanged();
}

void EventBroadcaster::setShowVWorldBuildings(bool on)
{
    if (_showVWorldBuildings == on) return;
    _showVWorldBuildings = on;
    emit showVWorldBuildingsChanged();
}

void EventBroadcaster::setShowVWorldRoads(bool on)
{
    if (_showVWorldRoads == on) return;
    _showVWorldRoads = on;
    emit showVWorldRoadsChanged();
}

void EventBroadcaster::setShowVWorldRivers(bool on)
{
    if (_showVWorldRivers == on) return;
    _showVWorldRivers = on;
    emit showVWorldRiversChanged();
}

void EventBroadcaster::setUxasPlannedWaypoints(int vehicleId, const QVariantList &waypoints)
{
    if (vehicleId <= 0) {
        return;
    }
    const QString key = QString::number(vehicleId);
    if (waypoints.isEmpty()) {
        _uxasPlannedWaypoints.remove(key);
    } else {
        _uxasPlannedWaypoints.insert(key, waypoints);
    }
    emit uxasPlannedWaypointsChanged();
}

void EventBroadcaster::clearUxasPlannedWaypoints(int vehicleId)
{
    if (vehicleId <= 0) {
        if (_uxasPlannedWaypoints.isEmpty()) {
            return;
        }
        _uxasPlannedWaypoints.clear();
    } else {
        const QString key = QString::number(vehicleId);
        if (!_uxasPlannedWaypoints.contains(key)) {
            return;
        }
        _uxasPlannedWaypoints.remove(key);
    }
    emit uxasPlannedWaypointsChanged();
}

void EventBroadcaster::_onPlanMirrorReadyRead()
{
    while (_planMirrorSocket->hasPendingDatagrams()) {
        QByteArray datagram;
        datagram.resize(_planMirrorSocket->pendingDatagramSize());
        QHostAddress sender;
        quint16 senderPort;
        _planMirrorSocket->readDatagram(datagram.data(), datagram.size(), &sender, &senderPort);

        const QJsonDocument doc = QJsonDocument::fromJson(datagram);
        if (!doc.isObject()) {
            qCWarning(EventBroadcasterLog) << "Plan-mirror got non-object JSON:" << datagram;
            continue;
        }
        const QJsonObject obj = doc.object();
        const QString category = obj.value(QStringLiteral("category")).toString();
        const QString event = obj.value(QStringLiteral("event")).toString();
        if (category != QStringLiteral("uxas_plan") || event != QStringLiteral("mission")) {
            // ignore anything else that happens to land on this port
            continue;
        }
        const QJsonObject data = obj.value(QStringLiteral("data")).toObject();
        const int vehicleId = data.value(QStringLiteral("vehicle_id")).toInt(0);
        if (vehicleId <= 0) {
            qCWarning(EventBroadcasterLog) << "uxas_plan/mission missing vehicle_id";
            continue;
        }
        const QJsonArray wpsArr = data.value(QStringLiteral("waypoints")).toArray();
        QVariantList wps;
        wps.reserve(wpsArr.size());
        for (const QJsonValue &v : wpsArr) {
            const QJsonArray trip = v.toArray();
            if (trip.size() < 2) {
                continue;
            }
            QVariantList t;
            t.append(trip.at(0).toDouble());
            t.append(trip.at(1).toDouble());
            t.append(trip.size() >= 3 ? trip.at(2).toDouble() : 0.0);
            wps.append(QVariant(t));
        }
        const QString key = QString::number(vehicleId);
        if (wps.isEmpty()) {
            _uxasPlannedWaypoints.remove(key);
        } else {
            _uxasPlannedWaypoints.insert(key, wps);
        }
        emit uxasPlannedWaypointsChanged();

        // Tap into the message log so the QGC Message Monitor panel shows it.
        const qint64 epochMs = QDateTime::currentMSecsSinceEpoch();
        QVariantMap payload;
        payload.insert(QStringLiteral("vehicle_id"), vehicleId);
        payload.insert(QStringLiteral("waypoints_count"), wps.size());
        LogEntry entry;
        entry.epochMs = epochMs;
        entry.dir = QStringLiteral("rx");
        entry.channel = QStringLiteral("plan");
        entry.category = QStringLiteral("uxas_plan");
        entry.event = QStringLiteral("mission");
        entry.summary = QStringLiteral("vid=%1 wps=%2").arg(vehicleId).arg(wps.size());
        entry.payload = payload;
        _appendLog(entry);
        emit messageReceived(QStringLiteral("plan"), payload, epochMs);
    }
}
