#include "EventBroadcaster.h"
#include "QGCLoggingCategory.h"

QGC_LOGGING_CATEGORY(EventBroadcasterLog, "EventBroadcaster")

EventBroadcaster *EventBroadcaster::_instance = nullptr;

EventBroadcaster::EventBroadcaster(QObject *parent)
    : QObject(parent)
    , _socket(new QUdpSocket(this))
{
    _instance = this;
    qCDebug(EventBroadcasterLog) << "EventBroadcaster initialized, broadcasting on port" << _broadcastPort;
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

void EventBroadcaster::sendEvent(const QString &category, const QString &event, const QVariantMap &data)
{
    if (!_enabled) {
        return;
    }

    _eventCounter++;

    QJsonObject json;
    json["seq"] = static_cast<qint64>(_eventCounter);
    json["timestamp"] = QDateTime::currentMSecsSinceEpoch() / 1000.0;
    json["category"] = category;
    json["event"] = event;

    if (!data.isEmpty()) {
        json["data"] = QJsonObject::fromVariantMap(data);
    }

    const QByteArray payload = QJsonDocument(json).toJson(QJsonDocument::Compact) + "\n";
    _broadcast(payload);

    qCDebug(EventBroadcasterLog) << "Event:" << category << event;
    emit eventSent(category, event);
}

void EventBroadcaster::setBroadcastPort(quint16 port)
{
    _broadcastPort = port;
    qCDebug(EventBroadcasterLog) << "Broadcast port changed to" << port;
}

void EventBroadcaster::setEnabled(bool enabled)
{
    _enabled = enabled;
    qCDebug(EventBroadcasterLog) << "Broadcasting" << (enabled ? "enabled" : "disabled");
}

void EventBroadcaster::_broadcast(const QByteArray &data)
{
    _socket->writeDatagram(data, QHostAddress::Broadcast, _broadcastPort);
    _socket->writeDatagram(data, QHostAddress::LocalHost, _broadcastPort);
}
